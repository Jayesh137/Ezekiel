#!/usr/bin/env python3
"""Sample every fill on the venue for a few minutes and fold the taker population.

Writes `data/tape/venue_population.json` (the bounded per-taker state) and
`data/tape/venue_latest.json` (the report: populations, outcome-market traders,
program-shaped takers venue-wide, outcome traders present in his treasury
sessions). Outcome traders in two or more of his sessions register as candidates
(`outcome_treasury`); program-shaped takers register as `venue_program` so the
execution-program detector reads their fills. No vote, no alert.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import fills_feed as ff  # noqa: E402
from src import venue_sample as vs  # noqa: E402
from src.utils import DATA_DIR, atomic_write_json, load_config  # noqa: E402

OUT_DIR = DATA_DIR / "tape"
STATE_FILE = OUT_DIR / "venue_population.json"
REPORT_FILE = OUT_DIR / "venue_latest.json"
DEFAULT_MINUTES = 3.0
MAX_REQUESTS = 12
MAX_REGISTER = 25


def load_state() -> dict:
    try:
        doc = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("wallets"), dict):
            return doc
    except (OSError, ValueError):
        pass
    return vs.empty_state()


def load_treasury_state() -> dict | None:
    try:
        doc = json.loads((DATA_DIR / "replica" / "treasury_census.json").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def register(rows: list[dict], source: str, keys: tuple) -> list[str]:
    from src.candidate_registry import observe_candidate, valid_wallet
    out = []
    for row in rows[:MAX_REGISTER]:
        try:
            wallet = valid_wallet(row["wallet"])
        except (ValueError, KeyError):
            continue
        observe_candidate(wallet, {"source": source, "positive": True, "event_id": f"{source}:{wallet}",
                                   "detail": {k: row.get(k) for k in keys}})
        out.append(wallet)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=DEFAULT_MINUTES)
    args = parser.parse_args()
    config = load_config()
    cluster = {str(config.get("target_wallet") or "").lower()}
    cluster |= {str(w).lower() for w in config.get("known_self_wallets") or []}
    state = load_state()
    seen: dict = {}
    error = None
    try:
        head = ff.head()
        start = max(ff.START_BLOCK, head - ff.blocks_for_minutes(args.minutes))
        out = ff.stream(start, head, max_requests=MAX_REQUESTS)
        seconds = args.minutes * 60 if out["complete"] else (
            (out["last_block"] - start + 1) / ff.BLOCKS_PER_MINUTE * 60 if out["last_block"] else 0)
        seen = vs.fold_sample(state, out["fills"], now_ms=int(time.time() * 1000), seconds=seconds)
        seen.update({"requests": out["requests"], "bytes": out["bytes"], "complete": out["complete"]})
        print(f"[venue] {seen['fills']} fills, {seen['takers']} takers, {seen['coins']} coins, "
              f"{seen['outcome_takers']} outcome, {seen['program_takers']} program-shaped "
              f"in {out['requests']} request(s), {out['bytes']} bytes")
    except ff.FeedError as exc:
        error = str(exc)[:200]
        print(f"[venue] feed unavailable: {exc}")
    vs.cap(state)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE_FILE, state, sort_keys=True, compact=True)

    programs = vs.program_takers(state)
    outcome = vs.outcome_traders(state)
    joined = vs.outcome_in_his_sessions(state, load_treasury_state(), cluster)
    registered = {"venue_program": register([r for r in programs if r["wallet"] not in cluster],
                                            "venue_program", ("program_runs", "program_coins", "fills")),
                  "outcome_treasury": register(joined, "outcome_treasury",
                                               ("outcome_fills", "sessions_with", "controls_with", "coins"))}
    report = {"computed_at": datetime.now(UTC).isoformat(), "sample": seen, "error": error,
              "summary": vs.summary(state), "program_takers": programs[:25],
              "outcome_traders": len(outcome), "outcome_top": outcome[:15],
              "outcome_in_his_sessions": joined[:25], "registered": registered}
    atomic_write_json(REPORT_FILE, report)
    s = report["summary"]
    print(f"[venue] population {s['wallets']} takers over {s['samples']} samples ({s['seconds']:.0f}s); "
          f"{s['outcome_traders']} outcome traders, {s['program_takers']} program-shaped, "
          f"{len(joined)} outcome traders in his treasury sessions; registered {registered}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
