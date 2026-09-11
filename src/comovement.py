# src/comovement.py
"""Who moves first: the one behavioural test a copy-trader cannot pass.

Portfolio overlap cannot tell the target from his copiers — this project
exists because its owner copies him by hand, so wallets holding his basket in
his direction certainly exist. Timing can tell them apart. A copier acts AFTER
the target's fills reach the tape; a second account run by the same hand acts
with them or before them.

So this measures, per coin and direction, how the candidate's decisions line
up with the target's in time, and which side leads. Fills are compressed into
decisions (bursts of same-direction fills in one coin separated by a quiet
gap), because a TWAP session is one decision however many fills it produces.

Two guards against reading noise as a verdict:

  * A shifted control. The same pairing is measured with the candidate's
    decisions moved a day later. Whatever pairs then is coincidence, and only
    the excess over it counts.
  * A minimum number of pairs. Five coincident trades in a busy market prove
    nothing; the verdict needs enough pairs for the lead share to mean
    something.

Everything here is pure; scripts/check_comovement.py does the I/O.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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
# The control shift: a day, so weekly rhythm is preserved and overlap is not.
CONTROL_SHIFT_MS = 24 * 3600 * 1000


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
    """For each candidate decision, the nearest target decision in the same
    coin and direction within the window. lag_min < 0 means the candidate led."""
    by_key: dict[tuple, list[dict]] = {}
    for d in target_decisions:
        by_key.setdefault((d["coin"], d["side"]), []).append(d)
    pairs = []
    for c in candidate_decisions:
        best = None
        for t in by_key.get((c["coin"], c["side"]), []):
            lag = (c["start"] - t["start"]) / MS
            if abs(lag) > window_min:
                continue
            if best is None or abs(lag) < abs(best["lag_min"]):
                best = {"coin": c["coin"], "side": c["side"], "lag_min": round(lag, 1),
                        "candidate_start": c["start"], "target_start": t["start"]}
        if best:
            pairs.append(best)
    return pairs


def _shifted(decs: list[dict], shift_ms: int) -> list[dict]:
    return [{**d, "start": d["start"] + shift_ms, "end": d["end"] + shift_ms} for d in decs]


def score(target_fills: list[dict], candidate_fills: list[dict]) -> dict:
    """The co-movement profile of one candidate against the target. Pure."""
    return score_decisions(decisions(target_fills), decisions(candidate_fills))


def score_decisions(t_dec: list[dict], c_dec: list[dict]) -> dict:
    """As `score`, on decisions already compressed (and possibly accumulated
    across runs, see scripts/check_comovement.py)."""
    if not t_dec or not c_dec:
        # `pairs` is present and zero rather than absent: a reader comparing
        # candidates should see the same fields whatever the verdict, and a
        # missing key renders as None, which reads like a failed measurement
        # rather than a measured nothing.
        return {"verdict": "untestable", "reason": "no decisions on one side",
                "pairs": 0, "candidate_decisions": len(c_dec),
                "target_decisions": len(t_dec)}
    pairs = pair(t_dec, c_dec)
    control = pair(t_dec, _shifted(c_dec, CONTROL_SHIFT_MS))
    paired_share = len(pairs) / len(c_dec)
    control_share = len(control) / len(c_dec)
    excess = round(paired_share - control_share, 4)
    lags = sorted(p["lag_min"] for p in pairs)
    median_lag = lags[len(lags) // 2] if lags else None
    leads = sum(1 for lag in lags if lag <= 0)
    reacts = sum(1 for lag in lags if 0 < lag <= COPIER_REACTION_MIN)
    lead_share = round(leads / len(lags), 4) if lags else None
    react_share = round(reacts / len(lags), 4) if lags else None

    if len(pairs) < MIN_PAIRS:
        verdict, reason = "untestable", f"only {len(pairs)} paired decision(s); need {MIN_PAIRS}"
    elif excess <= 0.1:
        verdict, reason = "independent", ("pairs no more often than a day-shifted control "
                                          f"(excess {excess:+.2f})")
    elif lead_share is not None and lead_share >= 0.4:
        verdict, reason = "same_hand", (f"leads or ties the target in {lead_share:.0%} of "
                                        f"{len(pairs)} paired decisions — a copier cannot")
    elif react_share is not None and react_share >= 0.5:
        verdict, reason = "copier", (f"follows the target within {COPIER_REACTION_MIN} min in "
                                     f"{react_share:.0%} of paired decisions")
    else:
        verdict, reason = "correlated", ("moves with the target more than chance, with no "
                                         "clear lead or reaction pattern")
    return {
        "verdict": verdict, "reason": reason,
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
