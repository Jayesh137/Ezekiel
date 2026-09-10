# tests/test_activity.py
"""Global address activity: the measurement that separates a router from a
person and a person from a deposit address."""

from datetime import UTC, datetime, timedelta

from src.chain import activity as act


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _get_factory(info, counters, *, status=200):
    def get(url, timeout=0, headers=None):
        if url.endswith("/counters"):
            return _Resp(status, counters)
        return _Resp(status, info)
    return get


def test_fetch_reads_code_flag_and_counters():
    got = act.fetch_activity(
        "0xAAA", "arbitrum",
        get=_get_factory({"is_contract": True, "name": "SocketGateway"},
                         {"transactions_count": "2190933", "token_transfers_count": "3011170"}))
    assert got == {"is_contract": True, "txs": 2190933, "token_transfers": 3011170,
                   "name": "SocketGateway"}
    assert act.is_busy(got) is True


def test_a_404_is_zero_activity_not_a_failure():
    got = act.fetch_activity("0xAAA", "ethereum", get=_get_factory({}, {}, status=404))
    assert got == {"is_contract": False, "txs": 0, "token_transfers": 0, "name": None}
    assert act.is_busy(got) is False


def test_a_failed_read_is_none_and_unknown():
    got = act.fetch_activity("0xAAA", "ethereum", get=_get_factory({}, {}, status=500))
    assert got is None
    assert act.is_busy(None) is None


def test_an_unknown_chain_is_none():
    assert act.fetch_activity("0xAAA", "bsc") is None


def test_cache_spends_one_lookup_and_persists(tmp_path):
    calls = []

    def fetch(addr, chain):
        calls.append((addr, chain))
        return {"is_contract": False, "txs": 35, "token_transfers": 558, "name": None}

    cache = act.ActivityCache(tmp_path / "act.json", fetch, sleep=lambda s: None)
    a = cache.get("0xAbC", "arbitrum")
    b = cache.get("0xabc", "arbitrum")
    assert a["txs"] == 35 and b["txs"] == 35
    assert calls == [("0xAbC", "arbitrum")]
    # A second instance reads the same file and spends nothing.
    again = act.ActivityCache(tmp_path / "act.json", fetch, sleep=lambda s: None)
    assert again.get("0xabc", "arbitrum")["txs"] == 35
    assert calls == [("0xAbC", "arbitrum")]


def test_a_failed_lookup_is_not_cached(tmp_path):
    cache = act.ActivityCache(tmp_path / "act.json", lambda a, c: None, sleep=lambda s: None)
    assert cache.get("0xabc", "arbitrum") is None
    assert cache.cached("0xabc", "arbitrum") is None


def test_the_lookup_budget_bounds_a_run(tmp_path):
    calls = []

    def fetch(addr, chain):
        calls.append(addr)
        return {"is_contract": False, "txs": 1, "token_transfers": 0, "name": None}

    cache = act.ActivityCache(tmp_path / "act.json", fetch, max_lookups=2, sleep=lambda s: None)
    for a in ("0x1", "0x2", "0x3"):
        cache.get(a, "arbitrum")
    assert calls == ["0x1", "0x2"]
    assert cache.get("0x3", "arbitrum") is None


def test_a_stale_reading_is_refreshed(tmp_path):
    readings = iter([
        {"is_contract": False, "txs": 10, "token_transfers": 0, "name": None},
        {"is_contract": False, "txs": 50_000, "token_transfers": 0, "name": None},
    ])
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    cache = act.ActivityCache(tmp_path / "act.json", lambda a, c: next(readings),
                              sleep=lambda s: None, now=lambda: clock[0])
    assert cache.get("0xabc", "arbitrum")["txs"] == 10
    clock[0] += timedelta(days=act.REFRESH_DAYS + 1)
    assert cache.get("0xabc", "arbitrum")["txs"] == 50_000
    assert act.is_busy(cache.cached("0xabc", "arbitrum")) is True
