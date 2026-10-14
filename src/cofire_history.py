"""Co-firing over his WHOLE stored history, not just the live tape.

`cofire.py` reads who opens his bursts with him on each live tape window, and
on 2026-10-14 it had seen none of his bursts: he trades in sessions days apart
and the tape has run for a day. Meanwhile this project holds every fill of his
since 2026-02-05 (195,093 fills, 92,915 taker orders, 296 bursts), and SQD's
portal (`fills_feed.py`) serves every fill on any coin for any block range back
to 2025-10-02, keyless. So the one vector that needs no link between two
wallets — a second account worked in the same seconds, often BEFORE the one we
watch — can be read over eight months of his sessions today, with:

* **positive controls** — his known wallets (`config.known_self_wallets`) are
  scored like strangers against the target alone. If his own second wallet
  co-fires with him, the method is validated on him; if it does not, that is
  written down as "his wallets are not worked together", a bound on the
  vector's reach rather than a failure of it;
* **a base rate** — over every wallet with enough events, the share that
  clears the hit bar and the distribution of lift and lead share. That is the
  number the casebook's `cofire` likelihood has been "assumed" for.

Sessions, not bursts: his orders are clustered by a 60 s gap across ALL coins,
and each session is read with `PAD_MS` of tape either side, filtered to the
coins he traded in it. The padding is what makes `expected` a base rate —
inside a dense burst every taker co-fires by construction; the window must hold
off-burst time for the same wallets. The fold is `cofire.fold_window` unchanged,
so the history and the live tape read with one rule.

Pure functions; `scripts/census_cofire_history.py` reads and writes.
"""

from __future__ import annotations

import math
from collections import defaultdict

from src import cofire

SESSION_GAP_MS = cofire.BURST_GAP_MS
PAD_MS = 15 * 60_000
# A session longer than this is read in slices, each padded; the fold sees each
# slice as a window of its own.
MAX_SLICE_MS = 60 * 60_000
MIN_RATE_EVENTS = cofire.MIN_EVENTS


def his_orders(fills: list[dict], target: str) -> list[dict]:
    """His taker orders from stored fills (`crossed` fills grouped by hash)."""
    target = str(target).lower()
    groups: dict = {}
    for fill in fills or []:
        if not isinstance(fill, dict) or not fill.get("crossed"):
            continue
        user = str(fill.get("user") or target).lower()
        if user != target:
            continue
        try:
            t, sz = int(fill.get("time")), float(fill.get("sz"))
        except (TypeError, ValueError):
            continue
        coin = fill.get("coin")
        if not coin or sz <= 0:
            continue
        key = (fill.get("hash"), coin)
        order = groups.setdefault(key, {"wallet": target, "coin": str(coin), "side": fill.get("side"),
                                        "base_size": 0.0, "t": t, "taker": True})
        order["base_size"] += sz
        order["t"] = min(order["t"], t)
    return sorted(groups.values(), key=lambda o: o["t"])


def sessions(orders: list[dict], gap_ms: int = SESSION_GAP_MS) -> list[dict]:
    """His orders clustered by `gap_ms` across all coins, oldest first.

    Each session: {key, start, end, coins, orders}. The key is the start time
    and the coin set, so a session re-derived after a fills re-dedupe keeps its
    identity and is never read twice.
    """
    out: list[dict] = []
    current: list[dict] = []
    for order in sorted(orders, key=lambda o: o["t"]):
        if current and order["t"] - current[-1]["t"] > gap_ms:
            out.append(_session(current))
            current = []
        current.append(order)
    if current:
        out.append(_session(current))
    return out


def _session(orders: list[dict]) -> dict:
    coins = sorted({o["coin"] for o in orders})
    start, end = orders[0]["t"], orders[-1]["t"]
    return {"key": f"{start}:{','.join(coins)}", "start": start, "end": end,
            "coins": coins, "orders": len(orders)}


def slices(session: dict, pad_ms: int = PAD_MS, max_slice_ms: int = MAX_SLICE_MS) -> list[tuple[int, int]]:
    """[(from_ms, to_ms)] windows to read for a session, each padded."""
    start, end = int(session["start"]), int(session["end"])
    out = []
    cursor = start
    while True:
        stop = min(end, cursor + max_slice_ms)
        out.append((cursor - pad_ms, stop + pad_ms))
        if stop >= end:
            break
        cursor = stop + 1
    return out


def fold_session(state: dict, orders: list[dict], target: str, now_ms: int) -> dict:
    """Fold one read window into `state` with the target ALONE as "him", so his
    known wallets are measured as controls rather than folded into the cluster."""
    return cofire.fold_window(state, orders, {str(target).lower()}, now_ms)


def controls(state: dict, known: set) -> list[dict]:
    """How his known wallets read against the target: the positive controls."""
    out = []
    wallets = state.get("wallets") or {}
    for wallet in sorted({str(w).lower() for w in known}):
        entry = wallets.get(wallet)
        if entry is None:
            out.append({"wallet": wallet, "seen": False})
            continue
        out.append({"wallet": wallet, "seen": True, "events": int(entry.get("o") or 0),
                    "expected": round(float(entry.get("e") or 0.0), 2),
                    "lift": _round(cofire.lift(entry)), "z": _round(cofire.z_score(entry)),
                    "lead_share": _round(cofire.lead_share(entry), 3),
                    "bursts": len(entry.get("bs") or []), "hit": cofire.is_hit(entry)})
    return out


def _round(value, places: int = 2):
    return None if value is None else round(float(value), places)


def _deciles(values: list[float]) -> list[float] | None:
    if not values:
        return None
    values = sorted(values)
    n = len(values)
    return [round(values[min(n - 1, int(math.floor(q * (n - 1) + 0.5)))], 3)
            for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)]


def base_rate(state: dict, *, exclude: set | None = None, min_events: int = MIN_RATE_EVENTS) -> dict:
    """The population's co-firing against him: what a stranger looks like.

    Over wallets with at least `min_events` events (fewer cannot clear the bar
    anyway): how many clear `is_hit`, and the deciles of lift and lead share.
    `hit_rate` is the base rate a casebook likelihood needs; `None` when the
    population is too small to say anything (rule 5: unmeasured, not zero).
    """
    exclude = {str(w).lower() for w in (exclude or ())}
    lifts, leads, hits, n = [], [], 0, 0
    for wallet, entry in (state.get("wallets") or {}).items():
        if wallet in exclude or int(entry.get("o") or 0) < min_events:
            continue
        n += 1
        lift = cofire.lift(entry)
        lead = cofire.lead_share(entry)
        if lift is not None:
            lifts.append(lift)
        if lead is not None:
            leads.append(lead)
        if cofire.is_hit(entry):
            hits += 1
    return {"wallets": n, "hits": hits, "hit_rate": (hits / n) if n >= 20 else None,
            "min_events": min_events,
            "lift_deciles": _deciles(lifts), "lead_share_deciles": _deciles(leads),
            "deciles_at": [0.1, 0.25, 0.5, 0.75, 0.9, 0.99]}


def coverage(state: dict, read_keys: set, all_sessions: list[dict]) -> dict:
    """How much of his history the census has read."""
    read = [s for s in all_sessions if s["key"] in read_keys]
    return {"sessions": len(all_sessions), "sessions_read": len(read),
            "orders": sum(int(s["orders"]) for s in all_sessions),
            "orders_read": sum(int(s["orders"]) for s in read),
            "first_ms": all_sessions[0]["start"] if all_sessions else None,
            "last_ms": all_sessions[-1]["end"] if all_sessions else None,
            "his_orders_folded": int(state.get("his_orders") or 0)}


def by_coin(state: dict, hits: list[dict]) -> dict:
    """Which coins the hits co-fire on — the reader's first question."""
    counts: dict = defaultdict(int)
    for hit in hits:
        for coin in hit.get("coins") or []:
            counts[coin] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
