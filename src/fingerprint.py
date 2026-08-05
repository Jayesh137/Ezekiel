# src/fingerprint.py
"""Computes behavioral fingerprint from all collected trading data."""

import json
import shutil
import sys
import time as _time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.utils import DATA_DIR, load_all_records, load_config, save_latest


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


# A timezone fingerprint needs many separate DAYS, not many fills. This trader
# executes via TWAP, so a single session can contribute thousands of fills across
# two or three hours. Below this many distinct days the hourly histogram records
# when a few bursts happened, not when the human is habitually awake.
#
# Measured: the backtest's two windows held 3,162 and 5,497 fills but only 4
# distinct trading days each, with 4 and 7 active hours. They shared one hour, so
# the trader scored 0.0045 on timing against his own history — a near-zero on a
# 0.14-weight dimension, which is a false-negative generator, not a finding.
MIN_TIMING_DAYS = 10


def compute_timing_profile(fills: list[dict]) -> dict:
    """When they trade — timezone fingerprint."""
    if not fills:
        return {"weight": 0.15, "hourly_distribution": [0]*24,
                "day_of_week_distribution": [0]*7,
                "distinct_days": 0, "sufficient_data": False}

    hours = []
    days = []
    distinct_days = set()
    for f in fills:
        ts = f.get("time", 0)
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
            hours.append(dt.hour)
            days.append(dt.weekday())
            distinct_days.add(int(ts) // 86_400_000)

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
        "distinct_days": len(distinct_days),
        "sufficient_data": len(distinct_days) >= MIN_TIMING_DAYS,
    }


# A 5-bucket histogram built from a handful of episodes is noise. Below this
# many episodes the dimension reports insufficient_data and the scanner excludes
# it (renormalizing the remaining weights) instead of scoring a misleading 0.0.
MIN_HOLD_EPISODES = 5


def compute_hold_duration(fills: list[dict]) -> dict:
    """How long positions are held, reconstructed from position episodes.

    Uses _position_episodes (startPosition-based) rather than scanning `dir`
    strings for Open/Close. The previous matcher kept a single open_time per coin,
    so a TWAP scale-in overwrote it and every close after the first was silently
    dropped; a window starting mid-position (all closes, no opens) produced no
    durations at all. Measured on the target's own history that gave 2 durations
    from 34,244 fills in one window and 0 from 3,162 in the next, scoring the
    trader 0.0 against himself on this dimension.

    Zero-length episodes are kept: a TWAP batch filling within one millisecond is
    a real sub-minute hold, and dropping it biased the distribution.
    """
    if not fills:
        return {"weight": 0.10, "overall_minutes": {}, "distribution_buckets": {},
                "episode_count": 0, "sufficient_data": False}

    episodes = _position_episodes(fills)

    durations_minutes = []
    per_coin_durations = defaultdict(list)
    for ep in episodes:
        if not ep:
            continue
        coin = ep[0].get("coin", "UNKNOWN")
        dur = (ep[-1].get("time", 0) - ep[0].get("time", 0)) / 60000  # ms to minutes
        if dur < 0:
            continue
        durations_minutes.append(dur)
        per_coin_durations[coin].append(dur)

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
        "episode_count": len(durations_minutes),
        "sufficient_data": len(durations_minutes) >= MIN_HOLD_EPISODES,
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


def _signed_size(fill: dict) -> float:
    sz = float(fill.get("sz", 0))
    return sz if fill.get("side") == "B" else -sz


def _position_episodes(fills: list[dict]) -> list[list[dict]]:
    """Reconstruct position episodes per coin: an episode runs from a flat
    position until the position returns to (approximately) zero."""
    coin_fills = defaultdict(list)
    for f in fills:
        if f.get("dir") in ("Buy", "Sell"):  # spot fills have no position lifecycle
            continue
        coin_fills[f.get("coin", "UNKNOWN")].append(f)

    episodes = []
    for cf in coin_fills.values():
        cf.sort(key=lambda t: t.get("time", 0))
        current = []
        for f in cf:
            start_pos = float(f.get("startPosition", 0) or 0)
            end_pos = start_pos + _signed_size(f)
            if not current and start_pos == 0 and end_pos == 0:
                continue
            current.append(f)
            # Position flat again (tolerance for float dust relative to fill size)
            if abs(end_pos) < max(1e-9, abs(_signed_size(f)) * 1e-6):
                episodes.append(current)
                current = []
        if current:
            episodes.append(current)  # still-open episode
    return episodes


def compute_style_profile(fills: list[dict], min_fills: int = 15) -> dict:
    """Style traits that distinguish HOW a trader trades — the dimensions a human
    reads as 'trading style'. Added after a false positive where a matched wallet
    had strong asset/timing overlap but an obviously different style."""
    profile = {"sufficient_data": len(fills) >= min_fills}
    if not fills:
        return profile

    episodes = _position_episodes(fills)

    # --- Activity: decision frequency is the most discriminative style trait.
    # Episodes (position round-trips) rather than raw fills — TWAP execution
    # inflates fill counts ~10x for the same human depending on the period.
    #
    # Normalised per ACTIVE day, not per calendar day. Dividing by the calendar
    # span measures how often the trader shows up, which is regime, not identity —
    # and `active_days_ratio` below already measures exactly that. Dividing both
    # counts by the span too meant intermittency was counted three times, once of
    # them as a hard veto, and on 2026-08-05 that rejected the target as an
    # impostor against his own history: 12 active days packed into 19 calendar
    # days scored 0.94 episodes/day, the same 12 active days spread over 72 scored
    # 0.17, and "Decision frequency 6x apart" failed the self-match, dropping the
    # scanner into OBSERVING and switching behavioural alerting off in production.
    #
    # The keys are deliberately renamed: a fingerprint written before this change
    # holds calendar-normalised values, and comparing those against per-active-day
    # ones would be worse than not comparing at all. Consumers see the old key as
    # absent and drop the dimension until the fingerprint is rebuilt.
    timestamps = sorted(f.get("time", 0) for f in fills if f.get("time"))
    if timestamps:
        span_days = max(1.0, (timestamps[-1] - timestamps[0]) / 86_400_000)
        active_days = len({ts // 86_400_000 for ts in timestamps})
        profile["activity"] = {
            "episodes_per_active_day": round(len(episodes) / max(1, active_days), 3),
            "fills_per_active_day": round(len(fills) / max(1, active_days), 2),
            # Presence, measured once and on its own. Clamped: a position opened
            # near midnight closes on the next calendar day, which could push the
            # ratio above 1.0 and distort the 1.0 - |difference| comparison.
            "active_days_ratio": round(
                min(1.0, active_days / max(1, int(span_days) + 1)), 4),
            "active_days": active_days,
            "span_days": round(span_days, 1),
        }

    # --- Direction bias: long vs short opens (flips count as opening the new side)
    long_opens = sum(1 for f in fills if f.get("dir") in ("Open Long", "Short > Long"))
    short_opens = sum(1 for f in fills if f.get("dir") in ("Open Short", "Long > Short"))
    total_opens = long_opens + short_opens
    if total_opens:
        profile["direction"] = {
            "long_open_pct": round(long_opens / total_opens, 4),
            "short_open_pct": round(short_opens / total_opens, 4),
            "total_opens": total_opens,
        }

    # --- Position management: scaling in/out habits via episode reconstruction
    if episodes:
        fills_per_ep = [len(ep) for ep in episodes]
        arr = np.array(fills_per_ep)
        profile["position_management"] = {
            "episodes": len(episodes),
            "mean_fills_per_episode": round(float(np.mean(arr)), 2),
            "median_fills_per_episode": round(float(np.median(arr)), 1),
        }

    # --- Loss handling: does the trader cut losers fast or hold them?
    winner_holds, loser_holds, wins, losses = [], [], [], []
    for ep in episodes:
        start = ep[0].get("time", 0)
        for f in ep:
            pnl = float(f.get("closedPnl", 0) or 0)
            if pnl == 0:
                continue
            hold_min = (f.get("time", 0) - start) / 60000
            if pnl > 0:
                wins.append(pnl)
                winner_holds.append(hold_min)
            else:
                losses.append(-pnl)
                loser_holds.append(hold_min)
    if wins or losses:
        lh = {
            "closed_wins": len(wins),
            "closed_losses": len(losses),
        }
        if winner_holds:
            lh["median_winner_hold_min"] = round(float(np.median(winner_holds)), 1)
        if loser_holds:
            lh["median_loser_hold_min"] = round(float(np.median(loser_holds)), 1)
        if winner_holds and loser_holds:
            med_w = max(0.1, float(np.median(winner_holds)))
            lh["loser_to_winner_hold_ratio"] = round(float(np.median(loser_holds)) / med_w, 3)
        if wins and losses:
            lh["win_loss_magnitude_ratio"] = round(
                float(np.mean(wins)) / max(1e-9, float(np.mean(losses))), 3)
        profile["loss_handling"] = lh

    # --- Execution habits: TWAP usage and perp/spot mix
    twap_fills = sum(1 for f in fills if f.get("twapId"))
    spot_fills = sum(1 for f in fills if f.get("dir") in ("Buy", "Sell"))
    profile["execution"] = {
        "twap_ratio": round(twap_fills / len(fills), 4),
        "spot_fill_ratio": round(spot_fills / len(fills), 4),
    }

    # --- Clip sizes: account-size-invariant order size habits
    notionals = [float(f.get("sz", 0)) * float(f.get("px", 0)) for f in fills]
    notionals = [n for n in notionals if n > 0]
    if notionals:
        arr = np.array(notionals)
        mean_n = float(np.mean(arr))
        round_pref = sum(
            1 for f in fills
            if len(str(f.get("sz", "")).rstrip("0").rstrip(".").replace(".", "").lstrip("0")) <= 2
        ) / len(fills)
        profile["clip_sizes"] = {
            "notional_cv": round(float(np.std(arr)) / max(1e-9, mean_n), 4),
            "round_size_pref": round(round_pref, 4),
        }

    return profile


def build_fingerprint(fills: list[dict] | None = None) -> dict:
    """Build the complete behavioral fingerprint."""
    if fills is None:
        fills = load_fills()
    funding = load_funding()
    positions = load_positions_latest()

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
            "first_fill": datetime.fromtimestamp(first / 1000, tz=UTC).isoformat(),
            "last_fill": datetime.fromtimestamp(last / 1000, tz=UTC).isoformat(),
            "total_fills": len(fills),
            "total_days_active": max(1, (last - first) // (24 * 60 * 60 * 1000)),
        }

    fingerprint = {
        "version": "1.0",
        "computed_at": datetime.now(UTC).isoformat(),
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
        "style_profile": compute_style_profile(fills),
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
        "computed_at": datetime.now(UTC).isoformat(),
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
        "style_profile": compute_style_profile(recent),
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
        today = datetime.now(UTC).strftime("%Y-%m-%d")
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

    # Validate the scorer against Ezekiel's own history (non-fatal on failure)
    try:
        from src.backtest import run_backtest
        run_backtest()
    except Exception as e:
        print(f"[fingerprint] Backtest failed to run: {e}")


if __name__ == "__main__":
    main()
