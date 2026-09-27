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

Bounded: read portfolio history first and fetch recent trades only when the
observed birth could fit a handoff. Partial checks rotate on the next run.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_dormancy_handoff, alert_target_dormant
from src.dormancy import active_days, build_report, handoff_score, save
from src.fingerprint import load_fills
from src.hl_budget import ReadBudget, current_budget
from src.hl_identity import parse_birth
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


def collect_candidates(wallets, target_fills, previous, *, fetch=None):
    from src.utils import hl_read
    fetch = fetch or hl_read
    budget = current_budget()
    attempts = previous.get('collection', {}).get('last_attempt_ms', {})
    attempts = {w: attempts[w] for w in wallets if w in attempts}
    ordered = sorted(wallets, key=lambda w: (attempts.get(w, 0), w))
    target_days = active_days(target_fills)
    candidates = {}
    coverage = {'requested': len(wallets), 'attempted': 0, 'checked': [],
                'errors': [], 'last_attempt_ms': attempts}
    for wallet in ordered:
        if budget and not budget.can_continue():
            break
        coverage['attempted'] += 1
        attempts[wallet] = int(time.time() * 1000)
        try:
            response = fetch({'type': 'portfolio', 'user': wallet})
            if not response.get('ok'):
                raise ValueError(response.get('error') or 'portfolio unreadable')
            birth = parse_birth(response.get('data'))
            if not birth:
                # Empty/unknown birth cannot justify interpreting the oldest
                # retained fill as a new account's first activity.
                raise ValueError('no observed portfolio birth')
            got = [{'time': birth}]
            if handoff_score(target_days, active_days(got)).get('score', 0) > 0:
                # Only this branch needs trading evidence. A retained fill
                # older than the portfolio point also rules out a false birth.
                response = fetch({'type': 'userFills', 'user': wallet, 'aggregateByTime': False})
                fills = response.get('data')
                if not response.get('ok') or not isinstance(fills, list) or any(
                    not isinstance(r, dict) or not isinstance(r.get('time'), (int, float)) for r in fills
                ):
                    raise ValueError(response.get('error') or 'fills unreadable')
                if fills:
                    got.extend(fills)
                else:
                    # This is an observed absence in the available fills, not
                    # a claim that this account never traded.
                    got = []
            candidates[wallet] = got
            coverage['checked'].append(wallet)
        except (ValueError, TypeError, OSError, RuntimeError) as exc:
            coverage['errors'].append({'wallet': wallet, 'error': str(exc)[:200]})
    coverage['deferred'] = len(wallets) - coverage['attempted']
    coverage['status'] = 'partial' if coverage['deferred'] or coverage['errors'] else 'ok'
    if budget:
        coverage['budget'] = budget.report()
    return candidates, coverage


def main() -> int:
    config = load_config()
    fills = load_fills()
    wallets = candidate_wallets(config)
    try:
        previous = json.loads((DATA_DIR / 'dormancy' / 'latest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    with ReadBudget(seconds=120):
        candidates, coverage = collect_candidates(wallets, fills, previous)

    now_day = int(datetime.now(UTC).timestamp()) // 86_400
    report = build_report(fills, candidates, now_day=now_day)
    report['collection'] = coverage
    for wallet, detail in previous.get('handoffs', {}).items():
        if wallet in wallets and wallet not in coverage['checked']:
            report['handoffs'][wallet] = {**detail, 'stale': True,
                'observed_at': detail.get('observed_at') or previous.get('computed_at')}
    save(report)
    print(f"[dormancy] collection: {coverage}")

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
        if detail.get('stale'):
            continue
        print(f"[dormancy] HANDOFF {wallet} score {detail['score']} — started "
              f"{detail['delay_days']}d into a {detail['gap_length']}d silence")
        alert_dormancy_handoff(wallet, detail)
    if not handoffs:
        print("[dormancy] no handoff found among successfully checked candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
