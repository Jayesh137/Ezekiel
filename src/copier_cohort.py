"""Copier-cohort consensus sensor: his followers point at his live wallet.

He has copiers — accounts that open his positions with a lag — and he does not
control them. That makes them a distributed sensor: whoever the cohort is following
is where the money-making wallet is. If a stable cohort stops following his known
wallet and starts following a new account, that account is his successor, with no
transfer, shared address or agent required.

The whole difficulty is telling a real copier from two accounts reacting to the
same market move. Three guards, none of which a coincidence clears together:

- **Placebo control.** A follow rate means nothing on its own; it is compared with
  the rate against his TIME-SHIFTED openings. A real copier follows his actual
  entries, not entries that never happened at those times.
- **Rarity weighting.** Following him into BTC is nearly free (everyone trades
  BTC); following him into ZEC or xyz:SILVER within the hour is the signal.
  `calibration.market_rarity_bonus` supplies the weight.
- **Direction and lead-lag.** A copier opens the SAME direction AFTER him with a
  consistent lag; a leader or a coincidence does not.

This module is pure. The measurement/sensor script fetches fills and applies it.
It records research leads, never ownership — a copier following a new account is a
lead to investigate, and the new account still has to earn its own vectors.
"""

import math
from statistics import median

SESSION_GAP_MS = 30 * 60_000
# Placebo shifts (ms): his openings moved by these offsets should NOT be followed.
PLACEBO_SHIFTS_MS = [d * 86_400_000 for d in (3, 7, 11, 17, 23, 31)]
MIN_FOLLOWS = 4
MIN_LIFT = 0.25  # follow_rate must beat placebo_rate by this margin
# A candidate must have been OBSERVED across at least this many of his openings for
# a follow rate to mean anything. A busy account's newest ~2,000 fills can span
# under a day, overlapping few of his openings — that is unmeasured, not a
# non-follower (rule 5). Below this the candidate is `insufficient_data`.
MIN_OPPORTUNITIES = 8


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def opening_events(fills: list[dict]) -> list[tuple]:
    """Distinct position OPENINGS as (coin, direction, ts_ms), sliced entries merged.

    An opening adds exposure: |after| > |before|, in the direction of `after`.
    Consecutive same-coin same-direction openings within a session gap are one
    event, timestamped when he STARTED opening (what a copier reacts to).
    """
    raw = []
    for row in fills or []:
        if not isinstance(row, dict) or row.get("side") not in ("A", "B"):
            continue
        ts, size, before = _num(row.get("time")), _num(row.get("sz")), _num(row.get("startPosition"))
        if ts is None or size is None or before is None or size <= 0:
            continue
        after = before + (size if row["side"] == "B" else -size)
        if abs(after) <= abs(before) or after == 0:
            continue  # a reduction or a flat close, not an opening
        raw.append((row.get("coin"), 1 if after > 0 else -1, ts))
    raw.sort(key=lambda r: (r[0] or "", r[1], r[2]))
    events, last = [], {}
    for coin, direction, ts in raw:
        key = (coin, direction)
        if key in last and ts - last[key] <= SESSION_GAP_MS:
            last[key] = ts
            continue
        events.append((coin, direction, ts))
        last[key] = ts
    return sorted(events, key=lambda e: e[2])


def _rarity(coin, freq):
    if not freq:
        return 1.0
    from src.calibration import market_rarity_bonus
    bonus, _ = market_rarity_bonus([coin], freq)
    return 1.0 + bonus  # common coin ~1.0, rare coin > 1.0


def _rate(target_openings, candidate_openings, lag_ms, offset=0):
    """Fraction of target openings the candidate matched within [T+offset, T+offset+lag]."""
    by_key = {}
    for coin, direction, ts in candidate_openings:
        by_key.setdefault((coin, direction), []).append(ts)
    follows, opportunities, lags = 0, 0, []
    for coin, direction, ts in target_openings:
        opportunities += 1
        window_lo, window_hi = ts + offset, ts + offset + lag_ms
        hit = next((t for t in sorted(by_key.get((coin, direction), [])) if window_lo <= t <= window_hi), None)
        if hit is not None:
            follows += 1
            lags.append(hit - ts)
    return follows, opportunities, lags


def follow_score(target_openings, candidate_openings, lag_ms, freq, observed_window=None) -> dict:
    """How much the candidate follows his openings, over the placebo baseline.

    `observed_window` (t_min, t_max) is the candidate's observed fill span; only his
    openings inside it are OPPORTUNITIES, so an account we barely overlap is
    `insufficient_data`, not a spurious non-follower (rule 5).
    """
    if observed_window is not None:
        lo, hi = observed_window
        target_openings = [e for e in target_openings if lo <= e[2] <= hi]
    if len(target_openings) < MIN_OPPORTUNITIES:
        return {"status": "insufficient_data", "opportunities": len(target_openings),
                "follows": 0, "follow_rate": 0.0, "placebo_rate": 0.0,
                "rarity_weighted_follows": 0.0, "lead_lag_median_ms": None}
    follows, opportunities, lags = _rate(target_openings, candidate_openings, lag_ms)
    placebo_rates = []
    for shift in PLACEBO_SHIFTS_MS:
        p_follows, p_opps, _ = _rate(target_openings, candidate_openings, lag_ms, offset=shift)
        if p_opps:
            placebo_rates.append(p_follows / p_opps)
    weighted = 0.0
    cand_keys = {}
    for coin, direction, ts in candidate_openings:
        cand_keys.setdefault((coin, direction), []).append(ts)
    for coin, direction, ts in target_openings:
        hit = next((t for t in sorted(cand_keys.get((coin, direction), [])) if ts <= t <= ts + lag_ms), None)
        if hit is not None:
            weighted += _rarity(coin, freq)
    return {
        "status": "measured",
        "opportunities": opportunities,
        "follows": follows,
        "follow_rate": round(follows / opportunities, 4) if opportunities else 0.0,
        "placebo_rate": round(sum(placebo_rates) / len(placebo_rates), 4) if placebo_rates else 0.0,
        "rarity_weighted_follows": round(weighted, 4),
        "lead_lag_median_ms": int(median(lags)) if lags else None,
    }


def is_copier(score: dict) -> bool:
    """A copier follows his real openings materially more than the placebo, on
    enough distinct openings that it is not a handful of coincidences."""
    return bool(score.get("status") == "measured"
                and (score.get("follows") or 0) >= MIN_FOLLOWS
                and (score.get("follow_rate") or 0) - (score.get("placebo_rate") or 0) >= MIN_LIFT)
