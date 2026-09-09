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


# --- definitive misses are cached; indeterminate ones are not -----------------
#
# PriceCache caches a miss so an unavailable date is not re-requested every run.
# That is right for a verdict and wrong for a non-verdict, and the difference is
# invisible in the return value -- both are None. So every case below asserts
# BOTH halves: what came back, AND whether anything reached the disk.
#
# The 429 case is why this matters. The whole design is built around a
# rate-limited free tier, so 429 is the expected steady state, not an edge case.
# Cached as a confirmed miss it would permanently burn that (symbol, date), and
# coverage meant to improve across runs would instead fill with holes
# indistinguishable from genuine no-data days.

def cache_file(tmp_path, symbol="ETH"):
    return tmp_path / f"{symbol}.json"


@pytest.mark.parametrize("name,response", [
    ("connection error", requests.exceptions.ConnectionError("dns failed")),
    ("timeout", requests.exceptions.Timeout("slow")),
    ("rate limited", FakeResponse(status_code=429)),
    ("server error", FakeResponse(status_code=500)),
    ("gateway error", FakeResponse(status_code=502)),
    ("unparseable body", FakeResponse(200, json_error=ValueError("not json"))),
    ("body is not an object", FakeResponse(200, payload=["not", "an", "object"])),
    ("body is a bare string", FakeResponse(200, payload="not-a-dict-at-all")),
    ("body is null", FakeResponse(200, payload=None)),
])
def test_an_indeterminate_outcome_returns_none_and_caches_nothing(tmp_path, name, response):
    """None of these is a verdict about whether a price exists, so none may be
    written to disk -- a later run has to get a real second attempt."""
    price_lookup = build(tmp_path, recording_transport(responses=[response]))

    result = price_lookup("ETH", days_ago(5))

    assert result is None, name
    assert result != 0.0, name
    assert not cache_file(tmp_path).exists(), (
        f"{name}: wrote a cache entry for something that was never a verdict — "
        f"PriceCache never re-requests a cached miss, so this date is now "
        f"permanently unpriceable")


def test_budget_exhaustion_caches_nothing_like_every_other_non_verdict(tmp_path):
    """Spending the budget is 'we never tried', which is the same class as a
    failed attempt even though it never touches the network."""
    price_lookup = build(tmp_path, recording_transport(), max_requests=0)

    assert price_lookup("ETH", days_ago(5)) is None
    assert not cache_file(tmp_path).exists()


@pytest.mark.parametrize("name,symbol,date,response", [
    ("unknown symbol", "SCAMCOIN", days_ago(5), None),
    ("date past the keyless window", "ETH", days_ago(400), None),
    ("unparseable date", "ETH", "not-a-date", None),
    ("200 with no market_data", "ETH", days_ago(5), FakeResponse(200, {})),
    ("200 with no usd figure", "ETH", days_ago(5),
     FakeResponse(200, {"market_data": {"current_price": {}}})),
    ("200 with a nonsensical usd figure", "ETH", days_ago(5),
     FakeResponse(200, {"market_data": {"current_price": {"usd": -1.0}}})),
])
def test_a_definitive_miss_returns_none_and_is_cached(tmp_path, name, symbol, date, response):
    """CoinGecko -- or our own static knowledge -- affirmatively telling us there
    is nothing here. Re-asking never gets a different answer, so the miss is
    worth persisting."""
    responses = [response] if response is not None else []
    price_lookup = build(tmp_path, recording_transport(responses=responses))

    result = price_lookup(symbol, date)

    assert result is None, name
    assert result != 0.0, name
    written = cache_file(tmp_path, symbol)
    assert written.exists(), f"{name}: a confirmed miss should not be re-requested every run"
    assert json.loads(written.read_text())[date] is None, name


def test_a_transient_failure_is_retried_and_can_succeed_within_the_same_run(tmp_path):
    """The regression guard for the whole split: a 429 must not foreclose the
    retry, in this run or any later one."""
    transport = recording_transport(responses=[
        FakeResponse(status_code=429),
        FakeResponse(200, history_payload(2500.0)),
    ])
    price_lookup = build(tmp_path, transport)
    date = days_ago(5)

    assert price_lookup("ETH", date) is None
    assert price_lookup("ETH", date) == 2500.0

    assert len(transport.calls) == 2, "the retry never reached the transport"
    assert json.loads(cache_file(tmp_path).read_text())[date] == 2500.0


def test_a_transient_failure_is_retried_by_a_later_run(tmp_path):
    """Same guarantee across process boundaries: a fresh instance over the same
    directory must not inherit a poisoned entry."""
    date = days_ago(5)
    first = build(tmp_path, recording_transport(responses=[FakeResponse(status_code=429)]))
    assert first("ETH", date) is None

    second_transport = recording_transport(responses=[FakeResponse(200, history_payload(2500.0))])
    second = build(tmp_path, second_transport)

    assert second("ETH", date) == 2500.0
    assert len(second_transport.calls) == 1


def test_a_definitive_miss_is_not_retried_by_a_later_run(tmp_path):
    """The other half of the contract -- otherwise every run re-asks CoinGecko
    for a date it has already said it has nothing for."""
    date = days_ago(5)
    first = build(tmp_path, recording_transport(responses=[FakeResponse(200, {})]))
    assert first("ETH", date) is None

    second_transport = recording_transport()
    second = build(tmp_path, second_transport)

    assert second("ETH", date) is None
    assert second_transport.calls == [], "a confirmed miss was re-requested"


# --- MATIC's coin id depends on the date, not the symbol ----------------------
#
# Polygon migrated MATIC to POL on 2024-09-04. CoinGecko kept the old series
# under "matic-network" and started a new one under "polygon-ecosystem-token",
# so a static mapping is wrong in one direction whichever id it picks. See
# prices.MIGRATED_COIN_IDS.

@pytest.fixture
def _with_key(monkeypatch):
    """Migration-era dates are years old, so the keyless 365-day guard rejects
    them before a request is ever made — which is itself why a static legacy
    mapping is unreachable in practice. These tests are about which id gets
    requested, so they need a key to get past that guard at all."""
    monkeypatch.setenv("COINGECKO_API_KEY", "test-key")


def test_matic_before_the_migration_uses_the_legacy_series(tmp_path, _with_key):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    price_lookup("MATIC", "2024-09-03")

    assert "matic-network" in transport.calls[0]["url"]


@pytest.mark.parametrize("date", ["2024-09-04", "2024-09-05"])
def test_matic_on_or_after_the_migration_prices_off_pol(tmp_path, date, _with_key):
    """A row still labelled MATIC after the migration is a legacy-symbol
    contract or a bridged wrapper — its price is POL's. Mapping it to the
    frozen legacy series would return a definitive miss for an asset that
    does have a price."""
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    price_lookup("MATIC", date)

    assert "polygon-ecosystem-token" in transport.calls[0]["url"]
    assert "matic-network" not in transport.calls[0]["url"]


def test_pol_always_uses_the_current_id(tmp_path):
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)

    price_lookup("POL", days_ago(5))

    assert "polygon-ecosystem-token" in transport.calls[0]["url"]


def test_post_migration_matic_and_pol_share_one_request(tmp_path):
    """They resolve to the same id, so the in-run memo should collapse them —
    the same way ETH and WETH already do."""
    transport = recording_transport()
    price_lookup = build(tmp_path, transport)
    date = days_ago(5)

    assert price_lookup("MATIC", date) == 2500.0
    assert price_lookup("POL", date) == 2500.0

    assert len(transport.calls) == 1


def test_an_unmigrated_symbol_is_unaffected_by_the_date(tmp_path, _with_key):
    """The migration table must not touch anything it does not name."""
    for date in ("2024-09-03", "2024-09-05", days_ago(5)):
        transport = recording_transport()
        build(tmp_path / date, transport)("ETH", date)
        assert "ethereum" in transport.calls[0]["url"], date
