# tests/test_cctp_feed.py
"""The Circle deposit feed: the forwarder's ledger, walked incrementally.

Row shapes captured live from 0x6b9e7731… on 2026-09-12. A page is 2,000
rows ascending by time, half `spotTransfer` credits and half `send`s; a page
shorter than that is the end of the data; past the present the venue
answers an empty list."""

from src import cctp_feed as cf

F = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
SYS = "0x2000000000000000000000000000000000000000"


def _nosleep(seconds):
    pass


def _send(t_ms, dest, amount, h=None, user=F, token="USDC"):
    return {"time": t_ms, "hash": h or f"0x{t_ms:x}",
            "delta": {"type": "send", "user": user, "destination": dest, "sourceDex": "spot",
                      "destinationDex": "", "token": token, "amount": str(amount),
                      "usdcValue": str(amount), "fee": "0.0", "nativeTokenFee": "0.0"}}


def _credit(t_ms, amount):
    return {"time": t_ms, "hash": f"0xc{t_ms:x}",
            "delta": {"type": "spotTransfer", "token": "USDC", "amount": str(amount),
                      "usdcValue": str(amount), "user": SYS, "destination": F, "fee": "0.0"}}


# --- parse_page -------------------------------------------------------------

def test_a_page_yields_the_forwarders_large_usdc_sends_to_fresh_accounts():
    rows = [
        _credit(1000, 250_000),
        _send(1001, "0xFRESH", 250_000, h="0xa"),
        _send(1002, T, 7_000_000, h="0xb"),                     # the target: not fresh
        _send(1003, "0xknown", 300_000, h="0xc"),                # a known wallet of his
        _send(1004, "0xsmall", 49_999, h="0xd"),                 # below the floor
        _send(1005, "0xhype", 900_000, h="0xe", token="HYPE"),   # not USDC
        _send(1006, "0xother", 900_000, h="0xf", user="0xsomeoneelse"),
        _send(1007, SYS, 900_000, h="0xg"),                      # a system address
        "garbage",
    ]
    got = cf.parse_page(rows, F.upper(), excluded={T, "0xKNOWN"})
    assert got == [{"wallet": "0xfresh", "amount": 250_000.0, "ts": 1, "hash": "0xa", "via": "cctp"}]


# --- walk -------------------------------------------------------------------

def _venue(pages):
    """A fake venue: pages keyed by startTime, recording every call."""
    calls = []

    def post(body):
        calls.append(body)
        return pages.get(body["startTime"], [])
    return post, calls


def test_walk_advances_past_full_pages_and_stops_at_a_short_one():
    full = [_send(10_000 + i, f"0x{i + 1:040x}", 100_000) for i in range(cf.PAGE_ROWS)]
    tail = [_send(20_000, "0xlast", 100_000, h="0xlast")]
    post, calls = _venue({0: full, 10_000 + cf.PAGE_ROWS - 1 + 1: tail})

    deposits, cursor, err = cf.walk(post, F, 0, 99_999, excluded=set(), max_calls=10)

    assert err is None and cursor == 99_999
    assert len(deposits) == cf.PAGE_ROWS + 1
    assert [c["startTime"] for c in calls] == [0, 10_000 + cf.PAGE_ROWS]
    assert all(c["endTime"] == 99_999 and c["user"] == F for c in calls)


def test_walk_reports_an_exhausted_budget_and_keeps_the_cursor_where_it_stopped():
    full = [_send(10_000 + i, f"0x{i + 1:040x}", 100_000) for i in range(cf.PAGE_ROWS)]
    post, calls = _venue({0: full, 10_000 + cf.PAGE_ROWS: full})

    deposits, cursor, err = cf.walk(post, F, 0, 99_999, excluded=set(), max_calls=1)

    assert "call budget (1) exhausted" in err and "incomplete" in err
    assert cursor == 10_000 + cf.PAGE_ROWS          # the next unread millisecond
    assert len(deposits) == cf.PAGE_ROWS and len(calls) == 1


def test_walk_reports_a_non_list_answer_as_unreadable_not_empty():
    deposits, cursor, err = cf.walk(lambda body: {"error": "rate limited"}, F, 5, 99_999,
                                    excluded=set(), max_calls=3)
    assert deposits == [] and cursor == 5 and err.startswith("unreadable at 5")

    def boom(body):
        raise ConnectionError("down")
    deposits, cursor, err = cf.walk(boom, F, 5, 99_999, excluded=set(), max_calls=3)
    assert cursor == 5 and "ConnectionError" in err


def test_walk_always_moves_forward_when_a_whole_page_shares_one_timestamp():
    same = [_send(500, f"0x{i + 1:040x}", 100_000) for i in range(cf.PAGE_ROWS)]
    post, calls = _venue({0: same, 501: []})
    deposits, cursor, err = cf.walk(post, F, 0, 99_999, excluded=set(), max_calls=5)
    assert err is None and [c["startTime"] for c in calls] == [0, 501]


def test_walk_reads_nothing_when_already_at_the_present():
    post, calls = _venue({})
    deposits, cursor, err = cf.walk(post, F, 1000, 1000, excluded=set(), max_calls=5)
    assert (deposits, cursor, err) == ([], 1000, None) and calls == []


# --- merge / refresh_pool --------------------------------------------------

def test_merge_unions_by_hash_prunes_the_window_and_orders_by_time():
    old = [{"wallet": "0xa", "amount": 1.0, "ts": 50, "hash": "0x1"},
           {"wallet": "0xb", "amount": 2.0, "ts": 150, "hash": "0x2"}]
    new = [{"wallet": "0xb", "amount": 2.0, "ts": 150, "hash": "0x2"},
           {"wallet": "0xc", "amount": 3.0, "ts": 120, "hash": "0x3"}]
    assert [d["hash"] for d in cf.merge(old, new, cutoff_s=100)] == ["0x3", "0x2"]


def test_refresh_pool_walks_from_the_cutoff_first_then_resumes_from_its_cursor(tmp_path):
    path = tmp_path / "cctp_pool.json"
    now_ms = 10_000_000_000
    cutoff_ms = now_ms - cf.POOL_DAYS * 86400 * 1000
    seen = []

    def post(body):
        if body["type"] == "spotMeta":
            return {"tokens": [{"index": 0, "evmContract": {"address": F.upper()}}]}
        seen.append(body["startTime"])
        return [_send(body["endTime"] - 1000, "0xfresh", 200_000, h=f"0x{body['endTime']}")]

    deposits, err = cf.refresh_pool(post, excluded={T}, now_ms=now_ms, path=path, max_calls=5, sleep=_nosleep)
    assert err is None and seen == [cutoff_ms]
    assert [d["wallet"] for d in deposits] == ["0xfresh"]
    pool = cf.load_pool(path)
    assert pool["forwarder"] == F and pool["cursor_ms"] == now_ms

    later = now_ms + 60_000
    deposits, err = cf.refresh_pool(post, excluded={T}, now_ms=later, path=path, max_calls=5, sleep=_nosleep)
    assert seen[-1] == now_ms                         # resumed, not re-walked
    assert len(deposits) == 2 and cf.load_pool(path)["cursor_ms"] == later


def test_refresh_pool_starts_over_when_the_forwarder_changed(tmp_path):
    path = tmp_path / "cctp_pool.json"
    now_ms = 10_000_000_000
    path.write_text('{"forwarder": "0xold", "cursor_ms": 9999999999, "deposits": [{"wallet": "0xstale", "amount": 1.0, "ts": 9999999, "hash": "0xs"}]}')
    seen = []

    def post(body):
        if body["type"] == "spotMeta":
            return {"tokens": [{"index": 0, "evmContract": {"address": F}}]}
        seen.append(body["startTime"])
        return []

    deposits, err = cf.refresh_pool(post, excluded=set(), now_ms=now_ms, path=path, max_calls=5, sleep=_nosleep)
    assert deposits == [] and err is None
    assert seen == [now_ms - cf.POOL_DAYS * 86400 * 1000]


def test_refresh_pool_reports_an_incomplete_read_and_keeps_the_partial_pool(tmp_path):
    path = tmp_path / "cctp_pool.json"
    now_ms = 10_000_000_000
    full = [_send(now_ms - 10_000 + i, f"0x{i + 1:040x}", 100_000) for i in range(cf.PAGE_ROWS)]

    def post(body):
        if body["type"] == "spotMeta":
            return {"tokens": [{"index": 0, "evmContract": {"address": F}}]}
        return full

    deposits, err = cf.refresh_pool(post, excluded=set(), now_ms=now_ms, path=path, max_calls=1, sleep=_nosleep)
    assert "exhausted" in err and len(deposits) == cf.PAGE_ROWS
    pool = cf.load_pool(path)
    assert pool["read_error"] == err and pool["cursor_ms"] < now_ms


def test_a_failed_forwarder_lookup_falls_back_and_says_so(tmp_path):
    path = tmp_path / "cctp_pool.json"

    def post(body):
        if body["type"] == "spotMeta":
            raise TimeoutError("slow")
        return []

    deposits, err = cf.refresh_pool(post, excluded=set(), now_ms=10_000_000_000, path=path, max_calls=2, sleep=_nosleep)
    assert deposits == [] and "spotMeta unreadable" in err
    assert cf.load_pool(path)["forwarder"] == cf.FORWARDER_FALLBACK


def test_resolve_forwarder_reads_token_zero_and_falls_back_on_a_bare_answer():
    assert cf.resolve_forwarder(lambda b: {"tokens": [
        {"index": 1, "evmContract": {"address": "0x" + "1" * 40}},
        {"index": 0, "evmContract": {"address": "0x" + "A" * 40}}]}) == ("0x" + "a" * 40, None)
    addr, note = cf.resolve_forwarder(lambda b: {"tokens": [{"index": 0, "evmContract": None}]})
    assert addr == cf.FORWARDER_FALLBACK and "did not name" in note


def test_walk_paces_between_full_pages_but_not_after_the_last():
    full = [_send(10_000 + i, f"0x{i + 1:040x}", 100_000) for i in range(cf.PAGE_ROWS)]
    post, calls = _venue({0: full, 10_000 + cf.PAGE_ROWS: [_send(50_000, "0xz", 100_000)]})
    slept = []
    cf.walk(post, F, 0, 99_999, excluded=set(), max_calls=5, pace_seconds=6.0,
            sleep=slept.append)
    assert slept == [6.0] and len(calls) == 2
