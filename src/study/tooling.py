"""The study's tooling tests (spec §7): how a wallet's orders are made. Pure.

T1 is his execution style and the habits around it; T2 the rhythm of his
slicer; T3 his per-coin clip table (`execution_program`). Tooling travels with
an operator to every account and is not what a copier reproduces, so it can
vote — once calibrated (calibration.py). Only T1 can count against a wallet:
his rhythm changed once (Feb–Mar) and his clip table drifts, so a mismatch on
T2 or T3 is not evidence he is elsewhere.
"""

from __future__ import annotations

from src import execution_program as ep
from src.study import records

STYLE_PROGRAM = "PROGRAM_IOC5"
STYLE_MAKER = "MAKER"
STYLE_MANUAL = "MANUAL_UI"
STYLE_MIXED = "MIXED"
MIN_ORDERS = 100
MIN_GAPS = 200
MIN_IOC_OFFSETS = 50  # IOC-dominant wallet's style decided only with ≥50 measured offsets
FLAG_SHARES = {"client_ids": 0.05, "triggers": 0.01, "maker": 0.10}
AGAINST_TRAITS = ("client_ids", "triggers", "maker")
NEVER_SHARE = 0.001
DOMINANT_SHARE = 0.5
SHARE_KEYS = ("ioc", "gtc", "alo", "frontend", "client_ids", "triggers", "maker", "canceled", "taker")


def _empty_coin() -> dict:
    return {"orders": 0, "buy_usd": 0.0, "sell_usd": 0.0, "taker_clips": {},
            "px_sum": 0.0, "px_n": 0}


def summarise(days: list[dict]) -> dict:
    """One window of daily records summed: habits, rhythm, coins and runs."""
    out = {"days": len(days), "covered_days": 0, "orders": 0, "taker_orders": 0,
           "program_runs": 0, "habits": None, "cadence": [0] * records.CADENCE_BINS,
           "coins": {}}
    for record in days:
        out["covered_days"] += records.covered_day(record)
        out["orders"] += record.get("orders", 0)
        out["taker_orders"] += record.get("taker_orders", 0)
        out["program_runs"] += record.get("program_runs", 0)
        if record.get("habits"):
            out["habits"] = records.add_habits(out["habits"], record["habits"])
        out["cadence"] = [a + b for a, b in zip(out["cadence"], record["cadence"], strict=True)]
        for coin, stats in (record.get("coins") or {}).items():
            if coin == records.OTHER:
                continue
            agg = out["coins"].setdefault(coin, _empty_coin())
            for key in ("orders", "buy_usd", "sell_usd", "px_sum", "px_n"):
                agg[key] += stats.get(key, 0)
            for size, n in (stats.get("taker_clips") or {}).items():
                agg["taker_clips"][size] = agg["taker_clips"].get(size, 0) + n
    return out


def profile(habits: dict | None, taker_orders: int = 0, orders: int = 0) -> dict | None:
    """Shares of each habit; None when no order was read (rule 6)."""
    if not habits or not habits.get("orders_seen"):
        return None
    n = habits["orders_seen"]
    tif = habits["tif"]
    seen = habits.get("ioc_offset_seen") or 0
    return {"orders_seen": n, "ioc": tif.get("Ioc", 0) / n, "gtc": tif.get("Gtc", 0) / n,
            "alo": tif.get("Alo", 0) / n, "frontend": tif.get("FrontendMarket", 0) / n,
            "client_ids": habits.get("cloid", 0) / n, "triggers": habits.get("trigger", 0) / n,
            "reduce_only": habits.get("reduce_only", 0) / n,
            "canceled": habits.get("canceled", 0) / n,
            "maker": (tif.get("Alo", 0) + tif.get("Gtc", 0)) / n,
            "ioc5": habits.get("ioc_offset_5pct", 0) / seen if seen else None,
            "taker": taker_orders / orders if orders else None,
            "ioc_offsets_seen": habits.get("ioc_offset_seen") or 0}


def summary_profile(summary: dict) -> dict | None:
    return profile(summary.get("habits"), summary.get("taker_orders", 0), summary.get("orders", 0))


def style(prof: dict) -> dict | None:
    # Undecidable: IOC-dominant but offsets not measured
    if prof["ioc"] >= DOMINANT_SHARE and prof.get("ioc_offsets_seen", 0) < MIN_IOC_OFFSETS:
        return None
    ioc5 = prof["ioc"] * prof["ioc5"] if prof.get("ioc5") is not None else None
    if ioc5 is not None and ioc5 >= 0.5:
        kind = STYLE_PROGRAM
    elif prof["maker"] >= 0.5:
        kind = STYLE_MAKER
    elif prof["frontend"] >= 0.5:
        kind = STYLE_MANUAL
    else:
        kind = STYLE_MIXED
    return {"style": kind, "flags": {k: prof[k] >= v for k, v in FLAG_SHARES.items()}}


def _test(name: str, status: str, statistic=None, n: int = 0, detail: dict | None = None) -> dict:
    return {"test": name, "status": status, "statistic": statistic, "n": n, "detail": detail or {}}


def t1_style(candidate: dict | None, his_recent: dict | None, his_all: dict | None) -> dict:
    """Same style and flags as his recent ones? And which traits he never shows
    (under 0.1% of his recorded orders) dominate the candidate?"""
    n = (candidate or {}).get("orders_seen", 0)
    if not candidate or n < MIN_ORDERS:
        return _test("T1", "insufficient", n=n, detail={"short": "candidate"})
    if not his_recent or his_recent["orders_seen"] < MIN_ORDERS:
        return _test("T1", "insufficient", n=n, detail={"short": "his"})
    mine, his = style(candidate), style(his_recent)
    if mine is None or his is None:
        return _test("T1", "insufficient", n=n, detail={"short": "candidate" if mine is None else "his"})
    # Require 1000 orders in his_all to say "he never does this"
    against = []
    if his_all and his_all.get("orders_seen", 0) >= round(1 / NEVER_SHARE):
        against = [t for t in AGAINST_TRAITS
                   if his_all[t] < NEVER_SHARE and candidate[t] > DOMINANT_SHARE]
    return _test("T1", "measured", mine == his, n, {
        "candidate": mine, "his": his, "against_traits": against,
        "shares": {k: (round(candidate[k], 4) if candidate[k] is not None else None) for k in SHARE_KEYS},
        "his_shares": {k: (round(his_recent[k], 4) if his_recent[k] is not None else None) for k in SHARE_KEYS}})


def wasserstein(a: list[int], b: list[int]) -> float | None:
    """Earth-mover distance between two gap histograms, in seconds."""
    sa, sb = sum(a or []), sum(b or [])
    if not sa or not sb:
        return None
    ca = cb = total = 0.0
    for x, y in zip(a, b, strict=True):
        ca += x / sa
        cb += y / sb
        total += abs(ca - cb)
    return round(total * records.CADENCE_BIN_MS / 1000, 4)


def t2_rhythm(candidate_hist: list[int], his_hist: list[int]) -> dict:
    n = sum(candidate_hist or [])
    if n < MIN_GAPS or sum(his_hist or []) < MIN_GAPS:
        return _test("T2", "insufficient", n=n)
    return _test("T2", "measured", wasserstein(candidate_hist, his_hist), n)


def clip_signature(summary: dict, prof: dict | None = None) -> dict:
    """execution_program's signature shape, rebuilt from summed daily records with
    its own clip rules (MIN_CLIP_ORDERS, MIN_CLIP_SHARE)."""
    table, notionals = {}, {}
    for coin, stats in (summary.get("coins") or {}).items():
        if coin == records.OTHER:
            continue
        sizes = stats.get("taker_clips") or {}
        total = sum(sizes.values())
        if not total:
            continue
        key, count = max(sizes.items(), key=lambda kv: (kv[1], kv[0]))
        share = count / total
        if total >= ep.MIN_CLIP_ORDERS and share >= ep.MIN_CLIP_SHARE:
            size = float(key)
            table[coin] = {"size": size, "share": round(share, 4), "count": count}
            if stats.get("px_n"):
                notionals[coin] = size * stats["px_sum"] / stats["px_n"]
    return {"clip_table": table, "clip_notionals": notionals,
            "program_runs": summary.get("program_runs", 0),
            "ioc_5pct_share": (prof or {}).get("ioc5")}


def t3_clips(candidate_sig: dict, his_sig: dict) -> dict:
    match = ep.compare(his_sig or {}, candidate_sig or {})
    if match["status"] != "measured":
        return _test("T3", "insufficient", detail=match)
    return _test("T3", "measured", match["strength"],
                 max(match["clips_compared"], match["notional_coins_compared"]), match)


def measure_snapshot(fills: list, entries: list) -> dict:
    """A one-off reading of an account from its newest fills and orders: the same
    measures the daily records accumulate, for the stranger and family panels."""
    orders = ep.reconstruct_orders(fills)
    counts = records.habit_counts(entries, records.first_prices(fills))
    prof = profile(counts, sum(1 for o in orders if o["taker"]), len(orders))
    sig = ep.signature(fills, entries)
    hist = records.cadence_histogram(orders)
    measurable = prof is not None and prof["orders_seen"] >= MIN_ORDERS
    prof_style = style(prof) if measurable else None if prof else None
    return {"orders_seen": counts["orders_seen"],
            "shares": {k: (round(prof[k], 4) if prof[k] is not None else None) for k in SHARE_KEYS} if prof else None,
            "ioc5": prof.get("ioc5") if prof else None,
            "style": prof_style,
            "cadence": hist if sum(hist) else None,
            "clip_table": {c: v["size"] for c, v in sig["clip_table"].items()} or None,
            "clip_notionals": {c: round(v, 6) for c, v in sig["clip_notionals"].items()} or None,
            "program_runs": sig["program_runs"]}


def snapshot_signature(snapshot: dict | None) -> dict:
    """execution_program's signature shape rebuilt from a stored snapshot."""
    if not snapshot:
        return {"clip_table": {}, "clip_notionals": {}, "program_runs": None, "ioc_5pct_share": None}
    return {"clip_table": {c: {"size": s, "share": None, "count": None}
                           for c, s in (snapshot.get("clip_table") or {}).items()},
            "clip_notionals": dict(snapshot.get("clip_notionals") or {}),
            "program_runs": snapshot.get("program_runs"),
            "ioc_5pct_share": snapshot.get("ioc5")}
