# Candidate Study — Phases 0 and 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the execution census so its population accumulates (Phase 0), then keep every identified Hyperliquid candidate under continuous study: daily records that outlive Hyperliquid's ~2-week window, the three tooling tests (execution style, slicing rhythm, clip table) calibrated against strangers, sub-account families and his own months, verdicts that annotate the roster, and a dashboard Study page (Phase 1).

**Architecture:** Pure modules in `src/study/` (`records`, `collect`, `archive`, `selection`, `tooling`, `calibration`, `panels`, `assemble`, `verdict`) driven by one I/O script, `scripts/run_study.py`, the only writer of `data/study/`, run every 6 h by a new `study.yml` in its own concurrency group. The census (`scripts/census_execution_program.py`, daily in analyze.yml) becomes a habit census that supplies the stranger panel. The roster reads `data/study/latest.json`.

**Tech Stack:** Python 3.12 (stdlib, `requests`, `numpy` already pinned — no new dependency), pytest, ruff; SvelteKit 2 / Svelte 5 in legacy syntax (`export let`, `$:`) with `node --test`; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-06-candidate-study-design.md` (approved; read it before starting).

**Sequence:** this plan covers spec Phases 0 and 1. The spec requires each phase to be merged and run in production before the next one starts, and Phase 2 (timing tests T4–T7, the 160-wallet reference panel, family co-activity, the `coactivity` vote) and Phase 3 (phone row and Studied tab, alerts, feed health, CLAUDE.md) depend on what Phase 1 measures in production — reference coverage, archive size, how fast the panels fill — so their plans are written after Phase 1 has run. Everything Phase 2 needs is already stored by Phase 1's daily records (minute maps, per-coin minute maps, decisions with coin and side, ledger), so no history is lost while it waits. Phase 0 (Task 1) ships as its own PR before Task 2 begins.

## Global Constraints

- A failed read is never an empty result (CLAUDE.md rule 5): it never advances a cursor and never writes a record; minutes outside read coverage are *unknown*, never quiet.
- A missing value is `None`/`null`, never `0` (rule 6).
- The target (`config.target_wallet`) never enters the study set, the census, any panel or any stranger list.
- One writer per file: `scripts/run_study.py` writes all of `data/study/**` and nothing else under `data/`; `scripts/census_execution_program.py` writes only `data/execution_program/census.json` and `census_state.json`.
- Evidence **against** never changes a tier, a vector or `peak_tier` (operator's decision, spec §9).
- Behaviour votes never reach PROBABLE alone: `roster.assign_tier` is not modified.
- Pre-registered bars (spec §8.2), copied verbatim into `src/study/calibration.py` and never tuned: ≥ 200 measurable strangers; ≥ 40 same-operator family pairs, or ≥ 6 of his own monthly windows for the `self_only` basis; **for** = one-sided 95% Clopper–Pearson upper bound on the stranger match rate ≤ 2%, evaluated at the looser of (the candidate's level, each usable same-operator median); **against** = T1 only, a trait under 0.1% of his recorded orders that the candidate shows on more than half of ≥ 100 orders, with same-operator mismatch ≤ 10% over ≥ 40 family pairs.
- Budgets nest: one read < `ReadBudget(seconds=900)` < step `timeout-minutes: 25` < job `timeout-minutes: 35`.
- Tests are network-free and never write into the real `data/` (`tests/conftest.py`). New modules read `utils.DATA_DIR` at call time, never at import.
- `python -m ruff check src/ tests/ scripts/` passes before every commit (`zip()` needs `strict=`).
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

The five inputs the spec implies that are most likely to bite, each pinned by a test in its owning task:

1. **More than 10,000 fills arrive between two reads** (a busy bot): coverage starts at the first fill read and the day is marked `saturated`; the earlier span is never claimed quiet. → Task 3 `test_five_full_pages_mean_the_oldest_span_may_be_missing`, Task 10 `test_a_saturated_read_moves_the_cursor_without_claiming_the_gap`.
2. **The orders read fails after the fills read succeeded in the same run** (a 429 or the budget mid-wallet): the fills fold and its cursor advance are saved, the orders are retried next run, and the run stops instead of marking anything read. → Task 10 `test_a_stop_after_the_fills_fold_keeps_the_fold_and_retries_the_orders`.
3. **A busy bot whose newest 2,000 orders are all minutes old**: its habits are still recorded (the live dry run on 2026-10-06 found two bots whose habits an age-based wait would never have counted). → Task 10 `test_a_busy_wallets_newest_orders_still_count`.
4. **A run of slices crossing UTC midnight**: orders split by their own day, the run belongs to its start day, coverage is split per day. → Task 2 `test_a_run_crossing_midnight_belongs_to_its_start_day`. (An overlapping re-read never double counts: Task 2 `test_an_overlapping_reread_never_counts_a_fill_twice`, confirmed live — 34 of 34 days before the old cursor unchanged on a second run.)
5. **The target appearing in the roster, a detector file, the census or a sub-account family**: filtered at every entry point. → Task 4 `test_sources_in_priority_order_and_the_target_never_studied`, Task 8 `test_panels_never_contain_the_target`, Task 10 `test_the_target_is_never_studied_even_when_pinned`.

## File Map

| File | Status | Responsibility |
|---|---|---|
| `scripts/census_execution_program.py` | modify | Phase 0 state path; habit-census rows (orders + sub-accounts) |
| `docs/incident-log.md` | modify | Phase 0 incident entry |
| `src/execution_program.py` | modify | `reconstruct_orders` also returns each order's `oid` |
| `src/study/__init__.py` | create | package marker |
| `src/study/records.py` | create | raw reads → additive daily records; quiet boundary; runs; habits |
| `src/study/collect.py` | create | strict paged reads (fills, recent fills, orders, ledger) |
| `src/study/archive.py` | create | daily files, sealed-month `.jsonl.gz`, state, write-if-changed |
| `src/study/selection.py` | create | study set: sources, HL-trader rule, decayed leads, stickiness |
| `src/study/tooling.py` | create | window summary, habit profile, style, T1/T2/T3, snapshots |
| `src/study/calibration.py` | create | Clopper–Pearson bound and the fixed bars |
| `src/study/panels.py` | create | stranger and family panels for T1–T3 |
| `src/study/verdict.py` | create | family verdicts, study rank, transitions |
| `src/study/assemble.py` | create | his reference, self-splits, per-wallet tests, rows, dossiers, latest |
| `scripts/run_study.py` | create | orchestration; only writer of `data/study/` |
| `src/roster.py` | modify | `evidence.study` annotation; tooling *for* → `execution_program` |
| `.github/workflows/study.yml` | create | 6-hourly run in group `study` |
| `scripts/keep_schedule.py`, `scripts/dispatch_workflows.ps1`, `scripts/apps_script/ezekiel_relay.gs` | modify | schedule `study.yml` (360 min, group `study`) |
| `config.json` | modify | `study_wallets: []`, `study: {max_wallets: 40}` |
| `dashboard/src/lib/study.js` (+ `study.test.js`) | create | pure helpers for the Study page |
| `dashboard/src/lib/api.js`, `src/lib/ui/Icon.svelte`, `src/routes/+layout.svelte` | modify | fetchers, icon, nav entry |
| `dashboard/src/routes/study/+page.svelte` | create | Study page |
| `tests/test_study_*.py`, `tests/test_run_study.py`, `tests/test_roster_study.py` | create | tests per task |
| `tests/conftest.py` | modify | probe: no test may write `data/study/` |

---

## Phase 0

### Task 1: The census state lives in the committed tree

**Files:**
- Modify: `scripts/census_execution_program.py:10-13` (docstring) and `:36-43` (delete the overriding assignment)
- Modify: `docs/incident-log.md` (append an entry)
- Test: `tests/test_census_execution_program.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `census.STATE == census.OUT_DIR / "census_state.json"`; the file `data/execution_program/census_state.json` is written by every census run and committed by analyze.yml's existing `git add data/`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_census_execution_program.py`:

```python
def test_state_lives_in_the_committed_tree():
    # bb21cb3092 moved the census state out of gitignored data/.local so the
    # population could build across ephemeral Actions runs, but the old
    # assignment stayed beneath the new one and won: every run started from an
    # empty state, and the census sat at 1 measured account of 83.
    assert census.STATE == census.OUT_DIR / "census_state.json"
    assert ".local" not in census.STATE.parts
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_census_execution_program.py::test_state_lives_in_the_committed_tree -v`
Expected: FAIL — `assert WindowsPath('…/data/.local/execution_census.json') == …census_state.json` (or the POSIX equivalent).

- [ ] **Step 3: Delete the overriding line and correct the docstring**

In `scripts/census_execution_program.py`, remove exactly this line (it sits directly under `MAX_STATE_ROWS = 20_000`):

```python
STATE = DATA_DIR / ".local" / "execution_census.json"
```

and replace the docstring paragraph

```
It is resumable: processed addresses and their ratios persist in the gitignored
`data/.local/execution_census.json`, so a bounded run on a free VM accumulates the
full population across restarts. Any account that itself reproduces the table
(a real lead) is recorded in the output regardless of the threshold.
```

with

```
It is resumable: processed addresses and their ratios persist in the committed
`data/execution_program/census_state.json`, so successive Actions runs walk deeper
into the population instead of re-measuring the same accounts. Any account that
itself reproduces the table (a real lead) is recorded in the output regardless of
the threshold.
```

- [ ] **Step 4: Run the census tests**

Run: `python -m pytest tests/test_census_execution_program.py -v`
Expected: all PASS.

- [ ] **Step 5: Record the incident** — append to `docs/incident-log.md`:

```markdown

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
```

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add scripts/census_execution_program.py tests/test_census_execution_program.py docs/incident-log.md
git commit -m "fix: census state persists in the committed tree (the override won)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Ship Phase 0 alone.** Open a PR for this commit, merge it, and after the next two daily `analyze.yml` runs confirm `data/execution_program/census.json` shows `measured` increasing (spec §14 acceptance 1). Phase 1 continues on its own branch.

---

## Phase 1

### Task 2: Daily records

**Files:**
- Modify: `src/execution_program.py` (`reconstruct_orders`, lines ~80-107)
- Create: `src/study/__init__.py`, `src/study/records.py`
- Test: `tests/test_study_records.py`, `tests/test_execution_program.py`

**Interfaces:**
- Consumes: `execution_program.reconstruct_orders(fills)`, `ep.MIN_PROGRAM_ORDERS`, `ep.MIN_CLIP_SHARE`, `ep.CADENCE_LOW_S`, `ep.CADENCE_HIGH_S`, `ep.CADENCE_MIN_P10_S`, `ep.PROGRAM_GAP_BREAK_S`, `ep.SDK_SLIPPAGE`, `ep.SLIPPAGE_TOLERANCE`.
- Produces (used by every later task):
  - constants `SCHEMA, DAY_MS, HOUR_MS, MINUTE_MS, COVERED_DAY_MS, CADENCE_BINS, CADENCE_BIN_MS, OTHER, BOUNDARY_LAG_MS`
  - `day_of(ts_ms) -> str`, `day_start_ms(day) -> int`
  - `encode_minutes(iterable[int]) -> str`, `decode_minutes(str|None) -> set[int]`
  - `merge_intervals(list) -> list[list[int]]`, `covered_ms(intervals, lo, hi) -> int`, `covered_day(record) -> bool`
  - `empty_day(wallet, day, role) -> dict`, `empty_habits() -> dict`, `add_habits(a|None, b) -> dict`
  - `taker_runs(orders) -> list[list[dict]]`, `cadence_histogram(orders) -> list[int]`
  - `first_prices(fills) -> dict[str, float]`, `habit_counts(entries, first_px) -> dict`
  - `fold_fills(days, fills, *, wallet, role, start_ms, end_ms, last_fill_ms, saturated=False, runs_split=False) -> int | None`
  - `fold_orders(days, entries, first_px, *, wallet, role, start_ms, end_ms) -> None`
  - `fold_ledger(days, rows, *, wallet, role, start_ms, end_ms) -> None`
  - `quiet_boundary(times, cursor_ms, known_until_ms) -> tuple[int, bool]`
  - `execution_program.reconstruct_orders` rows gain key `"oid"` (None for a fill without one).

- [ ] **Step 1: Write the failing tests** — create `tests/test_study_records.py`:

```python
"""Daily records: additive, never counting a fill twice, never claiming what was not read."""

from src.study import records as rec

W = "0x" + "a" * 40
DAY0 = 1_790_899_200_000  # 2026-10-02 00:00:00 UTC
HOUR = rec.HOUR_MS


def fill(t, coin="BTC", side="B", sz="0.1", px="100.0", crossed=True):
    return {"coin": coin, "side": side, "sz": sz, "px": px, "time": t,
            "crossed": crossed, "oid": t, "tid": t}


def program(start, n, gap_ms=1_700, coin="BTC", side="B"):
    return [fill(start + i * gap_ms, coin, side) for i in range(n)]


def order(t, tif="Ioc", side="B", px="105.0", cloid=None, status="filled",
          trigger=False, otype="Limit"):
    return {"order": {"coin": "BTC", "side": side, "limitPx": px, "oid": t, "timestamp": t,
                      "tif": tif, "cloid": cloid, "isTrigger": trigger, "orderType": otype,
                      "reduceOnly": False}, "status": status}


def test_minutes_round_trip():
    assert rec.decode_minutes(rec.encode_minutes({0, 7, 8, 1439})) == {0, 7, 8, 1439}
    assert rec.decode_minutes(None) == set()


def test_a_program_run_is_counted_once_with_its_cadence():
    days = {}
    fills = program(DAY0 + HOUR, 20)
    last = rec.fold_fills(days, fills, wallet=W, role="studied", start_ms=DAY0,
                          end_ms=DAY0 + 2 * HOUR, last_fill_ms=None)
    day = days["2026-10-02"]
    assert (day["fills"], day["orders"], day["taker_orders"], day["program_runs"]) == (20, 20, 20, 1)
    assert [r["n"] for r in day["runs"]] == [20] and day["runs"][0]["clip"] == 0.1
    assert day["cadence"][17] == 19 and sum(day["cadence"]) == 19
    assert [d[3] for d in day["decisions"]] == ["run", "session"]
    assert rec.decode_minutes(day["minutes"]) == {60}
    assert day["coverage"]["fills"] == [[DAY0, DAY0 + 2 * HOUR]]
    assert last == fills[-1]["time"]


def test_an_overlapping_reread_never_counts_a_fill_twice():
    fills = program(DAY0 + HOUR, 20) + program(DAY0 + 3 * HOUR, 20, coin="ETH")
    once = {}
    rec.fold_fills(once, fills, wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + 4 * HOUR, last_fill_ms=None)
    twice = {}
    mid = DAY0 + 2 * HOUR
    last = rec.fold_fills(twice, fills[:20], wallet=W, role="studied", start_ms=DAY0,
                          end_ms=mid, last_fill_ms=None)
    # The second read re-delivers every fill; only [mid, end) may be folded.
    rec.fold_fills(twice, fills, wallet=W, role="studied", start_ms=mid,
                   end_ms=DAY0 + 4 * HOUR, last_fill_ms=last)
    assert twice == once


def test_duplicate_rows_inside_one_batch_count_once():
    days = {}
    f = fill(DAY0 + HOUR)
    rec.fold_fills(days, [f, dict(f)], wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + 2 * HOUR, last_fill_ms=None)
    assert days["2026-10-02"]["fills"] == 1


def test_the_boundary_never_cuts_through_a_run():
    now = DAY0 + 10 * HOUR
    run = [now - 6 * 60_000 + i * 1_700 for i in range(200)]  # crosses now - 5 min
    boundary, split = rec.quiet_boundary([now - HOUR] + run, DAY0, now)
    assert (boundary, split) == (run[0], False)


def test_a_quiet_tail_commits_up_to_five_minutes_before_now():
    now = DAY0 + 10 * HOUR
    assert rec.quiet_boundary([now - 30 * 60_000], DAY0, now) == (now - rec.BOUNDARY_LAG_MS, False)


def test_continuous_quoting_falls_back_to_the_hour_mark():
    now = DAY0 + 10 * HOUR + 20 * 60_000
    times = list(range(DAY0 + 7 * HOUR, now, 5_000))
    assert rec.quiet_boundary(times, DAY0 + 7 * HOUR, now) == (DAY0 + 10 * HOUR, True)


def test_nothing_new_to_commit_leaves_the_cursor():
    now = DAY0 + HOUR
    assert rec.quiet_boundary([], now - 60_000, now) == (now - 60_000, False)


def test_a_run_crossing_midnight_belongs_to_its_start_day():
    fills = program(DAY0 + rec.DAY_MS - 30_000, 40)  # 23:59:30, ends 00:00:36
    days = {}
    rec.fold_fills(days, fills, wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + 2 * rec.DAY_MS, last_fill_ms=None)
    first, second = days["2026-10-02"], days["2026-10-03"]
    assert (first["orders"], second["orders"]) == (18, 22)
    assert [r["n"] for r in first["runs"]] == [40] and second["runs"] == []
    assert first["coverage"]["fills"] == [[DAY0, DAY0 + rec.DAY_MS]]
    assert second["coverage"]["fills"] == [[DAY0 + rec.DAY_MS, DAY0 + 2 * rec.DAY_MS]]


def test_a_session_start_is_never_assumed_before_the_read():
    days = {}
    rec.fold_fills(days, [fill(DAY0 + 10 * 60_000)], wallet=W, role="studied",
                   start_ms=DAY0, end_ms=DAY0 + HOUR, last_fill_ms=None)
    assert days["2026-10-02"]["decisions"] == []
    days = {}
    rec.fold_fills(days, [fill(DAY0 + 40 * 60_000)], wallet=W, role="studied",
                   start_ms=DAY0, end_ms=DAY0 + HOUR, last_fill_ms=None)
    assert [d[3] for d in days["2026-10-02"]["decisions"]] == ["session"]


def test_saturation_is_marked_on_the_first_day_read():
    days = {}
    rec.fold_fills(days, [], wallet=W, role="studied", start_ms=DAY0 + HOUR,
                   end_ms=DAY0 + rec.DAY_MS + HOUR, last_fill_ms=None, saturated=True)
    assert days["2026-10-02"]["coverage"]["saturated"] is True
    assert days["2026-10-03"]["coverage"]["saturated"] is False


def test_more_than_twenty_coins_fold_the_tail_into_other():
    fills = [fill(DAY0 + i * 40_000, coin=f"C{i:02d}") for i in range(25)]
    days = {}
    rec.fold_fills(days, fills, wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + rec.DAY_MS, last_fill_ms=None)
    coins = days["2026-10-02"]["coins"]
    assert len(coins) == rec.MAX_COINS
    assert coins[rec.OTHER]["orders"] == 25 - (rec.MAX_COINS - 1)
    assert sum(c["orders"] for c in coins.values()) == 25


def test_orders_fold_habits_offsets_and_web_clicks():
    t = DAY0 + HOUR
    entries = [order(t), order(t + 1, tif="FrontendMarket", otype="Market", px="0"),
               order(t + 2, tif="Alo", cloid="0x01", status="canceled"),
               order(t + 3, tif=None, trigger=True, otype="Stop Market", status="open")]
    days = {}
    rec.fold_orders(days, entries, {str(t): 100.0}, wallet=W, role="studied",
                    start_ms=DAY0, end_ms=DAY0 + rec.DAY_MS)
    day = days["2026-10-02"]
    h = day["habits"]
    assert h["orders_seen"] == 4
    assert h["tif"] == {"Ioc": 1, "Gtc": 0, "Alo": 1, "FrontendMarket": 1, "other": 1}
    assert (h["cloid"], h["trigger"], h["canceled"], h["open"]) == (1, 1, 1, 1)
    assert (h["ioc_offset_seen"], h["ioc_offset_5pct"]) == (1, 1)
    assert rec.decode_minutes(day["manual_minutes"]) == {60}
    assert [d[3] for d in day["decisions"]] == ["manual"]


def test_a_day_no_order_read_covered_keeps_its_habits_unknown():
    days = {}
    rec.fold_fills(days, [fill(DAY0 + 60_000)], wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + HOUR, last_fill_ms=None)
    assert days["2026-10-02"]["habits"] is None
    assert days["2026-10-02"]["manual_minutes"] is None
    assert days["2026-10-02"]["ledger"] is None


def test_ledger_rows_are_kept_once():
    row = {"time": DAY0 + 5, "hash": "0xh", "delta": {"type": "deposit", "usdc": "1000.0"}}
    days = {}
    for _ in range(2):
        rec.fold_ledger(days, [row], wallet=W, role="studied", start_ms=DAY0,
                        end_ms=DAY0 + rec.DAY_MS)
    assert days["2026-10-02"]["ledger"] == [
        {"ts_ms": DAY0 + 5, "type": "deposit", "usd": 1000.0, "hash": "0xh"}]


def test_covered_day_needs_twenty_hours():
    day = rec.empty_day(W, "2026-10-02", "studied")
    day["coverage"]["fills"] = [[DAY0, DAY0 + 19 * HOUR]]
    assert not rec.covered_day(day)
    day["coverage"]["fills"] = [[DAY0, DAY0 + 21 * HOUR]]
    assert rec.covered_day(day)
```

Append to `tests/test_execution_program.py`:

```python
def test_reconstructed_orders_carry_their_oid():
    fills = [{"coin": "BTC", "side": "B", "sz": "1", "px": "10", "time": 1, "oid": 7, "tid": 1,
              "crossed": True},
             {"coin": "BTC", "side": "B", "sz": "1", "px": "10", "time": 1, "oid": 7, "tid": 2,
              "crossed": True}]
    [order] = ep.reconstruct_orders(fills)
    assert order["oid"] == 7 and order["base_size"] == 2.0
```

(If `tests/test_execution_program.py` imports the module under another name, use that name instead of `ep`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_study_records.py tests/test_execution_program.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.study'` and `KeyError: 'oid'`.

- [ ] **Step 3: Carry the oid through `reconstruct_orders`** — in `src/execution_program.py` replace the loop that builds `orders`:

```python
    orders = []
    for key, rows in groups.items():
        first = min(rows, key=lambda r: r["t"])
        crossed = [r["crossed"] for r in rows if isinstance(r["crossed"], bool)]
        orders.append({"t": first["t"], "coin": first["coin"], "side": first["side"],
                       "base_size": round(sum(r["sz"] for r in rows), 10),
                       "notional": sum(r["sz"] * r["px"] for r in rows),
                       "taker": bool(crossed) and all(crossed), "first_px": first["px"],
                       "oid": None if isinstance(key, tuple) else key})
```

- [ ] **Step 4: Create the package and the records module**

`src/study/__init__.py`:

```python
"""Candidate study: every identified HL candidate under continuous study (spec 2026-10-06)."""
```

`src/study/records.py`:

```python
"""Raw Hyperliquid reads folded into additive daily records. Pure; no I/O.

A record is the sum of every batch folded into it, so the caller folds only rows
inside [start_ms, end_ms) and moves its cursor to end_ms; a re-read overlapping
the last batch adds nothing. `quiet_boundary` puts end_ms inside a gap of at
least 30 s, so no run of slices is split across two folds (spec 2026-10-06 §6).
Anything not read stays unknown: coverage says what was read, and a field the
reads never covered is None, never 0 (rules 5 and 6).
"""

from __future__ import annotations

import base64
import math
from collections import Counter
from datetime import UTC, datetime
from statistics import median

from src import execution_program as ep

SCHEMA = "study-day/1"
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
MINUTES_PER_DAY = 1_440
COVERED_DAY_MS = 20 * HOUR_MS
RUN_GAP_MS = int(ep.PROGRAM_GAP_BREAK_S * 1000)
SESSION_GAP_MS = 30 * MINUTE_MS
QUIET_GAP_MS = 30_000
BOUNDARY_LAG_MS = 5 * MINUTE_MS
MIN_STORED_RUN = 10
CADENCE_BINS = 50
CADENCE_BIN_MS = 100
MAX_DECISIONS = 100
MAX_RUNS = 100
MAX_COINS = 20
MAX_COIN_MINUTE_MAPS = 5
MAX_LEDGER = 50
OTHER = "_other"
TIFS = ("Ioc", "Gtc", "Alo", "FrontendMarket")


def num(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def day_start_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def encode_minutes(minutes) -> str:
    bits = bytearray(MINUTES_PER_DAY // 8)
    for minute in minutes:
        if 0 <= minute < MINUTES_PER_DAY:
            bits[minute // 8] |= 1 << (minute % 8)
    return base64.b64encode(bytes(bits)).decode("ascii")


def decode_minutes(text) -> set[int]:
    if not text:
        return set()
    raw = base64.b64decode(text)
    return {i * 8 + bit for i, byte in enumerate(raw) for bit in range(8) if byte >> bit & 1}


def merge_intervals(intervals) -> list[list[int]]:
    merged: list[list[int]] = []
    for lo, hi in sorted((int(a), int(b)) for a, b in intervals if b > a):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def covered_ms(intervals, lo: int, hi: int) -> int:
    return sum(max(0, min(b, hi) - max(a, lo)) for a, b in intervals)


def covered_day(record: dict) -> bool:
    """A day counts as covered once at least 20 hours of its fills were read."""
    start = day_start_ms(record["day"])
    return covered_ms(record["coverage"]["fills"], start, start + DAY_MS) >= COVERED_DAY_MS


def split_by_day(lo: int, hi: int) -> list[tuple[str, int, int]]:
    pieces, t = [], lo
    while t < hi:
        end = min(hi, (t // DAY_MS + 1) * DAY_MS)
        pieces.append((day_of(t), t, end))
        t = end
    return pieces


def empty_day(wallet: str, day: str, role: str) -> dict:
    return {"schema": SCHEMA, "wallet": wallet, "day": day, "role": role,
            "coverage": {"fills": [], "orders": [], "ledger": [],
                         "saturated": False, "runs_split": False},
            "fills": 0, "orders": 0, "taker_orders": 0,
            "minutes": encode_minutes(()), "coin_minutes": {}, "manual_minutes": None,
            "decisions": [], "decisions_overflow": 0,
            "runs": [], "runs_overflow": 0, "program_runs": 0,
            "habits": None, "cadence": [0] * CADENCE_BINS, "coins": {}, "ledger": None}


def size_key(size: float) -> str:
    return format(size, ".10g")


# --- runs ---------------------------------------------------------------------------

def taker_runs(orders: list[dict]) -> list[list[dict]]:
    """Consecutive same-coin, same-side taker orders, broken by a gap over 30 s:
    `execution_program.program_runs`' definition, so both modules see one run."""
    runs: list[list[dict]] = []
    current: list[dict] = []
    for order in orders:
        if not order.get("taker"):
            continue
        if current and (order["coin"] != current[-1]["coin"]
                        or order["side"] != current[-1]["side"]
                        or order["t"] - current[-1]["t"] > RUN_GAP_MS):
            runs.append(current)
            current = []
        current.append(order)
    if current:
        runs.append(current)
    return runs


def run_summary(run: list[dict]) -> dict:
    gaps = sorted(b["t"] - a["t"] for a, b in zip(run, run[1:], strict=False))
    clip, count = Counter(o["base_size"] for o in run).most_common(1)[0]
    return {"coin": run[0]["coin"], "side": run[0]["side"],
            "start_ms": int(run[0]["t"]), "end_ms": int(run[-1]["t"]), "n": len(run),
            "clip": clip, "clip_share": round(count / len(run), 4),
            "gap_p10_ms": int(gaps[len(gaps) // 10]) if gaps else None,
            "gap_p50_ms": int(median(gaps)) if gaps else None}


def is_program_run(summary: dict) -> bool:
    """`execution_program.program_runs`' test applied to a stored run summary."""
    p50, p10 = summary.get("gap_p50_ms"), summary.get("gap_p10_ms")
    return (summary["n"] >= ep.MIN_PROGRAM_ORDERS
            and summary["clip_share"] >= ep.MIN_CLIP_SHARE
            and p50 is not None and ep.CADENCE_LOW_S * 1000 <= p50 <= ep.CADENCE_HIGH_S * 1000
            and p10 is not None and p10 >= ep.CADENCE_MIN_P10_S * 1000)


def cadence_add(hist: list[int], run: list[dict]) -> None:
    if len(run) < ep.MIN_PROGRAM_ORDERS:
        return
    for a, b in zip(run, run[1:], strict=False):
        hist[min(int((b["t"] - a["t"]) // CADENCE_BIN_MS), CADENCE_BINS - 1)] += 1


def cadence_histogram(orders: list[dict]) -> list[int]:
    """Gaps between slices inside runs of >= 15 orders, 100 ms bins from 0 to 5 s."""
    hist = [0] * CADENCE_BINS
    for run in taker_runs(orders):
        cadence_add(hist, run)
    return hist


# --- folding fills --------------------------------------------------------------------

def _add_decision(record: dict, decision: list) -> None:
    if decision in record["decisions"]:
        return
    if len(record["decisions"]) >= MAX_DECISIONS:
        record["decisions_overflow"] += 1
        return
    record["decisions"].append(decision)
    record["decisions"].sort(key=lambda d: (d[0], str(d[1]), str(d[2]), d[3]))


def _cap_coins(record: dict) -> None:
    coins = record["coins"]
    other = coins.pop(OTHER, {"orders": 0})
    ranked = sorted(coins.items(), key=lambda kv: (-kv[1]["orders"], kv[0]))
    kept = dict(ranked[:MAX_COINS - 1])
    for _coin, stats in ranked[MAX_COINS - 1:]:
        other["orders"] += stats["orders"]
    kept[OTHER] = other
    record["coins"] = kept


def _add_coin(record: dict, order: dict) -> None:
    stats = record["coins"].setdefault(str(order["coin"]), {
        "orders": 0, "buy_usd": 0.0, "sell_usd": 0.0, "taker_clips": {},
        "px_sum": 0.0, "px_n": 0})
    stats["orders"] += 1
    side = "buy_usd" if order["side"] == "B" else "sell_usd"
    stats[side] = round(stats[side] + order["notional"], 2)
    if order["taker"]:
        key = size_key(order["base_size"])
        stats["taker_clips"][key] = stats["taker_clips"].get(key, 0) + 1
        stats["px_sum"] = round(stats["px_sum"] + order["first_px"], 8)
        stats["px_n"] += 1
    if len(record["coins"]) > MAX_COINS:
        _cap_coins(record)


def _merge_coin_minutes(record: dict, batch: dict[str, set]) -> None:
    current = {coin: decode_minutes(text) for coin, text in record["coin_minutes"].items()}
    for coin, minutes in batch.items():
        current[coin] = current.get(coin, set()) | minutes
    other = current.pop(OTHER, set())
    ranked = sorted(current.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    kept = dict(ranked[:MAX_COIN_MINUTE_MAPS])
    for _coin, minutes in ranked[MAX_COIN_MINUTE_MAPS:]:
        other |= minutes
    if other:
        kept[OTHER] = other
    record["coin_minutes"] = {coin: encode_minutes(minutes) for coin, minutes in kept.items()}


def fold_fills(days: dict, fills: list, *, wallet: str, role: str, start_ms: int,
               end_ms: int, last_fill_ms: int | None, saturated: bool = False,
               runs_split: bool = False) -> int | None:
    """Fold the fills with start_ms <= time < end_ms into `days` (mutated).

    Returns the time of the last order folded (or `last_fill_ms` if none), kept by
    the caller so the next batch can tell a session start. With no earlier fill
    known, an order is a session start only if the read itself covered the 30
    minutes before it: silence is never assumed (rule 5).
    """
    if end_ms <= start_ms:
        return last_fill_ms
    rows, seen = [], set()
    for fill in fills or []:
        t = fill.get("time") if isinstance(fill, dict) else None
        if not isinstance(t, (int, float)) or not start_ms <= t < end_ms:
            continue
        key = fill["tid"] if fill.get("tid") is not None else (
            fill.get("oid"), t, fill.get("px"), fill.get("sz"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(fill)
    for day, lo, hi in split_by_day(start_ms, end_ms):
        record = days.setdefault(day, empty_day(wallet, day, role))
        coverage = record["coverage"]
        coverage["fills"] = merge_intervals(coverage["fills"] + [[lo, hi]])
        if saturated and lo == start_ms:
            coverage["saturated"] = True
        if runs_split:
            coverage["runs_split"] = True
    minutes: dict[str, set] = {}
    coin_minutes: dict[str, dict[str, set]] = {}
    for fill in rows:
        t = int(fill["time"])
        day, minute = day_of(t), (t % DAY_MS) // MINUTE_MS
        days[day]["fills"] += 1
        minutes.setdefault(day, set()).add(minute)
        coin_minutes.setdefault(day, {}).setdefault(str(fill.get("coin")), set()).add(minute)
    for day, found in minutes.items():
        record = days[day]
        record["minutes"] = encode_minutes(decode_minutes(record["minutes"]) | found)
        _merge_coin_minutes(record, coin_minutes[day])
    orders = ep.reconstruct_orders(rows)
    previous = last_fill_ms
    for order in orders:
        t = int(order["t"])
        record = days[day_of(t)]
        record["orders"] += 1
        record["taker_orders"] += bool(order["taker"])
        _add_coin(record, order)
        session = (t - previous > SESSION_GAP_MS) if previous is not None \
            else (t - start_ms >= SESSION_GAP_MS)
        if session:
            _add_decision(record, [t, order["coin"], order["side"], "session"])
        previous = t
    for run in taker_runs(orders):
        summary = run_summary(run)
        record = days[day_of(summary["start_ms"])]
        cadence_add(record["cadence"], run)
        record["program_runs"] += is_program_run(summary)
        if summary["n"] >= MIN_STORED_RUN:
            if len(record["runs"]) < MAX_RUNS:
                record["runs"].append(summary)
            else:
                record["runs_overflow"] += 1
            _add_decision(record, [summary["start_ms"], summary["coin"], summary["side"], "run"])
    return int(orders[-1]["t"]) if orders else last_fill_ms


# --- folding orders -------------------------------------------------------------------

def status_kind(status) -> str:
    text = str(status or "")
    if text == "open":
        return "open"
    if text in ("filled", "triggered"):
        return "filled"
    if text.endswith("anceled"):          # canceled, marginCanceled, reduceOnlyCanceled, ...
        return "canceled"
    if text.endswith("Rejected"):         # badAloPxRejected, iocCancelRejected, ...
        return "rejected"
    return "other"


def is_trigger(order: dict) -> bool:
    return bool(order.get("isTrigger")) or str(order.get("orderType") or "").startswith(
        ("Stop", "Take Profit"))


def empty_habits() -> dict:
    return {"orders_seen": 0, "tif": {**dict.fromkeys(TIFS, 0), "other": 0}, "cloid": 0,
            "trigger": 0, "reduce_only": 0, "canceled": 0, "rejected": 0, "open": 0,
            "ioc_offset_seen": 0, "ioc_offset_5pct": 0}


def add_habits(a: dict | None, b: dict) -> dict:
    out = empty_habits() if a is None else {**a, "tif": dict(a["tif"])}
    for key, value in b.items():
        if key == "tif":
            for tif, n in value.items():
                out["tif"][tif] = out["tif"].get(tif, 0) + n
        else:
            out[key] = out.get(key, 0) + value
    return out


def first_prices(fills: list) -> dict[str, float]:
    """oid -> price of the order's earliest fill, the reference the SDK priced off."""
    prices: dict[str, float] = {}
    when: dict[str, float] = {}
    for fill in fills or []:
        if not isinstance(fill, dict) or fill.get("oid") is None:
            continue
        px, t = num(fill.get("px")), fill.get("time")
        oid = str(fill["oid"])
        if px and isinstance(t, (int, float)) and (oid not in when or t < when[oid]):
            prices[oid], when[oid] = px, t
    return prices


def habit_counts(entries: list, first_px: dict) -> dict:
    """Submission habits over historicalOrders entries. The IOC offset is measured only
    for orders whose first fill price is known."""
    counts = empty_habits()
    for entry in entries or []:
        order = entry.get("order") if isinstance(entry, dict) else None
        if not isinstance(order, dict):
            continue
        counts["orders_seen"] += 1
        tif = order.get("tif")
        counts["tif"][tif if tif in TIFS else "other"] += 1
        counts["cloid"] += bool(order.get("cloid"))
        counts["trigger"] += is_trigger(order)
        counts["reduce_only"] += bool(order.get("reduceOnly"))
        kind = status_kind(entry.get("status"))
        if kind in ("canceled", "rejected", "open"):
            counts[kind] += 1
        if tif == "Ioc":
            limit, ref = num(order.get("limitPx")), first_px.get(str(order.get("oid")))
            if limit and ref:
                counts["ioc_offset_seen"] += 1
                offset = (limit / ref - 1) if order.get("side") == "B" else (1 - limit / ref)
                if abs(offset - ep.SDK_SLIPPAGE) <= ep.SLIPPAGE_TOLERANCE:
                    counts["ioc_offset_5pct"] += 1
    return counts


def fold_orders(days: dict, entries: list, first_px: dict, *, wallet: str, role: str,
                start_ms: int, end_ms: int) -> None:
    """Fold historicalOrders entries PLACED in [start_ms, end_ms): habits, web-UI clicks."""
    if end_ms <= start_ms:
        return
    by_day: dict[str, list] = {}
    for entry in entries or []:
        order = entry.get("order") if isinstance(entry, dict) else None
        t = order.get("timestamp") if isinstance(order, dict) else None
        if isinstance(t, (int, float)) and start_ms <= t < end_ms:
            by_day.setdefault(day_of(int(t)), []).append(entry)
    for day, lo, hi in split_by_day(start_ms, end_ms):
        record = days.setdefault(day, empty_day(wallet, day, role))
        record["coverage"]["orders"] = merge_intervals(record["coverage"]["orders"] + [[lo, hi]])
        placed = by_day.get(day, [])
        record["habits"] = add_habits(record["habits"], habit_counts(placed, first_px))
        manual = decode_minutes(record["manual_minutes"])
        for entry in placed:
            order = entry["order"]
            if order.get("tif") == "FrontendMarket":
                t = int(order["timestamp"])
                manual.add((t % DAY_MS) // MINUTE_MS)
                _add_decision(record, [t, order.get("coin"), order.get("side"), "manual"])
        record["manual_minutes"] = encode_minutes(manual)


# --- folding the ledger --------------------------------------------------------------

def ledger_usd(delta: dict) -> float | None:
    for key in ("usdcValue", "usdc", "usd"):
        value = num(delta.get(key))
        if value is not None:
            return abs(value)
    return None


def fold_ledger(days: dict, rows: list, *, wallet: str, role: str, start_ms: int,
                end_ms: int) -> None:
    """Fold non-funding ledger rows (deposits, withdrawals, sends) in [start_ms, end_ms)."""
    if end_ms <= start_ms:
        return
    picked: dict[str, list] = {}
    for row in rows or []:
        t = row.get("time") if isinstance(row, dict) else None
        delta = row.get("delta") if isinstance(row, dict) else None
        if isinstance(t, (int, float)) and start_ms <= t < end_ms and isinstance(delta, dict):
            picked.setdefault(day_of(int(t)), []).append(
                {"ts_ms": int(t), "type": delta.get("type"), "usd": ledger_usd(delta),
                 "hash": row.get("hash")})
    for day, lo, hi in split_by_day(start_ms, end_ms):
        record = days.setdefault(day, empty_day(wallet, day, role))
        record["coverage"]["ledger"] = merge_intervals(record["coverage"]["ledger"] + [[lo, hi]])
        kept = list(record["ledger"] or [])
        keys = {(r["ts_ms"], r["type"], r.get("hash")) for r in kept}
        for item in picked.get(day, []):
            key = (item["ts_ms"], item["type"], item["hash"])
            if key not in keys and len(kept) < MAX_LEDGER:
                kept.append(item)
                keys.add(key)
        record["ledger"] = sorted(kept, key=lambda r: r["ts_ms"])


# --- the boundary -----------------------------------------------------------------------

def quiet_boundary(times, cursor_ms: int, known_until_ms: int) -> tuple[int, bool]:
    """Where this run may stop folding: (boundary_ms, runs_split).

    At most BOUNDARY_LAG_MS before everything known, and inside a gap of at least
    QUIET_GAP_MS so no run straddles it. A wallet with no such gap (a bot quoting
    every second) falls back to the last hour mark and says so.
    """
    cap = known_until_ms - BOUNDARY_LAG_MS
    if cap <= cursor_ms:
        return cursor_ms, False
    ts = sorted({int(t) for t in times
                 if isinstance(t, (int, float)) and cursor_ms <= t <= known_until_ms})
    before = [t for t in ts if t < cap]
    after = [t for t in ts if t >= cap]
    if not before or not after or after[0] - before[-1] >= QUIET_GAP_MS:
        return cap, False
    for i in range(len(before) - 1, 0, -1):
        if before[i] - before[i - 1] >= QUIET_GAP_MS:
            return before[i], False
    hour = cap // HOUR_MS * HOUR_MS
    return (hour, True) if hour > cursor_ms else (cursor_ms, False)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_study_records.py tests/test_execution_program.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/execution_program.py src/study/__init__.py src/study/records.py tests/test_study_records.py tests/test_execution_program.py
git commit -m "feat(study): additive daily records behind a quiet boundary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Strict reads

**Files:**
- Create: `src/study/collect.py`
- Test: `tests/test_study_collect.py`

**Interfaces:**
- Consumes: `src.utils.hl_read(body) -> {"ok", "data", "error", ...}` (with a `ReadBudget` active, a refusal returns `error == "time_budget"`; a 429 returns `"rate limited"`).
- Produces:
  - `read_fills(wallet, start_ms, now_ms, fetch=None, max_pages=5) -> {"ok", "stopped", "error", "fills", "known_until_ms", "saturated", "first_ms", "pages"}`
  - `read_recent_fills(wallet, fetch=None) -> {"ok", "stopped", "error", "fills"}`
  - `read_orders(wallet, fetch=None) -> {"ok", "stopped", "error", "orders", "oldest_ms", "full"}`
  - `read_ledger(wallet, start_ms, now_ms, fetch=None, max_pages=3) -> {"ok", "stopped", "error", "rows", "known_until_ms"}`

- [ ] **Step 1: Write the failing tests** — create `tests/test_study_collect.py`:

```python
"""Strict reads: a failed or refused read is never an empty one."""

from src.study import collect

W = "0x" + "b" * 40


def pages(*batches):
    calls = []

    def fetch(body):
        calls.append(body)
        batch = batches[len(calls) - 1]
        return batch if isinstance(batch, dict) and "ok" in batch else {"ok": True, "data": batch}

    fetch.calls = calls
    return fetch


def rows(start, n):
    return [{"time": start + i, "tid": start + i, "oid": start + i} for i in range(n)]


def test_a_short_page_means_everything_up_to_now_was_seen():
    got = collect.read_fills(W, 1_000, 9_000, pages(rows(1_000, 3)))
    assert got["ok"] and got["known_until_ms"] == 9_000 and not got["saturated"]
    assert [f["time"] for f in got["fills"]] == [1_000, 1_001, 1_002]


def test_five_full_pages_mean_the_oldest_span_may_be_missing():
    batches = [rows(1_000 + i * 2_000, 2_000) for i in range(5)]
    got = collect.read_fills(W, 0, 99_999_999, pages(*batches))
    assert got["ok"] and got["saturated"] and got["first_ms"] == 1_000
    assert got["known_until_ms"] == batches[-1][-1]["time"]


def test_pages_overlap_inclusively_and_rows_are_deduped():
    first = rows(1_000, 2_000)
    fetch = pages(first, [first[-1]] + rows(3_000, 10))
    got = collect.read_fills(W, 1_000, 50_000, fetch)
    assert len(got["fills"]) == 2_010
    assert fetch.calls[0]["aggregateByTime"] is True
    assert fetch.calls[1]["startTime"] == first[-1]["time"]


def test_a_failed_read_is_not_an_empty_one():
    got = collect.read_fills(W, 0, 10, pages({"ok": False, "error": "HTTP 500"}))
    assert not got["ok"] and not got["stopped"]
    assert got["fills"] == [] and got["known_until_ms"] is None


def test_a_budget_refusal_is_a_stop_not_a_failure():
    assert collect.read_fills(W, 0, 10, pages({"ok": False, "error": "time_budget"}))["stopped"]
    assert collect.read_orders(W, pages({"ok": False, "error": "rate limited"}))["stopped"]


def test_a_malformed_page_is_a_failure():
    got = collect.read_fills(W, 0, 10, pages([{"no_time": 1}]))
    assert not got["ok"] and "shape" in got["error"]


def test_orders_say_how_far_back_they_vouch():
    entries = [{"order": {"timestamp": 5}, "status": "filled"},
               {"order": {"timestamp": 9}, "status": "open"}]
    got = collect.read_orders(W, pages(entries))
    assert got["ok"] and got["oldest_ms"] == 5 and not got["full"]
    assert not collect.read_orders(W, pages([{"no_order": 1}]))["ok"]


def test_recent_fills_and_ledger_are_strict_too():
    assert collect.read_recent_fills(W, pages([{"time": 1, "tid": 1}]))["fills"] == [
        {"time": 1, "tid": 1}]
    assert not collect.read_recent_fills(W, pages({"ok": False, "error": "x"}))["ok"]
    assert not collect.read_ledger(W, 0, 10, pages({"ok": False, "error": "HTTP 502"}))["ok"]
    got = collect.read_ledger(W, 0, 10, pages([{"time": 3, "hash": "0x1",
                                                "delta": {"type": "deposit"}}]))
    assert got["ok"] and got["known_until_ms"] == 10 and len(got["rows"]) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_study_collect.py -q`
Expected: FAIL — `ImportError: cannot import name 'collect' from 'src.study'`.

- [ ] **Step 3: Write `src/study/collect.py`**

```python
"""Strict, budgeted reads of one wallet for the study (spec §6.1). No folding here.

Every reader distinguishes three outcomes: a read (ok), a refusal by the run's
ReadBudget or a 429 (stopped: the run ends and the wallet goes first next time),
and a failure (the wallet is unreadable this run). A failed read never looks like
an empty one (rule 5): it returns no rows and no `known_until_ms`.
"""

from __future__ import annotations

PAGE = 2_000
MAX_PAGES = 5
LEDGER_MAX_PAGES = 3
BUDGET_ERRORS = ("time_budget", "rate_limited", "rate limited")


def _fetch(fetch):
    if fetch is not None:
        return fetch
    from src.utils import hl_read
    return hl_read


def _call(fetch, body: dict) -> dict:
    result = _fetch(fetch)(body)
    if not isinstance(result, dict) or "ok" not in result:
        return {"ok": False, "data": None, "error": "malformed read result", "stopped": False}
    if result["ok"]:
        return {"ok": True, "data": result.get("data"), "error": None, "stopped": False}
    error = str(result.get("error") or "read failed")
    return {"ok": False, "data": None, "error": error, "stopped": error in BUDGET_ERRORS}


def _timed_rows(page) -> bool:
    return isinstance(page, list) and all(
        isinstance(r, dict) and isinstance(r.get("time"), (int, float)) for r in page)


def _fill_key(row: dict):
    return row["tid"] if row.get("tid") is not None else (
        row.get("oid"), row["time"], row.get("px"), row.get("sz"))


def _failed(got: dict, **empty) -> dict:
    return {"ok": False, "stopped": got.get("stopped", False), "error": got.get("error"), **empty}


def read_fills(wallet: str, start_ms: int, now_ms: int, fetch=None,
               max_pages: int = MAX_PAGES) -> dict:
    """Fills since start_ms, oldest first, aggregated by time.

    `known_until_ms`: every fill up to it has been seen. `saturated`: Hyperliquid
    keeps only an account's newest 10,000 fills, so when five full pages come back
    the span between start_ms and the first fill read may be missing.
    """
    empty = {"fills": [], "known_until_ms": None, "saturated": False, "first_ms": None}
    rows, cursor, pages, full_pages, complete = {}, int(start_ms), 0, 0, False
    while pages < max_pages:
        pages += 1
        got = _call(fetch, {"type": "userFillsByTime", "user": wallet, "startTime": cursor,
                            "endTime": int(now_ms), "aggregateByTime": True})
        if not got["ok"]:
            return _failed(got, pages=pages, **empty)
        page = got["data"]
        if not _timed_rows(page):
            return _failed({"error": "unexpected fills shape"}, pages=pages, **empty)
        for row in page:
            rows[_fill_key(row)] = row
        if len(page) < PAGE:
            complete = True
            break
        full_pages += 1
        latest = int(max(r["time"] for r in page))
        if latest <= cursor:
            break  # 2,000 fills in one millisecond: cannot page past it
        cursor = latest
    fills = sorted(rows.values(), key=lambda r: (r["time"], str(r.get("tid"))))
    if complete:
        known_until = int(now_ms)
    else:
        known_until = int(fills[-1]["time"]) if fills else int(start_ms)
    return {"ok": True, "stopped": False, "error": None, "fills": fills,
            "known_until_ms": known_until, "saturated": full_pages >= max_pages,
            "first_ms": int(fills[0]["time"]) if fills else None, "pages": pages}


def read_recent_fills(wallet: str, fetch=None) -> dict:
    """The newest 2,000 fills (`userFills`), for one-off panel snapshots."""
    got = _call(fetch, {"type": "userFills", "user": wallet})
    if not got["ok"]:
        return _failed(got, fills=[])
    if not _timed_rows(got["data"]):
        return _failed({"error": "unexpected fills shape"}, fills=[])
    return {"ok": True, "stopped": False, "error": None, "fills": got["data"]}


def read_orders(wallet: str, fetch=None) -> dict:
    """The newest 2,000 orders. With a full page, orders placed before `oldest_ms`
    may be missing, so the caller vouches only from there."""
    got = _call(fetch, {"type": "historicalOrders", "user": wallet})
    empty = {"orders": [], "oldest_ms": None, "full": False}
    if not got["ok"]:
        return _failed(got, **empty)
    data = got["data"]
    if not isinstance(data, list) or not all(
            isinstance(r, dict) and isinstance(r.get("order"), dict) for r in data):
        return _failed({"error": "unexpected orders shape"}, **empty)
    stamps = [r["order"]["timestamp"] for r in data
              if isinstance(r["order"].get("timestamp"), (int, float))]
    return {"ok": True, "stopped": False, "error": None, "orders": data,
            "oldest_ms": int(min(stamps)) if stamps else None, "full": len(data) >= PAGE}


def read_ledger(wallet: str, start_ms: int, now_ms: int, fetch=None,
                max_pages: int = LEDGER_MAX_PAGES) -> dict:
    """Non-funding ledger updates (deposits, withdrawals, sends) since start_ms."""
    rows, cursor, complete = {}, int(start_ms), False
    for _ in range(max_pages):
        got = _call(fetch, {"type": "userNonFundingLedgerUpdates", "user": wallet,
                            "startTime": cursor, "endTime": int(now_ms)})
        if not got["ok"]:
            return _failed(got, rows=[], known_until_ms=None)
        page = got["data"]
        if not _timed_rows(page):
            return _failed({"error": "unexpected ledger shape"}, rows=[], known_until_ms=None)
        for row in page:
            rows[(row.get("hash"), row["time"], str((row.get("delta") or {}).get("type")))] = row
        if len(page) < PAGE:
            complete = True
            break
        latest = int(max(r["time"] for r in page))
        if latest <= cursor:
            break
        cursor = latest
    ordered = sorted(rows.values(), key=lambda r: r["time"])
    if complete:
        known_until = int(now_ms)
    else:
        known_until = int(ordered[-1]["time"]) if ordered else int(start_ms)
    return {"ok": True, "stopped": False, "error": None, "rows": ordered,
            "known_until_ms": known_until}
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_study_collect.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/study/collect.py tests/test_study_collect.py
git commit -m "feat(study): strict paged reads that never mistake failure for empty

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The study set

**Files:**
- Create: `src/study/selection.py`
- Test: `tests/test_study_selection.py`

**Interfaces:**
- Consumes: `src.roster.evidence_strength(row) -> float`.
- Produces:
  - `DAY_MS`, `MAX_WALLETS = 40`, `STICKY_MS`, `DECAYED_KEEP_MS`, `MIN_HL_VALUE = 10_000.0`, `SOURCES = ("pinned", "roster_lead", "decayed_lead", "detector")`
  - `address(value) -> str` ("" if not a 42-char 0x address)
  - `hl_trader(row) -> bool`
  - `blocked_wallets(config, roster) -> set[str]`
  - `by_source(config, roster, detectors: list, decayed_seen: dict, now_ms) -> tuple[dict[str, list[str]], dict[str, int]]`
  - `choose(sources, previous: dict|None, now_ms, *, blocked=(), max_wallets=40) -> list[{"wallet", "source", "since_ms"}]`

- [ ] **Step 1: Write the failing tests** — create `tests/test_study_selection.py`:

```python
"""Which wallets the study reads (spec §5)."""

from src.study import selection as sel

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B, C, D = ("0x" + c * 40 for c in "abcd")
DAY = sel.DAY_MS
NOW = 1_790_000_000_000


def row(wallet, tier="WATCH", value=1e6, role="user", dropped=None, service=False):
    return {"wallet": wallet, "tier": tier, "tier_dropped_from": dropped, "is_service": service,
            "evidence": {"hl_role": role, "hl_account_value": value}}


def test_sources_in_priority_order_and_the_target_never_studied():
    config = {"target_wallet": T, "watch_wallets": [A], "study_wallets": [T]}
    roster = {"wallets": [row(B, "POSSIBLE"), row(C, "WATCH", dropped="PROBABLE"),
                          row(T, "CONFIRMED")]}
    sources, _ = sel.by_source(config, roster, [D, T], {}, NOW)
    assert sources == {"pinned": [A], "roster_lead": [B], "decayed_lead": [C], "detector": [D]}


def test_wallets_hyperliquid_does_not_know_as_traders_are_not_studied():
    roster = {"wallets": [row(A, "POSSIBLE", role="missing"), row(B, "POSSIBLE", value=54.0),
                          row(C, "POSSIBLE", value=None)]}
    sources, _ = sel.by_source({"target_wallet": T}, roster, [], {}, NOW)
    assert sources["roster_lead"] == []


def test_services_are_never_studied_even_when_a_detector_names_them():
    roster = {"wallets": [row(A, "INFRASTRUCTURE"), row(B, "POSSIBLE", service=True)]}
    sources, _ = sel.by_source({"target_wallet": T}, roster, [A, B], {}, NOW)
    assert all(not wallets for wallets in sources.values())


def test_a_decayed_lead_is_kept_for_sixty_days_then_released():
    roster = {"wallets": [row(A, "WATCH", dropped="POSSIBLE")]}
    sources, seen = sel.by_source({"target_wallet": T}, roster, [], {}, NOW)
    assert sources["decayed_lead"] == [A] and seen == {A: NOW}
    sources, seen = sel.by_source({"target_wallet": T}, roster, [], seen, NOW + 61 * DAY)
    assert sources["decayed_lead"] == [] and seen == {A: NOW}


def test_sticky_members_stay_for_fourteen_days_unless_a_higher_source_needs_the_slot():
    previous = {A: {"source": "detector", "since_ms": NOW - 3 * DAY}}
    sources = {"pinned": [], "roster_lead": [B], "decayed_lead": [], "detector": [C]}
    members = sel.choose(sources, previous, NOW, max_wallets=2)
    assert [m["wallet"] for m in members] == [B, A]
    assert members[1]["since_ms"] == NOW - 3 * DAY and members[0]["since_ms"] == NOW
    crowded = {**sources, "roster_lead": [B, D]}
    assert [m["wallet"] for m in sel.choose(crowded, previous, NOW, max_wallets=2)] == [B, D]


def test_a_member_older_than_fourteen_days_competes_like_anyone_else():
    previous = {A: {"source": "detector", "since_ms": NOW - 15 * DAY}}
    sources = {"pinned": [], "roster_lead": [], "decayed_lead": [], "detector": [C]}
    assert [m["wallet"] for m in sel.choose(sources, previous, NOW, max_wallets=1)] == [C]


def test_a_sticky_member_that_became_blocked_is_dropped():
    previous = {T: {"source": "pinned", "since_ms": NOW - DAY}}
    sources = {"pinned": [], "roster_lead": [], "decayed_lead": [], "detector": []}
    assert sel.choose(sources, previous, NOW, blocked={T}) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_study_selection.py -q`
Expected: FAIL — `ImportError: cannot import name 'selection'`.

- [ ] **Step 3: Write `src/study/selection.py`**

```python
"""Which wallets the study reads (spec 2026-10-06 §5). Pure.

Four sources in priority order: wallets the operator pinned, roster leads that
Hyperliquid knows as traders, leads that decayed out of their tier (kept 60 days
from when the study first saw them decayed), and other detectors' finds. A
member added under 14 days ago keeps its place ahead of newcomers from its own
or a lower source, so its history can build. The target is never studied: he
is the reference every test compares against.
"""

from __future__ import annotations

from src.roster import evidence_strength

DAY_MS = 86_400_000
MAX_WALLETS = 40
STICKY_MS = 14 * DAY_MS
DECAYED_KEEP_MS = 60 * DAY_MS
MIN_HL_VALUE = 10_000.0
SOURCES = ("pinned", "roster_lead", "decayed_lead", "detector")
LEAD_TIERS = ("CONFIRMED", "PROBABLE", "POSSIBLE")


def address(value) -> str:
    text = str(value or "").strip().lower()
    return text if len(text) == 42 and text.startswith("0x") else ""


def hl_trader(row: dict) -> bool:
    """Hyperliquid knows it as a trading account of real size."""
    evidence = row.get("evidence") or {}
    value = evidence.get("hl_account_value")
    return (evidence.get("hl_role") in ("user", "subAccount")
            and isinstance(value, (int, float)) and value >= MIN_HL_VALUE)


def blocked_wallets(config: dict, roster: dict | None) -> set[str]:
    """Never studied: the target, and anything the roster measured as a service."""
    rows = [r for r in (roster or {}).get("wallets") or [] if isinstance(r, dict)]
    blocked = {address(config.get("target_wallet"))}
    blocked |= {address(r.get("wallet")) for r in rows
                if r.get("tier") == "INFRASTRUCTURE" or r.get("is_service")}
    return blocked - {""}


def by_source(config: dict, roster: dict | None, detectors: list, decayed_seen: dict,
              now_ms: int) -> tuple[dict, dict]:
    """Candidate wallets per source, each in priority order, and the clock that
    releases a decayed lead 60 days after the study first saw it decayed."""
    rows = [r for r in (roster or {}).get("wallets") or [] if isinstance(r, dict)]
    blocked = blocked_wallets(config, roster)

    def rank(row):
        value = (row.get("evidence") or {}).get("hl_account_value")
        return (-evidence_strength(row), -(value if isinstance(value, (int, float)) else 0.0),
                address(row.get("wallet")))

    ordered = sorted(rows, key=rank)
    pinned = [address(w) for w in [*(config.get("watch_wallets") or []),
                                    *(config.get("study_wallets") or [])]]
    leads = [address(r.get("wallet")) for r in ordered
             if r.get("tier") in LEAD_TIERS and hl_trader(r)]
    decayed_now = [address(r.get("wallet")) for r in ordered
                   if r.get("tier_dropped_from") in LEAD_TIERS and hl_trader(r)]
    seen = {w: (decayed_seen or {}).get(w, now_ms) for w in decayed_now if w}
    decayed = [w for w in decayed_now if w and now_ms - seen[w] <= DECAYED_KEEP_MS]
    sources = {"pinned": pinned, "roster_lead": leads, "decayed_lead": decayed,
               "detector": [address(w) for w in detectors or []]}
    return ({name: list(dict.fromkeys(w for w in wallets if w and w not in blocked))
             for name, wallets in sources.items()}, seen)


def choose(sources: dict, previous: dict | None, now_ms: int, *, blocked=(),
           max_wallets: int = MAX_WALLETS) -> list[dict]:
    """The study set, at most `max_wallets` long."""
    previous = previous or {}
    entries, position = [], 0
    for rank, source in enumerate(SOURCES):
        for wallet in sources.get(source, []):
            entries.append((rank, 1, position, wallet, source))
            position += 1
    for wallet, row in previous.items():
        source, since = (row or {}).get("source"), (row or {}).get("since_ms")
        if source in SOURCES and isinstance(since, int) and now_ms - since < STICKY_MS:
            entries.append((SOURCES.index(source), 0, position, wallet, source))
            position += 1
    blocked = set(blocked)
    members, taken = [], set()
    for _rank, _sticky, _position, wallet, source in sorted(entries):
        if wallet in taken or wallet in blocked or len(members) >= max_wallets:
            continue
        taken.add(wallet)
        since = (previous.get(wallet) or {}).get("since_ms")
        members.append({"wallet": wallet, "source": source,
                        "since_ms": since if isinstance(since, int) else now_ms})
    return members
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_study_selection.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/study/selection.py tests/test_study_selection.py
git commit -m "feat(study): choose the study set, keeping decayed leads and new members

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: The study's files

**Files:**
- Create: `src/study/archive.py`
- Modify: `tests/conftest.py` (add a probe so no test can write the real `data/study/`)
- Test: `tests/test_study_archive.py`

**Interfaces:**
- Consumes: `src.utils.DATA_DIR` (read at call time), `src.utils.atomic_write_json` is NOT used for the archive (indentation would bloat it).
- Produces:
  - `root(data_dir=None) -> Path` (`<data>/study`), `wallet_dir(wallet, data_dir=None) -> Path`
  - `read_json(path, default)`, `write_compact(path, doc) -> None`
  - `load_days(wallet, first_day, last_day, data_dir=None) -> dict[str, dict]`
  - `save_days(days: dict[str, dict], data_dir=None) -> int` (writes only changed days)
  - `roll_sealed_months(wallet, today, data_dir=None) -> int`
  - `load_state(data_dir=None) -> dict`, `save_state(state, data_dir=None) -> None`
  - `write_if_changed(path, doc) -> bool`

- [ ] **Step 1: Write the failing tests** — create `tests/test_study_archive.py`:

```python
"""The study's files: daily records, sealed months, state, and nothing rewritten needlessly."""

from src import utils
from src.study import archive
from src.study import records as rec

W = "0x" + "c" * 40


def day(d, fills=1):
    record = rec.empty_day(W, d, "studied")
    record["fills"] = fills
    return record


def test_days_round_trip_and_unchanged_days_are_not_rewritten(tmp_path):
    assert archive.save_days({"2026-10-01": day("2026-10-01")}, tmp_path) == 1
    assert archive.save_days({"2026-10-01": day("2026-10-01")}, tmp_path) == 0
    assert archive.load_days(W, "2026-10-01", "2026-10-01", tmp_path) == {
        "2026-10-01": day("2026-10-01")}


def test_sealed_months_roll_into_one_verified_archive(tmp_path):
    archive.save_days({"2026-09-29": day("2026-09-29"), "2026-09-30": day("2026-09-30", 2),
                       "2026-10-01": day("2026-10-01")}, tmp_path)
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 1
    folder = archive.wallet_dir(W, tmp_path)
    assert sorted(p.name for p in folder.iterdir()) == ["2026-09.jsonl.gz", "2026-10-01.json"]
    got = archive.load_days(W, "2026-09-01", "2026-10-31", tmp_path)
    assert sorted(got) == ["2026-09-29", "2026-09-30", "2026-10-01"]
    assert got["2026-09-30"]["fills"] == 2


def test_a_late_daily_file_for_a_sealed_month_is_merged_on_the_next_roll(tmp_path):
    archive.save_days({"2026-09-29": day("2026-09-29")}, tmp_path)
    archive.roll_sealed_months(W, "2026-10-06", tmp_path)
    archive.save_days({"2026-09-30": day("2026-09-30")}, tmp_path)
    archive.roll_sealed_months(W, "2026-10-06", tmp_path)
    assert sorted(archive.load_days(W, "2026-09-01", "2026-09-30", tmp_path)) == [
        "2026-09-29", "2026-09-30"]


def test_an_unreadable_month_archive_is_never_overwritten(tmp_path):
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-09.jsonl.gz").write_bytes(b"not gzip")
    archive.save_days({"2026-09-30": day("2026-09-30")}, tmp_path)
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 0
    assert (folder / "2026-09.jsonl.gz").read_bytes() == b"not gzip"
    assert (folder / "2026-09-30.json").exists()


def test_the_default_root_follows_the_sandboxed_data_dir():
    assert archive.root() == utils.DATA_DIR / "study"


def test_state_round_trips_and_a_missing_state_is_empty(tmp_path):
    archive.save_state({"wallets": {W: {"fills_cursor_ms": 5}}}, tmp_path)
    assert archive.load_state(tmp_path) == {"wallets": {W: {"fills_cursor_ms": 5}}}
    assert archive.load_state(tmp_path / "nowhere") == {}


def test_write_if_changed(tmp_path):
    path = tmp_path / "study" / "x.json"
    assert archive.write_if_changed(path, {"a": 1}) is True
    assert archive.write_if_changed(path, {"a": 1}) is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_study_archive.py -q`
Expected: FAIL — `ImportError: cannot import name 'archive'`.

- [ ] **Step 3: Write `src/study/archive.py`**

```python
"""The study's files under data/study/ (spec §6.3, §10). Only scripts/run_study.py
calls the writers, so data/study/ has exactly one writer.

Daily records live in archive/<wallet>/<YYYY-MM-DD>.json until their month ends;
then they are packed into <YYYY-MM>.jsonl.gz beside them, read back and compared
before any daily file is deleted. The archive cannot be re-fetched — Hyperliquid
keeps about two weeks of an account like his — so nothing here deletes what it
has not verified, and an archive that will not read is never overwritten.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path

from src import utils

DAILY = ".json"
MONTHLY = ".jsonl.gz"


def root(data_dir=None) -> Path:
    return Path(data_dir or utils.DATA_DIR) / "study"


def wallet_dir(wallet: str, data_dir=None) -> Path:
    return root(data_dir) / "archive" / wallet.lower()


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_compact(path: Path, doc) -> None:
    """Atomic like utils.atomic_write_json, without indentation: the archive is
    irreplaceable and grows every day, so it is kept small."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_month(path: Path) -> dict[str, dict] | None:
    """The records in a month archive, or None when it cannot be read."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError, EOFError):
        return None
    return {row["day"]: row for row in rows if isinstance(row, dict) and row.get("day")}


def load_days(wallet: str, first_day: str, last_day: str, data_dir=None) -> dict[str, dict]:
    folder = wallet_dir(wallet, data_dir)
    days: dict[str, dict] = {}
    if not folder.exists():
        return days
    for path in sorted(folder.glob("*" + MONTHLY)):
        month = path.name[: -len(MONTHLY)]
        if not first_day[:7] <= month <= last_day[:7]:
            continue
        found = _read_month(path)
        if found is None:
            print(f"[study] unreadable month archive, its days count as unread: {path}")
            continue
        days.update({d: r for d, r in found.items() if first_day <= d <= last_day})
    for path in sorted(folder.glob("*" + DAILY)):
        day = path.name[: -len(DAILY)]
        if not first_day <= day <= last_day:
            continue
        record = read_json(path, None)
        if isinstance(record, dict) and record.get("day") == day:
            days[day] = record
    return days


def save_days(days: dict[str, dict], data_dir=None) -> int:
    """Write each changed day; an unchanged one is not rewritten."""
    written = 0
    for day, record in sorted(days.items()):
        path = wallet_dir(record["wallet"], data_dir) / f"{day}{DAILY}"
        if read_json(path, None) != record:
            write_compact(path, record)
            written += 1
    return written


def roll_sealed_months(wallet: str, today: str, data_dir=None) -> int:
    """Pack each earlier month's daily files into <YYYY-MM>.jsonl.gz."""
    folder = wallet_dir(wallet, data_dir)
    if not folder.exists():
        return 0
    by_month: dict[str, list[Path]] = {}
    for path in folder.glob("*" + DAILY):
        if path.name[:7] < today[:7]:
            by_month.setdefault(path.name[:7], []).append(path)
    rolled = 0
    for month, paths in sorted(by_month.items()):
        target = folder / f"{month}{MONTHLY}"
        records = _read_month(target) if target.exists() else {}
        if records is None:
            print(f"[study] month archive unreadable, left alone with its daily files: {target}")
            continue
        for path in paths:
            record = read_json(path, None)
            if isinstance(record, dict) and record.get("day"):
                records[record["day"]] = record
        payload = "".join(json.dumps(records[d], sort_keys=True, separators=(",", ":")) + "\n"
                          for d in sorted(records)).encode("utf-8")
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
            gz.write(payload)
        tmp = folder / f".{target.name}.{os.getpid()}.tmp"
        tmp.write_bytes(buffer.getvalue())
        if _read_month(tmp) != records:
            tmp.unlink(missing_ok=True)
            print(f"[study] month archive failed verification, daily files kept: {target}")
            continue
        os.replace(tmp, target)
        for path in paths:
            path.unlink()
        rolled += 1
    return rolled


def load_state(data_dir=None) -> dict:
    state = read_json(root(data_dir) / "state.json", {})
    return state if isinstance(state, dict) else {}


def save_state(state: dict, data_dir=None) -> None:
    write_compact(root(data_dir) / "state.json", state)


def write_if_changed(path: Path, doc) -> bool:
    if read_json(path, None) == doc:
        return False
    write_compact(path, doc)
    return True
```

- [ ] **Step 4: Add the conftest probe** — in `tests/conftest.py`, add one entry to the `_PROBES` dict, directly after `'the discovery database': …`:

```python
    # src/study/archive.py: the candidate study's irreplaceable daily records,
    # state and dossiers. Its modules read utils.DATA_DIR at call time, so the
    # sandbox covers them; this probe proves it stays that way.
    "the candidate study tree": REAL_DATA_DIR / "study",
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_study_archive.py tests/test_study_records.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/study/archive.py tests/test_study_archive.py tests/conftest.py
git commit -m "feat(study): daily files, verified month archives and state

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The tooling tests (T1, T2, T3)

**Files:**
- Create: `src/study/tooling.py`
- Test: `tests/test_study_tooling.py`

**Interfaces:**
- Consumes: `records.covered_day`, `records.add_habits`, `records.habit_counts`, `records.first_prices`, `records.cadence_histogram`, `records.CADENCE_BINS`, `records.CADENCE_BIN_MS`, `records.OTHER`; `execution_program.reconstruct_orders`, `.signature`, `.compare`, `.MIN_CLIP_ORDERS`, `.MIN_CLIP_SHARE`.
- Produces:
  - constants `STYLE_PROGRAM = "PROGRAM_IOC5"`, `STYLE_MAKER`, `STYLE_MANUAL`, `STYLE_MIXED`, `MIN_ORDERS = 100`, `MIN_GAPS = 200`, `AGAINST_TRAITS = ("client_ids", "triggers", "maker")`, `NEVER_SHARE = 0.001`, `DOMINANT_SHARE = 0.5`, `SHARE_KEYS`
  - `summarise(days: list[dict]) -> dict` with keys `days, covered_days, orders, taker_orders, program_runs, habits, cadence, coins`
  - `profile(habits, taker_orders=0, orders=0) -> dict | None`; `summary_profile(summary) -> dict | None`
  - `style(profile) -> {"style": str, "flags": {"client_ids": bool, "triggers": bool, "maker": bool}}`
  - `wasserstein(a, b) -> float | None` (seconds)
  - `clip_signature(summary, prof=None) -> dict` (execution_program's signature shape)
  - `t1_style(candidate, his_recent, his_all) -> test dict`, `t2_rhythm(candidate_hist, his_hist) -> test dict`, `t3_clips(candidate_sig, his_sig) -> test dict`; every test dict is `{"test", "status": "measured"|"insufficient", "statistic", "n", "detail"}`
  - `measure_snapshot(fills, entries) -> dict` and `snapshot_signature(snapshot) -> dict` (for the census and family panels)

- [ ] **Step 1: Write the failing tests** — create `tests/test_study_tooling.py`:

```python
"""Tooling tests: how a wallet's orders are made, compared with his."""

from src.study import records, tooling

W = "0x" + "d" * 40


def habits(n, ioc=0, gtc=0, alo=0, fm=0, cloid=0, trigger=0, seen=0, hit=0):
    h = records.empty_habits()
    h["orders_seen"] = n
    h["tif"].update(Ioc=ioc, Gtc=gtc, Alo=alo, FrontendMarket=fm)
    h["tif"]["other"] = n - ioc - gtc - alo - fm
    h.update(cloid=cloid, trigger=trigger, ioc_offset_seen=seen, ioc_offset_5pct=hit)
    return h


HIS = tooling.profile(habits(1000, ioc=988, fm=12, seen=988, hit=987), 1000, 1000)
MAKER_BOT = tooling.profile(habits(2000, alo=2000, cloid=2000), 0, 2000)
WEB_TRADER = tooling.profile(habits(500, fm=400, gtc=100), 400, 500)


def test_styles():
    assert tooling.style(HIS) == {"style": "PROGRAM_IOC5", "flags": {
        "client_ids": False, "triggers": False, "maker": False}}
    assert tooling.style(MAKER_BOT)["style"] == "MAKER"
    assert tooling.style(MAKER_BOT)["flags"]["client_ids"] is True
    assert tooling.style(WEB_TRADER)["style"] == "MANUAL_UI"


def test_t1_a_maker_bot_with_client_ids_carries_traits_he_never_shows():
    t1 = tooling.t1_style(MAKER_BOT, HIS, HIS)
    assert t1["status"] == "measured" and t1["statistic"] is False
    assert t1["detail"]["against_traits"] == ["client_ids", "maker"]


def test_t1_a_web_ui_trader_differs_but_shows_no_trait_he_never_shows():
    t1 = tooling.t1_style(WEB_TRADER, HIS, HIS)
    assert t1["statistic"] is False and t1["detail"]["against_traits"] == []


def test_t1_he_matches_himself():
    assert tooling.t1_style(HIS, HIS, HIS)["statistic"] is True


def test_t1_needs_a_hundred_orders():
    few = tooling.profile(habits(99, ioc=99, seen=99, hit=99), 99, 99)
    assert tooling.t1_style(few, HIS, HIS)["status"] == "insufficient"
    assert tooling.t1_style(None, HIS, HIS)["status"] == "insufficient"


def test_wasserstein_in_seconds():
    a = [0] * records.CADENCE_BINS
    b = [0] * records.CADENCE_BINS
    a[16], b[17] = 100, 100
    assert tooling.wasserstein(a, a) == 0.0
    assert tooling.wasserstein(a, b) == 0.1
    assert tooling.wasserstein(a, [0] * records.CADENCE_BINS) is None


def test_t2_needs_two_hundred_gaps():
    hist = [0] * records.CADENCE_BINS
    hist[17] = 199
    assert tooling.t2_rhythm(hist, hist)["status"] == "insufficient"
    hist[17] = 200
    t2 = tooling.t2_rhythm(hist, hist)
    assert (t2["status"], t2["statistic"], t2["n"]) == ("measured", 0.0, 200)


def program_fills(start, n, coin, sz, px="100.0"):
    return [{"coin": coin, "side": "A", "sz": sz, "px": px, "time": start + i * 1_700,
             "crossed": True, "oid": start + i * 1_700, "tid": start + i * 1_700}
            for i in range(n)]


def summary_of(fills):
    days = {}
    times = [f["time"] for f in fills]
    records.fold_fills(days, fills, wallet=W, role="studied", start_ms=min(times),
                       end_ms=max(times) + 1, last_fill_ms=None)
    return tooling.summarise(list(days.values()))


def test_t3_a_wallet_running_his_clip_table_matches_it():
    t0 = 1_790_899_200_000
    his = summary_of(program_fills(t0, 30, "ZEC", "1.0") + program_fills(t0 + 600_000, 30, "BTC", "0.1")
                     + program_fills(t0 + 1_200_000, 30, "NEAR", "250.0"))
    same = summary_of(program_fills(t0, 20, "ZEC", "1.0") + program_fills(t0 + 600_000, 20, "BTC", "0.1")
                      + program_fills(t0 + 1_200_000, 20, "NEAR", "250.0"))
    t3 = tooling.t3_clips(tooling.clip_signature(same), tooling.clip_signature(his))
    assert t3["status"] == "measured" and t3["statistic"] >= 1.0
    other = summary_of(program_fills(t0, 20, "SOL", "3.0"))
    assert tooling.t3_clips(tooling.clip_signature(other),
                            tooling.clip_signature(his))["status"] == "insufficient"


def test_a_snapshot_measures_style_rhythm_and_clips_from_raw_reads():
    t0 = 1_790_899_200_000
    fills = program_fills(t0, 120, "BTC", "0.1")
    entries = [{"order": {"coin": "BTC", "side": "A", "limitPx": "95.0", "oid": f["oid"],
                          "timestamp": f["time"], "tif": "Ioc", "cloid": None,
                          "isTrigger": False, "orderType": "Limit", "reduceOnly": False},
                "status": "filled"} for f in fills]
    snap = tooling.measure_snapshot(fills, entries)
    assert snap["orders_seen"] == 120 and snap["style"]["style"] == "PROGRAM_IOC5"
    assert sum(snap["cadence"]) == 119 and snap["clip_table"] == {"BTC": 0.1}
    sig = tooling.snapshot_signature(snap)
    assert sig["clip_table"]["BTC"]["size"] == 0.1 and sig["program_runs"] == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_study_tooling.py -q`
Expected: FAIL — `ImportError: cannot import name 'tooling'`.

- [ ] **Step 3: Write `src/study/tooling.py`**

```python
"""The study's tooling tests (spec §7): how a wallet's orders are made. Pure.

T1 is his execution style and the habits around it; T2 the rhythm of his
slicer; T3 his per-coin clip table (`execution_program`). Tooling travels with
an operator to every account and is not what a copier reproduces, so it can
vote — once calibrated (calibration.py). Only T1 can count against a wallet:
his rhythm changed once (Feb–Mar) and his clip table drifts, so a mismatch on
T2 or T3 is not evidence he is elsewhere.
"""

from __future__ import annotations

from src import execution_program as ep
from src.study import records

STYLE_PROGRAM = "PROGRAM_IOC5"
STYLE_MAKER = "MAKER"
STYLE_MANUAL = "MANUAL_UI"
STYLE_MIXED = "MIXED"
MIN_ORDERS = 100
MIN_GAPS = 200
FLAG_SHARES = {"client_ids": 0.05, "triggers": 0.01, "maker": 0.10}
AGAINST_TRAITS = ("client_ids", "triggers", "maker")
NEVER_SHARE = 0.001
DOMINANT_SHARE = 0.5
SHARE_KEYS = ("ioc", "gtc", "alo", "frontend", "client_ids", "triggers", "maker", "canceled")


def _empty_coin() -> dict:
    return {"orders": 0, "buy_usd": 0.0, "sell_usd": 0.0, "taker_clips": {},
            "px_sum": 0.0, "px_n": 0}


def summarise(days: list[dict]) -> dict:
    """One window of daily records summed: habits, rhythm, coins and runs."""
    out = {"days": len(days), "covered_days": 0, "orders": 0, "taker_orders": 0,
           "program_runs": 0, "habits": None, "cadence": [0] * records.CADENCE_BINS,
           "coins": {}}
    for record in days:
        out["covered_days"] += records.covered_day(record)
        out["orders"] += record.get("orders", 0)
        out["taker_orders"] += record.get("taker_orders", 0)
        out["program_runs"] += record.get("program_runs", 0)
        if record.get("habits"):
            out["habits"] = records.add_habits(out["habits"], record["habits"])
        out["cadence"] = [a + b for a, b in zip(out["cadence"], record["cadence"], strict=True)]
        for coin, stats in (record.get("coins") or {}).items():
            if coin == records.OTHER:
                continue
            agg = out["coins"].setdefault(coin, _empty_coin())
            for key in ("orders", "buy_usd", "sell_usd", "px_sum", "px_n"):
                agg[key] += stats.get(key, 0)
            for size, n in (stats.get("taker_clips") or {}).items():
                agg["taker_clips"][size] = agg["taker_clips"].get(size, 0) + n
    return out


def profile(habits: dict | None, taker_orders: int = 0, orders: int = 0) -> dict | None:
    """Shares of each habit; None when no order was read (rule 6)."""
    if not habits or not habits.get("orders_seen"):
        return None
    n = habits["orders_seen"]
    tif = habits["tif"]
    seen = habits.get("ioc_offset_seen") or 0
    return {"orders_seen": n, "ioc": tif.get("Ioc", 0) / n, "gtc": tif.get("Gtc", 0) / n,
            "alo": tif.get("Alo", 0) / n, "frontend": tif.get("FrontendMarket", 0) / n,
            "client_ids": habits.get("cloid", 0) / n, "triggers": habits.get("trigger", 0) / n,
            "reduce_only": habits.get("reduce_only", 0) / n,
            "canceled": habits.get("canceled", 0) / n,
            "maker": (tif.get("Alo", 0) + tif.get("Gtc", 0)) / n,
            "ioc5": habits.get("ioc_offset_5pct", 0) / seen if seen else None,
            "taker": taker_orders / orders if orders else None}


def summary_profile(summary: dict) -> dict | None:
    return profile(summary.get("habits"), summary.get("taker_orders", 0), summary.get("orders", 0))


def style(prof: dict) -> dict:
    ioc5 = prof["ioc"] * prof["ioc5"] if prof.get("ioc5") is not None else None
    if ioc5 is not None and ioc5 >= 0.5:
        kind = STYLE_PROGRAM
    elif prof["maker"] >= 0.5:
        kind = STYLE_MAKER
    elif prof["frontend"] >= 0.5:
        kind = STYLE_MANUAL
    else:
        kind = STYLE_MIXED
    return {"style": kind, "flags": {k: prof[k] >= v for k, v in FLAG_SHARES.items()}}


def _test(name: str, status: str, statistic=None, n: int = 0, detail: dict | None = None) -> dict:
    return {"test": name, "status": status, "statistic": statistic, "n": n, "detail": detail or {}}


def t1_style(candidate: dict | None, his_recent: dict | None, his_all: dict | None) -> dict:
    """Same style and flags as his recent ones? And which traits he never shows
    (under 0.1% of his recorded orders) dominate the candidate?"""
    n = (candidate or {}).get("orders_seen", 0)
    if (not candidate or n < MIN_ORDERS or not his_recent
            or his_recent["orders_seen"] < MIN_ORDERS):
        return _test("T1", "insufficient", n=n)
    mine, his = style(candidate), style(his_recent)
    against = [t for t in AGAINST_TRAITS
               if his_all and his_all[t] < NEVER_SHARE and candidate[t] > DOMINANT_SHARE]
    return _test("T1", "measured", mine == his, n, {
        "candidate": mine, "his": his, "against_traits": against,
        "shares": {k: round(candidate[k], 4) for k in SHARE_KEYS},
        "his_shares": {k: round(his_recent[k], 4) for k in SHARE_KEYS}})


def wasserstein(a: list[int], b: list[int]) -> float | None:
    """Earth-mover distance between two gap histograms, in seconds."""
    sa, sb = sum(a or []), sum(b or [])
    if not sa or not sb:
        return None
    ca = cb = total = 0.0
    for x, y in zip(a, b, strict=True):
        ca += x / sa
        cb += y / sb
        total += abs(ca - cb)
    return round(total * records.CADENCE_BIN_MS / 1000, 4)


def t2_rhythm(candidate_hist: list[int], his_hist: list[int]) -> dict:
    n = sum(candidate_hist or [])
    if n < MIN_GAPS or sum(his_hist or []) < MIN_GAPS:
        return _test("T2", "insufficient", n=n)
    return _test("T2", "measured", wasserstein(candidate_hist, his_hist), n)


def clip_signature(summary: dict, prof: dict | None = None) -> dict:
    """execution_program's signature shape, rebuilt from summed daily records with
    its own clip rules (MIN_CLIP_ORDERS, MIN_CLIP_SHARE)."""
    table, notionals = {}, {}
    for coin, stats in (summary.get("coins") or {}).items():
        if coin == records.OTHER:
            continue
        sizes = stats.get("taker_clips") or {}
        total = sum(sizes.values())
        if not total:
            continue
        key, count = max(sizes.items(), key=lambda kv: (kv[1], kv[0]))
        share = count / total
        if total >= ep.MIN_CLIP_ORDERS and share >= ep.MIN_CLIP_SHARE:
            size = float(key)
            table[coin] = {"size": size, "share": round(share, 4), "count": count}
            if stats.get("px_n"):
                notionals[coin] = size * stats["px_sum"] / stats["px_n"]
    return {"clip_table": table, "clip_notionals": notionals,
            "program_runs": summary.get("program_runs", 0),
            "ioc_5pct_share": (prof or {}).get("ioc5")}


def t3_clips(candidate_sig: dict, his_sig: dict) -> dict:
    match = ep.compare(his_sig or {}, candidate_sig or {})
    if match["status"] != "measured":
        return _test("T3", "insufficient", detail=match)
    return _test("T3", "measured", match["strength"],
                 max(match["clips_compared"], match["notional_coins_compared"]), match)


def measure_snapshot(fills: list, entries: list) -> dict:
    """A one-off reading of an account from its newest fills and orders: the same
    measures the daily records accumulate, for the stranger and family panels."""
    orders = ep.reconstruct_orders(fills)
    counts = records.habit_counts(entries, records.first_prices(fills))
    prof = profile(counts, sum(1 for o in orders if o["taker"]), len(orders))
    sig = ep.signature(fills, entries)
    hist = records.cadence_histogram(orders)
    measurable = prof is not None and prof["orders_seen"] >= MIN_ORDERS
    return {"orders_seen": counts["orders_seen"],
            "shares": {k: round(prof[k], 4) for k in SHARE_KEYS} if prof else None,
            "ioc5": prof.get("ioc5") if prof else None,
            "style": style(prof) if measurable else None,
            "cadence": hist if sum(hist) else None,
            "clip_table": {c: v["size"] for c, v in sig["clip_table"].items()} or None,
            "clip_notionals": {c: round(v, 6) for c, v in sig["clip_notionals"].items()} or None,
            "program_runs": sig["program_runs"]}


def snapshot_signature(snapshot: dict) -> dict:
    """execution_program's signature shape rebuilt from a stored snapshot."""
    return {"clip_table": {c: {"size": s, "share": 1.0, "count": ep.MIN_CLIP_ORDERS}
                           for c, s in (snapshot.get("clip_table") or {}).items()},
            "clip_notionals": dict(snapshot.get("clip_notionals") or {}),
            "program_runs": snapshot.get("program_runs") or 0,
            "ioc_5pct_share": snapshot.get("ioc5")}
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_study_tooling.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/study/tooling.py tests/test_study_tooling.py
git commit -m "feat(study): tooling tests T1-T3 from daily records and snapshots

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The fixed bars

**Files:**
- Create: `src/study/calibration.py`
- Test: `tests/test_study_calibration.py`

**Interfaces:**
- Produces:
  - constants `MIN_STRANGERS = 200`, `MIN_REFERENCES = 150`, `MIN_SAME_OP = {"family": 40, "self": 6}`, `FOR_UPPER = 0.02`, `AGAINST_MISMATCH = 0.10`, `CONFIDENCE = 0.95`
  - `binom_cdf(k, n, p) -> float`, `upper_bound(k, n, confidence=0.95) -> float`
  - `judge_continuous(x, *, strangers: list[float], same_op: dict[str, list[float]], higher_is_better: bool, min_strangers=200) -> dict`
  - `judge_binary(match: bool|None, *, stranger_k, stranger_n, same_op: dict[str, tuple[int, int]], min_strangers=200) -> dict`
  - `judge_against(traits: list[str], *, family_mismatch: dict[str, tuple[int, int]], stranger_trait_rate: dict[str, float]) -> dict`
  - Judgement dicts always carry `status` ∈ `for | neutral | uncalibrated | insufficient` (`judge_against`: `against | neutral | uncalibrated | none`); a `for`/`against` carries `lr`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_study_calibration.py`:

```python
"""The pre-registered bars (spec §8.2). Nothing here may be tuned to a result."""

import pytest

from src.study import calibration as cal

SELF_T2 = [0.03, 0.09, 0.19, 0.22, 0.25, 0.04]  # his monthly rhythm distances, median 0.14


def test_upper_bound_matches_clopper_pearson():
    assert cal.upper_bound(0, 148) > 0.02 >= cal.upper_bound(0, 149)
    assert cal.upper_bound(1, 200) == pytest.approx(0.0235, abs=5e-4)
    assert cal.upper_bound(0, 0) == 1.0 and cal.upper_bound(5, 5) == 1.0


def test_continuous_needs_two_hundred_strangers_and_a_same_operator_yardstick():
    assert cal.judge_continuous(0.1, strangers=[1.0] * 199, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "uncalibrated"
    assert cal.judge_continuous(0.1, strangers=[1.0] * 300, same_op={"self": SELF_T2[:5]},
                                higher_is_better=False)["status"] == "uncalibrated"
    assert cal.judge_continuous(None, strangers=[1.0] * 300, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "insufficient"


def test_for_is_judged_at_the_looser_of_the_candidate_and_the_same_operator_median():
    got = cal.judge_continuous(0.1, strangers=[0.5 + i / 1000 for i in range(300)],
                               same_op={"self": SELF_T2}, higher_is_better=False)
    assert got["status"] == "for" and got["level"] == 0.14 and got["basis"] == "self"
    assert got["same_op_rate"] >= 0.5 and got["stranger_k"] == 0
    # A closer match never fares worse than a looser one.
    assert cal.judge_continuous(0.01, strangers=[0.5] * 300, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "for"


def test_strangers_reaching_the_level_make_it_neutral():
    strangers = [0.1] * 10 + [0.9] * 290
    assert cal.judge_continuous(0.1, strangers=strangers, same_op={"self": SELF_T2},
                                higher_is_better=False)["status"] == "neutral"


def test_higher_is_better_mirrors():
    got = cal.judge_continuous(0.95, strangers=[0.2] * 300, same_op={"family": [0.9] * 40},
                               higher_is_better=True)
    assert got["status"] == "for" and got["level"] == 0.9


def test_binary_for_neutral_and_uncalibrated():
    fam = {"family": (41, 41)}
    assert cal.judge_binary(True, stranger_k=0, stranger_n=200, same_op=fam)["status"] == "for"
    assert cal.judge_binary(True, stranger_k=3, stranger_n=200, same_op=fam)["status"] == "neutral"
    assert cal.judge_binary(False, stranger_k=0, stranger_n=200, same_op=fam)["status"] == "neutral"
    assert cal.judge_binary(True, stranger_k=0, stranger_n=200,
                            same_op={"family": (20, 20), "self": (3, 3)})["status"] == "uncalibrated"
    assert cal.judge_binary(None, stranger_k=0, stranger_n=200, same_op=fam)["status"] == "insufficient"


def test_against_needs_forty_family_pairs_that_rarely_disagree():
    got = cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)},
                            stranger_trait_rate={"client_ids": 0.54})
    assert got["status"] == "against" and got["lr"] == pytest.approx(0.0705 / 0.54, rel=0.02)
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (6, 41)},
                             stranger_trait_rate={"client_ids": 0.54})["status"] == "neutral"
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 20)},
                             stranger_trait_rate={})["status"] == "uncalibrated"
    assert cal.judge_against([], family_mismatch={}, stranger_trait_rate={})["status"] == "none"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_study_calibration.py -q`
Expected: FAIL — `ImportError: cannot import name 'calibration' from 'src.study'`.

- [ ] **Step 3: Write `src/study/calibration.py`**

```python
"""The study's bars (spec 2026-10-06 §8.2), fixed before any result was seen. Pure.

A test says nothing until its panels are big enough (`uncalibrated`). It says
**for** only when strangers reach the candidate's level at most 2% of the time —
the one-sided 95% Clopper–Pearson upper bound — where the level is the LOOSER of
the candidate's own and each usable same-operator median. Judging at the looser
level means a closer match can never fare worse than a looser one, and the
same-operator rate at that level is at least one half by construction. Only T1
can say **against**, and only for a trait he never shows that same-operator
pairs almost never disagree on. Never tune these numbers to a result (rule 4).
"""

from __future__ import annotations

import math
from statistics import median

MIN_STRANGERS = 200
MIN_REFERENCES = 150
MIN_SAME_OP = {"family": 40, "self": 6}
FOR_UPPER = 0.02
AGAINST_MISMATCH = 0.10
CONFIDENCE = 0.95


def binom_cdf(k: int, n: int, p: float) -> float:
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    total = sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                         + i * log_p + (n - i) * log_q) for i in range(k + 1))
    return min(1.0, total)


def upper_bound(k: int, n: int, confidence: float = CONFIDENCE) -> float:
    """One-sided Clopper–Pearson upper bound on a rate seen k times in n."""
    if n <= 0 or k >= n:
        return 1.0
    alpha = 1 - confidence
    lo, hi = k / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def _usable(same_op: dict) -> dict:
    return {name: values for name, values in (same_op or {}).items()
            if len(values) >= MIN_SAME_OP.get(name, math.inf)}


def judge_continuous(x, *, strangers: list[float], same_op: dict[str, list[float]],
                     higher_is_better: bool, min_strangers: int = MIN_STRANGERS) -> dict:
    """T2/T3. `strangers` holds one value per measurable stranger; one that cannot
    produce the statistic carries -inf (higher is better) or inf (lower is better)."""
    if x is None:
        return {"status": "insufficient"}
    usable = _usable(same_op)
    if len(strangers) < min_strangers or not usable:
        return {"status": "uncalibrated", "strangers": len(strangers),
                "same_op": {k: len(v) for k, v in (same_op or {}).items()}}
    medians = [median(values) for values in usable.values()]
    level = min([x, *medians]) if higher_is_better else max([x, *medians])

    def reaches(value) -> bool:
        return value >= level if higher_is_better else value <= level

    k = sum(1 for s in strangers if reaches(s))
    ub = upper_bound(k, len(strangers))
    rate = min(sum(1 for v in values if reaches(v)) / len(values) for values in usable.values())
    return {"status": "for" if ub <= FOR_UPPER else "neutral", "level": round(level, 4),
            "stranger_k": k, "stranger_n": len(strangers), "stranger_upper": round(ub, 5),
            "same_op_rate": round(rate, 4), "basis": "+".join(sorted(usable)),
            "lr": round(rate / ub, 2)}


def judge_binary(match: bool | None, *, stranger_k: int, stranger_n: int,
                 same_op: dict[str, tuple[int, int]],
                 min_strangers: int = MIN_STRANGERS) -> dict:
    """T1. `same_op` maps a basis to (agreeing pairs or windows, total)."""
    if match is None:
        return {"status": "insufficient"}
    usable = {name: v for name, v in (same_op or {}).items()
              if v[1] >= MIN_SAME_OP.get(name, math.inf)}
    if stranger_n < min_strangers or not usable:
        return {"status": "uncalibrated", "strangers": stranger_n,
                "same_op": {k: v[1] for k, v in (same_op or {}).items()}}
    if not match:
        return {"status": "neutral", "stranger_k": stranger_k, "stranger_n": stranger_n}
    ub = upper_bound(stranger_k, stranger_n)
    rate = min(agree / total for agree, total in usable.values())
    return {"status": "for" if ub <= FOR_UPPER and rate >= 0.5 else "neutral",
            "stranger_k": stranger_k, "stranger_n": stranger_n,
            "stranger_upper": round(ub, 5), "same_op_rate": round(rate, 4),
            "basis": "+".join(sorted(usable)), "lr": round(rate / ub, 2)}


def judge_against(traits: list[str], *, family_mismatch: dict[str, tuple[int, int]],
                  stranger_trait_rate: dict[str, float]) -> dict:
    """T1 only: a trait he never shows dominates the candidate, and same-operator
    pairs disagree on it at most 10% of the time over at least 40 pairs."""
    if not traits:
        return {"status": "none"}
    measured = {t: tuple(family_mismatch.get(t, (0, 0))) for t in traits}
    ready = {t: v for t, v in measured.items() if v[1] >= MIN_SAME_OP["family"]}
    if not ready:
        return {"status": "uncalibrated", "traits": list(traits),
                "pairs": {t: v[1] for t, v in measured.items()}}
    holding = {t: v for t, v in ready.items() if v[0] / v[1] <= AGAINST_MISMATCH}
    if not holding:
        return {"status": "neutral", "traits": list(traits)}
    ratios = [upper_bound(k, n) / stranger_trait_rate[t]
              for t, (k, n) in holding.items() if stranger_trait_rate.get(t)]
    return {"status": "against", "traits": sorted(holding),
            "same_op_mismatch": {t: f"{k}/{n}" for t, (k, n) in holding.items()},
            "lr": round(min(ratios), 4) if ratios else None}
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_study_calibration.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/study/calibration.py tests/test_study_calibration.py
git commit -m "feat(study): the pre-registered bars, with Clopper-Pearson bounds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The habit census and the panels

**Files:**
- Modify: `scripts/census_execution_program.py` (habit rows, sub-accounts, state cap, summary)
- Create: `src/study/panels.py`
- Test: `tests/test_census_execution_program.py`, `tests/test_study_panels.py`

**Interfaces:**
- Consumes: `tooling.measure_snapshot`, `tooling.snapshot_signature`, `tooling.wasserstein`, `tooling.AGAINST_TRAITS`, `tooling.MIN_ORDERS`, `tooling.MIN_GAPS`, `tooling.DOMINANT_SHARE`; `src.hl_surface.parse_subaccounts(payload, master) -> list[dict] | None`; `execution_program.compare`.
- Produces:
  - census: `MAX_HABIT_ROWS = 5_000`; `measure_habits(fetch, addr, fills) -> dict | None`; `census_state.json` gains `"habits": {addr: {orders_seen, shares, ioc5, style, cadence, clip_table, clip_notionals, program_runs, subaccounts, at}}`; `census.json` gains `habit_measured`.
  - `panels.families(surface, census_habits, *, exclude=()) -> dict[str, list[str]]`
  - `panels.member_pairs(families, snapshots) -> list[tuple[dict, dict]]`
  - `panels.family_t1(pairs) -> {"agree": (k, n), "mismatch": {trait: (k, n)}}`, `panels.family_t2(pairs) -> list[float]`, `panels.family_t3(pairs) -> list[float]`
  - `panels.strangers(census_habits, *, exclude=()) -> list[dict]`
  - `panels.stranger_t1(rows, his_style) -> (k, n)`, `panels.stranger_trait_rates(rows) -> dict[str, float]`, `panels.stranger_t2(rows, his_hist) -> list[float]`, `panels.stranger_t3(rows, his_sig) -> list[float]`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_census_execution_program.py`:

```python
def test_a_habit_row_needs_its_order_read():
    def fetch(body):
        if body["type"] == "historicalOrders":
            return {"ok": False, "error": "HTTP 500"}
        return {"ok": True, "data": []}
    assert census.measure_habits(fetch, "0x" + "1" * 40, []) is None


def test_a_habit_row_records_style_and_subaccounts():
    master, sub = "0x" + "1" * 40, "0x" + "2" * 40

    def fetch(body):
        if body["type"] == "historicalOrders":
            return {"ok": True, "data": []}
        return {"ok": True, "data": [{"subAccountUser": sub, "master": master}]}
    row = census.measure_habits(fetch, master, [])
    assert row["subaccounts"] == [sub] and row["style"] is None and row["orders_seen"] == 0


def test_a_failed_subaccount_read_is_unknown_not_none():
    def fetch(body):
        if body["type"] == "historicalOrders":
            return {"ok": True, "data": []}
        return {"ok": False, "error": "HTTP 500"}
    assert census.measure_habits(fetch, "0x" + "1" * 40, [])["subaccounts"] is None


def test_habit_rows_are_capped_newest_first():
    habits = {f"0x{i:040x}": {"at": i} for i in range(census.MAX_HABIT_ROWS + 5)}
    capped = census.cap_state({"processed": {}, "hits": {}, "habits": habits})
    assert len(capped["habits"]) == census.MAX_HABIT_ROWS
    assert f"0x{0:040x}" not in capped["habits"]
```

Create `tests/test_study_panels.py`:

```python
"""Stranger and same-operator panels for the tooling tests."""

from src.study import panels, records, tooling

T = "0x45d26f28196d226497130c4bac709d808fed4029"
M, S1, S2, X = ("0x" + c * 40 for c in "1234")
HIS_STYLE = {"style": "PROGRAM_IOC5", "flags": {"client_ids": False, "triggers": False,
                                                "maker": False}}
MAKER = {"style": "MAKER", "flags": {"client_ids": True, "triggers": False, "maker": True}}


def snap(style, cadence=None, clips=None, shares=None):
    return {"orders_seen": 500, "style": style, "cadence": cadence, "clip_table": clips,
            "clip_notionals": None, "program_runs": 0, "ioc5": None,
            "shares": shares or {"client_ids": 0.0, "maker": 0.0, "triggers": 0.0}}


def test_families_join_both_sources_and_drop_singletons():
    surface = {"subaccounts": {S1: {"master": M}}}
    habits = {M: {"subaccounts": [S2]}, X: {"subaccounts": []}}
    assert panels.families(surface, habits) == {M: sorted([M, S1, S2])}


def test_panels_never_contain_the_target():
    surface = {"subaccounts": {S1: {"master": T}, T: {"master": M}}}
    habits = {T: {"orders_seen": 900, "style": HIS_STYLE}, X: {"orders_seen": 900, "style": MAKER}}
    assert panels.families(surface, habits, exclude={T}) == {}
    assert [r["wallet"] for r in panels.strangers(habits, exclude={T})] == [X]


def test_family_pairs_count_style_agreement_and_trait_disagreement():
    fams = {M: [M, S1, S2]}
    snaps = {M: snap(MAKER), S1: snap(MAKER), S2: snap(HIS_STYLE)}
    stats = panels.family_t1(panels.member_pairs(fams, snaps))
    assert stats["agree"] == (1, 3)
    assert stats["mismatch"]["client_ids"] == (2, 3)


def test_unmeasurable_members_are_left_out_not_counted_as_disagreeing():
    fams = {M: [M, S1]}
    stats = panels.family_t1(panels.member_pairs(fams, {M: snap(MAKER), S1: snap(None)}))
    assert stats["agree"] == (0, 0)


def test_stranger_rates_and_rhythm_values():
    hist = [0] * records.CADENCE_BINS
    hist[17] = 300
    rows = [{"wallet": X, "orders_seen": 500, "style": HIS_STYLE, "cadence": hist,
             "shares": {"client_ids": 0.9, "maker": 0.0, "triggers": 0.0}},
            {"wallet": M, "orders_seen": 500, "style": MAKER, "cadence": None,
             "shares": {"client_ids": 1.0, "maker": 1.0, "triggers": 0.0}}]
    assert panels.stranger_t1(rows, HIS_STYLE) == (1, 2)
    assert panels.stranger_trait_rates(rows) == {"client_ids": 1.0, "triggers": 0.0, "maker": 0.5}
    assert panels.stranger_t2(rows, hist) == [0.0, float("inf")]
    assert panels.stranger_t3(rows, tooling.snapshot_signature({})) == [float("-inf")] * 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_census_execution_program.py tests/test_study_panels.py -q`
Expected: FAIL — `AttributeError: module … has no attribute 'measure_habits'` and `ImportError: cannot import name 'panels'`.

- [ ] **Step 3: Extend the census** — in `scripts/census_execution_program.py`:

Add below `MAX_STATE_ROWS = 20_000`:

```python
# The habit census (candidate study, spec 2026-10-06 §8.1): one row per measured
# stranger, the newest kept. Each row carries a 50-bin rhythm histogram only when
# the account runs programs, so the committed file stays a few MB.
MAX_HABIT_ROWS = 5_000
```

Replace `load_state`'s fallback and `cap_state`:

```python
def load_state():
    try:
        with open(STATE) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    return {"processed": state.get("processed", {}), "hits": state.get("hits", {}),
            "habits": state.get("habits", {})}


def cap_state(state):
    """Keep the state bounded: the newest MAX_STATE_ROWS processed rows and
    MAX_HABIT_ROWS habit rows, all hits.

    Hits are few and precious (accounts reproducing his table), so they are never
    evicted; ordinary measured/insufficient rows are trimmed oldest-first by their
    observation time so the committed file cannot grow the repo without bound.
    """
    processed = state.get("processed", {})
    if len(processed) > MAX_STATE_ROWS:
        keep = sorted(processed.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_STATE_ROWS:]
        processed = dict(keep)
    habits = state.get("habits", {})
    if len(habits) > MAX_HABIT_ROWS:
        keep = sorted(habits.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_HABIT_ROWS:]
        habits = dict(keep)
    return {"processed": processed, "hits": state.get("hits", {}), "habits": habits}
```

Add after `register_hits`:

```python
def measure_habits(fetch, addr: str, fills: list) -> dict | None:
    """The habit-census row for one stranger: style, flags, rhythm and clips from
    its newest orders, plus its sub-accounts (the same-operator families). None
    when the order read fails — a failed read is never an empty row (rule 5); a
    failed sub-account read leaves `subaccounts` None, unknown rather than none."""
    from src.hl_surface import parse_subaccounts
    from src.study import tooling

    orders = fetch({"type": "historicalOrders", "user": addr})
    if not orders.get("ok") or not isinstance(orders.get("data"), list):
        return None
    row = tooling.measure_snapshot(fills, orders["data"])
    subs = fetch({"type": "subAccounts", "user": addr})
    parsed = parse_subaccounts(subs.get("data"), addr) if subs.get("ok") else None
    row["subaccounts"] = None if parsed is None else sorted({r["address"] for r in parsed})
    row["at"] = int(time.time())
    return row
```

In `run()`, directly after the `state["processed"][addr] = {...}` assignment, add:

```python
            habits = measure_habits(fetch, addr, result["data"])
            if habits is not None:
                state["habits"][addr] = habits
```

In `write_census`, add `habit_measured=len(state.get("habits") or {}),` to the `census.update(...)` call, after `attempted=len(state["processed"]),`.

- [ ] **Step 4: Write `src/study/panels.py`**

```python
"""Stranger and same-operator panels for the tooling tests (spec §8.1). Pure.

Strangers come from the habit census: uniformly sampled large accounts, the
target and the config cluster excluded. Same-operator pairs come from
sub-account families — the HL surface's sub-accounts and the census's
`subAccounts` reads — with two caveats recorded in the spec: a sub-account
shares its master's signer (an upper bound on how alike an operator's accounts
are), and the families measured so far are market makers.
"""

from __future__ import annotations

from src import execution_program as ep
from src.study import tooling


def families(surface: dict | None, census_habits: dict | None, *, exclude=()) -> dict[str, list[str]]:
    """{master: sorted members} for every family of two or more."""
    groups: dict[str, set[str]] = {}
    for sub, detail in ((surface or {}).get("subaccounts") or {}).items():
        master = str((detail or {}).get("master") or "").lower()
        if master and sub:
            groups.setdefault(master, {master}).add(str(sub).lower())
    for master, row in (census_habits or {}).items():
        subs = (row or {}).get("subaccounts") or []
        if subs:
            groups.setdefault(master.lower(), {master.lower()}).update(s.lower() for s in subs)
    blocked = {str(a).lower() for a in exclude}
    out = {}
    for master, members in groups.items():
        kept = sorted(m for m in members if m not in blocked)
        if master not in blocked and len(kept) >= 2:
            out[master] = kept
    return out


def member_pairs(fams: dict, snapshots: dict) -> list[tuple[dict, dict]]:
    pairs = []
    for members in fams.values():
        measured = [snapshots[m] for m in members if snapshots.get(m)]
        pairs.extend((a, b) for i, a in enumerate(measured) for b in measured[i + 1:])
    return pairs


def family_t1(pairs) -> dict:
    """Style agreement, and per-trait disagreement, over pairs where both members
    were measurable. An unmeasurable member is left out, never counted."""
    agree = n = 0
    mismatch = {t: [0, 0] for t in tooling.AGAINST_TRAITS}
    for a, b in pairs:
        if not a.get("style") or not b.get("style"):
            continue
        n += 1
        agree += a["style"] == b["style"]
        for trait in tooling.AGAINST_TRAITS:
            mismatch[trait][1] += 1
            mismatch[trait][0] += a["style"]["flags"][trait] != b["style"]["flags"][trait]
    return {"agree": (agree, n), "mismatch": {t: (v[0], v[1]) for t, v in mismatch.items()}}


def family_t2(pairs) -> list[float]:
    out = []
    for a, b in pairs:
        ha, hb = a.get("cadence") or [], b.get("cadence") or []
        if sum(ha) >= tooling.MIN_GAPS and sum(hb) >= tooling.MIN_GAPS:
            out.append(tooling.wasserstein(ha, hb))
    return out


def family_t3(pairs) -> list[float]:
    out = []
    for a, b in pairs:
        match = ep.compare(tooling.snapshot_signature(a), tooling.snapshot_signature(b))
        if match["status"] == "measured":
            out.append(match["strength"])
    return out


def strangers(census_habits: dict | None, *, exclude=()) -> list[dict]:
    """Measurable strangers: habit-census rows with at least 100 orders read."""
    blocked = {str(a).lower() for a in exclude}
    return [{**row, "wallet": wallet.lower()} for wallet, row in (census_habits or {}).items()
            if wallet.lower() not in blocked and isinstance(row, dict)
            and (row.get("orders_seen") or 0) >= tooling.MIN_ORDERS]


def stranger_t1(rows: list[dict], his_style: dict | None) -> tuple[int, int]:
    return sum(1 for r in rows if his_style and r.get("style") == his_style), len(rows)


def stranger_trait_rates(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {}
    return {t: sum(1 for r in rows
                   if ((r.get("shares") or {}).get(t) or 0) > tooling.DOMINANT_SHARE) / len(rows)
            for t in tooling.AGAINST_TRAITS}


def stranger_t2(rows: list[dict], his_hist: list[int]) -> list[float]:
    """Rhythm distance per stranger; one that runs no programs cannot match (inf)."""
    out = []
    for row in rows:
        hist = row.get("cadence") or []
        distance = tooling.wasserstein(hist, his_hist) if sum(hist) >= tooling.MIN_GAPS else None
        out.append(distance if distance is not None else float("inf"))
    return out


def stranger_t3(rows: list[dict], his_sig: dict) -> list[float]:
    """Clip strength per stranger; one with nothing to compare cannot match (-inf)."""
    out = []
    for row in rows:
        match = ep.compare(his_sig or {}, tooling.snapshot_signature(row))
        out.append(match["strength"] if match["status"] == "measured" else float("-inf"))
    return out
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_census_execution_program.py tests/test_study_panels.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add scripts/census_execution_program.py src/study/panels.py tests/test_census_execution_program.py tests/test_study_panels.py
git commit -m "feat(study): habit census rows and the stranger/family panels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Verdicts and assembly

**Files:**
- Create: `src/study/verdict.py`, `src/study/assemble.py`
- Modify: `src/study/calibration.py` (`judge_against` also requires 200 strangers)
- Test: `tests/test_study_verdict.py`, `tests/test_study_assemble.py`, `tests/test_study_calibration.py`

**Interfaces:**
- Consumes: Tasks 2, 6, 7, 8.
- Produces:
  - `verdict.FOR, AGAINST, MIXED, NEUTRAL, UNCALIBRATED, INSUFFICIENT`, `verdict.TOOLING = ("T1", "T2", "T3")`
  - `verdict.family_verdict(tests, names) -> {"verdict", "lr", "by"}`, `verdict.rank(families) -> float`, `verdict.newly(previous_rows, rows, family, verdict="for") -> list[dict]`
  - `assemble.REFERENCE_DAYS = 90`, `assemble.SCHEMA = "study/1"`
  - `assemble.window(days, last_day, n_days) -> list[dict]`
  - `assemble.his_reference(his_days) -> {"last_day", "span_days", "recent_profile", "full_profile", "style", "cadence", "signature"}` (`{}` without data)
  - `assemble.self_splits(his_days, ref) -> {"t1": (agree, total), "t2": [float], "t3": [float]}`
  - `assemble.panel_context(ref, splits, stranger_rows, family_pairs) -> {"t1", "t2", "t3", "status": {"strangers", "family_pairs", "self_windows"}}`
  - `assemble.tooling_tests(days: list[dict], ref, ctx) -> {"T1", "T2", "T3", "summary": {"days", "covered_days", "orders", "cadence"}}`
  - `assemble.study_row(member, tests, account_value, last_read_ms) -> dict` (keys: `wallet, source, studied_since_ms, account_value, coverage_days, orders, last_read_ms, families: {"tooling": {verdict, lr, by, key}}, rank`)
  - `assemble.dossier(member, tests, ref) -> dict`, `assemble.latest_doc(computed_at, target, ref, ctx_status, rows, collection) -> dict`
  - `calibration.judge_against(traits, *, family_mismatch, stranger_trait_rate, stranger_n, min_strangers=200)`

- [ ] **Step 1: Require 200 strangers for *against* too (spec §8.2: a test with fewer than 200 strangers is uncalibrated)** — in `tests/test_study_calibration.py` replace `test_against_needs_forty_family_pairs_that_rarely_disagree` with:

```python
def test_against_needs_forty_family_pairs_that_rarely_disagree_and_two_hundred_strangers():
    kw = {"stranger_trait_rate": {"client_ids": 0.54}, "stranger_n": 300}
    got = cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)}, **kw)
    assert got["status"] == "against" and got["lr"] == pytest.approx(0.0705 / 0.54, rel=0.02)
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (6, 41)},
                             **kw)["status"] == "neutral"
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 20)},
                             **kw)["status"] == "uncalibrated"
    assert cal.judge_against(["client_ids"], family_mismatch={"client_ids": (0, 41)},
                             stranger_trait_rate={"client_ids": 0.54},
                             stranger_n=150)["status"] == "uncalibrated"
    assert cal.judge_against([], family_mismatch={}, stranger_trait_rate={},
                             stranger_n=300)["status"] == "none"
```

and in `src/study/calibration.py` replace `judge_against`'s signature and first lines with:

```python
def judge_against(traits: list[str], *, family_mismatch: dict[str, tuple[int, int]],
                  stranger_trait_rate: dict[str, float], stranger_n: int,
                  min_strangers: int = MIN_STRANGERS) -> dict:
    """T1 only: a trait he never shows dominates the candidate, and same-operator
    pairs disagree on it at most 10% of the time over at least 40 pairs. Needs the
    same 200 strangers as every T1 judgement."""
    if not traits:
        return {"status": "none"}
    measured = {t: tuple(family_mismatch.get(t, (0, 0))) for t in traits}
    ready = {t: v for t, v in measured.items() if v[1] >= MIN_SAME_OP["family"]}
    if not ready or stranger_n < min_strangers:
        return {"status": "uncalibrated", "traits": list(traits), "strangers": stranger_n,
                "pairs": {t: v[1] for t, v in measured.items()}}
```

(the rest of the function — `holding`, `ratios`, the return — is unchanged).

- [ ] **Step 2: Write the failing tests** — create `tests/test_study_verdict.py`:

```python
"""Family verdicts, the study rank and transitions."""

from src.study import verdict


def t(status, lr=None):
    return {"judgement": {"status": status, "lr": lr}}


def make_tests(t1, against="none", t2="insufficient", t3="insufficient", against_lr=None, t2_lr=None):
    return {"T1": {**t(t1, 30.0 if t1 == "for" else None),
                   "against": {"status": against, "lr": against_lr}},
            "T2": t(t2, t2_lr), "T3": t(t3)}


def test_family_verdicts():
    assert verdict.family_verdict(make_tests("for"), verdict.TOOLING) == {
        "verdict": "for", "lr": 30.0, "by": ["T1"]}
    assert verdict.family_verdict(make_tests("neutral", "against", against_lr=0.1),
                                  verdict.TOOLING)["verdict"] == "against"
    assert verdict.family_verdict(make_tests("neutral", "against", "for", t2_lr=40.0),
                                  verdict.TOOLING)["verdict"] == "mixed"
    assert verdict.family_verdict(make_tests("uncalibrated"), verdict.TOOLING)["verdict"] == "uncalibrated"
    assert verdict.family_verdict(make_tests("insufficient"), verdict.TOOLING)["verdict"] == "insufficient"
    assert verdict.family_verdict(make_tests("neutral", t2="uncalibrated"),
                                  verdict.TOOLING)["verdict"] == "neutral"


def test_rank_clips_each_family():
    assert verdict.rank({"tooling": {"lr": 1e6}}) == 2.0
    assert verdict.rank({"tooling": {"lr": 0.001}}) == -2.0
    assert verdict.rank({"tooling": {"lr": None}}) == 0.0


def test_newly_reports_only_transitions():
    def row(wallet, v):
        return {"wallet": wallet, "families": {"tooling": {"verdict": v}}}
    before = [row("a", "for"), row("b", "neutral")]
    after = [row("a", "for"), row("b", "for"), row("c", "for"), row("d", "neutral")]
    assert [r["wallet"] for r in verdict.newly(before, after, "tooling")] == ["b", "c"]
```

Create `tests/test_study_assemble.py`:

```python
"""Assembly: his reference, his months, the panels, and each wallet's tooling verdict."""

from src.study import assemble, records, verdict

T = "0x45d26f28196d226497130c4bac709d808fed4029"
W, BOT = "0x" + "e" * 40, "0x" + "f" * 40
JUNE = 1_780_272_000_000  # 2026-06-01 00:00:00 UTC
HIS_STYLE = {"style": "PROGRAM_IOC5", "flags": {"client_ids": False, "triggers": False,
                                                "maker": False}}
MAKER = {"style": "MAKER", "flags": {"client_ids": True, "triggers": False, "maker": True}}


def history(wallet, start_ms, n_days, *, tif="Ioc", cloid=None, crossed=True, n=40):
    days = {}
    for d in range(n_days):
        lo = start_ms + d * records.DAY_MS
        t0 = lo + records.HOUR_MS
        fills = [{"coin": "BTC", "side": "A", "sz": "0.1", "px": "100.0", "time": t0 + i * 1_700,
                  "crossed": crossed, "oid": t0 + i * 1_700, "tid": t0 + i * 1_700}
                 for i in range(n)]
        entries = [{"order": {"coin": "BTC", "side": "A", "limitPx": "95.0", "oid": f["oid"],
                              "timestamp": f["time"], "tif": tif, "cloid": cloid,
                              "isTrigger": False, "orderType": "Limit", "reduceOnly": False},
                    "status": "filled"} for f in fills]
        records.fold_fills(days, fills, wallet=wallet, role="studied", start_ms=lo,
                           end_ms=lo + records.DAY_MS, last_fill_ms=None)
        records.fold_orders(days, entries, records.first_prices(fills), wallet=wallet,
                            role="studied", start_ms=lo, end_ms=lo + records.DAY_MS)
    return days


def context(his, ref, strangers=200, family_size=10):
    rows = [{"wallet": f"0x{i:040x}", "orders_seen": 500, "style": MAKER, "cadence": None,
             "clip_table": None, "clip_notionals": None, "program_runs": 0, "ioc5": None,
             "shares": {"client_ids": 1.0, "maker": 1.0, "triggers": 0.0}}
            for i in range(strangers)]
    snap = {"orders_seen": 500, "style": MAKER, "cadence": None, "clip_table": None,
            "clip_notionals": None, "program_runs": 0, "ioc5": None}
    pairs = [(snap, snap)] * (family_size * (family_size - 1) // 2)
    return assemble.panel_context(ref, assemble.self_splits(his, ref), rows, pairs)


def test_his_reference_is_built_by_the_same_code_as_any_wallet():
    ref = assemble.his_reference(history(T, JUNE, 120))
    assert ref["style"] == HIS_STYLE and sum(ref["cadence"]) >= 200
    assert ref["last_day"] == "2026-09-28" and ref["span_days"] == 90
    assert assemble.his_reference({}) == {}


def test_his_months_agree_with_the_rest_of_him():
    his = history(T, JUNE, 120)
    splits = assemble.self_splits(his, assemble.his_reference(his))
    assert splits["t1"] == (4, 4) and splits["t2"] == [0.0] * 4


def test_a_wallet_trading_his_way_reads_for_once_the_panels_are_calibrated():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    days = history(W, JUNE + 90 * records.DAY_MS, 20)
    tests = assemble.tooling_tests([days[d] for d in sorted(days)], ref, context(his, ref))
    assert tests["T1"]["judgement"]["status"] == "for"
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "for"
    row = assemble.study_row({"wallet": W, "source": "roster_lead", "since_ms": 1}, tests, 2e6, 5)
    assert row["families"]["tooling"]["verdict"] == "for" and row["rank"] > 0
    assert row["families"]["tooling"]["key"]["style"] == "PROGRAM_IOC5"


def test_a_maker_bot_with_client_ids_reads_against():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    bot = history(BOT, JUNE + 90 * records.DAY_MS, 20, tif="Alo", cloid="0x01", crossed=False)
    tests = assemble.tooling_tests([bot[d] for d in sorted(bot)], ref, context(his, ref))
    assert tests["T1"]["against"]["status"] == "against"
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "against"


def test_small_panels_read_uncalibrated_never_for_or_against():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    bot = history(BOT, JUNE + 90 * records.DAY_MS, 20, tif="Alo", cloid="0x01", crossed=False)
    tests = assemble.tooling_tests([bot[d] for d in sorted(bot)], ref,
                                   context(his, ref, strangers=10, family_size=3))
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "uncalibrated"


def test_a_wallet_never_read_is_insufficient():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    tests = assemble.tooling_tests([], ref, context(his, ref))
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "insufficient"


def test_the_latest_document_orders_by_rank_and_states_the_bars():
    rows = [{"wallet": "0x1", "rank": 0.0, "account_value": 5.0},
            {"wallet": "0x2", "rank": 1.5, "account_value": 1.0}]
    doc = assemble.latest_doc("2026-10-06T00:00:00+00:00", T, {}, {"strangers": 3}, rows,
                              {"read": ["0x1"], "unreadable": [], "stopped": False})
    assert [r["wallet"] for r in doc["wallets"]] == ["0x2", "0x1"]
    assert doc["panels"]["bars"] == {"strangers": 200, "family_pairs": 40, "self_windows": 6}
    assert doc["studied"] == 2 and doc["read"] == 1
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_study_verdict.py tests/test_study_assemble.py tests/test_study_calibration.py -q`
Expected: FAIL — `ImportError: cannot import name 'verdict'` / `'assemble'`.

- [ ] **Step 4: Write `src/study/verdict.py`**

```python
"""Per-family verdicts and the study rank (spec §8.3, §9). Pure.

A family is FOR when any of its calibrated tests is, AGAINST when T1 is and none
is for, MIXED when both; otherwise it reports the most informative of neutral,
uncalibrated or insufficient. The rank orders the Study list and nothing else:
it is never a tier and never stored as a confidence.
"""

from __future__ import annotations

import math

FOR, AGAINST, MIXED = "for", "against", "mixed"
NEUTRAL, UNCALIBRATED, INSUFFICIENT = "neutral", "uncalibrated", "insufficient"
TOOLING = ("T1", "T2", "T3")


def family_verdict(tests: dict, names: tuple[str, ...]) -> dict:
    judged = [(name, (tests.get(name) or {}).get("judgement") or {})
              for name in names if name in tests]
    fors = [(name, j) for name, j in judged if j.get("status") == FOR]
    against = ((tests.get("T1") or {}).get("against") or {}) if "T1" in names else {}
    is_against = against.get("status") == AGAINST
    if fors and is_against:
        status = MIXED
    elif fors:
        status = FOR
    elif is_against:
        status = AGAINST
    else:
        seen = {j.get("status") for _, j in judged} | {against.get("status")}
        status = (NEUTRAL if NEUTRAL in seen else UNCALIBRATED if UNCALIBRATED in seen
                  else INSUFFICIENT)
    lr = None
    if status == FOR:
        lr = max(j.get("lr") or 0 for _, j in fors) or None
    elif status == AGAINST:
        lr = against.get("lr")
    return {"verdict": status, "lr": lr,
            "by": [name for name, _ in fors] + (["T1:against"] if is_against else [])}


def rank(families: dict) -> float:
    total = 0.0
    for family in families.values():
        lr = family.get("lr")
        if isinstance(lr, (int, float)) and lr > 0:
            total += max(-2.0, min(2.0, math.log10(lr)))
    return round(total, 3)


def newly(previous_rows: list, rows: list, family: str, verdict: str = FOR) -> list[dict]:
    """Rows whose `family` verdict became `verdict` since the previous run."""
    before = {r.get("wallet"): ((r.get("families") or {}).get(family) or {}).get("verdict")
              for r in previous_rows or [] if isinstance(r, dict)}
    return [r for r in rows
            if ((r.get("families") or {}).get(family) or {}).get("verdict") == verdict
            and before.get(r["wallet"]) != verdict]
```

- [ ] **Step 5: Write `src/study/assemble.py`**

```python
"""The study's tests, rows and dossiers, assembled from in-memory inputs. Pure.

His reference is built from his own daily records by the same code as every
candidate's (spec §6.3). His months against the rest of his history are the
same-operator yardstick in time; the habit census gives the strangers; the
sub-account families give same-operator pairs (spec §8.1).
"""

from __future__ import annotations

from src import execution_program as ep
from src.study import calibration, panels, records, tooling, verdict

REFERENCE_DAYS = 90
SCHEMA = "study/1"


def window(days: dict, last_day: str, n_days: int) -> list[dict]:
    first = records.day_of(records.day_start_ms(last_day) - (n_days - 1) * records.DAY_MS)
    return [days[d] for d in sorted(days) if first <= d <= last_day]


def his_reference(his_days: dict) -> dict:
    """His recent habits and rhythm (90 days, widened until 200 in-run gaps) and his
    whole-history clip signature."""
    if not his_days:
        return {}
    last = max(his_days)
    span = REFERENCE_DAYS
    recent = tooling.summarise(window(his_days, last, span))
    while sum(recent["cadence"]) < tooling.MIN_GAPS and span < 3_650:
        span *= 2
        recent = tooling.summarise(window(his_days, last, span))
    full = tooling.summarise([his_days[d] for d in sorted(his_days)])
    recent_profile = tooling.summary_profile(recent)
    full_profile = tooling.summary_profile(full)
    return {"last_day": last, "span_days": span, "recent_profile": recent_profile,
            "full_profile": full_profile,
            "style": tooling.style(recent_profile) if recent_profile else None,
            "cadence": recent["cadence"],
            "signature": tooling.clip_signature(full, full_profile)}


def self_splits(his_days: dict, ref: dict) -> dict:
    """Each of his months against the rest of him: T1 agreement, T2 distances (against
    his recent window without that month), T3 strengths (against all his other months)."""
    out = {"t1": (0, 0), "t2": [], "t3": []}
    if not his_days or not ref:
        return out
    agree = total = 0
    ordered = [his_days[d] for d in sorted(his_days)]
    recent = window(his_days, ref["last_day"], ref.get("span_days", REFERENCE_DAYS))
    for month in sorted({d[:7] for d in his_days}):
        mine = tooling.summarise([r for r in ordered if r["day"].startswith(month)])
        rest_recent = tooling.summarise([r for r in recent if not r["day"].startswith(month)])
        rest_all = tooling.summarise([r for r in ordered if not r["day"].startswith(month)])
        mp, rp = tooling.summary_profile(mine), tooling.summary_profile(rest_recent)
        if (mp and rp and mp["orders_seen"] >= tooling.MIN_ORDERS
                and rp["orders_seen"] >= tooling.MIN_ORDERS):
            total += 1
            agree += tooling.style(mp) == tooling.style(rp)
        if (sum(mine["cadence"]) >= tooling.MIN_GAPS
                and sum(rest_recent["cadence"]) >= tooling.MIN_GAPS):
            out["t2"].append(tooling.wasserstein(mine["cadence"], rest_recent["cadence"]))
        match = ep.compare(tooling.clip_signature(rest_all, tooling.summary_profile(rest_all)),
                           tooling.clip_signature(mine, mp))
        if match["status"] == "measured":
            out["t3"].append(match["strength"])
    out["t1"] = (agree, total)
    return out


def panel_context(ref: dict, splits: dict, stranger_rows: list, family_pairs: list) -> dict:
    family = panels.family_t1(family_pairs)
    return {
        "t1": {"stranger": panels.stranger_t1(stranger_rows, ref.get("style")),
               "same_op": {"family": family["agree"], "self": splits["t1"]},
               "mismatch": family["mismatch"],
               "trait_rates": panels.stranger_trait_rates(stranger_rows)},
        "t2": {"strangers": panels.stranger_t2(stranger_rows, ref.get("cadence") or []),
               "same_op": {"family": panels.family_t2(family_pairs), "self": splits["t2"]}},
        "t3": {"strangers": panels.stranger_t3(stranger_rows, ref.get("signature") or {}),
               "same_op": {"family": panels.family_t3(family_pairs), "self": splits["t3"]}},
        "status": {"strangers": len(stranger_rows), "family_pairs": family["agree"][1],
                   "self_windows": splits["t1"][1]},
    }


def tooling_tests(days: list[dict], ref: dict, ctx: dict) -> dict:
    summary = tooling.summarise(days)
    prof = tooling.summary_profile(summary)
    t1 = tooling.t1_style(prof, ref.get("recent_profile"), ref.get("full_profile"))
    stranger_k, stranger_n = ctx["t1"]["stranger"]
    t1["judgement"] = calibration.judge_binary(
        t1["statistic"] if t1["status"] == "measured" else None,
        stranger_k=stranger_k, stranger_n=stranger_n, same_op=ctx["t1"]["same_op"])
    t1["against"] = calibration.judge_against(
        t1["detail"].get("against_traits", []), family_mismatch=ctx["t1"]["mismatch"],
        stranger_trait_rate=ctx["t1"]["trait_rates"], stranger_n=stranger_n)
    t2 = tooling.t2_rhythm(summary["cadence"], ref.get("cadence") or [])
    t2["judgement"] = calibration.judge_continuous(
        t2["statistic"], strangers=ctx["t2"]["strangers"], same_op=ctx["t2"]["same_op"],
        higher_is_better=False)
    t3 = tooling.t3_clips(tooling.clip_signature(summary, prof), ref.get("signature") or {})
    t3["judgement"] = calibration.judge_continuous(
        t3["statistic"], strangers=ctx["t3"]["strangers"], same_op=ctx["t3"]["same_op"],
        higher_is_better=True)
    return {"T1": t1, "T2": t2, "T3": t3,
            "summary": {"days": summary["days"], "covered_days": summary["covered_days"],
                        "orders": summary["orders"], "cadence": summary["cadence"]}}


def key_numbers(tests: dict) -> dict:
    detail = tests["T1"].get("detail") or {}
    shares = detail.get("shares") or {}
    return {"style": (detail.get("candidate") or {}).get("style"),
            "client_ids": shares.get("client_ids"), "maker": shares.get("maker"),
            "rhythm_s": tests["T2"].get("statistic"), "clip_strength": tests["T3"].get("statistic")}


def study_row(member: dict, tests: dict, account_value, last_read_ms) -> dict:
    families = {"tooling": {**verdict.family_verdict(tests, verdict.TOOLING),
                            "key": key_numbers(tests)}}
    return {"wallet": member["wallet"], "source": member["source"],
            "studied_since_ms": member["since_ms"], "account_value": account_value,
            "coverage_days": tests["summary"]["covered_days"], "orders": tests["summary"]["orders"],
            "last_read_ms": last_read_ms, "families": families, "rank": verdict.rank(families)}


def dossier(member: dict, tests: dict, ref: dict) -> dict:
    """Everything the Study page draws for one wallet. No timestamp of its own, so an
    unchanged dossier is not rewritten."""
    detail = tests["T1"].get("detail") or {}
    return {"schema": SCHEMA, "wallet": member["wallet"], "source": member["source"],
            "tests": {name: tests[name] for name in verdict.TOOLING},
            "series": {"cadence": tests["summary"]["cadence"], "his_cadence": ref.get("cadence"),
                       "shares": detail.get("shares"), "his_shares": detail.get("his_shares")}}


def latest_doc(computed_at: str, target: str, ref: dict, ctx_status: dict, rows: list,
               collection: dict) -> dict:
    return {"computed_at": computed_at, "schema": SCHEMA, "target": target,
            "reference": {"style": ref.get("style"), "last_day": ref.get("last_day"),
                          "cadence_gaps": sum(ref.get("cadence") or [])},
            "panels": {**ctx_status,
                       "bars": {"strangers": calibration.MIN_STRANGERS,
                                "family_pairs": calibration.MIN_SAME_OP["family"],
                                "self_windows": calibration.MIN_SAME_OP["self"]}},
            "studied": len(rows), "read": len(collection.get("read") or []),
            "unreadable": collection.get("unreadable") or [],
            "stopped": bool(collection.get("stopped")), "budget": collection.get("budget"),
            "wallets": sorted(rows, key=lambda r: (-r["rank"], -(r.get("account_value") or 0),
                                                   r["wallet"]))}
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_study_verdict.py tests/test_study_assemble.py tests/test_study_calibration.py -q`
Expected: all PASS.

- [ ] **Step 7: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/study/verdict.py src/study/assemble.py src/study/calibration.py tests/test_study_verdict.py tests/test_study_assemble.py tests/test_study_calibration.py
git commit -m "feat(study): verdicts, his reference and the per-wallet tooling assembly

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The run script

**Files:**
- Create: `scripts/run_study.py`
- Modify: `config.json` (add `study_wallets` and `study`)
- Test: `tests/test_run_study.py`

**Interfaces:**
- Consumes: everything above; `src.hl_budget.ReadBudget`; `src.utils.load_all_records`, `utils.save_latest`, `utils.load_config`.
- Produces:
  - `run(*, data_dir=None, config=None, now_ms=None, fetch=None, read_seconds=900) -> dict` (the latest document)
  - `study_wallet(wallet, mstate, now_ms, data_dir, fetch=None) -> {"status": "ok"|"stopped"|"unreadable", ...}`
  - `measure_families(fams, panel, now_ms, budget, fetch=None, limit=8) -> int`
  - `his_days(data_dir, target) -> dict`, `detector_wallets(data_dir) -> list[str]`
  - files: `data/study/latest.json`, `data/study/state.json`, `data/study/wallets/<addr>.json`, `data/study/panel/families.json`, `data/study/archive/<addr>/<day>.json|<month>.jsonl.gz`
  - state per wallet: `fills_cursor_ms, last_fill_ms, orders_cursor_ms, ledger_cursor_ms, orders_read_ms, last_read_ms`; top level `members, decayed_seen, wallets`.

- [ ] **Step 1: Add the config keys** — in `config.json`, after the `"watch_wallets": [...]` entry, add:

```json
  "study_wallets": [],
  "study": {"max_wallets": 40},
```

(keep the file valid JSON: a comma after the preceding entry, none after the last).

- [ ] **Step 2: Write the failing tests** — create `tests/test_run_study.py`:

```python
"""scripts/run_study.py end to end, network-free."""

import json
import re
from pathlib import Path

from scripts import run_study
from src.study import archive, records

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B = "0x" + "a" * 40, "0x" + "b" * 40
NOW = 1_791_000_000_000  # 2026-10-03 04:00:00 UTC
CONFIG = {"target_wallet": T, "known_self_wallets": [], "watch_wallets": [],
          "study_wallets": []}


def program(start, n, coin="BTC", step=1_700):
    return [{"coin": coin, "side": "A", "sz": "0.1", "px": "100.0", "time": start + i * step,
             "crossed": True, "oid": start + i * step, "tid": start + i * step}
            for i in range(n)]


def entries(fills):
    return [{"oid": f["oid"], "order": {"coin": f["coin"], "side": f["side"], "limitPx": "95.0",
                                        "oid": f["oid"], "timestamp": f["time"], "tif": "Ioc",
                                        "cloid": None, "isTrigger": False, "orderType": "Limit",
                                        "reduceOnly": False}, "status": "filled"}
            for f in fills]


class Fake:
    """Hyperliquid's info API for a few wallets: only the newest 10,000 fills exist."""

    def __init__(self, fills=None, orders=None, errors=None):
        self.fills, self.orders, self.errors = fills or {}, orders or {}, errors or {}
        self.calls = []

    def __call__(self, body):
        kind, user = body["type"], body.get("user")
        self.calls.append((kind, user))
        if (kind, user) in self.errors:
            return {"ok": False, "error": self.errors[(kind, user)]}
        if kind == "userFillsByTime":
            kept = sorted(self.fills.get(user, []), key=lambda f: f["time"])[-10_000:]
            rows = [f for f in kept if body["startTime"] <= f["time"] <= body["endTime"]]
            return {"ok": True, "data": rows[:2_000]}
        if kind == "userFills":
            return {"ok": True, "data": self.fills.get(user, [])[-2_000:]}
        if kind == "historicalOrders":
            return {"ok": True, "data": self.orders.get(user, [])[-2_000:]}
        return {"ok": True, "data": []}


def lead(wallet, value=2e6):
    return {"wallet": wallet, "tier": "POSSIBLE", "is_service": False,
            "evidence": {"hl_role": "user", "hl_account_value": value}}


def data_dir(tmp_path, roster_rows, surface=None):
    data = tmp_path / "data"
    (data / "roster").mkdir(parents=True)
    (data / "roster" / "latest.json").write_text(json.dumps({"wallets": roster_rows}))
    his = program(NOW - 3 * records.DAY_MS, 300)
    (data / "fills").mkdir()
    (data / "fills" / "2026-09-30.json").write_text(json.dumps(his))
    (data / "orders").mkdir()
    (data / "orders" / "2026-09-30.json").write_text(json.dumps(entries(his)))
    if surface:
        (data / "hl_surface").mkdir()
        (data / "hl_surface" / "latest.json").write_text(json.dumps(surface))
    return data


def test_a_run_reads_folds_and_writes_every_output(tmp_path):
    data = data_dir(tmp_path, [lead(A), lead(T)])
    fills = program(NOW - 2 * records.DAY_MS, 250)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    assert [r["wallet"] for r in doc["wallets"]] == [A] and doc["read"] == 1
    assert all(user != T for _kind, user in fake.calls)
    study = data / "study"
    assert (study / "latest.json").exists() and (study / "wallets" / f"{A}.json").exists()
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 250
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 250
    state = archive.load_state(data)
    assert state["wallets"][A]["fills_cursor_ms"] > NOW - 14 * records.DAY_MS
    assert doc["wallets"][0]["families"]["tooling"]["verdict"] == "uncalibrated"


def test_the_target_is_never_studied_even_when_pinned(tmp_path):
    data = data_dir(tmp_path, [lead(T)])
    fake = Fake()
    config = {**CONFIG, "watch_wallets": [T], "study_wallets": [T]}
    doc = run_study.run(data_dir=data, config=config, now_ms=NOW, fetch=fake)
    assert doc["studied"] == 0 and fake.calls == []


def test_a_failed_read_leaves_the_cursor_and_writes_nothing(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fake = Fake(errors={("userFillsByTime", A): "HTTP 500"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    assert doc["unreadable"] == [{"wallet": A, "error": "HTTP 500"}] and doc["read"] == 0
    assert "fills_cursor_ms" not in archive.load_state(data)["wallets"][A]
    assert not archive.wallet_dir(A, data).exists()


def test_a_stop_after_the_fills_fold_keeps_the_fold_and_retries_the_orders(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)},
                errors={("historicalOrders", A): "time_budget"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    mstate = archive.load_state(data)["wallets"][A]
    assert doc["stopped"] and "fills_cursor_ms" in mstate and "orders_read_ms" not in mstate
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 60_000,
                  fetch=Fake(fills={A: fills}, orders={A: entries(fills)}))
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 50
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 50


def test_a_busy_wallets_newest_orders_still_count(tmp_path):
    # Its newest 2,000 orders can all be minutes old; they must still be read
    # (the 2026-10-06 dry run found two bots whose habits were never recorded).
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 50 * 60_000, 2_500, step=1_000)
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW,
                  fetch=Fake(fills={A: fills}, orders={A: entries(fills)}))
    day = archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]
    assert day["habits"]["orders_seen"] == 2_000


def test_a_saturated_read_moves_the_cursor_without_claiming_the_gap(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 4 * records.HOUR_MS, 12_000, step=1_000)  # one a second from 00:00
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake(fills={A: fills}))
    first_read = fills[2_000]["time"]  # only the newest 10,000 exist
    day = archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]
    assert day["coverage"]["fills"][0][0] == first_read and day["coverage"]["saturated"]
    mstate = archive.load_state(data)["wallets"][A]
    assert mstate["fills_cursor_ms"] == NOW - records.HOUR_MS  # the 03:00 hour mark
    assert day["coverage"]["runs_split"]


def test_family_members_are_measured_and_kept(tmp_path):
    surface = {"subaccounts": {B: {"master": A}}}
    data = data_dir(tmp_path, [lead(A)], surface=surface)
    fills = program(NOW - 2 * records.DAY_MS, 150)  # 100+ orders: measurable members
    fake = Fake(fills={A: fills, B: fills}, orders={A: entries(fills), B: entries(fills)})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    panel = archive.read_json(data / "study" / "panel" / "families.json", {})
    assert set(panel["members"]) == {A, B} and panel["families"] == {A: [A, B]}
    assert doc["panels"]["family_pairs"] == 1


def test_the_study_step_is_bounded_inside_its_job():
    workflow = (Path(__file__).parent.parent / ".github/workflows/study.yml").read_text()
    job_text = workflow.split("\n  study:\n", 1)[1]  # the gate job has its own timeout
    job = int(re.search(r"\n    timeout-minutes:\s*(\d+)", job_text).group(1))
    step = job_text.split("name: Study the candidates", 1)[1].split("- name:", 1)[0]
    minutes = int(re.search(r"timeout-minutes:\s*(\d+)", step).group(1))
    assert run_study.READ_SECONDS + 60 < minutes * 60 < job * 60
```

(The last test reads `.github/workflows/study.yml`, created in Task 12; it fails until then. Mark it `pytest.mark.skip` only if you implement Task 10 and 12 in separate PRs — they ship together here.)

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_run_study.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.run_study'`.

- [ ] **Step 4: Write `scripts/run_study.py`**

```python
#!/usr/bin/env python3
"""Keep every identified Hyperliquid candidate under study (spec 2026-10-06).

The ONLY writer of data/study/. A run chooses the study set; reads each wallet's
new fills (and, daily, its orders and ledger) strictly and folds them into daily
records behind a quiet boundary; spends what the budget leaves measuring
sub-account families; builds his own records from data/fills, orders and ledger
with the same code; runs the tooling tests, calibrated on the habit census, the
families and his own months; and writes latest.json, the dossiers and state, then
rolls sealed months. Usage:

    python scripts/run_study.py                       # production (study.yml)
    python scripts/run_study.py --data-dir /tmp/x/data --max-wallets 8   # dry run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import utils
from src.hl_budget import ReadBudget
from src.study import archive, assemble, collect, panels, records, selection, tooling

FIRST_READ_DAYS = 14
ORDERS_EVERY_MS = 24 * records.HOUR_MS
WINDOW_DAYS = 120
FAMILY_REMEASURE_MS = 30 * records.DAY_MS
FAMILY_MEMBERS_PER_RUN = 8
READ_SECONDS = 900
WEIGHT_PER_MINUTE = 900


def read_json(path: Path, default):
    doc = archive.read_json(path, default)
    return doc if isinstance(doc, type(default)) else default


def detector_wallets(data_dir: Path) -> list[str]:
    """HL accounts other detectors found (spec §5 source 4), strongest first."""
    found: list = []
    handoffs = read_json(data_dir / "dormancy" / "latest.json", {}).get("handoffs") or {}
    found += sorted(handoffs, key=lambda a: -float((handoffs[a] or {}).get("score") or 0))
    newborn = read_json(data_dir / "newborn" / "latest.json", {}).get("newborn") or []
    found += [r.get("wallet") for r in sorted(newborn, key=lambda r: -float(r.get("account_value") or 0))
              if float(r.get("account_value") or 0) >= 1_000_000]
    census = read_json(data_dir / "execution_program" / "census.json", {})
    found += [h.get("wallet") for h in census.get("hits") or [] if isinstance(h, dict)]
    tape = read_json(data_dir / "tape" / "latest.json", {})
    found += [h.get("wallet") for h in tape.get("program_hits") or [] if isinstance(h, dict)]
    provenance = read_json(data_dir / "provenance" / "latest.json", {})
    found += [f.get("account") for f in provenance.get("findings") or [] if isinstance(f, dict)]
    return [w for w in found if isinstance(w, str)]


def _orders_and_ledger(wallet: str, mstate: dict, days: dict, fills: list, now_ms: int,
                       fetch) -> dict:
    """The daily reads. A stop or a failure leaves `orders_read_ms` alone so they are
    retried next run; whatever was folded before it stays folded."""
    default_start = now_ms - FIRST_READ_DAYS * records.DAY_MS
    ocursor = int(mstate.get("orders_cursor_ms") or default_start)
    lcursor = int(mstate.get("ledger_cursor_ms") or default_start)
    orders = collect.read_orders(wallet, fetch)
    if not orders["ok"]:
        return {"stopped": orders["stopped"], "orders_error": orders["error"]}
    # Counted as soon as read: a busy bot's newest 2,000 orders can all be minutes
    # old, so waiting for them to age would never record its habits (found by the
    # 2026-10-06 dry run). An order still open is counted as open.
    start = max(ocursor, orders["oldest_ms"] or ocursor) if orders["full"] else ocursor
    if now_ms > start:
        records.fold_orders(days, orders["orders"], records.first_prices(fills), wallet=wallet,
                            role="studied", start_ms=start, end_ms=now_ms)
        mstate["orders_cursor_ms"] = now_ms
    ledger = collect.read_ledger(wallet, lcursor, now_ms, fetch)
    if not ledger["ok"]:
        return {"stopped": ledger["stopped"], "ledger_error": ledger["error"]}
    if ledger["known_until_ms"] > lcursor:
        records.fold_ledger(days, ledger["rows"], wallet=wallet, role="studied",
                            start_ms=lcursor, end_ms=ledger["known_until_ms"])
        mstate["ledger_cursor_ms"] = ledger["known_until_ms"]
    mstate["orders_read_ms"] = now_ms
    return {"stopped": False}


def study_wallet(wallet: str, mstate: dict, now_ms: int, data_dir: Path, fetch=None) -> dict:
    """Read and fold one wallet. `mstate` changes only for what was folded."""
    default_start = now_ms - FIRST_READ_DAYS * records.DAY_MS
    cursor = int(mstate.get("fills_cursor_ms") or default_start)
    got = collect.read_fills(wallet, cursor, now_ms, fetch)
    if not got["ok"]:
        return {"status": "stopped" if got["stopped"] else "unreadable", "error": got["error"]}
    start, last_fill = cursor, mstate.get("last_fill_ms")
    if got["saturated"] and got["first_ms"]:
        # Hyperliquid no longer serves the span before the first fill read: it stays
        # unknown, never quiet, and so does whether that fill opened a session.
        start, last_fill = max(cursor, got["first_ms"]), None
    boundary, split = records.quiet_boundary([f["time"] for f in got["fills"]], start,
                                             got["known_until_ms"])
    due = now_ms - int(mstate.get("orders_read_ms") or 0) >= ORDERS_EVERY_MS
    lo = start
    if due:
        lo = min(start, int(mstate.get("orders_cursor_ms") or default_start),
                 int(mstate.get("ledger_cursor_ms") or default_start))
    days = archive.load_days(wallet, records.day_of(lo), records.day_of(now_ms), data_dir)
    before = {day: json.dumps(record, sort_keys=True) for day, record in days.items()}
    if boundary > start:
        mstate["last_fill_ms"] = records.fold_fills(
            days, got["fills"], wallet=wallet, role="studied", start_ms=start, end_ms=boundary,
            last_fill_ms=last_fill, saturated=got["saturated"], runs_split=split)
        mstate["fills_cursor_ms"] = boundary
    elif got["saturated"]:
        mstate["fills_cursor_ms"], mstate["last_fill_ms"] = start, None
    result = {"status": "ok", "fills": len(got["fills"]), "saturated": got["saturated"]}
    if due:
        extra = _orders_and_ledger(wallet, mstate, days, got["fills"], now_ms, fetch)
        if extra.pop("stopped"):
            result["status"] = "stopped"
        result.update(extra)
    archive.save_days({day: record for day, record in days.items()
                       if json.dumps(record, sort_keys=True) != before.get(day)}, data_dir)
    return result


def measure_families(fams: dict, panel: dict, now_ms: int, budget, fetch=None,
                     limit: int = FAMILY_MEMBERS_PER_RUN) -> int:
    """Re-measure family members older than 30 days, a few per run (spec §8.1)."""
    members = panel.setdefault("members", {})
    due = [w for w in dict.fromkeys(m for master in sorted(fams) for m in fams[master])
           if now_ms - int((members.get(w) or {}).get("at_ms") or 0) >= FAMILY_REMEASURE_MS]
    measured = 0
    for wallet in due[:limit]:
        if not budget.can_continue():
            break
        fills = collect.read_recent_fills(wallet, fetch)
        orders = collect.read_orders(wallet, fetch) if fills["ok"] else fills
        if not orders["ok"]:
            if orders["stopped"]:
                break
            continue
        members[wallet] = {"at_ms": now_ms,
                           **tooling.measure_snapshot(fills["fills"], orders["orders"])}
        measured += 1
    return measured


def his_days(data_dir: Path, target: str) -> dict:
    """His daily records, built from the collector's stored data by the same code."""
    fills = [f for f in utils.load_all_records(str(data_dir / "fills"))
             if isinstance(f, dict) and isinstance(f.get("time"), (int, float))]
    if not fills:
        return {}
    entries = [r for r in utils.load_all_records(str(data_dir / "orders"))
               if isinstance(r, dict) and isinstance(r.get("order"), dict)]
    ledger = utils.load_all_records(str(data_dir / "ledger"))
    start = int(min(f["time"] for f in fills))
    end = int(max(f["time"] for f in fills)) + 1
    days: dict = {}
    records.fold_fills(days, fills, wallet=target, role="target", start_ms=start, end_ms=end,
                       last_fill_ms=None)
    records.fold_orders(days, entries, records.first_prices(fills), wallet=target,
                        role="target", start_ms=start, end_ms=end)
    records.fold_ledger(days, ledger, wallet=target, role="target", start_ms=start, end_ms=end)
    return days


def _mark_departed(previous: dict, members: list, now_ms: int, data_dir: Path) -> None:
    """A wallet that left the set keeps its archive; its dossier says since when."""
    current = {m["wallet"] for m in members}
    for wallet in set(previous or {}) - current:
        path = archive.root(data_dir) / "wallets" / f"{wallet}.json"
        doc = archive.read_json(path, None)
        if isinstance(doc, dict) and not doc.get("left_ms"):
            archive.write_if_changed(path, {**doc, "left_ms": now_ms})


def run(*, data_dir: Path | None = None, config: dict | None = None, now_ms: int | None = None,
        fetch=None, read_seconds: int = READ_SECONDS) -> dict:
    data_dir = Path(data_dir or utils.DATA_DIR)
    config = config or utils.load_config()
    now_ms = int(now_ms or time.time() * 1000)
    target = config["target_wallet"].lower()
    exclude = {target, *(w.lower() for w in config.get("known_self_wallets") or [])}
    roster = read_json(data_dir / "roster" / "latest.json", {})
    state = archive.load_state(data_dir)
    wallets_state = state.setdefault("wallets", {})
    sources, state["decayed_seen"] = selection.by_source(
        config, roster, detector_wallets(data_dir), state.get("decayed_seen") or {}, now_ms)
    max_wallets = int((config.get("study") or {}).get("max_wallets") or selection.MAX_WALLETS)
    members = selection.choose(sources, state.get("members"), now_ms,
                               blocked=selection.blocked_wallets(config, roster),
                               max_wallets=max_wallets)
    _mark_departed(state.get("members"), members, now_ms, data_dir)
    state["members"] = {m["wallet"]: {"source": m["source"], "since_ms": m["since_ms"]}
                        for m in members}

    census_state = read_json(data_dir / "execution_program" / "census_state.json", {})
    fams = panels.families(read_json(data_dir / "hl_surface" / "latest.json", {}),
                           census_state.get("habits"), exclude=exclude)
    panel_path = archive.root(data_dir) / "panel" / "families.json"
    panel = read_json(panel_path, {})
    collection = {"read": [], "unreadable": [], "stopped": False}
    with ReadBudget(seconds=read_seconds, weight_per_minute=WEIGHT_PER_MINUTE) as budget:
        order = sorted(members, key=lambda m: (
            wallets_state.get(m["wallet"], {}).get("last_read_ms") or 0, m["wallet"]))
        for member in order:
            if not budget.can_continue():
                collection["stopped"] = True
                break
            mstate = wallets_state.setdefault(member["wallet"], {})
            result = study_wallet(member["wallet"], mstate, now_ms, data_dir, fetch)
            if result["status"] == "unreadable":
                collection["unreadable"].append({"wallet": member["wallet"],
                                                 "error": result.get("error")})
                continue
            if result["status"] != "ok":
                collection["stopped"] = True
                break
            mstate["last_read_ms"] = now_ms
            collection["read"].append(member["wallet"])
        if not collection["stopped"]:
            measure_families(fams, panel, now_ms, budget, fetch)
        collection["budget"] = budget.report()
    panel["families"] = fams

    his = his_days(data_dir, target)
    ref = assemble.his_reference(his)
    splits = assemble.self_splits(his, ref)
    strangers = panels.strangers(census_state.get("habits"), exclude=exclude)
    snapshots = {w: v for w, v in (panel.get("members") or {}).items() if isinstance(v, dict)}
    ctx = assemble.panel_context(ref, splits, strangers, panels.member_pairs(fams, snapshots))
    values = {selection.address(r.get("wallet")): (r.get("evidence") or {}).get("hl_account_value")
              for r in roster.get("wallets") or [] if isinstance(r, dict)}
    first_day = records.day_of(now_ms - (WINDOW_DAYS - 1) * records.DAY_MS)
    today = records.day_of(now_ms)
    rows = []
    for member in members:
        days = archive.load_days(member["wallet"], first_day, today, data_dir)
        tests = assemble.tooling_tests([days[d] for d in sorted(days)], ref, ctx)
        rows.append(assemble.study_row(member, tests, values.get(member["wallet"]),
                                       wallets_state.get(member["wallet"], {}).get("last_read_ms")))
        archive.write_if_changed(archive.root(data_dir) / "wallets" / f"{member['wallet']}.json",
                                 assemble.dossier(member, tests, ref))
        archive.roll_sealed_months(member["wallet"], today, data_dir)
    computed_at = datetime.fromtimestamp(now_ms / 1000, UTC).isoformat()
    doc = assemble.latest_doc(computed_at, target, ref, ctx["status"], rows, collection)
    archive.write_if_changed(panel_path, panel)
    archive.save_state(state, data_dir)
    utils.save_latest(str(archive.root(data_dir)), doc)
    return doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="read and write this data directory instead of data/ (dry runs)")
    parser.add_argument("--max-wallets", type=int, default=None)
    parser.add_argument("--read-seconds", type=int, default=READ_SECONDS)
    args = parser.parse_args(argv)
    config = utils.load_config()
    if args.max_wallets:
        config = {**config, "study": {**(config.get("study") or {}),
                                      "max_wallets": args.max_wallets}}
    doc = run(data_dir=args.data_dir, config=config, read_seconds=args.read_seconds)
    print(f"[study] {doc['studied']} studied, {doc['read']} read, "
          f"{len(doc['unreadable'])} unreadable, stopped={doc['stopped']}; "
          f"panels {doc['panels']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests (the workflow test waits for Task 12)**

Run: `python -m pytest tests/test_run_study.py -q -k "not bounded"`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add scripts/run_study.py tests/test_run_study.py config.json
git commit -m "feat(study): the run script, the only writer of data/study/

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The roster reads the study

**Files:**
- Modify: `src/roster.py` (in `build_roster`, directly after the execution-program block that ends with `e["reasons"].append(reason)` inside the `voting_wallets` loop)
- Test: `tests/test_roster_study.py`

**Interfaces:**
- Consumes: `data/study/latest.json` rows (`wallet`, `rank`, `coverage_days`, `families.<name>.{verdict, lr, by, key}`).
- Produces: roster row field `evidence.study = {as_of, rank, coverage_days, families: {name: {verdict, lr, by, key}}}`; a tooling `for` adds `VECTOR_EXECUTION` (`"execution_program"`).

- [ ] **Step 1: Write the failing test** — create `tests/test_roster_study.py`:

```python
"""The roster reads the candidate study: annotation always, a vote only for a calibrated FOR."""

import json

from src import roster

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B = "0x" + "a" * 40, "0x" + "b" * 40


def study_row(wallet, verdict):
    return {"wallet": wallet, "rank": 1.0, "coverage_days": 20,
            "families": {"tooling": {"verdict": verdict, "lr": 30.0, "by": ["T1"],
                                     "key": {"style": "PROGRAM_IOC5"}}}}


def build(tmp_path, monkeypatch, rows, previous=None):
    # ROSTER_DIR is fixed at import: patch it too, or carry_peak_tier reads the real roster.
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "study").mkdir(exist_ok=True)
    (tmp_path / "study" / "latest.json").write_text(json.dumps(
        {"computed_at": "2026-10-06T00:00:00+00:00", "wallets": rows}))
    if previous:
        (tmp_path / "roster").mkdir(exist_ok=True)
        (tmp_path / "roster" / "latest.json").write_text(json.dumps({"wallets": previous}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    return {w["wallet"]: w for w in doc["wallets"]}


def test_a_calibrated_tooling_for_casts_the_execution_vote(tmp_path, monkeypatch):
    rows = build(tmp_path, monkeypatch, [study_row(A, "for"), study_row(T, "for")])
    assert "execution_program" in rows[A]["vectors"] and rows[A]["tier"] == "POSSIBLE"
    assert rows[A]["evidence"]["study"]["families"]["tooling"]["verdict"] == "for"
    assert T not in rows


def test_against_is_annotation_only(tmp_path, monkeypatch):
    previous = [{"wallet": B, "tier": "POSSIBLE", "peak_tier": "POSSIBLE"}]
    rows = build(tmp_path, monkeypatch, [study_row(B, "against")], previous=previous)
    assert rows[B]["vectors"] == []
    assert rows[B]["evidence"]["study"]["families"]["tooling"]["verdict"] == "against"
    assert rows[B]["peak_tier"] == "POSSIBLE"


def test_no_study_file_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    assert all("study" not in w["evidence"] for w in doc["wallets"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_roster_study.py -q`
Expected: FAIL — `KeyError: '0xaaaa…'` (the study file is not read).

- [ ] **Step 3: Read the study in `build_roster`** — insert after the execution-program `for addr, match in voting_wallets(...)` loop:

```python
    # The candidate study (scripts/run_study.py, spec 2026-10-06 §9): every studied
    # wallet carries its verdicts as `evidence.study`. Only a calibrated tooling FOR
    # casts a vote, the existing execution_program one; AGAINST is annotation only
    # and never changes a tier or a vector (the operator's decision, 2026-10-06).
    try:
        with open(DATA_DIR / "study" / "latest.json") as f:
            study = json.load(f)
    except (OSError, ValueError):
        study = {}
    for row in (study.get("wallets") if isinstance(study, dict) else None) or []:
        a = (row.get("wallet") or "").lower() if isinstance(row, dict) else ""
        if not a or a == target:
            continue
        e = entry(a)
        families = row.get("families") or {}
        e["evidence"]["study"] = {
            "as_of": study.get("computed_at"), "rank": row.get("rank"),
            "coverage_days": row.get("coverage_days"),
            "families": {name: {k: fam.get(k) for k in ("verdict", "lr", "by", "key")}
                         for name, fam in families.items() if isinstance(fam, dict)}}
        if (families.get("tooling") or {}).get("verdict") == "for":
            e["vectors"].add(VECTOR_EXECUTION)
            reason = "Makes orders the way he does (candidate study, calibrated)"
            if reason not in e["reasons"]:
                e["reasons"].append(reason)
```

- [ ] **Step 4: Run the roster tests**

Run: `python -m pytest tests/test_roster_study.py tests/test_roster.py tests/test_roster_boundary.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add src/roster.py tests/test_roster_study.py
git commit -m "feat(roster): read the candidate study; only a calibrated FOR votes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 12: The workflow and its schedulers

**Files:**
- Create: `.github/workflows/study.yml`
- Modify: `scripts/keep_schedule.py` (`SCHEDULE`, `GROUP_MEMBERS`), `scripts/dispatch_workflows.ps1` (`$Schedule`), `scripts/apps_script/ezekiel_relay.gs` (`SCHEDULE`)
- Test: `tests/test_keep_schedule.py`, `scripts/apps_script/relay.test.mjs`, `tests/test_run_study.py::test_the_study_step_is_bounded_inside_its_job`

**Interfaces:**
- Consumes: `scripts/run_study.py` (Task 10), `scripts/keep_schedule.py --gate`, `scripts/pipeline_status.py`, `scripts/check_repo_size.py`.
- Produces: a 6-hourly run in concurrency group `study` committing only `data/study/`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_keep_schedule.py`:

```python
def test_the_study_runs_every_six_hours_in_its_own_group():
    from scripts.keep_schedule import GROUP_MEMBERS
    assert {"file": "study.yml", "minutes": 360, "group": "study"} in SCHEDULE
    assert GROUP_MEMBERS["study"] == ["study.yml"]
```

Append to `scripts/apps_script/relay.test.mjs`:

```js
test('the study runs every six hours in its own group, never behind data-commit', () => {
  const { sandbox } = load();
  const job = sandbox.SCHEDULE.find((j) => j.file === 'study.yml');
  assert.deepEqual({ ...job }, { file: 'study.yml', minutes: 360, group: 'study' });
  const newest = {
    'study.yml': { status: 'completed', created_at: ago(400) },
    'collect.yml': { status: 'in_progress', created_at: ago(5) }
  };
  const d = Object.fromEntries(sandbox.decideDispatch(sandbox.SCHEDULE, newest, NOW).map((x) => [x.file, x]));
  assert.equal(d['study.yml'].dispatch, true);
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_keep_schedule.py tests/test_run_study.py -q` and `node --test scripts/apps_script/relay.test.mjs`
Expected: FAIL — the study entry is missing; `study.yml` does not exist.

- [ ] **Step 3: Schedule it in all three schedulers**

`scripts/keep_schedule.py` — add to `SCHEDULE` after the `analyze.yml` entry, and to `GROUP_MEMBERS`:

```python
    {"file": "study.yml", "minutes": 360, "group": "study"},
```

```python
    "study": ["study.yml"],
```

`scripts/dispatch_workflows.ps1` — add to `$Schedule` after the `analyze.yml` line (add a comma to the line above it):

```powershell
    @{ File = "study.yml";   Minutes = 360;  Group = "study" }
```

`scripts/apps_script/ezekiel_relay.gs` — add to `SCHEDULE` after the `analyze.yml` entry (add a comma to the line above it):

```js
  { file: 'study.yml', minutes: 360, group: 'study' }
```

- [ ] **Step 4: Create `.github/workflows/study.yml`**

```yaml
name: Study candidates

# Keeps every identified Hyperliquid candidate under study (spec
# docs/superpowers/specs/2026-10-06-candidate-study-design.md): reads each
# wallet's new fills (orders and ledger daily), folds them into daily records
# under data/study/archive/, runs the tooling tests against him, calibrated on
# the habit census and the sub-account families, and writes data/study/.
# Its own concurrency group: scripts/run_study.py is the ONLY writer of
# data/study/, so it never queues behind data-commit and never races another
# writer for its files.
on:
  schedule:
    - cron: '17 */6 * * *'   # best-effort; the keeper/relay drives the real cadence
  workflow_dispatch:

permissions:
  contents: write
  issues: write

jobs:
  # Cron runs only: step aside rather than evict a run already waiting in the
  # concurrency group, or repeat one the keeper ran moments ago.
  gate:
    if: github.event_name == 'schedule'
    runs-on: ubuntu-latest
    timeout-minutes: 3
    permissions:
      actions: read
      contents: read
    outputs:
      run: ${{ steps.gate.outputs.run }}
    steps:
      - uses: actions/checkout@v5
        with:
          sparse-checkout: |
            scripts/keep_schedule.py
          sparse-checkout-cone-mode: false
      - uses: actions/setup-python@v6
        with:
          python-version: '3.12'
      - run: pip install requests==2.32.3
      - name: Gate the cron run
        id: gate
        env:
          GH_TOKEN: ${{ github.token }}
        run: python scripts/keep_schedule.py --gate study.yml

  study:
    runs-on: ubuntu-latest
    needs: gate
    if: ${{ !cancelled() && (needs.gate.result == 'skipped' || needs.gate.outputs.run != 'false') }}
    concurrency:
      group: study
      cancel-in-progress: false
    timeout-minutes: 35   # backstop: the study step is bounded at 25, its reads at 900 s
    env:
      PYTHONUNBUFFERED: "1"
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.12'
          cache: 'pip'
      - id: deps
        run: pip install -r requirements.txt
      - name: Study the candidates
        if: ${{ !cancelled() && steps.deps.conclusion == 'success' }}
        timeout-minutes: 25
        run: python scripts/run_study.py
      - name: Commit and push
        if: always()
        run: |
          python scripts/check_repo_size.py .
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/study/
          if git diff --cached --quiet; then echo "No changes to commit"; exit 0; fi
          git commit -m "data: study candidates [automated]"
          for i in 1 2 3 4 5; do
            if git push origin main; then exit 0; fi
            git fetch origin main || true
            if ! git rebase -X theirs --autostash FETCH_HEAD; then
              git rebase --abort || true
              echo "attempt $i: rebase could not be settled automatically"
            fi
            sleep $(( (RANDOM % 7) + 3 ))
          done
          git push origin main
      - name: Open failure issue
        if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
        run: >-
          python3 scripts/pipeline_status.py --outcome failure
          --title "[pipeline] study workflow failing"
      - name: Close failure issue
        if: success()
        env:
          GH_TOKEN: ${{ github.token }}
        run: >-
          python3 scripts/pipeline_status.py --outcome success
          --title "[pipeline] study workflow failing"
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_keep_schedule.py tests/test_run_study.py -q` and `node --test scripts/apps_script/relay.test.mjs`
Expected: all PASS (including `test_intervals_match_the_pc_dispatcher`, which pins the PS1 table to `SCHEDULE`).

- [ ] **Step 6: Lint and commit**

```bash
python -m ruff check src/ tests/ scripts/
git add .github/workflows/study.yml scripts/keep_schedule.py scripts/dispatch_workflows.ps1 scripts/apps_script/ezekiel_relay.gs scripts/apps_script/relay.test.mjs tests/test_keep_schedule.py
git commit -m "ci: study.yml every 6 hours in its own group, in all three schedulers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: The dashboard Study page

**Files:**
- Create: `dashboard/src/lib/study.js`, `dashboard/src/lib/study.test.js`, `dashboard/src/routes/study/+page.svelte`
- Modify: `dashboard/src/lib/api.js` (two fetchers), `dashboard/src/lib/ui/Icon.svelte` (a `study` icon), `dashboard/src/routes/+layout.svelte` (nav entry after Roster)

**Interfaces:**
- Consumes: `data/study/latest.json` (`computed_at, panels{strangers, family_pairs, self_windows, bars}, studied, read, wallets[]{wallet, source, account_value, coverage_days, last_read_ms, families.tooling.verdict, rank}`), `data/study/wallets/<addr>.json` (`tests.T1..T3{status, judgement}`, `series{cadence, his_cadence, shares, his_shares}`, `left_ms`).
- Produces: `study.js` exports `VERDICT_LABEL`, `VERDICT_MARK`, `FAMILY_LABEL`, `HABIT_LABEL`, `familyChips(row, order)`, `studyRows(study)`, `panelStatus(study)`, `maxShare(...hists)`, `histogramPath(hist, width, height, top)`, `habitRows(dossier)`, `pct(x)`; `api.js` exports `fetchStudy()`, `fetchStudyDossier(wallet)`.

- [ ] **Step 1: Write the failing tests** — create `dashboard/src/lib/study.test.js`:

```js
// src/lib/study.test.js
// Run with: npm test (from dashboard/). Pure functions, no network, no DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
	familyChips, studyRows, panelStatus, maxShare, histogramPath, habitRows, pct
} from './study.js';

const A = '0x' + 'a'.repeat(40);
const B = '0x' + 'b'.repeat(40);
const C = '0x' + 'c'.repeat(40);

test('familyChips follow a fixed order and never hide an unknown verdict', () => {
	const row = { families: { tooling: { verdict: 'against' }, timing: { verdict: 'weird' } } };
	const chips = familyChips(row, ['tooling', 'timing', 'lifecycle']);
	assert.deepEqual(chips.map((c) => [c.family, c.mark]), [['tooling', '✗'], ['timing', '?']]);
	assert.equal(chips[1].text, 'weird');
	assert.deepEqual(familyChips(null), []);
});

test('studyRows: rank first, then size; a missing rank sorts last, never as zero', () => {
	const study = { wallets: [
		{ wallet: A, rank: 0, account_value: 9 },
		{ wallet: B, rank: null, account_value: 99 },
		{ wallet: C, rank: 1.2, account_value: 1 }
	] };
	assert.deepEqual(studyRows(study).map((r) => r.wallet), [C, A, B]);
	assert.deepEqual(studyRows(null), []);
});

test('panelStatus reads each panel against its bar', () => {
	const study = { panels: { strangers: 210, family_pairs: 12, self_windows: 8,
		bars: { strangers: 200, family_pairs: 40, self_windows: 6 } } };
	assert.deepEqual(panelStatus(study).map((p) => p.ok), [true, false, true]);
	assert.deepEqual(panelStatus({}).map((p) => p.n), [null, null, null]);
});

test('histograms share one scale and an empty one draws nothing', () => {
	const a = [0, 2, 2];
	const b = [0, 0, 4];
	assert.equal(maxShare(a, b), 1);
	assert.equal(histogramPath(a, 100, 50, 1), 'M0.0,50.0 L50.0,25.0 L100.0,25.0');
	assert.equal(histogramPath([0, 0], 100, 50, 1), '');
	assert.equal(histogramPath(null, 100, 50), '');
});

test('habitRows keep a missing share as null and pct renders it as a dash', () => {
	const rows = habitRows({ series: { shares: { ioc: 0.5 }, his_shares: { ioc: 0.988 } } });
	const ioc = rows.find((r) => r.key === 'ioc');
	assert.deepEqual([ioc.his, ioc.it], [0.988, 0.5]);
	assert.equal(rows.find((r) => r.key === 'gtc').it, null);
	assert.equal(pct(null), '—');
	assert.equal(pct(0.5), '50.0%');
	assert.equal(pct(0.0012), '0.12%');
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd dashboard && npm test`
Expected: FAIL — `Cannot find module '…/src/lib/study.js'`.

- [ ] **Step 3: Write `dashboard/src/lib/study.js`**

```js
// src/lib/study.js
// Pure helpers for the candidate study page (spec
// docs/superpowers/specs/2026-10-06-candidate-study-design.md §12). Plain ES
// module with no SvelteKit aliases, so node --test can import it.

export const VERDICT_LABEL = {
	for: 'Trades like him',
	against: 'Not his way of trading',
	mixed: 'Mixed evidence',
	neutral: 'No signal either way',
	uncalibrated: 'Not calibrated yet',
	insufficient: 'Not enough data yet'
};

export const VERDICT_MARK = {
	for: '✓', against: '✗', mixed: '±', neutral: '–', uncalibrated: '?', insufficient: '?'
};

export const FAMILY_LABEL = {
	tooling: 'Tooling', timing: 'Timing', lifecycle: 'Lifecycle', strategy: 'Strategy'
};

export const HABIT_LABEL = {
	ioc: 'IOC orders',
	frontend: 'Web-UI market orders',
	gtc: 'Resting (GTC)',
	alo: 'Post-only (ALO)',
	client_ids: 'Client order IDs',
	triggers: 'Trigger / TP / SL',
	maker: 'Maker posting',
	canceled: 'Cancelled'
};

/** One chip per family the row carries, in a fixed order. An unknown verdict renders as '?'. */
export function familyChips(row, order = ['tooling', 'timing', 'lifecycle']) {
	const families = row?.families || {};
	return order.filter((f) => families[f]).map((f) => {
		const verdict = families[f].verdict || 'insufficient';
		return { family: f, label: FAMILY_LABEL[f] || f, verdict,
			mark: VERDICT_MARK[verdict] || '?', text: VERDICT_LABEL[verdict] || verdict };
	});
}

const finite = (x) => typeof x === 'number' && Number.isFinite(x);

/** Rows by study rank, then size, then address. A missing rank sorts last (rule 6). */
export function studyRows(study) {
	const rank = (r) => (finite(r.rank) ? r.rank : -Infinity);
	const value = (r) => (finite(r.account_value) ? r.account_value : -Infinity);
	return [...(study?.wallets || [])].sort((a, b) =>
		(rank(b) - rank(a)) || (value(b) - value(a)) || String(a.wallet).localeCompare(String(b.wallet)));
}

/** Each panel against its pre-registered bar. */
export function panelStatus(study) {
	const p = study?.panels || {};
	const bars = p.bars || {};
	return [
		{ name: 'Strangers (habit census)', n: p.strangers ?? null, need: bars.strangers ?? 200 },
		{ name: 'Same-operator pairs (sub-account families)', n: p.family_pairs ?? null, need: bars.family_pairs ?? 40 },
		{ name: 'His own months', n: p.self_windows ?? null, need: bars.self_windows ?? 6 }
	].map((r) => ({ ...r, ok: finite(r.n) && r.n >= r.need }));
}

function shares(hist) {
	const counts = Array.isArray(hist) ? hist : [];
	const total = counts.reduce((a, b) => a + b, 0);
	return total ? counts.map((c) => c / total) : [];
}

/** The largest single-bin share across histograms, so overlays share one scale. */
export function maxShare(...hists) {
	return Math.max(0, ...hists.flatMap((h) => shares(h)));
}

/** An SVG path for a histogram as shares, scaled so `top` reaches the top edge. */
export function histogramPath(hist, width, height, top = null) {
	const s = shares(hist);
	if (!s.length) return '';
	const ceiling = top || Math.max(...s) || 1;
	const step = width / Math.max(1, s.length - 1);
	return s.map((v, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)},${(height - (v / ceiling) * height).toFixed(1)}`).join(' ');
}

/** Him against this wallet, one row per habit; a share never read stays null. */
export function habitRows(dossier) {
	const it = dossier?.series?.shares || {};
	const his = dossier?.series?.his_shares || {};
	return Object.keys(HABIT_LABEL).map((key) => ({
		key, label: HABIT_LABEL[key],
		his: finite(his[key]) ? his[key] : null,
		it: finite(it[key]) ? it[key] : null
	}));
}

export function pct(x) {
	if (!finite(x)) return '—';
	return `${(x * 100).toFixed(x > 0 && x < 0.01 ? 2 : 1)}%`;
}
```

- [ ] **Step 4: Add the fetchers** — append to `dashboard/src/lib/api.js`:

```js
/** The candidate study's summary (data/study/latest.json), or null. */
export async function fetchStudy() {
	return fetchJSON('data/study/latest.json');
}

/** One studied wallet's dossier, or null for anything that is not an address. */
export async function fetchStudyDossier(wallet) {
	const w = String(wallet || '').toLowerCase();
	if (!/^0x[0-9a-f]{40}$/.test(w)) return null;
	return fetchJSON(`data/study/wallets/${w}.json`);
}
```

- [ ] **Step 5: Add the icon and the nav entry**

In `dashboard/src/lib/ui/Icon.svelte`, add to `PATHS` (Lucide "search" geometry):

```js
		study: 'M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0ZM21 21l-4.3-4.3',
```

In `dashboard/src/routes/+layout.svelte`, in the `Hunt` group, after the Roster item:

```js
			{ href: `${base}/study`, label: 'Study', icon: 'study' },
```

- [ ] **Step 6: Write `dashboard/src/routes/study/+page.svelte`**

```svelte
<script>
	import { onMount } from 'svelte';
	import { fetchStudy, fetchStudyDossier, formatUSD, formatTime } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';
	import {
		familyChips, studyRows, panelStatus, habitRows, histogramPath, maxShare, pct,
		VERDICT_LABEL
	} from '$lib/study.js';

	let study = null;
	let loading = true;
	let open = null;
	let dossier = null;
	let dossierLoading = false;

	onMount(async () => {
		study = await fetchStudy();
		loading = false;
	});

	async function toggle(wallet) {
		if (open === wallet) {
			open = null;
			dossier = null;
			return;
		}
		open = wallet;
		dossier = null;
		dossierLoading = true;
		const doc = await fetchStudyDossier(wallet);
		if (open === wallet) {
			dossier = doc;
			dossierLoading = false;
		}
	}

	$: rows = studyRows(study);
	$: panels = panelStatus(study);
	$: top = dossier ? maxShare(dossier.series?.cadence, dossier.series?.his_cadence) : 0;
</script>

<svelte:head><title>Study · Ezekiel</title></svelte:head>

<h1>Candidate study</h1>
<p class="lede">
	Every identified Hyperliquid candidate under continuous study, compared with how he makes
	orders. A verdict waits for its panels to be calibrated; until then the raw numbers are
	shown and nothing is called for or against. Evidence against him never changes a tier.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else if !study}
	<p class="text-muted">
		No study yet. It is written by <code>scripts/run_study.py</code> (study.yml, every 6 hours).
	</p>
{:else}
	<div class="panels">
		{#each panels as p}
			<span class="badge {p.ok ? 'badge-cyan' : 'badge-grey'}" title="Needs {p.need}">
				{p.name}: {p.n ?? '—'} / {p.need}
			</span>
		{/each}
	</div>

	<table>
		<thead>
			<tr>
				<th>Wallet</th>
				<th>Why studied</th>
				<th class="num">HL value</th>
				<th>Tooling</th>
				<th class="num">Covered days</th>
				<th>Last read</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.wallet)}
				<tr class="row" on:click={() => toggle(r.wallet)}>
					<td><Addr address={r.wallet} stopPropagation /></td>
					<td>{r.source}</td>
					<td class="num">{formatUSD(r.account_value)}</td>
					<td>
						{#each familyChips(r, ['tooling']) as c}
							<span class="chip v-{c.verdict}" title={c.text}>{c.mark} {c.text}</span>
						{/each}
					</td>
					<td class="num">{r.coverage_days ?? '—'}</td>
					<td>{r.last_read_ms ? formatTime(r.last_read_ms) : '—'}</td>
				</tr>
				{#if open === r.wallet}
					<tr class="detail">
						<td colspan="6">
							{#if dossierLoading}
								<p class="text-muted">Loading the dossier…</p>
							{:else if !dossier}
								<p class="text-muted">No dossier for this wallet yet.</p>
							{:else}
								{#if dossier.left_ms}
									<p class="caveat">Not studied since {formatTime(dossier.left_ms)}.</p>
								{/if}
								<div class="grid">
									<section>
										<h3>How orders are made</h3>
										<table class="habits">
											<thead>
												<tr><th></th><th class="num">Him</th><th class="num">This wallet</th></tr>
											</thead>
											<tbody>
												{#each habitRows(dossier) as h}
													<tr><td>{h.label}</td><td class="num">{pct(h.his)}</td><td class="num">{pct(h.it)}</td></tr>
												{/each}
											</tbody>
										</table>
									</section>
									<section>
										<h3>Slicing rhythm</h3>
										<svg viewBox="0 0 300 120" class="rhythm" role="img"
											aria-label="Gap between slices: his and this wallet's, 0 to 5 seconds">
											<path d={histogramPath(dossier.series?.his_cadence, 300, 110, top)} class="his" />
											<path d={histogramPath(dossier.series?.cadence, 300, 110, top)} class="it" />
										</svg>
										<p class="legend"><span class="his">— him</span> <span class="it">— this wallet</span> · 0 to 5 s between slices</p>
									</section>
								</div>
								<ul class="tests">
									{#each ['T1', 'T2', 'T3'] as t}
										{#if dossier.tests?.[t]}
											<li>
												<strong>{t}</strong> {dossier.tests[t].status}
												{#if dossier.tests[t].judgement}
													· {VERDICT_LABEL[dossier.tests[t].judgement.status] || dossier.tests[t].judgement.status}
													{#if dossier.tests[t].judgement.stranger_n}
														· strangers matching {dossier.tests[t].judgement.stranger_k} of {dossier.tests[t].judgement.stranger_n}
													{/if}
												{/if}
											</li>
										{/if}
									{/each}
								</ul>
							{/if}
						</td>
					</tr>
				{/if}
			{/each}
		</tbody>
	</table>

	<p class="text-muted">
		{study.studied} studied · {study.read} read this run · built {formatTime(study.computed_at)}
	</p>
{/if}

<style>
	.lede { max-width: 60rem; }
	.panels { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0; }
	table { width: 100%; border-collapse: collapse; }
	th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }
	th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
	.row { cursor: pointer; }
	.row:hover { background: rgba(255, 255, 255, 0.03); }
	.chip {
		font-size: 0.75rem; padding: 0.1rem 0.45rem; border-radius: 0.25rem;
		background: rgba(255, 255, 255, 0.06); white-space: nowrap;
	}
	.v-for { color: var(--accent-green); background: var(--tint-green); }
	.v-against { color: var(--accent-red); background: var(--tint-red); }
	.v-mixed { color: var(--accent-yellow); }
	.v-neutral, .v-uncalibrated, .v-insufficient { color: var(--text-muted); }
	.detail td { background: rgba(255, 255, 255, 0.02); }
	.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
	.habits td, .habits th { padding: 0.2rem 0.4rem; font-size: 0.85rem; }
	.rhythm { width: 100%; max-width: 420px; height: auto; }
	.rhythm path { fill: none; stroke-width: 1.5; }
	.rhythm .his, .legend .his { stroke: var(--accent-cyan); color: var(--accent-cyan); }
	.rhythm .it, .legend .it { stroke: var(--accent-yellow); color: var(--accent-yellow); }
	.legend { font-size: 0.8rem; opacity: 0.8; }
	.tests { margin: 0.5rem 0 0 1.1rem; font-size: 0.85rem; }
	.caveat { font-size: 0.85rem; opacity: 0.75; }
	h3 { margin: 0.25rem 0 0.5rem; font-size: 0.95rem; }
</style>
```

- [ ] **Step 7: Run the dashboard tests and build**

Run: `cd dashboard && npm test && npm run build`
Expected: all tests PASS (the existing ones plus `study.test.js`); the build completes without Svelte errors or warnings about unknown props.

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/lib/study.js dashboard/src/lib/study.test.js dashboard/src/lib/api.js dashboard/src/lib/ui/Icon.svelte dashboard/src/routes/+layout.svelte dashboard/src/routes/study/+page.svelte
git commit -m "feat(dashboard): Study page with habits, rhythm overlay and panel status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Verify against live data before merging

**Files:** none changed (unless the dry run finds a defect, which then gets its own test and fix).

- [ ] **Step 1: Run the full suites**

```bash
python -m pytest -q
python -m ruff check src/ tests/ scripts/
node --test scripts/apps_script/relay.test.mjs
cd dashboard && npm test && npm run build && cd ..
```

Expected: everything PASSES. Report the counts.

- [ ] **Step 2: Dry run against live Hyperliquid in a scratch data directory** (never the real `data/`)

```bash
python - <<'EOF'
import shutil, tempfile
from pathlib import Path
scratch = Path(tempfile.mkdtemp(prefix="study-dry-")) / "data"
for name in ("roster", "hl_surface", "execution_program", "dormancy", "newborn", "tape",
             "provenance", "fills", "orders", "ledger"):
    src = Path("data") / name
    if src.exists():
        shutil.copytree(src, scratch / name)
print(scratch)
EOF
python scripts/run_study.py --data-dir <the printed path> --max-wallets 8 --read-seconds 240
```

Expected output: `[study] 8 studied, N read, … panels {…}`. A first read costs up to five pages per busy wallet, so a 240 s budget usually reads 7 of the 8 and ends `stopped=True` (the rest go first next run). Then inspect `<path>/study/latest.json`:
- the eight wallets include the decayed leads (`0xdd53c529…`, `0x5b5d5120…`, `0xb83de012…`);
- each wallet read has day records under `<path>/study/archive/<wallet>/` (busy bots ≈ 18 KB/day, others 1–6 KB);
- their T1 raw shares match the 2026-10-06 measurement (spec §1): the maker bots read `style: MAKER`, `client_ids: 1.0`, `maker: 1.0` (measured in the plan's own dry run: `0x5b5d5120…`, `0xb83de012…`, `0x936cf4fb…`, `0xb2f7374b…`);
- every tooling verdict reads `uncalibrated` or `insufficient` (the census has not yet accumulated 200 strangers) — the expected state on day one;
- `reference.style` is `PROGRAM_IOC5` with all flags false, and `panels.self_windows` is 8.

Run the same command a second time: no day wholly before the previous cursor changes (count orders and fills per day file before and after), later days only grow, and every `state.json` fills cursor moves forward. The plan's own dry run measured 34 of 34 earlier days unchanged and 7 of 7 cursors advanced.

- [ ] **Step 3: Open the PR.** Summarise the dry-run numbers in the PR body, end it with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`, and merge only after review. After merge, confirm the first two production `study.yml` runs succeed and that `data/study/latest.json` lists ~40 wallets (spec §14 acceptance 2). Phase 2 starts only after Phase 1 has run in production.

---

## Self-review notes (for the reviewer of this plan)

- **Spec coverage, Phases 0–1:** §3 → Task 1; §5 study set → Task 4 (the reference panel is Phase 2); §6.1–6.4 → Tasks 2, 3, 5, 10; §7 T1–T3 → Task 6; §8.1 strangers/families/self → Tasks 8, 9; §8.2 bars → Task 7; §8.3 → Task 9; §9 roster → Task 11; §10 outputs → Tasks 9, 10; §13 scheduling → Task 12; §12 dashboard (Study page) → Task 13; §14 → Task 14. Phase 2/3 sections (§7 T4–T8, reference panel, `coactivity`, alerts, feed health, phone) get their own plans after Phase 1 runs in production; the daily records already store what they need.
- **Validated while writing:** every task's code and tests were extracted from this document into a throwaway copy of the repository and run with the repo's `.venv` (pytest, ruff) and the dashboard's `node_modules` (`npm test`, `npm run build`), and `run_study.py` was dry-run against live Hyperliquid on a scratch copy of `data/`. Defects found that way were fixed in this text before it was committed.
- **Refinements recorded in the spec on 2026-10-06 with this plan:** *for* is judged at the looser of the candidate's level and the same-operator median (otherwise a closer match could fail where a looser one passes); decisions are stored as `[t, coin, side, kind]` and coins as `px_sum`/`px_n` (a median is not additive across batches); `against` also needs 200 strangers; sealed-month rolling happens inside `run_study.py` rather than `compact_data.py` (one writer per file); orders are counted as soon as they are read rather than an hour later (the dry run showed a busy bot's newest 2,000 orders are all minutes old, so its habits were never recorded).
