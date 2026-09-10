# Ezekiel — the mission

**Find this trader's other wallets, and catch him the moment he moves to a new one.**

Target: `0x45d26f28196d226497130c4bac709d808fed4029` (Hyperliquid, possibly GCR).
The owner copy-trades him manually. If he migrates undetected, the owner is
following a dead wallet and the project has failed at the only thing it does.

He almost certainly has other wallets already. Assume so, and hunt accordingly.

Everything else in this repo — the graph, the scanner, the correlator, the
accounting — exists only to serve that. When a design choice is unclear, pick the
one that makes an undetected migration less likely.

---

## The standing instruction

Use **everything**: tracing, scanning, pattern recognition, behavioural
fingerprinting, on-chain clustering, off-chain signals, and anything you can
invent. Do not wait to be asked to look somewhere new. If a vector might reveal
another wallet, pursue it, measure whether it works, and either wire it in or
write down why it does not.

**Two independent vectors agreeing is the strongest evidence available**, because
the ways they can be fooled do not overlap: an amount coincidence does not also
fake a shared deposit address, and a shared deposit address does not also fake a
trading style. Never promote a wallet on one vector alone.

---

## Vectors that work today

| Vector | Where | Notes |
|---|---|---|
| Observed transfer | `transfer_graph.py` | On-chain and HL-native movement |
| Linkage | `linkage.py` | Shared funder, shared CEX deposit address, first-gas funding. **Address reuse is the strongest single signal** — a CEX deposit address belongs to one account |
| Amount correlation | `correlator.py` | Exit re-appears as a same-size deposit across a CEX gap |
| Behavioural | `scanner.py`, `fingerprint.py` | Trading style. **Currently unvalidated — see below** |
| HL-native | `ledger_analyzer.py` | Two-way flow entirely inside Hyperliquid, invisible to L1 |
| Shared agent | `agent_links.py` | An agent is authorised BY the account — two accounts sharing one are the same operator. Strong enough to CONFIRM alone |
| Dormancy handoff | `dormancy.py` | One wallet goes quiet, another is born. The only vector needing NO connection between them |
| HyperEVM watch | `scripts/check_hyperevm.py` | Nonce tripwire; history there cannot be reconstructed after the fact |

Unified in `roster.py` (tiers on how many vectors agree) and `accounting.py`
(what fraction of outflow is actually explained).

## Vectors collected but NOT wired into detection — pursue these

- **`data/agents/`** — HL API agent wallets. An agent is *explicitly authorised*
  by an account. An agent shared between two accounts is close to proof of common
  control. Collected by `collector.py`, used by nothing. **Highest-value unused
  signal.**
- ~~`data/orders/`~~ — **wired 2026-09-10** as the `order_profile` dimension.
  54,866 records were being collected and never read. It discriminates hard:
  the target is 94.6% `Ioc` limit slices with **0% cancels and 0% client order
  ids** (manual TWAP), while all three correlation leads are ~100% cancels and
  **100% client order ids** (bots). Now IN the backtest too, split by the same
  calendar days as the fills (verified disjoint: 0 shared oids, 0 shared days),
  with strangers given the same dimension so the target cannot score on one
  nobody else could earn.
- ~~`data/twitter/`~~ — **removed 2026-09-10**. It was three empty directories
  with no collector and no reference anywhere: scaffolding for an intention
  never built, not data going unused. X's API is paid and scraping is fragile
  and ToS-questionable, so it is not being built. The free equivalent of the
  same idea — a human-chosen label that travels between accounts — is agent
  NAMING SCHEMES, now in `agent_links.naming_families`.
- `data/vaults/`, `data/referral/`, `data/subaccounts/` reach `scanner.py` but
  only for leaderboard candidates, not for graph-discovered wallets.

## Vectors worth inventing

Think about these. Two were measured and rejected on evidence rather than left
as open ideas — that record is worth as much as a built vector, because it stops
the next session rebuilding them:

- **Naming schemes beyond agents** — vault names and referral codes are also
  human-chosen. `naming_families` handles agents; the same idea applies there.
- ~~Dormancy handoff~~ — **built 2026-09-10** (`src/dormancy.py`,
  `scripts/check_dormancy.py`). Calibrated on HIS rhythm: median gap 2d, p90 5d,
  longest ever 21d, so a fixed "dormant after a week" rule would cry wolf. Two
  alerts: an unusual/unprecedented silence, and a wallet whose FIRST activity
  lands inside one. Live: silent 4d (normal), 7 anomalous gaps in his history,
  no handoffs among 13 candidates.
- ~~Portfolio correlation~~ — **built 2026-09-10** (`src/portfolio_overlap.py`).
  Rarity-weighted via `calibration.market_frequency`, so the majors count for
  nothing: BTC is held by 37% of wallets, HYPE 36%, and even FARTCOIN clears the
  common threshold. **Recorded as EVIDENCE, never a tiering vector** — a
  copy-trader holds the same basket in the same direction at the same time by
  definition, and this project exists because its owner does exactly that. Live:
  only 4 of 34 candidates hold an open book; best score 0.079.
- ~~Amount signatures~~ — **measured 2026-09-10 and rejected.** His genuine
  outbound amounts are overwhelmingly ROUND: `1,000,000` appears 285 times,
  `999,999` 57 times. Round millions are what every whale sends, so they
  identify nobody. The distinctive-looking `8999999.00021` that motivated this
  idea occurs genuinely only twice — three further occurrences were counterfeit
  tokens, now quarantined. Cheap to revisit if his habits change, but there is
  no signature here today.
- ~~Approval fingerprints~~ — **measured 2026-09-10 and rejected FOR THIS
  TARGET.** He has 32 outbound L1 destinations and 25 are infrastructure: his
  entire footprint is stablecoin transfers to exchange deposit addresses and the
  HL bridge. **He does no DeFi**, so there are no approvals to fingerprint, and
  collecting them would spend scarce Etherscan budget on a signal he does not
  emit. Worth building the day he starts interacting with protocols.
- **Gas and fee habits** — priority-fee setting is a per-human default.
- **Counterparty-set overlap** — Jaccard over the full counterparty set, not just
  deposit addresses.
- **Liquidation and leverage habits**, cross vs isolated margin preference.
- **Rare-market co-presence** — already partly used via `xyz:` markets; extend to
  any market with few participants.

---

## Hard-won rules — violating these has already cost real findings

1. **Empty is not blind.** A wallet showing "no outbound" usually just has not
   been swept. `records_for` on an unswept wallet returns only rows where it
   touched something already swept. Check `swept_wallets()` before concluding a
   trail ended. This produced a false $509M narrative.
2. **A token is its contract, not its ticker.** Anyone can deploy "USDC".
   Pricing by symbol booked **$3.07B of counterfeit value as real** — more than
   half of every dollar figure in the system. Registry:
   `data/labels/token_contracts.json`. Only add contracts verified on-chain or
   against an explorer; a wrong entry quarantines real money.
3. **Verify "well-known" addresses.** Twice the commonly cited address was a
   different token — Polygon's cited USDT is `USDT0`; Optimism has **two**
   legitimate contracts both reporting `USDC`.
4. **Never tune the thing that validates you.** The self-match backtest still
   FAILS, but on one condition rather than two: he is now **rank 1** (his own
   best match) with a margin of **+0.0361** against a required **+0.05**. It got
   there by adding independent signal — order-submission habits, windowed to
   avoid leakage — not by moving weights or lowering the bar. Closing the last
   0.0139 by reweighting, or by relaxing the 0.05, would fit the one measurement
   that proves the scorer works. Treat any behavioural score as unvalidated until
   `profile/backtest.json` has `passed: true`.
5. **A failed read must never serialise as a clean result.** Distinguish
   "we could not tell" from "there is nothing there", everywhere.
6. **Never price a missing value as `0.0`** — zero is invisible to every
   threshold. Use `None`.
7. **Reach beats tidiness.** Do not classify a wallet as infrastructure to clean
   up a report if it costs graph depth. Conduit detection is deliberately a
   single pass for this reason.
8. **A transfer is not ownership.** Surface leads; never assert identity.

---

## The GCR hypothesis — use it, but never circularly

`research/` holds public GCR material: a 149-tweet archive
(`GCR_tweet_archive.docx`, text in `gcr_archive.txt`) and 119 pages of annotated
trade reviews (`Trade Reviews.pdf`, 406 images extracted to
`trade_review_images/`, catalogued in `trade_review_index.json`). Structured in
`research/gcr_reference.json`; tested by `src/gcr_hypothesis.py`.

**The operator puts the odds that this wallet is GCR's at 55-75%.** So:

- The profile must be able to move that estimate DOWN. A test that can only
  confirm is worthless. `check_*` returns CONTRADICTS as readily as CONSISTENT.
- **Never reason "the target is GCR, GCR does X, so a wallet doing X is the
  target."** That is circular and manufactures leads. The profile is an
  INDEPENDENT reference — a wallet may match GCR's style with no link to the
  target at all, which is exactly the case that matters if he migrates somewhere
  flow cannot reach.
- Unmeasurable traits return UNTESTABLE, never "consistent". Counting absent
  evidence as agreement is how a profile confirms itself.
- The writing is 2021-2023; on-chain history starts 2026-02-05. Only enduring
  style crosses that gap, and people change.

**Read the sources before trusting the summaries — his words describe him worse
than his blotters do.** This has now cost two checks:

- "Never short small caps" is contradicted by his own posts fading listing pumps
  on IOTX/TRU/CLV/MNGO/AXS, and by blotters holding RLC, GRT, EGLD, BAKE, SRM,
  GTC and BAND shorts at once. That tweet is ONLY in the PDF images.
- `hedged_book` scored a book with no longs as CONTRADICTS, built on his Dec 2021
  writing about being "delta neutral". His actual blotters are 8-of-9, 7-of-7 and
  6-of-7 short — img097's single long is circled "long?" by whoever compiled the
  review, because it stood out. Delta-neutral is a phase he rotates into after
  booking profits, not his standing structure. Replaced by `broad_short_basket`
  (7-9 simultaneous alt shorts), which is what the blotters actually show.

Only **10 of 406 images** are reviewed, and those 10 overturned a check — the
remaining 396 are worth real time. Blotter screenshots first: positions, sizes
and leverage are the only material that compares directly against on-chain data.
Set `reviewed`/`notes` in `trade_review_index.json`; re-running
`scripts/extract_trade_reviews.py` preserves them.

**Watch the falsifiability, not the tally.** Only `net_short_bias` can return
CONTRADICTS — removing `hedged_book` alone flipped the live reading from mixed to
unanimous, with no change in the wallet's behaviour. `CAN_DISCONFIRM` and a test
guard this; never let the last disconfirming check go.

Live: net-short bias CONSISTENT (52/52), broad short basket CONSISTENT (52
markets), small-cap shorts UNTESTABLE, round numbers CONSISTENT but weak (48% of
transfers, 54% of position sizes). Nothing contradicts — but read the paragraph
above before treating that as support.

The corpus is not only Twitter: img251 and img282 are DISCORD posts from a
private group, absent from the tweet archive.

## Not detection

`profile_builder.py` ingests research documents into `trader_profile.json`, which
nothing in `src/`, `scripts/` or the dashboard reads. It is a human-facing
artifact, not part of any vector — do not wire it into scoring on the assumption
that it is.

## Operating facts

- **Alerts arrive as GitHub Issues, not email.** Brevo SMTP has never once
  delivered (account unactivated). `_github_issue_fallback` in `alerts.py`.
- Free tiers only. Etherscan free does not serve account endpoints for
  **base, bsc, optimism** — those chains are unreadable, not empty.
- HyperEVM public RPC caps `eth_getLogs` at 1000 blocks against ~1s blocks:
  history there is unreconstructable, so it must be *watched*, not swept.
- Tests must stay network-free and must never write to real `data/`.
- Verify before claiming: run it, read the output, report what it actually says.
