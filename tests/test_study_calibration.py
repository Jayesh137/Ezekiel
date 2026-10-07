"""The pre-registered bars (spec §8.2). Nothing here may be tuned to a result."""

import pytest

from src.study import calibration as cal

SELF_T2 = [0.03, 0.09, 0.19, 0.22, 0.25, 0.04]  # his monthly rhythm distances, median 0.14


def test_upper_bound_matches_clopper_pearson():
    assert cal.upper_bound(0, 148) > 0.02 >= cal.upper_bound(0, 149)
    assert cal.upper_bound(1, 200) == pytest.approx(0.0235, abs=5e-4)
    assert cal.upper_bound(0, 0) == 1.0 and cal.upper_bound(5, 5) == 1.0


def test_continuous_needs_two_hundred_strangers_and_a_same_operator_yardstick():
    assert cal.judge_continuous(0.1, strangers=[1.0] * 199, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "uncalibrated"
    assert cal.judge_continuous(0.1, strangers=[1.0] * 300, same_op={"self": SELF_T2[:5]},
                                higher_is_better=False)["status"] == "uncalibrated"
    assert cal.judge_continuous(None, strangers=[1.0] * 300, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "insufficient"


def test_for_is_judged_at_the_looser_of_the_candidate_and_the_same_operator_median():
    got = cal.judge_continuous(0.1, strangers=[0.5 + i / 1000 for i in range(300)],
                               same_op={"self": SELF_T2}, higher_is_better=False)
    assert got["status"] == "for" and got["level"] == 0.14 and got["basis"] == "self"
    assert got["same_op_rate"] >= 0.5 and got["stranger_k"] == 0
    # A closer match never fares worse than a looser one.
    assert cal.judge_continuous(0.01, strangers=[0.5] * 300, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "for"


def test_strangers_reaching_the_level_make_it_neutral():
    strangers = [0.1] * 10 + [0.9] * 290
    assert cal.judge_continuous(0.1, strangers=strangers, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "neutral"


def test_higher_is_better_mirrors():
    got = cal.judge_continuous(0.95, strangers=[0.2] * 300, same_op={"family": [0.9] * 40},
                               higher_is_better=True)
    assert got["status"] == "for" and got["level"] == 0.9


def test_binary_for_neutral_and_uncalibrated():
    fam = {"family": (41, 41)}
    assert cal.judge_binary(True, stranger_k=0, stranger_n=200, same_op=fam)["status"] == "for"
    assert cal.judge_binary(True, stranger_k=3, stranger_n=200, same_op=fam)["status"] == "neutral"
    assert cal.judge_binary(False, stranger_k=0, stranger_n=200, same_op=fam)["status"] == "neutral"
    assert cal.judge_binary(True, stranger_k=0, stranger_n=200,
                            same_op={"family": (20, 20), "self": (3, 3)})["status"] == "uncalibrated"
    assert cal.judge_binary(None, stranger_k=0, stranger_n=200, same_op=fam)["status"] == "insufficient"


def test_against_needs_forty_family_pairs_that_rarely_disagree():
    got = cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)},
                            stranger_trait_rate={"client_ids": 0.54})
    assert got["status"] == "against" and got["lr"] == pytest.approx(0.0705 / 0.54, rel=0.02)
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (6, 41)},
                             stranger_trait_rate={"client_ids": 0.54})["status"] == "neutral"
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 20)},
                             stranger_trait_rate={})["status"] == "uncalibrated"
    assert cal.judge_against([], family_mismatch={}, stranger_trait_rate={})["status"] == "none"
