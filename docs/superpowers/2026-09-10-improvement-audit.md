# Ezekiel — improvement audit and roadmap (2026-09-10)

Everything below was checked against the repository, the data on disk, and the
live public APIs (Hyperliquid info + explorer, Blockscout, Solana RPC, HyperEVM
RPC) on 2026-09-10. Items marked **measured** were verified this session; items
marked **idea** were not built or tested. Nothing was committed and no code was
changed.

The mission is unchanged: a Hyperliquid address is the only deliverable. This
document is ordered by how much each item changes the odds of catching a
migration, not by how interesting it is.

---

## Part 1 — What is wrong today (correctness, ordered by impact)

### 1.1 The address-reuse vector is counting shared *contracts* as private deposit addresses — measured

The strongest single signal in the doctrine ("a CEX deposit address belongs to
one account") is implemented in `linkage.get_outbound_addresses`, which
excludes only registry categories, config addresses and the bridge. It does not
consult `data/labels/code_cache.json`, and `high_fanin_addresses` counts senders
only *within the substrate*, which only holds swept wallets. A global router
therefore looks like a five-sender private address.

Blockscout identities of the target's "shared deposit addresses":

| address | what it is | global size |
|---|---|---|
| `0x3a23f943…` | **SocketGateway** (bridge aggregator) | 2.19M txs |
| `0x724dc807…` | **Aave aToken proxy** (aArbUSDC) | 3.2M transfers |
| `0xc4922d64…` | **CCTP TokenMinter** (Ethereum) | 970k transfers |
| `0x6a000f20…` | **Paraswap AugustusV6** | 1.0M txs |
| `0x63242a4e…`, `0xf081470f…`, `0x000010036c…`, `0x0f4a1d7f…`, `0x365084b0…`, `0x28104d4f…` | unlabelled contracts (exchange sweep contracts, 0.3M–8.2M transfers) | — |

Consequences:

- The treasury `0x1419e75…` is CONFIRMED partly on `linkage` from these shared
  contracts. It is still his (two-way HL-native flow, `usdSend`/`sendAsset`
  from it to the target are observed acts), but the linkage vote is spurious.
- `0x84abc08c…` is PROBABLE on `linkage` because it and the target both touched
  the CCTP TokenMinter. It is an HL bot account (2,000 fills, $0 value, $9
  test deposits) that once received $999,999.80 from the target.
- The **one genuine shared private deposit address is invisible**:
  `0x8570c2ae…` is an EOA that forwards 100% of $230.9M straight to Binance hot
  wallets — the exact `infer_deposit_addresses` signature — and receives from
  the target ($58.3M) and from `0xf078969e…` ($114.8M). The system files it as
  "conduit: infrastructure" and never treats co-senders as linked.
- `0xf078969e…` (EOA, personal DeFi wallet, 84 senders / 130 recipients) and
  `0x160f6ef9…` (EOA, sent the target **$100.56M** directly) are graded
  INFRASTRUCTURE by the fan-degree rule, so they score 0.0, never alert, are
  never traversed, and are excluded from linkage. An active DeFi wallet
  naturally has dozens of counterparties; 25/25 is far too low a bar, and the
  rule does not distinguish contract counterparties from people.

Closed loop on HL (measured): `0xf078969e` is an HL `user` with $54, 2 fills
and 246 inbound airdrop spotTransfers; `0x160f6ef9` and `0x236f233d…` are
`missing`. None trades on Hyperliquid today — but they are the cluster, and they
are the wallets that would fund a new account.

**Fix (bounded):**
1. Linkage excludes every address the code cache says has bytecode, plus any
   address whose global transaction count (Blockscout counters, one call,
   cached forever) exceeds a few thousand.
2. `detect_services` fan degree counts only *EOA* counterparties carrying
   value, and an EOA is a service only above a much higher bar (hundreds), or
   when its global tx count says so.
3. Wire `infer_deposit_addresses` (already written, never called) so
   `0x8570c2ae` is labelled `cex_deposit`, then treat its co-senders as
   `shared_deposit_address` evidence.
4. Add `0xf92402bb…` (the target's first Arbitrum funder: an unlabelled hot
   wallet with 2.28M transactions) to `entities.json` as `cex_hot`, or
   `shared_funder` will match every wallet that exchange ever gassed.

### 1.2 The ledger is stored three times and fills are 13.5% duplicated — measured

`data/ledger/` holds 1,430 rows for **500 unique hashes** (copies in the
2026-02-20, 2026-02-21 and 2026-07-03 files). `data/fills/` holds 188,299 rows
for 162,818 unique `tid`s. `compact_data.py` dedupes `orders`, `fees` and
`rate_limit` only; fills/ledger are "never touched" and `load_all_records` does
not dedupe.

Effects, all measured by re-running on unique rows:

| figure | reported | real |
|---|---|---|
| treasury HL-native out / in | $76.9M / $92.6M, 69 transfers | **$25.6M / $36.3M, 28** |
| `0x6b9e7731…` inbound | $79.5M, 23 | **$66.5M, 18** (exactly the Arbitrum CCTP total, see 1.9) |
| correlator exits | 436 withdrawals | **146** |

Every exit is offered to the FIFO matcher three times; per-fill statistics
(fills per active day, clip sizes, hold reconstruction) are skewed by which
days happen to be duplicated, and the two backtest windows are not affected
equally (older window 25,833 fills, recent 63,511). Rebuild the fingerprint and
re-run the self-match after fixing this before touching the scorer.

**Fix:** dedupe by `tid` / `hash` inside `load_all_records` for `fills`,
`ledger`, `funding` (exact-key, lossless), and extend the daily compaction to
drop the cross-file copies.

### 1.3 A worthless spot token is booked at its quantity as dollars — measured

`normalise_hl_ledger_entry._usd()` tries `usdc`, `usdcValue`, then `amount`.
A spotTransfer of **1,030,689,918 MAX** with `usdcValue: "0.0"` therefore
enters the graph as **$1.03B**, and `0x207700bd…` sits in the roster as a
POSSIBLE lead that "sent the target $1,030,689,918". `usdcValue` is HL's own
valuation; `"0.0"` is a real zero for a token, never a reason to fall back to
the raw quantity.

### 1.4 "He has authorised no agents" is false — measured

`extraAgents` returns `[]`, but that endpoint only lists *named* extra agents.
`webData2` returns `agentAddress: 0x98cf3fee01cb61905a79b63c4d7662cc638c72e2`
(valid until 2026-09-17), and the explorer shows the `approveAgent` on
2026-08-31 signed with `signatureChainId 0xa4b1`. `userRole` on that address
answers `{"role": "agent", "data": {"user": <target>}}` — so agent → owner
resolution works and can be applied to any address. The treasury has approved
**18** unnamed agents since 2024-11 (each a browser session or device); those
older agents resolve as `missing`, so only the current agent resolves.

The agent baseline in `data/agents/` and the "target gained an agent" alert
both need `webData2.agentAddress` (and the explorer's `approveAgent` history),
not `extraAgents`.

### 1.5 Dormancy scores the wrong "first active day" — measured

`userFillsByTime(startTime=0)` returns the **oldest 2,000 of the ~10,000
retained fills** (verified: 2026-08-18 for the target), not the wallet's first
fills. For a bot doing 10k fills a week, "first active day" is always last
week, so a handoff can fire on wallets that have traded for years. `portfolio`
(one call) returns the all-time account-value series whose first non-zero
point is the true birth (target: 2024-01-03; `0x84abc08c`: 2025-07-24).

### 1.6 The scanner's bridge-depositor source still reads one page — code

`scanner.get_recent_bridge_depositors` uses `offset=1000, sort=desc` — the
bug already fixed in the correlator (that page covers hours on the busiest
contract on Arbitrum, not 30 days).

### 1.7 HIP-3 positions are excluded from the rarity-weighted overlap — code

`check_portfolio_overlap` and `scanner.get_candidate_state` call
`clearinghouseState` without `dex`, so `xyz:` books are absent on both sides.
The rarest, most informative positions never enter the overlap score.

### 1.8 "He does no DeFi" is false, so the approvals rejection stands on a wrong premise — measured

$152.6M in 67 transfers went to the Aave USDC aToken on Arbitrum; $1.7M
through Paraswap on Ethereum; $320M through Socket. He approves Aave, Socket
and CCTP. Approval fingerprints were rejected because "his footprint is
exchange deposits and the bridge"; that is not what the substrate shows.

### 1.9 Where the money goes is misdescribed — measured, and it resolves the "$13M question"

Decoding the calldata of his bridge transactions on Blockscout (no key) gives
the destination chain and recipient for every CCTP transfer (51 of 52 decoded):

| route | n | amount | recipient |
|---|---|---|---|
| CctpExtension → HyperEVM (domain 19) | 18 | $66,461,024 | forwarder `0xb21d281d…`, hookData `"cctp-forward" + <target address>` |
| CCTP v1/v2 → **Solana** (domain 5) | 23 | $22,751,990 | `2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv` |
| CCTP v2 → Ethereum (domain 0) | 10 | $9,999,999 | the target's own address |
| SocketGateway (175 txs, $320M) | — | — | receiver decodes to the target's own address |

So the $66.5M "to infrastructure `0xa95d9c1f`" is the target depositing into
**his own Hyperliquid account** via Circle CCTP; it arrives as the 18 `send`
events from the USDC EVM contract `0x6b9e7731…` (same count, same total after
dedupe). The RESUME-HERE "$13M question" is answered: it came back to him.

The Solana address is a cluster member the project has never seen. It is alive
(last signature 2026-08-17, dust SOL, no USDC left), and Solana → Hyperliquid
via CCTP domain 19 is a funding path for a brand-new account.

Unresolved: the **$23M** sent Core → HyperEVM (five `send`s to `0x2000…0000`)
is not at his address as Circle USDC (`balanceOf` = 0) while his HyperEVM nonce
is 0. Neither fact alone is wrong; together they say the money moved without a
transaction from him, or landed at a contract we have not identified. Neither
Blockscout instance indexes HyperEVM; Etherscan V2 `chainid=999`
(hyperevmscan) needs the key and should be tried from CI.

### 1.10 Graph wallets are never asked whether they trade on Hyperliquid — code

`trades_on_hl` comes only from `candidates/latest.json` (leaderboard scans),
so for every wallet the graph discovered itself the flag is false, the
`funded_before_trading` continuity signal cannot fire, and conduit detection's
`never` exemption cannot protect it. `check_gcr_wallets.hl_state` already does
the right probe; it is applied to 11 GCR addresses and to nobody else.

### 1.11 Documentation drift — measured

- CLAUDE.md says on-chain history starts 2026-02-05. The HL account's ledger
  begins 2024-02-29 (first $10k deposit) and the portfolio series on
  2024-01-03; collection began 2026-02-20.
- The memory note calling `0x3a23f943` a shared deposit address (and the
  treasury's "five shared deposit addresses") describes shared contracts.
- `roster/latest.json` reports `0xf078969e` and `0x160f6ef9` as
  INFRASTRUCTURE; they are personal wallets.

### 1.12 Alert delivery — measured

172 consecutive email failures as of 13:03 UTC today; only CRITICAL/HIGH reach
the GitHub-issue fallback. Everything INFO (every new graph node, every
counterparty) is written to disk and read by nobody.

---

## Part 2 — New vectors, verified feasible live (ordered by expected value)

### 2.1 An action ledger from the Hyperliquid explorer — measured

`POST https://rpc.hyperliquid.xyz/explorer {"type":"userDetails","user":…}`
returns the last 300 L1 actions with full payloads. The info API's ledger has
no destinations and no approvals; this has both. Observed on the treasury:

- `approveAgent` × 18 (agent address, `signatureChainId` 0xa4b1 / 0x1 / 0x66eee)
- `withdraw3` × 4 **with `destination`** (all to itself)
- `spotSend` / `sendAsset` / `usdSend` with destinations (HYPE and USDC to the
  target), `vaultTransfer` × 11 (HLP, deposits up to $21.5M),
  `tokenDelegate` × 6, `cDeposit` / `cWithdraw`, `agentSetAbstraction`,
  `usdClassTransfer`, one `cancel`
- third-party actions that mention the user (validator deposit votes, inbound
  airdrop `spotSend`s)
- failed actions with the error text ("Cannot switch leverage type with open
  position" — a human clicking, not a bot)

Not observed but in the same schema: `createSubAccount`, `subAccountTransfer`,
`setReferrer`, `approveBuilderFee`, `evmUserModify`, `linkStakingUser`.

Build: poll every collector run for the target, the treasury and every roster
wallet; persist every non-`order` action keyed by hash; alert on `withdraw3`
to a foreign destination, `approveAgent`, `createSubAccount`, `vaultTransfer`
to a non-HLP vault, `approveBuilderFee`. The window is 300 actions (~4 hours
on his busiest days), so the L1 join in 2.4 remains the backstop for
withdrawals.

### 2.2 Identity-resolving endpoints applied to *every* address — measured

| endpoint | answers | verified |
|---|---|---|
| `userRole` | user / agent (→ owner) / vault / subAccount (→ master) / missing | target=user, his agent→target, funder=missing |
| `webData2` | `agentAddress`, `agentValidUntil`, `leadingVaults`, `isVault`, `twapStates` | target's frontend agent found |
| `userFees.stakingLink` | an explicit link between a staking wallet and a trading wallet | null for both cluster wallets |
| `delegations` / `delegatorSummary` | validators and amounts | target 103,624 HYPE on ValiDAO + Nansen×HypurrCollective; treasury the same two (+2), ~504k HYPE |
| `portfolio` | all-time value series → account birth date | works for any address |
| `leadingVaults`, `vaultDetails` | vaults led / leader + followers | HLP details returned |

One function, `hl_identity(addr)`, run for every graph node, co-depositor,
first funder, bridge recipient and leaderboard newcomer. `userRole=agent`
gives the owner of any candidate address; `subAccount` gives the master;
`stakingLink` is a deliberate act of control like an agent and should be a
CONFIRM-alone vector.

### 2.3 Bridge calldata decoding — measured

Blockscout's `/api/v2/transactions/{hash}` returns `decoded_input` for verified
contracts, with no key. For CCTP it names `destinationDomain`, `mintRecipient`
and (for the Hyperliquid extension) a `hookData` blob containing the credited
HL account. This turns "trail ends at a bridge" into "arrived at address X on
chain Y", and for domain 19 it is the exact tripwire for a **new HL account
funded by CCTP** (today 18/18 name the target). Socket routes decode to a
receiver with the route ABI (route ids 0x18c and 0x1aa observed; receiver =
target in the sampled transactions).

Build: for every target/cluster transfer into a known bridge contract, decode
and store `(chain, recipient)`; alert when the recipient is not a cluster
address; feed the recipient to 2.2.

### 2.4 Withdrawal-destination join — measured

HL `withdraw3` can name any Arbitrum destination, and the ledger `withdraw`
delta carries no destination. Joining each unique withdrawal (`usdc − fee`,
±6h) to the bridge's outbound USDC transfers recovers it. Today **146 of 146**
withdrawals ($440.9M) land at his own Arbitrum address. A withdrawal with no
match is money leaving to an address the tracer never sweeps, and that is the
migration path the current tracer is blind to.

### 2.5 Newborn-whale detection from the leaderboard — measured

The leaderboard returns 45,005 rows with per-window volume. An account whose
all-time volume equals its month volume was born within 30 days; no per-wallet
call is needed. Today: **84 accounts ≥ $1M born in the last 30 days**,
including the correlation lead `0xdd53c529…` ($51.6M). Diff consecutive
snapshots (hourly) for first appearances, rank by size and birth recency,
fingerprint only those, and cross with dormancy. This is the only cheap net
that catches a fresh wallet *before* it is large enough for the top-500 sweep.

### 2.6 Co-depositor enumeration on private exchange deposit addresses — measured on data

For each destination labelled `cex_deposit` (after 1.1), sweep its *inbound*
side and list every other sender. `0x8570c2ae` already yields `0xf078969e`
($114.8M). Any co-sender is the same exchange customer or someone paying him;
run 2.2 on each. Alert on a *new* co-sender appearing after today.

### 2.7 A Solana tripwire — measured

`getSignaturesForAddress` on `2xm4bb8K…` (public RPC, no key) once per run.
Alert on any new signature; decode CCTP `depositForBurn` to domain 19 or a
Hyperliquid deposit program call, then run 2.2 on the recipient.

### 2.8 Lead/lag co-movement: the copycat-proof behavioural vector — idea

Portfolio overlap cannot separate him from his copiers. Timing can: a copier
acts *after* the target's fills; his own second account acts *with or before*
them. Build per-coin, per-direction event pairs (target episode opens vs
candidate fills), measure the lag distribution, and calibrate the base rate on
the 500-wallet sweep. A candidate whose median lag is ≤ 0 with many pairs
cannot be a copier; it is either him or a shared signal source.

### 2.9 Tooling and environment fingerprints — measured on the target, idea for candidates

Durable, cheap, and orthogonal to fills:

- `signatureChainId` on user-signed actions: target 0xa4b1; treasury mixes
  0xa4b1, 0x1 and 0x66eee (the Python SDK default). Which network the wallet
  extension sits on is a per-human habit.
- `userRateLimit.nRequestsUsed / cumVlm`: target 282,208 requests for $689.7M
  (≈0.41 per $1k). One call per candidate, no history needed.
- Order-slice cadence: the explorer shows Ioc slices of a fixed size every
  1–10 s, 94.5% Ioc Limit, 5.5% `FrontendMarket`, 0 cloids, 0 cancels — a
  script on the frontend agent key plus occasional manual clicks.
- Staking validator set and delegation timing; `optOutOfSpotDusting`;
  `approveBuilderFee` (none today — any builder approval would name the app).
- Spot-account shape: $51.7M USDC left idle in spot, 361k PURR, airdrop dust.

### 2.10 Human-chosen names on the leaderboard and in vaults — measured

1,441 leaderboard rows carry a `displayName`. The naming-family regex finds
one hit, "ACLGCR" (`0x49137568…`, empty account, coincidence). Re-run each
sweep; add `vaultSummaries` names and referral codes.

### 2.11 HyperEVM through Etherscan V2 `chainid=999` — idea, needs the key

If hyperevmscan is served by the V2 key, HyperEVM becomes sweepable and the
$23M question closes. Test from CI; the keyless probe returns "Missing/Invalid
API Key" so the answer cannot be had locally.

### 2.12 Vault-leadership watch — idea

Diff `vaultSummaries` for a leader in the cluster; `userRole=vault` on any
candidate returns its leader. A vault he leads is a tradeable address the
owner could follow directly.

### 2.13 Blockscout as a first-class client — measured

Arbitrum and Ethereum answer without a key: address counters (global fan-in),
labels, decoded inputs, per-address transfers. This closes the "base, bsc,
optimism are unreadable" gap the README records and supplies the global
transaction counts 1.1 needs.

### 2.14 Every migration path, and what covers it

| how he could move | covered today | after Parts 1–2 |
|---|---|---|
| HL-native `usdSend` / `sendAsset` to a fresh account | ledger analyser (totals ×3) | same, deduped, plus explorer destinations |
| `withdraw3` to a foreign Arbitrum address | not visible | 2.4 join + 2.1 explorer |
| CCTP / Socket to a fresh address, then deposit | trail ends at "infrastructure" | 2.3 decode → 2.2 identity |
| CEX round trip | correlator (exits ×3) | correlator on unique exits + 2.6 co-depositors |
| Solana → HL via CCTP | not visible | 2.7 |
| HyperEVM route | nonce tripwire | 2.11 if the key serves chain 999 |
| sub-account | `subAccounts` (null) | + `userRole` on candidates + explorer `createSubAccount` |
| vault he leads | not checked | 2.12 |
| fresh wallet funded by an unknown source | dormancy + top-500 sweep (late) | 2.5 newborn + 2.8 lead/lag |
| second account run in parallel today | nothing (copycats indistinguishable) | 2.8 |

---

## Part 3 — Behavioural scorer: independent signal without touching weights

The doctrine forbids reweighting to pass the backtest. These add dimensions
that do not exist today, each measurable for candidates in one or two calls:

1. Requests-per-volume (`userRateLimit`).
2. `signatureChainId` mix and agent-approval cadence (explorer).
3. Intra-session slice cadence and size constancy (explorer order actions).
4. HIP-3 book shape: isolated-vs-cross per dex, and the `xyz:` positions
   themselves (1.7).
5. Session structure: start hour per active day rather than a 24-bin histogram
   (his `timing_profile` self-scores 0.26; the histogram is the weakest
   dimension because a TWAP session smears fills across hours).
6. Re-run the self-match after 1.2; the current margin (+0.0369 vs +0.05) was
   measured on windows with unequal duplication.

---

## Part 4 — Latency and delivery

- **Cadence:** GitHub honours ~5% of the cron. A free external scheduler
  (cron-job.org → `workflow_dispatch` with a fine-grained token) restores real
  15-minute runs. The README calls external services a non-goal; the mission
  says detection latency matters more.
- **Delivery:** a Telegram bot or ntfy topic is free, needs no activation, and
  delivers in seconds. Keep GitHub issues for CRITICAL. Send a daily digest of
  INFO so the roster's churn is at least seen.
- **Explorer window:** 300 actions is ~4 hours on an active day; polling on
  every collector run keeps the action ledger complete.

---

## Part 5 — Measured and not worth building

- HyperEVM log windows around known ledger times: the EVM→Core credit is not
  a plain ERC-20 `Transfer` to the system address (0 logs in a 1,000-block
  window around a $3.5M credit), so this route cannot recover senders. Use 2.3
  instead; the recipient is in the Arbitrum calldata.
- Hypurrscan API: no public endpoints answered (404 on every guess).
- Blockscout for HyperEVM: no instance at the expected hosts.

---

## Part 6 — Suggested order

1. **Integrity first (a day):** dedupe fills/ledger at read; fix the token
   quantity-as-dollars fallback; fix the scanner's single-page bridge read;
   label `0xf92402bb`; rebuild fingerprint and roster; re-run the backtest.
2. **Fix the strongest vector (a day):** linkage excludes bytecode and
   globally-busy addresses; fan-degree over EOA counterparties only; wire
   `infer_deposit_addresses`; regrade `0x8570c2ae`, `0xf078969e`, `0x160f6ef9`.
3. **Close the loop everywhere (a day):** `hl_identity()` from 2.2 on every
   address the system touches; `webData2` agent baseline; `stakingLink` as a
   vector; `portfolio` birth dates for dormancy.
4. **New sight lines (two to three days):** explorer action ledger (2.1),
   bridge calldata decoding (2.3), withdrawal join (2.4), newborn-whale
   detector (2.5), Solana tripwire (2.7).
5. **Then** co-depositor enumeration (2.6), lead/lag co-movement (2.8),
   tooling fingerprints (2.9), vault watch (2.12), chain-999 test (2.11).
6. **Alongside:** Telegram/ntfy delivery and the external scheduler.

Each step is independently testable offline with fixtures in the existing
style, and none changes a scoring weight.
