"""Detect his execution program LIVE on the public trade tape.

The tape (`trades` websocket) names both sides of every trade, so a wallet running
his exact clip program on one of his markets is visible the instant it trades — off
the leaderboard, off the roster, with no per-wallet API call. This is the free-setup
recovery of the real-time behavioural net: bounded windows on GitHub Actions rather
than a 24/7 stream, so it samples rather than watches continuously.

It reuses the execution vector's own program-run detection. A hit does NOT alert
directly (a single-coin live burst is a lead, not proof, and direct tape alerts
would be noisy); it becomes an execution-program candidate that the census-gated
detector then examines fully on the next trace run. Pure functions here; the script
streams and persists.
"""

from collections import defaultdict


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def taker_orders(trades: list[dict]) -> list[dict]:
    """Group tape trades into the aggressor's orders (one taker action = one hash).

    Observed convention (validated against his live NEAR program): `users` is
    [buyer, seller] and `side` is the aggressor's side, so the taker is the buyer
    when side is "B" and the seller when side is "A". Fills of one taker order
    share its L1 `hash`.
    """
    groups: dict = {}
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        users = trade.get("users") or []
        side = trade.get("side")
        if len(users) != 2 or side not in ("A", "B"):
            continue
        taker = str(users[0] if side == "B" else users[1]).lower()
        size, price, ts = _num(trade.get("sz")), _num(trade.get("px")), _num(trade.get("time"))
        if size is None or price is None or ts is None or size <= 0:
            continue
        key = (trade.get("hash"), taker, trade.get("coin"))
        order = groups.setdefault(key, {"wallet": taker, "coin": trade.get("coin"), "side": side,
                                        "base_size": 0.0, "t": ts, "first_px": price, "taker": True})
        order["base_size"] += size
        if ts < order["t"]:
            order["t"], order["first_px"] = ts, price
    for order in groups.values():
        order["base_size"] = round(order["base_size"], 10)
    return sorted(groups.values(), key=lambda o: o["t"])


def program_clip_hits(orders: list[dict], target_clip_table: dict) -> list[dict]:
    """Wallets running a program-shaped burst at one of HIS exact per-coin clips."""
    from src.execution_program import program_runs

    by_wallet: dict = defaultdict(list)
    for order in orders:
        by_wallet[order["wallet"]].append(order)
    hits = []
    for wallet, wallet_orders in by_wallet.items():
        for run in program_runs(wallet_orders):
            entry = target_clip_table.get(run["coin"])
            if entry and abs(run["clip"] - entry["size"]) < 1e-9:
                hits.append({"wallet": wallet, **run})
    return sorted(hits, key=lambda h: (-h["orders"], h["wallet"]))
