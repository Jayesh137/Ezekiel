# src/fingerprint.py
"""Computes behavioral fingerprint from all collected trading data."""

import json
import shutil
import sys
import time as _time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.utils import (
    load_config, load_all_records, save_latest, DATA_DIR
)


def load_fills() -> list[dict]:
    return load_all_records(str(DATA_DIR / "fills"))


def load_funding() -> list[dict]:
    return load_all_records(str(DATA_DIR / "funding"))


def load_positions_latest() -> dict:
    fp = DATA_DIR / "positions" / "latest.json"
    if fp.exists():
        with open(fp) as f:
            return json.load(f)
    return {}


def load_account_latest() -> dict:
    fp = DATA_DIR / "account" / "latest.json"
    if fp.exists():
        with open(fp) as f:
            return json.load(f)
    return {}


def compute_asset_preferences(fills: list[dict]) -> dict:
    """Which coins traded and how often."""
    if not fills:
        return {"coins_traded": [], "coin_frequency": {}, "top_5_by_volume": []}

    coin_counts = Counter(f.get("coin", "UNKNOWN") for f in fills)
    total = sum(coin_counts.values())
    coin_freq = {k: round(v / total, 4) for k, v in coin_counts.most_common()}

    # Volume by coin
    coin_volume = defaultdict(float)
    for f in fills:
        coin = f.get("coin", "UNKNOWN")
        sz = float(f.get("sz", 0))
        px = float(f.get("px", 0))
        coin_volume[coin] += sz * px

    top_by_vol = sorted(coin_volume, key=lambda c: coin_volume[c], reverse=True)[:5]

    return {
        "weight": 0.15,
        "coins_traded": sorted(coin_counts.keys()),
        "coin_frequency": coin_freq,
        "top_5_by_volume": top_by_vol,
        "total_unique_coins": len(coin_counts),
    }


def compute_leverage_profile(fills: list[dict], positions: dict) -> dict:
    """Leverage habits per asset."""
    if not positions:
        return {"weight": 0.15, "per_coin": {}, "overall": {}}

    asset_positions = positions.get("assetPositions", [])
    per_coin = {}
    all_leverages = []

    for ap in asset_positions:
        pos = ap.get("position", {})
        coin = pos.get("coin", "UNKNOWN")
        leverage = pos.get("leverage", {})
        lev_type = leverage.get("type", "unknown")
        lev_value = float(leverage.get("value", 0))
        if lev_value > 0:
            per_coin[coin] = {
                "value": lev_value,
                "type": lev_type,
            }
            all_leverages.append(lev_value)

    overall = {}
    if all_leverages:
        arr = np.array(all_leverages)
        overall = {
            "mean": round(float(np.mean(arr)), 2),
            "median": round(float(np.median(arr)), 2),
            "max": round(float(np.max(arr)), 2),
        }

    return {
        "weight": 0.15,
        "per_coin": per_coin,
        "overall": overall,
    }


def compute_position_sizing(fills: list[dict], positions: dict) -> dict:
    """Position sizes relative to account value."""
    if not fills:
        return {"weight": 0.12, "notional_ranges": {}, "size_to_account_ratio": {}}

    # Compute notional per coin
    coin_notionals = defaultdict(list)
    for f in fills:
        coin = f.get("coin", "UNKNOWN")
        sz = float(f.get("sz", 0))
        px = float(f.get("px", 0))
        notional = sz * px
        if notional > 0:
            coin_notionals[coin].append(notional)

    notional_ranges = {}
    for coin, notionals in coin_notionals.items():
        arr = np.array(notionals)
        notional_ranges[coin] = {
            "typical_min_usd": round(float(np.percentile(arr, 10)), 2),
            "typical_max_usd": round(float(np.percentile(arr, 90)), 2),
            "mean_usd": round(float(np.mean(arr)), 2),
        }

    # Account value from positions
    account_value = 0
    if positions:
        account_value = float(positions.get("marginSummary", {}).get("accountValue", 0))

    size_ratio = {}
    if account_value > 0:
        all_notionals = [n for ns in coin_notionals.values() for n in ns]
        ratios = [n / account_value for n in all_notionals]
        arr = np.array(ratios)
        size_ratio = {
            "mean": round(float(np.mean(arr)), 4),
            "median": round(float(np.median(arr)), 4),
            "std": round(float(np.std(arr)), 4),
        }

    return {
        "weight": 0.12,
        "notional_ranges": notional_ranges,
        "size_to_account_ratio": size_ratio,
        "account_value_usd": round(account_value, 2),
    }


def compute_timing_profile(fills: list[dict]) -> dict:
    """When they trade — timezone fingerprint."""
    if not fills:
        return {"weight": 0.15, "hourly_distribution": [0]*24, "day_of_week_distribution": [0]*7}

    hours = []
    days = []
    for f in fills:
        ts = f.get("time", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            hours.append(dt.hour)
            days.append(dt.weekday())

    hourly_counts = Counter(hours)
    daily_counts = Counter(days)
    total_h = sum(hourly_counts.values()) or 1
    total_d = sum(daily_counts.values()) or 1

    hourly_dist = [round(hourly_counts.get(h, 0) / total_h, 4) for h in range(24)]
    daily_dist = [round(daily_counts.get(d, 0) / total_d, 4) for d in range(7)]

    # Most/least active hours
    sorted_hours = sorted(range(24), key=lambda h: hourly_dist[h], reverse=True)
    most_active = sorted_hours[:7]
    least_active = sorted_hours[-5:]

    # Infer timezone: peak activity hours suggest offset from UTC
    peak_hour = sorted_hours[0]
    # Assume trader is most active in afternoon (14-18 local time)
    inferred_offset = (16 - peak_hour) % 24
    if inferred_offset > 12:
        inferred_offset -= 24

    return {
        "weight": 0.15,
        "hourly_distribution": hourly_dist,
        "day_of_week_distribution": daily_dist,
        "most_active_hours_utc": sorted(most_active),
        "least_active_hours_utc": sorted(least_active),
        "inferred_timezone_offset": inferred_offset,
    }


def compute_hold_duration(fills: list[dict]) -> dict:
    """How long positions are held."""
    if not fills:
        return {"weight": 0.10, "overall_minutes": {}, "distribution_buckets": {}}

    # Match opens and closes per coin
    coin_trades = defaultdict(list)
    for f in fills:
        coin_trades[f.get("coin", "UNKNOWN")].append(f)

    durations_minutes = []
    per_coin_durations = defaultdict(list)

    for coin, trades in coin_trades.items():
        sorted_trades = sorted(trades, key=lambda t: t.get("time", 0))
        open_time = None
        for t in sorted_trades:
            dir_field = t.get("dir", "")
            if "Open" in str(dir_field):
                open_time = t.get("time", 0)
            elif "Close" in str(dir_field) and open_time:
                close_time = t.get("time", 0)
                dur = (close_time - open_time) / 60000  # ms to minutes
                if dur > 0:
                    durations_minutes.append(dur)
                    per_coin_durations[coin].append(dur)
                open_time = None

    overall = {}
    if durations_minutes:
        arr = np.array(durations_minutes)
        overall = {
            "mean": round(float(np.mean(arr)), 1),
            "median": round(float(np.median(arr)), 1),
            "p25": round(float(np.percentile(arr, 25)), 1),
            "p75": round(float(np.percentile(arr, 75)), 1),
        }

    # Distribution buckets
    buckets = {"under_1h": 0, "1h_to_4h": 0, "4h_to_24h": 0, "1d_to_7d": 0, "over_7d": 0}
    for d in durations_minutes:
        if d < 60:
            buckets["under_1h"] += 1
        elif d < 240:
            buckets["1h_to_4h"] += 1
        elif d < 1440:
            buckets["4h_to_24h"] += 1
        elif d < 10080:
            buckets["1d_to_7d"] += 1
        else:
            buckets["over_7d"] += 1

    total_b = sum(buckets.values()) or 1
    bucket_pcts = {k: round(v / total_b, 4) for k, v in buckets.items()}

    per_coin_summary = {}
    for coin, durs in per_coin_durations.items():
        arr = np.array(durs)
        per_coin_summary[coin] = {
            "mean_minutes": round(float(np.mean(arr)), 1),
            "median_minutes": round(float(np.median(arr)), 1),
        }

    return {
        "weight": 0.10,
        "overall_minutes": overall,
        "per_coin": per_coin_summary,
        "distribution_buckets": bucket_pcts,
    }


def compute_entry_exit_style(fills: list[dict]) -> dict:
    """Market vs limit, order types, etc."""
    if not fills:
        return {"weight": 0.10, "order_type_ratio": {}}

    # Count crossed (market) vs not crossed (limit)
    crossed_count = sum(1 for f in fills if f.get("crossed", False))
    total = len(fills)
    market_ratio = round(crossed_count / total, 4) if total else 0
    limit_ratio = round(1 - market_ratio, 4)

    # Closed PnL analysis for take profit / stop loss
    closed_pnls = []
    for f in fills:
        pnl = f.get("closedPnl")
        if pnl and pnl != "0":
            closed_pnls.append(float(pnl))

    tp_pnls = [p for p in closed_pnls if p > 0]
    sl_pnls = [p for p in closed_pnls if p < 0]

    tp_stats = {}
    if tp_pnls:
        arr = np.array(tp_pnls)
        tp_stats = {"mean": round(float(np.mean(arr)), 2), "median": round(float(np.median(arr)), 2)}

    sl_stats = {}
    if sl_pnls:
        arr = np.array(sl_pnls)
        sl_stats = {"mean": round(float(np.mean(arr)), 2), "median": round(float(np.median(arr)), 2)}

    # Fee analysis
    fees = [float(f.get("fee", 0)) for f in fills if f.get("fee")]
    fee_stats = {}
    if fees:
        arr = np.array(fees)
        fee_stats = {"total": round(float(np.sum(arr)), 2), "mean": round(float(np.mean(arr)), 4)}

    return {
        "weight": 0.10,
        "order_type_ratio": {"market": market_ratio, "limit": limit_ratio},
        "take_profit_pnl": tp_stats,
        "stop_loss_pnl": sl_stats,
        "fee_stats": fee_stats,
        "total_closed_trades": len(closed_pnls),
        "win_rate": round(len(tp_pnls) / len(closed_pnls), 4) if closed_pnls else 0,
    }


def compute_risk_management(fills: list[dict], positions: dict, funding: list[dict]) -> dict:
    """Drawdown behavior, margin utilization."""
    if not positions:
        return {"weight": 0.08}

    margin_summary = positions.get("marginSummary", {})
    account_value = float(margin_summary.get("accountValue", 0))
    total_margin = float(margin_summary.get("totalMarginUsed", 0))
    margin_util = round(total_margin / account_value, 4) if account_value else 0

    # Count simultaneous positions
    asset_positions = positions.get("assetPositions", [])
    active = [ap for ap in asset_positions if float(ap.get("position", {}).get("szi", 0)) != 0]

    # Funding sensitivity
    holds_through_funding = len(funding) > 0

    return {
        "weight": 0.08,
        "margin_utilization": margin_util,
        "account_value_usd": round(account_value, 2),
        "total_margin_used": round(total_margin, 2),
        "current_positions_count": len(active),
        "max_simultaneous_positions": len(active),  # Will improve with historical data
        "holds_through_funding": holds_through_funding,
        "total_funding_events": len(funding),
    }


def compute_trade_sequencing(fills: list[dict]) -> dict:
    """Patterns in how trades are ordered."""
    if not fills:
        return {"weight": 0.08}

    sorted_fills = sorted(fills, key=lambda f: f.get("time", 0))

    # Find correlated pairs: coins that tend to be traded within short time windows
    coin_times = defaultdict(list)
    for f in sorted_fills:
        coin_times[f.get("coin", "UNKNOWN")].append(f.get("time", 0))

    # Time between consecutive fills
    inter_fill_times = []
    for i in range(1, len(sorted_fills)):
        delta = sorted_fills[i].get("time", 0) - sorted_fills[i-1].get("time", 0)
        if delta > 0:
            inter_fill_times.append(delta / 60000)  # minutes

    inter_fill_stats = {}
    if inter_fill_times:
        arr = np.array(inter_fill_times)
        inter_fill_stats = {
            "mean_minutes": round(float(np.mean(arr)), 1),
            "median_minutes": round(float(np.median(arr)), 1),
        }

    # Direction patterns
    sides = [f.get("side", "") for f in sorted_fills]
    buy_count = sum(1 for s in sides if s == "B")
    sell_count = sum(1 for s in sides if s == "A")
    total = buy_count + sell_count or 1

    return {
        "weight": 0.08,
        "inter_fill_timing": inter_fill_stats,
        "buy_sell_ratio": {
            "buy_pct": round(buy_count / total, 4),
            "sell_pct": round(sell_count / total, 4),
        },
        "total_fills": len(sorted_fills),
    }


def compute_account_characteristics(positions: dict, fills: list[dict]) -> dict:
    """Account size and volume bracket."""
    account_value = 0
    if positions:
        account_value = float(positions.get("marginSummary", {}).get("accountValue", 0))

    total_volume = 0
    for f in fills:
        sz = float(f.get("sz", 0))
        px = float(f.get("px", 0))
        total_volume += sz * px

    # Estimate weekly volume
    if fills:
        timestamps = [f.get("time", 0) for f in fills]
        time_range_ms = max(timestamps) - min(timestamps)
        weeks = time_range_ms / (7 * 24 * 60 * 60 * 1000) if time_range_ms > 0 else 1
        weekly_volume = total_volume / max(weeks, 1)
    else:
        weekly_volume = 0

    return {
        "weight": 0.07,
        "account_value_usd": round(account_value, 2),
        "total_volume_usd": round(total_volume, 2),
        "weekly_volume_usd": round(weekly_volume, 2),
    }


def build_fingerprint(fills: list[dict] | None = None) -> dict:
    """Build the complete behavioral fingerprint."""
    if fills is None:
        fills = load_fills()
    funding = load_funding()
    positions = load_positions_latest()
    account = load_account_latest()

    # Use perp positions if available
    if isinstance(positions, dict) and "assetPositions" not in positions:
        if "perp" in positions:
            positions = positions["perp"]

    print(f"[fingerprint] Data: {len(fills)} fills, {len(funding)} funding events")

    data_range = {}
    if fills:
        timestamps = [f.get("time", 0) for f in fills]
        first = min(timestamps)
        last = max(timestamps)
        data_range = {
            "first_fill": datetime.fromtimestamp(first / 1000, tz=timezone.utc).isoformat(),
            "last_fill": datetime.fromtimestamp(last / 1000, tz=timezone.utc).isoformat(),
            "total_fills": len(fills),
            "total_days_active": max(1, (last - first) // (24 * 60 * 60 * 1000)),
        }

    fingerprint = {
        "version": "1.0",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "data_range": data_range,
        "asset_preferences": compute_asset_preferences(fills),
        "leverage_profile": compute_leverage_profile(fills, positions),
        "position_sizing": compute_position_sizing(fills, positions),
        "timing_profile": compute_timing_profile(fills),
        "hold_duration": compute_hold_duration(fills),
        "entry_exit_style": compute_entry_exit_style(fills),
        "risk_management": compute_risk_management(fills, positions, funding),
        "trade_sequencing": compute_trade_sequencing(fills),
        "account_characteristics": compute_account_characteristics(positions, fills),
    }

    return fingerprint


def build_fingerprint_recent(fills: list[dict], lookback_days: int = 21) -> dict:
    """Build a fingerprint from only the most recent fills.
    Used by the scanner for fair apples-to-apples comparison with candidate mini-fingerprints
    (which are also built from a short lookback window)."""
    cutoff_ms = (_time.time() - lookback_days * 86400) * 1000
    recent = [f for f in fills if f.get("time", 0) >= cutoff_ms]
    if len(recent) < 20:
        return {}

    positions = load_positions_latest()
    if isinstance(positions, dict) and "assetPositions" not in positions:
        if "perp" in positions:
            positions = positions["perp"]

    return {
        "version": "1.0-recent",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "data_range": {"total_fills": len(recent)},
        "asset_preferences": compute_asset_preferences(recent),
        "leverage_profile": compute_leverage_profile(recent, positions),
        "position_sizing": compute_position_sizing(recent, positions),
        "timing_profile": compute_timing_profile(recent),
        "hold_duration": compute_hold_duration(recent),
        "entry_exit_style": compute_entry_exit_style(recent),
        "trade_sequencing": compute_trade_sequencing(recent),
        "account_characteristics": compute_account_characteristics(positions, recent),
    }


def main():
    config = load_config()
    print(f"[fingerprint] Building fingerprint for {config['target_wallet']}")

    fills = load_fills()
    fingerprint = build_fingerprint(fills)

    profile_dir = Path(DATA_DIR.parent / "profile")
    profile_dir.mkdir(exist_ok=True)
    fp_path = profile_dir / "fingerprint.json"

    # Archive the existing fingerprint before overwriting
    if fp_path.exists():
        history_dir = profile_dir / "history"
        history_dir.mkdir(exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archive_path = history_dir / f"fingerprint_{today}.json"
        if not archive_path.exists():
            shutil.copy(fp_path, archive_path)
        archives = sorted(history_dir.glob("fingerprint_*.json"))
        for old in archives[:-30]:
            old.unlink()

    save_latest(str(profile_dir), fingerprint)
    with open(fp_path, "w") as f:
        json.dump(fingerprint, f, indent=2)
    print(f"[fingerprint] Full fingerprint saved ({len(fills)} fills)")

    # Build and save recent fingerprint for fair scanner comparisons (last 21 days only)
    recent_fp = build_fingerprint_recent(fills, lookback_days=21)
    if recent_fp:
        recent_path = profile_dir / "fingerprint_recent.json"
        with open(recent_path, "w") as f:
            json.dump(recent_fp, f, indent=2)
        print(f"[fingerprint] Recent fingerprint saved ({recent_fp['data_range']['total_fills']} fills, last 21 days)")
    else:
        print("[fingerprint] Not enough recent fills for recent fingerprint (need >=20)")

    print(f"[fingerprint] Dimensions computed: {len([k for k in fingerprint if k not in ['version', 'computed_at', 'data_range']])}")


if __name__ == "__main__":
    main()
