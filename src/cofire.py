"""Who fires WITH him, to the second: co-firing on the venue-wide fill tape.

His program fires one clip every ~1.7 s in runs of tens to thousands. A second
account of his that splits the same decision — two sub-accounts worked by one
script, or one hand on two keys — fires on the same coin and side in the same
seconds, and often BEFORE the account we watch. A copy-trader cannot do that: it
sees his fill after the block and follows it, half a second to a few seconds
later. `comovement.py` reads this at decision resolution over 21 days, where he
makes too few decisions to test; the continuous fill feed (`fills_feed.py`)
lets it be read at fill resolution, every window, for every taker on his
markets, at no API cost.

Chance is corrected for: an active market maker on BTC trades inside any
5-second window, so each wallet's co-fires are compared with what its own order
rate on that coin and side would produce by accident during his bursts
(`expected`), and a hit needs a lift over that, a minimum number of events over
several distinct bursts of his, and a share of leads or ties — a wallet that only
ever follows is a copier, however tight.

Pure functions over the tape's order shape; the script folds, screens and
registers. Evidence, not a vote: hits become `tape_cofire` registry candidates
for the per-wallet detectors, and the casebook records them as context until the
lift's base rate is measured.
"""

from __future__ import annotations

import math
from collections import defaultdict

# A co-fire is an order on the same coin and side within this of one of his.
WINDOW_MS = 5_000
# Lead, tie and follow are judged ONCE PER BURST, on the wallet's earliest order
# against his first: inside a burst his clips are ~1.75 s apart, so a copier 1.5 s
# behind one clip is 0.25 s "ahead" of the next and the nearest-order view cannot
# tell it from a twin. Who opens the burst can. Earlier than this before his
# first order is a LEAD, later than this after it a FOLLOW, between them a TIE;
# a copier needs a block (~70 ms) plus its own round trip.
LEAD_MS = 300
# A burst of his is a run of orders on one coin with no gap longer than this.
BURST_GAP_MS = 60_000
MAX_WALLETS = 2_000
MAX_BURST_IDS = 40
# Hit bar: events over bursts, lift over chance, and not only following.
MIN_EVENTS = 10
MIN_BURSTS = 3
MIN_LIFT = 5.0
MIN_LEAD_SHARE = 0.25


def empty_state() -> dict:
    return {"wallets": {}, "windows": 0, "his_windows": 0, "his_orders": 0}


def _bursts(his_orders: list[dict], window_index: int) -> dict:
    """order id -> burst id, clustering his orders per coin by BURST_GAP_MS."""
    ids: dict = {}
    by_coin: dict = defaultdict(list)
    for order in his_orders:
        by_coin[order["coin"]].append(order)
    for coin, orders in by_coin.items():
        orders.sort(key=lambda o: o["t"])
        k = 0
        last_t = None
        for order in orders:
            if last_t is not None and order["t"] - last_t > BURST_GAP_MS:
                k += 1
            ids[id(order)] = f"{window_index}:{coin}:{k}"
            last_t = order["t"]
    return ids


def _coverage(times: list[int], span_ms: float) -> float:
    """Share of the window inside ±WINDOW_MS of at least one of his orders."""
    if not times or span_ms <= 0:
        return 0.0
    covered = 0.0
    start = end = None
    for t in sorted(times):
        lo, hi = t - WINDOW_MS, t + WINDOW_MS
        if start is None:
            start, end = lo, hi
        elif lo <= end:
            end = max(end, hi)
        else:
            covered += end - start
            start, end = lo, hi
    covered += end - start
    return min(1.0, covered / span_ms)


def fold_window(state: dict, orders: list[dict], cluster: set, now_ms: int) -> dict:
    """Fold one window's taker orders (his included) into `state`. Mutates it.

    Returns what the window contributed: his order count, events, and the
    wallets that co-fired.
    """
    cluster = {str(w).lower() for w in cluster}
    state["windows"] = int(state.get("windows") or 0) + 1
    his = [o for o in orders if o.get("wallet") in cluster]
    others = [o for o in orders if o.get("wallet") not in cluster]
    if not his or not others:
        return {"his_orders": len(his), "events": 0, "wallets": 0}
    state["his_windows"] = int(state.get("his_windows") or 0) + 1
    state["his_orders"] = int(state.get("his_orders") or 0) + len(his)
    window_index = state["windows"]
    bursts = _bursts(his, window_index)
    his_by_key: dict = defaultdict(list)
    for o in his:
        his_by_key[(o["coin"], o.get("side"))].append(o)
    for key in his_by_key:
        his_by_key[key].sort(key=lambda o: o["t"])
    all_t = [o["t"] for o in orders if o.get("t") is not None]
    span = max(1_000.0, float(max(all_t) - min(all_t))) if all_t else 1_000.0
    coverage = {key: _coverage([o["t"] for o in hs], span) for key, hs in his_by_key.items()}

    burst_first: dict = {}
    for o in his:
        b = bursts[id(o)]
        burst_first[b] = min(burst_first.get(b, o["t"]), o["t"])

    wallets = state.setdefault("wallets", {})
    events = 0
    touched: set = set()
    earliest: dict = defaultdict(dict)   # wallet -> burst -> its earliest order time
    for o in others:
        key = (o["coin"], o.get("side"))
        wallet = o["wallet"]
        hs = his_by_key.get(key)
        if not hs:
            continue
        entry = wallets.get(wallet)
        if entry is None:
            entry = wallets[wallet] = {"o": 0, "e": 0.0, "ld": 0, "ti": 0, "fo": 0,
                                      "bs": [], "c": {}, "f": int(now_ms), "l": int(now_ms)}
        entry["e"] += coverage[key]
        # Nearest of his orders on this coin and side.
        nearest = min(hs, key=lambda h: abs(h["t"] - o["t"]))
        if abs(o["t"] - nearest["t"]) > WINDOW_MS:
            continue
        events += 1
        touched.add(wallet)
        entry["o"] += 1
        entry["l"] = int(now_ms)
        entry["c"][o["coin"]] = int(entry["c"].get(o["coin"]) or 0) + 1
        burst = bursts[id(nearest)]
        earliest[wallet][burst] = min(earliest[wallet].get(burst, o["t"]), o["t"])
    for wallet, by_burst in earliest.items():
        entry = wallets[wallet]
        for burst, t in by_burst.items():
            if burst in entry["bs"]:
                continue
            entry["bs"].append(burst)
            if len(entry["bs"]) > MAX_BURST_IDS:
                entry["bs"] = entry["bs"][-MAX_BURST_IDS:]
            dt = t - burst_first[burst]
            if dt < -LEAD_MS:
                entry["ld"] += 1
            elif dt > LEAD_MS:
                entry["fo"] += 1
            else:
                entry["ti"] += 1
    return {"his_orders": len(his), "events": events, "wallets": len(touched)}


def lift(entry: dict) -> float | None:
    observed = int(entry.get("o") or 0)
    expected = float(entry.get("e") or 0.0)
    if observed <= 0:
        return None
    return observed / max(expected, 0.5)


def z_score(entry: dict) -> float | None:
    observed = int(entry.get("o") or 0)
    expected = float(entry.get("e") or 0.0)
    if observed <= 0:
        return None
    return (observed - expected) / math.sqrt(max(expected, 0.5))


def lead_share(entry: dict) -> float | None:
    """Of the bursts of his it joined, the share it opened before or with him."""
    bursts = int(entry.get("ld") or 0) + int(entry.get("ti") or 0) + int(entry.get("fo") or 0)
    if bursts <= 0:
        return None
    return (int(entry.get("ld") or 0) + int(entry.get("ti") or 0)) / bursts


def is_hit(entry: dict) -> bool:
    l_ = lift(entry)
    return (int(entry.get("o") or 0) >= MIN_EVENTS
            and len(entry.get("bs") or []) >= MIN_BURSTS
            and l_ is not None and l_ >= MIN_LIFT
            and (lead_share(entry) or 0.0) >= MIN_LEAD_SHARE)


def screen(state: dict, *, exclude: set | None = None, limit: int = 50) -> list[dict]:
    """Wallets whose co-firing with him beats chance, leads or ties included."""
    exclude = {str(w).lower() for w in (exclude or ())}
    hits = []
    for wallet, entry in (state.get("wallets") or {}).items():
        if wallet in exclude or not is_hit(entry):
            continue
        hits.append({"wallet": wallet, "events": int(entry["o"]),
                     "expected": round(float(entry["e"]), 2),
                     "lift": round(lift(entry) or 0.0, 2), "z": round(z_score(entry) or 0.0, 2),
                     "lead": int(entry["ld"]), "tie": int(entry["ti"]), "follow": int(entry["fo"]),
                     "lead_share": round(lead_share(entry) or 0.0, 3),
                     "bursts": len(entry.get("bs") or []),
                     "coins": sorted(entry.get("c") or {}),
                     "first_seen_ms": entry.get("f"), "last_seen_ms": entry.get("l")})
    hits.sort(key=lambda h: (-h["z"], -h["events"], h["wallet"]))
    return hits[:limit]


def cap(state: dict, limit: int = MAX_WALLETS) -> dict:
    """Evict the wallets with the fewest events beyond `limit` (ties: oldest)."""
    wallets = state.get("wallets") or {}
    if len(wallets) <= limit:
        return state
    kept = sorted(wallets.items(),
                  key=lambda kv: (int(kv[1].get("o") or 0), int(kv[1].get("l") or 0), kv[0]))[-limit:]
    state["wallets"] = dict(sorted(kept))
    return state


def summary(state: dict, hits: list[dict]) -> dict:
    wallets = state.get("wallets") or {}
    return {"windows": int(state.get("windows") or 0),
            "his_windows": int(state.get("his_windows") or 0),
            "his_orders": int(state.get("his_orders") or 0),
            "wallets": len(wallets),
            "wallets_with_events": sum(1 for e in wallets.values() if int(e.get("o") or 0) > 0),
            "hits": len(hits)}
