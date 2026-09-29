"""Copier-cohort sensor: use accounts that follow him as a pointer to his wallet.

He has copiers — accounts that open his positions with a lag. He does not control
them. If a stable cohort of them stops trading his old wallet's positions and
starts trading a new account's, that account is his successor. The hard part is
telling a real copier from two accounts reacting to the same market move, so every
follow rate is compared against a time-shuffled placebo and weighted by coin
rarity (following him into ZEC or xyz:SILVER means far more than into BTC).
"""

from src import copier_cohort as cc

HOUR = 3600_000
COINS = ["NEAR", "ZEC", "AAVE", "LINK", "ONDO", "XRP", "SOL", "ADA", "DOGE", "SUI"]


def _fill(oid, ts, coin, side, before, sz):
    return {"oid": oid, "time": ts, "coin": coin, "side": side,
            "sz": str(sz), "startPosition": str(before), "px": "10", "crossed": True, "tid": oid}


def _open(oid, ts, coin, direction, sz=10):
    return _fill(oid, ts, coin, "B" if direction > 0 else "A", 0, sz)


def _his(coins=COINS, t0=1_000_000, step_h=10):
    return cc.opening_events([_open(i, t0 + i * step_h * HOUR, c, -1) for i, c in enumerate(coins)])


def test_opening_events_extracts_direction_and_dedupes_sliced_entries():
    fills = [_fill(1, 1000, "NEAR", "A", 0, 100), _fill(2, 5000, "NEAR", "A", -100, 100)]
    assert cc.opening_events(fills) == [("NEAR", -1, 1000)]


def test_a_reduction_is_not_an_opening():
    assert cc.opening_events([_fill(1, 1000, "NEAR", "B", -100, 100)]) == []


def test_a_consistent_follower_scores_far_above_its_placebo():
    target = _his()
    cand = cc.opening_events([_open(100 + i, 1_000_000 + i * 10 * HOUR + HOUR, c, -1)
                             for i, c in enumerate(COINS)])
    score = cc.follow_score(target, cand, lag_ms=3 * HOUR, freq={})
    assert score["status"] == "measured"
    assert score["opportunities"] == 10
    assert score["follows"] == 10
    assert score["follow_rate"] == 1.0
    assert score["placebo_rate"] < 0.5
    assert score["lead_lag_median_ms"] == HOUR


def test_an_unrelated_trader_does_not_look_like_a_follower():
    target = _his()
    cand = cc.opening_events([_open(100 + i, 1_000_000 + i * 7 * HOUR, c, 1)
                             for i, c in enumerate(["BTC", "ETH", "SOL", "kPEPE", "WIF", "TAO", "OP", "ARB", "TIA", "SEI"])])
    assert cc.follow_score(target, cand, lag_ms=3 * HOUR, freq={})["follow_rate"] == 0.0


def test_following_into_a_rare_coin_scores_above_a_common_one():
    freq = {"sufficient": True, "eligible_wallets": 500, "market_counts": {"XRP": 450, "ZEC": 3}}
    target = _his()  # includes both XRP (common) and ZEC (rare)
    common = cc.follow_score(target, cc.opening_events([_open(1, 1_000_000 + 5 * 10 * HOUR + HOUR, "XRP", -1)]),
                             lag_ms=3 * HOUR, freq=freq)
    rare = cc.follow_score(target, cc.opening_events([_open(1, 1_000_000 + 1 * 10 * HOUR + HOUR, "ZEC", -1)]),
                           lag_ms=3 * HOUR, freq=freq)
    assert rare["rarity_weighted_follows"] > common["rarity_weighted_follows"]


def test_is_copier_requires_lift_over_placebo_and_enough_follows():
    strong = {"status": "measured", "follow_rate": 0.8, "placebo_rate": 0.1, "follows": 8}
    assert cc.is_copier(strong) is True
    coincidental = {"status": "measured", "follow_rate": 0.5, "placebo_rate": 0.45, "follows": 8}
    assert cc.is_copier(coincidental) is False
    thin = {"status": "measured", "follow_rate": 1.0, "placebo_rate": 0.0, "follows": 2}
    assert cc.is_copier(thin) is False
    unmeasured = {"status": "insufficient_data", "follow_rate": 1.0, "placebo_rate": 0.0, "follows": 9}
    assert cc.is_copier(unmeasured) is False


def test_opportunities_are_restricted_to_the_candidate_observed_window():
    target = _his()  # 10 openings, 10h apart from t0
    cand = cc.opening_events([_open(100 + i, 1_000_000 + i * 10 * HOUR + HOUR, c, -1)
                             for i, c in enumerate(COINS)])
    window = (1_000_000, 1_000_000 + 7 * 10 * HOUR + HOUR)  # covers his first 8 openings
    score = cc.follow_score(target, cand, lag_ms=3 * HOUR, freq={}, observed_window=window)
    assert score["opportunities"] == 8
    assert score["status"] == "measured"


def test_no_overlap_is_insufficient_not_a_zero_follower():
    target = _his()
    cand = cc.opening_events([_open(1, 9_000_000_000, "NEAR", -1)])
    score = cc.follow_score(target, cand, lag_ms=3 * HOUR, freq={},
                            observed_window=(9_000_000_000, 9_000_100_000))
    assert score["status"] == "insufficient_data"
    assert cc.is_copier(score) is False
