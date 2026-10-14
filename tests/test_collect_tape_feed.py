"""The tape fed continuously from SQD's fills portal, and the co-firing fold."""

import pytest

from scripts import collect_tape as ct
from src import cofire
from src import fills_feed as ff

HIM = "0x45d26f28196d226497130c4bac709d808fed4029"
W1 = "0x" + "1" * 40


def _feed_result(last_block, fills):
    return {"fills": fills, "last_block": last_block, "last_ts": 5_000, "requests": 1,
            "overloads": 0, "bytes": 10, "complete": True}


def _fill(user, t, h="0xh", coin="NEAR", crossed=True):
    return {"user": user, "coin": coin, "px": 4.0, "sz": 250.0, "time": t, "side": "A",
            "hash": h, "tid": 1, "crossed": crossed}


def test_fetch_feed_window_starts_behind_the_head_and_advances_the_cursor_past_what_was_read():
    seen, written = {}, []

    def stream(start, to, *, coins, max_requests):
        seen.update(start=start, to=to, coins=coins)
        return _feed_result(start + 10, [_fill(W1, 1000, "0xa"), _fill(W1, 2000, "0xb")])
    out = ct.fetch_feed_window(["NEAR"], head=100_000, stream=stream, cursor=lambda: 0,
                               write=written.append)
    assert seen["start"] == 100_000 - ff.blocks_for_minutes(ct.FEED_BACKFILL_MINUTES)
    assert seen["to"] == 100_000 and seen["coins"] == ["NEAR"]
    assert written == [seen["start"] + 11], "the cursor is the next unread block"
    assert len(out["orders"]) == 2 and out["seconds"] == 1.0 and out["behind_blocks"] > 0


def test_fetch_feed_window_reads_at_most_the_catch_up_bound_per_run():
    seen = {}

    def stream(start, to, *, coins, max_requests):
        seen.update(start=start, to=to)
        return _feed_result(to, [_fill(W1, 1000)])
    ct.fetch_feed_window(["NEAR"], head=1_000_000, stream=stream, cursor=lambda: 500_000,
                         write=lambda v: None)
    assert seen["start"] == 500_000
    assert seen["to"] == 500_000 + ff.blocks_for_minutes(ct.FEED_MAX_MINUTES) - 1


def test_fetch_feed_window_leaves_the_cursor_alone_when_nothing_was_read():
    written = []
    with pytest.raises(ff.FeedError):
        ct.fetch_feed_window(["NEAR"], head=100, stream=lambda *a, **k: _feed_result(None, []),
                             cursor=lambda: 50, write=written.append)
    assert written == []


def test_a_cursor_ahead_of_the_head_is_an_error_not_a_skip():
    with pytest.raises(ff.FeedError):
        ct.fetch_feed_window(["NEAR"], head=100, stream=lambda *a, **k: _feed_result(5, []),
                             cursor=lambda: 500, write=lambda v: None)


def test_build_report_accepts_feed_orders_and_names_the_source():
    orders = [{"wallet": W1, "coin": "NEAR", "side": "A", "base_size": 250.0, "t": i * 1750,
               "first_px": 4.0, "taker": True} for i in range(40)]
    report = ct.build_report([], {"NEAR": {"size": 250}}, seconds=70, markets=["NEAR"],
                             orders=orders, source="sqd")
    assert report["source"] == "sqd" and report["hit_count"] == 1 and report["taker_orders"] == 40


def test_fold_and_census_takes_feed_orders_directly():
    config = {"target_wallet": HIM, "known_self_wallets": [], "excluded_addresses": []}
    sig = {"clip_table": {"NEAR": {"size": 250}}, "clip_notionals": {"NEAR": 1000.0}}
    orders = [{"wallet": W1, "coin": "NEAR", "side": "A", "base_size": 250.0, "t": i * 1750,
               "first_px": 4.0, "taker": True} for i in range(10)]
    out = ct.fold_and_census([], sig, config, orders=orders, now_ms=1_000)
    assert W1 in out["sketches"]["wallets"]
    assert out["window"]["touched"] == 1


def test_fold_cofire_registers_a_hit_as_a_tape_cofire_candidate(tmp_path):
    from src.candidate_registry import iter_candidates
    twin = "0x" + "2" * 40
    config = {"target_wallet": HIM, "known_self_wallets": [], "excluded_addresses": []}
    state = cofire.empty_state()
    out = None
    for w in range(3):
        base = 10_000_000 + w * 1_000_000
        his = [{"wallet": HIM, "coin": "ZEC", "side": "A", "base_size": 1.0, "t": base + i * 1750,
                "first_px": 10.0, "taker": True} for i in range(12)]
        twin_orders = [{**o, "wallet": twin, "t": o["t"] - 200} for o in his]
        filler = [{"wallet": "0x" + "4" * 40, "coin": "BTC", "side": "B", "base_size": 1.0,
                   "t": base + i * 100, "first_px": 1.0, "taker": True} for i in range(1500)]
        out = ct.fold_cofire(his + twin_orders + filler, config, state=state, now_ms=base,
                             data_dir=tmp_path)
    assert out["registered"] == [twin]
    rows = iter_candidates(tmp_path)
    assert [r["wallet"] for r in rows] == [twin] and "tape_cofire" in rows[0]["discovery_sources"]
    assert out["summary"]["his_windows"] == 3


def test_load_cofire_state_refuses_a_damaged_file_but_starts_empty_on_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "COFIRE_FILE", tmp_path / "cofire.json")
    assert ct.load_cofire_state() == cofire.empty_state()
    (tmp_path / "cofire.json").write_text("{not json")
    with pytest.raises(ValueError):
        ct.load_cofire_state()
