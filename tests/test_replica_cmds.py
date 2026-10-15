"""The SQD replica-command feed is strict, cursor-safe and yields explorer-shaped rows."""

import json

import pytest

from src import hl_actions
from src import replica_cmds as rc

U = "0x" + "a" * 40
V = "0x" + "b" * 40


class _Resp:
    def __init__(self, status, text="", headers=None, payload=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self._payload = payload

    def json(self):
        return self._payload


def _page(blocks):
    """blocks: [(number, ts_ms, [actions])] -> JSON lines."""
    return "\n".join(json.dumps({"header": {"number": n, "timestamp": ts}, "actions": acts})
                     for n, ts, acts in blocks)


def _act(index=0, user=U, kind="usdSend", status="ok", nonce=1_700_000_000_000, vault=None,
         **payload):
    action = {"type": kind, **payload}
    if kind == "usdSend" and "destination" not in payload:
        action.update({"destination": V, "amount": "5", "time": nonce,
                       "signatureChainId": "0xa4b1", "hyperliquidChain": "Mainnet"})
    return {"actionIndex": index, "user": user, "action": action, "nonce": nonce,
            "vaultAddress": vault, "status": status}


def test_stream_follows_the_portal_cursor_until_the_range_is_covered():
    calls = []

    def post(url, body):
        calls.append((body["fromBlock"], body["toBlock"]))
        assert body["type"] == "hyperliquidReplicaCmds"
        assert body["actions"] == [{"user": [U], "actionType": ["approveAgent", "usdSend"]}]
        if body["fromBlock"] == 100:
            return _Resp(200, _page([(100, 1000, [_act(0)]), (150, 2000, [_act(0, kind="approveAgent",
                                                                             agentAddress=V)])]))
        return _Resp(200, _page([(200, 3000, [])]))
    out = rc.stream(100, 200, users=[U.upper()], action_types=["usdSend", "approveAgent"], post=post)
    assert calls == [(100, 200), (151, 200)]
    assert out["complete"] and out["last_block"] == 200 and out["last_ts"] == 3000
    assert [r["type"] for r in out["actions"]] == ["usdSend", "approveAgent"]
    assert out["actions"][0] == {
        "user": U, "type": "usdSend", "action": out["actions"][0]["action"],
        "nonce": 1_700_000_000_000, "vault_address": None, "status": "ok",
        "block": 100, "index": 0, "time": 1000,
    }
    assert out["requests"] == 2 and out["overloads"] == 0


def test_an_overloaded_portal_is_waited_on_then_reported_incomplete_with_the_cursor_unmoved():
    waits, spans = [], []

    def post(url, body):
        spans.append(body["toBlock"] - body["fromBlock"] + 1)
        return _Resp(529, "overloaded", {"retry-after": "7"})
    out = rc.stream(100, 100_099, post=post, sleep=waits.append, max_overloads=2)
    assert out["complete"] is False and out["last_block"] is None and out["actions"] == []
    assert spans == [100_000] * 3      # the same shape is re-asked, never narrowed
    assert waits == [7, 7] and out["overloads"] == 3


def test_a_rate_limit_is_waited_on_not_raised_and_the_bound_is_on_consecutive_refusals():
    waits = []
    answers = iter([_Resp(429, "slow"), _Resp(200, _page([(100, 1, [_act(0)])])),
                    _Resp(429, "slow", {"retry-after": "3"}), _Resp(200, _page([(200, 2, [])]))])
    out = rc.stream(100, 200, post=lambda url, body: next(answers), sleep=waits.append,
                    max_overloads=1)
    assert out["complete"] and out["last_block"] == 200 and len(out["actions"]) == 1
    assert waits == [rc.RATE_LIMIT_WAIT, 3] and out["overloads"] == 2


def test_any_other_failure_raises_never_an_empty_page():
    with pytest.raises(rc.FeedError):
        rc.stream(1, 2, post=lambda url, body: _Resp(500, "boom"))
    with pytest.raises(rc.FeedError):
        rc.stream(1, 2, post=lambda url, body: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(rc.FeedError):
        rc.stream(1, 2, post=lambda url, body: _Resp(200, '{"error": {"code": "x"}}'))


def test_bounds_stop_the_walk_short_and_say_so():
    pages = iter([_page([(100, 1, [_act(0)])]), _page([(101, 2, [_act(0)])])])
    out = rc.stream(100, 500, post=lambda url, body: _Resp(200, next(pages)), max_requests=1)
    assert out["complete"] is False and out["last_block"] == 100 and len(out["actions"]) == 1


def test_a_malformed_action_row_is_skipped_not_fatal():
    text = _page([(100, 1, [_act(0), {"user": None, "action": "nope"}, {"actionIndex": 1,
                                                                        "user": U,
                                                                        "action": {},
                                                                        "status": "ok"}])])
    rows, last, ts = rc.parse_lines(text)
    assert len(rows) == 1 and last == 100 and ts == 1


def test_explorer_row_shape_is_what_hl_actions_parses():
    row = rc.normalise_action(_act(3, kind="sendToEvmWithData", destinationRecipient=V,
                                  destinationChainId=5, amount="1000", token="USDC",
                                  signatureChainId="0x66eee"), 1234, 99_000)
    ex = rc.as_explorer_row(row)
    assert ex["hash"] == "replica:1234:3" and ex["time"] == 99_000 and ex["block"] == 1234
    assert ex["error"] is None and ex["user"] == U
    parsed = hl_actions.own_actions([ex], U)
    assert len(parsed) == 1
    assert parsed[0]["destination"] == V and parsed[0]["destination_chain"] == "solana"
    assert parsed[0]["hash"] == "replica:1234:3"
    failed = rc.as_explorer_row(rc.normalise_action(_act(0, status="err"), 1, 1))
    assert failed["error"] == "err"
    assert hl_actions.own_actions([failed], U)[0]["error"] == "err"


def test_block_at_reads_the_portal_and_is_strict():
    def get(url):
        assert url.endswith("/timestamps/1769011200/block")
        return _Resp(200, payload={"block_number": 868700123})
    assert rc.block_at(1769011200, get=get) == 868700123
    assert rc.block_at(1769011200 * 1000, get=get) == 868700123   # ms are folded to seconds
    with pytest.raises(rc.FeedError):
        rc.block_at(1, get=lambda url: _Resp(503, "down"))
    with pytest.raises(rc.FeedError):
        rc.block_at(1, get=lambda url: _Resp(200, payload={"nope": 1}))


def test_head_is_strict():
    assert rc.head(get=lambda url: _Resp(200, payload={"number": 5})) == 5
    with pytest.raises(rc.FeedError):
        rc.head(get=lambda url: _Resp(200, payload={"number": 0}))
    with pytest.raises(rc.FeedError):
        rc.head(get=lambda url: _Resp(429, "slow down"))


def test_segments_split_a_range_into_fixed_pieces_covering_it_exactly():
    segs = rc.segments(100, 1_000, 400)
    assert segs == [(100, 499), (500, 899), (900, 1_000)]
    assert rc.segments(5, 5, 10) == [(5, 5)]
    assert rc.segments(10, 5, 10) == []


def test_sweep_parallel_reads_every_segment_and_reports_the_incomplete_ones():
    def post(url, body):
        if body["fromBlock"] >= 500:
            return _Resp(529, "overloaded", {"retry-after": "1"})
        return _Resp(200, _page([(body["toBlock"], 1, [_act(0)])]))
    out = rc.sweep_parallel(100, 899, segment_blocks=400, workers=2, post=post,
                            sleep=lambda s: None, max_overloads=0)
    assert out["done"] == [(100, 499)] and out["incomplete"] == [(500, 899)]
    assert len(out["actions"]) == 1 and out["requests"] >= 2


def test_sweep_parallel_can_take_the_newest_segment_first():
    order = []

    def post(url, body):
        order.append(body["fromBlock"])
        return _Resp(200, _page([(body["toBlock"], 1, [])]))
    rc.sweep_parallel(100, 899, segment_blocks=400, workers=1, post=post, newest_first=True)
    assert order == [900 - 100, 500, 100][1:] or order == [500, 100]


def test_the_status_filter_is_a_single_string_and_block_at_waits_on_a_refusal():
    bodies = []

    def post(url, body):
        bodies.append(body)
        return _Resp(200, _page([(5, 1, [])]))
    rc.stream(1, 5, status="ok", action_types=["usdSend"], post=post)
    assert bodies[0]["actions"] == [{"actionType": ["usdSend"], "status": "ok"}]
    answers = iter([_Resp(529, "busy"), _Resp(429, "slow"), _Resp(200, payload={"block_number": 7})])
    waits = []
    assert rc.block_at(1769011200, get=lambda url: next(answers), sleep=waits.append) == 7
    assert waits == [rc.OVERLOAD_WAIT, rc.RATE_LIMIT_WAIT]
    with pytest.raises(rc.FeedError):
        rc.block_at(1, get=lambda url: _Resp(529, "busy"), sleep=lambda s: None, max_overloads=1)
