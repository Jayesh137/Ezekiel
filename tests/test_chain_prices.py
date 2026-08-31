# tests/test_chain_prices.py
"""The CoinGecko price source: every failure mode, the budget, and the wiring.

No test here may make a real HTTP request -- `transport` is always a fake.
Real API behaviour this module's design depends on (date format, the 365-day
keyless cutoff, the demo-key param name) was verified live and is written up
in docs/superpowers/price-source-report.md; these tests pin the CODE's
behaviour against fakes shaped like what that research found, not against the
network itself.

`coingecko_price_lookup(...)` returns a plain `price_lookup(symbol, date_str)`
callable (not a PriceCache) -- see its docstring for why budget-exhaustion has
to be signalled by raising rather than returning None, so tests call it
directly (`cache("ETH", date)`), never `.get(...)`.
"""
import json
from datetime import UTC, datetime, timedelta

import pytest
import requests

from src.chain import collect, prices
from src.chain.pagination import WalkResult

ARB = {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0}


def days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).strftime("%Y-%m-%d")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def history_payload(usd=2500.0):
    return {"id": "ethereum", "symbol": "eth", "name": "Ethereum",
            "market_data": {"current_price": {"usd": usd}}}


def recording_transport(responses=None):
    """A fake `requests.get`. `responses` is consumed one-per-call; once
    exhausted (or if never given), every further call succeeds with a $2500
    history payload -- enough for tests that only care about call COUNT."""
    calls = []
    queue = list(responses or [])

    def transport(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        item = queue.pop(0) if queue else FakeResponse(200, history_payload())
        if isinstance(item, Exception):
            raise item
        return item

    transport.calls = calls
    return transport


@pytest.fixture(autouse=True)
def _no_coingecko_key(monkeypatch):
    """Default every test to the keyless path; a test that needs a key sets it
    explicitly. Without this, a machine with COINGECKO_API_KEY exported for
    some other purpose would silently change which code path these tests
    exercise."""
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)


def build(tmp_path, transport, **kw):
    kw.setdefault("sleep", lambda seconds: None)
    return prices.coingecko_price_lookup(tmp_path, transport=transport, **kw)


# --- the one success path, and the cache it must produce ---------------------

def test_a_successful_fetch_returns_a_float_and_is_cached(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    first = price_lookup("ETH", days_ago(10))
    second = price_lookup("ETH", days_ago(10))

    assert first == 2500.0
    assert isinstance(first, float)
    assert second == 2500.0
    assert len(transport.calls) == 1, "a repeat lookup must not make a second request"


def test_a_fresh_instance_reusing_the_same_directory_also_makes_no_request(tmp_path):
    """PriceCache's own contract (tests/test_chain_assets.py), exercised through
    this module's factory rather than constructed by hand."""
    build(tmp_path, recording_transport())("ETH", days_ago(10))

    transport2 = recording_transport()
    reloaded = build(tmp_path, transport2)
    assert reloaded("ETH", days_ago(10)) == 2500.0
    assert transport2.calls == []


def test_the_date_sent_to_coingecko_is_day_month_year_not_iso(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    price_lookup("ETH", "2026-06-16")

    assert transport.calls[0]["params"]["date"] == "16-06-2026"
    assert transport.calls[0]["params"]["localization"] == "false"


# --- every failure mode collapses to None, never 0.0 --------------------------

@pytest.mark.parametrize("response", [
    FakeResponse(status_code=500),
    FakeResponse(status_code=404),
    FakeResponse(status_code=429),
    FakeResponse(status_code=401, payload={"error": {"status": {"error_code": 10012}}}),
])
def test_non_200_status_yields_none_never_zero(tmp_path, response):
    price_lookup = build(tmp_path, recording_transport(responses=[response]))

    result = price_lookup("ETH", days_ago(5))

    assert result is None
    assert result != 0.0


def test_a_timeout_yields_none(tmp_path):
    price_lookup = build(tmp_path, recording_transport(
        responses=[requests.exceptions.Timeout("slow")]))
    result = price_lookup("ETH", days_ago(5))
    assert result is None
    assert result != 0.0


def test_a_connection_error_yields_none(tmp_path):
    price_lookup = build(tmp_path, recording_transport(
        responses=[requests.exceptions.ConnectionError("dns failed")]))
    assert price_lookup("ETH", days_ago(5)) is None


def test_malformed_json_yields_none(tmp_path):
    price_lookup = build(tmp_path, recording_transport(
        responses=[FakeResponse(200, json_error=ValueError("not json"))]))
    assert price_lookup("ETH", days_ago(5)) is None


@pytest.mark.parametrize("payload", [
    {},
    {"market_data": {}},
    {"market_data": {"current_price": {}}},
    {"market_data": {"current_price": {"usd": None}}},
    {"market_data": {"current_price": {"usd": "not-a-number"}}},
    {"market_data": None},
    {"market_data": "not-a-dict"},
    None,
    "not-a-dict-at-all",
])
def test_missing_or_malformed_price_field_yields_none(tmp_path, payload):
    price_lookup = build(tmp_path, recording_transport(responses=[FakeResponse(200, payload)]))
    result = price_lookup("ETH", days_ago(5))
    assert result is None
    assert result != 0.0


@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), float("-inf"), -100.0, 0.0, True])
def test_a_nonfinite_nonpositive_or_boolean_price_is_rejected(tmp_path, bad_price):
    price_lookup = build(tmp_path, recording_transport(
        responses=[FakeResponse(200, history_payload(bad_price))]))
    assert price_lookup("ETH", days_ago(5)) is None


def test_an_unknown_symbol_returns_none_without_a_request(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    assert price_lookup("SCAMCOIN", days_ago(5)) is None
    assert transport.calls == []


def test_an_unparseable_date_returns_none_without_a_request(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    assert price_lookup("ETH", "not-a-date") is None
    assert price_lookup("ETH", "") is None
    assert transport.calls == []


# --- the free tier's historical window -----------------------------------------

def test_a_date_past_the_keyless_window_returns_none_without_a_request(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    assert price_lookup("ETH", days_ago(400)) is None
    assert transport.calls == [], "a date this old is known to fail; it must not spend a request"


def test_a_date_exactly_at_the_confirmed_boundary_is_still_attempted(tmp_path):
    """365 days back is documented ('within the past 365 days') and this
    project's own live check confirmed 366 fails -- so 365 itself must still
    be tried rather than swallowed by an over-eager margin. See
    FREE_TIER_HISTORY_DAYS's docstring for why rounding down here would be a
    permanent, not a recoverable, mistake."""
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    assert price_lookup("ETH", days_ago(365)) == 2500.0
    assert len(transport.calls) == 1


def test_a_recent_date_is_attempted_normally(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    assert price_lookup("ETH", days_ago(10)) == 2500.0
    assert len(transport.calls) == 1


def test_a_date_past_the_window_is_still_attempted_when_a_key_is_present(tmp_path, monkeypatch):
    """The keyed tier's exact historical range is not confirmed -- CoinGecko's
    own materials disagree (365 days vs. 2 years) -- so with a key present we
    defer entirely to CoinGecko's own error response rather than guess and
    risk a permanent false-negative cache entry for history a key might
    actually reach."""
    monkeypatch.setenv("COINGECKO_API_KEY", "demo-key-123")
    transport = recording_transport(
        responses=[FakeResponse(401, {"error": {"status": {"error_code": 10012}}})])
    price_lookup = build(tmp_path, transport)

    assert price_lookup("ETH", days_ago(400)) is None
    assert len(transport.calls) == 1, "with a key, the request is attempted rather than assumed"


# --- the per-run budget ---------------------------------------------------------

def test_the_per_run_request_count_budget_is_enforced(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport, max_requests=2, max_seconds=1000)

    assert price_lookup("ETH", days_ago(1)) == 2500.0
    assert price_lookup("WBTC", days_ago(2)) == 2500.0
    assert price_lookup("ARB", days_ago(3)) is None            # budget spent

    assert len(transport.calls) == 2


def test_the_per_run_wall_clock_budget_is_enforced(tmp_path):
    clock_value = {"t": 0.0}
    transport = recording_transport()
    price_lookup = build(tmp_path, transport, max_requests=1000, max_seconds=5,
                         clock=lambda: clock_value["t"])

    assert price_lookup("ETH", days_ago(1)) == 2500.0
    clock_value["t"] = 10.0                                  # past the 5s ceiling
    assert price_lookup("WBTC", days_ago(2)) is None

    assert len(transport.calls) == 1


def test_budget_exhaustion_returns_none_without_touching_the_cache_file(tmp_path):
    """A budget-exhausted lookup must not be confused with a confirmed miss --
    the next RUN should still be free to try it, so it must not be written to
    disk as a cached None the way a real fetch failure is."""
    transport = recording_transport()
    price_lookup = build(tmp_path, transport, max_requests=0, max_seconds=1000)

    assert price_lookup("ETH", days_ago(1)) is None
    assert transport.calls == []
    assert not (tmp_path / "ETH.json").exists()


def test_a_budget_exhausted_date_is_fetched_for_real_by_the_next_run(tmp_path):
    """The behaviour test_budget_exhaustion_... only proves indirectly (no
    file written): a SECOND, fresh price_lookup pointed at the same directory
    -- standing in for the next scheduled run -- must still be able to fetch
    it, not find a poisoned None already on disk."""
    transport = recording_transport()
    exhausted = build(tmp_path, transport, max_requests=0, max_seconds=1000)
    assert exhausted("ETH", days_ago(1)) is None
    assert transport.calls == []

    next_run = build(tmp_path, transport, max_requests=10, max_seconds=1000)
    assert next_run("ETH", days_ago(1)) == 2500.0
    assert len(transport.calls) == 1


# --- the API key -----------------------------------------------------------------

def test_missing_api_key_omits_the_demo_key_query_param(tmp_path):
    transport = recording_transport()
    build(tmp_path, transport)("ETH", days_ago(5))

    assert "x_cg_demo_api_key" not in transport.calls[0]["params"]


def test_present_api_key_is_sent_as_the_demo_query_param(tmp_path, monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "demo-key-123")
    transport = recording_transport()
    build(tmp_path, transport)("ETH", days_ago(5))

    assert transport.calls[0]["params"]["x_cg_demo_api_key"] == "demo-key-123"


# --- in-run de-dup across symbols that share a CoinGecko id ----------------------

def test_eth_and_weth_share_one_request_for_the_same_date(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)
    day = days_ago(5)

    assert price_lookup("ETH", day) == 2500.0
    assert price_lookup("WETH", day) == 2500.0

    assert len(transport.calls) == 1
    assert json.loads((tmp_path / "ETH.json").read_text())[day] == 2500.0
    assert json.loads((tmp_path / "WETH.json").read_text())[day] == 2500.0


def test_eth_and_weth_on_different_dates_each_make_their_own_request(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    price_lookup("ETH", days_ago(5))
    price_lookup("WETH", days_ago(6))

    assert len(transport.calls) == 2


# --- throttling ---------------------------------------------------------------

def test_requests_are_throttled_between_calls(tmp_path):
    sleeps = []
    transport = recording_transport()
    price_lookup = prices.coingecko_price_lookup(tmp_path, transport=transport,
                                                 sleep=lambda s: sleeps.append(s))

    price_lookup("ETH", days_ago(1))
    price_lookup("WBTC", days_ago(2))

    assert sleeps == [prices.THROTTLE_SECONDS, prices.THROTTLE_SECONDS]


def test_a_cached_hit_does_not_throttle(tmp_path):
    sleeps = []
    transport = recording_transport()
    price_lookup = prices.coingecko_price_lookup(tmp_path, transport=transport,
                                                 sleep=lambda s: sleeps.append(s))

    price_lookup("ETH", days_ago(1))
    price_lookup("ETH", days_ago(1))

    assert sleeps == [prices.THROTTLE_SECONDS]


# --- integration: the real fetcher wired into the substrate ---------------------

def _budget():
    from src.chain.budget import CallBudget
    return CallBudget(max_calls=100, seconds=1000, clock=lambda: 0.0)


def test_a_priced_major_reaches_normalise_row_with_real_amount_usd_and_daily_close(tmp_path):
    price_lookup = build(tmp_path, recording_transport())
    row = {"blockNumber": "50", "timeStamp": "1781000000", "hash": "0xeth",
           "from": "0xa", "to": "0xb", "value": "2000000000000000000"}   # 2 ETH

    rec = collect.normalise_row(row, ARB, "native", price_lookup)

    assert rec["asset"] == "ETH"
    assert rec["amount"] == 2.0
    assert rec["amount_usd"] == 5000.0
    assert rec["value_basis"] == "daily_close"


def test_a_sweep_completes_and_marks_records_price_unavailable_when_coingecko_is_entirely_down(
        tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")

    def always_down(url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError("coingecko is down")

    price_lookup = build(tmp_path / "prices", always_down)

    def native_row(h):
        return {"blockNumber": "50", "timeStamp": "1781000000", "hash": h,
                "from": "0xtarget", "to": "0xdest", "value": "2000000000000000000"}

    def fake_fetch_kind(address, chain, kind, start, b, **kw):
        if kind != "native":
            return WalkResult([], start, 1, False, []), None
        return WalkResult([native_row("0xeth")], 50, 1, False, []), None

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch_kind)

    result = collect.sweep_wallet("0xtarget", [ARB], _budget(), cluster=True,
                                  price_lookup=price_lookup)

    chain = result["chains"]["arbitrum"]
    assert chain["records"] == 1
    assert chain["spam"] == 0
    assert chain["unpriced"] == 1

    got = collect.records_for("0xtarget")
    assert len(got) == 1
    assert got[0]["amount_usd"] is None
    assert got[0]["value_basis"] == "price_unavailable"
