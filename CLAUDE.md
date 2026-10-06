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
| Amount correlation | `correlator.py`, `src/boundary/gaps.py` | Exit re-appears as a same-size deposit across a CEX gap. **Two candidate pools**: Bridge2 deposits (the complete feed `run_provenance.py` owns, Etherscan only as fallback) and Circle's forwarder feed (keyless). **Since 2026-10-06 exits are custody-gap only** (into an exchange deposit address or a busy wallet, to a person, out of HL to an address outside his world) — DeFi he still holds, bridges to himself and nonce-paired withdrawals are not exits — **and a match must obey route physics**: an exchange exit pairs only with an account whose traced funding crossed an exchange; an unread route keeps the match as `route_unknown` |
| Circle withdrawals | `hl_actions.py`, `withdrawals.py`, `scripts/check_withdrawals.py` | `sendToEvmWithData` names a recipient on ANY CCTP chain; the ledger shows only a send to `0x2000…0000`. Each is paired with the mint at a cluster address; a foreign recipient alerts CRITICAL with the chain, one nobody can name alerts HIGH |
| Circle deposits | `cctp_feed.py`, `correlator.py --pools cctp` | USDC's forwarder `0x6b9e7731…` sends every CCTP deposit to its recipient, so its ledger is a complete keyless feed of every Circle deposit into every account. Incremental, paced, persisted; Circle depositors also join the scan priority set |
| New dex | `collector.new_dexes` (collector step) | A book on a HIP-3 dex he has never traded — a migration INSIDE Hyperliquid, with no L1 trace, no transfer and no new address. He uses only `xyz` of the ten the venue lists. CRITICAL, and the set was collected every run from 2026-09-11 with nothing diffing it until 2026-09-12 |
| Referral | `referral.py` (collector step) | He has no code today. Creating one, referring an account through it, or being referred alerts HIGH with the addresses — a referred account is one he chose to link to himself |
| Behavioural | `scanner.py`, `fingerprint.py` | Trading style. **Currently unvalidated, so it casts no vote** — the backtest is inconclusive until its fixed held-out cohort accumulates (rule 4); read `thresholds.policy` in `data/scans/latest.json` |
| Execution program | `execution_program.py`, `scripts/check_execution_program.py`, `scripts/census_execution_program.py` | His SDK slicer, recognised by his CLIP TABLE — the exact base size he fires per coin (ZEC 1, SILVER 20, NEAR 250, BTC 0.1) through `market_open` (5% IOC, ~1.7s, no client id). A behaviour vote (`execution_program`), so it corroborates and never reaches PROBABLE without a financial/protocol vector (rule 9), and casts NO vote until the census has MEASURED the match rare — `data/execution_program/census.json`, `ratio_p99` (rule 4). Match + independent vector → CRITICAL; census-rare match alone → HIGH. Measured 2026-09-28: 0 of 248 large accounts carry the full signature. **Scale-invariant too:** `notional_structure_rho` (Spearman rank correlation of per-coin clip notionals) survives him RESCALING every clip — a uniform rescale keeps rank order (rho 1.0), and his structure is stable across drift (recent-window rho ~0.9 vs exact-size ~0.5); `is_discriminating` clears on EITHER a census-rare `ratio_p99` (exact) OR `rho_p99` (structure) |
| HL-native | `ledger_analyzer.py` | Two-way flow entirely inside Hyperliquid, invisible to L1 |
| Shared agent | `agent_links.py` | An agent is authorised BY the account — two accounts sharing one are the same operator. Strong enough to CONFIRM alone |
| HL account surface | `hl_surface.py`, `scripts/check_hl_surface.py` | `subAccounts`, `referral`, `userVaultEquities` for every detector candidate. A sub-account on either side of a cluster wallet CONFIRMs alone; a master and its sub-accounts are ONE operator in the roster; a quiet referral (≤10 accounts on the code) with the cluster is one vote (`referral`) |
| Dormancy handoff | `dormancy.py` | One wallet goes quiet, another is born. The only vector needing NO connection between them |
| HyperEVM watch | `scripts/check_hyperevm.py`, `scripts/probe_hyperevm_index.py` | Nonce tripwire, plus a survey: chain 999 IS readable through the Etherscan key |
| Identity | `hl_identity.py`, `scripts/check_identity.py` | `userRole` (agent → owner, sub-account → master), `webData2` (frontend agent), `stakingLink`, delegations, `portfolio` birth. An explicit link CONFIRMs alone |
| Own actions | `hl_actions.py` (collector step) | The explorer's last 300 L1 actions: `withdraw3` destinations, agent approvals, vault transfers, sub-accounts, and multi-sig (`convertToMultiSigUser` names every signer, `multiSig` names the account signed for; each outside address is its own finding). A foreign destination alerts |
| Bridge destinations | `chain/bridges.py`, `scripts/check_bridge_destinations.py` | CCTP/Socket calldata names the destination chain and recipient; Hyperliquid's CCTP extension names the HL account. A non-cluster recipient alerts |
| Withdrawal pairing | `withdrawals.py`, `scripts/check_withdrawals.py` | Each HL withdrawal paired with the bridge payout at his own address; an unpaired one is resolved and alerted |
| Newborn accounts | `newborn.py`, `scripts/check_newborn.py` | Birth from the leaderboard's window volumes, no per-wallet calls; the youngest large accounts become priority scans |
| Solana | `solana_watch.py`, `scripts/check_solana.py` | The CCTP recipient of $22.75M of his, watched by signature |
| Co-movement | `comovement.py`, `scripts/check_comovement.py` | Who moves first. A copier follows; a second hand leads or ties. Evidence, and the one behavioural reading a copy-trader cannot fake |
| Global activity | `chain/activity.py` | Whole-chain transaction counts from Blockscout decide what is infrastructure; fan degree inside the substrate cannot overrule a quiet EOA |
| Close watch | `watchlist.py`, `scripts/check_watchlist.py`, `.github/workflows/watch.yml` | `config.watch_wallets` **plus every roster CONFIRMED/PROBABLE wallet** (capped at `MAX_WATCHED` = 6), read every run — value, agents, sub-accounts, withdrawal destinations, HyperEVM nonce, its size **relative to the target's own account**, and a bounded L1 sweep. A CONTACT with his world alerts **only for a wallet still in question** — a settled one records it without buzzing; a CHANGE is reported once, on the transition |
| Deposit-address sentinels | `deposit_sentinels.py`, `scripts/check_deposit_sentinels.py` (watch.yml) | His private exchange deposit addresses read from **their own** side: conduit/inferred deposit addresses the config cluster paid ≥$1K, measured quiet (rule 9), sticky in their own file. A new outside sender measured quiet alerts **CRITICAL** (unmeasured HIGH, busy recorded); first sight of a sentinel is a baseline. Quiet senders carry `linkage` in the roster from this file. Live at build: sentinels `0x8570c2ae…` and `0x499662e0…`; outside senders `0xda0932d2…` (2024) and `0xf078969e…` |
| New payee of his | `watchlist.outbound_payments`/`novel_payments`, `check_watchlist.check_new_payees` | A settled watched wallet (CONFIRMED or config) paying an address **no cluster wallet has ever transacted with**: native value at any amount (gas funds a fresh wallet), tokens ≥$100, never spam. Contract/busy is seen silently, unmeasured is held for the next run, a quiet EOA alerts HIGH — CRITICAL if Hyperliquid already shows the address in use. The target's own L1 is the tracer's; this covers the treasury and `0xf078969e…`, which had nothing. Seeded from history (615 addresses) so nothing old is announced |
| Circle flows, both ends | `circle_flows.py`, `scripts/check_circle_flows.py` (watch.yml) | Circle's MessageTransmitterV2 events on HyperEVM name BOTH ends of every Circle transfer into and out of Hyperliquid: `MessageReceived` carries the source domain, the source `messageSender` (EVM or Solana bytes32) and the credited account in `cctp-forward` hook data; `MessageSent` carries the withdrawing account, destination domain and recipient. Chain-agnostic and it cannot roll out of an explorer window. CRITICAL when a wallet of his funds an account outside the cluster, when an outside account pays one of his addresses (incl. private deposit addresses and his Solana wallet), or when his account withdraws to an outside address; such accounts carry `transfer` in the roster. Etherscan V2 (chainid 999) in CI, the public RPC (≈1 getLogs/min) as fallback; a cursor that never skips an unread range. First sample: 56 deposits in 1,000 blocks, from Arbitrum, Solana, Polygon, Base, Ethereum and Monad |
| Blind feeds | `feed_health.blind`, `scripts/check_feed_health.py` | A detector can write `computed_at` on time while every read fails or its parser no longer understands an endpoint. Per feed, a reading that is fresh but blind (half its reads failed, 0 agents or 0 sub-accounts across ≥20 wallets, 0 Circle transfers in ≥3,000 blocks, an empty roster/scan/candidate pool) is reported HIGH once it has lasted `BLIND_HOURS` = 6; the start time is kept per group in `data/feed_health/<group>.json` (one writer each). The dashboard's Tripwires page shows it as `blind` |
| Trace engine | `src/trace/`, `scripts/run_trace_engine.py` (trace.yml) | Follows HIS money (haircut share from the config cluster, both downstream and upstream) through units of (address, source): HL ledgers (incremental, strict reads — the treasury's and `0xf078969e…`'s had never been walked), whole-chain classification BEFORE any sweep, L1 sweeps of quiet wallets. Finds deposit addresses on both sides (HyperCore: whole amount forwarded within 10 min to one hub; L1: judged deposit by deposit), co-funders of single-purpose payees (≤100 txs, ≤4 senders), quiet funders of his, and HL accounts his money reaches through quiet hops. Links vote `linkage` from `data/trace/latest.json`; reach is evidence only. Sharded state under `data/trace/`. First production dry run (2026-10-04): `0x4aecac3b…` (HyperCore deposit address, hub `0x1f6093d3…`) found from config alone; `0x68797748…` among 7 funders. Spec: `docs/superpowers/specs/2026-10-04-trace-engine-design.md` |
| Boundary attribution | `src/boundary/attribution.py`, `scripts/check_boundary.py` (watch.yml) | Bridge2's `FinalizedWithdrawal` names the account that withdrew and the destination, globally and keyless (Blockscout getLogs, cursor); plus each core account's whole Bridge2 history (topic1), Unit per member, core HL sends, retro payouts and Circle mints into members. An outside account paying his wallet or deposit address is **CRITICAL** (HIGH when retro or unvalued); his account paying an outside address reuses `alert_foreign_destination`. Circle's forwarder-burned withdrawals are attributed through the USDC system address's ledger (`circle_flows.resolve_withdrawer`) — the forwarder is the message sender, so without it every Circle withdrawal into his world looked like an outsider (a latent false CRITICAL) |
| Funding provenance | `src/boundary/provenance.py`, `scripts/run_provenance.py` (trace.yml) | Large new money into any account (fresh Bridge2 deposits ≥$100K, the Circle pool, newborns, roster leads, dormancy handoffs, `route_unknown` matches) traced back ≤2 hops through its own HL ledger to its first boundary — Bridge2 depositor, Circle source, Unit, HyperEVM, an HL send — and joined against the perimeter. Hop-1 core/identity → CRITICAL with a `transfer` vote; any other member of weight ≥0.6 → HIGH, evidence only; his exchange family → `same_exchange` evidence. Owns the complete Bridge2 deposit pool (`data/provenance/bridge_deposits.json`, 30 days, ≥$10K) |
| Perimeter | `src/boundary/perimeter.py`, `scripts/build_perimeter.py` (trace.yml) | His world as ONE lookup table of measured roles and weights — core 1.0 (config), deposit 1.0 (sentinels + engine deposit addresses), identity 1.0 (Solana), sink/funder 0.6 (measured quiet only), associate 0.3 (≥$1M both ways, never alerts) — each put to HL (perp, spot, portfolio; strict). **Never built from a roster tier**: the roster calls his deposit addresses INFRASTRUCTURE, and reading that as "service" dropped all four (dry run, 2026-10-06). Exchange families (the hot wallets behind each deposit address) feed provenance |
| Re-scoring roster leads | `scanner.roster_rescore_targets` | Priority source 7: up to 25 roster leads (operator list pinned) and 10 stale unvetoed scores ≥0.65 re-scored every scan, so behaviour can corroborate what dormancy, linkage or a sentinel found. Not corroboration by itself |

Unified in `roster.py` (tiers on how many vectors agree) and `accounting.py`
(what fraction of outflow is actually explained).

**The full dated record of every finding and fix below — with the measurements,
replays and retractions — is in `docs/incident-log.md`.** It was moved out of
this file verbatim on 2026-09-28 to stay under the context-file size limit.
Read the relevant entry there before changing any mechanism named below, and
**append new incident write-ups there, not here**; this file keeps only the
standing state and the rule each incident taught.

## Where the hunt stands (read the roster, never this list, for a tier)

- **Where his money goes (decoded 2026-09-10, not inferred):** $66.5M "to
  infrastructure" at the CCTP extension was him depositing into his own HL
  account through Circle; $22.75M went to a Solana wallet of his (`2xm4bb8K…`);
  $10M by CCTP and $320M through Socket went to himself on other chains;
  $152.6M sits in Aave on Arbitrum; he also uses Pendle, Morpho, Paraswap. **He
  does DeFi.** His genuine shared exchange deposit address is `0x8570c2ae…` (an
  EOA forwarding 100% to Binance).
- **Config cluster:** the target, the treasury, and **`0xf078969e…`** (in
  `known_self_wallets` since 2026-09-17: two-way with him at $135M/$148M, pays
  the same private Binance deposit address, runs his Circle round trips).
  `0x160f6ef9…` sent him $100.56M and is a person too.
- **Capital leaving HL in size is routine treasury management, not by itself a
  migration.** The $31.8M to Monad (Circle domain 15) is idle HL capital parked
  in Aave on Monad and returned to his own HL account. Follow an outflow to
  where it lands. The "$30M to `0x2000…0000`" was Circle withdrawals
  (`sendToEvmWithData`) minted at his own Arbitrum address.
- **Walking his money forward is exhausted (measured 2026-10-06).** All 412
  L1 counterparties of the three config wallets (≥$50K) were put to HL: 16
  exist there and **none trades**, `0x160f6ef9…` ($180M two-way) included.
  The 72 newborn ≥$1M HL accounts were funded mostly by Circle (35), an
  account→account send (16), Unit (9), HyperEVM (6) and Bridge2 (6) — so
  another account of his sits behind a custody gap or has no flow link. That
  is why tracing now watches HL's edge from both sides (attribution and
  provenance, above) instead of walking forward. Spec:
  `docs/superpowers/specs/2026-10-06-boundary-trace-design.md`.
- **Nothing outside the config cluster is PROBABLE** (2026-09-16, after all
  seven post-backtest PROBABLEs proved false), and every HL-native vector asked
  of the 13 HL-present leads on 2026-09-22 — agents, agent-name families,
  sub-accounts, referrals — returned a **measured no**. That is a finding.
- **`0xdd53c529…`** — born 2026-08-17 inside a six-day silence of his, $999 →
  ~$51M, never withdrawn. POSSIBLE on dormancy (0.4286) alone; its old
  amount-correlation match fell out of the window. Its named agent
  `agent-2026-08-17` (`0x1e8695b7…`) is bot-shaped and resolves only to it —
  no shared agent with him. Size 0.86x his against a 1.15x band. Do not
  re-promote it by hand.
- **`0x5b5d5120…`** — $230M, correlation lapsed, POSSIBLE. It and two others
  were referred on the day of birth by a same-day account; its referrer
  **`0xb83de012…` (~$177M)** is almost certainly the same operator and is
  unwatched — the operator's call. Their shared "funder" `0xf92402bb…` is an
  **exchange hot wallet (2.28M txs)**, so that population links to him through
  nothing. `0x12e16e3d…` is retracted to WATCH.
- **`0xda0932d2…`** is the only outsider ever to pay his private deposit
  address (`$249,993.84`, 2024-07-31, a one-day wallet with a $6 HL account
  that never traded) — now watched by `deposit_sentinels.py`.
  `0x9430801e…` is an exchange gas feeder: settled no.
- **His frontend agent is now `0x6f4e393f…`** (was `0x98cf3fee…`); new agents
  of his alert (named CRITICAL, frontend HIGH).
- **Open:** `_load_cached`'s 256 MiB ceiling counts file bytes while holding
  parsed objects (~1.5x); recovering unpriced records lost across the frontier
  is an API-budget decision for the operator (target re-sweeps, never
  wholesale); `data/transfers/` still grows daily.

## Design rules each incident taught

Each one cost a real finding or a real outage. The story is in the incident log.

- **When a cap trims a collection, ask what the sort order MEANS** — for a queue
  it is priority, for a ledger age, for a node budget value moved; never the
  address or the file order. Broken four times (`expanded_ledger`, frontier
  queue, `evidence_strength` ranking, `build_graph` node budget).
- **A cap on a ranking of evidence-already-found starves the wallets with no
  evidence yet** — the population this project hunts. `detector_candidates`
  pins `config.watch_wallets`/`known_self_wallets` and ranks HL-known wallets
  first. `evidence_strength` orders a queue; it is **not a confidence** and is
  never stored or thresholded.
- **When one call in a pair is guarded, ask what guards the other**
  (`extraAgents`/`webData2`, the collector's `webData2`, the backtest's two
  loops, funder beside destination). **When a rule is enforced in one branch,
  check the branch beside it.**
- **`utils.hl_post` never raises**: exhausted retries answer `[]` if the
  request type contains "user", else `{}` — identical to "none". Use
  `cctp_feed.strict_post` or check the answer's shape
  (`agent_links.webdata_is_unreadable`). **When adding a field to a watch, ask
  which OTHER endpoint answers for it** (named agents live in `extraAgents`).
- **First readings:** an absent previous record is a BASELINE for a fact about
  the past (an agent approval) and counts as BELOW THE BAND for a live state
  (size ratio) — and ask whether the whole RECORD can be new, not just a field.
- **The severity token is the routing key.** Only `ESCALATING_SEVERITIES`
  (CRITICAL/HIGH) route; an invented word reaches no channel and marks health
  false. Ask `_severity_of`, not "did an alert fire". CRITICAL is about HIM;
  our own capability or an inference is HIGH.
- **A contact settles a question, so a settled wallet has nothing to report**;
  a wallet is not its own contact; `WORLD_TIERS` is CONFIRMED/PROBABLE only.
- **Two vectors that can fail together are one vector.** A finding reachable
  only through another detector's output (linkage via graph nodes) lapses with
  it. Ask what file a vector would be read from if every other detector were
  off. A permanent fact belongs in a permanent container.
- **Never fit a tier to the story** — no hand promotion. Demotions are recorded
  (`peak_tier`, `tier_dropped_from`), never alerted.
- **Votes need their own evidence bar:** `transfer` needs an observed transfer
  with the target or through a config wallet with `self_flow_usd` ≥ $1K (reach
  is `graph_reach_only`); behaviour votes only from the current
  `scoring_schema`; a shared funder votes only once MEASURED quiet; busy or
  contract addresses are services at tiering — **except an address HL knows as
  a trader**. **Replay a filter against production data before trusting it.**
- **A validator drawing its lineup from a shared file must be told who it is
  looking for** — the target was his own best stranger for four days.
- **Ground truth is config only, never a roster tier** (spam immunity, cluster
  membership, `settled`); immunity is one-sided.
- **Timeouts nest, and the ORDER is the invariant:** `LOOKUP_SECONDS` (45s) <
  walk budget < step `timeout-minutes`; requests carry `timeout=(10, 30)`. An
  internal budget cannot interrupt a call in flight; a step timeout is
  survivable and a job timeout is not. **Time a phase because it can be slow,
  not because it can block.**
- **A plan refusal is not a rate limit.** `unsupported_sources` (short-circuit
  per run via `plan_refused`, never persisted, always reported) vs
  `degraded_sources` (retried). Merging them once killed discovery for two days.
- **Derive a unit's budget from its plan, and count partial success as
  progress.** A per-lookup budget of `chains × 3` ignored each chain's probe and
  completeness calls; adding two Blockscout chains starved BSC and Monad on
  every wallet, one degraded chain deferred the whole wallet, and discovery was
  dark 15 of 17 days behind a daily alarm (2026-10-04). Keep what was read;
  retry only the rest.
- **Read the substrate once for many wallets** (`collect.records_by_wallet`);
  raising a cache ceiling only buys one step against a growing store.
- **The repo has a size budget — GitHub refuses any blob over 100 MiB** after
  the run's work is done. Graph edges are trimmed to 200/node at SAVE (counts
  never trimmed; truncation declared); sealed substrate days roll to
  `.jsonl.gz` in place (a day with both reads the `.json`); a day's substrate
  shards at 40 MiB with day-wide dedupe; the spam ledger keeps the 2,000
  loudest with persisted running totals. **Ask who reads the large part before
  shrinking it; ask what an aggregate sums OVER before capping it; when
  changing a storage format, grep for readers that do not name the store**
  (`load_all_records`).
- **Price by contract, never by ticker.** Par attaches to a registry row with
  `"par": true`; `normalise_symbol` folds only measured homoglyphs (`₮`);
  `confusable_fold` is for DETECTING forged symbols, by equality never
  containment. `unpriced` records are kept (decided on `value_basis`),
  `impostor_token` quarantined; an unpriced edge enters the graph only if its
  sender was swept; `edge_passes_dust` treats unknown as not-dust. A classifier
  that destroys its input needs the evidence bar of an alert.
- **Workflow steps are independent:** detectors carry `!cancelled() &&
  steps.deps.conclusion == 'success'`, commit steps `if: always()`, never
  `continue-on-error`. Safe only because `save_latest` is atomic. Reprice stays
  gated on quarantine — **ask whether a step loses FRESHNESS or CORRECTNESS
  when its predecessor fails.** Run `python -m ruff check src/ tests/ scripts/`
  before pushing.
- **A number measured under one access pattern does not size another**, and
  check a commit landed with `merge-base --is-ancestor`, not a log window.

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
  **Re-measured 2026-09-12 and the same numbers now mean something different.**
  That reading was taken over a candidate set that did not CONTAIN
  `0xdd53c529…` — it sorted 166th and the cap was 120, so `0x1e8695b7…` was
  never in the index and the "no" was an absent read wearing the shape of an
  answer. With the set fixed it is 120 wallets and 49 agents again, still 0
  shared and 0 linked, and now it is an answer. `naming_families` is `{}`
  correctly: `0xfc667adb…` runs 33 agents named `chip_oe02b…chip_oe33b`, but a
  template used by ONE account is a habit with nothing to travel between, and
  the vector looks for a scheme crossing accounts.
- ~~`data/orders/`~~ — **wired 2026-09-10** as the `order_profile` dimension.
  54,866 records were being collected and never read. It discriminates hard:
  the target is 94.6% `Ioc` limit slices with **0% cancels and 0% client order
  ids**, while all three correlation leads are ~100% cancels and **100% client
  order ids** (bots). Now IN the backtest too, split by the same calendar days as
  the fills (verified disjoint: 0 shared oids, 0 shared days), with strangers
  given the same dimension so the target cannot score on one nobody else could
  earn.
  **Corrected 2026-09-29: not "manual TWAP".** His limit sits EXACTLY 5.0% through
  the book on 99.4% of orders — the hyperliquid-python-sdk `market_open` default
  (`DEFAULT_SLIPPAGE = 0.05`) — fired at one round base size per coin, ~1.7s
  apart, in runs of tens to thousands, closing with `market_open` too. It is a
  SCRIPT. That is a stronger fingerprint than `order_profile` captures, and it is
  now its own vector (`execution_program`, below); the ~8% `FrontendMarket` orders
  are his web-UI clicks.
- ~~`data/twitter/`~~ — **removed 2026-09-10**. It was three empty directories
  with no collector and no reference anywhere: scaffolding for an intention
  never built, not data going unused. X's API is paid and scraping is fragile
  and ToS-questionable, so it is not being built. The free equivalent of the
  same idea — a human-chosen label that travels between accounts — is agent
  NAMING SCHEMES, now in `agent_links.naming_families`.
- ~~`data/vaults/`, `data/referral/`, `data/subaccounts/`~~ — **wired
  2026-09-16** as the HL account surface (see `docs/incident-log.md`). They were asked about the
  target alone, and for him all three are empty, so the scanner's overlap checks
  returned early for every wallet — and `_check_referral_link` read
  `referrerAddress`/`referredUsers`, keys the endpoint never sends, so it could
  not have found a link anyway.

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
- ~~**Cross vs isolated margin preference**~~ — **measured 2026-09-12 and
  rejected.** It looked like a clean per-human default, and the data for it was
  already being collected and thrown away (`compute_leverage_profile` stores
  `type` per coin; `compare_leverage` reads only the mean). It is not
  discriminating: of the top-40 leaderboard accounts only **8 hold an open book
  at all, and 7 of those 8 are all-cross**. A dimension that scores ~1.0 for
  nearly everyone adds no separation and dilutes the ones that do. The target
  is mixed (51 cross + 1 isolated on perp, 3 isolated + 1 cross on `xyz`) and so
  was 1 of the 8 — n is far too small to call that rare. Revisit only with a
  population measured in hundreds.
- ~~**Liquidation-distance habits**~~ — **measured 2026-09-16 and rejected, on
  the same grounds.** Of the 120 largest leaderboard accounts (>$5M), **95 hold
  no perp book** on the main dex, so only 23 carry a liquidation distance at
  all. His closest position sits 65–98% from liquidation across 98 snapshots,
  and **57% of that population also sits ≥65%** — the middle of the pack. His
  median distance (~300x) is top-22%, but on n=23 and dominated by shorts,
  whose liquidation price sits far above a falling market by construction. A
  dimension nearly everyone scores alike dilutes the ones that separate. It
  stays useful exactly where it already is: `gcr_hypothesis`'s
  `liquidation_distance`, the one check that can DISconfirm.
- **A flat wallet is unknown, not dissimilar (fixed 2026-09-12).** Looking for
  the above found rule 6 inside the scorer: `compare_leverage` returned **0.0**
  when either side held no open positions, and leverage is computed FROM open
  positions. Measured on the live scan, **69 of 174 candidates (40%)** carried
  exactly 0.0 — median score 0.1531 against 0.2980 for the 105 with a real
  reading — while every sibling dimension already renormalised (`hold_duration`
  119 None; `timing_profile`, `loss_handling`, `order_profile` 174 None each).
  It now returns `None`. Recomputed on those 69: median **+0.0433**, max
  **+0.0719**, three then clear 0.55 and the top one goes 0.5247 → **0.5966**.
  **Why this is a fix and not tuning:** a newly migrated wallet is
  disproportionately likely to be FLAT at the moment it is scanned — just
  deposited, no book open yet, or just closed out — so the artefact ran
  precisely against the population this project hunts. It barely touches the
  backtest (−0.0085 → −0.0082, still rank 2, still FAILING) because the
  backtest's strangers are drawn from the scan's top scorers, who mostly hold
  books. No weight and no threshold was changed.
- **Rare-market co-presence** — already partly used via `xyz:` markets; extend to
  any market with few participants.
- **Measured 2026-09-17 and rejected — do not rebuild without new evidence:**
  - *Testnet.* The target and the treasury exist on testnet, but every row is
    inbound: `spotGenesis` airdrops and token deployers sending the same tokens
    to both within minutes. No trade, no send, no agent. He never signed there.
  - *Unit (BTC/ETH/SOL bridge), ENS names, Hypurrscan aliases, multi-sig
    signers:* empty for the target, the treasury and `0xf078969e…`. Multi-sig
    stays a tripwire (see the 2026-09-17 fixes in `docs/incident-log.md`).
  - *Self-cross (a position handed between his accounts through the book).*
    No fill of the target's 163,633 shares a trade id with the treasury's whole
    fill history, `0xf078969e…`, or the newest 2,000 fills of 25 roster wallets.
    The endpoints that name BOTH sides of a trade (`recentTrades`, the trades
    websocket) cover only the last few trades, so going further needs a stream.
  - *Block co-occurrence.* One of his blocks held 441 transactions, 52 from a
    single market maker: co-occurrence finds bots present in every block.
  - *A "fading activity" alert on the target.* His weekly notional runs from $0
    to 20x its $2.7M median, and 6 of 31 weeks fall below a quarter of it. It
    would fire on normal weeks; `dormancy.py`, calibrated on his gaps, is the
    right instrument.
  - *Copier classification from co-movement.* All 12 candidates are
    `untestable`: he made 2 decisions inside the 21-day window. There is
    nothing to classify until he trades more.
  - *Transaction legs (every transfer inside his transactions, and their
    signers) — measured 2026-10-04.* 117 contract-facing transactions read in
    full (80 of `0xf078969e…`'s 1,468 on Ethereum, 37 of the target's 572 on
    Arbitrum): every third-party recipient and every foreign signer was a solver,
    market maker or relayer (`0x37305b1c…` 1.47M transfers, 21 legs;
    `0x7ddb0773…` 218K txs). Three "quiet" recipients each appeared once with
    1K–5K transfers. Not built; the engine's spec moves it out of phase 2.
    **Trap found doing it: Blockscout counters can read 0 for a busy address**
    (`0x153e996e…`, a Circle relayer). A zero for an address seen SENDING is now
    unmeasured, in the engine and in `deposit_sentinels.classify(sender=True)`
    — where it would otherwise have paged a false CRITICAL.
  - *Relay/Across/deBridge/LI.FI decoding.* 4 of the cluster's 4,978 records
    touch one (a $500K LI.FI swap in 2024 and three inbound Across fills, all
    on `0xf078969e…`). He bridges through Circle and Socket, both decoded.

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

   | run | self | best stranger | margin | top stranger | rank |
   |---|---|---|---|---|---|
   | 2026-09-10 09:51 | 0.5864 | 0.5503 | +0.0361 | `0x5b5d5120…` | 1 |
   | 2026-09-10 09:58 | 0.5864 | 0.5495 | +0.0369 | `0x5b5d5120…` | 1 |
   | 2026-09-11 02:11 | 0.5880 | 0.5783 | **+0.0097** | `0x97cc9bb5…` | 1 |
   | 2026-09-12 00:06 | 0.5880 | 0.5965 | **−0.0085** | `0xe2ad3768…` | **2** |
   | 2026-09-13 → 09-16 | 0.588-0.6593 | ~0.698 | negative | **the TARGET** | 2-3 |
   | 2026-09-16 (fixed) | 0.6593 | 0.5924 | **+0.0669** | `0xc435249d…` | **1** |

   His own score moved +0.0016 then stopped moving entirely. The margin fell by
   two thirds and then **went negative** because a closer-matching stranger
   turned up in the lineup each time — a different wallet on every run. So a
   shrinking margin is not evidence the scorer got worse at recognising him, and
   a single run's number is a snapshot of who happened to be drawn that day,
   never a trend. That is exactly why chasing it with weights would be fitting
   to the draw.

   **It PASSES as of 2026-09-16, and it was the lineup all along — the target
   was in it.** For four days the "closer-matching stranger" beating him was
   **himself**, at ~0.698 against a windowed self-match of 0.588-0.6593. The
   reading above that a shrinking margin says nothing about the scorer was
   right, and understated: on 09-13 the margin stopped being a measurement at
   all. Removing that one row — changing no weight and no threshold, which
   rule 4 forbids — gives **self 0.6593, rank 1 of 20, margin +0.0669** against
   the required +0.05. `passed: true` for the first time.

   How he got in: `scan_priority_targets` had no target exclusion while the
   leaderboard sweep one screen below it did. Six sources feed that priority
   set and any can name him; what actually did was **three fund-flow findings
   whose source and destination are both him, worth $0.00004 each**. The cost
   was three deep, and none of it looked like a bug:

   - `backtest.py` scores every row of `data/scans/latest.json` as a stranger,
     so the validator ranked him behind himself and reported FAILING;
   - `roster.behavioural_is_trustworthy()` reads that verdict, so **a whole
     vector stopped casting a vote** while nothing said so;
   - `risk.py` takes the top candidate's score, so he paid the **full 22 of 22
     points for trading like himself** — a third of the 64.7 ELEVATED standing
     on 09-16, and the `CRITICAL: Migration Risk 75/100` that fired on 09-15.

   Three guards, because the contamination is durable at a different stage in
   each: the priority set (where he gets in), `persist_candidate` (whose
   per-wallet file outlives the scan that wrote it — `latest.json` is re-globbed
   and sorted on a `best_score` that only ratchets, so one contaminated scan
   pins him at the top for ever), and the backtest's stranger loop (the
   instrument that validates the scorer must not be fooled by a file it did not
   write). The stored `0x45d26f28….json` — 27 entries, every one 1.0 — was
   deleted and the rollup rebuilt.

   **A validator drawing its lineup from a shared file must be told who it is
   looking for.** Nothing here was a scoring error; the scorer was being asked
   to tell the target apart from the target. And the asymmetry was visible in
   two adjacent loops for four days — **when one loop is guarded, ask what
   guards the other**, the same rule `extraAgents` and `webData2` already cost.

   Expect 7 wallets to move WATCH → POSSIBLE when the behavioural vector starts
   voting again (one vector each, confidence 0.0). None reaches PROBABLE, so
   none enters the close watch. Read the file, never this table, for the
   current number.

   **Unvalidated again since 2026-09-26, by design rather than by failure.**
   Scoring schema `2026-09-26.2` stopped drawing strangers from the scan's top
   scorers (a selected lineup) and scores a **fixed, score-blind held-out
   cohort** frozen before the trial window, needing at least 20 independent
   controls (`docs/discovery-operations.md`). Until that cohort has built up
   the report reads `passed: null` ("only 0 independent strangers; need 20"),
   the 2026-09-16 pass is **refused as a carry-forward** because it was measured
   under schema `2026-08-05.1`, and thresholds run as `OBSERVING`, so the
   behavioural vector casts no vote. 16 controls had built up by 2026-09-28. Do
   not shortcut this by seeding the cohort from scored wallets: that is the
   selected lineup the change exists to remove.
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
- **A STEP timeout is survivable; a JOB timeout is not (2026-09-12).** Run
  34673026019 died on trace.yml's 15-minute job ceiling with
  `transfer_graph.py` stuck at **699s having printed nothing at all** — so it
  was inside an external call, not working. A cancelled job skips every
  remaining step, **including `if: always()` ones**, so the finished
  Circle-pool read, the withdrawal pairing, the frontier, the roster and the
  accounting were all discarded. A STEP timeout instead fails only that step
  and the run carries on. So every step making unbounded external calls now
  carries `timeout-minutes` at roughly 3x its observed duration (measured over
  the last eight successful runs: totals **347-534s**, graph **64-202s**), the
  job ceiling is a 20-minute backstop rather than the primary limit, and every
  committing workflow's push step is `if: always()`. Partial data beats none:
  each writer rewrites its own file whole, so a file not updated this run keeps
  the previous value. `failure()` still opens the failure issue.
  **An internal `time_budget_seconds` cannot cover this** — it is checked
  BETWEEN calls and cannot interrupt one that never returns.
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
- **The Google Apps Script relay replaces an always-on machine (2026-09-17).**
  The operator has no always-on machine, so real-time websocket streaming is
  SCRAPPED. `scripts/apps_script/ezekiel_relay.gs` runs free on Google's
  servers every 5 minutes: it dispatches the workflows exactly as the PC
  dispatcher does (never into a queued run or the busy `data-commit` group,
  and never two into one group in the same tick),
  and it is a fast tripwire — the explorer for the config wallets, an urgent
  ntfy within ~5 minutes of a non-trading action reaching outside the cluster
  or handing out control, heightening the watch to 5 minutes for 6 hours after
  one. Routine his-own round trips, staking delegation and shared venues (HLP)
  are not alerted: measured over his history, ~1 alert a month. A multi-sig
  conversion or a multi-sig action names its outside signer or account. Tested under
  Node with Google's globals mocked (`relay.test.mjs`, in CI) and dry-run live
  against GitHub and the explorer, which found two real bugs: the seen-set held
  orders and evicted a real withdrawal (re-alert), and a 60-entry cap was below
  the treasury's 72 non-trading actions. Setup: `scripts/apps_script/SETUP.md`.
- **The Schedule Keeper drives the crons from GitHub itself (2026-09-28).**
  Two heartbeat failures on 2026-09-28 (475-minute-old data) were the alarm
  working: collect ran at 06:58 and next at 15:17 because NOTHING dispatched a
  run from 03:45 to 20:50 — the PC dispatcher below was asleep and the Apps
  Script relay was never installed. `keeper.yml` + `scripts/keep_schedule.py`
  run the same rules (intervals pinned equal to the PS1 so they never double
  up) as a job that ticks for an hour and then **dispatches itself** in an
  `always()` step; an hourly cron restarts a broken chain.
  **Do not make it event-driven.** The first version woke on `workflow_run`
  and died after one link: a run STARTED by `GITHUB_TOKEN` raises no
  `workflow_run` on completion (measured: watch dispatched 21:13, finished,
  nothing followed). `workflow_dispatch` is the one event `GITHUB_TOKEN` may
  trigger, which is why the chain is keeper → keeper. The same fact means the
  heartbeat's `workflow_run` trigger does not fire after keeper-dispatched
  runs; its own cron and the collector's inline freshness check cover it.
  `cancel-in-progress` stays **false** or the successor cancels its creator.
  Verified live: one keeper run dispatched collect 21:34, watch 21:36 and trace
  21:41 (after collect left `data-commit`) with no PC involved.
- **A local dispatcher now drives the crons (2026-09-12).**
  `scripts/dispatch_workflows.ps1` runs from Windows Task Scheduler on the
  operator's machine every 5 minutes and dispatches `watch.yml` (10 min),
  `collect.yml` (15 min) and `trace.yml` (30 min) whenever the newest run is
  complete and older than the interval — never while one is queued or
  running, and a cron-started run counts, so the two schedulers do not
  double up. Measured at install: watch.yml's scheduled runs were 119-145
  minutes apart, and collect.yml's last run was **240 minutes** old against
  its 15-minute cron. Only while the machine is on; GitHub's crons remain
  the fallback. Log at `%LOCALAPPDATA%\Ezekiel\dispatch.log`; remove with
  `schtasks /Delete /F /TN "Ezekiel workflow dispatcher"`. The repo is public,
  so the minutes are free.
  **It must not queue behind a busy group, and now does not.** A dispatched
  collect at 04:26 sat pending behind trace in `data-commit`; collect's own
  cron fired at 04:35 and EVICTED it (run 34673027518, "cancelled") — a group
  holds only one pending run. Nothing was lost, since the evicting run does the
  same work, but the dispatch bought nothing. The script now checks every
  workflow in the group first and skips instead. Splitting the group would be
  the better fix and is deliberately NOT done: it needs a proven
  single-writer map, and a wrong one is the lost-update bug this repo has
  already paid for once.
  **Nor may it send two runs into one group in the same tick (fixed
  2026-09-17).** The busy check ran once, before any dispatch, so when collect,
  trace and scan were all due it sent all three within four seconds (04:10:54
  to 04:10:58): collect started, trace queued, and scan's arrival evicted
  trace (run 35180941359, "cancelled" — a red mark in the Actions tab for work
  that was never lost). Both schedulers now dispatch at most ONE workflow per
  group per tick, the most overdue as a multiple of its interval; the rest go
  on a later tick. `-DryRun` logs the decisions without dispatching. Measured
  after the fix: 44 runs from 04:20 to 07:10, none cancelled or failed.
  **`schtasks /Create` silently kills it on a laptop — install with `-Install`
  (2026-09-12).** Registered with the documented `schtasks` line, the task
  carried `DisallowStartIfOnBatteries` and `StopIfGoingOnBatteries`, both TRUE
  by default. The machine went to battery and the dispatcher **stopped dead
  after two runs**: measured at 06:05 UTC the log held six lines — 04:06 and
  04:39 — against the ~36 lines an hour it writes when alive, and `watch.yml`
  had last run 65 minutes earlier against its 10-minute cadence. So the
  mitigation for GitHub's 198-minute blind spot spent its first two hours
  dead, in exactly the way it was built to prevent, and nothing said so.
  `schtasks /Query` was no help: it reported `Status: Ready`, `Last Run Time:
  30/11/1999` and `Last Result: 267011`. The power state is what tells you —
  `Get-CimInstance Win32_Battery`, `BatteryStatus=1` is discharging.
  The registration is now a `-Install` switch on the script itself rather than
  a comment, because settings that keep it alive belong in version control
  next to the thing they keep alive: `-AllowStartIfOnBatteries`
  `-DontStopIfGoingOnBatteries` (the defaults that killed it),
  `-StartWhenAvailable` (run on wake instead of skipping every occurrence
  missed while asleep) and `-ExecutionTimeLimit 10m` (with `IgnoreNew`, a hung
  `gh` would otherwise hold the only permitted instance for the 72-hour
  default — the same silent death by another route). Verified on battery at
  36%: `LastTaskResult 0`, next run 3 minutes out, and it immediately
  dispatched a `watch.yml` that was 111 minutes stale.
  **A tripwire that can die quietly is worth what a dead tripwire is worth.**
  **It also ran in a VISIBLE window until 2026-09-16.** The task runs as the
  logged-on user ("Interactive only"), so Windows gave `powershell.exe` a
  console: every five minutes a terminal opened over whatever the operator was
  doing, showed nothing (the script logs to a file) and vanished. Reported as
  "windows terminal keeps popping up randomly then closing". The action is now
  `wscript.exe //nologo scripts\dispatch_hidden.vbs`, which has no console of
  its own and starts PowerShell hidden from the first instant —
  `-WindowStyle Hidden` alone does not fix it, because PowerShell applies that
  only after the console already exists, which IS the flash.
  Two things that look like simpler fixes and are not. Making the task "run
  whether the user is logged on or not" removes the window completely, but
  without a stored password that is an **S4U logon, whose token is denied
  NETWORK access** — and calling the GitHub API with the user's `gh`
  credentials is the script's entire job, so it would trade a visible
  annoyance for a dispatcher that fails silently. And the launcher waits on
  PowerShell (`Run(cmd, 0, True)`) rather than firing and forgetting: with
  `False` the task would report finished immediately while the work continued,
  defeating both `-ExecutionTimeLimit` (a hung `gh` no longer killed at ten
  minutes) and `-MultipleInstances IgnoreNew` — the two settings `-Install`
  exists for. **A cosmetic fix that reintroduces the silent death is not a
  fix.**
  Check the log's newest line, never the task's `Status`.
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
- **The owner reviews candidates on an iPhone app (2026-09-22).**
  `https://jayesh137.github.io/Ezekiel/review` is a read-only PWA inside the
  dashboard (Add to Home Screen), described in `dashboard/ARCHITECTURE.md`.
  It reads `data/roster/latest.json` and `data/watchlist/latest.json` live, and
  every address links to its Hypurrscan wallet page through `addressUrl()`.
  **Renaming a roster or watch field it reads (§8 of that file) breaks the
  phone silently**, so check that list before changing either writer. Review
  marks live only in the phone's localStorage: nothing on the phone feeds the
  pipeline, and its tiers are the roster's own.
  It shows **Likely** (CONFIRMED/PROBABLE — two or more agreeing vectors) and
  **Leads** (POSSIBLE — one vector); the WATCH tier is counted in a footnote
  and never listed, because a wallet with no vector is evidence of nothing. On
  the day it was built Likely held only the two config wallets, and the app says
  so on screen rather than letting known wallets read as new finds.
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
  shape. No USDC at either address — which was correct, and was read as a
  mystery for two days because the sends to `0x2000…0000` were assumed to be
  HyperEVM transfers. They were Circle withdrawals to Arbitrum (see below).
- **The $30,000,000 to `0x2000…0000` is RESOLVED (2026-09-12), and the
  premise behind two days of searching was wrong.** A spot `send` to the USDC
  system address is not a transfer to HyperEVM. It is the ledger's only trace
  of **`sendToEvmWithData`** — Hyperliquid's native Circle/CCTP withdrawal —
  whose explorer payload carries `destinationRecipient` and a
  `destinationChainId` that is a **Circle domain** (3 = Arbitrum, 6 = Base,
  5 = Solana, 19 = HyperEVM), not an EVM chain id. All six sends minted at
  **his own Arbitrum address within the same minute**, from the zero address,
  for the amount less Circle's $0.20; the substrate held every mint the whole
  time and nothing joined the two sides. Read it as a lesson about the
  vocabulary: `0x2000…0000` is the *system* address, and what the system does
  with the money depends on the action, which only the explorer shows.
  What the route means for the mission is worse than a lost $30M: the action
  can name **any recipient on ~14 chains**, `withdraw3` pairing never saw it,
  `hl_actions` stored it with `destination: None`, and the explorer's
  300-action window rolls a payload out within a day (295 orders on
  2026-09-06 alone). Built the same day: the parser and CRITICAL alert, the
  mint pairing with a HIGH alert for one nobody can name, the watch counting
  it as a withdrawal destination, and unpaired Circle withdrawals as
  correlator exits. The HyperEVM nonce tripwire stays — as a tripwire on the
  one chain he can reach without L1, not as the answer to this question.
- **The inbound half of the same route was the correlator's blind spot.** A
  Circle deposit into Hyperliquid never touches the Arbitrum bridge: it is
  minted on HyperEVM to USDC's linked contract `0x6b9e7731…` and forwarded on
  HyperCore by that contract's own account, so it arrives as a spot `send`
  FROM the forwarder — the exact shape of his own 18 deposits ($66.46M).
  The forwarder's `userNonFundingLedgerUpdates` is therefore a complete feed
  of every Circle deposit into every account (measured: ~4,400 sends a day,
  2,000-row pages, ascending, `endTime` honoured, `[]` past the present, and
  rate-limited by page weight — hence paced and incremental). A fresh wallet
  funded from a CEX on Ethereum or Base and deposited through Circle enters
  the candidate pool through `cctp_feed.py` and nowhere else. **Never read
  this feed through `utils.hl_post`**: it returns `[]` on failure, and to a
  cursor walker an empty page means "reached the present".
- Tests must stay network-free and must never write to real `data/`.
- Verify before claiming: run it, read the output, report what it actually says.
