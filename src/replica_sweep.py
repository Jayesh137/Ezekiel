"""His world's complete non-trading action history, from SQD's replica commands.

The explorer's `userDetails` shows the last 300 actions of one account and rolls a
payload out within a day, so for eight months the project never held a complete
list of the agents he approved, the Circle recipients he named, the builders and
referral codes he set — only what the ledger shows (money moved) and what the
collector happened to catch in its window. `src/replica_cmds.py` reads every
action of every account since 2026-01-21 with no key, and one request covers
his whole world at the cost of one wallet. This module turns that into:

* rows in `data/actions/<wallet>/` (the explorer's shape, through
  `hl_actions.own_actions`, so every reader of that store sees them);
* a report: per wallet, kinds and statuses; every agent ever approved; builders;
  referral codes; Circle recipients by chain; destinations outside his
  perimeter; failed actions (an intended destination is evidence too);
* one check nobody else makes: a historical agent of his whose CURRENT owner
  (`userRole`) is another account — an agent key reused by a second account is
  his (CRITICAL through `alert_explicit_link`);
* candidates: every outside destination is registered for the per-wallet
  detectors. Nothing here mints a vote.

It pages nothing the live detectors already page: the collector alerts a foreign
destination from the explorer within minutes, and replaying it ten days later
(the dataset's lag) is noise. Everything is pure or injectable; the script is
the shell.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from src import replica_cmds as rc
from src.chain.bridges import CCTP_DOMAINS
from src.hl_actions import foreign_destinations, normalise_address, own_actions

WORLD_TIERS = ("CONFIRMED", "PROBABLE")
AGENT_RECHECK_MS = 7 * 86_400_000
MAX_AGENT_READS = 40


def _low(value) -> str:
    return normalise_address(value) if isinstance(value, str) else ""


def world_users(config: dict, perimeter: dict | None, roster: dict | None) -> list[str]:
    """Everything the project calls his world, as one user list.

    Config cluster, known wallets, watch questions, every perimeter member (his
    deposit addresses are L1 addresses, but any EVM address is a Hyperliquid
    address too) and roster CONFIRMED/PROBABLE wallets.
    """
    users = {_low(config.get("target_wallet"))}
    users |= {_low(w) for w in config.get("known_self_wallets") or []}
    for w in config.get("watch_wallets") or []:
        users.add(_low(w.get("address") if isinstance(w, dict) else w))
    for member in (perimeter or {}).get("members") or {}:
        users.add(_low(member))
    rows = (roster or {}).get("wallets") or []
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    for row in rows:
        if isinstance(row, dict) and row.get("tier") in WORLD_TIERS:
            users.add(_low(row.get("wallet") or row.get("address")))
    return sorted(u for u in users if u.startswith("0x") and len(u) == 42)


def cluster_of(config: dict) -> set[str]:
    out = {_low(config.get("target_wallet"))}
    out |= {_low(w) for w in config.get("known_self_wallets") or []}
    return {c for c in out if c}


def by_wallet(rows: list[dict]) -> dict[str, list[dict]]:
    """Replica rows grouped by user, each parsed by `hl_actions.own_actions`."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows or []:
        grouped[row["user"]].append(rc.as_explorer_row(row))
    return {w: own_actions(ex, w) for w, ex in grouped.items()}


def dedupe_key(action: dict) -> tuple:
    """The same action seen through the explorer (keyed by hash) and through the
    replica stream (keyed by block:index) must count once. User-signed actions
    carry their nonce or time inside the payload; the rest fall back to the
    block, which an account rarely repeats a kind in."""
    payload = action.get("action") or {}
    stamp = payload.get("nonce") or payload.get("time") or action.get("nonce")
    if stamp is None:
        stamp = f"b{action.get('block')}"
    return (action.get("type"), str(stamp))


def dedupe(actions: list[dict]) -> list[dict]:
    """One row per action across both views. Two rows are the same action when
    they share a signing stamp, or — for kinds whose payload carries none, which
    the explorer stores without the row nonce the replica stream has — the same
    kind in the same block."""
    seen: set = set()
    out = []
    for a in sorted(actions or [], key=lambda a: (a.get("time") or 0)):
        keys = [dedupe_key(a)]
        if a.get("block") is not None:
            keys.append(("block", a.get("type"), str(a.get("block"))))
        if any(k in seen for k in keys):
            continue
        seen.update(keys)
        out.append(a)
    return out


def _stamp(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def wallet_report(wallet: str, actions: list[dict], perimeter_members: set, cluster: set,
                  ignore: set | None = None) -> dict:
    """Everything the history says about one wallet. Pure."""
    actions = dedupe(actions)
    kinds: dict[str, int] = defaultdict(int)
    failed: dict[str, int] = defaultdict(int)
    agents: dict[str, dict] = {}
    builders: dict[str, dict] = {}
    referrals: list[dict] = []
    circle: dict[str, list] = defaultdict(list)
    failed_destinations: list[dict] = []
    first = last = None
    for a in actions:
        kinds[a["type"]] += 1
        t = a.get("time")
        if isinstance(t, int):
            first = t if first is None else min(first, t)
            last = t if last is None else max(last, t)
        if a.get("error"):
            failed[a["type"]] += 1
            if a.get("destination") and a["type"] in ("withdraw3", "usdSend", "spotSend",
                                                      "sendToEvmWithData", "sendAsset"):
                failed_destinations.append({"type": a["type"], "destination": a["destination"],
                                            "amount": a.get("amount"), "time": a.get("time"),
                                            "chain": a.get("destination_chain")})
            continue
        payload = a.get("action") or {}
        if a["type"] == "approveAgent" and a.get("destination"):
            entry = agents.setdefault(a["destination"], {
                "address": a["destination"], "name": payload.get("agentName"),
                "first": t, "last": t, "approvals": 0,
                "signature_chain_id": a.get("signature_chain_id")})
            entry["approvals"] += 1
            entry["first"] = min(entry["first"], t) if isinstance(t, int) else entry["first"]
            entry["last"] = max(entry["last"], t) if isinstance(t, int) else entry["last"]
            if payload.get("agentName") and not entry.get("name"):
                entry["name"] = payload.get("agentName")
        elif a["type"] == "approveBuilderFee" and a.get("destination"):
            entry = builders.setdefault(a["destination"], {"address": a["destination"],
                                                           "max_fee_rate": payload.get("maxFeeRate"),
                                                           "first": t, "approvals": 0})
            entry["approvals"] += 1
        elif a["type"] in ("setReferrer", "registerReferrer"):
            referrals.append({"type": a["type"], "code": payload.get("code"), "time": t})
        elif a["type"] == "sendToEvmWithData":
            chain = a.get("destination_chain") or f"domain-{a.get('destination_domain')}"
            circle[chain].append({"recipient": a.get("destination"), "amount": a.get("amount"),
                                  "token": a.get("token"), "time": t})
    # An agent is an address he empowered, not an account that trades; agents are
    # reported above and checked through `userRole`, so they are not outsiders.
    outside = [f for f in foreign_destinations(actions, perimeter_members | cluster, ignore)
               if f.get("type") != "approveAgent"]
    outside_rows = [{"type": f["type"], "destination": f["destination"],
                     "chain": f.get("destination_chain"), "amount": f.get("amount"),
                     "token": f.get("token"), "time": f.get("time"), "hash": f.get("hash")}
                    for f in outside]
    return {
        "address": wallet,
        "actions": len(actions),
        "first": _stamp(first), "last": _stamp(last),
        "kinds": dict(sorted(kinds.items())),
        "failed": dict(sorted(failed.items())),
        "agents": sorted(agents.values(), key=lambda e: e.get("first") or 0),
        "builders": sorted(builders.values(), key=lambda e: e.get("first") or 0),
        "referrals": referrals,
        "circle_recipients": {k: v for k, v in circle.items()},
        "outside_destinations": outside_rows,
        "failed_destinations": failed_destinations,
    }


def outside_addresses(reports: dict) -> dict[str, dict]:
    """Every address outside his perimeter that his world paid or empowered,
    with the first row that named it. Hex addresses only (a Solana recipient is
    not a Hyperliquid account; the perimeter already holds his)."""
    out: dict[str, dict] = {}
    for wallet, rep in (reports or {}).items():
        for row in rep.get("outside_destinations") or []:
            dest = _low(row.get("destination"))
            if not dest.startswith("0x") or len(dest) != 42:
                continue
            if dest not in out or (row.get("time") or 0) < (out[dest].get("time") or 0):
                out[dest] = {**row, "from": wallet}
    return out


def agents_to_check(reports: dict, previous: dict | None, now_ms: int,
                    limit: int = MAX_AGENT_READS) -> list[tuple[str, str]]:
    """(agent, approving wallet) pairs due a `userRole` read: never read, or read
    more than AGENT_RECHECK_MS ago. Oldest reading first."""
    prev = (previous or {})
    due = []
    for wallet, rep in (reports or {}).items():
        for ag in rep.get("agents") or []:
            addr = _low(ag.get("address"))
            if not addr:
                continue
            last = (prev.get(addr) or {}).get("checked_ms")
            if last is None or now_ms - int(last) >= AGENT_RECHECK_MS:
                due.append((last or 0, addr, wallet))
    due.sort()
    return [(a, w) for _, a, w in due[:limit]]


def check_agent_owners(pairs: list[tuple[str, str]], cluster: set, fetch, now_ms: int,
                       previous: dict | None = None, *, sleep=None) -> tuple[dict, list[dict]]:
    """Ask `userRole` for each agent. `fetch(body)` is `utils.hl_read`'s shape
    ({"ok", "data", "error"}) or a fake. Returns (readings by agent, findings):
    a finding is an agent approved by his wallet whose current owner is an
    account outside the cluster — the strongest kind of link this project
    knows, since only the owner can approve an agent. An unreadable answer is
    recorded as unread, never as "owned by nobody" (rule 5)."""
    from src.hl_identity import parse_role
    readings = dict(previous or {})
    findings = []
    for agent, wallet in pairs:
        got = fetch({"type": "userRole", "user": agent})
        if sleep:
            sleep(0.3)
        ok = isinstance(got, dict) and got.get("ok") and isinstance(got.get("data"), dict)
        if not ok:
            readings[agent] = {**(readings.get(agent) or {}), "approved_by": wallet,
                               "checked_ms": now_ms, "read_ok": False,
                               "error": (got or {}).get("error") if isinstance(got, dict) else "no answer"}
            continue
        role = parse_role(got["data"])
        owner = role.get("owner")
        readings[agent] = {"approved_by": wallet, "checked_ms": now_ms, "read_ok": True,
                           "role": role.get("role"), "owner": owner}
        if role.get("role") == "agent" and owner and owner not in cluster:
            findings.append({"agent": agent, "approved_by": wallet, "owner": owner,
                             "why": (f"agent {agent[:10]}… was approved by {wallet[:10]}… and "
                                     f"Hyperliquid now resolves it to {owner[:10]}…")})
    return readings, findings


def build_report(reports: dict, *, state: dict, head: int | None, users: list[str],
                 agent_readings: dict, agent_findings: list[dict], registered: list[str],
                 now_iso: str) -> dict:
    kinds_total: dict[str, int] = defaultdict(int)
    for rep in reports.values():
        for k, n in (rep.get("kinds") or {}).items():
            kinds_total[k] += n
    return {
        "computed_at": now_iso,
        "dataset": {"start_block": rc.START_BLOCK, "start": _stamp(rc.START_TS_MS),
                    "head": head, "swept_to": state.get("swept_to"),
                    "swept_to_ts": _stamp(state.get("swept_to_ts")),
                    "behind_blocks": (head - int(state["swept_to"])) if head and state.get("swept_to") else None,
                    "backfill_segments_done": len(state.get("segments_done") or []),
                    "backfill_complete": bool(state.get("backfill_complete"))},
        "users": users,
        "wallets": reports,
        "kinds_total": dict(sorted(kinds_total.items())),
        "agents_total": sum(len(r.get("agents") or []) for r in reports.values()),
        "outside_total": sum(len(r.get("outside_destinations") or []) for r in reports.values()),
        "agent_owner_readings": agent_readings,
        "agent_reused": agent_findings,
        "registered_outsiders": registered,
        "cctp_domains": {str(k): v for k, v in CCTP_DOMAINS.items()},
    }
