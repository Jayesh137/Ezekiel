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
now merges every dex in `hip3_dexes`, and reports a dex it cannot read rather
than skipping it. Including them moved `liquidation_distance` from CONSISTENT to
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

**`data/agents/` now has a baseline.** It sat empty because the fixed collector
had not been run against the target since the empty-vs-blind fix. Run: `agents:
[]` with `errors: []` — we asked, he has none. Any later non-empty list is a new
address he controls.

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
it, **active until 2024-12-21** — and on into Binance and MEXC deposit
addresses. All of it is in `data/labels/gcr_addresses.json` with per-address
evidence and confidence tiers.

**None of it connects to the target.** No GCR-side address has ever touched
Hyperliquid (all four HL endpoints answered, all empty — a real "no"), and none
is a counterparty of the target. The only shared counterparties are two Binance
hot wallets with 15.8M and 30.5M transactions, which identify nobody and are
listed in `not_gcr` so they can never be matched on. **This is not evidence
against the hypothesis**: the confirmed wallet went quiet in 2022 and the
treasury in Dec 2024, while the target's history starts 2026-02-05, so there is
nothing to connect yet.

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

- **Alerts arrive as GitHub Issues, not email.** Brevo SMTP has never once
  delivered (account unactivated). `_github_issue_fallback` in `alerts.py`.
- Free tiers only. Etherscan free does not serve account endpoints for
  **base, bsc, optimism** — those chains are unreadable, not empty.
- HyperEVM public RPC caps `eth_getLogs` at 1000 blocks against ~1s blocks:
  history there is unreconstructable, so it must be *watched*, not swept.
- Tests must stay network-free and must never write to real `data/`.
- Verify before claiming: run it, read the output, report what it actually says.
