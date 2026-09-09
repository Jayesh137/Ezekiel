# RESUME HERE — say "continue where we left off"

**Last updated:** 2026-09-09 — Phase 1 complete, all loose ends closed
**Branch:** `feat/universal-fund-tracing` — **pushed to `origin`, everything committed, working tree clean**
**HEAD:** `fix(prices): resolve MATIC's coin id by date, and pin the cache split`
**Tests:** 665 passing (~20s) · `ruff check src tests scripts` clean · suite verified network-free

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
2. Run the **Substrate Backfill** workflow manually (`workflow_dispatch`) with
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

**All three code loose ends are closed** (2026-09-09, commit `93000c327`).
Phase 1 is complete. The only thing left is the live run.

For the record, what closed:

1. **Price-cache test coverage** — every failure case now asserts *both* the
   return value and whether anything reached disk, which is the half the
   original tests missed. Mutation-checked: 11 fail against the pre-fix module.
2. **`MAJORS["MATIC"]` settled, and the previous answer was wrong.** The id
   depends on the *date*, not the symbol: Polygon migrated MATIC to POL on
   2024-09-04, CoinGecko froze the old series and began a new one. Leaving MATIC
   statically on `matic-network` was wrong for every date currently reachable —
   the keyless window is 365 days and the migration was ~two years ago, so
   `_too_old` rejects every pre-migration date before a request is made, while a
   *post*-migration row still labelled MATIC (legacy-symbol contracts and
   bridged wrappers are common) burned a request and cached a miss for an asset
   that does have a price. `prices.MIGRATED_COIN_IDS` now resolves per date.
3. **`docs/superpowers/price-source-report.md`** — §9 (the definitive /
   indeterminate split) and §10 (the MATIC verdict, including what could *not*
   be verified live) are written.

So: **go run the backfill.** See the top of this document.

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

---

# ANSWERED — 2026-09-09

Phase 1 ran against live data. The `$13,000,000` question has an answer, and
finding it turned up two real bugs first.

## The answer

`0xa95d9c1f655341597c94393fddc30cf3c08e4fce` is **infrastructure, not a wallet.**

Swept directly, it has **15,193 distinct senders**, is USDC-only, and has zero
outbound. `detect_services` classifies it unaided: *"high fan-in (15185 distinct
senders)"*. Our target accounts for 18 of its 25,484 transactions.

So the money went into an exchange or bridge deposit address. The trail ends
there **correctly** — the graph scores it 0.0, never alerts on it, and never
traverses through it. Following it further is Phase 2's CEX-gap re-linking
problem, exactly where that was scoped.

The old truncated window also understated the relationship badly: it showed 5
records and $13M. The real figure from the cluster sweep is **$438,100,887.53
across 519 transfers**.

## Two bugs the live run exposed

**1. `endblock=99999999` silently ended every walk.** Hardcoded in all three
Etherscan requests. Arbitrum is past block 501,000,000, so once the cursor
climbed above 99,999,999 the window inverted and Etherscan answered *"No
transactions found"* — indistinguishable from genuinely reaching the end.
Measured directly:

```
startblock=281,189,292 endblock=99999999 -> status 0, "No transactions found"
startblock=281,189,292 endblock=latest   -> status 1, 1000 rows, 281M..289M
```

The sweep stopped 220 million blocks early reporting `truncated=False, gaps=0,
error=None`. Fixed to `latest`; a test asserts no request ever sends a numeric
ceiling again. Records went 1,057 -> 2,805 on the next run.

**2. The health output hid it.** `chain_result["cursor"]` reported the
furthest-along kind, so native's 501,442,874 masked erc20 stopping at
281,189,292. Now `cursor_by_kind` carries the per-kind truth, and
`client.newest_block` *verifies* completeness with one request instead of
inferring it from a short page — the chain is degraded when a sweep provably
stopped short. A failed probe records `unverified_kinds` and does NOT degrade,
because degrading on an unverifiable check would flag every chain on any
rate-limited run.

## What the live runs proved about the rest

| | |
|---|---|
| Spam quarantine | 49,944 suppressed against 24,967 kept on one address — the poisoning problem is real and handled |
| Multi-chain | Ethereum turned up 687 records the old Arbitrum-only system never saw, incl. repeated $7.25M USDC |
| Degradation reporting | `base`, `optimism`, `bsc` return *"Free API access is not supported for this endpoint"* and are named, not silently empty |
| Truncation reporting | *"budget ran out before finishing ['arbitrum'] — re-run (without --reset) to continue"* |

**Correction to the spec:** "six chains on one Etherscan key" is wrong. The free
tier serves **arbitrum, ethereum, polygon** for these account endpoints. The
other three need a paid plan.

## Next

- The `0xa95d9c1f` sweep is **truncated** — re-run Substrate Backfill with
  `wallet` set and **no** `full_reset` to continue from its cursor, if a full
  picture of that address is ever wanted. It is a service, so probably not.
- Phase 2 ("The chase") is now the real work: CEX-gap re-linking is what picks
  the trail up on the far side of an address like this one.
