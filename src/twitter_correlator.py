# src/twitter_correlator.py
"""Correlates monitored tweets against wallet trades to measure timing alignment."""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.utils import load_config, load_all_records, save_latest, DATA_DIR


def load_tweets() -> list[dict]:
    return load_all_records(str(DATA_DIR / "twitter" / "tweets"))


def load_fills() -> list[dict]:
    return load_all_records(str(DATA_DIR / "fills"))


def parse_timestamp(ts_str: str) -> int | None:
    """Parse various timestamp formats to Unix ms."""
    if not ts_str:
        return None
    try:
        if isinstance(ts_str, (int, float)):
            return int(ts_str)
        for fmt in [
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
        ]:
            try:
                dt = datetime.strptime(ts_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def find_fills_near_tweet(tweet_ts_ms: int, fills: list[dict],
                          window_before_ms: int = 3600000,
                          window_after_ms: int = 14400000) -> list[dict]:
    """Find fills within a time window around a tweet."""
    start = tweet_ts_ms - window_before_ms  # 1h before
    end = tweet_ts_ms + window_after_ms      # 4h after
    return [
        f for f in fills
        if start <= f.get("time", 0) <= end
    ]


def compute_timing_correlation(tweets: list[dict], fills: list[dict]) -> dict:
    """Compute timing correlation between tweets and fills."""
    if not tweets or not fills:
        return {"score": 0, "sample_size": 0}

    matches = 0
    total_tweets_with_trading = 0

    for tweet in tweets:
        if not tweet.get("has_trading_content"):
            continue

        total_tweets_with_trading += 1
        tweet_ts = parse_timestamp(tweet.get("timestamp", ""))
        if tweet_ts is None:
            continue

        nearby_fills = find_fills_near_tweet(tweet_ts, fills)
        if nearby_fills:
            matches += 1

    if total_tweets_with_trading == 0:
        return {"score": 0, "sample_size": 0}

    return {
        "score": round(matches / total_tweets_with_trading, 4),
        "matches": matches,
        "sample_size": total_tweets_with_trading,
    }


def compute_direction_correlation(tweets: list[dict], fills: list[dict]) -> dict:
    """Check if tweet sentiment matches fill direction."""
    if not tweets or not fills:
        return {"score": 0, "sample_size": 0}

    direction_matches = 0
    total_comparable = 0

    for tweet in tweets:
        if not tweet.get("mentioned_coins") or not tweet.get("mentioned_direction"):
            continue

        tweet_ts = parse_timestamp(tweet.get("timestamp", ""))
        if tweet_ts is None:
            continue

        nearby_fills = find_fills_near_tweet(tweet_ts, fills)
        mentioned_coins = set(tweet["mentioned_coins"])

        for fill in nearby_fills:
            fill_coin = fill.get("coin", "")
            if fill_coin not in mentioned_coins:
                continue

            total_comparable += 1
            fill_side = fill.get("side", "")
            tweet_direction = tweet["mentioned_direction"]

            # B = buy/long, A = sell/short
            if (tweet_direction == "long" and fill_side == "B") or \
               (tweet_direction == "short" and fill_side == "A"):
                direction_matches += 1

    if total_comparable == 0:
        return {"score": 0, "sample_size": 0}

    return {
        "score": round(direction_matches / total_comparable, 4),
        "matches": direction_matches,
        "sample_size": total_comparable,
    }


def find_notable_matches(tweets: list[dict], fills: list[dict], top_n: int = 10) -> list[dict]:
    """Find the most notable tweet-trade correlations."""
    notable = []

    for tweet in tweets:
        if not tweet.get("has_trading_content"):
            continue

        tweet_ts = parse_timestamp(tweet.get("timestamp", ""))
        if tweet_ts is None:
            continue

        nearby_fills = find_fills_near_tweet(tweet_ts, fills, window_after_ms=7200000)  # 2h

        for fill in nearby_fills:
            fill_ts = fill.get("time", 0)
            delay_minutes = (fill_ts - tweet_ts) / 60000

            coin_match = fill.get("coin", "") in tweet.get("mentioned_coins", [])

            notable.append({
                "tweet": f"@{tweet.get('source_account', '?')} tweeted '{tweet.get('text', '')[:80]}' at {tweet.get('timestamp', '?')}",
                "trade": f"Wallet {'bought' if fill.get('side') == 'B' else 'sold'} {fill.get('coin', '?')} at {fill.get('px', '?')}",
                "delay_minutes": round(delay_minutes, 1),
                "coin_match": coin_match,
                "correlation_type": ("timing + direction + coin" if coin_match else "timing only"),
            })

    # Sort by strongest correlations
    notable.sort(key=lambda x: (x["coin_match"], -abs(x["delay_minutes"])), reverse=True)
    return notable[:top_n]


def correlate():
    """Run full correlation analysis."""
    tweets = load_tweets()
    fills = load_fills()

    print(f"[correlator] Data: {len(tweets)} tweets, {len(fills)} fills")

    if not tweets:
        print("[correlator] No tweets to correlate. Skipping.")
        return

    timing_corr = compute_timing_correlation(tweets, fills)
    direction_corr = compute_direction_correlation(tweets, fills)
    notable = find_notable_matches(tweets, fills)

    # Compute overall confidence
    timing_score = timing_corr.get("score", 0)
    direction_score = direction_corr.get("score", 0)
    sample_size = timing_corr.get("sample_size", 0)

    # Confidence level
    if sample_size < 5:
        confidence = "INSUFFICIENT_DATA"
    elif timing_score > 0.6 and direction_score > 0.6:
        confidence = "HIGH"
    elif timing_score > 0.4 or direction_score > 0.4:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Get time range
    tweet_timestamps = []
    for t in tweets:
        ts = parse_timestamp(t.get("timestamp", ""))
        if ts:
            tweet_timestamps.append(ts)

    time_range = ""
    if tweet_timestamps:
        first = datetime.fromtimestamp(min(tweet_timestamps) / 1000, tz=timezone.utc)
        last = datetime.fromtimestamp(max(tweet_timestamps) / 1000, tz=timezone.utc)
        time_range = f"{first.strftime('%Y-%m-%d')} to {last.strftime('%Y-%m-%d')}"

    result = {
        "summary": "Trade-tweet timing correlation analysis",
        "confidence": confidence,
        "evidence": {
            "timing_correlation": timing_corr,
            "direction_correlation": direction_corr,
            "sample_size": sample_size,
            "time_range": time_range,
        },
        "notable_matches": notable,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Save results
    corr_dir = str(DATA_DIR / "twitter" / "correlation")
    save_latest(corr_dir, result)
    append_records(corr_dir, [result], key_field="computed_at")

    print(f"[correlator] Confidence: {confidence}")
    print(f"[correlator] Timing correlation: {timing_score:.2f} ({timing_corr.get('sample_size', 0)} samples)")
    print(f"[correlator] Direction correlation: {direction_score:.2f} ({direction_corr.get('sample_size', 0)} samples)")
    print(f"[correlator] Notable matches: {len(notable)}")

    return result


def main():
    correlate()


if __name__ == "__main__":
    main()
