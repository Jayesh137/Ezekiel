# Boundary tracing — watch Hyperliquid's edges, search from both sides

**Date:** 2026-10-06 · **Status:** implemented on `feat/boundary-trace` (plan
`docs/superpowers/plans/2026-10-06-boundary-trace.md`); the production dry run
and every fix it forced are in `docs/incident-log.md` (2026-10-06). Approved
design: the operator chose approach A and asked for it to be carried through to the end. · **Scope:** fund tracing — the
outbound attribution, inbound provenance and custody-gap correlation of value
crossing into and out of Hyperliquid accounts, plus a dashboard page that shows
it. The behavioural scanner, the execution-program detector and the scheduling
infrastructure are out of scope.

Supersedes nothing: the 2026-10-04 trace engine (`src/trace/`) stays and becomes
one of the inputs that builds the perimeter (§4).

## 1. Why — measured 2026-10-06, read-only

| Measurement | Result |
|---|---|
| Trace engine's last production run | 0 HL accounts reached, 0 links, 1 deposit address, 6 funders; whole 180 s budget spent |
| All 412 L1 counterparties (≥ $50K or ≥ 2 unpriced) of the three config wallets, asked of HL (perp, spot, portfolio; ledger + `userRole` when present) | 0 read errors; 16 exist on HL; **0 trade** — incl. `0x160f6ef9…` ($180M two-way) |
| How the 72 newborn ≥ $1M HL accounts were funded (dominant route) | Circle 35 · HL account→account send 16 · Unit 9 · HyperEVM 6 · Bridge2 6 |
| Correlator candidate pools | Circle + Bridge2 only; the Bridge2 pool is incomplete ("hit the page ceiling") |
| Correlator exits | $3.39B "unresolved"; most are DeFi positions he still holds, Socket/CCTP to himself, and $370M of the target's own withdrawals whose ledger row has no destination |
| Bridge2 on Arbitrum | `FinalizedWithdrawal(user indexed, destination, usd, nonce, message)` names the withdrawing HL account; ~3,200/day, 25% with user ≠ destination; deposits (USDC `Transfer` to the bridge) ~1,200/day; one keyless Blockscout `getLogs` ≈ 6 h of either |
| Unit (`api.hyperunit.xyz/operations/<address>`) | keyless; answers for an HL account AND for an external BTC/SOL/EVM address, both directions; his three wallets, his Solana wallet and `0x8570c2ae…` have 0 operations |
| Bridge payouts into target (146) and treasury (4) | all their own withdrawals |
| Dashboard | shows only the stale transfer graph (frontier empty; config wallets labelled "Migration candidate"); `data/trace/latest.json` is shown nowhere |

**Conclusion.** Walking his money forward one hop and asking Hyperliquid is
exhausted: no counterparty trades there. Any other account of his sits behind a
custody gap (an exchange, a bridge, Unit), two or more hops away, or has no flow
link at all. Walking further forward multiplies cost (depth 2 is ~50x depth 1,
mostly exchanges and protocols) for a measured yield near zero.

## 2. First principles

1. **Hyperliquid has a finite edge.** Value enters an account only through
   Bridge2, Circle, Unit, HyperEVM or a send from another account, and leaves
   through Bridge2, Circle, Unit, HyperEVM or a send. Four of the five have a
   global, keyless, permanent record outside HyperCore.
2. **Index the edge, not the graph.** Watching his wallets forward costs a sweep
   per wallet and grows with every wallet added. Joining edge events against a
   table of his addresses costs one lookup per event, so the table can be large.
3. **Search from both sides.** Outbound: who sent value *to* his world
   (attribution). Inbound: where every large new account's money came from,
   back to its first custody boundary (provenance). The two meet on addresses.
4. **A custody gap is crossed only by its own physics.** Money that enters an
   exchange can only re-emerge as that exchange's withdrawal; money in a DeFi
   position he holds has not left him. Correlation must respect the route.
5. **Doctrine unchanged.** Ground truth is config only; a transfer is not
   ownership; unmeasured is never quiet (rule 9); a failed read is never empty
   (rule 5); a missing value is never 0 (rule 6); money-flow votes stay one
   "financial" family, so nothing here promotes a wallet to PROBABLE alone.

## 3. Architecture

```
  build_perimeter.py (trace.yml)        check_boundary.py (watch.yml)
  ─────────────────────────────         ─────────────────────────────
  config, sentinels, trace engine,      Bridge2 withdrawals (cursor)  ─┐
  Solana labels, roster, substrate      core withdrawals by topic1     │ attribution
        │                               Unit ops per member (rotating) │ (§6)
        ▼                               retro: payouts + mints into    │
  data/perimeter/latest.json ───────► members (bounded, resumable)  ─┘
        │                                      │
        │                               data/boundary/latest.json
        ▼
  run_provenance.py (trace.yml)
  ───────────────────────────────
  Bridge2 deposits (cursor) → pool ──► correlator (gap exits, route filter, §8)
  queue: large entries, newborn,
  roster leads, dormancy, matches
  → resolve funding ≤ 2 hops (§7)
        │
  data/provenance/latest.json, accounts/<xx>.json
        │
  roster.py (votes) · alerts · dashboard /trace (§9)
```

New package `src/boundary/` (pure functions; all IO through injected callables):

| Module | Responsibility |
|---|---|
| `perimeter.py` | Build the perimeter from detector outputs; exchange families |
| `bridge2.py` | Decode `FinalizedWithdrawal` and bridge-deposit logs; cursor walk that never skips |
| `logs.py` | `getLogs` readers: Blockscout keyless, Etherscan V2 fallback; strict (raise, never empty) |
| `unit.py` | Unit operations reader (strict) and normaliser |
| `attribution.py` | Join edge events against the perimeter → findings, severities, votes |
| `provenance.py` | Classify an account's inbound entries by route and resolve sources ≤ 2 hops |
| `gaps.py` | Custody-gap exits and the route-consistency filter for the correlator |

Scripts (each the single writer of its files):

| Script | Workflow | Writes |
|---|---|---|
| `scripts/build_perimeter.py` | trace.yml, after the trace engine | `data/perimeter/latest.json` |
| `scripts/check_boundary.py` | watch.yml | `data/boundary/latest.json` |
| `scripts/run_provenance.py` | trace.yml, before the correlator | `data/provenance/latest.json`, `data/provenance/accounts/<xx>.json`, `data/provenance/bridge_deposits.json` |

watch.yml reads `data/perimeter/latest.json` read-only; a perimeter up to one trace run
stale is acceptable for a tripwire.

## 4. The perimeter (`data/perimeter/latest.json`)

Rebuilt every trace run from what other detectors measured. Each member:
`{address, role, weight, why, sources, chains, first_seen, hl}`. (Roster
CONFIRMED is config-only by design, so there is no separate `confirmed` role.)

| Role | Built from | Weight |
|---|---|---|
| `core` | `target_wallet` + `known_self_wallets` (ground truth) | 1.0 |
| `deposit` | `deposit_sentinels.sentinels`; trace-engine `deposit_addresses` the cluster paid (incl. HyperCore `0x4aecac3b…`) | 1.0 |
| `identity` | `labels/solana_addresses.json` role `cluster` (with its bytes32 form) | 1.0 |
| `sink` | trace registry: class `quiet_eoa` (measured), his-money share ≥ 0.5 and ≥ $100K | 0.6 |
| `funder` | trace report `funders`: depth 1, paid him ≥ $100K, class `quiet_eoa` | 0.6 |
| `associate` | ≥ $1M in BOTH directions with core on L1, EOA (no bytecode), not measured busy, not a labelled service | 0.3 |

Exclusions: config `excluded_addresses` / `known_service_addresses`, the zero
address, HyperCore system addresses (`0x20…`, `0x2222…`), labelled services. A
member keeps the strongest role it qualifies for.

**Exchange families** (not members): for each `deposit` member, the busy EOAs
or contracts it forwards to on L1 ("his exchange's hot wallets"); plus busy
EOAs that paid core ≥ $100K ("exchanges he withdraws from"). Used by
provenance (§7) and the gap filter (§8). No exchange names are invented —
`entities.json` has none.

**Closing the loop.** Up to 25 members per run whose HL reading is older than
24 h get `clearinghouseState`, `spotClearinghouseState` and `portfolio`
(strict reads). A non-core member with account value ≥ $10K or all-time volume
≥ $100K is an **HL account in the perimeter**: HIGH for `sink`/`funder`, recorded for
`associate`, never for `core`. Failed reads are
recorded as unreadable, never as absent.

## 5. Edge feeds

| Feed | Source | Cursor / scope | Used by |
|---|---|---|---|
| Bridge2 withdrawals | Arbitrum `getLogs`, bridge `0x2df1c51e…`, topic0 `0xe5c7fe3a…1978` | block cursor; first run starts 1 day back | attribution |
| Core withdrawals (exact) | same, filtered `topic1 = core account` | every run, whole history (≤ 1,000 rows each) | attribution, gap exits, exact nonce pairing |
| Bridge2 deposits | Arbitrum `getLogs`, USDC `0xaf88d065…`, `Transfer` with `topic2 = bridge` | block cursor; first run 14 days back; pool keeps 30 days ≥ $10K | correlator pool, provenance queue, perimeter-deposit check |
| Circle | existing `cctp_feed` pool (in) and `circle_flows` (both directions) | unchanged | provenance queue, attribution (existing tripwires) |
| Unit | `GET api.hyperunit.xyz/operations/<address>` | per perimeter member (rotating, ≤ 10/run) and per provenance account | attribution, provenance |
| HL sends | core ledgers already walked by the trace engine (`data/trace/hl_edges`) | engine refresh (6 h) | attribution |

Readers (`boundary/logs.py`): Blockscout's Etherscan-compatible
`module=logs&action=getLogs` (keyless, ≤ 1,000 rows per call) is primary;
Etherscan V2 (`chainid=42161`, `page`/`offset`) is the fallback when a key is
set. A call returning 1,000 rows continues from that last row's block and
dedupes by `(transactionHash, logIndex)`. Any error stops the walk with the
cursor at the last fully read block — never advanced past an unread range.
Ranges are chunked at 100,000 blocks (~7 h of Arbitrum).

Storage stays small: no global history is kept. Attribution needs only the live
stream plus per-member retro checks (§6.3); the correlator needs a 30-day pool.

## 6. Attribution — who sent value to his world (outbound side)

### 6.1 Events

Every feed is normalised to
`{source, direction, hl_account, counterparty, counterparty_raw, chain,
amount_usd, ts, ref, event_id}` where `direction` is relative to Hyperliquid:

| Feed | `out` (left HL) | `in` (entered HL) |
|---|---|---|
| Bridge2 | user → destination | depositor → its own account |
| Circle | `messageSender` account → `mintRecipient` | `messageSender` → hook account |
| Unit | HL source → external destination | external source → HL destination |
| HL send | core → other account (as `in` for the other) | other account → core (as `out` for the other) |

### 6.2 Rules (pure, `attribution.classify`)

| Event | Severity | Roster vote |
|---|---|---|
| `out` by a non-core account to a `core`/`identity` member | CRITICAL | `transfer` |
| `out` by a non-core account to a `deposit` member | CRITICAL | `linkage` (address reuse) |
| `out` by a non-core account to a `sink`/`funder` member | HIGH | evidence only |
| `in` to a non-core account from a `core`/`identity` member | CRITICAL | `transfer` |
| `in` to a non-core account from a `sink`/`funder` member | HIGH | evidence only |
| `out` by a core account to a non-perimeter address | CRITICAL (same alert and key as `withdrawals.py`, so no double page) | — |
| A non-core member active on its own HL account (Bridge2 deposit or withdrawal to itself) | HIGH for weight ≥ 0.6 | evidence only |
| Anything involving only an `associate` | recorded | evidence only |

A withdrawal to its own address (user = destination) carries no relationship
and is skipped. Valued amounts under $100 are dust and never alert (poisoning
shape). Unit operations carry native amounts the project does not price; they
are kept unvalued (rule 6) and are not dust (Unit's own minimums are far
above $100), so they alert by role — but never above HIGH, because their size
is unknown.

### 6.3 Retro — has anyone EVER sent value to his world?

Per member, once, then again whenever it joins the perimeter, bounded per run
and resumable (state kept in `latest.json`):

- **Bridge2 payouts into an EVM member:** Blockscout token transfers into the
  member on Arbitrum from the bridge; each payout's transaction logs decoded for
  the `FinalizedWithdrawal` naming that destination → the withdrawing account.
- **Unit:** `/operations/<member>` (both directions).
- **Circle mints into `core`/`deposit`/`identity` members:** USDC mints (from
  the zero address) not already paired by `withdrawals.py`; each mint's
  `MessageReceived` decoded for source domain and sender. Domain 19 (HyperEVM)
  means a Hyperliquid account paid him.

Retro findings are history: they alert once at **HIGH** ("historical"), carry
the same votes as §6.2, and are never re-announced.

### 6.4 Baseline and delivery

The live withdrawal feed's first run starts one day behind the head; whatever
it finds in that first window is treated as retro (historical, HIGH once).
After that every finding is live. Each finding has a key
`kind:hl_account:counterparty:ref`; a finding is alerted once and kept in
`alerted`; an undelivered alert is retried next run and not recorded as seen
(the `deposit_sentinels` rule).

## 7. Provenance — where did this account's money come from (inbound side)

### 7.1 Queue (bounded, priority order)

1. Accounts credited by Bridge2 deposits or Circle deposits ≥ $100K in the
   last run's new events.
2. `newborn.newborn` (≥ $1M, young).
3. Roster POSSIBLE/PROBABLE non-core wallets.
4. Dormancy handoff candidates.
5. Correlator matches whose route is unknown (§8).

A cached record is re-resolved when a new ≥ $100K entry arrives, or after 7
days for queue sources 2–5. Budgets per run: 30 accounts, 150 s, 120
Blockscout calls, 30 Unit calls, HL weight through `hl_budget.ReadBudget`.

### 7.2 Resolution (`provenance.resolve`)

1. **HL entries.** The account's ledger from its cursor (strict reads).
   Value-bearing inbound rows are routed: `deposit` → Bridge2 (L1 source = the
   account on Arbitrum); a send from USDC's forwarder `0x6b9e7731…` → Circle
   (source = `circle_flows` sender when stored, else the extension's Arbitrum
   transfer of the same amount within 30 min, else unresolved); a send that
   matches one of the account's Unit deposit operations (one
   `/operations/<account>` call; same asset, within ±1 h of `opCreatedAt`) →
   Unit (source = the operation's external address and chain); a send from
   `0x20…`/`0x2222…` → HyperEVM (source = the account on chain 999, Etherscan
   only); a send from any other account → HL-native (source = that account).
   First native gas is read only for fresh addresses (≤ 50 transactions): the
   oldest value-bearing incoming transaction from Blockscout v2 (keyless).
2. **Hop 1.** The sources that put value into the entry: for an L1 address,
   its inbound token transfers (Blockscout v2, newest first, ≤ 2 pages) in the
   30 days before the entry, plus who paid its first native gas; for an HL
   source account, its own ledger entries.
3. **Classify** each source: perimeter member (role); service (label, bytecode,
   measured busy); quiet EOA (measured); unmeasured.
4. **Hop 2.** Only for quiet or unmeasured EOAs that supplied ≥ 20% and ≥ $50K
   of the hop-1 address's inflow, one more level, same classification.
5. **Record** `{account, entries by route, sources by hop with class/role/usd/ts,
   exchange_sources, first_boundary, unreadable, resolved_at}`.

### 7.3 Joins and severities

| Path | Severity | Vote |
|---|---|---|
| A `core`/`identity` member at hop 1 | CRITICAL | `transfer` |
| A `sink`/`funder` member at hop 1 | HIGH | evidence |
| Any member of weight ≥ 0.6 at hop 2 | HIGH | evidence (`graph_reach_only`, never a vote) |
| An `associate` anywhere | recorded | evidence |
| An exchange source in one of his families | recorded | evidence (`same_exchange`); used by §8 |
| A quiet EOA source shared by ≥ 2 resolved accounts | recorded | evidence (`shared_funder_group`) |

A source that could not be read is `unreadable`, never "no source".

## 8. The custody-gap correlator

**Exits** (`gaps.gap_exits`) are only money that crossed a custody gap where it
could re-emerge in a new account:

1. **Exchange deposits:** core → `deposit` member payments ≥ `min_amount`.
2. **Person transfers:** core → an EOA outside the perimeter (no bytecode, not
   measured busy, not a labelled service; unmeasured included — reach beats
   tidiness), not resolved by `movements.py`.
3. **HL withdrawals to outside addresses:** core withdrawals (exact, from the
   topic1 feed) and Circle withdrawals whose destination is outside the
   perimeter.

Not exits: transfers into contracts (DeFi positions he holds), decoded bridges
to himself, Bridge2 deposits (they credit his own account), withdrawals paired
by nonce to his own address, anything between perimeter members.

**Entries:** the complete Bridge2 deposit pool (§5, replacing the page-capped
Etherscan reader) and the Circle pool, unchanged otherwise.

**Route filter** (`gaps.route_consistent`): an exchange exit can only re-emerge
as an exchange withdrawal. When a match's deposit account has a provenance
record whose hop 1 is FULLY resolved (every source read and classified), a
record showing no exchange-class source between the exit and the deposit
drops the match; a record showing a source in one of his exchange families
marks it `same_exchange`. A record with any unreadable or unresolved hop-1
source, or no record at all, keeps the match, marks it `route_unknown`, and
queues the account for provenance (§7.1 item 5) — a gap in what we read is
never evidence against a match. Person-transfer and HL-withdrawal exits are not
filtered (their onward route is unconstrained). Scores, tolerances and windows
are unchanged (rule 4 spirit: no tuning to the story).

## 9. Visibility — dashboard `/trace`

A new page reading `perimeter/latest.json`, `boundary/latest.json`,
`provenance/latest.json`, `trace/latest.json` and the feed-health state:

1. **Status** — each feed: last read, cursor lag, blind or healthy.
2. **Findings** — attribution and provenance, newest first, severity badges,
   each address linked to Hypurrscan through `addressUrl()`.
3. **His world** — perimeter members by role with why and HL state.
4. **Where new money came from** — the newest resolved accounts: route, hop-1
   source and its class, verdict (`touches his world` / `same exchange` /
   `unrelated` / `unresolved`).
5. **Where his money stops** — the trace engine's boundaries with dollars.
6. **Measured no's** — computed counts ("N members asked of HL, K trade") and
   the fixed list from §1.

The Transfers page stops calling config wallets "Migration candidate" (label
`Known wallet (config)`).

## 10. Health, alerts, roster

- `feed_health`: "boundary attribution" (watch group, `data/boundary/latest.json`,
  360 min), "his perimeter" (other group, `data/perimeter/latest.json`, 720 min)
  and "funding provenance" (other group, `data/provenance/latest.json`,
  720 min). Blind when a boundary read covered ≥ 20,000 blocks with 0
  withdrawals (impossible on a live bridge), when the perimeter holds no core
  member, or when provenance attempted ≥ 5 accounts and resolved none.
- Alerts: `alert_boundary_finding(row)` and `alert_provenance_hit(row)`,
  subjects `[EZEKIEL] CRITICAL|HIGH: …`, cooldown keys per (kind, account,
  counterparty), 168 h. Only CRITICAL/HIGH route (`ESCALATING_SEVERITIES`).
- Roster: reads `data/boundary/latest.json` `findings` and
  `data/provenance/latest.json` `findings`; votes per §6.2/§7.3 with evidence
  under `boundary` / `provenance`.

## 11. Small fixes carried with this work

- `route_index.discover` stops making the zero address, HyperCore system
  addresses and Polygon's `0x…1010` "funding route" candidates
  (`data/candidates/0x000…0000.json` holds hundreds of them).
- `movements.py` hl_withdraw rows resolve by exact nonce when the core
  withdrawal feed is present (the $370M of "unresolved" target withdrawals).

## 12. Acceptance

Known answers, network-free, from recorded fixtures of real data:

- The treasury payout tx `0xc0758212…` decodes to user `0x1419e753…`,
  destination `0x1419e753…`, $1,999,999.00, nonce `1742999426126000`.
- A FinalizedWithdrawal to a `deposit` member from a non-core user → CRITICAL,
  `linkage`; to `core` → CRITICAL, `transfer`; user = destination → nothing.
- A walk that errors mid-range leaves the cursor at the last fully read block;
  a 1,000-row page continues from its last block without duplicates.
- Unit fixtures: a Bitcoin deposit into an account → route `unit`, source
  `bc1p…` (chain bitcoin, unmeasured, no hop 2).
- Provenance of an account funded by a core wallet's L1 transfer → CRITICAL;
  of one funded only by a measured exchange hot wallet in his Binance family →
  `same_exchange` evidence, no alert.
- Gap exits on the production substrate exclude DeFi contracts, Socket-to-self
  and nonce-paired withdrawals; include core → `0x8570c2ae…` payments.
- Perimeter built from production files contains the 3 core wallets, the 4
  sentinels, `0x4aecac3b…`, the Solana wallet, and no labelled service.
- Every new step passes `tests/test_workflow_step_independence.py`; no test
  writes real `data/`; `ruff` clean.
- Read-only dry runs of all three scripts on production state complete inside
  their budgets and report what they found (recorded in the incident log).

## 13. Risks

- **False attribution through shared custodians.** A withdrawal to an exchange's
  shared address is not reuse — members are only addresses measured quiet
  (deposit sentinels) or his own; services are excluded from the perimeter.
- **Alert noise.** Associates never alert; HIGH needs weight ≥ 0.6; history is
  announced once; dust never alerts.
- **Rate limits.** Blockscout calls paced (0.35 s) and budgeted per run; Unit
  ≤ 10 calls per watch run; HL through `ReadBudget`.
- **Repo growth.** Pools are 30-day windows; provenance records sharded and
  pruned to 2,000 accounts by recency; retro state is per member.
- **A perimeter that lies.** Perimeter roles beyond `core` are evidence weights,
  never identity: nothing here touches config, spam immunity or `settled`.
