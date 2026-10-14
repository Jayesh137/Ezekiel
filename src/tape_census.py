"""The venue-wide execution census, read off the public tape for free.

Each `tape.yml` run streams his markets for ~150 s and sees several hundred
distinct takers (measured 2026-10-12: 461 in 90 s over 43 markets, 7-19 of them
on each of his `xyz:` markets). Until now everything but an exact-clip program
burst was discarded. This keeps a bounded per-wallet SKETCH — how many taker
orders at which base sizes on which coins, first and last seen — folded across
windows, and reads three things off it that no per-wallet API call could give:

* **A second execution census.** Of the wallets active enough on his coins to show
  a clip table, how many carry one comparable with his? That is the reference
  class the detector actually draws candidates from (takers of his markets), so
  its comparability ceiling is the one the vote gate must not undercut
  (`execution_program.merge_census` takes the larger ceiling).
* **A scale-invariant screen of everyone.** A sketch with clips on enough of his
  coins is compared with his table exactly as a stranger's fills would be, so a
  wallet that reproduces his sizes — or his per-coin size RANKING after a rescale —
  becomes an investigation candidate without ever having been on a leaderboard or
  a roster. The vote still comes only from the census-gated detector.
* **Newcomers born in his silence.** A wallet first seen on his markets in size
  while he is unusually quiet (judged against his own rhythm, `dormancy.py`) is
  the dormancy-handoff shape. The script reads its birth from `portfolio` and,
  if the first activity lands inside the silence, alerts exactly as the dormancy
  detector would — the detector's own candidate list is roster-ranked and would
  reach a wallet like this late or never.

Bounded on purpose: the sketch file is committed every run, so wallets are capped
and the oldest-seen evicted (a wallet with a clip on two of his coins is kept
longest), sizes per coin and coins per wallet are capped, and "first seen" means
first seen within the sketch's memory — the handoff judgement is made on the
wallet's real birth, never on the tape's.

Pure functions; the script (`scripts/collect_tape.py`) streams, reads and alerts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

from src import execution_program as ep

# Measured 2026-10-12: ~170 bytes a wallet compact, 700 wallets a 120 s window, so
# 3,000 is ~0.5 MB rewritten per run and holds roughly a day of takers.
MAX_WALLETS = 3000
MAX_COINS_PER_WALLET = 24
MAX_SIZES_PER_COIN = 6
# A wallet is JUDGEABLE once it has shown enough taker orders on at least two of
# his clip coins to carry a comparable clip table if it ran one. Fewer orders is
# unknown, not a non-match (rule 5), and a wallet active on only one of his coins
# could never be comparable, so it does not belong in the denominator either.
MIN_JUDGEABLE_COINS = ep.MIN_SHARED_CLIP_COINS
# A market is RARE when the sketch has seen at most this many distinct takers on
# it, OR at most RARE_MARKET_FRACTION of the takers of his busiest market. The
# fixed bar was calibrated on 150 s samples (his `xyz:` markets measured 7-19
# takers per 90 s window); under the continuous fill feed (2026-10-14) a day of
# sketch memory holds hundreds of takers on `xyz:MU` against ~1,100 on BTC, so
# rarity is read relative to the population the sketch actually holds.
RARE_MARKET_TAKERS = 40
RARE_MARKET_FRACTION = 0.10
# A newcomer matters only in size: gas-level traders on a rare market are retail.
NEWCOMER_MIN_NOTIONAL_USD = 25_000
# Candidate-registry bars for a sketch that reproduces his program.
HIT_MIN_RATIO = ep.BOUNDED_MIN_RATIO
HIT_MIN_CLIPS = ep.MIN_VOTE_CLIPS
HIT_MIN_RHO = ep.BOUNDED_MIN_RHO
HIT_MIN_STRUCTURE_COINS = ep.BOUNDED_MIN_STRUCTURE_COINS


def _size_key(size: float) -> str:
    return f"{float(size):.10g}"


def empty_sketches() -> dict:
    return {"wallets": {}, "prices": {}, "windows": 0}


def fold_window(sketches: dict, orders: list[dict], now_ms: int) -> dict:
    """Fold one window's taker orders (`tape.taker_orders`) into the sketches.

    Returns what the window contributed: the wallets first seen in it, each
    wallet's notional this window, and the markets traded. Mutates `sketches`.
    """
    wallets = sketches.setdefault("wallets", {})
    prices = sketches.setdefault("prices", {})
    sketches["windows"] = int(sketches.get("windows") or 0) + 1
    window_px: dict = defaultdict(list)
    notional: Counter = Counter()
    markets: dict = defaultdict(set)
    first_seen: list[str] = []
    for order in orders:
        wallet = (order.get("wallet") or "").lower()
        coin = order.get("coin")
        size, px = order.get("base_size"), order.get("first_px")
        if not wallet or not coin or not size or not px:
            continue
        entry = wallets.get(wallet)
        if entry is None:
            entry = wallets[wallet] = {"f": int(now_ms), "l": int(now_ms), "w": 0, "c": {}}
            first_seen.append(wallet)
        entry["l"] = int(now_ms)
        coins = entry["c"]
        if coin not in coins and len(coins) >= MAX_COINS_PER_WALLET:
            # Keep the coins with the most orders: a clip forms where he fires most.
            weakest = min(coins, key=lambda c: coins[c]["n"])
            if coins[weakest]["n"] > 1:
                continue
            del coins[weakest]
        slot = coins.setdefault(coin, {"n": 0, "s": {}})
        slot["n"] += 1
        key = _size_key(size)
        sizes = slot["s"]
        if key not in sizes and len(sizes) >= MAX_SIZES_PER_COIN:
            # Evict the least common size. A program's constant clip dominates the
            # counter within a handful of orders, so it is never the one evicted.
            rarest = min(sizes, key=lambda k: sizes[k])
            if sizes[rarest] > 1:
                continue
            del sizes[rarest]
        sizes[key] = sizes.get(key, 0) + 1
        window_px[coin].append(float(px))
        notional[wallet] += float(size) * float(px)
        markets[wallet].add(coin)
    touched = {w for w in markets}
    for wallet in touched:
        wallets[wallet]["w"] = int(wallets[wallet].get("w") or 0) + 1
    for coin, pxs in window_px.items():
        prices[coin] = round(median(pxs), 10)
    return {"first_seen": first_seen, "notional": dict(notional),
            "markets": {w: sorted(m) for w, m in markets.items()}, "touched": len(touched)}


def market_counts(sketches: dict) -> dict:
    """Distinct sketch wallets seen on each market."""
    counts: Counter = Counter()
    for entry in (sketches.get("wallets") or {}).values():
        for coin in (entry.get("c") or {}):
            counts[coin] += 1
    return dict(counts)


def rare_bar(counts: dict, target_coins: set) -> int:
    """The taker count at or below which one of his markets is rare."""
    busiest = max((int(counts.get(m) or 0) for m in target_coins), default=0)
    return max(RARE_MARKET_TAKERS, int(busiest * RARE_MARKET_FRACTION))


def rare_markets(counts: dict, target_coins: set) -> list[str]:
    bar = rare_bar(counts, target_coins)
    return sorted(m for m in target_coins if int(counts.get(m) or 0) <= bar)


def clip_table(entry: dict) -> dict:
    """The sketch's clip table, by the execution vector's own bar."""
    table = {}
    for coin, slot in (entry.get("c") or {}).items():
        total = int(slot.get("n") or 0)
        sizes = slot.get("s") or {}
        if total < ep.MIN_CLIP_ORDERS or not sizes:
            continue
        key, count = max(sizes.items(), key=lambda kv: kv[1])
        share = count / total
        if share >= ep.MIN_CLIP_SHARE:
            table[coin] = {"size": float(key), "share": round(share, 4), "count": count}
    return table


def sketch_signature(entry: dict, prices: dict) -> dict:
    """A signature `execution_program.compare` accepts, built from the sketch.

    Orders, cadence and the 5% offset are unknown from a sketch (None, never 0):
    only the clip table and its notionals are read here.
    """
    table = clip_table(entry)
    notionals = {coin: table[coin]["size"] * float(prices[coin])
                 for coin in table if prices.get(coin)}
    return {"orders_observed": sum(int(s.get("n") or 0) for s in (entry.get("c") or {}).values()),
            "clip_table": table, "clip_notionals": notionals, "program_runs": None,
            "taker_share": None, "ioc_5pct_share": None, "cloid_share": None, "ioc_share": None}


def judgeable(entry: dict, target_coins: set) -> bool:
    coins = entry.get("c") or {}
    enough = [c for c in coins if c in target_coins and int(coins[c].get("n") or 0) >= ep.MIN_CLIP_ORDERS]
    return len(enough) >= MIN_JUDGEABLE_COINS


def is_hit(match: dict) -> bool:
    ratio = match.get("clip_match_ratio")
    exact = (ratio is not None and ratio >= HIT_MIN_RATIO
             and (match.get("clips_matched") or 0) >= HIT_MIN_CLIPS)
    rho = match.get("notional_structure_rho")
    structural = (rho is not None and rho >= HIT_MIN_RHO
                  and (match.get("notional_coins_compared") or 0) >= HIT_MIN_STRUCTURE_COINS)
    return bool(exact or structural)


def census(sketches: dict, target_sig: dict, *, exclude: set | None = None) -> dict:
    """Compare every judgeable sketch with his signature; summarise the population.

    `attempted` is the judgeable count (the denominator of the comparability
    ceiling), `population` the comparable count, and `hits` the sketches that
    reproduce his program by either path.
    """
    exclude = {str(w).lower() for w in (exclude or ())}
    target_coins = set((target_sig.get("clip_table") or {}).keys())
    prices = sketches.get("prices") or {}
    ratios, rhos, hits = [], [], []
    judged = 0
    for wallet, entry in (sketches.get("wallets") or {}).items():
        if wallet in exclude or not judgeable(entry, target_coins):
            continue
        judged += 1
        match = ep.compare(target_sig, sketch_signature(entry, prices))
        if match["status"] != "measured":
            continue
        if match.get("clip_match_ratio") is not None:
            ratios.append(match["clip_match_ratio"])
        if match.get("notional_structure_rho") is not None:
            rhos.append(match["notional_structure_rho"])
        if is_hit(match):
            hits.append({"wallet": wallet, "first_seen_ms": entry.get("f"),
                         "last_seen_ms": entry.get("l"), "windows": entry.get("w"),
                         **{k: match.get(k) for k in (
                             "clip_match_ratio", "clips_matched", "clips_compared",
                             "clips_matched_coins", "notional_structure_rho",
                             "notional_coins_compared")}})
    out = ep.summarise_census(ratios, rhos, attempted=judged)
    out["source"] = "tape"
    out["sketch_wallets"] = len(sketches.get("wallets") or {})
    out["windows"] = int(sketches.get("windows") or 0)
    out["hits"] = sorted(hits, key=lambda h: (-(h.get("clips_matched") or 0),
                                              -(h.get("notional_structure_rho") or 0),
                                              h["wallet"]))[:50]
    return out


def newcomers(window: dict, counts: dict, target_coins: set, *,
              exclude: set | None = None) -> list[dict]:
    """Wallets first seen this window, in size, on one of his rare markets.

    A lead for the handoff check, not a finding: the script reads each one's real
    birth before anything is said about it.
    """
    exclude = {str(w).lower() for w in (exclude or ())}
    bar = rare_bar(counts, target_coins)
    out = []
    for wallet in window.get("first_seen") or []:
        if wallet in exclude:
            continue
        notional = float((window.get("notional") or {}).get(wallet) or 0)
        if notional < NEWCOMER_MIN_NOTIONAL_USD:
            continue
        markets = (window.get("markets") or {}).get(wallet) or []
        rare = sorted(m for m in markets
                      if m in target_coins and int(counts.get(m) or 0) <= bar)
        if not rare:
            continue
        out.append({"wallet": wallet, "notional_usd": round(notional, 2),
                    "rare_markets": rare, "markets": sorted(markets)})
    out.sort(key=lambda r: (-r["notional_usd"], r["wallet"]))
    return out


def born_in_silence(target_days: list[int], birth_day: int, now_day: int,
                    state: dict | None) -> dict:
    """Did a newcomer's first active day land inside his silence — a closed one in
    his history (`dormancy.handoff_score`) or the one open right now?

    The open silence is not yet a gap in his day list, so `handoff_score` cannot
    see it; it is judged here from the live dormancy state (`unusual` against his
    own p90), with the same closeness-times-backdrop score.
    """
    from src.dormancy import HANDOFF_WINDOW_DAYS, handoff_score

    closed = handoff_score(target_days, [birth_day])
    if closed.get("score", 0) > 0:
        return {**closed, "open": False}
    state = state or {}
    last = state.get("last_active_day")
    if (state.get("unusual") and isinstance(last, int) and last < birth_day <= now_day
            and birth_day - last <= HANDOFF_WINDOW_DAYS):
        delay = birth_day - last
        closeness = 1.0 - (delay - 1) / max(1, HANDOFF_WINDOW_DAYS)
        weight = min(1.0, (now_day - last) / 21.0)
        return {"score": round(max(0.0, closeness) * (0.5 + 0.5 * weight), 4),
                "gap_start_day": last, "gap_length": now_day - last, "delay_days": delay,
                "candidate_first_day": birth_day, "open": True}
    return {"score": 0.0, "reason": "not born in an unusual silence of his", "open": None}


def cap(sketches: dict, limit: int = MAX_WALLETS, target_coins: set | None = None) -> dict:
    """Evict the oldest-seen wallets beyond `limit`; comparable sketches go last.

    The ORDER of the trim is what matters (a cap on a collection must mean
    something): a wallet whose sketch already carries a clip on two of his coins
    is the rare thing the census exists to count, so it outlives a one-window
    passer-by however old.
    """
    wallets = sketches.get("wallets") or {}
    if len(wallets) <= limit:
        return sketches
    target_coins = set(target_coins or ())

    def keep_rank(item):
        wallet, entry = item
        comparable = len(set(clip_table(entry)) & target_coins) >= MIN_JUDGEABLE_COINS
        return (1 if comparable else 0, int(entry.get("l") or 0), wallet)

    kept = sorted(wallets.items(), key=keep_rank)[-limit:]
    sketches["wallets"] = dict(sorted(kept))
    return sketches
