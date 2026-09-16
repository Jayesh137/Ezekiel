# tests/test_stale_scorer_cannot_vote.py
"""A behavioural score counts only if the scorer the backtest validates produced it.

The backtest passed for the first time on 2026-09-16 and the roster's
behavioural vector began to vote the same evening. PROBABLE went 1 -> 7.
Every one of the seven wallets voting did so on a SINGLE scan from 2026-06-30
or 2026-07-01 — a scorer with no `order_profile` dimension, from before the
flat-wallet leverage fix — and none of them had been re-scored since, because a
wallet that leaves the scan population is never re-scored at all. The same
June number was `risk.py`'s top candidate (22 of 100 points) and the "trades
like the target" line inside the graph's CRITICAL email.

The backtest validates one scorer, named by `thresholds.SCORING_SCHEMA`. A
score from any other scorer — including one written before scores carried a
schema — is history. It stays on disk and on the dashboard; it may not decide.
"""

import json

import src.roster as roster
from src import risk, scanner, tracer
from src import thresholds as th
from src import transfer_graph as tg
from src.utils import candidate_scored_by_current_scorer

TARGET = "0x" + "11" * 20
W = "0x" + "22" * 20

JUNE = {"wallet": W, "best_score": 0.7019, "latest_score": 0.7019,
        "last_seen": "2026-06-30T10:30:17+00:00", "status": "ACTIVE",
        "latest_evidence": {"vetoes": []}}
CURRENT = {**JUNE, "last_seen": "2026-09-16T18:00:00+00:00",
           "latest_scoring_schema": th.SCORING_SCHEMA}


def test_the_helper_refuses_a_missing_or_retired_schema():
    assert candidate_scored_by_current_scorer(CURRENT)
    assert not candidate_scored_by_current_scorer(JUNE)
    assert not candidate_scored_by_current_scorer({**JUNE, "latest_scoring_schema": "2026-07-01.0"})
    assert not candidate_scored_by_current_scorer({})
    assert not candidate_scored_by_current_scorer(None)


def _roster(tmp_path, monkeypatch, cand):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": True}))
    (tmp_path / "candidates").mkdir(parents=True, exist_ok=True)
    (tmp_path / "candidates" / "latest.json").write_text(json.dumps({"candidates": [cand]}))
    return roster.build_roster({"target_wallet": TARGET})["wallets"][0]


def test_a_june_score_casts_no_vote_but_stays_on_record(tmp_path, monkeypatch):
    row = _roster(tmp_path, monkeypatch, JUNE)

    assert "behavioural" not in row["vectors"]
    assert row["evidence"]["behavioural_score"] == 0.7019
    assert row["evidence"]["behavioural_scorer_current"] is False
    assert row["evidence"]["behavioural_scored_at"].startswith("2026-06-30")


def test_a_current_score_still_votes(tmp_path, monkeypatch):
    row = _roster(tmp_path, monkeypatch, CURRENT)

    assert "behavioural" in row["vectors"]
    assert row["evidence"]["behavioural_scorer_current"] is True


def test_risk_skips_a_retired_score_for_its_top_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(risk, "read_cursor", lambda n: 0)
    monkeypatch.setattr(risk, "load_all_records", lambda d: [])
    other = "0x" + "33" * 20
    (tmp_path / "candidates").mkdir(parents=True)
    (tmp_path / "candidates" / "latest.json").write_text(json.dumps({"candidates": [
        JUNE,
        {**CURRENT, "wallet": other, "latest_score": 0.61},
    ]}))

    signals = risk._gather_signals()

    assert signals["top_candidate_wallet"] == other
    assert signals["top_candidate_score"] == 0.61


def test_risk_with_only_retired_scores_names_no_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(risk, "read_cursor", lambda n: 0)
    monkeypatch.setattr(risk, "load_all_records", lambda d: [])
    (tmp_path / "candidates").mkdir(parents=True)
    (tmp_path / "candidates" / "latest.json").write_text(json.dumps({"candidates": [JUNE]}))

    signals = risk._gather_signals()

    assert "top_candidate_wallet" not in signals


def test_the_graph_does_not_quote_a_retired_score(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    (tmp_path / "candidates").mkdir(parents=True)
    (tmp_path / "candidates" / "latest.json").write_text(json.dumps({"candidates": [JUNE]}))

    scores, active = tg._load_behavioural_scores()

    assert W not in scores
    # Whether the wallet trades on HL is a separate fact from how it scored.
    assert W in active


def test_the_tracer_sends_no_combined_alert_on_a_retired_score(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(tracer, "alert_combined_match", lambda *a, **k: sent.append(a) or True)
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path)
    (tmp_path / "candidates").mkdir(parents=True)
    (tmp_path / "candidates" / "latest.json").write_text(json.dumps({"candidates": [JUNE]}))
    eff = th.resolve({"similarity_high": 0.90, "similarity_medium": 0.80, "similarity_low": 0.65},
                     {"passed": True, "self_score": 0.7163, "scoring_schema": th.SCORING_SCHEMA})

    tracer._crossref_findings_with_candidates(
        [{"destination": W, "deposited_to_hl": True, "amount_usdc": "500000"}], eff)

    assert sent == []


def test_persist_candidate_records_which_scorer_wrote_the_score(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)

    scanner.persist_candidate({"wallet": W, "score": 0.66,
                               "scanned_at": "2026-09-16T18:00:00+00:00",
                               "evidence": {"tier": "WATCH_CLOSELY"}, "dimensions": {}})

    stored = json.loads((tmp_path / "candidates" / f"{W}.json").read_text())
    assert stored["latest_scoring_schema"] == th.SCORING_SCHEMA
    assert stored["score_history"][-1]["scoring_schema"] == th.SCORING_SCHEMA
    assert candidate_scored_by_current_scorer(stored)
