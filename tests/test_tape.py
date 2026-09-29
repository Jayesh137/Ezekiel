"""Detect his execution program LIVE on the public trade tape.

The tape names both sides of every trade, so a wallet running his exact clip
program on one of his markets is visible the instant it trades — off the
leaderboard, off the roster, with no per-wallet API call. This reuses the same
program-run detection as the execution vector; a hit becomes a candidate the
census-gated detector then examines fully (never a direct alert, to avoid noise).
"""

from src import tape


def _trade(h, buyer, seller, side, coin, px, sz, t):
    return {"hash": h, "users": [buyer, seller], "side": side, "coin": coin,
            "px": str(px), "sz": str(sz), "time": t, "tid": h}


def _burst(wallet, coin, clip, n, t0=0, gap_ms=1750, side="A", h0=1):
    # side "A" = sell-aggressor; the taker is users[1] (the seller).
    other = "0x" + "9" * 40
    return [_trade(h0 + i, other, wallet, side, coin, 4.0, clip, t0 + i * gap_ms) for i in range(n)]


def test_taker_orders_group_a_sliced_taker_order_and_pick_the_aggressor():
    W = "0x" + "1" * 40
    other = "0x" + "9" * 40
    # one taker SELL order (side A) filled in two trades sharing a hash
    trades = [_trade(7, other, W, "A", "NEAR", 4.0, 150, 1000),
              _trade(7, other, W, "A", "NEAR", 4.0, 100, 1000)]
    orders = tape.taker_orders(trades)
    assert len(orders) == 1
    assert orders[0]["wallet"] == W
    assert orders[0]["base_size"] == 250
    assert orders[0]["taker"] is True


def test_the_buyer_is_the_taker_when_side_is_B():
    buyer, seller = "0x" + "a" * 40, "0x" + "b" * 40
    orders = tape.taker_orders([_trade(1, buyer, seller, "B", "BTC", 100000, 0.1, 0)])
    assert orders[0]["wallet"] == buyer


def test_a_wallet_running_his_exact_clip_program_is_a_hit():
    W = "0x" + "1" * 40
    target_clips = {"NEAR": {"size": 250}, "ZEC": {"size": 1}}
    hits = tape.program_clip_hits(tape.taker_orders(_burst(W, "NEAR", 250, 40)), target_clips)
    assert len(hits) == 1
    assert hits[0]["wallet"] == W
    assert hits[0]["coin"] == "NEAR"
    assert hits[0]["clip"] == 250


def test_a_program_at_a_different_clip_size_is_not_a_hit():
    W = "0x" + "1" * 40
    target_clips = {"NEAR": {"size": 250}}
    hits = tape.program_clip_hits(tape.taker_orders(_burst(W, "NEAR", 999, 40)), target_clips)
    assert hits == []


def test_a_short_non_program_burst_is_not_a_hit():
    W = "0x" + "1" * 40
    target_clips = {"NEAR": {"size": 250}}
    hits = tape.program_clip_hits(tape.taker_orders(_burst(W, "NEAR", 250, 5)), target_clips)
    assert hits == []


def test_a_coin_he_does_not_clip_is_not_a_hit():
    W = "0x" + "1" * 40
    hits = tape.program_clip_hits(tape.taker_orders(_burst(W, "WIF", 1000, 40)), {"NEAR": {"size": 250}})
    assert hits == []
