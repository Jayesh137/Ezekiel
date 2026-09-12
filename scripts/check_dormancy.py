#!/usr/bin/env python3
"""Watch for the target going quiet, and for wallets born while he is.

The clearest migration signature there is, and the only one that needs no
connection between the two wallets — no transfer, no shared deposit address, no
authorised agent. If he abandons this wallet and funds a fresh one from
somewhere unobservable, the alignment of his silence with another wallet's birth
is the only thing left to find him by.

Everything is judged against HIS OWN rhythm. Measured 2026-09-10 across 75 active
days: median gap 2 days, p90 5, p95 10, longest ever 21. A fixed "dormant after a
week" rule would fire constantly on a trader like that and be ignored within a
month.

Cheap: the target's fills are already on disk, and candidate fills are one call
each for a bounded set.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_dormancy_handoff, alert_target_dormant
from src.dormancy import build_report, save
from src.fingerprint import load_fills
from src.utils import DATA_DIR, load_config

# One call each. Only wallets already interesting enough to be on the roster.
MAX_CANDIDATES = 40


def candidate_wallets(config: dict) -> list[str]:
    """Roster candidates, with the operator's own wallets pinned ahead of them.

    Taking the top of the roster alone excluded `0xdd53c529…` — the wallet this
    vector was built for, named in its own docstring — because the roster ranks
    by evidence already found and that wallet's evidence had lapsed. See
    `roster.detector_candidates`.
    """
    from src.roster import detector_candidates

    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            roster = json.load(f)
    except (OSError, ValueError, AttributeError):
        roster = {}
    return detector_candidates(config, roster, MAX_CANDIDATES)


def main() -> int:
    from src.scanner import get_candidate_fills

    config = load_config()
    fills = load_fills()
    wallets = candidate_wallets(config)

    from src.hl_identity import parse_birth
    from src.utils import hl_post

    candidates = {}
    for wallet in wallets:
        try:
            got = get_candidate_fills(wallet, 3650)
        except Exception as exc:                      # noqa: BLE001 - transport
            print(f"[dormancy] {wallet[:12]}... unreadable: {type(exc).__name__}")
            continue
        # userFillsByTime returns the OLDEST 2,000 of the ~10,000 fills the API
        # retains, so a busy wallet's "first fill" is a few weeks ago and it
        # would look newborn on every run. The all-time value series gives the
        # real birth; it is folded in as the wallet's earliest active day.
        try:
            birth = parse_birth(hl_post({"type": "portfolio", "user": wallet}))
        except Exception:                             # noqa: BLE001 - transport
            birth = None
        if birth:
            got = list(got or []) + [{"time": birth}]
        if got:
            candidates[wallet] = got
        time.sleep(0.12)

    now_day = int(datetime.now(UTC).timestamp()) // 86_400
    report = build_report(fills, candidates, now_day=now_day)
    save(report)

    state = report["dormancy"]
    stats = state["stats"]
    print(f"[dormancy] silent {state['silent_days']}d "
          f"(his median gap {stats['median']}d, p90 {stats['p90']}d, "
          f"longest ever {stats['max']}d)")
    print(f"[dormancy] {len(report['anomalous_gaps'])} anomalous gap(s) in his "
          f"history; {report['candidates_scored']} candidate(s) scored")

    if state["unusual"]:
        print(f"[dormancy] ALERT: silence is "
              f"{'UNPRECEDENTED' if state['unprecedented'] else 'unusual'}")
        alert_target_dormant(state["silent_days"], stats, state["unprecedented"])
    else:
        # Said explicitly: "not dormant" is a measured finding, not an absence
        # of output.
        print("[dormancy] silence is within his normal rhythm — not dormant")

    handoffs = report["handoffs"]
    for wallet, detail in sorted(handoffs.items(),
                                 key=lambda kv: -kv[1]["score"]):
        print(f"[dormancy] HANDOFF {wallet} score {detail['score']} — started "
              f"{detail['delay_days']}d into a {detail['gap_length']}d silence")
        alert_dormancy_handoff(wallet, detail)
    if not handoffs:
        print("[dormancy] no candidate began trading during one of his silences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
