"""The sweep script's shell: cursor semantics, the store, and the ingest path."""

import json

from scripts import sweep_replica_actions as sw
from src import hl_actions
from src import replica_cmds as rc

T = "0x" + "1" * 40
AG = "0x" + "a" * 40


def _row(block, index=0, kind="approveAgent", **payload):
    action = {"type": kind, "agentAddress": AG, "agentName": "bot", **payload}
    return {"user": T, "type": kind, "action": action, "nonce": block, "vault_address": None,
            "status": "ok", "block": block, "index": index, "time": block * 1000}


def test_incremental_moves_the_cursor_only_past_blocks_read_in_full():
    state = {"swept_to": 999}
    saved, stored, calls = [], [], []

    def stream(a, b, **kw):
        calls.append((a, b))
        assert kw["users"] == [T] and kw["action_types"] == rc.NON_TRADING_TYPES
        if a == 1000:
            return {"actions": [_row(1200)], "last_block": 1500, "last_ts": 1, "requests": 2,
                    "overloads": 1, "complete": False}          # read short: a bound stopped it
        return {"actions": [], "last_block": b, "last_ts": 2, "requests": 1, "overloads": 0,
                "complete": True}
    out = sw.incremental([T], state, max_requests=10, time_budget=60, head=lambda: 5000, stream=stream,
                         store=lambda rows: stored.extend(rows) or {}, save=lambda st: saved.append(dict(st)))
    assert calls == [(1000, min(5000, 1000 + rc.BLOCKS_PER_REQUEST * 20))]
    assert state["swept_to"] == 1500 and out["complete"] is False and out["rows"] == 1
    assert saved[-1]["swept_to"] == 1500 and len(stored) == 1
    out = sw.incremental([T], state, max_requests=10, time_budget=60, head=lambda: 5000, stream=stream,
                         store=lambda rows: {}, save=lambda st: None)
    assert state["swept_to"] == 5000 and out["complete"] is True
    assert sw.incremental([T], state, max_requests=10, time_budget=60, head=lambda: 5000,
                          stream=stream, store=lambda r: {}, save=lambda s: None)["requests"] == 0


def test_incremental_stops_at_its_budget_and_keeps_what_was_read():
    state = {}
    ticks = iter([0, 0, 1000, 1000, 1000])

    def stream(a, b, **kw):
        return {"actions": [_row(a + 1)], "last_block": b, "last_ts": 1, "requests": 1, "overloads": 0,
                "complete": True}
    out = sw.incremental([T], state, max_requests=10, time_budget=5, head=lambda: 10 ** 9, stream=stream,
                         store=lambda r: {T: len(r)}, save=lambda s: None, clock=lambda: next(ticks))
    assert out["complete"] is False and out["rows"] == 1 and state["swept_to"] > rc.START_BLOCK


def test_store_rows_and_stored_history_round_trip_through_the_actions_store(tmp_path, monkeypatch):
    monkeypatch.setattr(hl_actions, "ACTIONS_DIR", tmp_path / "actions")
    monkeypatch.setattr(sw, "ACTIONS_DIR", tmp_path / "actions")
    added = sw.store_rows([_row(100), _row(100, index=1, kind="usdSend", destination=AG, amount="5")])
    assert added == {T: 2}
    assert sw.store_rows([_row(100)]) == {T: 0}            # same key, same day: not stored twice
    history = sw.stored_history(T)
    assert sorted(a["type"] for a in history) == ["approveAgent", "usdSend"]
    assert history[0]["hash"].startswith("replica:100:") and history[0]["nonce"] == 100


def test_ingest_file_reads_json_lines_and_skips_junk(tmp_path, monkeypatch):
    monkeypatch.setattr(hl_actions, "ACTIONS_DIR", tmp_path / "actions")
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(_row(7)) + "\n\n" + json.dumps({"nope": 1}) + "\n", encoding="utf-8")
    n, added = sw.ingest_file(path)
    assert n == 1 and added == {T: 1}
