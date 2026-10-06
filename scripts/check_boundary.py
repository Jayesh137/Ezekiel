#!/usr/bin/env python3
"""Attribute value crossing Hyperliquid's edge to his world. watch.yml.

    python scripts/check_boundary.py                # the watch.yml step
    python scripts/check_boundary.py --dry-run DIR  # read-only: writes only DIR, no alerts

Spec §6 of docs/superpowers/specs/2026-10-06-boundary-trace-design.md. Four
sources, each independent of the others (a failing one costs only its own
reading): every Bridge2 withdrawal since the cursor; each core account's whole
Bridge2 withdrawal history (one call each — `user` is indexed); Unit operations
of perimeter members (rotating); and the core ledgers the trace engine already
walked. Plus retro, bounded and resumable: who ever withdrew to a member.
Single writer: data/boundary/.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import utils  # noqa: E402

FIRST_RUN_BLOCKS = 345_600      # ~1 day of Arbitrum at ~4 blocks/s
WALK_CALLS, WALK_SECONDS = 12, 60.0
UNIT_PER_RUN = 8
RETRO_MEMBERS_PER_RUN = 4
RETRO_TX_PER_MEMBER = 8
KEEP_FINDINGS = 500
RETRO_MIN_WEIGHT = 0.6


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def default_readers() -> dict:
    from src.boundary import bridge2, logs, readers, unit
    budget = readers.Budget(40, seconds=90)

    def payouts(member):
        topics = {0: bridge2.TOPIC_TRANSFER, 1: bridge2.topic_address(bridge2.BRIDGE),
                  2: bridge2.topic_address(member)}
        return logs.read_logs("arbitrum", bridge2.USDC, topics, 0, "latest")
    return {
        "head": lambda: logs.head_block("arbitrum"),
        "withdrawals": lambda lo, hi: logs.read_logs("arbitrum", bridge2.BRIDGE,
                                                     bridge2.withdrawal_topics(), lo, hi),
        "user_withdrawals": lambda u: logs.read_logs("arbitrum", bridge2.BRIDGE,
                                                     bridge2.withdrawal_topics(u), 0, "latest"),
        "payouts": payouts,
        "tx_logs": lambda tx: readers.tx_logs("arbitrum", tx, budget=budget),
        "unit": lambda a: unit.read_operations(a),
    }


def hubs(data: Path) -> set:
    from src.trace import store
    try:
        registry = store.load(data / "trace" / "registry")
    except RuntimeError:
        return set()
    return {a for a, r in registry.items()
            if r.get("class") == "hub" or (r.get("hl") or {}).get("hub")}


def core_edges(data: Path, core: set) -> list[dict]:
    from src.trace import store
    try:
        stored = store.load(data / "trace" / "hl_edges")
    except RuntimeError:
        return []
    return [e for owner in sorted(core) for e in stored.get(owner, [])]


def main(argv=None, *, readers=None, now=None) -> int:
    from src import alerts
    from src.boundary import attribution as at
    from src.boundary import bridge2, logs, unit
    from src.boundary import perimeter as pm

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", metavar="DIR")
    args = parser.parse_args(argv)
    readers = readers or default_readers()
    config, data = utils.load_config(), utils.DATA_DIR
    now_iso = now or datetime.now(UTC).isoformat()
    out_dir = Path(args.dry_run) if args.dry_run else data / "boundary"
    previous = _read(data / "boundary" / "latest.json")
    doc = _read(data / "perimeter" / "latest.json")
    fallback = not doc.get("members")
    if fallback:
        doc = pm.core_only(config, now_iso)
    index = pm.Index(doc)
    errors, events = [], []

    # 1. Every Bridge2 withdrawal since the cursor.
    cursor = previous.get("withdrawal_cursor")
    walk = {"logs": [], "last_block": cursor, "error": None}
    head = start = None
    try:
        head = int(readers["head"]())
        start = int(cursor) + 1 if cursor is not None else max(0, head - FIRST_RUN_BLOCKS)
        walk = logs.walk(readers["withdrawals"], start, head, max_calls=WALK_CALLS,
                         seconds=WALK_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the head itself was unreadable
        walk["error"] = f"{type(exc).__name__}: {exc}"
    live = [r for r in (bridge2.decode_withdrawal(x) for x in walk["logs"]) if r]
    first_run = cursor is None
    events += [at.from_bridge2_withdrawal(r, retro=first_run) for r in live]

    # 2. Each core account's whole withdrawal history (exact, by nonce).
    known = dict(previous.get("core_withdrawals") or {})
    own_txs = set()
    for user in sorted(index.core):
        try:
            rows = [r for r in (bridge2.decode_withdrawal(x)
                                for x in readers["user_withdrawals"](user)) if r]
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "core_withdrawals", "address": user, "error": str(exc)[:200]})
            continue
        for r in rows:
            own_txs.add(r["tx_hash"])
            fresh = str(r["nonce"]) not in known
            known[str(r["nonce"])] = {k: r[k] for k in ("user", "destination", "usd", "ts", "tx_hash")}
            events.append(at.from_bridge2_withdrawal(r, retro=first_run or not fresh))

    # 3. Unit operations of perimeter members, least recently checked first.
    unit_checked = dict(previous.get("unit_checked") or {})
    members = sorted(index.members.values(),
                     key=lambda m: (unit_checked.get(m["address"]) or "", m["address"]))
    for m in [m for m in members if m["weight"] >= RETRO_MIN_WEIGHT][:UNIT_PER_RUN]:
        try:
            got = unit.events(readers["unit"](m["address"]))
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "unit", "address": m["address"], "error": str(exc)[:200]})
            continue
        seen_before = m["address"] in unit_checked
        unit_checked[m["address"]] = now_iso
        events += [{**e, "retro": not seen_before} for e in got]

    # 4. The core ledgers the trace engine walked (HL-native sends).
    edge_cursor = int(previous.get("hl_edges_cursor") or 0)
    edges = core_edges(data, index.core)
    for e in at.from_hl_edges(edges, index.core, exclude=hubs(data)):
        events.append({**e, "retro": not edge_cursor or int(e.get("ts") or 0) <= edge_cursor})
    new_edge_cursor = max([edge_cursor] + [int(e.get("ts") or 0) for e in edges])

    # 5. Retro: who ever withdrew to a member (bounded, resumable).
    retro = dict(previous.get("retro") or {})
    pending = [m for m in index.members.values()
               if m["weight"] >= RETRO_MIN_WEIGHT and str(m["address"]).startswith("0x")
               and m["role"] != "core" and not (retro.get(m["address"]) or {}).get("bridge2")]
    for m in sorted(pending, key=lambda m: m["address"])[:RETRO_MEMBERS_PER_RUN]:
        try:
            payouts = readers["payouts"](m["address"])
            for tx in sorted({(p.get("transactionHash") or "").lower() for p in payouts}
                             - own_txs)[:RETRO_TX_PER_MEMBER]:
                for r in (bridge2.decode_withdrawal(x) for x in readers["tx_logs"](tx)):
                    if r and r["destination"] == m["address"]:
                        events.append(at.from_bridge2_withdrawal(r, retro=True))
            retro.setdefault(m["address"], {})["bridge2"] = now_iso
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "retro", "address": m["address"], "error": str(exc)[:200]})

    # Judge, merge, alert.
    found = at.classify_all(events, index)
    by_key = {f["key"]: f for f in previous.get("findings") or []}
    new = [f for f in found if f["key"] not in by_key]
    for f in found:
        by_key[f["key"]] = f
    alerted = list(previous.get("alerted") or [])
    undelivered = []
    queue = [r for r in previous.get("undelivered") or [] if r.get("key") not in alerted]
    queue += [f for f in new if f["severity"] in at.SEVERITIES]
    for row in queue if not args.dry_run else []:
        if row["kind"] == at.KIND_HIS_ACCOUNT_PAID_OUTSIDE:
            when = (datetime.fromtimestamp(int(row["ts"]), tz=UTC).isoformat()
                    if row.get("ts") else None)
            kind = "withdraw3" if row["source"] == "bridge2" else f"{row['source']}_withdrawal"
            ok = alerts.alert_foreign_destination(
                row["hl_account"], kind, row["counterparty"], row.get("amount_usd"),
                row.get("asset") or "USDC", when, row.get("ref"), chain=row.get("chain"))
        else:
            ok = alerts.alert_boundary_finding(row)
        if ok:
            alerted.append(row["key"])
        else:
            undelivered.append(row)

    findings = sorted(by_key.values(), key=lambda f: -int(f.get("ts") or 0))[:KEEP_FINDINGS]
    counts = {"findings": len(findings), "new": len(new),
              "critical": sum(f["severity"] == "CRITICAL" for f in findings),
              "high": sum(f["severity"] == "HIGH" for f in findings),
              "events": len(events)}
    blocks_read = (walk["last_block"] - start + 1
                   if start is not None and walk["last_block"] is not None else 0)
    state = {"computed_at": now_iso, "perimeter_fallback": fallback, "head_block": head,
             "withdrawal_cursor": walk["last_block"], "blocks_read": max(0, blocks_read),
             "withdrawals_read": len(live), "read_error": walk.get("error"),
             "core_withdrawals": known, "hl_edges_cursor": new_edge_cursor,
             "findings": findings, "alerted": sorted(set(alerted)), "undelivered": undelivered,
             "unit_checked": unit_checked, "retro": retro, "counts": counts,
             "errors": errors[:50]}
    out_dir.mkdir(parents=True, exist_ok=True)
    utils.atomic_write_json(out_dir / "latest.json", state)
    print(f"[boundary] {len(live)} withdrawal(s) read to block {walk['last_block']}"
          f"{' (error: ' + walk['error'] + ')' if walk.get('error') else ''}; "
          f"{len(events)} event(s), {len(new)} new finding(s); {len(errors)} error(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
