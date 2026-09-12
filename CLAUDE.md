# Ezekiel — the mission

**Find this trader's other HYPERLIQUID wallets, and catch him the moment he
moves to a new one.**

Target: `0x45d26f28196d226497130c4bac709d808fed4029` (Hyperliquid, possibly GCR).
The owner copy-trades him manually. If he migrates undetected, the owner is
following a dead wallet and the project has failed at the only thing it does.

He almost certainly has other wallets already. Assume so, and hunt accordingly.

Everything else in this repo — the graph, the scanner, the correlator, the
accounting — exists only to serve that. When a design choice is unclear, pick the
one that makes an undetected migration less likely.

## What counts as a result

**A Hyperliquid address. Nothing else is the deliverable.**

The owner copy-trades on Hyperliquid. A wallet he cannot follow there is worth
nothing to him, however interesting it is otherwise. So:

- The output of every vector is ultimately **an address that trades on
  Hyperliquid**. If a lead cannot be converted into one, it has not paid off yet.
- Other chains are **instruments, not targets**. Ethereum, Arbitrum, Base and the
  rest matter exactly insofar as they carry flow that lands on a Hyperliquid
  address — a bridge deposit, a shared CEX deposit address, a funder. Trace them
  freely, but the question at the end of every trace is always "and which
  Hyperliquid account does this reach?"
- **Always close the loop by asking Hyperliquid directly.** Any EVM address is a
  Hyperliquid address too. Before calling a trail dead, put the candidate to the
  HL API — and ask its whole surface, not just perp state: an account can exist
  spot-only, in a vault, under a subaccount, or holding only an authorised agent.
  `scripts/check_gcr_wallets.py` does this correctly; copy its shape.
- **Prefer the HL-native vectors when they apply.** `ledger_analyzer.py`,
  `agent_links.py`, `dormancy.py`, `subaccounts` and `vaults` see movement that
  never touches L1 at all, so an L1-only search is blind to the most likely kind
  of migration: one that happens entirely inside Hyperliquid.
- A worked example of this rule biting: the GCR Ethereum cluster below is
  confirmed, traced across four chains, and **reaches no Hyperliquid account**.
  Real work, correctly done, and it did not move the mission. Good research on
  the wrong chain still leaves the owner following a dead wallet.

Two things are worth chasing off-Hyperliquid anyway, and only these: evidence
that identifies the person (which then tells you where to look on HL), and flow
that ends at a bridge or a deposit address (which can be joined to an HL account
later). Everything else is a detour.

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
| HyperEVM watch | `scripts/check_hyperevm.py`, `scripts/probe_hyperevm_index.py` | Nonce tripwire, plus a survey: chain 999 IS readable through the Etherscan key |
| Identity | `hl_identity.py`, `scripts/check_identity.py` | `userRole` (agent → owner, sub-account → master), `webData2` (frontend agent), `stakingLink`, delegations, `portfolio` birth. An explicit link CONFIRMs alone |
| Own actions | `hl_actions.py` (collector step) | The explorer's last 300 L1 actions: `withdraw3` destinations, agent approvals, vault transfers, sub-accounts. A foreign destination alerts |
| Bridge destinations | `chain/bridges.py`, `scripts/check_bridge_destinations.py` | CCTP/Socket calldata names the destination chain and recipient; Hyperliquid's CCTP extension names the HL account. A non-cluster recipient alerts |
| Withdrawal pairing | `withdrawals.py`, `scripts/check_withdrawals.py` | Each HL withdrawal paired with the bridge payout at his own address; an unpaired one is resolved and alerted |
| Newborn accounts | `newborn.py`, `scripts/check_newborn.py` | Birth from the leaderboard's window volumes, no per-wallet calls; the youngest large accounts become priority scans |
| Solana | `solana_watch.py`, `scripts/check_solana.py` | The CCTP recipient of $22.75M of his, watched by signature |
| Co-movement | `comovement.py`, `scripts/check_comovement.py` | Who moves first. A copier follows; a second hand leads or ties. Evidence, and the one behavioural reading a copy-trader cannot fake |
| Global activity | `chain/activity.py` | Whole-chain transaction counts from Blockscout decide what is infrastructure; fan degree inside the substrate cannot overrule a quiet EOA |
| Close watch | `watchlist.py`, `scripts/check_watchlist.py`, `.github/workflows/watch.yml` | `config.watch_wallets`: a wallet that is probably his and is not confirmed, read every run — value, agents, sub-accounts, withdrawal destinations, HyperEVM nonce, its size **relative to the target's own account**, and a bounded L1 sweep. A CONTACT with his world alerts; a CHANGE is reported once, on the transition |

Unified in `roster.py` (tiers on how many vectors agree) and `accounting.py`
(what fraction of outflow is actually explained).

**Where his money actually goes (decoded 2026-09-10, not inferred):** $66.5M
"to infrastructure" at the CCTP extension was him depositing into his own HL
account through Circle (the hook data names him); $22.75M went to a Solana
wallet of his (`2xm4bb8K…`); $10M by CCTP and $320M through Socket went to
himself on other chains; $152.6M sits in Aave on Arbitrum and he also uses
Pendle, Morpho and Paraswap. **He does DeFi**, so the approval-fingerprint
rejection below rested on a false premise and is reopened. The genuine shared
exchange deposit address is `0x8570c2ae…` (an EOA forwarding 100% to Binance),
shared with `0xf078969e…`, which is a personal wallet two-way with the target
at $135M/$148M and now grades MIGRATION_CANDIDATE; `0x160f6ef9…` sent him
$100.56M and is a person too. Both were INFRASTRUCTURE on fan degree before
whole-chain activity was measured.

**Under close watch: `0xdd53c5297309130ab5fe5623dc905752e3342b13`.** It opened
at zero on 2026-08-17, two days into a six-day silence of the target's (his p90
gap is five days), ran $999 → $51.3M in three weeks, and matches his exits on
amount and timing — two independent vectors, so PROBABLE, and deliberately *not*
a `known_self_wallet`: that list is operator ground truth, this is a question
under observation. Baseline 2026-09-11: role `user`, **$49.87M**, 0 agents, 0
sub-accounts, **0 withdrawals ever**, HyperEVM nonce 0, 24 Arbitrum transactions.
Every dollar it holds came from two addresses and every dollar it sends goes to
the Hyperliquid bridge.

**Correction 2026-09-12: "0 agents" was a missing read, not a fact — and it was
rule 5 in the one place it costs most.** An account holds agents in TWO fields.
`webData2.agentAddress` is the UNNAMED frontend agent; **`extraAgents` is the
NAMED list, which is where an API wallet appears.** `read_wallet` asked the
first plus the explorer's `approveAgent` actions and never the second, so the
watch serialised `"agents": []` while **`agent-2026-08-17`
(`0x1e8695b7261ff0b422ccf46f0ccf093fad308c3a`)** was live to 2026-12-07. The
explorer cannot cover for this: 300 actions, unpageable, so a birth-day
approval on a wallet trading this hard has long rolled out — it is not in
`data/actions/` at all. Net effect: the watch's `new_agent` CHANGE was dead for
named agents on precisely the wallets busy enough to be worth watching, and a
shared agent is the only vector here strong enough to CONFIRM alone.
`scripts/check_watchlist.py` now calls `extraAgents` (reusing
`agent_links.normalise_agents`), `tests/test_check_watchlist.py` covers it —
the script had no tests at all — and the stored record was seeded so a
three-week-old approval is not reported as a transition, the same rule
`changes()` already applies to `vaults_led`, `dexes` and `hyperevm_nonce`.
**When adding a field to a watch, ask which OTHER endpoint answers for it.**

**Nothing compared its size with his, and the hand-comparison that noticed
was wrong (fixed 2026-09-12).** The watch read its value every eleven minutes
and never asked what the TARGET was worth, so the plainest reading of a
migration in progress — capital leaving him and appearing somewhere else —
could only be got by hand-diffing two files. Done that way it said the watched
wallet held **$53.2M against his $24.1M, 2.21x**, and that figure is FALSE: it
summed his perp and `xyz` from `data/account/latest.json` and omitted **$44.7M
of spot USDC**. His account on 2026-09-12 is $17.5M perp + $6.7M `xyz` +
**$44.7M spot USDC = $68.9M**.

Read properly — both sides through one function, live, across every dex plus
spot — the first real reading is **$54,074,595 against $62,536,023, a ratio of
0.86x**, and the check correctly fired nothing. So the wallet has NOT outgrown
him; it is at 0.86x and the band is 1.15x. The vector is a forward tripwire,
not a finding. Its value was proving the hand figure wrong within a minute of
going live.

`snapshot` now carries `target_value` and `size_ratio`, and `changes` reports
`outgrew_target` on the crossing. Three things decided deliberately:

- **Both sides are read by one function** (`check_watchlist.account_value`),
  across the same live dexes plus spot. The stored `data/account/latest.json`
  was the tempting source and is the wrong one: it holds only the CONFIGURED
  dexes and is written by a best-effort cron, so it would divide two different
  quantities and could manufacture a crossing out of a stale file.
- **A band at 1.15x, not parity.** Both are live books — he fell 42% in one day
  on $138M notional while `withdrawable` stayed $0.00, which is the market
  marking him, not money moving. At parity the pair would flap across the line
  on noise like that.
- **An ABSENT previous ratio counts as below the band, not as a first reading
  to skip.** This is the opposite of the `extraAgents` seeding decision: that
  was a three-week-old approval whose transition had already passed, where this
  would be a live state nobody had been told. A repeat after an outage costs one
  alert a day against the 24h cooldown; a missed crossing costs the mission. It
  rates HIGH, not CRITICAL — a size ratio is an inference, not a contact with
  his world. (This was decided expecting the first run to alert at 2.21x. It
  did not, because that number was wrong — the rule stands on its own reasoning,
  not on the case that prompted it.)

Nothing reads `data/watchlist/latest.json` on the dashboard, so the ratio is in
the record and the run log, and reaches the operator through the alert.

**That agent is bot-shaped, and it leans away from him.** Named for the
wallet's own birth day, so approved at funding: API-driven from day one, where
the target signs from the web frontend (unnamed `agentAddress`, 94.6% `Ioc`
manual TWAP slices). `userRole` on it answers `agent → 0xdd53c529…` and nothing
else, so there is **no shared-agent link to the target** today. Weigh it against
the amount/timing match rather than discarding either: he could run a bot on a
second account, and all three earlier correlation leads were bots too and were
style-vetoed.

**Its first CONTACT was an exchange, and that is rule 9 again.** The watch fired
CRITICAL on `0xd7a827fb…` because the roster held it at POSSIBLE — while it
carries **590,836 transactions and 489,454 token transfers on Arbitrum**. Fan
degree never caught it because the cluster used it twice; it is now measured,
cached and graded infrastructure. Two consequences, both built: `transfer_graph`
now spends twelve whole-chain readings a run on the **highest-value unmeasured**
addresses rather than only fan-flagged ones, and the watch measures every
counterparty before alerting — busy is shared infrastructure and never a
contact, unmeasured still alerts, and only his own addresses (target, known
self, a private deposit address) rate CRITICAL; a roster tier is an inference,
so it rates HIGH.

## Vectors collected but NOT wired into detection — pursue these

- ~~`data/agents/`~~ — **wired 2026-09-10** (`d1a0de06b`), and this bullet went
  on claiming otherwise until 2026-09-12 while the table above already listed
  the vector as working. `agent_links.py` + `scripts/check_agents.py` run in
  `trace.yml`, `roster.py` reads the result as `VECTOR_AGENT` — the one vector
  that CONFIRMs alone — and the close watch asks `extraAgents` per wallet.
  The live answer is a real and useful **no**: 120 wallets, 49 agents seen,
  **0 shared, 0 naming-family hits, 0 linked to the target**. His frontend
  agent `0x98cf3fee…` and the watched wallet's named bot agent `0x1e8695b7…`
  each resolve by `userRole` to their own owner and to nobody else. Worth
  keeping written down: the strongest vector in the project has been asked and
  does not connect `0xdd53c529…` to him, which is evidence to weigh against the
  amount/timing match rather than a gap still to be filled.
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
- **Approval fingerprints — rejected 2026-09-10 on a premise that turned out
  to be false, and REOPENED 2026-09-11.** The rejection said "he does no
  DeFi". Decoding his bridge calldata and labelling his destinations showed
  otherwise: $152.6M into the Aave aArbUSDC aToken across 67 transfers, plus
  Paraswap, Pendle, Morpho and Socket. The protocols he approves, and the
  order he approved them in, are a fingerprint he does emit. Nobody has built
  it yet.
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
   FAILS, but on one condition rather than two: he is **rank 1** (his own best
   match) and the margin is short of the required **+0.05**. It got there by
   adding independent signal — order-submission habits, windowed to avoid
   leakage — not by moving weights or lowering the bar. Closing the remainder by
   reweighting, or by relaxing the 0.05, would fit the one measurement that
   proves the scorer works. Treat any behavioural score as unvalidated until
   `profile/backtest.json` has `passed: true`.
   **Read the margin from the file, not from here, and read it as a property of
   the LINEUP.** Measured across three consecutive runs with the scorer
   unchanged and his own windows identical (older 2026-05-25→08-04, recent
   2026-08-05→09-06):

   | run | self | best stranger | margin | top stranger |
   |---|---|---|---|---|
   | 2026-09-10 09:51 | 0.5864 | 0.5503 | +0.0361 | `0x5b5d5120…` |
   | 2026-09-10 09:58 | 0.5864 | 0.5495 | +0.0369 | `0x5b5d5120…` |
   | 2026-09-11 02:11 | 0.5880 | 0.5783 | **+0.0097** | `0x97cc9bb5…` |

   His own score moved +0.0016. The margin fell by two thirds because a
   **closer-matching stranger turned up in the lineup** — a different wallet
   entirely. So a shrinking margin is not evidence the scorer got worse at
   recognising him, and a single run's number is a snapshot of who happened to
   be drawn that day, never a trend. That is exactly why chasing it with weights
   would be fitting to the draw.
5. **A failed read must never serialise as a clean result.** Distinguish
   "we could not tell" from "there is nothing there", everywhere.
6. **Never price a missing value as `0.0`** — zero is invisible to every
   threshold. Use `None`.
7. **Reach beats tidiness.** Do not classify a wallet as infrastructure to clean
   up a report if it costs graph depth. Conduit detection is deliberately a
   single pass for this reason.
8. **A transfer is not ownership.** Surface leads; never assert identity.
9. **A shared destination is evidence only once the whole chain says it is
   quiet.** Fan-in inside the substrate counts only wallets we swept, so
   SocketGateway (2.19M transactions) looked like a five-sender private
   deposit address and the treasury was "confirmed" on shared routers.
   `chain/activity.py` measures the real count; unmeasured means excluded.
10. **Stored records are read deduplicated.** The ledger was on disk three
    times and fills 13.5% duplicated, so every HL-native total was triple and
    every exit reached the correlator three times. `load_all_records` dedupes
    by `record_key`; funding is keyed on (time, coin) because its hash is
    always zero.
11. **A token quantity is never a dollar value.** 1.03 billion MAX with
    `usdcValue "0.0"` was booked as $1.03B. Only USDC's own quantity may
    stand in for its value.
12. **`userFillsByTime` returns the OLDEST 2,000 fills after its start;
    `userFills` the newest.** A busy wallet's "first fill" is last week.
    Birth comes from `portfolio`; recent behaviour from `userFills`.

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
- The writing is 2021-2023; the Hyperliquid account is funded on 2024-02-29,
  first holds value on 2024-03-06 (`portfolio`'s first non-zero point, which
  is what `hl_identity.parse_birth` reads), first trades perps in 2024-05, and
  this project's collection starts 2026-02-20. Only enduring style crosses
  that gap, and people change.

**Read the sources before trusting the summaries — his words describe him worse
than his blotters do.** Two review passes, two checks overturned:

- "Never short small caps" is contradicted by his own posts fading listing pumps
  on IOTX/TRU/CLV/MNGO/AXS, and by blotters holding RLC, GRT, EGLD, BAKE, SRM,
  GTC, BAND and PEOPLE shorts. That tweet is ONLY in the PDF images.
- `hedged_book` scored a book with no longs as CONTRADICTS, built on his Dec 2021
  "delta neutral" writing. His blotters are 8-of-9, 7-of-7 and 6-of-7 short —
  img097's single long is circled "long?" by the compiler because it stood out.
  Replaced by `broad_short_basket`.
- `net_short_bias` scored a LONG book as CONTRADICTS. **img007 is an all-long FTX
  book** (+$2.19M), and he wrote "never short in a bull market" and "back to
  degen longing". Direction is regime, not identity — it can no longer disconfirm.

**The disconfirming check is now `liquidation_distance`**, because how he carries
risk survives the regime while direction does not. He claimed "nearly impossible
to liquidate me" (img013) on 1x-5x cross blotters, and carried one 8,346,280 CHZ
short from -$470k to +$2.1M (img012 → img017). Thresholds come from HIS leverage,
never from the target's numbers. `CAN_DISCONFIRM` plus three tests guard the fact
that something can still return CONTRADICTS; never let the last one go.

**img007 also shows "Show positions on all subaccounts" enabled** — he split
activity across subaccounts and viewed them aggregated. Off-chain, but it is the
documented habit this project exists to catch on-chain.

**Finding blotters: look, do not filter.** They are the highest-value images and
pixel heuristics fail — 1 row to 9, light (Binance) and dark (FTX). A filter
tuned on three light blotters missed img335 entirely. Use
`scripts/contact_sheets.py` (Pillow, lazily imported, not a CI dependency) and
read the grids; then open only the tables at full size.

**The check was reading only part of his book.** `clearinghouseState` does not
include the HIP-3 `xyz:` deployments, so the wallet scored 52-of-52 short while
it also held **long SP500 and XYZ100 against short MU and SKHX** — two
memory-chip competitors, an equity sector pair trade, and two-sided. The runner
now merges every dex, and reports a dex it cannot read rather than skipping it.
For the CLUSTER that means every dex the venue lists (`scanner.live_hip3_dexes`,
ten on 2026-09-11: xyz, flx, vntl, hyna, km, abcd, cash, para, mkts, io), not
just the configured `hip3_dexes` — a book opened on another is what a migration
inside Hyperliquid would look like, and he uses only `xyz` today ($6.76M, four
positions). Candidates keep the configured list, because eleven calls per wallet
across a five-hundred-wallet sweep is not free.

Including the HIP-3 book moved `liquidation_distance` from CONSISTENT to
UNTESTABLE, which is the honest reading: the crypto book is all cross and 100%+
from liquidation, but `xyz:XYZ100` sits **22%** away and `xyz:SP500` 40%, both
LONG indices on **isolated** 4x. GCR ran cross throughout.

**All 406 images triaged; 217 carry notes; 19 blotters, 1 order table, 1 BetFair
slip, 1 leaderboard.** `scripts/contact_sheets.py` tiles them into grids — that
is what works, because blotters run 1 row to 22, light Binance and dark FTX, and
a pixel filter tuned on three light blotters missed a dark one entirely. Set
`reviewed`/`content`/`notes` in `trade_review_index.json`;
`scripts/extract_trade_reviews.py` preserves them.

**Two things make every blotter a partial view.** He blurred market names and
sizes himself before posting, "to save you from copytrading" (img320/img321),
and the PDF's compiler whited out more. More importantly img337: **"75% of net
profit made on spot, other exchanges, ieos/idos, defi, nft, on chain"** — the
perp account is about a quarter of him. Any read of a single Hyperliquid perp
account inherits that limit.

**He treats visibility as a cost** — "going disable my account being visible on
this; visibility is -ev" (img337), positions blurred, repeated "do not copytrade
me". HL's leaderboard is computed from public state and has no opt-out, so he
could not hide there as he did on FTX — but a fresh wallet is small and outside
any top-N for a long time anyway. `scanner.py` draws candidates from the
leaderboard, so it finds a new wallet **late or never**. Flow, linkage and
dormancy are what find one early; never let the behavioural vector become the
primary net.

**Naming family.** His identities are @GiganticRebirth, @GCRClassic (display
name **"Ezekiel X"** — what this project is named after), @MingXMecca,
@rebirthdao, `trueshiba` (Discord, 2020, self-disclosed in img034), and the FTX
alias `Gigantic-Cassocked-Rebirth`. The recurring token is **Rebirth**. Searched
`data/agents`, `data/vaults`, `data/referral`, `data/subaccounts` for
`rebirth|gigantic|gcr|ezekiel|ming|mecca|trueshiba|kabosu|goblin|stonehenge` on
2026-09-10: **zero hits**. Cheap — re-run it whenever new agent or vault names
are collected.

**`data/agents/` has a baseline, and it is not empty.** `extraAgents` lists only
NAMED extra agents and returns `[]` for him; `webData2` shows the frontend agent
that actually signs his orders (`0x98cf3fee…`, approved 2026-08-31,
`signatureChainId 0xa4b1`), and `userRole` on it answers agent → owner = him.
The collector and `check_agents.py` read both. A new agent address is a new
address he controls; the treasury has approved eighteen of them since 2024-11.

**The most specific claim in the whole corpus is not a style trait.** He selects
shorts on **tokenomics — low float with large scheduled unlocks** — run as an
operation out of a Discord channel called `#the-big-short`, "getting precision
information on distributions" (img242, img239, img281, img303, img324). Recorded
as `shorts_high_emission_tokens` and **not implemented**: it needs a float and
unlock-schedule source this project does not have. If one is ever added, it beats
every behavioural dimension here.

**The NFT lead was pursued, and it produced a confirmed GCR address.**
`0x246eA68F4516F2d09DE8754708255D567477Ec21`. Four things GCR said publicly in
img163 all land on it: it won Zora token 3372 (Sad Doge / Kabosu) on 2021-06-23
settling a **15.6558675 ETH** June bid, sold it on **2021-08-27** to
`0x7d01dd0c...`, was paid exactly **2,000,000 USDC** in that same transaction,
and that buyer is the under-bidder who lost the June auction — matching the
public account that @TwoDollaHotDoge lost then acquired it. No single fact would
be enough; four are. Funded on 2021-06-11 with 676.8 ETH straight out of Binance.

Following the money: **$21.6M (84% of its outflow) went to
`0xd7d8f266c637948846bd2fdc4d906f6ba112de39`** — 75 transactions, $66.6M through
it — and on into Binance and MEXC deposit addresses. **Correction:** that address
was described here as a treasury "active until 2024-12-21". It is neither. Its
entire $66.6M moved in a **two-week window, 2021-07-28 to 2021-08-10** — a
conduit, not a treasury — and the Dec 2024 date is a single inbound WOWLABS
airdrop. Its last deliberate act is seven 0.01 ETH NFT mints in Feb 2023. The
dust-is-not-activity rule was applied to the L2s and should have been applied to
this mainnet timestamp at the same time.

**The trail forward ends on 2025-01-19, at an exchange.** Two cluster wallets
consolidated into one deposit address inside eighty minutes: `0x398d2824...` sent
**197.0000 ETH** (after unwrapping WETH), then `0xc70a4ddd...` sent **166.20
ETH**, both to `0x7502aafc...`, which forwarded each within two minutes to a hot
wallet with 6,024,489 transactions. **363.2 ETH, roughly $1.2M, into one exchange
account** — thirteen months before the target appears. A closing-out pattern, and
*not* evidence of a migration: money entering an exchange cannot be followed.

That shared deposit address is itself the find. A CEX deposit address belongs to
one account, so `0xc70a4ddd...` — funded from the same two Binance hot wallets as
the confirmed wallet, and a Blur NFT trader — is the **same exchange account** as
`0x398d2824...`. It is linked to a *lead*, not to the confirmed wallet, so do not
call it GCR's.

**None of it connects to the target.** No GCR-side address has ever touched
Hyperliquid (all four HL endpoints answered, all empty — a real "no"), and none
is a counterparty of the target. The only shared counterparties are two Binance
hot wallets with 15.8M and 30.5M transactions, which identify nobody and are
listed in `not_gcr` so they can never be matched on. **This is not evidence
against the hypothesis**: the confirmed wallet went quiet in 2022 and the
treasury in Dec 2024, while the target's history starts 2026-02-05, so there is
nothing to connect yet.

**Nor is he on Hyperliquid, checked properly.** Ten addresses (the nine cluster
members plus the NFT buyer) against twelve endpoints each — perp state, spot,
vault equities, subaccounts, agents, open and historical orders, fills, funding,
ledger, fee volume, referral. **120 calls, 0 unreadable, 0 with real use.**
Subaccounts and agents are empty everywhere, which closes two ways a GCR address
could have traded while looking idle. The one address returning anything holds
inbound-only airdrop spam.

Two measurement traps found doing it, both of which flagged *every* address on
the first pass: `userFees` always returns a 16-entry `dailyUserVlm` array whose
`exchange` field is the **whole venue's** volume (only `userCross`/`userAdd` are
the user's), and a fill with dir `Spot Dust Conversion` is the venue sweeping
dust, not a trade. Both are now handled in `check_gcr_wallets.py`.

**He never bridged.** Surveyed 2026-09-10 across Arbitrum, Optimism and Base:
the treasury `0xd7d8f266...` has **no Arbitrum history at all**, and no cluster
address has ever touched the Hyperliquid bridge on any chain. Every read
succeeded, so that is a real no.

**Watch out — the cluster looks alive in 2026 and is not.** The survey first
showed the confirmed GCR wallet dated 2026-08-14 on Optimism and the treasury
2026-08-16, contemporaneous with the target. It is all **airdrop dust**: no
cluster address has signed a single transaction on any L2. What arrived was TWT
minted from the zero address, a token whose symbol is literally `1`, and a
phishing token named `www.resemion.top ✅ claim`. The mainnet dormancy stands —
Dec 2022 and Dec 2024. **Do not feed these dates to `dormancy.py` as activity.**
One address also received tokens with the symbol **`GCR`** on Base; anyone can
deploy that, and someone did. A token is its contract, not its ticker.

Blockscout's public API reads base/optimism/arbitrum with no key, which is how
all of this was done — those chains are unreadable *through Etherscan's free
tier*, not unreadable.

So it is wired as a **tripwire**, not a finding: `src/gcr_wallets.py` +
`scripts/check_gcr_wallets.py`, in the daily workflow, and it **does** alert. It
watches three things: the target's graph, Hyperliquid itself, and **the Arbitrum
bridge** — that last one because a deposit credits whatever ACCOUNT it names, so
a GCR wallet could fund a brand-new HL account while every HL endpoint for that
wallet still answered "nothing here". If any of the three fires, that is flow
rather than resemblance — the strongest evidence this
project can produce, in either direction. Watch out for two traps the tests pin:
matching on shared exchange infrastructure, and reading inbound airdrop spam as
activity (`0x398d2824...` looks live and is not).

Live: net-short bias CONSISTENT (54/56), liquidation distance UNTESTABLE (closest
22%, the isolated `xyz:XYZ100` index long), broad short basket CONSISTENT (54
markets), small-cap shorts UNTESTABLE, round numbers CONSISTENT but weak. Nothing
contradicts — but only one check can, so read `falsifiability`, not the tally.

His method, in his own words (img388): *"the key was to focus on pair trading,
and to stay delta neutral to net short on the general market, while being able to
pick the winners."* The target is net short 54 of 56 with a two-sided equity pair
book — long SP500 and XYZ100 against short MU and SKHX, two memory-chip
competitors. A real structural rhyme, and still only evidence.

The corpus spans FOUR platforms: Twitter, Discord (`#alpha-discussions`,
`#the-big-short`), Telegram, and a BetFair slip — he traded political prediction
markets full time for years before crypto (img185, img186), and was still trading
Supreme Court and French-election markets in 2022.

## Not detection

`profile_builder.py` ingests research documents into `trader_profile.json`, which
nothing in `src/`, `scripts/` or the dashboard reads. It is a human-facing
artifact, not part of any vector — do not wire it into scoring on the assumption
that it is.

## Operating facts

- **Alerts arrive as GitHub Issues and ntfy, not email.** Brevo SMTP has never
  once delivered (account unactivated). `_github_issue_fallback` in
  `alerts.py`. `send_alert` also delivers through Telegram
  (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`) and ntfy (`NTFY_TOPIC`) when
  those secrets exist.
- **Only CRITICAL and HIGH are routed anywhere.** Both the instant channels
  and the GitHub fallback gate on `ESCALATING_SEVERITIES`, because the first
  trace run after ntfy went live pushed 24 "Operational Counterparty" notices
  at 3-11% confidence to a phone in one minute, which is how an operator
  learns to swipe the channel away. INFO is still written to disk, still on
  the dashboard and still counted; it just does not buzz. `NTFY_INCLUDE_INFO=1`
  opts back in. **Do not read "an alert fired" as "the operator was told"** —
  ask `_severity_of`.
- **Delivery health is judged on the alerts that were MEANT to arrive, and
  three fields in `data/alerts/latest.json` mean three different things.**
  `healthy`/`consecutive_failures`/`undelivered` count only health-bearing
  alerts (CRITICAL, HIGH, and anything whose severity cannot be parsed — an
  alert we cannot classify is never assumed harmless); `suppressed` counts
  what policy withheld. Before this split, every INFO recorded a delivery
  FAILURE, so on 2026-09-11 the file read `healthy: false` with six failures
  and the dashboard said ALERTING IS DOWN while all three CRITICALs still in
  the record — including the watched wallet touching his world — had arrived
  on ntfy in seconds. A flag pinned false by our own policy cannot report an
  outage, because there is no state left for a real one to change. The stored
  record was reclassified and replayed when this landed.
- **A withheld alert is disposed of, not pending — `_send_with_cooldown`
  returns True for it.** The rule that a failed send consumes no cooldown and
  stays queued is right for a FAILURE and a livelock for an alert policy
  routes nowhere: no cursor was written, `fire_alerts` recorded it
  undelivered, and `select_alerts` re-selects an undelivered wallet
  unconditionally, ahead of every confidence gate. One INFO notice came round
  seven times in five hours, each pass evicting a real row from the 20-entry
  delivery record. Fixing only the cursor leaves it re-alerting every 48h
  forever; both halves go together. A genuine failure keeps every bit of its
  retryability. `fire_alerts` returns `(delivered, undelivered, withheld)` and
  counts withheld apart from delivered, because "never say sent for a send
  that did not happen" applies here too.
- **A stalled frontier now alerts, because the last one did not.** Discovery is
  the only vector that reaches an address nobody has seen; every other vector
  starts from something already known. When it died for two days the graph kept
  rebuilding from known edges and nothing looked different, so the failure
  presented as an absence of discoveries — indistinguishable from there being
  nothing to discover. `alert_discovery_stalled` fires **HIGH** (not CRITICAL:
  CRITICAL means something about HIM, this is a capability of OURS) when no run
  has expanded a wallet in `transfer_graph.STALL_HOURS` = 12h, cooled down to
  once a day. Replayed against the real outage it would have fired at
  **2026-09-10 06:56 UTC** instead of nobody noticing until the 11th.
  **Liveness is keyed on wallets expanded, never on status** — `ok` requires the
  frontier to fully drain, which a graph with more work than budget never does,
  so the healthy steady state is `partial` and a status-keyed check would report
  a permanent stall on a perfectly healthy walk. `last_successful` is kept only
  for the dashboard's "last complete pass" line.
- **Blockscout reads arbitrum/ethereum/base/optimism/polygon with no key**:
  address counters and labels (`chain/activity.py`), decoded transaction
  inputs (`chain/bridges.py`). The Hyperliquid explorer
  (`rpc.hyperliquid.xyz/explorer`, `userDetails`) returns the last 300
  actions with payloads and cannot be paged. `vaultSummaries` answered `[]`;
  vault leadership comes from `webData2.leadingVaults`.
- **A conflicted data push used to throw the run's reading away.** Every
  committing workflow retried a failed push with `git pull --rebase`, which
  cannot settle a content conflict: the bare form left the tree mid-rebase so
  each later retry failed for the wrong reason, and the `|| git rebase --abort`
  form met the same deterministic conflict again. The run then opened a failure
  issue having lost what it read (measured: run 34630235534, 2026-09-11, three
  `data/*/latest.json` files). All seven now rebase with **`-X theirs`** — in a
  rebase that means the commit being replayed, i.e. this run's newer reading —
  which keeps our files and leaves untouched every file only the other side
  changed. Verified both ways against a simulated race before it was pushed.
- **A cron in this repo is a wish, not a schedule.** `trace.yml` asks for
  every 30 minutes; measured over its last 73 scheduled runs (2026-09-01 to
  2026-09-11) GitHub actually started it a **median of 198 minutes apart,
  never under 99, up to 337**. Scheduled events on free shared runners are
  best-effort and get dropped under load, and this repo had seven workflows
  competing for them. That is a five-hour blind spot in the one thing the
  project exists to catch. Two mitigations are in: `watch.yml` is a small,
  fast, single-purpose job (a small job is dropped less often), and
  **`workflow_dispatch` is not best-effort** — anything outside GitHub that
  can make one API call drives a run immediately. Do not quote the cron
  interval as the cadence; quote the measurement.
- **`watch.yml` has its OWN concurrency group, and `check_watchlist.py` /
  `check_hyperevm.py` have exactly ONE writer (2026-09-12).** They used to run
  in `trace.yml` as well, while both workflows shared the `data-commit` group.
  A group holds only one PENDING run, so the watch queued behind the long trace
  job and a dispatch was dropped when the next arrived — measured on run
  34658275747, cancelled after 90 seconds. A swallowed dispatch on the fastest
  tripwire in the project is the stalled-frontier failure again: an absence
  that looks like nothing happening. The group was really protecting two jobs
  writing the same files, so that is fixed at the source instead — the two
  steps were removed from `trace.yml`, which bought no freshness anyway
  (watch.yml runs every ~11 minutes against trace's measured median of 198).
  **Do not re-add them.** A second writer here is not a duplicate but a
  lost-update bug: `watchlist.changes()` diffs against the STORED previous
  reading, so a record discarded by a concurrent write can skip the very
  transition the watch exists to report. The remaining shared file is
  `data/alerts/latest.json`, which every workflow appends to and which the
  push step's `-X theirs` rebase settles.
- **Delivery health is SHARDED per run, and `latest.json` is only a cache
  (2026-09-12).** `_record_delivery` was a read-modify-write on one shared
  file, safe only while the shared concurrency group meant no two committing
  workflows overlapped. Giving `watch.yml` its own group ended that, and
  `-X theirs` takes the pushing run's file WHOLE — so two runs computing
  counters from the same base would erase one another: run A records a failed
  CRITICAL (`healthy: false`), run B records a suppressed INFO from the same
  base (`healthy: true`), B pushes second and A's failure is gone. That is the
  outage this file exists to make visible, so the rebase settling the CONFLICT
  was never the same thing as settling the LOST UPDATE.
  Now each run appends to `data/alerts/runs/<run id>.json` — two runs can never
  collide on distinct filenames — and `derive_health` recomputes
  `latest.json` from every shard on each write. Every field is a pure function
  of the events, which is the property that matters: **a rollup discarded by a
  rebase costs nothing, because the shards it was built from all survive and
  the next write rebuilds it.** Verified against the real production file in a
  sandbox: all four counters came out identical (`healthy: true`, 0, 0,
  `suppressed: 8`) and the field shape is unchanged, so the dashboard and the
  69 existing alert tests needed no edit. Two things to know: `suppressed` now
  counts the 30-day retention window rather than all time (the shards are the
  only record and they are pruned, bounded also at 400 files), and migration is
  automatic and once-only — keyed on no shards existing yet, NOT on
  `legacy.json` being absent, because `latest.json` is derived after the first
  shard write and re-migrating it would fold every event in twice.
- **`NTFY_TOPIC` is configured and delivering.** Verified 2026-09-11: a
  collector run's silence and account-drop alerts arrived on the topic within
  seconds while email failed as usual, as did every CRITICAL that day.
  Telegram is still unset.
- Free tiers only. Etherscan free does not serve account endpoints for
  **base, bsc, optimism** — those chains are unreadable, not empty. They say so
  in the response ("Free API access is not supported for this chain"), and that
  is a **permanent coverage gap, not a failed read**. The two are now separate
  buckets everywhere: `unsupported_sources` (never retried, always reported) and
  `degraded_sources` (blindness we re-read out of). **Merging them cost two days
  of discovery**: `expand_frontier` deferred every frontier wallet over three
  chains no retry can reach, so from 2026-09-09 18:56 to 2026-09-11 every run
  reported `failed` with 0 wallets explored and 0 new edges, throwing away the
  arbitrum, ethereum and polygon reads it had just completed. `0xf078969e…` sat
  at the head of that queue the whole time. One rule decides which bucket —
  `chain.collect.unreadability` — because the per-wallet sweep and the run
  summary both record it and must never disagree.
  **If the Etherscan plan is ever upgraded**, clear `expanded_ledger` in
  `data/transfer_graph/latest.json`: wallets marked explored were explored on
  the chains we could read at the time, and nothing re-walks them by itself.
- **HyperEVM IS readable through the Etherscan key, at `chainid=999`.**
  Measured 2026-09-11: `status: "1"`, real rows. What is unreconstructable is
  the PUBLIC RPC, which caps `eth_getLogs` at 1000 blocks against ~1s blocks.
  The surveyed answer for the cluster is **nothing**: the target has 8 ERC-20
  transfers there, all inbound airdrop spam, 0 native and 0 internal
  transactions, and has never sent anything; the treasury has 3, the same
  shape. No USDC at either address.
- **The $30,000,000 to `0x2000…0000` is still unaccounted for.** Six sends of
  spot USDC to the HyperCore system address for token 0 (2026-06-12 to
  2026-09-11), nothing returning that way, nonce 0, no ERC-20 on HyperEVM, no
  balance on any of the ten HIP-3 dexes, and no Transfer log crediting him in
  a window around the latest send. Every tool this project has says it is not
  where it should be. The nonce tripwire remains the watch.
- Tests must stay network-free and must never write to real `data/`.
- Verify before claiming: run it, read the output, report what it actually says.
