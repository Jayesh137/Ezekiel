# Universal Fund Tracing — Phase 1 (Line of Sight) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-chain, single-asset, 1,000-row-truncated, spam-flooded transfer collection with a multi-chain, multi-asset, fully paginated substrate that quarantines address poisoning and names exchanges correctly.

**Architecture:** A new bounded ingestion package `src/chain/` produces normalised, USD-valued, spam-classified transfer records to `data/transfers/{chain}/`. The existing scoring and classification brain in `src/transfer_graph.py` is preserved exactly and reads the new substrate through a thin adapter. Pure logic (pagination walking, spam classification, valuation, label resolution) is separated from IO so every branch is testable without network.

**Tech Stack:** Python 3.12, `requests`, pytest, ruff. Etherscan V2 (one API key, many chains via `chainid`). No new runtime dependencies.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-28-universal-fund-tracing-phase1-design.md`. Read it before Task 1.
- **No grading rule changes.** No classification threshold, confidence weight, or alert gate may change in this phase. Phase 1 changes what the system *sees*, not what it concludes.
- **A missing price yields `None`, never `0.0`.** Test-covered in Task 5.
- **An empty sweep and a failed sweep must never serialise identically.** Test-covered in Task 8.
- **Quarantined records never become graph edges and never consume expansion budget.**
- **An address with bytecode can never be graded `MIGRATION_CANDIDATE`.**
- Ruff config: line-length 100, target py312, rules `F,E,I,UP,B,PERF`, `E501` ignored. Run `ruff check src tests` before every commit.
- Tests must never write to the real `data/` — `tests/conftest.py` enforces this globally and fails the suite if violated.
- Follow the established pure-core / IO-wrapper split (see `src/linkage.py`: `compute_linkage` pure, `check_candidate` does IO).
- Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Work happens on branch `feat/universal-fund-tracing` (already created).

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `src/chain/__init__.py` | package marker, empty |
| `src/chain/chains.py` | chain registry: name ↔ id ↔ enablement. No IO. |
| `src/chain/budget.py` | `CallBudget` — call count + wall clock ceiling. No IO. |
| `src/chain/pagination.py` | `walk_blocks` — block-range walker over an injected fetch. Pure. |
| `src/chain/client.py` | Etherscan V2 reader: three record kinds, activity probe, bytecode. IO. |
| `src/chain/assets.py` | token registry, USD valuation, price cache. Pure core + IO wrapper. |
| `src/chain/spam.py` | `classify_spam`, `is_lookalike`, `derive_real_counterparties`. Pure. |
| `src/chain/labels.py` | entity registry, bytecode check, deposit-address inference. Pure core + IO wrapper. |
| `src/chain/collect.py` | `sweep_wallet` orchestration, persistence, health reporting. IO. |
| `data/labels/entities.json` | curated entity registry, versioned in git |
| `tests/fixtures/poisoning_live.json` | the real 11-of-14 poisoning case |
| `tests/test_chain_chains.py`, `test_chain_budget.py`, `test_chain_pagination.py`, `test_chain_client.py`, `test_chain_assets.py`, `test_chain_spam.py`, `test_chain_labels.py`, `test_chain_collect.py`, `test_chain_graph_adapter.py` | one test module per unit |

**Modify:**

| File | Change |
|---|---|
| `src/utils.py:55-75` | `etherscan_get` gains optional `chain_id=` |
| `config.json` | `chains`, `collection`, `spam`, `assets` blocks |
| `src/transfer_graph.py` | `normalise_transfer_record`, `collect_known_edges` reader, `expand_frontier` swap, `known_services` from labels |
| `src/linkage.py:105-125` | `get_outbound_usdc_addresses` → all-chain `get_outbound_addresses` |
| `src/tracer.py` | reads the substrate; outputs unchanged |
| `.github/workflows/backfill.yml` | full-history sweep mode |
| `README.md` | document the substrate |

---

## Task 1: Chain registry and config

**Files:**
- Create: `src/chain/__init__.py`, `src/chain/chains.py`
- Modify: `config.json`
- Test: `tests/test_chain_chains.py`

**Interfaces:**
- Consumes: `src.utils.load_config`
- Produces: `enabled_chains(config) -> list[dict]`, `chain_by_name(name, config) -> dict`, `DEFAULT_CHAINS`. A chain dict is `{"name": str, "chain_id": int, "native": str, "enabled": bool, "priority": int}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_chains.py
import pytest

from src.chain import chains


def test_defaults_include_the_six_phase_one_chains():
    names = {c["name"] for c in chains.DEFAULT_CHAINS}
    assert names == {"arbitrum", "ethereum", "base", "optimism", "polygon", "bsc"}
    assert {c["name"]: c["chain_id"] for c in chains.DEFAULT_CHAINS}["arbitrum"] == 42161


def test_enabled_chains_falls_back_to_defaults_when_config_is_silent():
    got = chains.enabled_chains({})
    assert [c["name"] for c in got] == [c["name"] for c in chains.DEFAULT_CHAINS]


def test_enabled_chains_filters_disabled_and_sorts_by_priority():
    cfg = {"chains": [
        {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": True, "priority": 9},
        {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0},
        {"name": "bsc", "chain_id": 56, "native": "BNB", "enabled": False, "priority": 1},
    ]}
    assert [c["name"] for c in chains.enabled_chains(cfg)] == ["arbitrum", "base"]


def test_chain_by_name_is_case_insensitive_and_raises_on_unknown():
    cfg = {"chains": list(chains.DEFAULT_CHAINS)}
    assert chains.chain_by_name("ARBITRUM", cfg)["chain_id"] == 42161
    with pytest.raises(KeyError):
        chains.chain_by_name("solana", cfg)


def test_a_chain_entry_missing_required_keys_is_rejected_loudly():
    with pytest.raises(ValueError):
        chains.enabled_chains({"chains": [{"name": "base"}]})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_chains.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/chain/__init__.py
```

```python
# src/chain/chains.py
"""Which chains we read, and how they are named.

One place knows chain names, ids and enablement, so every other module takes a
chain name and never a magic number. Etherscan V2 serves every chain here from
the same API key by varying `chainid`, which is why widening coverage costs no
new credentials.
"""

REQUIRED_KEYS = ("name", "chain_id", "native", "enabled", "priority")

DEFAULT_CHAINS = [
    {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0},
    {"name": "ethereum", "chain_id": 1, "native": "ETH", "enabled": True, "priority": 1},
    {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": True, "priority": 2},
    {"name": "optimism", "chain_id": 10, "native": "ETH", "enabled": True, "priority": 3},
    {"name": "polygon", "chain_id": 137, "native": "POL", "enabled": True, "priority": 4},
    {"name": "bsc", "chain_id": 56, "native": "BNB", "enabled": True, "priority": 5},
]


def _validated(entry: dict) -> dict:
    missing = [k for k in REQUIRED_KEYS if k not in entry]
    if missing:
        raise ValueError(f"chain entry {entry!r} is missing {missing}")
    return entry


def enabled_chains(config: dict) -> list[dict]:
    """Configured chains that are switched on, strongest priority first.

    A config written before this key existed simply omits it, so the defaults
    apply and Arbitrum keeps working exactly as before.
    """
    entries = config.get("chains") or DEFAULT_CHAINS
    kept = [_validated(e) for e in entries]
    return sorted((e for e in kept if e["enabled"]), key=lambda e: e["priority"])


def chain_by_name(name: str, config: dict) -> dict:
    """The chain entry for `name`, enabled or not. Raises KeyError if unknown."""
    wanted = (name or "").lower()
    for entry in (config.get("chains") or DEFAULT_CHAINS):
        if entry["name"].lower() == wanted:
            return _validated(entry)
    raise KeyError(f"unknown chain: {name!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain_chains.py -v`
Expected: 5 passed

- [ ] **Step 5: Add the config blocks**

Add these keys to `config.json` at the top level, after `"known_service_addresses"`:

```json
  "chains": [
    {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": true, "priority": 0},
    {"name": "ethereum", "chain_id": 1, "native": "ETH", "enabled": true, "priority": 1},
    {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": true, "priority": 2},
    {"name": "optimism", "chain_id": 10, "native": "ETH", "enabled": true, "priority": 3},
    {"name": "polygon", "chain_id": 137, "native": "POL", "enabled": true, "priority": 4},
    {"name": "bsc", "chain_id": 56, "native": "BNB", "enabled": true, "priority": 5}
  ],
  "collection": {
    "page_size": 1000,
    "max_pages_per_kind": 50,
    "max_calls_per_run": 2500,
    "time_budget_seconds": 420,
    "probe_frontier_chains": true
  },
  "spam": {
    "lookalike_prefix": 4,
    "lookalike_suffix": 4
  },
  "assets": {
    "deposit_forward_ratio": 0.95,
    "deposit_window_hours": 24
  }
```

- [ ] **Step 6: Verify config parses and the registry reads it**

Run: `python -c "from src.chain.chains import enabled_chains; from src.utils import load_config; print([c['name'] for c in enabled_chains(load_config())])"`
Expected: `['arbitrum', 'ethereum', 'base', 'optimism', 'polygon', 'bsc']`

- [ ] **Step 7: Lint and commit**

```bash
ruff check src tests
git add src/chain/__init__.py src/chain/chains.py tests/test_chain_chains.py config.json
git commit -m "feat(chain): chain registry for multi-chain collection

Etherscan V2 reaches every chain here from the existing single API key by
varying chainid, so widening from Arbitrum-only costs no new credentials.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Call budget

**Files:**
- Create: `src/chain/budget.py`
- Test: `tests/test_chain_budget.py`

**Interfaces:**
- Consumes: nothing
- Produces: `CallBudget(max_calls: int, seconds: float, clock=time.monotonic)` with `spend(n=1) -> None`, `can_spend(n=1) -> bool`, `calls_used: int`, `remaining_calls() -> int`, `exhausted_reason() -> str | None`; exception `BudgetExhausted`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_budget.py
import pytest

from src.chain.budget import BudgetExhausted, CallBudget


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_spending_counts_calls():
    b = CallBudget(max_calls=3, seconds=100, clock=FakeClock())
    b.spend()
    b.spend()
    assert b.calls_used == 2
    assert b.remaining_calls() == 1
    assert b.exhausted_reason() is None


def test_exhausting_calls_raises_and_names_the_reason():
    b = CallBudget(max_calls=1, seconds=100, clock=FakeClock())
    b.spend()
    assert not b.can_spend()
    assert b.exhausted_reason() == "call_budget"
    with pytest.raises(BudgetExhausted):
        b.spend()


def test_deadline_exhausts_independently_of_call_count():
    clock = FakeClock()
    b = CallBudget(max_calls=100, seconds=10, clock=clock)
    b.spend()
    clock.t = 11.0
    assert not b.can_spend()
    assert b.exhausted_reason() == "time_budget"
    with pytest.raises(BudgetExhausted):
        b.spend()


def test_calls_used_survives_exhaustion_so_partial_work_is_reportable():
    b = CallBudget(max_calls=1, seconds=100, clock=FakeClock())
    b.spend()
    with pytest.raises(BudgetExhausted):
        b.spend()
    assert b.calls_used == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain.budget'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/chain/budget.py
"""A hard ceiling on API calls and wall clock for one collection run.

The collection jobs run under a 10-minute GitHub Actions timeout against a
rate-limited free API tier. Every reader takes a budget and checks it before
spending, so a wallet with a very long history degrades into a partial sweep
that reports where it stopped, rather than a cancelled job that reports
nothing. `calls_used` deliberately survives exhaustion: the diagnostics are the
point.
"""

import time


class BudgetExhausted(RuntimeError):
    """Raised when a caller tries to spend past the ceiling."""


class CallBudget:
    __slots__ = ("max_calls", "seconds", "_clock", "_started", "calls_used")

    def __init__(self, max_calls: int, seconds: float, clock=time.monotonic):
        self.max_calls = int(max_calls)
        self.seconds = float(seconds)
        self._clock = clock
        self._started = clock()
        self.calls_used = 0

    def elapsed(self) -> float:
        return self._clock() - self._started

    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def exhausted_reason(self) -> str | None:
        if self.calls_used >= self.max_calls:
            return "call_budget"
        if self.elapsed() >= self.seconds:
            return "time_budget"
        return None

    def can_spend(self, n: int = 1) -> bool:
        if self.elapsed() >= self.seconds:
            return False
        return self.calls_used + n <= self.max_calls

    def spend(self, n: int = 1) -> None:
        if not self.can_spend(n):
            raise BudgetExhausted(self.exhausted_reason() or "call_budget")
        self.calls_used += n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain_budget.py -v`
Expected: 4 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/chain/budget.py tests/test_chain_budget.py
git commit -m "feat(chain): call and wall-clock budget for collection runs

Partial sweeps must report where they stopped rather than be cancelled, so
calls_used survives exhaustion and the reason is named.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Block-range pagination walker

This is the fix for the 1,000-row ceiling (spec L3). Pure — no network.

**Files:**
- Create: `src/chain/pagination.py`
- Test: `tests/test_chain_pagination.py`

**Interfaces:**
- Consumes: nothing
- Produces: `walk_blocks(fetch, start_block, *, page_size=1000, max_pages=50, key=default_row_key) -> WalkResult`. `WalkResult` is a `NamedTuple(rows: list[dict], last_block: int, pages: int, truncated: bool, possible_gaps: list[int])`. `fetch(start_block: int, page_size: int) -> list[dict]`; each row must carry `blockNumber`. Also exports `default_row_key(row) -> tuple`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_pagination.py
from src.chain.pagination import walk_blocks


def row(block, h, log=""):
    return {"blockNumber": str(block), "hash": h, "logIndex": log}


def test_a_short_page_ends_the_walk():
    pages = [[row(10, "a"), row(11, "b")]]
    got = walk_blocks(lambda s, n: pages.pop(0), 0, page_size=5)
    assert [r["hash"] for r in got.rows] == ["a", "b"]
    assert got.pages == 1
    assert got.last_block == 11
    assert got.truncated is False
    assert got.possible_gaps == []


def test_a_full_page_advances_the_start_block_and_continues():
    calls = []

    def fetch(start, n):
        calls.append(start)
        if start == 0:
            return [row(5, "a"), row(9, "b")]
        return [row(12, "c")]

    got = walk_blocks(fetch, 0, page_size=2)
    assert calls == [0, 9]                       # resumed from the highest block seen
    assert [r["hash"] for r in got.rows] == ["a", "b", "c"]
    assert got.pages == 2
    assert got.last_block == 12


def test_boundary_duplicates_collapse_by_row_key():
    def fetch(start, n):
        if start == 0:
            return [row(5, "a"), row(9, "b")]
        return [row(9, "b"), row(12, "c")]       # b repeats across the boundary

    got = walk_blocks(fetch, 0, page_size=2)
    assert [r["hash"] for r in got.rows] == ["a", "b", "c"]


def test_same_block_stall_advances_by_one_and_records_a_possible_gap():
    """A single block holding more than page_size rows would otherwise loop
    forever on the same start_block, or silently drop the overflow."""
    calls = []

    def fetch(start, n):
        calls.append(start)
        if start == 0:
            return [row(7, "a"), row(7, "b")]    # full page, all one block
        return []

    got = walk_blocks(fetch, 0, page_size=2)
    assert calls == [0, 8]                       # advanced past the stalled block
    assert got.possible_gaps == [7]


def test_max_pages_truncates_and_says_so():
    def fetch(start, n):
        return [row(start + 1, f"h{start}"), row(start + 2, f"i{start}")]

    got = walk_blocks(fetch, 0, page_size=2, max_pages=3)
    assert got.pages == 3
    assert got.truncated is True


def test_rows_without_a_usable_block_number_do_not_stall_the_walk():
    def fetch(start, n):
        if start == 0:
            return [{"hash": "a", "blockNumber": "not-a-number"}, row(4, "b")]
        return []

    got = walk_blocks(fetch, 0, page_size=2)
    assert [r["hash"] for r in got.rows] == ["a", "b"]
    assert got.last_block == 4


def test_an_empty_first_page_returns_cleanly():
    got = walk_blocks(lambda s, n: [], 100, page_size=10)
    assert got.rows == []
    assert got.pages == 1
    assert got.last_block == 100
    assert got.truncated is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_pagination.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain.pagination'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/chain/pagination.py
"""Walk an address's history by block range instead of by page number.

Etherscan caps any single query at 10,000 results, so `page=N` paging hits a
wall and silently stops. The collection this replaces asked for `page=1,
offset=1000, sort=desc` exactly once, which is why data/l1_transactions holds
exactly 1000 records and no history older than the newest 1000 transfers —
905 of which were address-poisoning dust.

Walking forward by block instead has no ceiling: ask from `start`, take the
highest block returned, ask again. The overlap this creates at each boundary is
absorbed by deduplication on a row key.

Pure: `fetch` is injected, so every branch — including the pathological
single-block stall — is testable without a network.
"""

from typing import Callable, NamedTuple


class WalkResult(NamedTuple):
    rows: list[dict]
    last_block: int
    pages: int
    truncated: bool
    possible_gaps: list[int]


def default_row_key(row: dict) -> tuple:
    """Identity of an Etherscan row across the three record kinds.

    tokentx is unique on (hash, logIndex); txlist on hash alone; txlistinternal
    on (hash, traceId). Combining all three is unique for every kind without
    needing to know which kind produced the row.
    """
    return (
        str(row.get("hash", "")),
        str(row.get("logIndex", "")),
        str(row.get("traceId", "")),
    )


def _block_of(row: dict) -> int | None:
    try:
        return int(row.get("blockNumber"))
    except (TypeError, ValueError):
        return None


def walk_blocks(fetch: Callable[[int, int], list[dict]], start_block: int, *,
                page_size: int = 1000, max_pages: int = 50,
                key: Callable[[dict], tuple] = default_row_key) -> WalkResult:
    """Collect every row from `start_block` forward, in page_size chunks."""
    rows: list[dict] = []
    seen: set[tuple] = set()
    gaps: list[int] = []
    start = int(start_block)
    highest = start
    pages = 0
    truncated = False

    while pages < max_pages:
        page = fetch(start, page_size)
        pages += 1

        for row in page:
            k = key(row)
            if k in seen:
                continue
            seen.add(k)
            rows.append(row)
            block = _block_of(row)
            if block is not None and block > highest:
                highest = block

        if len(page) < page_size:
            break

        page_blocks = [b for b in (_block_of(r) for r in page) if b is not None]
        next_start = max(page_blocks) if page_blocks else start
        if next_start <= start:
            # One block holds at least a full page. Staying here loops forever;
            # stepping over it is the only way forward, and the step is recorded
            # because rows in that block beyond page_size are unreachable.
            gaps.append(start)
            next_start = start + 1
        start = next_start
    else:
        truncated = True

    return WalkResult(rows=rows, last_block=highest, pages=pages,
                      truncated=truncated, possible_gaps=gaps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain_pagination.py -v`
Expected: 7 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/chain/pagination.py tests/test_chain_pagination.py
git commit -m "feat(chain): block-range pagination that cannot truncate

Replaces the single page=1,offset=1000,sort=desc request that capped stored
history at 1000 rows. Walks forward by block with dedup across boundaries, and
steps over a block that holds more than one page rather than looping on it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Multi-chain Etherscan client

**Files:**
- Modify: `src/utils.py:55-75`
- Create: `src/chain/client.py`
- Test: `tests/test_chain_client.py`

**Interfaces:**
- Consumes: `walk_blocks`, `WalkResult`, `CallBudget`, `BudgetExhausted`, `src.utils.etherscan_get`
- Produces: `ACTIONS: dict[str, str]`, `fetch_kind(address, chain, kind, start_block, budget, *, page_size=1000, max_pages=50) -> tuple[WalkResult, str | None]` (rows, error), `probe_activity(address, chain, budget) -> tuple[bool, str | None]` (active, error), `fetch_code(address, chain, budget) -> str | None`. `kind` is one of `"erc20" | "native" | "internal"`.

> **Every reader returns (value, error).** A probe that returns a bare `False` cannot distinguish "this address never transacted here" from "we could not read this chain", and a caller that treats the first meaning as the second marks a chain empty when the system is simply blind to it. That is the exact silent all-clear this phase exists to prevent, so the error channel is part of the signature, not an optional extra.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_client.py
import pytest

from src.chain import client
from src.chain.budget import CallBudget

ARB = {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0}


def budget(calls=100):
    return CallBudget(max_calls=calls, seconds=1000, clock=lambda: 0.0)


def test_each_kind_maps_to_its_etherscan_action():
    assert client.ACTIONS == {
        "erc20": "tokentx", "native": "txlist", "internal": "txlistinternal"}


def test_fetch_kind_passes_the_chain_id_and_walks_pages(monkeypatch):
    seen = []

    def fake_get(params, chain_id=None):
        seen.append((params["action"], params["startblock"], chain_id))
        if params["startblock"] == 0:
            return {"status": "1", "result": [
                {"blockNumber": "5", "hash": "a"}, {"blockNumber": "9", "hash": "b"}]}
        return {"status": "1", "result": [{"blockNumber": "12", "hash": "c"}]}

    monkeypatch.setattr(client, "etherscan_get", fake_get)
    result, error = client.fetch_kind("0xabc", ARB, "erc20", 0, budget(), page_size=2)

    assert error is None
    assert [r["hash"] for r in result.rows] == ["a", "b", "c"]
    assert seen == [("tokentx", 0, 42161), ("tokentx", 9, 42161)]


def test_no_transactions_found_is_an_empty_result_not_an_error(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "No transactions found", "result": []})
    result, error = client.fetch_kind("0xabc", ARB, "native", 0, budget())
    assert result.rows == []
    assert error is None


def test_a_real_api_error_is_reported_and_does_not_look_like_empty(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "Max rate limit reached", "result": []})
    result, error = client.fetch_kind("0xabc", ARB, "native", 0, budget())
    assert result.rows == []
    assert error == "Max rate limit reached"


def test_a_non_list_result_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "1", "result": "Invalid API Key"})
    result, error = client.fetch_kind("0xabc", ARB, "erc20", 0, budget())
    assert result.rows == []
    assert error and "Invalid API Key" in error


def test_exhausted_budget_stops_the_walk_and_reports_it(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "1", "result": [{"blockNumber": "5", "hash": "a"},
                                  {"blockNumber": "9", "hash": "b"}]})
    b = budget(calls=1)
    result, error = client.fetch_kind("0xabc", ARB, "erc20", 0, b, page_size=2)
    assert [r["hash"] for r in result.rows] == ["a", "b"]   # first page retained
    assert error == "budget_exhausted:call_budget"
    assert b.calls_used == 1


def test_probe_activity_costs_one_call_and_answers_yes_or_no(monkeypatch):
    calls = []

    def fake_get(params, chain_id=None):
        calls.append(params)
        return {"status": "1", "result": [{"blockNumber": "1", "hash": "a"}]}

    monkeypatch.setattr(client, "etherscan_get", fake_get)
    b = budget()
    assert client.probe_activity("0xabc", ARB, b) == (True, None)
    assert b.calls_used == 1
    assert calls[0]["offset"] == 1


def test_probe_activity_is_false_when_the_address_never_transacted(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "No transactions found", "result": []})
    assert client.probe_activity("0xabc", ARB, budget()) == (False, None)


def test_a_failed_probe_reports_an_error_and_is_not_merely_inactive(monkeypatch):
    """The caller skips chains a probe calls inactive. If a rate-limited probe
    returned a bare False, the sweep would record 'nothing here' for a chain it
    simply could not read."""
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "Max rate limit reached", "result": []})
    active, error = client.probe_activity("0xabc", ARB, budget())
    assert active is False
    assert error == "Max rate limit reached"


def test_a_probe_with_no_budget_reports_exhaustion_rather_than_inactivity(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get",
                        lambda p, chain_id=None: {"status": "1", "result": []})
    b = CallBudget(max_calls=0, seconds=1000, clock=lambda: 0.0)
    active, error = client.probe_activity("0xabc", ARB, b)
    assert active is False
    assert error == "budget_exhausted:call_budget"


def test_fetch_code_returns_the_bytecode_string(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "jsonrpc": "2.0", "result": "0x60806040"})
    assert client.fetch_code("0xabc", ARB, budget()) == "0x60806040"


def test_fetch_code_rejects_an_error_string_in_the_result_position(monkeypatch):
    """Etherscan puts bare error strings where the payload belongs. Returned as
    bytecode, one would mark a real wallet a contract — and a contract is never
    graded a person."""
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "NOTOK", "result": "Max rate limit reached"})
    assert client.fetch_code("0xabc", ARB, budget()) is None


def test_fetch_code_returns_none_when_the_budget_is_gone(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {"result": "0x"})
    b = CallBudget(max_calls=0, seconds=1000, clock=lambda: 0.0)
    assert client.fetch_code("0xabc", ARB, b) is None


def test_etherscan_get_defaults_to_arbitrum_and_honours_an_override(monkeypatch):
    from src import utils
    seen = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "1", "result": []}

    def fake_request_get(url, params=None, timeout=None):
        seen.update(params)
        return FakeResp()

    monkeypatch.setattr(utils.requests, "get", fake_request_get)
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)

    utils.etherscan_get({"module": "account"})
    assert seen["chainid"] == 42161
    utils.etherscan_get({"module": "account"}, chain_id=8453)
    assert seen["chainid"] == 8453
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain.client'`

- [ ] **Step 3: Add the `chain_id` parameter to `etherscan_get`**

In `src/utils.py`, replace the body of `etherscan_get` (currently lines 55-75):

```python
def etherscan_get(params: dict, chain_id: int | None = None) -> dict:
    """GET from the Etherscan V2 API.

    V2 serves every supported chain from one API key by varying `chainid`, so
    `chain_id` is the only thing that changes between chains. It defaults to the
    configured Arbitrum id, which keeps every pre-existing call site behaving
    exactly as it did when the id was hardcoded.
    """
    config = load_config()
    api_key = os.environ.get("ETHERSCAN_API_KEY", "")
    base_params = {
        "chainid": chain_id if chain_id is not None else config["arbitrum_chain_id"],
        "apikey": api_key,
    }
    base_params.update(params)
    time.sleep(0.25)  # Rate limit: 5 req/sec
    try:
        resp = requests.get(
            config["etherscan_v2_base"],
            params=base_params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[etherscan] API error: {e}")
        return {"status": "0", "message": str(e), "result": []}
```

- [ ] **Step 4: Write the client**

```python
# src/chain/client.py
"""Reading transfers from Etherscan V2, on any chain, under a budget.

Three record kinds are collected per address, not one. `txlistinternal` matters
most: a contract-mediated transfer — which is what every bridge and router
emits — appears in neither `tokentx` nor `txlist`, so collecting only those two
leaves a hole exactly the shape of a bridged migration.

Every function distinguishes "this address has no such records" from "we could
not read". Those must never serialise the same way: one is knowledge, the other
is blindness.
"""

from src.chain.budget import BudgetExhausted, CallBudget
from src.chain.pagination import WalkResult, walk_blocks
from src.utils import etherscan_get

# No sys.path insert here. `src/*.py` modules carry one because they run as
# scripts; `src/chain/*.py` never do — reaching this module's body at all means
# `src.chain` already resolved.

ACTIONS = {
    "erc20": "tokentx",
    "native": "txlist",
    "internal": "txlistinternal",
}

EMPTY_MESSAGES = ("no transactions found", "no records found")


def _rows_or_error(payload: dict) -> tuple[list[dict], str | None]:
    """Split an Etherscan payload into rows and an error string."""
    result = payload.get("result")
    if payload.get("status") == "1":
        if isinstance(result, list):
            return result, None
        return [], f"unexpected result: {result!r}"
    message = str(payload.get("message", "") or "")
    if message.lower() in EMPTY_MESSAGES:
        return [], None
    if isinstance(result, str) and result:
        return [], result
    return [], message or "unknown etherscan error"


def fetch_kind(address: str, chain: dict, kind: str, start_block: int,
               budget: CallBudget, *, page_size: int = 1000,
               max_pages: int = 50) -> tuple[WalkResult, str | None]:
    """Every record of one kind for one address on one chain, from start_block.

    Returns whatever was collected plus an error string when the sweep did not
    finish. A partial result is still returned — discarding it would throw away
    real history to report a failure that is already reported.
    """
    action = ACTIONS[kind]
    error: str | None = None

    def fetch(start: int, size: int) -> list[dict]:
        nonlocal error
        if error is not None:
            return []
        try:
            budget.spend()
        except BudgetExhausted as exc:
            error = f"budget_exhausted:{exc}"
            return []
        payload = etherscan_get({
            "module": "account",
            "action": action,
            "address": address,
            "startblock": start,
            "endblock": 99999999,
            "page": 1,
            "offset": size,
            "sort": "asc",
        }, chain_id=chain["chain_id"])
        rows, err = _rows_or_error(payload)
        if err:
            error = err
        return rows

    result = walk_blocks(fetch, start_block, page_size=page_size, max_pages=max_pages)
    return result, error


def probe_activity(address: str, chain: dict, budget: CallBudget
                   ) -> tuple[bool, str | None]:
    """Has this address ever transacted on this chain? One call.

    Sweeping every frontier wallet across every chain costs six full sweeps per
    wallet; this costs one request and skips the chains that would return
    nothing.

    Returns (active, error). A caller must treat a non-None error as "we could
    not tell", never as "inactive": a failed probe that reads as an empty chain
    is the silent all-clear this phase exists to prevent.
    """
    try:
        budget.spend()
    except BudgetExhausted as exc:
        return False, f"budget_exhausted:{exc}"
    payload = etherscan_get({
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": 1,
        "sort": "asc",
    }, chain_id=chain["chain_id"])
    rows, error = _rows_or_error(payload)
    return bool(rows), error


def fetch_code(address: str, chain: dict, budget: CallBudget) -> str | None:
    """The address's deployed bytecode, or None if it could not be read.

    "0x" means an externally owned account. Anything longer is a contract, and a
    contract is never a person.
    """
    try:
        budget.spend()
    except BudgetExhausted:
        return None
    payload = etherscan_get({
        "module": "proxy",
        "action": "eth_getCode",
        "address": address,
        "tag": "latest",
    }, chain_id=chain["chain_id"])
    code = payload.get("result")
    # Etherscan puts a bare error string in `result` on rate-limit and
    # invalid-key responses across every module, so an unguarded isinstance
    # check returns "Max rate limit reached" as though it were bytecode — which
    # this function's own contract would then read as "contract, never a
    # person", misclassifying a real wallet because of a transient API error.
    return code if isinstance(code, str) and code.startswith("0x") else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_chain_client.py -v`
Expected: 11 passed

- [ ] **Step 6: Verify no existing caller regressed**

Run: `python -m pytest tests -q`
Expected: all pre-existing tests still pass (the `chain_id` default preserves every call site)

- [ ] **Step 7: Lint and commit**

```bash
ruff check src tests
git add src/utils.py src/chain/client.py tests/test_chain_client.py
git commit -m "feat(chain): multi-chain Etherscan reader with internal transactions

etherscan_get gains an optional chain_id defaulting to the configured Arbitrum
id, so every existing call site is unchanged. The new client collects erc20,
native AND internal records: contract-mediated transfers, which is what bridges
emit, appear in none of the previously-read endpoints.

Empty and unreadable are kept distinct throughout.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Asset registry and USD valuation

**Files:**
- Create: `src/chain/assets.py`
- Test: `tests/test_chain_assets.py`

**Interfaces:**
- Consumes: nothing from this repo — `json` and `pathlib` only. The cache directory arrives as a constructor argument, so the module never reaches for `DATA_DIR` and stays testable against `tmp_path`.
- Produces: `STABLES: set[str]`, `MAJORS: dict[str, str]`, `decimals_of(row, kind) -> int`, `value_usd(symbol, amount, date_str, price_lookup) -> tuple[float | None, str]`, `PriceCache(directory, fetch=None)` with `get(symbol, date_str) -> float | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_assets.py
import json

from src.chain import assets


def test_stablecoins_value_at_par_without_consulting_a_price_source():
    def never_called(symbol, date):
        raise AssertionError("stables must not need a price lookup")

    amount, basis = assets.value_usd("USDC", 5_000_000.0, "2026-06-16", never_called)
    assert amount == 5_000_000.0
    assert basis == "stable_par"


def test_stablecoin_matching_is_case_and_variant_insensitive():
    for symbol in ("usdc", "USDC.e", "USDT", "DAI", "USDe"):
        _, basis = assets.value_usd(symbol, 1.0, "2026-06-16", lambda s, d: None)
        assert basis == "stable_par", symbol


def test_majors_use_the_daily_close():
    amount, basis = assets.value_usd("WETH", 3.0, "2026-06-16", lambda s, d: 2000.0)
    assert amount == 6000.0
    assert basis == "daily_close"


def test_a_missing_price_yields_none_and_never_zero():
    """A $2M ETH transfer booked as 0.0 drops below every threshold in the
    system and the migration walks past unnoticed."""
    amount, basis = assets.value_usd("WETH", 1000.0, "2026-06-16", lambda s, d: None)
    assert amount is None
    assert basis == "unpriced"
    assert amount != 0.0        # the distinction this whole rule exists for


def test_an_unknown_token_is_unpriced_not_valued():
    amount, basis = assets.value_usd("SCAMAIRDROP", 1e9, "2026-06-16", lambda s, d: 1.0)
    assert amount is None
    assert basis == "unpriced"


def test_decimals_come_from_the_row_for_erc20_and_are_18_for_native():
    assert assets.decimals_of({"tokenDecimal": "6"}, "erc20") == 6
    assert assets.decimals_of({}, "native") == 18
    assert assets.decimals_of({}, "internal") == 18
    assert assets.decimals_of({"tokenDecimal": "bogus"}, "erc20") == 18


def test_price_cache_reads_and_writes_disk_and_only_fetches_once(tmp_path):
    calls = []

    def fetch(symbol, date):
        calls.append((symbol, date))
        return 2500.0

    cache = assets.PriceCache(tmp_path, fetch=fetch)
    assert cache.get("WETH", "2026-06-16") == 2500.0
    assert cache.get("WETH", "2026-06-16") == 2500.0
    assert calls == [("WETH", "2026-06-16")]

    reloaded = assets.PriceCache(tmp_path, fetch=fetch)
    assert reloaded.get("WETH", "2026-06-16") == 2500.0
    assert calls == [("WETH", "2026-06-16")]
    assert json.loads((tmp_path / "WETH.json").read_text())["2026-06-16"] == 2500.0


def test_price_cache_records_a_miss_so_it_is_not_retried_every_run(tmp_path):
    calls = []

    def fetch(symbol, date):
        calls.append((symbol, date))
        return None

    cache = assets.PriceCache(tmp_path, fetch=fetch)
    assert cache.get("WETH", "2026-06-16") is None
    assert cache.get("WETH", "2026-06-16") is None
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_assets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain.assets'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/chain/assets.py
"""What a token is worth, and what to do when we do not know.

The rule that matters is at the bottom of `value_usd`: an unknown price returns
None, never 0.0. A price-source outage that books a $2,000,000 ETH transfer as
zero does not raise an error anywhere — it silently drops the transfer below
every value threshold in the system, and the migration it represents is never
looked at again. None is loud; zero is invisible.
"""

import json
from pathlib import Path

# Dollar-pegged. Valued at par, so the overwhelming majority of observed flow
# never needs a price source at all.
STABLES = {
    "USDC", "USDC.E", "USDT", "USDT0", "DAI", "USDE", "SUSDE", "FRAX",
    "USDBC", "TUSD", "USDD", "FDUSD", "LUSD", "USDS",
}

# Symbol -> price-source id. Only assets we are willing to value.
MAJORS = {
    "ETH": "ethereum",
    "WETH": "ethereum",
    "WBTC": "wrapped-bitcoin",
    "CBBTC": "coinbase-wrapped-btc",
    "ARB": "arbitrum",
    "OP": "optimism",
    "BNB": "binancecoin",
    "POL": "matic-network",
    "MATIC": "matic-network",
    "WSTETH": "wrapped-steth",
    "WEETH": "wrapped-eeth",
}

DEFAULT_DECIMALS = 18


def decimals_of(row: dict, kind: str) -> int:
    """Token decimals for a raw Etherscan row.

    Native and internal transfers are always wei. ERC-20 rows carry their own
    `tokenDecimal`; a malformed one falls back to 18 rather than raising, since
    a bad decimal on one row must not abort a whole sweep.
    """
    if kind in ("native", "internal"):
        return DEFAULT_DECIMALS
    try:
        return int(row.get("tokenDecimal"))
    except (TypeError, ValueError):
        return DEFAULT_DECIMALS


def value_usd(symbol: str, amount: float, date_str: str,
              price_lookup) -> tuple[float | None, str]:
    """USD value of `amount` of `symbol` on `date_str`, and the basis used.

    Returns (None, "unpriced") for anything we cannot value — never (0.0, ...).
    """
    sym = (symbol or "").strip().upper()
    if sym in STABLES:
        return round(float(amount), 2), "stable_par"
    if sym in MAJORS:
        price = price_lookup(sym, date_str)
        if price is None:
            # Distinct from "unpriced". A known major we could not price is not
            # an unknown token, and conflating them lets the spam classifier
            # quarantine a real ETH transfer on the strength of a price outage.
            return None, "price_unavailable"
        return round(float(amount) * float(price), 2), "daily_close"
    return None, "unpriced"


class PriceCache:
    """Daily closes on disk, one file per symbol, fetched at most once ever.

    A recorded miss is cached too. Without that, every run re-requests the same
    unavailable date for as long as the record exists.
    """

    def __init__(self, directory: Path, fetch=None):
        self.directory = Path(directory)
        self._fetch = fetch
        self._loaded: dict[str, dict] = {}

    def _path(self, symbol: str) -> Path:
        return self.directory / f"{symbol}.json"

    def _table(self, symbol: str) -> dict:
        if symbol not in self._loaded:
            path = self._path(symbol)
            try:
                self._loaded[symbol] = json.loads(path.read_text())
            except (OSError, ValueError):
                self._loaded[symbol] = {}
        return self._loaded[symbol]

    def get(self, symbol: str, date_str: str) -> float | None:
        table = self._table(symbol)
        if date_str in table:
            value = table[date_str]
            return None if value is None else float(value)
        if self._fetch is None:
            return None
        price = self._fetch(symbol, date_str)
        table[date_str] = price
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(symbol).write_text(json.dumps(table, indent=2, sort_keys=True))
        return price
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain_assets.py -v`
Expected: 8 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/chain/assets.py tests/test_chain_assets.py
git commit -m "feat(chain): asset registry and USD valuation

Stables value at par and never touch a price source. Majors use a cached daily
close, one fetch per symbol-date ever, misses cached too.

An unknown price returns None, never 0.0: a \$2M ETH transfer booked as zero
raises no error and silently drops below every threshold in the system.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Spam and address-poisoning quarantine

Validated against live data before being specified: 11 of 14 dust-only counterparties match the 4+4 rule, and one lookalike of the known self-wallet accounts for 510 of the 1,000 stored records.

**Files:**
- Create: `src/chain/spam.py`, `tests/fixtures/poisoning_live.json`
- Test: `tests/test_chain_spam.py`

**Interfaces:**
- Consumes: nothing (pure)
- Produces: `counterparty_volume(records, wallet) -> dict[str, float]`, `is_lookalike(addr, volume, *, prefix=4, suffix=4, dust_usd=1.0) -> str | None` (returns the mimicked address), `derive_real_counterparties(records, wallet, dust_usd=1.0) -> set[str]`, `classify_spam(record, volume, *, dust_usd=1.0, prefix=4, suffix=4) -> str | None`, `rollup(records, wallet) -> list[dict]`.

> **The anchor set is a volume map, not a membership set.** A forgery mimics something *bigger* than itself — that is the entire economics of the attack — so an address is a forgery of another only when the other has moved strictly more value with the wallet. A flat "is this address real?" test cannot express that, and gets the direction wrong in both ways: it lets a $1 clone erase the $13M counterparty it forges, and, once patched with a blanket exemption, whitewashes the clone completely. Magnitude tells the two apart; membership cannot.

- [ ] **Step 1: Create the live fixture**

```json
[
  {"address": "0x1419b0d742da87d053373018740e7c3a41402d5f", "count": 510},
  {"address": "0x1419b5d906762b97be7c482a8cfc878175af2d5f", "count": 1},
  {"address": "0x14197158ac076861d4b5d9334c3299c80b412d5f", "count": 2},
  {"address": "0x1419919ebe53c713b2984d45d8018f495b612d5f", "count": 3},
  {"address": "0x2df1df582d0a1efc7178fd78b2bcd9aa08a73df7", "count": 3},
  {"address": "0x2df1d3d6122aefe82cd6d8f337e9e61e7a533df7", "count": 1},
  {"address": "0x2df1c46a394d49896b4324b0449468357f163df7", "count": 2},
  {"address": "0x2df17470c5de6a5d2d41feb8fcf5fb0deeb43df7", "count": 56}
]
```

Save as `tests/fixtures/poisoning_live.json`. These are the real poisoning addresses observed against the target; the first four mimic the known self-wallet `0x1419e75330c71ce463102e6a1eb62fe80b412d5f` and the last four mimic the HL bridge `0x2df1c51e09aecf9cacb7bc98cb1742757f163df7`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_chain_spam.py
import json
from pathlib import Path

from src.chain import spam

SELF_WALLET = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
HL_BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
FIXTURE = Path(__file__).parent / "fixtures" / "poisoning_live.json"


def record(src, dst, usd=100.0, basis="stable_par", amount=100.0):
    return {"src": src, "dst": dst, "amount_usd": usd, "amount": amount,
            "value_basis": basis, "asset": "USDC"}


def test_every_live_poisoning_address_is_caught_by_the_four_four_rule():
    real = {SELF_WALLET, HL_BRIDGE}
    entries = json.loads(FIXTURE.read_text())
    for entry in entries:
        assert spam.is_lookalike(entry["address"], real) in real, entry["address"]


def test_a_genuine_counterparty_is_not_a_lookalike():
    real = {SELF_WALLET, HL_BRIDGE}
    assert spam.is_lookalike("0xa95d9c1f655341597c94393fddc30cf3c08e4fce", real) is None


def test_an_address_is_never_a_lookalike_of_itself():
    assert spam.is_lookalike(SELF_WALLET, {SELF_WALLET}) is None


def test_real_counterparties_need_a_priced_non_dust_transfer():
    wallet = "0xtarget"
    records = [
        record(wallet, "0xbig", usd=5000.0),
        record(wallet, "0xdusty", usd=0.4),
        record(wallet, "0xzero", usd=0.0),
        record(wallet, "0xunpriced", usd=None, basis="unpriced"),
    ]
    assert spam.derive_real_counterparties(records, wallet) == {"0xbig"}


def test_lookalike_is_evaluated_before_dust():
    """Which address is being mimicked is intelligence: attackers mimic
    addresses that received large sums."""
    real = {SELF_WALLET}
    poison = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    reason = spam.classify_spam(record("0xtarget", poison, usd=0.0, amount=0.0), real)
    assert reason == "lookalike"


def test_zero_value_transfer_is_quarantined():
    reason = spam.classify_spam(record("0xtarget", "0xsomebody", usd=0.0, amount=0.0), set())
    assert reason == "zero_value"


def test_sub_dust_transfer_is_quarantined():
    reason = spam.classify_spam(record("0xtarget", "0xsomebody", usd=0.4), set())
    assert reason == "dust"


def test_unpriced_token_is_quarantined():
    r = record("0xtarget", "0xsomebody", usd=None, basis="unpriced", amount=1e9)
    r["asset"] = "SCAMAIRDROP"
    assert spam.classify_spam(r, set()) == "unpriced_token"


def test_a_real_transfer_is_not_spam():
    assert spam.classify_spam(record("0xtarget", "0xbig", usd=13_000_000.0), set()) is None


def test_rollup_aggregates_by_address_and_keeps_the_mimic_target():
    records = [
        {"src": "0xtarget", "dst": "0xpoison", "spam": True, "spam_reason": "lookalike",
         "mimics": SELF_WALLET, "forged": "0xpoison", "ts": 100,
         "asset": "USDC", "token_address": "0xaf88"},
        {"src": "0xpoison", "dst": "0xtarget", "spam": True, "spam_reason": "lookalike",
         "mimics": SELF_WALLET, "forged": "0xpoison", "ts": 300,
         "asset": "USDC", "token_address": "0xaf88"},
        {"src": "0xtarget", "dst": "0xok", "spam": False, "spam_reason": None, "ts": 200},
    ]
    rolled = spam.rollup(records)
    assert len(rolled) == 1
    entry = rolled[0]
    assert entry["address"] == "0xpoison"
    assert entry["count"] == 2
    assert entry["mimics"] == SELF_WALLET
    assert entry["first_seen"] == 100
    assert entry["last_seen"] == 300


def test_rollup_keeps_the_token_of_an_unpriced_entry_so_it_can_be_registered():
    records = [{"src": "0xtarget", "dst": "0xnew", "spam": True,
                "spam_reason": "unpriced_token", "ts": 5,
                "asset": "REALTOKEN", "token_address": "0xdeadbeef"}]
    entry = spam.rollup(records)[0]
    assert entry["asset"] == "REALTOKEN"
    assert entry["token_address"] == "0xdeadbeef"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_spam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain.spam'`

- [ ] **Step 4: Write minimal implementation**

```python
# src/chain/spam.py
"""Separating the target's money from the noise sprayed at it.

Measured on the live data before this module was written: 905 of the 1,000
stored transfer records moved less than a dollar, and a single address —
0x1419b0d7…2d5f, a vanity forgery of the target's own known self-wallet
0x1419e753…2d5f — accounted for 510 of them. The target has five real
counterparties. Collection was not short of capacity; spam had evicted the
signal from a fixed-size window.

Address poisoning works by generating an address matching a real
counterparty's first and last characters, then sending a zero-value transfer so
it lands in the victim's history and gets copied out of it later. That leaves a
signature this module matches exactly: at 4 leading and 4 trailing hex
characters, 11 of 14 dust-only counterparties are forgeries of either the
self-wallet or the Hyperliquid bridge.

Pure — no IO, no config reads.
"""


def counterparty_volume(records: list[dict], wallet: str) -> dict[str, float]:
    """Total priced USD moved with `wallet`, per counterparty address.

    Magnitude, not membership. A flat "is this address real?" set cannot tell a
    forgery from the address it forges, because the pattern match is symmetric —
    and getting that direction wrong is not a small error. A $1 clone of the
    $13M counterparty would make the GENUINE address match the clone and be
    quarantined out of the graph entirely.

    Volume settles it, because the attack only makes sense against a
    counterparty richer than the attacker's own address.

    Unpriced records contribute nothing: an unpriced transfer is not evidence of
    value. Zero-value and dust records do contribute, so a pure-poisoning
    address lands at or near 0.0 rather than being absent from the map.
    """
    w = (wallet or "").lower()
    volume: dict[str, float] = {}
    for rec in records:
        usd = rec.get("amount_usd")
        if usd is None:
            continue
        src, dst = (rec.get("src") or "").lower(), (rec.get("dst") or "").lower()
        other = dst if src == w else src
        if other and other != w:
            volume[other] = volume.get(other, 0.0) + float(usd)
    return volume


def is_lookalike(addr: str, volume: dict, *, prefix: int = 4, suffix: int = 4,
                 dust_usd: float = 1.0) -> str | None:
    """The address this one is forging, or None.

    Returned rather than a bool because *which* address is being mimicked is
    itself intelligence: forgers target addresses that received large sums, so
    the mimic list points at the counterparties that matter.

    Two conditions, both required. The 4+4 character pattern must match, and the
    candidate anchor must have moved strictly more value than `addr` — nobody
    forges an address poorer than their own. Strictly greater, so an exact tie
    flags neither side.
    """
    a = (addr or "").lower()
    if not a.startswith("0x") or len(a) != 42:
        return None
    mine = float(volume.get(a, 0.0))
    head, tail = a[2:2 + prefix], a[-suffix:]
    for real, value in volume.items():
        r = (real or "").lower()
        if r == a or len(r) != 42:
            continue
        # An address that has itself moved nothing is not worth forging, so it
        # must never serve as an anchor.
        if float(value) < dust_usd or float(value) <= mine:
            continue
        if r[2:2 + prefix] == head and r[-suffix:] == tail:
            return r
    return None


def derive_real_counterparties(records: list[dict], wallet: str,
                               dust_usd: float = 1.0) -> set[str]:
    """Addresses that moved real money with `wallet`.

    Deliberately computed before spam classification, on valued records: the
    lookalike rule is defined against value, so valuation has to happen first.
    """
    return {a for a, v in counterparty_volume(records, wallet).items()
            if v >= dust_usd}


def classify_spam(record: dict, volume: dict, *, dust_usd: float = 1.0,
                  prefix: int = 4, suffix: int = 4) -> str | None:
    """Why this record is noise, or None if it is real money.

    Order is deliberate. The lookalike check runs before the dust check because
    a forgery is almost always sub-dust, and reporting it as "dust" would throw
    away the mimic relationship that makes it worth recording.
    """
    vol = {(a or "").lower(): float(v) for a, v in volume.items()}
    for side in ((record.get("src") or ""), (record.get("dst") or "")):
        if is_lookalike(side.lower(), vol, prefix=prefix, suffix=suffix,
                        dust_usd=dust_usd):
            return "lookalike"

    amount = record.get("amount")
    if amount is not None and float(amount) == 0.0:
        return "zero_value"

    if record.get("value_basis") == "price_unavailable":
        # A known major we could not price is not noise. Quarantining it would
        # discard a potentially large real transfer on the strength of a price
        # outage — and quarantined records never reach the substrate at all, so
        # once the cursor has advanced past them the loss is permanent.
        return None

    usd = record.get("amount_usd")
    if usd is None:
        return "unpriced_token"
    if float(usd) < dust_usd:
        return "dust"
    return None


def rollup(records: list[dict], wallet: str) -> list[dict]:
    """Aggregate quarantined records to one entry per address.

    `wallet` is required, not defaulted. With an empty default `src == wallet`
    is never true, so the counterparty resolves to `src` unconditionally — right
    for incoming spam, wrong for outgoing, and silently so.

    Stored instead of the records themselves: 1,842 junk rows must not live in
    git forever to prove a count. `asset`/`token_address` are retained so a
    legitimate token the registry does not yet know is visible and can be added
    to assets.py, rather than silently discarded on every future run.
    """
    w = (wallet or "").lower()
    by_addr: dict[str, dict] = {}
    for rec in records:
        if not rec.get("spam"):
            continue
        # `forged` is set by the classifier, which knows which side matched.
        # Re-deriving it here from `mimics` cannot work: a poisoning transfer
        # arrives in both directions, so "the side that is not the mimicked
        # address" is the target's own wallet half the time.
        #
        # For the non-lookalike reasons there is no `forged`, and an
        # unconditional `dst` fallback is wrong for the same directional
        # reason: poisoning is overwhelmingly INCOMING, so the wallet is `dst`
        # and every distinct spammer would collapse into one entry keyed by the
        # victim's own address — destroying the per-address breakdown this
        # function exists to produce.
        src = (rec.get("src") or "").lower()
        dst = (rec.get("dst") or "").lower()
        counterparty = dst if src == w else src
        addr = (rec.get("forged") or counterparty or dst or "").lower()
        mimics = rec.get("mimics")
        if not addr:
            continue
        ts = int(rec.get("ts", 0) or 0)
        entry = by_addr.get(addr)
        if entry is None:
            by_addr[addr] = {
                "address": addr,
                "reason": rec.get("spam_reason"),
                "mimics": mimics,
                "asset": rec.get("asset"),
                "token_address": rec.get("token_address"),
                "count": 1,
                "first_seen": ts,
                "last_seen": ts,
            }
            continue
        entry["count"] += 1
        entry["first_seen"] = min(entry["first_seen"], ts)
        entry["last_seen"] = max(entry["last_seen"], ts)
        entry["mimics"] = entry["mimics"] or mimics
    return sorted(by_addr.values(), key=lambda e: -e["count"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_chain_spam.py -v`
Expected: 11 passed

- [ ] **Step 6: Lint and commit**

```bash
ruff check src tests
git add src/chain/spam.py tests/test_chain_spam.py tests/fixtures/poisoning_live.json
git commit -m "feat(chain): quarantine address poisoning and dust

905 of the 1000 stored records moved under a dollar; one forgery of the known
self-wallet accounted for 510 of them. The target has five real counterparties.

Matches the forgery signature directly: 4 leading + 4 trailing hex characters
against addresses that moved real money. Catches all 8 live poisoning addresses
in the fixture. Lookalike is checked before dust so the mimic relationship
survives.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Entity label registry

**Files:**
- Create: `src/chain/labels.py`, `data/labels/entities.json`
- Test: `tests/test_chain_labels.py`

**Interfaces:**
- Consumes: `src.chain.client.fetch_code` (injected as `CodeCache`'s `fetcher`, not imported by `labels.py`). Paths arrive as arguments — the module never reaches for `DATA_DIR`.
- Produces: `SERVICE_CATEGORIES: set[str]`, `load_registry(path) -> dict[str, dict]`, `classify_address(addr, registry, *, has_code=None, fan_reason=None, inferred=None) -> dict`, `infer_deposit_addresses(records, cex_hot, *, forward_ratio=0.95, window_hours=24) -> dict[str, dict]`, `service_addresses(registry, inferred=None) -> set[str]`, `CodeCache(path, fetcher)` with `has_code(address, chain) -> bool | None`.

A classification result is `{"category": str | None, "entity": str | None, "source": str | None, "is_service": bool}`.

- [ ] **Step 1: Create the curated registry seed**

Create `data/labels/entities.json`. Categories must be drawn from `SERVICE_CATEGORIES`. Seed it with the entries below; the `source` field records provenance so a wrong label can be traced and corrected.

```json
{
  "schema_version": 1,
  "updated": "2026-08-28",
  "entities": [
    {"address": "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7", "chain": "arbitrum", "entity": "Hyperliquid Bridge2", "category": "hl_infra", "source": "config.json hl_bridge_contract", "added": "2026-08-28"},
    {"address": "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "chain": "arbitrum", "entity": "USDC (native)", "category": "contract", "source": "config.json usdc_contract_arbitrum", "added": "2026-08-28"},
    {"address": "0xb38e8c17e38363af6ebdcb3dae12e0243582891d", "chain": "ethereum", "entity": "Binance", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0xf977814e90da44bfa03b6295a0616a897441acec", "chain": "ethereum", "entity": "Binance 8", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "chain": "ethereum", "entity": "Binance 14", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x21a31ee1afc51d94c2efccaa2092ad1028285549", "chain": "ethereum", "entity": "Binance 15", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0xdfd5293d8e347dfe59e90efd55b2956a1343963d", "chain": "ethereum", "entity": "Binance 16", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x5a52e96bacdabb82fd05763e25335261b270efcb", "chain": "arbitrum", "entity": "Binance (Arbitrum)", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b", "chain": "ethereum", "entity": "OKX", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x5041ed759dd4afc3a72b8192c143f72f4724081a", "chain": "ethereum", "entity": "OKX 2", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0xf89d7b9c864f589bbf53a82105107622b35eaa40", "chain": "ethereum", "entity": "Bybit Hot", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0xa7efae728d2936e78bda97dc267687568dd593f3", "chain": "ethereum", "entity": "Bybit 2", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x71660c4005ba85c37ccec55d0c4493e66fe775d3", "chain": "ethereum", "entity": "Coinbase 1", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x503828976d22510aad0201ac7ec88293211d23da", "chain": "ethereum", "entity": "Coinbase 2", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740", "chain": "ethereum", "entity": "Coinbase 3", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x2910543af39aba0cd09dbb2d50200b3e800a63d2", "chain": "ethereum", "entity": "Kraken 1", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0xe93381fb4c4f14bda253907b18fad305d799241a", "chain": "ethereum", "entity": "Huobi/HTX 1", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x0d0707963952f2fba59dd06f2b425ace40b492fe", "chain": "ethereum", "entity": "Gate.io 1", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x1c4b70a3968436b9a0a9cf5205c787eb81bb558c", "chain": "ethereum", "entity": "Gate.io 2", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x8f22f2063d253846b53609231ed80fa571bc0c8f", "chain": "ethereum", "entity": "MEXC", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x38147794ff247e5fc179edbae6c37fff88f68c52", "chain": "ethereum", "entity": "Bitget", "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    {"address": "0x8731d54e9d02c286767d56ac03e8037c07e01e98", "chain": "ethereum", "entity": "Stargate Router", "category": "bridge", "source": "public label", "added": "2026-08-28"},
    {"address": "0x53bf833a5d6c4dda888f69c22c88c9f356a41614", "chain": "arbitrum", "entity": "Stargate Router (Arbitrum)", "category": "bridge", "source": "public label", "added": "2026-08-28"},
    {"address": "0xbd3fa81b58ba92a82136038b25adec7066af3155", "chain": "ethereum", "entity": "Circle CCTP TokenMessenger", "category": "bridge", "source": "public label", "added": "2026-08-28"},
    {"address": "0x19330d10d9cc8751218eaf51e8885d058642e08a", "chain": "ethereum", "entity": "Circle CCTP TokenMessenger v2", "category": "bridge", "source": "public label", "added": "2026-08-28"},
    {"address": "0x6b7a87899490ece95443e979ca9485cbe7e71522", "chain": "ethereum", "entity": "Across SpokePool", "category": "bridge", "source": "public label", "added": "2026-08-28"},
    {"address": "0xe35e9842fceaca96570b734083f4a58e8f7c5f2a", "chain": "arbitrum", "entity": "Across SpokePool (Arbitrum)", "category": "bridge", "source": "public label", "added": "2026-08-28"},
    {"address": "0x43de2d77bf8027e25dbd179b491e8d64f38398aa", "chain": "ethereum", "entity": "deBridge Gate", "category": "bridge", "source": "public label", "added": "2026-08-28"},
    {"address": "0x1111111254eeb25477b68fb85ed929f73a960582", "chain": "ethereum", "entity": "1inch v5 Router", "category": "dex_router", "source": "public label", "added": "2026-08-28"},
    {"address": "0x111111125421ca6dc452d289314280a0f8842a65", "chain": "ethereum", "entity": "1inch v6 Router", "category": "dex_router", "source": "public label", "added": "2026-08-28"},
    {"address": "0xdef1c0ded9bec7f1a1670819833240f027b25eff", "chain": "ethereum", "entity": "0x Exchange Proxy", "category": "dex_router", "source": "public label", "added": "2026-08-28"},
    {"address": "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45", "chain": "ethereum", "entity": "Uniswap Universal Router", "category": "dex_router", "source": "public label", "added": "2026-08-28"},
    {"address": "0xe592427a0aece92de3edee1f18e0157c05861564", "chain": "ethereum", "entity": "Uniswap V3 Router", "category": "dex_router", "source": "public label", "added": "2026-08-28"},
    {"address": "0x8589427373d6d84e98730d7795d8f6f8731fda16", "chain": "ethereum", "entity": "Tornado Cash Router", "category": "mixer", "source": "public label", "added": "2026-08-28"}
  ]
}
```

> **Label provenance matters.** Every entry carries `source`. A label is a claim about a third party, and the registry has to stay auditable and correctable. If any address above is later found wrong, delete it — a stale label suppresses a real lead, and that is a worse failure than an unlabelled hub.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_chain_labels.py
import json

from src.chain import labels

BINANCE = "0xf977814e90da44bfa03b6295a0616a897441acec"
BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"


def registry():
    return labels.load_registry_data({"entities": [
        {"address": BINANCE, "chain": "ethereum", "entity": "Binance 8",
         "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
        {"address": BRIDGE, "chain": "arbitrum", "entity": "Hyperliquid Bridge2",
         "category": "hl_infra", "source": "config", "added": "2026-08-28"},
    ]})


def test_the_shipped_registry_parses_and_uses_only_known_categories():
    from pathlib import Path
    path = Path(__file__).parent.parent / "data" / "labels" / "entities.json"
    data = json.loads(path.read_text())
    for entry in data["entities"]:
        assert entry["category"] in labels.SERVICE_CATEGORIES, entry
        assert entry["address"] == entry["address"].lower(), entry
        assert entry["source"], entry


def test_a_curated_label_wins_over_every_other_signal():
    got = labels.classify_address(BINANCE, registry(), has_code=False,
                                  fan_reason="high fan-in (900 senders)",
                                  inferred={"category": "cex_deposit"})
    assert got["category"] == "cex_hot"
    assert got["entity"] == "Binance 8"
    assert got["source"] == "curated"
    assert got["is_service"] is True


def test_an_inferred_label_beats_fan_degree():
    got = labels.classify_address("0xdeadbeef", registry(), has_code=False,
                                  fan_reason="high fan-in (900 senders)",
                                  inferred={"category": "cex_deposit",
                                            "entity": "Binance (inferred deposit)"})
    assert got["category"] == "cex_deposit"
    assert got["source"] == "inferred"


def test_bytecode_makes_an_address_a_contract_and_never_a_person():
    got = labels.classify_address("0xdeadbeef", registry(), has_code=True)
    assert got["category"] == "contract"
    assert got["source"] == "code"
    assert got["is_service"] is True


def test_fan_degree_is_the_last_resort():
    got = labels.classify_address("0xdeadbeef", registry(), has_code=False,
                                  fan_reason="many-to-many flow (40 senders, 40 recipients)")
    assert got["category"] == "service"
    assert got["source"] == "fan_degree"
    assert got["is_service"] is True


def test_an_ordinary_wallet_is_not_a_service():
    got = labels.classify_address("0xa95d9c1f655341597c94393fddc30cf3c08e4fce",
                                  registry(), has_code=False)
    assert got["category"] is None
    assert got["source"] is None
    assert got["is_service"] is False


def test_unknown_bytecode_state_does_not_invent_a_contract():
    """has_code=None means we could not read it. Absence of evidence is not
    evidence of a contract."""
    got = labels.classify_address("0xdeadbeef", registry(), has_code=None)
    assert got["category"] is None
    assert got["is_service"] is False


def test_a_deposit_address_forwarding_almost_everything_to_a_cex_is_inferred():
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xdeposit", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xdeposit", "dst": BINANCE, "amount_usd": 999_000.0, "ts": 2 * hour},
    ]
    got = labels.infer_deposit_addresses(records, {BINANCE})
    assert "0xdeposit" in got
    assert got["0xdeposit"]["category"] == "cex_deposit"
    assert got["0xdeposit"]["forwarded_to"] == BINANCE


def test_forwarding_too_little_is_not_a_deposit_address():
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 500_000.0, "ts": 2 * hour},
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_forwarding_too_late_is_not_a_deposit_address():
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 999_000.0, "ts": 40 * 3600},
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_a_wallet_with_other_material_destinations_is_not_a_deposit_address():
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 950_000.0, "ts": hour},
        {"src": "0xmaybe", "dst": "0xelsewhere", "amount_usd": 300_000.0, "ts": hour},
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_service_addresses_unions_curated_and_inferred():
    inferred = {"0xdeposit": {"category": "cex_deposit", "entity": "Binance (inferred)"}}
    got = labels.service_addresses(registry(), inferred)
    assert BINANCE in got and BRIDGE in got and "0xdeposit" in got


def test_code_cache_asks_once_and_persists(tmp_path):
    calls = []

    def fetcher(address, chain):
        calls.append(address)
        return "0x60806040"

    cache = labels.CodeCache(tmp_path / "code_cache.json", fetcher)
    assert cache.has_code("0xABC", {"name": "arbitrum"}) is True
    assert cache.has_code("0xabc", {"name": "arbitrum"}) is True
    assert calls == ["0xABC"]

    reloaded = labels.CodeCache(tmp_path / "code_cache.json", fetcher)
    assert reloaded.has_code("0xabc", {"name": "arbitrum"}) is True
    assert len(calls) == 1


def test_code_cache_returns_none_and_caches_nothing_on_a_read_failure(tmp_path):
    cache = labels.CodeCache(tmp_path / "code_cache.json", lambda a, c: None)
    assert cache.has_code("0xabc", {"name": "arbitrum"}) is None
    assert not (tmp_path / "code_cache.json").exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_labels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain.labels'`

- [ ] **Step 4: Write minimal implementation**

```python
# src/chain/labels.py
"""Naming the things on the graph that are not people.

Before this module the system knew three service addresses: the Hyperliquid
bridge, the USDC contract and the zero address. A trail entering a Binance hot
wallet was therefore indistinguishable from a trail entering the trader's new
wallet — and worse, the frontier would happily spend its whole expansion budget
walking an exchange that receives from a million unrelated people.

Resolution order, strongest first:

  curated  — a human-checked entry in data/labels/entities.json
  code     — the address has bytecode, so it is not a person
  inferred — behaves like a CEX deposit address for one of our wallets
  fan      — many-to-many degree, the pre-existing heuristic

This strengthens the invariant in transfer_graph.py rather than bending it:
services still score 0.0, still never alert, still are never traversed through.
They are simply identified correctly now.
"""

import json
from pathlib import Path

SERVICE_CATEGORIES = {
    "cex_hot", "cex_deposit", "cex_deposit_sweep", "bridge", "dex_router",
    "contract", "mixer", "hl_infra", "service",
}

DEFAULT_FORWARD_RATIO = 0.95
DEFAULT_WINDOW_HOURS = 24.0
# A destination taking less than this share of what the address received is not
# evidence against it being a deposit address — deposit addresses pay gas too.
MATERIAL_DESTINATION_RATIO = 0.05


def load_registry_data(data: dict) -> dict[str, dict]:
    """Index a parsed entities document by lowercase address."""
    out: dict[str, dict] = {}
    for entry in data.get("entities", []):
        addr = (entry.get("address") or "").lower()
        if addr:
            out[addr] = entry
    return out


def load_registry(path: Path) -> dict[str, dict]:
    """The curated registry, or empty if it has not been created yet."""
    try:
        return load_registry_data(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return {}


def classify_address(addr: str, registry: dict[str, dict], *,
                     has_code: bool | None = None,
                     fan_reason: str | None = None,
                     inferred: dict | None = None) -> dict:
    """What this address is, and on what authority."""
    a = (addr or "").lower()

    entry = registry.get(a)
    if entry:
        return {"category": entry.get("category"), "entity": entry.get("entity"),
                "source": "curated", "is_service": True}

    if has_code is True:
        return {"category": "contract", "entity": None,
                "source": "code", "is_service": True}

    if inferred:
        return {"category": inferred.get("category", "cex_deposit"),
                "entity": inferred.get("entity"),
                "source": "inferred", "is_service": True}

    if fan_reason:
        return {"category": "service", "entity": fan_reason,
                "source": "fan_degree", "is_service": True}

    # has_code None means the lookup failed. Absence of evidence is not
    # evidence of a contract, so nothing is asserted.
    return {"category": None, "entity": None, "source": None, "is_service": False}


def infer_deposit_addresses(records: list[dict], cex_hot,
                            *, forward_ratio: float = DEFAULT_FORWARD_RATIO,
                            window_hours: float = DEFAULT_WINDOW_HOURS
                            ) -> dict[str, dict]:
    """Addresses that behave like an exchange deposit address.

    A CEX deposit address is never publicly labelled, and it is the highest
    value identity artifact on chain: it belongs to exactly one exchange
    account, so two wallets funding the same one are the same customer. Phase 2
    re-links on that; Phase 1 only has to name it.

    The signature is narrow on purpose — receives, then forwards nearly all of
    it to a known hot wallet, quickly, with no other material destination.
    """
    hot = {h.lower() for h in cex_hot}
    received: dict[str, float] = {}
    first_in: dict[str, int] = {}
    sent_to: dict[str, dict[str, float]] = {}
    # The LARGEST send to a hot wallet, not the earliest: (amount, ts, hot).
    # Anchoring the window on the earliest lets a trivial test-send — ordinary
    # behaviour before committing a large transfer — satisfy "quickly" on behalf
    # of a bulk forward that happened days later.
    primary_out: dict[str, tuple[float, int, str]] = {}

    for rec in records:
        usd = rec.get("amount_usd")
        if usd is None:
            continue
        usd = float(usd)
        src = (rec.get("src") or "").lower()
        dst = (rec.get("dst") or "").lower()
        ts = int(rec.get("ts", 0) or 0)

        if dst and dst not in hot:
            received[dst] = received.get(dst, 0.0) + usd
            if dst not in first_in or ts < first_in[dst]:
                first_in[dst] = ts
        if src and dst:
            sent_to.setdefault(src, {})
            sent_to[src][dst] = sent_to[src].get(dst, 0.0) + usd
            if dst in hot:
                best = primary_out.get(src)
                # Strictly greater, so an exact tie keeps the earlier send.
                if best is None or usd > best[0]:
                    primary_out[src] = (usd, ts, dst)

    out: dict[str, dict] = {}
    for addr, total_in in received.items():
        if total_in <= 0 or addr in hot:
            continue
        destinations = sent_to.get(addr) or {}
        to_hot = sum(v for d, v in destinations.items() if d in hot)
        if to_hot / total_in < forward_ratio:
            continue
        # Summed, not maxed. A max only rejects one large sibling destination,
        # so the same value fanned across ten addresses at 4.9% each slips
        # under the threshold and a wallet with half its activity elsewhere
        # still reads as a deposit address.
        other_total = sum(v for d, v in destinations.items() if d not in hot)
        if other_total / total_in > MATERIAL_DESTINATION_RATIO:
            continue
        best = primary_out.get(addr)
        if best is None:
            continue
        _, out_ts, hot_addr = best
        elapsed_hours = (out_ts - first_in.get(addr, out_ts)) / 3600.0
        # Measured to the transfer that actually carries the value. A long-lived
        # address whose bulk forward is far from its first receipt now falls
        # outside the window and is left unlabelled — deliberately. A false
        # negative costs some wasted expansion budget; a false positive marks a
        # real wallet a service, and services are never traversed, so the trail
        # stops dead at the one address we most needed to follow. The two are
        # not symmetric, so the rule fails toward traversing.
        if elapsed_hours < 0 or elapsed_hours > window_hours:
            continue
        out[addr] = {
            "category": "cex_deposit",
            "entity": f"deposit address forwarding to {hot_addr}",
            "forwarded_to": hot_addr,
            "forward_ratio": round(to_hot / total_in, 4),
            "hours_to_forward": round(elapsed_hours, 2),
        }
    return out


def service_addresses(registry: dict[str, dict],
                      inferred: dict | None = None) -> set[str]:
    """Every address the graph must treat as infrastructure."""
    out = {a for a, e in registry.items()
           if e.get("category") in SERVICE_CATEGORIES}
    out |= {a.lower() for a in (inferred or {})}
    return out


class CodeCache:
    """Whether an address has bytecode. Asked once per address, ever.

    A failed lookup is not cached: recording "no code" because the API was down
    would permanently mislabel a contract as a person.
    """

    def __init__(self, path: Path, fetcher):
        self.path = Path(path)
        self._fetcher = fetcher
        try:
            self._table = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._table = {}

    def has_code(self, address: str, chain: dict) -> bool | None:
        key = f"{chain['name']}:{(address or '').lower()}"
        if key in self._table:
            return self._table[key]
        code = self._fetcher(address, chain)
        if code is None:
            return None
        self._table[key] = bool(code and code not in ("0x", "0X"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._table, indent=2, sort_keys=True))
        return self._table[key]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_chain_labels.py -v`
Expected: 14 passed

- [ ] **Step 6: Lint and commit**

```bash
ruff check src tests
git add src/chain/labels.py data/labels/entities.json tests/test_chain_labels.py
git commit -m "feat(chain): entity label registry and contract detection

The graph knew three service addresses, so a trail entering Binance looked like
a trail entering a fresh personal wallet and the frontier would spend its whole
budget walking an exchange.

Adds a curated registry with provenance on every entry, a permanent bytecode
check (a contract is never a person), and CEX deposit-address inference — the
strongest identity artifact on chain, since a deposit address belongs to exactly
one exchange account.

Services still score 0.0 and are still never traversed. They are only named
correctly now.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Sweep orchestration and persistence

**Files:**
- Create: `src/chain/collect.py`
- Test: `tests/test_chain_collect.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7, plus `src.utils.append_records`, `save_latest`, `DATA_DIR`
- Produces: `normalise_row(row, chain, kind, price_lookup) -> dict | None`, `read_cursors() -> dict`, `write_cursors(cursors) -> None`, `records_for(wallet, *, include_spam=False) -> list[dict]`, `sweep_wallet(address, chains, budget, *, cluster=False, price_lookup=None) -> dict`, `sweep_health(results) -> dict`, `TRANSFERS_DIR`, `SPAM_DIR`, `CURSOR_PATH`.

> **`records_for` is the single reader.** Tasks 9, 10 and 11 all need "every stored record touching this wallet". Defining it three times would guarantee three different spam-filtering rules. It lives here; everything else imports it.

A sweep result is `{"address": str, "chains": {name: {"records": int, "spam": int, "calls": int, "cursor": int, "gaps": [int], "truncated": bool, "error": str | None, "probed_inactive": bool}}, "degraded_sources": [str]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_collect.py
import json

import pytest

from src.chain import collect
from src.chain.budget import CallBudget
from src.chain.pagination import WalkResult

ARB = {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0}
BASE = {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": True, "priority": 1}


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Every sweep test except the skip test needs a key present.

    Without this, sweep_wallet takes the skipped_no_api_key path on any machine
    that has not exported one, and the whole module passes vacuously.
    """
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")


def budget(calls=100):
    return CallBudget(max_calls=calls, seconds=1000, clock=lambda: 0.0)


def erc20_row(value="5000000000000", to="0xdest", frm="0xtarget", block="100",
              ts="1781000000", h="0xhash1", log="0", symbol="USDC"):
    return {"blockNumber": block, "timeStamp": ts, "hash": h, "logIndex": log,
            "from": frm, "to": to, "value": value, "tokenSymbol": symbol,
            "tokenDecimal": "6", "contractAddress": "0xaf88"}


def test_normalise_row_produces_the_phase_one_record():
    rec = collect.normalise_row(erc20_row(), ARB, "erc20", lambda s, d: None)
    assert rec["id"] == "arbitrum:0xhash1:erc20:0"
    assert rec["chain"] == "arbitrum" and rec["chain_id"] == 42161
    assert rec["src"] == "0xtarget" and rec["dst"] == "0xdest"
    assert rec["kind"] == "erc20" and rec["asset"] == "USDC"
    assert rec["amount"] == 5_000_000.0
    assert rec["amount_usd"] == 5_000_000.0
    assert rec["value_basis"] == "stable_par"
    assert rec["block"] == 100 and rec["ts"] == 1781000000
    assert rec["timestamp"].startswith("2026-")


def test_normalise_row_drops_self_transfers_and_rows_without_both_sides():
    assert collect.normalise_row(erc20_row(to="0xtarget"), ARB, "erc20", lambda s, d: None) is None
    assert collect.normalise_row(erc20_row(to=""), ARB, "erc20", lambda s, d: None) is None


def test_normalise_row_uses_eighteen_decimals_for_native_transfers():
    row = {"blockNumber": "1", "timeStamp": "1781000000", "hash": "0xh",
           "from": "0xa", "to": "0xb", "value": "1000000000000000000"}
    rec = collect.normalise_row(row, ARB, "native", lambda s, d: 2000.0)
    assert rec["amount"] == 1.0
    assert rec["asset"] == "ETH"
    assert rec["amount_usd"] == 2000.0
    assert rec["value_basis"] == "daily_close"


def test_sweep_writes_records_marks_spam_and_advances_the_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    poison = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    real = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"

    def fake_fetch_kind(address, chain, kind, start, b, **kw):
        if kind != "erc20":
            return WalkResult([], start, 1, False, []), None
        return WalkResult([
            erc20_row(h="0xreal", to=real, value="13000000000000"),
            erc20_row(h="0xpoison", to=poison, value="0", log="1"),
        ], 100, 1, False, []), None

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch_kind)

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert result["chains"]["arbitrum"]["records"] == 1
    assert result["chains"]["arbitrum"]["spam"] == 1
    assert result["chains"]["arbitrum"]["cursor"] == 100

    written = json.loads(next((tmp_path / "transfers" / "arbitrum").glob("*.json")).read_text())
    assert [r["dst"] for r in written] == [real]

    rolled = json.loads((tmp_path / "transfers_spam" / "latest.json").read_text())
    assert rolled["entries"][0]["address"] == poison
    assert rolled["entries"][0]["mimics"] == real

    cursors = json.loads((tmp_path / "state" / "transfer_cursors.json").read_text())
    assert cursors["arbitrum:0xtarget:erc20"] == 100


def test_a_failed_chain_is_recorded_as_degraded_not_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []),
                                                     "Max rate limit reached"))

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert result["chains"]["arbitrum"]["error"] == "Max rate limit reached"
    assert "arbitrum" in result["degraded_sources"]


def test_an_empty_sweep_and_a_failed_sweep_do_not_serialise_identically(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []), None))
    healthy = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []), "boom"))
    failed = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert healthy != failed
    assert healthy["degraded_sources"] == []
    assert failed["degraded_sources"] == ["arbitrum"]


def test_a_non_cluster_wallet_is_probed_before_being_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    swept = []
    monkeypatch.setattr(collect, "probe_activity",
                        lambda a, c, b: (c["name"] == "arbitrum", None))

    def fake_fetch_kind(address, chain, kind, start, b, **kw):
        swept.append(chain["name"])
        return WalkResult([], start, 1, False, []), None

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch_kind)

    result = collect.sweep_wallet("0xfrontier", [ARB, BASE], budget(), cluster=False)

    assert set(swept) == {"arbitrum"}
    assert result["chains"]["base"]["probed_inactive"] is True
    assert result["chains"]["base"]["records"] == 0
    assert result["degraded_sources"] == []


def test_a_failed_probe_degrades_the_chain_rather_than_calling_it_inactive(
        tmp_path, monkeypatch):
    """A probe that could not read must never be recorded as an empty chain."""
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setattr(collect, "probe_activity",
                        lambda a, c, b: (False, "Max rate limit reached"))
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda *a, **k: pytest.fail("must not sweep after a failed probe"))

    result = collect.sweep_wallet("0xfrontier", [BASE], budget(), cluster=False)

    assert result["chains"]["base"]["error"] == "Max rate limit reached"
    assert result["chains"]["base"]["probed_inactive"] is False
    assert result["degraded_sources"] == ["base"]


def test_a_cluster_wallet_is_never_probed(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    def boom(*a, **k):
        raise AssertionError("cluster wallets must be swept unconditionally")

    monkeypatch.setattr(collect, "probe_activity", boom)
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []), None))

    collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)


def test_a_partial_sweep_persists_what_it_collected(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setattr(collect, "fetch_kind", lambda a, c, k, s, b, **kw: (
        WalkResult([erc20_row(h="0xkept", value="13000000000000")], 100, 1, True, []),
        "budget_exhausted:call_budget"))

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert result["chains"]["arbitrum"]["records"] == 1
    assert result["chains"]["arbitrum"]["truncated"] is True
    assert "arbitrum" in result["degraded_sources"]
    written = json.loads(next((tmp_path / "transfers" / "arbitrum").glob("*.json")).read_text())
    assert len(written) == 1


def test_records_for_reads_every_chain_and_excludes_spam(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    for chain in ("arbitrum", "base"):
        d = tmp_path / "transfers" / chain
        d.mkdir(parents=True)
        (d / "2026-08-28.json").write_text(json.dumps([
            {"id": f"{chain}:a", "chain": chain, "src": "0xtarget", "dst": "0xreal",
             "amount_usd": 100.0, "ts": 1, "spam": False},
            {"id": f"{chain}:b", "chain": chain, "src": "0xtarget", "dst": "0xpoison",
             "amount_usd": 0.0, "ts": 2, "spam": True, "spam_reason": "lookalike"},
            {"id": f"{chain}:c", "chain": chain, "src": "0xstranger", "dst": "0xother",
             "amount_usd": 50.0, "ts": 3, "spam": False},
        ]))

    got = collect.records_for("0xTARGET")
    assert {r["chain"] for r in got} == {"arbitrum", "base"}
    assert all(r["dst"] == "0xreal" for r in got)
    assert len(got) == 2


def test_records_for_can_include_spam_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "b", "chain": "arbitrum", "src": "0xtarget", "dst": "0xpoison",
         "amount_usd": 0.0, "ts": 2, "spam": True, "spam_reason": "lookalike"}]))
    assert collect.records_for("0xtarget") == []
    assert len(collect.records_for("0xtarget", include_spam=True)) == 1


def test_records_for_returns_empty_when_nothing_has_been_collected(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "nothing-here")
    assert collect.records_for("0xtarget") == []


def test_sweep_is_skipped_without_an_api_key(tmp_path, monkeypatch):
    """Matches the existing expand_frontier pattern: no key is a named skip, not
    a stream of Invalid API Key errors that burn the whole budget."""
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda *a, **k: pytest.fail("must not call the API"))

    b = budget()
    result = collect.sweep_wallet("0xtarget", [ARB], b, cluster=True)
    assert result["status"] == "skipped_no_api_key"
    assert result["degraded_sources"] == ["arbitrum"]
    assert b.calls_used == 0


def test_sweep_health_summarises_across_wallets():
    results = [
        {"address": "0xa", "chains": {"arbitrum": {"records": 3, "spam": 5, "calls": 2,
                                                   "cursor": 10, "gaps": [], "truncated": False,
                                                   "error": None, "probed_inactive": False}},
         "degraded_sources": []},
        {"address": "0xb", "chains": {"arbitrum": {"records": 1, "spam": 0, "calls": 1,
                                                   "cursor": 12, "gaps": [7], "truncated": False,
                                                   "error": "boom", "probed_inactive": False}},
         "degraded_sources": ["arbitrum"]},
    ]
    health = collect.sweep_health(results)
    assert health["records"] == 4
    assert health["spam_suppressed"] == 5
    assert health["calls"] == 3
    assert health["degraded_sources"] == ["arbitrum"]
    assert health["possible_gaps"] == 1
    assert health["wallets"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_collect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.chain.collect'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/chain/collect.py
"""Sweeping a wallet across chains and writing what we found.

The one entry point is `sweep_wallet`. Everything it depends on is a module
level name so tests can substitute it — the sweep itself is orchestration, and
orchestration is only worth testing if the pieces can be faked.

Two rules this module exists to enforce:

  * A partial sweep persists what it collected. Discarding real history to
    report a failure that is already reported in `degraded_sources` loses data
    for nothing.
  * An empty sweep and a failed sweep never serialise the same way. One says
    "this wallet did nothing"; the other says "we are blind here". Collapsing
    them turns an outage into a silent all-clear.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from src.chain import spam as spam_mod
from src.chain.assets import decimals_of, value_usd
from src.chain.client import fetch_kind, probe_activity
from src.utils import DATA_DIR, append_records, load_all_records, save_latest

TRANSFERS_DIR = DATA_DIR / "transfers"
SPAM_DIR = DATA_DIR / "transfers_spam"
CURSOR_PATH = DATA_DIR / "state" / "transfer_cursors.json"

KINDS = ("erc20", "native", "internal")


def _iso(ts: int) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def read_cursors() -> dict:
    try:
        return json.loads(Path(CURSOR_PATH).read_text())
    except (OSError, ValueError):
        return {}


def write_cursors(cursors: dict) -> None:
    path = Path(CURSOR_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursors, indent=2, sort_keys=True))


def normalise_row(row: dict, chain: dict, kind: str, price_lookup) -> dict | None:
    """One raw Etherscan row into the Phase 1 normalised record."""
    src = (row.get("from") or "").lower()
    dst = (row.get("to") or "").lower()
    if not src or not dst or src == dst:
        return None
    try:
        ts = int(row.get("timeStamp", 0) or 0)
        block = int(row.get("blockNumber", 0) or 0)
        raw = int(row.get("value", 0) or 0)
    except (TypeError, ValueError):
        return None

    decimals = decimals_of(row, kind)
    amount = raw / (10 ** decimals)
    symbol = row.get("tokenSymbol") or (chain["native"] if kind != "erc20" else "")
    date_str = (_iso(ts) or "")[:10]
    amount_usd, basis = value_usd(symbol, amount, date_str, price_lookup)

    index = str(row.get("logIndex") or row.get("traceId") or "0")
    tx_hash = row.get("hash", "")
    return {
        "id": f"{chain['name']}:{tx_hash}:{kind}:{index}",
        "chain": chain["name"],
        "chain_id": chain["chain_id"],
        "block": block,
        "ts": ts,
        "timestamp": _iso(ts),
        "tx_hash": tx_hash,
        "src": src,
        "dst": dst,
        "kind": kind,
        "asset": symbol,
        "token_address": (row.get("contractAddress") or "").lower() or None,
        "amount": amount,
        "amount_usd": amount_usd,
        "value_basis": basis,
        "spam": False,
        "spam_reason": None,
    }


def records_for(wallet: str, *, include_spam: bool = False) -> list[dict]:
    """Every stored record touching `wallet`, across every collected chain.

    The single reader. The graph frontier, the tracer and linkage all need
    exactly this, and defining it three times would guarantee three different
    spam-filtering rules — which is how a quarantined forgery ends up alerting
    through one path while being suppressed on another.
    """
    wl = (wallet or "").lower()
    root = Path(TRANSFERS_DIR)
    if not root.exists():
        return []
    out = []
    for chain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for rec in load_all_records(str(chain_dir)):
            if rec.get("spam") and not include_spam:
                continue
            if wl in ((rec.get("src") or "").lower(), (rec.get("dst") or "").lower()):
                out.append(rec)
    return out


def _blank_chain_result() -> dict:
    return {"records": 0, "spam": 0, "spam_by_reason": {}, "unpriced": 0,
            "calls": 0, "cursor": 0, "gaps": [], "truncated": False,
            "error": None, "probed_inactive": False}


def sweep_wallet(address: str, chains: list[dict], budget, *, cluster: bool = False,
                 price_lookup=None,
                 dust_usd: float = 1.0, page_size: int = 1000,
                 max_pages: int = 50) -> dict:
    """Collect every transfer for one wallet across `chains`.

    `cluster` wallets (the target and its confirmed wallets) are swept
    unconditionally. Everything else is probed first: one call establishes
    whether an address has ever transacted on a chain, which is far cheaper
    than six full sweeps that return nothing.
    """
    addr = (address or "").lower()
    price_lookup = price_lookup or (lambda symbol, date: None)
    cursors = read_cursors()
    result = {"address": addr, "status": "ok", "chains": {},
              "degraded_sources": []}

    # Without a key every request returns "Invalid API Key", which would burn
    # the whole budget producing nothing while looking like a rate-limit
    # problem. Named skip instead, matching expand_frontier's existing pattern.
    if not os.environ.get("ETHERSCAN_API_KEY"):
        result["status"] = "skipped_no_api_key"
        for chain in chains:
            blank = _blank_chain_result()
            blank["error"] = "skipped_no_api_key"
            result["chains"][chain["name"]] = blank
            result["degraded_sources"].append(chain["name"])
        print("[collect] ETHERSCAN_API_KEY absent — sweep SKIPPED.")
        return result

    for chain in chains:
        name = chain["name"]
        chain_result = _blank_chain_result()
        result["chains"][name] = chain_result

        if not cluster:
            before = budget.calls_used
            active, probe_error = probe_activity(addr, chain, budget)
            chain_result["calls"] += budget.calls_used - before
            if probe_error:
                # Could not read the chain. Recording this as "inactive" would
                # claim the wallet has nothing here on the strength of a failed
                # request — blindness dressed as knowledge.
                chain_result["error"] = probe_error
                result["degraded_sources"].append(name)
                continue
            if not active:
                chain_result["probed_inactive"] = True
                continue

        collected: list[dict] = []
        for kind in KINDS:
            key = f"{name}:{addr}:{kind}"
            start = int(cursors.get(key, 0) or 0)
            before = budget.calls_used
            walk, error = fetch_kind(addr, chain, kind, start, budget,
                                     page_size=page_size, max_pages=max_pages)
            chain_result["calls"] += budget.calls_used - before
            chain_result["gaps"].extend(walk.possible_gaps)
            chain_result["truncated"] = chain_result["truncated"] or walk.truncated
            if error:
                chain_result["error"] = error

            for row in walk.rows:
                rec = normalise_row(row, chain, kind, price_lookup)
                if rec is not None:
                    collected.append(rec)

            if walk.last_block > start:
                cursors[key] = walk.last_block
                chain_result["cursor"] = max(chain_result["cursor"], walk.last_block)

        # Volume, not membership: the lookalike rule needs to know which side of
        # a matched pair moved more money. The genuine anchors earn their
        # standing from the records themselves — on the live data the
        # self-wallet and the bridge carry millions, which is exactly why the
        # forgeries of them are detectable.
        volume = spam_mod.counterparty_volume(collected, addr)

        clean, quarantined = [], []
        for rec in collected:
            reason = spam_mod.classify_spam(rec, volume, dust_usd=dust_usd)
            if reason is None:
                clean.append(rec)
                continue
            rec["spam"] = True
            rec["spam_reason"] = reason
            chain_result["spam_by_reason"][reason] = (
                chain_result["spam_by_reason"].get(reason, 0) + 1)
            if reason == "lookalike":
                for side in (rec["src"], rec["dst"]):
                    mimicked = spam_mod.is_lookalike(side, volume,
                                                     dust_usd=dust_usd)
                    if mimicked:
                        rec["mimics"] = mimicked   # the address being forged
                        rec["forged"] = side       # the forgery itself
                        break
            quarantined.append(rec)

        if clean:
            append_records(str(Path(TRANSFERS_DIR) / name), clean, key_field="id")
        if quarantined:
            _merge_spam_rollup(spam_mod.rollup(quarantined, addr))

        chain_result["records"] = len(clean)
        chain_result["spam"] = len(quarantined)
        chain_result["unpriced"] = sum(
            1 for r in clean if r.get("value_basis") == "price_unavailable")
        if chain_result["error"]:
            result["degraded_sources"].append(name)

        # Flushed per chain, not once at the end. append_records dedups by id
        # and is safe to repeat, but _merge_spam_rollup merges by straight
        # addition — so a crash in a later chain would make the next run
        # re-fetch this chain's already-processed range and add its counts on
        # top of the persisted entry, inflating them without bound.
        write_cursors(cursors)

    return result


def _merge_spam_rollup(entries: list[dict]) -> None:
    """Fold this run's quarantine into the persisted rollup."""
    path = Path(SPAM_DIR) / "latest.json"
    try:
        existing = json.loads(path.read_text()).get("entries", [])
    except (OSError, ValueError):
        existing = []

    by_addr = {e["address"]: e for e in existing if e.get("address")}
    for entry in entries:
        prior = by_addr.get(entry["address"])
        if prior is None:
            by_addr[entry["address"]] = entry
            continue
        prior["count"] += entry["count"]
        prior["first_seen"] = min(prior.get("first_seen", 0) or 0, entry["first_seen"])
        prior["last_seen"] = max(prior.get("last_seen", 0) or 0, entry["last_seen"])
        prior["mimics"] = prior.get("mimics") or entry.get("mimics")

    merged = sorted(by_addr.values(), key=lambda e: -e["count"])
    save_latest(str(SPAM_DIR), {
        "last_updated": datetime.now(UTC).isoformat(),
        "suppressed_total": sum(e["count"] for e in merged),
        "entries": merged,
    })


def sweep_health(results: list[dict]) -> dict:
    """One summary across every wallet swept this run."""
    records = spam = calls = gaps = unpriced = 0
    by_reason: dict[str, int] = {}
    degraded: list[str] = []
    for res in results:
        for name, chain_result in res["chains"].items():
            records += chain_result["records"]
            spam += chain_result["spam"]
            calls += chain_result["calls"]
            gaps += len(chain_result["gaps"])
            unpriced += chain_result.get("unpriced", 0)
            for reason, n in (chain_result.get("spam_by_reason") or {}).items():
                by_reason[reason] = by_reason.get(reason, 0) + n
            if chain_result["error"] and name not in degraded:
                degraded.append(name)
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "wallets": len(results),
        "records": records,
        "spam_suppressed": spam,
        # Broken out by reason so a price outage or a poisoning campaign is
        # legible at a glance instead of being one flat number.
        "spam_by_reason": by_reason,
        # Records kept, but whose known asset could not be priced this run.
        # A non-zero value here means the graph is missing real transfers.
        "unpriced": unpriced,
        "calls": calls,
        "possible_gaps": gaps,
        "degraded_sources": sorted(degraded),
        "per_wallet": results,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chain_collect.py -v`
Expected: 9 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: all pass, including the `conftest.py` guard proving nothing touched real `data/`

- [ ] **Step 6: Lint and commit**

```bash
ruff check src tests
git add src/chain/collect.py tests/test_chain_collect.py
git commit -m "feat(chain): wallet sweep orchestration and persistence

Cluster wallets sweep every chain unconditionally; frontier wallets are probed
with one call first. Partial sweeps persist what they collected, and a failed
sweep can never serialise the same way as an empty one — collapsing those turns
an outage into a silent all-clear.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Graph adapter — feed the substrate to the existing brain

**Files:**
- Modify: `src/transfer_graph.py` (add `normalise_transfer_record` near `normalise_l1_transfer:165`; extend `collect_known_edges:1222`; swap the import in `expand_frontier:1457`; extend `known_services` in `run_transfer_graph:1689`)
- Test: `tests/test_chain_graph_adapter.py`

**Interfaces:**
- Consumes: `src.chain.collect` records, `src.chain.labels.service_addresses`
- Produces: `transfer_graph.normalise_transfer_record(rec) -> dict | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_graph_adapter.py
import json

from src import transfer_graph as tg


def record(**kw):
    base = {
        "id": "base:0xhash:erc20:0", "chain": "base", "chain_id": 8453,
        "block": 100, "ts": 1781000000, "timestamp": "2026-06-16T00:00:00+00:00",
        "tx_hash": "0xhash", "src": "0xtarget", "dst": "0xdest", "kind": "erc20",
        "asset": "USDC", "token_address": "0xaf88", "amount": 5_000_000.0,
        "amount_usd": 5_000_000.0, "value_basis": "stable_par",
        "spam": False, "spam_reason": None,
    }
    base.update(kw)
    return base


def test_a_normalised_record_becomes_a_graph_edge_on_its_own_chain():
    edge = tg.normalise_transfer_record(record())
    assert edge["src"] == "0xtarget" and edge["dst"] == "0xdest"
    assert edge["chain"] == "base"
    assert edge["asset"] == "USDC"
    assert edge["amount_usd"] == 5_000_000.0
    assert edge["ts"] == 1781000000
    assert edge["discovery_source"] == tg.SRC_L1


def test_a_quarantined_record_never_becomes_an_edge():
    assert tg.normalise_transfer_record(record(spam=True, spam_reason="lookalike")) is None


def test_an_unpriced_record_never_becomes_an_edge():
    """An unpriced token must not be able to satisfy a value threshold."""
    assert tg.normalise_transfer_record(
        record(amount_usd=None, value_basis="unpriced", asset="SCAM")) is None


def test_a_self_transfer_never_becomes_an_edge():
    assert tg.normalise_transfer_record(record(dst="0xtarget")) is None


def test_the_same_transfer_from_legacy_and_new_storage_collapses_to_one_edge():
    """data/l1_transactions is the only copy of some history, so both readers
    stay live. The same movement must not be counted twice."""
    legacy = tg.normalise_l1_transfer({
        "from": "0xtarget", "to": "0xdest", "value": "5000000000000",
        "timeStamp": "1781000000", "hash": "0xhash", "tokenSymbol": "USDC"})
    fresh = tg.normalise_transfer_record(record(chain="arbitrum", chain_id=42161))
    assert legacy["id"] == fresh["id"]
    assert len(tg.dedupe_edges([legacy, fresh])) == 1


def test_collect_known_edges_reads_every_chain_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    for chain in ("arbitrum", "base"):
        d = tmp_path / "transfers" / chain
        d.mkdir(parents=True)
        (d / "2026-08-28.json").write_text(json.dumps([
            record(chain=chain, id=f"{chain}:0xh:erc20:0", tx_hash=f"0xh{chain}")]))

    edges = tg.collect_known_edges()
    assert {e["chain"] for e in edges} == {"arbitrum", "base"}


def test_collect_known_edges_still_reads_legacy_l1_transactions(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    legacy = tmp_path / "l1_transactions"
    legacy.mkdir(parents=True)
    (legacy / "2026-06-16.json").write_text(json.dumps([{
        "from": "0xtarget", "to": "0xlegacy", "value": "5000000000000",
        "timeStamp": "1781000000", "hash": "0xold", "tokenSymbol": "USDC"}]))

    edges = tg.collect_known_edges()
    assert any(e["dst"] == "0xlegacy" for e in edges)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_graph_adapter.py -v`
Expected: FAIL — `AttributeError: module 'src.transfer_graph' has no attribute 'normalise_transfer_record'`

- [ ] **Step 3: Add the adapter after `normalise_l1_transfer` (line 188)**

```python
def normalise_transfer_record(rec: dict) -> dict | None:
    """A src/chain normalised record into a graph edge.

    Quarantined and unpriced records are dropped here rather than filtered by
    the caller, so there is exactly one place that decides what the graph is
    allowed to reason over. An unpriced token must never be able to satisfy a
    value threshold; a poisoning forgery must never become a node.

    The edge id is chain-scoped and hash-scoped, so a movement that arrives both
    from data/l1_transactions and from data/transfers collapses to one edge.
    """
    if rec.get("spam"):
        return None
    amount_usd = rec.get("amount_usd")
    if amount_usd is None:
        return None
    src = (rec.get("src") or "").lower()
    dst = (rec.get("dst") or "").lower()
    if not src or not dst or src == dst:
        return None
    chain = rec.get("chain") or CHAIN_ARBITRUM
    ref = rec.get("tx_hash", "")
    try:
        ts = int(rec.get("ts", 0) or 0)
    except (TypeError, ValueError):
        ts = 0
    return {
        "id": edge_id(src, dst, chain, ref, ts),
        "src": src,
        "dst": dst,
        "chain": chain,
        "asset": rec.get("asset") or "UNKNOWN",
        "amount_usd": round(float(amount_usd), 2),
        "ref": ref,
        "ts": ts,
        "timestamp": _iso(ts),
        "discovery_source": SRC_L1,
        "kind": rec.get("kind"),
    }
```

- [ ] **Step 4: Extend `collect_known_edges` (line 1222)**

Insert this block immediately after the `l1_transactions` loop and before the `ledger` loop:

```python
    # Multi-chain substrate written by src/chain/collect.py. Read before the
    # legacy table so the richer record wins on any field the two share; the
    # legacy reader stays live because data/l1_transactions is the only copy of
    # history collected before the substrate existed.
    transfers_root = DATA_DIR / "transfers"
    if transfers_root.exists():
        for chain_dir in sorted(p for p in transfers_root.iterdir() if p.is_dir()):
            for rec in load_all_records(str(chain_dir)):
                e = normalise_transfer_record(rec)
                if e:
                    edges.append(e)
```

- [ ] **Step 5: Swap the frontier's reader**

There is exactly **one** call site. Two edits, both inside `expand_frontier`.

**5a.** Replace the import at line 1457:

```python
    from src.tracer import get_usdc_transfers
```

with:

```python
    from src.chain.budget import CallBudget
    from src.chain.chains import enabled_chains
    from src.chain.collect import records_for, sweep_wallet

    # The frontier's own ceiling, expressed in the units the substrate spends.
    # max_expansions counted wallet lookups when one wallet cost one call; a
    # wallet now costs up to three calls per chain, so the budget has to be
    # denominated in calls or the ceiling silently means something else.
    sweep_budget = CallBudget(
        max_calls=budget["max_expansions"] * len(enabled_chains(load_config())) * 3,
        seconds=budget["time_budget_seconds"])
    sweep_chains = enabled_chains(load_config())
```

**5b.** Replace line 1531 and the normalisation loop that follows it. The current code is:

```python
                    rows = list(get_usdc_transfers(wallet))
```

...followed at line 1544 by `for tx in rows:` / `e = normalise_l1_transfer(tx)`. Replace the fetch with:

```python
                    sweep_wallet(wallet, sweep_chains, sweep_budget, cluster=False)
                    rows = records_for(wallet)
```

and change the two lines at the top of the loop from:

```python
                for tx in rows:
                    e = normalise_l1_transfer(tx)
```

to:

```python
                for rec in rows:
                    e = normalise_transfer_record(rec)
```

The rest of that loop body is unchanged — it already reads `e["id"]`, `e["src"]`, `e["dst"]` and `e["amount_usd"]`, all of which `normalise_transfer_record` produces with the same names and types.

> The dust check inside that loop (`amount_usd < dust_usd`) now runs on records the substrate already quarantined, so it is redundant but harmless. Leave it: it is the guard `_expandable_edges` matches against, and the two must stay in agreement.

- [ ] **Step 6: Seed `known_services` from the registry (line 1689)**

In `run_transfer_graph`, after the existing `known_services` assembly:

```python
    from src.chain.labels import load_registry, service_addresses
    known_services |= service_addresses(load_registry(DATA_DIR / "labels" / "entities.json"))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_chain_graph_adapter.py tests/test_transfer_graph.py tests/test_frontier_retention.py -v`
Expected: all pass — the pre-existing graph tests are the regression guard

- [ ] **Step 8: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: all pass

- [ ] **Step 9: Lint and commit**

```bash
ruff check src tests
git add src/transfer_graph.py tests/test_chain_graph_adapter.py
git commit -m "feat(graph): read the multi-chain substrate

collect_known_edges gains a reader for data/transfers/**, the frontier expands
via sweep_wallet instead of Arbitrum-USDC-only lookups, and known_services is
seeded from the curated label registry.

Quarantined and unpriced records are rejected in one place, so an airdrop token
can never satisfy a value threshold. The legacy l1_transactions reader stays
live and the shared edge id collapses the overlap.

No grading rule, confidence weight or alert gate changed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: All-chain address reuse in linkage

**Files:**
- Modify: `src/linkage.py:105-125` (`get_outbound_usdc_addresses`) and `src/linkage.py:128-147` (`target_l1_profile`)
- Test: `tests/test_migration_signals.py`, in its existing `# --- linkage ---` section. That file already imports `linkage` and covers `compute_linkage`, so it is the established home. **Not** `tests/test_wallet_links.py` — despite the name, that module is about Hypurrscan URL construction and imports `src.links`, an unrelated concern.

**Interfaces:**
- Consumes: `src.utils.load_all_records`, `src.chain.chains.enabled_chains`
- Produces: `get_outbound_addresses(wallet, config=None) -> set[str]`. `get_outbound_usdc_addresses` is kept as a thin alias so no existing caller breaks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wallet_links.py`:

```python
def test_outbound_addresses_come_from_every_chain_without_api_calls(tmp_path, monkeypatch):
    """Address reuse is the strongest linkage signal available, and it was
    limited to Arbitrum USDC. The substrate already holds every chain, so
    widening it costs nothing."""
    import json

    from src import linkage
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(linkage, "etherscan_get",
                        lambda *a, **k: pytest.fail("must not call the API"))

    for chain, dst in (("arbitrum", "0xdeposita"), ("base", "0xdepositb")):
        d = tmp_path / "transfers" / chain
        d.mkdir(parents=True)
        (d / "2026-08-28.json").write_text(json.dumps([{
            "id": f"{chain}:0xh:erc20:0", "chain": chain, "src": "0xtarget",
            "dst": dst, "amount_usd": 500000.0, "ts": 1781000000,
            "spam": False, "value_basis": "stable_par", "asset": "USDC"}]))

    got = linkage.get_outbound_addresses("0xtarget")
    assert got == {"0xdeposita", "0xdepositb"}


def test_outbound_addresses_exclude_spam_and_the_bridge(tmp_path, monkeypatch):
    import json

    from src import linkage
    from src.chain import collect
    from src.utils import load_config

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    bridge = load_config()["hl_bridge_contract"].lower()

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": "0xtarget", "dst": bridge,
         "amount_usd": 1.0, "ts": 1, "spam": False},
        {"id": "b", "chain": "arbitrum", "src": "0xtarget", "dst": "0xpoison",
         "amount_usd": 0.0, "ts": 2, "spam": True, "spam_reason": "lookalike"},
        {"id": "c", "chain": "arbitrum", "src": "0xtarget", "dst": "0xreal",
         "amount_usd": 900.0, "ts": 3, "spam": False},
        {"id": "d", "chain": "arbitrum", "src": "0xstranger", "dst": "0xtarget",
         "amount_usd": 900.0, "ts": 4, "spam": False},
    ]))

    assert linkage.get_outbound_addresses("0xtarget") == {"0xreal"}
```

Add `import pytest` at the top of the file if it is not already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wallet_links.py -v`
Expected: FAIL — `AttributeError: module 'src.linkage' has no attribute 'get_outbound_addresses'`

- [ ] **Step 3: Replace `get_outbound_usdc_addresses`**

```python
def get_outbound_addresses(wallet: str, config: dict | None = None) -> set:
    """Every address this wallet has sent value to, on every collected chain.

    Reads the substrate rather than the API: src/chain/collect.py has already
    stored these, so widening the strongest linkage signal we have — a CEX
    deposit address belongs to exactly one account, so two wallets funding the
    same one are the same customer — from Arbitrum USDC to every chain and asset
    costs no calls at all.
    """
    from src.chain.collect import records_for
    from src.chain.labels import load_registry, service_addresses

    config = config or load_config()
    wl = (wallet or "").lower()

    # A shared destination is evidence only when it could be a private deposit
    # address. Routers, wrapper contracts and exchange HOT wallets receive from
    # millions of unrelated people, so an overlap there is coincidence — and
    # this result feeds a bonus the module calls "cryptographic certainty" and
    # fires a standalone alert, so a coincidence reaches the user as a
    # confident ownership claim. Widening from one chain and one asset to six
    # chains, every asset and three record kinds is exactly what makes that
    # collision likely enough to defend against.
    excluded = service_addresses(load_registry(DATA_DIR / "labels" / "entities.json"))
    excluded |= {a.lower() for a in config.get("known_service_addresses", [])}
    excluded.add(config["hl_bridge_contract"].lower())
    excluded.add(wl)

    out = set()
    for rec in records_for(wl):
        if (rec.get("src") or "").lower() != wl:
            continue
        usd = rec.get("amount_usd")
        if usd is None or float(usd) <= 0:
            continue
        dst = (rec.get("dst") or "").lower()
        if dst and dst not in excluded:
            out.add(dst)
    return out


def get_outbound_usdc_addresses(wallet: str, limit: int = 300) -> set:
    """Backwards-compatible alias. `limit` is unused: the substrate is complete,
    so there is no page to cap."""
    return get_outbound_addresses(wallet)
```

- [ ] **Step 4: Point `target_l1_profile` at the same source**

Replace the `out_addrs` construction in `target_l1_profile` (lines 136-142) with:

```python
    out_addrs = get_outbound_addresses(target, config)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_wallet_links.py -v`
Expected: all pass

- [ ] **Step 6: Lint and commit**

```bash
ruff check src tests
git add src/linkage.py tests/test_wallet_links.py
git commit -m "feat(linkage): address reuse across every chain, at zero API cost

A CEX deposit address belongs to exactly one exchange account, so two wallets
funding the same one are the same customer — the strongest linkage signal
available, and it was limited to Arbitrum USDC through a capped API page.

Reads the substrate instead. Every chain, every asset, no calls.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: Point the tracer at the substrate

The tracer's outputs must not change. `fund_flows` findings and `alert_fund_movement` are consumed by the dashboard and the combined-alert path.

**Files:**
- Modify: `src/tracer.py:43-76` (`get_usdc_transfers`), `src/tracer.py:109-128` (`trace_outbound_transfers`)
- Test: `tests/test_chain_tracer_substrate.py`

**Interfaces:**
- Consumes: `src.chain.collect.sweep_wallet`, `src.chain.collect.records_for`, `src.chain.chains.enabled_chains`
- Produces: nothing new. `trace_outbound_transfers` keeps its existing signature and return shape; `_as_etherscan_row` is private.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_tracer_substrate.py
import json

from src import tracer


def substrate_record(**kw):
    base = {"id": "base:0xh:erc20:0", "chain": "base", "chain_id": 8453,
            "block": 1, "ts": 1781000000, "timestamp": "2026-06-16T00:00:00+00:00",
            "tx_hash": "0xh", "src": "0xtarget", "dst": "0xdest", "kind": "erc20",
            "asset": "USDC", "amount": 100.0, "amount_usd": 100.0,
            "value_basis": "stable_par", "spam": False, "spam_reason": None}
    base.update(kw)
    return base


def test_outbound_transfers_keep_the_etherscan_row_shape(tmp_path, monkeypatch):
    """unique_destinations and build_finding read `to`, `value` and `hash`.
    Those keys must survive the switch or the alert path breaks silently."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", dst="0xdest", amount=250.0, amount_usd=250.0)]))

    out = tracer.trace_outbound_transfers("0xtarget")
    assert len(out) == 1
    assert out[0]["to"] == "0xdest"
    assert out[0]["hash"] == "0xh"
    assert int(out[0]["value"]) == 250_000_000        # 250 USDC at 6 decimals
    assert tracer.unique_destinations(out, "0xtarget")[0]["to"] == "0xdest"


def test_outbound_transfers_exclude_quarantined_records(tmp_path, monkeypatch):
    """905 of 1000 live records are poisoning. If they reached this function
    they would each raise a fund-movement alert."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", id="a", dst="0xpoison",
                         spam=True, spam_reason="lookalike"),
        substrate_record(chain="arbitrum", id="b", dst="0xreal"),
    ]))

    assert [r["to"] for r in tracer.trace_outbound_transfers("0xtarget")] == ["0xreal"]


def test_outbound_transfers_exclude_inbound_ones(tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", id="a", src="0xfunder", dst="0xtarget"),
        substrate_record(chain="arbitrum", id="b", src="0xtarget", dst="0xreal"),
    ]))

    assert [r["to"] for r in tracer.trace_outbound_transfers("0xtarget")] == ["0xreal"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_tracer_substrate.py -v`
Expected: FAIL — `AttributeError: module 'src.tracer' has no attribute 'sweep_wallet'`

- [ ] **Step 3: Rewire `trace_outbound_transfers` onto the substrate**

Add these imports at the top of `src/tracer.py`:

```python
from src.chain.budget import CallBudget
from src.chain.chains import enabled_chains
from src.chain.collect import records_for, sweep_wallet
```

**Remove `read_cursor` and `write_cursor` from the `src.utils` import block** (lines 19 and 21). They were used only by the old `trace_outbound_transfers` body, and ruff's `F401` will fail the build if they are left behind. `append_records` stays — `save_fund_flow_findings` still uses it.

Add after `get_usdc_transfers`:

```python
def _as_etherscan_row(rec: dict) -> dict:
    """A normalised record in the row shape the finding builders already read.

    unique_destinations and build_finding index `to`, `value` and `hash`, and
    value is expected in 6-decimal USDC units. Converting here keeps the whole
    downstream alert path — which the dashboard and the combined-alert route
    depend on — byte-identical.
    """
    usd = rec.get("amount_usd") or 0.0
    return {
        "to": rec.get("dst", ""),
        "from": rec.get("src", ""),
        "value": str(int(round(float(usd) * 1e6))),
        "hash": rec.get("tx_hash", ""),
        "blockNumber": str(rec.get("block", 0)),
        "timeStamp": str(rec.get("ts", 0)),
        "tokenSymbol": rec.get("asset", ""),
        "chain": rec.get("chain", CHAIN_DEFAULT),
    }
```

Add near the top of the module, beside `MAX_DESTINATIONS`:

```python
CHAIN_DEFAULT = "arbitrum"
```

Replace the body of `trace_outbound_transfers`:

```python
def trace_outbound_transfers(wallet: str) -> list[dict]:
    """Find transfers OUT from the tracked wallet, on every collected chain.

    Collection is delegated to the substrate, which paginates properly and
    quarantines poisoning; this function is now only about selecting the
    outbound side of it.
    """
    config = load_config()
    budget = CallBudget(
        max_calls=(config.get("collection") or {}).get("max_calls_per_run", 2500),
        seconds=(config.get("collection") or {}).get("time_budget_seconds", 420),
    )
    sweep_wallet(wallet, enabled_chains(config), budget, cluster=True)

    wl = (wallet or "").lower()
    return [_as_etherscan_row(r) for r in records_for(wl)
            if (r.get("src") or "").lower() == wl]
```

> `records_for` already excludes quarantined records, so a poisoning forgery can
> never reach `alert_fund_movement`. On the live data that is 905 of 1000 records
> that would each otherwise have raised an alert.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_chain_tracer_substrate.py -v`
Expected: 3 passed

- [ ] **Step 5: Verify the alert path is unchanged**

Run: `python -m pytest tests/test_dedup_and_alerts.py tests/test_migration_signals.py tests/test_market_alert_routing.py -v`
Expected: all pass

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: all pass

- [ ] **Step 7: Lint and commit**

```bash
ruff check src tests
git add src/tracer.py tests/test_chain_tracer_substrate.py
git commit -m "feat(tracer): collect through the substrate, outputs unchanged

The tracer now sees every chain and asset, with proper pagination and poisoning
quarantined — on the live data that is 905 spurious fund-movement alerts out of
1000 records that can no longer fire.

fund_flows findings and alert_fund_movement keep their exact shape: the row
adapter preserves the to/value/hash keys the finding builders index.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: Backfill mode, workflow wiring, and documentation

This is the task that actually recovers the evicted history and answers where the $13M went.

**Files:**
- Create: `scripts/backfill_transfers.py`
- Modify: `.github/workflows/backfill.yml`, `.github/workflows/trace.yml`, `README.md`
- Test: `tests/test_chain_backfill.py`

**Interfaces:**
- Consumes: `src.chain.collect.sweep_wallet`, `sweep_health`, `src.chain.chains.enabled_chains`, `src.chain.budget.CallBudget`
- Produces: `scripts/backfill_transfers.py: cluster_wallets(config) -> list[str]`, `main(argv=None) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chain_backfill.py
import json

from scripts import backfill_transfers as bf


def test_cluster_wallets_is_the_target_plus_known_self_wallets():
    cfg = {"target_wallet": "0xTARGET",
           "known_self_wallets": ["0xSELF", "0xtarget"]}
    assert bf.cluster_wallets(cfg) == ["0xtarget", "0xself"]


def test_backfill_resets_cursors_so_history_is_re_read(tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    (tmp_path / "cursors.json").write_text(json.dumps({"arbitrum:0xtarget:erc20": 477345405}))

    bf.reset_cursors(["0xtarget"])
    assert collect.read_cursors() == {}


def test_backfill_only_resets_the_wallets_it_was_asked_for(tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    (tmp_path / "cursors.json").write_text(json.dumps({
        "arbitrum:0xtarget:erc20": 100, "arbitrum:0xother:erc20": 200}))

    bf.reset_cursors(["0xtarget"])
    assert collect.read_cursors() == {"arbitrum:0xother:erc20": 200}


def test_main_writes_sweep_health(tmp_path, monkeypatch):
    from src.chain import collect

    # main() resets cursors before sweeping. Without this the test writes to the
    # real data/state/transfer_cursors.json — conftest's backstop only watches
    # the alert-health file, so this one would slip through into a commit.
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget",
        "chains": {"arbitrum": {"records": 7, "spam": 905, "calls": 3, "cursor": 9,
                                "gaps": [], "truncated": False, "error": None,
                                "probed_inactive": False}},
        "degraded_sources": []})
    monkeypatch.setattr(bf, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(bf, "load_config", lambda: {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})

    assert bf.main([]) == 0
    health = json.loads((tmp_path / "transfers" / "latest.json").read_text())
    assert health["records"] == 7
    assert health["spam_suppressed"] == 905
    assert health["degraded_sources"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chain_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_transfers'`

- [ ] **Step 3: Create `scripts/__init__.py` if absent, then the backfill script**

```python
# scripts/backfill_transfers.py
"""Re-collect the cluster's full transfer history across every enabled chain.

Run once after the substrate lands, and any time a chain is added. The regular
trace job is incremental — it resumes from a cursor — so it can never recover
history that was already evicted. On the live data, that history is 905
poisoning records occupying a 1000-row window, which pushed everything older
than 2025-11-30 out of reach and left a $13,000,000 transfer with no onward
trail.

Resetting the cursors is what makes the sweep re-read from block 0.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chain.budget import CallBudget
from src.chain.chains import enabled_chains
from src.chain.collect import (
    TRANSFERS_DIR,
    read_cursors,
    sweep_health,
    sweep_wallet,
    write_cursors,
)
from src.utils import load_config, save_latest


def cluster_wallets(config: dict) -> list[str]:
    """The target plus every wallet already confirmed to be his, deduplicated."""
    wallets = [config["target_wallet"]] + list(config.get("known_self_wallets", []))
    seen, out = set(), []
    for w in wallets:
        wl = (w or "").lower()
        if wl and wl not in seen:
            seen.add(wl)
            out.append(wl)
    return out


def reset_cursors(wallets: list[str]) -> None:
    """Drop the resume points for these wallets so the sweep starts at block 0."""
    targets = {(w or "").lower() for w in wallets}
    cursors = read_cursors()
    kept = {k: v for k, v in cursors.items()
            if len(k.split(":")) < 2 or k.split(":")[1] not in targets}
    write_cursors(kept)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet", action="append", default=None,
                        help="sweep this address instead of the cluster; repeatable")
    parser.add_argument("--keep-cursors", action="store_true",
                        help="incremental sweep instead of full history")
    args = parser.parse_args(argv)

    config = load_config()
    wallets = [w.lower() for w in args.wallet] if args.wallet else cluster_wallets(config)
    collection = config.get("collection") or {}

    if not args.keep_cursors:
        reset_cursors(wallets)
        print(f"[backfill] cursors reset for {len(wallets)} wallet(s) — reading full history")

    budget = CallBudget(
        max_calls=collection.get("max_calls_per_run", 2500),
        seconds=collection.get("time_budget_seconds", 420),
    )

    results = []
    for wallet in wallets:
        print(f"[backfill] sweeping {wallet} across "
              f"{len(enabled_chains(config))} chain(s)")
        results.append(sweep_wallet(wallet, enabled_chains(config), budget,
                                    cluster=True))

    health = sweep_health(results)
    save_latest(str(TRANSFERS_DIR), health)
    print(f"[backfill] {health['records']} record(s), "
          f"{health['spam_suppressed']} suppressed as spam, "
          f"{health['calls']} API call(s)")
    if health["degraded_sources"]:
        print(f"[backfill] DEGRADED: could not fully read {health['degraded_sources']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_chain_backfill.py -v`
Expected: 4 passed

- [ ] **Step 5: Wire the workflows**

In `.github/workflows/backfill.yml`, add this step before the commit step (match the surrounding step's `env` block, which already carries `ETHERSCAN_API_KEY`):

```yaml
      - name: Backfill multi-chain transfer history
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
        run: python scripts/backfill_transfers.py
```

In `.github/workflows/trace.yml`, add a manual input so a single wallet can be investigated on demand, and pass it through. Replace the `on:` block:

```yaml
on:
  schedule:
    - cron: '*/30 * * * *'
  workflow_dispatch:
    inputs:
      investigate_wallet:
        description: 'Sweep this address across all chains before tracing'
        required: false
        type: string
```

and add this step immediately before `- run: python src/tracer.py`:

```yaml
      - name: Investigate a specific wallet
        if: inputs.investigate_wallet != ''
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
        run: python scripts/backfill_transfers.py --wallet "${{ inputs.investigate_wallet }}"
```

- [ ] **Step 6: Validate the workflow YAML parses**

Run: `python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ['.github/workflows/trace.yml','.github/workflows/backfill.yml']]; print('ok')"`
Expected: `ok`
(If `yaml` is missing: `pip install pyyaml` — it is a dev-only check, do not add it to `requirements.txt`.)

- [ ] **Step 7: Document it in the README**

Add this section immediately before `## Known rough edges`:

```markdown
## Transfer substrate

`src/chain/` collects transfers and writes `data/transfers/{chain}/YYYY-MM-DD.json`.
Everything downstream — the transfer graph, the tracer, linkage — reads it rather
than calling Etherscan directly.

What it does that the previous single-endpoint collection did not:

- **Six chains, not one.** Etherscan V2 serves Arbitrum, Ethereum, Base, Optimism,
  Polygon and BSC from the same API key by varying `chainid`. Configure in
  `config.json` under `chains`.
- **Three record kinds, not one.** `tokentx`, `txlist` and `txlistinternal`.
  Internal transactions matter most: a contract-mediated transfer, which is what
  every bridge emits, appears in neither of the others.
- **Block-range pagination.** The old collection asked for `page=1, offset=1000,
  sort=desc` exactly once, so `data/l1_transactions/` capped at 1000 records
  forever. The walker steps forward by block with no ceiling.
- **Poisoning quarantine.** 905 of those 1000 records moved under a dollar, and a
  single forgery of the known self-wallet accounted for 510. Forged addresses are
  matched on their first and last 4 hex characters and rolled up into
  `data/transfers_spam/latest.json` instead of entering the graph.
- **Entity labels.** `data/labels/entities.json` names exchanges, bridges and
  routers; bytecode is checked once per address and cached. A contract can never
  be graded a personal wallet.

**Blindness is reported, never inferred.** `data/transfers/latest.json` carries
`degraded_sources`; a chain that could not be read is recorded there, and an empty
sweep never serialises the same way as a failed one.

To investigate one address on demand, run the **Trace Fund Flows** workflow with
the `investigate_wallet` input, or locally:

```powershell
python scripts/backfill_transfers.py --wallet 0xa95d9c1f655341597c94393fddc30cf3c08e4fce
```
```

Also update the `## Layout` block to add the new package:

```
  chain/          multi-chain collection substrate (client, spam, labels, assets)
```

- [ ] **Step 8: Run the whole suite one final time**

Run: `python -m pytest tests -q`
Expected: all pass

- [ ] **Step 9: Lint and commit**

```bash
ruff check src tests scripts
git add scripts/backfill_transfers.py tests/test_chain_backfill.py .github/workflows/backfill.yml .github/workflows/trace.yml README.md
git commit -m "feat(backfill): recover the full multi-chain history

The trace job is incremental and can never recover history already evicted from
the 1000-row window. This resets cursors and re-reads from block 0 across every
enabled chain.

Also adds an investigate_wallet dispatch input, so a single address can be swept
on demand instead of waiting for the cron.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification: what "done" means for Phase 1

After Task 12, run the backfill workflow manually and confirm each of these against `data/`:

- [ ] `data/transfers/` contains a directory per enabled chain the target has touched
- [ ] `data/transfers/latest.json` reports `records`, `spam_suppressed`, `calls`, `degraded_sources`
- [ ] `data/transfers_spam/latest.json` lists `0x1419b0d742da87d053373018740e7c3a41402d5f` with `mimics` set to `0x1419e75330c71ce463102e6a1eb62fe80b412d5f`
- [ ] `data/transfer_graph/latest.json` `chains` contains more than `["arbitrum", "hyperliquid"]` **if** the target has moved value off Arbitrum
- [ ] `data/transfer_graph/latest.json` `services` contains more than three entries
- [ ] No node classified `MIGRATION_CANDIDATE` has bytecode
- [ ] `0xa95d9c1f655341597c94393fddc30cf3c08e4fce` has outbound edges, or `data/transfers/latest.json` explains why not (`degraded_sources`, or a genuinely empty result)

The last item is the one that matters. It is the $13,000,000 question, and Phase 1 is successful if the system can now either answer it or say precisely why it cannot.
