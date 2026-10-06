"""Strict getLogs and a cursor that never skips (spec §5)."""

import pytest

from src.boundary import logs


def test_a_genuine_empty_answer_is_empty():
    assert logs.rows_of({"status": "0", "message": "No logs found", "result": []}, "bs") == []
    assert logs.rows_of({"status": "0", "message": "No records found", "result": []}, "es") == []


def test_an_error_answer_is_never_empty():
    for doc in ({"status": "0", "message": "Too many requests", "result": None},
                {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"},
                {"status": "0", "message": "Invalid API Key", "result": []},
                [], None):
        with pytest.raises(logs.LogReadError):
            logs.rows_of(doc, "x")


def test_three_topics_name_an_operator_for_every_pair():
    # Blockscout refuses a three-topic query without topic0_2_opr ("Required
    # query parameters missing"), which failed every retro Circle read on the
    # 2026-10-06 dry run.
    p = logs._params("0xabc", {0: "0xt0", 1: "0xt1", 2: "0xt2"}, 5, 9)
    assert p["topic0_1_opr"] == p["topic0_2_opr"] == p["topic1_2_opr"] == "and"


def test_params_join_topics_with_and():
    p = logs._params("0xabc", {0: "0xt0", 2: "0xt2"}, 5, 9)
    assert p["topic0"] == "0xt0" and p["topic2"] == "0xt2" and p["topic0_2_opr"] == "and"
    assert p["fromBlock"] == 5 and p["toBlock"] == 9 and "topic1" not in p


def _log(block, tx="0xaa", index=0):
    return {"blockNumber": hex(block), "transactionHash": tx, "logIndex": hex(index)}


def test_walk_reads_in_chunks_and_advances_the_cursor():
    seen = []

    def read(lo, hi):
        seen.append((lo, hi))
        return [_log(lo, tx=f"0x{lo}")]
    out = logs.walk(read, 100, 349, chunk=100)
    assert seen == [(100, 199), (200, 299), (300, 349)]
    assert out["last_block"] == 349 and out["complete"] and out["error"] is None
    assert len(out["logs"]) == 3


def test_a_full_page_resumes_at_its_last_block_without_duplicates():
    page1 = [_log(100 + i // 10, tx=f"0x{i}", index=i) for i in range(1000)]   # blocks 100..199
    rest = [_log(199, tx="0x999", index=999), _log(199, tx="0xnew", index=1)]
    calls = []

    def read(lo, hi):
        calls.append((lo, hi))
        return page1 if len(calls) == 1 else rest
    out = logs.walk(read, 100, 250, chunk=1000)
    assert calls == [(100, 250), (199, 250)]
    keys = [logs.log_key(r) for r in out["logs"]]
    assert len(keys) == len(set(keys)) == 1001
    assert out["last_block"] == 250 and out["complete"]


def test_an_error_stops_the_walk_without_moving_past_the_unread_range():
    def read(lo, hi):
        if lo >= 200:
            raise logs.LogReadError("blockscout: 429")
        return []
    out = logs.walk(read, 100, 400, chunk=100)
    assert out["last_block"] == 199 and not out["complete"] and "429" in out["error"]


def test_a_single_block_with_a_full_page_is_refused():
    out = logs.walk(lambda lo, hi: [_log(lo, tx=f"0x{i}", index=i) for i in range(1000)],
                    7, 50, chunk=100)
    assert out["last_block"] == 6 and "partial block" in out["error"]


def test_the_walk_respects_its_call_budget():
    out = logs.walk(lambda lo, hi: [], 0, 10_000, chunk=10, max_calls=3)
    assert out["calls"] == 3 and out["last_block"] == 29 and not out["complete"]


def test_read_logs_falls_back_to_etherscan_only_with_a_key():
    def bs(*a, **k):
        raise logs.LogReadError("blockscout: 429")

    def es(*a, **k):
        return [{"ok": 1}]
    assert logs.read_logs("arbitrum", "0xa", {0: "0x1"}, 1, 2, blockscout=bs, etherscan=es,
                          has_key=True) == [{"ok": 1}]
    with pytest.raises(logs.LogReadError):
        logs.read_logs("arbitrum", "0xa", {0: "0x1"}, 1, 2, blockscout=bs, etherscan=es,
                       has_key=False)


def test_http_get_backs_off_on_429_then_raises(monkeypatch):
    class R:
        status_code = 429

        def json(self):
            return {}

        def raise_for_status(self):
            pass
    monkeypatch.setattr(logs.requests, "get", lambda *a, **k: R())
    with pytest.raises(logs.LogReadError):
        logs._http_get("u", {}, tries=2, sleep=lambda s: None)


def test_to_int_reads_hex_and_decimal():
    assert logs.to_int("0x10") == 16 and logs.to_int("16") == 16 and logs.to_int(16) == 16


def test_http_get_never_sleeps_past_its_deadline_nor_after_its_last_try(monkeypatch):
    # A throttled host cost ~100s per call on the 2026-10-06 dry run (10+20+30+40s,
    # the last sleep after the final try).
    class R:
        status_code = 429

        def json(self):
            return {}

        def raise_for_status(self):
            pass
    monkeypatch.setattr(logs.requests, "get", lambda *a, **k: R())
    now, slept = [0.0], []

    def sleep(s):
        slept.append(s)
        now[0] += s
    with pytest.raises(logs.LogReadError):
        logs._http_get("u", {}, sleep=sleep, clock=lambda: now[0])
    unbounded = now[0]
    assert unbounded < 30.0 and slept[-1] == logs.PACE_SECONDS
    now[0], slept[:] = 0.0, []
    with pytest.raises(logs.LogReadError, match="deadline"):
        logs._http_get("u", {}, sleep=sleep, clock=lambda: now[0], deadline=4.0)
    assert now[0] <= 4.0


def test_read_logs_carries_its_deadline_to_the_default_reader(monkeypatch):
    seen = {}

    def fake(url, params, **kw):
        seen.update(kw)
        return {"status": "0", "message": "No logs found", "result": []}
    monkeypatch.setattr(logs, "_http_get", fake)
    assert logs.read_logs("arbitrum", "0xabc", {0: "0xt"}, 1, 2, has_key=False, deadline=7.0) == []
    assert seen["deadline"] == 7.0


def test_with_a_key_etherscan_is_read_first_because_blockscout_has_holes():
    # 2026-10-06: Blockscout's Arbitrum index answered "Not found" for blocks
    # 507,912,970 to at least 508,500,000, and getLogs over them answers "No logs
    # found" - a hole that reads exactly like an empty range. Etherscan indexes
    # every block, so with a key it is the reader and Blockscout the fallback.
    calls = []

    def bs(*a, **k):
        calls.append("blockscout")
        return [{"bs": 1}]

    def es(*a, **k):
        calls.append("etherscan")
        return [{"es": 1}]
    assert logs.read_logs("arbitrum", "0xa", {0: "0x1"}, 1, 2, blockscout=bs, etherscan=es,
                          has_key=True) == [{"es": 1}]
    assert calls == ["etherscan"]

    def es_down(*a, **k):
        raise logs.LogReadError("etherscan: Max rate limit reached")
    assert logs.read_logs("arbitrum", "0xa", {0: "0x1"}, 1, 2, blockscout=bs, etherscan=es_down,
                          has_key=True) == [{"bs": 1}]
    assert logs.read_logs("arbitrum", "0xa", {0: "0x1"}, 1, 2, blockscout=bs, etherscan=es_down,
                          has_key=False) == [{"bs": 1}]


def test_block_at_refuses_a_blockscout_answer_from_inside_an_index_hole():
    # Asked for 2026-09-23 19:41 and 20:13, Blockscout answered 507,912,969/970
    # for both - the last block before its hole, 22 hours earlier.
    def get(url, params):
        if params.get("action") == "getblocknobytime":
            return {"status": "1", "message": "OK", "result": {"blockNumber": "507912969"}}
        raise AssertionError((url, params))

    def block_ts(chain, number):
        assert number == 507_912_969
        return 1_790_113_296                      # 2026-09-22 21:41:36
    with pytest.raises(logs.LogReadError, match="hole"):
        logs.block_at("arbitrum", 1_790_192_472, get=get, block_ts=block_ts, has_key=False)
    assert logs.block_at("arbitrum", 1_790_113_300, get=get, block_ts=block_ts,
                         has_key=False) == 507_912_969


def test_with_a_key_the_head_comes_from_etherscan_too(monkeypatch):
    monkeypatch.setattr("src.utils.etherscan_get",
                        lambda params, chain_id=None: {"jsonrpc": "2.0", "result": "0x1e8f2a3d"})

    def bs(*a, **k):
        raise AssertionError("Blockscout read with a key set")
    assert logs.head_block("arbitrum", get=bs, has_key=True) == 0x1E8F2A3D


def test_a_whole_history_that_fills_a_page_is_refused_not_cut_short(monkeypatch):
    monkeypatch.setattr(logs, "read_logs", lambda *a, **k: [{"n": i} for i in range(logs.PAGE)])
    with pytest.raises(logs.LogReadError, match="paging"):
        logs.read_history("arbitrum", "0xa", {0: "0x1"})
    monkeypatch.setattr(logs, "read_logs", lambda *a, **k: [{"n": 1}])
    assert logs.read_history("arbitrum", "0xa", {0: "0x1"}) == [{"n": 1}]


def test_etherscan_writes_zero_as_a_bare_0x_and_it_reads_as_zero():
    # First live trace run (2026-10-06): Etherscan's getLogs gives logIndex and
    # transactionIndex "0x" for zero, so every deposit that was the first log of
    # its transaction crashed the provenance step in 30 seconds.
    from src.boundary import bridge2
    assert logs.to_int("0x") == 0 and logs.to_int("0X") == 0
    row = {"address": bridge2.USDC, "data": "0x" + format(250_000 * 10**6, "064x"),
           "topics": [bridge2.TOPIC_TRANSFER, bridge2.topic_address("0x" + "5" * 40),
                      bridge2.topic_address(bridge2.BRIDGE)],
           "blockNumber": "0x1e8f2a3d", "timeStamp": "0x6900aa00", "logIndex": "0x",
           "transactionIndex": "0x", "transactionHash": "0x" + "ab" * 32}
    assert bridge2.decode_deposit(row)["usd"] == 250_000.0
