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
    load_config, hl_post, append_records, save_latest, DATA_DIR
)
from src.fingerprint import build_fingerprint, compute_asset_preferences, compute_timing_profile
from src.alerts import alert_behavioral_match


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
    """Compare current account value bracket without overfitting exact balances."""
    val_a = float(fp_a.get("account_value_usd", 0) or 0)
    val_b = float(fp_b.get("account_value_usd", 0) or 0)
    if val_a <= 0 or val_b <= 0:
        return 0.0
    ratio = min(val_a, val_b) / max(val_a, val_b)
    return float(np.sqrt(ratio))


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


def classify_match(score: float) -> str:
    if score >= 0.90:
        return "CONFIRMED_CANDIDATE"
    if score >= 0.80:
        return "WATCH_CLOSELY"
    if score >= 0.65:
        return "WEAK_LEAD"
    return "BACKGROUND"


def build_evidence(ezekiel_fp: dict, candidate_fp: dict, dimensions: dict, score: float) -> dict:
    """Explain the score so the dashboard can rank leads by evidence."""
    asset_overlap = get_asset_overlap(
        ezekiel_fp.get("asset_preferences", {}),
        candidate_fp.get("asset_preferences", {}),
    )
    reasons = []
    warnings = []

    if asset_overlap["rare_overlap"]:
        reasons.append(f"Shares rare HIP-3 markets: {', '.join(asset_overlap['rare_overlap'][:5])}")
    if dimensions.get("asset_preferences", 0) >= 0.65:
        reasons.append("Strong asset mix overlap")
    if dimensions.get("timing_profile", 0) >= 0.80:
        reasons.append("Trades in similar active UTC hours")
    if dimensions.get("entry_exit_style", 0) >= 0.85:
        reasons.append("Similar market/limit execution style")
    if dimensions.get("hold_duration", 0) >= 0.85:
        reasons.append("Similar holding-duration profile")
    if dimensions.get("account_size", 0) >= 0.70:
        reasons.append("Similar account-size bracket")

    if asset_overlap["overlap_count"] < 3:
        warnings.append("Weak asset overlap")
    if dimensions.get("timing_profile", 0) < 0.35:
        warnings.append("Different active trading hours")
    if dimensions.get("account_size", 0) < 0.25:
        warnings.append("Very different account-size bracket")

    return {
        "tier": classify_match(score),
        "reasons": reasons,
        "warnings": warnings,
        "asset_overlap": asset_overlap,
    }


def compute_similarity(ezekiel_fp: dict, candidate_fp: dict) -> tuple[float, dict, dict]:
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

    # Hold duration buckets
    buckets_a = ezekiel_fp.get("hold_duration", {}).get("distribution_buckets", {})
    buckets_b = candidate_fp.get("hold_duration", {}).get("distribution_buckets", {})
    if buckets_a and buckets_b:
        keys = sorted(set(list(buckets_a.keys()) + list(buckets_b.keys())))
        vec_a = [buckets_a.get(k, 0) for k in keys]
        vec_b = [buckets_b.get(k, 0) for k in keys]
        dimensions["hold_duration"] = round(cosine_similarity(vec_a, vec_b), 4)
    else:
        dimensions["hold_duration"] = 0.0

    dim_score = compare_account_size(
        ezekiel_fp.get("account_characteristics", {}),
        candidate_fp.get("account_characteristics", {}),
    )
    dimensions["account_size"] = round(dim_score, 4)

    # Weighted average (sum to 1.0)
    weights = {
        "asset_preferences": 0.30,
        "timing_profile": 0.20,
        "leverage_profile": 0.15,
        "entry_exit_style": 0.12,
        "hold_duration": 0.13,
        "account_size": 0.10,
    }

    weighted_sum = sum(dimensions.get(k, 0) * w for k, w in weights.items())

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
    if dimensions["account_size"] < 0.20:
        penalty += 0.05

    score = max(0.0, weighted_sum - penalty)
    score = round(score, 4)
    evidence = build_evidence(ezekiel_fp, candidate_fp, dimensions, score)
    return score, dimensions, evidence


def build_candidate_fingerprint(fills: list[dict], state: dict) -> dict:
    """Build a mini-fingerprint for a candidate wallet from their data."""
    from src.fingerprint import (
        compute_leverage_profile, compute_hold_duration, compute_entry_exit_style
    )

    positions = state
    if isinstance(positions, dict) and "assetPositions" not in positions:
        if "perp" in positions:
            positions = positions["perp"]

    return {
        "asset_preferences": compute_asset_preferences(fills),
        "timing_profile": compute_timing_profile(fills),
        "leverage_profile": compute_leverage_profile(fills, positions),
        "entry_exit_style": compute_entry_exit_style(fills),
        "hold_duration": compute_hold_duration(fills),
        "account_characteristics": {
            "account_value_usd": round(float(positions.get("marginSummary", {}).get("accountValue", 0) or 0), 2)
            if isinstance(positions, dict) else 0
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

    candidate = {
        "wallet": result["wallet"],
        "first_seen": existing.get("first_seen", result["scanned_at"]),
        "last_seen": result["scanned_at"],
        "best_score": max(float(existing.get("best_score", 0)), result["score"]),
        "latest_score": result["score"],
        "latest_tier": result.get("evidence", {}).get("tier"),
        "latest_evidence": result.get("evidence", {}),
        "score_history": history,
    }
    with open(path, "w") as f:
        json.dump(candidate, f, indent=2)

    latest = []
    for fp in candidate_dir.glob("0x*.json"):
        with open(fp) as f:
            latest.append(json.load(f))
    latest.sort(key=lambda c: c.get("best_score", 0), reverse=True)
    save_latest(str(candidate_dir), {"candidates": latest[:50]})


def scan_leaderboard():
    """Main scanning loop: check leaderboard wallets against fingerprint."""
    config = load_config()
    target = config["target_wallet"].lower()
    thresholds = config["alert_thresholds"]
    scanner_config = config["scanner"]

    # Load or build Ezekiel fingerprint
    fp_path = Path(DATA_DIR.parent / "profile" / "fingerprint.json")
    if fp_path.exists():
        with open(fp_path) as f:
            ezekiel_fp = json.load(f)
        print("[scanner] Loaded existing fingerprint")
    else:
        print("[scanner] No fingerprint found, building...")
        ezekiel_fp = build_fingerprint()

    # Fetch leaderboard
    leaderboard = fetch_leaderboard()
    print(f"[scanner] Leaderboard: {len(leaderboard)} entries")

    max_wallets = scanner_config["max_leaderboard_wallets"]
    min_fills = scanner_config["min_fills_for_comparison"]
    lookback_days = scanner_config["fills_lookback_days"]

    results = []
    top_scores = []  # Track all scores for diagnostics
    scanned = 0

    for entry in leaderboard[:max_wallets]:
        wallet = entry.get("ethAddress", entry.get("address", ""))
        if not wallet or wallet.lower() == target:
            continue

        scanned += 1
        if scanned % 50 == 0:
            print(f"[scanner] Scanned {scanned}/{min(len(leaderboard), max_wallets)}...")

        # Get candidate data
        fills = get_candidate_fills(wallet, lookback_days)
        if len(fills) < min_fills:
            continue

        state = get_candidate_state(wallet)
        candidate_fp = build_candidate_fingerprint(fills, state)

        score, dimensions, evidence = compute_similarity(ezekiel_fp, candidate_fp)

        result = {
            "wallet": wallet,
            "score": score,
            "dimensions": dimensions,
            "evidence": evidence,
            "fills_count": len(fills),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "fingerprint": _summarize_fingerprint(candidate_fp),
        }

        # Track top 10 scores regardless of threshold
        top_scores.append({"wallet": wallet[:10], "score": score})
        top_scores.sort(key=lambda x: x["score"], reverse=True)
        top_scores = top_scores[:10]

        if score >= thresholds["similarity_low"]:
            results.append(result)

        if score >= scanner_config.get("candidate_threshold", thresholds["similarity_medium"]):
            persist_candidate(result)

        if score >= thresholds["similarity_high"]:
            print(f"[scanner] HIGH MATCH: {wallet} (score={score:.4f})")
            alert_behavioral_match(wallet, score, dimensions)
        elif score >= thresholds["similarity_medium"]:
            print(f"[scanner] MEDIUM MATCH: {wallet} (score={score:.4f})")

        time.sleep(0.5)  # Rate limiting (avoid 429s)

    # Log top scores for diagnostics
    print(f"[scanner] Top 5 scores (any threshold): {top_scores[:5]}")

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)

    # Only keep full fingerprint data for top 20 (saves file size)
    for i, r in enumerate(results):
        if i >= 20:
            r.pop("fingerprint", None)

    # Save results
    scan_result = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "wallets_scanned": scanned,
        "matches_found": len(results),
        "thresholds": thresholds,
        "top_scores": top_scores,
        "results": results,
    }

    append_records(str(DATA_DIR / "scans"), [scan_result], key_field="scan_time")
    save_latest(str(DATA_DIR / "scans"), scan_result)

    print(f"[scanner] Scan complete: {scanned} wallets scanned, {len(results)} matches found")
    return scan_result


def main():
    scan_leaderboard()


if __name__ == "__main__":
    main()
