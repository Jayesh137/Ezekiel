# src/calibration.py
"""Population calibration for similarity scores.

Cosine similarity on smooth distributions gives any two active traders a high
baseline score, so a raw 0.8 can mean 'generic active trader' rather than
'probably the same human'. This module keeps a rolling population of scores
from ordinary leaderboard wallets so a score can be expressed as a percentile:
how unusual is this similarity compared to unrelated traders?
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR

POPULATION_PATH = DATA_DIR / "calibration" / "population.json"
MAX_SAMPLES = 1000
# Below this many samples the population isn't representative; callers should
# fall back to raw-threshold behavior.
MIN_SAMPLES_FOR_GATE = 50
ALERT_PERCENTILE = 99.0


def load_population() -> list[float]:
    if not POPULATION_PATH.exists():
        return []
    try:
        with open(POPULATION_PATH) as f:
            data = json.load(f)
        return [float(s["score"]) for s in data.get("samples", [])]
    except Exception:
        return []


def record_population_scores(scores: list[float]) -> int:
    """Append this sweep's leaderboard scores to the rolling population.
    Only unbiased leaderboard scans belong here — priority targets are
    pre-selected by linkage evidence and would skew the null distribution."""
    if not scores:
        return 0
    POPULATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    samples = []
    if POPULATION_PATH.exists():
        try:
            with open(POPULATION_PATH) as f:
                samples = json.load(f).get("samples", [])
        except Exception:
            samples = []
    now = datetime.now(UTC).isoformat()
    samples.extend({"score": round(float(s), 4), "recorded_at": now} for s in scores)
    samples = samples[-MAX_SAMPLES:]
    with open(POPULATION_PATH, "w") as f:
        json.dump({"updated_at": now, "samples": samples}, f)
    return len(samples)


def gate_active(population: list[float] | None = None) -> bool:
    """Whether the gate is ENFORCING (enough samples) or merely OBSERVING.

    Until this returns True the gate is open and only logs what it *would* have
    suppressed, so switching it on can't silently start dropping real leads.
    """
    if population is None:
        population = load_population()
    return len(population) >= MIN_SAMPLES_FOR_GATE


def score_percentile(score: float, population: list[float] | None = None) -> float | None:
    """Percentile of `score` within the population; None if too few samples."""
    if population is None:
        population = load_population()
    if not gate_active(population):
        return None
    below = sum(1 for s in population if s < score)
    return round(100.0 * below / len(population), 2)


def passes_percentile_gate(score: float, population: list[float] | None = None) -> bool:
    """True when the score is unusual enough vs the population to alert on.
    With too few samples the gate is open (raw thresholds still apply)."""
    pct = score_percentile(score, population)
    return pct is None or pct >= ALERT_PERCENTILE
