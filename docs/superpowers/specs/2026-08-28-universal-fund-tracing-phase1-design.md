# Universal Fund Tracing — Phase 1: Line of Sight

Rebuild what the system can *see* of the target's money, so the fund-flow vector
stops being "one asset, one chain, through a spam-filled window" and becomes
"every asset, every EVM chain, complete history, exchanges correctly named".

Goal: surface **leads**, never assert ownership. This spec adds sight; it does
not loosen a single grading rule.

---

## 1. Limitations proven in the current code

Each measured against `main` at `0fca737f0`, not asserted.

| # | Limitation | Evidence |
|---|---|---|
| L1 | **One chain only** | `utils.etherscan_get` hardcodes `chainid: config["arbitrum_chain_id"]` (`src/utils.py:60`). No caller can reach another chain. |
| L2 | **One asset only** | Five call sites pin `contractaddress` to `usdc_contract_arbitrum` (`tracer.py:54`, `linkage.py:94,115`, `correlator.py:180`, `scanner.py:997`). All 811 Arbitrum edges in `data/transfer_graph/latest.json` are USDC. |
| L3 | **1,000-row ceiling, no pagination** | `page: 1, offset: 1000, sort: desc` (`tracer.py:57-59`). `data/l1_transactions/` holds exactly **1000** records in **one** file. Nothing walks block ranges. |
| L4 | **Spam has evicted the history** | 905 of those 1000 records are sub-$1. One lookalike address, `0x1419b0d7…2d5f`, accounts for **510 records** — over half the stored history — mimicking the known self-wallet `0x1419e753…2d5f`. 11 of 14 dust-only addresses are 4+4-char lookalikes. The target has **5 real counterparties**. |
| L5 | **Internal transactions invisible** | `txlistinternal` appears **0 times** in `src/`. Contract-mediated transfers — which is what bridges and routers emit — are in neither `txlist` nor `tokentx`. |
| L6 | **No contract detection** | `eth_getCode` appears **0 times** in `src/`. A contract address can currently be graded as a personal wallet. |
| L7 | **Three service labels** | `data/transfer_graph/latest.json` `services` = HL bridge, USDC, zero address. `config.known_service_addresses` has 2 entries. No CEX, no bridge, no router is known by name. |

### The live consequence

`0xa95d9c1f655341597c94393fddc30cf3c08e4fce` received **$13,000,000** from the
target across 14 transfers in June 2026 and returned none of it
(`totals.sent_to_target_usd: 0`). Current verdict: `OPERATIONAL_COUNTERPARTY`,
confidence **0.30**, with no knowledge of where the money went next. That is the
most important fund trail in the dataset, and it is cold — not because the graph
reasoned poorly, but because L1–L7 mean the onward hops were never fetched.

---

## 2. Scope

The full ask decomposes into three sub-projects. **This spec covers Phase 1 only.**

| Phase | Delivers |
|---|---|
| **1 — Line of sight** *(this spec)* | Multi-chain, multi-asset, fully paginated collection; spam quarantine; entity labels; wired into the existing graph. |
| 2 — The chase | Value-accounting ledger (accounted vs unexplained dollars), bridge-crossing handoff, generalised CEX-gap re-link with amount-rarity scoring, reverse tracing of inbound funders, HyperEVM reader, tracer/frontier consolidation. |
| 3 — The empire | Persistent roster with CONFIRMED/PROBABLE/POSSIBLE tiers and manual pins, cross-chain balances and total AUM, dashboard page. |

Each phase gets its own spec → plan → implementation cycle.

---

## 3. Architecture

New bounded ingestion layer under `src/chain/`. The existing scoring and
classification brain is preserved exactly; only its **input** improves. This
follows the established pure-core / IO-wrapper split and keeps
`src/transfer_graph.py` (already 1,793 lines) from growing.

```
             ┌──────────── src/chain/ (new, bounded) ────────────┐
             │                                                    │
  Etherscan  │  client.py    paginated multi-chain reader, budget │
  V2 (1 key) │  assets.py    token registry + USD valuation       │
      ──────►│  spam.py      poisoning / dust classifier   (pure) │
             │  labels.py    entity registry + code check         │
             │  collect.py   sweep_wallet() → normalised records  │
             └──────────────────────┬─────────────────────────────┘
                                    │  normalised transfer records
                                    ▼
                     data/transfers/{chain}/YYYY-MM-DD.json
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
      transfer_graph.py         tracer.py              linkage.py
      (unchanged brain)     (unchanged outputs)   (now all-chain, 0 new calls)
            │
            ▼
      data/transfer_graph/latest.json → /transfers dashboard
```

Module responsibilities, each independently testable:

| Module | Does | Depends on |
|---|---|---|
| `chain/client.py` | Block-range pagination, rate limit, call budget, cursors | `utils.etherscan_get` |
| `chain/assets.py` | Token registry, USD valuation, price cache | price source, disk |
| `chain/spam.py` | `classify_spam()` — **pure**, no IO | — |
| `chain/labels.py` | `classify_address()` — curated → code → fan-degree → inferred | `client`, disk |
| `chain/collect.py` | `sweep_wallet()` orchestration and persistence | all of the above |

---

## 4. Data model

One normalised record, written by every source on every chain:

```jsonc
{
  "id": "arbitrum:0x8f3a…:erc20:12",   // chain:tx_hash:kind:index — globally unique
  "chain": "arbitrum", "chain_id": 42161,
  "block": 473995514,
  "ts": 1781234567, "timestamp": "2026-06-16T14:22:11Z",
  "tx_hash": "0x8f3a…",
  "src": "0x45d2…", "dst": "0xa95d…",
  "kind": "erc20",                      // erc20 | native | internal
  "asset": "USDC", "token_address": "0xaf88…",
  "amount": 5000000.0,
  "amount_usd": 5000000.0,
  "value_basis": "stable_par",          // stable_par | daily_close | unpriced
  "spam": false, "spam_reason": null
}
```

`kind` distinguishes the three Etherscan actions: `tokentx` → `erc20`,
`txlist` → `native`, `txlistinternal` → `internal`. All three are collected;
today only the first is, filtered to a single token (L2, L5).

### Storage

| Path | Contents |
|---|---|
| `data/transfers/{chain}/YYYY-MM-DD.json` | normalised records, appended via `append_records(key_field="id")` |
| `data/transfers/latest.json` | sweep health per chain/wallet: records, calls used, cursor, gaps, spam suppressed, degraded sources |
| `data/transfers_spam/latest.json` | rollup only: `{address, mimics, count, first_seen, last_seen}` |
| `data/labels/entities.json` | curated entity registry (in git) |
| `data/labels/code_cache.json` | `address → has_code`, permanent |
| `data/prices/{symbol}.json` | `date → close`, permanent |
| `data/state/transfer_cursors.json` | `{chain}:{address}:{kind} → last_block`, atomic |

Cursors move from the single global `last_l1_block` to one atomic keyed file, so
adding a chain or a wallet starts clean without resetting anything else. Spam is
rolled up rather than stored per-record: 1,842 junk rows must not live in git
forever to prove a count.

---

## 5. Collection

### Pagination that cannot truncate

Etherscan caps any single query at 10,000 results, so `page=N` paging hits a
wall. Block-range walking is the correct pattern:

```
start = cursor
loop:
  rows = fetch(startblock=start, sort=asc, offset=PAGE)
  yield rows
  if len(rows) < PAGE: stop
  next_start = max(r.block for r in rows)
  if next_start == start:        # one block holds > PAGE records
      next_start = start + 1     # advance, record possible_gap
  start = next_start
```

`PAGE` is 1,000 — Etherscan's per-request maximum is 10,000, but a smaller page
keeps each call inside the request timeout and bounds the memory a single sweep
holds. `sort=asc` plus block advancement, with `id`-dedup absorbing boundary
overlap.
Implemented as a **pure generator over an injected fetch callable**, so tests
drive every branch — including the same-block stall, which otherwise either
loops forever or silently drops rows — with no network.

### Budget

A `CallBudget` (max calls, wall-clock deadline, per-chain accounting) is threaded
through every client call, matching the `transfer_graph` budget discipline. Free
Etherscan allows 5 req/s and 100k/day; a 10-minute job affords roughly 3,000
calls. **A partial sweep always persists what it collected and records where it
stopped** — never discard-on-timeout.

### Chain policy

Sweeping every chain × every record type × every frontier wallet is wasteful.

- **Cluster wallets** (target + `known_self_wallets` + confirmed roster) — all
  enabled chains, always, all three record types.
- **Frontier wallets** — one probe call first (`txlist`, `offset=1`) establishes
  whether the address has ever transacted on that chain; only non-empty chains
  are swept.

Chains are configured as a list with `chain_id`, native symbol, enabled flag and
priority. Phase 1 enables Arbitrum (42161), Ethereum (1), Base (8453),
Optimism (10), Polygon (137), BSC (56). All reachable on the existing single
Etherscan V2 key by varying `chainid`.

**Known gap, documented not papered over:** HyperEVM (chain 999) is not on
Etherscan V2 and needs its own reader. Deferred to Phase 2.

---

## 6. Spam quarantine

`src/chain/spam.py` exposes one pure function,
`classify_spam(record, volume) -> str | None`:

| Reason | Rule |
|---|---|
| `lookalike` | address shares its **first 4 and last 4** hex characters with a counterparty that has moved strictly more value |
| `zero_value` | zero-amount transfer — moves no money, exists only to appear in history |
| `dust` | `amount_usd < dust_usd` (existing config, 1.0) |
| `unpriced_token` | ERC-20 outside the valued-asset registry. **Never** a known major whose price merely failed — that carries `value_basis: price_unavailable` and is retained. |

`volume` is derived in a first pass, before classification: total priced USD
moved with the wallet, per counterparty address. Valuation therefore runs before
spam classification, not after — the order matters, because the lookalike rule is
defined against value.

### Hard rules

- **A forgery is the poorer side.** An address is a forgery of another only when
  the other has moved *strictly more* value with the wallet, and only when that
  anchor itself clears `dust_usd`. This is not a refinement — a symmetric
  membership test is actively dangerous, because the 4+4 match is symmetric while
  the attack is not. Under a membership test a $1 clone of the $13M counterparty
  makes the **genuine** address match the clone, quarantining a real relationship
  out of the graph for a dollar; patching that with a blanket "real addresses are
  exempt" rule instead whitewashes the clone. Only magnitude separates them, and
  it does so in both directions at once: nobody forges an address poorer than
  their own.
- The **lookalike check runs before dust filtering**. Which address is being
  mimicked is itself intelligence: attackers mimic addresses that received large
  sums, so the mimic list points at the wallets that matter.
- Quarantined records never become graph edges and never consume expansion
  budget.
- A record is quarantined, never deleted; the rollup retains address, mimic
  target, count and first/last seen.
- `unpriced_token` rollup entries additionally retain `token_address` and
  `asset`, so a *legitimate* token the registry does not yet know is visible and
  can be added to `assets.py` rather than silently discarded forever.

Validated against live data before being specified: 11 of 14 dust-only addresses
match the 4+4 rule, including all 510 records of the single largest campaign.

---

## 7. Entity labels

`src/chain/labels.py`, `classify_address()` resolving in priority order:

1. **Curated registry** — `data/labels/entities.json`, versioned in the repo:
   CEX hot wallets and sweep addresses (Binance, OKX, Bybit, Coinbase, Kraken,
   Bitget, Gate, MEXC), bridges (Stargate, Across, deBridge, Relay, Hop, Synapse,
   Circle CCTP, Orbiter), DEX routers and aggregators, HL infrastructure. Each
   entry carries `{address, chain, entity, category, source, added}`.
2. **Contract code** — `module=proxy&action=eth_getCode`, one call, cached
   permanently. **An address with bytecode is not a person and can never be
   graded `MIGRATION_CANDIDATE`.**
3. **Fan-degree** — the existing `detect_services` heuristic, retained as the
   fallback for unlabelled hubs.
4. **Inferred CEX deposit address** — an address that receives from a cluster
   wallet and forwards **≥ 95% of the received value** to a known CEX hot wallet,
   where everything sent elsewhere **sums** to ≤ 5% of what it received, and the
   hot-wallet transfer **carrying the largest amount** lands **within 24 hours**
   of the first receipt. Never publicly labelled, and the highest-value identity
   artifact on chain: a deposit address belongs to exactly one exchange account.
   Phase 1 labels it; Phase 2 re-links on it.

   Both qualifiers are load-bearing and were added after each was shown to admit
   a false positive. Capping the *largest* other destination rather than their
   sum lets the same value fan across ten addresses at 4.9% each, so a wallet
   with half its activity elsewhere still qualifies. Anchoring the window on the
   *earliest* hot-wallet send lets a trivial test-send — ordinary behaviour
   before committing a large transfer — satisfy "quickly" on behalf of a bulk
   forward days later.

   The rule fails toward traversing, on purpose. A false negative wastes some
   expansion budget on an address we could have skipped. A false positive marks
   a real wallet a service, and services are never traversed — so the fund trail
   stops dead at the one address we most needed to follow, silently.

### Hard rules

- Labelling **strengthens** the existing invariant, never bends it: services
  still score 0.0, still never alert, still are never traversed through.
- **"Service" is purpose-relative, and the category set must be passed
  explicitly.** The graph asks "may I walk into this address?" — and for a CEX
  deposit address the answer is no. Linkage asks "does shared use of this
  address imply common ownership?" — and for the same address the answer is the
  strongest yes available, since it belongs to exactly one exchange account.
  One category set cannot answer both, so `service_addresses()` takes the
  categories it should apply. Linkage passes `SERVICE_CATEGORIES` minus the two
  deposit categories, derived by subtraction so a new infrastructure category
  cannot silently escape exclusion.
- A curated label always beats an inferred one; an inferred label always beats
  fan-degree.
- Category `cex_deposit` is recorded as **evidence about the sender**, not a
  claim about the address's owner.

---

## 8. Valuation

`src/chain/assets.py`:

| Class | Basis |
|---|---|
| Stablecoins (USDC, USDC.e, USDT, DAI, USDe) | `$1` par → `stable_par` |
| Majors (ETH/WETH, WBTC, ARB) | daily close, cached per `(symbol, date)` → `daily_close` |
| Majors whose price could not be fetched | `amount_usd: null` → `price_unavailable` |
| Anything else | `amount_usd: null` → `unpriced` |

**`price_unavailable` and `unpriced` are different facts and must not share a
basis.** One says "we know this asset is valuable and could not price it today";
the other says "we have never heard of this token". Collapsing them lets the spam
classifier quarantine a real ETH transfer on the strength of a price outage — and
quarantined records never reach the substrate at all, so once the cursor has
advanced past them the loss is permanent and invisible. A `price_unavailable`
record is retained, counted in the sweep's `unpriced` health field, and can be
re-priced later from what was stored.

### Hard rules

- **A missing price yields `null`, never `0.0`.** If a price source outage books
  a $2M ETH transfer as zero, it silently drops below every threshold in the
  system and a migration walks past unnoticed. This rule is test-covered.
- An `unpriced` record is **quarantined** by `spam.py` under `unpriced_token`,
  so it can never become an edge, be traversed, or satisfy a value threshold — a
  worthless airdrop token cannot manufacture a migration signal. The rollup keeps
  its token address so a genuine unregistered token surfaces for registration.
- Each `(symbol, date)` is fetched once ever and cached in git.

---

## 9. Integration

| Existing code | Change |
|---|---|
| `utils.etherscan_get` | optional `chain_id=` defaulting to the configured Arbitrum id — one HTTP path, zero call-site churn |
| `transfer_graph.collect_known_edges` | new reader for `data/transfers/**`; **keeps** reading `data/l1_transactions` via `normalise_l1_transfer` |
| `transfer_graph.expand_frontier` | `get_usdc_transfers(dest)` → `sweep_wallet(dest, chains, budget)` |
| `transfer_graph.run_transfer_graph` | seeds `known_services` from the label registry ∪ config |
| `linkage.get_outbound_usdc_addresses` | becomes all-chain, all-asset, reading stored records — **zero extra API calls** |
| `tracer.py` | reads the new substrate; `fund_flows` output and `alert_fund_movement` behaviour unchanged |

`data/l1_transactions/` stays readable because it is the only copy of some
history. `edge_id()` and `build_graph()` already carry a `chain` dimension, so
multi-chain edges need no schema change to the graph itself.

**Deliberate duplication, retained.** `tracer.py`'s hand-rolled hop-1/2/3 loops
are strictly worse than `expand_frontier` — no budget, no resume, no dedupe — so
the two overlap. But the tracer also owns the `fund_flows` findings and the
combined-alert path the dashboard depends on. Phase 1 keeps both; Phase 2
consolidates. Deleting a working alert path in the same change that rebuilds
collection is how a system goes blind without noticing.

### Backfill

`backfill.yml` (60-minute timeout) gains a mode that re-sweeps the target and
`known_self_wallets` from block 0 across all enabled chains with full
pagination. This run recovers the history evicted by L3/L4 and is what actually
answers where the $13M went.

---

## 10. Failure modes

| Failure | Behaviour |
|---|---|
| One chain errors | recorded in `data/transfers/latest.json` as a `degraded_sources` entry, mirroring `expansion.degraded_sources` |
| `ETHERSCAN_API_KEY` absent | whole sweep marked `skipped_no_api_key` (existing pattern) |
| Price source down | records land `unpriced`; **never** `0.0` |
| Budget exhausted | partial results persisted, stop position recorded |
| Single block exceeds page size | `start + 1` advance plus a `possible_gap` diagnostic |

A chain that could not be read is **blindness**, and blindness must be visible on
the dashboard rather than inferred from an empty result. An empty sweep and a
failed sweep must never serialise identically.

---

## 11. Testing

Pure logic unit-tested, network faked, `tmp_path` data dir per `tests/conftest.py`.

| Area | Cases |
|---|---|
| Pagination | full page advances · short page stops · same-block stall advances by 1 and flags `possible_gap` · boundary duplicates collapse by `id` |
| Spam | **the live 11-of-14 poisoning case as a fixture** · zero-value · dust · unpriced · lookalike evaluated before dust |
| Valuation | stable par · cached daily close · unpriced yields `null`, asserted **not** `0.0` |
| Labels | curated beats inferred beats fan-degree · bytecode ⇒ never personal · deposit-sweep inference |
| Regression | legacy `l1_transactions` still produce identical edges |
| Budget | hard stop preserves partial results and records stop position |
| Degradation | a failing chain marks `degraded_sources` and cannot serialise as a healthy empty sweep |

---

## 12. Safety and scope

**In scope:** collection breadth, completeness, spam suppression, entity naming,
valuation, and the wiring that delivers them to the existing graph.

**Not in scope for Phase 1:** value accounting, bridge handoff, CEX-gap
re-linking, reverse tracing, the roster, balances, and any dashboard page — all
Phase 2 and 3.

**Grading rules are untouched.** No classification threshold, no confidence
weight, and no alert gate changes in this phase. A transfer is still not
ownership; the top two tiers still require independent corroboration; services
still score 0.0 and are never traversed. Phase 1 changes only what the system can
see, not what it is willing to conclude — so any new lead it produces has cleared
exactly the same bar every existing lead cleared.
