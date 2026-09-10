#!/usr/bin/env python3
"""Fresh accounts on the leaderboard, ranked by size, no per-wallet calls.

Runs before the behavioural sweep so the newest large accounts become
priority targets, and diffs the whole leaderboard against the addresses seen
before so a first appearance is on record with a date.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_newborn_whale
from src.newborn import NEWBORN_DIR, build_report, save
from src.scanner import fetch_leaderboard

# Born within a week and already this large: worth a message on its own.
ALERT_VALUE_USD = 10_000_000.0
MAX_ALERTS = 3


def _seen() -> dict:
    try:
        with open(NEWBORN_DIR / "seen.json") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    rows = fetch_leaderboard()
    if not rows:
        print("[newborn] leaderboard unreadable — nothing judged this run")
        return 0
    report, seen = build_report(rows, _seen())
    save(report, seen)
    born = report["newborn"]
    print(f"[newborn] {report['leaderboard_rows']} rows, {report['first_seen_this_run']} "
          f"first-seen address(es), {len(born)} account(s) >= "
          f"${report['min_value_usd']:,.0f} born within a month")
    for b in born[:10]:
        print(f"[newborn]   {b['wallet']} ${b['account_value']:>14,.0f} born<{b['age']} "
              f"vol ${b['all_time_volume']:,.0f} {b.get('display_name') or ''}")
    sent = 0
    for b in born:
        if sent >= MAX_ALERTS:
            break
        if b["age"] == "week" and b["account_value"] >= ALERT_VALUE_USD:
            if alert_newborn_whale(b["wallet"], b["account_value"], b["age"],
                                   b["all_time_volume"], b.get("display_name")):
                sent += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
