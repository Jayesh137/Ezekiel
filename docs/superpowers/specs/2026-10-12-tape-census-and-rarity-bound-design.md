# Tape census, comparability bound and daily digest — design (2026-10-12)

## Why

The mission is to identify every Hyperliquid wallet he operates, and to catch
a new one the moment it appears. The
sharpest behavioural instrument this project owns is the execution-program
fingerprint: his SDK slicer's clip table (ZEC 1, SILVER 20, NEAR 250, BTC 0.1).
Measured 2026-09-28, 0 of 248 large accounts carried it. It was built to vote
only once a census had measured the match rare (rule 4), and that census could
not form: after 252 leaderboard accounts, 2 were *comparable* (carried a
constant-clip table on two or more of his coins), while the vote needed 20. At
one comparable account per ~125 reads the vote was years away, and the live
vector was casting nothing.

At the same time, `tape.yml` streams his 43 markets for ~150 s every run and
sees several hundred distinct takers per window (measured: 461 in 90 s; 700 in
120 s; his `xyz:` markets 7-19 takers each), then discards everything but an
exact-clip program burst. That is a free, keyless, venue-wide census of exactly
the population a migration wallet would join.

## What changes

### 1. The comparability bound (`src/execution_program.py`)

A stranger can reproduce his clip sizes only if it is comparable, so
P(reproduces his table) ≤ P(comparable). P(comparable) is a binomial
proportion the census has measured over hundreds of successful reads; its
Wilson 95% upper limit is a measured **ceiling** on the false-match rate.
A match is rare by the new `bounded` path when:

* the census has read ≥ `MIN_CENSUS_ATTEMPTS` = 200 accounts and reports a
  ceiling ≤ `MAX_FALSE_MATCH_RATE` = 0.05 (likelihood ratio ≥ 20), and
* the match is strong: exact ratio ≥ 0.75 over ≥ 3 shared clips with ≥ 3
  matched, **or** rank structure rho ≥ 0.9 over ≥ 4 coins (the identity
  permutation of 4 is 1 in 24 by chance; of 3 it is 1 in 6, too easy).

Not a relaxation of rule 4: no bar moved, a different statistic was measured,
and the bound is conservative — it assumes every comparable stranger would
match in full. Live: 2 of 252 → ceiling 2.85%. `rarity_path` names which path
judged a match rare (`exact` / `structure` / `bounded`) and the alert says so,
along with WHICH coins matched (round sizes on majors are common; his odd
sizes are not).

`merge_census` folds several censuses into one gate reading and takes the
**largest** ceiling, never the kindest: the detector's candidates come from
wallets trading his markets, where comparability is likelier than on the
leaderboard at large.

### 2. The tape census (`src/tape_census.py`, `scripts/collect_tape.py`)

Every window's taker orders are folded into a bounded per-wallet **sketch**
(`data/tape/sketches.json`, compact JSON, committed): first/last seen, windows,
and per coin the order count and a capped counter of base sizes. From it:

* **Census.** A wallet is *judgeable* once it has ≥ 8 taker orders on ≥ 2 of
  his clip coins (enough to show a comparable table if it ran one); fewer is
  unknown, not a non-match. Judgeable sketches are compared with his
  signature exactly as a stranger's fills would be. `data/tape/census.json`
  carries the comparability ceiling for this population, and the detector
  merges it with the leaderboard census.
* **Screen.** A sketch reproducing his table (ratio ≥ 0.75, ≥ 3 clips) or his
  rank structure (rho ≥ 0.9, ≥ 4 coins) is registered as a `tape_census`
  candidate. The detector reserves a slice of its budget for registry leads
  (`registry_candidates`: `tape_census`, `execution_program_tape`,
  `execution_program_census`, `tape_handoff`), reads their real fills and
  applies the census gate. Nothing on the tape mints a vote.
* **Newcomers in his silence.** A wallet first seen this window, in size
  (≥ $25K notional), on one of his *rare* markets (≤ 40 distinct sketch
  takers) gets one `portfolio` read (≤ 8 per window, largest first). If its
  real birth day lands inside an unusual silence of his — a closed anomalous
  gap in his history (`dormancy.handoff_score`) or the one open right now,
  judged against his own p90 — it is registered as a `tape_handoff` candidate
  and alerted through `alert_dormancy_handoff` (same cooldown key as the
  dormancy detector, so one wallet never pages twice). An unreadable birth is
  recorded as unread, never as "not a handoff".

Bounds: 3,000 wallets (~0.5 MB compact; evicts oldest-seen, comparable
sketches last), 24 coins per wallet, 6 sizes per coin. "First seen" means
first seen within the sketch's memory; the handoff judgement is made on the
wallet's real birth.

### 3. The daily digest (`src/digest.py`, `scripts/send_digest.py`, watch.yml)

One low-priority message a day at 07:00 UTC (the first watch run at or after
it; the day is a committed cursor): is he trading and is his silence normal;
hunt tiers, top casebook suspects, watched wallets, handoffs; the execution
gate and both ceilings; feeds blind, alerts healthy, data age, what paged in
the last 24 h. Sent through `alerts.send_notice`, which bypasses the severity
gate on purpose, posts at `low` priority (no sound) and records nothing in
delivery health. `data/digest/latest.json` holds the rendered text.

## What it costs if wrong

* A ceiling that is too kind would let a common clip style vote. Guarded by:
  the larger of two independent populations, the 5% cap, the strength
  requirement, and rule 9 (behaviour never reaches PROBABLE alone).
* A tape newcomer judged against the open silence could page on a wallet that
  merely opened during a quiet week of his: the rule requires the silence to
  be unusual against his own p90 and the birth within 3 days of its start,
  which is the dormancy detector's own bar.
* The sketch file is rewritten every ~20 minutes; measured 0.12 MB per 700
  wallets compact, capped at 3,000.

## Verification

Unit tests: `tests/test_execution_rarity_bound.py`, `tests/test_tape_census.py`,
`tests/test_digest.py`. Live: a 120 s local window folded 700 wallets, 7
judgeable, 0 comparable, 15 newcomers of which 8 births were read and none
scored (he was active, correctly); the digest rendered from production data.
