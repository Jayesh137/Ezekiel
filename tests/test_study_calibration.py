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


# A1: against must be evidence against (LR < 1)
def test_against_lr_under_one():
    # rate 0.54 → LR = 0.0705 / 0.54 ≈ 0.1305 → against
    got = cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)},
                            stranger_trait_rate={"client_ids": 0.54})
    assert got["status"] == "against"
    # rate 0.05 → LR = 0.0705 / 0.05 = 1.41 → neutral
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)},
                             stranger_trait_rate={"client_ids": 0.05})["status"] == "neutral"


def test_against_handles_zero_rate():
    # rate 0.0 → division by zero, treat as no evidence, neutral
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)},
                             stranger_trait_rate={"client_ids": 0.0})["status"] == "neutral"


def test_against_missing_rate_is_uncalibrated():
    # rate key missing → uncalibrated
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)},
                             stranger_trait_rate={})["status"] == "uncalibrated"


def test_against_filters_traits_by_lr():
    # Two traits: one with LR < 1 (rate 0.54), one with LR >= 1 (rate 0.05)
    # Only the first should be in "against"
    got = cal.judge_against(["client_ids", "cancels"],
                            family_mismatch={"client_ids": (0, 41), "cancels": (0, 41)},
                            stranger_trait_rate={"client_ids": 0.54, "cancels": 0.05})
    assert got["status"] == "against"
    assert got["traits"] == ["client_ids"]


# A2: NaN handling
def test_continuous_nan_input_insufficient():
    # x is NaN → insufficient
    assert cal.judge_continuous(float("nan"), strangers=[1.0] * 300, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "insufficient"
    # higher is better, x is NaN → insufficient
    assert cal.judge_continuous(float("nan"), strangers=[1.0] * 300, same_op={"self": SELF_T2},
                                higher_is_better=True)["status"] == "insufficient"


def test_continuous_nan_in_strangers():
    # 150 good strangers + 100 NaN → uncalibrated (only 150 valid strangers)
    got = cal.judge_continuous(0.1, strangers=[1.0]*150 + [float("nan")]*100,
                               same_op={"self": SELF_T2}, higher_is_better=False)
    assert got["status"] == "uncalibrated" and got["strangers"] == 150
    # 250 good strangers + 100 NaN → for (250 valid strangers)
    got = cal.judge_continuous(0.1, strangers=[1.0]*250 + [float("nan")]*100,
                               same_op={"self": SELF_T2}, higher_is_better=False)
    assert got["status"] == "for" and got["stranger_n"] == 250


def test_continuous_nan_in_same_op():
    # same_op list with NaNs: only 3 usable values → uncalibrated
    same_op_with_nan = [float("nan"), float("nan"), float("nan"), 0.22, 0.25, 0.04]
    got = cal.judge_continuous(0.1, strangers=[1.0]*300, same_op={"self": same_op_with_nan},
                               higher_is_better=False)
    assert got["status"] == "uncalibrated"


# A3: Tests that pin the bars
def test_constants_pinned():
    assert cal.MIN_STRANGERS == 200
    assert cal.MIN_REFERENCES == 150
    assert cal.MIN_SAME_OP == {"family": 40, "self": 6}
    assert cal.FOR_UPPER == 0.02
    assert cal.AGAINST_MISMATCH == 0.10
    assert cal.CONFIDENCE == 0.95


def test_exactly_200_strangers_calibrate():
    got = cal.judge_continuous(0.1, strangers=[1.0]*200, same_op={"self": SELF_T2},
                               higher_is_better=False)
    assert got["status"] == "for"


def test_candidates_level_counts_when_looser():
    # Lower is better, x=0.30 (worse than median 0.14), strangers [0.25]*5 + [1.0]*295
    # 5 strangers reach 0.30, so neutral
    got = cal.judge_continuous(0.30, strangers=[0.25]*5 + [1.0]*295,
                               same_op={"self": SELF_T2}, higher_is_better=False)
    assert got["status"] == "neutral" and got["stranger_k"] == 5
    # x=0.10 (better than median 0.14), no strangers reach 0.10, so for
    got = cal.judge_continuous(0.10, strangers=[0.25]*5 + [1.0]*295,
                               same_op={"self": SELF_T2}, higher_is_better=False)
    assert got["status"] == "for" and got["stranger_k"] == 0


def test_two_bases_smaller_rate():
    # Two bases: family rate at level is 40/40=1.0, self rate at level is 1/6≈0.167
    # Should use the smaller (self)
    got = cal.judge_continuous(0.05, strangers=[1.0]*300,
                               same_op={"family": [0.1]*40, "self": SELF_T2},
                               higher_is_better=False)
    assert got["basis"] == "family+self"
    # The level is max(0.05, 0.1, 0.14) = 0.14
    # family values: all 40 at 0.1 ≤ 0.14, so rate 40/40 = 1.0
    # self values: 0.03, 0.04, 0.09 ≤ 0.14 → 3 of 6
    # min rate should be 3/6 = 0.5
    assert got["same_op_rate"] == 0.5


def test_lr_assertion():
    got = cal.judge_continuous(0.1, strangers=[0.5 + i / 1000 for i in range(300)],
                               same_op={"self": SELF_T2}, higher_is_better=False)
    assert got["status"] == "for"
    # LR should equal same_op_rate / stranger_upper (computed from unrounded values)
    assert got["lr"] == pytest.approx(got["same_op_rate"] / got["stranger_upper"], rel=0.01)


def test_binary_needs_fifty_percent_agreement():
    # family rate = 19/40 = 0.475 < 0.5 → neutral
    got = cal.judge_binary(True, stranger_k=0, stranger_n=200,
                           same_op={"family": (19, 40)})
    assert got["status"] == "neutral"


def test_upper_bound_at_additional_points():
    assert cal.upper_bound(5, 300) == pytest.approx(0.0347, abs=2e-4)
    assert cal.upper_bound(10, 1000) == pytest.approx(0.0169, abs=2e-4)
