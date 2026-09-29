#!/usr/bin/env python3
"""Measure whether the pipeline would CATCH him if he migrated to a fresh wallet.

The project has many detectors but has never demonstrated it finds a hidden wallet.
This runs a pseudo-migration on his own history: split his fills at several cutoffs,
build his execution signature from the earlier half, and test whether the later
half — a fresh wallet running the same script — is recognised as him. It also runs
a "recent migration" split (could we catch a move that happened in the last N days).

No network, no alerts: it reads stored fills and the census and writes a report.
It is wired into analyze.yml so recall is tracked continuously, and it is the
honest answer to "does this actually work", including where it is blind.

Real result at build (2026-09-29): recognised on 3 of 4 whole-history splits, at a
clip-match ratio of ~0.5 — his clip SIZES drift over months, so about half his
exact per-coin sizes persist across a long gap. That is real signal (plus the 5%
offset and cadence), and it is exactly why the vector corroborates rather than
confirms alone. Keep the reference fresh; do not require a high exact-ratio.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import recall
from src.utils import DATA_DIR, save_latest

RECENT_MIGRATION_DAYS = 30
DAY_MS = 86_400_000


def load_census():
    try:
        with open(DATA_DIR / "execution_program" / "census.json") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def build_report(fills, census, *, now_ms=None):
    times = sorted(f["time"] for f in fills if isinstance(f.get("time"), (int, float)))
    if not times:
        return {"status": "no_fills"}
    lo, hi = times[0], times[-1]
    now_ms = now_ms if now_ms is not None else hi
    cutoffs = [int(lo + (hi - lo) * q) for q in (0.4, 0.55, 0.7, 0.85)]
    history = recall.sweep(fills, cutoffs, census)
    recent = recall.self_migration_recall(fills, now_ms - RECENT_MIGRATION_DAYS * DAY_MS, census)
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "vector": "execution_program",
        "method": "pseudo_migration_on_own_history",
        "census_present": census is not None,
        "history_split": history,
        "recent_migration": recent,
        "interpretation": (
            "recognised_rate is how often his post-cutoff trading is recognised as "
            "his pre-cutoff signature; caught_rate additionally requires the census "
            "to have measured the match rare (rule 4). His clip sizes drift over "
            "months, so exact-match is partial by design — the vector corroborates."),
    }


def main():
    from src.fingerprint import load_fills
    report = build_report(load_fills(), load_census())
    save_latest(str(DATA_DIR / "recall"), report)
    h = report.get("history_split", {})
    print(f"[recall] recognised_rate={h.get('recognised_rate')} caught_rate={h.get('caught_rate')} "
          f"measured_cutoffs={h.get('measured_cutoffs')} census={'yes' if report.get('census_present') else 'no'}")
    return report


if __name__ == "__main__":
    main()
    time.sleep(0)
