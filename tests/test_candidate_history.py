"""Discovery survives shortlist limits, sparse fills and unsuccessful reads."""

import json

from src import scanner

WALLET = "0x" + "2" * 40
TARGET = "0x" + "1" * 40


def fill(tid, timestamp=None):
    return {"tid": tid, "time": timestamp if timestamp is not None else tid + 100,
            "coin": "BTC", "px": "100", "sz": "1", "side": "B"}


def test_registry_reads_individual_records_beyond_display_and_prefers_them(tmp_path):
    from src.candidate_registry import iter_candidates
    directory = tmp_path / "candidates"
    directory.mkdir()
    (directory / "latest.json").write_text(json.dumps({"candidates": [{"wallet": WALLET, "latest_score": .9}]}))
    (directory / f"{WALLET}.json").write_text(json.dumps({"wallet": WALLET, "latest_score": .3}))
    second = "0x" + "3" * 40
    (directory / f"{second}.json").write_text(json.dumps({"wallet": second, "latest_score": .8}))
    (directory / "0xbroken.json").write_text("{")
    rows = {row["wallet"]: row for row in iter_candidates(tmp_path)}
    assert rows[WALLET]["latest_score"] == .3
    assert second in rows


def test_failed_registry_observation_preserves_last_success_and_positive_evidence(tmp_path):
    from src.candidate_registry import observe_candidate
    observe_candidate(WALLET, {"source": "bridge", "event_id": "tx1", "observed_at": "2026-09-20T00:00:00+00:00",
                               "status": "ok", "positive": True}, tmp_path)
    row = observe_candidate(WALLET, {"source": "fills", "observed_at": "2026-09-21T00:00:00+00:00",
                                     "status": "error", "error": "timeout"}, tmp_path)
    assert row["last_checked"].startswith("2026-09-21")
    assert row["last_successful_read"].startswith("2026-09-20")
    assert row["last_positive_evidence"].startswith("2026-09-20")
    assert any(e.get("event_id") == "tx1" for e in row["observations"])


def test_failed_retry_of_same_event_cannot_erase_positive_fact(tmp_path):
    from src.candidate_registry import observe_candidate
    observe_candidate(WALLET, {'source': 'route', 'event_id': 'tx1', 'status': 'ok', 'positive': True}, tmp_path)
    row = observe_candidate(WALLET, {'source': 'route', 'event_id': 'tx1', 'status': 'error', 'error': 'timeout'}, tmp_path)
    assert any(o.get('positive') and o.get('event_id') == 'tx1' for o in row['observations'])


def test_persisted_score_downgrade_keeps_discovery_and_updates_freshness(tmp_path, monkeypatch):
    from src.candidate_registry import observe_candidate
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scanner, "load_config", lambda: {"target_wallet": TARGET})
    observe_candidate(WALLET, {"source": "public_trades", "status": "ok"}, tmp_path)
    result = {"wallet": WALLET, "score": .8, "scanned_at": "2026-09-20T00:00:00+00:00",
              "evidence": {"tier": "WATCH_CLOSELY"}, "dimensions": {}}
    scanner.persist_candidate(result)
    scanner.persist_candidate({**result, "score": .2, "scanned_at": "2026-09-21T00:00:00+00:00"})
    saved = json.loads((tmp_path / "candidates" / f"{WALLET}.json").read_text())
    assert saved["latest_score"] == .2
    assert saved["last_scored"].startswith("2026-09-21")
    assert "public_trades" in saved["discovery_sources"]


def test_registry_wallet_paths_cannot_escape_data_directory(tmp_path):
    import pytest

    from src.candidate_registry import observe_candidate
    with pytest.raises(ValueError):
        observe_candidate("../escape", {"source": "test"}, tmp_path)


def test_pagination_uses_overlap_dedup_and_reports_available_coverage(monkeypatch):
    from src import history
    monkeypatch.setattr(history, "PAGE_SIZE", 3)
    calls = []
    def fetch(body):
        calls.append(body)
        return [fill(1), fill(2), fill(3)] if len(calls) == 1 else [fill(3), fill(4)]
    result = history.fetch_fill_history(WALLET, 0, 1000, fetch=fetch)
    assert [row["tid"] for row in result["fills"]] == [1, 2, 3, 4]
    assert calls[1]["startTime"] == 103
    assert result["status"] == "ok"
    assert result["coverage"]["complete_available_window"] is True


def test_same_timestamp_page_saturation_does_not_skip_unseen_events(monkeypatch):
    from src import history
    monkeypatch.setattr(history, "PAGE_SIZE", 3)
    result = history.fetch_fill_history(WALLET, 0, 1000,
        fetch=lambda body: [fill(i, 100) for i in range(3)])
    assert result["status"] == "partial"
    assert result["coverage"]["complete_available_window"] is False
    assert result["gaps"][0]["reason"] == "timestamp_saturation"


def test_partial_read_failure_preserves_observed_fills_without_claiming_complete(monkeypatch):
    from src import history
    monkeypatch.setattr(history, "PAGE_SIZE", 2)
    calls = []
    def fetch(body):
        calls.append(body)
        if len(calls) > 1:
            raise TimeoutError("down")
        return [fill(1), fill(2)]
    result = history.fetch_fill_history(WALLET, 0, 1000, fetch=fetch)
    assert len(result["fills"]) == 2
    assert result["status"] == "partial"
    assert result["error"]
    assert not result["coverage"]["complete_available_window"]


def test_empty_success_is_distinct_from_failed_read():
    from src.history import fetch_fill_history
    assert fetch_fill_history(WALLET, 0, 1000, fetch=lambda body: [])["status"] == "ok"
    assert fetch_fill_history(WALLET, 0, 1000, fetch=lambda body: {})["status"] == "error"


def test_history_store_deduplicates_and_preserves_cached_fills_after_failure(tmp_path):
    from src.history import cached_fill_history
    db = tmp_path / "history.sqlite3"
    one = cached_fill_history(WALLET, 0, 1000, db_path=db, fetch=lambda body: [fill(1), fill(2)])
    two = cached_fill_history(WALLET, 0, 2000, db_path=db,
                             fetch=lambda body: {"ok": False, "data": None, "error": "down"})
    assert [r["tid"] for r in one["fills"]] == [1, 2]
    assert [r["tid"] for r in two["fills"]] == [1, 2]
    assert two["status"] == "error"
    assert two["last_successful_end_ms"] == 1000


def test_explicit_hl_read_distinguishes_timeout_from_successful_empty(monkeypatch):
    import requests

    from src import utils
    monkeypatch.setattr(utils, "load_config", lambda: {"hyperliquid_api": "https://unused.invalid"})
    monkeypatch.setattr(utils.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(utils.requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("down")))
    result = utils.hl_read({"type": "userFillsByTime", "user": WALLET}, retries=1)
    assert result["ok"] is False and result["data"] is None
    class Response:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return []
    monkeypatch.setattr(utils.requests, "post", lambda *a, **k: Response())
    result = utils.hl_read({"type": "userFillsByTime", "user": WALLET}, retries=1)
    assert result["ok"] is True and result["data"] == []


def test_failed_targeted_read_keeps_previous_score(tmp_path, monkeypatch):
    from src.history import FillBatch
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    directory = tmp_path / "candidates"
    directory.mkdir()
    path = directory / f"{WALLET}.json"
    path.write_text(json.dumps({"wallet": WALLET, "latest_score": .8,
                                "last_successful_read": "2026-09-20"}))
    monkeypatch.setattr(scanner, "get_candidate_fills", lambda *a: FillBatch(
        {"fills": [fill(i) for i in range(20)], "status": "error", "error": "down"}))
    assert scanner.scan_specific_wallet(WALLET, {}, {"scanner": {}}) is None
    row = json.loads(path.read_text())
    assert row["latest_score"] == .8
    assert row["last_successful_read"] == "2026-09-20"
    assert row["last_read_status"] == "error"


def test_graph_reads_candidate_evidence_outside_display(tmp_path, monkeypatch):
    from src import thresholds, transfer_graph
    directory = tmp_path / "candidates"
    directory.mkdir()
    (directory / f"{WALLET}.json").write_text(json.dumps({
        "wallet": WALLET, "latest_score": .8, "latest_scoring_schema": thresholds.SCORING_SCHEMA,
        "latest_evidence": {"linkage": {"shared_funder": True}}}))
    monkeypatch.setattr(transfer_graph, "DATA_DIR", tmp_path)
    assert transfer_graph._load_behavioural_scores()[0][WALLET] == .8
    assert transfer_graph._load_linkage_evidence()[WALLET]["shared_funder"]


def test_unreadable_position_state_does_not_measure_zero_equity_or_leverage():
    fp = scanner.build_candidate_fingerprint([fill(i) for i in range(20)], {})
    assert {"account_size", "leverage"} <= set(fp["excluded_dimensions"])


def test_empty_hip3_failure_is_reported_as_incomplete_state():
    state = scanner.merged_clearinghouse_state(WALLET, ["xyz"], fetch=lambda body:
        {} if body.get("dex") else {"marginSummary": {"accountValue": "100"}, "assetPositions": []})
    assert state["unreadable_dexes"] == ["xyz"]


def test_partial_history_resumes_across_runs_without_claiming_early_coverage(tmp_path, monkeypatch):
    from src import history
    monkeypatch.setattr(history, 'PAGE_SIZE', 2)
    calls = []
    def fetch(body):
        calls.append(body['startTime'])
        return [fill(1), fill(2)] if body['startTime'] == 0 else [fill(2)]
    db = tmp_path / 'resume.sqlite3'
    one = history.cached_fill_history(WALLET, 0, 1000, db_path=db, fetch=fetch, max_pages=1)
    assert one['status'] == 'partial' and one['last_successful_end_ms'] is None
    two = history.cached_fill_history(WALLET, 0, 2000, db_path=db, fetch=fetch, max_pages=1)
    assert calls == [0, 102]
    assert two['status'] == 'ok' and two['last_successful_end_ms'] == 2000
    assert [r['tid'] for r in two['fills']] == [1, 2]
    assert two['coverage']['start_ms'] == 0


def test_partial_history_failure_retries_the_unread_boundary(tmp_path, monkeypatch):
    from src import history
    monkeypatch.setattr(history, 'PAGE_SIZE', 2)
    calls = []
    def fetch(body):
        calls.append(body['startTime'])
        if len(calls) == 2:
            raise TimeoutError('down')
        return [fill(1), fill(2)] if body['startTime'] == 0 else [fill(2)]
    db = tmp_path / 'resume.sqlite3'
    one = history.cached_fill_history(WALLET, 0, 1000, db_path=db, fetch=fetch)
    assert one['status'] == 'partial'
    two = history.cached_fill_history(WALLET, 0, 1000, db_path=db, fetch=fetch)
    assert calls == [0, 102, 102] and two['status'] == 'ok'


def test_larger_lookback_does_not_resume_past_missing_older_history(tmp_path, monkeypatch):
    from src import history
    monkeypatch.setattr(history, 'PAGE_SIZE', 2)
    db = tmp_path / 'resume.sqlite3'
    history.cached_fill_history(WALLET, 100, 1000, db_path=db,
        fetch=lambda body: [fill(1), fill(2)], max_pages=1)
    calls = []
    history.cached_fill_history(WALLET, 0, 1000, db_path=db,
        fetch=lambda body: calls.append(body['startTime']) or [])
    assert calls == [0]


def test_saturated_resume_never_skips_same_timestamp_fills(tmp_path, monkeypatch):
    from src import history
    monkeypatch.setattr(history, 'PAGE_SIZE', 2)
    db = tmp_path / 'resume.sqlite3'
    calls = []
    def fetch(body):
        calls.append(body['startTime'])
        return [fill(1, 100), fill(2, 100)]
    history.cached_fill_history(WALLET, 0, 1000, db_path=db, fetch=fetch)
    two = history.cached_fill_history(WALLET, 0, 2000, db_path=db, fetch=fetch)
    assert calls == [0, 100, 100]
    assert two['saturation'] and two['last_successful_end_ms'] is None


def test_global_fill_pruning_invalidates_pending_resume(tmp_path, monkeypatch):
    import sqlite3

    from src import history
    from src.discovery_state import compact
    monkeypatch.setattr(history, 'PAGE_SIZE', 2)
    db = tmp_path / 'resume.sqlite3'
    history.cached_fill_history(WALLET, 0, 1000, db_path=db,
        fetch=lambda body: [fill(1), fill(2)], max_pages=1)
    compact(db, now_ms=200 * 86400_000)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM fill_progress').fetchone()[0] == 0


def test_disjoint_older_query_does_not_claim_the_gap_to_cached_newer_history(tmp_path):
    import sqlite3

    from src.history import cached_fill_history
    db = tmp_path / 'resume.sqlite3'
    cached_fill_history(WALLET, 1000, 2000, db_path=db, fetch=lambda _: [])
    cached_fill_history(WALLET, 0, 500, db_path=db, fetch=lambda _: [])
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT start_ms,end_ms FROM fill_coverage').fetchone() == (0, 500)
