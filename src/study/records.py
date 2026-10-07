"""Raw Hyperliquid reads folded into additive daily records. Pure; no I/O.

A record is the sum of every batch folded into it, so the caller folds only rows
inside [start_ms, end_ms) and moves its cursor to end_ms; a re-read overlapping
the last batch adds nothing. `quiet_boundary` puts end_ms inside a gap of at
least 30 s, so no run of slices is split across two folds (spec 2026-10-06 §6).
Anything not read stays unknown: coverage says what was read, and a field the
reads never covered is None, never 0 (rules 5 and 6).
"""

from __future__ import annotations

import base64
import math
from collections import Counter
from datetime import UTC, datetime
from statistics import median

from src import execution_program as ep

SCHEMA = "study-day/1"
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
MINUTES_PER_DAY = 1_440
COVERED_DAY_MS = 20 * HOUR_MS
RUN_GAP_MS = int(ep.PROGRAM_GAP_BREAK_S * 1000)
SESSION_GAP_MS = 30 * MINUTE_MS
QUIET_GAP_MS = 30_000
BOUNDARY_LAG_MS = 5 * MINUTE_MS
MIN_STORED_RUN = 10
CADENCE_BINS = 50
CADENCE_BIN_MS = 100
MAX_DECISIONS = 100
MAX_RUNS = 100
MAX_COINS = 20
MAX_COIN_MINUTE_MAPS = 5
MAX_LEDGER = 50
OTHER = "_other"
# A coin's `taker_clips` maps each taker order size to a count, so a bot quoting random sizes
# grows it without limit (1,045 distinct BTC sizes in one day: a 42 KB day file against the
# 15 KB the spec budgets). Each fold keeps the MAX_CLIP_SIZES commonest sizes and counts the
# rest under OTHER_SIZE (a key of that map, not the coin-level OTHER). `ep.clip_table` reads only
# the dominant size's count and the coin's total, both of which stay exact.
MAX_CLIP_SIZES = 20
OTHER_SIZE = "_other"
TIFS = ("Ioc", "Gtc", "Alo", "FrontendMarket")


def num(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def day_start_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def encode_minutes(minutes) -> str:
    bits = bytearray(MINUTES_PER_DAY // 8)
    for minute in minutes:
        if 0 <= minute < MINUTES_PER_DAY:
            bits[minute // 8] |= 1 << (minute % 8)
    return base64.b64encode(bytes(bits)).decode("ascii")


def decode_minutes(text) -> set[int]:
    if not text:
        return set()
    raw = base64.b64decode(text)
    return {i * 8 + bit for i, byte in enumerate(raw) for bit in range(8) if byte >> bit & 1}


def merge_intervals(intervals) -> list[list[int]]:
    merged: list[list[int]] = []
    for lo, hi in sorted((int(a), int(b)) for a, b in intervals if b > a):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def covered_ms(intervals, lo: int, hi: int) -> int:
    return sum(max(0, min(b, hi) - max(a, lo)) for a, b in intervals)


def covered_day(record: dict) -> bool:
    """A day counts as covered once at least 20 hours of its fills were read."""
    start = day_start_ms(record["day"])
    return covered_ms(record["coverage"]["fills"], start, start + DAY_MS) >= COVERED_DAY_MS


def split_by_day(lo: int, hi: int) -> list[tuple[str, int, int]]:
    pieces, t = [], lo
    while t < hi:
        end = min(hi, (t // DAY_MS + 1) * DAY_MS)
        pieces.append((day_of(t), t, end))
        t = end
    return pieces


def empty_day(wallet: str, day: str, role: str) -> dict:
    return {"schema": SCHEMA, "wallet": wallet, "day": day, "role": role,
            "coverage": {"fills": [], "orders": [], "ledger": [],
                         "saturated": False, "runs_split": False},
            "fills": 0, "orders": 0, "taker_orders": 0,
            "minutes": encode_minutes(()), "coin_minutes": {}, "manual_minutes": None,
            "decisions": [], "decisions_overflow": 0,
            "runs": [], "runs_overflow": 0, "program_runs": 0,
            "habits": None, "cadence": [0] * CADENCE_BINS, "coins": {}, "ledger": None,
            "ledger_overflow": 0}


def size_key(size: float) -> str:
    return format(size, ".10g")


def uncovered(intervals, lo: int, hi: int) -> list[tuple[int, int]]:
    """Sub-spans of [lo, hi) not already in intervals (merged first)."""
    merged = merge_intervals(intervals)
    gaps: list[tuple[int, int]] = []
    current = lo
    for a, b in merged:
        if a >= hi:
            break
        if current < a:
            gap = (max(current, lo), min(a, hi))
            if gap[0] < gap[1]:
                gaps.append(gap)
        current = max(current, b)
    if current < hi:
        gaps.append((current, hi))
    return gaps


# --- runs ---------------------------------------------------------------------------

def taker_runs(orders: list[dict]) -> list[list[dict]]:
    """Consecutive same-coin, same-side taker orders, broken by a gap over 30 s:
    `execution_program.program_runs`' definition, so both modules see one run."""
    runs: list[list[dict]] = []
    current: list[dict] = []
    for order in orders:
        if not order.get("taker"):
            continue
        if current and (order["coin"] != current[-1]["coin"]
                        or order["side"] != current[-1]["side"]
                        or order["t"] - current[-1]["t"] > RUN_GAP_MS):
            runs.append(current)
            current = []
        current.append(order)
    if current:
        runs.append(current)
    return runs


def run_summary(run: list[dict]) -> dict:
    gaps = sorted(b["t"] - a["t"] for a, b in zip(run, run[1:], strict=False))
    clip, count = Counter(o["base_size"] for o in run).most_common(1)[0]
    return {"coin": run[0]["coin"], "side": run[0]["side"],
            "start_ms": int(run[0]["t"]), "end_ms": int(run[-1]["t"]), "n": len(run),
            "clip": clip, "clip_share": round(count / len(run), 4),
            "gap_p10_ms": int(gaps[len(gaps) // 10]) if gaps else None,
            "gap_p50_ms": int(median(gaps)) if gaps else None}


def is_program_run(summary: dict) -> bool:
    """`execution_program.program_runs`' test applied to a stored run summary."""
    p50, p10 = summary.get("gap_p50_ms"), summary.get("gap_p10_ms")
    return (summary["n"] >= ep.MIN_PROGRAM_ORDERS
            and summary["clip_share"] >= ep.MIN_CLIP_SHARE
            and p50 is not None and ep.CADENCE_LOW_S * 1000 <= p50 <= ep.CADENCE_HIGH_S * 1000
            and p10 is not None and p10 >= ep.CADENCE_MIN_P10_S * 1000)


def cadence_add(hist: list[int], run: list[dict]) -> None:
    if len(run) < ep.MIN_PROGRAM_ORDERS:
        return
    for a, b in zip(run, run[1:], strict=False):
        hist[min(int((b["t"] - a["t"]) // CADENCE_BIN_MS), CADENCE_BINS - 1)] += 1


def cadence_histogram(orders: list[dict]) -> list[int]:
    """Gaps between slices inside runs of >= 15 orders, 100 ms bins from 0 to 5 s."""
    hist = [0] * CADENCE_BINS
    for run in taker_runs(orders):
        cadence_add(hist, run)
    return hist


# --- folding fills --------------------------------------------------------------------

def _add_decision(record: dict, decision: list) -> None:
    if decision in record["decisions"]:
        return
    if len(record["decisions"]) >= MAX_DECISIONS:
        record["decisions_overflow"] += 1
        return
    record["decisions"].append(decision)
    record["decisions"].sort(key=lambda d: (d[0], str(d[1]), str(d[2]), d[3]))


def _cap_coins(record: dict) -> None:
    coins = record["coins"]
    if len(coins) <= MAX_COINS:
        return
    other = coins.pop(OTHER, {"orders": 0})
    ranked = sorted(coins.items(), key=lambda kv: (-kv[1]["orders"], kv[0]))
    kept = dict(ranked[:MAX_COINS - 1])
    for _coin, stats in ranked[MAX_COINS - 1:]:
        other["orders"] += stats["orders"]
    kept[OTHER] = other
    record["coins"] = kept


def _bound_clip_sizes(record: dict) -> None:
    """Bound every coin's `taker_clips` to its MAX_CLIP_SIZES commonest sizes. The rest are
    added to OTHER_SIZE (not counted as a size, so the bound is idempotent), which keeps the
    map's total, the coin's taker order count, exact. Equal counts keep the smaller key."""
    for stats in record["coins"].values():
        clips = stats.get("taker_clips")  # the coin-level OTHER has none
        sizes = [(size, n) for size, n in (clips or {}).items() if size != OTHER_SIZE]
        if len(sizes) <= MAX_CLIP_SIZES:
            continue
        sizes.sort(key=lambda item: (-item[1], item[0]))
        kept = dict(sizes[:MAX_CLIP_SIZES])
        kept[OTHER_SIZE] = clips.get(OTHER_SIZE, 0) + sum(n for _size, n in sizes[MAX_CLIP_SIZES:])
        stats["taker_clips"] = kept


def _add_coin(record: dict, order: dict) -> None:
    """Add an order's stats to a coin (does not cap; capping is done per fold)."""
    stats = record["coins"].setdefault(str(order["coin"]), {
        "orders": 0, "buy_usd": 0.0, "sell_usd": 0.0, "taker_clips": {},
        "px_sum": 0.0, "px_n": 0})
    stats["orders"] += 1
    side = "buy_usd" if order["side"] == "B" else "sell_usd"
    stats[side] = round(stats[side] + order["notional"], 2)
    if order["taker"]:
        key = size_key(order["base_size"])
        stats["taker_clips"][key] = stats["taker_clips"].get(key, 0) + 1
        stats["px_sum"] = round(stats["px_sum"] + order["first_px"], 8)
        stats["px_n"] += 1


def _merge_coin_minutes(record: dict, batch: dict[str, set]) -> None:
    current = {coin: decode_minutes(text) for coin, text in record["coin_minutes"].items()}
    for coin, minutes in batch.items():
        current[coin] = current.get(coin, set()) | minutes
    other = current.pop(OTHER, set())
    ranked = sorted(current.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    kept = dict(ranked[:MAX_COIN_MINUTE_MAPS])
    for _coin, minutes in ranked[MAX_COIN_MINUTE_MAPS:]:
        other |= minutes
    if other:
        kept[OTHER] = other
    record["coin_minutes"] = {coin: encode_minutes(minutes) for coin, minutes in kept.items()}


def fold_fills(days: dict, fills: list, *, wallet: str, role: str, start_ms: int,
               end_ms: int, last_fill_ms: int | None, saturated: bool = False,
               runs_split: bool = False) -> int | None:
    """Fold the fills with start_ms <= time < end_ms into `days` (mutated).

    Returns the time of the last order folded (or `last_fill_ms` if none), kept by
    the caller so the next batch can tell a session start. With no earlier fill
    known, an order is a session start only if the read itself covered the 30
    minutes before it: silence is never assumed (rule 5).
    """
    if end_ms <= start_ms:
        return last_fill_ms
    rows, seen = [], set()
    for fill in fills or []:
        t = fill.get("time") if isinstance(fill, dict) else None
        if not isinstance(t, (int, float)) or not start_ms <= t < end_ms:
            continue
        key = fill["tid"] if fill.get("tid") is not None else (
            fill.get("oid"), t, fill.get("px"), fill.get("sz"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(fill)
    day_to_uncovered: dict[str, list[tuple[int, int]]] = {}
    for day, lo, hi in split_by_day(start_ms, end_ms):
        record = days.setdefault(day, empty_day(wallet, day, role))
        coverage = record["coverage"]
        day_to_uncovered[day] = uncovered(coverage["fills"], lo, hi)
        coverage["fills"] = merge_intervals(coverage["fills"] + [[lo, hi]])
        if saturated and lo == start_ms:
            coverage["saturated"] = True
        if runs_split:
            coverage["runs_split"] = True
    uncovered_rows, minutes, coin_minutes = [], {}, {}
    for fill in rows:
        t = int(fill["time"])
        day = day_of(t)
        uncov = day_to_uncovered.get(day, [])
        if not any(lo <= t < hi for lo, hi in uncov):
            continue
        uncovered_rows.append(fill)
        minute = (t % DAY_MS) // MINUTE_MS
        days[day]["fills"] += 1
        minutes.setdefault(day, set()).add(minute)
        coin_minutes.setdefault(day, {}).setdefault(str(fill.get("coin")), set()).add(minute)
    for day, found in minutes.items():
        record = days[day]
        record["minutes"] = encode_minutes(decode_minutes(record["minutes"]) | found)
        _merge_coin_minutes(record, coin_minutes[day])
    orders = ep.reconstruct_orders(uncovered_rows)
    previous = last_fill_ms
    for order in orders:
        t = int(order["t"])
        record = days[day_of(t)]
        record["orders"] += 1
        record["taker_orders"] += bool(order["taker"])
        _add_coin(record, order)
        session = (t - previous > SESSION_GAP_MS) if previous is not None \
            else (t - start_ms >= SESSION_GAP_MS)
        if session:
            _add_decision(record, [t, order["coin"], order["side"], "session"])
        previous = t
    for day in {day_of(int(o["t"])) for o in orders}:
        _cap_coins(days[day])
        _bound_clip_sizes(days[day])
    for run in taker_runs(orders):
        summary = run_summary(run)
        record = days[day_of(summary["start_ms"])]
        cadence_add(record["cadence"], run)
        record["program_runs"] += is_program_run(summary)
        if summary["n"] >= MIN_STORED_RUN:
            if len(record["runs"]) < MAX_RUNS:
                record["runs"].append(summary)
            else:
                record["runs_overflow"] += 1
            _add_decision(record, [summary["start_ms"], summary["coin"], summary["side"], "run"])
    last_row_time = int(max(r.get("time") for r in rows)) if rows else None
    return last_row_time if last_row_time is not None else last_fill_ms


# --- folding orders -------------------------------------------------------------------

def status_kind(status) -> str:
    text = str(status or "")
    if text == "open":
        return "open"
    if text in ("filled", "triggered"):
        return "filled"
    if text.endswith("anceled"):          # canceled, marginCanceled, reduceOnlyCanceled, ...
        return "canceled"
    if text.endswith("Rejected"):         # badAloPxRejected, iocCancelRejected, ...
        return "rejected"
    return "other"


def is_trigger(order: dict) -> bool:
    return bool(order.get("isTrigger")) or str(order.get("orderType") or "").startswith(
        ("Stop", "Take Profit"))


def empty_habits() -> dict:
    return {"orders_seen": 0, "tif": {**dict.fromkeys(TIFS, 0), "other": 0}, "cloid": 0,
            "trigger": 0, "reduce_only": 0, "canceled": 0, "rejected": 0, "open": 0,
            "ioc_offset_seen": 0, "ioc_offset_5pct": 0}


def add_habits(a: dict | None, b: dict) -> dict:
    out = empty_habits() if a is None else {**a, "tif": dict(a["tif"])}
    for key, value in b.items():
        if key == "tif":
            for tif, n in value.items():
                out["tif"][tif] = out["tif"].get(tif, 0) + n
        else:
            out[key] = out.get(key, 0) + value
    return out


def first_prices(fills: list) -> dict[str, float]:
    """oid -> price of the order's earliest fill, the reference the SDK priced off."""
    prices: dict[str, float] = {}
    when: dict[str, float] = {}
    for fill in fills or []:
        if not isinstance(fill, dict) or fill.get("oid") is None:
            continue
        px, t = num(fill.get("px")), fill.get("time")
        oid = str(fill["oid"])
        if px and isinstance(t, (int, float)) and (oid not in when or t < when[oid]):
            prices[oid], when[oid] = px, t
    return prices


def habit_counts(entries: list, first_px: dict) -> dict:
    """Submission habits over historicalOrders entries. The IOC offset is measured only
    for orders whose first fill price is known."""
    counts = empty_habits()
    for entry in entries or []:
        order = entry.get("order") if isinstance(entry, dict) else None
        if not isinstance(order, dict):
            continue
        counts["orders_seen"] += 1
        tif = order.get("tif")
        counts["tif"][tif if tif in TIFS else "other"] += 1
        counts["cloid"] += bool(order.get("cloid"))
        counts["trigger"] += is_trigger(order)
        counts["reduce_only"] += bool(order.get("reduceOnly"))
        kind = status_kind(entry.get("status"))
        if kind in ("canceled", "rejected", "open"):
            counts[kind] += 1
        if tif == "Ioc":
            limit, ref = num(order.get("limitPx")), first_px.get(str(order.get("oid")))
            if limit and ref:
                counts["ioc_offset_seen"] += 1
                offset = (limit / ref - 1) if order.get("side") == "B" else (1 - limit / ref)
                if abs(offset - ep.SDK_SLIPPAGE) <= ep.SLIPPAGE_TOLERANCE:
                    counts["ioc_offset_5pct"] += 1
    return counts


def fold_orders(days: dict, entries: list, first_px: dict, *, wallet: str, role: str,
                start_ms: int, end_ms: int) -> None:
    """Fold historicalOrders entries PLACED in [start_ms, end_ms): habits, web-UI clicks."""
    if end_ms <= start_ms:
        return
    by_day: dict[str, list] = {}
    for entry in entries or []:
        order = entry.get("order") if isinstance(entry, dict) else None
        t = order.get("timestamp") if isinstance(order, dict) else None
        if isinstance(t, (int, float)) and start_ms <= t < end_ms:
            by_day.setdefault(day_of(int(t)), []).append(entry)
    day_to_uncovered: dict[str, list[tuple[int, int]]] = {}
    for day, lo, hi in split_by_day(start_ms, end_ms):
        record = days.setdefault(day, empty_day(wallet, day, role))
        day_to_uncovered[day] = uncovered(record["coverage"]["orders"], lo, hi)
        record["coverage"]["orders"] = merge_intervals(record["coverage"]["orders"] + [[lo, hi]])
        uncov = day_to_uncovered[day]
        placed = [e for e in by_day.get(day, [])
                  if any(lo <= int(e.get("order", {}).get("timestamp", 0)) < hi
                         for lo, hi in uncov)]
        record["habits"] = add_habits(record["habits"], habit_counts(placed, first_px))
        manual = decode_minutes(record["manual_minutes"])
        for entry in placed:
            order = entry["order"]
            if order.get("tif") == "FrontendMarket":
                t = int(order["timestamp"])
                manual.add((t % DAY_MS) // MINUTE_MS)
                _add_decision(record, [t, order.get("coin"), order.get("side"), "manual"])
        record["manual_minutes"] = encode_minutes(manual)


# --- folding the ledger --------------------------------------------------------------

def ledger_usd(delta: dict) -> float | None:
    for key in ("usdcValue", "usdc", "usd"):
        value = num(delta.get(key))
        if value is not None:
            return abs(value)
    return None


def fold_ledger(days: dict, rows: list, *, wallet: str, role: str, start_ms: int,
                end_ms: int) -> None:
    """Fold non-funding ledger rows (deposits, withdrawals, sends) in [start_ms, end_ms)."""
    if end_ms <= start_ms:
        return
    picked: dict[str, list] = {}
    for row in rows or []:
        t = row.get("time") if isinstance(row, dict) else None
        delta = row.get("delta") if isinstance(row, dict) else None
        if isinstance(t, (int, float)) and start_ms <= t < end_ms and isinstance(delta, dict):
            picked.setdefault(day_of(int(t)), []).append(
                {"ts_ms": int(t), "type": delta.get("type"), "usd": ledger_usd(delta),
                 "hash": row.get("hash")})
    day_to_uncovered: dict[str, list[tuple[int, int]]] = {}
    for day, lo, hi in split_by_day(start_ms, end_ms):
        record = days.setdefault(day, empty_day(wallet, day, role))
        day_to_uncovered[day] = uncovered(record["coverage"]["ledger"], lo, hi)
        record["coverage"]["ledger"] = merge_intervals(record["coverage"]["ledger"] + [[lo, hi]])
        uncov = day_to_uncovered[day]
        kept = list(record["ledger"] or [])
        keys = {(r["ts_ms"], r["type"], r.get("hash")) for r in kept}
        for item in picked.get(day, []):
            if not any(lo <= item["ts_ms"] < hi for lo, hi in uncov):
                continue
            key = (item["ts_ms"], item["type"], item["hash"])
            if key not in keys:
                kept.append(item)
                keys.add(key)
        if len(kept) > MAX_LEDGER:
            def rank_ledger(r):
                usd = num(r.get("usd"))
                return (usd is None, -(usd or 0.0), r["ts_ms"])
            sorted_kept = sorted(kept, key=rank_ledger)
            kept = sorted_kept[:MAX_LEDGER]
            record["ledger_overflow"] += len(sorted_kept) - MAX_LEDGER
        record["ledger"] = sorted(kept, key=lambda r: r["ts_ms"])


# --- the boundary -----------------------------------------------------------------------

def quiet_boundary(times, cursor_ms: int, known_until_ms: int) -> tuple[int, bool]:
    """Where this run may stop folding: (boundary_ms, runs_split).

    At most BOUNDARY_LAG_MS before everything known, and inside a gap of at least
    QUIET_GAP_MS so no run straddles it. A wallet with no such gap (a bot quoting
    every second) falls back to the last hour mark and says so.
    """
    cap = known_until_ms - BOUNDARY_LAG_MS
    if cap <= cursor_ms:
        return cursor_ms, False
    ts = sorted({int(t) for t in times
                 if isinstance(t, (int, float)) and cursor_ms <= t <= known_until_ms})
    before = [t for t in ts if t < cap]
    after = [t for t in ts if t >= cap]
    if not before or not after or after[0] - before[-1] >= QUIET_GAP_MS:
        return cap, False
    for i in range(len(before) - 1, -1, -1):
        prev = before[i - 1] if i > 0 else cursor_ms
        if before[i] - prev >= QUIET_GAP_MS:
            return before[i], False
    hour = cap // HOUR_MS * HOUR_MS
    return (hour, True) if hour > cursor_ms else (cursor_ms, False)
