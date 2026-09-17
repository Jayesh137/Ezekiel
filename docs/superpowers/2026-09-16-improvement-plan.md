# Ezekiel — Improvement Plan, 2026-09-16 (refined, then checked against the code)

**Mission:** find this trader's other Hyperliquid wallets and catch the moment he
moves to a new one. Every item is judged on one question — *does it make an
undetected migration less likely?* Read `CLAUDE.md` first; its rules bind
everything here, above all 5 (a failed read is never a clean result), 6 (never
price a missing value as 0), 9 (a shared address counts only once the whole
chain says it is quiet), 4 (never tune the validator) and 8 (a transfer is not
ownership).

## Read this first: the plan was wrong in places, and that is recorded

This plan was drafted from the documentation, then every Phase-1 claim was put
to the code and the live data before anything was built. Several were wrong.
The corrections are the most useful part of the document, because they stop the
next session building on them.

| Item | Draft claim | Measured |
|---|---|---|
| P0-1 make repo private | "today, no build" | **Kills the pipeline.** 795 Actions minutes in 24h (scan 280, trace 221, watch 120) against 2,000/month on a private free plan — about 2.5 days. Needs the fast path off Actions first (P3-1). Operator decision. |
| P0-4 delete backup dirs | repo clutter | **Gitignored local copies**, 733 MB+ each. Harmless to the repo and irreversible to delete — the operator's call, removed from the plan. |
| P1-1 "a $10 test send is invisible" | top gap | **Wrong for the target and treasury.** `collector.collect_actions` reads the explorer for target + `known_self_wallets` every run and alerts CRITICAL on any `usdSend`/`spotSend`/`withdraw3`/`sendToEvmWithData`/sub-account/vault/agent action to an outside address, at any amount. The tracer alerts CRITICAL on every new L1 outbound of the target at any amount. The real gap is narrower — see P1-1 below. |
| P1-2 treasury has no collector coverage | high | **HL half already built** (same `collect_actions`). L1 half merged into P1-1. |
| P5-5 behavioural vector not voting | a gate bug | The gate worked (the roster was built 7 minutes before the backtest passed). The real defect was worse — see DONE-2. |
| A-4 `userRateLimit` fingerprint | new idea | **Already built** as `scanner.tooling_fingerprint`. |
| P2-2 decode CctpExtension deposits | new | **Decoder already exists** (`chain/bridges.decode_cctp_extension` returns the credited `hl_account`); only the cluster is fed to it. |

## Done on 2026-09-16 (tests, replayed on live data, in CLAUDE.md)

- **DONE-1 Shared funder measured (rule 9).** The target's first funder
  `0xf92402bb…` has 2,282,986 Arbitrum transactions and was never measured; five
  wallets held a linkage vote for sharing it. `linkage.substrate_linkage`
  measures the funder first; `roster.funder_exclusions` requires a measured quiet
  funder. `tests/test_shared_funder_measured.py`.
- **DONE-2 Only the validated scorer's scores decide.** All seven wallets casting
  a behavioural vote did so on single scans from 2026-06-30/07-01, under a scorer
  the backtest never validated. `persist_candidate` stamps `scoring_schema`;
  `utils.candidate_scored_by_current_scorer` gates the roster vote, risk's top
  candidate, graph evidence and the tracer's combined alert.
  `tests/test_stale_scorer_cannot_vote.py`.
- **DONE-3 `transfer` means money moved with him.** 110 rows voted `transfer` at
  depth 2 with $0 direct flow; two PROBABLE rows were a market maker's accounts
  (referral `MMREFCSI`, agents `XYZ_SET11`/`APTS`). `roster.transfer_touches_cluster`.
- **DONE-4 Close watch ranks on evidence, one slot per operator.**
  `watchlist.watched` uses `roster.rank_key`; sub-accounts of a watched master
  take no slot.

Replay: PROBABLE 7 → 0, POSSIBLE 140 → 25, WATCH 41 → 159, CONFIRMED unchanged.
**Verified in production** (trace run 35149575324, 21:04 UTC): CONFIRMED 2,
PROBABLE 0, POSSIBLE 26; the graph step logged the funder as busy; risk fell
57.0 ELEVATED → 35.2 GUARDED once the June score stopped earning 22 points.

- **DONE-5 Private-deposit-address sentinels (was P1-3).** `src/deposit_sentinels.py`,
  `scripts/check_deposit_sentinels.py` in watch.yml. Live: sentinels `0x8570c2ae…`
  and `0x499662e0…`; `0xda0932d2…` now POSSIBLE on linkage from this file.
- **DONE-6 Roster leads re-scored (was P1-6).** `scanner.roster_rescore_targets`.
- **DONE-7 New payee of his (was P1-1, re-scoped).** `check_watchlist.check_new_payees`;
  first CI run seeded 615 addresses and alerted nothing.
- **DONE-8 Bridge decoding for CONFIRMED wallets, Monad.** The full Circle domain
  table; `0xf078969e…` sent **$31.8M to Monad (domain 15)** 2026-08-15 → 09-15 and
  holds 0 USDC there (218K MON, 18 txs); Monad joined `config.chains` with its USDC
  contract registered first. Foreign bridge landings now alert once, not every 72h.
- **DONE-9 Test isolation.** Linkage caches sandboxed; graph pipeline tests no
  longer call Blockscout (suite 171s → 90s).

## Done on 2026-09-17 (operator delegated all remaining decisions)

- **DONE-10 $84.6M of counterfeit value stopped.** Polygon "USDT" (15 contracts, $54.3M)
  and ERC-20 "ETH" on ETH-native chains (25 contracts, ~$30.5M) were priced as real;
  quarantined in production by analyze.yml: 316 records, $84,635,116.82.
- **DONE-11 `0xf078969e…` in `known_self_wallets`,** with the hop-through-a-config-wallet
  transfer vote now requiring >= $1,000 of valued flow (`self_flow_usd`) — 56 of the
  72 wallets beside it were $0 poisoners.
- **DONE-12 `config.owner_wallets`.** The operator's own copy-trading accounts are
  refused by every candidate path (scan, priority set, candidate files, backtest
  lineup, roster). Ships empty: **paste your wallet addresses into it.**
- **DONE-13 Circle flows, both ends (supersedes P2-1 and P2-3).** Circle's
  MessageTransmitterV2 events on HyperEVM name source chain, source sender and
  credited account for every deposit, and withdrawing account plus recipient for every
  withdrawal. CRITICAL when his wallet funds an outside account, an outside account pays
  him, or his account withdraws outside. Etherscan chainid 999 in CI, RPC fallback.
- **DONE-14 Detector freshness.** `src/feed_health.py`, checked crosswise by watch.yml
  and trace.yml; HIGH when a feed stops.
- **DONE-15 Dashboard Tripwires page.**

**Measured and rejected on 2026-09-17** (do not rebuild):
- *Clock-skew fingerprint from action nonces (P3-5):* the explorer's ORDER rows carry no
  nonce; only wallet-signed actions do, and those include human confirmation delay
  (17.2s on his Circle withdrawal). Not buildable from this API.
- *Market-maker classification (P1-7):* after DONE-1..3 only 4 POSSIBLE rows rest on
  dormancy alone, 2 of them the market maker's; not worth a classifier today.

**Remaining, in order:** P3 real-time stream (needs a host decision), P4 self-cross /
OI conservation, P5-1 likelihood ratios, P5-9 find his previous account.

## Route matrix additions (found by measurement, 2026-09-16)

| # | Route | Watched now |
|---|---|---|
| R20 | A CONFIRMED wallet bridges to a chain the substrate does not sweep (Monad, Unichain) | decoded and named (DONE-8); the far side is swept only if Etherscan's free tier serves it — read `unsupported_sources` |
| R21 | A fresh wallet pays his private exchange deposit address | DONE-5 |
| R22 | His treasury or `0xf078969e…` pays a never-seen EOA | DONE-7 |

**Next, in order:** (1) follow the $31.8M inside Monad — if chain 143 is refused
on the free tier, scan `eth_getLogs` on rpc.monad.xyz (capped at 100 blocks) in a
window after each known mint for USDC leaving `0xf078969e…`, and ask Hyperliquid
about every recipient; (2) P1-7 market-maker classification; (3) P2-1/P2-3 Circle
source and Solana burn decoding.

---

## Phase 1 — next, highest value per hour

**P1-3. Private-deposit-address sentinels. VALIDATED by a real find.** A CEX
deposit address belongs to one account. The only outsider ever to pay his
Binance deposit address `0x8570c2ae…` is `0xda0932d2…` ($249,993.84,
2024-07-31, with a $6 Hyperliquid deposit the same day), and nothing surfaced it:
the address is graded a conduit SERVICE and linkage reads only swept wallets'
OUTBOUND, while the deposit address's own sweep holds every sender. Build:
- Identify private deposit addresses: paid ≥ $1k by a config-cluster wallet,
  forwarding (conduit grading or `inferred_deposits.json`), EOA, **measured quiet**
  (rule 9 — a shared memo-style deposit address is busy).
- Re-sweep each in the watch job (one cheap call per chain; the treasury paid
  `0x8570c2ae…` on 2026-08-15, so it is live).
- New non-cluster, non-spam sender above dust and measured quiet → **CRITICAL**,
  with the sender's HL state in the alert, and into the roster as `linkage`
  from its own detector file (never only via the graph — the lesson of
  `linkage_from_first_funders`). Senders before the baseline are recorded as
  roster evidence without an alert (old news is not breaking news).
- Filter address-poisoning: zero-value/spam records and vanity look-alikes
  (`0xf0775c88…719e` imitating `0xf078969e…`) are not senders.
*Verify:* historical replay lists exactly target, treasury, `0xf078969e…` and
`0xda0932d2…`; nothing else alerts.

**P1-6. Scanner rescoring of roster leads and stale high scores.** The scanner's
six priority sources never include a wallet found by dormancy, linkage, identity
or the roster, so behaviour can never corroborate those vectors — and after
DONE-2 a stale high score is never re-measured. Add a seventh source: roster
POSSIBLE+ non-service wallets and candidates whose stored score is ≥ 0.65 under
a retired scorer, capped (≈25/run) inside the scan step's time budget. Measure
the scan step's duration before and after.

**P1-1 (re-scoped). L1 novelty for settled watched wallets.** Gap that remains:
the treasury's and `0xf078969e…`'s L1 outbound to a never-seen **EOA**. Measured
over their whole stored history: treasury 6 novel destinations ever (5 contracts,
1 unknown); `0xf078969e…` 181 (81 contracts, 10 EOAs, 90 unclassified). The
watch already sweeps both. Alert HIGH on a first-ever EOA destination (bytecode
cache / Blockscout `is_contract`), contracts recorded silently. Classify the 90
unknowns first to size the alert rate.

**P1-7. Market-maker classification.** A registered market maker (referral code
`MMREF*`, 100% client order ids, Gtc/Alo quoting) opens accounts constantly, so
some are born inside his silences and earn a dormancy handoff. Record `mm_program`
from `referral.referredBy.code` and the order profile already collected; show it
on the roster row; after measuring on the dormancy population, decide whether a
dormancy vote on a measured MM account should count. Evidence first (rule 4-style
caution: never tuned to one case).

**P1-5. Shadow mode for new detectors.** A per-alert-kind `shadow` flag that
records without routing, used for N runs by every new tripwire (P1-3 first).
Historical replay is the stronger check and should be done anyway.

**P1-8. Live fire drill.** A small HL account (excluded as the owner's) runs each
route with $20–$50: test send, sub-account transfer, CCTP out/in, a bridge deposit
from a fresh wallet, an agent approval. Record what fired and how fast.

---

## Phase 2 — evidence-grade feeds (unchanged in substance)

- **P2-1 Source of every Circle deposit into HL.** Decode `MessageReceived`
  (`sourceDomain`, `sender`) on HyperEVM and join to the forwarder feed
  `cctp_feed.py` already reads. A cluster address or his Solana wallet funding a
  non-cluster HL account → CRITICAL, observed edge. VERIFY-API the
  MessageTransmitter address on chain 999.
- **P2-2 Arbitrum bridge as identity feed.** Depositor and withdrawal-recipient
  joins; relayer deposits (Relay/deBridge/Across) decoded to the originator.
  Reuse `bridges.decode_cctp_extension` for CctpExtension, which already names
  the credited account.
- **P2-3 Solana CCTP burns** of `2xm4bb8K…` decoded to `mintRecipient`/domain.
- **P2-4 Blockscout for Base/Optimism** (and BSC if an instance exists) —
  permanent Etherscan-free-tier gap on chains he uses.
- **P2-5 Aave capital-in-motion tripwire** ($152.6M) — earliest warning.
- **P2-6 Newborn × funding-route join**, including the first-ten-actions
  sequence for young accounts.

## Phase 3 — real time and global

- **P3-1 Runtime decision** (VM vs PC with gap accounting). Prerequisite for
  P0-1 going private.
- **P3-2 Per-user websocket** on target, cluster and watch set.
- **P3-3 Global non-order L1 action feed** (explorer websocket, VERIFY-API).
  Consumers: cluster agent approved by anyone → CRITICAL; withdrawal by anyone
  to a sentinel → CRITICAL.
- **P3-4 Block co-occurrence**, **P3-5 nonce clock-skew fingerprint**
  (VERIFY-API nonce exposure), **P3-6 tape detection of his IoC slice trains**.
- **P3-7 Migration as a ratio** (HL share of observable capital, unexplained
  residual), **P3-8 adaptive cadence + changepoints**, **P3-9 SUSPEND COPYING /
  compromise vs migration**, **P3-10 style drift on the target**.

## Phase 4 — handoffs that move no funds

- **P4-1 Self-crossing via fill `tid` join**, **P4-2 OI conservation on his
  closes**, **P4-3 mirror-on-close**, **P4-4 follow the followers** (copiers'
  lag target shifting to one account).

## Phase 5 — roster calibration and continuity

- **P5-1 Likelihood ratios measured on strangers** + FDR control.
- **P5-2 Historical evidence ledger** (facts never decay, inferences decay).
- **P5-3 COPIER classification**, **P5-4 action-named states + runbook**
  (both CONFIRMED wallets would read "HIS, NOT TRADING").
- **P5-6 Target is a set + promotion script with continuity check.** Also: ask
  the operator whether `0xf078969e…` (CONFIRMED, two-way $135M/$148M, shares his
  Binance deposit address) belongs in `known_self_wallets`; config ground truth
  is what DONE-3 and the spam immunity key on.
- **P5-8 Exact exchange fees + his own delay prior + run sequences** in the
  correlator; **P5-9 find the account he migrated FROM**; **P5-10 his full
  history from the node archive**; **P5-11 "what would change my mind" queue**.

## Phase 6 — platform

SQLite substrate synced to object storage; async sweeps; canary reads and
schema-drift tripwire; 30-day raw API retention; blindness page; run-summary
anomaly detection; delivery canaries and CRITICAL escalation; versioned schemas;
rules-as-data alerts; property tests for rules 2/5/6; module ownership map and
vector ROI audit; "measured no" registry with alert precision; recall harness.

## Phase 7 — remaining cheap checks

HyperEVM full sweep + EVM→Core landing; Unit operations; testnet rehearsal
(shared agents on testnet); agent/sub-account EOAs swept; sleep-window veto;
builder-fee/multi-sig tripwires; validator co-delegation; name resolution;
public trackers' related-wallet labels; public-attention precursor; vault
leaders as candidates; venue-wide value conservation; unlock-schedule check;
approval/counterparty/gas fingerprints; community detection; nightly analyst
note; shadow book from tape; automated copying (scope decision only).

## Operator decisions pending

1. Runtime for the fast path (VM or PC) — gates P3 and going private.
2. `0xf078969e…` into `known_self_wallets`?
3. `0xb83de012…` (~$177M) into `watch_wallets`? (Its link to the target rested
   partly on the retracted funder; the same-day referral habit with
   `0x5b5d5120…` stands.)
4. The owner's own wallet addresses, to exclude from every lineup.
5. S3 egress (a few dollars) for his full history.

## Not to build (measured and rejected)

Amount signatures; cross-vs-isolated margin; liquidation-distance habits;
Twitter scraping; raising the cache ceiling; widening `cctp_time_budget`;
re-adding watch steps to `trace.yml`; restoring any tier by hand.
