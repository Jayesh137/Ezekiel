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


# --- time -> block ----------------------------------------------------------

def _headers_only(blocks):
    return "\n".join(json.dumps({"header": {"number": n, "timestamp": ts}}) for n, ts in blocks)


def _chain_post(rate_ms_per_block=70, refuse=None, log=None):
    """A portal whose block n sits at START_TS + n*rate; `refuse` makes some
    request shapes answer 529 (measured: refusal is per request shape)."""
    def post(url, body):
        if log is not None:
            log.append((body["fromBlock"], body["toBlock"]))
        if refuse and refuse(body):
            return _Resp(529, "overloaded", {"retry-after": "3"})
        n = body["fromBlock"]
        ts = ff.START_TS_MS + (n - ff.START_BLOCK) * rate_ms_per_block
        return _Resp(200, _headers_only([(n, ts), (n + 1, ts + rate_ms_per_block)]))
    return post


def test_block_at_converges_by_interpolation_and_caches_checkpoints():
    log = []
    idx = ff.BlockIndex()
    head = (ff.START_BLOCK + 10_000_000, ff.START_TS_MS + 10_000_000 * 70)
    target_ts = ff.START_TS_MS + 4_000_000 * 70 + 35
    block = idx.block_at(target_ts, post=_chain_post(log=log), head=head)
    assert abs(block - (ff.START_BLOCK + 4_000_000)) <= 1
    assert len(log) <= 3, log  # linear chain: the first guess lands
    # The checkpoints are kept, sorted and unique, so the next lookup is cheaper.
    assert idx.points == sorted(set(idx.points)) and len(idx.points) >= 2
    assert idx.block_at(target_ts, post=_chain_post(log=log), head=head) == block


def test_block_at_past_the_head_is_the_head_and_a_dead_portal_raises():
    idx = ff.BlockIndex()
    head = (ff.START_BLOCK + 10_000_000, ff.START_TS_MS + 10_000_000 * 70)
    assert idx.block_at(head[1] + 99_999_999, post=_chain_post(), head=head) == head[0]
    with pytest.raises(ff.FeedError):
        idx.block_at(ff.START_TS_MS + 5_000_000 * 70, post=lambda url, body: _Resp(500, "boom"),
                     head=head)


def test_a_refused_header_read_is_retried_with_another_shape_before_waiting():
    waits = []
    # Refuse every 400-wide request; serve the narrow ones.
    post = _chain_post(refuse=lambda body: body["toBlock"] - body["fromBlock"] >= 400)
    number, ts = ff.header_at(ff.START_BLOCK + 50, post=post, sleep=waits.append)
    assert number == ff.START_BLOCK + 50 and waits == []
    # Refused at every shape: waits retry-after, bounded, then raises.
    always = _chain_post(refuse=lambda body: True)
    with pytest.raises(ff.FeedError):
        ff.header_at(ff.START_BLOCK, post=always, sleep=waits.append)
    assert waits and all(w == 3 for w in waits) and len(waits) <= ff.MAX_OVERLOADS


def test_stream_halves_a_refused_range_and_widens_again_once_served():
    asked = []

    def post(url, body):
        span = body["toBlock"] - body["fromBlock"] + 1
        asked.append(span)
        if span > 4_000:
            return _Resp(529, "overloaded", {"retry-after": "9"})
        return _Resp(200, _page([(body["toBlock"], 1, [_fill(h=f"0x{body['toBlock']}")])]))
    waits = []
    out = ff.stream(0, 20_000, coins=["NEAR"], post=post, sleep=waits.append)
    assert out["complete"] and out["last_block"] == 20_000 and waits == []
    assert asked[0] == 20_001 and min(asked) >= ff.MIN_NARROW_BLOCKS
    assert out["overloads"] == 0, "a refusal answered by narrowing is not an overload wait"
