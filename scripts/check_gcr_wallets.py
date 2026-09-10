#!/usr/bin/env python3
"""Tripwire on the Ethereum addresses GCR's own words led to.

None of them touches Hyperliquid or the target today. The point is the day one
does: a confirmed GCR address appearing in the target's transfer graph, or
trading on Hyperliquid, would be the strongest evidence this project can
produce about who the tracked wallet is — and unlike every style check, it would
be flow rather than resemblance.

Cheap: a handful of Hyperliquid calls plus addresses already on disk.
"""

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import send_alert
from src.chain.collect import records_for
from src.gcr_wallets import (
    HL_BRIDGE,
    build_report,
    load_addresses,
    save,
    watched,
)
from src.utils import hl_post, load_config


def hl_state(addr: str) -> dict:
    """Ask Hyperliquid about one address, across its whole surface.

    An account can exist without holding a perp position — spot only, sitting in
    a vault, trading under a subaccount, or having authorised an agent and
    nothing else. A check that only asked for perp state, fills and ledger would
    miss every one of those, so it asks for all of them.

    Two things are deliberately NOT treated as presence, because both flagged
    every address in the cluster when measured naively:

      userFees always returns a 16-entry dailyUserVlm array whose `exchange`
      field is the WHOLE VENUE's volume. Only userCross and userAdd belong to
      the account being asked about.

      A fill with dir "Spot Dust Conversion" is the venue sweeping worthless
      balances automatically, not the account trading.

    Every failure is recorded as a failure. An exception here must never
    serialise as "this address is clean" — that is the difference between
    "we could not tell" and "there is nothing there".
    """
    out = {"read_ok": True, "fills": 0, "ledger": 0, "outbound_ledger": 0,
           "account_value": "0", "spot": 0, "vaults": 0, "subaccounts": 0,
           "agents": 0, "open_orders": 0, "historical_orders": 0,
           "user_volume": 0.0}
    try:
        st = hl_post({"type": "clearinghouseState", "user": addr}) or {}
        out["account_value"] = (st.get("marginSummary") or {}).get(
            "accountValue", "0")

        spot = hl_post({"type": "spotClearinghouseState", "user": addr}) or {}
        out["spot"] = len(spot.get("balances") or [])

        for key, req in (("vaults", "userVaultEquities"),
                         ("subaccounts", "subAccounts"),
                         ("agents", "extraAgents"),
                         ("open_orders", "openOrders"),
                         ("historical_orders", "historicalOrders")):
            out[key] = len(hl_post({"type": req, "user": addr}) or [])
            time.sleep(0.15)

        fills = hl_post({"type": "userFills", "user": addr}) or []
        # Dust conversions are the venue acting, not the account.
        out["fills"] = sum(
            1 for f in fills
            if "dust conversion" not in str(f.get("dir", "")).lower())

        ledger = hl_post({"type": "userNonFundingLedgerUpdates",
                          "user": addr, "startTime": 0}) or []
        out["ledger"] = len(ledger)
        # Inbound-only ledger rows are airdrop spam, not the address acting.
        # 0x398d2824... looks "active" on that basis alone and is not.
        out["outbound_ledger"] = sum(
            1 for e in ledger
            if ((e.get("delta") or {}).get("user") or "").lower() == addr.lower())

        fees = hl_post({"type": "userFees", "user": addr}) or {}
        total = 0.0
        for row in fees.get("dailyUserVlm") or []:
            for k in ("userCross", "userAdd"):
                try:
                    total += float(row.get(k) or 0)
                except (TypeError, ValueError):
                    pass
        out["user_volume"] = total
    except Exception as exc:                          # noqa: BLE001 - transport
        out["read_ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# Blockscout serves Arbitrum without an API key. Etherscan's free tier will not
# serve account endpoints for several chains, which is why this does not reuse
# src/utils.etherscan_get.
ARBITRUM_API = "https://arbitrum.blockscout.com/api/v2"


def bridge_state(addr: str) -> dict:
    """Has this address ever touched the Hyperliquid bridge on Arbitrum?

    The bridge lives only on Arbitrum, so Arbitrum is the whole question. A
    404 is a real answer -- the address has no history there. Anything else
    that goes wrong is recorded as unreadable, never as clean.
    """
    out = {"read_ok": True, "touched": False, "chain": "arbitrum"}
    try:
        for path in (f"/addresses/{addr}/transactions",
                     f"/addresses/{addr}/token-transfers"):
            r = requests.get(f"{ARBITRUM_API}{path}", timeout=45,
                             headers={"accept": "application/json"})
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                out["read_ok"] = False
                out["error"] = f"HTTP {r.status_code}"
                return out
            for item in r.json().get("items") or []:
                for side in ("from", "to"):
                    who = ((item.get(side) or {}).get("hash") or "").lower()
                    if who == HL_BRIDGE:
                        out["touched"] = True
                        return out
            time.sleep(0.25)
    except Exception as exc:                          # noqa: BLE001 - transport
        out["read_ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    data = load_addresses()
    if not data:
        print("[gcr-eth] no address labels on disk — nothing to watch")
        return 1

    config = load_config()
    target = (config.get("target_wallet") or "").lower()

    graph = set()
    try:
        for r in records_for(target):
            for key in ("src", "dst"):
                v = (r.get(key) or "").lower()
                if v:
                    graph.add(v)
    except Exception as exc:                          # noqa: BLE001
        print(f"[gcr-eth] graph unreadable: {type(exc).__name__}: {exc}")

    states, bridges = {}, {}
    for addr in watched(data):
        states[addr] = hl_state(addr)
        bridges[addr] = bridge_state(addr)
        time.sleep(0.3)

    report = build_report(graph_addresses=graph, hl_states=states,
                          bridge_states=bridges, data=data)
    save(report)

    print(f"[gcr-eth] watching {report['watched']} address(es) against "
          f"{len(graph)} target counterparties")
    for hit in report["graph_hits"]:
        print(f"[gcr-eth]   GRAPH HIT  {hit['address']}  ({hit['tier']}) "
              f"{hit['role']}")
    for hit in report["hyperliquid_hits"]:
        print(f"[gcr-eth]   HL ACTIVE  {hit['address']}  ({hit['tier']}) "
              f"value={hit['account_value']} fills={hit['fills']} "
              f"volume={hit.get('volume')} structural={hit.get('structural')}")
    for hit in report["bridge_hits"]:
        print(f"[gcr-eth]   BRIDGE     {hit['address']}  ({hit['tier']}) "
              f"funded the Hyperliquid bridge on {hit.get('chain')}")
    for u in report["unreadable"]:
        print(f"[gcr-eth]   UNREADABLE {u['address']}  {u['why']}")
    print(f"[gcr-eth] {report['reading']}")

    if report["graph_hits"] or report["hyperliquid_hits"] or report["bridge_hits"]:
        lines = [
            "An Ethereum address confirmed or strongly linked to GCR has moved "
            "into range of the tracked wallet.",
            "",
            "This is FLOW, not style. Every other GCR check compares habits; "
            "this one is a transaction that either happened or did not.",
            "",
        ]
        lines.extend(f"GRAPH: {h['address']} ({h['tier']}) — {h['role']}"
                     for h in report["graph_hits"])
        lines.extend(f"HYPERLIQUID: {h['address']} ({h['tier']}) — "
                     f"value {h['account_value']}, {h['fills']} fill(s)"
                     for h in report["hyperliquid_hits"])
        lines.extend(f"BRIDGE: {h['address']} ({h['tier']}) — funded the "
                     f"Hyperliquid bridge on {h.get('chain')}. The deposit may "
                     f"credit an account other than this address."
                     for h in report["bridge_hits"])
        lines += [
            "",
            "Provenance is in data/labels/gcr_addresses.json. Verify by hand "
            "before acting: a transfer is not ownership.",
        ]
        send_alert("GCR Ethereum address in range of the target",
                   "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
