#!/usr/bin/env python3
"""Sweep his world's non-trading actions from SQD's replica-command feed.

Default: incremental. Reads from the stored cursor (`data/replica/state.json`,
`swept_to`) towards the dataset head, bounded by `--max-requests` and
`--time-budget`, and never past an unread range. `--backfill` sweeps the whole
dataset from 2026-01-21 in parallel segments, resumable; `--ingest FILE` folds
rows a local backfill driver wrote (JSON lines of `replica_cmds` rows).

One writer of `data/replica/`; appends to `data/actions/<wallet>/` through
`hl_actions.record` with keys `replica:<block>:<index>` (the explorer's rows keep
their hash keys; `replica_sweep.dedupe` folds the two views at read time).
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import replica_cmds as rc  # noqa: E402
from src import replica_sweep as rs  # noqa: E402
from src.hl_actions import ACTIONS_DIR, own_actions, record  # noqa: E402
from src.utils import (  # noqa: E402
    DATA_DIR,
    atomic_write_json,
    load_all_records,
    load_config,
    save_latest,
)

OUT_DIR = DATA_DIR / "replica"
STATE_FILE = OUT_DIR / "state.json"
DEFAULT_MAX_REQUESTS = 200
DEFAULT_TIME_BUDGET = 150
CHUNK_REQUESTS = 25
BACKFILL_SEGMENT = 2_000_000


def load_state() -> dict:
    try:
        doc = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE_FILE, state)


def previous_report() -> dict:
    try:
        doc = json.loads((OUT_DIR / "latest.json").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def store_rows(rows: list[dict]) -> dict[str, int]:
    """Append replica rows to the actions store, per wallet. Returns added counts."""
    added = {}
    for wallet, acts in rs.by_wallet(rows).items():
        added[wallet] = record(wallet, acts)
    return added


def stored_history(wallet: str) -> list[dict]:
    """Every stored non-trading action of a wallet, explorer and replica rows alike."""
    rows = load_all_records(str(ACTIONS_DIR / wallet))
    # Explorer rows were stored already parsed (own_actions shape); keep them.
    parsed = [r for r in rows if "type" in r and "action" in r]
    raw = [r for r in rows if "type" not in r and isinstance(r.get("action"), dict)]
    if raw:
        parsed.extend(own_actions(raw, wallet))
    return parsed


def ingest_file(path: Path) -> tuple[int, dict[str, int]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("user") and isinstance(row.get("action"), dict):
                    rows.append(row)
    return len(rows), store_rows(rows)


def incremental(users: list[str], state: dict, *, max_requests: int, time_budget: float,
                head=None, stream=None, store=None, save=None, clock=time.time) -> dict:
    """Read from the cursor towards the head, bounded; the cursor moves only past
    blocks read in full. `head`/`stream`/`store`/`save` are injectable for tests."""
    head_fn, stream_fn = head or rc.head, stream or rc.stream
    store_fn, save_fn = store or store_rows, save or save_state
    head = head_fn()
    start = int(state.get("swept_to") or rc.START_BLOCK - 1) + 1
    if start > head:
        return {"head": head, "requests": 0, "rows": 0, "complete": True, "from": start}
    t0 = clock()
    total_rows = 0
    requests_made = overloads = 0
    complete = False
    cursor = start
    while cursor <= head and requests_made < max_requests and clock() - t0 < time_budget:
        chunk = min(head, cursor + rc.BLOCKS_PER_REQUEST * 20)
        # A chunk is bounded in REQUESTS as well as blocks, because the time budget
        # is checked only between chunks: with ~40% of requests refused and waited
        # on, 20 blocks' worth of pages can cost far more than 20 requests.
        out = stream_fn(cursor, chunk, users=users, action_types=rc.NON_TRADING_TYPES,
                        max_requests=max(1, min(CHUNK_REQUESTS, max_requests - requests_made)))
        requests_made += out["requests"]
        overloads += out["overloads"]
        if out["last_block"] is None:
            break
        added = store_fn(out["actions"])
        total_rows += len(out["actions"])
        state["swept_to"] = out["last_block"]
        state["swept_to_ts"] = out["last_ts"]
        state["head_seen"] = head
        save_fn(state)
        cursor = out["last_block"] + 1
        if not out["complete"]:
            break
        if added:
            print(f"[replica] blocks {out['last_block']}: {len(out['actions'])} row(s) "
                  f"{dict(added)}")
    complete = cursor > head
    return {"head": head, "requests": requests_made, "overloads": overloads, "rows": total_rows,
            "complete": complete, "from": start, "to": state.get("swept_to")}


def backfill(users: list[str], state: dict, *, workers: int, max_segments: int | None) -> dict:
    head = rc.head()
    done = {tuple(s) for s in state.get("segments_done") or []}
    counts = {"rows": 0, "requests": 0, "segments": 0}

    def on_segment(seg, res):
        rows = res.get("actions") or []
        if res.get("complete"):
            store_rows(rows)
            done.add(tuple(seg))
            state["segments_done"] = sorted(done)
            state["backfill_head"] = head
            save_state(state)
        counts["rows"] += len(rows)
        counts["requests"] += int(res.get("requests") or 0)
        counts["segments"] += 1
        print(f"[replica] segment {seg} complete={res.get('complete')} rows={len(rows)} "
              f"requests={res.get('requests')} overloads={res.get('overloads')} "
              f"error={res.get('error')}", flush=True)

    segs = [s for s in rc.segments(rc.START_BLOCK, head, BACKFILL_SEGMENT) if s not in done]
    if max_segments is not None:
        segs = segs[:max_segments]
    skip = {s for s in rc.segments(rc.START_BLOCK, head, BACKFILL_SEGMENT) if s not in segs}
    out = rc.sweep_parallel(rc.START_BLOCK, head, segment_blocks=BACKFILL_SEGMENT, workers=workers,
                            users=users, action_types=rc.NON_TRADING_TYPES, skip=skip,
                            on_segment=on_segment, max_overloads=20)
    all_segments = rc.segments(rc.START_BLOCK, head, BACKFILL_SEGMENT)
    if all(tuple(s) in done for s in all_segments):
        state["backfill_complete"] = True
        # The incremental cursor takes over from the backfill's head.
        if int(state.get("swept_to") or 0) < head:
            state["swept_to"] = head
        save_state(state)
    return {"head": head, **counts, "incomplete": out["incomplete"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-segments", type=int, default=None)
    parser.add_argument("--ingest", type=str, default=None, help="JSON lines of replica rows")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--time-budget", type=float, default=DEFAULT_TIME_BUDGET)
    parser.add_argument("--no-agent-check", action="store_true")
    args = parser.parse_args()

    config = load_config()
    perimeter = _json(DATA_DIR / "perimeter" / "latest.json")
    roster = _json(DATA_DIR / "roster" / "latest.json")
    users = rs.world_users(config, perimeter, roster)
    cluster = rs.cluster_of(config)
    members = {rs._low(m) for m in (perimeter.get("members") or {})}
    ignore = {rs._low(a) for a in config.get("excluded_addresses") or []}
    ignore |= {rs._low(a) for a in config.get("hl_shared_destinations") or []}
    state = load_state()
    previous = previous_report()
    print(f"[replica] {len(users)} users in his world; cursor {state.get('swept_to')} "
          f"backfill_complete={bool(state.get('backfill_complete'))}")

    sweep: dict = {}
    try:
        if args.ingest:
            n, added = ingest_file(Path(args.ingest))
            sweep = {"ingested_rows": n, "added": added}
            print(f"[replica] ingested {n} row(s): {added}")
        elif args.backfill:
            sweep = backfill(users, state, workers=args.workers, max_segments=args.max_segments)
            print(f"[replica] backfill: {sweep}")
        else:
            sweep = incremental(users, state, max_requests=args.max_requests,
                                time_budget=args.time_budget)
            print(f"[replica] incremental: {sweep}")
    except rc.FeedError as exc:
        sweep = {"error": str(exc)}
        print(f"[replica] feed unavailable: {exc}")

    reports = {}
    for wallet in users:
        history = stored_history(wallet)
        if history:
            reports[wallet] = rs.wallet_report(wallet, history, members, cluster, ignore)

    now_ms = int(time.time() * 1000)
    readings = previous.get("agent_owner_readings") or {}
    findings = list(previous.get("agent_reused") or [])
    if not args.no_agent_check:
        from src.alerts import alert_explicit_link
        from src.utils import hl_read
        due = rs.agents_to_check({w: r for w, r in reports.items() if w in cluster}, readings, now_ms)
        readings, fresh = rs.check_agent_owners(due, cluster, hl_read, now_ms, readings, sleep=time.sleep)
        known = {(f["agent"], f["owner"]) for f in findings}
        for f in fresh:
            if (f["agent"], f["owner"]) not in known:
                findings.append({**f, "found_at": datetime.now(UTC).isoformat()})
            alert_explicit_link("agent_reused", f["owner"], f["approved_by"], f["why"])
            print(f"[replica] AGENT REUSED: {f['why']}")
        print(f"[replica] agent owners: {len(due)} read, {len(fresh)} reused")

    registered = []
    from src.candidate_registry import observe_candidate
    for dest, row in rs.outside_addresses(reports).items():
        if dest in users:
            continue
        observe_candidate(dest, {"source": "replica_actions", "positive": True,
                                 "event_id": f"replica:{row.get('from')}:{dest}",
                                 "detail": {k: row.get(k) for k in ("type", "chain", "amount",
                                                                    "token", "time", "from")}})
        registered.append(dest)

    report = rs.build_report(reports, state=state, head=sweep.get("head") or state.get("head_seen"),
                             users=users, agent_readings=readings, agent_findings=findings,
                             registered=registered, now_iso=datetime.now(UTC).isoformat())
    report["sweep"] = sweep
    save_latest(str(OUT_DIR), report)
    print(f"[replica] {len(reports)} wallet(s) with history, {report['agents_total']} agent(s), "
          f"{report['outside_total']} outside destination(s), {len(registered)} registered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
