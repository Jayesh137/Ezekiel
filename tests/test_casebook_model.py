"""The likelihood table is complete, ordered and self-describing (spec 2026-10-08 §7.2)."""

import math

import pytest

from src.casebook import model

FOR_FACTS = {"verdict": "for", "lr": 50.0}


def test_every_kind_has_a_family_a_label_a_basis_and_a_reason():
    for kind, spec in model.KINDS.items():
        assert spec["label"] and spec["why"], kind
        assert spec["basis"] in ("measured", "estimated", "assumed", "operator", "context"), kind
        if kind in model.CONTEXT_KINDS:
            assert model.band(kind, 1.0, FOR_FACTS) == (0.0, 0.0, 0.0)
            assert spec["family"] == "context"
        else:
            assert spec["family"] in model.FAMILY_CATEGORY, kind


@pytest.mark.parametrize("kind", [k for k in model.KINDS if k not in model.CONTEXT_KINDS])
def test_bands_are_ordered_low_mid_high(kind):
    facts = {"verdict": "same_hand"} if kind == "comovement" else FOR_FACTS
    low, mid, high = model.band(kind, 0.8, facts)
    assert low <= mid <= high


def test_against_kinds_stay_below_zero():
    assert model.band("style_veto")[2] < 0
    assert model.band("operator_not_him")[2] < 0
    assert model.band("comovement", None, {"verdict": "copier"})[1] < 0
    low, mid, high = model.band("study_tooling", 0.05, {"verdict": "against", "lr": 0.05})
    assert low == mid == pytest.approx(math.log10(0.05), abs=1e-4)
    assert mid < high < 0


def test_scaled_bands_follow_their_strength_and_need_a_reading():
    weak = model.band("amount_correlation", 0.2, {})
    strong = model.band("amount_correlation", 0.99, {})
    assert strong[1] > weak[1] > 0
    assert model.band("dormancy_handoff", None, {}) == (0.0, 0.0, 0.0)   # rule 6
    assert model.band("amount_correlation", float("nan"), {}) == (0.0, 0.0, 0.0)
    assert model.band("dormancy_handoff", 7.0, {}) == model.band("dormancy_handoff", 1.0, {})


def test_a_study_verdict_that_judged_nothing_weighs_nothing():
    for verdict in ("neutral", "uncalibrated", "insufficient", None):
        assert model.band("study_tooling", 30.0, {"verdict": verdict, "lr": 30.0}) == (0.0, 0.0, 0.0)
    assert model.band("study_tooling", None, {"verdict": "for", "lr": None}) == (0.0, 0.0, 0.0)
    assert model.band("study_tooling", 0.0, {"verdict": "for", "lr": 0.0}) == (0.0, 0.0, 0.0)


def test_the_measured_study_band_is_the_studys_own_ratio():
    low, mid, high = model.band("study_tooling", 74.0, {"verdict": "for", "lr": 74.0})
    assert low == mid == pytest.approx(math.log10(74), abs=1e-4)
    assert high == pytest.approx(mid + 0.3, abs=1e-4)
    # A mixed family carries its strongest ratio, either side of one.
    assert model.band("study_tooling", 0.1, {"verdict": "mixed", "lr": 0.1})[1] < 0


def test_unknown_kinds_weigh_nothing():
    assert model.band("no_such_kind", 1.0, {}) == (0.0, 0.0, 0.0)


def test_describe_carries_the_whole_table_for_a_reader_in_a_year():
    table = model.describe()
    assert table["version"] == model.MODEL_VERSION
    assert table["prior_log10_odds"] == -3.0
    assert set(table["kinds"]) == set(model.KINDS)
    assert table["kinds"]["amount_correlation"]["band"].startswith("confidence")
    assert table["kinds"]["direct_transfer"]["band"] == [0.3, 0.7, 1.3]
    assert table["status_weights"]["lapsed"] == [0.0, 0.5, 1.0]
