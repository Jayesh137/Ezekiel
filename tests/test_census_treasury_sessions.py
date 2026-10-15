"""The census script's shell: window reads, bounded folding, known-wallet readings."""

import pytest

from scripts import census_treasury_sessions as cs
from src import replica_cmds as rc
from src import treasury_sessions as ts

T = "0x" + "1" * 40
K = "0x" + "2" * 40
A = "0x" + "a" * 40
DAY = 86_400_000


def _act(user, t, kind="usdSend"):
    return {"user": user, "type": kind, "action": {"type": kind}, "status": "ok", "time": t,
            "block": 1, "index": 0, "nonce": t, "vault_address": None}


def test_read_window_maps_timestamps_to_blocks_and_demands_a_complete_read():
    calls = []

    def stream(a, b, **kw):
        calls.append((a, b, kw.get("status"), tuple(kw.get("action_types") or ())))
        return {"actions": [_act(A, 5)], "complete": True, "last_block": b, "requests": 1, "overloads": 0}
    out = cs.read_window(1_000, 2_000, block_at=lambda ms: ms // 10, stream=stream)
    assert out["actions"][0]["user"] == A
    assert calls == [(100, 199, "ok", ts.WINDOW_TYPES)]
    with pytest.raises(rc.FeedError):
        cs.read_window(1_000, 2_000, block_at=lambda ms: ms // 10,
                       stream=lambda a, b, **kw: {"actions": [], "complete": False, "last_block": 150,
                                                  "requests": 60, "overloads": 9})


def test_fold_sessions_reads_each_pending_session_and_its_controls_within_the_bounds():
    state = ts.empty_state()
    s1 = {"key": "s1", "start": 10 * DAY, "end": 10 * DAY + 60_000, "n": 1, "destinations": set()}
    s2 = {"key": "s2", "start": 11 * DAY, "end": 11 * DAY + 60_000, "n": 1, "destinations": set()}
    s3 = {"key": "s3", "start": 30 * DAY, "end": 30 * DAY, "n": 1, "destinations": set()}   # beyond the head
    reads = []

    def read(lo, hi):
        reads.append((lo, hi))
        if lo < 5 * DAY:                               # the -7d control of s1
            return {"actions": [_act(K, lo + 1)]}
        return {"actions": [_act(A, lo + 1), _act(K, lo + 2)]}
    out = cs.fold_sessions(state, [s1, s2, s3], {T}, dataset_start_ms=DAY, head_ms=20 * DAY,
                           now_ms=99, max_sessions=5, time_budget=60, read=read, log=lambda *a: None)
    assert [f["key"] for f in out["folded"]] == ["s1", "s2"] and out["failed"] == []
    assert len(reads) == 6                             # 2 sessions x (window + 2 controls)
    assert state["sessions"] == 2 and state["controls"] == 4
    # A is absent from the two -7d controls (they fall before 5*DAY), present in the two +7d ones.
    assert state["wallets"][A] == {"s": 2, "c": 2, "k": {"usdSend": 2}, "ss": ["s1", "s2"], "f": 99, "l": 99}
    assert state["wallets"][K]["s"] == 2 and state["wallets"][K]["c"] == 4


def test_fold_sessions_records_an_unreadable_session_and_moves_on():
    state = ts.empty_state()
    s1 = {"key": "s1", "start": 10 * DAY, "end": 10 * DAY, "n": 1, "destinations": set()}
    s2 = {"key": "s2", "start": 11 * DAY, "end": 11 * DAY, "n": 1, "destinations": set()}

    def read(lo, hi):
        if abs(lo - (10 * DAY - ts.PAD_MS)) < 1000:          # s1's own window only
            raise rc.FeedError("busy")
        return {"actions": []}
    out = cs.fold_sessions(state, [s1, s2], {T}, dataset_start_ms=DAY, head_ms=20 * DAY, now_ms=1,
                           max_sessions=5, time_budget=60, read=read, log=lambda *a: None)
    assert [f["key"] for f in out["failed"]] == ["s1"] and [f["key"] for f in out["folded"]] == ["s2"]
    assert "s1" not in state["done"] and "s2" in state["done"]


def test_fold_sessions_stops_at_max_sessions():
    state = ts.empty_state()
    pending = [{"key": f"s{i}", "start": (10 + i) * DAY, "end": (10 + i) * DAY, "n": 1, "destinations": set()}
               for i in range(5)]
    checkpoints = []
    out = cs.fold_sessions(state, pending, {T}, dataset_start_ms=DAY, head_ms=40 * DAY, now_ms=1,
                           max_sessions=2, time_budget=60, read=lambda lo, hi: {"actions": []},
                           log=lambda *a: None, checkpoint=lambda st: checkpoints.append(st["sessions"]))
    assert len(out["folded"]) == 2 and state["sessions"] == 2
    assert checkpoints == [1, 2]            # a killed run keeps every session already folded


def test_known_readings_score_the_positive_controls_like_strangers():
    state = ts.empty_state()
    state.update(sessions=40, controls=80, wallets={K: {"s": 8, "c": 0, "k": {}, "ss": [], "f": 1, "l": 1}})
    out = cs.known_readings(state, {K, "0x" + "3" * 40})
    assert out[K]["hit"] is True and out[K]["sessions_with"] == 8
    assert out["0x" + "3" * 40] == {"sessions_with": 0, "controls_with": 0}
