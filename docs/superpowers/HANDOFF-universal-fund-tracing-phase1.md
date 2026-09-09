# Handoff — Universal Fund Tracing, Phase 1 ("Line of Sight")

**Branch:** `feat/universal-fund-tracing` · **Not merged, not pushed.**
**Written:** 2026-08-31 · **Status: implementation complete and reviewed; never run against live data.**

This file exists as a failsafe. The detailed per-task ledger lives at
`.superpowers/sdd/2026-08-28-universal-fund-tracing-phase1/progress.md`, which is
**git-ignored** and will not survive a `git clean -fdx` or a lost session. This
document is tracked, so it survives. Where the two disagree, trust `git log`.

---

## What this branch does

Replaces a collection layer that read **one asset (USDC) on one chain (Arbitrum)
through a single un-paginated 1,000-row request** with a multi-chain, multi-asset,
fully paginated substrate under `src/chain/`, then wires it into the existing
transfer graph, linkage, tracer and correlator.

The measured problems it addresses, all from live data at `main`:

| | |
|---|---|
| Collection capped at the most recent 1,000 token transfers, no pagination | `tracer.py` asked `page=1, offset=1000, sort=desc` exactly once |
| **905 of those 1,000 records were address-poisoning dust** | one forged address mimicking the trader's own known wallet accounted for **510** of them |
| The target has **five real counterparties** | the rest was spam occupying the window |
| Three service addresses known | a Binance hot wallet was indistinguishable from a fresh personal wallet |
| **$13,000,000 left the target for `0xa95d9c1f…` in June 2026 and the system does not know where it went** | the onward hops were never fetchable |

**The scoring and classification brain was deliberately not touched.** No
threshold, confidence weight or alert gate moved. Phase 1 changes what the system
*sees*, not what it concludes — so any new lead clears exactly the bar every
existing lead cleared.

---

## Current state

- **HEAD:** `19c4b0938` · ~52 commits from merge-base `0fca737f0`
- **Tests:** 594 passing in ~10-18s · `ruff check src tests scripts` clean
- **The suite is network-free.** A set of tests were silently making live calls to
  api.etherscan.io (the suite took 58 minutes); that is fixed and was verified by
  running the whole suite with sockets hard-blocked — 0 connection attempts.

### What was built

| Module | Purpose |
|---|---|
| `src/chain/chains.py` | chain registry — six EVM chains on one Etherscan V2 key |
| `src/chain/budget.py` | `CallBudget` — call + wall-clock ceiling, partial results always survive |
| `src/chain/pagination.py` | `walk_blocks` — block-range walking, no 1,000-row ceiling |
| `src/chain/client.py` | multi-chain reader; **three** record kinds (`tokentx`, `txlist`, `txlistinternal`) |
| `src/chain/assets.py` | token registry + USD valuation + price cache |
| `src/chain/spam.py` | poisoning/dust quarantine, value-ordered |
| `src/chain/labels.py` | entity registry, bytecode check, deposit-address inference |
| `src/chain/collect.py` | `sweep_wallet` / `records_for` — the single reader everything else uses |
| `scripts/backfill_transfers.py` | full-history re-read; `--reset`, `--wallet` |

Data: `data/transfers/{chain}/YYYY-MM-DD.json`, `data/transfers/latest.json`
(sweep health), `data/transfers_spam/latest.json`, `data/labels/`,
`data/state/transfer_cursors.json`, `data/state/traced_outbound.json`.

---

## The five binding constraints

Every review gated on these. All are satisfied at HEAD.

1. **No grading rule changes.**
2. **An empty result and a failed read never serialise identically.** One is
   knowledge, the other is blindness; collapsing them turns an outage into a
   silent all-clear.
3. **A missing price yields `None`, never `0.0`.** Zero is invisible to every
   threshold in the system.
4. **Quarantined records never become graph edges** and never consume budget.
5. **An address with bytecode is not a person** and can never be graded
   `MIGRATION_CANDIDATE`.

---

## THE IMPORTANT PART: what is still open

### 1. It has never run against live data

Everything verified is unit-level or replayed from stored records. **No real
multi-chain sweep has happened.** The $13M question is still unanswered.

**Next action:** run the **Backfill** workflow manually with `full_reset: true`,
once, with a real `ETHERSCAN_API_KEY`. Then read `data/transfers/latest.json` and
`data/transfer_graph/latest.json`. Do this before starting Phase 2 — the result
should shape what Phase 2 actually is.

Success looks like: `data/transfers/` has a directory per chain the target has
touched; `services` in the graph has more than three entries; and
`0xa95d9c1f655341597c94393fddc30cf3c08e4fce` either has outbound edges, or
`degraded_sources` explains why not. "No result" is not a pass.

### 2. Nothing supplies `PriceCache`'s fetcher — collection is multi-asset, detection is not

Every non-stablecoin transfer carries `value_basis: "price_unavailable"`. Those
records **are** retained on disk and counted in `sweep_health["unpriced"]`, so the
gap is visible rather than silent — but the graph gets **no ETH/WBTC/ARB edges**
and `alert_fund_movement` cannot fire on them.

This narrows the phase's headline claim. A trader deliberately migrating is
exactly the person who would move in ETH. **Recommended as the very next task**,
before Phase 2 proper: `PriceCache` already takes an injected fetcher, so it is
one module plus a free CoinGecko demo credential.

### 3. `data/` size

Already ~142 MB, and every workflow run does `git add data/` every 30 minutes. The
substrate adds per-chain, per-day files on top. Fine now; will not be fine
indefinitely. `scripts/compact_data.py` exists.

### 4. Accepted, deferred minors

~25 of them, each with a written ruling, in the gitignored ledger. The two worth
remembering:

- `spam.rollup` keys on address only, so a second distinct unpriced token from the
  same sender is dropped from the rollup — defeating the "surface it for
  registration" purpose for all but the first. Fix is to key on `(address, token)`.
- `records_for` does a full linear scan of the substrate on every call, and is now
  called per frontier wallet, per candidate, and per correlator run.

---

## What the review process found (read this before Phase 2)

Twelve tasks, each implemented by a fresh subagent, each gated by an independent
review, with fix rounds until clean, then a whole-branch review.

**Nine of the twelve tasks found genuine bugs in the plan's own prescribed code.**
That is the headline lesson, not a footnote. The worst:

- **A price outage would have silently deleted every ETH transfer.** `value_usd`
  returned the same `(None, "unpriced")` for a scam token and for ETH that merely
  failed to price; `classify_spam` quarantined on `amount_usd is None` alone;
  quarantined records never reach disk; the cursor had already advanced. And
  `price_lookup` defaults to returning `None`, so this was the *default* path.
- **A $1 transfer could have erased the $13M counterparty.** The lookalike rule
  was a symmetric membership test, so a vanity clone that paid $1 joined the
  "real" set and the *genuine* address then matched it and was quarantined. The
  fix is value-ordering: nobody forges an address poorer than their own.
- **The same rule, one layer up, could erase a wallet's entire sweep.** The swept
  wallet is excluded from its own volume map, so its own volume read as `0.0` and
  any 4+4 match beat it — every record quarantined, sweep reported healthy, cursor
  advanced. Reproduced end-to-end before the fix.
- **The tracer lost its novelty gate**, which would have re-alerted stale history
  on a 24-hour loop forever while permanently shadowing new smaller movements.

**Process lesson.** The plan prescribed exact code, which made it authoritative
enough that implementers transcribed it faithfully rather than thinking. The best
catches came from the two dispatches that explicitly said "the plan may be wrong;
if it cannot pass its own test, reality wins." **For Phase 2: specify intent,
invariants and tests precisely, and leave more of the implementation to be
derived rather than copied.**

---

## Reference

- Spec: `docs/superpowers/specs/2026-08-28-universal-fund-tracing-phase1-design.md`
- Plan: `docs/superpowers/plans/2026-08-28-universal-fund-tracing-phase1.md`
  (kept in sync with every fix — plan and code do not drift)
- Ledger (**gitignored**, may not exist):
  `.superpowers/sdd/2026-08-28-universal-fund-tracing-phase1/progress.md`
- Phase 2 ("The chase") and Phase 3 ("The empire") are scoped in spec §2.

To resume cold: `git log --oneline 0fca737f0..HEAD` is the authoritative record.
Every commit message states what it fixed and why.

---

## Addendum — 2026-08-31

### Residual findings from the final gate: all three fixed

- `4a2774393` — the README-documented `investigate_wallet` path always inherited
  the 2700s backfill budget inside a 600s job, so it would blow the timeout and
  discard its cursors. Now bounded to what that job can afford.
- `bd352b5d7` — `fetch_code` fired real HTTP without an `ETHERSCAN_API_KEY`,
  against the codebase's own convention; the test meant to cover it over-mocked
  and never exercised the path. Both fixed.
- `19c4b0938` — a corrupt `traced_outbound.json` was indistinguishable from a
  clean first run and silently suppressed that run's alerts. A missing file is
  still a quiet first run; a file that exists but will not parse now raises and
  says so. The write is atomic via a shared `atomic_write_json`, extracted from
  `save_latest` rather than reimplemented.

### The price source is built (commits `d8d0332c8`..`87783a75a`)

`src/chain/prices.py` supplies `PriceCache`'s fetcher from CoinGecko, wired into
the tracer and the backfill. **This closes the "collection is multi-asset,
detection is stablecoin-only" gap** listed as open item 2 above.

Design points worth remembering:

- **Best-effort within a per-run budget.** Pricing 11 majors across a year is
  ~4,000 requests against a ~10-30/min free tier. Unaffordable in one run — so
  the budget is small, the cache fills incrementally across runs, and unpriced
  records are retained rather than dropped. That is what makes incremental
  coverage safe.
- **Budget arithmetic:** trace worst case +17s inside ~39s of slack; backfill
  +95s inside ~805s. Both fit without moving a job timeout.
- `expand_frontier` is **deliberately unpriced** — it shares the trace job's
  tight margin and frontier data is lower value. A design choice, recorded in
  `c37af64af` with a regression test. Worth revisiting if frontier ETH edges
  turn out to matter.
- **`MAJORS["POL"]` was pointing at a deprecated CoinGecko id** (`matic-network`
  → `polygon-ecosystem-token`). Found by verifying against the live API rather
  than trusting the plan.
- `COINGECKO_API_KEY` is optional — verified the endpoint works keyless for
  in-range dates, so a missing key is attempted rather than skipped. This
  deliberately differs from `ETHERSCAN_API_KEY`'s "skip cleanly" convention.

**Open at time of writing:** transient failures (connection error, timeout,
non-200 including 429, malformed JSON) were being cached as permanent misses.
`PriceCache` never re-requests a cached miss, so a single rate-limit response
permanently burned that `(symbol, date)` — and 429s are the expected steady
state on a free tier. The definitive/indeterminate split is being fixed; check
`git log src/chain/prices.py` for whether it landed. `MATIC` may still carry the
same stale id that `POL` had.

### Still true, and still the most important thing

**Nothing here has run against live data.** The $13M question remains open. Run
the Backfill workflow once with `full_reset: true` and a real
`ETHERSCAN_API_KEY` before scoping Phase 2.

### New follow-up

Records already stored as `price_unavailable` are not re-priced when the cache
later gains their date — pricing happens at collection time in `normalise_row`.
A repricing pass over stored records would make coverage genuinely self-healing.
Not built; deliberately out of scope.
