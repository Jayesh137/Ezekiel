# src/calibration.py
"""Population calibration for similarity scores.

Cosine similarity on smooth distributions gives any two active traders a high
baseline score, so a raw 0.8 can mean 'generic active trader' rather than
'probably the same human'. This module keeps a rolling population of scores
from ordinary leaderboard wallets so a score can be expressed as a percentile:
how unusual is this similarity compared to unrelated traders?
"""

import json
import math
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

# --- market rarity calibration --------------------------------------------------
#
# The scanner used to add a flat +0.12 for ANY shared xyz: market on the premise
# that HIP-3 markets are near-unique. Measurement disproved that: in a 30-wallet
# leaderboard sample, six wallets traded xyz:BRENTOIL. The flat bonus lifted three
# style-vetoed wallets to 0.57 — above the target's own 0.5395 self-match — and
# tiered them CONFIRMED_CANDIDATE.
#
# Rarity is now measured from the wallets the scanner already fingerprints on
# every sweep, and the bonus is scaled by it.
MARKET_FREQ_PATH = DATA_DIR / "calibration" / "market_frequency.json"
# Bounded rolling window: at most this many sweep observations, and none older
# than MARKET_WINDOW_DAYS. Market popularity drifts, so old sweeps must age out.
MAX_MARKET_OBSERVATIONS = 60
MARKET_WINDOW_DAYS = 30
# Below this many observed wallets the sample cannot support any rarity claim, so
# the bonus is zero. Conservative by construction: thin data means no bonus, never
# the maximum bonus.
MIN_ELIGIBLE_FOR_RARITY = 50
# At or above this share of eligible wallets a market is simply popular; sharing it
# is not evidence of anything. xyz:BRENTOIL measured ~0.20 here.
COMMON_FREQUENCY = 0.05
# The rarity floor that earns the full bonus: roughly one wallet in five hundred.
RARE_FREQUENCY = 0.002
MAX_MARKET_BONUS = 0.12

# A market may only be CALLED rare — in an alert subject, or as grounds for
# promoting a candidate — when measurement supports it. Deliberately stricter
# than "earns a bonus": a market can be mildly unusual (and score a small bonus)
# without being evidence strong enough to headline a CRITICAL alert.
#
# Measured example: xyz:BRENTOIL sits at ~26% of eligible wallets, so it is
# COMMON. It nevertheless produced "xyz: SIGNATURE MATCH — Same Rare HIP-3
# Markets" CRITICAL alerts, because the alert route classified rarity by the
# "xyz:" name prefix and never consulted this module.
RARE_MAX_FREQUENCY = 0.01      # traded by <=1% of eligible wallets

MARKET_RARE = "RARE"
MARKET_UNUSUAL = "UNUSUAL"
MARKET_COMMON = "COMMON"
MARKET_UNKNOWN = "UNKNOWN"     # insufficient sample — never treated as rare


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


# --- market rarity: measurement --------------------------------------------------

def record_market_observation(markets_by_wallet: dict[str, list],
                              path: Path | None = None) -> dict:
    """Record which markets each eligible wallet traded during one sweep.

    "Eligible" means the wallet cleared min_fills_for_comparison and was actually
    fingerprinted — the same population the similarity scores come from, so the
    frequencies describe the comparison set rather than all of Hyperliquid.

    Bounded: at most MAX_MARKET_OBSERVATIONS sweeps, none older than
    MARKET_WINDOW_DAYS.
    """
    p = path or MARKET_FREQ_PATH
    if not markets_by_wallet:
        return load_market_frequencies(p)

    counts: dict[str, int] = {}
    for markets in markets_by_wallet.values():
        for m in set(markets):
            counts[m] = counts.get(m, 0) + 1

    now = datetime.now(UTC)
    observation = {
        "recorded_at": now.isoformat(),
        "eligible_wallets": len(markets_by_wallet),
        "market_counts": counts,
    }

    p.parent.mkdir(parents=True, exist_ok=True)
    observations = []
    if p.exists():
        try:
            with open(p) as f:
                observations = json.load(f).get("observations", [])
        except (OSError, ValueError):
            observations = []

    observations.append(observation)
    cutoff = now.timestamp() - MARKET_WINDOW_DAYS * 86400
    kept = []
    for o in observations:
        try:
            if datetime.fromisoformat(o["recorded_at"]).timestamp() >= cutoff:
                kept.append(o)
        except (KeyError, TypeError, ValueError):
            continue
    kept = kept[-MAX_MARKET_OBSERVATIONS:]

    with open(p, "w") as f:
        json.dump({"updated_at": now.isoformat(),
                   "window_days": MARKET_WINDOW_DAYS,
                   "observations": kept}, f)
    return summarise_market_observations(kept)


def summarise_market_observations(observations: list[dict]) -> dict:
    """Collapse rolling observations into totals. Pure."""
    eligible = 0
    counts: dict[str, int] = {}
    stamps = []
    for o in observations:
        eligible += int(o.get("eligible_wallets", 0) or 0)
        for m, c in (o.get("market_counts") or {}).items():
            counts[m] = counts.get(m, 0) + int(c or 0)
        if o.get("recorded_at"):
            stamps.append(o["recorded_at"])
    return {
        "eligible_wallets": eligible,
        "market_counts": counts,
        "observations": len(observations),
        "first_observed": min(stamps) if stamps else None,
        "last_observed": max(stamps) if stamps else None,
        "sufficient": eligible >= MIN_ELIGIBLE_FOR_RARITY,
    }


def load_market_frequencies(path: Path | None = None) -> dict:
    p = path or MARKET_FREQ_PATH
    if not p.exists():
        return summarise_market_observations([])
    try:
        with open(p) as f:
            return summarise_market_observations(json.load(f).get("observations", []))
    except (OSError, ValueError):
        return summarise_market_observations([])


# --- market rarity: scoring (pure) -----------------------------------------------

def market_frequency(market: str, freq: dict) -> float:
    """Add-one (Laplace) smoothed share of eligible wallets trading `market`.

        f = (hits + 1) / (eligible + 2)

    Smoothing matters: a market seen once in a one-wallet sample would otherwise
    read as frequency 1.0 or, worse, an unseen market as 0.0 — infinitely rare, and
    thus maximally rewarded, on no evidence. Add-one pulls both toward 0.5, which
    lands above COMMON_FREQUENCY and therefore earns nothing.
    """
    eligible = int(freq.get("eligible_wallets", 0) or 0)
    hits = int((freq.get("market_counts") or {}).get(market, 0) or 0)
    return (hits + 1) / (eligible + 2)


def market_rarity_bonus(markets: list[str], freq: dict) -> tuple[float, list[str]]:
    """Bonus for shared markets, scaled by measured rarity. Pure.

    Per market, on a log scale between "popular" and "one in five hundred":

        f    = smoothed frequency
        b    = 0                                        if f >= COMMON_FREQUENCY
        b    = MAX * log(COMMON/f) / log(COMMON/RARE)   otherwise, clamped to [0, MAX]

    Log rather than linear because the interesting range spans two orders of
    magnitude: 5% and 0.2% differ far more in evidential weight than 50% and 45%.

    Several shared markets compound with diminishing returns (1, 1/2, 1/4, …) so a
    wallet touching many mildly-uncommon markets cannot accumulate the full bonus.

    Returns (bonus, human-readable explanations).
    """
    if not markets:
        return 0.0, []
    if not freq.get("sufficient"):
        return 0.0, [
            f"No market-rarity bonus: only {freq.get('eligible_wallets', 0)} wallets "
            f"observed (need {MIN_ELIGIBLE_FOR_RARITY}) — treating rarity as unproven"
        ]

    span = math.log(COMMON_FREQUENCY / RARE_FREQUENCY)
    scored = []
    for m in sorted(set(markets)):
        f = market_frequency(m, freq)
        if f >= COMMON_FREQUENCY:
            scored.append((0.0, m, f))
            continue
        frac = min(1.0, math.log(COMMON_FREQUENCY / f) / span)
        scored.append((MAX_MARKET_BONUS * frac, m, f))

    scored.sort(reverse=True)
    total = 0.0
    reasons = []
    for i, (b, m, f) in enumerate(scored):
        share = b / (2 ** i)
        total += share
        pct = f * 100
        if b <= 0:
            reasons.append(f"{m}: traded by ~{pct:.1f}% of scanned wallets — "
                           f"too common to be evidence (no bonus)")
        else:
            reasons.append(f"{m}: traded by ~{pct:.2f}% of scanned wallets — "
                           f"rarity bonus +{share:.4f}")
    total = round(min(MAX_MARKET_BONUS, total), 4)
    return total, reasons


# --- market rarity: classification (pure) -----------------------------------------

def classify_market(market: str, freq: dict) -> str:
    """Measured rarity class for one market.

    UNKNOWN when the sample is too small to support any claim — never RARE.
    This is the single authority on whether a market may be described as rare.
    """
    if not freq.get("sufficient"):
        return MARKET_UNKNOWN
    f = market_frequency(market, freq)
    if f <= RARE_MAX_FREQUENCY:
        return MARKET_RARE
    if f < COMMON_FREQUENCY:
        return MARKET_UNUSUAL
    return MARKET_COMMON


def rare_markets(markets: list, freq: dict) -> list:
    """Subset of `markets` that measurement classifies as genuinely rare.

    The alert routes must use this, never a name-prefix match: `xyz:` is a venue
    prefix, not evidence of rarity.
    """
    return sorted(m for m in set(markets or []) if classify_market(m, freq) == MARKET_RARE)


def describe_markets(markets: list, freq: dict) -> str:
    """Accurate human phrasing for an alert subject or reason line."""
    if not markets:
        return "no shared markets"
    if not freq.get("sufficient"):
        return (f"{len(markets)} shared HIP-3 market(s), rarity UNMEASURED "
                f"({freq.get('eligible_wallets', 0)} wallets observed, "
                f"need {MIN_ELIGIBLE_FOR_RARITY})")
    parts = []
    for m in sorted(set(markets)):
        cls = classify_market(m, freq)
        parts.append(f"{m} ({cls.lower()}, ~{market_frequency(m, freq) * 100:.1f}% of wallets)")
    return "; ".join(parts)
