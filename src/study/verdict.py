"""Per-family verdicts and the study rank (spec §8.3, §9). Pure.

A family is FOR when any of its calibrated tests is, AGAINST when T1 is and none
is for, MIXED when both; otherwise it reports the most informative of neutral,
uncalibrated or insufficient. The rank orders the Study list and nothing else:
it is never a tier and never stored as a confidence. A family's LR is its
strongest calibrated test's (spec §8.3), so a neutral family still orders the
list by how close it came.
"""

from __future__ import annotations

import math

FOR, AGAINST, MIXED = "for", "against", "mixed"
NEUTRAL, UNCALIBRATED, INSUFFICIENT = "neutral", "uncalibrated", "insufficient"
TOOLING = ("T1", "T2", "T3")
FAMILY_BASIS = "family"  # T1's against is judged on same-operator family pairs only


def _strongest(lrs) -> float | None:
    """The LR furthest from 1 — the strongest evidence either way; None if none."""
    usable = [x for x in lrs if isinstance(x, (int, float)) and x > 0]
    return max(usable, key=lambda x: (abs(math.log10(x)), x)) if usable else None


def _basis(judgement: dict) -> str | None:
    """The same-operator basis a judgement was calibrated on. His own months alone
    ("self") is the spec's `self_only`; "family" and "family+self" pass through."""
    basis = judgement.get("basis")
    return "self_only" if basis == "self" else basis


def _pick(pool: list[tuple[dict, str | None]]) -> tuple[float | None, str | None]:
    """The strongest LR in a pool of (judgement, basis) and the basis it came from;
    on a tie, the first in the pool."""
    lr = _strongest(j.get("lr") for j, _ in pool)
    return lr, next((basis for j, basis in pool if lr is not None and j.get("lr") == lr), None)


def family_verdict(tests: dict, names: tuple[str, ...]) -> dict:
    judged = [(name, (tests.get(name) or {}).get("judgement") or {})
              for name in names if name in tests]
    fors = [(name, j) for name, j in judged if j.get("status") == FOR]
    against = ((tests.get("T1") or {}).get("against") or {}) if "T1" in names else {}
    is_against = against.get("status") == AGAINST
    if fors and is_against:
        status = MIXED
    elif fors:
        status = FOR
    elif is_against:
        status = AGAINST
    else:
        seen = {j.get("status") for _, j in judged} | {against.get("status")}
        status = (NEUTRAL if NEUTRAL in seen else UNCALIBRATED if UNCALIBRATED in seen
                  else INSUFFICIENT)
    # Spec §8.3: a family's LR is its strongest calibrated test's; only an
    # uncalibrated or insufficient family adds nothing to the rank. Spec §8.1: it also
    # says which same-operator basis that test stood on ("self_only" when only his own
    # months measured it). T1's against is judged on family pairs alone.
    lr, basis = None, None
    if status == FOR:
        best = max((j for _, j in fors), key=lambda j: j.get("lr") or 0)
        lr, basis = best.get("lr") or None, _basis(best)
    elif status == AGAINST:
        lr, basis = against.get("lr"), FAMILY_BASIS
    elif status == MIXED:
        lr, basis = _pick([*((j, _basis(j)) for _, j in fors), (against, FAMILY_BASIS)])
    elif status == NEUTRAL:
        lr, basis = _pick([(j, _basis(j)) for _, j in judged if j.get("status") == NEUTRAL])
    return {"verdict": status, "lr": lr, "basis": basis,
            "by": [name for name, _ in fors] + (["T1:against"] if is_against else [])}


def rank(families: dict) -> float:
    total = 0.0
    for family in families.values():
        lr = family.get("lr")
        if isinstance(lr, (int, float)) and lr > 0:
            total += max(-2.0, min(2.0, math.log10(lr)))
    return round(total, 3)


def newly(previous_rows: list, rows: list, family: str, verdict: str = FOR) -> list[dict]:
    """Rows whose `family` verdict became `verdict` since the previous run."""
    before = {r.get("wallet"): ((r.get("families") or {}).get(family) or {}).get("verdict")
              for r in previous_rows or [] if isinstance(r, dict)}
    return [r for r in rows
            if ((r.get("families") or {}).get(family) or {}).get("verdict") == verdict
            and before.get(r["wallet"]) != verdict]
