"""The SQD fills feed is strict, cursor-safe and produces the tape's order shape."""

import json

import pytest

from src import fills_feed as ff

U = "0x" + "a" * 40


class _Resp:
    def __init__(self, status, text="", headers=None, payload=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self._payload = payload

    def json(self):
        return self._payload


def _page(blocks):
    """blocks: [(number, ts, [fills])] -> JSON lines."""
    return "\n".join(json.dumps({"header": {"number": n, "timestamp": ts}, "fills": fills})
                     for n, ts, fills in blocks)


def _fill(user=U, coin="NEAR", sz="250", px="4.0", t=1000, h="0xh1", crossed=True, side="B"):
    return {"user": user, "coin": coin, "sz": sz, "px": px, "time": t, "hash": h,
            "crossed": crossed, "side": side, "tid": 1}


def test_stream_follows_the_portal_cursor_until_the_range_is_covered():
    calls = []

    def post(url, body):
        calls.append(body["fromBlock"])
        if body["fromBlock"] == 100:
            return _Resp(200, _page([(100, 1, [_fill()]), (150, 2, [_fill(h="0xh2")])]))
        return _Resp(200, _page([(200, 3, [_fill(h="0xh3")])]))
    out = ff.stream(100, 200, coins=["NEAR"], post=post)
    assert calls == [100, 151]
    assert out["complete"] and out["last_block"] == 200 and len(out["fills"]) == 3
    assert out["requests"] == 2 and out["overloads"] == 0


def test_an_overloaded_portal_is_waited_on_then_reported_incomplete_with_the_cursor_unmoved():
    waits = []
    post = lambda url, body: _Resp(529, "overloaded", {"retry-after": "7"})  # noqa: E731
    out = ff.stream(100, 200, post=post, sleep=waits.append, max_overloads=2)
    assert out["complete"] is False and out["last_block"] is None and out["fills"] == []
    assert waits == [7, 7] and out["overloads"] == 3


def test_any_other_failure_raises_never_an_empty_page():
    with pytest.raises(ff.FeedError):
        ff.stream(1, 2, post=lambda url, body: _Resp(500, "boom"))
    with pytest.raises(ff.FeedError):
        ff.stream(1, 2, post=lambda url, body: (_ for _ in ()).throw(ConnectionError("down")))


def test_an_answer_with_no_blocks_means_the_present_was_reached():
    out = ff.stream(1, 10, post=lambda url, body: _Resp(200, ""))
    assert out["complete"] and out["last_block"] is None and out["fills"] == []


def test_a_bound_stops_the_walk_short_and_says_so():
    post = lambda url, body: _Resp(200, _page([(body["fromBlock"], 1, [_fill()])]))  # noqa: E731
    out = ff.stream(1, 1000, post=post, max_requests=3)
    assert out["complete"] is False and out["last_block"] == 3 and out["requests"] == 3


def test_filters_are_sent_lower_cased_and_sorted():
    seen = {}

    def post(url, body):
        seen.update(body)
        return _Resp(200, "")
    ff.stream(1, 2, coins=["xyz:MU", "BTC"], users=["0xABC"], post=post)
    assert seen["fills"] == [{"coin": ["BTC", "xyz:MU"], "user": ["0xabc"]}]
    assert seen["type"] == "hyperliquidFills"


def test_taker_orders_group_crossing_fills_by_hash_and_skip_maker_fills():
    fills = [ff.normalise_fill(_fill(t=1000, sz="100")), ff.normalise_fill(_fill(t=999, sz="150", px="3.9")),
             ff.normalise_fill(_fill(h="0xmaker", crossed=False)),
             ff.normalise_fill(_fill(user="0x" + "b" * 40, h="0xh9", t=2000, side="A"))]
    orders = ff.taker_orders(fills)
    assert [(o["wallet"], o["base_size"], o["t"], o["first_px"], o["side"]) for o in orders] == [
        (U, 250.0, 999, 3.9, "B"), ("0x" + "b" * 40, 250.0, 2000, 4.0, "A")]
    assert all(o["taker"] for o in orders)


def test_normalise_rejects_malformed_fills():
    assert ff.normalise_fill({"user": "nope", "coin": "X", "px": "1", "sz": "1", "time": 1}) is None
    assert ff.normalise_fill({"user": U, "coin": "X", "px": "1", "sz": "0", "time": 1}) is None
    assert ff.normalise_fill({"user": U, "coin": "X", "px": "x", "sz": "1", "time": 1}) is None
    assert ff.normalise_fill("junk") is None


def test_head_is_strict():
    assert ff.head(get=lambda url: _Resp(200, payload={"number": 5})) == 5
    with pytest.raises(ff.FeedError):
        ff.head(get=lambda url: _Resp(503))
    with pytest.raises(ff.FeedError):
        ff.head(get=lambda url: _Resp(200, payload={"number": 0}))
