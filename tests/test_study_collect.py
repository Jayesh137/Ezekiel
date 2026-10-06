"""Strict reads: a failed or refused read is never an empty one."""

from src.study import collect

W = "0x" + "b" * 40


def pages(*batches):
    calls = []

    def fetch(body):
        calls.append(body)
        batch = batches[len(calls) - 1]
        return batch if isinstance(batch, dict) and "ok" in batch else {"ok": True, "data": batch}

    fetch.calls = calls
    return fetch


def rows(start, n):
    return [{"time": start + i, "tid": start + i, "oid": start + i} for i in range(n)]


def test_a_short_page_means_everything_up_to_now_was_seen():
    got = collect.read_fills(W, 1_000, 9_000, pages(rows(1_000, 3)))
    assert got["ok"] and got["known_until_ms"] == 9_000 and not got["saturated"]
    assert [f["time"] for f in got["fills"]] == [1_000, 1_001, 1_002]


def test_five_full_pages_mean_the_oldest_span_may_be_missing():
    batches = [rows(1_000 + i * 2_000, 2_000) for i in range(5)]
    got = collect.read_fills(W, 0, 99_999_999, pages(*batches))
    assert got["ok"] and got["saturated"] and got["first_ms"] == 1_000
    assert got["known_until_ms"] == batches[-1][-1]["time"]


def test_pages_overlap_inclusively_and_rows_are_deduped():
    first = rows(1_000, 2_000)
    fetch = pages(first, [first[-1]] + rows(3_000, 10))
    got = collect.read_fills(W, 1_000, 50_000, fetch)
    assert len(got["fills"]) == 2_010
    assert fetch.calls[0]["aggregateByTime"] is True
    assert fetch.calls[1]["startTime"] == first[-1]["time"]


def test_a_failed_read_is_not_an_empty_one():
    got = collect.read_fills(W, 0, 10, pages({"ok": False, "error": "HTTP 500"}))
    assert not got["ok"] and not got["stopped"]
    assert got["fills"] == [] and got["known_until_ms"] is None


def test_a_budget_refusal_is_a_stop_not_a_failure():
    assert collect.read_fills(W, 0, 10, pages({"ok": False, "error": "time_budget"}))["stopped"]
    assert collect.read_orders(W, pages({"ok": False, "error": "rate limited"}))["stopped"]


def test_a_malformed_page_is_a_failure():
    got = collect.read_fills(W, 0, 10, pages([{"no_time": 1}]))
    assert not got["ok"] and "shape" in got["error"]


def test_orders_say_how_far_back_they_vouch():
    entries = [{"order": {"timestamp": 5}, "status": "filled"},
               {"order": {"timestamp": 9}, "status": "open"}]
    got = collect.read_orders(W, pages(entries))
    assert got["ok"] and got["oldest_ms"] == 5 and not got["full"]
    assert not collect.read_orders(W, pages([{"no_order": 1}]))["ok"]


def test_recent_fills_and_ledger_are_strict_too():
    assert collect.read_recent_fills(W, pages([{"time": 1, "tid": 1}]))["fills"] == [
        {"time": 1, "tid": 1}]
    assert not collect.read_recent_fills(W, pages({"ok": False, "error": "x"}))["ok"]
    assert not collect.read_ledger(W, 0, 10, pages({"ok": False, "error": "HTTP 502"}))["ok"]
    got = collect.read_ledger(W, 0, 10, pages([{"time": 3, "hash": "0x1",
                                                "delta": {"type": "deposit"}}]))
    assert got["ok"] and got["known_until_ms"] == 10 and len(got["rows"]) == 1
