"""The execution-program vector: recognising the trader's slicer across wallets.

The target trades through a script (hyperliquid-python-sdk `market_open`): taker
IOC orders whose limit sits a fixed slippage through the book, repeated at one
round base size per coin, fired every ~1.7s in runs of tens to thousands. The
discriminating fact is his CLIP TABLE — the exact base size he uses per coin
(NEAR 250, ZEC 1, SILVER 20, BTC 0.1). Two accounts reproducing that table are
running the same program. It is a BEHAVIOUR signal (a shared bot/frontend is the
confounder, rule 9), so it corroborates and does not confirm alone, and a missing
measurement is None, never 0.0 (rule 6).
"""

from src import execution_program as ep


def _fill(oid, t, coin, side, sz, px, crossed=True):
    return {"oid": oid, "time": t, "coin": coin, "side": side, "sz": str(sz),
            "px": str(px), "crossed": crossed, "tid": oid * 1000 + int(t) % 1000}


def _program(coin, side, clip, n, px=4.0, start=0, gap_ms=1750, oid0=1):
    return [_fill(oid0 + i, start + i * gap_ms, coin, side, clip, px) for i in range(n)]


# --- order reconstruction -------------------------------------------------

def test_partial_fills_of_one_order_are_one_order_at_the_summed_size():
    fills = [_fill(7, 1000, "BTC", "A", 0.06, 100000),
             _fill(7, 1000, "BTC", "A", 0.04, 100000)]
    orders = ep.reconstruct_orders(fills)
    assert len(orders) == 1
    assert orders[0]["base_size"] == 0.1
    assert orders[0]["taker"] is True


def test_a_maker_fill_makes_its_order_not_taker():
    orders = ep.reconstruct_orders([_fill(1, 0, "ETH", "A", 2, 3000, crossed=False)])
    assert orders[0]["taker"] is False


# --- clip table -----------------------------------------------------------

def test_clip_table_reports_the_repeated_base_size_per_coin():
    table = ep.clip_table(ep.reconstruct_orders(_program("NEAR", "A", 250, 40)))
    assert table["NEAR"]["size"] == 250
    assert table["NEAR"]["share"] == 1.0
    assert table["NEAR"]["count"] == 40


def test_scattered_sizes_do_not_form_a_clip():
    fills = [_fill(i, i * 1000, "DOGE", "A", 100 * (i + 1), 0.1) for i in range(40)]
    assert "DOGE" not in ep.clip_table(ep.reconstruct_orders(fills))


def test_a_coin_with_too_few_orders_forms_no_clip():
    table = ep.clip_table(ep.reconstruct_orders(_program("ZEC", "A", 1, 3, px=300)))
    assert table == {}


# --- signature ------------------------------------------------------------

def test_signature_without_orders_leaves_offset_unknown_not_zero():
    sig = ep.signature(_program("NEAR", "A", 250, 40))
    assert sig["taker_share"] == 1.0
    assert sig["ioc_5pct_share"] is None  # needs the order limit price, not fills
    assert sig["clip_table"]["NEAR"]["size"] == 250


def test_empty_input_is_insufficient_never_a_zero_score():
    sig = ep.signature([])
    assert sig["taker_share"] is None
    assert sig["clip_table"] == {}
    assert sig["program_runs"] == 0


def test_signature_with_orders_measures_the_five_percent_ioc_offset():
    fills = _program("NEAR", "A", 250, 40, px=4.0)
    # A sell order priced 5% below the fill = market_open default slippage.
    orders = [{"oid": 1 + i, "order": {"oid": 1 + i, "coin": "NEAR", "side": "A",
               "limitPx": str(4.0 * 0.95), "tif": "Ioc", "orderType": "Limit",
               "cloid": None, "reduceOnly": False}, "status": "filled"} for i in range(40)]
    sig = ep.signature(fills, orders)
    assert sig["ioc_5pct_share"] == 1.0
    assert sig["cloid_share"] == 0.0


# --- comparison -----------------------------------------------------------

def _sig(*programs):
    fills = [f for p in programs for f in p]
    return ep.signature(fills)


def test_identical_clip_tables_match_strongly():
    a = _sig(_program("NEAR", "A", 250, 40), _program("ZEC", "A", 1, 40, oid0=100))
    b = _sig(_program("NEAR", "A", 250, 40, oid0=200), _program("ZEC", "A", 1, 40, oid0=300))
    result = ep.compare(a, b)
    assert result["status"] == "measured"
    assert result["clip_match_ratio"] == 1.0
    assert result["clips_matched"] == 2


def test_same_coins_different_clip_sizes_do_not_match():
    a = _sig(_program("NEAR", "A", 250, 40), _program("ZEC", "A", 1, 40, oid0=100))
    b = _sig(_program("NEAR", "A", 999, 40, oid0=200), _program("ZEC", "A", 7, 40, oid0=300))
    result = ep.compare(a, b)
    assert result["status"] == "measured"
    assert result["clip_match_ratio"] == 0.0


def test_too_few_shared_coins_is_insufficient_not_a_low_score():
    a = _sig(_program("NEAR", "A", 250, 40))
    b = _sig(_program("ZEC", "A", 1, 40, oid0=200))
    result = ep.compare(a, b)
    assert result["status"] == "insufficient_data"
    assert result["clip_match_ratio"] is None
    assert result["promotable"] is False


# --- ranking and the census gate (rules 4 & 9) ----------------------------

def _clips(*coins_sizes):
    return {c: {"size": s, "share": 1.0, "count": 40} for c, s in coins_sizes}


def test_rank_matches_orders_by_strength_and_keeps_only_measured():
    target = {"clip_table": _clips(("NEAR", 250), ("ZEC", 1), ("BTC", 0.1)), "program_runs": 5}
    candidates = [
        {"wallet": "0xhit", "signature": {"clip_table": _clips(("NEAR", 250), ("ZEC", 1), ("BTC", 0.1)), "program_runs": 5}},
        {"wallet": "0xmiss", "signature": {"clip_table": _clips(("NEAR", 9), ("ZEC", 9), ("BTC", 9)), "program_runs": 5}},
        {"wallet": "0xthin", "signature": {"clip_table": _clips(("NEAR", 250)), "program_runs": 0}},
    ]
    ranked = ep.rank_matches(target, candidates)
    assert [r["wallet"] for r in ranked] == ["0xhit", "0xmiss"]  # 0xthin insufficient, dropped
    assert ranked[0]["clip_match_ratio"] == 1.0


def test_no_census_means_no_vote_even_on_a_perfect_match():
    match = {"clip_match_ratio": 1.0, "clips_compared": 5, "clips_matched": 5}
    assert ep.is_discriminating(match, census=None) is False


def test_a_common_ratio_in_the_population_does_not_vote():
    # Half the population reproduces 60% of his clips, so 0.6 is not rare.
    census = {"population": 200, "ratio_p99": 0.6, "min_clips": 3}
    assert ep.is_discriminating({"clip_match_ratio": 0.6, "clips_compared": 5, "clips_matched": 3}, census) is False


def test_a_rare_high_ratio_over_enough_clips_is_discriminating():
    census = {"population": 200, "ratio_p99": 0.34, "min_clips": 3}
    match = {"clip_match_ratio": 1.0, "clips_compared": 6, "clips_matched": 6}
    assert ep.is_discriminating(match, census) is True


def test_a_rare_ratio_on_too_few_clips_is_not_trusted():
    census = {"population": 200, "ratio_p99": 0.34, "min_clips": 3}
    match = {"clip_match_ratio": 1.0, "clips_compared": 2, "clips_matched": 2}
    assert ep.is_discriminating(match, census) is False


# --- census summary -------------------------------------------------------

def test_census_summary_reports_the_population_and_p99():
    ratios = [0.0] * 90 + [0.2] * 9 + [1.0]  # one outlier reproduces everything
    census = ep.summarise_census(ratios)
    assert census["population"] == 100
    assert census["ratio_max"] == 1.0
    assert 0.2 <= census["ratio_p99"] <= 1.0
    assert census["min_clips"] == ep.MIN_VOTE_CLIPS


def test_an_empty_census_has_no_threshold_so_nothing_votes():
    census = ep.summarise_census([])
    assert census["population"] == 0
    assert census["ratio_p99"] is None
    assert ep.is_discriminating({"clip_match_ratio": 1.0, "clips_compared": 9, "clips_matched": 9}, census) is False


# --- roster integration ---------------------------------------------------

def test_execution_program_is_a_behaviour_category():
    from src.evidence import VECTOR_CATEGORY
    assert VECTOR_CATEGORY["execution_program"] == "behaviour"


def test_execution_plus_a_financial_vector_can_reach_probable():
    from src.evidence import aggregate_evidence, vector_observations
    from src.roster import assign_tier
    vectors = {"execution_program", "transfer"}
    summary = aggregate_evidence(vector_observations(vectors))
    assert assign_tier(vectors, 0.0, False, False, summary) == "PROBABLE"


def test_execution_alone_is_only_possible():
    from src.evidence import aggregate_evidence, vector_observations
    from src.roster import assign_tier
    vectors = {"execution_program"}
    summary = aggregate_evidence(vector_observations(vectors))
    assert assign_tier(vectors, 0.0, False, False, summary) == "POSSIBLE"


def test_only_discriminating_matches_vote():
    report = {"matches": [
        {"wallet": "0xVOTE", "discriminating": True, "clip_match_ratio": 1.0},
        {"wallet": "0xnope", "discriminating": False, "clip_match_ratio": 0.5}]}
    voting = ep.voting_wallets(report)
    assert set(voting) == {"0xvote"}


# --- scale-invariant notional structure (survives clip rescaling) ---------

def _multi(px_by_coin, clips, n=10, oid0=1):
    fills, oid = [], oid0
    for coin, clip in clips.items():
        for i in range(n):
            fills.append(_fill(oid, i * 1750, coin, "A", clip, px_by_coin[coin]))
            oid += 1
    return fills


def test_signature_exposes_per_coin_clip_notionals():
    sig = ep.signature(_program("NEAR", "A", 250, 40, px=4.0))
    assert abs(sig["clip_notionals"]["NEAR"] - 1000) < 1


def test_a_uniform_rescale_breaks_exact_size_but_keeps_rank_structure():
    px = {"NEAR": 4.0, "ZEC": 300.0, "BTC": 100000.0}
    ref = ep.signature(_multi(px, {"NEAR": 250, "ZEC": 1, "BTC": 0.1}))
    mig = ep.signature(_multi(px, {"NEAR": 500, "ZEC": 2, "BTC": 0.2}, oid0=9000))  # all 2x
    result = ep.compare(ref, mig)
    assert result["clip_match_ratio"] == 0.0                 # absolute sizes differ
    assert result["notional_structure_rho"] > 0.99        # structure preserved
    assert result["notional_coins_compared"] == 3


def test_a_different_structure_does_not_match_on_notionals():
    px = {"NEAR": 4.0, "ZEC": 300.0, "BTC": 100000.0}
    ref = ep.signature(_multi(px, {"NEAR": 250, "ZEC": 1, "BTC": 0.1}))
    # candidate trades the same coins but with a totally different size structure
    other = ep.signature(_multi(px, {"NEAR": 1, "ZEC": 1000, "BTC": 0.001}, oid0=9000))
    result = ep.compare(ref, other)
    assert result["notional_structure_rho"] < 0.9


def test_a_rescaled_match_is_discriminating_when_the_census_measures_it_rare():
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3, "rho_p99": 0.9, "rho_population": 300}
    match = {"clip_match_ratio": 0.0, "clips_compared": 3, "clips_matched": 0,
             "notional_structure_rho": 0.995, "notional_coins_compared": 4}
    assert ep.is_discriminating(match, census) is True


def test_a_common_rho_does_not_vote():
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3, "rho_p99": 0.997}
    match = {"clip_match_ratio": 0.0, "clips_compared": 3, "clips_matched": 0,
             "notional_structure_rho": 0.99, "notional_coins_compared": 4}
    assert ep.is_discriminating(match, census) is False


# --- the census must be large enough to trust (rule 4) ---------------------

def test_a_threshold_from_a_tiny_population_does_not_vote():
    # population 1 gives a meaningless p99; a perfect match must still not vote.
    census = {"population": 1, "ratio_p99": 0.0, "min_clips": 3}
    match = {"clip_match_ratio": 1.0, "clips_compared": 6, "clips_matched": 6}
    assert ep.is_discriminating(match, census) is False


def test_the_structure_path_also_needs_a_large_enough_rho_population():
    census = {"population": 300, "ratio_p99": 0.34, "rho_p99": 0.9, "rho_population": 2, "min_clips": 3}
    match = {"clip_match_ratio": 0.0, "clips_compared": 3, "clips_matched": 0,
             "notional_structure_rho": 0.99, "notional_coins_compared": 4}
    assert ep.is_discriminating(match, census) is False


def test_a_large_population_lets_a_rare_match_vote():
    census = {"population": 50, "ratio_p99": 0.34, "min_clips": 3}
    match = {"clip_match_ratio": 1.0, "clips_compared": 6, "clips_matched": 6}
    assert ep.is_discriminating(match, census) is True


def test_reconstructed_orders_carry_their_oid():
    fills = [{"coin": "BTC", "side": "B", "sz": "1", "px": "10", "time": 1, "oid": 7, "tid": 1,
              "crossed": True},
             {"coin": "BTC", "side": "B", "sz": "1", "px": "10", "time": 1, "oid": 7, "tid": 2,
              "crossed": True}]
    [order] = ep.reconstruct_orders(fills)
    assert order["oid"] == 7 and order["base_size"] == 2.0
