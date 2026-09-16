# Ezekiel — Improvement Plan, 2026-09-16

**Written for execution by a later session (Opus). Plan only; nothing here is built.**

The mission is unchanged: find this trader's other Hyperliquid wallets and
catch the moment he moves to a new one. Every item below is judged on one
question — *does it make an undetected migration less likely?* — and ranked by
mission value per hour of build time, not by how interesting it is.

Read `CLAUDE.md` first. Its twelve rules bind every item here; the ones that
bite most often in this plan are **5** (a failed read must never serialise as a
clean result), **6** (never price a missing value as 0), **9** (a shared
destination is evidence only once the whole chain says it is quiet) and **4**
(never tune the thing that validates you).

Conventions for each item:

- **ID** — stable handle, use it in commit messages.
- **Why** — the mission link, in one sentence.
- **Where** — files to touch.
- **Verify** — what "done" is measured by. Every item must be replayed against
  live/stored data before it is claimed, per the repo's own standard.
- **VERIFY-API** — an API capability this plan *believes* exists but has not
  been measured in this repo. Probe it with one call before building on it,
  and if it does not exist, record that in `CLAUDE.md` so nobody re-tries.

---

## 0. The framing that produces the priorities: the migration-route matrix

The project watches a set of *routes* by which capital or activity can move
from his known accounts to an unknown one. The right way to find gaps is to
enumerate every route and ask what watches it. This is the matrix as of today;
items in **bold** are the gaps this plan closes.

| # | Route | Watched by today | Gap |
|---|---|---|---|
| R1 | HL withdraw → CEX → deposit into new HL account | `correlator.py` (amount match, 14-day window), bridge pool daily | **Only amount-matched; depositor identity never joined to the L1 graph (T1-3)** |
| R2 | HL withdraw → own L1 wallet → deposit into new account | `withdrawals.py` pairing, bridge destinations | Covered for HIS withdrawals only |
| R3 | HL spot `send` / `usdSend` to another account | `ledger_analyzer.py` | Covered |
| R4 | Sub-account transfer | `hl_surface.py`, `check_watchlist` | Covered for candidates read; **not global (T1-1)** |
| R5 | Vault deposit / withdraw | `hl_surface.py` | Evidence only, fine |
| R6 | Circle CCTP out (`sendToEvmWithData`) | `hl_actions.py`, alerts CRITICAL | Covered for him; **300-action window rolls in a day (T1-1)** |
| R7 | Circle CCTP in, from any chain | `cctp_feed.py` (forwarder ledger) | **Amount+recipient only — source chain and source SENDER are never decoded (T1-2)** |
| R8 | HyperEVM: Core→EVM, EVM transfer, EVM→Core into another account | HyperEVM nonce tripwire (his address only) | **EVM→Core landing in a stranger's account is not read; HyperEVM is not a full sweep chain (T2-4)** |
| R9 | Unit native BTC/ETH/SOL: withdraw to a BTC address, deposit into a new account | **Nothing** | **Invisible to every feed we read (T2-5)** |
| R10 | Position handoff by self-crossing (close here, open there by trading against yourself; no funds move) | **Nothing** | **Detectable from fill `tid` joins (T1-5)** |
| R11 | New HIP-3 dex inside HL | `collector.new_dexes` | Covered |
| R12 | New agent / API wallet | `agent_links.py`, `extraAgents` | Covered for candidates; **not global (T1-1)** |
| R13 | Referral pair | `referral.py`, `hl_surface.py` | Covered |
| R14 | Same exchange account (shared CEX deposit address) | `linkage.py` | **Discovery of NEW senders into his deposit addresses is not an explicit tripwire (T1-4)** |
| R15 | Capital leaving DeFi (Aave $152.6M) ahead of redeployment | **Nothing** | **Cheapest early-warning available (T2-1)** |
| R16 | Solana wallet ($22.75M) burning via CCTP to a new HL account | `solana_watch.py` by signature | **Burns are not decoded to their mint recipient (T2-2)** |
| R17 | Base / Optimism / BSC flow (Socket sent $320M to himself on other chains) | **Nothing — Etherscan free tier refuses them** | **Blockscout reads them for free (T2-3)** |

Two structural observations that also shape the plan:

1. **Detection latency is ~11 minutes at best** (watch.yml cadence, when the
   local dispatcher is alive) and hours when GitHub drops a cron. A migration
   inside HL takes seconds. Nothing in the repo is event-driven. (T1-1, T1-6)
2. **Every global question is answered from a per-wallet read.** "Who approved
   an agent in the minute after he did?", "Which account was created in his
   silence?", "Who else trades `xyz`?" — all are unanswerable because we only
   ever ask about wallets we already know. The explorer websocket (T1-1) is the
   single change that makes global questions askable.

---

## Tier 1 — build first

### T1-1. Real-time L1 action feed via the explorer websocket (global, keyless)

**Why.** It closes R4, R6, R12 globally and cuts detection latency from
minutes to seconds. It is also the only way to answer any *global* question.

**What.** An always-on process subscribing to Hyperliquid's explorer websocket
(`wss://rpc.hyperliquid.xyz/ws`, subscriptions `explorerBlock` /
`explorerTxs` — **VERIFY-API**: confirm the subscription names and payload
shape with one connection; the info websocket `wss://api.hyperliquid.xyz/ws`
definitely exists for per-user streams, see T1-6). Filter to **non-order
actions only** — `approveAgent`, `createSubAccount`, `subAccountTransfer`,
`spotSend`, `usdSend`, `withdraw3`, `sendToEvmWithData`, `vaultTransfer`,
`setReferrer`, `approveBuilderFee`, `convertToMultiSigUser`,
`cDeposit`/`cWithdraw` (staking), `tokenDelegate`. Orders are >95% of volume
and are dropped at the socket. Persist as `data/l1_actions/<day>.jsonl.gz`
with the same rolling/compaction shape as `data/transfers/`.

**Consumers, each its own small item once the feed exists:**

- `approveAgent` by ANY account whose agent *name* matches a naming family
  seen in the cluster (`agent_links.naming_families`) → HIGH.
- `approveAgent` where the agent address equals any agent ever seen in the
  cluster → CRITICAL (shared agent CONFIRMs alone; today this can only be
  found for wallets we already read).
- `createSubAccount` / `subAccountTransfer` involving any cluster or
  CONFIRMED/PROBABLE address → CRITICAL.
- `spotSend`/`usdSend` where either side is in the cluster → observed
  transfer edge, same second, instead of waiting for the next ledger read.
- `withdraw3` / `sendToEvmWithData` from ANY account to a destination that is
  in his L1 world (deposit address, known self, cluster) → CRITICAL. This is
  R14 from the HL side and is the strongest thing in this plan.
- Timing co-occurrence: accounts whose non-order actions land within ±N
  seconds of his, repeatedly, above base rate (see T1-7 for the block-level
  version using orders).

**Hosting.** The operator's PC dies on battery/sleep (the dispatcher lesson).
Prefer a free always-on VM (Oracle Cloud free tier, or fly.io free
allowance) running one Python process that also pushes ntfy directly, with
GitHub only as the archive (commit the daily file via a small workflow that
pulls from the VM, or have the VM push with a deploy key). **Record gaps as
gaps** (rule 5): the process writes `connected_since`/`gap_ranges`, and a gap
is reported on the dashboard, never treated as "nothing happened". If a VM is
unacceptable, run on the PC with the same gap accounting and accept blindness
while asleep — but say so on the dashboard.

**Where.** New `src/l1_feed.py`, `scripts/run_l1_feed.py`, `data/l1_actions/`,
a `l1-feed-archive.yml` workflow; consumers in `alerts.py`, `agent_links.py`,
`transfer_graph.py` (edge source).

**Verify.** One hour of feed replayed against `data/actions/` for the target:
every non-order action he took in that hour appears in the feed within 5s of
its block time. Filter drop rate measured (orders excluded). Gap accounting
tested by killing the socket.

**Cost.** Free API. ~$0 hosting on free tiers. Storage: estimate before
building — count non-order actions per day from one hour's sample and
extrapolate; if >50 MB/day, keep only actions touching a rolling set of
"interesting" addresses plus all `approveAgent`/`createSubAccount`.

### T1-2. Decode the *source* of every Circle deposit into Hyperliquid

**Why.** R7. Today the CCTP feed knows amount and HL recipient. The mint on
HyperEVM carries the **source domain and the source sender** — so his Solana
wallet, his Arbitrum address, or any cluster address burning USDC to a NEW HL
account becomes an *observed transfer*, not a correlation. That is the
difference between POSSIBLE and CONFIRMED.

**What.** Read `MessageReceived(address caller, uint32 sourceDomain, uint64
nonce, bytes32 sender, bytes messageBody)` logs from Circle's
MessageTransmitter on HyperEVM (chain 999 through the Etherscan key, which
CLAUDE.md measured as working; or the HyperEVM Blockscout if one exists —
**VERIFY-API** the contract address on HyperEVM from Circle's published
deployments). Decode `sender` (bytes32 → address, or a Solana pubkey for domain
5), `sourceDomain`, and from `messageBody` the amount and `mintRecipient`.
Join `mintRecipient` (= the forwarder contract for HL deposits) + amount +
block time to the forwarder's ledger send that `cctp_feed.py` already reads,
which names the HL account. Result: `(source_chain, source_address) →
hl_account, amount, time` for every Circle deposit into HL.

**Then:**
- Any source address in the cluster/CONFIRMED/PROBABLE set, or the Solana
  wallet `2xm4bb8K…`, depositing into a non-cluster HL account → **CRITICAL**,
  observed-transfer edge into the graph.
- Every (source_address → hl_account) pair becomes a first-funder-style
  linkage row: two HL accounts funded from the same source address on any
  chain share a funder (rule 9 applies: measure the source's whole-chain
  activity first; a CEX hot wallet funds everyone).
- Feed the source addresses into the L1 substrate sweep priority.

**Where.** `src/cctp_feed.py` (new decoder module beside it, e.g.
`src/cctp_mints.py`), `src/chain/bridges.py` (share the message-body decoder
that already parses `depositForBurn`), `linkage.py`, `roster.py`.

**Verify.** His own 18 Circle deposits ($66.46M) decode to source = his
Arbitrum address / Solana wallet with amounts matching to the $0.20 fee.
Zero unresolved rows for those 18. A day's decoded mints reconcile 1:1 with
the forwarder's ledger sends for that day (count and sum).

### T1-3. Turn the Arbitrum bridge into an identity feed, not just an amount pool

**Why.** R1. The HL bridge contract emits a deposit event per deposit with the
depositor address and USD amount, and processes withdrawals naming the HL
account and recipient. Today the bridge is read once a day and only for
amount matching. The *depositor address* is the join key to the whole L1
graph, and the *withdrawal recipient* is the join key to his exchange deposit
addresses.

**What.**
1. Incremental, cursored reader of the bridge's deposit and withdrawal events
   (Etherscan `getLogs` on `0x2df1c51e…`, or Blockscout) into
   `data/bridge_feed/`. Same pacing/persistence shape as `cctp_feed.py`.
   **VERIFY-API** the exact event signatures on the Bridge2 contract.
2. **Deposit join:** depositor ∈ {cluster, CONFIRMED/PROBABLE, any address
   that is a *quiet* counterparty of the cluster} → observed edge L1→HL
   account, alert by tier as the watch does (CRITICAL for his own addresses,
   HIGH for roster tiers).
3. **Withdrawal join:** an HL account (any, unknown to us) withdrawing to an
   address in his L1 world — above all his private exchange deposit address
   `0x8570c2ae…` — is the same exchange account. CRITICAL, and an
   `explicit_link`-strength vector for the roster (a CEX deposit address
   belongs to one account; CLAUDE.md already calls address reuse the
   strongest single signal).
4. Every depositor address gets resolved through `chain/activity.py` before
   it counts (rule 9), cached.

**Where.** New `src/bridge_feed.py`, `scripts/check_bridge_feed.py`,
`correlator.py` (consume as the bridge pool instead of the daily read),
`linkage.py`, `roster.py`, `alerts.py`.

**Verify.** His own deposits/withdrawals over the stored history reconcile
with `withdrawals.py`'s pairings. Replay the feed against the day
`0xdd53c529…` was born (2026-08-17): report what depositor funded it and
whether that depositor is reachable in the graph.

### T1-4. Deposit-address sentinels

**Why.** R14, and it is nearly free. A CEX deposit address belongs to one
exchange account. Any *new* sender into one of his private deposit addresses
is, by construction, another wallet of the same person. Today this is caught
only incidentally (as a contact of a watched wallet, or as a linkage row when
the frontier happens to sweep it).

**What.** A `sentinels` list: every address labelled as a private deposit
address of the cluster (`0x8570c2ae…` today; grow it from
`transfer_graph`'s deposit inference and T1-3's withdrawal join), on every
chain. Sweep each sentinel every watch run (they are low-volume EOAs, so one
call each). Any inbound from an address not in the cluster → **CRITICAL**,
with the sender added to the roster carrying an `explicit_link`-grade vector
and to the close watch. Also apply to his Solana wallet's CEX deposit
addresses once T2-2 identifies them.

**Where.** `src/watchlist.py` (a sentinel section beside `contacts()`),
`scripts/check_watchlist.py`, `config.json` (`sentinel_addresses`, operator
ground truth), `roster.py`.

**Verify.** Replay against the substrate: list every historical sender into
`0x8570c2ae…`; it should be exactly the target and `0xf078969e…`. Confirm
the cadence cost (<5s per sentinel).

### T1-5. Position handoff by self-crossing (fill `tid` join)

**Why.** R10 — a migration that moves *no funds at all*: he closes a position
in the old account by trading against a resting order from the new one. Both
fills share one trade id. Nothing in the repo can see this, and it is the
quietest handoff possible.

**What.** Fills carry `tid` (shared by both sides of a trade), `oid`,
`crossed`. We hold his fills (`data/fills/`) and the scanner holds candidates'
fills. Join on `tid`: an account that is his counterparty **repeatedly** —
above the base rate expected for a market maker of its size — and in
particular where the counterparty's fill is the *resting* side (`crossed:
false`) at the exact moment he crosses, with matching size, is either a
market maker (high whole-venue fill count, many counterparties) or a second
hand. Score: number of shared `tid`s, share of his volume they absorb,
whether the counterparty's position *opens* as his *closes* (notional
conservation across the pair, see T2-8).

Extend candidate fill coverage: for the top-N roster and every watched
wallet, pull `userFillsByTime` incrementally so the join has both sides.

**Where.** New `src/self_cross.py`, `scripts/check_self_cross.py`,
`roster.py` (`VECTOR_SELF_CROSS`, two votes' worth only when combined with
notional conservation; one otherwise), `alerts.py`.

**Verify.** Measure base rate first: distribution of shared-`tid` counts
between him and every scanned wallet. Report the top ten with their
whole-venue fill counts. Pin a test that a high-frequency market maker (many
counterparties, tiny per-fill share) scores below threshold.

### T1-6. Per-user websocket watch on the target and watched wallets

**Why.** Latency. The info websocket (`wss://api.hyperliquid.xyz/ws`) streams
`userNonFundingLedgerUpdates`, `userFills`, `userEvents`, and `webData2` per
address. A withdrawal, a spot send, a sub-account transfer, a new agent or a
new dex on the target is known in ~1s rather than ~11 minutes. This is the
per-user half of T1-1 and can be built first if T1-1's subscription proves
unavailable.

**What.** Same always-on process as T1-1. Subscribe for the target,
`known_self_wallets`, and the watch set (re-read the roster every few
minutes). Push ntfy directly for anything `alerts.py` would rate CRITICAL/
HIGH, and write a shard to `data/alerts/runs/` so delivery health stays a
pure function of shards. Never a second writer on `data/actions/` or
`data/watchlist/` (single-writer rule): the stream writes its own store,
`data/stream/`, and the batch jobs remain authoritative for the files they own.

**Verify.** Trigger nothing on his account (obviously); instead subscribe to a
busy stranger and confirm events arrive; measure reconnect behaviour and gap
accounting.

### T1-7. Block co-occurrence (who acts in his blocks)

**Why.** A second account driven from the same browser session or script
submits in the same second. HL blocks are ~1s. Over weeks, an account that
appears in his blocks far above its base rate is co-driven — and this is a
behavioural reading a copy-trader cannot fake, because a copier reacts
*after* his fill lands.

**What.** `userDetails` gives the block height of each of his actions. Fetch
`blockDetails` for those heights (`rpc.hyperliquid.xyz/explorer`, ~300
calls/day — **VERIFY-API** the request shape), record every other user in
the block. Base rate per account = that account's actions per day ÷ blocks
per day (from T1-1 once it exists, else estimate from the leaderboard).
Report accounts with co-occurrence > 5x base rate and ≥ 20 shared blocks.
Sharpen with sub-second ordering inside the block if the payload has it:
an account whose action precedes his in the same block is leading, not
copying (ties `comovement.py`'s "who moves first" to the block, which is far
finer than fill timestamps).

**Where.** New `src/block_cooccurrence.py`, `scripts/check_block_cooccurrence.py`,
`roster.py` (evidence at first; promote to a vector once the base-rate model
is measured and a stranger population is scored).

**Verify.** Score the whole scan population; the distribution should be long-
tailed with market makers at the top by raw count but below threshold after
base-rate correction. Report the top ten with reasons.

---

## Tier 2 — high value, build after Tier 1

### T2-1. DeFi capital-in-motion tripwire (Aave, Pendle, Morpho on Arbitrum)

**Why.** R15. $152.6M sits in Aave. A withdrawal from it is capital about to
go somewhere, and it is the earliest possible warning that a migration may
follow. One `balanceOf` per protocol token per run.

**What.** Track `aArbUSDCn` (and any other position token the substrate shows
him holding) balances for the cluster each collect run via Blockscout/RPC. A
drop > X% or > $Y (config) → HIGH "capital in motion", *and* it opens a
"heightened" correlator window: for the following 7 days, lower
`min_amount_usd` and widen `tolerance_pct` only for exits that trace to that
withdrawal. Record the chain of custody from the aToken burn onward.

**Where.** `src/defi_positions.py`, `collector.py` step, `risk.py` factor,
`correlator.py` (window hint), `alerts.py`.

**Verify.** Replay against the 67 Aave transfers already in the substrate; the
tripwire must fire on each historical withdrawal day and nowhere else.

### T2-2. Decode the Solana wallet's CCTP burns

**Why.** R16. `2xm4bb8K…` received $22.75M of his. If it burns USDC through
CCTP, the `depositForBurn` instruction names `mintRecipient` and destination
domain — 19 is HyperEVM, i.e. a direct Circle deposit into a named HL account.
Today the watch only notices "a signature happened".

**What.** In `solana_watch.py`, parse each new transaction's instructions for
Circle's TokenMessengerMinter program; decode `destinationDomain`,
`mintRecipient` (bytes32 → EVM address), amount. Join with T1-2's feed. Also
label the wallet's SOL/USDC counterparties: if it sends to a Solana CEX
deposit address, add that address as a sentinel (T1-4).

**Verify.** Replay over the wallet's full history; report every burn with its
recipient and whether the recipient is an HL account with any use.

### T2-3. Sweep Base, Optimism and BSC through Blockscout

**Why.** R17. He sent $320M to himself on other chains via Socket and uses
Base as a CCTP domain. These three chains are a *permanent* gap under the
Etherscan free tier, yet `chain/activity.py` and `chain/bridges.py` already
read Blockscout for two of them keyless. The frontier is blind on chains he
demonstrably uses.

**What.** A Blockscout provider in `chain/client.py` implementing the same
three record kinds (`txlist`, `tokentx`, `txlistinternal`) via Blockscout's
`/api?module=account` compatibility endpoints (they mirror Etherscan's shape
— **VERIFY-API** per chain, and BSC's Blockscout instance may not exist; note
it). Route a chain to Blockscout when Etherscan reports a plan refusal
(reuse `plan_refused`). Rate limits: Blockscout public instances are ~10
req/s unauthenticated; page conservatively and keep `unsupported_sources`
honest per chain.

**Verify.** Sweep the target on Base and Optimism; reconcile against the CCTP
domain-6 destinations already decoded from Arbitrum calldata (every mint
recipient on Base should now appear as a Base record). Frontier
`unsupported_sources` should drop to BSC only (or to nothing).

### T2-4. HyperEVM as a full sweep chain, plus the EVM→Core landing

**Why.** R8. The nonce tripwire covers only his own address sending. A route
that never touches L1 — Core→EVM from his account, ERC-20 hop on HyperEVM,
EVM→Core into a stranger — is invisible. Chain 999 reads through the Etherscan
key (measured).

**What.** Add `hyperevm` to `config.chains` with the same sweep treatment as
Arbitrum (it has `chain/hyperevm.py` already — check what it covers and
promote it). Treat transfers to the Core system address (`0x2222…2222`) as
"EVM→Core" edges and resolve the receiving HL account from the ledger
(`spotSend` from the system address appears in the recipient's
`userNonFundingLedgerUpdates`; the *forwarder-style feed* trick from
`cctp_feed.py` may apply to the system address — **VERIFY-API** whether the
system address's own ledger is readable the way `0x6b9e7731…`'s is; if it is,
that is a complete keyless feed of every EVM→Core transfer).

**Verify.** The eight airdrop-spam rows on his HyperEVM address appear as
records classified spam; zero genuine outbound; the feed (if readable)
reconciles with a known EVM→Core transfer of any busy account.

### T2-5. Unit (native BTC/ETH/SOL) operations

**Why.** R9 — the one door nothing watches. Unit gives each HL account
per-asset deposit addresses; a withdrawal of BTC to an external address and a
later BTC deposit into a *different* HL account is invisible to CCTP, the
bridge and the ledger's USDC view. Unit's public API exposes operations per
address (**VERIFY-API**: `api.hyperunit.xyz` — endpoints for generating
addresses and listing operations by HL address; confirm shape and whether
history is served keyless).

**What.** For the target, cluster and every watch/PROBABLE wallet, read Unit
operations each trace run. Record source/destination native addresses. A
native address that appears in two accounts' operations is address reuse —
an `explicit_link`-grade vector. Also: a BTC/ETH/SOL address he withdraws to
becomes a sentinel via the relevant chain (BTC needs a keyless explorer such
as mempool.space; SOL is already read).

**Verify.** His own operations (if any) reconcile with spot balance changes in
`data/spot/`. If he has none, record the measured "no" in `CLAUDE.md`.

### T2-6. Newborn × first-funder × deposit-route join

**Why.** `newborn.py` finds young large accounts from the leaderboard;
`resolve_first_funders.py` resolves first funders; T1-2/T1-3 give deposit
routes. Nothing joins them into "which newborns were funded the way he funds".

**What.** For every newborn above `$1M` (config), resolve: first ETH gas
funder on Arbitrum, first HL deposit route (bridge depositor / CCTP source
chain+sender / Unit), and the CEX hot wallet behind the depositor if any.
Score against his own routes (Binance via `0x8570c2ae…`, CCTP from his
Arbitrum address, CCTP from Solana). A newborn funded from a *quiet* address
already in the graph enters the roster with linkage; one funded from the same
CEX hot wallet within an hour of his own withdrawal from that hot wallet
enters as a correlator candidate with the CEX-hop timing as a second signal.

**Where.** `newborn.py`, `linkage.py`, `scripts/resolve_first_funders.py`,
`roster.py`.

**Verify.** Replay on the 2026-08-17 birth of `0xdd53c529…`: the join must
name its two funding sources and their hot-wallet provenance.

### T2-7. Sequence matching in the correlator (a run of exits ↔ a run of deposits)

**Why.** Single-amount matching against round millions is weak (CLAUDE.md's
rejected "amount signature"), which is why the current best correlation is
0.57 with 18 competing deposits. But he moves in *runs*: N slices of ~$1M over
M hours. A run of k deposits into one account with matching sizes and similar
inter-arrival gaps is far more specific than any single amount.

**What.** In `correlator.py`, after single matches, group his exits into runs
(gap < 6h, config), and for each candidate account group its deposits the
same way; score runs on count, size profile and gap profile (DTW or simple
sorted-diff). A run match with k ≥ 3 replaces the single-match confidence.
Keep the 14-day window; keep every rule about pools.

**Verify.** Backtest on his known self-transfers between his own accounts (the
$66.5M of Circle deposits, the treasury flows): the run matcher must rank his
own accounts first with a margin, measured, before it scores strangers (rule 4
— do not tune to strangers).

### T2-8. Mirror-on-close and notional conservation

**Why.** A copier closes when he closes. A second hand *opens* the same
direction when he closes, so that combined exposure stays flat. This is the
inverse of copying and `comovement.py` does not look for it.

**What.** For each of his position reductions (from `data/positions/` diffs
and fills), look for a candidate whose position on the same coin and side
*increases* within a window, with notional ≈ his reduction. Score on notional
conservation across the pair over a day (sum of both books per coin ≈
constant while individually they swing). Combine with T1-5 (self-cross makes
this exact).

**Where.** `comovement.py` (new mode) or `src/handoff.py`; `roster.py`
evidence, promoted to a vector after a stranger-population measurement.

**Verify.** Measure the false-positive rate on the scan population first; a
market maker absorbs everyone's closes and must be excluded by fill-count
base rate.

### T2-9. Historical evidence ledger (a finding does not un-happen)

**Why.** The roster is rebuilt from scratch each run — right — but an
observed correlation match is a *historical observation* that currently
vanishes when the 14-day window moves past it, taking the wallet's tier and
its close-watch slot with it (`0x5b5d5120…` at $230M). CLAUDE.md rightly
forbids hand-restoring tiers; this is the principled version.

**What.** An append-only `data/evidence/` ledger: every vector finding is
written once with `observed_at`, source file, score. The roster reads *live*
vectors as today, and additionally counts a **decayed** historical finding
(e.g. half-weight after 30 days, zero after 90 — config) as *evidence*, and
as a vector only while above a floor. Facts (transfer, shared funder, agent,
sub-account) never decay; inferences (correlation, dormancy, behavioural)
decay. Show `evidence_age` on the dashboard.

**Verify.** Replay the roster over the stored history: `0x5b5d5120…` should
stay PROBABLE for the decay period after its correlation lapsed, then drop
with a `tier_dropped_from` reason that names decay rather than absence.

### T2-10. Unlock-schedule check for the strongest GCR claim

**Why.** CLAUDE.md: "the most specific claim in the whole corpus" — he shorts
low-float tokens ahead of large scheduled unlocks — is unimplemented for want
of an unlock source. DefiLlama's emissions/unlocks API is free
(**VERIFY-API**: `api.llama.fi/emissions` and `emission/{protocol}`).

**What.** For every short he has held (from `data/positions/` history and
fills), compute days-to-next-large-unlock at entry and unlock size as a share
of float. Compare with a null (random tokens, same dates). Wire as a
`gcr_hypothesis` check that CAN CONTRADICT, and as a behavioural dimension
(`unlock_short_propensity`) for candidates — only added to the scorer through
the backtest, never by reweighting (rule 4).

**Verify.** Report his propensity vs the null with n; if n is too small, say
UNTESTABLE.

---

## Tier 3 — cheap wins and calibration

### T3-1. Exclude and label the operator's own wallet(s)

The owner copy-trades him manually. The owner's wallet is therefore the most
copier-shaped account on the venue and, if it is ever in the leaderboard
pull, it will top the behavioural ranking and pollute co-movement. Add
`config.owner_wallets`, exclude from every candidate set and lineup exactly
as the target is (three guards, same places), and classify as `COPIER`.
Ask the operator for the addresses.

### T3-2. `COPIER` as an explicit roster classification

Accounts that consistently *lag* him (`comovement.py`) are copiers. Grade
them `COPIER` (like `INFRASTRUCTURE`: a measurement that outranks inference),
exclude them from detector budgets and the backtest lineup, and show the
count. Cleans the ranking and stops copiers consuming the top-N caps.

### T3-3. `transfer` should not vote at depth 2 with $0 direct flow

Observed today: `0x3b2d7db2…` is PROBABLE on `dormancy_handoff` +
`transfer`, where `transfer` means "reached at 2 hops, 4 edges, $0 to/from
target" — and it holds $42.6K. That takes one of six close-watch slots.
Require the `transfer` vector to mean direct flow with the cluster (depth 1,
or an explained path whose intermediate is not infrastructure); otherwise
record it as `graph_reach` evidence. Replay the roster and report tier moves.

### T3-4. `hl_active` and "copyable" on every roster row

The deliverable is an HL address that trades. Both CONFIRMED wallets hold
~$0 on HL. Tag each row with `hl_active` (perp or spot use, any dex, from
`hl_surface`/`hl_identity` reads) and surface a **"copyable now"** list on the
dashboard: CONFIRMED/PROBABLE ∧ hl_active. Every CRITICAL alert about a
wallet carries its HL state (value, open positions, dexes) so the operator
can act from the notification.

### T3-5. Make the behavioural vector vote when the backtest passes

`profile/backtest.json` has `passed: true` (self 0.6593, margin +0.067) yet
the roster reports `behavioural_counts_as_a_vector: false`. Find the reason
(stale read, N-consecutive requirement, or a schema gate) and fix it — without
touching weights or thresholds. Expect ~7 wallets WATCH → POSSIBLE.

### T3-6. Accumulate history for watched wallets

The HL API serves ~2,000 records per endpoint. The collector accumulates the
target's fills/ledger/orders every 15 minutes; watched wallets get a 21-day
scan snapshot only. If one of them is him, we will want its full history.
Have the watch job pull `userFillsByTime` and `userNonFundingLedgerUpdates`
incrementally by cursor for the watch set into `data/watch_history/<addr>/`.
Bounded by the six-wallet cap and the existing cadence budget.

### T3-7. Dead-man's switch on the dispatcher

The local dispatcher died silently on battery for two hours. Have it ping a
free healthchecks.io check on each run; healthchecks alerts to ntfy when the
ping is missed for 20 minutes. Independent of GitHub and of the PC.

### T3-8. Daily INFO digest

INFO never buzzes (right). Send one ntfy message a day summarising INFO
counts, roster tier moves, demotions, demoted_count, discovery liveness and
feed gaps, so the quiet channel is still read.

### T3-9. Builder-fee and multi-sig tripwires

`approveBuilderFee` names the frontend/terminal he uses — a per-human default
that narrows candidates; `convertToMultiSigUser` names signer addresses. Both
appear in `userDetails` (and globally via T1-1). Parse them in `hl_actions.py`,
store, and alert on a new one for the cluster.

### T3-10. Validator co-delegation timing

If he stakes HYPE, `delegations`/`delegatorHistory` show validator, amount,
time. Two accounts delegating to the same small validator within minutes,
with related amounts, is evidence. Cheap: one call per candidate, already
partly in `hl_identity.py` (`stakingLink`). Extend to timing and validator
rarity (validators with few delegators weigh more; the top validators weigh
nothing — the same rarity logic as `portfolio_overlap`).

### T3-11. Name resolution sweep

Reverse-resolve ENS (mainnet), Base names and `.hl` names for every roster
and cluster address; grep the results with the naming-family regex
(`rebirth|gigantic|gcr|ezekiel|ming|mecca|trueshiba|kabosu|goblin|stonehenge`).
Check what `scripts/check_names.py` already covers and extend rather than
duplicate. Also grep leaderboard `displayName`s each scan.

### T3-12. Approval fingerprint (reopened in CLAUDE.md, still unbuilt)

The protocols he approves and the order he approved them (Aave, Paraswap,
Pendle, Morpho, Socket) from `txlist` `approve` calls; Jaccard + order
similarity against candidates' Arbitrum approvals. Evidence, not a vector,
until measured on a stranger population.

### T3-13. Counterparty-set overlap and gas habits

Both listed in CLAUDE.md as unbuilt. Jaccard over full counterparty sets
(excluding infrastructure) is one function over the substrate; priority-fee
habits need `txlist` `gasPrice`/`maxPriorityFeePerGas` per tx, already in the
records. Evidence only; measure separation on strangers before either votes.

### T3-14. Operator decision queue

Some findings are decisions, not code: whether `0xb83de012…` (~$177M,
same-day referral twin of the ex-PROBABLE) joins `config.watch_wallets`;
whether to re-sweep the frontier with `--reset` (est. ~3.2M records); whether
a VM is acceptable for T1-1/T1-6. Put them in a `docs/decisions.md` with the
evidence for each, so they are answered rather than re-derived every session.

---

## Tier 4 — reliability and debt (protects everything above)

- **D-1.** `_load_cached` 256 MiB ceiling counts file bytes while holding
  parsed objects; with gzipped shards it no longer protects anything. Bound
  on parsed size (or record count) and measure heap before/after.
- **D-2.** The four remaining `records_for` loops (frontier, tracer,
  correlator, scripts) → `records_by_wallet` batch reads. Measured 36x on the
  linkage phase; expect similar.
- **D-3.** The Circle pool backfill lag: leave the budgets alone (CLAUDE.md),
  but show "feed position vs present" on the dashboard so a stalled feed is
  visible, and alert HIGH if a feed's cursor has not advanced in 24h (the
  stalled-frontier lesson, applied to every cursor: cctp, bridge (T1-3),
  l1 feed (T1-1), Solana).
- **D-4.** A **mission regression harness**: replay stored history through the
  detectors for known events (the 2026-08-17 birth of `0xdd53c529…`, the
  $2.2M/4.4h correlation, the treasury's two-way flow, the referral twins)
  and assert each vector finds what it found, with measured *detection
  latency* per event. This is the test that says "would we have caught it",
  which no unit test does. Run in `test.yml` against fixtures cut from live
  data.
- **D-5.** Detection-latency KPI on the dashboard per alert type (event time →
  alert time), so cadence regressions show as a number.
- **D-6.** Split the `data-commit` concurrency group once a single-writer map
  exists (T1-6 makes this urgent, since the stream is a new writer). Derive
  the map by grepping every `save_latest`/`append_*` call for its path and
  asserting one workflow per path in a test.
- **D-7.** Alert de-duplication keyed on (subject, wallet, evidence hash) so
  a re-fire carries only *new* evidence; today the 24h cooldown is the only
  guard.

---

## What NOT to build (already measured and rejected — see CLAUDE.md)

Amount signatures; cross-vs-isolated margin; liquidation-distance habits;
Twitter scraping; raising the cache ceiling; widening `cctp_time_budget`;
re-adding the watch steps to `trace.yml`; hand-restoring any tier.

---

## Suggested build order for Opus

Each step ends with the repo's standard: run it, read the output, replay
against stored data, `ruff` clean, tests green, `CLAUDE.md` updated with what
was measured (including any VERIFY-API that failed).

1. **T1-4** (sentinels) and **T3-3** (`transfer` vote) — one session, small,
   immediate value, and T3-3 frees a watch slot.
2. **T1-2** (CCTP source decode) — the highest evidence-grade upgrade with no
   hosting question.
3. **T1-3** (bridge feed with identity joins) and **T2-6** (newborn join).
4. **T1-5** (self-cross) and **T2-8** (mirror-on-close) together — they share
   the fill infrastructure.
5. **T1-6** then **T1-1** (websocket stream, then global feed), after the
   hosting decision in T3-14. Build gap accounting first.
6. **T1-7** (block co-occurrence) once T1-1 gives base rates.
7. **T2-1, T2-2, T2-3, T2-4, T2-5** — each independent; order by which door he
   is most likely to use next (Aave first: the money is there today).
8. **T2-7, T2-9, T2-10, T3-\*, D-\*** as budget allows.

Every VERIFY-API probe should be its own tiny commit that records the answer,
so a session that runs out of budget mid-item still leaves the measurement
behind.

---

## Addendum (same day) — further ideas, not yet ranked into the tiers above

Each is cheap to probe. Same conventions; VERIFY-API where marked.

### A-1. Adaptive cadence: a "migration in progress" mode
The dispatcher runs fixed intervals. When a trigger fires — capital in motion
(T2-1), an unusual silence, a withdrawal above his p90, a CRITICAL of any
kind — the dispatcher should drop `watch.yml` to every 2 minutes and
`collect.yml` to every 5 for the next 24h, and the correlator should narrow
its window. The moment that matters is exactly when fixed cadence is
weakest. State lives in a small file the dispatcher reads; the trigger is
written by `alerts.py`. **Where:** `scripts/dispatch_workflows.ps1`, `alerts.py`.

### A-2. Testnet as a rehearsal room
People test API wallets and new setups on `api.hyperliquid-testnet.xyz` with
the same keys before mainnet. Read every cluster address, every cluster agent
and every watch wallet on testnet: agents, sub-accounts, fills. A **shared
agent on testnet** between a cluster address and a stranger is the same
CONFIRM-alone vector, and it would precede mainnet use. Free, ~30 calls.
Record a measured "no" if empty.

### A-3. Agent and sub-account addresses are EOAs he holds keys to
Every API wallet the cluster has approved (the treasury has approved 18) is
an EOA. Sweep them on Arbitrum/Ethereum and read them as HL *users*: if one
ever receives funds, trades, or is approved as an agent by a *different*
account, it links. ~25 addresses, one sweep each, then into the sentinel set
(T1-4). Nothing reads them as anything but agents today.

### A-4. `userRateLimit` as a software fingerprint
`userRateLimit` returns cumulative volume and requests used/cap per address.
Requests per dollar of volume separates a manual frontend user (him) from an
API bot far more cheaply than the order profile, at one call per candidate.
Measure separation on the scan population before it scores anything (rule 4).
Also `userFees.activeStakingDiscount` reveals HYPE staked → T3-10.

### A-5. Sleep-window veto
From his fill timestamps compute his daily inactive window (a human sleeps
~6h). A manual-style candidate that is active *inside* that window on most
days is not him — a cheap disconfirmer for the roster, which today can only
veto bots. Bots are exempt (they never sleep). Measure his window's
stability first; if he has none, say UNTESTABLE.

### A-6. Relayer-mediated deposits fool the bridge join (T1-3 caveat)
Deposits into HL via Relay, deBridge, Across or Socket arrive at the bridge
from the *relayer's* Arbitrum address, so T1-3's depositor join sees
infrastructure. Grade those relayers infrastructure (rule 9) **and** decode
the originator: Relay exposes a free public request API keyed by user
(**VERIFY-API** `api.relay.link`), Across and deBridge have public
explorers/APIs. This is R1 through a relayer — the exact route someone
avoiding a visible L1 hop would use.

### A-7. `setDisplayName` and naming-family grep, globally
Once T1-1 exists, grep every `setDisplayName`, agent name and sub-account
name venue-wide against the naming-family regex. Until then, grep the
leaderboard's `displayName` field each scan (cheap, ties into T3-11).

### A-8. Public trackers may already have clustered him
Hypurrscan, Hyperdash, Coinglass whale feeds and Arkham label HL addresses
and publish "related wallets". One manual pass (and a scripted check where
an API is free) asking whether any public tracker labels `0x45d26f28…` or
links it to another address. Off-HL research is allowed when it identifies
the person; this is the cheapest such check and it has not been done.

### A-9. Historical global backfill from Hyperliquid's public node data
The `hl-mainnet-node-data` S3 bucket (requester-pays, ~$0.09/GB egress)
holds every L1 action since genesis. Filtering one day's explorer blocks for
non-order actions would backfill T1-1 *before* the stream started: every
`approveAgent`, `createSubAccount`, `spotSend` ever, for every account.
That answers "who has ever shared an agent with the cluster" historically,
which no live feed can. Cost it before running: sample one day's size first.

### A-10. Alert precision ledger
Record for every CRITICAL/HIGH whether the operator judged it true, false or
undecided (a one-line file, or an issue label). Show precision per alert
type on the dashboard. It is how the operator learns which alerts to act on
in seconds, and how the next session learns which vector is crying wolf.

---

## Addendum B — a third pass

### B-1. Follow the followers (the herd as a sensor)
His copiers will find his new wallet through channels we cannot read —
Telegram, X, copy-trade platforms rerouting. Their fills then reveal it.
Once copiers are classified (T3-2), watch **who each copier lags against**
across a candidate set (leaderboard top-N by recent volume plus newborns).
When the herd's lag target shifts from him to one account, that account is
the new wallet — and this fires even if every on-chain route was hidden.
Score: share of copiers whose best-lag target changed to the same address
within a week. CRITICAL. **Where:** `comovement.py` (many-to-many mode),
`roster.py`.

### B-2. Test transfers are the precursor, and they are filtered out today
Before moving size, people send $1–$100 to the new address. `config
.hl_transfer.min_usdc_track` is **1,000**, so a $10 HL-native test send from
him to a brand-new account is *ignored*; the correlator's floor is $100k;
the graph keeps ≥$1 but a new depth-1 node at low confidence only rates
INFO, which never buzzes. Fix: **any outbound from the target or a CONFIRMED
wallet to a never-seen address, at any amount, on HL or L1, alerts HIGH** —
novelty is the signal, not value. Check the same for non-USDC spot sends
(rule 11 values them at None → below every floor). This is probably the
single cheapest high-value change in the whole plan.

### B-3. Exact CEX withdrawal fees turn fuzzy matches into exact ones
The correlator matches at 3% tolerance. Exchanges charge a *fixed* USDC
withdrawal fee on Arbitrum (Binance, OKX, Bybit, MEXC each differ, and
Circle's $0.20 is already used this way). A deposit equal to an exit **minus
one exchange's exact fee** is a near-exact match *and names the exchange*.
Build a fee table (verified against his own historical round-trips through
`0x8570c2ae…`), match exact-minus-fee first, fall back to tolerance.
Also weight matches by **his own measured exit→re-deposit delay
distribution** from confirmed self-transfers, instead of a flat 14-day window.

### B-4. A newborn's first ten actions
The explorer's 300-action window is a limitation for him and *not* for a
newborn: it holds a young account's entire early life. First action, first
coin, first order type, agent-before-trade or trade-before-agent, sub-account
creation timing, deposit-to-first-trade latency — compare each newborn's
opening sequence with the opening sequences of his known accounts (target,
treasury, `0xf078969e…`; `0xdd53c529…` if it settles). A per-human habit,
one call per newborn, and it works before the wallet has any fill history to
fingerprint. Evidence into T2-6.

### B-5. Wind-down and staging signals on his own account
Cheap `risk.py` factors from data already collected: idle spot USDC above X%
of account value for > N hours (he parked $44.7M there once — capital
staged), resting order count trending to zero, per-coin position notional
shrinking uniformly while account value holds, cancel-all events in
`historicalOrders`. Each is a pre-migration posture; together they should
trigger A-1's adaptive cadence.

### B-6. Style drift on the target himself
The fingerprint is rebuilt from him daily but nothing asks whether *he*
changed: a switch to client order ids, native TWAP instead of IoC slices,
a new slice size, a new frontend agent cadence. Tooling changes on the old
account often mean new software being set up — the same software that will
drive the new one — and they also mean the fingerprint used on strangers is
going stale. Alert HIGH on a drift beyond his own historical variance.

### B-7. Venue-wide conservation of value
If the leaderboard file carries `accountValue` per account and refreshes
often enough (**VERIFY-API** cadence), then a drop of ΔV at his account in
one interval paired with a rise of ≈ΔV (net of market move) at a single
other account is a global correlator that needs **no per-wallet calls**.
Copiers rise with him, not against him, so the sign separates them.

### B-8. Vault leaders as candidates
He treats visibility as a cost; a vault is the one HL structure that shows
positions under an address that is not the trader's own. The public vault
list (**VERIFY-API** `stats-data.hyperliquid.xyz/Mainnet/vaults` or
equivalent) gives leader addresses and TVL. New vaults with a young leader
and large TVL enter the newborn set; a vault whose leader is any cluster
address is CRITICAL (T1-1's `createVault` catches it live).

### B-9. Cluster-relative depth, not target-relative
The treasury is CONFIRMED and two-way with him at $135M/$148M. Its direct
counterparties are his accounts by construction, but if the graph's
`transfer` vector and depth discount are measured from the *target*, they
sit at depth 2 with no direct flow (the T3-3 shape). Verify that depth and
the `transfer` vote are measured from the nearest CONFIRMED wallet; if not,
make them so. Replay and report tier moves.

### B-10. API budget gauge
Etherscan's free tier is 100k calls/day. Nothing counts usage, so hitting
the cap would degrade discovery silently — the stalled-frontier shape.
Count calls per key per day in `chain/client.py`, show it on the dashboard,
and alert HIGH at 80%.

---

## Addendum C — forensic-grade ideas

### C-1. Clock-skew fingerprinting from action nonces
Every HL L1 action carries a `nonce` that the *client* sets to its own clock
in milliseconds (the frontend uses `Date.now()`; bots use theirs). The block
carries the venue's time. `nonce − block_time` is therefore the submitting
machine's clock offset plus network latency — and a desktop clock drifts in
a way that is stable over hours and unique to the machine (the classic
remote device-fingerprint result). Two accounts whose offsets track each
other over days are driven from **one machine**, whatever their trading
style. NTP-synced servers sit at ~0 with no drift, which also separates bots
from desktops for free. **VERIFY-API** that `txDetails`/`userDetails`
expose the nonce; if they do, this is the least fakeable vector in the plan
and works on a single day of data. Measure on his own actions first: the
offset series should be smooth; then on strangers for the base rate of
coincidental agreement.

### C-2. Find him on the public tape before knowing an address
His IoC slice trains are visible on the public trade feed (`trades`
websocket / `recentTrades`) as sequences of same-side taker prints with his
size distribution and cadence — no address, but global and keyless. Detect
slice-train signatures on the tape across all coins, then resolve the
*address* by reading `blockDetails` at those seconds (T1-7). This is the
only candidate generator in the project that depends on neither the
leaderboard, nor funding, nor a link — it finds a fresh wallet on its first
day of trading. Calibrate the signature on his own prints (they are in the
tape too) and measure the false-positive rate against the whole tape.

### C-3. Open-interest conservation on his closes
If he closes N contracts and the coin's open interest falls by less than N,
his counterparty was *opening* — the exact shape of a self-cross handoff
(T1-5), detectable with **no candidate fills at all**, from
`metaAndAssetCtxs` OI before and after his fill. Noise from other traders
is the confound, so weight by his share of OI; on the `xyz` markets and thin
alts he *is* most of the OI, and there the test is nearly exact. A close
that conserves OI on a market he dominates is HIGH on its own.

### C-4. Find the account he migrated FROM
The target is born 2026-02-05 in our data; the treasury and `0xf078969e…`
are older. He has therefore done this before, and his previous HL account
teaches his migration playbook — route, test transfer, overlap period,
sizing, whether the old account was drained or left. Dormancy in reverse:
an HL account funded two-way by the treasury that went quiet as the target
was born. Same detectors, run backwards from his birth. Then every
threshold in this plan (windows, delays, test sizes) is set from his own
measured behaviour rather than guessed.

### C-5. Likelihood-ratio roster instead of vector counting
Tiers count agreeing vectors, which is why a $42K wallet is PROBABLE on two
weak ones. Each vector's likelihood ratio can be *measured* on the stranger
population we already hold (how often does dormancy-handoff fire on a
random wallet? shared funder? correlation at 0.57 with 18 competitors?).
Combine as a log-likelihood sum; derive tiers from the posterior; keep the
CONFIRM-alone vectors as effectively infinite ratios. Calibrated on
strangers only — never on him (rule 4) — and reported with the ratios so a
tier is explainable. Independence between vectors is still the assumption;
T2-9's "same data source" rule guards it.

### C-6. Public attention as a migration precursor
He treats visibility as a cost and left FTX's leaderboard for it. Migration
becomes likely right after a public callout. Count daily open-web mentions
of the address string (search-engine and GitHub code search, keyless), and
whale-tracker bot posts where an API is free. A spike triggers A-1's
adaptive cadence. Cheap, and it models *why* he would move, not only how.

### C-7. Joint silence calendar
Days when every cluster account is silent are days he is away. A manual
candidate silent on the same days — holidays, travel — over months is
evidence a copier's bot cannot produce and a human stranger rarely does.
Measure the stranger base rate first; the vector is only as good as the
rarity of the shared days.

### C-8. Route-recall harness
D-4 replays known events. Add a *generative* version: synthesise a
migration through each route in the matrix (R1–R17), inject it into fixtures
and measure recall and detection latency per route. The matrix then carries a
measured recall column instead of "covered", and a route whose recall falls
after a refactor fails CI.

### C-9. Off-HL identity, cheap forms only
Farcaster and Lens publish verified addresses (Neynar free tier); a cluster
address appearing on a profile names a person. If the Solana wallet trades
perps on Drift, its style is public and fingerprintable — not a deliverable,
but "he is trading elsewhere" is a migration-risk factor. Both are one-call
checks; record a measured no.

---

## Addendum D — outside the box

### D-1. The treasury is the birthplace; treat it as a co-target
`0x1419e75…` is CONFIRMED, two-way with him at $135M/$148M, and is the hub
every dollar routes through. A new HL account of his is most likely funded
by an HL-native `usdSend` **from the treasury**, which appears in the
treasury's own ledger the same minute. Today the treasury gets the watch's
reads (value, agents, sub-accounts, destinations) but not the collector's
(ledger, fills, orders every 15 min). Give it full collector coverage and
read its ledger every watch run; any send from it to a never-seen address
is B-2's HIGH. This is the cheapest single change with the highest prior
of being the one that fires.

### D-2. Migration is a ratio, not an event
The accounting reconciles ~$1.3B of outflow; his HL account holds ~$69M,
Aave $152M, and 99.7% of the rest went to exchanges. So the HL account is
a fraction of him, and "migration" may be a gradual shift of share rather
than a day. Track **his observable capital's HL share** over time (HL ÷ [HL +
Aave + known L1 + Solana]) and the **unexplained residual** (total observable
net worth change − PnL − traced flows). A falling HL share with a growing
residual is a migration in progress with no single event to alert on. Put
both on the dashboard and in `risk.py`.

### D-3. A live fire drill through every route
The only true end-to-end test is a real migration. The operator opens a
small HL account (excluded via T3-1) and performs each route in the matrix
with $20–$50: test send, sub-account transfer, CCTP out and in, bridge
deposit from a fresh wallet, agent approval, self-cross of a tiny position.
Measure what fired, how fast, and what stayed silent. Costs gas and an
afternoon; it would have caught most of the blind spots in `CLAUDE.md`
before they cost findings.

### D-4. "What would change my mind" — next best measurement
For every PROBABLE/POSSIBLE wallet, generate automatically the cheapest
observation that would CONFIRM or refute it: "a withdrawal to `0x8570c2ae…`
confirms", "an agent shared with X confirms", "birth before 2024-02 refutes",
"a fill inside his sleep window refutes". Rank by expected information per
API call and spend a small budget each run on the top ones. Turns the roster
from a scoreboard into a research queue, and gives a session with little
budget the one thing worth doing next.

### D-5. Vector ROI audit — retire what never moves a tier
Fifty-plus signals, 21k lines, and the repo's own history is mostly
self-inflicted outages. Measure per vector: runs, API calls, seconds of
cadence, and how many times it *ever changed a tier or fired a routed
alert*. Retire or demote to daily anything that costs the fast jobs cadence
and has produced nothing. Fewer, faster, better-measured signals catch more
than many slow ones; the watch's whole value is its cadence.

### D-6. Cross-chain CEX hop as a correlator pool
His Solana wallet's deposit into a CEX and a withdrawal from that CEX's
Arbitrum hot wallets minutes later, amount-linked, is one exchange account
seen from two chains — and the withdrawal's recipient is the candidate
before it ever touches HL. The correlator only sees the HL deposit end.
Add the CEX-hop pool: cluster deposits into any labelled exchange deposit
address (any chain) matched against that exchange's hot-wallet outflows
within hours, exact fee (B-3) preferred.

### D-7. A nightly analyst note (Claude API, Haiku 4.5)
One call a night over the day's alerts, roster diffs, demotions, feed gaps
and the top "next best measurements": a five-line note saying what changed
and what to look at. At Haiku prices this is cents a day, and it is the
only thing that reads *across* files the way a person would. It must never
alter a tier or an alert — advice only, rule 8.

### D-8. Copy the signal, not the wallet
The owner needs his *trades*, and an address is one way to get them. C-2's
tape detection yields his slice trains in real time without any address.
A "shadow book" — estimated positions rebuilt from tape-detected trains —
keeps the owner copying through the gap between his migration and our
identification of the new address, and its divergence from the target's
own book is itself the migration alarm. Experimental; confidence-labelled;
never presented as his book.

### D-9. The minimum viable catch, if only one session gets built
Three items, all cheap, covering the most probable route (HL-native funding
from the treasury or a test send, then trading):
**B-2** (novel destination at any amount → HIGH), **D-1** (treasury ledger at
watch cadence), **T1-4** (deposit-address sentinels). Then T3-3 to free a
watch slot. Everything else in this plan raises the ceiling; these three
raise the floor.

---

## Addendum E — architecture: add, change, remove

### Change

**E-1. Git is the archive, not the database.** Half of `CLAUDE.md`'s outages
are storage-in-git: the 100 MiB blob wall, push races, `-X theirs`,
concurrency groups, alert shards, sharded days, compaction, a 256 MiB cache
that counts the wrong bytes, `records_for` walking every file. Move the
substrate and graph into **one SQLite file** (WAL mode; `records_by_wallet`
becomes an indexed query; dedupe becomes a primary key; quarantine and
reprice become `UPDATE`s) synced to free object storage (Cloudflare R2 or
Backblaze B2, 10 GB free) and keep only small `latest.json` files in git for
the dashboard. Removes five subsystems and the whole class of lost-update
bugs at once. Largest change in the plan; do it after D-9, before T1-1.

**E-2. GitHub is the batch runner, not the tripwire.** The dispatcher saga
exists because Actions cannot keep a cadence. One always-on process on a
free VM owns everything fast — the websocket streams (T1-1/T1-6), the watch,
the collector — and pushes ntfy directly; Actions keep the heavy daily jobs
and the archive commits. Decide it once, in `docs/decisions.md`.

**E-3. Blockscout first, Etherscan second.** Blockscout serves every chain
we use keyless at ~10 req/s; Etherscan's free key is 5 req/s, refuses three
chains, and is the only thing a 100k/day cap can stall. Invert the order:
Blockscout for bulk sweeps, Etherscan for what Blockscout lacks. T2-3 stops
being a special case.

**E-4. Tiers named for the operator's action.** CONFIRMED/PROBABLE/POSSIBLE
describe our confidence; the operator needs *what to do*. Present four
states: **COPY NOW** (his, trading on HL), **HIS, NOT TRADING**, **WATCHING**,
**NOISE**. Both CONFIRMED wallets today hold ~$0 on HL and would read
"HIS, NOT TRADING" — which is the truth the current label hides.

**E-5. Tiered watch cadence instead of a cap of six.** Top three every run,
the next ten every third run, the rest of PROBABLE/POSSIBLE-with-HL-use
daily. A hard cap is a cliff; a cadence ladder degrades gracefully.

**E-6. One `thresholds` home.** `config.json` and a 532-line `thresholds.py`
both hold tunables. Merge into one declarative file with the *reason* and
the *measurement date* beside each number — the file already carries this
discipline in prose; make the numbers carry it too.

### Add

**E-7. Canary reads — rule 5 made systemic.** For every HL and chain
endpoint, read a known-busy stranger each run and assert a non-empty,
schema-matching answer. An empty target read is then provably "he did
nothing" rather than "the endpoint changed". The `extraAgents`/`webData2`
class of silent blindness becomes a HIGH alert about *us* within one run.

**E-8. Schema-drift tripwire.** Hash the key set of each endpoint's
response per run; a change alerts HIGH. APIs add and rename fields without
notice, and a parser that keeps returning `[]` is indistinguishable from
calm.

**E-9. Keep raw API pages for 30 days.** The recurring lesson — "the data was
there and nothing joined it", "the cursor advanced past what we threw away",
"neither fix recovers what was lost" — has one cure: retain the raw
responses (gzipped, cheap) so a parser fix is replayed rather than
re-swept. Every fix in this plan becomes retroactive for a month.

**E-10. A single blindness page.** `data/health/latest.json` and one
dashboard panel: per-feed cursor age, canary status per endpoint, API budget
used, dispatcher/stream heartbeat, gap ranges, last successful run per
workflow. The mission's failure mode is blindness that looks like calm; this
is the one page that says where we cannot see *right now*.

**E-11. A structured "measured no" registry.** `CLAUDE.md` records answered
questions in prose ("GCR cluster on HL: no, 120 calls, 2026-09-10"). Make it
a file with question, answer, date, cost, and a re-ask interval; show
staleness; schedule re-asks. Stops sessions re-deriving, and a "no" from a
week ago gets asked again automatically.

**E-12. An adversary model beside the route matrix.** For each route, the
cost to *him* of evading our detector (in money, effort, and visibility).
Prioritise by what a careful, visibility-averse trader would actually do —
he has told us he is one. Routes that are cheap to evade and cheap for us to
watch come first.

**E-13. Operator runbook.** What to do in the first ten minutes after each
CRITICAL type: which page to open, what confirms it, what to copy and what
not to. The deliverable is an address the owner acts on; the acting part
has no documentation.

### Remove

**E-14. Email.** Brevo has never delivered once (1,947 failures) and still
pollutes delivery health and `last_failure_reason`. Delete the SMTP path and
`smtp-smoke-test.yml`; ntfy + GitHub Issues + Telegram are the channels.

**E-15. The three `Ezekiel-backup-*-2026-07-27` directories** at the repo
root, if tracked or even if merely present — they confuse every grep and
every reader. Verify gitignore, then delete.

**E-16. The leaderboard top-500 behavioural sweep as a candidate
generator.** `CLAUDE.md` says it finds a new wallet "late or never"; it is
the largest module (1,962 lines) and the heaviest job. Keep the scorer as a
*validator* on candidates other vectors produce, cut the sweep to newborns
plus the roster, and give its budget to the tape/block/newborn generators.

**E-17. `profile_builder.py` / `trader_profile.json` / `PRD.md`** if nothing
reads them — `CLAUDE.md` already says nothing does. Move to `research/` or
delete; the repo should contain only what serves the mission.

**E-18. INFO alerts as a delivery concept.** They are already withheld from
every channel; stop generating them as *alerts* at all and write them as
dashboard rows. Removes a whole branch of health logic (suppressed counts,
withheld vs undelivered) that exists only to cope with them.

---

## Addendum F — the adversary reads GitHub, and other expert points

### Remove / change

**F-1. The repo is public, and it is the tracking playbook.** Every
sentinel, the watch list, the roster, the thresholds, the blind spots and
the exact routes we do not watch are readable by anyone — including a
visibility-averse trader who does on-chain sleuthing himself. An adversary
who can read the detector evades it for free. Make the repo **private** and
move the data-bearing files out of any public surface (the dashboard reads
`raw.githubusercontent.com`, so it must move to a token-authenticated
source or a private Pages deploy). Private Actions minutes are limited
(2,000/month free), which is one more reason for E-2. At minimum, stop
publishing the roster, the watch list and the sentinels today.

**F-2. The target is a set, not an address.** `config.target_wallet` is one
value and every module hangs on it, so *catching* a migration is followed
by a manual, error-prone re-point of the whole system. Make the target the
**cluster** (target + known self + promoted wallets): collector coverage,
fingerprint, dormancy calibration, risk and the watch all iterate over it.
Migration then becomes "add a member", not "re-target the project".

**F-3. A promotion procedure.** `scripts/promote_wallet.py`: adds a wallet
to the cluster, starts collector coverage, seeds its fingerprint from its
own fills, moves it from the watch into the target set, and writes the
decision with its evidence. Then a **continuity check**: the promoted
wallet's style must self-match against his (the backtest, unchanged) before
the owner copies it — so we never re-target onto a copier.

**F-4. Shadow mode for every new vector.** A new detector runs silent for N
runs — logged, no tiering, no alerts — and reports what it *would* have
done. Every CRITICAL flood in `CLAUDE.md` (39 contacts in six seconds, the
new-dex false alarm, the ELEVATED that could not route) would have been a
log line instead. One decorator, applied to every item in this plan.

**F-5. Rules as data.** `alerts.py` is 1,524 lines of hand-written tripwires.
Express each as a declarative rule (condition over fields, severity,
cooldown, subject template) in YAML, with a generic evaluator and
schema-generated tests. Adding a tripwire becomes a config change, the
severity vocabulary is validated by the schema, and the rule set is
reviewable in one file.

**F-6. Incremental graph, not a rebuild.** `collect_known_edges` rebuilds
every edge from the substrate on every run (42.7s and growing). With E-1
the graph is a set of queries and only new records are folded in.

**F-7. Community detection beside the BFS.** A depth-4 walk finds *paths*;
his cluster is a *community*: shared gas funders, shared deposit addresses,
shared approval spender sets, synchronised bursts. Run label propagation or
Louvain over the substrate periodically, take the community containing the
cluster, and filter to HL-active members. Finds wallets the walk's budgets
never reach.

### Add

**F-8. "Suspend copying" — the dead-wallet declaration.** The mission's
failure is "following a dead wallet". Nothing declares one dead. Define it
on his own rhythm (silence beyond p99, value below X% of peak, book
unwound, unexplained outflow rising) and alert **SUSPEND COPYING** as
CRITICAL — the one alert whose action is to stop, not to start.

**F-9. Compromise vs migration.** A hacked account drains to a new address
with a sudden style change — the same shape as a migration, with the
opposite meaning: the "new wallet" is a thief's. Tell them apart (drain speed,
approval of an unknown agent minutes before, sells into thin books, no test
transfer) and say which in the alert; either way the owner stops copying.

**F-10. Sentinels discovered, not configured.** The graph already infers
deposit addresses (an EOA forwarding ≥99% to a hot wallet within minutes).
Exchanges rotate deposit addresses after inactivity, so a hand-kept
sentinel list goes stale. Feed every inferred cluster deposit address into
T1-4 automatically, with the inference evidence attached.

**F-11. His complete history from node data.** The API's 2,000-record cap
means his fills before 2026-02 are gone from our store; the public node
archive (A-9) has every action since 2024-02. One backfill of the *target
alone* doubles the behavioural window, gives C-4 real data on his previous
migration, and costs a few dollars of egress. Do it before any scorer work.

**F-12. Self-anomaly detection on run summaries.** Roster size ±20% in a
run, edge count halving, a feed's records/day dropping to zero, a
detector's candidates_considered falling to 0 — each has already happened
here and was found by a person reading a log. Track every run-summary
number as a series and alert HIGH (ours) on a step change.

**F-13. Property tests on every parser.** Hypothesis-generated inputs
asserting the invariants the rules state: a failed read never returns the
empty-success shape (5), a missing value is never 0 (6), a token is keyed on
its contract (2). The rules are prose today; make them executable across
every parser at once.

**F-14. A weekly "what we are not watching" review.** Automated: the route
matrix with measured recall (C-8), the blindness page (E-10), the stale
"no"s (E-11), the vectors with zero ROI (D-5), rendered as one page and
pushed to the operator on Mondays. The project's history is of blind spots
found by accident; make finding them a schedule.
