# src/thresholds.py
"""Single source of truth for match thresholds, tiers, and candidate disposition.

Before this module the same numbers lived in four places that disagreed:
config.json, scanner._effective_thresholds (backtest-adapted at runtime),
scanner.classify_match (hardcoded 0.90/0.80/0.65) and the dashboard (hardcoded
again). A wallet could email as a HIGH match, be labelled WEAK_LEAD in the JSON
and render muted grey in the UI. Everything now resolves through resolve() and
the resolved values are written into scans/latest.json so the dashboard reads
the same numbers the alerts used.

Disposition is deliberately three-way. Recall matters more than a clean inbox
here: a suppressed candidate is still WATCHLISTed with its evidence intact, so
vetoes and the percentile gate downgrade rather than erase.
"""

import json
from pathlib import Path

# A style-vetoed candidate is capped here for ranking/display so it always sorts
# below clean candidates. The cap is NOT what blocks alerting — `vetoed` is a
# flag checked by can_alert(); relying on the score alone meant a bonus could
# lift a vetoed wallet back over an alert threshold.
VETO_SCORE_CAP = 0.45
# Bonuses (xyz:, vault, referral, linkage) may lift a vetoed wallet this far so
# it stays visible and rankable, but can_alert() still refuses to promote it.
VETO_BONUS_CEILING = 0.60

TIER_CONFIRMED = "CONFIRMED_CANDIDATE"
TIER_WATCH = "WATCH_CLOSELY"
TIER_WEAK = "WEAK_LEAD"
TIER_BACKGROUND = "BACKGROUND"

ACTION_ALERT = "ALERT"
ACTION_WATCHLIST = "WATCHLIST"
ACTION_BACKGROUND = "BACKGROUND"

# Floor for the adapted high threshold — a sanity bound, not a veto defence.
# It deliberately sits BELOW VETO_BONUS_CEILING: with a self-match ceiling near
# 0.53, flooring high at 0.61 would put the alert threshold above what the true
# trader can score, guaranteeing the false negative this system exists to avoid.
# A bonus-lifted vetoed wallet may therefore share the alert score band, which is
# harmless because can_alert() rejects it by flag rather than by score.
MIN_HIGH = VETO_SCORE_CAP + 0.01
MIN_MEDIUM = VETO_SCORE_CAP
MIN_LOW = 0.40


def resolve(alert_thresholds: dict, backtest_report: dict | None = None) -> dict:
    """Resolve effective thresholds from config, adapted to the self-match ceiling.

    The scorer's realistic ceiling for the true trader is what he scores against
    his own adjacent windows. Config thresholds above that ceiling would
    guarantee missing the migration, so they are lowered toward it. Precision is
    protected by the percentile gate, the persistence requirement and the style
    vetoes instead of by an unreachable raw number.

    Pure function: takes the parsed report, does no I/O.
    """
    eff = {
        "high": float(alert_thresholds["similarity_high"]),
        "medium": float(alert_thresholds["similarity_medium"]),
        "low": float(alert_thresholds["similarity_low"]),
        "source": "config",
        "self_match_ceiling": None,
    }
    if not backtest_report:
        return eff

    if not backtest_report.get("passed") or not backtest_report.get("self_score"):
        return eff

    achievable = float(backtest_report["self_score"])
    adapted = dict(eff)
    adapted["high"] = max(MIN_HIGH, min(eff["high"], round(achievable - 0.02, 4)))
    adapted["medium"] = max(MIN_MEDIUM, min(eff["medium"], round(achievable - 0.07, 4)))
    adapted["low"] = max(MIN_LOW, min(eff["low"], round(achievable - 0.12, 4)))
    adapted["self_match_ceiling"] = achievable
    if (adapted["high"], adapted["medium"], adapted["low"]) != (
            eff["high"], eff["medium"], eff["low"]):
        adapted["source"] = "backtest_adapted"
    return adapted


def load_backtest_report(profile_dir: Path) -> dict | None:
    """Read profile/backtest.json. Returns None (with a visible warning) when the
    report is missing or unreadable — silence here previously hid the fact that
    unreachable config thresholds were in force."""
    path = Path(profile_dir) / "backtest.json"
    if not path.exists():
        print("[thresholds] WARNING: no backtest.json — using raw config thresholds. "
              "Run src/backtest.py so thresholds can adapt to the self-match ceiling.")
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f"[thresholds] WARNING: could not read {path}: {e}. "
              f"Falling back to raw config thresholds.")
        return None


def normalise(thresholds: dict) -> dict:
    """Accept either the resolved shape or raw config keys.

    Scan files written before the threshold unification carry
    similarity_high/medium/low. The dashboard's getThresholds() has the same
    fallback, so both sides tier old data identically instead of raising.
    """
    if "high" in thresholds:
        return thresholds
    if "similarity_high" in thresholds:
        return {
            "high": float(thresholds["similarity_high"]),
            "medium": float(thresholds["similarity_medium"]),
            "low": float(thresholds["similarity_low"]),
            "source": "legacy",
            "self_match_ceiling": None,
        }
    raise KeyError(f"unrecognised threshold shape: {sorted(thresholds)}")


def classify(score: float, thresholds: dict) -> str:
    """Tier label derived from the SAME effective thresholds used for alerting."""
    t = normalise(thresholds)
    if score >= t["high"]:
        return TIER_CONFIRMED
    if score >= t["medium"]:
        return TIER_WATCH
    if score >= t["low"]:
        return TIER_WEAK
    return TIER_BACKGROUND


def can_alert(vetoes: list | None) -> bool:
    """A style-vetoed wallet never produces a high-confidence alert on ANY route.

    Previously only the behavioural route checked vetoes; the combined, xyz:,
    vault and linkage routes did not, so the false positive the veto feature was
    built to stop could still reach the inbox sideways.
    """
    return not vetoes


def disposition(score: float, thresholds: dict, *, vetoes: list | None = None,
                percentile_ok: bool = True, sustained: bool = True,
                rare_overlap: bool = False,
                score_without_market_bonus: float | None = None) -> dict:
    """Decide promote / watchlist / drop, and record why.

    Recall-biased: anything clearing `low`, or carrying a rare-market signature,
    stays visible on the watchlist even when suppressed from alerting. Only
    genuinely unremarkable wallets fall through to BACKGROUND.

    `score_without_market_bonus` enforces that shared markets can corroborate a
    match but never manufacture one: if removing the rarity bonus drops the wallet
    below the high threshold, it is watchlisted rather than promoted. Trading the
    same instrument is weak evidence of common ownership no matter how rare the
    instrument is.
    """
    t = normalise(thresholds)
    reasons = []
    blockers = []

    if vetoes:
        blockers.append(f"style veto: {'; '.join(vetoes)}")
    if score < t["high"]:
        blockers.append(f"score {score:.4f} below high threshold {t['high']:.4f}")
    elif (score_without_market_bonus is not None
          and score_without_market_bonus < t["high"]):
        blockers.append(
            f"clears the high threshold only via the shared-market bonus "
            f"({score_without_market_bonus:.4f} without it) — market overlap alone "
            f"is not evidence of common ownership")
    if not percentile_ok:
        blockers.append("score not unusual vs scanned population (percentile gate)")
    if not sustained:
        blockers.append("awaiting persistence (needs 2 consecutive high scans)")

    if not blockers:
        reasons.append(f"score {score:.4f} >= high threshold {t['high']:.4f}")
        if rare_overlap:
            reasons.append("corroborated by shared rare markets")
        return {"action": ACTION_ALERT, "tier": classify(score, t),
                "reasons": reasons, "blockers": []}

    if score >= t["low"] or rare_overlap:
        if rare_overlap:
            reasons.append("shares uncommon markets — kept regardless of score")
        else:
            reasons.append(f"score {score:.4f} >= low threshold {t['low']:.4f}")
        return {"action": ACTION_WATCHLIST, "tier": classify(score, t),
                "reasons": reasons, "blockers": blockers}

    return {"action": ACTION_BACKGROUND, "tier": TIER_BACKGROUND,
            "reasons": [], "blockers": blockers}
