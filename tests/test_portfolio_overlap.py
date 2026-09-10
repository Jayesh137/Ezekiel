# tests/test_portfolio_overlap.py
"""Same unusual basket, same moment — weighted so the majors count for nothing.

Holding BTC short during a sell-off says nothing: measured 2026-09-10, BTC is
held by 37% of eligible wallets and HYPE by 36%. Holding a market almost nobody
touches, in the same direction, at the same time, is a different statement.

Without rarity weighting this vector would match every whale running a broad book
against every other, since they all hold the majors. These tests pin that, and
pin the confound that keeps it out of the roster's tiering: a COPY-TRADER holds
the same basket by definition, and this project exists because its owner copies
this trader by hand.
"""

from src.portfolio_overlap import MIN_POSITIONS, basket, overlap_score

# 100 eligible wallets: a market held by 40 is common, by 1 is rare.
FREQ = {"eligible_wallets": 100,
        "market_counts": {"BTC": 40, "ETH": 38, "SOL": 30,
                          "RARE1": 0, "RARE2": 0, "RARE3": 0}}


def state(**coins):
    return {"assetPositions": [
        {"position": {"coin": c, "szi": "1.0" if d == "long" else "-1.0"}}
        for c, d in coins.items()]}


def test_basket_reads_direction_from_signed_size():
    assert basket(state(BTC="short", ETH="long")) == {"BTC": "short", "ETH": "long"}


def test_basket_ignores_closed_and_malformed_positions():
    raw = {"assetPositions": [
        {"position": {"coin": "BTC", "szi": "0"}},
        {"position": {"coin": "", "szi": "1"}},
        {"position": {"coin": "ETH", "szi": "notanumber"}},
        "junk", None]}
    assert basket(raw) == {}


def test_basket_of_an_unreadable_state_is_empty():
    assert basket(None) == {}
    assert basket({}) == {}


def test_sharing_only_the_majors_scores_nothing():
    """The whole point. Two whales both short BTC, ETH and SOL is Tuesday, not
    evidence — and plain overlap would score that 1.0."""
    a = basket(state(BTC="short", ETH="short", SOL="short"))
    b = basket(state(BTC="short", ETH="short", SOL="short"))
    got = overlap_score(a, b, FREQ)
    assert got["score"] == 0.0
    assert "only common ones" in got["reason"]


def test_sharing_rare_markets_scores():
    a = basket(state(BTC="short", RARE1="long", RARE2="long"))
    b = basket(state(ETH="short", RARE1="long", RARE2="long"))
    got = overlap_score(a, b, FREQ)
    assert got["score"] > 0.9
    assert set(got["shared"]) == {"RARE1", "RARE2"}


def test_opposite_directions_in_the_same_rare_market_do_not_count():
    """They are both interested in it and they disagree — that is a union, not
    an intersection."""
    a = basket(state(BTC="short", RARE1="long", RARE2="long"))
    b = basket(state(BTC="short", RARE1="short", RARE2="long"))
    got = overlap_score(a, b, FREQ)
    assert got["shared"] == ["RARE2"]
    assert 0 < got["score"] < 1.0


def test_a_book_too_small_to_be_a_basket_scores_nothing():
    a = basket(state(RARE1="long", RARE2="long", RARE3="long"))
    b = basket(state(RARE1="long"))
    got = overlap_score(a, b, FREQ)
    assert got["score"] == 0.0
    assert "too small" in got["reason"]
    assert MIN_POSITIONS >= 3


def test_holding_more_things_does_not_inflate_the_score():
    """A rarity-weighted Jaccard, not raw overlap: a wallet is not rewarded for
    simply holding a lot."""
    a = basket(state(RARE1="long", RARE2="long", RARE3="long"))
    tight = basket(state(RARE1="long", RARE2="long", RARE3="long"))
    broad = basket(state(RARE1="long", RARE2="long", RARE3="long",
                         BTC="long", ETH="long", SOL="long"))
    # The extra majors weigh nothing, so both are perfect matches.
    assert overlap_score(a, tight, FREQ)["score"] == 1.0
    assert overlap_score(a, broad, FREQ)["score"] == 1.0


def test_a_disjoint_rare_book_scores_zero():
    a = basket(state(RARE1="long", RARE2="long", RARE3="long"))
    b = basket(state(RARE1="short", RARE2="short", RARE3="short"))
    assert overlap_score(a, b, FREQ)["score"] == 0.0


def test_rarity_is_reported_so_a_human_can_judge():
    a = basket(state(RARE1="long", RARE2="long", RARE3="long"))
    b = basket(state(RARE1="long", RARE2="long", RARE3="long"))
    rare = overlap_score(a, b, FREQ)["shared_rare"]
    assert rare and all(0 < s["rarity"] <= 1.0 for s in rare)
    assert all("direction" in s for s in rare)
