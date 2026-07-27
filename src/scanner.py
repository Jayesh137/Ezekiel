# src/scanner.py
"""Scans Hyperliquid leaderboard for wallets matching the Ezekiel fingerprint."""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import requests

from src.utils import (
    load_config, hl_post, append_records, save_latest, DATA_DIR, etherscan_get, read_cursor
)
from src.fingerprint import (
    build_fingerprint, compute_asset_preferences, compute_timing_profile,
    compute_trade_sequencing, compute_position_sizing,
)
from src.alerts import (
    alert_behavioral_match, alert_combined_match, alert_vault_match,
    alert_migration_correlation, alert_xyz_signature_match, alert_linkage_match,
)
from src import calibration
from src import thresholds as th
from src.thresholds import VETO_SCORE_CAP, VETO_BONUS_CEILING


def _effective_thresholds(alert_thresholds: dict) -> dict:
    """Resolve effective thresholds via the shared policy module.

    Kept as a thin wrapper so the scanner reads naturally; all the logic (and the
    now-visible warning when backtest.json is missing or unreadable) lives in
    src/thresholds.py, which the dashboard's numbers also derive from.
    """
    report = th.load_backtest_report(DATA_DIR.parent / "profile")
    eff = th.resolve(alert_thresholds, report)
    if eff["source"] == "backtest_adapted":
        print(f"[scanner] Backtest-adapted thresholds: high={eff['high']}, "
              f"medium={eff['medium']}, low={eff['low']} "
              f"(self-match ceiling {eff['self_match_ceiling']})")
    else:
        print(f"[scanner] Config thresholds in force: high={eff['high']}, "
              f"medium={eff['medium']}, low={eff['low']}")
    return eff


def _sustained_high(wallet: str, threshold: float, n: int = 2) -> bool:
    """True when the candidate's last n persisted scans (including the current
    one) all cleared the threshold — one lucky window shouldn't page anyone."""
    path = DATA_DIR / "candidates" / f"{wallet.lower()}.json"
    if not path.exists():
        return False
    try:
        with open(path) as f:
            history = json.load(f).get("score_history", [])
    except Exception:
        return False
    return len(history) >= n and all(h.get("score", 0) >= threshold for h in history[-n:])


def evaluate_candidate(wallet: str, score: float, eff: dict,
                       population: list[float] | None = None,
                       vetoes: list | None = None,
                       rare_overlap: bool = False) -> dict:
    """Full disposition for a candidate: promote, watchlist, or drop — with reasons.

    Every alert route consults this, so a style veto or a failed percentile gate
    blocks promotion consistently instead of only on the behavioural route. The
    returned record is persisted so the dashboard can show WHY a wallet was or
    wasn't promoted.
    """
    d = th.disposition(
        score, eff,
        vetoes=vetoes,
        percentile_ok=calibration.passes_percentile_gate(score, population),
        sustained=_sustained_high(wallet, eff["high"]),
        rare_overlap=rare_overlap,
    )
    if d["blockers"] and score >= eff["low"]:
        print(f"[scanner] {wallet[:10]}... {score:.4f} -> {d['action']}: "
              f"{'; '.join(d['blockers'])}")
    return d


def _should_alert_behavioral(wallet: str, score: float, eff: dict,
                             population: list[float] | None = None,
                             vetoes: list | None = None) -> bool:
    """True only when the candidate is promoted to a high-confidence alert."""
    return evaluate_candidate(wallet, score, eff, population, vetoes)["action"] == th.ACTION_ALERT


def fetch_leaderboard() -> list[dict]:
    """Fetch top wallets from the Hyperliquid leaderboard."""
    config = load_config()
    try:
        resp = requests.get(config["leaderboard_url"], timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("leaderboardRows", data.get("rows", []))
    except Exception as e:
        print(f"[scanner] Failed to fetch leaderboard: {e}")
    return []


def get_candidate_fills(wallet: str, lookback_days: int = 7) -> list[dict]:
    """Get recent fills for a candidate wallet."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (lookback_days * 24 * 60 * 60 * 1000)

    try:
        fills = hl_post({
            "type": "userFillsByTime",
            "user": wallet,
            "startTime": start_ms,
        })
        return fills if isinstance(fills, list) else []
    except Exception:
        return []


def get_candidate_state(wallet: str) -> dict:
    """Get current clearinghouse state for a candidate wallet."""
    try:
        return hl_post({"type": "clearinghouseState", "user": wallet})
    except Exception:
        return {}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a, dtype=float)
    b_arr = np.array(b, dtype=float)
    dot = np.dot(a_arr, b_arr)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def compare_asset_preferences(fp_a: dict, fp_b: dict) -> float:
    """Compare asset preference dimensions."""
    coins_a = set(fp_a.get("coins_traded", []))
    coins_b = set(fp_b.get("coins_traded", []))

    jaccard = jaccard_similarity(coins_a, coins_b)

    # Frequency correlation on common coins
    common = coins_a & coins_b
    if len(common) < 2:
        return jaccard

    freq_a = fp_a.get("coin_frequency", {})
    freq_b = fp_b.get("coin_frequency", {})
    vec_a = [freq_a.get(c, 0) for c in sorted(common)]
    vec_b = [freq_b.get(c, 0) for c in sorted(common)]

    freq_sim = cosine_similarity(vec_a, vec_b)
    return (jaccard + freq_sim) / 2


def get_asset_overlap(fp_a: dict, fp_b: dict) -> dict:
    """Return overlap details for candidate evidence and penalties."""
    coins_a = set(fp_a.get("coins_traded", []))
    coins_b = set(fp_b.get("coins_traded", []))
    overlap = sorted(coins_a & coins_b)
    target_only = sorted(coins_a - coins_b)
    candidate_only = sorted(coins_b - coins_a)
    rare_overlap = [c for c in overlap if c.startswith("xyz:")]
    return {
        "overlap": overlap,
        "target_only": target_only,
        "candidate_only": candidate_only,
        "overlap_count": len(overlap),
        "rare_overlap": rare_overlap,
        "jaccard": round(jaccard_similarity(coins_a, coins_b), 4),
    }


def compare_timing_profiles(fp_a: dict, fp_b: dict) -> float:
    """Compare timing profile dimensions using cosine similarity."""
    hourly_a = fp_a.get("hourly_distribution", [0]*24)
    hourly_b = fp_b.get("hourly_distribution", [0]*24)
    return cosine_similarity(hourly_a, hourly_b)


def compare_account_size(fp_a: dict, fp_b: dict) -> float:
    """Compare account value bracket and weekly volume bracket."""
    val_a = float(fp_a.get("account_value_usd", 0) or 0)
    val_b = float(fp_b.get("account_value_usd", 0) or 0)
    vol_a = float(fp_a.get("weekly_volume_usd", 0) or 0)
    vol_b = float(fp_b.get("weekly_volume_usd", 0) or 0)

    scores = []
    if val_a > 0 and val_b > 0:
        scores.append(float(np.sqrt(min(val_a, val_b) / max(val_a, val_b))))
    if vol_a > 0 and vol_b > 0:
        scores.append(float(np.sqrt(min(vol_a, vol_b) / max(vol_a, vol_b))))

    return float(np.mean(scores)) if scores else 0.0


def compare_trade_sequencing(fp_a: dict, fp_b: dict) -> float:
    """Compare trade sequencing: inter-fill timing rhythm and buy/sell ratio."""
    seq_a = fp_a.get("trade_sequencing", {})
    seq_b = fp_b.get("trade_sequencing", {})
    if not seq_a or not seq_b:
        return 0.5  # Neutral when unavailable — never penalise

    scores = []
    ift_a = seq_a.get("inter_fill_timing", {}).get("mean_minutes", 0)
    ift_b = seq_b.get("inter_fill_timing", {}).get("mean_minutes", 0)
    if ift_a > 0 and ift_b > 0:
        scores.append(float(np.sqrt(min(ift_a, ift_b) / max(ift_a, ift_b))))

    bsr_a = seq_a.get("buy_sell_ratio", {})
    bsr_b = seq_b.get("buy_sell_ratio", {})
    if bsr_a and bsr_b:
        vec_a = [bsr_a.get("buy_pct", 0), bsr_a.get("sell_pct", 0)]
        vec_b = [bsr_b.get("buy_pct", 0), bsr_b.get("sell_pct", 0)]
        scores.append(cosine_similarity(vec_a, vec_b))

    return float(np.mean(scores)) if scores else 0.5


def compare_position_sizing(fp_a: dict, fp_b: dict) -> float:
    """Compare position sizing relative to account (size-to-account ratio)."""
    ps_a = fp_a.get("position_sizing", {})
    ps_b = fp_b.get("position_sizing", {})
    ratio_a = float(ps_a.get("size_to_account_ratio", {}).get("mean", 0) or 0)
    ratio_b = float(ps_b.get("size_to_account_ratio", {}).get("mean", 0) or 0)
    if ratio_a <= 0 or ratio_b <= 0:
        return 0.5  # Neutral when unavailable
    return float(np.sqrt(min(ratio_a, ratio_b) / max(ratio_a, ratio_b)))


def compare_hold_duration(fp_a: dict, fp_b: dict) -> float | None:
    """Holding-period character. Returns None when either side has too few
    position episodes to characterise — a 5-bucket histogram built from 4
    episodes is noise, and scoring it 0.0 previously penalised the true trader
    harder than it penalised strangers.

    Combines the bucket-shape cosine with a ratio on median hold length; the
    ratio stays meaningful at small sample sizes where the histogram does not.
    """
    hd_a = fp_a.get("hold_duration", {})
    hd_b = fp_b.get("hold_duration", {})
    if not hd_a.get("sufficient_data") or not hd_b.get("sufficient_data"):
        return None

    scores = []
    buckets_a = hd_a.get("distribution_buckets", {})
    buckets_b = hd_b.get("distribution_buckets", {})
    if buckets_a and buckets_b:
        keys = sorted(set(buckets_a) | set(buckets_b))
        vec_a = [buckets_a.get(k, 0) for k in keys]
        vec_b = [buckets_b.get(k, 0) for k in keys]
        if any(vec_a) and any(vec_b):
            scores.append(cosine_similarity(vec_a, vec_b))

    med = _ratio_score(hd_a.get("overall_minutes", {}).get("median", 0),
                       hd_b.get("overall_minutes", {}).get("median", 0))
    if med is not None:
        scores.append(med)

    return float(np.mean(scores)) if scores else None


def compare_leverage(fp_a: dict, fp_b: dict) -> float:
    """Compare leverage profiles."""
    overall_a = fp_a.get("overall", {})
    overall_b = fp_b.get("overall", {})

    if not overall_a or not overall_b:
        return 0.0

    mean_a = overall_a.get("mean", 0)
    mean_b = overall_b.get("mean", 0)

    if mean_a == 0 and mean_b == 0:
        return 1.0

    max_val = max(mean_a, mean_b)
    if max_val == 0:
        return 0.0

    return 1.0 - abs(mean_a - mean_b) / max_val


def _ratio_score(a: float, b: float) -> float | None:
    """sqrt(min/max) similarity for positive scalars; None when not comparable."""
    if a <= 0 or b <= 0:
        return None
    return float(np.sqrt(min(a, b) / max(a, b)))


def compare_activity(sp_a: dict, sp_b: dict) -> float | None:
    """Decision frequency and cadence — the most discriminative style trait.
    Episodes/day is primary; raw fills/day is TWAP-inflated and only secondary."""
    act_a = sp_a.get("activity", {})
    act_b = sp_b.get("activity", {})
    scores = []
    epd = _ratio_score(act_a.get("episodes_per_day", 0), act_b.get("episodes_per_day", 0))
    if epd is not None:
        scores.append(epd)
        scores.append(epd)  # double weight vs the softer components
    freq = _ratio_score(act_a.get("fills_per_day", 0), act_b.get("fills_per_day", 0))
    if freq is not None:
        scores.append(freq)
    adr_a, adr_b = act_a.get("active_days_ratio", 0), act_b.get("active_days_ratio", 0)
    if adr_a > 0 and adr_b > 0:
        scores.append(1.0 - abs(adr_a - adr_b))
    return float(np.mean(scores)) if scores else None


def compare_direction_bias(sp_a: dict, sp_b: dict) -> float | None:
    """Long/short opening bias. Needs enough opens on both sides to mean anything."""
    dir_a = sp_a.get("direction", {})
    dir_b = sp_b.get("direction", {})
    if dir_a.get("total_opens", 0) < 10 or dir_b.get("total_opens", 0) < 10:
        return None
    return 1.0 - abs(dir_a.get("long_open_pct", 0) - dir_b.get("long_open_pct", 0))


def compare_position_management(sp_a: dict, sp_b: dict) -> float | None:
    """Scaling habits, TWAP usage, perp/spot mix, clip-size character."""
    scores = []
    pm_a = sp_a.get("position_management", {})
    pm_b = sp_b.get("position_management", {})
    fpe = _ratio_score(pm_a.get("mean_fills_per_episode", 0),
                       pm_b.get("mean_fills_per_episode", 0))
    if fpe is not None:
        scores.append(fpe)
    ex_a, ex_b = sp_a.get("execution", {}), sp_b.get("execution", {})
    if ex_a and ex_b:
        scores.append(1.0 - abs(ex_a.get("twap_ratio", 0) - ex_b.get("twap_ratio", 0)))
        scores.append(1.0 - abs(ex_a.get("spot_fill_ratio", 0) - ex_b.get("spot_fill_ratio", 0)))
    cs_a, cs_b = sp_a.get("clip_sizes", {}), sp_b.get("clip_sizes", {})
    cv = _ratio_score(cs_a.get("notional_cv", 0), cs_b.get("notional_cv", 0))
    if cv is not None:
        scores.append(cv)
    return float(np.mean(scores)) if scores else None


def compare_loss_handling(sp_a: dict, sp_b: dict) -> float | None:
    """Cuts losers fast vs holds them; win/loss magnitude character."""
    lh_a, lh_b = sp_a.get("loss_handling", {}), sp_b.get("loss_handling", {})
    scores = []
    hold = _ratio_score(lh_a.get("loser_to_winner_hold_ratio", 0),
                        lh_b.get("loser_to_winner_hold_ratio", 0))
    if hold is not None:
        scores.append(hold)
    mag = _ratio_score(lh_a.get("win_loss_magnitude_ratio", 0),
                       lh_b.get("win_loss_magnitude_ratio", 0))
    if mag is not None:
        scores.append(mag)
    return float(np.mean(scores)) if scores else None


def check_style_vetoes(ezekiel_fp: dict, candidate_fp: dict) -> list[str]:
    """Hard incompatibilities: a mismatch on a defining trait means this is a
    different human no matter how similar the coarse distributions look.
    Only applied when both sides have enough data to judge."""
    sp_a = ezekiel_fp.get("style_profile", {})
    sp_b = candidate_fp.get("style_profile", {})
    if not (sp_a.get("sufficient_data") and sp_b.get("sufficient_data")):
        return []

    vetoes = []
    # Decision frequency, not raw fills: TWAP execution inflates fill counts
    # ~10x for the same human depending on the period.
    epd_a = sp_a.get("activity", {}).get("episodes_per_day", 0)
    epd_b = sp_b.get("activity", {}).get("episodes_per_day", 0)
    if epd_a > 0 and epd_b > 0:
        ratio = max(epd_a, epd_b) / min(epd_a, epd_b)
        if ratio > 5:
            vetoes.append(f"Decision frequency {ratio:.0f}x apart "
                          f"({epd_a:.2f} vs {epd_b:.2f} position episodes/day)")

    # Scalper vs swing: dominant sub-1h holding on one side only
    u1h_a = ezekiel_fp.get("hold_duration", {}).get("distribution_buckets", {}).get("under_1h", 0)
    u1h_b = candidate_fp.get("hold_duration", {}).get("distribution_buckets", {}).get("under_1h", 0)
    if (u1h_a > 0.7 and u1h_b < 0.15) or (u1h_b > 0.7 and u1h_a < 0.15):
        vetoes.append(f"Incompatible hold style: {u1h_a:.0%} vs {u1h_b:.0%} of holds under 1h")

    # NOTE: no shorting/direction veto — the self-match backtest showed the
    # target flipping from ~all-long to ~all-short between adjacent windows.
    # Direction tracks market view, not identity; it stays a low-weight dim only.

    return vetoes


def classify_match(score: float, eff: dict | None = None) -> str:
    """Tier label. Uses the SAME effective thresholds the alerts use — this was
    previously hardcoded to 0.90/0.80/0.65 while alerting ran on backtest-adapted
    values near 0.51, so a wallet could email as HIGH and be labelled WEAK_LEAD."""
    if eff is None:
        eff = _effective_thresholds(load_config()["alert_thresholds"])
    return th.classify(score, eff)


def build_evidence(ezekiel_fp: dict, candidate_fp: dict, dimensions: dict, score: float,
                   vetoes: list[str] | None = None, eff: dict | None = None) -> dict:
    """Explain the score so the dashboard can rank leads by evidence."""
    asset_overlap = get_asset_overlap(
        ezekiel_fp.get("asset_preferences", {}),
        candidate_fp.get("asset_preferences", {}),
    )
    reasons = []
    warnings = []

    def dim(name: str) -> float | None:
        """Dimension value, or None when it was excluded for insufficient data.
        Any dimension can now be None, so never compare one directly."""
        v = dimensions.get(name)
        return v if isinstance(v, (int, float)) else None

    def at_least(name: str, cutoff: float) -> bool:
        v = dim(name)
        return v is not None and v >= cutoff

    def below(name: str, cutoff: float) -> bool:
        v = dim(name)
        return v is not None and v < cutoff

    if asset_overlap["rare_overlap"]:
        reasons.append(f"Shares rare HIP-3 markets: {', '.join(asset_overlap['rare_overlap'][:5])}")
    if at_least("asset_preferences", 0.65):
        reasons.append("Strong asset mix overlap")
    if at_least("timing_profile", 0.80):
        reasons.append("Trades in similar active UTC hours")
    if at_least("entry_exit_style", 0.85):
        reasons.append("Similar market/limit execution style")
    if at_least("hold_duration", 0.85):
        reasons.append("Similar holding-duration profile")
    if at_least("account_size", 0.70):
        reasons.append("Similar account-size bracket")
    if at_least("activity", 0.75):
        reasons.append("Matching trade frequency and cadence")
    if at_least("direction_bias", 0.85):
        reasons.append("Matching long/short bias")
    if at_least("position_management", 0.80):
        reasons.append("Similar scaling/TWAP/clip-size habits")

    if asset_overlap["overlap_count"] < 3:
        warnings.append("Weak asset overlap")
    if below("timing_profile", 0.35):
        warnings.append("Different active trading hours")
    if below("account_size", 0.25):
        warnings.append("Very different account-size bracket")
    if below("activity", 0.35):
        warnings.append("Very different trading frequency")
    if dim("hold_duration") is None:
        warnings.append("Too few position episodes to compare holding style")

    return {
        "tier": classify_match(score, eff),
        "reasons": reasons,
        "warnings": warnings,
        "vetoes": vetoes or [],
        "asset_overlap": asset_overlap,
    }


def compute_similarity(ezekiel_fp: dict, candidate_fp: dict,
                       eff: dict | None = None) -> tuple[float, dict, dict]:
    """Compute weighted similarity between Ezekiel and a candidate fingerprint."""
    dimensions = {}

    # Asset preferences
    dim_score = compare_asset_preferences(
        ezekiel_fp.get("asset_preferences", {}),
        candidate_fp.get("asset_preferences", {})
    )
    dimensions["asset_preferences"] = round(dim_score, 4)

    # Timing profile
    dim_score = compare_timing_profiles(
        ezekiel_fp.get("timing_profile", {}),
        candidate_fp.get("timing_profile", {})
    )
    dimensions["timing_profile"] = round(dim_score, 4)

    # Leverage
    dim_score = compare_leverage(
        ezekiel_fp.get("leverage_profile", {}),
        candidate_fp.get("leverage_profile", {})
    )
    dimensions["leverage_profile"] = round(dim_score, 4)

    # Entry/exit style (market/limit ratio similarity)
    style_a = ezekiel_fp.get("entry_exit_style", {}).get("order_type_ratio", {})
    style_b = candidate_fp.get("entry_exit_style", {}).get("order_type_ratio", {})
    if style_a and style_b:
        vec_a = [style_a.get("market", 0), style_a.get("limit", 0)]
        vec_b = [style_b.get("market", 0), style_b.get("limit", 0)]
        dimensions["entry_exit_style"] = round(cosine_similarity(vec_a, vec_b), 4)
    else:
        dimensions["entry_exit_style"] = 0.0

    # Hold duration — None when either side has too few episodes to judge, so the
    # weight is redistributed rather than scoring a misleading 0.0.
    hd_score = compare_hold_duration(ezekiel_fp, candidate_fp)
    dimensions["hold_duration"] = round(hd_score, 4) if hd_score is not None else None

    dim_score = compare_account_size(
        ezekiel_fp.get("account_characteristics", {}),
        candidate_fp.get("account_characteristics", {}),
    )
    dimensions["account_size"] = round(dim_score, 4)

    dim_score = compare_trade_sequencing(ezekiel_fp, candidate_fp)
    dimensions["trade_sequencing"] = round(dim_score, 4)

    dim_score = compare_position_sizing(ezekiel_fp, candidate_fp)
    dimensions["position_sizing"] = round(dim_score, 4)

    # Style dimensions — how the trader trades. These return None when either
    # side lacks the data to judge; None dims are excluded and weights renormalized
    # so thin data never fakes a signal in either direction.
    style_a = ezekiel_fp.get("style_profile", {})
    style_b = candidate_fp.get("style_profile", {})
    style_dims = {
        "activity": compare_activity(style_a, style_b),
        "direction_bias": compare_direction_bias(style_a, style_b),
        "position_management": compare_position_management(style_a, style_b),
        "loss_handling": compare_loss_handling(style_a, style_b),
    }
    for name, val in style_dims.items():
        dimensions[name] = round(val, 4) if val is not None else None

    # Dynamic weights: discount account_size for fresh/small candidate wallets.
    # A migrated trader starts with a new account — size comparison is misleading early on.
    weights = {
        "asset_preferences": 0.20,
        "timing_profile": 0.14,
        "leverage_profile": 0.10,
        "entry_exit_style": 0.07,
        "hold_duration": 0.09,
        "account_size": 0.06,
        "trade_sequencing": 0.06,
        "position_sizing": 0.05,
        # Direction bias is regime-dependent (the target flips long/short with
        # market view) — kept tiny; activity/management carry the style weight.
        "activity": 0.11,
        "direction_bias": 0.03,
        "position_management": 0.06,
        "loss_handling": 0.03,
    }

    candidate_acct_val = float(
        candidate_fp.get("account_characteristics", {}).get("account_value_usd", 0) or 0
    )
    target_acct_val = float(
        ezekiel_fp.get("account_characteristics", {}).get("account_value_usd", 0) or 0
    )
    if candidate_acct_val < 100_000 and target_acct_val > 500_000:
        redistributed = weights["account_size"]
        weights["account_size"] = 0.0
        weights["asset_preferences"] += redistributed * 0.6
        weights["timing_profile"] += redistributed * 0.4

    # Exclude dims with no data (None) and renormalize the remaining weights
    usable = {k: w for k, w in weights.items() if dimensions.get(k) is not None and w > 0}
    total_w = sum(usable.values()) or 1.0
    weighted_sum = sum(dimensions[k] * (w / total_w) for k, w in usable.items())

    # Apply conservative penalties for mismatches that are especially useful
    # when trying to identify a migrated human rather than a similar trader.
    overlap = get_asset_overlap(
        ezekiel_fp.get("asset_preferences", {}),
        candidate_fp.get("asset_preferences", {}),
    )
    penalty = 0.0
    if overlap["overlap_count"] < 3:
        penalty += 0.12
    if dimensions["timing_profile"] < 0.30:
        penalty += 0.08
    if dimensions["account_size"] < 0.20 and weights["account_size"] > 0:
        penalty += 0.05

    score = max(0.0, weighted_sum - penalty)

    # Hard style vetoes: an incompatible defining trait caps the score below
    # alert tiers regardless of how similar the coarse distributions look.
    vetoes = check_style_vetoes(ezekiel_fp, candidate_fp)
    if vetoes:
        score = min(score, VETO_SCORE_CAP)

    # xyz: HIP-3 market bonus — trading these exotic markets is extremely rare.
    # Any overlap here is near-conclusive behavioral evidence of the same trader.
    if overlap.get("rare_overlap"):
        score = min(VETO_BONUS_CEILING if vetoes else 1.0, score + 0.12)

    score = round(score, 4)
    evidence = build_evidence(ezekiel_fp, candidate_fp, dimensions, score, vetoes, eff)
    return score, dimensions, evidence


def _estimate_weekly_volume(fills: list[dict]) -> float:
    """Estimate weekly trading volume from a list of fills."""
    if not fills:
        return 0.0
    total = sum(float(f.get("sz", 0)) * float(f.get("px", 0)) for f in fills)
    timestamps = [f.get("time", 0) for f in fills]
    time_range_ms = max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0
    weeks = time_range_ms / (7 * 24 * 60 * 60 * 1000) if time_range_ms > 0 else 1
    return round(total / max(weeks, 1), 2)


def build_candidate_fingerprint(fills: list[dict], state: dict) -> dict:
    """Build a mini-fingerprint for a candidate wallet from their data.
    Now includes all 8 comparable dimensions (trade_sequencing, position_sizing added)."""
    from src.fingerprint import (
        compute_leverage_profile, compute_hold_duration, compute_entry_exit_style,
        compute_style_profile,
    )

    positions = state
    if isinstance(positions, dict) and "assetPositions" not in positions:
        if "perp" in positions:
            positions = positions["perp"]

    acct_val = round(float(positions.get("marginSummary", {}).get("accountValue", 0) or 0), 2) \
        if isinstance(positions, dict) else 0

    return {
        "asset_preferences": compute_asset_preferences(fills),
        "timing_profile": compute_timing_profile(fills),
        "leverage_profile": compute_leverage_profile(fills, positions),
        "entry_exit_style": compute_entry_exit_style(fills),
        "hold_duration": compute_hold_duration(fills),
        "trade_sequencing": compute_trade_sequencing(fills),
        "position_sizing": compute_position_sizing(fills, positions),
        "style_profile": compute_style_profile(fills),
        "account_characteristics": {
            "account_value_usd": acct_val,
            "weekly_volume_usd": _estimate_weekly_volume(fills),
        },
    }


def _summarize_fingerprint(fp: dict) -> dict:
    """Extract comparison-relevant data from a candidate fingerprint.
    Kept compact — only fields needed for dashboard drill-down."""
    ap = fp.get("asset_preferences", {})
    tp = fp.get("timing_profile", {})
    lp = fp.get("leverage_profile", {})
    ee = fp.get("entry_exit_style", {})
    hd = fp.get("hold_duration", {})

    return {
        "asset_preferences": {
            "coins_traded": ap.get("coins_traded", []),
            "coin_frequency": ap.get("coin_frequency", {}),
            "top_5_by_volume": ap.get("top_5_by_volume", []),
        },
        "timing_profile": {
            "hourly_distribution": tp.get("hourly_distribution", []),
            "most_active_hours_utc": tp.get("most_active_hours_utc", []),
        },
        "leverage_profile": {
            "overall": lp.get("overall", {}),
        },
        "entry_exit_style": {
            "order_type_ratio": ee.get("order_type_ratio", {}),
            "win_rate": ee.get("win_rate", 0),
        },
        "hold_duration": {
            "overall_minutes": hd.get("overall_minutes", {}),
            "distribution_buckets": hd.get("distribution_buckets", {}),
        },
        "trade_sequencing": {
            "inter_fill_timing": fp.get("trade_sequencing", {}).get("inter_fill_timing", {}),
            "buy_sell_ratio": fp.get("trade_sequencing", {}).get("buy_sell_ratio", {}),
        },
        "position_sizing": {
            "size_to_account_ratio": fp.get("position_sizing", {}).get("size_to_account_ratio", {}),
        },
        "style_profile": fp.get("style_profile", {}),
        "account_characteristics": fp.get("account_characteristics", {}),
    }


def persist_candidate(result: dict) -> None:
    """Save a promoted candidate for persistent review across scan runs."""
    candidate_dir = DATA_DIR / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    path = candidate_dir / f"{result['wallet'].lower()}.json"
    history = []
    existing = {}
    if path.exists():
        with open(path) as f:
            existing = json.load(f)
        history = existing.get("score_history", [])

    history.append({
        "scan_time": result["scanned_at"],
        "score": result["score"],
        "tier": result.get("evidence", {}).get("tier"),
        "dimensions": result.get("dimensions", {}),
    })
    history = history[-100:]

    # Score decay: mark candidates whose recent scores have drifted low
    recent_window = history[-7:]
    recent_avg = sum(h["score"] for h in recent_window) / len(recent_window) if len(recent_window) >= 7 else None
    best_score = max(float(existing.get("best_score", 0)), result["score"])
    if recent_avg is not None and recent_avg < 0.55 and best_score < 0.85:
        status = "COOLING"
    elif recent_avg is not None and recent_avg >= 0.65:
        status = "ACTIVE"
    else:
        status = existing.get("status", "ACTIVE")

    candidate = {
        "wallet": result["wallet"],
        "first_seen": existing.get("first_seen", result["scanned_at"]),
        "last_seen": result["scanned_at"],
        "best_score": best_score,
        "latest_score": result["score"],
        "latest_tier": result.get("evidence", {}).get("tier"),
        "latest_evidence": result.get("evidence", {}),
        "score_history": history,
        "status": status,
        "recent_avg_score": round(recent_avg, 4) if recent_avg is not None else None,
    }
    with open(path, "w") as f:
        json.dump(candidate, f, indent=2)

    latest = []
    for fp in candidate_dir.glob("0x*.json"):
        with open(fp) as f:
            latest.append(json.load(f))
    latest.sort(key=lambda c: c.get("best_score", 0), reverse=True)
    save_latest(str(candidate_dir), {"candidates": latest[:50]})


def scan_specific_wallet(wallet: str, ezekiel_fp: dict, config: dict,
                          source: str = "targeted", eff: dict | None = None) -> dict | None:
    """Score a single wallet against the fingerprint, bypassing the leaderboard.
    Used for fund-flow destinations, bridge depositors, and subaccounts."""
    lookback_days = config["scanner"].get("fills_lookback_days", 21)
    min_fills = config["scanner"].get("min_fills_for_comparison", 20)

    fills = get_candidate_fills(wallet, lookback_days)
    if len(fills) < min_fills:
        fills = get_candidate_fills(wallet, lookback_days * 2)

    # Accept thin histories for targeted scans — a fresh wallet won't have many fills yet
    floor = max(5, min_fills // 4)
    if len(fills) < floor:
        print(f"[scanner] {source}: {wallet[:10]}... only {len(fills)} fills, skipping")
        return None

    state = get_candidate_state(wallet)
    candidate_fp = build_candidate_fingerprint(fills, state)
    score, dimensions, evidence = compute_similarity(ezekiel_fp, candidate_fp, eff)

    return {
        "wallet": wallet,
        "score": score,
        "dimensions": dimensions,
        "evidence": evidence,
        "fills_count": len(fills),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": _summarize_fingerprint(candidate_fp),
        "source": source,
    }


def get_recent_bridge_depositors(min_usdc: float = 50_000, days: int = 30) -> list[str]:
    """Return wallet addresses that recently made large deposits to the HL bridge on Arbitrum."""
    import os
    api_key = os.environ.get("ETHERSCAN_API_KEY", "")
    if not api_key:
        print("[scanner] Etherscan API key missing, skipping bridge depositor scan")
        return []

    config = load_config()
    result = etherscan_get({
        "module": "account",
        "action": "tokentx",
        "address": config["hl_bridge_contract"],
        "contractaddress": config["usdc_contract_arbitrum"],
        "page": 1,
        "offset": 1000,
        "sort": "desc",
    })

    transfers = result.get("result", []) if result.get("status") == "1" else []
    if not isinstance(transfers, list):
        return []

    cutoff_ts = int(time.time()) - (days * 86400)
    target = config["target_wallet"].lower()
    bridge = config["hl_bridge_contract"].lower()

    depositors: dict[str, float] = {}
    for t in transfers:
        if int(t.get("timeStamp", 0)) < cutoff_ts:
            continue
        to_addr = t.get("to", "").lower()
        from_addr = t.get("from", "").lower()
        if to_addr != bridge or from_addr in (target, bridge):
            continue
        amount = int(t.get("value", 0)) / 1e6
        if amount >= min_usdc:
            depositors[from_addr] = max(depositors.get(from_addr, 0), amount)

    print(f"[scanner] Bridge depositor scan: {len(depositors)} wallets >= ${min_usdc:,.0f} in last {days}d")
    return list(depositors.keys())


def _load_target_vault_addresses() -> set:
    """Load vault addresses the target wallet has deposited to."""
    vaults_path = DATA_DIR / "vaults" / "latest.json"
    if not vaults_path.exists():
        return set()
    try:
        with open(vaults_path) as f:
            data = json.load(f)
        vaults = data if isinstance(data, list) else []
        return {v.get("vaultAddress", "").lower() for v in vaults if v.get("vaultAddress")}
    except Exception:
        return set()


def _load_target_referral_addresses() -> set:
    """Load wallet addresses in the target's referral network."""
    ref_path = DATA_DIR / "referral" / "latest.json"
    if not ref_path.exists():
        return set()
    try:
        with open(ref_path) as f:
            data = json.load(f)
        addrs = set()
        if isinstance(data, dict):
            if data.get("referrerAddress"):
                addrs.add(data["referrerAddress"].lower())
            for r in data.get("referredUsers", []):
                addr = r.get("address", "") or r.get("wallet", "")
                if addr:
                    addrs.add(addr.lower())
        return addrs
    except Exception:
        return set()


def _check_vault_overlap(wallet: str, target_vaults: set) -> list:
    """Return shared vault addresses between candidate and target. Empty list = no overlap."""
    if not target_vaults:
        return []
    try:
        raw = hl_post({"type": "userVaultEquities", "user": wallet})
        if not isinstance(raw, list):
            return []
        candidate_vaults = {v.get("vaultAddress", "").lower() for v in raw if v.get("vaultAddress")}
        return list(target_vaults & candidate_vaults)
    except Exception:
        return []


def _check_referral_link(wallet: str, target_referral_addrs: set) -> bool:
    """Return True if candidate has a referral link to/from the target's network."""
    if not target_referral_addrs:
        return False
    if wallet.lower() in target_referral_addrs:
        return True
    try:
        ref = hl_post({"type": "referral", "user": wallet})
        if isinstance(ref, dict):
            referrer = (ref.get("referrerAddress") or "").lower()
            if referrer and referrer in target_referral_addrs:
                return True
            for r in ref.get("referredUsers", []):
                addr = (r.get("address", "") or r.get("wallet", "")).lower()
                if addr in target_referral_addrs:
                    return True
    except Exception:
        pass
    return False


def _rare_overlap(result: dict) -> list:
    """xyz: HIP-3 markets the candidate shares with the target (the rarest tell)."""
    return result.get("evidence", {}).get("asset_overlap", {}).get("rare_overlap", []) or []


def _apply_linkage(result: dict, target: str, profile: dict) -> float:
    """Run L1 clustering heuristics (shared funder / address reuse) on a candidate.
    Adds evidence + a score bonus and fires the linkage alert. Returns new score."""
    from src import linkage as linkage_mod
    wallet = result["wallet"]
    try:
        link = linkage_mod.check_candidate(wallet, target, profile)
    except Exception as e:
        print(f"[scanner] Linkage check failed for {wallet[:10]}...: {e}")
        return result["score"]

    if link.get("linkage_bonus", 0) > 0:
        result["evidence"].setdefault("reasons", []).extend(link["reasons"])
        result["evidence"]["linkage"] = link
        cap = VETO_BONUS_CEILING if result["evidence"].get("vetoes") else 1.0
        result["score"] = round(min(cap, result["score"] + link["linkage_bonus"]), 4)
        print(f"[scanner] Linkage for {wallet[:10]}...: +{link['linkage_bonus']} {link['reasons']}")
        # Linkage is on-chain evidence, but a style-vetoed wallet still must not
        # be promoted to a high-confidence alert on any route.
        if th.can_alert(result["evidence"].get("vetoes")):
            alert_linkage_match(wallet, link["reasons"], result["score"])
        else:
            print(f"[scanner] Linkage alert suppressed for {wallet[:10]}... (style veto); "
                  f"evidence retained on watchlist")
    return result["score"]


def scan_priority_targets(ezekiel_fp: dict, config: dict, eff: dict,
                          population: list[float] | None = None) -> list[dict]:
    """Scan high-priority candidates before the leaderboard sweep:
    fund-flow destinations, subaccounts, and recent large bridge depositors.
    Returns scored results and fires combined alerts when both vectors agree.

    `eff` and `population` are passed in rather than resolved here so priority
    targets go through exactly the same threshold policy and percentile gate as
    the leaderboard sweep — previously the population was loaded after this ran,
    so the highest-priority wallets silently bypassed calibration."""
    candidate_threshold = min(config["scanner"].get("candidate_threshold", 0.65),
                              eff["low"])

    # Collect priority wallets with their source metadata
    priority: dict[str, dict] = {}

    # 1. Fund flow destinations (highest priority)
    fund_flows_path = DATA_DIR / "fund_flows" / "latest.json"
    if fund_flows_path.exists():
        try:
            with open(fund_flows_path) as f:
                flows = json.load(f)
            for finding in flows.get("findings", []):
                dest = finding.get("destination", "").lower()
                if dest:
                    priority[dest] = {
                        "source": "fund_flow",
                        "deposited_to_hl": finding.get("deposited_to_hl", False),
                        "amount": finding.get("amount_usdc", "unknown"),
                        "method": finding.get("method", "fund_trace"),
                    }
        except Exception as e:
            print(f"[scanner] Could not load fund flows: {e}")

    # 2. Subaccounts of the target wallet
    subaccounts_path = DATA_DIR / "subaccounts" / "latest.json"
    if subaccounts_path.exists():
        try:
            with open(subaccounts_path) as f:
                subs = json.load(f)
            sub_list = subs if isinstance(subs, list) else subs.get("subaccounts", [])
            for sub in sub_list:
                addr = (sub.get("user", "") or sub.get("address", "")).lower()
                if addr and addr not in priority:
                    priority[addr] = {"source": "subaccount"}
        except Exception as e:
            print(f"[scanner] Could not load subaccounts: {e}")

    # 3. HL-native transfer counterparties (send / internalTransfer / spotTransfer).
    # These are wallets the target moved funds to/from ENTIRELY inside Hyperliquid,
    # never touching L1 — the most likely migration path and invisible to the tracer.
    hl_transfers_path = DATA_DIR / "hl_transfers" / "latest.json"
    if hl_transfers_path.exists():
        try:
            with open(hl_transfers_path) as f:
                hl_data = json.load(f)
            for cp in hl_data.get("counterparties", []):
                addr = cp.get("wallet", "").lower()
                if not addr:
                    continue
                src = "known_linked" if cp.get("known_self") else "hl_transfer"
                # Don't downgrade a higher-signal source already recorded.
                if addr not in priority:
                    priority[addr] = {
                        "source": src,
                        "out_usd": cp.get("total_out_usd", 0),
                        "in_usd": cp.get("total_in_usd", 0),
                        "bidirectional": cp.get("bidirectional", False),
                    }
        except Exception as e:
            print(f"[scanner] Could not load hl_transfers: {e}")

    # 4. Deposit/withdrawal correlation matches — wallets whose fresh bridge deposit
    # closely matches a target exit in amount + timing (re-linked across a CEX gap).
    correlations_path = DATA_DIR / "correlations" / "latest.json"
    if correlations_path.exists():
        try:
            with open(correlations_path) as f:
                corr = json.load(f)
            for m in corr.get("matches", []):
                addr = m.get("wallet", "").lower()
                if addr and addr not in priority:
                    priority[addr] = {
                        "source": "correlation",
                        "confidence": m.get("confidence", 0),
                        "amount": f"${m.get('deposit_amount_usd', 0):,.0f} deposit ~ ${m.get('exit_amount_usd', 0):,.0f} exit",
                    }
        except Exception as e:
            print(f"[scanner] Could not load correlations: {e}")

    # 5. Recent large bridge depositors
    for addr in get_recent_bridge_depositors():
        if addr not in priority:
            priority[addr] = {"source": "bridge_depositor"}

    if not priority:
        print("[scanner] No priority targets to scan")
        return []

    print(f"[scanner] Scanning {len(priority)} priority targets...")
    target_vaults = _load_target_vault_addresses()
    target_referral_addrs = _load_target_referral_addresses()
    target_l1 = None  # lazily built L1 clustering profile (one Etherscan call)
    target_addr = config["target_wallet"]
    results = []

    for wallet, meta in priority.items():
        source = meta.get("source", "targeted")
        result = scan_specific_wallet(wallet, ezekiel_fp, config, source=source, eff=eff)
        if result is None:
            continue

        score = result["score"]
        print(f"[scanner] Priority {wallet[:10]}... ({source}): {score:.4f}")

        vetoes = result["evidence"].get("vetoes")
        alertable = th.can_alert(vetoes)

        rare = _rare_overlap(result)
        if rare:
            print(f"[scanner] xyz: SIGNATURE MATCH {wallet[:10]}...: {rare}")
            if alertable:
                alert_xyz_signature_match(wallet, rare, score)
            else:
                print(f"[scanner] xyz: alert suppressed for {wallet[:10]}... (style veto); "
                      f"still watchlisted with evidence")

        # Vault, referral, and L1-clustering checks for promising candidates
        bonus_cap = VETO_BONUS_CEILING if vetoes else 1.0
        if score >= 0.70 or rare:
            shared_vaults = _check_vault_overlap(wallet, target_vaults)
            if shared_vaults:
                result["evidence"]["reasons"].append(
                    f"Deposits to same HL vault(s): {', '.join(v[:10]+'...' for v in shared_vaults[:2])}"
                )
                score = min(bonus_cap, score + 0.08)
                result["score"] = round(score, 4)
                print(f"[scanner] Vault overlap for {wallet[:10]}...: {shared_vaults}")
                if alertable:
                    alert_vault_match(wallet, shared_vaults)

            if _check_referral_link(wallet, target_referral_addrs):
                result["evidence"]["reasons"].append("Referral network connection to target wallet")
                score = min(bonus_cap, score + 0.06)
                result["score"] = round(score, 4)
                print(f"[scanner] Referral link for {wallet[:10]}...")

            if target_l1 is None:
                from src import linkage as linkage_mod
                target_l1 = linkage_mod.target_l1_profile(target_addr)
            score = _apply_linkage(result, target_addr, target_l1)

        # One disposition decides promote / watchlist / drop for every route below.
        disp = evaluate_candidate(wallet, score, eff, population, vetoes, bool(rare))
        result["disposition"] = disp
        results.append(result)

        # Persist anything watchlisted or better — suppressed candidates keep their
        # evidence and stay visible rather than being discarded.
        if disp["action"] != th.ACTION_BACKGROUND or score >= candidate_threshold:
            persist_candidate(result)

        promoted = disp["action"] == th.ACTION_ALERT
        if promoted:
            alert_behavioral_match(wallet, score, result["dimensions"])

        # Combined alerts: fund-flow/HL-native/correlation evidence AND behaviour.
        # All require `alertable` so a style-vetoed wallet cannot be promoted by a
        # side route — previously only the behavioural route checked vetoes.
        if alertable:
            if meta.get("deposited_to_hl") and score >= eff["medium"]:
                alert_combined_match(wallet, score, meta["amount"], meta["method"])
            elif source == "bridge_depositor" and score >= eff["high"]:
                alert_combined_match(wallet, score, "large bridge deposit", "bridge_depositor")
            elif source in ("hl_transfer", "known_linked") and score >= eff["medium"]:
                # Funds moved HL-natively to this wallet AND it trades like the target —
                # the strongest possible in-platform migration signal.
                amount = f"${meta.get('out_usd', 0):,.0f} sent in-platform"
                alert_combined_match(wallet, score, amount, f"hl_native_transfer ({source})")
            elif source == "correlation" and score >= eff["low"]:
                # Deposit/withdrawal amount+timing correlation AND behavioral match.
                alert_combined_match(wallet, score, meta.get("amount", "amount match"),
                                     "deposit_correlation")

        time.sleep(0.5)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def scan_leaderboard():
    """Main scanning loop: check leaderboard wallets against fingerprint."""
    config = load_config()
    target = config["target_wallet"].lower()
    eff = _effective_thresholds(config["alert_thresholds"])
    scanner_config = config["scanner"]
    # Loaded up front so priority targets and the leaderboard sweep share one
    # null distribution; previously this happened after the priority phase.
    population = calibration.load_population()
    print(f"[scanner] Calibration: {len(population)} samples, "
          f"gate {'ENFORCING' if calibration.gate_active(population) else 'OBSERVING'}")

    # Load fingerprint — prefer recent (21-day window) for fair candidate comparison
    fp_path = Path(DATA_DIR.parent / "profile" / "fingerprint.json")
    recent_fp_path = Path(DATA_DIR.parent / "profile" / "fingerprint_recent.json")
    if recent_fp_path.exists():
        with open(recent_fp_path) as f:
            ezekiel_fp = json.load(f)
        print("[scanner] Loaded recent fingerprint (21-day window)")
    elif fp_path.exists():
        with open(fp_path) as f:
            ezekiel_fp = json.load(f)
        print("[scanner] Loaded full fingerprint (recent not yet available)")
    else:
        print("[scanner] No fingerprint found, building...")
        ezekiel_fp = build_fingerprint()

    # Phase 1: priority targets — fund-flow destinations, subaccounts, bridge depositors
    # These bypass the leaderboard and get scanned regardless of ranking.
    priority_results = scan_priority_targets(ezekiel_fp, config, eff, population)
    priority_wallets = {r["wallet"].lower() for r in priority_results}

    # Phase 2: leaderboard sweep
    leaderboard = fetch_leaderboard()
    print(f"[scanner] Leaderboard: {len(leaderboard)} entries")

    max_wallets = scanner_config["max_leaderboard_wallets"]
    min_fills = scanner_config["min_fills_for_comparison"]
    lookback_days = scanner_config["fills_lookback_days"]
    candidate_threshold = min(scanner_config.get("candidate_threshold", 0.65),
                              eff["low"])

    results = list(priority_results)  # Start with priority results
    top_scores = [{"wallet": r["wallet"][:10], "score": r["score"]} for r in priority_results]
    scanned = 0
    sweep_scores = []  # this sweep's scores → next sweep's null distribution

    for entry in leaderboard[:max_wallets]:
        wallet = entry.get("ethAddress", entry.get("address", ""))
        if not wallet or wallet.lower() == target:
            continue
        if wallet.lower() in priority_wallets:
            continue  # Already scanned in priority phase

        scanned += 1
        if scanned % 50 == 0:
            print(f"[scanner] Scanned {scanned}/{min(len(leaderboard), max_wallets)}...")

        fills = get_candidate_fills(wallet, lookback_days)
        if len(fills) < min_fills:
            continue

        state = get_candidate_state(wallet)
        candidate_fp = build_candidate_fingerprint(fills, state)
        score, dimensions, evidence = compute_similarity(ezekiel_fp, candidate_fp, eff)
        sweep_scores.append(score)

        result = {
            "wallet": wallet,
            "score": score,
            "score_percentile": calibration.score_percentile(score, population),
            "dimensions": dimensions,
            "evidence": evidence,
            "fills_count": len(fills),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "fingerprint": _summarize_fingerprint(candidate_fp),
        }

        top_scores.append({"wallet": wallet[:10], "score": score})
        top_scores.sort(key=lambda x: x["score"], reverse=True)
        top_scores = top_scores[:10]

        # xyz: HIP-3 signature — trading these rare markets is near-conclusive on
        # its own, so a leaderboard wallet sharing them is auto-promoted and alerted
        # even if its overall score is modest (it may be a fresh, thin-history wallet).
        rare = _rare_overlap(result)
        vetoes = evidence.get("vetoes")
        if rare:
            print(f"[scanner] xyz: SIGNATURE MATCH (leaderboard): {wallet} {rare}")
            if th.can_alert(vetoes):
                alert_xyz_signature_match(wallet, rare, score)
            else:
                print(f"[scanner] xyz: alert suppressed for {wallet[:10]}... (style veto); "
                      f"still watchlisted with evidence")

        disp = evaluate_candidate(wallet, score, eff, population, vetoes, bool(rare))
        result["disposition"] = disp

        # Keep anything watchlisted or better in the scan output and on the
        # persistent watchlist — suppression downgrades, it never discards.
        if disp["action"] != th.ACTION_BACKGROUND:
            results.append(result)
        if disp["action"] != th.ACTION_BACKGROUND or score >= candidate_threshold:
            persist_candidate(result)

        if disp["action"] == th.ACTION_ALERT:
            print(f"[scanner] HIGH MATCH: {wallet} (score={score:.4f})")
            alert_behavioral_match(wallet, score, dimensions)
        elif score >= eff["medium"]:
            print(f"[scanner] MEDIUM MATCH: {wallet} (score={score:.4f})")

        time.sleep(0.5)

    print(f"[scanner] Top 5 scores: {top_scores[:5]}")

    pop_size = calibration.record_population_scores(sweep_scores)
    print(f"[scanner] Calibration population: {pop_size} samples")

    results.sort(key=lambda r: r["score"], reverse=True)

    for i, r in enumerate(results):
        if i >= 20:
            r.pop("fingerprint", None)

    scan_result = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "wallets_scanned": scanned + len(priority_wallets),
        "priority_scanned": len(priority_wallets),
        "matches_found": len(results),
        # Resolved effective thresholds — the dashboard reads these instead of
        # hardcoding 0.90/0.80, so UI tiers match the tiers the alerts used.
        "thresholds": eff,
        "calibration": {
            "population_size": len(population),
            "gate_active": calibration.gate_active(population),
            "alert_percentile": calibration.ALERT_PERCENTILE,
        },
        "top_scores": top_scores,
        "results": results,
    }

    append_records(str(DATA_DIR / "scans"), [scan_result], key_field="scan_time")
    save_latest(str(DATA_DIR / "scans"), scan_result)

    # Migration correlation: if target is silent AND a strong new candidate appeared, fire alert
    try:
        last_fill_ts = read_cursor("last_fill_time")
        if last_fill_ts:
            days_silent = (time.time() * 1000 - last_fill_ts) / (24 * 60 * 60 * 1000)
            if days_silent >= 5 and results:
                top = results[0]
                corr_threshold = min(0.75, eff["medium"])
                if (top["score"] >= corr_threshold
                        and th.can_alert(top.get("evidence", {}).get("vetoes"))
                        and calibration.passes_percentile_gate(top["score"], population)):
                    print(f"[scanner] MIGRATION CORRELATION: {days_silent:.1f}d silence + {top['score']:.4f} candidate")
                    alert_migration_correlation(top["wallet"], top["score"], round(days_silent, 1))
    except Exception as e:
        print(f"[scanner] Migration correlation check failed: {e}")

    print(f"[scanner] Scan complete: {scanned} leaderboard + {len(priority_wallets)} priority = {scan_result['wallets_scanned']} total, {len(results)} matches")
    return scan_result


def main():
    scan_leaderboard()


if __name__ == "__main__":
    main()
