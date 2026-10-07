"""Family verdicts, the study rank and transitions."""

from src.study import verdict


def t(status, lr=None):
    return {"judgement": {"status": status, "lr": lr}}


def make_tests(t1, against="none", t2="insufficient", t3="insufficient", against_lr=None, t2_lr=None):
    return {"T1": {**t(t1, 30.0 if t1 == "for" else None),
                   "against": {"status": against, "lr": against_lr}},
            "T2": t(t2, t2_lr), "T3": t(t3)}


def with_basis(tests, name, basis):
    tests[name]["judgement"]["basis"] = basis
    return tests


def test_family_verdicts():
    assert verdict.family_verdict(make_tests("for"), verdict.TOOLING) == {
        "verdict": "for", "lr": 30.0, "by": ["T1"], "basis": None}
    assert verdict.family_verdict(make_tests("neutral", "against", against_lr=0.1),
                                  verdict.TOOLING)["verdict"] == "against"
    assert verdict.family_verdict(make_tests("neutral", "against", "for", t2_lr=40.0),
                                  verdict.TOOLING)["verdict"] == "mixed"
    assert verdict.family_verdict(make_tests("uncalibrated"), verdict.TOOLING)["verdict"] == "uncalibrated"
    assert verdict.family_verdict(make_tests("insufficient"), verdict.TOOLING)["verdict"] == "insufficient"
    assert verdict.family_verdict(make_tests("neutral", t2="uncalibrated"),
                                  verdict.TOOLING)["verdict"] == "neutral"


def test_a_neutral_family_carries_its_strongest_calibrated_test():
    # spec §8.3: only an uncalibrated or insufficient family adds nothing to the rank.
    tests = make_tests("neutral", t2="neutral", t3="neutral", t2_lr=20.0)
    tests["T3"]["judgement"]["lr"] = 0.8
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got == {"verdict": "neutral", "lr": 20.0, "by": [], "basis": None}
    assert verdict.rank({"tooling": got}) == 1.301
    assert verdict.family_verdict(make_tests("neutral"), verdict.TOOLING)["lr"] is None


def test_a_mixed_family_carries_the_stronger_side():
    tests = make_tests("neutral", "against", "for", against_lr=0.1, t2_lr=40.0)
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got["verdict"] == "mixed" and got["lr"] == 40.0
    assert got["by"] == ["T2", "T1:against"]
    tests = make_tests("neutral", "against", "for", against_lr=0.001, t2_lr=40.0)
    assert verdict.family_verdict(tests, verdict.TOOLING)["lr"] == 0.001


def test_a_for_family_reports_the_basis_of_the_test_its_lr_comes_from():
    # "self" alone is the spec's self_only basis; "family" and "family+self" pass through.
    tests = with_basis(make_tests("for"), "T1", "self")
    assert verdict.family_verdict(tests, verdict.TOOLING)["basis"] == "self_only"
    for basis in ("family", "family+self"):
        tests = with_basis(make_tests("for"), "T1", basis)
        assert verdict.family_verdict(tests, verdict.TOOLING)["basis"] == basis
    # Two for-tests: the basis of the one with the largest LR.
    tests = with_basis(with_basis(make_tests("for", t2="for", t2_lr=90.0), "T1", "family"),
                       "T2", "self")
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got["lr"] == 90.0 and got["by"] == ["T1", "T2"] and got["basis"] == "self_only"
    tests = with_basis(with_basis(make_tests("for", t2="for", t2_lr=10.0), "T1", "family"),
                       "T2", "self")
    assert verdict.family_verdict(tests, verdict.TOOLING)["basis"] == "family"


def test_an_against_family_is_judged_on_family_pairs():
    got = verdict.family_verdict(make_tests("neutral", "against", against_lr=0.1), verdict.TOOLING)
    assert got["verdict"] == "against" and got["lr"] == 0.1 and got["basis"] == "family"


def test_a_neutral_family_reports_the_basis_of_its_strongest_neutral_test():
    tests = make_tests("neutral", t2="neutral", t3="neutral", t2_lr=20.0)
    tests["T3"]["judgement"]["lr"] = 0.8
    with_basis(with_basis(tests, "T2", "family+self"), "T3", "self")
    assert verdict.family_verdict(tests, verdict.TOOLING)["basis"] == "family+self"
    # An LR well under 1 is as strong as one well over it: 0.01 is further from 1 than 20.
    tests["T3"]["judgement"]["lr"] = 0.01
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got["lr"] == 0.01 and got["basis"] == "self_only"


def test_a_mixed_family_reports_the_basis_of_the_stronger_side():
    tests = with_basis(make_tests("neutral", "against", "for", against_lr=0.1, t2_lr=40.0),
                       "T2", "self")
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got["lr"] == 40.0 and got["basis"] == "self_only"
    # The against side has no basis of its own on the judgement: it is judged on family pairs.
    tests = with_basis(make_tests("neutral", "against", "for", against_lr=0.001, t2_lr=40.0),
                       "T2", "self")
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got["lr"] == 0.001 and got["basis"] == "family"


def test_equal_strongest_lrs_report_the_first_tests_basis():
    tests = make_tests("neutral", t2="neutral", t3="neutral", t2_lr=20.0)
    tests["T3"]["judgement"]["lr"] = 20.0
    with_basis(with_basis(tests, "T2", "family"), "T3", "self")
    assert verdict.family_verdict(tests, verdict.TOOLING)["basis"] == "family"
    tests = with_basis(with_basis(make_tests("for", t2="for", t2_lr=30.0), "T1", "family+self"),
                       "T2", "self")
    assert verdict.family_verdict(tests, verdict.TOOLING)["basis"] == "family+self"


def test_a_family_is_judged_on_the_tests_it_names_and_only_t1_can_be_against():
    tests = make_tests("neutral", "against", "for", against_lr=0.1, t2_lr=40.0)
    got = verdict.family_verdict(tests, ("T2", "T3"))
    assert got["verdict"] == "for" and got["by"] == ["T2"] and got["lr"] == 40.0
    assert verdict.family_verdict(tests, ("T9",))["verdict"] == "insufficient"


def test_an_uncalibrated_or_insufficient_family_has_no_basis_and_no_lr():
    for status in ("uncalibrated", "insufficient"):
        got = verdict.family_verdict(with_basis(make_tests(status), "T1", "family"), verdict.TOOLING)
        assert got["verdict"] == status and got["basis"] is None and got["lr"] is None


def test_rank_clips_each_family():
    assert verdict.rank({"tooling": {"lr": 1e6}}) == 2.0
    assert verdict.rank({"tooling": {"lr": 0.001}}) == -2.0
    assert verdict.rank({"tooling": {"lr": None}}) == 0.0


def test_rank_sums_families_and_skips_a_family_with_no_lr():
    assert verdict.rank({"tooling": {"lr": 10.0}, "timing": {"lr": 0.1}}) == 0.0
    assert verdict.rank({"tooling": {"lr": 100.0}, "timing": {"lr": 1e9},
                         "lifecycle": {"lr": None}}) == 4.0


def test_rank_sums_tooling_and_timing_only():
    # Spec 8.3: a lifecycle or strategy family that ever carries an lr adds nothing.
    assert verdict.rank({"tooling": {"lr": 100.0}, "strategy": {"lr": 1e6}}) == 2.0
    assert verdict.rank({"tooling": {"lr": 100.0}, "lifecycle": {"lr": 0.001}}) == 2.0
    assert verdict.rank({"lifecycle": {"lr": 1e6}, "strategy": {"lr": 1e6}}) == 0.0
    assert verdict.rank({}) == 0.0


def test_newly_reports_only_transitions():
    def row(wallet, v):
        return {"wallet": wallet, "families": {"tooling": {"verdict": v}}}
    before = [row("a", "for"), row("b", "neutral")]
    after = [row("a", "for"), row("b", "for"), row("c", "for"), row("d", "neutral")]
    assert [r["wallet"] for r in verdict.newly(before, after, "tooling")] == ["b", "c"]
