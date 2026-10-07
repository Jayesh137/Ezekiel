# Candidate study — keep every identified HL candidate under study, and say with base rates whether it trades like him

**Date:** 2026-10-06 · **Status:** design approved section by section in
conversation; awaiting review of this written spec. · **Approved choices:**
approach A (a dedicated study subsystem, preceded by a separate census fix);
evidence that a lead does **not** trade like him is **annotation only** and never
changes a tier; verdicts are shown on **both** the phone app and a new dashboard
page. · **Scope:** the wallets the detectors have already identified that trade
on Hyperliquid — how they are kept under continuous study, the tests that compare
them to the target, how those tests are calibrated, and where the results are
read. Discovery of new wallets, fund tracing and the behavioural scanner are out
of scope, except where noted (§3, §17).

---

## 1. Why — measured 2026-10-06, read-only

Measured from the stored target data (169,136 fills, 56,672 orders,
2026-02-05 → 2026-10-02) and a labelled sample fetched from the public API: 12
identified leads, 24 members of 6 sub-account families (known same operator),
and 32 roster accounts of ≥ $1M chosen by address hash. Each sample wallet:
`userFillsByTime` (aggregated, last 45 days, ≤ 3 pages) and `historicalOrders`.
Scratch scripts only; nothing was written to `data/`.

| Measurement | Result |
|---|---|
| Fills held for the candidates the investigations queue examines | **4 of 100** (`data/investigations/latest.json` coverage) |
| Last behavioural score of the HL leads (`0xdd53c529…`, `0x5b5d5120…`, `0xb83de012…`, `0x12e16e3d…`) | 2026-09-21 → 09-23, schema `2026-08-05.1`, `scorer_current: false`; all four now WATCH with zero vectors |
| Execution-program census (`data/execution_program/census.json`, 2026-10-05) | **1 measured of 83 attempted.** The vector can never vote (§3) |
| How much of his history Hyperliquid keeps | the newest **10,000 fills** and **2,000 orders**. He averages ~700 fills a day, so ≈ **two weeks** |
| His hour-of-day activity, month against month (7 periods, day-weighted) | correlation **−0.29 to 0.74, median 0.14**; the quietest 6-hour window moves (03, 08, 18, 03, 21, 06, 07 h UTC). His ledger actions fall in every hour (11–39 per hour) |
| His execution habits over 8 months (56,672 orders) | Limit-IOC 94.6%, web-UI market 5.4%, GTC 6, **client IDs 0, triggers 0**, reduce-only 6, cancels 0, taker 100%. IOC limit exactly 5.0% through the book on 83%, within 4.5–5.5% on ~98% (99.9% in the last 45 days) |
| His slicing rhythm (gap between slices inside a run, Wasserstein-1 against his last 45 days) | his months Apr–Sep **0.03–0.25 s**; Feb 0.71 s and Mar 1.12 s (an older, faster script) |
| Execution style of the sample (≥ 100 orders) | leads: **10 maker bots, 1 web-UI trader**; strangers: 22 maker, 2 mixed, 2 web-UI. Client-ID users: leads 10/11, strangers 14/26. **0 of 37 share his style and flags** |
| Style agreement within one operator | **41 of 41** family pairs agree on style, client-ID use and maker use; random pairs agree 72% / 48% / 85% |
| Slicing rhythm of everyone else with ≥ 50 in-run gaps (33 wallets) | **0.45–1.82 s** from his |
| Same-minute activity (±2 min) against the same wallet shifted by whole days | family pairs: median **2.4x**, 7 of 11 clearly coupled. Random pairs: median 0.64x, but **p90 3.4x** — shared market events pass straight through a whole-day shift |
| Same-minute activity with him | `0x12e16e3d…` **2.77x**, above all 14 random accounts measured against him (max 2.0x); `0xb2f7374b…` 0x |
| Activity on his silent days vs his active days | too few of his days inside 45 days to judge anything |

**Conclusion.** Nothing studies how the identified candidates trade, and nothing
keeps their history long enough to. When their habits are read, the leads are not
even the same species as him: he is a default-SDK `market_open` loop with no
client IDs; ten of the eleven leads are maker bots that tag every order. A clock
profile does not identify him — his own months disagree — but timing measured
*against his timeline* can, once weeks of overlapping history exist, and only if
judged against the right reference: other wallets measured against him over the
same days, not a time-shifted copy of the candidate.

## 2. First principles

1. **An operator carries some things to every account and can change others at
   will.** Tooling (his script, its SDK defaults, its rhythm) and attention (one
   person at one screen, asleep or away at one time) travel with him; strategy is
   what a copier reproduces by definition. Tooling and timing can vote; strategy
   is evidence only.
2. **Timing identifies only relative to his own timeline.** Co-activity, activity
   during his silences and birth inside a silence compare the candidate to *his*
   days. A free-standing clock profile is measured to be unstable for him.
3. **Every test is a measurement with two base rates.** How often does a stranger
   match this well? How often does the same operator? A test with either unknown
   says nothing (rule 4). The bars are fixed here, before any result is seen.
4. **History is kept or lost.** Hyperliquid serves ≈ two weeks of an account like
   his. What is not read and kept within that window is gone, so the study keeps a
   compact record per wallet per day.
5. **Doctrine unchanged.** Ground truth is config only; a transfer is not
   ownership; a failed read is never empty (rule 5); a missing value is never 0
   (rule 6); the target is never his own stranger; one writer per file; behaviour
   never reaches PROBABLE alone; nothing here is fitted to the story.

## 3. Phase 0 — the census fix (ships alone, first)

`scripts/census_execution_program.py` lines 36–43:

```python
STATE = OUT_DIR / "census_state.json"          # added by bb21cb3092 (2026-09-29)
MAX_STATE_ROWS = 20_000
STATE = DATA_DIR / ".local" / "execution_census.json"   # the old line, still last
```

The fix meant to keep the census state in the committed tree left the old
assignment directly beneath it, so it never took effect. Every Actions run starts
from an empty state, walks the same hash-ordered accounts, and the measured
population stays at 1 — so `is_discriminating` (needs ≥ 20) is never true and the
`execution_program` vector cannot vote. No test pins the path, which is why CI
stayed green.

Fix: delete the overriding line; add `test_state_lives_in_the_committed_tree`
(the path resolves under `data/execution_program/`, never `data/.local/`); record
the incident in `docs/incident-log.md` with the rule it teaches (**when a fix moves
a path, test the path**). Success: `measured` grows across two consecutive
`analyze.yml` runs.

## 4. Architecture

```
 analyze.yml (daily)                           study.yml (every 6 h, group "study")
 ───────────────────                           ────────────────────────────────────
 census_execution_program.py                   run_study.py
   strangers: userFills + historicalOrders       1. choose the study set + references   (selection)
   → census_state.json (committed, capped)       2. read: fills / orders / ledger       (collect)
   → census.json (rates for T1–T3)               3. fold into daily records             (records)
            │                                    4. his records from data/fills, orders, ledger
            │                                    5. tests T1–T8                          (tooling, timing, lifecycle, strategy)
            └──────────── read ────────────────► 6. calibrate against panels           (calibration)
                                                 7. verdicts + study rank               (verdict)
 hl_surface (families), roster, dormancy,        8. write data/study/**; alerts on transitions
 identity, newborn, provenance ── read ───────►
                                                         │
                     roster.py ◄── data/study/latest.json ┤──► dashboard /study, phone app
```

New code lives in `src/study/` as pure modules plus one I/O script:

| Module | Responsibility |
|---|---|
| `src/study/selection.py` | study set and reference panel, priorities, stickiness, exclusions |
| `src/study/collect.py` | strict reads, cursors, budget; returns raw batches with coverage |
| `src/study/records.py` | raw batches → additive hour blocks → daily records; merge; coverage |
| `src/study/tooling.py` | T1 style and flags, T2 slicing rhythm, T3 adapter onto `execution_program` |
| `src/study/timing.py` | T4 co-activity (+ cross-coin, lead/lag), T5 complementarity, T6 decision hours |
| `src/study/lifecycle.py` | T7 birth-in-silence, first-week ramp, first coins |
| `src/study/strategy.py` | T8 from archived coins: `calibration.market_rarity_bonus`, direction agreement, relative size |
| `src/study/calibration.py` | panel summaries, Clopper–Pearson bounds, the fixed bars, statuses |
| `src/study/verdict.py` | per-test → per-family verdicts, study rank, transitions |
| `src/study/archive.py` | daily files, verified month archives, state, write-if-changed (added by the plan) |
| `src/study/panels.py` | stranger and family panels for T1–T3 (added by the plan) |
| `src/study/assemble.py` | his reference, his self-splits, per-wallet tests, rows, dossiers, latest (added by the plan) |
| `scripts/run_study.py` | the only writer of `data/study/**`; orchestrates the run |

**Single-writer map.** `run_study.py` writes everything under `data/study/` and
nothing outside it. `census_execution_program.py` writes only
`data/execution_program/census.json` and `census_state.json`;
`check_execution_program.py` keeps its own `latest.json` and `target.json` there.
`roster.py` reads `data/study/latest.json` and writes only the roster.

## 5. Study set and reference panel

**Study set** — at most `study.max_wallets` (default 40), re-chosen each run, in
this priority order:

1. **Pinned:** `config.watch_wallets` and a new `config.study_wallets`.
2. **Roster leads:** CONFIRMED, PROBABLE and POSSIBLE wallets Hyperliquid knows as
   traders.
3. **Leads that decayed:** `peak_tier` POSSIBLE or higher, HL-present, kept for 60
   days after `tier_dropped_from` was set (`0xdd53c529…`, `0x5b5d5120…`,
   `0xb83de012…`, `0x12e16e3d…` today).
4. **Other detectors' finds, HL-present:** dormancy handoffs, newborns ≥ $1M,
   census and tape hits, provenance leads, referral and operator-group twins.

Within a source, order by `evidence_strength` then account value. **HL-present**
means the roster's `hl_role` is `user` or `subAccount` and either account value
> 0 or a fill in the last 30 days. **Sticky:** once in, a wallet stays at least 14
days unless displaced by a higher source when the set is full; a wallet that leaves
keeps its archive and its dossier says "not studied since …".

**Never studied:** the target (he is the reference — §7 builds his records from his
own stored data); config wallets that do not trade on HL; INFRASTRUCTURE rows.

**Reference panel** — `study.reference_wallets` (default 160) large accounts chosen
score-blind: the census eligibility band (account ≥ $250K, weekly volume $100K–$2B),
hash order, excluding the target, the config cluster, the study set and every
member of a candidate's operator group. A reference must average ≤ 5,000 fills a
day at its first read, so a 36-hour read cycle covers it completely; one above that
is replaced at once. Frozen once chosen; a member is otherwise replaced (next in
hash order) only if its account is emptied. References are read for fills only,
27 per run, so each is read about every 36 hours. They exist to answer one
question for the timing tests: how often does a stranger line up with *him* this
well over these same days? 160 leaves margin over the 150 the bars need (§8.2).

## 6. Collection and the daily record

### 6.1 Reads

| Role | Read | Every |
|---|---|---|
| studied | `userFillsByTime` from the fills cursor, `aggregateByTime: true`, ≤ 5 pages | run |
| studied | `historicalOrders` (newest 2,000) | 4th run (daily) |
| studied | `userNonFundingLedgerUpdates` from the ledger cursor | 4th run (daily) |
| reference | `userFillsByTime` from the fills cursor, ≤ 2 pages | ~36 h |

Reads go through a strict reader (shape-checked; `utils.hl_read`'s `ok` flag, never
`hl_post`). A failed read records the wallet as unreadable for that source, does
**not** move its cursor, and writes **no** record for the uncovered span. A
budget refusal is not a failure: the wallet goes first next run.

### 6.2 Committing behind a quiet boundary

Fills are folded into **hour blocks** that are additive, so a day record is the sum
of its blocks and re-reading never double-counts. A run commits fills only up to a
**quiet boundary**: the latest moment at least 5 minutes before the read whose
surrounding gap in this wallet's orders is ≥ 30 s. Runs are defined by gaps ≤ 30 s,
so no run straddles the boundary. A wallet with no such gap (a bot quoting every
second) falls back to the hour mark and its blocks carry `runs_split: true`. The
cursor advances to the boundary; the tail is re-read next run (deduped by `tid`, or
`oid`+time). The time of the wallet's last fill is kept in state, so the first fill
after a boundary is classified correctly as a session start or not.

Orders are counted once, by placement time, as soon as they are read; an order
still open is counted as `open`. (The first design waited an hour for each order's
final status; the 2026-10-06 dry run showed that a busy bot's newest 2,000 orders
are all minutes old, so its habits would never have been recorded, and only the
cancel share — which no verdict uses — needed the wait.) If more than 10,000 fills
or 2,000 orders arrived since the cursor, the unreachable span is recorded as
`saturated`.

### 6.3 The daily record — `data/study/archive/<wallet>/<YYYY-MM-DD>.json`

```json
{
  "schema": "study-day/1", "wallet": "0x45d26f28196d226497130c4bac709d808fed4029",
  "day": "2026-10-02", "role": "studied",
  "coverage": {"fills": [[1790899200000, 1790985599999]],
               "orders": [[1790899200000, 1790985599999]],
               "ledger": [[1790899200000, 1790985599999]],
               "saturated": false, "runs_split": false},
  "fills": 812, "orders": 297,
  "minutes": "AAAAAAAA…(240 base64 characters)",
  "manual_minutes": "AAAAAAAA…(240 base64 characters)",
  "decisions": [1790958826865],
  "runs": [{"coin": "BTC", "side": "A", "start_ms": 1790958826865,
            "end_ms": 1790958845158, "n": 40, "clip": 0.1, "clip_share": 1.0,
            "gap_p50_ms": 1700}],
  "runs_overflow": 0,
  "habits": {"orders_seen": 297, "taker": 297, "fills_seen": 812,
             "tif": {"Ioc": 290, "Gtc": 0, "Alo": 0, "FrontendMarket": 7, "other": 0},
             "cloid": 0, "trigger": 0, "reduce_only": 0, "canceled": 0, "open": 0,
             "ioc_offset_seen": 290, "ioc_offset_5pct": 289},
  "cadence": [0, 0, 1, 2, 0, 1, 3, 2, 4, 3, 5, 4, 6, 4, 8, 22, 71, 48, 31, 12],
  "coins": {"BTC": {"orders": 40, "buy_usd": 0.0, "sell_usd": 340000.0,
                    "taker_clips": {"0.1": 40}, "px_median": 85199.0}},
  "ledger": [{"ts_ms": 1790958826865, "type": "deposit", "usd": 1000000.0}]
}
```

(`cadence` is shown truncated; it always holds 50 counts.)

**As implemented (plan 2026-10-06):** the record also carries `taker_orders`,
`program_runs`, `decisions_overflow` and `coin_minutes` (a minute map for each of
the day's five busiest coins plus `_other`, which the cross-coin reading of T4
needs); `decisions` are `[t_ms, coin, side, kind]` with kind `session`, `run` or
`manual`; `habits` come from orders only (taker share comes from fills); and each
coin carries `px_sum`/`px_n` instead of `px_median`, because a median cannot be
added across batches. A coin's `taker_clips` keeps its 20 commonest sizes and counts
the rest under `_other` (a maker bot quoting random sizes put 1,045 distinct BTC sizes
in one day's record, 42 KB against the budget below); the map's total is still the
coin's taker order count, and the clip rule, which needs only the dominant size and
that total, reads it unchanged (`_other` is in the total, never the clip). A size
holding 80% of a day's orders is always kept that day, and a size's count summed over
days can only fall short, so over a window the bound can cost the table a borderline
clip and never add one. `habits`, `manual_minutes` and `ledger` stay `null` for a day
no read of that kind covered.

Rules: a field the reads did not cover is absent or `null`, never 0; minutes
outside `coverage.fills` are *unknown*, never quiet; a **covered day** has ≥ 20
hours of fills coverage; `cadence` counts gaps inside runs of ≥ 15 orders in 50
bins of 100 ms (0–5 s); `decisions` are session starts
(≥ 30 min of silence before), run starts and web-UI orders, capped at 100 a day;
`runs` keeps runs of ≥ 10 orders, capped at 100 a day with `runs_overflow`
counting the rest; `coins` keeps the top 20 by orders, with `taker_clips` counting
taker orders (every fill crossed) by base size and `px_median` the median first-fill
price of those orders; `ledger` caps at 50. A
reference's record has no order or ledger fields (`null`). A day's record is
rewritten only while it is still receiving blocks.

**His records** are built by the same functions from `data/fills/`,
`data/orders/` and `data/ledger/` each run (≈ 12 s to load) and are not stored:
he and the candidates are measured by identical code.

**Storage.** ≈ 2–5 KB a day for a studied wallet, ≤ 15 KB for a busy bot, ≈ 1 KB
for a reference. Sealed months roll into `<YYYY-MM>.jsonl.gz` beside the daily
files, done by `run_study.py` itself so `data/study/` keeps one writer (read-back
verified; an archive that will not read is never overwritten; readers accept both
forms — grep for readers before changing the format). The archive is
irreplaceable and `scripts/compact_data.py` never touches it. Estimate:
10–15 MB a year compressed for 40 studied + 160 references.

### 6.4 Budget

Per run ≈ 40 × 120 (fills) + 10 × 140 (orders + ledger) + 27 × 120 (references)
≈ 9,400 weight, ≈ 10.5 minutes; first reads of a busy wallet cost up to 5 pages
and are spread by rotation. `ReadBudget(seconds=900, weight_per_minute=900)`; step `timeout-minutes:
25`; job `timeout-minutes: 35`. Order kept: lookup < internal budget < step < job.

## 7. Tests

Each test returns `{status: measured | insufficient | unreadable, statistic, n,
detail}`. A test below its minimum data is `insufficient` — never a low score.
**Him** below means his own daily records over the same window unless a test says
otherwise.

### Tooling — how orders are made

**T1 Execution style and flags** (orders, ≥ 100 seen).
Style: `PROGRAM_IOC5` (IOC with 4.5–5.5% offset ≥ 50% of orders), `MAKER` (ALO+GTC
≥ 50%), `MANUAL_UI` (web-UI market ≥ 50%), else `MIXED`. Flags: client IDs (≥ 5%
of orders), triggers (≥ 1%), maker share (≥ 10%), cancel share, taker share.
Statistic: full match of style and flags with his (`PROGRAM_IOC5`, no client IDs,
no triggers, no maker). This is the only test that can say **against**: a
candidate showing, on > 50% of ≥ 100 orders, a trait that makes up **under 0.1%
of his recorded orders** — client IDs (0 of 56,672), triggers (0), maker posting
(6 GTC of 56,672).

**T2 Slicing rhythm** (≥ 200 in-run gaps). Wasserstein-1 between the candidate's
cadence histogram and his own over the most recent window holding ≥ 200 gaps
(at least his last 90 days). Never says against: his own history includes a
script change (Feb–Mar 0.71–1.12 s).

**T3 Clip table and rank structure.** The clip table and clip notionals are rebuilt
from the archived `taker_clips` and `px_median` with `execution_program`'s own rules
(`MIN_CLIP_ORDERS`, `MIN_CLIP_SHARE`), then passed to `execution_program.compare`
unchanged. Never says against (his clip table drifts: 43–50% of clip coins
re-match across his own windows, `data/recall`).

### Timing — measured against his timeline

**T4 Co-activity.** Statistic: the **lift** — over jointly covered days, the
share of his active minutes in which the candidate is active within ±2 minutes,
divided by the share of *all* covered minutes in which it is active (the same ±2
dilation on both). A bot active every minute scores 1.0, not 1.0-of-everything.
Minimum: ≥ 14 jointly covered days containing ≥ 5 of his active days, ≥ 20 active
minutes each.
Reference: the same statistic for him against every reference wallet and every
other studied wallet (leave-one-out, excluding the candidate's operator group and
the config cluster), over each one's own jointly covered days.
Supporting readings:
- **cross-coin co-activity:** the same lift restricted to candidate minutes whose
  coins he did not trade within ±2 minutes. A copier cannot produce it.
- **lead/lag:** on shared coins, his decisions paired with the candidate's nearest
  decision on the same coin within ±15 minutes (comovement's pairing); the median
  lag over ≥ 10 pairs. ≤ 0 s means the candidate leads or moves with him; > 0 means
  it follows, as a copier does.
- **day-shift placebo:** the candidate shifted by ±1, 2, 3, 5, 7 days — reported,
  not used for the verdict (it lets shared market events through, §1).

**T5 Complementarity.** Activity rate on his silent days minus on his active days,
over ≥ 21 covered days containing ≥ 5 of each; plus activity inside his anomalous
silences (`dormancy`). Referenced against the reference panel. Evidence only: no
same-operator ground truth exists for alternation (§8.2), so it can be *notable*,
never *for*.

**T6 Decision hours.** Day-weighted 24-bin histogram of decisions over ≥ 15
decision-days, compared with his month-to-month spread (his own months are the
yardstick of how different "different" is). Evidence only; expected weak, and the
dossier says so with the 0.14 median.

### Lifecycle

**T7.** Birth inside one of his silences (`dormancy` handoff score, read from its
file — the `dormancy_handoff` vector stays where it is), first-week ramp (days from
first deposit to $1M notional), and the overlap of its first-week coins with his
clip coins. Evidence only.

### Strategy — evidence only (copiers match it by definition)

**T8.** Rarity-weighted market overlap (`calibration.market_rarity_bonus`), daily
direction agreement on shared coins (signed notional), and size relative to his
account.

## 8. Calibration

### 8.1 Panels

| Panel | Source | Written by |
|---|---|---|
| **Strangers** (T1–T3) | the census, extended: each sampled account also gets `historicalOrders`; its row records style, flags, cadence histogram (program runners only), clip compare | `census_execution_program.py` → `census_state.json` (committed; habit detail capped at 5,000 rows) |
| **Same operator** (T1–T4) | sub-account families from `data/hl_surface/latest.json` plus `subAccounts` asked of census accounts (weight 20); shared-agent pairs from `agent_links`. Members re-measured once per 30 days (≤ 3 pages of fills + orders); ≤ 2,000 weight per study run | `run_study.py` → `data/study/panel/families.json` |
| **Himself** (T1–T4, T6) | his months against the rest of his history, as `recall.py` does | `run_study.py` → `data/study/panel/self.json` |
| **References** (T4–T6) | §5 reference panel + the other studied wallets | `run_study.py` (archive) |

The same-operator rate used in a bar is the **smaller** of the family rate and his
self-split rate where both exist. Families are recorded with two caveats: they
share one signer (an upper bound on tooling consistency), and the measured sample
was all market makers. Where families cannot measure a test (e.g. cadence, when no
family member runs programs), his self-splits are used alone with ≥ 6 windows, and
the verdict carries `same_op_basis: "self_only"`.

### 8.2 The bars — fixed now, not tuned after results

| Status | Condition |
|---|---|
| `uncalibrated` | fewer than 200 measurable strangers (T1–T3), 150 references (T4–T6), or 40 same-operator pairs (6 self windows for `self_only`). Shown grey with the raw numbers |
| **for** | stranger match rate at least this good has a one-sided 95% Clopper–Pearson **upper bound ≤ 2%**, and the same-operator rate at this level is **≥ 50%**. "This level" is the **looser** of the candidate's own statistic and each usable same-operator median, so a closer match can never fare worse than a looser one and the same-operator rate is at least one half by construction (refined with the implementation plan, 2026-10-06) |
| **against** (T1 only) | the candidate shows, on > 50% of ≥ 100 orders, a trait under 0.1% of his recorded orders (§7 T1), and the same-operator mismatch rate for that trait is **≤ 10%** over ≥ 40 family pairs; like every T1 judgement it also needs the 200 strangers |
| `neutral` | measured and calibrated, neither of the above |
| `insufficient` | below the test's minimum data |

"Measurable strangers" are census accounts with ≥ 100 orders seen; a stranger that
runs no programs counts as a non-match for T2 (it cannot produce the rhythm), not as
missing. For T4 a candidate is **for** when its co-activity is above the
reference distribution by the bar **and** (its cross-coin co-activity is also above
it by the bar **or** it leads/moves with him on ≥ 10 pairs) **and** family pairs
reach the same reference percentile ≥ 50% of the time. T5, T6, T7 and T8 never
reach **for**; T5 and T6 can be `notable` (above 98% of references) as evidence.

### 8.3 Likelihood ratio and study rank

For a calibrated test, `LR = same-operator rate / stranger upper bound` at the
candidate's level; for **against**, `LR = same-operator mismatch / stranger
mismatch`. A family's LR is its strongest calibrated test's (tests inside a family
are not independent, so they are never multiplied). Study rank = Σ over tooling
and timing of `log10 LR`, each clipped to [−2, +2]; an uncalibrated or
insufficient family contributes 0. It orders the Study list and nothing else —
never a tier, never stored as a confidence.

## 9. Verdicts and the roster

**Per family:** tooling = T1–T3; timing = T4 (T5, T6 as evidence); lifecycle = T7;
strategy = T8. A family is **for** if any calibrated test is for and none against;
**against** if any against and none for; **mixed** if both; else `neutral`,
`insufficient` or `uncalibrated`.

**Roster** (`roster.py` reads `data/study/latest.json`):
- every studied wallet gets `evidence.study = {as_of, tooling, timing, lifecycle,
  strategy, rank, coverage_days, calibrated}` (each family: verdict, basis, ≤ 4 key
  numbers);
- **against never changes a tier, a vector or `peak_tier`** (operator's choice);
- tooling **for** adds the existing `execution_program` vector;
- timing **for** adds a new `coactivity` vector (behaviour category).

Both votes are behaviour: they corroborate, can make a wallet POSSIBLE, never
PROBABLE without a financial or protocol vector. `evidence.VECTOR_CATEGORY` gains
`coactivity: behaviour`; nothing else in `evidence.py` changes.
`check_execution_program.py` keeps its wider fills-only watch; the roster takes the
vote from either file.

## 10. Outputs

- **`data/study/latest.json`** — `computed_at`, `schema`, his reference summary
  (style, flags, cadence profile, active days, silences), panel status (n for each
  panel against its bar), budget/unreadable counts, and one row per studied wallet:
  wallet, source, account value, coverage days, each family's verdict + key numbers,
  rank, last read, `studied_since`. Target ≲ 150 KB.
- **`data/study/wallets/<addr>.json`** — the dossier: every test's full output and
  the chart series (90-day activity strings for him and it with his silences,
  both cadence histograms, both decision-hour histograms with his monthly band,
  co-activity against the reference distribution, habit table). Rewritten only when
  its content hash changes. ≲ 20 KB each.
- **`data/study/state.json`** — membership, roles, cursors, last-fill times,
  stickiness clocks, reference panel.
- **`data/study/panel/families.json`, `self.json`** — panel summaries.
- **`data/study/archive/…`** — §6.3.

## 11. Alerts and feed health

Alerts fire on a **transition** (compared with the previous `latest.json`), are
CRITICAL or HIGH only, and **against never alerts**:
- tooling turns **for** → HIGH; CRITICAL if the roster row already has a financial
  or protocol vector. Reuses `alert_execution_program_match`'s cooldown key
  `execprog_<wallet>`, so the census-gated detector and the study never both page
  for one wallet.
- timing turns **for** → HIGH; CRITICAL with a financial or protocol vector. New
  `alert_study_coactivity`, key `coactivity_<wallet>`, 72 h cooldown.

**Feed health** gains a `candidate study` feed (`BLIND_HOURS` = 6), placed in the
existing `other` group so `watch.yml` checks it crosswise: half of a run's reads
failed; 0 wallets studied while the set is non-empty; or the stranger panel
(`census.json` `habit_measured`) has not grown for 48 hours.

## 12. Dashboard and phone

**Dashboard — `/study`, in the Hunt group after Roster.**
- Header: panel status against each bar, so "uncalibrated" is visibly a property of
  the panels, not of a wallet.
- Ranked table: wallet (Hypurrscan link), source, size, Tooling / Timing /
  Lifecycle chips, coverage days, last read.
- Dossier panel per wallet: habit table him vs it; both slicing rhythms overlaid;
  90-day activity calendar for him and it with his silences shaded; co-activity
  against the reference distribution with the candidate marked; decision hours
  against his monthly band (labelled weak by measurement); which tests are
  calibrated. Inline SVG with the existing UI kit; no new dependencies.

**Phone (`/review`).**
- Each Likely and Leads card gains a **"Trades like him?"** row: Tooling, Timing,
  Lifecycle chips (for ✓ / against ✗ / neutral – / not yet ?). One tap opens a
  sheet with the 3–4 numbers behind each chip and what they mean.
- A third tab, **Studied**, lists the whole study set by study rank, because most
  HL candidates sit in WATCH and would otherwise never reach the phone.
- Missing or unreadable `data/study/latest.json` shows "study unavailable", never
  "no evidence" (rule 5). Still read-only; still no token on the phone.
- `dashboard/ARCHITECTURE.md` §8 gains the study fields the app reads, and the
  vector labels gain `coactivity` and `execution_program`.

## 13. Scheduling

- **`.github/workflows/study.yml`**: cron `17 */6 * * *` + `workflow_dispatch`; the
  usual `gate` job (`keep_schedule.py --gate study.yml`); job concurrency group
  `study`, `cancel-in-progress: false`; steps: checkout, Python 3.12, deps,
  `run_study.py` (`timeout-minutes: 25`), commit + push `if: always()` with the
  `-X theirs` rebase, failure issue on `failure()`. Job ceiling 35 minutes.
- **Schedulers**: `scripts/keep_schedule.py` (`JOBS`, `GROUP_MEMBERS`),
  `scripts/dispatch_workflows.ps1` and `scripts/apps_script/ezekiel_relay.gs`
  (+ `relay.test.mjs`) each gain `study.yml`, 360 minutes, group `study`.
- **`analyze.yml`** census step keeps its 900 s budget; each account now costs about
  twice the weight, so fewer accounts a day — acceptable now that the state
  accumulates.

## 14. Testing and verification

Network-free tests; nothing writes to the real `data/`.

- `test_census_execution_program.py`: the state path is committed (§3); habit rows
  are capped and hits never evicted.
- `test_study_selection.py`: priority order, HL-presence rule, 60-day decayed-lead
  retention, 14-day stickiness, the target and non-trading config wallets never
  selected, references exclude operator groups and the study set.
- `test_study_records.py`: hour blocks are additive and idempotent under re-read;
  no run straddles a quiet boundary; `runs_split` fallback; coverage intervals;
  saturation; unknown minutes stay unknown; a busy wallet's newest orders still count.
- `test_study_collect.py`: a failed read never advances a cursor or writes a record;
  a budget refusal is not a failure; strict shape checks.
- `test_study_tooling.py`, `test_study_timing.py`, `test_study_lifecycle.py`: each
  statistic on fixtures, including copier-shaped (follows) and same-hand (leads)
  timing.
- `test_study_calibration.py`: Clopper–Pearson bound, each bar, `self_only` basis,
  `uncalibrated` and `insufficient` paths.
- `test_study_verdict.py`: family rules, against only from T1, rank clipping,
  transitions.
- **Target never a stranger:** in the study set, the census, the reference panel and
  the family panel (the 2026-09-16 lesson).
- `test_roster_study.py`: annotation; votes; against changes no tier, vector or
  `peak_tier`.
- Alerts, feed health, `review.test.js`, `api.test.js`, `relay.test.mjs`.
- **Fixture:** today's sample, trimmed (him + 3 leads + 3 strangers + 1 family), as a
  regression of the §1 numbers.

**Before merge:** a dry run against live Hyperliquid on a small budget, compared
with §1; `python -m ruff check src/ tests/ scripts/`; the dashboard built and
checked in headless Chrome against live data.

**Acceptance:**
1. Census `measured` grows across two consecutive daily runs.
2. After the first study runs, every wallet in the set has day records with
   coverage, no failed read is stored as an empty day, and `latest.json` carries a
   row per studied wallet.
3. Once the stranger panel passes 200 and the family panel 40 pairs, the maker bots
   among today's leads read tooling **against** (client IDs and maker posting) and
   `0x12e16e3d…` reads tooling **neutral**; before that they read **uncalibrated**
   with the raw numbers.
4. His own recent months read tooling **for** against his history once the stranger
   panel passes 200 (the self-recall check).
5. The phone row, the Studied tab and the Study page render against live data.

## 15. Delivery phases

| Phase | Ships |
|---|---|
| **0** | §3 census fix + test + incident log entry |
| **1** | selection, collection, daily records, `study.yml` + scheduler entries; T1–T3 with the habit census, the family panel (style, flags, rhythm) and his self-splits; verdicts; roster annotation; `/study` page |
| **2** | reference panel; T4–T7; family co-activity; the `coactivity` vote |
| **3** | phone row + Studied tab; alerts; feed health; CLAUDE.md vector-table row and rules |

Each phase is merged and run in production before the next starts.

## 16. Risks and open questions

- **The family panel is all market makers so far.** It says operators keep their
  style across accounts; it says little about a discretionary trader with a
  script. His self-splits cover the second case; the smaller rate wins.
- **He may change his script again** (he did between March and April). T2 and T3
  then miss him; T1's flags (no client IDs, no triggers, no maker) and timing still
  carry. This is why only T1 can say against.
- **He may trade a new account by hand at first.** T1 reads `MANUAL_UI` →
  neutral, not against; timing must carry it.
- **Copiers.** Co-activity alone cannot tell a fast copier from a second hand; the
  cross-coin reading and lead/lag are the guard, and a copier still needs a money
  vector to reach PROBABLE.
- **Storage and history growth.** ≈ 10–15 MB/yr of archive plus rewritten summary
  files (dossiers only on change). Watched by `check_repo_size.py`.
- **Rate limits.** 900 weight/minute leaves headroom under Hyperliquid's 1,200 per IP.

## 17. Out of scope, and measured-no

- **Clock-skew fingerprinting** from action nonces: order nonces are not in the
  payloads we read; signed user actions (agent approvals, withdrawals) are too
  sparse.
- **A free-standing hour-of-day identifier:** measured unstable (median
  month-to-month correlation 0.14); kept only as T6 evidence.
- **Operator-parameter habits** (run length, run total, launch gap), measured
  2026-10-06 over 324 runs: lengths spread over 10, 11, 20, 30, 40, 100; totals are
  round numbers like everyone's; launch gap median 88 s. Not built; revisit with
  the census.
- **Changing the behavioural scanner** (approach B) and **real-time streaming**.
