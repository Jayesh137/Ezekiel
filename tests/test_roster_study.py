"""The roster reads the candidate study: annotation always, a vote only for a calibrated FOR."""

import json

import pytest

from src import roster

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B = "0x" + "a" * 40, "0x" + "b" * 40
C = "0x" + "c" * 40


def study_row(wallet, verdict):
    return {"wallet": wallet, "rank": 1.0, "coverage_days": 20,
            "families": {"tooling": {"verdict": verdict, "lr": 30.0, "by": ["T1"],
                                     "key": {"style": "PROGRAM_IOC5"}, "basis": "family"}}}


def build(tmp_path, monkeypatch, rows, previous=None, raw=None):
    # ROSTER_DIR is fixed at import: patch it too, or carry_peak_tier reads the real roster.
    # `raw` is the study file's exact text, for a document that is not a well-formed one.
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "study").mkdir(exist_ok=True)
    (tmp_path / "study" / "latest.json").write_text(
        raw if raw is not None else json.dumps(
            {"computed_at": "2026-10-06T00:00:00+00:00", "wallets": rows}))
    if previous:
        (tmp_path / "roster").mkdir(exist_ok=True)
        (tmp_path / "roster" / "latest.json").write_text(json.dumps({"wallets": previous}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    return {w["wallet"]: w for w in doc["wallets"]}


def test_a_calibrated_tooling_for_casts_the_execution_vote(tmp_path, monkeypatch):
    rows = build(tmp_path, monkeypatch, [study_row(A, "for"), study_row(T, "for")])
    assert "execution_program" in rows[A]["vectors"] and rows[A]["tier"] == "POSSIBLE"
    assert rows[A]["evidence"]["study"]["families"]["tooling"]["verdict"] == "for"
    assert rows[A]["evidence"]["study"]["families"]["tooling"]["basis"] == "family"
    assert T not in rows


def test_a_mixed_tooling_verdict_keeps_the_vote(tmp_path, monkeypatch):
    # `mixed` is a calibrated FOR beside a T1 AGAINST. The operator ruled that evidence
    # against never changes a vector, so the vote stays and the wallet does not fall.
    previous = [{"wallet": A, "tier": "POSSIBLE", "peak_tier": "POSSIBLE"}]
    rows = build(tmp_path, monkeypatch, [study_row(A, "mixed")], previous=previous)
    assert "execution_program" in rows[A]["vectors"]
    assert rows[A]["tier"] == "POSSIBLE" and rows[A]["tier_dropped_from"] is None
    assert rows[A]["evidence"]["study"]["families"]["tooling"]["verdict"] == "mixed"


def test_the_reason_says_which_tooling_verdict_cast_the_vote(tmp_path, monkeypatch):
    # `mixed` used to read "Makes orders the way he does", which says the opposite of half
    # of what the study found. The vote is the same either way; the words are not.
    rows = build(tmp_path, monkeypatch, [study_row(A, "for"), study_row(B, "mixed")])
    makes = "Makes orders the way he does (candidate study, calibrated)"
    mixed = "Some of his tooling matches, some contradicts (candidate study, calibrated)"
    assert "execution_program" in rows[A]["vectors"] and "execution_program" in rows[B]["vectors"]
    assert [r for r in rows[A]["reasons"] if "candidate study" in r] == [makes]
    assert [r for r in rows[B]["reasons"] if "candidate study" in r] == [mixed]


def test_against_is_annotation_only(tmp_path, monkeypatch):
    # B already carries an independent vector (a shared agent) and a POSSIBLE history.
    # An AGAINST in the study changes none of it: not the vector, not the tier, not the peak.
    (tmp_path / "agent_links").mkdir()
    (tmp_path / "agent_links" / "latest.json").write_text(json.dumps(
        {"linked_to_target": {B: ["0xagent"]}}))
    previous = [{"wallet": B, "tier": "POSSIBLE", "peak_tier": "POSSIBLE"}]
    before = build(tmp_path, monkeypatch, [], previous=previous)[B]
    rows = build(tmp_path, monkeypatch, [study_row(B, "against")], previous=previous)
    assert before["vectors"] == ["shared_agent"] and before["tier"] == "POSSIBLE"
    assert rows[B]["vectors"] == ["shared_agent"]     # survives, and no execution vote beside it
    assert rows[B]["tier"] == before["tier"]
    assert rows[B]["peak_tier"] == "POSSIBLE" and rows[B]["tier_dropped_from"] is None
    assert rows[B]["evidence"]["study"]["families"]["tooling"]["verdict"] == "against"


@pytest.mark.parametrize("verdict", ["against", "neutral", "uncalibrated", "insufficient",
                                     "unreadable"])
def test_every_other_verdict_casts_no_vote(tmp_path, monkeypatch, verdict):
    rows = build(tmp_path, monkeypatch, [study_row(A, verdict)])
    assert rows[A]["vectors"] == []
    assert rows[A]["evidence"]["study"]["families"]["tooling"]["verdict"] == verdict


def test_an_unreadable_archive_is_annotation_only(tmp_path, monkeypatch):
    # An archive the study could not read says nothing about the wallet: it is
    # recorded as `unreadable` beside the other verdicts and never casts a vote.
    rows = build(tmp_path, monkeypatch, [study_row(A, "unreadable")])
    assert rows[A]["evidence"]["study"]["families"]["tooling"]["verdict"] == "unreadable"
    assert "execution_program" not in rows[A]["vectors"]
    assert rows[A]["vectors"] == []


def test_a_malformed_study_row_never_raises_and_a_good_row_still_votes(tmp_path, monkeypatch):
    # build_roster is shared by every vector, so one bad study file must not drop them
    # all: a row of the wrong shape is skipped, or annotated with what reads, and casts
    # no vote. A well-formed FOR in the same file still does.
    rows = build(tmp_path, monkeypatch, [
        {"wallet": A, "families": {"tooling": "for"}},      # a family that is not a dict
        {"wallet": B, "families": ["tooling"]},             # families that is not a dict
        {"wallet": 7, "families": {}},                      # a wallet that is not a string
        study_row(C, "for")])
    assert "execution_program" not in rows[A]["vectors"]
    assert "execution_program" not in rows[B]["vectors"]
    assert rows[A]["evidence"]["study"]["families"] == {}
    assert rows[B]["evidence"]["study"]["families"] == {}
    assert "execution_program" in rows[C]["vectors"]
    assert set(rows) == {A, B, C}                           # the integer wallet made no row


@pytest.mark.parametrize("wallets", [7, True, "x", {"a": 1}])
def test_a_study_file_whose_wallets_is_not_a_list_changes_nothing(tmp_path, monkeypatch, wallets):
    assert build(tmp_path, monkeypatch, wallets) == {}


@pytest.mark.parametrize("raw", [
    json.dumps([study_row(A, "for")]),                       # a JSON list, not a document
    "null",                                                  # a document that is null
    '"x"',                                                   # a document that is a string
    '{"computed_at": "2026-10-06T00:00:00+00:00", "wallets": [{"wallet": "0xaaaa',  # truncated
    '{"wallets": [null, 3, "x"]}',                           # rows that are not dicts
], ids=["list", "null", "string", "truncated", "rows_not_dicts"])
def test_a_malformed_study_document_builds_the_roster_and_annotates_nothing(
        tmp_path, monkeypatch, raw):
    rows = build(tmp_path, monkeypatch, None, raw=raw)
    assert not any("study" in w["evidence"] for w in rows.values())


def test_no_study_file_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    assert all("study" not in w["evidence"] for w in doc["wallets"])
