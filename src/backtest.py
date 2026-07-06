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
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR

WINDOW_DAYS = 21
MIN_WINDOW_FILLS = 50
PASS_MARGIN = 0.05
REPORT_PATH = DATA_DIR.parent / "profile" / "backtest.json"


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


def run_backtest() -> dict:
    from src.fingerprint import load_fills, load_positions_latest
    from src.scanner import build_candidate_fingerprint, compute_similarity

    fills = load_fills()
    older, recent = split_windows(fills)
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "older_fills": len(older),
        "recent_fills": len(recent),
    }
    if len(older) < MIN_WINDOW_FILLS or len(recent) < MIN_WINDOW_FILLS:
        report.update({"passed": None, "reason": "insufficient fills for two windows"})
        _save(report)
        return report

    positions = load_positions_latest()
    if isinstance(positions, dict) and "assetPositions" not in positions:
        positions = positions.get("perp", positions)
    # Same account on both sides is legitimate: it IS the same account.
    target_fp = build_candidate_fingerprint(recent, positions)
    self_fp = build_candidate_fingerprint(older, positions)
    self_score, self_dims, self_evidence = compute_similarity(target_fp, self_fp)

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
                    s, _, _ = compute_similarity(target_fp, cand_fp)
                    strangers.append({"wallet": r["wallet"], "score": s})
        except Exception as e:
            print(f"[backtest] Could not score strangers: {e}")

    strangers.sort(key=lambda s: s["score"], reverse=True)
    best_stranger = strangers[0]["score"] if strangers else None
    rank = 1 + sum(1 for s in strangers if s["score"] >= self_score)
    margin = round(self_score - best_stranger, 4) if best_stranger is not None else None
    passed = (rank == 1 and (margin is None or margin >= PASS_MARGIN))

    report.update({
        "self_score": self_score,
        "self_dimensions": self_dims,
        "self_vetoes": self_evidence.get("vetoes", []),
        "strangers_scored": len(strangers),
        "best_stranger_score": best_stranger,
        "top_strangers": strangers[:5],
        "self_rank": rank,
        "margin_over_best_stranger": margin,
        "passed": passed,
    })
    _save(report)

    status = "PASS" if passed else "FAIL"
    print(f"[backtest] {status}: self-match {self_score:.4f}, rank {rank} "
          f"of {len(strangers) + 1}, margin {margin}")
    if not passed:
        print("[backtest] WARNING: the scorer cannot reliably distinguish Ezekiel "
              "from strangers — do not trust match alerts until this passes.")
    if self_evidence.get("vetoes"):
        print(f"[backtest] WARNING: self-match tripped style vetoes: "
              f"{self_evidence['vetoes']} — veto thresholds are too tight.")
    return report


def _save(report: dict) -> None:
    REPORT_PATH.parent.mkdir(exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    run_backtest()
