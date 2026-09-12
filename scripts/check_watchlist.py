#!/usr/bin/env python3
"""Follow the wallets under close watch, and say when they touch his world.

`config.watch_wallets` holds wallets that are probably his and are not
confirmed — today `0xdd53c529…`, which opened at zero two days into a six-day
silence of the target's and ran to $51.3M in three weeks while matching his
exits on amount and timing.

Per wallet, per run: who Hyperliquid says it is, what it is worth across every
dex, when it last traded, the agents and sub-accounts it has, where it has
withdrawn to, its HyperEVM nonce, and a bounded L1 sweep so its on-chain
destinations enter the substrate and can be compared with the target's own.

Two outcomes, and they are not the same. A CONTACT — it touched the target, a
wallet believed to be his, or one of his private deposit addresses — is an
observed connection and the thing worth waking someone for. A CHANGE is
evidence about what it is doing, reported once, on the transition.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent_links import normalise_agents
from src.alerts import alert_explicit_link, alert_watchlist_change, alert_watchlist_contact
from src.chain.budget import CallBudget
from src.chain.chains import enabled_chains
from src.chain.collect import records_for, sweep_wallet
from src.chain.hyperevm import account_activity
from src.hl_actions import fetch_actions, own_actions, record
from src.hl_identity import probe
from src.scanner import live_hip3_dexes, merged_clearinghouse_state
from src.utils import DATA_DIR, hl_post, load_config
from src.watchlist import (
    WATCHLIST_DIR,
    build_report,
    changes,
    contact_severity,
    contacts,
    explicit_links,
    save,
    shared_infrastructure,
    snapshot,
    watched,
)

# The trace job has a 15-minute ceiling and six other steps. A watched wallet
# is swept incrementally from a stored cursor, so only the first run is deep.
SWEEP_SECONDS = 45
SWEEP_CALLS = 60

# Whole-chain readings spent on the counterparties of a watched wallet. A
# contact is the one finding here worth waking someone for, so it is worth a
# call each to know whether the address is a person or an exchange.
CONTACT_READINGS = 8


def target_world(config: dict) -> dict:
    """Every address whose appearance beside a watched wallet would matter."""
    target = (config.get("target_wallet") or "").lower()
    world = {target: "the target"}
    for w in config.get("known_self_wallets", []) or []:
        a = (w or "").lower()
        if a:
            world[a] = "a known wallet of his"

    # His private deposit addresses: a destination he sends to that the whole
    # chain says is quiet. Two wallets funding one are the same exchange
    # customer, which is the strongest single signal this project has.
    try:
        from src.linkage import (
            ACTIVITY_LOOKUPS_PER_RUN,
            activity_cache,
            activity_exclusions,
            get_outbound_addresses,
            outbound_chains,
        )
        destinations = get_outbound_addresses(target, config)
        excluded, _pending = activity_exclusions(
            destinations, outbound_chains(target),
            activity_cache(max_lookups=ACTIVITY_LOOKUPS_PER_RUN))
        for a in destinations - excluded:
            world.setdefault(a, "a private deposit address of his")
    except Exception as exc:                          # noqa: BLE001
        print(f"[watchlist] deposit addresses unavailable ({type(exc).__name__}) — "
              f"a contact with one would not be recognised this run")

    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            rows = json.load(f).get("wallets") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    for row in rows:
        a = (row.get("wallet") or "").lower()
        tier = row.get("tier")
        if a and tier in ("CONFIRMED", "PROBABLE", "POSSIBLE"):
            world.setdefault(a, f"roster: {tier}")
    return world


def _previous() -> dict:
    try:
        with open(WATCHLIST_DIR / "latest.json") as f:
            return {w["address"]: w for w in json.load(f).get("wallets", [])
                    if w.get("address")}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def account_value(address: str, dexes: list) -> tuple[float, dict]:
    """What an account is worth across every dex it can trade, plus spot USDC.

    One function for BOTH sides of the comparison. A watched wallet read across
    the live dexes against a target read some other way would divide two
    different quantities and call the answer a ratio, which is how this project
    has been bitten before — so the only way to read a value here is this one.

    Raises rather than returning a number it could not compute: the callers
    disagree about what to do with a failure and neither may see a 0.0.
    """
    state = merged_clearinghouse_state(address, dexes=dexes)
    value = float((state.get("marginSummary") or {}).get("accountValue") or 0)
    spot = hl_post({"type": "spotClearinghouseState", "user": address}) or {}
    for b in spot.get("balances") or []:
        if str(b.get("coin", "")).upper() == "USDC":
            value += float(b.get("total") or 0)
    return value, state


def target_account_value(config: dict) -> float | None:
    """The target's own size, so a watched wallet can be measured against it.

    None when he could not be read. Rule 6: a target priced at 0.0 would make
    every watched wallet infinitely larger than him and fire the crossing on an
    outage — the one reading that must never be manufactured.
    """
    target = (config.get("target_wallet") or "").strip().lower()
    if not target:
        return None
    try:
        return account_value(target, live_hip3_dexes())[0]
    except Exception as exc:                          # noqa: BLE001 - transport
        print(f"[watchlist] the target's own value is unreadable "
              f"({type(exc).__name__}: {exc}) — no wallet can be sized against "
              f"him this run")
        return None


def read_wallet(address: str, config: dict, target_value: float | None = None
                ) -> tuple[dict, list]:
    """One reading, plus every counterparty it has been seen with."""
    errors, counterparties = [], set()

    ident = probe(address, hl_post, sleep=time.sleep)
    if not ident.get("read_ok"):
        errors.extend(ident.get("errors") or [])

    value, dexes = None, []
    try:
        value, state = account_value(address, live_hip3_dexes())
        # Which venues it actually has a book on. A position opening on a dex
        # it has never used is what a migration INSIDE Hyperliquid looks like,
        # and the total account value alone cannot show it.
        for pos in state.get("assetPositions") or []:
            coin = str(((pos or {}).get("position") or {}).get("coin") or "")
            dexes.append(coin.split(":", 1)[0].lower() if ":" in coin else "perp")
    except Exception as exc:                          # noqa: BLE001 - transport
        errors.append(f"account value: {type(exc).__name__}: {exc}")

    last_fill = None
    try:
        fills = hl_post({"type": "userFills", "user": address})
        if isinstance(fills, list) and fills:
            last_fill = max(int(f.get("time") or 0) for f in fills)
    except Exception as exc:                          # noqa: BLE001 - transport
        errors.append(f"fills: {type(exc).__name__}: {exc}")

    subaccounts = []
    try:
        subs = hl_post({"type": "subAccounts", "user": address})
        subaccounts = [(s.get("user") or s.get("address") or "") for s in subs or []
                       if isinstance(s, dict)]
    except Exception as exc:                          # noqa: BLE001 - transport
        errors.append(f"subAccounts: {type(exc).__name__}: {exc}")

    # Its own L1 actions: withdrawal destinations, agent approvals, sends.
    withdrawals, agents = [], []
    rows, err = fetch_actions(address)
    if err:
        errors.append(f"explorer: {err}")
    else:
        acts = own_actions(rows, address)
        record(address, acts)
        for a in acts:
            dest = a.get("destination")
            if dest:
                counterparties.add(dest)
                # sendToEvmWithData is the native Circle withdrawal: it names
                # a recipient on any CCTP chain, and the ledger cannot show it.
                if a["type"] in ("withdraw3", "sendToEvmWithData", "usdSend",
                                 "spotSend", "sendAsset"):
                    withdrawals.append(dest)
                elif a["type"] == "approveAgent":
                    agents.append(dest)

    # The NAMED agents, which is where an API wallet appears. Neither source
    # above can see one: `webData2.agentAddress` reports only the unnamed
    # frontend agent, and the explorer's window is the last 300 actions, so on
    # a wallet trading this hard an `approveAgent` from weeks ago has already
    # rolled out of it. `extraAgents` is the only endpoint that still answers.
    # Measured on the watched wallet: agentAddress None, extraAgents one entry.
    try:
        agents.extend(a["address"] for a in normalise_agents(
            hl_post({"type": "extraAgents", "user": address})))
    except Exception as exc:                          # noqa: BLE001 - transport
        errors.append(f"extraAgents: {type(exc).__name__}: {exc}")

    activity = account_activity(address)
    if activity.get("errors"):
        errors.extend(activity["errors"])

    # Its on-chain counterparties, from whatever the substrate holds. The sweep
    # below is what puts them there.
    for rec in records_for(address):
        for side in ("src", "dst"):
            other = (rec.get(side) or "").lower()
            if other and other != address:
                counterparties.add(other)

    snap = snapshot(
        address, account_value=value, last_fill_ms=last_fill,
        agents=(agents + [ident.get("agent_address")] if ident.get("agent_address")
                else agents),
        subaccounts=subaccounts, withdrawal_destinations=withdrawals,
        hyperevm_nonce=activity.get("nonce"), role=ident.get("role"),
        master=ident.get("master"), owner=ident.get("owner"),
        staking_link=ident.get("staking_link"),
        vaults_led=[(v or {}).get("vaultAddress") if isinstance(v, dict) else v
                    for v in (ident.get("leading_vaults") or [])],
        dexes=dexes if value is not None else None,
        target_value=target_value,
        read_ok=not errors, errors=errors)
    return snap, sorted(counterparties)


def busy_flags(hits: list[dict]) -> dict:
    """True/False/None per contact: is this address busy anywhere we can read?

    Measured on every readable chain until one says busy, because busy on one
    chain is busy: `0xd7a827fb…` shows 2 transactions on Ethereum and 590,833
    on Arbitrum. A chain that cannot be read leaves None, and None still
    alerts.
    """
    from src.chain.activity import HOSTS, ActivityCache, is_busy

    out: dict = {}
    if not hits:
        return out
    try:
        cache = ActivityCache(DATA_DIR / "labels" / "address_activity.json",
                              max_lookups=CONTACT_READINGS)
    except Exception as exc:                          # noqa: BLE001
        print(f"[watchlist] activity cache unavailable ({type(exc).__name__}) — "
              f"contacts cannot be checked against the whole chain")
        return out
    for hit in hits:
        address = hit.get("address")
        verdict = None
        for chain in HOSTS:
            reading = cache.get(address, chain)
            busy = is_busy(reading)
            if busy:
                verdict = True
                break
            if busy is False:
                verdict = False
        out[address] = verdict
    return out


def sweep(address: str, config: dict) -> None:
    """Bounded multi-chain sweep so its destinations reach the substrate."""
    import os
    if not os.environ.get("ETHERSCAN_API_KEY"):
        print(f"[watchlist] no Etherscan key — {address[:12]}... not swept, so a "
              f"shared deposit address cannot be seen")
        return
    from src.chain.assets import load_canonical_contracts
    budget = CallBudget(max_calls=SWEEP_CALLS, seconds=SWEEP_SECONDS)
    try:
        result = sweep_wallet(address, enabled_chains(config), budget, cluster=True,
                              canonical=load_canonical_contracts(
                                  config, DATA_DIR / "labels" / "token_contracts.json"))
    except Exception as exc:                          # noqa: BLE001
        print(f"[watchlist] sweep failed for {address[:12]}...: {type(exc).__name__}: {exc}")
        return
    degraded = (result or {}).get("degraded_sources") or []
    records = sum(c.get("records", 0) for c in (result or {}).get("chains", {}).values())
    print(f"[watchlist] swept {address[:12]}...: {records} record(s)"
          + (f", could not read {degraded}" if degraded else ""))


def main() -> int:
    config = load_config()
    wallets = watched(config)
    if not wallets:
        print("[watchlist] config.watch_wallets is empty — nothing under close watch")
        return 0

    world = target_world(config)
    previous = _previous()
    snapshots, found_changes, found_contacts = [], {}, {}

    # Once per run, so every watched wallet is sized against the same moment of
    # his book rather than against whatever it happened to be worth when its
    # own turn came round.
    his_value = target_account_value(config)
    if his_value is not None:
        print(f"[watchlist] the target is worth ${his_value:,.0f} across every "
              f"live dex and spot")

    for entry in wallets:
        address = entry["address"]
        sweep(address, config)
        snap, counterparties = read_wallet(address, config, target_value=his_value)
        snap["why"] = entry.get("why")
        snapshots.append(snap)

        seen = contacts(counterparties, world)
        hits, infra = shared_infrastructure(seen, busy_flags(seen))
        if hits:
            found_contacts[address] = hits
        deltas = changes(previous.get(address), snap)
        if deltas:
            found_changes[address] = deltas

        value = snap["account_value"]
        ratio = snap["size_ratio"]
        print(f"[watchlist] {address} role={snap['role']} "
              f"value={'unreadable' if value is None else f'${value:,.0f}'} "
              f"vs-target={'unknown' if ratio is None else f'{ratio:.2f}x'} "
              f"agents={len(snap['agents'])} subaccounts={len(snap['subaccounts'])} "
              f"withdrawals={len(snap['withdrawal_destinations'])} "
              f"nonce={snap['hyperevm_nonce']} counterparties={len(counterparties)}")
        if snap["errors"]:
            print(f"[watchlist]   unreadable in part: {snap['errors'][:3]}")

        for hit in infra:
            print(f"[watchlist]   shared infrastructure, not a contact: "
                  f"{hit['address']} ({hit['is']} — busy on the whole chain)")
        for hit in hits:
            severity = contact_severity(hit["is"])
            print(f"[watchlist]   CONTACT ({severity}) {hit['address']} — {hit['is']}")
            alert_watchlist_contact(address, hit["address"], hit["is"], entry.get("why"),
                                    severity=severity)
        for d in deltas:
            print(f"[watchlist]   CHANGE {d['kind']}: {d['detail']}")
        # Hyperliquid naming this wallet's owner is not a "change" among
        # others — it is the deliverable, and it goes out on its own channel
        # at CRITICAL as well as in the summary.
        for d in explicit_links(deltas):
            print(f"[watchlist]   EXPLICIT LINK {d['kind']}: {d['detail']}")
            alert_explicit_link(d["kind"], address, d.get("address") or "?",
                                f"watched wallet: {entry.get('why') or '(not recorded)'}")
        if deltas:
            alert_watchlist_change(address, deltas, entry.get("why"))
        if not previous.get(address):
            print("[watchlist]   first reading — baseline recorded, nothing alerted")

    save(build_report(snapshots, {"changes": found_changes, "contacts": found_contacts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
