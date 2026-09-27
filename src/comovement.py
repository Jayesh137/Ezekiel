# src/comovement.py
"""Research lead/lag patterns with one-to-one pairing and shifted controls.

Synchrony can also arise from news, common signals or execution software.
The legacy `same_hand` key denotes a leading pattern, never proven ownership.
"""

import sys
from bisect import bisect_left, bisect_right
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.matching import maximum_weight_pairs
from src.utils import DATA_DIR, save_latest

COMOVEMENT_DIR = DATA_DIR / "comovement"

MS = 60_000
# Fills in one coin and direction closer than this are one decision.
DECISION_GAP_MIN = 30
# A candidate decision pairs with a target decision this close in time.
PAIR_WINDOW_MIN = 120
# Within this many minutes after the target, a copier could plausibly react.
COPIER_REACTION_MIN = 15
# Fewer pairs than this and no verdict is offered.
MIN_PAIRS = 10
# Whole-day shifts preserve clock time and within-session timing.
CONTROL_SHIFT_MS = 24 * 3600 * 1000
CONTROL_SHIFTS = (-7, -2, -1, 1, 2, 7)
MIN_SESSIONS = 5


def decisions(fills: list[dict], gap_min: float = DECISION_GAP_MIN) -> list[dict]:
    """(coin, direction, start_ms, end_ms, fills) per burst of same-direction fills."""
    by_key: dict[tuple, list[int]] = {}
    for f in fills or []:
        if not isinstance(f, dict):
            continue
        coin = f.get("coin")
        side = f.get("side")
        if f.get("time") is None:
            continue
        try:
            t = int(f["time"])
        except (TypeError, ValueError):
            continue
        if not coin or side not in ("B", "A"):
            continue
        by_key.setdefault((coin, side), []).append(t)
    out = []
    for (coin, side), times in by_key.items():
        times.sort()
        start = prev = times[0]
        n = 1
        for t in times[1:]:
            if t - prev > gap_min * MS:
                out.append({"coin": coin, "side": side, "start": start, "end": prev, "fills": n})
                start, n = t, 0
            prev = t
            n += 1
        out.append({"coin": coin, "side": side, "start": start, "end": prev, "fills": n})
    return sorted(out, key=lambda d: d["start"])


def pair(target_decisions: list[dict], candidate_decisions: list[dict],
         window_min: float = PAIR_WINDOW_MIN) -> list[dict]:
    """Distinct decision pairs, preferring cardinality then temporal proximity."""
    target_decisions = list({(d['coin'], d['side'], d['start']): d for d in target_decisions}.values())
    candidate_decisions = list({(d['coin'], d['side'], d['start']): d for d in candidate_decisions}.values())
    by_key: dict[tuple, list[dict]] = {}
    for i, d in enumerate(target_decisions):
        by_key.setdefault((d["coin"], d["side"]), []).append((d['start'], i))
    for rows in by_key.values():
        rows.sort()
    edges = []
    window = window_min * MS
    base = min(len(target_decisions), len(candidate_decisions)) + 1
    for j, c in enumerate(candidate_decisions):
        rows = by_key.get((c['coin'], c['side']), [])
        lo = bisect_left(rows, (c['start'] - window, -1))
        hi = bisect_right(rows, (c['start'] + window, float('inf')))
        edges.extend((i, j, base - abs(c['start'] - ts) / (window + 1)) for ts, i in rows[lo:hi])
    assignment, mode = maximum_weight_pairs(edges)
    pairs = []
    for i, j in assignment:
        t, c = target_decisions[i], candidate_decisions[j]
        pairs.append({'coin': c['coin'], 'side': c['side'], 'lag_min': round((c['start'] - t['start']) / MS, 1),
                      'candidate_start': c['start'], 'target_start': t['start'], 'assignment_mode': mode})
    return pairs


def _shifted(decs: list[dict], shift_ms: int) -> list[dict]:
    return [{**d, "start": d["start"] + shift_ms, "end": d["end"] + shift_ms} for d in decs]


def score(target_fills: list[dict], candidate_fills: list[dict]) -> dict:
    """The co-movement profile of one candidate against the target. Pure."""
    return score_decisions(decisions(target_fills), decisions(candidate_fills))


def score_decisions(t_dec: list[dict], c_dec: list[dict]) -> dict:
    """As `score`, on decisions already compressed (and possibly accumulated
    across runs, see scripts/check_comovement.py)."""
    research = {'promotable': False, 'research_only': True,
                'confounders': ['common_market_event', 'shared_execution_software', 'common_signal_source']}
    if not t_dec or not c_dec:
        # `pairs` is present and zero rather than absent: a reader comparing
        # candidates should see the same fields whatever the verdict, and a
        # missing key renders as None, which reads like a failed measurement
        # rather than a measured nothing.
        return {"verdict": "untestable", "reason": "no decisions on one side",
                "pairs": 0, "candidate_decisions": len(c_dec),
                "target_decisions": len(t_dec), 'independent_sessions': 0, 'null_controls': [], **research}
    pairs = pair(t_dec, c_dec)
    controls = [{'shift_days': shift, 'pairs': len(pair(t_dec, _shifted(c_dec, shift * CONTROL_SHIFT_MS)))}
                for shift in CONTROL_SHIFTS]
    paired_share = len(pairs) / len(c_dec)
    control_share = max(c['pairs'] for c in controls) / len(c_dec)
    times = sorted({p['target_start'] for p in pairs})
    sessions = sum(i == 0 or ts - times[i - 1] > 120 * MS for i, ts in enumerate(times))
    excess = round(paired_share - control_share, 4)
    lags = sorted(p["lag_min"] for p in pairs)
    median_lag = lags[len(lags) // 2] if lags else None
    leads = sum(1 for lag in lags if lag <= 0)
    reacts = sum(1 for lag in lags if 0 < lag <= COPIER_REACTION_MIN)
    lead_share = round(leads / len(lags), 4) if lags else None
    react_share = round(reacts / len(lags), 4) if lags else None

    if len(pairs) < MIN_PAIRS:
        verdict, reason = "untestable", f"only {len(pairs)} paired decision(s); need {MIN_PAIRS}"
    elif sessions < MIN_SESSIONS:
        verdict, reason = 'untestable', f'only {sessions} independent sessions; need {MIN_SESSIONS}'
    elif excess <= 0.1:
        verdict, reason = "independent", ("no clear excess over the strongest shifted control "
                                          f"(excess {excess:+.2f})")
    elif lead_share is not None and lead_share >= 0.4:
        verdict, reason = "same_hand", (f"leads or ties the target in {lead_share:.0%} of "
                                        f"{len(pairs)} paired decisions; ownership unproven")
    elif react_share is not None and react_share >= 0.5:
        verdict, reason = "copier", (f"follows the target within {COPIER_REACTION_MIN} min in "
                                     f"{react_share:.0%} of paired decisions")
    else:
        verdict, reason = "correlated", ("moves with the target more than chance, with no "
                                         "clear lead or reaction pattern")
    return {
        "verdict": verdict, "reason": reason, **research,
        'independent_sessions': sessions, 'null_controls': controls,
        'control_method': 'maximum overlap of whole-session day shifts; heuristic, not a p-value',
        "candidate_decisions": len(c_dec), "target_decisions": len(t_dec),
        "pairs": len(pairs), "paired_share": round(paired_share, 4),
        "control_share": round(control_share, 4), "excess": excess,
        "median_lag_min": median_lag, "lead_share": lead_share, "react_share": react_share,
        "coins": sorted({p["coin"] for p in pairs}),
    }


def build_report(target_fills: list[dict], candidates: dict) -> dict:
    scored = {}
    for wallet, fills in (candidates or {}).items():
        w = (wallet or "").lower()
        if w:
            scored[w] = score(target_fills, fills)
    order = {"same_hand": 0, "copier": 1, "correlated": 2, "independent": 3, "untestable": 4}
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "candidates_scored": len(scored),
        "results": dict(sorted(scored.items(),
                               key=lambda kv: (order.get(kv[1]["verdict"], 9),
                                               -(kv[1].get("pairs") or 0)))),
    }


def save(report: dict) -> None:
    save_latest(str(COMOVEMENT_DIR), report)
