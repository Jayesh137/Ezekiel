"""Who ran HIS script on his markets on a given day — asked of every day he has
been silent, and of every day he traded as the control.

The execution-program detector compares a few dozen candidates a run. This asks
the whole venue instead: SQD serves every fill on his coins for any past day
(`src/fills_feed.py`; ~2 GB and a few minutes a day for his ~47 coins without
BTC and ZEC), so each day's taker orders are folded per wallet into the sizes it
fired per coin and the pauses of its loop, and every wallet is judged on two
things at once:

  * CLIPS — its constant size on a coin is one HE has used on that coin (the
    dominant size of any earlier calendar month of his), on at least
    `MIN_HIT_CLIPS` coins; and
  * CADENCE — its loop is paced like his (`execution_program.cadence_match`).

Measured 2026-11-13 on 2026-08-21..23, two days he traded heavily: of 67,044
wallets on his markets, 1,632 judgeable, 32 shared his cadence, 2 matched three
or more of his sizes, and **exactly one wallet did both — his own, 18 of 18
clips**. The thirteen cadence-only wallets above $1M of notional were each put
to Hyperliquid and are firms' sub-accounts and small bots; the venue also holds
a separate cluster pacing at 1.48-1.50 s, which is not his 1.55-1.68.

So the conjunction is the hit, and the days are read in the order that serves
the mission: his SILENT days first (a wallet doing both while he is quiet is
his script running somewhere else), then his active days, where finding him is
the positive control and its rate is this screen's measured recall.

Everything a day is judged against is built from his fills BEFORE that day, so
a control day cannot find him by reading his own answer.

A day is judged alone and only its counts and hits are kept — the per-wallet
state of a day is hundreds of megabytes and is never stored.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime

from src import execution_program as ep

DAY_MS = 86_400_000

# Left out of the read: 40% of the bytes on his markets, and a constant size on
# either is something a great many wallets have.
EXCLUDED_COINS = ("BTC", "ZEC")
# Three of his sizes, the same number the vote needs (`MIN_VOTE_CLIPS`).
MIN_HIT_CLIPS = 3
# A wallet's pauses beyond this are not read: enough for stable quartiles.
MAX_GAPS_PER_WALLET = 4000
# His history needs this many days behind it before a day can be judged at all.
MIN_REFERENCE_DAYS = 30


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def day_start_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def reference(own_orders: list[dict], before_ms: int) -> dict:
    """What he looked like BEFORE `before_ms`: every size he used per coin (the
    dominant size of each calendar month) and his loop's current cadence."""
    prior = [o for o in own_orders if o["t"] < before_ms]
    by_month: dict = defaultdict(list)
    for order in prior:
        by_month[datetime.fromtimestamp(order["t"] / 1000, UTC).strftime("%Y-%m")].append(order)
    sizes: dict = defaultdict(set)
    for month_orders in by_month.values():
        for coin, row in ep.clip_table(month_orders).items():
            sizes[coin].add(row["size"])
    return {"sizes": {coin: sorted(values) for coin, values in sizes.items()},
            "cadence": ep.cadence(prior),
            "orders": len(prior)}


def coins_to_read(ref: dict) -> list[str]:
    return sorted(c for c in ref.get("sizes") or {} if c not in EXCLUDED_COINS)


def empty_state() -> dict:
    return {"sizes": defaultdict(lambda: defaultdict(Counter)),
            "gaps": defaultdict(list), "notional": Counter()}


def fold(state: dict, orders: list[dict]) -> dict:
    """Fold one chunk of taker orders (`fills_feed.taker_orders`) into the day."""
    by_wallet: dict = defaultdict(list)
    for order in orders:
        by_wallet[order["wallet"]].append(order)
    for wallet, rows in by_wallet.items():
        streams: dict = defaultdict(list)
        for order in rows:
            state["sizes"][wallet][order["coin"]][order["base_size"]] += 1
            state["notional"][wallet] += order["base_size"] * order["first_px"]
            streams[(order["coin"], order.get("side"))].append(order["t"])
        gaps = state["gaps"][wallet]
        if len(gaps) >= MAX_GAPS_PER_WALLET:
            continue
        for stamps in streams.values():
            stamps.sort()
            gaps.extend((b - a) / 1000 for a, b in zip(stamps, stamps[1:], strict=False)
                        if 0 < b - a < ep.CADENCE_PAIR_MAX_S * 1000)
    return state


def wallet_table(by_coin: dict) -> dict:
    """The wallet's constant size per coin, by the execution vector's own bar."""
    table = {}
    for coin, counter in by_coin.items():
        total = sum(counter.values())
        size, count = counter.most_common(1)[0]
        if total >= ep.MIN_CLIP_ORDERS and count / total >= ep.MIN_CLIP_SHARE:
            table[coin] = size
    return table


def wallet_cadence(gaps: list[float]) -> dict | None:
    if len(gaps) < ep.MIN_CADENCE_GAPS:
        return None
    ordered = sorted(gaps)
    return {"gaps": len(ordered), "p25": round(ordered[len(ordered) // 4], 3),
            "p50": round(ordered[len(ordered) // 2], 3)}


def judge_wallet(wallet: str, state: dict, ref: dict) -> dict | None:
    """One wallet's reading for the day, or None when nothing could be read."""
    table = wallet_table(state["sizes"].get(wallet) or {})
    cadence = wallet_cadence(state["gaps"].get(wallet) or [])
    if not table and not cadence:
        return None
    his = ref.get("sizes") or {}
    shared = sorted(c for c in table if c in his)
    matched = [c for c in shared
               if any(abs(table[c] - s) <= 1e-9 * max(1.0, abs(s)) for s in his[c])]
    paced = ep.cadence_match({"cadence": ref.get("cadence")}, {"cadence": cadence})
    return {"wallet": wallet, "cadence": cadence, "cadence_match": paced,
            "clips_compared": len(shared), "clips_matched": len(matched),
            "clips_matched_coins": matched,
            "notional": round(state["notional"].get(wallet) or 0),
            "hit": bool(paced and len(matched) >= MIN_HIT_CLIPS)}


def judge_day(state: dict, ref: dict, *, target: str, exclude: set | None = None) -> dict:
    """Every wallet of the day against him. Hits are wallets other than his own
    that did BOTH; `target_found` is the control (None when he did not trade)."""
    exclude = {str(w).lower() for w in (exclude or ())}
    target = str(target).lower()
    judged = cadence_hits = clip_hits = 0
    hits, near, target_row = [], [], None
    for wallet in state["sizes"]:
        row = judge_wallet(wallet, state, ref)
        if row is None:
            continue
        if wallet == target:
            target_row = row
            continue
        if wallet in exclude:
            continue
        judged += 1
        cadence_hits += bool(row["cadence_match"])
        clip_hits += row["clips_matched"] >= MIN_HIT_CLIPS
        if row["hit"]:
            hits.append(row)
        elif (row["cadence_match"] and row["clips_matched"] >= 2) or (
                row["clips_matched"] >= MIN_HIT_CLIPS and row["cadence"] is None):
            # One short of a hit, or his sizes with a cadence that could not be
            # read: kept so the operator can see how close strangers come.
            near.append(row)
    hits.sort(key=lambda r: (-r["clips_matched"], -r["notional"]))
    near.sort(key=lambda r: (-r["clips_matched"], -r["notional"]))
    return {"wallets": len(state["sizes"]), "judged": judged,
            "cadence_hits": cadence_hits, "clip_hits": clip_hits,
            "hits": hits, "near": near[:10],
            "target_seen": target in state["sizes"],
            # Measurable only when he ran enough that day to be judged at all (a
            # readable cadence and a constant size on MIN_HIT_CLIPS coins): a
            # day of forty orders is unmeasured, not a miss (rule 5). A day he
            # traded coins his earlier history never held IS a miss.
            "target_found": (bool(target_row["hit"]) if target_row is not None
                             and target_row["cadence"] is not None
                             and len(wallet_table(state["sizes"].get(target) or {})) >= MIN_HIT_CLIPS
                             else None),
            "target_row": target_row}


def plan(his_days: set, first_day: str, last_day: str, done: set) -> list[tuple[str, bool]]:
    """(day, silent) still to read: silent days newest first, then his active
    days newest first. A silent day is one inside his history with no fill of
    his — where a second wallet running his script would be alone."""
    lo, hi = day_start_ms(first_day), day_start_ms(last_day)
    days = [day_of(ms) for ms in range(lo, hi + 1, DAY_MS)]
    todo = [d for d in days if d not in done]
    silent = sorted((d for d in todo if d not in his_days), reverse=True)
    active = sorted((d for d in todo if d in his_days), reverse=True)
    return [(d, True) for d in silent] + [(d, False) for d in active]


def summarise(days: dict, hits: dict) -> dict:
    """Coverage, the control's recall and the stranger base rates, from the
    per-day rows alone."""
    rows = [r for r in days.values() if isinstance(r, dict) and not r.get("error")]
    silent = [r for r in rows if r.get("silent")]
    active = [r for r in rows if not r.get("silent")]
    measurable = [r for r in active if r.get("target_found") is not None]
    judged = sum(r.get("judged") or 0 for r in rows)
    return {
        "days_read": len(rows), "silent_days_read": len(silent),
        "active_days_read": len(active),
        "target_measurable_days": len(measurable),
        "target_found_days": sum(1 for r in measurable if r["target_found"]),
        "wallet_days_judged": judged,
        "cadence_wallet_days": sum(r.get("cadence_hits") or 0 for r in rows),
        "clip_wallet_days": sum(r.get("clip_hits") or 0 for r in rows),
        "hit_wallets": len(hits),
        "hit_wallets_on_silent_days": sum(1 for h in hits.values() if h.get("silent_days")),
    }
