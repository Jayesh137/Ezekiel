# tests/test_gcr_hypothesis.py
"""Testing the GCR hypothesis in BOTH directions.

The operator puts the odds that this wallet is GCR's at 55-75%. A profile that
can only confirm is worthless, so these tests pin that traits can return
CONTRADICTS, and that anything unmeasurable returns UNTESTABLE rather than
quietly counting as agreement.

They also pin the nuance that careful reading of the source produced: the
distilled slogan "never short small caps" is contradicted by GCR's own posts,
which describe fading listing pumps on IOTX, TRU, CLV, MNGO and AXS. Reading the
slogan over his actual behaviour would have manufactured a false contradiction
about the target.
"""

from src.gcr_hypothesis import (
    CONSISTENT,
    CONTRADICTS,
    UNTESTABLE,
    check_hedged_book,
    check_net_short_bias,
    check_round_number_affinity,
    check_shorts_small_caps,
    evaluate,
    load_reference,
    positions_from,
)

FREQ = {"eligible_wallets": 1000,
        "market_counts": {"BTC": 400, "ETH": 380, "TINY": 0}}


def pos(coin, direction, notional=1000.0):
    return {"coin": coin, "direction": direction, "notional": notional}


def test_positions_are_read_with_direction_from_signed_size():
    state = {"assetPositions": [
        {"position": {"coin": "BTC", "szi": "-1", "positionValue": "100"}},
        {"position": {"coin": "ETH", "szi": "2", "positionValue": "200"}}]}
    got = positions_from(state)
    assert [(p["coin"], p["direction"]) for p in got] == [("BTC", "short"), ("ETH", "long")]


def test_closed_and_malformed_positions_are_skipped():
    state = {"assetPositions": [
        {"position": {"coin": "BTC", "szi": "0"}},
        {"position": {"coin": "", "szi": "1"}},
        "junk", None]}
    assert positions_from(state) == []


def test_a_short_book_is_consistent_with_bear_at_heart():
    assert check_net_short_bias([pos("A", "short"), pos("B", "short"),
                                pos("C", "short")])["verdict"] == CONSISTENT


def test_a_long_book_contradicts_bear_at_heart():
    """The test must be able to point AWAY from the hypothesis."""
    assert check_net_short_bias([pos("A", "long"), pos("B", "long"),
                                pos("C", "long")])["verdict"] == CONTRADICTS


def test_a_balanced_book_resolves_nothing():
    got = check_net_short_bias([pos("A", "long"), pos("B", "short")])
    assert got["verdict"] == UNTESTABLE


def test_a_one_sided_book_contradicts_the_hedged_structure():
    """He described shorting weaker alts AGAINST favoured longs. A book with no
    longs at all is directional, which is a real difference."""
    got = check_hedged_book([pos("A", "short"), pos("B", "short")])
    assert got["verdict"] == CONTRADICTS
    assert "one-sided" in got["detail"]


def test_a_two_sided_book_is_consistent_with_hedging():
    assert check_hedged_book([pos("A", "long"), pos("B", "short")])["verdict"] \
        == CONSISTENT


def test_shorting_a_thin_market_is_untestable_not_a_contradiction():
    """The load-bearing nuance. His own posts fade small-cap listing pumps, so
    reading the distilled slogan literally would invent a contradiction."""
    got = check_shorts_small_caps([pos("TINY", "short")], freq=FREQ)
    assert got["verdict"] == UNTESTABLE
    assert "listing pumps" in got["detail"]


def test_shorting_only_liquid_markets_is_consistent():
    got = check_shorts_small_caps([pos("BTC", "short"), pos("ETH", "short")],
                                 freq=FREQ)
    assert got["verdict"] == CONSISTENT


def test_missing_frequencies_cannot_be_judged():
    assert check_shorts_small_caps([pos("TINY", "short")], freq=None)["verdict"] \
        == UNTESTABLE


def test_round_amounts_are_consistent_but_never_a_contradiction():
    """Round numbers are too common among large traders to disconfirm anything."""
    assert check_round_number_affinity(
        amounts=[1_000_000.0] * 10)["verdict"] == CONSISTENT
    assert check_round_number_affinity(
        amounts=[1234.56] * 10)["verdict"] == UNTESTABLE


def test_no_data_never_counts_as_agreement():
    """Absence of evidence is not evidence — the failure mode that lets a
    profile confirm itself."""
    out = evaluate({}, freq=None, amounts=None)
    assert out["tally"][CONSISTENT] == 0
    assert out["tally"][UNTESTABLE] == len(out["results"])
    assert "No movement" in out["reading"]


def test_a_wholly_contradicting_wallet_reads_as_evidence_against():
    state = {"assetPositions": [
        {"position": {"coin": "BTC", "szi": "1", "positionValue": "100"}},
        {"position": {"coin": "ETH", "szi": "2", "positionValue": "100"}},
        {"position": {"coin": "SOL", "szi": "3", "positionValue": "100"}}]}
    out = evaluate(state, freq=FREQ, amounts=None)
    assert out["tally"][CONTRADICTS] >= 1
    assert "AWAY" in out["reading"]


def test_no_single_probability_is_emitted():
    """Four style traits cannot be combined into a number that means anything,
    and printing one would invite exactly the false precision this avoids."""
    out = evaluate({}, freq=None, amounts=None)
    assert "probability" not in out
    assert "score" not in out
    assert "55-75%" in out["prior_note"]


def test_the_shipped_reference_parses_and_keeps_its_warnings():
    """A typo here would silently disarm the anti-circularity guidance."""
    ref = load_reference()
    assert ref["handles"]
    assert any("circular" in line.lower() for line in ref["_epistemic_status"])
    assert any("NOT A FACT" in line for line in ref["_epistemic_status"])
    # The small-cap caveat must survive, or a future reader re-adds the bug.
    small = next(p for p in ref["principles"] if p["name"] == "Never short small caps")
    assert "CONTRADICTED BY HIS OWN POSTS" in small["caveat"]
