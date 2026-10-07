# Ezekiel — incident log

The dated record of findings, fixes, measurements and retractions, moved
verbatim out of `CLAUDE.md` on 2026-09-28 (it had pushed that file past the
context-file size limit). `CLAUDE.md` keeps the standing state and the rule
each incident taught; this file keeps the evidence. Append new write-ups here.
Claims below are as of their dates — later entries correct earlier ones, and
the roster, not this file, is the authority on any wallet's tier.

---

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

**The watch is fed by the ROSTER, not just by hand (2026-09-12).** It read one
address chosen by hand out of 348, and `0xf078969e…` — **CONFIRMED as his at
0.84**, two-way with him at $155M/$135M and sharing his Binance deposit address
— had nothing reading it per-wallet. The close watch is the only instrument
that asks a wallet for its NAMED agents, sub-accounts, withdrawal destinations,
HyperEVM nonce and size against his; leaving a confirmed wallet out of it is
the migration this project exists to catch, happening somewhere nobody looks.
`watched()` now takes the roster and appends every CONFIRMED/PROBABLE wallet
after the operator's list, marked `source: "roster"` — being watched stays a
QUESTION and never leaks into `known_self_wallets`. POSSIBLE is excluded (140
of them, one weak vector each), services are excluded, and the TARGET is
excluded because the collector already reads him and a second writer on
`data/actions/` is a lost update. Capped at **6** to protect the cadence, which
is the watch's whole value: 60-73s for one wallet, ~20-30s each once swept.
Live, this took the watch from 1 wallet to 3.

**Under close watch: `0xdd53c5297309130ab5fe5623dc905752e3342b13`.** It opened
at zero on 2026-08-17, two days into a six-day silence of the target's (his p90
gap is five days), ran $999 → $51.3M in three weeks, and matches his exits on
amount and timing — two independent vectors, so PROBABLE, and deliberately *not*
a `known_self_wallet`: that list is operator ground truth, this is a question
under observation. **Read the roster, not this paragraph, for its tier.** As of
2026-09-12 it grades POSSIBLE on ONE vector: the amount-correlation match is an
older reading outside the current 14-day window, and only the dormancy handoff
(0.4286) is live. PROBABLE is what two agreeing vectors bought when both were
supported; re-asserting it here by hand would fit the tier to the story. Baseline 2026-09-11: role `user`, **$49.87M**, 0 agents, 0
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

**Then the roster fed the watch and the vector ate itself (fixed 2026-09-12).**
Adding CONFIRMED wallets to the watch — right, and the fix directly above —
pointed the contact vector at wallets whose counterparties **are** his world by
construction. The first run after it landed pushed **39 contact alerts in six
seconds** (29 HIGH, 10 CRITICAL), burying that day's four real CRITICALs: a
six-day silence, a CCTP recipient outside the cluster, a Socket destination
outside the cluster, and an 84% migration candidate. That is the INFO-flood
lesson again — "how an operator learns to swipe the channel away" — arriving
this time at CRITICAL, where it costs most.

Three defects, each independently wrong, measured on the stored record:

- **A contact SETTLES a question, so a settled wallet has nothing to report.**
  `alert_watchlist_contact` says so in its own docstring: the wallet is watched
  because two inferences agreed, and an observed transfer is the third vector
  that decides it. For a wallet already CONFIRMED as his there is no question
  left, and "a known wallet of his touched another known wallet of his" is a
  tautology with a siren on it. `watchlist.contacts_are_news` now gates the
  alert on `settled` — set by `watched()` from `known_self_wallets` and roster
  CONFIRMED. **PROBABLE stays unsettled**: it is still a question, and a contact
  is exactly what would settle it. So is every `config.watch_wallets` entry, per
  the rule that the operator's list is ground truth and the watch is a question.
  All 38 stored contacts came from settled wallets; `0xdd53c529…`, the wallet
  the vector was built for, fired none.
- **A wallet was its own contact.** A watched wallet is itself in `world` —
  usually *why* it is watched — and `contacts()` had no notion of a subject.
  Two of the 38.
- **`roster: POSSIBLE` counted as his world.** 29 of the 38. POSSIBLE is 140
  wallets on one weak vector each, which is precisely why `WATCHED_TIERS`
  already refuses to spend the fast job reading them; treating the same tier as
  evidence on the way back in contradicted that judgement in the same file.
  `check_watchlist.WORLD_TIERS` is now CONFIRMED/PROBABLE, matching it.

Replayed against the live record: **38 alerts → 0**, with the contacts still
written to `data/watchlist/latest.json` (6→3 and 32→4 rows) and still on the
dashboard. Only the buzz stops, which is the policy INFO already follows.

**The frontier was re-walking half the address space, forever (fixed
2026-09-12).** `expanded_ledger` — the memory of which wallets are finished, and
the only thing stopping the walk repeating hop 1 every run — was written as
`sorted(explored)[:max_ledger]`. Sorting a set of COMPLETED WORK and cutting the
tail makes retention alphabetical, so a saturated ledger keeps the lowest
addresses and silently drops everything above them. Live on 2026-09-12 it held
exactly 2,000 entries topping out at `0x7bfa…`, and **all nine wallets expanded
that run sorted above it** — every one discarded, re-queued, and re-swept on the
next run, out of a lookup budget that stops on time and never drains. The
ledger now keeps insertion order and trims from the FRONT, so eviction drops the
work finished longest ago. Replayed on the live file: remembered **0 of 9 → 9 of
9**.

This is the same defect `tests/test_frontier_retention.py` was opened for —
truncating the pending queue by `(depth, address)` — fixed there and left
standing in the second place that truncates. **When a cap trims a collection,
ask what the sort order MEANS**: for a queue it is chase priority, for a ledger
it is age, and in neither is it the address.

The cap moved 2,000 → **20,000** at the same time, because it is not a tuning
knob here: the live explored set was already **2,017**, so the ledger shed work
every run whichever end it dropped. Eviction order decides *which* work is
forgotten; only headroom stops work being forgotten at all. At ~44 bytes an
address inside a 71MB graph file the headroom is free.

**A chain the API plan refuses was re-asked for every wallet, every run (fixed
2026-09-12).** Etherscan's free tier refuses base, optimism and bsc outright,
and CLAUDE.md has said since 2026-09-11 that `unsupported_sources` is "never
retried, always reported". The code reported it and retried it anyway: the
refusal is discovered reactively, per chain, per wallet, with nothing
remembering the answer. Measured on the live sweep — 10 wallets, 373 calls —
**90 of them (24%) went to those three chains and every one came back refused**,
at 3 calls each because a refusal on the first record kind did not stop the
other two. `sweep_wallet` now takes a `plan_refused` map shared across the
wallets of one run and breaks out of the kind loop on a plan refusal, so the
same sweep costs **3 calls instead of 90**. Wired into the frontier, the
backfill and the close watch.

**One slow wallet held the entire walk, and the step then retried it forever
(fixed 2026-09-12).** With the ledger fixed the graph step still failed on its
6-minute cap on every run from 05:05 UTC — 369 seconds against a 150s internal
budget, having printed **nothing at all** between the seed counts and the
timeout. Bisecting by hand ruled out the cheap explanations (frontier setup and
ranking over 155,541 edges is 10.6s; `records_for` averages 1.3s a wallet) and
could go no further, because nothing said where the time went.

So the diagnostic came first: **one line per lookup carrying the wallet, the
depth and the elapsed clock, plus `_phase` around expansion, bytecode
labelling, activity verification and deposit inference** (reporting on
`finally`, because the phase most likely to be killed is the slow one). It
answered on its first run (34680963132): lookups 1-8 took 33.7s at 1.3-7s
each, then lookup 9 — `0x892785f3…` at depth 4 — started and never came back.

The frontier had handed that one wallet every second left in the walk, and
nothing could take it back. **This is the exact case the "an internal
`time_budget_seconds` cannot cover this" rule was written about.** The cost is
not one slow wallet, it is a livelock: the walk stops so the other 195 queued
wallets get nothing; the STEP is killed so the graph is never written; the
frontier therefore never advances past the wallet that hung it; and it is
retried on every run after. The `expanded_ledger` bug again, by another road —
and discovery, the only vector that reaches an address nobody has seen, had
been dead for three hours.

Three bounds, nesting, each cutting a different link:

- **`LOOKUP_SECONDS` = 45s.** `sweep_budget` is built PER LOOKUP from the time
  left to the deadline, not once for the whole walk. Well above the 1.3-7s a
  healthy lookup takes, small enough that three pathological wallets still
  leave the walk time to finish and **persist** — which is what actually
  carries the frontier past them.
- **`timeout=(10, 30)`** on `etherscan_get` and `hl_post`. A scalar is one
  number doing two jobs, and a slice cannot interrupt a request already in
  flight, so this is the only bound on a stalled socket.
- **The graph step 6 → 10 minutes.** A step that fails on every run is not a
  backstop, it is an outage: a killed step writes nothing at all.

**What is pinned is the ORDER, not the numbers** — lookup slice < walk budget <
step timeout — because the three live in three different files and only their
nesting is the invariant.

Live after the fix: the run **succeeds**, `0x892785f3…` costs 46s instead of
the run, **13 lookups instead of 9**, 155 new edges, expansion 152.7s /
labelling 9.2s / activity 10.1s / inference 0.2s, and `expanded_ledger` broke
past its old wall to **2,019 entries** with every wallet expanded that run
remembered.

Two things kept deliberately: it is **per-run and in-memory, never persisted**,
so an upgraded API plan is picked up by the next run with nothing to invalidate
by hand (contrast `expanded_ledger`, which does need clearing); and a skipped
chain still appears in `unsupported_sources` every wallet, carrying the API's
own words — a chain that vanished from the summary would read as "nothing
there", which is rule 5. Only a PLAN refusal short-circuits; a rate limit is
degradation we retry out of, and merging those two once stalled the frontier
for two days.

**The ranking that feeds every bounded budget was sorting on the ADDRESS, and
it starved the leads (fixed 2026-09-12).** Five defects, one cascade, found by
asking why the wallet under close watch carried zero vectors.

It began with a regression. Splitting the candidate pools gave
`run_correlation` a `pools` dict and a rule that a pool not read this run keeps
its stored block — but every file written before the split had no `pools` key
at all, only a flat `matches` list, which IS the bridge pool's answer.
`_stored_pools` found nothing to preserve and overwrote it. Measured on the
stored record: **7-10 matches on every run up to 02:14, then 0 on every run
after 05:58**, with the bridge block absent entirely. Nothing had cleared the
bridge pool; it had stopped being asked and its last answer was gone. The
recovery path was analyze.yml — a daily cron, and the one workflow the local
dispatcher did not drive. The lost reading included a **0.9974** match, a $2.2M
deposit 4.4 hours after a $2.2M exit. It is restored, through the migration
rather than by hand, and `_stored_pools` is keyed on the ABSENCE of a bridge
block rather than the presence of the legacy list — the same rule the alert
shards follow, because the file is rewritten carrying both shapes and the other
keying would fold its own output back in every run.

Then the loss propagated, because **the roster ranks by evidence already found
and four detectors take the top N of that ranking**. `0xdd53c529…` lost its
correlation vector, fell PROBABLE → WATCH, and at WATCH sorted **166th of 180**
— outside the caps of 40 in `check_dormancy`, `check_comovement` and
`check_portfolio_overlap`, and outside the 120 in `check_agents`. The cut-off
was a wallet carrying confidence **0.0311**. So dormancy, the vector added FOR
that wallet and named in its own docstring, had never once scored it; its
agent `0x1e8695b7…` was not in the agent index at all, while
`linked_to_target: {}` read as a measured no. Two vectors became none and the
wallet the operator names by hand became one of 165 strangers.

**A cap on a ranking of evidence-already-found starves the wallets with no
evidence yet — which is the population this project hunts.** A wallet he has
just migrated to is the newest, quietest, least-connected thing on the list:
exactly the shape the ranking puts last. `roster.detector_candidates` now pins
`config.watch_wallets` and `known_self_wallets` ahead of roster order and never
trims them; the cap protects an API budget, and silence about a wallet a human
named is not a saving.

Underneath that, the sort itself was wrong. `confidence` is produced by the
transfer graph alone, so a wallet reached by correlation, dormancy or an
explicit link carries **0.0 by construction** — rule 6 inside the ranking.
Measured live: **124 of 181 non-infrastructure wallets (69%) sat at exactly
0.0, and 75 tied inside POSSIBLE**, so the sort fell through to its last key,
the wallet address. Which leads a bounded budget looked at was being decided by
leading hex digits: the 0.9974 correlation lead survived at #102 against a cap
of 119 because it begins `0x7f`, while the 0.6839 lead sat at #130 and was
cut — holding **33 live named agents in a `chip_oe*` family** that nothing had
ever indexed. `roster.evidence_strength` now ranks on the best score any vector
gives a wallet, with tier and vector COUNT still primary (two agreeing vectors
must outrank one loud one) and the address demoted to a deterministic
last-resort tiebreaker. **The `expanded_ledger` rule, third time: when a cap
trims a collection, ask what the sort order MEANS.** For a chase queue it is
priority, and an address is not one.

Live after the fixes: the leads moved **#102/#130/#81/#166 → #2/#3/#5/#6**, the
agent index went **15 → 49 agents**, `0xdd53c529…` scored a dormancy handoff of
**0.4286** (started 2 days into a 6-day silence) and returned WATCH → POSSIBLE,
and portfolio overlap went from 2 wallets with an open book to 7. The
shared-agent answer is still **no** — `0x1e8695b7…` resolves to `0xdd53c529…`
and nobody else — but it is now a measured no rather than an absent read, which
is the difference rule 5 exists for.

**Two things deliberately NOT done.** The `evidence_strength` maximum is taken
over different scales and is **not a confidence**: it must never be stored as
one or compared against a threshold, only used to order a queue. And the tier
`0xdd53c529…` now holds is **POSSIBLE on one vector, not the PROBABLE this file
claimed** — the amount-correlation match behind that grading is an older
reading outside the current 14-day window. Promoting it back by hand would be
fitting the tier to the story.

**A wallet that LOSES evidence must not look like one that never had any.** The
cascade above was invisible for hours because the roster is rebuilt from
scratch every run — right, so that a change inside one detector cannot silently
move a tier — and a wallet whose evidence lapses is therefore rewritten as
though the evidence never existed. `roster.carry_peak_tier` now records
`peak_tier` and `tier_dropped_from`, counted as `demoted_count` and printed in
the run log. **Recorded, never alerted**: a lost inference is a fact about our
own coverage, not a contact with his world, and grading a wallet INFRASTRUCTURE
is a measurement that outranks an inference rather than a demotion. Rule 5 over
time.

**And the sibling call nobody guarded.** `extraAgents` was checked for a failed
read; the `webData2` call beside it was not. `utils.hl_post` returns `{}` when
its retries are exhausted, `parse_web_data({})` answers `agent_address: None`,
and that is indistinguishable from a wallet that has authorised nobody — so a
timeout silently removed a frontend agent while `unreadable` stayed 0. Observed
during a live run that logged a read timeout on api.hyperliquid.xyz. webData2
is the ONLY endpoint reporting the agent that actually signs orders, and two
accounts driven by one are the same browser session: the CONFIRM-alone vector.
A successful read always carries a document, so an empty payload is only ever a
failure — `agent_links.webdata_is_unreadable` says so and the caller counts it.
**When one call in a pair is guarded, ask what guards the other.**

**The new-dex CRITICAL fired on the dex he has always used (fixed
2026-09-12).** `new_dexes` correctly answers "no news" on a first reading, but
it returns only the DIFF — so `check_new_dex`, computing `known | fresh`, stored
an **empty** baseline and threw the live reading away. The next run read that
empty file as "he trades no dexes" and `xyz` fired `CRITICAL: Target Opened A
Book On A New Dex (xyz)` at 07:28 UTC. A false alarm at the top severity, on
the alarm for a migration inside Hyperliquid — the INFO-flood lesson again,
arriving where it costs most. `live_dexes` is now split out so the caller can
seed what he is actually on; an unreadable account still never overwrites the
stored set.

**analyze.yml and scan.yml are dispatched now.** A daily cron is the most
droppable kind: GitHub delivered a 30-minute cron a median of 198 minutes
apart, and a once-a-day job that gets dropped is gone for a day. analyze.yml is
the ONLY thing that reads the Arbitrum bridge pool, rebuilds the fingerprint
and runs the GCR wallet tripwire. Their dispatch intervals are their own crons
(60 and 1440 minutes), so the two schedulers agree rather than doubling up, and
the existing group-busy check keeps them off a busy `data-commit`.

**The Circle pool is behind and that is a backfill, not a fault.** Measured
across six commits it advances ~2.7 days of feed per run and stood at
2026-08-27 against a 14-day correlation window opening 08-29 — so it
contributes nothing yet and says so, `candidates_considered: 0` under an
explicit `candidate_pool_error`. That is the correct shape: a pool that could
not be read whole has not cleared anyone, it has not looked. **Do not widen
`cctp_time_budget_seconds` to hurry it** — the step timeout, the internal
budget and the pacing are a nested order, and it reaches the present on its own
within a run or two.

**Three alerts about HIM could not buzz at all (fixed 2026-09-12).** The
severity token in a subject is not decoration — it IS the routing key.
`_send_webhooks` and `_github_issue_fallback` both gate on
`ESCALATING_SEVERITIES`, and `_health_bearing` treats an unclassifiable subject
as health-bearing precisely so an alert nobody can read is never assumed
harmless. Put those together and a word outside the vocabulary is the worst of
both: the alert reaches **no channel at all**, falls through to email (which has
never delivered), and then marks the whole system unhealthy for failing.

Three live paths invented their own words:

- **`alert_risk_level`** interpolated `risk.py`'s level straight into the
  subject, so the unified migration-risk alert went out as `ELEVATED`. It fired
  at **08:51 and 09:08**, reached nobody both times, and left `healthy: false`
  with the dashboard announcing ALERTING IS DOWN — while every CRITICAL that day
  arrived on ntfy in seconds. Risk CRITICAL now maps to CRITICAL and ELEVATED to
  HIGH, with the level still named in the subject so nothing is lost.
- **`alert_account_value_drop`** — "Possible Liquidation" — used `WARNING`. Now
  HIGH, not CRITICAL: the cause is an inference, as the 52% "drop" on 2026-09-11
  that was really a perp-to-spot transfer showed.
- **`alert_target_silence`** used `WARNING`. Now HIGH, since
  `alert_target_dormant` is the one calibrated on his own rhythm and already
  rates CRITICAL.

Guarded two ways, because a static reader cannot tell a safe variable from an
unsafe one: every **literal** severity in `alerts.py` must be routable, and
every function feeding an interpolated `{severity}` slot is exercised over its
whole domain. `discovery_severity` and the watch-contact severity were checked
and are fine — the test exists so the next invented word fails in CI rather than
in the field.

**This is the INFO-flood lesson inverted.** There, an alert buzzed when policy
meant it not to; here, three alerts about him could not buzz at all — and both
faults present identically in `data/alerts/latest.json`, as a health flag that
is wrong for a reason of our own making. **Ask `_severity_of`, not "did an alert
fire".**

**A wallet can now ARRIVE above the size band, and nothing reported it (fixed
2026-09-12).** `changes()` returns `[]` with no previous record. That is right
for agents, sub-accounts, dexes and vaults — announcing a three-week-old
approval as a transition is the `extraAgents` seeding mistake — and the size
ratio is the documented exception, because a missed crossing costs the mission
while a duplicate costs one alert a day against the 24h cooldown.

But that exception was written for an absent **field** on a known wallet. Since
the roster began feeding the watch, the absent thing can be the whole **record**:
a wallet promoted to CONFIRMED or PROBABLE arrives already sized, and the early
return skipped the one field meant to survive it. Measured live:
`0x5b5d5120…` was promoted PROBABLE on two independent vectors, entered the
watch on its first read holding **$230,394,643 against his $61,084,699 — 3.77x
against a band of 1.15** — and `changes` was empty. A wallet worth nearly four
times his appearing in his world is the plainest migration shape there is, and
the tripwire built for exactly that said nothing.

A first reading now evaluates the size ratio and nothing else. **The stored 3.77
will not re-fire**, so that particular crossing stays unannounced in the
channels; it is written here instead. **When a rule says "an absent X counts as
below", ask whether X can be absent because the whole RECORD is new.**

**`0x5b5d51203a0f9079f8aeb098a6523a13f298c060` is the roster's first PROBABLE,
and it is worth reading properly.** Two independent vectors: it **shares the
target's original funding source**, and an exit amount re-appears as a deposit
(0.5674 confidence, 249.2h gap, **18 competing deposits** — which is why the
confidence is only middling). It also scores **0.8293 behavioural with no style
veto** and was the top stranger in the 2026-09-10 backtest, but that casts no
vote while the backtest fails, and a high behavioural score on a wallet drawn
FROM the scan's top scorers is close to circular. It holds $230M, runs two named
agents, trades `perp` and `xyz`, has a HyperEVM nonce of 18 and no withdrawal
destinations. It is now under close watch automatically. **No transfer to or
from the target has ever been observed** — the correlation re-linked it across a
gap on amount and timing alone.

**And that last sentence was the whole problem: one lapsing vector deleted the
other (fixed 2026-09-16).** Of the eleven detector files `build_roster` reads,
every vector had its own — correlation from `correlations/`, shared agent from
`agent_links/`, dormancy from `dormancy/`, HL-native from `hl_transfers/`,
behavioural from `candidates/`, an explicit link from `identity/`. **Linkage
alone had none.** It was readable only off `transfer_graph/nodes[].evidence`,
so a wallet the BFS did not reach could not carry it however true it was.

This wallet reached the target at depth 1 through an inferred **correlation
edge and nothing else**. When its amount-correlation decayed — 0.6346, 0.634,
0.5513, then below `min_confidence` and gone at **2026-09-15 06:31** — the edge
went with it, the wallet stopped being reachable, the node vanished, and the
roster lost `linkage` AND `transfer` in the same run as `correlation`:
**PROBABLE → WATCH with zero vectors**, and out of the close watch, which takes
only CONFIRMED/PROBABLE. A $230M wallet stopped being watched and nothing said
why.

The shared funder had not changed and has not changed since. Both it and the
target were first funded by **`0xf92402bb…`**, and `data/labels/first_funders.json`
still says so. **A permanent fact was being stored in a volatile container.**

That inverts the roster's premise. Tiers reward INDEPENDENT vectors agreeing,
and `_read`'s own docstring says "the point of five vectors is that four still
say something" — but here one detector lapsing silently deleted another
detector's finding. **Two vectors that can fail together were never two
vectors.** `roster.linkage_from_first_funders` now reads the funder cache
directly, with an unresolved funder refusing to match (rule 5 — otherwise every
wallet with an unresolved funder matches the target on `None == None`) and an
excluded/service funder linking nobody (rule 9 — a Binance hot wallet would
hand the vector to everyone it ever paid).

Live: **four** wallets share that funder and regain linkage; the roster goes
348 → 349, POSSIBLE 126 → 130, WATCH 47 → 44. `0x498216a2…` was not in the
roster at all and is now surfaced by linkage alone.

**RETRACTED the same evening: that funder is an exchange hot wallet.**
`0xf92402bb…` has **2,282,986 transactions on Arbitrum** — nobody had measured
it, and "excluded" meant only the hand-kept config lists. The container fix
above stands; the fact it protected was never evidence. See "The first
PROBABLEs after the backtest passed were all false" below.

Two outcomes worth stating separately. `0x5b5d5120…` returns WATCH →
**POSSIBLE, not PROBABLE** — its correlation genuinely lapsed, and restoring
the tier by hand would be fitting the tier to the story, the same refusal this
file already records for `0xdd53c529…`. And once the behavioural vector votes
again, **`0x12e16e3dc4a4fb3c802be62105f15783eb95f92a` becomes PROBABLE on two
genuinely independent vectors** — behavioural 0.702 with no style veto, plus
the shared funder — and enters the close watch automatically. It was the top
candidate feeding `risk.py` once the target was removed from that list.
**RETRACTED: neither vector was real.** The 0.702 is a single scan from
2026-06-30 under a scorer the backtest never validated, and the funder is an
exchange hot wallet. It is WATCH with no vectors. Details below.

**When a detector's finding is only reachable through ANOTHER detector's
output, the two are one vector wearing two names.** Ask what file a vector
would be read from if every other detector were switched off.

**The stored graph outgrew GitHub, so every trace run lost its work (fixed
2026-09-12).** `data/transfer_graph/latest.json` reached **107 MB against a hard
limit of 100 MiB**, and from **09:27 UTC every "Trace Fund Flows" run computed
the graph and then had its push declined by the pre-receive hook** — failing the
whole job — **12 consecutive runs over six hours**, the last success at
08:52. Discovery is the only vector that reaches an
address nobody has seen, and it had stopped being able to save anything.

Note the units: the limit is 100 **MiB** = 104.86 MB. The version sitting on
origin was 104.23 MB — 99.4 MiB, under by a hair — so the file had only just
crossed, and the failure looked sudden while the cause had been building for
weeks.

**81.4 MB of it was `edges`: 212,457 of them, one node alone carrying 44,196
`edge_ids`.** Nothing reads that array back. `collect_known_edges()` rebuilds
every edge from `data/transfers/` on each run, and `annotate_changes`,
`select_alerts`, `advance_alert_state` and `migrate_graph` never touch it. Its
only consumers are the alert body — the earliest **15** of a node's edges, taken
from the IN-MEMORY graph before the save — and the dashboard, which sorts a
node's edges newest-first and shows **40**. The file was carrying 212,457 edges
to serve a maximum of 40.

`trim_edges_for_storage` keeps the **200 most recent per node**, applied at the
SAVE only so `fire_alerts` still reads everything: **107.23 MB → 8.43 MB**, with
`expanded_ledger` (2,037) and `frontier_queue` (176) — the state the next run
depends on — untouched.

**`edge_count`, `node.totals.edge_count` and `node.edge_ids` are deliberately
NOT trimmed.** How many transfers were observed is a fact; which details we kept
is a storage decision. A truncated list that reads as complete would understate
a wallet's activity, and that is the direction that loses a migration — rule 5,
pointed the expensive way. The file declares
`edges_truncated`/`edges_per_node_cap`/`edges_stored`, and the dashboard now
takes its count from `totals` and says when it is showing only the most recent
held on file.

**The substrate is compacted (2026-09-16).** `data/transfers/` was **416 MB
in the seven days since 2026-09-09**, nothing pruned it, and
`ethereum/2026-09-10.json` stood at **76.69 MB against GitHub's 100 MiB hard
blob limit** — 27 MiB of headroom on a store growing ~59 MB a day. A file over
that line is refused by the pre-receive hook, which fails the push AFTER the
run has done its work; that is the failure that cost twelve consecutive trace
runs when the graph file crossed it on 09-12.

Sealed days now roll to `<date>.jsonl.gz` **in place**. Live: **416.1 MB →
68.4 MB in 28.4s**, 21 days, 563,304 records, 0 unreadable, and the five
wallets checked before and after return **byte-identical** records through
`records_by_wallet`.

Three decisions worth keeping:

- **In place, not an `archive/` subdirectory** like the snapshot roller. Sealed
  days are still AMENDED: `quarantine_impostor_tokens` marks newly-found
  counterfeit contracts as spam on old records (rule 2) and `reprice` fills in
  prices it could not resolve at sweep time. An archive out of the way would
  have silently removed both abilities.
- **A day holding BOTH encodings reads from the `.json`.** That pair is the
  crash window between writing the archive and deleting the original, and the
  original is authoritative. Reading both doubles every record in the day — and
  amount matching is the correlator's whole basis, so a duplicated exit is one
  that can be matched twice, against a deposit with no real exit behind it.
- **`decode_records` raises where `read_records` returns `[]`.** The rewriters
  need the distinction: reprice says it in its own comment — "a file we cannot
  read is not a file we may rewrite" — and reports the path in
  `files_unreadable`. A corrupt archive decoding to `[]` would look like an
  empty day, be skipped silently, and vanish from the count that exists to say
  we are blind. Rule 5, deciding whether a file gets rewritten.

**The reader that nearly got missed is the whole lesson.** Four readers glob
the substrate directly and were easy to find. The fifth, `collect_known_edges`
— **the function that rebuilds the ENTIRE graph** — reached it through
`utils.load_all_records`, a generic dated-file reader whose `*.json` glob makes
no mention of transfers anywhere. Measured live the moment compaction landed:
a chain directory of seven sealed days plus today returned **26,001 records
instead of ~300,000**, with no error raised anywhere. The graph would have been
rebuilt from one day of history and reported itself healthy — an absence
wearing the clothes of an answer, which is rule 5 again at the largest possible
scale. It is now read through `substrate_files`; `record_key` returns None for
a chain directory, so `load_all_records` was deduping nothing there and the
swap loses no behaviour.

**When you change a storage format, grep for the readers that do not name the
store.** The ones that say `transfers` are the easy half.

No workflow change was needed: `compact_data.py --apply` already runs in
analyze.yml and `git add data/` stages deletions (verified: 21 deletions and 21
additions). **The next file to watch is `data/transfers_spam/latest.json` at
38.4 MB**, now the largest single file in the repo.

**The quarantine ledger was the largest file in the repo, and nothing read it
(fixed 2026-09-16).** `data/transfers_spam/latest.json` held **130,099 address
entries covering 611,811 suppressed records at 38.9 MB**, growing ~5 MB a day —
about **thirteen days from the 100 MiB blob limit**. It is also rewritten WHOLE
on every run, 200 commits so far, so each run added another ~38 MB blob. Read
by nothing: not `src/`, not `scripts/`, not the dashboard, not
`data/index.json`. The transfer-graph edges again — 212,457 carried to serve a
maximum of 40 — in the file that replaced it at the top of the list.

**61.5% of the entries had been seen exactly once** and the top 1,000 covered
54.4% of all suppressions, so the tail paid for itself in nothing. Capped at
the **2,000 loudest**, which is also the right axis for the file's one human
purpose, stated in `spam.rollup`: a legitimate token the registry does not know
yet should stay visible so it can be added to `assets.py` — and such a token is
one that keeps turning up. Live: **38.86 MB → 0.61 MB, 63.7x**, with
`suppressed_total` and every per-reason figure identical before and after.

**The number that had to change shape first.** `suppressed_total` was
`sum(e["count"] for e in merged)` — a sum over the STORED list, correct only
while the list was complete. Trimming it as written would have silently shrunk
the count of what we threw away, so the ledger of our own blindness would have
started under-reporting **at the moment it started saving space**. It is a
persisted running total now, with per-reason totals beside it, both seeded from
the legacy file with no recount needed: the old sum WAS the running total,
because it summed a complete list.

**And one figure is deliberately absent: the number of distinct addresses ever
seen.** Once an entry is evicted a re-sighting cannot be told from a first
sighting, so it cannot be maintained exactly. Rule 5 cuts both ways — a number
that cannot be exact is left out rather than published as fact. `entries_stored`,
`entries_cap` and `entries_truncated` are declared instead, the same way the
graph declares `edges_truncated`.

**Ask what an aggregate is a sum OVER before capping the thing it sums.** A
total computed from the collection you are about to trim is not a total any
more, and it will not complain.


**Real money was being quarantined, and it had been since 2024 (fixed
2026-09-16).** Reading the ledger above answered the question it exists for.
Quarantined records **never reach `data/transfers/` and the cursor advances
past them**, so every one of these is a permanently lost edge.

**Two independent defects, both found in one file.**

**1. Pricing decided by TICKER, so genuine tokens were called noise.**
`value_usd` matched `symbol` against 25 entries (14 `STABLES`, 11 `MAJORS`);
anything else returned `(None, "unpriced")`, which `classify_spam` turns into
`unpriced_token`. **332,636 records went that way, and 61.2% are three
contracts verified on Blockscout as genuine**: Tether USDT0 (4,352,450
holders), Axelar Bridged USDC, Aave v3 USDC — **33.2% of all 613,578
suppressions**. Only 5.2% of the bucket carries an advertising-shaped symbol,
which is the actual junk.

**The largest single case is one character.** Etherscan returns Tether's symbol
as **`USD₮0` — U+20AE, TUGRIK SIGN**, not ASCII `T`. The registry entry `USDT0`
is CORRECT and `.upper()` does not fold `₮`, so **97,662 records of real Tether
were dropped by a glyph.** `normalise_symbol` now folds NFKC plus a homoglyph
map that is extended **only as cases are measured** — a speculative fold maps a
forgery onto a genuine ticker, which is rule 2 pointed the expensive way.

Rule 2 had only ever been enforced in the REJECTING direction: `is_impostor`
uses `token_contracts.json` to refuse a token wearing a known ticker from the
wrong contract. Nothing used it to ACCEPT a genuine contract reporting an
unexpected ticker — and Aave's aToken calls itself `aArbUSDCn`, which no ticker
registry could ever carry. `canonical_symbol` and `load_par_contracts` are that
missing direction.

**Par is keyed on the CONTRACT, and the first attempt got this wrong.** Adding
`AXLUSDC`/`AUSDC`/`GHO`/`PYUSD` to `STABLES` passed every test and was caught
only by replaying the fix against the live ledger: a counterfeit `GHO` at
`0x7dff7269…` priced at par, because a ticker in `STABLES` prices **any**
contract reporting it on **any** chain with no registry row. That is rule 2's
own $3.07B failure mode, reintroduced by the fix for rule 2's opposite
direction. Par now attaches to a contract carrying `"par": true` in the
registry, verified on-chain per row, and `STABLES` was left alone.

Replayed against the live ledger: **204,563 of 332,636 records (61.5%) would
now be kept**, and the counterfeit GHO is not among them.

**2. The classifier convicted the TARGET himself.** The ledger held
`address: 0x45d26f28…, reason: lookalike, mimics: 0x45d2e417…, asset: USDC`,
token `0xaf88d065…` — **the canonical Arbitrum USDC named in our own
config.json** — **796 records, 2024-06-13 to 2026-08**.

**Correction, measured after the fix: those 796 were almost certainly NOT
lost, and the first write-up here said they were.** `forged_side` has always
protected the SWEPT wallet, so the target was never convicted in his own
sweep — only in sweeps of third wallets, where he is a counterparty. But a
transfer involving him is fetched by HIS sweep too, which stores it. The
quarantined copy was the third-party duplicate. Verified after a full
`--reset` re-read of the cluster: his USDC went 1,444 → 1,489, not 1,444 →
2,240, and his ledger entry still reads exactly 796 with nothing added.

The defect is real and the fix stands — a local per-sweep count must not
settle a global question of identity, and the ledger now correctly records
the TWIN as mimicking the target rather than the reverse. But the population
it actually costs is **a counterparty that is never swept in its own right**,
which is most of the frontier, not the cluster. **Do not quote the 796 as lost
records.** On Arbitrum he has 688
transactions and 9,625 token transfers against the twin's 59 and 698: he is
14x the more active address, and the twin is the poisoner.

`is_lookalike` ranks by volume **inside one sweep's `counterparty_volume`** — a
local count answering a global question about identity, which is rule 9 in a
different file, and `chain/activity.py` exists to answer it whole-chain.
`forged_side` already refused to convict the SWEPT wallet and says why at
length; but when a third wallet is swept the target is merely a counterparty
and becomes eligible again. `spam.ground_truth_addresses` now makes
`config.target_wallet` and `known_self_wallets` immune, threaded through all
four `sweep_wallet` call sites.

**Ground truth is CONFIG only, never a roster tier** — a tier is measurement,
and letting one detector's inference silence another is the coupling the
linkage fix was about. And the immunity is **one-sided**: it stops a protected
address being called the forgery and still catches whatever forges it, because
these are precisely the addresses worth poisoning.

**Neither fix recovers what was lost.** The records are gone and the cursors
advanced past them, so this is forward-looking only. Recovering the ~204,000
records and the 796 needs a re-sweep with `--reset` on the affected chains, or
a targeted backfill of the cluster — an API-budget decision for the operator,
deliberately **not** taken here.

**The recovery re-sweep, and what it actually bought (2026-09-16).**
`substrate-backfill.yml` dispatched with `full_reset=true` — the instrument
already existed, so no new code. It reset cursors for the 2 cluster wallets,
swept 6 chains, and cost **51 API calls in 2.5 minutes**, not the hour budgeted.

Measured against a baseline taken before it ran:

| wallet | records | counterparties | new assets |
|---|---|---|---|
| target `0x45d26f28…` | 1,490 → **1,623** | 53 → **54** | aArbUSDCn 82, GHO 2, PYUSD 3 |
| `0x1419e75…` | 114 → **205** | 37 → **38** | aArbUSDCn 34, GHO 12 |

**224 records and 2 previously unseen counterparties**, every one of them a
token the contract-pricing fix admitted. Real, and an order of magnitude below
what the ledger headline suggested.

**The headline numbers were never the cluster's.** USDT0's 97,662 and
axlUSDC's 72,667 are totals across **every wallet ever swept** — 208 and
growing — so a cluster re-sweep could not have recovered them and it was wrong
to imply otherwise. Recovering those means re-sweeping the frontier, which is
a different and much larger spend.

**Two bounds worth knowing before the next one.** `watch.yml` sits in its own
concurrency group, so the fastest tripwire keeps running through a backfill;
only collect/trace/scan queue, which is inside GitHub's measured 198-minute
cron variance. And the run's FIRST push attempt failed — the retry loop
settled it, and the commit landed, but a `gh run view` of the last fifteen
commits did not show it because automated commits had already stacked on top.
**Check `merge-base --is-ancestor`, not a log window.**

**"We could not price it" no longer means "destroy it" (2026-09-16, operator
decision).** Quarantining `unpriced_token` was rule 6 at the wrong end of the
pipe: the rule forbids pricing a missing value as 0.0 because zero is invisible
to every threshold, and the same reasoning forbids deleting the record. A
transfer between two addresses is an observed edge whether or not we know what
it was worth, and discovery — the only vector that reaches an address nobody
has seen — runs on edges, not on dollars.

**The decision is made on `value_basis`, never on the absence of a number.**
Three states used to collapse into one `amount_usd is None` test and two of
them are real money:

| basis | meaning | now |
|---|---|---|
| `price_unavailable` | a major we could not price today | kept (already was) |
| `unpriced` | a token we do not value at all | **kept** |
| `impostor_token` | PROVEN forgery wearing a ticker | still quarantined |

**That third row is the trap.** The old test covered both "we cannot value it"
and "we have proven it counterfeit", so admitting the first by that test would
have admitted forgeries too — rule 2's $3.07B lesson undone through the door
opened for rule 6.

**Two gates had to move, not one.** `classify_spam` decides what is STORED;
`normalise_transfer_record` independently dropped `amount_usd is None` before
the graph ever saw it, so fixing only the first would have changed nothing
visible. The invariant that made dropping look necessary — "an unpriced token
must never satisfy a value threshold" — is preserved by the value STAYING None:
every consumer reads `or 0`, which is the right reading for a minimum.

**`edge_passes_dust` is the one place `or 0` was wrong.** Dust means MEASURED
and tiny; unknown is a different statement, and collapsing them would have
deleted from the graph exactly what the substrate had just stopped deleting,
one function later.

**Measured, and larger than the change that prompted it.** Edges went
**326,886 → 619,877**, and 203,279 of the 277,208 newcomers are **ETH**. Those
are `price_unavailable` majors that were always STORED and always dropped at
the graph gate — so the graph had been blind to frontier ETH movement all
along. The node budget did not degrade: 299 nodes before and after, all three
key wallets retained, and every one of the 82 nodes that swapped out had **$0
direct flow** with the target. Graph build 42.7s, stored file 16.7 → 22.1 MB.

**The frontier walks unvalued edges too (operator decision, 2026-09-16).**
The two filters diverged for one day — an unvalued edge was a graph edge but
not walkable — on the argument that walking one spends an Etherscan lookup.

**What settled it is that these are permanently unvaluable, not temporarily
so.** 260,006 of the 277,208 unvalued edges are ETH/WETH, and measured against
the price cache **only 641 of them fall on a date we have a price for**: every
ETH miss is dated 2020-07-25 to 2025-09-14, every date inside CoinGecko's free
365-day window is already priced, and there are **zero misses inside it**. So
"wait for a price" was never going to arrive, and refusing to walk them meant
refusing to follow the target's ETH history at all.

**The starvation I expected did not happen, and the reason is worth keeping.**
`_frontier_priority` weights value at 0.45 from `received_usd`, so I expected
ETH-reached wallets to score ~0 and never be walked. They do not, because
`received_usd` is the wallet's WHOLE inbound profile, not the edge that reached
it: a wallet found down an unvalued ETH edge still carries every valued edge it
has. Measured on the live graph — depth-1 candidates 33 → 38, and the best
newcomer ranks **#11 of 38 at priority 0.8109 on $92M received**. No ranking
change was needed, and the one I had drafted would have been a fix for a
problem that does not exist.

**It costs ranking time, which is the real price.** Walkable edges go 340,626 →
617,834, and `_frontier_priority` runs ~10ms per candidate against the index
(EdgeIndex build 0.53s). A full 2,000-deep queue therefore costs ~20s of the
150s walk budget where it cost ~5s. The walk stops on time, so that is fewer
lookups per run, not a failure — but it is the thing to watch if discovery
slows.

**The classifier change is forward-looking.** `unpriced` tokens are not in the
substrate yet — nothing re-fetches what the cursor has passed. The +277,208 is
entirely `price_unavailable` majors already on disk.

**A day's substrate is a SET of files now, because one file could not be
bounded (fixed 2026-09-16).** Run 35082536547 swept 25 wallets, stored 345,100
genuinely new records — the `known_ids` dedupe correctly held back all 615,375
already held — and then died in `Commit and push`:

    [size] OVER LIMIT data/transfers/ethereum/2026-09-16.json is 233.74 MiB

`check_repo_size.py` refused the push, nothing was committed, and **1,229 API
calls and 25 minutes were lost**. The guard did its job; the design behind it
did not. A day's collection was one file, so the only bound on that file was
how much the sweep happened to collect — and `--reset` collects everything.

`append_transfer_records` rolls to `<day>.p2.json`, `<day>.p3.json` at
**40 MiB**, which leaves room for a further whole append on top of a shard
already at the bar. Nothing downstream changed: `substrate_files` keys a day
off the filename, so a shard is just another day to every reader, and
`compact_transfers` rolls each to its own `.jsonl.gz`.

Two properties, either of which re-creates the bug if missed:

- **A single append is itself split.** Rolling only BETWEEN appends leaves the
  hole open — one wallet-chain can legitimately return
  `max_pages_per_kind` x `page_size` x 3 kinds = 150,000 rows, ~102 MB at the
  measured 683 bytes a record, on a fresh empty shard.
- **Dedupe covers the whole DAY.** `append_records` dedupes against the one
  file it writes, so a naive split re-stores into `<day>.p2` a record already
  in `<day>.json` — the duplication `known_ids` had just removed, one layer
  down.

**And the volume estimate that led here was wrong, which is the lesson worth
keeping.** The frontier re-sweep was sized from the quarantine ledger's 332,636
`unpriced_token` count — but that ledger only ever recorded what INCREMENTAL
sweeps encountered. A full-history re-read hits far more: 25 wallets alone
exceeded the estimate for all 229. Extrapolated, the frontier is ~3.2M records
and roughly 2.2 GB. **A number measured under one access pattern does not size
a different one.**

Measured per wallet the distribution is heavily skewed: a single-wallet reset
of `0xf078969e…` (CONFIRMED) cost **27 API calls and stored 1,930 records** in
two minutes, where the 25-wallet batch stored 345,100. Re-sweeping is cheap
per wallet and expensive in aggregate, so **target it rather than sweeping the
frontier wholesale.**

**Keeping unpriced records opened a hole, and the fix is the same signal
pointed the other way (2026-09-16).** Address-poisoning was always caught —
`forged_side` compares addresses. SYMBOL poisoning never had to be: a token
calling itself `UЅDС` with a Cyrillic Ѕ and С is in no registry, so it priced
as `unpriced` and was quarantined for that reason. Once unpriced records became
worth keeping, the forgeries came with them.

Measured right after the first targeted re-sweeps: **314 records across 11
forged symbols** — `USDС`, `UЅDС`, `ÚSDС`, `UЅDC`, `ÚЅDС`, `USḌC`, `EТH`,
`ЕТН`, `UЅDT`, `WBТC`, and one padded with invisible U+180E. Their value stays
None so rule 2's money protection held and no dollar figure moved, but they
become graph EDGES linking a wallet to whoever sent the poison.

**A confusable fold is WRONG for pricing and RIGHT for detection**, and each
direction is only safe because the other exists:

- **Pricing never folds.** `USDС` folded onto `USDC` prices a counterfeit at
  par — the $3.07B lesson. `SYMBOL_HOMOGLYPHS` stays a measured map with its
  one entry (U+20AE, Tether's own `USD₮0`), and a test pins that the Cyrillic
  case must NOT fold there.
- **Detection folds hard.** `confusable_fold` strips combining marks, maps
  look-alike Cyrillic/Greek letters to Latin, and discards non-alphanumerics.
  A symbol that folds onto a ticker we price while not BEING that ticker is
  disguised on purpose.

**Equality, never containment — an earlier version used a substring and flagged
every legitimate DeFi token.** `aUSDC`, `gtUSDC`, `variableDebtEthUSDC`,
`yDAI+yUSDC+yUSDT+yTUSD` and `vAMM-WETH/USDC` are real Aave, Morpho and Yearn
tokens that merely CONTAIN a ticker; they fold to themselves. Replayed live:
**314 caught, 34,373 genuine unpriced records kept** — full precision, nothing
real lost.

`quarantine_impostor_tokens` checks it too, because nothing re-reads a stored
record's classification except that pass: without it the 314 already on disk
stay graph edges for ever.

**A classifier that destroys its input needs the same evidence bar as an
alert.** Both defects were invisible for months because the only trace left
behind was an address and a count, in a file nothing read.

**Still not fixed, and made worse by this:** `_load_cached`'s 256 MiB ceiling
counts FILE bytes while holding PARSED objects. With the substrate 6x smaller
on disk the ceiling stops being protective — 68 MB of `.gz` parses to the same
~575 MB of heap it always did.

**Two general rules.** A repo that commits its own output on every run has a
size budget, and nothing was watching it — the check is cheap and belongs in CI
before the push, not in a pre-receive hook's rejection. And when a file has to
shrink, ask **who actually reads the part that is large**: here the answer was
"nobody, across runs" and the 92% reduction cost no consumer anything.

**Keeping unpriced records let airdrop spam become graph edges (fixed
2026-09-16, the same day).** The first trace run after `ea1c20f2c` (11:02 UTC)
replaced **161 of 299 graph nodes** and graded the Ethereum USDC contract,
vitalik.eth and Polygon's USDT/USDC/WETH contracts as OPERATIONAL_COUNTERPARTY,
with 129 zero-confidence nodes hanging off inbound airdrops to the CONFIRMED
`0xf078969e…` (swept with `--reset` that morning) — `$ USDCNotice.com <- Visit
to secure your wallet`, `BITLORD`, `SPXDOG`. At ~0.1 confidence they
outranked genuine zero-confidence leads in `detector_candidates`.

**An ERC-20 Transfer's `from` is whatever the token contract writes.** For a
token we know nothing about that is the contract's claim, not an observed act —
rule 2 at the level of an edge. `normalise_transfer_record` now admits an
`unpriced` record only when its SENDER is a swept wallet (its own outbound).
The substrate keeps every record, so the operator decision stands, and an edge
returns the day its sender is swept. `price_unavailable` majors are never
gated. Replayed: those contracts leave the graph and nodes backed only by
unpriced tokens halve, **157 → 79**. The write-up above that "every node that
swapped out had $0 direct flow" was true and was the wrong question: what came
IN was the harm.

**And the node budget was admitting by FILE ORDER (fixed 2026-09-16).** Found
while measuring the above: `build_graph` walked each BFS level in adjacency
order, so once `max_nodes` bit, the order edges were read off disk decided who
entered the graph — and the roster, and every detector budget behind it. Each
level is now offered whole and admitted by value moved with the level above,
unvalued after valued, address last. Replayed old vs new with production
inputs: **depth-2 value $1.84B → $2.25B, wallets at $1M+ 50 → 73, $0 wallets
114 → 73**, key wallets unchanged, 0 alerts selected either way. **The
`expanded_ledger` rule, fourth time: when a cap trims a collection, ask what
the sort order MEANS.**

**The substrate outgrew its own cache, and the linkage phase was reading it
once PER WALLET (fixed 2026-09-16).** `trace.yml` failed on 4 of its last 10
runs — 2026-09-14 02:28, then 09-15 16:02, 19:44 and 09-16 00:53 — every one
of them "Rebuild transfer graph has timed out after 10 minutes". A failed step
SKIPS the steps behind it, so each failure also cost the six detectors that
follow the graph: identities, agents, dormancy, portfolio overlap, the roster
and the accounting.

The log looked like a hang and was not. `_phase` timed expansion (99.3s),
bytecode labelling (9.8s), activity verification (10.3s) and deposit inference
(0.4s) — and then printed **nothing for seven minutes and fifty-two seconds**
before the final summary, which it reached one second before the kill. The work
was finishing and being thrown away on the line.

The unmeasured phase is `_substrate_linkage`. It called
`get_outbound_addresses` per wallet, which calls `records_for`, which walks
**every record on every chain** to find the ones touching one address. So the
cost is O(swept wallets x whole substrate) and **both terms grow on their
own**: the frontier took swept wallets from 61 on 09-12 to **208** on 09-16,
and `data/transfers/` gains a file a day per chain and is never pruned.

What turned a slow phase into a failing one is a cliff. `_load_cached` holds
parsed files under a **256 MiB** ceiling; the substrate reached **394 MB**, so
the LRU can no longer hold a single pass and every wallet re-parses most of the
files. Measured on the live data: **9.519s per wallet against 0.263s with
eviction disabled, a 36x penalty.** The phase cost 344-472s of a 600s cap while
every other phase together cost ~130s — so the step sat exactly on the boundary
and crossed it as the substrate grew, which is why it failed intermittently
rather than always.

`collect.records_by_wallet` now answers for many wallets in ONE walk — a
sibling of `records_for` rather than a second implementation, for the reason
that function's docstring already gives about three spam rules. Measured
end-to-end on live data: the phase goes **472s → 66.2s**, of which the walk
itself is **5.5s for all 208 wallets**. Equivalence was checked against
`records_for` on nine wallets including the target: identical record ids in
identical order, identical outbound sets, 0 mismatches.

**Raising the cache ceiling is NOT the fix, and that is the general rule here.**
It buys one step against a substrate that grows daily, and the ceiling counts
FILE bytes while holding parsed objects — 394 MB of JSON measured at **575 MB
of heap**, so the real cost of the knob is not the number written on it.
Reading the substrate once removes the wallet term instead, which is what makes
the phase indifferent to how large the substrate gets. **This makes the
un-compacted `data/transfers/` above less urgent for linkage and no less urgent
for everything else** — the frontier, the tracer, the correlator and four
scripts still call `records_for` in a loop, bounded by budgets rather than by
being cheap.

**And the instrumentation rule was scoped on the wrong property.** `_phase` was
applied to the four phases that make external calls, on the reasoning that
those are the ones that can block. The phase that actually failed the step
makes no external calls at all, which is exactly why it was invisible for eight
minutes. It is now timed. **Time a phase because it can be slow, not because it
can block.**


**One failing step took five others down with it, and the reasoning to prevent
that was already written one step away (fixed 2026-09-16).** A workflow step
with no `if:` carries an implicit `success()`, so a single failure skips the
whole remainder of the job. While the graph step was timing out on 4 of
trace.yml's last 10 runs, **each failure also skipped the six detectors behind
it** — identities, agents, dormancy, portfolio overlap, the roster and the
accounting. `Commit and push` already carried the right rule in its own
comment — "a step that failed costs its own reading, not the whole run's" —
and it had never been extended to the detectors it was written about.

That is the stalled-frontier shape again: nothing in a run summary says "six
detectors did not run". The job goes red for the one step that failed, and the
five silent absences look like nothing happening.

Every detector step in `trace.yml` (16), `scan.yml` (3) and `analyze.yml` (10)
now carries `!cancelled() && steps.deps.conclusion == 'success'`. Three
deliberate choices:

- **`!cancelled()`, not `always()`.** `always()` also runs while the job is
  being CANCELLED, when the runner is tearing down and the seconds left belong
  to `Commit and push` — which keeps `always()`, because persisting what
  exists is the thing worth doing in every state.
- **Still gated on the environment.** A failed `pip install` is not an
  independent sibling, it is a broken interpreter, and sixteen ImportErrors is
  noise rather than resilience. Hence `id: deps` and a named gate.
- **Failures are not hidden.** A step that runs and fails still fails the JOB,
  so `failure()` still opens the issue and the run still shows red. Only the
  cascade is removed — and `continue-on-error`, which WOULD hide it, is
  asserted absent.

Safe because `utils.save_latest` is write-then-rename: a killed step leaves
`latest.json` wholly old or wholly new, never truncated, so a later step reads
a stale-but-valid file — the documented "a file not updated this run keeps the
previous value" case. **Had the write not been atomic this fix would have been
wrong**, and that is the thing to check first next time.

**One step is deliberately NOT independent, and it is the reason to reason
about each one rather than paste the condition in.** `analyze.yml`'s
`Reprice stored transfers` prices by SYMBOL, and its only defence against a
counterfeit is `_needs_price`'s `not rec.get("spam")` — **the flag the
Quarantine step immediately above it sets.** Run it after a FAILED quarantine
and impostor records are looked up by ticker and booked as real money: rule 2,
which once booked $3.07B of counterfeit value, reached this time through a CI
condition. It stays gated on `steps.quarantine`.

The distinction worth keeping: every other step here loses only FRESHNESS when
a predecessor fails — "runs after repricing so exits are valued" means a
correlator that sees fewer priced exits, which is yesterday's answer, not a
wrong one. That one loses CORRECTNESS. **Ask which of the two a step loses
before making it independent.**

**Looking for the cascade found the same rule broken at the other end, in the
two most expensive places.** `backfill.yml` and `substrate-backfill.yml` were
the only committing workflows whose `Commit and push` lacked `if: always()` —
and they are the LONGEST jobs in the repo (48-60 minutes) and the ones that
advance sweep CURSORS. A step failure fifty minutes in therefore discarded
every cursor the sweep had advanced, so the next run restarted from block 0.
That is the livelock `--reset` was fixed for and that
`test_chain_budget.py` reasons about for job CANCELLATION, still wide open for
the ordinary step failure. All seven committing workflows now persist what
they finished.

**And the suite's own CI had the same cascade, which is how it was found.**
`test.yml` ran `Lint` before `Tests` with no condition, so **one unused import
failed Lint and the 1362-test suite was skipped — on two consecutive pushes,
including the one fixing all of the above.** Both went red for a lint error
while nothing in CI had run the tests at all. "Fail fast" saves about three
minutes of runner time and costs the answer to the only question CI is asked;
`Lint`, `Tests`, the dashboard's `Unit tests` and its `Build` now all report
independently. **Run `python -m ruff check src/ tests/ scripts/` before
pushing — the suite passing locally is not the same as CI being green.**

`tests/test_workflow_step_independence.py` pins all of it — and every
assertion was checked by breaking the thing it guards and confirming it names
the offender, because a workflow test that has never failed is only a claim.

**The HL account surface: three endpoints nobody asked the candidates (built
2026-09-16).** `subAccounts`, `referral` and `userVaultEquities` were read for
the target (collector) and the ≤6 watched wallets, and nowhere else. Measured
over the 25 strongest roster wallets before building: **31 sub-account
addresses under 5 of them, none in the roster, the graph or the identity
cache**, one — "funding-test" under `0x7fdafde5…` — holding $1.26M. A
sub-account is an address the owner can copy on Hyperliquid directly: the
deliverable's exact shape, and nothing read it. Live first run: **120 wallets
in 172.6s, 0 unreadable, 35 sub-accounts, 7 referral pairs.**

What each finding is worth, decided on rules already in this file:

- **A sub-account** exists only because its master created it. Between a
  cluster wallet (config only — target + `known_self_wallets`) and anyone
  else, it is `explicit_link` and CONFIRMs alone, alerting CRITICAL. Among
  candidates, `roster.apply_operator_groups` tiers a master and its
  sub-accounts on the UNION of their vectors — each vector still comes from
  its own detector, attributed to accounts one person provably runs. Services
  neither lend nor borrow. A sub-account gets a roster row at ≥$1,000, and
  EVERY sub-account of a PROBABLE-or-better group gets one: an empty
  sub-account of his is where he would move next.
- **A referral** is chosen by whoever types the code: association, never
  control. With the cluster it is ONE vote (`VECTOR_REFERRAL`), alerting HIGH,
  and only on a QUIET code — ≤10 accounts, **measured, never assumed**; an
  influencer's code links everyone who used it (rule 9), and one of the seven
  referrers had 5,000. Between two strangers it is `referral_pair` evidence,
  and on a quiet code the other side gets a roster row, so every per-wallet
  detector starts asking about it.
- **A vault deposit** is evidence only, HLP and `hl_shared_destinations` removed.

**Reads go through `cctp_feed.strict_post`, never `utils.hl_post`.**
`hl_post`'s failure sentinel for `userVaultEquities` is `[]` — byte-identical
to "deposits into no vault" — so a timeout would have serialised as a measured
absence. A failed read is `null` in `readings` and counted; `[]` is a real
none. A failed alert is queued in `undelivered_alerts` and retried, because
diffing against the previous report alone would lose it — the link is already
on file by the next run. An absent previous report counts as EMPTY (the
size-ratio decision, not the `extraAgents` one): the cluster had no such link
when this was built, so there is nothing to seed and nothing to swallow.

**What its first run found — a habit, not the target.** Five of the seven
referral codes have ≤3 accounts on them, and in **three pairs the referrer and
the referred account were born on the SAME DAY**:

| referred (roster) | referrer | born | code users | referrer holds |
|---|---|---|---|---|
| `0x5b5d5120…` (the $230M ex-PROBABLE) | `0xb83de012…` | 2025-02-26 | 3 | **$77.7M perp + $99.1M spot** |
| `0x12e16e3d…` (top risk candidate) | `0x264fe26f…` | 2025-09-17 | 1 | $8K spot |
| `0x498216a2…` | `0x498255da…` (same `0x4982` prefix) | 2026-07-29 | 1 | $4K |

**All three referred wallets are the ones sharing the target's original funder
`0xf92402bb…`** — three of the four wallets on that vector. Opening two
accounts on one day and referring one from the other is an operator's habit,
and it recurs across exactly the population linked to him. It does NOT link
any of them to the target: he has no code and no referrer, and a habit shared
by strangers is not identity. But **`0xb83de012…` is a ~$177M Hyperliquid
account almost certainly run by whoever runs `0x5b5d5120…`**, and nothing
watched it. Whether it joins `config.watch_wallets` is the operator's call.
(Correction, same day: the "funder" these three share is an exchange hot wallet
with 2.28M transactions — so the population is "withdrew from the same exchange",
not "linked to him". The same-day referral habit is still real; its link to the
target was never there.)

**The first PROBABLEs after the backtest passed were all false (fixed
2026-09-16).** The backtest passed at 17:10 and the 20:17 roster counted the
behavioural vector for the first time: **PROBABLE 1 → 7**, and those seven
filled the close watch. Every one of them was false, for three independent
reasons — each a rule this file already states, broken one call away from where
it was enforced.

1. **A shared funder was never measured (rule 9).** `compute_linkage` checked
   every shared DESTINATION against whole-chain activity and never checked the
   shared FUNDER beside it. The target's first funder `0xf92402bb…` has
   **2,282,986 Arbitrum transactions**; five wallets held a linkage vote for
   having withdrawn from the same exchange. Now both `substrate_linkage` (which
   measures the funder FIRST, because one reading decides a vote for every
   wallet sharing it) and the roster's `funder_exclusions` require a measured
   quiet funder; unmeasured is excluded until measured. Funded directly BY the
   target needs no measurement.
2. **The behavioural vote read scores the validated scorer never produced.**
   All seven voting wallets carried a **single scan from 2026-06-30 or 07-01**
   — no `order_profile` dimension, before the flat-wallet leverage fix — never
   re-scored because a wallet that leaves the scan population never is. The
   same June number was `risk.py`'s top candidate and the "trades like the
   target" line in the graph's CRITICAL email. `persist_candidate` now stamps
   `scoring_schema`; `utils.candidate_scored_by_current_scorer` gates the
   roster vote, risk's top candidate, the graph's behavioural evidence and the
   tracer's combined alert. The stored scores stay on disk and on the
   dashboard; they just decide nothing.
3. **Two hops through a stranger voted `transfer`.** The vector was
   `transfer_count > 0`, true of every node the walk reaches. **110 rows** held
   it at depth 2 with $0 direct flow, two of them PROBABLE: `0x3b2d7db2…` and
   `0xddea9827…`, paired with a dormancy handoff. Both are accounts of **a
   market maker** — referred through code `MMREFCSI`, running code `XYZSET9`,
   agents `XYZ_SET11`/`APTS`, 100% client order ids, quoting `xyz` equity
   perps — reached through its hub `0x84abc08c…`, which the target paid
   **$999,999.80 once, on 2026-09-10**. Every one of the group's 123 decoded
   CctpExtension deposits credits its own account. A market maker opens
   accounts constantly, so some are born inside his silences.
   `roster.transfer_touches_cluster` now requires an observed transfer with the
   target, or a path whose previous hop is a config `known_self_wallet`; reach
   alone is kept as `graph_reach_only` evidence (rule 7) and does not vote.

Replayed on the live data before landing: **PROBABLE 7 → 0, POSSIBLE 140 → 25,
WATCH 41 → 159**; both CONFIRMED wallets unchanged; `0x12e16e3d…` → WATCH with
no vectors and its four sub-accounts leave the roster. The close watch also now
ranks with `roster.rank_key` rather than graph confidence (0.0 by construction
for any non-graph vector — the defect `evidence_strength` fixed, left standing
in `watched()`) and gives an operator group one slot, not five.

**The honest state is that nothing outside the config cluster is PROBABLE.**
That is a finding, not a failure: a tier nobody can defend spends the close
watch on the wrong wallets and teaches the operator that PROBABLE means nothing.

**Two leads worth keeping from the same investigation.**

- **`0xda0932d2a880bafa82bc2ac41ab0caafc5544f52` is the only outsider ever to pay
  his private Binance deposit address `0x8570c2ae…`** — $249,993.84 on
  2024-07-31. Its whole life is that day: gas and $249,999.84 USDC from two
  exchange hot wallets (~2.8M transactions each), five `buyExactShares` calls to
  a FixedERC20Pool, a **$6 deposit into Hyperliquid** (its HL account is born
  that day and has never traded), then everything to his deposit address. A
  deposit address belongs to one exchange account, so this is his account being
  funded — by him through a fresh wallet, or by someone paying him. It never
  traded, so it is not a copy target. **Nothing surfaced it** because
  `0x8570c2ae…` is graded a conduit SERVICE and linkage scores only SWEPT
  wallets' outbound — the deposit address's own sweep holds every sender, and
  nobody reads that side. A sentinel on his private deposit addresses (new
  sender → CRITICAL) is the vector this points at; the treasury paid into
  `0x8570c2ae…` as recently as 2026-08-15, so the address is live.
- **Settled no: `0x9430801e…`** sends ETH to five of his conduits across three
  chains, which looks like gas funding by him. It has 1.4M Arbitrum and 3.6M
  Ethereum transactions: an exchange gas feeder.

**$31.8M of his went to Monad in the month to 2026-09-15, and nothing could see
it (found 2026-09-16).** `0xf078969e…` (CONFIRMED) burned USDC through Circle to
**domain 15** five times — 08-15 $5.0M, 08-27 $6.8M, 08-28 $7.0M, 09-11 $7.0M,
09-15 $6.0M — each minted to its own address. Nothing decoded that wallet's
bridging (config wallets only) and the decoder's hand-kept domain map stopped
at "domain-15". **Domain 15 is Monad** (Circle's own table). Read straight from
`rpc.monad.xyz` (chain id 143): the wallet holds **0 USDC and 217,812 MON** and
has signed **18 transactions** there — so the money was moved on inside Monad,
to somewhere this project cannot yet see. Earlier it used Unichain (domain 10,
2025-04/05) and Socket the same way.

**Followed the same evening, and it is a yield loop back into the SAME account —
not a migration.** Monad's public RPC caps `eth_getLogs` at 100 blocks, so each
tranche was followed from its known burn time: four of five were minted to
`0xf078969e…` 16-25 minutes after the burn and **supplied in full to Aave V3 on
Monad** within minutes (`Pool.supply`, pool `0x69a5f9ad…`, aToken `aMonUSDC`
`0x35a73bac…`). The fifth (09-11) did not mint within the hour scanned. The
wallet holds **$11.82M aMonUSDC** now, so ~$20M came back out. The substrate
shows where: Circle mints back to `0xf078969e…` on Arbitrum/Ethereum, each
followed within a minute by the TARGET's own CctpExtension deposit into his own
Hyperliquid account — 08-27 16:49 **$5,005,143.73** back ($4,999,590.26 sent
08-15 plus ~$5.5K of twelve days' interest), deposited 16:49:56 as $5,005,143.75;
09-14 20:36 $6,000,000 back, deposited 20:37. The outbound legs start the same
way: a Circle withdrawal from HL mints at the target's Arbitrum address
(08-28 21:34, 09-11 03:16, 09-15 22:21) and `0xf078969e…` burns the same amount
to Monad three to seven minutes later. **Idle trading capital parked in lending
between trades, returning to the account the owner already copies.** The
lesson for the risk score: capital leaving HL in size is his routine treasury
management, so an outbound flow is not by itself a migration signal — follow
it to where it lands. Built the
same day: the full Circle domain table, bridge decoding for CONFIRMED wallets
(history as a baseline), foreign landings alerting once instead of every 72h,
and Monad in `config.chains` with its USDC contract registered FIRST (read
on-chain) so a counterfeit there cannot be priced at par. **Whether Etherscan's
free tier serves chain 143 is measured by the first sweep** — read
`unsupported_sources` before assuming the Monad substrate is complete.

**$84.6M of counterfeit value was still priced as real (fixed 2026-09-17).**
Found by asking what `0xf078969e…`'s large EOA counterparties were: several
were vanity look-alikes of the real ones (`0x5c2ccbdf…a210f` beside
`0x5c2c1aa8…a210f`, `0xffd6d9df…91636` beside `0xffd62ae3…91636`,
`0x8579b784…0eb68e` beside his own deposit address `0x8570c2ae…0eb68e`) — and
the substrate booked them as paying or being paid millions. Two holes in rule 2:

- **Polygon "USDT" had no registry row**, so the ticker priced at par from any
  contract: 15 contracts, $54.3M, `0x26c68e12…` (supply one trillion — the
  counterfeit signature the registry's own comment already named) alone $53.7M.
  It was left out because Tether's contract reads `USDT0`; that contract IS
  Tether's Polygon USDT after the migration, so it is now filed under both
  tickers and everything else is an impostor.
- **An ERC-20 calling itself "ETH" priced at ETH's close.** Native ETH has no
  contract on Ethereum, Arbitrum, Base or Optimism: 25 contracts, ~$30.5M, one
  sender each. `assets.CANONICAL_CONTRACTS` now files ETH there as `native`
  only (Optimism's legacy OVM ETH predeploy excepted), so `is_impostor` catches
  every contract and never a native record.

Dry run of `quarantine_impostor_tokens.py`: **316 records, $84,635,116.82**.
The same stored-record pass analyze.yml runs daily applies it.

**`0xf078969e…` joined `known_self_wallets` (2026-09-17, operator-delegated).**
The evidence is no longer an inference stack: two-way with the target at
$135M/$148M, pays the same private Binance deposit address as the target and
the treasury ($114.8M), and runs the Circle round trip that parks his idle HL
capital in Aave on Monad and returns it to his own account. As config ground
truth it gets the collector's explorer read (a send or withdrawal to an outside
address alerts CRITICAL), spam immunity, Circle-withdrawal pairing, and the
hop-through-a-config-wallet transfer vote.

**That vote now needs real money (`self_flow_usd` ≥ $1,000).** Replayed before
the promotion, the path-based rule would have lifted 72 wallets WATCH →
POSSIBLE; 56 of them had moved $0 or dust with `0xf078969e…` — poisoners,
including vanity look-alikes of the target (`0x45d27089…`, `0x45dc921e…`,
`0x45dd380d…`). The graph now records each node's valued flow with his config
wallets and the roster requires $1K; the smallest genuine counterparty moved
$97,430. Adjacency is where a poisoner lives, not evidence.

**When a rule is enforced in one branch, check the branch beside it** — the
funder beside the destination, the scorer beside the backtest, the reach beside
the transfer. All three were found by asking why a PROBABLE was PROBABLE.

**Four silent defects, found while probing for new vectors (fixed 2026-09-17).**

- **A conversion to multi-sig would have been thrown away.** The collector has
  asked `userToMultiSigSigners` since 2026-09-10 and read the answer as a list.
  It is `null` or `{"authorizedUsers": [...], "threshold": n}`, so a signer —
  an address that can act for the account, which is identity — could never
  have been recorded. `agent_links.multisig_signers` separates null (none) from
  anything else (unreadable), the close watch now reads signers as agents, and
  the explorer parser and the relay recognise the two multi-sig actions.
- **The watch priced the target $6.7M light.** `check_watchlist.account_value`
  summed the main dex's margin and spot USDC; a HIP-3 dex keeps its own margin,
  so live it read him at **$59.6M against $66.3M held**, inflating every watched
  wallet's size ratio ~11% towards the 1.15 band. It now uses
  `utils.account_value_components` (perp + every HIP-3 dex + spot USDC), and a
  failed read raises instead of counting as $0.00.
- **`utils.hl_post` never raises.** When every retry fails it answers `[]` if
  the request type contains "user", else `{}`. The watch read `{}` from
  `extraAgents` and `subAccounts` as "none". Each read now checks the answer has
  the shape a real answer has. **Before trusting an empty result from
  `hl_post`, ask which sentinel that request type gets.**
- **"Stopped trading xyz" cost 5 risk points while he held the xyz book.** His
  last xyz fill was 2026-08-14; four xyz positions (~$13.2M notional) were
  still open. Holding is not leaving: `risk._xyz_abandoned` is now False while
  `data/account/latest.json` shows an open xyz position.

**An exchange or a contract could reach the operator's phone, and eight did
(fixed 2026-09-22).** The owner asked for "only the most highly probable
wallets" and, reading the live roster to answer, the list he was being shown
held **five exchange-scale addresses** (110,535 to 929,361 transactions) and
**three named contracts** — `DeusdMerkleDistributor`, `BoringSolver`,
`GnosisSafeProxy`. Each was POSSIBLE on a real `transfer` vector, and each was
put to him as a wallet that might be his.

Neither kind can be caught from inside our own substrate. Fan degree only sees
wallets we swept, so an exchange the cluster paid twice never trips it — rule 9,
which already cost a false PROBABLE on a funder with 2,282,986 transactions —
and **code is not a fan-out pattern at all**, so a Gnosis Safe with 35
transactions looks exactly like a quiet personal wallet. Only the whole-chain
reading answers, and `roster.services_from_activity` now applies both verdicts
at tiering: busy → service, contract → service, config ground truth immune.
Live: candidates **35 → 27**.

**A contract flag must NOT bury an address Hyperliquid knows as a trader.**
The first version of the guard graded `0xb798aef7…` INFRASTRUCTURE — and that
address is an **EOA on Arbitrum (108 txs), a contract on Ethereum (649 txs),
and a live HL account holding $9.7M with 2,000 fills**. One address can be
both, and code on some other chain does not unmake a trading account. Removing
it would have deleted precisely what the mission is for. Caught by replaying
the guard against the live roster before it landed, never by a unit test —
**replay a filter against production data before trusting it**.

**And nothing was measuring the candidates.** 18 of the 35 had no reading on
any chain, because the graph spends its measurement budget on the
highest-VALUE unmeasured addresses, which is a different set from "the wallets
the operator is being shown". `scripts/measure_candidates.py` gives the
candidate list its own bounded pass (15 addresses / 90s, keyless Blockscout),
wired into trace.yml before the roster build.

**The phone lists only wallets Hyperliquid has heard of (2026-09-22).** The
deliverable is an address that trades on HL, and **18 of 33 leads on the phone
had no HL account at all** — nothing the owner could follow. `review.js`
excludes `hl_role == "missing"` and keeps an unreadable role (rule 5: failed is
not absent), counting both in a footnote. They stay in the roster, where one
opening an HL account is itself news.

**The detector budget was being spent off-Hyperliquid.** `detector_candidates`
ranked by evidence alone, so the shared-agent index — the one vector strong
enough to CONFIRM a wallet alone — covered 120 wallets and **missed 6 of the 13
leads that actually hold HL accounts**, while carrying addresses with none.
Wallets HL knows now sort first, unreadable roles rank with them, and an
address with no account is deferred rather than dropped. Live: HL leads inside
the cap **7/13 → 15/15**.

**What asking every HL-native vector about those leads actually returned: a
measured no (2026-09-22).** Across the 13 HL-present leads plus the config
cluster — `extraAgents`, `webData2`, `subAccounts`, `referral`, `portfolio`,
`userFills`: **0 shared agents, 0 shared agent-name families, 0 sub-account
crossings, 0 referrals by a cluster wallet.** Three leads carry someone else's
referral code (`XYZSET9`, `REF8888GO`, `MMREFCSI`) and none is his. Two leads
run agent fleets — `0xf5d81a135f` with **102 agents and 20 sub-accounts**,
`0x60a8c761f3` with 10 — which is a market maker's shape, not a person's.
So the strongest vector in the project has now been asked about every lead that
can be the deliverable, and the answer is no. **Nothing outside the config
cluster is above one vector.**

**A new agent OF HIS was never announced, and that is the CONFIRM-alone vector
(fixed 2026-09-22).** His frontend agent is now
`0x6f4e393f490f8b5f8bfb63eb44fe9d6bd2f0f191` where this file recorded
`0x98cf3fee…` approved 2026-08-31, and **no agent alert exists in the last 60
delivery shards**. `collect_agents` has stored the answer since 2026-09-10 and
nothing ever diffed it: `watchlist.changes` is the project's ONLY agent diff,
and the target is deliberately outside the close watch because a second writer
on `data/actions/` is a lost update. So the one signal strong enough to confirm
a wallet alone was being recorded and thrown away for him.

`collector.new_agents` diffs the stored record and `alert_new_target_agent`
fires on ADDITIONS only — an agent falling off is an expiry, and an unreadable
endpoint presents identically, so alerting on a disappearance would fire on our
own blindness. An absent previous record is a baseline (the `extraAgents`
seeding rule; the size-ratio exception does not apply, because an agent is a
fact about the past rather than a live state nobody has been told). A NAMED
agent (`extraAgents`, an API wallet) is CRITICAL; the unnamed frontend agent is
HIGH, because it rotates when he signs in again — measured twice in the week to
2026-09-22, and a weekly CRITICAL for logging in is how an operator learns to
swipe the channel away.

**And the same rule-5 hole was in the collector, one call from where it was
already fixed.** `collect_agents` called `parse_web_data(hl_post(...))` with no
guard, so an exhausted-retry `{}` answered `agent_address: None` — recorded as
"he has authorised nobody". `agent_links.webdata_is_unreadable` has existed
since 2026-09-12 for exactly this, and the collector never used it. The stored
history shows it: his agent record reads `none` between two real agents.
**When one call in a pair is guarded, ask what guards the other** — third time
this file has recorded that sentence.

---

**The execution-program vector — his slicer is a script, not manual TWAP
(2026-09-29).** CLAUDE.md had called his execution "manual TWAP" since
2026-09-10. Measured over his 168,170 stored fills / 92,402 reconstructed
orders: **53,630 of 57,580 historical orders are taker `Limit`/`Ioc`, no client
id, `reduceOnly` false, with the limit priced EXACTLY 5.0% through the book**
(99.4% within ±0.3%). That is the hyperliquid-python-sdk `market_open` default,
`DEFAULT_SLIPPAGE = 0.05` (verified in the SDK's `exchange.py`, which sends
`{"limit": {"tif": "Ioc"}}` at `ref * (1 ± slippage)` and no cloid). He closes
with `market_open` too (`reduceOnly` false, not `market_close`). The program
fires **one round base size per coin** — his CLIP TABLE — at a ~1.7s median
cadence (p10 ~1.55s) in runs of tens to 3,000+ orders. The treasury
`0x1419e75…` shows the manual precursor of the same habit: 37 × 500 HYPE
`FrontendMarket` orders ~2.5s apart. His ~8% `FrontendMarket` orders are web-UI
clicks. No `twapId` and no `builderFee` on any of his fills.

What travels to a new wallet is the clip table — the exact size per coin (ZEC 1,
SILVER 20, NEAR 250, BTC 0.1, HYPE 40, XRP 1000, LINK 100, PUMP 200000,
FARTCOIN 3000, DOGE 10000). Anyone can fire a 5% IOC; almost nobody reproduces
his whole size table. **Rarity measured 2026-09-28: 0 of 248 large active
accounts (≥$250k, top week volume) carried the full signature**; 5 ran a
constant-clip cadence only and 3 of those were manual clickers. A 10-minute
full-venue tape sample (658 markets, one websocket, ~52 trades/s, 3,133 wallets)
held 131 taker sequences ≥15 orders, 3 with constant clip and cadence, 0 with
the full signature. Constant clip alone is not specific; the clip table plus the
5% offset is.

Built as a first-class vector: `src/execution_program.py` (signature extraction
and clip-table comparison, self-compare 1.0 on his real data, recovering his
38-coin table), `scripts/check_execution_program.py` (live detector in
`trace.yml`, before the roster so its matches vote the same run) and
`scripts/census_execution_program.py` (measures the population distribution in
`analyze.yml`, writing `data/execution_program/census.json`). Bound by the same
discipline as the fuzzy behavioural score: it is a BEHAVIOUR vote
(`execution_program`, category `behaviour`), so it corroborates a
financial/protocol vote and never reaches PROBABLE alone (rule 9 — a shared bot
or frontend is the confounder), and it casts NO vote until the census has
MEASURED the match rare (rule 4 — `is_discriminating` returns False without a
census, however perfect the match, and requires the ratio to beat the
population's 99th percentile over ≥3 shared clips). A missing/flat/unread wallet
is `insufficient_data`, never a 0.0 score (rules 5, 6). Alerts:
`alert_execution_program_match` routes CRITICAL when the wallet already carries
an independent financial/protocol vector (two vectors agree), HIGH for a
census-rare match alone; both are clamped to the routing vocabulary. 47 new
tests; full suite 1917 passing.

**Why this matters for the mission:** the linkage vectors all need him to leave a
connection — a transfer, a shared deposit address, an authorised agent. The
execution program needs none: a wallet funded from anywhere and traded by his
own script is caught by HOW it trades. It is the one net that catches a wallet he
kept deliberately clean, provided he keeps using the SDK. If he switches tooling
the vector goes silent — which is itself worth knowing.

---

**Builder/frontend fingerprint — measured and rejected as a discovery channel
(2026-09-29).** Hyperliquid publishes a keyless per-builder fills dataset at
`stats-data.hyperliquid.xyz/Mainnet/builder_fills/<builder>/<YYYYMMDD>.csv.lz4`
(columns: time, user, coin, side, px, sz, crossed, tif, counterparty, closed_pnl,
twap_id, builder_fee). His `0xf078969e…` approved the builder `0x1924b856…`
("BasedApp") on 2026-01-02, so the idea was: the set of accounts trading through
the same app is a small, enriching candidate pool.

Measured over 6 days (2026-09-22 → 27): the BasedApp feed carries ~200-260
distinct users/day, **623 distinct over the window** — small enough to check all
against his clip table with zero per-wallet API calls. **But his cluster is absent
from it** (target, treasury, `0xf078969e…` — none appear in 6 days), and the
target pays **0 builder fee on every one of his fills**, so he does not trade
through any builder and cannot appear in any builder feed. The channel therefore
cannot catch HIM unless he adopts a builder on a new wallet, which is not his
current behaviour. Not wired in. Worth knowing the feed exists: it is a free
source of fills-WITH-counterparties (which `userFills` lacks) should a future
vector need counterparties for the ~600 accounts that do use a builder. Re-check
only if the target starts paying a builder fee.

---

**Correlator self-loop cleanup via observed-return reconciliation (2026-09-29).**
The correlator surfaced his outbounds to Monad as unresolved "exits" that then
matched same-size deposits at other wallets, on the assumption they were his
Aave/Monad yield loop. `movements.reconcile_movements` now resolves a cluster
outbound as `self_roundtrip` when an equal-size deposit (within 2%) returns to his
own HL account within 45 days — an OBSERVED return, never an inference. Measured on
live data: **176 round-trips totalling $383M** now resolve as internal instead of
feeding the exit pool, with zero over-filter risk, because a genuine exit does not
come back (rule 7) — the "an outbound with no return stays an exit" test pins this.

A correction to the earlier framing: not every Monad outbound is a round-trip. The
specific $6M Monad outbound `0x7867fd62…` does NOT return size-matched, so it
remains a genuinely unresolved exit — and its $6M match at `0xeaad1c35…` is
therefore a real (if low-confidence, single-vector POSSIBLE) lead the mission wants
surfaced, not noise to suppress. Aggregated or split round-trips that do not
size-match are left as leads on purpose; catching those needs source-chain-attributed
deposits (a larger change with over-filter risk), deliberately not done.

---

**Position-handoff strengthened to a rarity-weighted BASKET signal (2026-09-29).**
`successor_hypotheses._handoffs` already matched his position reductions against a
candidate's additions on the same coin+direction within 3 days, one-to-one. But a
single-coin handoff (he sells BTC, someone buys BTC) is market noise, and the old
priority counted it the same as a distinctive multi-coin basket. `basket_handoff`
now clusters a candidate's handoffs into a 24h window and scores the coin set by
calibrated rarity (`calibration.market_rarity_bonus`, which compounds several coins
with diminishing returns and gives common coins zero weight). A BTC/ETH basket
earns nothing; his memory-chip pair book (long SP500/XYZ100, short MU/SKHX) moving
to one account together earns a real bonus that lifts that candidate's
investigation priority. Still `research_only`/`promotable: false` — it reorders the
research queue, it does not mint a roster tier, so no rule-4 exposure. Result rows
now carry `handoff_basket`. The behavioural handoff a copy-trader cannot fake is
now weighted by how distinctive the basket is, not merely whether one coin moved.

---

**Scale-invariant execution signature — hardening the flagship against rescaling
(2026-09-29).** The execution-program vector matched his EXACT per-coin clip sizes,
so the cheapest evasion is to rescale every clip (NEAR 250→500, ZEC 1→2), and the
recall test already showed his absolute sizes drift ~50% over months. Measured what
survives a rescale/drift: his per-coin clip NOTIONALS keep the same rank ordering —
Spearman rho of pre/post-cutoff structure ran -0.07, 0.26, **0.90, 0.89** across
splits (strong and rising toward recent windows), while exact-size match sat at
~0.5 and coin-set overlap at ~0.17. So the rank of his per-coin sizes is the
invariant, not the sizes themselves.

`signature` now carries `clip_notionals` (modal clip × typical price per coin);
`compare` adds `notional_structure_rho` (Spearman rank correlation over shared
clip coins, magnitude-robust — unlike raw cosine, which the biggest coin
dominates); `is_discriminating` clears the bar by EITHER the exact clip match
beating `ratio_p99` OR the rank structure beating a measured `rho_p99` (the
census now measures both distributions); `recall` reports which path recognised
each split. A uniform rescale preserves ranks exactly (rho 1.0), so a migrated
wallet that rescales its clips is still caught. Both gated by the census (rule 4),
so a non-discriminating rho self-disables. Recall on live data rose from
caught_rate 0 to 0.75 once the census populated; the recent-migration split is
recognised at rho 1.0. Full suite 1955.

---

**Copier-cohort sensor — built, measured, and found NO cohort (2026-09-29).** Idea:
his copiers are a sensor he cannot control; if a cohort shifts from his wallet to a
new account, that account is his successor. `src/copier_cohort.py` measures who
opens his positions shortly after him, guarded by a time-shuffled placebo, coin
rarity, lead-lag, and (rule 5) opportunities counted only within each candidate's
observed fill window.

First run said "cohort 0" but with follow=0 AND placebo=0 for all 116 — a coverage
artifact: top-volume accounts churn their newest ~2,000 fills in under a day and
never overlap his 60-day opening history. Fixed to hash-sample the leaderboard and
treat no-overlap as insufficient, then re-measured **validly**: 106 read, **65
measured** (≥8 overlapping opportunities), **cohort 0**. The strongest follower
matched **2 of his 61 openings** (follow 0.033 vs placebo 0.003) — noise. Nobody in
the measurable large-account population copies him, which fits his documented
anti-copy OPSEC (blurred positions, "do not copytrade me", visibility is -ev).

A real measured-no, correcting the earlier premature "untestable (21-day window)".
Bounds: 65 large accounts whose recent fills overlap his openings — not the whole
venue; sub-leaderboard retail copiers and busy accounts (whose 2,000-fill window is
too short) are not covered, and covering them needs the live trade tape, i.e. a
host. **The machinery is merged but deliberately NOT wired** (no cohort to watch;
phase 2 shift-detector not built). Re-run `scripts/check_copier_cohort.py` if the
tape ever becomes available; do not wire it on the current data.

---

**Live-tape discovery for the execution vector (2026-09-29).** With the always-on
host declined (staying on GitHub Actions), the real-time behavioural net is
recovered as a SAMPLER rather than a 24/7 stream: `tape.yml` runs a bounded ~150s
websocket window each ~20 min (driven by the keeper/PC dispatcher, added to both in
sync). `src/tape.py` reconstructs each taker's orders from the trade feed (which
names both sides) and `program_clip_hits` flags any wallet running a program-shaped
burst at one of his EXACT per-coin clip sizes — reusing the execution vector's own
`program_runs`. A hit becomes an `execution_program_tape` candidate the census-gated
detector examines fully next trace run; the tape raises no alerts of its own (a
single-coin live burst is a lead, not proof), so it adds coverage without a noisy
path. It reaches wallets off the leaderboard and off the roster with no per-wallet
API call — the population a migration lands in. Cadence was separately verified
already good: the keeper drives watch every ~10 min and trace every ~30 min right
now (the old 198-min cron figure is obsolete). Live smoke: 41 of his markets, 60s,
3,992 trades → 2,308 taker orders → 675 takers, 0 false hits in a quiet window.
Needs `requirements-stream.txt` (websockets); a trade in the gap between windows is
still caught later by the other vectors, not instantly.

---

**The frontier was dead for 15 of 17 days, behind an alarm that fired daily
(2026-10-04).** No wallet was expanded from 2026-09-16 to 09-27 or from 09-29
08:12 to this fix. `alert_discovery_stalled` reported it HIGH fifteen times, up
to 248.9h; every run's `health.expansion` read `failed`, 5 lookups, 0 wallets,
`could not read bsc, monad`.

Cause: the per-lookup call budget was `len(chains) * 3` — three record kinds per
chain — while a frontier sweep also spends a probe per Etherscan chain and, on a
Blockscout chain, a newest-block completeness check per kind. When Base and
Optimism moved to keyless Blockscout readers (09-26/27) a wallet active on the
usual chains spent all 21 calls (arbitrum 4, ethereum 4, base 6, optimism 6,
polygon 1) before BSC was probed. BSC and Monad were then budget-exhausted on
EVERY wallet; exhaustion is not a plan refusal, so they were DEGRADED, and a
degraded chain deferred the whole wallet — throwing away the chains just read,
re-queueing it, and failing identically next run. The same class as the
2026-09-10 unsupported/degraded merge, reached by a different route: a
deterministic per-chain failure plus all-or-nothing wallet accounting.

Fix (`transfer_graph.lookup_call_budget`, `tests/test_frontier_lookup_budget.py`):
the budget is the chain plan itself (probe + kinds for Etherscan, kinds +
completeness for Blockscout) plus one further page per kind — 53 calls for the
7 production chains, bounded so endless history still stops; and a lookup that
read SOME chains is kept and walked, its unread chains revisited within the hour
through the refresh schedule and still reported degraded. A lookup that read
nothing stays deferred.

Found during a tracing review the same day, which also measured what the dead
walk had left unexamined: of 992 direct counterparties (>= $1K) of the three
cluster wallets, 936 had never been swept — 109 contracts carrying $1.9B and 10
quiet EOAs carrying $48.6M. Hand-tracing four of them with keyless APIs found a
second private Binance deposit address (`0x841b9e4f…`, 2023, $39.4M, 12 txs
ever), a Hyperliquid-internal exchange deposit address (`0x4aecac3b…`, UENA
forwarded in 11s to hub `0x1f6093d3…`), a Binance -> fresh wallet -> cluster hop
(`0x68797748…`, $6M), and a wallet co-funded by `0xf078969e…` and `0x793a3e8a…`
within two hours (`0x734c9213…`, $5.5M into ApolloX/Aster) — none an HL trading
account, all reachable, none surfaced. The redesign this motivated is specified
separately.

**Rule:** a per-unit budget is derived from the unit's actual plan, never a fixed
multiple of something that later changes; and partial success is progress — a
unit that read part of its sources must keep what it read and retry the rest,
or one deterministic failure anywhere becomes a total outage.

---

**The trace engine, phase 1 (2026-10-04).** Built to the spec in
`docs/superpowers/specs/2026-10-04-trace-engine-design.md` after the frontier
review above. Three production dry runs (read-only, no sweeps, no alerts) shaped
it, and each found a real defect before anything was wired:

1. HL rows alone made token contracts look like people: `0x5d3a1ff2…` ($68M from
   `0xf078969e…`) and `0x6e4141d3…` were "HL accounts reached". An address seen
   on L1 now needs an L1 measurement (or the bytecode cache) to be quiet.
2. It ignored what the project already knew: `0x8570c2ae…` read as a quiet
   wallet because the deposit sentinels, graph conduits, roster INFRASTRUCTURE
   and the 320 wallets the frontier had swept were not passed in.
3. The shared-payee link fired 14 times through `0x160f6ef9…`, a busy personal
   wallet paid by mints, Binance hot wallets and WETH. A payee must now be
   measured single-purpose (≤ 100 txs, ≤ 4 distinct senders) and a boundary
   never counts as a sender.

Also: `chain.labels.infer_deposit_addresses` anchors its 24h window from the
FIRST inflow to the LARGEST forward, so a deposit address used for weeks (his
`0x841b9e4f…`, 2023) reads as a wallet; the engine judges deposit by deposit
(`patterns.l1_deposit_hot`).

Third dry run, 199s inside a 240s budget: 60 HL ledger reads, 25 whole-chain
readings, 321 addresses, 14,203 edges; found the HyperCore deposit address
`0x4aecac3b…` (hub `0x1f6093d3…`) from config alone, 7 quiet funders including
`0x68797748…`, and two co-funders of `0xffd62ae3…` (which paid him $18M) —
`0x5c2c1aa8…` paid it $4M two minutes after the cluster did. Asked of
Hyperliquid, none of `0xffd62ae3…`, `0x5c2c1aa8…`, `0x7eb59373…` is an account
(all endpoints answered empty): L1 trails for the engine's sweep units, not
results.

---

**Transaction legs measured, not built; a zero-counter trap fixed (2026-10-04).**
Phase 2 of the trace engine was to read every transfer inside his transactions
and their signers. Measured first: 117 contract-facing transactions read in
full via Blockscout (80 of `0xf078969e…`'s on Ethereum, 37 of the target's on
Arbitrum). Third-party recipients: `0x37305b1c…` (21 legs, 1.47M token
transfers), `0xe8736af1…` (6, 897K), `0x6efa3205…` (7) and three that appeared
once with 983–5,341 transfers — solvers and market makers filling his swaps.
Foreign signers: relayers (`0x7ddb0773…`, 218K txs; `0x99f5a2e5…`). No
single-purpose third party in the sample, so the vector is recorded as measured
and not built; built instead is an L1 refresh of quiet wallets still holding
his money (every 3 days, incremental from cursors).

One reading said a signer was quiet: `0x153e996e…`, 0 transactions on Arbitrum.
It is a Circle relayer calling `receiveMessage` every few minutes — Blockscout's
counter simply had not been computed. A zero for an address seen SENDING is now
treated as unmeasured in the engine and in `deposit_sentinels.classify`, where a
"quiet" sender pages CRITICAL.

---

**The trace engine pushed the trace job past its ceiling (2026-10-05).** Within a
day of going live the engine's registry held 63,660 addresses and a run read
1.2M edges in 345s against a 240s budget; the job then hit its 25-minute limit,
so `Match execution program` was cancelled and **the roster and accounting were
skipped on every cancelled run** (trace runs 16:45–18:59 UTC, all cancelled).
Causes, each fixed test-first:

1. Every address his money touched was remembered, down to cents. The registry
   now keeps only his wallets, anything read or swept, and addresses holding
   >= $1K of his money — pruned on entry and on exit (63,660 -> 1,603).
2. Units were spent on crumbs; now only on >= $1K or unvalued direct transfers.
3. A wallet read quiet on one chain and unread on another counted as quiet, so
   the engine swept busy wallets (one held 131K records). Quiet now needs a
   reading on EVERY chain the address was seen on (rule 9).
4. Every swept wallet's full history was loaded every pass, and each load was a
   full pass over the 1.7M-record substrate. Histories now load only for
   wallets holding his money (one depth deeper per pass), a wallet past 5,000
   stored records is a busy boundary, and the runner indexes the substrate once
   per run (`SubstrateIndex`).
5. "Reached" listed a busy trader holding $344K of his money that was 0.07% of
   its $490M inflow (`0x8d4d699a…`). Reach now also needs his money to be >= 5%
   of the account's inflow.

Production dry run from the bloated live state after the fixes: 124s inside a
180s budget (lowered from 240), 25,064 edges, one substrate pass, all unit
budgets spent. The job ceiling is raised 25 -> 35 minutes as a backstop, since
the job's healthy runtime grew by the engine's step.

---

**Tracing measured, then rebuilt around Hyperliquid's edge (2026-10-06).** Asked
to make fund tracing drastically better, the first job was to measure why it
found nothing:

| Measurement | Result |
|---|---|
| All 412 L1 counterparties (>= $50K) of the three config wallets, put to HL | 16 exist there and **none trades** — `0x160f6ef9…` ($180M two-way) included |
| How the 72 newborn >= $1M HL accounts were funded | Circle 35 · account-to-account send 16 · Unit 9 · HyperEVM 6 · Bridge2 6 |
| Correlator exits | $3.39B "unresolved" — mostly DeFi he still holds, bridges to himself, and his own withdrawals whose ledger row has no destination |
| Trace engine's last run | 0 HL accounts reached; the whole 180s budget spent |

Walking his money forward is exhausted: another account of his sits behind a
custody gap or has no flow link at all. So tracing now watches the edge, from
both sides (spec `docs/superpowers/specs/2026-10-06-boundary-trace-design.md`):
his world as one table (`src/boundary/perimeter.py`); every Bridge2 withdrawal
attributed to the account that made it (`scripts/check_boundary.py`, watch.yml);
every large new account's money traced back to its first boundary
(`scripts/run_provenance.py`, trace.yml); the correlator restricted to
custody-gap exits and route physics; and a dashboard Trace page.

**Found while building: a latent false CRITICAL in `circle_flows`.** A Circle
withdrawal burned by USDC's forwarder names the forwarder as `messageSender`,
so a withdrawal into his world read as an outsider paying him. The withdrawer
now comes from the USDC system address's ledger, matched on the exact amount.

**The production dry run caught what the tests could not, each fixed test-first:**

1. **The perimeter held none of his four private deposit addresses.** The
   roster tiers them INFRASTRUCTURE ("conduit: forwards 100% … to
   infrastructure") and `load_services` read that tier as "service"; the same
   leak made the correlator call every exit to `0x8570c2ae…` a contract.
   Services now come from labels and config only, as the engine's own list
   does. A roster tier is never ground truth, in either direction.
2. **All 30 boundary findings were false.** 27 were USDC's forwarder delivering
   his own Circle deposits — every later one would have paged CRITICAL — 2 were
   spam airdrops (MAX, LATINA) whose senders would have received `transfer`
   votes, and 1 was his payment to his own HyperCore deposit address. The
   forwarder and vaults are not accounts; an unpriced token sent into his world
   is not a payment; the roster's `transfer` vote now needs its $1K bar here too.
3. **Every retro Circle read failed:** a three-topic getLogs needs
   `topic0_2_opr`, not only the consecutive pairs.
4. **The watch step ran 441s against its 240s limit**, and provenance spent
   216s on one account: a throttled call slept 10+20+30+40s, the last after its
   final try, and nothing bounded the run. Each step now runs on one clock (140s
   and 200s), backoff is 2/5/10s, and a record cut short by a 429 keeps its
   findings and is re-resolved next run instead of hiding the account for the
   7-day TTL. Rerun under a fully throttled Blockscout: 131s and 192s, cursors
   kept, the failures reported.
5. **Blockscout's Arbitrum index has a hole:** blocks 507,912,970 to about
   508,546,9xx, 2026-09-22 21:41 to 09-24 20:07 UTC. It answers "Not found" for
   those blocks, "No logs found" for getLogs over them — the words of an empty
   range — and its block-by-time answers the last block before the hole, 22
   hours off. With a key, logs, the head and block-by-time come from Etherscan
   first; a Blockscout block-by-time answer is checked against the block's own
   timestamp; a whole-history read that fills a page is refused; an empty hop is
   unresolved, never "unrelated". **Any keyless Blockscout read of Arbitrum
   across that window, anywhere in this project, is suspect.**
6. **A credit "from the forwarder" is Circle's deposit wallet, not only a Circle
   message.** The dry run's "unresolved" $15.3M account had paid in its own
   HyperEVM USDC. The payer is read on HyperEVM in the credit's own block (the
   credit lands 0.36s after it): from Circle's CctpForwarder it is a message
   whose `MessageReceived`, in the same transaction, names the source chain and
   sender; otherwise it is a HyperEVM holder, whose funders are read through
   Etherscan on chain 999.
7. **The VM job map had drifted from the workflows** (`test_vm_jobs`).

Measured and left alone: all six engine funders are `unknown`, because rule 9
counts a wallet quiet only once it is measured on every chain it was seen on, so
the perimeter's `funder` role is empty in production; four of the six are
associates anyway.

Dry-run numbers: perimeter 13 members (core 3, deposit 5, identity 1,
associate 4), 9 put to HL, none active outside the core. Boundary, first run
before the fixes: 2,132 withdrawals in 345,601 blocks, 30 findings, all false;
after them, rerun under a fully throttled Blockscout: 0 findings in 131s, the
cursor kept and the failures reported. Provenance under the same throttle: 2
accounts in 192s — `0x1cb5b5c2…` ($15.3M) final `unresolved` (paid in from its
own HyperEVM address, whose funders need the Etherscan key), `0x96de0254…` six
Circle messages from the Arbitrum extension, marked for retry. Correlator
exits on the same data: **1,458 worth $3.388B before, 92 worth $0.275B after** —
91 of them into his four private exchange deposit addresses ($219.9M to
`0x8570c2ae…`), one $0.3M to a person (`0x1aa522b9…`), and all 146 of his HL
withdrawals resolved by nonce to his own addresses. What is left is exactly the
money that can come back out of an exchange into a new account.

**The first live runs (2026-10-06, after merging PR #63) found two more.** The
watch step read 2,136 Bridge2 withdrawals over 345,601 blocks and the 150 core
withdrawals by nonce through Etherscan in 35s, and the perimeter step took 40s.
But (1) that watch run came before any trace run had built the perimeter, so
on the config-only fallback his own payment to his HyperCore deposit address
`0x4aecac3b…` read as "his world funded an outside account" and **paged HIGH
— a false alert, delivered**. Findings true only if the far address is not his
are now held on the fallback and told only if they still stand once the
perimeter exists, and stored findings are judged again every run. And (2) the
provenance step crashed in 30s: with the key set, Bridge2 deposits now come
from Etherscan, whose getLogs writes zero as a bare `"0x"` (`logIndex`,
`transactionIndex`), and a deposit's USDC transfer is usually the first log of
its transaction. Neither could show in a keyless local dry run — **a reader
switched to a new source needs a run against that source before it ships.**

The next runs, now on Etherscan, found three more (PRs #66 and after):
Blockscout's v2 API answers **403 Forbidden to the GitHub runner**, so the
provenance and retro readers (inbound transfers, first gas, transaction logs)
read Etherscan first with the key; the key's plan is **3 calls a second** and
`utils.etherscan_get` paced 4, handing the refusal back as a failed read — it
now paces 0.34s and waits out a per-second refusal; and the **zero address sat
in his exchange family** (it "paid him" every USDC mint and is busy), so an
account funded by a mint read `same_exchange`, while mints, `0x2222…` and
Circle's deposit wallet came back as hop-1 funders. First successful live
provenance run: 28 accounts in 206s, 21 marked to retry the reads it could not
make.

Reading the live verdicts found the last two (PR #68): accounts that paid in
from **their own address** through Hyperliquid's Arbitrum extension read as
their own hop-1 source, pushing their real funders to hop 2; and the **Bridge2
contract** ("paid him" every HL withdrawal) sat in his exchange family as if
it were an exchange. And both caches outlived their code — perimeter families
are rebuilt by a daily pass and provenance records live 7 days — so the fixes
would have waited a day and a week; `RULES_VERSION` and `RESOLVER_VERSION` now
make a fix land on the next run. **A cache that outlives its code needs a
version stamp.** After #68, live: families rebuilt at once (`paid_him` 60 -> 11
entries, neither the zero address nor the bridge), the Bridge2 deposit feed
current to the head, the correlator's two pools complete (1,204 + 1,353
candidates, 0 matches against the 92 exits — a real no), and four large new
accounts (`0x96de0254…` $20.4M, `0xcfe27294…` $18.0M, `0x60a8c761…` $37.0M,
`0xd978850b…` $7.5M) traced to `0xee7ae85f…`, the Binance hot wallet his own
deposit address forwards into: `same_exchange`, evidence only — Binance is
everybody's exchange.

**The rule: index the edge, not the graph.** A table of his addresses joined
against a global feed of every crossing costs one lookup per event and scales
with the table; a forward walk costs a sweep per wallet, and its yield had
fallen to zero.

---

**The scanner's discovery checkpoint stopped saving for three days (2026-10-03
to 10-06, issue #57).** Every scan failed at "Prepare discovery checkpoint":
`compressed discovery checkpoint exceeds artifact budget`. Retention caps
ROWS (250,000 fills, 100,000 observations, 100,000 market events) and the
artifact budget is BYTES (48 MiB compressed); the last two checkpoints that
saved were 47.7 and 47.9 MiB, the store was 262 MiB with fills (92.9 MiB of
raw JSON) and observations (51 MiB) both exactly at their caps, and from then
on nothing fit. Because observation shards are deleted only after a saved
checkpoint, 465 piled up, and each run imported the same oldest 40 and threw
the work away with the failed snapshot - discovery was frozen on Oct 3 while
the shards ran toward their 7-day expiry. Fixed by `snapshot_within_budget`:
the owner's checkpoint is trimmed oldest-first (never active backfills, never
authority, never a shard) until it fits. On a copy of the real checkpoint under
a 44 MiB test budget: 2 rounds, 36,659 fills and 21,591 observations, 40.9 MiB,
all 22,199 authority snapshots kept. Found on the way: an empty protected list
made `wallet NOT IN (NULL)` - never true - so the first version trimmed no fill
at all. **A cap in rows does not bound bytes; a budget in bytes needs a byte
check before it is enforced.**

---

**The execution census never accumulated (2026-09-29 to 2026-10-06).** Commit
`bb21cb3092` moved the census state from gitignored `data/.local/` into the
committed tree so the population would build across ephemeral Actions runs. It
added the new `STATE = …/census_state.json` line above the old one and left
`STATE = DATA_DIR / ".local" / "execution_census.json"` in place, and the second
assignment won. Every run started from an empty state, walked the same
hash-ordered accounts and wrote `measured 1 / attempted 83` (2026-10-05), so
`is_discriminating`, which needs 20, never passed and the `execution_program`
vector could not vote. No test pinned the path; CI stayed green. Found while
designing the candidate study (spec 2026-10-06). **When a fix moves a path, test
the path.**

---

**Hyperliquid presence was judged on perp margin (found 2026-10-07).** The
identity row's `account_value` is `webData2`'s perp margin, and it reads 0 for
an account whose collateral sits in spot. The candidate study's plan used it as
"Hyperliquid-present" and would have excluded the POSSIBLE lead `0x84abc08c0e…`
($9.37M in spot, $80.0M of volume in 30 days, $0 of margin). The transfer graph
did the same: it marked 19 Hyperliquid users in the target's graph as not
trading, and five of them traded in the last 30 days (volume from $2K to $111M).
`0x84abc08c0e…` received $999,999.80 straight from the target on 2026-09-10
(UTC). Measured on their orders, three tag every order with a client ID (maker
bots: `0x84abc08c0e…`, `0x4c78a97cef…`, `0x07ae8551be…`) and two are web-UI
traders with no client IDs - `0xb663c9b86c…` (2,000 of 2,000 orders
FrontendMarket, in his markets xyz:SP500 and ZEC, account since emptied) and
`0x4a89709691…` (about 80% FrontendMarket) - and he clicks the web UI himself
(about 8% FrontendMarket), so their order habits do not exclude them. A new
wallet of his that kept its collateral in spot would never have read as trading
in his graph, nor been protected from the conduit pass, and the 2026-10-06
reading that "16 exist on HL, 0 trade" among his 412 depth-1 counterparties was
wrong for at least `0x84abc08c0e…`. Fixed by storing `portfolio`'s total value
and 30-day volume on every identity row (`hl_identity.parse_activity`, copied
into the roster as `hl_total_value` and `hl_month_volume`), and by judging graph
presence with `transfer_graph.trades_on_hl`: role `user` or `subAccount`, and
volume in the 30 days before the row was read or perp margin when it was read.
Holding value is not trading - the CONFIRMED config wallet `0x1419e75330…` holds
$56.7M and traded nothing in 30 days - and a row probed before the fields
existed is still judged on perp margin until it is probed again. That is the
reach limit: the conduit-pass protection sees only addresses the identity probe
refreshes, and the graph's conduits were never probed (9 of its 145 traded
$196.1M on Hyperliquid in 30 days, and one sat in the probe rota), so the first
fix did not reach the case the exemption exists for. Fixed by queueing the
graph's conduits for the identity probe right after the roster's leads
(`scripts/check_identity.py`, 15 a run, all 145 read within about twelve runs).
**Perp margin is not presence: "trades there" is 30-day volume, "holds value
there" is total value - both live in `portfolio`, not `marginSummary`.**

---

**The execution-program CRITICAL path could never fire (2026-09-29 to
2026-10-07).** `scripts/check_execution_program.py`'s `roster_vector_map` read
each roster row's `address`, but every roster row is keyed `wallet` (measured
2026-10-07: 2,635 of 2,635), so the map was always empty,
`has_independent_vector` was always False, and "a match plus an independent
vector -> CRITICAL" (CLAUDE.md's execution-program row) could only ever page
HIGH. Its tests passed a hand-built map and never went through the function.
Found while the candidate study wired its tooling vote into the same vector;
fixed in commit `4d3b1bea9c` (reads `wallet`, falls back to `address`), with a
test through the roster's real shape. No discriminating match existed in that
window (the census measured 1 account of the 20 it needs), so no alert was
lost. **A reader of another writer's file is tested through that writer's real
shape, never a hand-built stand-in.**
