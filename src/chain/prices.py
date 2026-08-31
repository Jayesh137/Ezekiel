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

## Definitive misses versus indeterminate ones

Every outcome resolves to `None` and nothing here ever raises into a caller
(`price_lookup`, at the bottom, is the one place `_Indeterminate` is ever
caught). But `None` reaches the caller two structurally different ways, and
`PriceCache` (src/chain/assets.py) only gets to see one of them:

  * A **definitive** `None` is CoinGecko (or our own static knowledge)
    affirmatively telling us there is nothing to find here, now or later: the
    symbol is not one we price, the date is confirmed outside the free tier's
    window, or a well-formed 200 response simply has no usable USD figure for
    that date. `fetch` returns `None` normally for these, and `PriceCache.get`
    persists that -- correctly, since re-asking never gets a different answer.
  * An **indeterminate** outcome is anything that is not a verdict about the
    price at all: a transport failure, a non-200 status (429 above all --
    this whole design exists because the free tier rate-limits, so 429 is the
    expected steady state, not an edge case), or a 200 body that will not
    parse or is not even shaped like a coin-history object. `fetch` raises
    `_Indeterminate` for these, which `PriceCache.get()` never gets to catch
    (it propagates straight through, skipping the `table[date_str] = price`
    write) -- so a later run's fresh attempt is not permanently foreclosed by
    this run's bad luck. See `_Indeterminate`'s own docstring for the exact
    mechanics, first built for budget exhaustion and reused here rather than
    duplicated: one path for "no verdict," not two.

Getting this split wrong in the indeterminate direction (caching a 429 as a
confirmed miss) is the same failure as booking a price at `0.0` one layer up
in `assets.value_usd`: both quietly convert "we could not tell" into "we
checked and there is nothing," and the free tier's rate limiting makes the
429 case common enough that a few runs would fill the cache with holes
indistinguishable from genuine no-data days.
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
# comfortably below it. 365 is attempted; 366 is skipped. (This one IS a
# definitive miss, not an indeterminate one: we know the shape of the
# rejection in advance -- error_code 10012, every time, confirmed live -- so
# there is nothing to retry a later run for.)
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


class _Indeterminate(Exception):
    """Internal signal only: we tried, or could not try, and learned nothing
    about whether a price exists -- the opposite of a definitive miss.

    Covers this run's budget being spent (we never tried) through every kind
    of failed attempt (we tried and got nothing usable): a transport-level
    exception, a non-200 status, or a 200 body that does not parse or is not
    shaped like a coin-history response. See the module docstring's
    definitive/indeterminate split for the full reasoning.

    `PriceCache.get()` (src/chain/assets.py) writes whatever `fetch` returns
    to disk, unconditionally, as soon as `fetch` returns normally -- that is
    the whole point of it (a confirmed miss must not be re-requested every
    run). None of the above are confirmed misses. Raising instead of
    returning sidesteps the write: `PriceCache.get()` computes
    `price = self._fetch(...)` and only reaches `table[date_str] = price` on
    the line after, so an exception here skips it entirely and propagates out
    of `PriceCache.get()` untouched. `coingecko_price_lookup`'s returned
    wrapper is the only thing that ever catches this; it returns `None` for
    THIS call, leaving the on-disk cache exactly as it was, so a later run's
    fresh budget -- or a CoinGecko that has stopped rate-limiting -- gets a
    real second attempt instead of a permanently poisoned entry.
    """


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


def _extract_usd_price(payload: dict) -> float | None:
    """`market_data.current_price.usd` from an already-confirmed dict payload.

    Every path through here is a DEFINITIVE "no usable price for this date":
    the caller (`_request_history`) has already confirmed the response parsed
    as JSON and is shaped like an object, so a missing or nonsensical value
    inside it -- no `market_data`, no `current_price`, a non-numeric or
    non-finite or non-positive `usd` -- is CoinGecko's own well-formed
    response affirmatively having nothing usable for us here, not a failure
    to determine one. A missing key, a wrong-shaped nested value, and a
    nonsensical number are all treated identically: not a usable price, and
    distinguishing "malformed" from "nonsensical" from "absent" here would
    not change what any caller does with the result.
    """
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
    """One HTTP round trip to `/coins/{id}/history`.

    Returns a price, or `None` for a DEFINITIVE "no price for this date" (a
    well-formed 200 whose body simply lacks a usable USD figure --
    `_extract_usd_price`). Raises `_Indeterminate` for everything that is not
    a verdict about the price at all: a transport failure, a non-200 status,
    or a 200 body that does not even parse as JSON or does not parse to a
    dict (some other shape entirely -- not what a coin-history response ever
    legitimately looks like). This is the one place those failures are
    caught and reclassified; nothing above it needs its own exception
    handling for them.
    """
    params = {"date": cg_date, "localization": "false"}
    if api_key:
        params[DEMO_KEY_PARAM] = api_key
    try:
        resp = transport(f"{COINGECKO_BASE}/coins/{coin_id}/history",
                         params=params, timeout=timeout)
    except Exception as exc:
        raise _Indeterminate from exc

    if resp.status_code != 200:
        raise _Indeterminate(f"http {resp.status_code}")

    try:
        payload = resp.json()
    except Exception as exc:
        raise _Indeterminate from exc

    if not isinstance(payload, dict):
        raise _Indeterminate("response body was not a JSON object")

    return _extract_usd_price(payload)


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
    # answer to both symbols' files as usual. Only ever populated on a
    # DEFINITIVE outcome (see below): an indeterminate one must remain
    # retryable within this same run too, not just across runs, in case a
    # 429 clears up a few seconds later.
    resolved: dict[tuple[str, str], float | None] = {}

    def fetch(symbol: str, date_str: str) -> float | None:
        coin_id = MAJORS.get((symbol or "").strip().upper())
        if not coin_id:
            return None                                  # definitive

        api_key = os.environ.get(DEMO_KEY_ENV_VAR, "")
        if not api_key and _too_old(date_str, FREE_TIER_HISTORY_DAYS):
            return None                                  # definitive

        cg_date = _to_coingecko_date(date_str)
        if cg_date is None:
            return None                                  # definitive

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
            raise _Indeterminate from exc                # indeterminate
        sleep(throttle_seconds)

        # _request_history raises _Indeterminate itself for every failed-
        # attempt case; only a genuine verdict (a price, or a confirmed
        # absence of one) reaches the line below.
        price = _request_history(transport, coin_id, cg_date,
                                 timeout=request_timeout, api_key=api_key)
        resolved[key] = price                            # definitive either way
        return price

    cache = PriceCache(directory, fetch=fetch)

    def price_lookup(symbol: str, date_str: str) -> float | None:
        try:
            return cache.get(symbol, date_str)
        except _Indeterminate:
            return None

    return price_lookup
