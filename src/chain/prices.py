"""Fetching real-world USD closes from CoinGecko, budgeted for one process run.

`src/chain/assets.py` already knows WHAT to price (`MAJORS`) and WHERE a price
that cannot be determined must go (`None`, never `0.0`). This module is the one
piece that was missing: an actual `fetch(symbol, date_str) -> float | None`
to hand to `assets.PriceCache`, plus the budget discipline that keeps it from
ever blowing a job timeout or a rate limit.

Two things learned empirically (a live, unauthenticated GET against
api.coingecko.com, on 2026-08-31 -- see docs/superpowers/price-source-report.md
for the full transcript) shape this module, and both contradict a plausible-
looking assumption:

  * `/coins/{id}/history` DOES work with no API key at all, for a date within
    the free tier's rolling window -- an actual 200 with real market data, no
    key sent. A key is optional here in a way ETHERSCAN_API_KEY is not: without
    an Etherscan key EVERY request fails, so collect.py and client.py skip
    cleanly rather than spend budget on a guaranteed failure. Skipping
    CoinGecko calls the same way when COINGECKO_API_KEY is absent would forfeit
    pricing that actually works.
  * The failure for a too-old date is HTTP 401 (not 429, not 400), with body
    `{"error":{"status":{"error_code":10012,"error_message":"...within the
    past 365 days..."}}}`. 366 days back failed this way; 32 days back
    succeeded. The proactive `_too_old` short-circuit below targets that exact,
    confirmed boundary -- see its docstring for why it does not "round down"
    for extra safety margin.

Every failure mode -- HTTP error, timeout, malformed JSON, a missing or
nonsensical price field, an unknown symbol, a date outside the free tier's
range, an exhausted budget -- resolves to `None`. Nothing in this module ever
raises into a caller; `_request_history` is the one place exceptions are
expected, and the one place they are guaranteed not to escape.
"""

import math
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime

import requests

from src.chain.assets import MAJORS, PriceCache
from src.chain.budget import BudgetExhausted, CallBudget

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# CoinGecko's own documented and empirically-confirmed limit for a request
# carrying no API key: historical data is available for the trailing 365 days.
# Deliberately NOT rounded down for "safety margin": PriceCache caches a miss
# forever (a date only gets older, never re-enters the window), so treating a
# fetchable date as unfetchable is a PERMANENT loss of real coverage, while
# attempting a doomed request and getting None back costs one wasted call out
# of a small per-run budget -- a recoverable, one-time cost. That asymmetry
# means the proactive check should sit exactly at the confirmed boundary, not
# comfortably below it. 365 is attempted; 366 is skipped.
FREE_TIER_HISTORY_DAYS = 365

# A registered (free) Demo API key's own historical range is not something
# this module guesses at: CoinGecko's own materials disagree with each other
# (one page says 365 days, another says 2 years, for what appears to be the
# same Demo plan). Rather than risk the same permanent-miss mistake on an
# unconfirmed number, `_too_old` is only consulted when NO key is configured;
# with a key present, every date is attempted and CoinGecko's own response is
# the sole authority.
DEMO_KEY_ENV_VAR = "COINGECKO_API_KEY"
DEMO_KEY_PARAM = "x_cg_demo_api_key"

# Single HTTP call ceiling. Etherscan's own reader (src/utils.py:etherscan_get)
# uses 30s; this is deliberately much tighter, so one hung CoinGecko request
# cannot cost more than a fraction of what one hung Etherscan request already
# risks costing today.
REQUEST_TIMEOUT_SECONDS = 5.0

# Pacing between actual HTTP requests. CoinGecko's own pricing page states the
# free "Demo" (registered key) tier at 100 calls/min; the true no-key tier is
# undocumented and, in the 2026-08-31 research for this module, returned a 429
# after only a handful of unthrottled requests. 2.5s -> 24 requests/min is
# comfortably under the conservative end of "10-30/min" this project already
# assumes for the free tier, and trivially under the keyed 100/min ceiling.
THROTTLE_SECONDS = 2.5

# Defaults sized for the tightest caller. src/tracer.py's cluster sweep runs
# inside trace.yml's 600s job, which already commits ~561s and leaves ~39s of
# slack for checkout/pip/push (see trace.yml's own job-level comment). Worst
# case here is max_seconds + one request's own timeout (a request can start
# just under the ceiling and still run to REQUEST_TIMEOUT_SECONDS):
# 12 + 5 = 17s, leaving ~22s of that slack rather than consuming all of it.
# scripts/backfill_transfers.py deliberately overrides both, since
# backfill.yml's job has roughly 900s of slack to draw from instead of 39s --
# see docs/superpowers/price-source-report.md for the full arithmetic.
DEFAULT_MAX_REQUESTS_PER_RUN = 12
DEFAULT_MAX_SECONDS_PER_RUN = 12.0


def _too_old(date_str: str, max_age_days: int) -> bool:
    """True if `date_str` is confirmed too old for a keyless request.

    An unparseable date is treated as "too old" too: it can never succeed
    either way, and this keeps the caller from needing a second code path for
    "not a real date" versus "a real date outside the window."
    """
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True
    return (datetime.now(UTC).date() - day).days > max_age_days


def _to_coingecko_date(date_str: str) -> str | None:
    """ISO `YYYY-MM-DD` -> CoinGecko's own `DD-MM-YYYY`, or None if unparseable.

    Confirmed directly against the live endpoint (not assumed from docs, which
    disagreed with this): a request for `bitcoin` history with
    `date=30-07-2026` returned a real 200 with market data; `date=2026-07-30`
    was never verified to succeed and this project has no reason to trust the
    ISO-looking form over what the plan and prior CoinGecko documentation both
    independently state is the correct one.
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return None


def _extract_usd_price(payload) -> float | None:
    """`market_data.current_price.usd` from a decoded history response.

    A missing key, a wrong-shaped payload, a non-numeric value, or a value no
    real asset price could take (non-finite, zero, negative) are all treated
    identically: not a usable price. Distinguishing "malformed" from
    "nonsensical" from "absent" would not change what any caller does with the
    result, and collapsing them keeps this the one place that logic lives.
    """
    if not isinstance(payload, dict):
        return None
    market_data = payload.get("market_data")
    if not isinstance(market_data, dict):
        return None
    current_price = market_data.get("current_price")
    if not isinstance(current_price, dict):
        return None
    price = current_price.get("usd")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    price = float(price)
    return price if math.isfinite(price) and price > 0 else None


def _request_history(transport: Callable, coin_id: str, cg_date: str, *,
                     timeout: float, api_key: str) -> float | None:
    """One HTTP round trip to `/coins/{id}/history`. Never raises.

    This is the one place a network or parse exception is expected, and the
    one place it is guaranteed not to escape -- matching etherscan_get's own
    resilience pattern (src/utils.py) for the same reason: a single flaky
    response from a third-party API must never abort a collection run.
    """
    params = {"date": cg_date, "localization": "false"}
    if api_key:
        params[DEMO_KEY_PARAM] = api_key
    try:
        resp = transport(f"{COINGECKO_BASE}/coins/{coin_id}/history",
                         params=params, timeout=timeout)
        if resp.status_code != 200:
            return None
        return _extract_usd_price(resp.json())
    except Exception:
        return None


class _BudgetExhausted(Exception):
    """Internal signal only: this run's request/time ceiling is spent.

    `PriceCache.get()` (src/chain/assets.py) writes whatever `fetch` returns
    to disk, unconditionally, as soon as `fetch` returns normally -- that is
    the whole point of it (a confirmed miss must not be re-requested every
    run). But "this run's budget is already spent" is not a confirmed miss:
    it is a deferral, and the very next run gets a fresh budget and should
    still be free to try. Returning None the normal way would let PriceCache
    persist that None forever, permanently blacklisting a date that was never
    actually asked about -- silently defeating the "coverage improves across
    runs" design this whole module exists for.

    Raising instead of returning sidesteps that: `PriceCache.get()` computes
    `price = self._fetch(...)` and only writes to disk on the line after, so
    an exception here skips the write entirely and propagates out of
    `PriceCache.get()` untouched. `coingecko_price_lookup`'s returned wrapper
    is the only thing that ever sees this exception; it catches it and
    returns None for THIS call, leaving the on-disk cache exactly as it was.
    """


def coingecko_price_lookup(directory, *,
                           max_requests: int = DEFAULT_MAX_REQUESTS_PER_RUN,
                           max_seconds: float = DEFAULT_MAX_SECONDS_PER_RUN,
                           request_timeout: float = REQUEST_TIMEOUT_SECONDS,
                           throttle_seconds: float = THROTTLE_SECONDS,
                           transport: Callable | None = None,
                           clock: Callable[[], float] = time.monotonic,
                           sleep: Callable[[float], None] = time.sleep) -> Callable:
    """A `price_lookup(symbol, date_str)` callable, backed by CoinGecko and
    disk-cached via `PriceCache`, budgeted for exactly one process run.

    `directory` has no default on purpose: every real caller must say where the
    cache lives, so a test that forgets to redirect it fails loudly (an
    unresolvable NameError/TypeError) instead of quietly writing into whatever
    a stale default happened to point at -- the same discipline
    tests/conftest.py already enforces for data/transfers, data/state and
    data/transfers_spam.

    The returned callable is what `sweep_wallet(..., price_lookup=...)` and
    `assets.value_usd` expect directly -- no `.get` needed.

    `max_requests`/`max_seconds` are an independent ceiling from any
    `CallBudget` the caller already built for Etherscan -- CoinGecko calls
    happen on the same wall clock sweep_wallet's own budget measures, but
    nothing in this module ever calls that budget's `.spend()`, so without a
    ceiling of its own a slow run of price lookups could consume time no one
    is accounting for. Both default small; see the module docstring's
    arithmetic.
    """
    budget = CallBudget(max_calls=max_requests, seconds=max_seconds, clock=clock)
    transport = transport or requests.get
    # (coingecko_id, date_str) -> price, for this process run only. ETH and
    # WETH both resolve to the "ethereum" id (assets.MAJORS); without this, a
    # wallet moving both on the same day would spend two requests on the
    # identical fact. PriceCache's own on-disk cache stays keyed per SYMBOL
    # (its existing, tested contract, unchanged here) -- this only dedupes the
    # network round trip within one run, then lets PriceCache persist the
    # answer to both symbols' files as usual.
    resolved: dict[tuple[str, str], float | None] = {}

    def fetch(symbol: str, date_str: str) -> float | None:
        coin_id = MAJORS.get((symbol or "").strip().upper())
        if not coin_id:
            return None

        api_key = os.environ.get(DEMO_KEY_ENV_VAR, "")
        if not api_key and _too_old(date_str, FREE_TIER_HISTORY_DAYS):
            return None

        cg_date = _to_coingecko_date(date_str)
        if cg_date is None:
            return None

        key = (coin_id, date_str)
        if key in resolved:
            return resolved[key]

        # A separate `if not budget.can_spend(): raise ...` followed by
        # `budget.spend()` would leave a check-then-act gap: wall-clock time
        # could cross the deadline in between the two calls (CallBudget's
        # elapsed() is a live clock read, not a snapshot), and `spend()` would
        # then raise budget.BudgetExhausted -- a DIFFERENT exception than the
        # one `price_lookup` below catches, which would escape uncaught into
        # normalise_row. One try/spend/except collapses the check and the act
        # into a single call, closing that gap.
        try:
            budget.spend()
        except BudgetExhausted as exc:
            raise _BudgetExhausted from exc
        sleep(throttle_seconds)

        price = _request_history(transport, coin_id, cg_date,
                                 timeout=request_timeout, api_key=api_key)
        resolved[key] = price
        return price

    cache = PriceCache(directory, fetch=fetch)

    def price_lookup(symbol: str, date_str: str) -> float | None:
        try:
            return cache.get(symbol, date_str)
        except _BudgetExhausted:
            return None

    return price_lookup
