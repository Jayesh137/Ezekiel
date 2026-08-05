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
import math
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

# Bump whenever scoring dimensions, weights or normalisation change. A validated
# threshold set is only reusable while the thing it validated still exists; a
# ceiling proven under different weights says nothing about the current scorer.
# 2026-08-05.1: activity normalisation changed from per-calendar-day to
# per-active-day (episodes_per_active_day / fills_per_active_day). That moves
# every activity score and the decision-frequency veto, so any ceiling proven
# under 2026-07-27.1 is not evidence about this scorer and must not carry over.
SCORING_SCHEMA = "2026-08-05.1"

# Where the thresholds in force came from. Published in scans/latest.json and
# rendered on the dashboard so the operating mode is never ambiguous.
SRC_CURRENT_VALIDATED = "CURRENT_VALIDATED"
SRC_CARRIED_FORWARD = "CARRIED_FORWARD"
SRC_POPULATION_FALLBACK = "POPULATION_WATCHLIST_FALLBACK"
SRC_OBSERVING = "OBSERVING"

# Population fallback: with no validated ceiling, a candidate may still earn a
# WATCHLIST slot by being extreme against the measured null distribution. This is
# deliberately stricter than the alerting percentile — it is a discovery aid, not
# an alert, and behaviour alone can never promote past WATCHLIST from here.
FALLBACK_WATCHLIST_PERCENTILE = 99.0
FALLBACK_MIN_POPULATION = 50

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
        # Default policy when nothing has been validated: watch, never alert.
        "policy": SRC_OBSERVING,
        "scoring_schema": SCORING_SCHEMA,
        "self_match_ceiling": None,
    }
    if not backtest_report:
        return eff

    if backtest_report.get("passed") and backtest_report.get("self_score"):
        achievable = float(backtest_report["self_score"])
        eff["policy"] = SRC_CURRENT_VALIDATED
        eff["validated_at"] = backtest_report.get("run_at")
        eff["provenance"] = _provenance(backtest_report)
    elif backtest_report.get("passed") is None and backtest_report.get("last_validated"):
        # Inconclusive run (the target has been too quiet to re-test). Reuse the
        # last PROVEN ceiling rather than snapping back to the raw config numbers:
        # 0.90 is unreachable for this trader, so reverting would empty the
        # watchlist during exactly the quiet stretch a migration hides in.
        lv = backtest_report["last_validated"]
        if not lv.get("self_score"):
            return eff
        if not schema_compatible(lv.get("scoring_schema")):
            # The ceiling was proven under a different scorer. Reusing it would be
            # asserting a validation that never happened for this code.
            eff["policy"] = SRC_OBSERVING
            eff["carry_forward_rejected"] = (
                f"validated under scoring schema {lv.get('scoring_schema')!r}, "
                f"current is {SCORING_SCHEMA!r} — revalidation required")
            return eff
        achievable = float(lv["self_score"])
        eff["source"] = "last_validated"
        eff["policy"] = SRC_CARRIED_FORWARD
        eff["validated_at"] = lv.get("validated_at")
        eff["provenance"] = _provenance(lv)
    else:
        # An outright FAILED self-match must not lower thresholds — the scorer is
        # known-unreliable, so the conservative config values stand.
        return eff
    adapted = dict(eff)
    adapted["high"] = max(MIN_HIGH, min(eff["high"], round(achievable - 0.02, 4)))
    adapted["medium"] = max(MIN_MEDIUM, min(eff["medium"], round(achievable - 0.07, 4)))
    adapted["low"] = max(MIN_LOW, min(eff["low"], round(achievable - 0.12, 4)))
    adapted["self_match_ceiling"] = achievable
    if (adapted["high"], adapted["medium"], adapted["low"]) != (
            eff["high"], eff["medium"], eff["low"]):
        # Preserve "last_validated" if that is where the ceiling came from, so the
        # dashboard can show the numbers are carried over rather than freshly proven.
        if adapted["source"] == "config":
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


def schema_compatible(schema: str | None) -> bool:
    """Whether a previously-validated threshold set still describes this scorer.

    Deliberately exact. A ceiling proven under different dimensions, weights or
    normalisation is not evidence about the current scorer, and a report written
    before schema tracking existed (None) cannot be shown to be compatible.
    """
    return schema is not None and schema == SCORING_SCHEMA


def _provenance(report: dict) -> dict:
    """Audit trail for whichever validation the thresholds rest on."""
    return {
        "validated_at": report.get("validated_at") or report.get("run_at"),
        "scoring_schema": report.get("scoring_schema"),
        "self_score": report.get("self_score"),
        "best_stranger_score": report.get("best_stranger_score"),
        "margin": report.get("margin") or report.get("margin_over_best_stranger"),
        "strangers_scored": report.get("strangers_scored"),
        "windows": report.get("windows"),
    }


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


def behavioural_gate(thresholds: dict) -> float:
    """The similarity at which a wallet starts counting as "trades like the target".

    This is the medium threshold — the tier the system itself labels
    WATCH_CLOSELY. Four modules used to compare against a literal 0.65 instead:
    risk.py, tracer.py and transfer_graph.py (three sites). Once thresholds
    adapted to the measured self-match ceiling the operating boundaries became
    0.5963 / 0.6463 / 0.6963, so 0.65 sat between tiers and meant nothing — a
    WATCH_CLOSELY wallet at 0.6463 contributed no corroboration and no risk
    points, while the literal itself never moved when the ceiling was
    re-validated.
    """
    return float(normalise(thresholds)["medium"])


def behavioural_strength(score: float, thresholds: dict) -> float:
    """Normalise a similarity score to 0.0–1.0 for weighting.

    0.0 at the gate, 1.0 at the proven self-match ceiling — what the REAL trader
    scores against his own adjacent history, and therefore the most any wallet
    can be expected to score. Weighting against a notional 1.0 instead was a
    structural under-count: with a ceiling of 0.7163 a perfect re-identification
    could reach only 19% of the weight allocated to it.

    Falls back to the high threshold when nothing has been validated, so the
    function stays defined under OBSERVING and after a failed self-match.
    """
    t = normalise(thresholds)
    try:
        score = float(score)
    except (TypeError, ValueError):
        return 0.0
    # NaN survives json.load and compares False against everything, so the clamp
    # max(0.0, min(1.0, nan)) returned 1.0 and a corrupt score earned full weight.
    if not math.isfinite(score):
        return 0.0
    gate = float(t["medium"])
    ceiling = t.get("self_match_ceiling") or t["high"]
    ceiling = float(ceiling)
    if ceiling <= gate:
        # Degenerate resolution (e.g. a ceiling at or below the gate). Keep the
        # function total rather than dividing by zero; anything at the gate or
        # above is then full strength.
        return 1.0 if float(score) >= gate else 0.0
    return max(0.0, min(1.0, (float(score) - gate) / (ceiling - gate)))


# Which threshold each combined-alert route is held to. These mirror the bars
# scanner.py already applied inline: a bridge depositor must clear `high`, while
# an amount+timing correlation is independent enough to alert from `low`.
COMBINED_ROUTE_THRESHOLDS = {
    "deposited_to_hl": "medium",
    "bridge_depositor": "high",
    "hl_transfer": "medium",
    "known_linked": "medium",
    "correlation": "low",
}


def combined_alert_ok(score: float, thresholds: dict, vetoes: list | None, *,
                      route: str) -> bool:
    """Whether a wallet carrying independent evidence AND a behavioural match may
    be promoted to a combined alert.

    The same rule lived in two places: scanner.py's priority sweep (veto-checked,
    current score, per-route threshold) and tracer.py's cross-reference
    (unchecked, all-time best score, hardcoded 0.65). Two systems deciding one
    outcome differently is exactly what this module exists to prevent, so both
    now route through here.
    """
    if not can_alert(vetoes):
        return False
    try:
        key = COMBINED_ROUTE_THRESHOLDS[route]
    except KeyError:
        raise ValueError(
            f"unknown combined-alert route {route!r}; "
            f"expected one of {sorted(COMBINED_ROUTE_THRESHOLDS)}") from None
    return float(score) >= float(normalise(thresholds)[key])


def can_alert(vetoes: list | None) -> bool:
    """A style-vetoed wallet never produces a high-confidence alert on ANY route.

    Previously only the behavioural route checked vetoes; the combined, xyz:,
    vault and linkage routes did not, so the false positive the veto feature was
    built to stop could still reach the inbox sideways.
    """
    return not vetoes


def population_fallback(score: float, percentile: float | None,
                        population_size: int, vetoes: list | None,
                        corroborated: bool) -> dict | None:
    """Disposition when no validated ceiling exists.

    Raw config thresholds are unreachable for this trader, so applying them makes
    behavioural discovery silently invisible. Instead judge the score against the
    measured null distribution: a wallet in the extreme tail of the population is
    worth looking at even though we cannot currently prove the scorer works.

    Behaviour alone is capped at WATCHLIST — an unvalidated scorer must never page
    anyone on its own. Independent corroboration (fund flow, HL-native transfer,
    deposit correlation, transfer-graph linkage) is what promotes to ALERT.

    Returns None when calibration cannot support even this, leaving the caller in
    OBSERVING mode.
    """
    if vetoes:
        return {"action": ACTION_WATCHLIST, "tier": TIER_WEAK,
                "reasons": ["retained for review despite style veto"],
                "blockers": [f"style veto: {'; '.join(vetoes)}"],
                "policy": SRC_POPULATION_FALLBACK}

    if population_size < FALLBACK_MIN_POPULATION or percentile is None:
        return None

    if percentile < FALLBACK_WATCHLIST_PERCENTILE:
        return None

    reasons = [
        f"score {score:.4f} is in the top {100 - percentile:.2f}% of "
        f"{population_size} scanned wallets (percentile {percentile:.2f})",
        "self-match validation unavailable — judged against the measured "
        "population instead of a proven threshold",
    ]
    if corroborated:
        reasons.append("independently corroborated by fund-flow / HL-native / "
                       "correlation / linkage evidence")
        return {"action": ACTION_ALERT, "tier": TIER_WATCH, "reasons": reasons,
                "blockers": [], "policy": SRC_POPULATION_FALLBACK}

    return {
        "action": ACTION_WATCHLIST, "tier": TIER_WEAK, "reasons": reasons,
        "blockers": ["behaviour alone cannot alert while the scorer is "
                     "unvalidated — needs independent corroboration"],
        "policy": SRC_POPULATION_FALLBACK,
    }


def disposition(score: float, thresholds: dict, *, vetoes: list | None = None,
                percentile_ok: bool = True, sustained: bool = True,
                rare_overlap: bool = False,
                score_without_market_bonus: float | None = None,
                percentile: float | None = None,
                population_size: int = 0,
                corroborated: bool = False) -> dict:
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
    policy = t.get("policy", SRC_CURRENT_VALIDATED)

    # No validated ceiling: the raw config thresholds are unreachable here, so
    # applying them would silently hide every behavioural lead. Judge against the
    # measured population instead, capped at WATCHLIST unless corroborated.
    if policy == SRC_OBSERVING:
        fb = population_fallback(score, percentile, population_size, vetoes,
                                 corroborated)
        if fb is not None:
            return fb
        return {
            "action": ACTION_BACKGROUND if score < t["low"] else ACTION_WATCHLIST,
            "tier": classify(score, t),
            "reasons": ["retained without alerting: scorer unvalidated and "
                        "calibration population too small to rank this score"],
            "blockers": ["OBSERVING — no validated threshold and insufficient "
                         "calibration data"],
            "policy": SRC_OBSERVING,
        }

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
        # Persistence exists to filter a single lucky scoring window in the
        # BEHAVIOUR-ONLY case. Independent evidence — funds actually reaching the
        # wallet, an amount/timing correlation, address reuse, or the target
        # falling silent while a lookalike appears — is a second, separate
        # observation, so it serves the same purpose and waives the wait.
        #
        # Deliberately narrow: corroboration waives ONLY persistence. Style
        # vetoes, the percentile gate and the shared-market-bonus guard all still
        # apply, so this cannot become a bypass route.
        if corroborated:
            reasons.append("persistence waived: independently corroborated by "
                           "non-behavioural evidence")
        else:
            blockers.append("awaiting persistence (needs 2 consecutive high scans)")

    if not blockers:
        reasons.append(f"score {score:.4f} >= high threshold {t['high']:.4f}")
        if rare_overlap:
            reasons.append("corroborated by shared rare markets")
        return {"action": ACTION_ALERT, "tier": classify(score, t),
                "reasons": reasons, "blockers": [], "policy": policy}

    if score >= t["low"] or rare_overlap:
        if rare_overlap:
            reasons.append("shares uncommon markets — kept regardless of score")
        else:
            reasons.append(f"score {score:.4f} >= low threshold {t['low']:.4f}")
        return {"action": ACTION_WATCHLIST, "tier": classify(score, t),
                "reasons": reasons, "blockers": blockers, "policy": policy}

    return {"action": ACTION_BACKGROUND, "tier": TIER_BACKGROUND,
            "reasons": [], "blockers": blockers, "policy": policy}
