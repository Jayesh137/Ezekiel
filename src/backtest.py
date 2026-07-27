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

from src.utils import DATA_DIR

WINDOW_DAYS = 21
MIN_WINDOW_FILLS = 50
# A fill count says nothing about window quality for a TWAP trader: 5,497 fills
# concentrated in 4 sessions is not 5,497 independent observations. Both windows
# must also span enough separate trading days for a self-match to mean anything.
# Below this the test is declared INCONCLUSIVE rather than pass or fail — asserting
# either from two 4-day windows would be a fabricated result.
MIN_WINDOW_DAYS = 8
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
        return {"self_score": prev["self_score"], "validated_at": prev.get("run_at")}
    return prev.get("last_validated")


def split_windows(fills: list[dict], window_days: int = WINDOW_DAYS) -> tuple[list, list]:
    """Two disjoint windows anchored to the last fill: (older, recent).
    Falls back to halving the full history when the windows are too thin."""
    if not fills:
        return [], []
    fills = sorted(fills, key=lambda f: f.get("time", 0))
    last_ts = fills[-1].get("time", 0)
    day_ms = 86_400_000
    recent_start = last_ts - window_days * day_ms
    older_start = recent_start - window_days * day_ms

    recent = [f for f in fills if f.get("time", 0) >= recent_start]
    older = [f for f in fills if older_start <= f.get("time", 0) < recent_start]

    if len(recent) < MIN_WINDOW_FILLS or len(older) < MIN_WINDOW_FILLS:
        mid = len(fills) // 2
        older, recent = fills[:mid], fills[mid:]
    return older, recent


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
    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "older_fills": len(older),
        "recent_fills": len(recent),
        "older_distinct_days": older_days,
        "recent_distinct_days": recent_days,
    }
    if len(older) < MIN_WINDOW_FILLS or len(recent) < MIN_WINDOW_FILLS:
        report.update({"passed": None, "reason": "insufficient fills for two windows"})
        _save(report)
        print(f"[backtest] INCONCLUSIVE: {len(older)}/{len(recent)} fills — "
              f"need {MIN_WINDOW_FILLS} in each window")
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
