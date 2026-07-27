# src/backtest.py
"""Self-match backtest: proof that the scorer actually recognizes Ezekiel.

Splits the target's own history into two disjoint time windows, fingerprints
each with the exact code path candidates go through, and checks that
window-vs-window similarity outranks every stranger from the latest scan.
If Ezekiel-past can't be matched to Ezekiel-recent, no weight tweak should be
trusted — this runs after every fingerprint rebuild so scoring changes are
validated instead of guessed.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import thresholds as th
from src.utils import DATA_DIR

WINDOW_DAYS = 21
MIN_WINDOW_FILLS = 50
# A fill count says nothing about window quality for a TWAP trader: 5,496 fills
# concentrated in 4 sessions is not 5,496 independent observations. Both windows
# must also span enough separate trading days for a self-match to mean anything.
# Below this the test is declared INCONCLUSIVE rather than pass or fail — asserting
# either from two 4-day windows would be a fabricated result.
MIN_WINDOW_DAYS = 8
# Preferred active days per window when history allows. Roughly matches the active
# days a leaderboard wallet accumulates in the scanner's 21-day lookback, so the
# target is characterised from a comparable amount of behaviour to its strangers.
TARGET_WINDOW_DAYS = 12
PASS_MARGIN = 0.05
REPORT_PATH = DATA_DIR.parent / "profile" / "backtest.json"


def distinct_days(fills: list[dict]) -> int:
    """Separate UTC days the window actually contains fills on."""
    return len({int(f.get("time", 0)) // 86_400_000 for f in fills if f.get("time")})


def _previous_validation() -> dict | None:
    """The most recent PROVEN self-match, carried across inconclusive runs.

    Read from the report we are about to overwrite, so a validated ceiling
    survives a quiet stretch instead of being lost the first day the target stops
    trading enough to re-test.
    """
    if not REPORT_PATH.exists():
        return None
    try:
        with open(REPORT_PATH) as f:
            prev = json.load(f)
    except (OSError, ValueError):
        return None
    if prev.get("passed") and prev.get("self_score"):
        # Full provenance so a carried-forward threshold set can be audited, and
        # so it can be rejected if the scoring schema has moved on since.
        return {
            "self_score": prev["self_score"],
            "validated_at": prev.get("run_at"),
            "scoring_schema": prev.get("scoring_schema"),
            "best_stranger_score": prev.get("best_stranger_score"),
            "margin": prev.get("margin_over_best_stranger"),
            "strangers_scored": prev.get("strangers_scored"),
            "windows": prev.get("windows"),
        }
    return prev.get("last_validated")


def split_windows(fills: list[dict],
                  target_days: int = TARGET_WINDOW_DAYS) -> tuple[list, list]:
    """Two disjoint chronological windows selected by ACTIVE TRADING DAYS.

    Previously this sliced two fixed 21-calendar-day windows anchored to the last
    fill. This target trades sparsely — 61 active days across a 166-day span — so
    the last 21 calendar days held only 4 active days, and the 21 before them
    another 4. MIN_WINDOW_FILLS could not detect that: TWAP concentrates thousands
    of fills into single sessions (the busiest 5 days hold 40% of all fills), so
    both windows cleared the fill floor while discarding ~90% of usable history.

    Now: take the most recent 2N active days and split them in half, newest N as
    `recent` and the N before as `older`. The calendar span stretches as far back
    as needed to collect the days; it is never cherry-picked, and the two windows
    share no fill, tid, episode or calendar day by construction.
    """
    if not fills:
        return [], []
    fills = sorted(fills, key=lambda f: f.get("time", 0))
    day_of = {}
    for f in fills:
        ts = f.get("time")
        if ts:
            day_of.setdefault(int(ts) // 86_400_000, []).append(f)

    active_days = sorted(day_of)
    if len(active_days) < 2 * MIN_WINDOW_DAYS:
        # Not enough separate sessions to build two honest windows. Return the
        # chronological halves so the caller can report exactly how short it fell.
        mid = len(active_days) // 2
        older = [f for d in active_days[:mid] for f in day_of[d]]
        recent = [f for d in active_days[mid:] for f in day_of[d]]
        return older, recent

    max_side = len(active_days) // 2
    per_side = max(MIN_WINDOW_DAYS, min(target_days, max_side))

    def build(n):
        older = [f for d in active_days[-2 * n:-n] for f in day_of[d]]
        recent = [f for d in active_days[-n:] for f in day_of[d]]
        return older, recent

    older, recent = build(per_side)
    # A low-volume trader can clear the day floor while missing the fill floor.
    # Widen by taking MORE active days rather than relaxing either requirement —
    # sufficiency is never weakened to manufacture a usable window.
    while (len(older) < MIN_WINDOW_FILLS or len(recent) < MIN_WINDOW_FILLS) \
            and per_side < max_side:
        per_side += 1
        older, recent = build(per_side)
    return older, recent


def window_summary(fills: list[dict]) -> dict:
    """Dates, active-day count and fill count for a window — published so the
    windows a verdict rests on can be audited."""
    ts = sorted(f.get("time", 0) for f in fills if f.get("time"))
    if not ts:
        return {"fills": 0, "active_days": 0, "first_day": None, "last_day": None,
                "calendar_span_days": 0}
    days = sorted({t // 86_400_000 for t in ts})
    fmt = "%Y-%m-%d"
    return {
        "fills": len(fills),
        "active_days": len(days),
        "first_day": datetime.fromtimestamp(ts[0] / 1000, tz=UTC).strftime(fmt),
        "last_day": datetime.fromtimestamp(ts[-1] / 1000, tz=UTC).strftime(fmt),
        "calendar_span_days": days[-1] - days[0] + 1,
    }


def window_leakage(older: list[dict], recent: list[dict]) -> dict:
    """Prove the two windows are disjoint. Any non-zero value invalidates the test."""
    o_tid = {f.get("tid") for f in older if f.get("tid") is not None}
    r_tid = {f.get("tid") for f in recent if f.get("tid") is not None}
    o_day = {int(f["time"]) // 86_400_000 for f in older if f.get("time")}
    r_day = {int(f["time"]) // 86_400_000 for f in recent if f.get("time")}
    return {
        "shared_tids": len(o_tid & r_tid),
        "shared_days": len(o_day & r_day),
        "chronological": (max(o_day) < min(r_day)) if (o_day and r_day) else True,
    }


def zero_dimensions(dims: dict) -> list[str]:
    """Dimensions that scored exactly 0.0 for the trader against his own history.

    None means "excluded, not enough data" and is fine. A hard 0.0 means the
    dimension actively votes against the true trader — that is always a scoring
    bug, and it shipped undetected for months on hold_duration.
    """
    return sorted(k for k, v in dims.items()
                  if isinstance(v, (int, float)) and float(v) == 0.0)


def run_backtest() -> dict:
    from src.fingerprint import load_fills, load_positions_latest
    from src.scanner import _effective_thresholds, build_candidate_fingerprint, compute_similarity
    from src.utils import load_config

    fills = load_fills()
    older, recent = split_windows(fills)
    older_days, recent_days = distinct_days(older), distinct_days(recent)
    total_active = distinct_days(fills)
    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "scoring_schema": th.SCORING_SCHEMA,
        "older_fills": len(older),
        "recent_fills": len(recent),
        "older_distinct_days": older_days,
        "recent_distinct_days": recent_days,
        # Published so a verdict can be audited: exactly which days were used,
        # how far the calendar had to stretch, and proof the windows are disjoint.
        "windows": {
            "selection": "activity_based",
            "target_days_per_window": TARGET_WINDOW_DAYS,
            "min_days_per_window": MIN_WINDOW_DAYS,
            "total_active_days_available": total_active,
            "older": window_summary(older),
            "recent": window_summary(recent),
            "leakage": window_leakage(older, recent),
            "excluded": (
                f"{max(0, total_active - older_days - recent_days)} older active "
                f"day(s) outside the two most recent windows"),
        },
    }
    if len(older) < MIN_WINDOW_FILLS or len(recent) < MIN_WINDOW_FILLS:
        report.update({"passed": None,
                       "reason": "insufficient fills for two windows",
                       "last_validated": _previous_validation()})
        _save(report)
        print(f"[backtest] INCONCLUSIVE: {len(older)}/{len(recent)} fills — "
              f"need {MIN_WINDOW_FILLS} in each window")
        return report

    leak = report["windows"]["leakage"]
    if leak["shared_tids"] or leak["shared_days"] or not leak["chronological"]:
        report.update({"passed": None,
                       "reason": f"window leakage detected: {leak}",
                       "last_validated": _previous_validation()})
        _save(report)
        print(f"[backtest] INCONCLUSIVE: windows are not disjoint — {leak}")
        return report

    if older_days < MIN_WINDOW_DAYS or recent_days < MIN_WINDOW_DAYS:
        # Not a pass and not a failure: the data cannot support the assertion
        # either way. Reporting FAIL here would raise a daily alarm about a trader
        # who has simply been quiet; reporting PASS would be fabricated.
        report.update({
            "passed": None,
            "reason": (f"inconclusive: windows span {older_days} and {recent_days} "
                       f"distinct trading days, need {MIN_WINDOW_DAYS} each. The "
                       f"target has not traded on enough separate days to validate "
                       f"the scorer against his own history."),
            # Carry the last PROVEN ceiling forward. Without it, thresholds revert
            # to the raw config 0.90 — unreachable for a trader whose demonstrated
            # ceiling is ~0.59 — which would silently empty the watchlist during
            # exactly the quiet period a migration is most likely to happen in.
            "last_validated": _previous_validation(),
        })
        _save(report)
        print(f"[backtest] INCONCLUSIVE: windows span {older_days} and {recent_days} "
              f"distinct trading days (need {MIN_WINDOW_DAYS} each).")
        print("[backtest] Not a failure — the target has been too quiet to test "
              "the scorer. Alerting still runs on config thresholds.")
        return report

    positions = load_positions_latest()
    if isinstance(positions, dict) and "assetPositions" not in positions:
        positions = positions.get("perp", positions)
    # Resolved once and threaded through: compute_similarity would otherwise
    # re-read backtest.json for every stranger it scores.
    eff = _effective_thresholds(load_config()["alert_thresholds"])

    # Same account on both sides is legitimate: it IS the same account.
    target_fp = build_candidate_fingerprint(recent, positions)
    self_fp = build_candidate_fingerprint(older, positions)
    self_score, self_dims, self_evidence = compute_similarity(target_fp, self_fp, eff)

    # Strangers: fingerprint summaries from the latest scan sweep
    strangers = []
    scans_path = DATA_DIR / "scans" / "latest.json"
    if scans_path.exists():
        try:
            with open(scans_path) as f:
                scan = json.load(f)
            for r in scan.get("results", []):
                cand_fp = r.get("fingerprint")
                if cand_fp:
                    s, _, _ = compute_similarity(target_fp, cand_fp, eff)
                    strangers.append({"wallet": r["wallet"], "score": s})
        except Exception as e:
            print(f"[backtest] Could not score strangers: {e}")

    strangers.sort(key=lambda s: s["score"], reverse=True)
    best_stranger = strangers[0]["score"] if strangers else None
    rank = 1 + sum(1 for s in strangers if s["score"] >= self_score)
    margin = round(self_score - best_stranger, 4) if best_stranger is not None else None

    zeros = zero_dimensions(self_dims)
    vetoes = self_evidence.get("vetoes", [])
    failures = []
    if rank != 1:
        failures.append(f"self-match ranked {rank}, not 1")
    if margin is not None and margin < PASS_MARGIN:
        failures.append(f"margin {margin} below required {PASS_MARGIN}")
    if zeros:
        failures.append(f"dimension(s) scored 0.0 against the trader's own "
                        f"history: {', '.join(zeros)}")
    if vetoes:
        failures.append(f"self-match tripped style vetoes: {vetoes}")
    passed = not failures

    report.update({
        "self_score": self_score,
        "self_dimensions": self_dims,
        "self_vetoes": vetoes,
        "zero_dimensions": zeros,
        "excluded_dimensions": sorted(k for k, v in self_dims.items() if v is None),
        "strangers_scored": len(strangers),
        "best_stranger_score": best_stranger,
        "top_strangers": strangers[:5],
        "self_rank": rank,
        "margin_over_best_stranger": margin,
        "failures": failures,
        "passed": passed,
    })
    _save(report)

    status = "PASS" if passed else "FAIL"
    print(f"[backtest] {status}: self-match {self_score:.4f}, rank {rank} "
          f"of {len(strangers) + 1}, margin {margin}")
    for f in failures:
        print(f"[backtest] FAILURE: {f}")
    if zeros:
        print("[backtest] A 0.0 self-dimension is always a scoring bug: it votes "
              "against the true trader. (None = excluded for thin data, which is fine.)")
    if not passed:
        print("[backtest] WARNING: the scorer cannot reliably distinguish Ezekiel "
              "from strangers — do not trust match alerts until this passes.")
    return report


def _save(report: dict) -> None:
    REPORT_PATH.parent.mkdir(exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    import sys as _sys
    _report = run_backtest()
    # Non-zero exit when the scorer can't recognise its own target, so the daily
    # analyze workflow goes red instead of quietly shipping a broken fingerprint.
    if _report.get("passed") is False:
        _sys.exit(1)
