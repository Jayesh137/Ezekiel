"""Families, bands, clusters and the calibration checks (spec §7)."""

import pytest

from src.casebook import model, score

A, B, C, D = ("0x" + c * 40 for c in "abcd")


def entry(kind, status="current", strength=None, **facts):
    return {"kind": kind, "status": status, "strength": strength, "facts": facts}


def test_the_prior_alone():
    s = score.score_evidence({})
    assert s["now"] == s["central"] == s["ceiling"] == model.PRIOR_LOG10_ODDS
    assert s["p_central"] == pytest.approx(1 / 1001, rel=1e-5)
    assert s["families"] == {} and s["model"] == model.MODEL_VERSION


def test_a_family_takes_its_strongest_item_never_a_sum():
    s = score.score_evidence({"direct_transfer": entry("direct_transfer"),
                              "circle_flow": entry("circle_flow"),
                              "two_way_flow": entry("two_way_flow")})
    assert s["families"]["money"]["central"] == 1.0
    assert s["central"] == pytest.approx(-2.0)


def test_a_second_family_in_a_category_counts_half():
    s = score.score_evidence({"two_way_flow": entry("two_way_flow"),
                              "private_deposit_address": entry("private_deposit_address")})
    assert s["central"] == pytest.approx(-3.0 + 1.5 + 0.5 * 1.0)


def test_families_in_different_categories_add():
    s = score.score_evidence({"two_way_flow": entry("two_way_flow"),
                              "shared_agent": entry("shared_agent")})
    assert s["central"] == pytest.approx(-3.0 + 3.0 + 1.0)


def test_evidence_against_moves_the_score_down():
    base = {"behavioural_vote": entry("behavioural_vote")}
    vetoed = {**base, "style_veto": entry("style_veto")}
    assert score.score_evidence(vetoed)["central"] < score.score_evidence(base)["central"]
    ruled = score.score_evidence({"two_way_flow": entry("two_way_flow"),
                                  "operator_not_him": entry("operator_not_him")})
    assert ruled["central"] == pytest.approx(-3.0 + 1.0 - 3.0)


def test_status_decides_which_band_an_item_reaches():
    s = score.score_evidence({"two_way_flow": entry("two_way_flow", "lapsed")})
    assert s["now"] == -3.0
    assert s["central"] == pytest.approx(-2.5) and s["ceiling"] == pytest.approx(-1.3)
    s = score.score_evidence({"two_way_flow": entry("two_way_flow", "historical")})
    assert s["now"] == s["central"] == -3.0 and s["ceiling"] == pytest.approx(-1.3)
    assert score.score_evidence({"two_way_flow": entry("two_way_flow", "invalidated")})["ceiling"] == -3.0
    assert score.score_evidence({"shared_agent": entry("shared_agent", "standing")})["now"] == pytest.approx(-1.0)


def test_family_clips():
    many = {f"k{i}": entry("subaccount_of_cluster") for i in range(3)}
    assert score.score_evidence(many)["families"]["control"]["ceiling"] == 5.0
    big = score.score_evidence({"study_tooling": entry("study_tooling", strength=1e6, verdict="for", lr=1e6)})
    assert big["families"]["tooling"]["central"] == 2.0


def test_the_study_supersedes_the_program_match():
    both = score.score_evidence({
        "study_tooling": entry("study_tooling", strength=0.05, verdict="against", lr=0.05),
        "execution_program": entry("execution_program", strength=0.8)})
    assert both["families"]["tooling"]["central"] == pytest.approx(-1.301, abs=1e-3)
    alone = score.score_evidence({"execution_program": entry("execution_program", strength=0.8)})
    assert alone["families"]["tooling"]["central"] == 1.3


def test_an_excluded_case_scores_the_prior():
    s = score.score_evidence({"shared_agent": entry("shared_agent")}, excluded=True)
    assert s["central"] == s["ceiling"] == -3.0


def test_probability_never_overflows():
    assert score.probability(-500.0) == 0.0
    assert score.probability(500.0) == 1.0


def case(address, evidence=None, links=None, known=None, excluded=None):
    return {"address": address, "evidence": evidence or {}, "links": links or {},
            "known": known, "excluded": excluded}


def test_clusters_join_operator_groups_but_never_his_known_wallets():
    cases = {A: case(A, {"two_way_flow": entry("two_way_flow")},
                     links={"operator_group": {"master": A, "subaccounts": [B]}}),
             B: case(B, {"shared_agent": entry("shared_agent")}, links={"subaccount_of": A}),
             C: case(C, links={"subaccount_of": D}),
             D: case(D, known="config:known_self")}
    ids = score.clusters(cases, {D})
    assert ids[A] == ids[B] == A and C not in ids and D not in ids
    scores = score.score_all(cases, {D})
    assert scores[A]["central"] == scores[B]["central"] == pytest.approx(-3.0 + 3.0 + 1.0)
    assert scores[A]["solo_central"] == pytest.approx(-2.0) and scores[A]["cluster_size"] == 2
    assert scores[C]["cluster"] is None and scores[C]["cluster_size"] == 1


def test_rank_key_orders_by_central_then_now_then_ceiling_then_address():
    a = {"central": -1.0, "now": -2.0, "ceiling": 0.0}
    b = {"central": -1.0, "now": -1.5, "ceiling": 0.0}
    ordered = sorted([("x", a), ("y", b)], key=lambda p: score.rank_key(p[1], p[0]))
    assert ordered[0][0] == "y"


def test_calibration_reports_recall_and_coherence():
    cases = {A: case(A, {"two_way_flow": entry("two_way_flow")}, known="config:known_self"),
             B: case(B, {"private_deposit_address": entry("private_deposit_address")}),
             C: case(C, {})}
    scores = score.score_all(cases, {A})
    cal = score.calibration(cases, scores)
    assert cal["recall"] == [{"address": A, "rank_among_unknown": 2, "central": -2.0,
                              "p_central": scores[A]["p_central"]}]
    assert cal["unknown_cases"] == 2
    assert cal["sum_p_central_unknown"] == pytest.approx(
        scores[B]["p_central"] + scores[C]["p_central"], abs=1e-6)
