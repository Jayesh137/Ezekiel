"""The study's bars (spec 2026-10-06 §8.2), fixed before any result was seen. Pure.

A test says nothing until its panels are big enough (`uncalibrated`). It says
**for** only when strangers reach the candidate's level at most 2% of the time —
the one-sided 95% Clopper–Pearson upper bound — where the level is the LOOSER of
the candidate's own and each usable same-operator median. Judging at the looser
level means a closer match can never fare worse than a looser one, and the
same-operator rate at that level is at least one half by construction. Only T1
can say **against**, and only for a trait he never shows that same-operator
pairs almost never disagree on. Never tune these numbers to a result (rule 4).
"""

from __future__ import annotations

import math
from statistics import median

MIN_STRANGERS = 200
MIN_REFERENCES = 150
MIN_SAME_OP = {"family": 40, "self": 6}
FOR_UPPER = 0.02
AGAINST_MISMATCH = 0.10
CONFIDENCE = 0.95


def binom_cdf(k: int, n: int, p: float) -> float:
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    total = sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                         + i * log_p + (n - i) * log_q) for i in range(k + 1))
    return min(1.0, total)


def upper_bound(k: int, n: int, confidence: float = CONFIDENCE) -> float:
    """One-sided Clopper–Pearson upper bound on a rate seen k times in n."""
    if n <= 0 or k >= n:
        return 1.0
    alpha = 1 - confidence
    lo, hi = k / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def _usable(same_op: dict) -> dict:
    return {name: values for name, values in (same_op or {}).items()
            if len(values) >= MIN_SAME_OP.get(name, math.inf)}


def judge_continuous(x, *, strangers: list[float], same_op: dict[str, list[float]],
                     higher_is_better: bool, min_strangers: int = MIN_STRANGERS) -> dict:
    """T2/T3. `strangers` holds one value per measurable stranger; one that cannot
    produce the statistic carries -inf (higher is better) or inf (lower is better)."""
    if x is None:
        return {"status": "insufficient"}
    usable = _usable(same_op)
    if len(strangers) < min_strangers or not usable:
        return {"status": "uncalibrated", "strangers": len(strangers),
                "same_op": {k: len(v) for k, v in (same_op or {}).items()}}
    medians = [median(values) for values in usable.values()]
    level = min([x, *medians]) if higher_is_better else max([x, *medians])

    def reaches(value) -> bool:
        return value >= level if higher_is_better else value <= level

    k = sum(1 for s in strangers if reaches(s))
    ub = upper_bound(k, len(strangers))
    rate = min(sum(1 for v in values if reaches(v)) / len(values) for values in usable.values())
    return {"status": "for" if ub <= FOR_UPPER else "neutral", "level": round(level, 4),
            "stranger_k": k, "stranger_n": len(strangers), "stranger_upper": round(ub, 5),
            "same_op_rate": round(rate, 4), "basis": "+".join(sorted(usable)),
            "lr": round(rate / ub, 2)}


def judge_binary(match: bool | None, *, stranger_k: int, stranger_n: int,
                 same_op: dict[str, tuple[int, int]],
                 min_strangers: int = MIN_STRANGERS) -> dict:
    """T1. `same_op` maps a basis to (agreeing pairs or windows, total)."""
    if match is None:
        return {"status": "insufficient"}
    usable = {name: v for name, v in (same_op or {}).items()
              if v[1] >= MIN_SAME_OP.get(name, math.inf)}
    if stranger_n < min_strangers or not usable:
        return {"status": "uncalibrated", "strangers": stranger_n,
                "same_op": {k: v[1] for k, v in (same_op or {}).items()}}
    if not match:
        return {"status": "neutral", "stranger_k": stranger_k, "stranger_n": stranger_n}
    ub = upper_bound(stranger_k, stranger_n)
    rate = min(agree / total for agree, total in usable.values())
    return {"status": "for" if ub <= FOR_UPPER and rate >= 0.5 else "neutral",
            "stranger_k": stranger_k, "stranger_n": stranger_n,
            "stranger_upper": round(ub, 5), "same_op_rate": round(rate, 4),
            "basis": "+".join(sorted(usable)), "lr": round(rate / ub, 2)}


def judge_against(traits: list[str], *, family_mismatch: dict[str, tuple[int, int]],
                  stranger_trait_rate: dict[str, float]) -> dict:
    """T1 only: a trait he never shows dominates the candidate, and same-operator
    pairs disagree on it at most 10% of the time over at least 40 pairs."""
    if not traits:
        return {"status": "none"}
    measured = {t: tuple(family_mismatch.get(t, (0, 0))) for t in traits}
    ready = {t: v for t, v in measured.items() if v[1] >= MIN_SAME_OP["family"]}
    if not ready:
        return {"status": "uncalibrated", "traits": list(traits),
                "pairs": {t: v[1] for t, v in measured.items()}}
    holding = {t: v for t, v in ready.items() if v[0] / v[1] <= AGAINST_MISMATCH}
    if not holding:
        return {"status": "neutral", "traits": list(traits)}
    ratios = [upper_bound(k, n) / stranger_trait_rate[t]
              for t, (k, n) in holding.items() if stranger_trait_rate.get(t)]
    return {"status": "against", "traits": sorted(holding),
            "same_op_mismatch": {t: f"{k}/{n}" for t, (k, n) in holding.items()},
            "lr": round(min(ratios), 4) if ratios else None}
