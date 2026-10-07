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


def build(tmp_path, monkeypatch, rows, previous=None):
    # ROSTER_DIR is fixed at import: patch it too, or carry_peak_tier reads the real roster.
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "study").mkdir(exist_ok=True)
    (tmp_path / "study" / "latest.json").write_text(json.dumps(
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


def test_against_is_annotation_only(tmp_path, monkeypatch):
    previous = [{"wallet": B, "tier": "POSSIBLE", "peak_tier": "POSSIBLE"}]
    rows = build(tmp_path, monkeypatch, [study_row(B, "against")], previous=previous)
    assert rows[B]["vectors"] == []
    assert rows[B]["evidence"]["study"]["families"]["tooling"]["verdict"] == "against"
    assert rows[B]["peak_tier"] == "POSSIBLE"


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


def test_no_study_file_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    assert all("study" not in w["evidence"] for w in doc["wallets"])
