# src/twitter_monitor.py
"""Fetches tweets via RSS bridges and extracts trading signals."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import feedparser

from src.utils import load_config, append_records, save_latest, DATA_DIR

# Known crypto tickers for signal extraction
CRYPTO_TICKERS = {
    "BTC", "ETH", "SOL", "AVAX", "ARB", "OP", "MATIC", "DOGE", "SHIB",
    "LINK", "UNI", "AAVE", "CRV", "MKR", "SNX", "COMP", "SUSHI", "YFI",
    "DOT", "ADA", "ATOM", "NEAR", "FTM", "ALGO", "XRP", "LTC", "BCH",
    "APT", "SUI", "SEI", "TIA", "JUP", "WIF", "PEPE", "BONK", "ONDO",
    "ENA", "PENDLE", "EIGEN", "STRK", "BLAST", "MANTA", "DYM", "JTO",
    "TRX", "BNB", "TON", "RENDER", "FET", "TAO", "INJ", "ORDI", "STX",
}

# RSS bridge URLs to try (in priority order)
RSS_BRIDGES = [
    "https://nitter.net/{account}/rss",
    "https://nitter.privacydev.net/{account}/rss",
    "https://nitter.poast.org/{account}/rss",
    "https://rss.app/feeds/v1.1/twitter/{account}",
]

BULLISH_KEYWORDS = {
    "long", "buy", "bullish", "moon", "pump", "send it", "accumulate",
    "bid", "calls", "upside", "breakout", "higher", "support",
    "bottom", "dip", "undervalued", "cheap",
}

BEARISH_KEYWORDS = {
    "short", "sell", "bearish", "dump", "crash", "fade", "top",
    "overvalued", "resistance", "lower", "breakdown", "puts",
    "exit", "close", "reduce",
}


def fetch_tweets_rss(account: str) -> list[dict]:
    """Fetch tweets for an account via RSS bridges."""
    for bridge_template in RSS_BRIDGES:
        url = bridge_template.format(account=account)
        try:
            feed = feedparser.parse(url)
            if feed.entries:
                tweets = []
                for entry in feed.entries:
                    tweet = {
                        "source_account": f"@{account}",
                        "tweet_id": entry.get("id", entry.get("link", "")),
                        "timestamp": entry.get("published", ""),
                        "text": entry.get("title", "") or entry.get("summary", ""),
                        "link": entry.get("link", ""),
                    }
                    tweets.append(tweet)
                print(f"[twitter] Fetched {len(tweets)} tweets from @{account} via {url}")
                return tweets
        except Exception as e:
            continue

    print(f"[twitter] Could not fetch tweets for @{account} from any RSS bridge")
    return []


def extract_trading_signals(tweet: dict) -> dict:
    """Extract trading signals from a tweet."""
    text = tweet.get("text", "").upper()
    text_lower = tweet.get("text", "").lower()

    # Find mentioned coins
    mentioned_coins = []
    for ticker in CRYPTO_TICKERS:
        # Match $BTC, BTC, #BTC patterns
        patterns = [
            rf'\${ticker}\b',
            rf'\b{ticker}\b',
            rf'#{ticker}\b',
        ]
        for pattern in patterns:
            if re.search(pattern, text):
                mentioned_coins.append(ticker)
                break

    # Determine sentiment
    bullish_matches = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bearish_matches = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)

    if bullish_matches > bearish_matches:
        sentiment = "bullish"
        direction = "long"
    elif bearish_matches > bullish_matches:
        sentiment = "bearish"
        direction = "short"
    else:
        sentiment = "neutral"
        direction = None

    # Extract price targets
    price_pattern = r'\$[\d,]+\.?\d*[kKmM]?'
    targets = re.findall(price_pattern, tweet.get("text", ""))

    # Extract leverage mentions
    leverage_pattern = r'(\d+)[xX](?:\s|[.,!?]|$)'
    leverage_matches = re.findall(leverage_pattern, tweet.get("text", ""))
    mentioned_leverage = int(leverage_matches[0]) if leverage_matches else None

    has_trading = bool(mentioned_coins) or bullish_matches > 0 or bearish_matches > 0

    tweet.update({
        "has_trading_content": has_trading,
        "mentioned_coins": list(set(mentioned_coins)),
        "sentiment": sentiment,
        "mentioned_direction": direction,
        "mentioned_leverage": mentioned_leverage,
        "mentioned_targets": targets,
    })

    return tweet


def monitor_tweets():
    """Main monitoring loop: fetch and analyze new tweets."""
    config = load_config()
    accounts = config.get("twitter_accounts", [])

    all_tweets = []
    for account in accounts:
        print(f"[twitter] Monitoring @{account}...")
        raw_tweets = fetch_tweets_rss(account)

        for tweet in raw_tweets:
            enriched = extract_trading_signals(tweet)
            all_tweets.append(enriched)

    if all_tweets:
        # Store tweets
        twitter_dir = str(DATA_DIR / "twitter" / "tweets")
        added = append_records(twitter_dir, all_tweets, key_field="tweet_id")
        print(f"[twitter] Stored {added} new tweets")

        # Save latest
        save_latest(str(DATA_DIR / "twitter"), {
            "last_check": datetime.now(timezone.utc).isoformat(),
            "accounts_checked": accounts,
            "tweets_found": len(all_tweets),
            "trading_signals": [t for t in all_tweets if t.get("has_trading_content")],
        })
    else:
        print("[twitter] No new tweets found")

    return all_tweets


def main():
    monitor_tweets()


if __name__ == "__main__":
    main()
