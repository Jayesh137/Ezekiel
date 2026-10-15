"""Venue-wide population: coin classes, folding, bounds, outcome traders, program takers."""

from src import venue_sample as vs

A = "0x" + "a" * 40
B = "0x" + "b" * 40
T = "0x" + "1" * 40


def _fill(user, coin, t, sz=1.0, px=10.0, h=None, crossed=True):
    return {"user": user, "coin": coin, "px": px, "sz": sz, "time": t, "side": "B",
            "hash": h or f"0x{user[-4:]}{coin}{t}", "tid": t, "crossed": crossed}


def test_coin_classes():
    assert vs.coin_class("#126880") == "outcome"
    assert vs.coin_class("@107") == "spot" and vs.coin_class("PURR/USDC") == "spot"
    assert vs.coin_class("xyz:SILVER") == "hip3" and vs.coin_class("BTC") == "perp"


def test_fold_sample_counts_takers_coins_outcomes_and_program_runs():
    state = vs.empty_state()
    fills = [_fill(A, "BTC", 1_000), _fill(A, "#12", 2_000), _fill(A, "#12", 3_000), _fill(A, "#13", 3_500),
             _fill(A, "xyz:SILVER", 4_000), _fill(A, "@107", 5_000),
             _fill(B, "ETH", 1_000, crossed=False)]                    # a maker fill: not a taker
    # A program-shaped run by B: 16 equal clips 1.7 s apart on NEAR.
    fills += [_fill(B, "NEAR", 10_000 + i * 1_700, sz=250.0, h=f"0xrun{i}") for i in range(16)]
    seen = vs.fold_sample(state, fills, now_ms=99, seconds=180)
    # Seven coins are named, but ETH came only from a maker fill: six taker coins.
    assert seen == {"fills": 23, "orders": 22, "takers": 2, "coins": 6, "outcome_takers": 1,
                    "program_takers": 1}
    a, b = state["wallets"][A], state["wallets"][B]
    assert a["n"] == 6 and a["o"] == 3 and a["s"] == 1 and a["d"] == {"xyz": 1} and a["p"] == 0
    assert set(a["c"]) == {"BTC", "#12", "#13", "xyz:SILVER", "@107"}
    assert b["n"] == 16 and b["p"] == 1 and b["pc"] == ["NEAR"]
    assert state["samples"] == 1 and state["fills"] == 23 and state["seconds"] == 180.0
    assert vs.summary(state) == {"samples": 1, "fills": 23, "seconds": 180.0, "wallets": 2,
                                 "outcome_traders": 1, "spot_traders": 1, "program_takers": 1,
                                 "hip3_dex_takers": {"xyz": 1}}


def test_cap_keeps_the_most_recently_seen():
    state = vs.empty_state()
    state["wallets"] = {A: {"l": 1, "n": 5}, B: {"l": 2, "n": 1}, T: {"l": 3, "n": 1}}
    assert set(vs.cap(state, limit=2)["wallets"]) == {B, T}


def test_outcome_traders_and_the_join_with_his_treasury_sessions():
    state = vs.empty_state()
    state["wallets"] = {
        A: {"f": 1, "l": 9, "n": 10, "c": ["#10", "BTC"], "d": {}, "o": 4, "s": 0, "p": 0, "pc": []},
        B: {"f": 1, "l": 9, "n": 10, "c": ["#11"], "d": {}, "o": 2, "s": 0, "p": 0, "pc": []},   # below the bar
        T: {"f": 1, "l": 9, "n": 10, "c": ["#12"], "d": {}, "o": 9, "s": 0, "p": 2, "pc": ["#12"]},
    }
    traders = vs.outcome_traders(state)
    assert [r["wallet"] for r in traders] == [T, A] and traders[1]["coins"] == ["#10"]
    treasury = {"wallets": {A: {"s": 3, "c": 0}, T: {"s": 5, "c": 1}}}
    joined = vs.outcome_in_his_sessions(state, treasury, cluster={T})
    assert [(r["wallet"], r["sessions_with"]) for r in joined] == [(A, 3)]
    assert vs.outcome_in_his_sessions(state, None, cluster=set()) == []
    assert [r["wallet"] for r in vs.program_takers(state)] == [T]
