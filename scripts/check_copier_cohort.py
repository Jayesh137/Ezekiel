#!/usr/bin/env python3
"""Identify his copier cohort, and watch whether it shifts to a new account.

Phase 1 (this script): measure which leaderboard accounts follow his position
openings materially more than a time-shuffled placebo, weighted by coin rarity —
his copier cohort. Phase 2 (once a cohort exists): a drop in the cohort's recent
follow rate on his known wallet, while they collectively pick up a new account, is
a migration signal that needs no transfer, shared address or agent.

Public data only; no alerts from the measurement itself. Records research leads,
never ownership: a copier following a new account is a lead, and the new account
must still earn its own vectors. `build_report` is pure and unit-tested; `main` is
the paced network shell.
"""

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import copier_cohort as cc
from src.utils import DATA_DIR, load_config, save_latest

OUT_DIR = DATA_DIR / "copier_cohort"
MAX_CANDIDATES = 200
LAG_MS = 6 * 3600_000
# His openings from roughly the window candidate userFills (newest ~2,000) covers.
LOOKBACK_MS = 60 * 86_400_000


def build_report(target_openings, candidates, freq, lag_ms=LAG_MS):
    """candidates: [{wallet, openings}]. Returns the scored cohort, best first."""
    scored = []
    for candidate in candidates:
        score = cc.follow_score(target_openings, candidate.get("openings") or [], lag_ms, freq,
                                observed_window=candidate.get("observed_window"))
        scored.append({"wallet": (candidate.get("wallet") or "").lower(), **score,
                       "is_copier": cc.is_copier(score)})
    measured = [r for r in scored if r["status"] == "measured"]
    measured.sort(key=lambda r: (-(r["follow_rate"] - r["placebo_rate"]), -r["follows"], r["wallet"]))
    cohort = [r for r in measured if r["is_copier"]]
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "target_openings": len(target_openings),
        "candidates_read": len(scored),
        "candidates_measured": len(measured),
        "insufficient_overlap": len(scored) - len(measured),
        "cohort_size": len(cohort),
        "lag_hours": lag_ms / 3600_000,
        "cohort": cohort[:50],
        "top_non_cohort": [r for r in measured if not r["is_copier"]][:10],
    }


def _leaderboard(config):
    import requests
    r = requests.get(config["leaderboard_url"], timeout=120)
    r.raise_for_status()
    return r.json().get("leaderboardRows", [])


def _week_vlm(row):
    for name, perf in row.get("windowPerformances", []):
        if name == "week":
            try:
                return float(perf.get("vlm") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def main():
    import argparse

    from src.calibration import load_market_frequencies
    from src.fingerprint import load_fills
    from src.hl_budget import ReadBudget
    from src.utils import hl_read

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=MAX_CANDIDATES)
    parser.add_argument("--budget-seconds", type=int, default=1500)
    args = parser.parse_args()
    config = load_config()
    exclude = {config.get("target_wallet", "").lower(),
               *[w.lower() for w in config.get("known_self_wallets", [])]}
    now_ms = int(time.time() * 1000)
    target_openings = [e for e in cc.opening_events(load_fills()) if e[2] >= now_ms - LOOKBACK_MS]
    try:
        freq = load_market_frequencies()
    except (OSError, ValueError):
        freq = {}
    import hashlib
    rows = [r for r in _leaderboard(config)
            if float(r.get("accountValue") or 0) >= 100_000 and _week_vlm(r) > 0
            and (r.get("ethAddress") or "").lower() not in exclude]
    # A uniform (hash-ordered) sample, NOT volume-desc: the highest-volume accounts
    # churn their newest 2,000 fills in under a day, so they never overlap his
    # opening history — measuring them is measuring nothing (verified 2026-09-29).
    rows.sort(key=lambda r: hashlib.sha256(r["ethAddress"].encode()).hexdigest())
    candidates, errors = [], []
    with ReadBudget(seconds=args.budget_seconds, weight_per_minute=600) as budget:
        for row in rows[:args.limit]:
            if not budget.can_continue():
                break
            wallet = row["ethAddress"].lower()
            result = hl_read({"type": "userFills", "user": wallet})
            if not result.get("ok"):
                errors.append(wallet)
                continue
            fill_times = [f["time"] for f in result["data"] if isinstance(f.get("time"), (int, float))]
            window = (min(fill_times), max(fill_times)) if fill_times else None
            candidates.append({"wallet": wallet, "openings": cc.opening_events(result["data"]),
                               "observed_window": window})
    report = build_report(target_openings, candidates, freq)
    report["read_failures"] = len(errors)
    save_latest(str(OUT_DIR), report)
    print(f"[copier-cohort] his openings={report['target_openings']} "
          f"measured={report['candidates_measured']} cohort={report['cohort_size']}")
    for c in report["cohort"][:10]:
        print(f"  {c['wallet']} follow={c['follow_rate']} placebo={c['placebo_rate']} "
              f"follows={c['follows']} lead_lag_h={round((c['lead_lag_median_ms'] or 0)/3600_000,2)}")
    return report


if __name__ == "__main__":
    main()
    time.sleep(0)
