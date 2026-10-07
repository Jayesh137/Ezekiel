"""Is the candidate study ready for Phase 2? (spec §14 acceptance, §15 delivery). Pure.

Phase 2 builds more tests on Phase 1's calibration, so it waits until Phase 1 has run
in production and shown, on live data, that it can tell him from strangers:

- the study is fresh, and has been live for `MIN_PRODUCTION_DAYS`;
- every wallet it has read has day records with coverage, and none is unreadable
  (acceptance 2);
- T1's stranger panel has reached its bar;
- **self-recall** (acceptance 4): each of his recent whole months, judged as a
  candidate against the rest of him, reads tooling FOR. It is leave-one-out — the
  month judged is excluded from his reference AND from his own same-operator windows
  — so a month never calibrates itself (rule 4);
- **no false FOR** (acceptance 3's negative half): no studied wallet that shows a
  trait he never shows (client order ids, triggers, maker posting) reads FOR or MIXED.

Acceptance 1 (the census vote) and the family panel's bar are reported and never
required: neither measures whether Phase 1 can recognise him.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from src.study import assemble, calibration, verdict

PHASE1_LIVE_MS = 1_791_397_551_000  # PR #74 merged, 2026-10-07T18:25:51Z
MIN_PRODUCTION_DAYS = 5
FRESH_HOURS = 13  # the study runs every six hours: two missed runs are not yet stale
RECALL_MONTHS = 2
_HOUR_MS = 3_600_000


def recent_months(now_ms: int, n: int = RECALL_MONTHS) -> list[str]:
    """The `n` whole calendar months before the month `now_ms` falls in, oldest first."""
    at = datetime.fromtimestamp(now_ms / 1000, UTC)
    year, month, out = at.year, at.month, []
    for _ in range(n):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        out.append(f"{year:04d}-{month:02d}")
    return sorted(out)


def self_recall(his_days: dict, strangers: list, family_pairs: list,
                months: list[str]) -> list[dict]:
    """Each month judged exactly as the study judges a candidate, against a reference and
    self windows built from the rest of his days only. A month he did not trade is
    `insufficient`: counted neither way."""
    out = []
    for month in months:
        mine = [his_days[d] for d in sorted(his_days) if d.startswith(month)]
        rest = {d: r for d, r in his_days.items() if not d.startswith(month)}
        if not mine or not rest:
            out.append({"month": month, "verdict": verdict.INSUFFICIENT, "lr": None, "by": [],
                        "basis": None, "days": len(mine), "tests": {}})
            continue
        ref = assemble.his_reference(rest)
        ctx = assemble.panel_context(ref, assemble.self_splits(rest, ref), strangers,
                                     family_pairs)
        tests = assemble.tooling_tests(mine, ref, ctx)
        got = verdict.family_verdict(tests, verdict.TOOLING)
        out.append({"month": month, "verdict": got["verdict"], "lr": got["lr"], "by": got["by"],
                    "basis": got["basis"], "days": len(mine),
                    "tests": {name: (tests[name].get("judgement") or {}).get("status")
                              for name in verdict.TOOLING}})
    return out


def recall_ok(results: list[dict]) -> bool:
    """Every month that could be judged reads FOR, and at least one could."""
    judged = [r for r in results if r.get("verdict") != verdict.INSUFFICIENT]
    return bool(judged) and all(r.get("verdict") == verdict.FOR for r in judged)


def _check(ok: bool, value, bar, why: str) -> dict:
    return {"ok": bool(ok), "value": value, "bar": bar, "why": why}


def _age_hours(computed_at, now_ms: int) -> float | None:
    try:
        at = datetime.fromisoformat(str(computed_at))
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    return round((now_ms - at.timestamp() * 1000) / _HOUR_MS, 2)


def readiness(*, now_ms: int, latest: dict, read_without_coverage: list,
              unreadable_archives: list, recall: list[dict], false_for: list,
              census: dict | None) -> dict:
    """The gate, from what the caller read. `ready` only when every check passes."""
    panels = latest.get("panels") or {}
    rows = [r for r in latest.get("wallets") or [] if isinstance(r, dict)]
    age = _age_hours(latest.get("computed_at"), now_ms)
    live_days = round((now_ms - PHASE1_LIVE_MS) / (24 * _HOUR_MS), 2)
    in_run = len(latest.get("unreadable") or [])
    strangers = panels.get("strangers")
    checks = {
        "study_fresh": _check(age is not None and age <= FRESH_HOURS, age, FRESH_HOURS,
                              "hours since the study last wrote its summary"),
        "in_production": _check(live_days >= MIN_PRODUCTION_DAYS, live_days,
                                MIN_PRODUCTION_DAYS, "days since Phase 1 went live"),
        "records": _check(
            len(rows) == latest.get("studied") and not read_without_coverage
            and not unreadable_archives and not in_run,
            {"rows": len(rows), "studied": latest.get("studied"),
             "read_without_coverage": sorted(read_without_coverage),
             "unreadable_archives": sorted(unreadable_archives), "unreadable_in_run": in_run},
            "every wallet read has records with coverage; none unreadable",
            "acceptance 2: every wallet read has day records with coverage"),
        "strangers": _check(isinstance(strangers, int) and strangers >= calibration.MIN_STRANGERS,
                            strangers, calibration.MIN_STRANGERS,
                            "strangers with a decided style, T1's panel"),
        "self_recall": _check(recall_ok(recall), recall, "every judged month reads for",
                              "acceptance 4: his recent months, each judged against the "
                              "rest of him, read tooling FOR"),
        "no_false_for": _check(not false_for, sorted(false_for, key=lambda f: f["wallet"]), [],
                               "acceptance 3: no wallet showing a trait he never shows "
                               "reads FOR or MIXED"),
    }
    verdicts = Counter(((r.get("families") or {}).get("tooling") or {}).get("verdict")
                       for r in rows)
    info = {
        "census": {k: (census or {}).get(k) for k in ("measured", "attempted", "habit_measured")},
        "family_pairs": {"value": panels.get("family_pairs"),
                         "bar": calibration.MIN_SAME_OP["family"]},
        "tooling_verdicts": dict(sorted((str(k), v) for k, v in verdicts.items())),
    }
    return {"ready": all(c["ok"] for c in checks.values()), "checks": checks, "info": info,
            "evaluated_at": datetime.fromtimestamp(now_ms / 1000, UTC).isoformat()}
