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


def compare_timing_profiles(fp_a: dict, fp_b: dict) -> float:
    """Compare timing profile dimensions using cosine similarity."""
    hourly_a = fp_a.get("hourly_distribution", [0]*24)
    hourly_b = fp_b.get("hourly_distribution", [0]*24)
    return cosine_similarity(hourly_a, hourly_b)


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


def compute_similarity(ezekiel_fp: dict, candidate_fp: dict) -> tuple[float, dict]:
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

    # Weighted average (sum to 1.0)
    weights = {
        "asset_preferences": 0.25,
        "timing_profile": 0.25,
        "leverage_profile": 0.20,
        "entry_exit_style": 0.15,
        "hold_duration": 0.15,
    }

    weighted_sum = sum(dimensions.get(k, 0) * w for k, w in weights.items())

    return round(weighted_sum, 4), dimensions


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
    }


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

        score, dimensions = compute_similarity(ezekiel_fp, candidate_fp)

        result = {
            "wallet": wallet,
            "score": score,
            "dimensions": dimensions,
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
