#!/usr/bin/env python3
"""Who does treasury work when he does — fold his sessions into the co-occurrence census.

His sessions come from the target's own stored non-trading actions
(`data/actions/<target>/`, explorer and replica rows alike) and his ledger
deposits (`data/ledger/`), clustered by `treasury_sessions.SESSION_GAP_MS`. Each
unread session is read venue-wide from SQD's replica-command feed
(`src/replica_cmds.py`, treasury kinds only, status ok) over its padded window
and two control windows (±7 days), then folded into
`data/replica/treasury_census.json`. Resumable by session key; bounded per run.

His known wallets are measured like strangers — positive controls — and the
report names what the census read for each. Hits register as candidates
(`treasury_cooccurrence`); nothing here mints a vote.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import replica_cmds as rc  # noqa: E402
from src import treasury_sessions as ts  # noqa: E402
from src.utils import DATA_DIR, atomic_write_json, load_all_records, load_config  # noqa: E402

OUT_DIR = DATA_DIR / "replica"
STATE_FILE = OUT_DIR / "treasury_census.json"
REPORT_FILE = OUT_DIR / "treasury_latest.json"
DEFAULT_MAX_SESSIONS = 3
DEFAULT_TIME_BUDGET = 150.0
# Reads per window are data-bound (~2,900 blocks a request); a 30-minute window is
# ~25,000 blocks. Cap a window's requests so one busy hour cannot eat the run.
MAX_REQUESTS_PER_WINDOW = 60


def load_state() -> dict:
    try:
        doc = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("wallets") is not None:
            return doc
    except (OSError, ValueError):
        pass
    return ts.empty_state()


def his_actions(target: str) -> list[dict]:
    rows = load_all_records(str(DATA_DIR / "actions" / target))
    return [r for r in rows if isinstance(r, dict) and r.get("type")]


def his_ledger() -> list[dict]:
    return [r for r in load_all_records(str(DATA_DIR / "ledger")) if isinstance(r, dict)]


def read_window(lo_ms: int, hi_ms: int, *, block_at=rc.block_at, stream=rc.stream) -> dict:
    """Every treasury-kind action venue-wide between two timestamps (ms)."""
    a = block_at(lo_ms)
    b = max(a, block_at(hi_ms) - 1)
    out = stream(a, b, action_types=ts.WINDOW_TYPES, status="ok",
                 max_requests=MAX_REQUESTS_PER_WINDOW)
    if not out["complete"]:
        raise rc.FeedError(f"window {lo_ms}-{hi_ms} read short at {out['last_block']} "
                           f"after {out['requests']} requests ({out['overloads']} refused)")
    return out


def fold_sessions(state: dict, pending: list[dict], cluster: set, *, dataset_start_ms: int,
                  head_ms: int, now_ms: int, max_sessions: int, time_budget: float,
                  read=read_window, log=print, checkpoint=None) -> dict:
    """Read and fold up to `max_sessions` unread sessions within the budget.
    `checkpoint(state)` is called after each fold, so a killed run keeps what it read."""
    t0 = time.time()
    folded, failed = [], []
    for session in pending:
        if len(folded) >= max_sessions or time.time() - t0 > time_budget:
            break
        lo, hi = ts.window(session)
        if hi > head_ms:
            continue                      # the dataset has not reached it yet
        controls = ts.control_windows(session, dataset_start_ms, head_ms)
        try:
            rows = read(lo, hi)["actions"]
            control_rows = [read(a, b)["actions"] for a, b in controls]
        except rc.FeedError as exc:
            failed.append({"key": session["key"], "error": str(exc)[:200]})
            log(f"[treasury] session {session['key']} unreadable: {exc}")
            continue
        contribution = ts.fold_session(state, session, rows, control_rows, cluster, now_ms)
        folded.append({"key": session["key"], **contribution, "rows": len(rows)})
        if checkpoint is not None:
            checkpoint(state)
        log(f"[treasury] session {session['key']} ({datetime.fromtimestamp(session['start'] / 1000, tz=UTC):%Y-%m-%d %H:%M}, "
            f"{session['n']} act(s)): {len(rows)} venue rows, {contribution.get('present')} present, "
            f"{contribution.get('control_present')} in {len(control_rows)} control(s)")
    return {"folded": folded, "failed": failed, "seconds": round(time.time() - t0, 1)}


def known_readings(state: dict, known: set) -> dict:
    n, c = int(state.get("sessions") or 0), int(state.get("controls") or 0)
    out = {}
    for w in sorted(known):
        e = (state.get("wallets") or {}).get(w)
        if not e:
            out[w] = {"sessions_with": 0, "controls_with": 0}
            continue
        lf, pv = ts.lift(e, n, c), ts.p_value(e, n, c)
        out[w] = {"sessions_with": int(e.get("s") or 0), "controls_with": int(e.get("c") or 0),
                  "lift": None if lf is None else round(lf, 2),
                  "p": None if pv is None else float(f"{pv:.3g}"), "hit": ts.is_hit(e, n, c)}
    return out


def register_hits(hits: list[dict]) -> list[str]:
    from src.candidate_registry import observe_candidate, valid_wallet
    registered = []
    for hit in hits:
        try:
            wallet = valid_wallet(hit["wallet"])
        except (ValueError, KeyError):
            continue
        observe_candidate(wallet, {"source": "treasury_cooccurrence", "positive": True,
                                   "event_id": f"treasury:{wallet}",
                                   "detail": {k: hit.get(k) for k in ("sessions_with", "controls_with",
                                                                      "sessions", "controls", "lift",
                                                                      "p", "kinds")}})
        registered.append(wallet)
    return registered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    parser.add_argument("--time-budget", type=float, default=DEFAULT_TIME_BUDGET)
    args = parser.parse_args()

    config = load_config()
    target = str(config.get("target_wallet") or "").lower()
    known = {str(w).lower() for w in config.get("known_self_wallets") or []}
    cluster = {target}            # HIS sessions are the target's alone; known wallets are controls
    state = load_state()
    events = ts.target_events(his_actions(target), his_ledger(), rc.START_TS_MS)
    all_sessions = ts.sessions(events)
    pending = [s for s in all_sessions if s["key"] not in state["done"]]
    now_ms = int(time.time() * 1000)
    sweep: dict = {}
    head_ms = None
    try:
        head_block = rc.head()
        head_ms = rc.stream(head_block - 50, head_block, users=[target], max_requests=3)["last_ts"]
        if head_ms is None:
            raise rc.FeedError("head timestamp unreadable")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        sweep = fold_sessions(state, pending, cluster, dataset_start_ms=rc.START_TS_MS,
                              head_ms=head_ms, now_ms=now_ms, max_sessions=args.max_sessions,
                              time_budget=args.time_budget,
                              checkpoint=lambda st: atomic_write_json(STATE_FILE, st, sort_keys=True,
                                                                      compact=True))
    except rc.FeedError as exc:
        sweep = {"error": str(exc)[:200], "folded": [], "failed": []}
        print(f"[treasury] feed unavailable: {exc}")
    ts.cap(state)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE_FILE, state, sort_keys=True, compact=True)

    ranked = ts.screen(state, exclude=cluster, limit=50)
    hits = [r for r in ranked if r["hit"] and r["wallet"] not in known]
    registered = register_hits(hits)
    report = {
        "computed_at": datetime.now(UTC).isoformat(),
        "sessions_total": len(all_sessions), "sessions_read": int(state.get("sessions") or 0),
        "sessions_pending": len(pending) - len(sweep.get("folded") or []),
        "events": len(events), "head_ms": head_ms,
        "summary": ts.summary(state, hits, known_readings(state, known)),
        "known_wallets": known_readings(state, known),
        "top": ranked[:25], "hits": hits, "registered": registered,
        "run": sweep,
        "bar": {"min_sessions": ts.MIN_SESSIONS, "min_lift": ts.MIN_LIFT, "max_p": ts.MAX_P,
                "session_gap_min": ts.SESSION_GAP_MS // 60_000, "pad_min": ts.PAD_MS // 60_000,
                "bot_actions": ts.BOT_ACTIONS},
    }
    atomic_write_json(REPORT_FILE, report)
    s = report["summary"]
    print(f"[treasury] {report['sessions_read']}/{report['sessions_total']} sessions read "
          f"({report['sessions_pending']} pending), {s['controls']} controls, {s['wallets_seen']} wallets "
          f"seen, {s['wallets_with_2plus']} in 2+ sessions, {len(hits)} hit(s)")
    for w, r in report["known_wallets"].items():
        print(f"  known {w[:12]}… sessions_with={r['sessions_with']} controls_with={r['controls_with']} "
              f"lift={r.get('lift')} p={r.get('p')}")
    for r in ranked[:8]:
        print(f"  {r['wallet']} s={r['sessions_with']}/{r['sessions']} c={r['controls_with']}/{r['controls']} "
              f"lift={r['lift']} p={r['p']} {'HIT' if r['hit'] else ''} {r['kinds']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
