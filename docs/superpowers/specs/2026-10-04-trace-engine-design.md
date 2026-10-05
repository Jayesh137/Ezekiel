# Trace engine — follow his money to Hyperliquid

**Date:** 2026-10-04 · **Status (2026-10-05):** phase 1 LIVE (PR #59, bounded in #61 —
see the incident log); phase 2's transaction legs measured and not built, L1
refresh built (#60); phase 3's netted accounting built (`routed_to_self`), the
HyperCore exchange-hub correlator pool deferred (his only HyperCore exchange
deposit is $24K). Operator chose approach B and delegated the detailed design · **Scope:** fund tracing only. Behavioural
vectors, the scanner and the dashboard restyle are out of scope.

## 1. Why

Measured 2026-10-03/04 (`docs/incident-log.md`, entry of 2026-10-04):

- The L1 frontier expanded **no wallet for 15 of 17 days** (hot-fixed the same day).
- Even when alive it never got past **depth 2**; of the three cluster wallets'
  992 direct counterparties ≥ $1K, **936 were never swept** (109 contracts,
  $1.9B; 10 quiet EOAs, $48.6M).
- The HL ledgers of the treasury and `0xf078969e…` had **never been walked**.
- Tracing only sees transfers where the swept wallet is sender or receiver.
- One hour of hand-tracing with keyless APIs found four things the pipeline never
  surfaced: a second private Binance deposit address (`0x841b9e4f…`), a deposit
  address **inside Hyperliquid** (`0x4aecac3b…` → hub `0x1f6093d3…`), a
  Binance → fresh wallet → cluster hop (`0x68797748…`), and a quiet wallet
  co-funded by `0xf078969e…` and `0x793a3e8a…` (`0x734c9213…`).

The finds were reachable; the walker was asking the wrong questions in the wrong
order. This design fixes the questions.

## 2. First principles

A Hyperliquid account H receives value only through enumerable entry points:
a Bridge2 deposit (depositor = H), a Circle deposit naming H, an HL-native
transfer from another account (send / spotTransfer / internal / sub-account /
vault), a HyperEVM→HyperCore move, or an exchange withdrawing straight to H on
HyperCore. So:

1. **H's own ledger names its funders.** Reverse-tracing an HL account is one
   free call; forward-tracing must end at an HL ledger.
2. **Every reached address must be asked of Hyperliquid** (CLAUDE.md "close the
   loop"). The deliverable is an HL account; everything else is a route to one.
3. **Money stops at custody boundaries** (exchanges, protocols, bridges). A walker
   that sweeps a boundary learns nothing; each boundary needs its own gap-crosser.
4. **Where his money went matters, not who is busy.** Priority is the share of
   *his* dollars that reached an address, not total value received from anyone.
5. **Two of his wallets paying the same quiet address link to each other** — the
   general form of the shared-deposit-address vector, on both L1 and HyperCore.

## 3. Architecture

New package `src/trace/`, run by `scripts/run_trace_engine.py` as its own step in
`trace.yml`, after the tracer/correlator and before the graph rebuild.

| Module | Responsibility | Purity |
|---|---|---|
| `trace/registry.py` | Per-address records; load/save sharded JSON | IO at edges |
| `trace/units.py` | Unit = (address, source); cost model; due/next-check | pure |
| `trace/classify.py` | Address class from labels, bytecode, whole-chain activity, flow shape | pure |
| `trace/hl_ledger.py` | Read an HL ledger (paged, strict), normalise transfers to edges | IO + pure normaliser |
| `trace/patterns.py` | Deposit-address detection (L1 + HyperCore), hub detection, shared-payee links | pure |
| `trace/value.py` | Haircut "his-money" propagation over edges | pure |
| `trace/scheduler.py` | Order units by priority; run under per-resource budgets | IO via injected readers |
| `trace/report.py` | Run report, findings, boundaries | pure |

### 3.1 Units, never wallet lookups

Sources per address: `classify`, `hl` (role + ledger), `l1` (existing
`chain.collect.sweep_wallet`, per-chain cursors already persisted). Each unit has
its own `next_check`, `last_read`, `last_error`. **A failing source never blocks
another; a partial read is progress** (the 2026-10-04 rule, made structural).
Phase 2 adds `tx_legs`.

### 3.2 Budgets

Per resource, all derived from unit plans and checked before each unit:
HL weight via `hl_budget.ReadBudget` (600 weight/min), Etherscan calls via
`chain.budget.CallBudget`, Blockscout calls, wall clock. Order invariant:
unit slice < engine budget (240 s) < step `timeout-minutes` (8).

### 3.3 State

`data/trace/registry/<00..ff>.json` — sharded by the address's first byte so a
run rewrites only shards it touched (the git pack is already 1.7 GiB).
`data/trace/hl_edges/<00..ff>.json` — normalised HL-native transfers for walked
accounts (hubs keep a summary, never their full ledger).
`data/trace/latest.json` — the run report (single writer: this step).

## 4. What it detects

### 4.1 Classification (before any sweep)

`cluster` (config only — ground truth), `service` (curated label, or whole-chain
txs ≥ 10,000, or contract), `cex_deposit` (L1: forwards ≥ 90% of inflow to one
hub within 48 h — reuses `inferred_deposits` semantics), `hl_deposit` (HyperCore:
each inbound forwarded in full, same token and amount, within 10 min, to one hub),
`hub` (HL fan-in or fan-out ≥ 25, or a saturated 2,000-row ledger), `quiet_eoa`
(measured, < 2,000 txs), `unknown` (not yet measured — never treated as quiet,
rule 9). Services and hubs are **boundaries**: recorded, never walked.

### 4.2 His-money propagation (priority)

Seeds: config cluster, share 1. For every other address,
`share = min(1, Σ inbound × share(sender) / Σ inbound)` computed in depth order
from the seeds (back-edges ignored, so round trips cannot inflate it).
Boundaries pass on 0. An unswept address uses its known inbound only.
`priority = log-scaled his-money-in × class weight × recency × HL bonus`.
Unvalued edges (ETH outside the price window) count as a floor, never as $0
(rule 6).

### 4.3 HL closure

Every address reaching `quiet_eoa`/`unknown` with his-money > 0 gets a
`userRole` read; a `user` gets its ledger walked (paged with `startTime`,
read through a strict reader — `hl_post`'s `[]` on failure must never look like
an empty ledger, CLAUDE.md "utils.hl_post never raises").

### 4.4 Links (new roster vectors)

- **`shared_payee`** — a quiet payee P received from a cluster wallet and from
  outsider Y within 7 days; Y is linked. Excludes deposit addresses (already
  `linkage`), services and anything unmeasured.
- **`hl_deposit_address`** — an `hl_deposit` the cluster paid; any other sender is
  the same exchange account. Feeds `deposit_sentinels` (HyperCore side), so a new
  sender alerts like an L1 sentinel sender (CRITICAL if measured quiet).
- **`reach`** — a path of quiet hops from the cluster to an HL account with HL
  activity. Evidence only; `transfer` keeps its existing evidence bar.

### 4.5 Alerts

- HIGH: an HL account newly reached by his money through quiet hops only.
- CRITICAL: via the sentinel path (new sender into a deposit address of his,
  either side) — reuses `deposit_sentinels` severity rules.
- Nothing else buzzes; the report records it (`ESCALATING_SEVERITIES` only).

## 5. Phases

1. **Engine core + HL-native (this plan).** Registry, units, classify, HL ledger
   walk, patterns, his-money priority, report, roster + sentinel wiring. Runs
   *beside* the repaired frontier: L1 units call the same `sweep_wallet`, seeded
   from the engine's priority, under their own small budget.
2. **~~See inside transactions~~ — measured and not built (2026-10-04):** 117 of
   his contract-facing transactions read in full surfaced only solvers, market
   makers and relayers (CLAUDE.md, measured-and-rejected). Built instead: L1
   refresh of quiet wallets holding his money (re-swept every 3 days). The old
   frontier stays — after the hotfix its queue is empty and it costs ~0, while
   its refresh schedule still covers 2,226 explored wallets. Original item:
   **See inside transactions; retire `expand_frontier`.** `tx_legs` (all transfers,
   internal transfers and signer of each cluster / high-share transaction),
   initiator links, receipt-token following; frontier tests ported as scheduler
   properties (retention by priority, revisits, services never walked).
3. **Cross the exchange gap.** HyperCore exchange hubs become a correlator pool
   (deferred: one $24K deposit to match against); netted accounting (built:
   outflows movements.py proves landed on him count as `routed_to_self`, not
   infrastructure); the engine's report already lists boundaries — where his
   money stopped and why.

## 6. Acceptance (phase 1)

Known answers, from config alone, on recorded fixtures of the real transfers:

- `0x841b9e4f…` classified `cex_deposit` of the cluster.
- `0x4aecac3b…` classified `hl_deposit` with hub `0x1f6093d3…`; the hub a boundary.
- `0x793a3e8a…` linked `shared_payee` through `0x734c9213…`.
- `0x68797748…` reached as a quiet EOA funded from a Binance hot wallet.

Plus: a source failing for one address leaves its other sources and every other
address progressing; a failed HL read is recorded as unreadable, never empty;
the engine never writes real `data/` in tests; a live read-only dry run on
production state reports units run, budgets used and what it found.

## 7. Risks

- **False links from shared payees** (OTC desks, payroll): one vector never
  promotes (roster's ≥ 2 rule); payee must be measured quiet.
- **HL rate limits** shared with other trace steps: the engine owns its own
  `ReadBudget` window and stops cleanly on 429.
- **Repo growth:** sharded state, hubs summarised, edges only for walked
  non-hub accounts.
- **Unbounded state (happened, 2026-10-05):** the registry reached 63,660
  addresses and pushed the job past its ceiling. Bounds now in code: keep only
  >= $1K of his money, quiet only when measured on every chain, histories only
  for value holders, > 5,000 stored records is busy, one substrate pass per run.
  Dry-run any engine change on production state before merging.
