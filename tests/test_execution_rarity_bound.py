"""The comparability bound: the third measured way an execution match is rare.

The exact and structure paths need a p99 from 20 COMPARABLE controls. After 252
leaderboard reads only 2 were comparable (2026-10-12), so that distribution would
take years to form — while the fact it could not form IS the rarity. A stranger can
reproduce his clip table only if it is comparable, so P(match) <= P(comparable), and
P(comparable) is a binomial proportion with a Wilson ceiling the census measures.
Rule 4 still holds: no bar moved, a different statistic was measured, and the bound
assumes every comparable stranger would match in full.
"""

import pytest

from src import execution_program as ep


def _census(attempted, comparable, ratios=None):
    return ep.summarise_census(ratios if ratios is not None else [0.0] * comparable, [],
                               attempted=attempted)


def test_wilson_upper_is_conservative_and_narrows_with_trials():
    assert ep.wilson_upper(0, 200) > 0                      # zero successes is never "never"
    assert ep.wilson_upper(2, 249) == pytest.approx(0.0288, abs=1e-3)
    assert ep.wilson_upper(2, 2490) < ep.wilson_upper(2, 249)
    assert ep.wilson_upper(5, 5) == 1.0
    assert ep.wilson_upper(1, 0) is None


def test_the_census_reports_the_comparability_ceiling_once_enough_were_read():
    census = _census(attempted=252, comparable=2)
    assert census["comparable"] == 2
    assert census["comparable_rate"] == pytest.approx(0.0079, abs=1e-3)
    assert census["comparable_upper"] == pytest.approx(0.0285, abs=1e-3)


def test_too_few_reads_leave_the_ceiling_unmeasured_not_zero():
    census = _census(attempted=50, comparable=0)
    assert census["comparable_upper"] is None
    strong = {"clip_match_ratio": 1.0, "clips_compared": 5, "clips_matched": 5}
    assert ep.is_discriminating(strong, census) is False


def test_a_strong_exact_match_votes_through_the_bound_while_the_p99_cannot_form():
    census = _census(attempted=252, comparable=2)
    assert census["population"] < ep.MIN_CENSUS_POPULATION   # the old paths are closed
    strong = {"clip_match_ratio": 1.0, "clips_compared": 4, "clips_matched": 4}
    assert ep.is_discriminating(strong, census) is True
    assert ep.rarity_path(strong, census) == "bounded"


def test_being_comparable_alone_never_clears_the_bound():
    census = _census(attempted=252, comparable=2)
    weak = {"clip_match_ratio": 0.5, "clips_compared": 4, "clips_matched": 2}
    assert ep.is_discriminating(weak, census) is False
    few = {"clip_match_ratio": 1.0, "clips_compared": 2, "clips_matched": 2}
    assert ep.is_discriminating(few, census) is False          # under MIN_VOTE_CLIPS
    assert ep.rarity_path(weak, census) is None


def test_a_common_comparability_rate_keeps_the_bound_closed():
    # 30 of 250 comparable, and they all reproduce his sizes: a fixed-clip slicer at
    # his sizes is ordinary in this population, so neither path may open.
    census = _census(attempted=250, comparable=30, ratios=[1.0] * 30)
    assert census["comparable_upper"] > ep.MAX_FALSE_MATCH_RATE
    strong = {"clip_match_ratio": 1.0, "clips_compared": 5, "clips_matched": 5}
    assert ep.bounded_rare(strong, census) is False
    assert ep.is_discriminating(strong, census) is False


def test_the_rescaled_structure_votes_through_the_bound_over_enough_coins():
    census = _census(attempted=252, comparable=2)
    rescaled = {"clip_match_ratio": 0.0, "clips_compared": 4, "clips_matched": 0,
                "notional_structure_rho": 1.0, "notional_coins_compared": 4}
    assert ep.is_discriminating(rescaled, census) is True
    three = {**rescaled, "notional_coins_compared": 3}     # 1 in 6 by chance: not enough
    assert ep.is_discriminating(three, census) is False


def test_the_p99_paths_still_name_themselves_when_they_apply():
    census = {"population": 40, "ratio_p99": 0.3, "min_clips": 3, "rho_population": 0,
              "comparable_upper": 0.02, "attempted": 300}
    match = {"clip_match_ratio": 1.0, "clips_compared": 5, "clips_matched": 5}
    assert ep.rarity_path(match, census) == "exact"


def test_compare_names_the_coins_that_matched():
    target = {"clip_table": {"ZEC": {"size": 1}, "NEAR": {"size": 250}, "BTC": {"size": 0.1}}}
    cand = {"clip_table": {"ZEC": {"size": 1}, "NEAR": {"size": 250}, "BTC": {"size": 0.5}}}
    result = ep.compare(target, cand)
    assert result["clips_matched_coins"] == ["NEAR", "ZEC"]
    assert result["clips_matched"] == 2


def test_merge_takes_the_largest_ceiling_never_the_kindest():
    leaderboard = _census(attempted=252, comparable=2)
    tape = _census(attempted=600, comparable=18)            # takers of his markets
    merged = ep.merge_census(leaderboard, tape)
    assert merged["comparable_upper"] == tape["comparable_upper"]
    assert merged["bound_sources"] == 2
    assert merged["population"] == leaderboard["population"]   # p99 stays the leaderboard's


def test_merge_ignores_an_unmeasured_population_and_survives_missing_files():
    leaderboard = _census(attempted=252, comparable=2)
    assert ep.merge_census(leaderboard, None)["comparable_upper"] == leaderboard["comparable_upper"]
    young_tape = _census(attempted=40, comparable=1)        # ceiling None: not counted
    assert ep.merge_census(leaderboard, young_tape)["bound_sources"] == 1
    assert ep.merge_census(None, None) is None
    only_tape = ep.merge_census(None, _census(attempted=600, comparable=3))
    assert only_tape["comparable_upper"] is not None
    assert only_tape["population"] == 0


def test_a_merged_bound_above_the_cap_closes_the_vote_even_if_one_source_is_rare():
    leaderboard = _census(attempted=252, comparable=2)      # 2.85%
    tape = _census(attempted=300, comparable=30)            # ~13%
    merged = ep.merge_census(leaderboard, tape)
    strong = {"clip_match_ratio": 1.0, "clips_compared": 5, "clips_matched": 5}
    assert ep.is_discriminating(strong, merged) is False
