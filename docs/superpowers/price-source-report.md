# Price source report — CoinGecko wiring for `feat/universal-fund-tracing`

Task: supply `PriceCache`'s `fetch` (src/chain/assets.py), wire it into
`sweep_wallet`'s callers, and close the "collection is multi-asset, detection
is stablecoin-only" gap the phase-1 handoff left open. No branch created,
nothing merged, nothing pushed.

**New file:** `src/chain/prices.py`. **Changed:** `src/chain/assets.py`
(one wrong coin id), `src/tracer.py`, `scripts/backfill_transfers.py`,
`src/transfer_graph.py` (comment only — no behaviour change),
`tests/conftest.py` (one new probe). **New tests:** `tests/test_chain_prices.py`
(43 tests) plus three wiring/regression tests added to existing files.

---

## 1. Design, and why

`assets.value_usd(symbol, amount, date_str, price_lookup)` already existed and
was already tested; `PriceCache(directory, fetch)` already existed and was
already tested. The only missing piece was `fetch(symbol, date_str) -> float
| None` itself, plus enough budget discipline around it that calling it
can't cost more than a small, fixed slice of a job's wall clock. That's
`src/chain/prices.py`'s entire job: one function,
`coingecko_price_lookup(directory, **budget_knobs) -> price_lookup`, that
returns a ready-to-use `price_lookup(symbol, date_str)` closure wrapping a
`PriceCache`.

Internally it's four small, independently-testable pieces:

- `_to_coingecko_date` — ISO `YYYY-MM-DD` → CoinGecko's `DD-MM-YYYY`.
- `_too_old` — a proactive "don't bother" check against the confirmed 365-day
  keyless cutoff (only consulted when unauthenticated — see §5).
- `_request_history` — the one HTTP round trip, wrapped in a blanket
  `except Exception: return None` (the same resilience pattern
  `src/utils.py:etherscan_get` already uses for exactly the same reason: a
  flaky third-party API must never abort a collection run).
- `_extract_usd_price` — pulls `market_data.current_price.usd` out of the
  decoded response and rejects anything that isn't a real, finite, positive
  number (a missing key, `null`, a string, `NaN`, `-5`, `0.0`, `True` all
  collapse to the same "not a usable price").

`coingecko_price_lookup` wires those together with two budgets: a
`CallBudget` (reused from `src/chain/budget.py` rather than reinvented — it's
already the tested "N calls or S seconds, whichever first" primitive this
codebase uses for Etherscan) and a small in-run `dict[(coin_id, date), price]`
that de-duplicates `ETH`/`WETH` lookups (both resolve to CoinGecko's
`"ethereum"` id — see §4), so a wallet trading both on the same day spends
one request, not two.

### The bug this design had to avoid: a budget-exhausted lookup poisoning the disk cache forever

The first version of this module returned `None` from `fetch` whenever the
run's `CallBudget` was spent. That is wrong, and the first run of
`tests/test_chain_prices.py` caught it immediately
(`test_budget_exhaustion_returns_none_without_touching_the_cache_file` failed):
`PriceCache.get()` writes **whatever `fetch` returns** to disk unconditionally,
the instant `fetch` returns normally — that's the whole point of it (a
genuinely-confirmed miss must not be re-requested every run, per its own
docstring). But "this run's small budget is already spent" is not a confirmed
miss — it's a deferral, and the next run gets a fresh budget and must still be
free to try. Returning `None` the ordinary way would have `PriceCache`
persist that `None` to `{symbol}.json` **forever**, permanently blacklisting
a date that was never actually asked about. That silently defeats the entire
"coverage improves incrementally across runs" premise this task is built on —
a wallet with 50 unpriced majors and a 12-request budget would price the
first dozen and permanently write off the other 38 on its very first sweep.

Fix: `fetch` **raises** `_BudgetExhausted` (an internal, undocumented-outside-
this-module exception) instead of returning `None` when spent.
`PriceCache.get()` computes `price = self._fetch(...)` and only reaches the
`table[date_str] = price; ...write...` lines on the line *after* — so an
exception there skips the disk write entirely and propagates out of
`PriceCache.get()` untouched. The `price_lookup` wrapper `coingecko_price_lookup`
returns is the only thing that ever sees `_BudgetExhausted`; it catches it and
returns `None` for that one call, leaving the on-disk cache exactly as it was.
`test_a_budget_exhausted_date_is_fetched_for_real_by_the_next_run` proves a
second, fresh instance pointed at the same directory can still fetch it for
real.

A related, narrower bug was caught by re-reading my own first draft rather
than by a failing test: checking `budget.can_spend()` and then separately
calling `budget.spend()` leaves a check-then-act gap — wall-clock time
(`CallBudget.elapsed()` is a live clock read) could cross the deadline in
between the two calls, and `spend()` would then raise `budget.BudgetExhausted`
(the *module's* exception, not this module's `_BudgetExhausted`), which would
escape `price_lookup`'s `except _BudgetExhausted` uncaught — straight into
`normalise_row`, violating the single hardest invariant in the brief. Fixed
by collapsing the check and the act into one `try: budget.spend() except
BudgetExhausted: raise _BudgetExhausted`.

---

## 2. Budget arithmetic against both job timeouts

Two independent knobs bound the worst case per process: `max_seconds` (a
`CallBudget`'s own wall-clock ceiling) and `request_timeout` (a single
`requests.get(..., timeout=...)` call's own ceiling). Worst case per fetcher
instance is **`max_seconds + request_timeout`**: a request can start in the
instant just before the budget's deadline and still be allowed to run to its
own timeout before returning. `max_requests` is a secondary ceiling — in
practice (throttle 2.5s + ~0.1–0.3s observed real CoinGecko latency, see §6)
it's `max_seconds` that binds first for any run large enough to matter, so
`max_requests` mainly protects against a pathological case where requests
somehow return near-instantly.

**Trace job — `.github/workflows/trace.yml`, 600s timeout.** Its own comment
already itemises 561s of committed work (150 cluster sweep + 240 destination
tracing + 1 correlator + 150 frontier expansion + 20 bytecode lookups),
leaving **~39s** slack for checkout/pip/push. Only `src/tracer.py`'s cluster
sweep prices in this job (see §3 for why `expand_frontier` deliberately does
not). `prices.py`'s defaults are sized for exactly this caller:

```
DEFAULT_MAX_SECONDS_PER_RUN = 12s
REQUEST_TIMEOUT_SECONDS     = 5s
worst case added            = 12 + 5 = 17s
remaining slack             = 39 - 17 = 22s
```

Typical case is far cheaper: throttle (2.5s) + observed real latency
(~0.1–0.3s server-side per §6's curl transcript, call it ≤1s round-trip from a
CI runner) ≈ 3s/request, so a 12s budget yields roughly 4 new (symbol, date)
prices per run in the common case — `max_seconds` binds before
`max_requests` (12) does. Run every 30 minutes, that's ample to keep up with
each day's actual new transfers (a handful of distinct majors on a handful of
distinct days), because once a (symbol, date) is priced it is priced forever
— coverage compounds, it doesn't need to be re-earned each run.

**Backfill job — `.github/workflows/backfill.yml`, 3600s timeout.** Its
comment gives `config.backfill.time_budget_seconds = 2700` plus **~900s**
(15 min) for checkout/pip/`python src/backfill.py`/push.
`scripts/backfill_transfers.py` explicitly overrides the defaults, sized for
this much larger margin and for the job's actual purpose (deep historical
catch-up):

```
BACKFILL_PRICE_MAX_SECONDS  = 90s
REQUEST_TIMEOUT_SECONDS     = 5s
worst case added            = 90 + 5 = 95s
remaining margin            = 900 - 95 = 805s
```

Typical case: 90s / ~3s per request ≈ 30 new prices per invocation — enough
to make visible progress across a handful of manual dispatches without
guessing at how much of the 900s margin `python src/backfill.py` (a separate,
pre-existing, unrelated script) actually needs. **Both fit comfortably; I did
not have to propose moving pricing out of these jobs.** If a much larger
backfill allowance is wanted later, the honest way to get it is to widen
`BACKFILL_PRICE_MAX_SECONDS` (still bounded by the ~805s margin above) rather
than to change `src/tracer.py`'s tighter numbers.

One instance's budget is shared across every wallet a run sweeps (built once,
before the `for wallet in wallets:` loop in `backfill_transfers.py`; a fresh
one per call in `trace_outbound_transfers`, which sweeps exactly one wallet
per run) — so "per-run" means per process invocation, not per wallet.
`test_main_wires_one_shared_coingecko_price_lookup_across_every_wallet` pins
this for backfill.

**Throttle.** `THROTTLE_SECONDS = 2.5` (24 requests/min) applies uniformly
regardless of caller — it protects CoinGecko's own rate limit, a property of
the remote service, not of either job's time budget, so there's no reason for
it to vary by caller the way `max_seconds` does.

---

## 3. Which `sweep_wallet` callers price, and why

| Caller | Prices? | Why |
|---|---|---|
| `src/tracer.py: trace_outbound_transfers` | **Yes** | This sweeps the *target's own* transfers — the ones that feed `alert_fund_movement` directly. This is the entire point of the task: "a trader deliberately migrating is exactly the person who would move in ETH." |
| `scripts/backfill_transfers.py: main` | **Yes** | Sweeps the cluster (target + confirmed self wallets) with a much larger budget — see §2. This is where deep historical majors-pricing coverage should actually accumulate. |
| `src/transfer_graph.py: expand_frontier` | **No, deliberately** | This sweeps *frontier candidates* — unconfirmed wallets discovered while walking the graph outward, explicitly called out in the task prompt as mattering less than the target's own transfers. It shares the **same 600s trace.yml job** as `tracer.py`'s cluster sweep. Giving it a second, independent CoinGecko allowance would double the worst-case addition to the tightest-margin job in the system (39s slack) for the lower-value sweep. Left unchanged: `sweep_wallet(wallet, sweep_chains, sweep_budget, cluster=False)` still passes no `price_lookup`, so it keeps today's behaviour (frontier majors stay `price_unavailable`, and so stay out of `_expandable_edges`, exactly as before this task). This is a comment-only diff at that call site, explaining the decision in place, plus a regression test (`test_expand_frontier_does_not_pass_a_price_lookup_override`) so a later change has to consciously re-do this arithmetic rather than wire pricing in by accident. |

If a frontier wallet is later promoted to `known_self_wallets`, a subsequent
backfill sweep of it *would* price its transfers going forward — though see
§8 for why records from its *first* (frontier) sweep specifically would stay
stuck unpriced.

---

## 4. `assets.MAJORS`: one coin id was wrong

Verified live against `api.coingecko.com` on 2026-08-31 (transcripts in §6).
Ten of eleven entries are correct. One is not:

- **`"POL": "matic-network"` was wrong.** `GET /coins/matic-network` returns
  `{"id": "matic-network", "symbol": "matic", "name": "MATIC (migrated to
  POL)"}`, with an explicit deprecation notice: *"On September 4th, 2024,
  Polygon's previous network token, $MATIC, was upgraded 1:1 to $POL."*
  `GET /search?query=POL` returns `"polygon-ecosystem-token"` (symbol `POL`,
  name `"POL (ex-MATIC)"`) as the live token's actual id. **Fixed** to
  `"POL": "polygon-ecosystem-token"`.
- **`"MATIC": "matic-network"` is correct as-is, left unchanged.** A transfer
  row genuinely labelled `MATIC` (pre-migration, i.e. before 2024-09-04) needs
  exactly that legacy id's own historical price series, which CoinGecko
  continues to serve under the old id. This is not the same mistake as POL's
  — POL and MATIC are now two distinct, correctly-distinct ids in `MAJORS`,
  where before the fix they were (wrongly) the same id.
- **`ETH`/`WETH` sharing `"ethereum"` is correct and intentional, not a
  second bug.** WETH is arbitrage-pegged 1:1 to ETH; pricing it off ETH's own
  series is the standard, economically-sound simplification (the same
  reasoning that makes stablecoin-at-par valid). I considered whether
  Arbitrum/Base/Optimism's own *bridged* wrapped variants of WETH/WBTC/wstETH/
  weETH (CoinGecko lists these as separate ids — e.g.
  `"arbitrum-bridged-weth-arbitrum-one"`, confirmed in the `/search?query=Arbitrum`
  transcript in §6) should get their own mappings, and decided not to: these
  are fungible 1:1 with their canonical counterparts by construction, so
  using the canonical id introduces no real error, and per-chain mappings
  would multiply `MAJORS`' size for no practical benefit.
- **Every other id — `WBTC→wrapped-bitcoin`, `CBBTC→coinbase-wrapped-btc`,
  `ARB→arbitrum`, `OP→optimism`, `BNB→binancecoin`, `WSTETH→wrapped-steth`,
  `WEETH→wrapped-eeth`, `ETH/WETH→ethereum`** — independently confirmed
  correct against the live API (transcripts in §6).

---

## 5. Failure modes and what each resolves to

Every one of these resolves to `None` — never `0.0`, and never an exception
that reaches `normalise_row`. `tests/test_chain_prices.py` asserts both
halves (`is None` and `!= 0.0`) for the modes the task calls out explicitly.

| Failure | Where handled | Resolves to |
|---|---|---|
| HTTP error status (500, 404, 429, 401) | `_request_history`: `if resp.status_code != 200` | `None` |
| Timeout / connection error / any transport exception | `_request_history`: blanket `except Exception` | `None` |
| Malformed JSON (`.json()` raises) | same blanket except | `None` |
| Missing `market_data` / `current_price` / `usd` key, wrong-shaped payload | `_extract_usd_price`'s `isinstance` chain | `None` |
| Non-numeric, boolean, non-finite, zero, or negative price | `_extract_usd_price`'s final guard | `None` |
| Unknown symbol (not in `MAJORS`) | `fetch`'s first check | `None`, **no request made** |
| Unparseable date | `_to_coingecko_date` returns `None` | `None`, no request made |
| Date past the confirmed 365-day keyless window, no key configured | `_too_old` | `None`, no request made |
| Date past the window, **key configured** | not short-circuited — request is attempted | `None` if CoinGecko rejects it (its own authority; see the reasoning just below on why this isn't guessed) |
| This run's request/time budget already spent | `_BudgetExhausted` → caught by `price_lookup` wrapper | `None`, on-disk cache **untouched** (see §1) |
| `COINGECKO_API_KEY` absent | `fetch` just omits the `x_cg_demo_api_key` param | Request still attempted — see next section |

### `COINGECKO_API_KEY` absent — behaviour, not assumption

This is the one place I pushed back on the task's own framing. The brief
says to treat `COINGECKO_API_KEY` "exactly as `ETHERSCAN_API_KEY` is
treated" — i.e., skip cleanly without firing a doomed request, the way
`fetch_code` and `sweep_wallet` do when `ETHERSCAN_API_KEY` is absent. I
tested this assumption directly against the live API (§6) before writing any
code: **without a key, `/coins/{id}/history` returns a real HTTP 200 with
real market data**, for any date within the rolling 365-day window. Skipping
the request whenever no key is present — mirroring Etherscan's convention —
would therefore forfeit pricing that actually works, for no benefit. Every
Etherscan request without a key fails identically ("Invalid API Key"); that
is not true here, so the same convention doesn't transfer. `COINGECKO_API_KEY`
is optional in a *different* sense than `ETHERSCAN_API_KEY`: it raises the
rate ceiling (and, per CoinGecko's own materials, possibly the historical
window — unconfirmed, see below) rather than gating access outright.
`test_missing_api_key_omits_the_demo_key_query_param` /
`test_present_api_key_is_sent_as_the_demo_query_param` assert the designed
behaviour (param omitted vs. sent) rather than assuming it.

The one place the key *does* change behaviour: `_too_old`'s proactive
365-day short-circuit is only consulted when unauthenticated. CoinGecko's own
public materials disagree with each other on the Demo (keyed) tier's
historical range — one pricing page states 365 days, another states 2 years,
apparently for the same plan — and I could not resolve this without a real
key to test against. Given the permanent-miss-cache risk explained in §1,
guessing wrong in the conservative direction (skipping proactively) would
permanently forfeit real coverage a key might actually reach, so with a key
present every date is attempted and CoinGecko's own response is the sole
authority. `test_a_date_past_the_window_is_still_attempted_when_a_key_is_present`
pins this.

---

## 6. Live research transcripts

All of the following were run directly against `api.coingecko.com` on
2026-08-31, outside the test suite (research only — the test suite itself
makes zero real requests; see §7's socket-blocked run).

**Date format is `DD-MM-YYYY`, not ISO** — confirmed by actually trying both,
rather than trusting a docs page that (via an intermediate summarising step)
claimed ISO:

```
$ curl -s -i "https://api.coingecko.com/api/v3/coins/bitcoin/history?date=30-08-2025&localization=false"
HTTP/1.1 401 Unauthorized
{"error":{"status":{"timestamp":"2026-08-31T01:33:30.184+00:00","error_code":10012,
"error_message":"Your request exceeds the allowed time range. Public API users are
limited to querying historical data within the past 365 days. Upgrade to a paid
plan to enjoy full historical data access: https://www.coingecko.com/en/api/pricing. "}}}

$ curl -s -i "https://api.coingecko.com/api/v3/coins/bitcoin/history?date=30-07-2026&localization=false"
HTTP/1.1 200 OK
{"id":"bitcoin","symbol":"btc","name":"Bitcoin", ... ,
 "market_data":{"current_price":{"usd":63916.59794922001, ...
```

The first call (366 days back from the server's own clock, which the error
body's timestamp confirms as 2026-08-31) is the exact live confirmation of
the 365-day cutoff `FREE_TIER_HISTORY_DAYS` targets, and of the `error_code
10012` / HTTP 401 shape (not 429, not 400 — which is why `_request_history`
treats every non-200 identically rather than special-casing one status). The
second call (32 days back) is the live confirmation that no API key is
required for an in-range date, and that `DD-MM-YYYY` is accepted and produces
real data — `30-07-2026` unambiguously means 30 July, and the response is a
genuine, cached (`cf-cache-status: HIT`) CoinGecko response, not an error
page.

**Demo key parameter**, from `docs.coingecko.com/demo/reference/authentication`:
header `x-cg-demo-api-key`, query param `x_cg_demo_api_key` — used as the
latter, matching `etherscan_get`'s existing pattern of building one flat
params dict.

**Coin id verification** (`/coins/{id}` and `/search?query=...`):

```
GET /coins/matic-network        -> {"id":"matic-network","symbol":"matic",
                                     "name":"MATIC (migrated to POL)"}   [DEPRECATED]
GET /search?query=POL           -> ranked #2 overall (#1 was Polkadot, symbol
                                    DOT, a text match on "Pol"kadot); #2 is
                                    "polygon-ecosystem-token" | POL | "POL
                                    (ex-MATIC)" -- the first, and only, result
                                    among 25 whose symbol field is exactly POL
GET /coins/wrapped-steth        -> {"id":"wrapped-steth","symbol":"wsteth",
                                     "name":"Wrapped stETH"}             [confirms WSTETH]
GET /search?query=weETH         -> "wrapped-eeth" | WEETH | "Wrapped eETH", ranked #1 [confirms WEETH]
GET /search?query=cbBTC         -> "coinbase-wrapped-btc" | CBBTC | "Coinbase Wrapped BTC", ranked #3
                                    (#1-2 were third-party vaults holding cbBTC, not cbBTC itself) [confirms CBBTC]
GET /search?query=Arbitrum      -> "arbitrum" | ARB | "Arbitrum", ranked #1    [confirms ARB]
GET /search?query=Optimism      -> "optimism" | OP | "Optimism", ranked #1    [confirms OP]
```

(`WBTC→wrapped-bitcoin`, `BNB→binancecoin`, `ETH/WETH→ethereum` were not
re-queried live — these are long-established, unchanged top-100 ids with no
plausible ambiguity, and spending research-phase requests confirming them
would have been the same "one request per fact" waste this module is
designed to avoid in production.)

**Rate limiting is real and immediate without a key**: after roughly half a
dozen unthrottled research requests, the same IP started receiving `429 Too
Many Requests` with a `Retry-After` header. This directly informed
`THROTTLE_SECONDS = 2.5` (§2) — the free tier is not a generous target to
design against.

---

## 7. Test evidence

Commands and output, run from the repo root:

```
$ .venv/Scripts/python.exe -m pytest -q
........................................................................ [ 11%]
........................................................................ [ 22%]
........................................................................ [ 33%]
........................................................................ [ 45%]
........................................................................ [ 56%]
........................................................................ [ 67%]
........................................................................ [ 78%]
........................................................................ [ 90%]
................................................................         [100%]
640 passed in 8.27s
```

640 = 594 baseline (pre-existing, all still passing) + 46 new: 43 in
`tests/test_chain_prices.py`, plus one wiring test each in
`tests/test_chain_tracer_substrate.py`, `tests/test_chain_backfill.py`, and
`tests/test_frontier_retention.py`.

```
$ .venv/Scripts/python.exe -m ruff check src tests scripts
All checks passed!
```

**Network-free, verified directly** (not just assumed from "no test imports
`requests` with a real key"): `socket.create_connection` — what
`urllib3`/`requests` calls under the hood for every HTTP and HTTPS
connection — was monkeypatched to raise before the suite ran:

```
$ .venv/Scripts/python.exe block_sockets_and_run_pytest.py
........................................................................ [ 11%]
...
................................................................         [100%]
640 passed in 9.99s

=== socket.create_connection call attempts: 0 ===
```

Zero attempts, full suite green. (Script lives in the session scratchpad, not
committed — it's a one-off verification harness, not project code.)

**Required-tests checklist**, each with its own test(s) in
`tests/test_chain_prices.py` unless noted:

- Successful fetch returns a float and is cached; second call makes no
  second request — `test_a_successful_fetch_returns_a_float_and_is_cached`,
  `test_a_fresh_instance_reusing_the_same_directory_also_makes_no_request`.
- Every failure mode returns `None`, explicitly not `0.0` —
  `test_non_200_status_yields_none_never_zero` (parametrized: 500/404/429/401),
  `test_a_timeout_yields_none`, `test_a_connection_error_yields_none`,
  `test_malformed_json_yields_none`,
  `test_missing_or_malformed_price_field_yields_none` (9 payload shapes),
  `test_a_nonfinite_nonpositive_or_boolean_price_is_rejected` (6 bad values),
  `test_an_unknown_symbol_returns_none_without_a_request`,
  `test_an_unparseable_date_returns_none_without_a_request`,
  `test_a_date_past_the_keyless_window_returns_none_without_a_request`.
- A sweep whose price source is entirely unavailable still completes, stores
  its records, marks them `price_unavailable` —
  `test_a_sweep_completes_and_marks_records_price_unavailable_when_coingecko_is_entirely_down`
  (uses the real `coingecko_price_lookup` factory with a transport that
  always raises `ConnectionError`, through the real `collect.sweep_wallet`).
- Per-run budget enforced, further lookups return `None` without requesting —
  `test_the_per_run_request_count_budget_is_enforced`,
  `test_the_per_run_wall_clock_budget_is_enforced`,
  `test_budget_exhaustion_returns_none_without_touching_the_cache_file`,
  `test_a_budget_exhausted_date_is_fetched_for_real_by_the_next_run`.
- Missing `COINGECKO_API_KEY` behaviour asserted, not assumed —
  `test_missing_api_key_omits_the_demo_key_query_param`,
  `test_present_api_key_is_sent_as_the_demo_query_param`,
  `test_a_date_past_the_window_is_still_attempted_when_a_key_is_present`.
- End-to-end: a priced major reaching `normalise_row` gets a real `amount_usd`
  and `value_basis: "daily_close"` —
  `test_a_priced_major_reaches_normalise_row_with_real_amount_usd_and_daily_close`.

Plus, beyond the required list: the exact date-format conversion
(`test_the_date_sent_to_coingecko_is_day_month_year_not_iso`), the
boundary-is-attempted-not-rounded-down behaviour explained in
`FREE_TIER_HISTORY_DAYS`'s code comment (permanently caching a false-negative
miss is worse than one wasted request, so the proactive check sits exactly at
the confirmed 365-day boundary rather than a few days inside it —
`test_a_date_exactly_at_the_confirmed_boundary_is_still_attempted`), the
ETH/WETH in-run de-dup (`test_eth_and_weth_share_one_request_for_the_same_date`,
`test_eth_and_weth_on_different_dates_each_make_their_own_request`),
throttle behaviour (`test_requests_are_throttled_between_calls`,
`test_a_cached_hit_does_not_throttle`), and three wiring/regression tests
outside this file:
`test_trace_outbound_transfers_wires_a_real_coingecko_price_lookup_into_the_sweep`
(tracer.py actually constructs and passes a real `src.chain.prices` callable —
never invokes it, since that would be a genuine network call),
`test_main_wires_one_shared_coingecko_price_lookup_across_every_wallet`
(backfill shares one budget across its whole wallet loop, not one per
wallet), and `test_expand_frontier_does_not_pass_a_price_lookup_override`
(locks in §3's deliberate non-change so a future edit has to consciously
revisit the arithmetic).

---

## 8. What I chose not to do

- **No repricing pass over already-stored `price_unavailable` records.**
  Explicitly out of scope per the brief ("Pricing happens at collection time
  in `normalise_row`... A repricing pass over stored records is a sensible
  follow-up — note it in your report, do not build it"). Worth being precise
  about the resulting gap: `sweep_wallet`'s cursor advances past a wallet's
  transfers the first time they're read regardless of pricing outcome, so a
  record collected once while unpriced generally won't be re-offered to
  `normalise_row` on a later sweep of the same wallet — it stays
  `price_unavailable` until a dedicated repricing pass exists.
- **No `/simple/price` usage.** The task mentions it as handling "current
  prices"; I used only `/coins/{id}/history` for every date, including
  today's, to keep the module to one HTTP call shape and one error-handling
  path. The system processes transfers after the fact (never truly
  real-time), so the marginal benefit of a second endpoint for "today" didn't
  justify the doubled surface area.
- **No `max_age_days` value guessed for the keyed tier.** Covered in §5 —
  CoinGecko's own materials disagree with each other, and guessing wrong
  costs permanent coverage, not a retry.
- **No change to `PriceCache` itself.** It was already correct and tested;
  the budget-exhaustion problem in §1 is solved entirely on this module's
  side (raise instead of return), without touching its disk-write contract.
- **Did not re-verify `WBTC`/`BNB`/`ETH` coin ids live** — see §6's
  parenthetical; spending scarce, rate-limited research requests confirming
  ids with no plausible ambiguity would have been wasteful by this module's
  own logic.
- **`transfer_graph.py`'s `expand_frontier` is unpriced by design, not by
  omission** — see §3. If this is revisited later (e.g. once real timing data
  from an actual workflow run shows more slack than the ~39s assumed here),
  the regression test named in §7 will need to be updated deliberately, which
  is the intended friction.
