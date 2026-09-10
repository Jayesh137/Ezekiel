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
    CAN_DISCONFIRM,
    CHECKS,
    CONSISTENT,
    CONTRADICTS,
    UNTESTABLE,
    check_broad_short_basket,
    check_liquidation_distance,
    check_net_short_bias,
    check_round_number_affinity,
    check_shorts_small_caps,
    evaluate,
    load_reference,
    positions_from,
)

FREQ = {"eligible_wallets": 1000,
        "market_counts": {"BTC": 400, "ETH": 380, "TINY": 0}}


def pos(coin, direction, notional=1000.0, liq_distance=5.0):
    """A position far from liquidation by default, so tests aimed at other
    traits do not trip the liquidation check by accident."""
    return {"coin": coin, "direction": direction, "notional": notional,
            "size": -1000.0 if direction == "short" else 1000.0,
            "liq_distance": liq_distance}


def test_positions_carry_signed_size_for_the_sizing_test():
    state = {"assetPositions": [
        {"position": {"coin": "RLC", "szi": "-280000", "positionValue": "100"}}]}
    assert positions_from(state)[0]["size"] == -280000.0


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


def test_a_long_book_no_longer_contradicts_bear_at_heart():
    """The second correction the images forced. img007 is an FTX grouped-
    positions panel with BTC, ETH and SUSHI all LONG and $2.19M open — and he
    wrote "never short in a bull market" and "back to degen longing". Direction
    tracks the regime, so scoring a long book as evidence against him would fire
    hardest exactly when he is behaving most like himself."""
    got = check_net_short_bias([pos("A", "long"), pos("B", "long"),
                                pos("C", "long")])
    assert got["verdict"] == UNTESTABLE
    assert "not a contradiction" in got["detail"]


def test_a_balanced_book_resolves_nothing():
    got = check_net_short_bias([pos("A", "long"), pos("B", "short")])
    assert got["verdict"] == UNTESTABLE


def liq(coin, distance):
    return {"coin": coin, "direction": "short", "notional": 1000.0,
            "size": -1000.0, "liq_distance": distance}


def test_a_book_run_near_liquidation_contradicts_him():
    """The disconfirming check. He called himself "nearly impossible to
    liquidate" while holding an 8.3M CHZ short he would carry for months, and
    every blotter runs 1x-5x cross."""
    got = check_liquidation_distance([liq("A", 0.05), liq("B", 0.10),
                                      liq("C", 8.0)])
    assert got["verdict"] == CONTRADICTS
    assert "of 3 positions sit within" in got["detail"]


def test_a_book_far_from_liquidation_is_consistent():
    got = check_liquidation_distance([liq("A", 1.0), liq("B", 6.0)])
    assert got["verdict"] == CONSISTENT


def test_one_stretched_position_in_a_safe_book_is_not_a_contradiction():
    """A single tight position is a trade, not a way of carrying risk."""
    got = check_liquidation_distance([liq("A", 0.05)] + [liq(f"B{i}", 5.0)
                                                         for i in range(9)])
    assert got["verdict"] != CONTRADICTS


def test_missing_liquidation_prices_are_untestable_not_safe():
    """A position with no liquidation price must not read as a distant one."""
    assert check_liquidation_distance(
        [liq("A", None), liq("B", None)])["verdict"] == UNTESTABLE
    assert check_liquidation_distance([])["verdict"] == UNTESTABLE


def test_liquidation_distance_is_never_zero_when_unreadable():
    """Zero would read as 'about to be liquidated' to every threshold above.
    An unreadable entry price yields None; a null liquidation price is a
    different thing entirely and is covered by the unliquidatable test below."""
    state = {"assetPositions": [
        {"position": {"coin": "B", "szi": "-1", "positionValue": "10",
                      "entryPx": "0", "liquidationPx": "50"}},
        {"position": {"coin": "C", "szi": "-1", "positionValue": "10",
                      "entryPx": "abc", "liquidationPx": "50"}}]}
    assert [p["liq_distance"] for p in positions_from(state)] == [None, None]


def test_liquidation_distance_is_read_from_entry_and_liq_price():
    state = {"assetPositions": [
        {"position": {"coin": "A", "szi": "-1", "positionValue": "10",
                      "entryPx": "100", "liquidationPx": "150"}}]}
    assert positions_from(state)[0]["liq_distance"] == 0.5


def test_a_one_sided_short_book_is_not_a_contradiction():
    """The correction. An earlier `hedged_book` check called a book with no
    longs a CONTRADICTION, reading his writing about being delta neutral. His
    own blotters are 8-of-9, 7-of-7 and 6-of-7 short — one-sided IS his shape,
    and the check was manufacturing evidence against the hypothesis."""
    got = check_broad_short_basket([pos(f"A{i}", "short") for i in range(8)])
    assert got["verdict"] != CONTRADICTS
    assert got["verdict"] == CONSISTENT


def test_breadth_is_what_the_blotters_actually_show():
    """Seven-plus simultaneous alt shorts, not one concentrated bet."""
    assert check_broad_short_basket(
        [pos(f"A{i}", "short") for i in range(7)])["verdict"] == CONSISTENT


def test_a_narrow_book_resolves_nothing_rather_than_disconfirming():
    """A smaller account cannot run nine markets; that is a size constraint, not
    a difference in style."""
    got = check_broad_short_basket([pos("A", "short"), pos("B", "short")])
    assert got["verdict"] == UNTESTABLE


def test_a_single_short_cannot_demonstrate_breadth():
    assert check_broad_short_basket([pos("A", "short")])["verdict"] == UNTESTABLE


def test_breadth_counts_shorts_not_the_whole_book():
    """A wallet holding seven longs and one short is not running his basket."""
    positions = [pos(f"L{i}", "long") for i in range(7)] + [pos("S", "short")]
    assert check_broad_short_basket(positions)["verdict"] == UNTESTABLE


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


def test_round_position_sizes_count_as_well_as_transfers():
    """His blotters round the SIZES he trades — -280,000 RLC, -47,000 GTC,
    -27,000 AVAX — which is nearer a trading fingerprint than treasury flow."""
    sized = [dict(pos("RLC", "short"), size=-280_000.0),
             dict(pos("GTC", "short"), size=-47_000.0),
             dict(pos("AVAX", "short"), size=-27_000.0)]
    got = check_round_number_affinity(sized, amounts=None)
    assert got["verdict"] == CONSISTENT
    assert "position sizes" in got["detail"]


def test_unround_position_sizes_do_not_disconfirm():
    sized = [dict(pos("X", "short"), size=-12_345.67) for _ in range(5)]
    assert check_round_number_affinity(sized, amounts=None)["verdict"] == UNTESTABLE


def test_dust_sized_positions_are_not_counted_as_round():
    """abs(size) < 1000 would make almost anything look round."""
    sized = [dict(pos("X", "short"), size=-5.0) for _ in range(5)]
    assert check_round_number_affinity(sized, amounts=None)["verdict"] == UNTESTABLE


def test_no_data_never_counts_as_agreement():
    """Absence of evidence is not evidence — the failure mode that lets a
    profile confirm itself."""
    out = evaluate({}, freq=None, amounts=None)
    assert out["tally"][CONSISTENT] == 0
    assert out["tally"][UNTESTABLE] == len(out["results"])
    assert "No movement" in out["reading"]


def test_a_wholly_contradicting_wallet_reads_as_evidence_against():
    """A book run right up against liquidation — the thing he says he never
    does. Note it is the RISK, not the direction, that disconfirms now."""
    state = {"assetPositions": [
        {"position": {"coin": c, "szi": "1", "positionValue": "100",
                      "entryPx": "100", "liquidationPx": "105"}}
        for c in ("BTC", "ETH", "SOL")]}
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


def test_the_suite_can_still_disconfirm_at_all():
    """The guard on this module's whole purpose. Removing check_hedged_book was
    right on the evidence, but it left only ONE check able to return CONTRADICTS
    — and that removal alone flipped the live tally from mixed to unanimous. If a
    future edit removes the last one, the suite becomes a machine that can only
    agree with itself, and this test is what stops that landing quietly."""
    assert CAN_DISCONFIRM, "no check can disconfirm — the suite cannot be wrong"
    assert CAN_DISCONFIRM <= set(CHECKS), "CAN_DISCONFIRM names a check that is gone"

    stretched = {"assetPositions": [
        {"position": {"coin": c, "szi": "1", "positionValue": "100",
                      "entryPx": "100", "liquidationPx": "105"}}
        for c in ("BTC", "ETH", "SOL")]}
    assert evaluate(stretched, freq=FREQ, amounts=None)["tally"][CONTRADICTS] >= 1


def test_the_report_admits_how_little_of_it_could_go_the_other_way():
    out = evaluate({}, freq=None, amounts=None)
    f = out["falsifiability"]
    assert f["checks_that_can_disconfirm"] == sorted(CAN_DISCONFIRM)
    assert f["of_total"] == len(CHECKS)


def test_a_check_named_as_disconfirming_actually_can():
    """CAN_DISCONFIRM is hand-maintained, so it can drift from the code. Every
    check it names must have a real input that makes it return CONTRADICTS."""
    books = [
        [pos(c, "long") for c in ("A", "B", "C")],
        [pos(c, "short") for c in ("A", "B", "C")],
        [pos(c, "short", liq_distance=0.02) for c in ("A", "B", "C")],
        [pos(c, "long", liq_distance=0.02) for c in ("A", "B", "C")],
        [],
    ]
    for name in CAN_DISCONFIRM:
        fn = CHECKS[name]
        verdicts = {fn(book, freq=FREQ, amounts=[1234.56])["verdict"]
                    for book in books}
        assert CONTRADICTS in verdicts, f"{name} cannot return CONTRADICTS"


def test_an_absent_liquidation_price_means_unliquidatable_not_unknown():
    """FTX printed his liquidation price as "Infinity" (img113) and "N/A"
    (img007/032/059/060/123) — the BEST possible state for this trait, and the
    literal form of "I have unlimited collateral, you don't" (img125). A venue
    answering null is not a failed read, and collapsing the two would score his
    strongest evidence as no evidence."""
    state = {"assetPositions": [
        {"position": {"coin": "AXS", "szi": "-1", "positionValue": "10",
                      "entryPx": "47.699", "liquidationPx": None}}]}
    got = positions_from(state)[0]
    assert got["liq_distance"] == float("inf")
    verdict = check_liquidation_distance([got])
    assert verdict["verdict"] == CONSISTENT
    assert "unliquidatable" in verdict["detail"]


def test_a_missing_liquidation_field_is_still_unknown():
    """Key absent is a failed read; key present and null is an answer."""
    state = {"assetPositions": [
        {"position": {"coin": "A", "szi": "-1", "positionValue": "10",
                      "entryPx": "100"}}]}
    assert positions_from(state)[0]["liq_distance"] is None


def test_an_unliquidatable_position_never_counts_as_near():
    mixed = [{"coin": "A", "direction": "short", "notional": 1.0, "size": -1.0,
              "liq_distance": float("inf")} for _ in range(5)]
    assert check_liquidation_distance(mixed)["verdict"] == CONSISTENT
