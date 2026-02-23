# src/profile_builder.py
"""Ingests research documents into a structured trader profile."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import save_latest, DATA_DIR


def parse_docx(filepath: str) -> str:
    """Extract text from a Word document."""
    try:
        from docx import Document
        doc = Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except ImportError:
        print(f"[profile] python-docx not installed, skipping {filepath}")
        return ""
    except Exception as e:
        print(f"[profile] Error reading {filepath}: {e}")
        return ""


def parse_pdf(filepath: str) -> str:
    """Extract text from a PDF document."""
    text = ""

    # Try PyPDF2 first
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        text = "\n".join(text_parts)
        if len(text) > 50:
            return text
        print(f"[profile] PyPDF2 extracted only {len(text)} chars, trying fallback...")
    except ImportError:
        print(f"[profile] PyPDF2 not installed")
    except Exception as e:
        print(f"[profile] PyPDF2 error: {e}")

    # Fallback: try pdfminer
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        text = pdfminer_extract(filepath)
        if text.strip():
            print(f"[profile] pdfminer extracted {len(text)} chars")
            return text
    except ImportError:
        print(f"[profile] pdfminer not installed, skipping fallback for {filepath}")
    except Exception as e:
        print(f"[profile] pdfminer error: {e}")

    if not text:
        print(f"[profile] WARNING: Could not extract text from {filepath} — may be image-only PDF")
    return text


def extract_trading_patterns(text: str) -> dict:
    """Extract trading patterns and strategies from research text."""
    patterns = {
        "mentioned_coins": [],
        "strategies": [],
        "risk_style": [],
        "timeframes": [],
        "key_quotes": [],
    }

    if not text:
        return patterns

    # Known crypto tickers
    tickers = {
        "BTC", "ETH", "SOL", "AVAX", "ARB", "OP", "MATIC", "DOGE",
        "LINK", "UNI", "AAVE", "DOT", "ADA", "ATOM", "NEAR", "FTM",
        "APT", "SUI", "SEI", "TIA", "JUP", "XRP", "LTC", "BNB",
        "WIF", "PEPE", "BONK", "ONDO", "ENA", "PENDLE", "INJ", "TAO",
        "RENDER", "FET", "STX", "TON", "TRX", "EIGEN", "STRK",
    }

    # HIP-3 / stock perp tickers
    hip3_tickers = {
        "XYZ100", "SILVER", "COPPER", "MU", "SNDK", "GOLD", "OIL",
        "SPX", "NDX", "TSLA", "AAPL", "NVDA", "MSFT", "AMZN", "META",
    }

    # Find mentioned coins
    for ticker in tickers:
        if re.search(rf'\b{ticker}\b', text.upper()):
            patterns["mentioned_coins"].append(ticker)

    # Find HIP-3 mentions (with or without xyz: prefix)
    for ticker in hip3_tickers:
        if re.search(rf'\b{ticker}\b', text.upper()):
            patterns["mentioned_coins"].append(f"xyz:{ticker}")

    # Extract strategy keywords
    strategy_keywords = [
        "trend following", "mean reversion", "momentum", "contrarian",
        "swing trading", "scalping", "position trading", "macro",
        "technical analysis", "fundamental analysis", "narrative",
        "DCA", "dollar cost averaging", "grid trading",
    ]
    for kw in strategy_keywords:
        if kw.lower() in text.lower():
            patterns["strategies"].append(kw)

    # Risk style keywords
    risk_keywords = [
        "high leverage", "low leverage", "conservative", "aggressive",
        "risk management", "stop loss", "take profit", "hedging",
        "portfolio allocation", "position sizing",
    ]
    for kw in risk_keywords:
        if kw.lower() in text.lower():
            patterns["risk_style"].append(kw)

    # Timeframe keywords
    timeframe_keywords = [
        "short term", "long term", "intraday", "swing", "weekly",
        "daily", "hourly", "multi-day", "scalp",
    ]
    for kw in timeframe_keywords:
        if kw.lower() in text.lower():
            patterns["timeframes"].append(kw)

    # Extract sentences that mention trading (key quotes)
    sentences = re.split(r'[.!?]+', text)
    trading_words = {"trade", "position", "long", "short", "buy", "sell", "profit", "loss", "leverage"}
    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) > 20 and len(sentence) < 300:
            words = set(sentence.lower().split())
            if words & trading_words:
                patterns["key_quotes"].append(sentence)
                if len(patterns["key_quotes"]) >= 20:
                    break

    return patterns


def build_profile():
    """Build trader profile from research documents."""
    research_dir = DATA_DIR.parent / "research"
    profile_dir = str(DATA_DIR.parent / "profile")

    profile = {
        "codename": "Ezekiel",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "sources": [],
        "aggregated_patterns": {
            "mentioned_coins": [],
            "strategies": [],
            "risk_style": [],
            "timeframes": [],
        },
        "key_quotes": [],
        "raw_extractions": {},
    }

    # Process each research file
    for filepath in sorted(research_dir.glob("*")):
        if filepath.suffix.lower() == ".docx":
            text = parse_docx(str(filepath))
        elif filepath.suffix.lower() == ".pdf":
            text = parse_pdf(str(filepath))
        else:
            continue

        if not text:
            print(f"[profile] No text extracted from {filepath.name}")
            continue

        print(f"[profile] Processing {filepath.name} ({len(text)} chars)")
        profile["sources"].append({
            "filename": filepath.name,
            "char_count": len(text),
        })

        patterns = extract_trading_patterns(text)
        profile["raw_extractions"][filepath.name] = patterns

        # Aggregate
        for key in ["mentioned_coins", "strategies", "risk_style", "timeframes"]:
            profile["aggregated_patterns"][key].extend(patterns.get(key, []))

        profile["key_quotes"].extend(patterns.get("key_quotes", []))

    # Deduplicate aggregated patterns
    for key in ["mentioned_coins", "strategies", "risk_style", "timeframes"]:
        profile["aggregated_patterns"][key] = sorted(set(profile["aggregated_patterns"][key]))

    profile["key_quotes"] = list(dict.fromkeys(profile["key_quotes"]))[:30]

    # Save
    save_latest(profile_dir, profile)

    # Also save as trader_profile.json
    tp_path = Path(profile_dir) / "trader_profile.json"
    with open(tp_path, "w") as f:
        json.dump(profile, f, indent=2)

    print(f"[profile] Profile saved with {len(profile['sources'])} sources")
    print(f"[profile] Coins: {profile['aggregated_patterns']['mentioned_coins']}")
    print(f"[profile] Strategies: {profile['aggregated_patterns']['strategies']}")

    return profile


def main():
    build_profile()


if __name__ == "__main__":
    main()
