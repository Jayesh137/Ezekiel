#!/usr/bin/env python3
"""Where did large new money into Hyperliquid accounts come from? trace.yml.

    python scripts/run_provenance.py                # the trace.yml step
    python scripts/run_provenance.py --dry-run DIR  # read-only: writes only DIR, no alerts

Spec §7 of docs/superpowers/specs/2026-10-06-boundary-trace-design.md. Owns the
Bridge2 deposit feed (the correlator's complete bridge pool), queues the
accounts large money just entered plus every lead the project holds, and
resolves each one's funding back to its first boundary. Single writer:
data/provenance/.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import utils  # noqa: E402

FIRST_RUN_BLOCKS = 14 * 345_600      # 14 days of Arbitrum
POOL_DAYS, POOL_MIN_USD = 30, 10_000.0
QUEUE_MIN_USD = 100_000.0
ACCOUNTS_PER_RUN, SECONDS = 30, 150.0
CACHE_TTL_S = 7 * 86400
KEEP_ACCOUNTS, KEEP_FINDINGS = 2000, 500
# Hyperliquid's CCTP extension on Arbitrum: a Circle deposit through it is a
# USDC transfer from the depositor's own address into this contract.
EXTENSION = "0xa95d9c1f655341597c94393fddc30cf3c08e4fce"


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def make_label_of(config: dict, data: Path):
    """What each source is, from what the project already measured (rule 9)."""
    from src.boundary.measure import load_services, measured
    from src.trace import store
    services, hot = load_services(config, data)
    contract, busy, known = measured(data)
    try:
        registry = store.load(data / "trace" / "registry")
    except RuntimeError:
        registry = {}

    def label_of(address: str, chain: str):
        a = (address or "").lower()
        if a in hot:
            return "exchange"
        if a in services:
            return "service"
        if chain == "hyperliquid":
            r = registry.get(a) or {}
            return "busy" if r.get("class") == "hub" or (r.get("hl") or {}).get("hub") else None
        if contract(a):
            return "contract"
        if busy(a):
            return "busy"
        return "quiet" if known(a) else None
    return label_of


def circle_source_reader(budget):
    """The burner behind a Circle deposit: the Arbitrum CCTP extension's own
    USDC transfer of the same amount in the 30 minutes before (keyless). A
    deposit from any other route stays unresolved here — recorded, not guessed."""
    from src.boundary import bridge2, logs

    def resolve(entry: dict, account: str):
        usd, ts = entry.get("usd"), int(entry.get("ts") or 0)
        if not usd or not ts or not budget.can(3):
            return None
        budget.spend()
        try:
            lo = logs.block_at("arbitrum", ts - 1800)
            hi = logs.block_at("arbitrum", ts + 120, closest="after")
            rows = logs.read_logs("arbitrum", bridge2.USDC,
                                  {0: bridge2.TOPIC_TRANSFER, 2: bridge2.topic_address(EXTENSION)},
                                  lo, hi)
        except logs.LogReadError:
            return None
        hits = [r for r in rows if abs(int(r["data"], 16) / 1e6 - usd) <= max(1.0, usd * 0.002)]
        if len(hits) != 1:
            return None
        return {"address": "0x" + hits[0]["topics"][1][-40:].lower(), "chain": "arbitrum"}
    return resolve


def default_readers(config: dict, data: Path, hl_budget) -> dict:
    from scripts.run_trace_engine import paced_post
    from src.boundary import bridge2, logs, readers, unit
    from src.trace import hl_ledger
    budget = readers.Budget(120, seconds=SECONDS)
    post = paced_post(hl_budget)

    def ledger(account):
        rows, error, _complete = hl_ledger.read(account, post, start_ms=0, max_pages=2)
        if error and not rows:
            raise readers.ReadError(error)
        return rows
    return {
        "head": lambda: logs.head_block("arbitrum"),
        "deposits": lambda lo, hi: logs.read_logs("arbitrum", bridge2.USDC,
                                                  bridge2.deposit_topics(), lo, hi),
        "ledger": ledger,
        "unit": lambda a: unit.read_operations(a),
        "inbound": lambda chain, a, since_ts, until_ts: readers.inbound_transfers(
            chain, a, since_ts=since_ts, until_ts=until_ts, budget=budget),
        "first_gas": lambda chain, a: readers.first_gas(chain, a, budget=budget),
        "circle_source": circle_source_reader(budget),
        "label_of": make_label_of(config, data),
    }


def queue(data: Path, fresh: list, previous: dict, core: set) -> list[tuple]:
    """(rank, -usd, account, reason) — fresh money first, then the leads."""
    items: dict[str, tuple] = {}

    def add(account, rank, usd, reason):
        a = (account or "").lower()
        if not a.startswith("0x") or a in core:
            return
        key = (rank, -float(usd or 0))
        if a not in items or key < items[a][:2]:
            items[a] = (*key, a, reason)
    for d in fresh:
        if d["amount"] >= QUEUE_MIN_USD:
            add(d["wallet"], 0, d["amount"], "bridge2 deposit")
    seen_ts = int(previous.get("cctp_seen_ts") or 0)
    for d in _read(data / "correlations" / "cctp_pool.json").get("deposits") or []:
        if int(d.get("ts") or 0) > seen_ts and float(d.get("amount") or 0) >= QUEUE_MIN_USD:
            add(d.get("wallet"), 0, d.get("amount"), "circle deposit")
    for n in _read(data / "newborn" / "latest.json").get("newborn") or []:
        add(n.get("wallet"), 1, n.get("account_value"), "newborn")
    for w in _read(data / "roster" / "latest.json").get("wallets") or []:
        if w.get("tier") in ("POSSIBLE", "PROBABLE"):
            add(w.get("wallet"), 2, 0, f"roster {w.get('tier')}")
    for a in (_read(data / "dormancy" / "latest.json").get("handoffs") or {}):
        add(a, 3, 0, "dormancy handoff")
    for m in _read(data / "correlations" / "latest.json").get("matches") or []:
        if m.get("route") == "route_unknown":
            add(m.get("wallet"), 4, m.get("deposit_amount_usd"), "correlation route unknown")
    return sorted(items.values())


def main(argv=None, *, readers=None, now=None) -> int:
    from src import alerts
    from src.boundary import attribution as at
    from src.boundary import bridge2, logs, provenance, unit
    from src.boundary import perimeter as pm
    from src.hl_budget import ReadBudget
    from src.trace import store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", metavar="DIR")
    args = parser.parse_args(argv)
    config, data = utils.load_config(), utils.DATA_DIR
    now_dt = datetime.fromisoformat(now) if now else datetime.now(UTC)
    now_ts, now_iso = int(now_dt.timestamp()), now_dt.isoformat()
    out_dir = Path(args.dry_run) if args.dry_run else data / "provenance"
    previous = _read(data / "provenance" / "latest.json")
    doc = _read(data / "perimeter" / "latest.json")
    index = pm.Index(doc if doc.get("members") else pm.core_only(config, now_iso))
    started = time.monotonic()

    with ReadBudget(seconds=SECONDS, weight_per_minute=500) as hl_budget:
        readers = readers or default_readers(config, data, hl_budget)

        # 1. The Bridge2 deposit feed -> the correlator's complete pool.
        pool = _read(data / "provenance" / "bridge_deposits.json")
        cursor = pool.get("cursor")
        walk, start = {"logs": [], "last_block": cursor, "error": None}, None
        try:
            head = int(readers["head"]())
            start = int(cursor) + 1 if cursor is not None else max(0, head - FIRST_RUN_BLOCKS)
            walk = logs.walk(readers["deposits"], start, head, max_calls=40, seconds=60)
        except Exception as exc:  # noqa: BLE001 - the head itself was unreadable
            walk["error"] = f"{type(exc).__name__}: {exc}"
        fresh = [{"wallet": d["depositor"], "amount": d["usd"], "ts": d["ts"],
                  "hash": d["tx_hash"], "via": "bridge"}
                 for d in (bridge2.decode_deposit(x) for x in walk["logs"]) if d]
        horizon = now_ts - POOL_DAYS * 86400
        kept = {(d["hash"], d["wallet"]): d for d in (pool.get("deposits") or []) + fresh
                if d["amount"] >= POOL_MIN_USD and int(d["ts"]) >= horizon}
        pool = {"cursor": walk["last_block"], "updated_at": now_iso,
                "deposits": sorted(kept.values(), key=lambda d: d["ts"])}

        # 2. A perimeter member opening its own HL account.
        member_found = at.classify_all([at.from_bridge2_deposit(
            {"depositor": d["wallet"], "usd": d["amount"], "ts": d["ts"], "tx_hash": d["hash"],
             "log_index": 0}) for d in fresh], index)

        # 3. Queue, skipping accounts resolved inside the cache's lifetime.
        cache = {}
        try:
            cache = store.load(data / "provenance" / "accounts")
        except RuntimeError as exc:
            print(f"[provenance] cache unreadable, starting empty: {exc}")
        fresh_wallets = {d["wallet"] for d in fresh if d["amount"] >= QUEUE_MIN_USD}
        todo = [q for q in queue(data, fresh, previous, index.core)
                if q[2] in fresh_wallets or q[2] not in cache
                or now_ts - int((cache.get(q[2]) or {}).get("resolved_at") or 0) >= CACHE_TTL_S]

        # 4. Resolve, one account never stopping the run.
        attempted, unreadable = 0, []
        for _rank, _usd, account, reason in todo:
            if attempted >= ACCOUNTS_PER_RUN or time.monotonic() - started >= SECONDS:
                break
            attempted += 1
            try:
                ledger = readers["ledger"](account)
                unit_events = []
                if any(str(((r or {}).get("delta") or {}).get("token") or "").upper().startswith("U")
                       for r in ledger):
                    unit_events = unit.events(readers["unit"](account))
                record = provenance.resolve(
                    account, ledger=ledger, unit_events=unit_events, index=index,
                    label_of=readers["label_of"], read_inbound=readers["inbound"],
                    read_first_gas=readers["first_gas"], read_ledger=readers["ledger"],
                    circle_source=readers["circle_source"], now_ts=now_ts)
            except Exception as exc:  # noqa: BLE001
                unreadable.append({"account": account, "error": f"{type(exc).__name__}: {exc}"[:200]})
                continue
            record["reason"] = reason
            cache[account] = record

    # 5. Findings and alerts.
    found = [f for rec in cache.values() for f in provenance.findings(rec)]
    alerted = list(previous.get("alerted") or [])
    undelivered = []
    queue_rows = [r for r in previous.get("undelivered") or [] if r.get("key") not in alerted]
    queue_rows += [f for f in found + member_found
                   if f.get("severity") in at.SEVERITIES and f["key"] not in alerted
                   and f["key"] not in {r.get("key") for r in queue_rows}]
    for row in queue_rows if not args.dry_run else []:
        if row.get("kind") == "provenance_touches_his_world":
            ok = alerts.alert_provenance_hit(row)
        else:
            ok = alerts.alert_boundary_finding(row)
        if ok:
            alerted.append(row["key"])
        else:
            undelivered.append(row)

    # 6. Persist.
    keep = dict(sorted(cache.items(), key=lambda kv: -int(kv[1].get("resolved_at") or 0))
                [:KEEP_ACCOUNTS])
    recent = sorted(keep.values(), key=lambda r: -int(r.get("resolved_at") or 0))[:50]
    blocks_read = (walk["last_block"] - start + 1
                   if start is not None and walk["last_block"] is not None else 0)
    cctp_ts = [int(d.get("ts") or 0) for d in
               _read(data / "correlations" / "cctp_pool.json").get("deposits") or []]
    report = {
        "computed_at": now_iso, "deposit_cursor": walk["last_block"],
        "blocks_read": max(0, blocks_read), "deposits_read": len(fresh),
        "read_error": walk.get("error"), "queue": len(todo), "attempted": attempted,
        "resolved": attempted - len(unreadable), "unreadable_accounts": unreadable[:50],
        "findings": sorted(found, key=lambda f: -int(f.get("ts") or 0))[:KEEP_FINDINGS],
        "member_findings": member_found[:100],
        "alerted": sorted(set(alerted)), "undelivered": undelivered,
        "shared_funders": provenance.shared_funders(keep)[:200],
        "cctp_seen_ts": max([int(previous.get("cctp_seen_ts") or 0)] + cctp_ts),
        "recent": [{"account": r["account"], "verdict": r.get("verdict"), "reason": r.get("reason"),
                    "routes": sorted({e["route"] for e in r.get("entries") or []}),
                    "usd": round(sum(e["usd"] or 0 for e in r.get("entries") or []), 2),
                    "hop1": [{k: s.get(k) for k in ("address", "class", "role", "family", "usd",
                                                    "chain")}
                             for s in r.get("sources") or [] if s.get("hop") == 1][:4],
                    "resolved_at": r.get("resolved_at")} for r in recent],
        "counts": {"accounts_cached": len(keep), "findings": len(found),
                   "verdicts": {v: sum(r.get("verdict") == v for r in keep.values())
                                for v in ("touches_his_world", "same_exchange", "exchange",
                                          "unresolved", "unrelated")}},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    utils.atomic_write_json(out_dir / "bridge_deposits.json", pool)
    store.save(out_dir / "accounts", keep)
    utils.atomic_write_json(out_dir / "latest.json", report)
    print(f"[provenance] {len(fresh)} deposit(s) read to block {walk['last_block']}; queue "
          f"{len(todo)}, resolved {report['resolved']}/{attempted}; findings {len(found)}; "
          f"verdicts {report['counts']['verdicts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
