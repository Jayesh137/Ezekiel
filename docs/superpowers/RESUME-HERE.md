# RESUME HERE — say "continue where we left off"

**Last session ended:** 2026-08-31 (hit the weekly API limit mid-task)
**Branch:** `feat/universal-fund-tracing` — **pushed to `origin`, everything committed, working tree clean**
**HEAD:** `fix(prices): stop transient failures poisoning the price cache`
**Tests:** 640 passing (~20s) · `ruff check src tests scripts` clean · suite verified network-free

Nothing is lost. Everything below is in git and on GitHub.

---

## THE ONE THING THAT MATTERS MOST

**None of this has ever run against live data.** Every check is unit-level or
replayed from stored records. The system has never done a real multi-chain sweep.

**The $13,000,000 question is still open.** In June 2026, `0xa95d9c1f…` received
$13M from the target across 14 transfers and returned none of it. The old
collection layer could not follow it. The new one should be able to — but nobody
has pressed the button.

**Do this first, before anything else:**

1. Confirm `ETHERSCAN_API_KEY` is set in the repo's GitHub Actions secrets.
2. Run the **Backfill** workflow manually (`workflow_dispatch`) with
   `full_reset: true`. It has a 60-minute timeout and re-reads from block 0
   across six chains.
3. Read the results:
   - `data/transfers/latest.json` — `records`, `spam_suppressed`, `unpriced`,
     `degraded_sources`, `possible_gaps`
   - `data/transfers/{chain}/` — one directory per chain the target has touched
   - `data/transfer_graph/latest.json` — `services` should now have far more
     than three entries
   - Search for `0xa95d9c1f655341597c94393fddc30cf3c08e4fce` — does it now have
     outbound edges?

**A pass is:** either the $13M has an onward trail, or `degraded_sources` says
precisely why not. "No result and no explanation" is a failure, not a pass — the
whole branch is built so blindness is reported rather than inferred.

The result should shape what Phase 2 actually is. Do not scope Phase 2 first.

---

## Why the branch is NOT merged to `main`

Deliberate, and worth a moment before you merge.

`main` drives live workflows that email you every 30 minutes. This branch changes
what the tracer collects, what the graph is fed, and what alerts say. It is
reviewed and green, but unproven against real data. Merging makes it live in the
same motion.

The safer order: run the backfill from the branch (or merge, run once, and watch
closely). **My recommendation is to run it first and merge after you have seen
real output.** Nothing about the branch expires.

---

## What we did this session

### Built Phase 1 end to end — 12 tasks

A multi-chain collection substrate under `src/chain/`, replacing a layer that
read **one asset (USDC) on one chain (Arbitrum)** through a **single
un-paginated 1,000-row request**. Measured problems it fixes, all from live data:

- **905 of 1,000 stored records were address-poisoning dust.** One forged
  address mimicking the trader's own known wallet accounted for **510** of them.
- The target has **five real counterparties**; spam filled the rest of the window.
- Only **three** service addresses were known, so a Binance hot wallet was
  indistinguishable from a fresh personal wallet.

Each task: fresh implementer → independent review → fix rounds until clean.
Then a whole-branch review, a fix wave, and a scoped re-review.

### The four worst bugs the reviews caught — all in *my own plan*

1. **A price outage would have silently deleted every ETH transfer.** `value_usd`
   returned the same `(None, "unpriced")` for a scam token and for ETH that
   merely failed to price; `classify_spam` quarantined on `amount_usd is None`
   alone; quarantined records never reach disk; the cursor had already advanced.
   And `price_lookup` defaulted to returning `None` — so this was the *default*.
2. **A $1 transfer could have erased the $13M counterparty.** The lookalike rule
   was a symmetric membership test, so a vanity clone that paid $1 joined the
   "real" set and the *genuine* address then matched it and was quarantined. Fix:
   value-ordering — nobody forges an address poorer than their own.
3. **The same rule, one layer up, erased a wallet's entire sweep.** The swept
   wallet is excluded from its own volume map, so its volume read `0.0` and any
   4+4 match beat it. Every record quarantined, sweep reported healthy, cursor
   advanced. Reproduced end-to-end before the fix.
4. **The tracer lost its novelty gate**, which would have re-alerted stale
   history on a 24-hour loop forever while permanently shadowing new movements.

Also: a **shell injection** in `trace.yml` (`${{ inputs.* }}` spliced into `run:`
text), and the **documented** `investigate_wallet` path inheriting a 2700s budget
inside a 600s job — so it would have blown its timeout and discarded its cursors.

### Built the price source (last piece of Phase 1)

`src/chain/prices.py` — CoinGecko, wired into the tracer and backfill. Closes the
"collection is multi-asset, detection is stablecoin-only" gap, so ETH/WBTC/ARB
movements can now become graph edges and fire alerts.

- Best-effort within a small per-run budget (pricing 11 majors across a year is
  ~4,000 requests against a ~10-30/min free tier). The cache fills incrementally
  across runs — safe only because unpriced records are retained.
- Budget: trace +17s inside ~39s slack; backfill +95s inside ~805s.
- Found `MAJORS["POL"]` was pointing at a **deprecated** CoinGecko id.
- Last fix of the session: **transient failures were being cached as permanent
  misses.** A 429 — the expected steady state on a free tier — permanently burned
  that `(symbol, date)`, because `PriceCache` never retries a cached miss.

---

## PICK UP EXACTLY HERE

The price-source agent died writing its report, *after* finishing and committing
the code. Three small loose ends:

### 1. Finish the price-source test coverage (small)

`tests/test_chain_prices.py` has 27 tests but only ~4 assert the **cache side**.
I verified the full taxonomy by hand and it is correct, but it is not locked in.

Add tests asserting **both** the return value and whether a cache file was written:

- **Must return `None` and write NOTHING:** connection error, timeout, 429, 500,
  malformed JSON, non-object body, budget exhausted
- **Must return `None` and CACHE the miss:** unknown symbol, date outside the
  free-tier window, well-formed 200 with no usable `usd`
- **The regression guard:** a retry after a transient failure reaches the
  transport again and can succeed

### 2. Settle `MAJORS["MATIC"]` (small)

`POL` was corrected to `polygon-ecosystem-token`; `MATIC` still maps to
`matic-network`. The agent's last finding before dying: for a post-migration date
`matic-network` returns HTTP 200 with **no `market_data` at all** — a clean
"nothing here", consistent with "frozen at migration, not deleted" — while
`polygon-ecosystem-token` has live data. It could **not** query a pre-migration
date (the 365-day keyless cap applies uniformly). So: either `matic-network` is
still right for historical MATIC, or it is the same staleness half-fixed. Settle
it and record the verdict.

### 3. Finish `docs/superpowers/price-source-report.md`

The definitive/indeterminate split section was never written.

---

## Known-open, deliberately not done

- **Repricing pass.** Records already stored `price_unavailable` are not
  re-priced when the cache later gains their date — pricing happens at collection
  time in `normalise_row`. Makes coverage genuinely self-healing. Worth doing.
- **`expand_frontier` is deliberately unpriced** — shares the trace job's tight
  margin, frontier data is lower value. Recorded with a regression test. Revisit
  if frontier ETH edges matter.
- **`data/` is ~142 MB** and every run does `git add data/` every 30 minutes.
  Fine now, not indefinitely. `scripts/compact_data.py` exists.
- **`spam.rollup` keys on address only**, so a second distinct unpriced token
  from the same sender is dropped. Fix: key on `(address, token)`.
- **`records_for` does a full linear scan** on every call — now per frontier
  wallet, per candidate, per correlator run.
- ~25 further deferred minors, each with a written ruling, in the ledger.

---

## Process lesson for Phase 2

**Nine of twelve tasks found genuine bugs in the plan's own prescribed code.**

The plan specified exact code, which made it authoritative enough that
implementers transcribed it faithfully instead of thinking. The best catches came
from the dispatches that said explicitly: *"the plan may be wrong; if it cannot
pass its own test, reality wins."* The price source — specified as intent,
invariants and required tests rather than code — produced the best work of the
session and caught a deprecated API id I had wrong.

**For Phase 2: specify intent, invariants and tests precisely. Leave the
implementation to be derived, not copied.**

---

## Reference

| | |
|---|---|
| Full handoff | `docs/superpowers/HANDOFF-universal-fund-tracing-phase1.md` |
| Spec | `docs/superpowers/specs/2026-08-28-universal-fund-tracing-phase1-design.md` |
| Plan (kept in sync with every fix) | `docs/superpowers/plans/2026-08-28-universal-fund-tracing-phase1.md` |
| Per-task ledger (tracked copy) | `docs/superpowers/ledger-universal-fund-tracing-phase1.md` |
| Final fix report | `docs/superpowers/final-fix-report-universal-fund-tracing-phase1.md` |
| Price source report (incomplete) | `docs/superpowers/price-source-report.md` |

Phase 2 ("The chase") and Phase 3 ("The empire") are scoped in spec §2.

`git log --oneline 0fca737f0..HEAD` is the authoritative record — every commit
message states what it fixed and why.
