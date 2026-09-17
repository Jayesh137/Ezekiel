#!/usr/bin/env python3
"""Report detectors that have stopped producing readings. See src/feed_health.py.

    python scripts/check_feed_health.py watch   # the watch's feeds (run by trace.yml)
    python scripts/check_feed_health.py other   # everything else (run by watch.yml)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import feed_health, utils


def main(argv: list[str]) -> int:
    from src.alerts import alert_feed_stale

    group = (argv[1] if len(argv) > 1 else "other").lower()
    feeds = feed_health.GROUPS.get(group)
    if feeds is None:
        print(f"[feeds] unknown group {group!r}; expected one of {sorted(feed_health.GROUPS)}")
        return 2
    problems = feed_health.assess(feeds, utils.DATA_DIR)

    # Blindness needs a memory of when it started, so it is kept per group:
    # each group has exactly one writing workflow (watch.yml checks "other",
    # trace.yml checks "watch"), and two writers on one file lose updates.
    state_path = utils.DATA_DIR / "feed_health" / f"{group}.json"
    try:
        with open(state_path) as f:
            since = json.load(f).get("blind_since") or {}
    except (OSError, ValueError, AttributeError):
        since = {}
    blind, since = feed_health.blind(feeds, utils.DATA_DIR, since)
    problems += blind
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump({"blind_since": since}, f, indent=2, sort_keys=True)
    if since:
        print(f"[feeds] {group}: blind but not yet reported: "
              f"{sorted(set(since) - {p['feed'] for p in blind})}")

    print(f"[feeds] {group}: {len(feeds)} feed(s) checked, {len(problems)} problem(s)")
    for p in problems:
        print(f"[feeds]   {p['feed']}: {p['problem']} ({p['path']})")
        alert_feed_stale(p["feed"], p["problem"], p["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
