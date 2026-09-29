"""The execution-program vector: the trader's slicer, recognised across wallets.

The target does not TWAP by hand. 53,630 of his 57,580 stored orders are taker
`Limit`/`Ioc` with no client order id and a limit price sitting exactly 5.0%
through the book — the hyperliquid-python-sdk `market_open` default
(`DEFAULT_SLIPPAGE = 0.05`). He fires them at one round base size per coin, every
~1.7s, in runs of tens to thousands, and closes the same way (`reduceOnly` false).

What travels to a new wallet is the CLIP TABLE — the exact base size per coin
(NEAR 250, ZEC 1, SILVER 20, BTC 0.1, HYPE 40, XRP 1000, LINK 100, PUMP 200000).
Anyone can send a 5% IOC; almost nobody reproduces his whole size table. Measured
2026-09-28: 0 of 248 large active accounts carried the full signature.

This is a BEHAVIOUR signal, so two rules bind it:

- **It corroborates; it never confirms alone (rule 9).** A shared bot or frontend
  is the confounder — the same reason a shared builder or agent-frontend is not
  ownership. It votes only alongside an independent financial or protocol vector,
  and only once its false-positive rate has been MEASURED against the population
  (`scripts/census_execution_program.py`), exactly as the fuzzy behavioural score
  waits on its backtest (rule 4). Until then it is recorded as context.
- **A missing measurement is None, never 0.0 (rules 5, 6).** A flat or unread
  wallet is unknown, not dissimilar; a comparison with too few shared coins is
  `insufficient_data`, not a low score.

Pure functions only; I/O lives in the check/census scripts.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import median

# A coin forms a "clip" only with enough orders at one dominant size. Below this
# a modal size is a coincidence, not a habit.
MIN_CLIP_ORDERS = 8
MIN_CLIP_SHARE = 0.80

# A program run: consecutive same-coin, same-side taker orders at a constant clip,
# paced like a script rather than clicked by hand.
MIN_PROGRAM_ORDERS = 15
PROGRAM_GAP_BREAK_S = 30.0
CADENCE_LOW_S = 1.2
CADENCE_HIGH_S = 3.0
CADENCE_MIN_P10_S = 0.6

# market_open's default slippage. His limit sits this far through the book.
SDK_SLIPPAGE = 0.05
SLIPPAGE_TOLERANCE = 0.004

# A comparison needs at least this many coins where BOTH sides carry a clip.
MIN_SHARED_CLIP_COINS = 2

# A vote needs the match measured over at least this many shared clips, on top of
# whatever the census says is rare. Two exact size matches can be luck; six of his
# idiosyncratic sizes (ZEC 1, SILVER 20, NEAR 250) reproduced together cannot.
MIN_VOTE_CLIPS = 3


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def reconstruct_orders(fills: list[dict]) -> list[dict]:
    """Group fills into their parent orders (one order = one `oid`).

    An order is `taker` only if every one of its fills crossed the book; one
    maker fill means it rested, which the slicer's IOC orders never do. Fills of
    one order share a coin and side, so those are taken from the first fill.
    """
    groups: dict = defaultdict(list)
    for row in fills or []:
        if not isinstance(row, dict) or row.get("side") not in ("A", "B"):
            continue
        ts, size, price = _num(row.get("time")), _num(row.get("sz")), _num(row.get("px"))
        if ts is None or size is None or price is None or size <= 0 or price <= 0:
            continue
        oid = row.get("oid")
        key = oid if oid is not None else ("h", row.get("tid"))
        groups[key].append({"t": ts, "sz": size, "px": price, "crossed": row.get("crossed"),
                            "coin": row.get("coin"), "side": row["side"]})
    orders = []
    for rows in groups.values():
        first = min(rows, key=lambda r: r["t"])
        crossed = [r["crossed"] for r in rows if isinstance(r["crossed"], bool)]
        orders.append({"t": first["t"], "coin": first["coin"], "side": first["side"],
                       "base_size": round(sum(r["sz"] for r in rows), 10),
                       "notional": sum(r["sz"] * r["px"] for r in rows),
                       "taker": bool(crossed) and all(crossed), "first_px": first["px"]})
    orders.sort(key=lambda o: o["t"])
    return orders


def clip_table(orders: list[dict]) -> dict:
    """The repeated base size per coin, where one size dominates enough orders."""
    by_coin: dict = defaultdict(Counter)
    for order in orders:
        if order.get("taker") and order.get("coin") and order.get("base_size"):
            by_coin[order["coin"]][order["base_size"]] += 1
    table = {}
    for coin, sizes in by_coin.items():
        total = sum(sizes.values())
        size, count = sizes.most_common(1)[0]
        share = count / total
        if total >= MIN_CLIP_ORDERS and share >= MIN_CLIP_SHARE:
            table[coin] = {"size": size, "share": round(share, 4), "count": count}
    return table


def program_runs(orders: list[dict]) -> list[dict]:
    """Runs of same-coin, same-side taker orders at a constant clip and cadence."""
    runs, current = [], []
    for order in orders:
        if not order.get("taker"):
            continue
        if current and (order["coin"] != current[-1]["coin"] or order["side"] != current[-1]["side"]
                        or order["t"] - current[-1]["t"] > PROGRAM_GAP_BREAK_S * 1000):
            runs.append(current)
            current = []
        current.append(order)
    if current:
        runs.append(current)
    programs = []
    for run in runs:
        if len(run) < MIN_PROGRAM_ORDERS:
            continue
        gaps = sorted((b["t"] - a["t"]) / 1000 for a, b in zip(run, run[1:], strict=False))
        if not gaps:
            continue
        gap_med = median(gaps)
        gap_p10 = gaps[len(gaps) // 10]
        sizes = Counter(o["base_size"] for o in run)
        size, count = sizes.most_common(1)[0]
        if (count / len(run) >= MIN_CLIP_SHARE and CADENCE_LOW_S <= gap_med <= CADENCE_HIGH_S
                and gap_p10 >= CADENCE_MIN_P10_S):
            programs.append({"coin": run[0]["coin"], "side": run[0]["side"], "orders": len(run),
                             "clip": size, "gap_med": round(gap_med, 3),
                             "notional": round(size * run[0]["first_px"])})
    return programs


def _order_index(orders):
    index = {}
    for record in orders or []:
        order = record.get("order") if isinstance(record, dict) else None
        order = order if isinstance(order, dict) else (record if isinstance(record, dict) else None)
        if isinstance(order, dict) and order.get("oid") is not None:
            index[str(order["oid"])] = order
    return index


def signature(fills: list[dict], orders: list[dict] | None = None) -> dict:
    """The trader's execution signature from fills, enriched by orders if given.

    Fills give the clip table, cadence and taker share. The 5% IOC offset, TIF
    and client-id habits need the submitted order, so they are None without it.
    """
    reconstructed = reconstruct_orders(fills)
    taker = [o["taker"] for o in reconstructed]
    sig = {
        "orders_observed": len(reconstructed),
        "taker_share": round(sum(taker) / len(taker), 4) if taker else None,
        "clip_table": clip_table(reconstructed),
        "program_runs": len(program_runs(reconstructed)),
        "ioc_5pct_share": None,
        "cloid_share": None,
        "ioc_share": None,
    }
    if orders:
        index = _order_index(orders)
        # First fill price per oid approximates the reference the SDK priced off.
        first_px: dict = {}
        for row in sorted((r for r in fills if isinstance(r, dict)), key=lambda r: r.get("tid") or 0):
            oid = str(row.get("oid"))
            if oid not in first_px and _num(row.get("px")):
                first_px[oid] = _num(row["px"])
        ioc = offset_hits = offset_seen = with_cloid = counted = 0
        for oid, order in index.items():
            counted += 1
            tif = str(order.get("tif") or "")
            if tif == "Ioc":
                ioc += 1
            if order.get("cloid"):
                with_cloid += 1
            limit = _num(order.get("limitPx"))
            ref = first_px.get(oid)
            if tif == "Ioc" and limit and ref:
                offset_seen += 1
                offset = (limit / ref - 1) if order.get("side") == "B" else (1 - limit / ref)
                if abs(offset - SDK_SLIPPAGE) <= SLIPPAGE_TOLERANCE:
                    offset_hits += 1
        if counted:
            sig["ioc_share"] = round(ioc / counted, 4)
            sig["cloid_share"] = round(with_cloid / counted, 4)
        if offset_seen:
            sig["ioc_5pct_share"] = round(offset_hits / offset_seen, 4)
    return sig


def compare(target: dict, candidate: dict) -> dict:
    """How far the candidate reproduces the target's execution program.

    The load-bearing feature is the clip table: of the coins where BOTH carry a
    clip, the fraction where the base size is identical. Too few shared coins is
    `insufficient_data`, never a low score.
    """
    result = {"status": "insufficient_data", "clip_match_ratio": None, "clips_matched": 0,
              "clips_compared": 0, "cadence_agreement": None, "offset_agreement": None,
              "strength": None, "promotable": False}
    t_table = target.get("clip_table") or {}
    c_table = candidate.get("clip_table") or {}
    shared = set(t_table) & set(c_table)
    if len(shared) < MIN_SHARED_CLIP_COINS:
        return result
    matched = sum(1 for coin in shared
                  if math.isclose(t_table[coin]["size"], c_table[coin]["size"], rel_tol=1e-9))
    ratio = matched / len(shared)
    result.update(status="measured", clips_compared=len(shared), clips_matched=matched,
                  clip_match_ratio=round(ratio, 4))
    # Both running programs at a scripted cadence is corroborating, not decisive.
    result["cadence_agreement"] = bool(target.get("program_runs") and candidate.get("program_runs"))
    if target.get("ioc_5pct_share") is not None and candidate.get("ioc_5pct_share") is not None:
        result["offset_agreement"] = bool(target["ioc_5pct_share"] >= 0.8
                                          and candidate["ioc_5pct_share"] >= 0.8)
    # Strength is the clip-table match, nudged by the corroborating habits. It is
    # an ordering aid, not a probability, and never promotes on its own.
    strength = ratio
    if result["cadence_agreement"]:
        strength = min(1.0, strength + 0.05)
    if result["offset_agreement"]:
        strength = min(1.0, strength + 0.05)
    result["strength"] = round(strength, 4)
    return result


def rank_matches(target: dict, candidates: list[dict]) -> list[dict]:
    """Compare each candidate's signature to the target's, best match first.

    `candidates` is [{"wallet", "signature", ...extra}]. Insufficient comparisons
    are dropped, not ranked as weak — a wallet we could not measure against him is
    unknown, not dissimilar (rule 5). Any extra keys on a candidate (e.g. an
    independent-vector flag) are carried through onto its row.
    """
    ranked = []
    for candidate in candidates:
        result = compare(target, candidate.get("signature") or {})
        if result["status"] != "measured":
            continue
        extra = {k: v for k, v in candidate.items() if k not in ("signature",)}
        ranked.append({**extra, **result})
    ranked.sort(key=lambda r: (-(r["strength"] or 0), -r["clips_matched"], str(r.get("wallet"))))
    return ranked


def is_discriminating(match: dict, census: dict | None) -> bool:
    """Whether a clip-table match is rare enough in the population to be evidence.

    Rule 4: a behavioural signal casts no vote until its false-positive rate has
    been MEASURED. Without a census this returns False however perfect the match,
    exactly as the fuzzy behavioural score waits on its backtest. With a census, a
    match must beat the population's 99th-percentile ratio AND rest on enough
    shared clips that a coincidence of common sizes cannot reach it.
    """
    if not census:
        return False
    ratio = match.get("clip_match_ratio")
    compared = match.get("clips_compared") or 0
    matched = match.get("clips_matched") or 0
    if ratio is None:
        return False
    threshold = census.get("ratio_p99")
    min_clips = max(MIN_VOTE_CLIPS, int(census.get("min_clips") or 0))
    if threshold is None:
        return False
    return ratio > threshold and compared >= min_clips and matched >= MIN_VOTE_CLIPS


def summarise_census(ratios: list[float]) -> dict:
    """Distribution of stranger clip-match ratios, and the rarity threshold.

    `ratios` are the clip_match_ratio of every population wallet that had a
    MEASURED comparison against the target. The 99th percentile is the bar a
    candidate must beat to count as rare. An empty sample yields no threshold, so
    `is_discriminating` refuses every match until a real population exists.
    """
    clean = sorted(r for r in ratios if isinstance(r, (int, float)))
    if not clean:
        return {"population": 0, "ratio_p99": None, "ratio_max": None,
                "ratio_mean": None, "min_clips": MIN_VOTE_CLIPS}
    idx = min(len(clean) - 1, math.ceil(0.99 * len(clean)) - 1)
    return {"population": len(clean), "ratio_p99": round(clean[idx], 4),
            "ratio_max": round(clean[-1], 4), "ratio_mean": round(sum(clean) / len(clean), 4),
            "min_clips": MIN_VOTE_CLIPS}


def voting_wallets(report: dict | None) -> dict:
    """The wallets whose execution match is discriminating — the roster's vote.

    The check script has already applied the census gate per match
    (`is_discriminating`) and written the verdict, so the roster does not repeat
    the measurement; it reads which wallets earned the behaviour vote.
    """
    out = {}
    for match in (report or {}).get("matches", []):
        if match.get("discriminating"):
            addr = (match.get("wallet") or "").lower()
            if addr:
                out[addr] = match
    return out
