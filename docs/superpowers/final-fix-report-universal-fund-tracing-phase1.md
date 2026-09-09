# Final fix wave — `feat/universal-fund-tracing`

Eight commits, one per finding, on `feat/universal-fund-tracing`. No branch created,
nothing merged, nothing pushed.

**Suite: 545 → 581 passing (36 new tests), 6.2s → 9.1s.** Ruff clean across
`src tests scripts`. `git status` clean; `git diff --stat 99bc18f1c..HEAD -- data/`
is empty, so nothing under `data/` was touched by this work or leaked from a test run.

| Commit | Finding |
|---|---|
| `e3c263644` | C1 — spam classifier erasing the swept wallet's sweep |
| `0fa76d500` | C2 — tracer's lost incremental gate |
| `5667b47be` | C3 — frontier marking wallets explored after reading nothing |
| `0b85616ef` | I4 — address-reuse signal dark for candidates |
| `6f5a76044` | I5 — blindness record not written on the scheduled path |
| `dcbd3bb49` | I6 — bytecode tier unwired; docs overclaiming |
| `9ad3ef1bd` | I7 — budget arithmetic exceeding workflow timeouts |
| `b9ce18369` | MINOR — README `full_reset` / `--reset` walkthrough |

---

## Commands run

Baseline, before any change:

```
$ .venv/Scripts/python.exe -m pytest -q
545 passed in 6.21s
real  0m7.150s
```

After each Critical, and at the end:

```
$ .venv/Scripts/python.exe -m pytest -q        # after C1
549 passed in 6.64s
$ .venv/Scripts/python.exe -m pytest -q        # after C2
556 passed in 6.23s
$ .venv/Scripts/python.exe -m pytest -q        # after C3
560 passed in 7.86s
$ .venv/Scripts/python.exe -m pytest -q        # final
581 passed in 9.12s
real  0m10.140s

$ .venv/Scripts/python.exe -m ruff check src tests scripts
All checks passed!
```

Runtime went 6.2s → 9.1s. That is the 36 new tests, not the network: the two
new live-lookup paths (`linkage._live_outbound_usdc`, `label_contracts`) are
covered by tests that either stub `etherscan_get`/`fetch_code` or assert
`pytest.fail` if it is reached, and two tests
(`test_without_a_key_the_live_half_degrades_to_empty_rather_than_erroring`,
`test_labelling_never_reaches_the_network_without_a_key`) exist specifically to
pin that. An order-of-magnitude jump did not occur.

Each fix was also confirmed to *fail* before the change, by stashing only the
source file and re-running the new tests. Those outputs are quoted per finding
below.

---

## C1 — a $1 transfer erasing an entire wallet's sweep

**Files:** `src/chain/spam.py`, `src/chain/collect.py:205-227`

`counterparty_volume` excludes the swept wallet by construction, so
`classify_spam` running `is_lookalike` over *both* sides read the wallet itself
as `$0.00` of volume — beaten by any counterparty sharing its first-4/last-4 hex
that cleared `dust_usd`.

Fix, the reviewer's preferred option: the swept wallet is passed in and skipped
as a forgery candidate.

- `spam.forged_side(record, volume, *, wallet=None, ...)` — new, and now the
  **single** definition of "which side of this record is a forgery".
  `classify_spam` and `collect.py`'s rollup-labelling loop both call it. Before,
  `collect.py` re-derived the forged side with its own copy of the loop, which
  is how the quarantine reason and the address the rollup files it under drift
  apart.
- `classify_spam` gains a keyword-only `wallet`. Defaulting to `None` keeps the
  old behaviour, so every existing pure-function test is unchanged and still
  passing.
- `collect.py` passes `wallet=addr` to both.

`is_lookalike`'s contract stays coherent: its docstring now states explicitly
that `volume` is a *counterparty* map, that the swept wallet is absent from it,
and that callers must exclude the wallet themselves — with `forged_side` named
as the thing that does it.

**Evidence it was real, and is fixed.** Stashing only `src/chain/spam.py` and
`src/chain/collect.py`:

```
$ git stash push -- src/chain/spam.py src/chain/collect.py
$ .venv/Scripts/python.exe -m pytest tests/test_chain_collect.py -q -k "funded_forgery or counterparty"
>       assert chain["records"] == 2          # was 0
E       assert 0 == 2
1 failed, 1 passed, 19 deselected in 0.23s
```

`records == 0` reproduces the reviewer's end-to-end result exactly. The second
test (the forgery still being caught as a counterparty) passes both before and
after — it is the non-regression guard.

**Tests.** `tests/test_chain_collect.py`:
`test_a_funded_forgery_does_not_erase_the_swept_wallets_own_sweep` (sweeps the
real `known_self_wallets` address with its real live forgery paying $1.01;
asserts 2 records kept, 0 quarantined, `spam_by_reason == {}`,
`degraded_sources == []`, both records on disk, and readable through
`records_for`); `test_a_forgery_is_still_quarantined_when_it_appears_as_a_counterparty`
(sweeps the target; the forgery is adjudicated against the $13.5M self-wallet as
before, filed under the forgery with `mimics` pointing at the victim).
`tests/test_chain_spam.py`:
`test_the_swept_wallet_is_never_a_forgery_of_its_own_counterparty` (also asserts
the old behaviour still holds when `wallet` is omitted) and
`test_forged_side_names_the_forgery_and_never_the_swept_wallet`.

All 8 entries in `tests/fixtures/poisoning_live.json` still classify as
`lookalike` through both `is_lookalike` and `classify_spam` — those two fixture
tests are untouched and passing.

**One behaviour change worth naming.** During a cluster wallet's *own* sweep, a
≥ `dust_usd` ping from a forgery *of that wallet* is now retained as a real
record instead of quarantined. It cannot be adjudicated there, because the
wallet is not its own counterparty and so has no volume to compare against. That
same forgery is still quarantined on every other wallet's sweep where the real
address *is* a counterparty — which is the live case in the module docstring
(510 records against the target). Retaining one $1 record is the conservative
side of constraints 2 and 4; the alternative was losing $13.5M.

---

## C2 — the tracer's lost incremental gate

**Files:** `src/tracer.py`

### The marker

A per-wallet **set of record ids**, at `data/state/traced_outbound.json`, not a
block or timestamp high-water mark. Reason: `unique_destinations` orders by
value and truncates at `MAX_DESTINATIONS`, so what a run actually processes is
not a contiguous prefix of anything. A positional marker would have to either
skip the deferred tail permanently or re-offer the traced head forever — and
"advance it only for records actually processed" is precisely what the finding
required.

New functions: `_traced_path` / `_load_traced` / `_save_traced`,
`untraced_outbound(wallet, rows)`, `mark_traced(wallet, ids, *, known_ids=None)`,
and `is_traceable(transfer, wallet)` (extracted verbatim from
`unique_destinations`, whose behaviour is unchanged).

`_as_etherscan_row` gains one additive key, `record_id`. Nothing downstream
reads it; `to`/`value`/`hash`/`blockNumber`/`timeStamp`/`tokenSymbol`/`chain`
are untouched, so `unique_destinations`, `build_finding` and the whole alert
path are byte-identical.

### Where the gate sits

`trace_fund_flow` now reads `stored = trace_outbound_transfers(wallet)` then
`outbound = untraced_outbound(wallet, stored)` — the filter applied **before**
`unique_destinations`, so the value-ordered cap ranks only untraced work.
`trace_outbound_transfers` keeps its existing contract (all stored outbound
rows), which is why the five pre-existing `test_outbound_transfers_*` tests are
unchanged.

### What advances, and what does not

After the loop:

- `traced_dests` = the destinations actually iterated, before any time-budget break.
- `deferred` = `{traceable destinations in this run's input} - traced_dests` —
  i.e. everything dropped by the `MAX_DESTINATIONS` cap or the deadline. Their
  ids stay **unmarked** and come back next run. This is what turns the cap back
  into a per-run cap instead of the permanent ceiling the finding identified.
- Everything else is marked, **including** rows `is_traceable` rejects (zero
  value, self-transfer). Without that the wallet stays permanently "dirty" and
  "no new outbound transfers" never prints again.
- `known_ids` intersects the marker with the ids the substrate still holds, so
  it cannot grow past the outbound history it tracks. It is ignored when empty,
  so a transiently unreadable substrate cannot erase the marker.

### First-run decision (latitude taken)

**Chosen: seed the marker to everything currently stored, and alert on nothing.**

The substrate is deliberately deep — the backfill recovers history back past
2025-11-30 — so "alert on everything" means a burst of up to 50 CRITICAL emails
about months-old transfers on the first scheduled run after merge, which is the
exact failure the gate exists to prevent. Nothing is discarded by seeding: the
records stay in `data/transfers/`, on the transfer graph, and in whatever
`fund_flows` findings earlier runs recorded. Only the email is suppressed, and
only for movements that predate the gate.

It is made non-silent rather than implicit: the seed prints
`[tracer] First run of the incremental gate for <wallet>: N stored outbound
record(s) marked as already-seen…` and stamps `seeded_at` / `seeded` into the
marker file, so "we chose not to look at this" is legible on disk.

Consequence for tests: a fresh environment *is* a first run, so the five
pre-existing `trace_fund_flow` tests would have seeded and found nothing. They
now call a one-line `_gate_already_initialised(tmp_path)` helper that writes an
empty-but-present marker. That is deliberate — it makes each test say which run
it represents rather than relying on the absence of a file.

The one detection cost: `find_hl_deposits` is not run against the historical
destinations on the first run. Those destinations were already walked by the
pre-branch tracer as the transfers happened, and they remain reachable through
`expand_frontier` and the correlator, both of which read the same substrate.

**Evidence.**

```
$ .venv/Scripts/python.exe -m pytest tests/test_chain_tracer_substrate.py -q
18 passed in 0.56s
```

**Tests** (all in `tests/test_chain_tracer_substrate.py`):
`test_the_first_run_seeds_the_marker_instead_of_alerting_on_all_history`
(0 findings, 0 alerts, marker seeded, the print present, `seeded`/`seeded_at`
stamped); `test_an_already_traced_record_is_not_re_alerted_on_the_next_run`
(run 1 alerts and the marker advances; run 2 alerts nothing);
`test_a_genuinely_new_record_is_still_traced_after_the_gate`;
`test_the_no_new_outbound_message_is_accurate_again` (asserts the message text
and that zero-value / self-transfer rows are marked);
`test_a_destination_deferred_by_the_per_run_cap_is_traced_on_the_next_run`
(51 destinations: 50 traced and marked, the 51st unmarked and picked up next
run); `test_the_marker_never_grows_past_the_records_it_tracks`;
`test_an_unreadable_substrate_never_erases_the_marker`.

`tests/conftest.py` gains `data/state/traced_outbound.json` as a probe, so a
future test that reaches `trace_fund_flow` without repointing `tracer.DATA_DIR`
fails loudly instead of marking production records as already-traced. Confirmed
absent from the real `data/state/` after a full run.

---

## C3 — the frontier marking wallets explored after reading nothing

**File:** `src/transfer_graph.py`

`sweep_wallet`'s return value is now captured. On `degraded_sources` non-empty
**or** `status != "ok"`, the wallet is **not** added to `explored`, a
`partial_failures` entry naming the wallet, depth and failing `chains` is
recorded, those chain names are merged into `diag["degraded_sources"]`, and the
decision is logged as `deferred` with the chains named — so it is re-queued and
retried next run.

The four hardcoded `diag["degraded_sources"] = ["arbitrum_l1"]` assignments
inside `expand_frontier` are replaced by a `degrade(names)` helper that
**merges** rather than assigns (a later failure must not erase an earlier one)
over the real enabled chain names. The `expand=False` branch in
`run_transfer_graph` is updated the same way. Verified — `arbitrum_l1` no longer
appears anywhere in `src/`:

```
$ grep -n "degrade(\|all_chain_names\|arbitrum_l1" src/transfer_graph.py
1516:    # Expansion spans every enabled chain. "arbitrum_l1" was the honest label
1519:    all_chain_names = [c["name"] for c in sweep_chains]
1526:    def degrade(names):
1538:        degrade(all_chain_names)      # no API key
1632:                         "chains": list(all_chain_names)})
1633:                    degrade(all_chain_names)   # sweep raised
1659:                    degrade(degraded)          # sweep degraded
1695:        degrade(all_chain_names)      # outer failure
1759:            degrade(all_chain_names)  # total outage
```

The chain imports and `sweep_chains` moved above the API-key check so the skip
path can name the chains too; they are still function-local, so module load
order is unchanged.

**Decision worth flagging:** a degraded wallet's rows are *not* ingested as
edges this run (`continue`, mirroring the existing exception branch). Nothing is
lost — `sweep_wallet` already persisted what it collected, and
`collect_known_edges` reads the whole `data/transfers/` tree, so those records
seed the very next run. The alternative (ingest but defer) would have made
`expanded_now` mean two things at once, and `expanded_now` gates the
"total outage → failed" branch.

**Evidence.** Stashing only `src/transfer_graph.py`:

```
$ .venv/Scripts/python.exe -m pytest tests/test_continuity.py -q -k "degraded_sweep or budget_exhausted_sweep or want_of_a_key or clean_sweep_returning"
FAILED tests/test_continuity.py::test_a_degraded_sweep_leaves_the_wallet_out_of_explored
FAILED tests/test_continuity.py::test_a_budget_exhausted_sweep_does_not_look_like_an_empty_wallet
FAILED tests/test_continuity.py::test_a_sweep_skipped_for_want_of_a_key_is_not_a_finished_expansion
3 failed, 1 passed, 33 deselected in 0.66s
--- Captured stdout ---
[graph] L1 expansion ok: 1 lookup(s) to depth 1, 0 new edge(s), 0 wallet(s) queued
```

That captured line is the symptom itself: a wallet whose sweep read nothing,
reported as `ok` with `0 new edge(s)`. The fourth test (a clean sweep still
marks the wallet explored) passes both before and after — it is the guard
against turning the retry path into a treadmill.

**Tests** (`tests/test_continuity.py`):
`test_a_degraded_sweep_leaves_the_wallet_out_of_explored` (A not in
`expanded_ledger`, B still is, `partial_failures[A]["chains"] == ["base","bsc"]`,
`diag["degraded_sources"] == ["base","bsc"]`, A back in `frontier_queue`);
`test_a_budget_exhausted_sweep_does_not_look_like_an_empty_wallet` (constraint 2:
both wallets return zero rows, one is `expanded` and one `deferred`);
`test_a_sweep_skipped_for_want_of_a_key_is_not_a_finished_expansion`;
`test_a_clean_sweep_returning_nothing_still_marks_the_wallet_explored`.

Two pre-existing tests asserted the stale `"arbitrum_l1"` literal
(`test_total_lookup_outage_is_reported_as_failed`,
`test_missing_api_key_degrades_explicitly_not_silently`). Both updated to assert
the real enabled chain names, plus `len(expected) > 1` so the assertion cannot
pass vacuously on a single-chain config.

---

## I4 — the address-reuse signal dark for candidates

**File:** `src/linkage.py`

**Approach chosen: keep the substrate read for swept wallets, and union in the
one live Etherscan lookup for everything else.** Not sweeping. Justification:

1. **A sweep has side effects far outside linkage.** `sweep_wallet` writes the
   candidate's entire history into the shared substrate, and
   `collect_known_edges` reads that whole tree into the transfer graph. Scoring
   a leaderboard wallet would therefore add unrelated wallets' edges to the
   target's graph — a much larger behavioural change than the finding calls for,
   in a wave with no second review.
2. **`scanner.py` has no call budget.** `_apply_linkage` runs for every
   candidate with `score >= 0.70 or rare`. Six chains × three kinds per
   candidate, unbounded, inside a job with its own timeout.
3. **One call is what this path already spends.** `check_candidate` already
   calls `get_first_funder(wallet)`, which makes one or two live Etherscan calls
   per candidate. Adding one more matches pre-branch cost and pre-branch
   behaviour exactly.

**Unioned, not fallback-on-empty.** The finding itself notes that for an unswept
candidate `records_for` returns the records where it transacted with an
*already-swept* wallet — so a non-empty result is not evidence the wallet was
swept, only that it touched something that was. Falling back only on empty would
leave that case with a partial set that looks like an answer. `swept_wallets(config)`
(target + `known_self_wallets`) decides which side of the line a wallet is on;
the target therefore keeps the pure-substrate, all-chain, zero-call path the
branch introduced.

The live half applies the **same** exclusion set as the substrate half (curated
registry minus the two deposit categories, `known_service_addresses`, the HL
bridge, self), so the "cryptographic certainty" bonus and the standalone
`alert_linkage_match` cannot be reached through a weaker rule. No threshold,
weight or gate moved.

`get_outbound_usdc_addresses`'s `limit` parameter is meaningful again — it caps
the live page — and its docstring says so.

**Evidence.** Stashing only `src/linkage.py`:

```
$ .venv/Scripts/python.exe -m pytest tests/test_migration_signals.py -q -k "unswept"
FAILED tests/test_migration_signals.py::test_an_unswept_candidate_still_yields_its_outbound_addresses
FAILED tests/test_migration_signals.py::test_an_unswept_candidates_partial_substrate_is_unioned_not_trusted
2 failed, 27 deselected in 0.20s
```

**Tests** (`tests/test_migration_signals.py`):
`test_an_unswept_candidate_still_yields_its_outbound_addresses` (the required
one — also asserted through `get_outbound_usdc_addresses`, the alias the scanner
actually calls); `test_an_unswept_candidates_partial_substrate_is_unioned_not_trusted`;
`test_a_swept_wallet_still_costs_no_api_call` (`pytest.fail` on
`etherscan_get`); `test_the_live_lookup_excludes_infrastructure_exactly_like_the_substrate_path`;
`test_without_a_key_the_live_half_degrades_to_empty_rather_than_erroring`.

**Network-safety note.** The five pre-existing `get_outbound_addresses` tests use
`"0xtarget"`, which is not the configured target, so after this change they take
the live path. They passed only because no `ETHERSCAN_API_KEY` happened to be
exported — on a machine with one, they would have hit the network. Each now
calls a `_swept(monkeypatch, "0xtarget")` helper that declares the population it
is testing. That is a correction to a latent hazard, not a workaround.

---

## I5 — the blindness record on the scheduled path

**Files:** `src/chain/collect.py`, `src/tracer.py`, `scripts/backfill_transfers.py`

`trace_outbound_transfers` now writes its sweep result. Three new functions in
`collect.py`: `read_sweep_health`, `merge_sweep_health`, `save_sweep_health`.

**Merge semantics.** Per-wallet detail for wallets *this* run did not sweep is
carried over from the file and listed under a new `carried_over_wallets` key.
Totals and `degraded_sources` describe **this run only**, deliberately: folding
in a chain the other job could not read last week would leave an outage showing
long after it ended, which is the mirror image of the failure this file exists
to prevent.

`scripts/backfill_transfers.py` goes through the same writer, so the merge holds
in both directions. `save_sweep_health(results, directory=None)` takes the
directory explicitly for the backfill (keeping its `bf.TRANSFERS_DIR` patch
load-bearing) and resolves `collect.TRANSFERS_DIR` at call time for the tracer
(matching how the tracer tests patch). The tracer guards on
`isinstance(result, dict)` so a stubbed `sweep_wallet` returning `None` writes
nothing.

**`expand_frontier` deliberately still does not write this file**, as the
finding permits. Its degradation is now fully reported by C3 through
`diag["degraded_sources"]` and `diag["partial_failures"]` in the graph health
artifact, and folding up to `max_expansions` frontier wallets' per-chain detail
into a file committed every 30 minutes would grow it without surfacing anything
not already visible. This did not complicate C3; it is a size/signal judgement.

**Evidence.** Stashing `src/tracer.py`, `src/chain/collect.py`,
`scripts/backfill_transfers.py`:

```
FAILED tests/test_chain_collect.py::test_merge_keeps_the_other_jobs_per_wallet_detail
FAILED tests/test_chain_tracer_substrate.py::test_the_tracer_writes_sweep_health_so_an_outage_is_visible
FAILED tests/test_chain_tracer_substrate.py::test_a_skipped_sweep_is_recorded_rather_than_reading_as_a_quiet_run
3 failed, 566 deselected in 1.13s
```

**Tests.** `tests/test_chain_tracer_substrate.py`:
`test_the_tracer_writes_sweep_health_so_an_outage_is_visible` (a degraded chain
reaches `latest.json`), `test_a_skipped_sweep_is_recorded_rather_than_reading_as_a_quiet_run`.
`tests/test_chain_collect.py`: `test_merge_keeps_the_other_jobs_per_wallet_detail`
(both wallets present, `carried_over_wallets` correct, totals and
`degraded_sources` scoped to this run, on-disk content equals the return value),
`test_a_re_sweep_of_the_same_wallet_replaces_rather_than_duplicates`.

---

## I6 — the bytecode constraint, enforced and honestly documented

**Files:** `src/transfer_graph.py`, spec §7, `README.md`

### Part 1 — wired

`transfer_graph.label_contracts(edges, known_services, config, dust_usd, cache=None)`
builds a `CodeCache` at `data/labels/code_cache.json` backed by
`client.fetch_code`, and adds every address with bytecode to `known_services`.

**Call site: after expansion, before `build_graph`** (`run_transfer_graph`,
line 1935), wrapped in a `try/except` that prints and continues — labelling must
never break the graph. `build_graph` is where a node is graded, so that is where
"an address with bytecode can never be graded `MIGRATION_CANDIDATE`" has to hold;
running it there also covers the addresses expansion just discovered, which a
pre-expansion pass would miss for a whole run.

**Scope chosen:** the destination of every **expandable** (non-dust, non-bridge)
edge that is not already a known service. Reasons:

- That is exactly the set `build_graph` can grade — a node exists only because
  value reached it — so it is the smallest set that fully enforces the constraint.
- It reuses `_expandable_edges`, so it excludes the sub-dollar poisoning clones
  that make up most raw edges (770 of the target's 874 recorded out-edges).
- Each address is asked on the chain its **largest** edge was observed on: a
  contract at an address on one chain need not exist at the same address on
  another, so the question is only meaningful per chain.
- Ordered **highest value first**, so if the per-run cap bites, the addresses
  closest to being graded are the ones that got checked.
- Capped at `MAX_CODE_LOOKUPS_PER_RUN = 25` / `CODE_LOOKUP_SECONDS = 20`. The
  cache makes it one call per address *ever*, so the backlog drains over a few
  runs and the steady-state cost is zero.

A failed lookup returns `None` and marks nothing — absence of evidence is not
evidence of an externally owned account — and `CodeCache` does not cache it, so
a rate-limited run is retried rather than remembered wrong.

`data/labels/code_cache.json` added to `conftest.py`'s probes: asked once per
address ever means a wrong entry written by a test is permanent.

### Part 2 — documented honestly

Spec §7 now opens with what Phase 1 actually ships (curated, contract-code,
fan-degree) and marks each tier `(shipped)` or
`(Phase 2 — specified, implemented as a pure function, not wired)`. Tier 2 names
`transfer_graph.label_contracts` as the enforcer. The README's Entity-labels
bullet says the same in operational terms.

Not changed, and flagged rather than silently fixed: the spec's numbered list
orders fan-degree (3) above inferred (4), while `classify_address` resolves
inferred *first*. The section's own "Hard rules" already state the correct
precedence ("an inferred label always beats fan-degree"), so the code is right
and only the list ordering is loose. Renumbering was out of the finding's scope
and would have muddied this diff.

**Tests** (`tests/test_chain_labels.py`, 9 new):
`test_an_address_with_bytecode_becomes_a_known_service`;
`test_a_contract_can_never_be_graded_a_migration_candidate` — the constraint
end-to-end through `build_graph`, asserting `CLASS_SERVICE` and `confidence == 0.0`
*and* asserting that the same address without the bytecode tier is **not**
graded a service, so the test cannot pass vacuously;
`test_a_failed_lookup_never_marks_an_address_codeless`;
`test_only_gradeable_addresses_are_checked` (sub-dust and already-known
addresses cost no call); `test_each_address_is_checked_on_the_chain_it_was_seen_on`;
`test_the_highest_value_addresses_are_checked_first`;
`test_code_cache_asks_once_per_address_and_never_caches_a_failure`;
`test_labelling_a_graph_without_enabled_chains_is_a_no_op`;
`test_labelling_never_reaches_the_network_without_a_key`.

---

## I7 — budget arithmetic

**Files:** `config.json`, `.github/workflows/trace.yml`, `.github/workflows/backfill.yml`,
`src/tracer.py`, `src/transfer_graph.py`

- `collection.time_budget_seconds`: **420 → 150**
- `backfill.time_budget_seconds`: **3300 → 2700**
- `MAX_CODE_LOOKUPS_PER_RUN` 40 → 25, `CODE_LOOKUP_SECONDS` 120 → 20

`config.json`'s structure and every other key are untouched (verified: all 23
top-level keys unchanged, only the two values above differ in `git diff config.json`).

**Deviation from the suggested number, stated deliberately.** The finding
suggested "roughly 240 + 180". I used **150**, because the finding's arithmetic
predates the bytecode labelling added in the previous commit, which adds its own
ceiling to the graph step. Full accounting for the 600s trace job:

| Budget | s |
|---|---|
| `config.collection.time_budget_seconds` (cluster sweep) | 150 |
| `tracer.TRACE_BUDGET_SECONDS` (destination loop) | 240 |
| `correlator.run_correlation` (one Etherscan call) | ~1 |
| `config.transfer_graph.time_budget_seconds` (expansion) | 150 |
| `transfer_graph.CODE_LOOKUP_SECONDS` (bytecode) | 20 |
| **total** | **561** |

leaving ~39s for checkout, `setup-python`, a cached `pip install` and the push.
At 180 the total would be 591s — inside the timeout but on the line, with no
room for a slow `pip install`. 150 is within the finding's "roughly" and is the
conservative side of it. The cost is 30 fewer seconds of cold-start collection
per 30-minute run, which is irrelevant: `--reset` on the 3600s backfill job is
the designed path for a cold start, and cursors are flushed per chain so the
trace job makes incremental progress regardless.

I verified the correlator is not an unbudgeted third consumer: `run_correlation`
makes exactly one `etherscan_get` call (`get_recent_bridge_deposits`).

The arithmetic is written out as a comment beside `timeout-minutes` in
`trace.yml`, pointed at from `tracer.TRACE_BUDGET_SECONDS`, and — more
importantly — **pinned by tests**, so it cannot drift silently again.

**Tests** (`tests/test_chain_budget.py`):
`test_the_trace_jobs_budgets_fit_inside_its_timeout` sums all four numbers
(reading `config.json`, `tracer.TRACE_BUDGET_SECONDS`,
`transfer_graph.CODE_LOOKUP_SECONDS`) against `timeout-minutes` parsed out of
`trace.yml` with a regex — PyYAML is not in `requirements.txt`, so the suite
must not depend on it — and requires 30s of headroom;
`test_the_backfill_jobs_budget_fits_inside_its_timeout` (600s headroom);
`test_the_collection_budget_is_not_wider_than_the_backfill_one`.

**Evidence the guards bite.** Restoring only the two pre-fix config values:

```
E       AssertionError: trace job budgets total 830s against a 600s timeout;
        a cancelled job discards the cursors it advanced
E       assert 830 <= (600 - 30)
```

and the backfill guard fails too (3300 > 3600 − 600). Both pass with the new
values. This guard also caught a mistake of mine mid-session — a stray
`git checkout -- config.json` silently reverted the uncommitted change, and the
test failed rather than letting it reach a commit.

---

## MINOR — README `full_reset` / `--reset` walkthrough

Added to the Transfer-substrate section, matching the existing
`investigate_wallet` block's style: the workflow input and the local command,
the once-only rule, the plain resume command, and what to do when a run reports
`TRUNCATED` — re-run *without* `--reset`, because re-running *with* it wipes the
cursor progress and the sweep can loop from block 0 forever. That is the
livelock `scripts/backfill_transfers.py`'s own docstring warns about, now stated
where an operator will actually read it.

---

## Constraint check

1. **No grading rule changes.** Confirmed by grepping the whole
   `99bc18f1c..HEAD` diff of `src/` and `config.json` for threshold, weight and
   gate identifiers. The only hit is the word "threshold" inside a new docstring.
   No threshold, confidence weight or alert gate moved.
2. **Empty ≠ failed.** Strengthened in three places: C1 (a swept wallet's real
   records no longer serialise as an empty quarantine while reporting
   `degraded_sources == []`); C3 (a degraded sweep is `deferred` with the chains
   named, where an empty one is `expanded` — pinned by
   `test_a_budget_exhausted_sweep_does_not_look_like_an_empty_wallet`); I5 (the
   record is now written on the scheduled path at all).
3. **Missing price yields `None`.** Untouched. `_as_etherscan_row` still returns
   `None` for `amount_usd is None`, and `classify_spam` still returns `None` for
   `value_basis == "price_unavailable"`.
4. **Quarantined records never become edges or consume budget.** Strengthened by
   C1 — real records are no longer quarantined in the first place.
   `normalise_transfer_record` still drops `spam` records, unchanged.
5. **An address with bytecode is not a person.** Now actually enforced (I6),
   with `test_a_contract_can_never_be_graded_a_migration_candidate` proving it
   through `build_graph` and proving the pre-fix behaviour differed.

## Nothing left unfixed

All eight findings are addressed. No fix turned out to be wrong or to conflict
with another; the one interaction (I6's new budget against I7's arithmetic) is
resolved above by accounting for it explicitly rather than ignoring it, and is
called out in both the I7 commit message and this report.
