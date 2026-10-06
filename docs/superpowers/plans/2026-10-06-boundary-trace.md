# Boundary Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Watch every way value crosses Hyperliquid's edge and join it against a table of his addresses from both sides — who paid his world (attribution), where every large new account's money came from (provenance) — and make the correlator respect custody-gap physics, with a dashboard page that shows it all.

**Architecture:** New pure package `src/boundary/` (logs, bridge2, unit, perimeter, attribution, readers, provenance, gaps) with all IO injected; three single-writer scripts (`build_perimeter.py` and `run_provenance.py` in trace.yml, `check_boundary.py` in watch.yml); roster, alerts, feed health, correlator and movements wired to the new files; a Svelte `/trace` page.

**Tech Stack:** Python 3.12, requests, pytest (network-free), GitHub Actions; Blockscout keyless APIs (`/api` Etherscan-compatible and `/api/v2`), Etherscan V2 fallback, Hyperliquid info API, Unit operations API; SvelteKit 2 / Svelte 5 dashboard with `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-06-boundary-trace-design.md`

## Global Constraints

- Tests are network-free and never write real `data/` (`tests/conftest.py` probes enforce part of this; every new script test patches `utils.DATA_DIR`).
- A failed read is never an empty answer (rule 5): readers RAISE; callers record `unreadable`.
- A missing value is never 0 (rule 6): unpriced amounts are `None`; a token quantity is never a dollar value (rule 11).
- Unmeasured is never quiet (rule 9).
- Ground truth is config only; perimeter roles beyond `core` are evidence weights, never identity.
- Only `CRITICAL` and `HIGH` route (`alerts.ESCALATING_SEVERITIES`); subjects are `[EZEKIEL] <SEVERITY>: …`.
- Money-flow votes (`transfer`, `linkage`) stay one "financial" family; nothing here edits `assign_tier` or `evidence.py`.
- Single writer per file: `data/perimeter/` ← build_perimeter (trace.yml); `data/boundary/` ← check_boundary (watch.yml); `data/provenance/` ← run_provenance (trace.yml).
- Every feed file registered in `feed_health` ends in `latest.json`; limits ≥ 360 min.
- Workflow detector steps carry `if: ${{ !cancelled() && steps.deps.conclusion == 'success' }}` and a `timeout-minutes`; no `continue-on-error`.
- `python -m ruff check src/ tests/ scripts/` clean before every commit.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Airdrop spam from HyperCore distributors** (tokens with a `usdcValue`, sent to his account by a fan-out hub) must not read as "an outside account paid his world" → `from_hl_edges` skips hubs and system addresses; test in Task 5.
2. **A page of exactly 1,000 logs whose last block is cut in half** must neither skip nor duplicate logs → resume at that block and dedupe; test in Task 1.
3. **Blockscout or Etherscan answering an error with an empty result** (`status "0"`, message not "No logs/records found") must stop the walk with the cursor unmoved → test in Task 1.
4. **The perimeter file missing or empty** (first deploy, before trace.yml has run) must still leave the tripwire live for the config wallets → `check_boundary` falls back to a core-only perimeter; test in Task 7.
5. **A correlator match whose provenance could only be partly read** must be kept as `route_unknown`, never dropped as "no exchange source" → test in Task 11.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/boundary/__init__.py` | Package docstring |
| `src/boundary/logs.py` | Strict `getLogs` (Blockscout → Etherscan), head, block-by-time, cursor walk |
| `src/boundary/bridge2.py` | Bridge2 constants, decoders, topic builders |
| `src/boundary/unit.py` | Unit operations reader + normaliser |
| `src/boundary/perimeter.py` | Perimeter build, associates, exchange families, `Index` |
| `src/boundary/attribution.py` | Event normalisers, classification rules, finding keys, titles |
| `src/boundary/readers.py` | Blockscout v2 IO for provenance/retro: inbound transfers, first gas, tx logs, budget |
| `src/boundary/provenance.py` | Route entries, resolution ≤ 2 hops, findings, shared funders |
| `src/boundary/gaps.py` | Custody-gap exits, route-consistency filter |
| `scripts/build_perimeter.py` | trace.yml step: build + close the loop on HL |
| `scripts/check_boundary.py` | watch.yml step: live withdrawals, core withdrawals, Unit, retro, attribution |
| `scripts/run_provenance.py` | trace.yml step: deposit feed + pool, queue, resolve, findings |
| `src/alerts.py` | `alert_boundary_finding`, `alert_provenance_hit`, `alert_perimeter_hl_account` |
| `src/roster.py` | Votes/evidence from `boundary` and `provenance` findings |
| `src/feed_health.py` | Three feeds + blind checks |
| `src/correlator.py` | Gap exits, complete bridge pool, route filter |
| `src/movements.py` | Exact nonce resolution of HL withdrawals |
| `src/route_index.py` | No candidates for zero/system addresses |
| `.github/workflows/watch.yml`, `trace.yml` | New steps, correlator moved after provenance |
| `dashboard/src/lib/api.js`, `dashboard/src/routes/trace/+page.svelte`, `+layout.svelte`, `transfers/+page.svelte` | Trace page, feeds, nav, known-wallet label |
| `tests/fixtures/boundary/*.json` | Real captured answers |
| `tests/test_boundary_*.py`, `tests/test_build_perimeter.py`, `tests/test_check_boundary.py`, `tests/test_run_provenance.py` | Tests |

### Task 1: Strict log reader and cursor walk (`src/boundary/logs.py`)

**Files:**
- Create: `src/boundary/__init__.py`, `src/boundary/logs.py`
- Test: `tests/test_boundary_logs.py`

**Interfaces:**
- Produces: `LogReadError`; `rows_of(doc, source) -> list[dict]`; `read_logs(chain, address, topics: dict[int,str], from_block, to_block, *, blockscout=None, etherscan=None, has_key=None) -> list[dict]`; `head_block(chain, *, get=None) -> int`; `block_at(chain, ts, *, closest="before", get=None) -> int`; `walk(read, start_block, head, *, max_calls=40, seconds=60.0, chunk=CHUNK_BLOCKS, clock=time.monotonic) -> {"logs","last_block","calls","complete","error"}`; `log_key(row) -> tuple`; `to_int(value) -> int`; constants `BLOCKSCOUT_API` (arbitrum, ethereum, base, optimism, polygon), `ETHERSCAN_CHAIN_IDS`, `PAGE=1000`, `CHUNK_BLOCKS=100_000`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_logs.py`:

```python
"""Strict getLogs and a cursor that never skips (spec §5)."""

import pytest

from src.boundary import logs


def test_a_genuine_empty_answer_is_empty():
    assert logs.rows_of({"status": "0", "message": "No logs found", "result": []}, "bs") == []
    assert logs.rows_of({"status": "0", "message": "No records found", "result": []}, "es") == []


def test_an_error_answer_is_never_empty():
    for doc in ({"status": "0", "message": "Too many requests", "result": None},
                {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"},
                {"status": "0", "message": "Invalid API Key", "result": []},
                [], None):
        with pytest.raises(logs.LogReadError):
            logs.rows_of(doc, "x")


def test_params_join_topics_with_and():
    p = logs._params("0xabc", {0: "0xt0", 2: "0xt2"}, 5, 9)
    assert p["topic0"] == "0xt0" and p["topic2"] == "0xt2" and p["topic0_2_opr"] == "and"
    assert p["fromBlock"] == 5 and p["toBlock"] == 9 and "topic1" not in p


def _log(block, tx="0xaa", index=0):
    return {"blockNumber": hex(block), "transactionHash": tx, "logIndex": hex(index)}


def test_walk_reads_in_chunks_and_advances_the_cursor():
    seen = []

    def read(lo, hi):
        seen.append((lo, hi))
        return [_log(lo, tx=f"0x{lo}")]
    out = logs.walk(read, 100, 349, chunk=100)
    assert seen == [(100, 199), (200, 299), (300, 349)]
    assert out["last_block"] == 349 and out["complete"] and out["error"] is None
    assert len(out["logs"]) == 3


def test_a_full_page_resumes_at_its_last_block_without_duplicates():
    page1 = [_log(100 + i // 10, tx=f"0x{i}", index=i) for i in range(1000)]   # blocks 100..199
    rest = [_log(199, tx="0x999", index=999), _log(199, tx="0xnew", index=1)]
    calls = []

    def read(lo, hi):
        calls.append((lo, hi))
        return page1 if len(calls) == 1 else rest
    out = logs.walk(read, 100, 250, chunk=1000)
    assert calls == [(100, 250), (199, 250)]
    keys = [logs.log_key(r) for r in out["logs"]]
    assert len(keys) == len(set(keys)) == 1001
    assert out["last_block"] == 250 and out["complete"]


def test_an_error_stops_the_walk_without_moving_past_the_unread_range():
    def read(lo, hi):
        if lo >= 200:
            raise logs.LogReadError("blockscout: 429")
        return []
    out = logs.walk(read, 100, 400, chunk=100)
    assert out["last_block"] == 199 and not out["complete"] and "429" in out["error"]


def test_a_single_block_with_a_full_page_is_refused():
    out = logs.walk(lambda lo, hi: [_log(lo, tx=f"0x{i}", index=i) for i in range(1000)],
                    7, 50, chunk=100)
    assert out["last_block"] == 6 and "partial block" in out["error"]


def test_the_walk_respects_its_call_budget():
    out = logs.walk(lambda lo, hi: [], 0, 10_000, chunk=10, max_calls=3)
    assert out["calls"] == 3 and out["last_block"] == 29 and not out["complete"]


def test_read_logs_falls_back_to_etherscan_only_with_a_key():
    def bs(*a, **k):
        raise logs.LogReadError("blockscout: 429")

    def es(*a, **k):
        return [{"ok": 1}]
    assert logs.read_logs("arbitrum", "0xa", {0: "0x1"}, 1, 2, blockscout=bs, etherscan=es,
                          has_key=True) == [{"ok": 1}]
    with pytest.raises(logs.LogReadError):
        logs.read_logs("arbitrum", "0xa", {0: "0x1"}, 1, 2, blockscout=bs, etherscan=es,
                       has_key=False)


def test_http_get_backs_off_on_429_then_raises(monkeypatch):
    class R:
        status_code = 429

        def json(self):
            return {}

        def raise_for_status(self):
            pass
    monkeypatch.setattr(logs.requests, "get", lambda *a, **k: R())
    with pytest.raises(logs.LogReadError):
        logs._http_get("u", {}, tries=2, sleep=lambda s: None)


def test_to_int_reads_hex_and_decimal():
    assert logs.to_int("0x10") == 16 and logs.to_int("16") == 16 and logs.to_int(16) == 16
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_logs.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.boundary'`

- [ ] **Step 3: Implement** — `src/boundary/__init__.py`:

```python
"""Hyperliquid's edge: who sent value to his world, and where new money came from.

Spec: docs/superpowers/specs/2026-10-06-boundary-trace-design.md
"""
```

`src/boundary/logs.py`:

```python
# src/boundary/logs.py
"""Event logs read strictly: Blockscout (keyless) first, Etherscan V2 as fallback.

Two rules this module exists to keep:

* A failed read is never "no logs" (rule 5). Blockscout answers an empty range
  with status "0" and "No logs found", Etherscan with "No records found". Only
  those answers are empty. A 429 from Blockscout is status "0" with a null
  result, and `utils.etherscan_get` reports a transport failure as status "0"
  with an empty list — the same shape as an empty answer — so anything else
  RAISES.
* A cursor never passes a block it did not finish reading. A page that comes
  back full (1,000 rows) may have cut its last block in half, so the walk
  resumes AT that block and dedupes by (transaction, log index).
"""

from __future__ import annotations

import os
import time

import requests

BLOCKSCOUT_API = {
    "arbitrum": "https://arbitrum.blockscout.com/api",
    "ethereum": "https://eth.blockscout.com/api",
    "base": "https://base.blockscout.com/api",
    "optimism": "https://optimism.blockscout.com/api",
    "polygon": "https://polygon.blockscout.com/api",
}
ETHERSCAN_CHAIN_IDS = {"arbitrum": 42161, "ethereum": 1, "base": 8453, "optimism": 10,
                       "polygon": 137, "hyperevm": 999}
PAGE = 1_000
CHUNK_BLOCKS = 100_000       # ~7h of Arbitrum; one call stays fast
PACE_SECONDS = 0.35


class LogReadError(RuntimeError):
    """A read that returned no answer. Never to be caught as "no logs"."""


def to_int(value) -> int:
    s = str(value if value is not None else "0").strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _http_get(url: str, params: dict, *, tries: int = 4, sleep=time.sleep) -> dict:
    """GET JSON. Backs off on 429 (measured: Blockscout throttles a busy IP)."""
    last = None
    for attempt in range(tries):
        sleep(PACE_SECONDS)
        try:
            response = requests.get(url, params=params, timeout=(10, 60))
        except requests.RequestException as exc:
            last = exc
            sleep(2 * (attempt + 1))
            continue
        if response.status_code == 429:
            last = RuntimeError("rate limited (HTTP 429)")
            sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    raise LogReadError(f"unavailable after {tries} tries: {last}")


def _params(address: str, topics: dict, from_block, to_block) -> dict:
    params = {"module": "logs", "action": "getLogs", "address": address,
              "fromBlock": from_block, "toBlock": to_block}
    keys = sorted(topics)
    for i in keys:
        params[f"topic{i}"] = topics[i]
    for a, b in zip(keys, keys[1:]):
        params[f"topic{a}_{b}_opr"] = "and"
    return params


def rows_of(doc, source: str) -> list[dict]:
    """The log rows of one answer; [] only for a genuine empty answer."""
    if not isinstance(doc, dict):
        raise LogReadError(f"{source}: unexpected answer {type(doc).__name__}")
    result = doc.get("result")
    if str(doc.get("status")) == "1" and isinstance(result, list):
        return result
    message = str(doc.get("message") or "").lower()
    if isinstance(result, list) and not result and ("no logs" in message
                                                    or "no records" in message):
        return []
    raise LogReadError(f"{source}: {doc.get('message')} {str(result)[:160]}")


def blockscout_logs(chain: str, address: str, topics: dict, from_block, to_block,
                    *, get=None) -> list[dict]:
    get = get or _http_get
    try:
        doc = get(BLOCKSCOUT_API[chain], _params(address, topics, from_block, to_block))
    except LogReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport: a failed read, reported
        raise LogReadError(f"blockscout: {type(exc).__name__}: {exc}") from exc
    return rows_of(doc, "blockscout")


def etherscan_logs(chain: str, address: str, topics: dict, from_block, to_block,
                   *, get=None) -> list[dict]:
    from src.utils import etherscan_get
    get = get or etherscan_get
    params = {**_params(address, topics, from_block, to_block), "page": 1, "offset": PAGE}
    return rows_of(get(params, chain_id=ETHERSCAN_CHAIN_IDS[chain]), "etherscan")


def read_logs(chain: str, address: str, topics: dict, from_block, to_block, *,
              blockscout=None, etherscan=None, has_key=None) -> list[dict]:
    """Blockscout first; Etherscan when Blockscout fails and a key is set."""
    blockscout = blockscout or blockscout_logs
    etherscan = etherscan or etherscan_logs
    has_key = bool(os.environ.get("ETHERSCAN_API_KEY")) if has_key is None else has_key
    try:
        if chain not in BLOCKSCOUT_API:
            raise LogReadError(f"no keyless log reader for {chain}")
        return blockscout(chain, address, topics, from_block, to_block)
    except LogReadError as first:
        if not has_key or chain not in ETHERSCAN_CHAIN_IDS:
            raise
        try:
            return etherscan(chain, address, topics, from_block, to_block)
        except LogReadError as second:
            raise LogReadError(f"{first}; then {second}") from second


def head_block(chain: str, *, get=None) -> int:
    """The chain head (Blockscout eth_block_number; Etherscan proxy as fallback)."""
    get = get or _http_get
    try:
        return to_int(get(BLOCKSCOUT_API[chain],
                          {"module": "block", "action": "eth_block_number"})["result"])
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("ETHERSCAN_API_KEY") and chain in ETHERSCAN_CHAIN_IDS:
            from src.utils import etherscan_get
            result = etherscan_get({"module": "proxy", "action": "eth_blockNumber"},
                                   chain_id=ETHERSCAN_CHAIN_IDS[chain]).get("result")
            if isinstance(result, str) and result.startswith("0x"):
                return int(result, 16)
        raise LogReadError(f"head of {chain} unreadable: {exc}") from exc


def block_at(chain: str, ts: int, *, closest: str = "before", get=None) -> int:
    """The block at a unix time (Etherscan-compatible getblocknobytime)."""
    get = get or _http_get
    params = {"module": "block", "action": "getblocknobytime", "timestamp": int(ts),
              "closest": closest}
    try:
        if chain in BLOCKSCOUT_API:
            doc = get(BLOCKSCOUT_API[chain], params)
        else:
            from src.utils import etherscan_get
            doc = etherscan_get(params, chain_id=ETHERSCAN_CHAIN_IDS[chain])
        result = doc.get("result")
        if isinstance(result, dict):          # Blockscout: {"blockNumber": "…"}
            result = result.get("blockNumber")
        return to_int(result)
    except Exception as exc:  # noqa: BLE001
        raise LogReadError(f"block at {ts} on {chain} unreadable: {exc}") from exc


def log_key(row: dict) -> tuple:
    return ((row.get("transactionHash") or "").lower(), str(row.get("logIndex")))


def walk(read, start_block: int, head: int, *, max_calls: int = 40, seconds: float = 60.0,
         chunk: int = CHUNK_BLOCKS, clock=time.monotonic) -> dict:
    """Read [start_block, head] in order without ever skipping a block.

    `read(lo, hi) -> rows` raises LogReadError on failure. `last_block` is the
    last block FULLY read (start_block - 1 when nothing was): the cursor a
    caller stores.
    """
    started = clock()
    found, seen = [], set()
    cursor, lo, calls, error = start_block - 1, start_block, 0, None
    while lo <= head:
        if calls >= max_calls or clock() - started >= seconds:
            break
        hi = min(lo + chunk - 1, head)
        try:
            rows = read(lo, hi)
        except LogReadError as exc:
            error = str(exc)
            break
        calls += 1
        for row in rows:
            key = log_key(row)
            if key not in seen:
                seen.add(key)
                found.append(row)
        if len(rows) >= PAGE:
            last = max(to_int(r.get("blockNumber")) for r in rows)
            if last <= lo:
                error = f"more than {PAGE} logs in block {lo}; refusing to read a partial block"
                break
            cursor, lo = last - 1, last
            continue
        cursor, lo = hi, hi + 1
    return {"logs": found, "last_block": cursor, "calls": calls,
            "complete": cursor >= head and error is None, "error": error}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_logs.py -q -p no:cacheprovider`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
python -m ruff check src/boundary tests/test_boundary_logs.py
git add src/boundary/__init__.py src/boundary/logs.py tests/test_boundary_logs.py
git commit -m "feat(boundary): strict getLogs reader and a cursor walk that never skips"
```

---

### Task 2: Bridge2 decoders (`src/boundary/bridge2.py`)

**Files:**
- Create: `src/boundary/bridge2.py`
- Fixtures (already captured from Arbitrum, 2026-10-06): `tests/fixtures/boundary/bridge2_withdrawals_treasury.json` (getLogs topic1 = treasury: 4 rows), `bridge2_withdrawals_sample.json` (9 live rows, 5 with user ≠ destination), `bridge2_deposits_sample.json` (23 USDC transfers into the bridge)
- Test: `tests/test_boundary_bridge2.py`

**Interfaces:**
- Consumes: `logs.to_int`
- Produces: constants `BRIDGE`, `USDC`, `CHAIN="arbitrum"`, `TOPIC_FINALIZED_WITHDRAWAL`, `TOPIC_TRANSFER`; `decode_withdrawal(log) -> {"kind","user","destination","usd","nonce","message","tx_hash","log_index","block","ts"} | None`; `decode_deposit(log) -> {"kind","depositor","usd","tx_hash","log_index","block","ts"} | None`; `withdrawal_topics(user=None) -> dict`; `deposit_topics() -> dict`; `topic_address(addr) -> str`; `event_id(row) -> str`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_bridge2.py`:

```python
"""Bridge2's own events name the account that withdrew (spec §1, §5)."""

import json
from pathlib import Path

from src.boundary import bridge2

FIX = Path(__file__).parent / "fixtures" / "boundary"
TREASURY = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"


def _rows(name):
    return json.loads((FIX / name).read_text())["result"]


def test_the_treasury_payout_names_the_account_that_withdrew():
    rows = [bridge2.decode_withdrawal(log) for log in _rows("bridge2_withdrawals_treasury.json")]
    assert len(rows) == 4 and all(rows)
    first = next(r for r in rows if r["tx_hash"] ==
                 "0xc0758212634d6d92262e4c9d1bf25b51c8da13e1b30bb060bdf21387703761ad")
    assert first["user"] == first["destination"] == TREASURY
    assert first["usd"] == 1_999_999.0 and first["nonce"] == 1742999426126000
    assert {r["usd"] for r in rows} == {1_999_999.0, 499_999.0, 999_999.0, 939_038.02}
    assert all(r["ts"] > 1_700_000_000 and r["block"] > 0 for r in rows)


def test_live_withdrawals_decode_and_some_go_to_another_address():
    rows = [bridge2.decode_withdrawal(log) for log in _rows("bridge2_withdrawals_sample.json")]
    assert rows and all(rows)
    assert any(r["user"] != r["destination"] for r in rows)


def test_deposits_name_the_depositor():
    rows = [bridge2.decode_deposit(log) for log in _rows("bridge2_deposits_sample.json")]
    assert rows and all(r and r["depositor"].startswith("0x") and r["usd"] > 0 for r in rows)


def test_other_events_and_contracts_are_not_decoded():
    log = _rows("bridge2_withdrawals_treasury.json")[0]
    assert bridge2.decode_withdrawal({**log, "address": "0x" + "1" * 40}) is None
    assert bridge2.decode_withdrawal({**log, "topics": ["0x" + "2" * 64] + log["topics"][1:]}) is None
    assert bridge2.decode_withdrawal({**log, "data": "0x1234"}) is None
    assert bridge2.decode_deposit(log) is None


def test_topics_filter_on_the_indexed_user():
    t = bridge2.withdrawal_topics("0x1419E75330C71ce463102e6A1Eb62FE80b412D5f")
    assert t == {0: bridge2.TOPIC_FINALIZED_WITHDRAWAL,
                 1: "0x000000000000000000000000" + TREASURY[2:]}
    assert bridge2.withdrawal_topics() == {0: bridge2.TOPIC_FINALIZED_WITHDRAWAL}
    assert bridge2.deposit_topics() == {0: bridge2.TOPIC_TRANSFER,
                                        2: "0x000000000000000000000000" + bridge2.BRIDGE[2:]}


def test_event_ids_are_stable():
    row = bridge2.decode_withdrawal(_rows("bridge2_withdrawals_treasury.json")[0])
    assert bridge2.event_id(row) == f"arbitrum:{row['tx_hash']}:{row['log_index']}"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_bridge2.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'bridge2'`

- [ ] **Step 3: Implement** — `src/boundary/bridge2.py`:

```python
# src/boundary/bridge2.py
"""Hyperliquid's Arbitrum bridge, read from its own events.

`FinalizedWithdrawal(address indexed user, address destination, uint64 usd,
uint64 nonce, bytes32 message)` is emitted in the transaction that pays a
withdrawal out, and names the HYPERLIQUID ACCOUNT that withdrew and where the
money went (verified 2026-10-06 on the treasury payout 0xc0758212…). The
Arbitrum logs are therefore a complete, permanent, keyless record of every
Bridge2 withdrawal by every account: ~3,200 a day, a quarter of them to an
address other than the account. On 2026-09-29 this was deferred as needing an
always-on host; it needs one getLogs call per ~6 hours of chain.

`user` is indexed, so one call filtered on an account returns its whole
withdrawal history, and the HL ledger's `withdraw` row carries the same nonce.
Deposits are plain USDC transfers into the bridge: the depositor IS the account
credited.
"""

from __future__ import annotations

from src.boundary.logs import to_int

BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
CHAIN = "arbitrum"
TOPIC_FINALIZED_WITHDRAWAL = "0xe5c7fe3a4ffca1590f26d74c8ba8b0db69557f7f4607a2a43f82e93041611978"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USD_DECIMALS = 6


def topic_address(address: str) -> str:
    return "0x" + "0" * 24 + (address or "").lower().removeprefix("0x")


def _word_address(word: str) -> str:
    return "0x" + word[-40:].lower()


def _words(data) -> list[str]:
    h = str(data or "").lower().removeprefix("0x")
    return [h[i:i + 64] for i in range(0, len(h) - len(h) % 64, 64)]


def _common(log: dict) -> dict:
    return {"tx_hash": (log.get("transactionHash") or "").lower(),
            "log_index": to_int(log.get("logIndex")), "block": to_int(log.get("blockNumber")),
            "ts": to_int(log.get("timeStamp"))}


def decode_withdrawal(log: dict) -> dict | None:
    topics = [str(t or "").lower() for t in (log.get("topics") or [])]
    if (len(topics) < 2 or topics[0] != TOPIC_FINALIZED_WITHDRAWAL
            or (log.get("address") or "").lower() != BRIDGE):
        return None
    words = _words(log.get("data"))
    if len(words) < 4:
        return None
    return {"kind": "bridge2_withdrawal", "user": _word_address(topics[1]),
            "destination": _word_address(words[0]),
            "usd": int(words[1], 16) / 10 ** USD_DECIMALS, "nonce": int(words[2], 16),
            "message": "0x" + words[3], **_common(log)}


def decode_deposit(log: dict) -> dict | None:
    topics = [str(t or "").lower() for t in (log.get("topics") or [])]
    if (len(topics) < 3 or topics[0] != TOPIC_TRANSFER
            or (log.get("address") or "").lower() != USDC
            or _word_address(topics[2]) != BRIDGE):
        return None
    words = _words(log.get("data"))
    if not words:
        return None
    return {"kind": "bridge2_deposit", "depositor": _word_address(topics[1]),
            "usd": int(words[0], 16) / 10 ** USD_DECIMALS, **_common(log)}


def withdrawal_topics(user: str | None = None) -> dict:
    topics = {0: TOPIC_FINALIZED_WITHDRAWAL}
    if user:
        topics[1] = topic_address(user)
    return topics


def deposit_topics() -> dict:
    return {0: TOPIC_TRANSFER, 2: topic_address(BRIDGE)}


def event_id(row: dict) -> str:
    return f"{CHAIN}:{row.get('tx_hash')}:{row.get('log_index')}"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_bridge2.py -q -p no:cacheprovider`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/boundary/bridge2.py tests/test_boundary_bridge2.py tests/fixtures/boundary/bridge2_*.json
git commit -m "feat(boundary): decode Bridge2 withdrawals and deposits from Arbitrum logs"
```

---

### Task 3: Unit operations (`src/boundary/unit.py`)

**Files:**
- Create: `src/boundary/unit.py`
- Fixtures (captured 2026-10-06): `unit_ops_btc_deposit.json` (`0x458d583d…`: 2 Bitcoin deposits), `unit_ops_mixed.json` (`0x92fca16b…`: withdrawals and a Solana deposit), `unit_ops_target_empty.json` (the target: addresses, no operations)
- Test: `tests/test_boundary_unit.py`

**Interfaces:**
- Produces: `UnitReadError`; `read_operations(address, *, get=None) -> dict` (raises); `normalise(op) -> event | None`; `events(doc) -> list[event]`; `norm_address(a) -> str`. Event shape (shared with Task 5): `{"source","direction","hl_account","counterparty","counterparty_raw","chain","amount_usd","ts","ref","event_id", ...extra}`; Unit adds `asset`, `amount_native`, `state`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_unit.py`:

```python
"""Unit is a keyless feed in both directions (spec §5)."""

import json
from pathlib import Path

import pytest

from src.boundary import unit

FIX = Path(__file__).parent / "fixtures" / "boundary"


def _doc(name):
    return json.loads((FIX / name).read_text())


def test_a_bitcoin_deposit_is_inbound_from_its_source_address():
    evs = unit.events(_doc("unit_ops_btc_deposit.json"))
    assert len(evs) == 2
    e = evs[0]
    assert e["source"] == "unit" and e["direction"] == "in"
    assert e["hl_account"] == "0x458d583dbfe5b0a143bf09f45acf29f0022aab3b"
    assert e["counterparty"] == "bc1pg64kvzamkafsld07tsrqpk402pdwfe5jyxr9tnfgaku9guh998nqeguj7u"
    assert e["chain"] == "bitcoin" and e["asset"] == "btc"
    assert e["amount_usd"] is None and e["ts"] > 1_700_000_000 and e["event_id"]


def test_withdrawals_name_their_destination():
    outs = [e for e in unit.events(_doc("unit_ops_mixed.json")) if e["direction"] == "out"]
    assert outs and all(e["hl_account"] == "0x92fca16b23ec24dd7206552be0286eb06ca83cd2"
                        for e in outs)


def test_no_operations_is_empty_not_unreadable():
    assert unit.events(_doc("unit_ops_target_empty.json")) == []


def test_a_failed_read_raises():
    def down(url):
        raise OSError("down")
    with pytest.raises(unit.UnitReadError):
        unit.read_operations("0xabc", get=down)
    with pytest.raises(unit.UnitReadError):
        unit.read_operations("0xabc", get=lambda url: {"error": "x"})


def test_failed_operations_drop_and_non_evm_case_is_kept():
    op = {"sourceChain": "solana", "destinationChain": "hyperliquid",
          "sourceAddress": "5tzFkiKscXHK", "state": "failed", "sourceTxHash": "x",
          "destinationAddress": "0xABC0000000000000000000000000000000000001"}
    assert unit.normalise(op) is None
    e = unit.normalise({**op, "state": "done"})
    assert e["counterparty"] == "5tzFkiKscXHK"
    assert e["hl_account"] == "0xabc0000000000000000000000000000000000001"


def test_hyperliquid_to_hyperliquid_is_not_an_edge_event():
    assert unit.normalise({"sourceChain": "hyperliquid", "destinationChain": "hyperliquid",
                           "sourceAddress": "0x1", "destinationAddress": "0x2"}) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_unit.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'unit'`

- [ ] **Step 3: Implement** — `src/boundary/unit.py`:

```python
# src/boundary/unit.py
"""Unit (Hyperunit), the BTC/ETH/SOL/ZEC bridge into HyperCore, read per address.

`GET https://api.hyperunit.xyz/operations/<address>` is keyless and answers for
an HL account AND for an external address (a Bitcoin, Solana or EVM source or
destination), in both directions (measured 2026-10-06). Nine of the 72 newborn
≥ $1M accounts that week were funded through it and nothing here read it. His
three wallets, his Solana wallet and his Binance deposit address had none.

Amounts are native units; this module never prices them (rule 11).
"""

from __future__ import annotations

import time
from datetime import datetime

import requests

API = "https://api.hyperunit.xyz/operations/"
PACE_SECONDS = 1.0
FAILED_STATES = {"failed", "error", "cancelled", "canceled", "rejected", "refunded"}


class UnitReadError(RuntimeError):
    pass


def _get(url: str) -> dict:
    time.sleep(PACE_SECONDS)
    response = requests.get(url, timeout=(10, 30))
    response.raise_for_status()
    return response.json()


def read_operations(address: str, *, get=None) -> dict:
    """{"addresses": [...], "operations": [...]}; UnitReadError, never {}, on failure."""
    get = get or _get
    try:
        doc = get(API + address)
    except Exception as exc:  # noqa: BLE001 - a failed read, reported
        raise UnitReadError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("operations"), list):
        raise UnitReadError(f"unexpected Unit answer: {str(doc)[:120]}")
    return doc


def norm_address(address) -> str:
    a = str(address or "").strip()
    return a.lower() if a.lower().startswith("0x") and len(a) == 42 else a


def _ts(value) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def normalise(op) -> dict | None:
    if not isinstance(op, dict) or str(op.get("state") or "").lower() in FAILED_STATES:
        return None
    src_chain = str(op.get("sourceChain") or "").lower()
    dst_chain = str(op.get("destinationChain") or "").lower()
    if src_chain == "hyperliquid" and dst_chain and dst_chain != "hyperliquid":
        direction, account = "out", norm_address(op.get("sourceAddress"))
        counterparty, chain = norm_address(op.get("destinationAddress")), dst_chain
    elif dst_chain == "hyperliquid" and src_chain and src_chain != "hyperliquid":
        direction, account = "in", norm_address(op.get("destinationAddress"))
        counterparty, chain = norm_address(op.get("sourceAddress")), src_chain
    else:
        return None
    if not account or not counterparty:
        return None
    ref = str(op.get("sourceTxHash") or op.get("operationId") or "")
    return {"source": "unit", "direction": direction, "hl_account": account,
            "counterparty": counterparty, "counterparty_raw": None, "chain": chain,
            "asset": str(op.get("asset") or "").lower() or None,
            "amount_native": op.get("sourceAmount"), "amount_usd": None,
            "ts": _ts(op.get("opCreatedAt")), "ref": ref, "state": op.get("state"),
            "event_id": f"unit:{ref}" if ref else None}


def events(doc) -> list[dict]:
    out, seen = [], set()
    for op in (doc or {}).get("operations") or []:
        row = normalise(op)
        if row is None:
            continue
        key = row["event_id"] or (row["hl_account"], row["counterparty"], row["ts"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_unit.py -q -p no:cacheprovider`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/boundary/unit.py tests/test_boundary_unit.py tests/fixtures/boundary/unit_ops_*.json
git commit -m "feat(boundary): read Unit operations in both directions"
```

---

### Task 4: The perimeter (`src/boundary/perimeter.py`)

**Files:**
- Create: `src/boundary/perimeter.py`
- Test: `tests/test_boundary_perimeter.py`

**Interfaces:**
- Produces: `ROLE_WEIGHTS`, `ROLE_ORDER`, `STRONG_ROLES=("core","identity")`; `build(*, config, sentinels, trace_report, trace_registry, solana, associates_found, families, services, previous, now_iso) -> {"computed_at","members":{addr: member},"counts","exchange_families"}` where member = `{"address","role","weight","why","sources","first_seen","hl", ["raw"]}`; `associates(records, core, *, is_contract, is_busy, services, min_usd=1e6) -> {addr: {"paid_him_usd","he_paid_usd"}}`; `exchange_families(records, deposit_members, core, *, is_hot) -> {"deposit:<addr>": [hot...], "paid_him": [hot...]}`; `family_index(families) -> {hot: [family_id]}`; `class Index(perimeter)` with `.get(address, raw=None) -> member|None`, `.core: set`, `.members`, `.families: {hot:[ids]}`; `core_only(config, now_iso) -> perimeter`; `excluded(address, services) -> bool`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_perimeter.py`:

```python
"""His world as one lookup table, built only from measured facts (spec §4)."""

from src.boundary import perimeter

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
F = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
HD = "0x4aecac3b90dd0ad50d274c19221874e5ba8a4d45"
HUB = "0x1f6093d33db935b2ebd81d23312da5f11759973e"
FUND = "0x68797748dd0151819841df908adb93891997b711"
BUSYF = "0x160f6ef9fcdde6ff3febc7a57edbfd476a8aab5b"
SINK, SMALL, MIXED = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40
SOL = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"
ASSOC, SVC = "0x" + "d" * 40, "0x" + "e" * 40
CONFIG = {"target_wallet": T, "known_self_wallets": [TR, F]}


def _build(**kw):
    base = dict(config=CONFIG, sentinels={}, trace_report={}, trace_registry={}, solana={},
                associates_found={}, families={}, services=set(), previous=None,
                now_iso="2026-10-06T00:00:00+00:00")
    base.update(kw)
    return perimeter.build(**base)


def rec(src, dst, usd):
    return {"src": src, "dst": dst, "amount_usd": usd}


def test_core_is_config_and_only_config():
    doc = _build()
    assert {a for a, m in doc["members"].items() if m["role"] == "core"} == {T, TR, F}
    assert doc["counts"] == {"core": 3}


def test_roles_come_from_the_detector_that_measured_them():
    doc = _build(
        sentinels={S: {"reason": "conduit"}},
        trace_report={"deposit_addresses": [{"address": HD, "kind": "hl_deposit", "hub": HUB}],
                      "funders": [{"address": FUND, "class": "quiet_eoa", "paid_him_usd": 6e6},
                                  {"address": BUSYF, "class": "unknown", "paid_him_usd": 9e7}]},
        trace_registry={SINK: {"class": "quiet_eoa", "his_money": {"in_usd": 2e5, "share": 0.9}},
                        SMALL: {"class": "quiet_eoa", "his_money": {"in_usd": 5e4, "share": 1.0}},
                        MIXED: {"class": "quiet_eoa", "his_money": {"in_usd": 5e6, "share": 0.1}}},
        solana={SOL: {"role": "cluster", "mint_recipient_hex": "0xABCD"}},
        associates_found={ASSOC: {"paid_him_usd": 2e6, "he_paid_usd": 3e6}})
    m = doc["members"]
    assert m[S]["role"] == "deposit" and m[HD]["role"] == "deposit" and m[S]["weight"] == 1.0
    assert m[FUND]["role"] == "funder" and m[FUND]["weight"] == 0.6
    assert BUSYF not in m                      # unmeasured is never quiet (rule 9)
    assert m[SINK]["role"] == "sink" and SMALL not in m and MIXED not in m
    assert m[SOL]["role"] == "identity" and m[SOL]["raw"] == "0xabcd"
    assert m[ASSOC]["role"] == "associate" and m[ASSOC]["weight"] == 0.3


def test_a_member_keeps_its_strongest_role_and_core_is_never_demoted():
    doc = _build(sentinels={T: {"reason": "x"}, S: {"reason": "y"}},
                 associates_found={S: {"paid_him_usd": 2e6, "he_paid_usd": 2e6}})
    assert doc["members"][T]["role"] == "core"
    assert doc["members"][S]["role"] == "deposit"
    assert doc["members"][S]["sources"] == ["deposit_sentinels", "substrate"]


def test_services_zero_and_system_addresses_never_join():
    zero, system = "0x" + "0" * 40, "0x2000000000000000000000000000000000000000"
    doc = _build(sentinels={zero: {}, system: {}, SVC: {}}, services={SVC})
    assert not ({zero, system, SVC} & set(doc["members"]))


def test_readings_and_first_seen_survive_a_rebuild():
    prev = {"members": {S: {"first_seen": "2026-01-01", "hl": {"checked_at": "x"}}}}
    doc = _build(sentinels={S: {"reason": "y"}}, previous=prev)
    assert doc["members"][S]["first_seen"] == "2026-01-01"
    assert doc["members"][S]["hl"] == {"checked_at": "x"}


def test_associates_need_large_flows_both_ways_with_a_person():
    P, ONEWAY, BIG, CON = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40, "0x" + "4" * 40
    recs = [rec(T, P, 2e6), rec(P, T, 1.5e6), rec(T, ONEWAY, 9e6), rec(T, BIG, 2e6),
            rec(BIG, T, 2e6), rec(T, CON, 2e6), rec(CON, T, 2e6),
            {"src": P, "dst": T, "amount_usd": None}, {**rec(P, T, 5e6), "spam": True}]
    out = perimeter.associates(recs, {T}, is_contract=lambda a: a == CON,
                               is_busy=lambda a: a == BIG, services=set())
    assert out == {P: {"paid_him_usd": 1.5e6, "he_paid_usd": 2e6}}


def test_exchange_families_follow_his_deposit_address_and_who_paid_him():
    H1, H2, H3, H4, PERSON = ("0x" + c * 40 for c in "56789")
    recs = [rec(S, H1, 5e6), rec(S, H2, 1e6), rec(S, PERSON, 50), rec(H3, T, 2e5), rec(H4, T, 10)]
    fam = perimeter.exchange_families(recs, {S}, {T}, is_hot=lambda a: a in {H1, H2, H3, H4})
    assert fam == {f"deposit:{S}": sorted([H1, H2]), "paid_him": [H3]}
    assert perimeter.family_index(fam)[H1] == [f"deposit:{S}"]


def test_index_finds_members_by_address_or_raw_form_and_knows_core():
    doc = _build(solana={SOL: {"role": "cluster", "mint_recipient_hex": "0xABCD"}},
                 families={"paid_him": ["0x" + "5" * 40]})
    idx = perimeter.Index(doc)
    assert idx.get(T.upper().replace("0X", "0x"))["role"] == "core"
    assert idx.get(None, raw="0xABCD")["address"] == SOL
    assert idx.get("0x" + "9" * 40) is None
    assert idx.core == {T, TR, F} and idx.families["0x" + "5" * 40] == ["paid_him"]


def test_core_only_is_a_working_perimeter_from_config_alone():
    idx = perimeter.Index(perimeter.core_only(CONFIG, "2026-10-06T00:00:00+00:00"))
    assert idx.core == {T, TR, F} and len(idx.members) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_perimeter.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'perimeter'`

- [ ] **Step 3: Implement** — `src/boundary/perimeter.py`:

```python
# src/boundary/perimeter.py
"""His world as one lookup table: every address whose payment means him.

Built every trace run from what other detectors already MEASURED, never
inferred here, so a member's evidence is always another file's reading. Joining
edge events against a table costs one lookup per event, so the table can grow
without anything sweeping its members — the forward walk's scaling problem
(spec §2).

Roles are evidence weights, not identity. Ground truth stays config only
(CLAUDE.md): nothing here feeds spam immunity, cluster membership or `settled`.
Roster CONFIRMED is config-only by design, so it adds no role of its own.
"""

from __future__ import annotations

ROLE_WEIGHTS = {"core": 1.0, "deposit": 1.0, "identity": 1.0,
                "sink": 0.6, "funder": 0.6, "associate": 0.3}
ROLE_ORDER = ("core", "deposit", "identity", "sink", "funder", "associate")
STRONG_ROLES = ("core", "identity")
SINK_MIN_USD = 100_000.0
SINK_MIN_SHARE = 0.5
FUNDER_MIN_USD = 100_000.0
ASSOCIATE_MIN_USD = 1_000_000.0
HOT_MIN_USD = 100_000.0
ZERO = "0x" + "0" * 40
SYSTEM_PREFIXES = ("0x20000000000000000000000000000000000000", "0x2222222222")


def low(address) -> str:
    a = str(address or "").strip()
    return a.lower() if a.lower().startswith("0x") else a


def excluded(address, services) -> bool:
    a = low(address)
    return (not a or a == ZERO or a == "0x" + "f" * 40 or a in services
            or a.startswith(SYSTEM_PREFIXES))


def associates(records, core, *, is_contract, is_busy, services,
               min_usd: float = ASSOCIATE_MIN_USD) -> dict:
    """Large two-way personal counterparties of the core wallets. Pure."""
    core = {low(c) for c in core}
    services = {low(s) for s in services}
    flows: dict[str, dict] = {}
    for rec in records or []:
        usd = rec.get("amount_usd")
        if rec.get("spam") or usd is None:
            continue
        src, dst = low(rec.get("src")), low(rec.get("dst"))
        if src in core and dst not in core:
            flows.setdefault(dst, {"out": 0.0, "in": 0.0})["out"] += float(usd)
        elif dst in core and src not in core:
            flows.setdefault(src, {"out": 0.0, "in": 0.0})["in"] += float(usd)
    out = {}
    for addr, f in sorted(flows.items()):
        if f["out"] < min_usd or f["in"] < min_usd or excluded(addr, services):
            continue
        if is_contract(addr) or is_busy(addr):
            continue
        out[addr] = {"paid_him_usd": round(f["in"], 2), "he_paid_usd": round(f["out"], 2)}
    return out


def exchange_families(records, deposit_members, core, *, is_hot) -> dict:
    """Hot wallets behind his deposit addresses, and exchange wallets that paid him."""
    deposit_members = {low(d) for d in deposit_members}
    core = {low(c) for c in core}
    families: dict[str, set] = {}
    paid_him: dict[str, float] = {}
    for rec in records or []:
        if rec.get("spam"):
            continue
        src, dst = low(rec.get("src")), low(rec.get("dst"))
        usd = rec.get("amount_usd")
        if src in deposit_members and dst not in core and is_hot(dst):
            families.setdefault(src, set()).add(dst)
        elif dst in core and src not in core and usd is not None and is_hot(src):
            paid_him[src] = paid_him.get(src, 0.0) + float(usd)
    out = {f"deposit:{d}": sorted(h) for d, h in sorted(families.items())}
    withdraws = sorted(a for a, usd in paid_him.items() if usd >= HOT_MIN_USD)
    if withdraws:
        out["paid_him"] = withdraws
    return out


def family_index(families) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for fid, hots in sorted((families or {}).items()):
        for h in hots:
            index.setdefault(low(h), []).append(fid)
    return index


def build(*, config: dict, sentinels: dict, trace_report: dict, trace_registry: dict,
          solana: dict, associates_found: dict, families: dict, services,
          previous: dict | None, now_iso: str) -> dict:
    """The perimeter document. Pure."""
    services = {low(s) for s in services or ()}
    core = {low(config.get("target_wallet"))} | {low(w) for w in
                                                 config.get("known_self_wallets") or []}
    core.discard("")
    prior_members = (previous or {}).get("members") or {}
    members: dict[str, dict] = {}

    def add(address, role, why, source, **extra):
        a = low(address)
        if not a or (role != "core" and (a in core or excluded(a, services))):
            return
        old = members.get(a)
        if old is not None and ROLE_ORDER.index(old["role"]) <= ROLE_ORDER.index(role):
            old["sources"] = sorted(set(old["sources"]) | {source})
            return
        prior = prior_members.get(a) or {}
        members[a] = {"address": a, "role": role, "weight": ROLE_WEIGHTS[role], "why": why,
                      "sources": sorted(set((old or {}).get("sources") or []) | {source}),
                      "first_seen": prior.get("first_seen") or now_iso,
                      "hl": prior.get("hl"), **extra}

    for a in sorted(core):
        add(a, "core", "configured (ground truth)", "config")
    for a, s in sorted((sentinels or {}).items()):
        add(a, "deposit", "his private exchange deposit address: "
            + str((s or {}).get("reason") or "deposit sentinel"), "deposit_sentinels")
    for row in (trace_report or {}).get("deposit_addresses") or []:
        add(row.get("address"), "deposit", f"{row.get('kind')} the cluster paid "
            f"(hub {row.get('hub') or 'unknown'})", "trace_engine")
    for addr, info in sorted((solana or {}).items()):
        if (info or {}).get("role") == "cluster":
            add(addr, "identity", "Solana address his CCTP burns minted to",
                "solana_addresses", raw=low((info or {}).get("mint_recipient_hex")) or None)
    for addr, info in sorted((trace_registry or {}).items()):
        money = (info or {}).get("his_money") or {}
        if ((info or {}).get("class") == "quiet_eoa"
                and float(money.get("share") or 0) >= SINK_MIN_SHARE
                and float(money.get("in_usd") or 0) >= SINK_MIN_USD):
            add(addr, "sink", f"quiet wallet holding ${float(money['in_usd']):,.0f} of his "
                f"money ({float(money['share']):.0%} of its inflow)", "trace_engine")
    for row in (trace_report or {}).get("funders") or []:
        if row.get("class") == "quiet_eoa" and float(row.get("paid_him_usd") or 0) >= FUNDER_MIN_USD:
            add(row.get("address"), "funder",
                f"quiet wallet that paid him ${float(row['paid_him_usd']):,.0f}", "trace_engine")
    for addr, row in sorted((associates_found or {}).items()):
        add(addr, "associate", f"two-way counterparty: paid him ${row['paid_him_usd']:,.0f}, "
            f"he paid ${row['he_paid_usd']:,.0f}", "substrate")

    counts: dict[str, int] = {}
    for m in members.values():
        counts[m["role"]] = counts.get(m["role"], 0) + 1
    return {"computed_at": now_iso, "members": dict(sorted(members.items())),
            "counts": counts, "exchange_families": families or {}}


def core_only(config: dict, now_iso: str) -> dict:
    """A working perimeter from config alone, for when no build has run yet."""
    return build(config=config, sentinels={}, trace_report={}, trace_registry={}, solana={},
                 associates_found={}, families={}, services=set(), previous=None,
                 now_iso=now_iso)


class Index:
    """O(1) membership by address or by raw (bytes32) form."""

    def __init__(self, perimeter: dict | None):
        self.members = dict((perimeter or {}).get("members") or {})
        self._by: dict[str, dict] = {}
        for a, m in self.members.items():
            self._by[low(a)] = m
            if m.get("raw"):
                self._by.setdefault(low(m["raw"]), m)
        self.core = {a for a, m in self.members.items() if m.get("role") == "core"}
        self.families = family_index((perimeter or {}).get("exchange_families") or {})

    def get(self, address, raw=None) -> dict | None:
        for key in (low(address), low(raw)):
            if key and key in self._by:
                return self._by[key]
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_perimeter.py -q -p no:cacheprovider`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/boundary/perimeter.py tests/test_boundary_perimeter.py
git commit -m "feat(boundary): the perimeter - his world as one table of measured roles"
```

---

### Task 5: Attribution rules (`src/boundary/attribution.py`)

**Files:**
- Create: `src/boundary/attribution.py`
- Test: `tests/test_boundary_attribution.py`

**Interfaces:**
- Consumes: `perimeter.Index`, `perimeter.STRONG_ROLES`, `bridge2.event_id`, the event shape from Task 3.
- Produces: kinds `KIND_PAID_HIS_WORLD`, `KIND_FUNDED_FROM_HIS_WORLD`, `KIND_HIS_ACCOUNT_PAID_OUTSIDE`, `KIND_MEMBER_ACTIVE`; `TITLES: {kind: str}`; `SEVERITIES=("CRITICAL","HIGH")`; `DUST_USD=100.0`; `event(**fields) -> dict`; `from_bridge2_withdrawal(row, *, retro=False)`, `from_bridge2_deposit(row, *, retro=False)`, `from_hl_edges(edges, core, *, exclude=frozenset()) -> list`, `classify(ev, index) -> finding|None`, `classify_all(events, index) -> list`, `finding_key(row) -> str`. Finding = event + `{"kind","severity" (CRITICAL|HIGH|None),"vote" (transfer|linkage|None),"role","member","member_why","key"}`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_attribution.py`:

```python
"""Spec §6.2, one test per row of the rules table."""

from src.boundary import attribution as at
from src.boundary import perimeter

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SINK, FUND, ASSOC = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40
SOL = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"
OUT, OTHER, HUB = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40

INDEX = perimeter.Index(perimeter.build(
    config={"target_wallet": T, "known_self_wallets": [TR]},
    sentinels={S: {"reason": "conduit"}},
    trace_report={"funders": [{"address": FUND, "class": "quiet_eoa", "paid_him_usd": 6e6}]},
    trace_registry={SINK: {"class": "quiet_eoa", "his_money": {"in_usd": 5e5, "share": 1.0}}},
    solana={SOL: {"role": "cluster", "mint_recipient_hex": "0x" + "ab" * 32}},
    associates_found={ASSOC: {"paid_him_usd": 2e6, "he_paid_usd": 2e6}},
    families={}, services=set(), previous=None, now_iso="2026-10-06T00:00:00+00:00"))


def ev(direction, account, counterparty, usd=50_000.0, source="bridge2", **kw):
    return at.event(source=source, direction=direction, hl_account=account,
                    counterparty=counterparty, chain="arbitrum", amount_usd=usd, ts=1, ref="0xr",
                    **kw)


def test_an_outside_account_paying_his_wallet_is_critical_transfer():
    f = at.classify(ev("out", OUT, T), INDEX)
    assert (f["kind"], f["severity"], f["vote"], f["role"]) == \
        (at.KIND_PAID_HIS_WORLD, "CRITICAL", "transfer", "core")


def test_an_outside_account_paying_his_deposit_address_is_critical_linkage():
    f = at.classify(ev("out", OUT, S), INDEX)
    assert (f["severity"], f["vote"], f["role"]) == ("CRITICAL", "linkage", "deposit")


def test_paying_a_sink_or_funder_is_high_evidence_only():
    for member in (SINK, FUND):
        f = at.classify(ev("out", OUT, member), INDEX)
        assert (f["severity"], f["vote"]) == ("HIGH", None)


def test_an_associate_is_recorded_never_alerted():
    f = at.classify(ev("out", OUT, ASSOC), INDEX)
    assert f is not None and f["severity"] is None and f["vote"] is None


def test_his_world_funding_an_outside_account_is_critical_transfer():
    f = at.classify(ev("in", OUT, T, source="hl_send"), INDEX)
    assert (f["kind"], f["severity"], f["vote"]) == (at.KIND_FUNDED_FROM_HIS_WORLD, "CRITICAL", "transfer")
    f = at.classify(ev("in", OUT, FUND, source="circle"), INDEX)
    assert (f["severity"], f["vote"]) == ("HIGH", None)


def test_his_account_paying_an_address_outside_his_world_is_critical():
    f = at.classify(ev("out", TR, OTHER), INDEX)
    assert (f["kind"], f["severity"], f["vote"]) == (at.KIND_HIS_ACCOUNT_PAID_OUTSIDE, "CRITICAL", None)


def test_his_account_paying_his_own_world_or_itself_is_nothing():
    assert at.classify(ev("out", T, S), INDEX) is None
    assert at.classify(ev("out", T, T), INDEX) is None
    assert at.classify(ev("in", T, OTHER), INDEX) is None


def test_a_withdrawal_to_itself_carries_no_relationship_unless_it_is_a_member():
    assert at.classify(ev("out", OUT, OUT), INDEX) is None
    f = at.classify(ev("in", SINK, SINK), INDEX)
    assert (f["kind"], f["severity"]) == (at.KIND_MEMBER_ACTIVE, "HIGH")
    f = at.classify(ev("out", ASSOC, ASSOC), INDEX)
    assert f["kind"] == at.KIND_MEMBER_ACTIVE and f["severity"] is None


def test_dust_never_counts_and_unvalued_never_pages_critical():
    assert at.classify(ev("out", OUT, T, usd=40.0), INDEX) is None
    f = at.classify(ev("out", OUT, SOL, usd=None, source="unit"), INDEX)
    assert (f["severity"], f["vote"], f["role"]) == ("HIGH", "transfer", "identity")


def test_raw_forms_match_his_solana_identity():
    f = at.classify(ev("out", OUT, None, counterparty_raw="0x" + "ab" * 32, source="circle"), INDEX)
    assert f["role"] == "identity" and f["severity"] == "CRITICAL"


def test_history_is_announced_at_high_but_keeps_its_vote():
    f = at.classify(ev("out", OUT, T, retro=True), INDEX)
    assert (f["severity"], f["vote"]) == ("HIGH", "transfer")


def test_hl_edges_become_events_for_the_other_account_and_skip_hubs():
    edges = [{"src": T, "dst": OTHER, "amount_usd": 5e5, "ts": 9, "tx_hash": "0xa",
              "id": "e1", "kind": "send"},
             {"src": OTHER, "dst": T, "amount_usd": 2e3, "ts": 10, "tx_hash": "0xb",
              "id": "e2", "kind": "send"},
             {"src": HUB, "dst": T, "amount_usd": 6e3, "ts": 11, "tx_hash": "0xc",
              "id": "e3", "kind": "spotTransfer"},
             {"src": T, "dst": TR, "amount_usd": 9e6, "ts": 12, "tx_hash": "0xd",
              "id": "e4", "kind": "internalTransfer"},
             {"src": "0x2000000000000000000000000000000000000000", "dst": T,
              "amount_usd": 9e6, "ts": 13, "tx_hash": "0xe", "id": "e5", "kind": "send"}]
    evs = at.from_hl_edges(edges, {T, TR}, exclude={HUB})
    assert [(e["direction"], e["hl_account"], e["counterparty"]) for e in evs] == \
        [("in", OTHER, T), ("out", OTHER, T)]
    found = at.classify_all(evs, INDEX)
    assert {f["kind"] for f in found} == {at.KIND_FUNDED_FROM_HIS_WORLD, at.KIND_PAID_HIS_WORLD}


def test_bridge2_rows_become_events():
    w = {"user": OUT, "destination": S, "usd": 1e6, "ts": 5, "tx_hash": "0xt", "log_index": 3,
         "nonce": 7}
    e = at.from_bridge2_withdrawal(w)
    assert (e["direction"], e["hl_account"], e["counterparty"], e["nonce"]) == ("out", OUT, S, 7)
    d = at.from_bridge2_deposit({"depositor": SINK, "usd": 2e5, "ts": 6, "tx_hash": "0xu",
                                 "log_index": 1})
    assert (d["direction"], d["hl_account"], d["counterparty"]) == ("in", SINK, SINK)
    assert at.classify(d, INDEX)["kind"] == at.KIND_MEMBER_ACTIVE


def test_finding_keys_are_stable_and_distinct():
    a = at.classify(ev("out", OUT, T), INDEX)
    b = at.classify(ev("out", OTHER, T), INDEX)
    assert a["key"] == at.finding_key(a) and a["key"] != b["key"]
    assert set(at.TITLES) == {at.KIND_PAID_HIS_WORLD, at.KIND_FUNDED_FROM_HIS_WORLD,
                              at.KIND_HIS_ACCOUNT_PAID_OUTSIDE, at.KIND_MEMBER_ACTIVE}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_attribution.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'attribution'`

- [ ] **Step 3: Implement** — `src/boundary/attribution.py`:

```python
# src/boundary/attribution.py
"""Who sent value to his world, and whom his world funded. Pure.

Every edge feed — Bridge2, Circle, Unit, HL sends — is normalised to one event
shape and judged against the perimeter (spec §6.2). Votes follow the doctrine:
money-flow vectors are one family, so nothing here promotes a wallet alone.
Severity: CRITICAL is about HIM moving now; history, unknown size and
inferences of ours are HIGH; associates are recorded and never buzz.
"""

from __future__ import annotations

from src.boundary.perimeter import STRONG_ROLES, SYSTEM_PREFIXES, low

DUST_USD = 100.0
SEVERITIES = ("CRITICAL", "HIGH")
KIND_PAID_HIS_WORLD = "outside_account_paid_his_world"
KIND_FUNDED_FROM_HIS_WORLD = "his_world_funded_outside_account"
KIND_HIS_ACCOUNT_PAID_OUTSIDE = "his_account_paid_outside_address"
KIND_MEMBER_ACTIVE = "perimeter_member_active_on_hl"
TITLES = {
    KIND_PAID_HIS_WORLD: "A Hyperliquid Account Outside His Cluster Paid His Address",
    KIND_FUNDED_FROM_HIS_WORLD: "His Address Funded A Hyperliquid Account Outside His Cluster",
    KIND_HIS_ACCOUNT_PAID_OUTSIDE: "His Account Sent Money To A New Address",
    KIND_MEMBER_ACTIVE: "An Address Holding His Money Is Active On Hyperliquid",
}
VOTE_TRANSFER, VOTE_LINKAGE = "transfer", "linkage"
HIGH_WEIGHT = 0.6


def event(*, source, direction, hl_account, counterparty, chain, amount_usd, ts, ref,
          counterparty_raw=None, event_id=None, retro=False, **extra) -> dict:
    return {"source": source, "direction": direction, "hl_account": low(hl_account),
            "counterparty": low(counterparty), "counterparty_raw": low(counterparty_raw) or None,
            "chain": chain, "amount_usd": None if amount_usd is None else float(amount_usd),
            "ts": ts, "ref": ref, "event_id": event_id or f"{source}:{ref}", "retro": bool(retro),
            **extra}


def from_bridge2_withdrawal(row: dict, *, retro: bool = False) -> dict:
    from src.boundary.bridge2 import event_id
    return event(source="bridge2", direction="out", hl_account=row["user"],
                 counterparty=row["destination"], chain="arbitrum", amount_usd=row["usd"],
                 ts=row.get("ts"), ref=row.get("tx_hash"), event_id=event_id(row),
                 retro=retro, nonce=row.get("nonce"))


def from_bridge2_deposit(row: dict, *, retro: bool = False) -> dict:
    from src.boundary.bridge2 import event_id
    return event(source="bridge2", direction="in", hl_account=row["depositor"],
                 counterparty=row["depositor"], chain="arbitrum", amount_usd=row["usd"],
                 ts=row.get("ts"), ref=row.get("tx_hash"), event_id=event_id(row), retro=retro)


def from_hl_edges(edges, core, *, exclude=frozenset()) -> list[dict]:
    """Core-ledger edges as events for the OTHER account. Hubs and system
    addresses are not people: a token distributor's airdrop into his account
    (`0x3d855cf5…` sent him $6,000 of SENT) is not an account paying him."""
    core = {low(c) for c in core}
    exclude = {low(x) for x in exclude}
    out = []
    for e in edges or []:
        src, dst = low(e.get("src")), low(e.get("dst"))
        if src in core and dst not in core:
            other, direction = dst, "in"
        elif dst in core and src not in core:
            other, direction = src, "out"
        else:
            continue
        if other in exclude or other.startswith(SYSTEM_PREFIXES):
            continue
        counterparty = src if direction == "in" else dst
        out.append(event(source="hl_send", direction=direction, hl_account=other,
                         counterparty=counterparty, chain="hyperliquid",
                         amount_usd=e.get("amount_usd"), ts=e.get("ts"),
                         ref=e.get("tx_hash"), event_id=e.get("id"), asset=e.get("asset")))
    return out


def finding_key(row: dict) -> str:
    return (f"{row.get('kind')}:{row.get('hl_account')}:"
            f"{row.get('counterparty') or row.get('counterparty_raw')}:"
            f"{row.get('ref') or row.get('event_id')}")


def _finding(ev: dict, kind: str, severity, vote, member) -> dict:
    if severity == "CRITICAL" and (ev.get("amount_usd") is None or ev.get("retro")):
        severity = "HIGH"      # unknown size, or history: never paged as a live move
    row = {**ev, "kind": kind, "severity": severity, "vote": vote,
           "role": (member or {}).get("role"), "member": (member or {}).get("address"),
           "member_why": (member or {}).get("why")}
    row["key"] = finding_key(row)
    return row


def classify(ev: dict, index) -> dict | None:
    account, counterparty = ev.get("hl_account"), ev.get("counterparty")
    raw = ev.get("counterparty_raw")
    if not account or not (counterparty or raw):
        return None
    usd = ev.get("amount_usd")
    if usd is not None and usd < DUST_USD:
        return None
    member = index.get(counterparty, raw)
    if account in index.core:
        if (ev.get("direction") == "out" and counterparty != account and member is None):
            return _finding(ev, KIND_HIS_ACCOUNT_PAID_OUTSIDE, "CRITICAL", None, None)
        return None
    if account == counterparty:
        own = index.get(account)
        if own is None:
            return None
        severity = "HIGH" if own["weight"] >= HIGH_WEIGHT else None
        return _finding(ev, KIND_MEMBER_ACTIVE, severity, None, own)
    if member is None:
        return None
    role, weight = member["role"], member["weight"]
    if ev.get("direction") == "out":
        kind = KIND_PAID_HIS_WORLD
        if role in STRONG_ROLES:
            severity, vote = "CRITICAL", VOTE_TRANSFER
        elif role == "deposit":
            severity, vote = "CRITICAL", VOTE_LINKAGE
        else:
            severity, vote = ("HIGH" if weight >= HIGH_WEIGHT else None), None
    else:
        kind = KIND_FUNDED_FROM_HIS_WORLD
        if role in STRONG_ROLES:
            severity, vote = "CRITICAL", VOTE_TRANSFER
        else:
            severity, vote = ("HIGH" if weight >= HIGH_WEIGHT else None), None
    return _finding(ev, kind, severity, vote, member)


def classify_all(events, index) -> list[dict]:
    out, seen = [], set()
    for ev in events or []:
        f = classify(ev, index)
        if f and f["key"] not in seen:
            seen.add(f["key"])
            out.append(f)
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_attribution.py -q -p no:cacheprovider`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/boundary/attribution.py tests/test_boundary_attribution.py
git commit -m "feat(boundary): attribution rules - who paid his world, whom it funded"
```

---

### Task 6: Blockscout v2 readers for hops and retro (`src/boundary/readers.py`)

**Files:**
- Create: `src/boundary/readers.py`
- Fixtures (captured 2026-10-06): `bs_inbound_dd53.json` (12 inbound transfers incl. 0xee7ae85f/0xd7a827fb and poisoning dust from 0x2df1df58), `bs_txs_to_dd53.json` (incoming txs; first gas from 0xd7a827fb), `bs_counters_dd53.json`, `bs_txlogs_payout.json` (treasury payout tx), `bs_txlogs_cctp_mint.json` (a Circle mint into the target)
- Test: `tests/test_boundary_readers.py`

**Interfaces:**
- Consumes: `logs.LogReadError`, `logs.to_int`
- Produces: `ReadError(RuntimeError)`; `class Budget(calls, seconds=None, clock=time.monotonic)` with `.spend()`, `.can(n=1)`, `.used`; `HOSTS` (arbitrum, ethereum, base, optimism, polygon → Blockscout base URL); `inbound_transfers(chain, address, *, since_ts, until_ts, budget, get=None, pages=2) -> list[transfer]` where transfer = `{"from","from_is_contract","from_is_scam","usd","token","symbol","ts","tx_hash","chain"}`; `first_gas(chain, address, *, budget, get=None, fresh_max=50) -> dict|None` = `{"from","ts","tx_hash","fresh":bool}` (None when not fresh or no value-bearing tx); `tx_logs(chain, tx_hash, *, budget, get=None) -> list[log]` in Etherscan shape (`address`, `topics`, `data`, `logIndex`, `transactionHash`, `blockNumber`).

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_readers.py`:

```python
"""Blockscout v2, read strictly and on a budget (spec §6.3, §7.2)."""

import json
from pathlib import Path

import pytest

from src.boundary import bridge2, readers

FIX = Path(__file__).parent / "fixtures" / "boundary"
DD53 = "0xdd53c5297309130ab5fe5623dc905752e3342b13"


def _fx(name):
    return json.loads((FIX / name).read_text())


def test_inbound_transfers_are_valued_by_contract_rate_and_bounded_in_time():
    doc = _fx("bs_inbound_dd53.json")
    got = readers.inbound_transfers("arbitrum", DD53, since_ts=0, until_ts=2_000_000_000,
                                    budget=readers.Budget(5), get=lambda url, params: doc, pages=1)
    assert len(got) == len(doc["items"])
    senders = {t["from"] for t in got}
    assert "0xee7ae85f2fe2239e27d9c1e23fffe168d63b4055" in senders
    big = max(got, key=lambda t: t["usd"] or 0)
    assert big["usd"] > 1_000_000 and big["symbol"] == "USDC" and big["ts"] > 1_700_000_000
    dust = [t for t in got if t["from"] == "0x2df1df582d0a1efc7178fd78b2bcd9aa08a73df7"]
    assert dust and all(t["usd"] < 1 and t["from_is_contract"] for t in dust)


def test_inbound_transfers_stop_at_the_window_start():
    doc = _fx("bs_inbound_dd53.json")
    newest = max(readers._ts(i["timestamp"]) for i in doc["items"])
    got = readers.inbound_transfers("arbitrum", DD53, since_ts=newest, until_ts=newest,
                                    budget=readers.Budget(5), get=lambda url, params: doc)
    assert got and all(t["ts"] == newest for t in got)


def test_first_gas_comes_from_the_oldest_value_bearing_incoming_transaction():
    answers = {"counters": _fx("bs_counters_dd53.json"), "transactions": _fx("bs_txs_to_dd53.json")}

    def get(url, params):
        return answers["counters"] if url.endswith("/counters") else answers["transactions"]
    gas = readers.first_gas("arbitrum", DD53, budget=readers.Budget(5), get=get)
    assert gas["from"] == "0xd7a827fbaf38c98e8336c5658e4bcbcd20a4fd2d" and gas["fresh"]


def test_first_gas_is_not_read_for_a_busy_address():
    def get(url, params):
        return {"transactions_count": "5000", "token_transfers_count": "9000"}
    assert readers.first_gas("arbitrum", DD53, budget=readers.Budget(5), get=get) is None


def test_tx_logs_come_back_in_etherscan_shape_and_decode():
    doc = _fx("bs_txlogs_payout.json")
    rows = readers.tx_logs("arbitrum", "0xc0758212634d6d92262e4c9d1bf25b51c8da13e1b30bb060bdf21387703761ad",
                           budget=readers.Budget(2), get=lambda url, params: doc)
    payouts = [r for r in (bridge2.decode_withdrawal(x) for x in rows) if r]
    assert any(p["user"] == "0x1419e75330c71ce463102e6a1eb62fe80b412d5f" for p in payouts)


def test_a_spent_budget_or_failed_read_raises_never_empty():
    b = readers.Budget(1)
    b.spend()
    with pytest.raises(readers.ReadError):
        b.spend()

    def down(url, params):
        raise OSError("down")
    with pytest.raises(readers.ReadError):
        readers.inbound_transfers("arbitrum", DD53, since_ts=0, until_ts=1,
                                  budget=readers.Budget(3), get=down)
    with pytest.raises(readers.ReadError):
        readers.inbound_transfers("monad", DD53, since_ts=0, until_ts=1, budget=readers.Budget(3))
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_readers.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'readers'`

- [ ] **Step 3: Implement** — `src/boundary/readers.py`:

```python
# src/boundary/readers.py
"""Keyless Blockscout v2 reads for provenance hops and retro attribution.

Every reader spends from a `Budget` and RAISES on failure (rule 5): a hop that
could not be read is recorded as unreadable by the caller, never as "no
source". Values come from Blockscout's per-CONTRACT exchange rate, so a forged
"USDC" (a different contract) has no rate and stays unvalued (rules 2, 6).
"""

from __future__ import annotations

import time
from datetime import datetime

from src.boundary.logs import LogReadError, _http_get, to_int

HOSTS = {
    "arbitrum": "https://arbitrum.blockscout.com",
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
}
FRESH_MAX_TXS = 50


class ReadError(RuntimeError):
    pass


class Budget:
    """Calls (and optionally seconds) a run may spend on Blockscout."""

    def __init__(self, calls: int, seconds: float | None = None, clock=time.monotonic):
        self.calls, self.seconds, self.clock = int(calls), seconds, clock
        self.started, self.used = clock(), 0

    def can(self, n: int = 1) -> bool:
        if self.seconds is not None and self.clock() - self.started >= self.seconds:
            return False
        return self.used + n <= self.calls

    def spend(self) -> None:
        if not self.can():
            raise ReadError("read budget spent")
        self.used += 1


def _ts(value) -> int:
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def _call(url: str, params: dict, budget: Budget, get) -> dict:
    budget.spend()
    try:
        doc = (get or _http_get)(url, params)
    except (LogReadError, Exception) as exc:  # noqa: BLE001 - reported as unreadable
        raise ReadError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ReadError(f"unexpected answer {type(doc).__name__}")
    return doc


def _host(chain: str) -> str:
    host = HOSTS.get(chain)
    if not host:
        raise ReadError(f"no keyless reader for {chain}")
    return host


def _transfer(item: dict, chain: str) -> dict:
    token, total, frm = item.get("token") or {}, item.get("total") or {}, item.get("from") or {}
    usd = None
    try:
        decimals = int(total.get("decimals") or token.get("decimals"))
        rate = token.get("exchange_rate")
        if rate is not None:
            usd = int(total.get("value")) / 10 ** decimals * float(rate)
    except (TypeError, ValueError):
        usd = None
    return {"from": (frm.get("hash") or "").lower(), "from_is_contract": bool(frm.get("is_contract")),
            "from_is_scam": bool(frm.get("is_scam")), "usd": usd,
            "token": (token.get("address_hash") or "").lower(), "symbol": token.get("symbol"),
            "ts": _ts(item["timestamp"]), "tx_hash": (item.get("transaction_hash") or "").lower(),
            "chain": chain}


def inbound_transfers(chain: str, address: str, *, since_ts: int, until_ts: int, budget: Budget,
                      get=None, pages: int = 2) -> list[dict]:
    """ERC-20 transfers INTO `address` with since_ts <= ts <= until_ts, newest first."""
    url = f"{_host(chain)}/api/v2/addresses/{address}/token-transfers"
    params = {"type": "ERC-20", "filter": "to"}
    out = []
    for _ in range(max(1, pages)):
        doc = _call(url, params, budget, get)
        items = doc.get("items")
        if not isinstance(items, list):
            raise ReadError("invalid Blockscout page")
        older = False
        for item in items:
            try:
                row = _transfer(item, chain)
            except (KeyError, TypeError, ValueError):
                continue
            if row["ts"] > until_ts:
                continue
            if row["ts"] < since_ts:
                older = True
                break
            out.append(row)
        nxt = doc.get("next_page_params")
        if older or not isinstance(nxt, dict) or not nxt:
            break
        params = {"type": "ERC-20", "filter": "to", **nxt}
    return out


def first_gas(chain: str, address: str, *, budget: Budget, get=None,
              fresh_max: int = FRESH_MAX_TXS) -> dict | None:
    """Who paid a FRESH address its first native gas; None for an established one."""
    host = _host(chain)
    counters = _call(f"{host}/api/v2/addresses/{address}/counters", {}, budget, get)
    try:
        txs = int(counters.get("transactions_count") or 0)
    except (TypeError, ValueError):
        return None
    if txs > fresh_max:
        return None
    doc = _call(f"{host}/api/v2/addresses/{address}/transactions", {"filter": "to"}, budget, get)
    rows = [i for i in doc.get("items") or [] if isinstance(i, dict)
            and str(i.get("value") or "0") not in ("0", "")
            and (i.get("from") or {}).get("hash")]
    if not rows:
        return None
    oldest = min(rows, key=lambda i: _ts(i["timestamp"]))
    return {"from": oldest["from"]["hash"].lower(), "ts": _ts(oldest["timestamp"]),
            "tx_hash": (oldest.get("hash") or "").lower(), "fresh": True}


def tx_logs(chain: str, tx_hash: str, *, budget: Budget, get=None) -> list[dict]:
    """A transaction's logs in Etherscan's shape, so the log decoders apply."""
    doc = _call(f"{_host(chain)}/api/v2/transactions/{tx_hash}/logs", {}, budget, get)
    items = doc.get("items")
    if not isinstance(items, list):
        raise ReadError("invalid Blockscout logs page")
    out = []
    for i in items:
        out.append({"address": ((i.get("address") or {}).get("hash") or "").lower(),
                    "topics": [t for t in (i.get("topics") or []) if t],
                    "data": i.get("data") or "0x", "logIndex": i.get("index"),
                    "transactionHash": (i.get("transaction_hash") or tx_hash).lower(),
                    "blockNumber": i.get("block_number"), "timeStamp": None})
    return out


def block_of(row: dict) -> int:
    return to_int(row.get("blockNumber"))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_readers.py -q -p no:cacheprovider`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/boundary/readers.py tests/test_boundary_readers.py tests/fixtures/boundary/bs_*.json
git commit -m "feat(boundary): budgeted keyless Blockscout v2 readers for hops and retro"
```

---

### Task 7: Alerts (`src/alerts.py`)

**Files:**
- Modify: `src/alerts.py` (append three functions after `alert_trace_reached`)
- Test: `tests/test_boundary_alerts.py`

**Interfaces:**
- Consumes: `attribution.TITLES`, `attribution.SEVERITIES`, `links.address_line`, `_send_with_cooldown`
- Produces: `alert_boundary_finding(row) -> bool`, `alert_provenance_hit(row) -> bool`, `alert_perimeter_hl_account(member: dict, reading: dict) -> bool`. Each returns False without sending for a severity outside `SEVERITIES`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_alerts.py`:

```python
"""Boundary alerts speak the routing vocabulary and never page a recorded row."""

from src import alerts
from src.boundary import attribution as at

KNOWN = ("CRITICAL", "HIGH")


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append((key, subject, body)) or True)
    return sent


def _row(**kw):
    base = {"kind": at.KIND_PAID_HIS_WORLD, "severity": "CRITICAL", "vote": "linkage",
            "role": "deposit", "member": "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e",
            "member_why": "his private exchange deposit address", "source": "bridge2",
            "hl_account": "0x" + "1" * 40, "counterparty": "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e",
            "amount_usd": 250_000.0, "ts": 1_790_000_000, "ref": "0xabc", "chain": "arbitrum",
            "key": "k1", "retro": False}
    base.update(kw)
    return base


def test_a_boundary_finding_routes_with_its_severity(monkeypatch):
    sent = _capture(monkeypatch)
    assert alerts.alert_boundary_finding(_row())
    key, subject, body = sent[0]
    assert alerts._severity_of(subject) == "CRITICAL"
    assert "0x" + "1" * 40 in body and "deposit" in body and "$250,000" in body


def test_history_is_labelled_and_recorded_rows_never_send(monkeypatch):
    sent = _capture(monkeypatch)
    assert alerts.alert_boundary_finding(_row(severity="HIGH", retro=True))
    assert "historical" in sent[0][1].lower() and alerts._severity_of(sent[0][1]) == "HIGH"
    assert not alerts.alert_boundary_finding(_row(severity=None))
    assert len(sent) == 1


def test_every_title_and_severity_is_routable(monkeypatch):
    sent = _capture(monkeypatch)
    for kind in at.TITLES:
        for sev in KNOWN:
            alerts.alert_boundary_finding(_row(kind=kind, severity=sev, key=f"{kind}{sev}"))
    assert len(sent) == len(at.TITLES) * 2
    assert all(alerts._severity_of(s) in KNOWN for _, s, _ in sent)


def test_provenance_and_perimeter_alerts(monkeypatch):
    sent = _capture(monkeypatch)
    assert alerts.alert_provenance_hit({
        "account": "0x" + "2" * 40, "severity": "CRITICAL", "hop": 1, "role": "core",
        "member": "0x45d26f28196d226497130c4bac709d808fed4029", "route": "bridge2",
        "usd": 3e6, "ts": 1_790_000_000, "key": "p1", "via": None})
    assert alerts.alert_perimeter_hl_account(
        {"address": "0x" + "3" * 40, "role": "sink", "why": "quiet wallet holding $500,000"},
        {"account_value": 25_000.0, "all_time_volume": 2e6, "birth": "2026-09-01"})
    assert [alerts._severity_of(s) for _, s, _ in sent] == ["CRITICAL", "HIGH"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_alerts.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'src.alerts' has no attribute 'alert_boundary_finding'`

- [ ] **Step 3: Implement** — append to `src/alerts.py` after `alert_trace_reached`:

```python
def _when(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return "unknown"


def alert_boundary_finding(row: dict) -> bool:
    """Money crossed Hyperliquid's edge between an outside account and his world.

    Read from the protocol's own record (Bridge2's FinalizedWithdrawal, Circle's
    message, Unit's operation, the HL ledger), so both ends are named. The
    severity is decided in src/boundary/attribution.py: CRITICAL is a live move
    touching his wallets or private deposit addresses; HIGH is history, an
    unknown amount, or an address that only holds his money.
    """
    from src.boundary.attribution import SEVERITIES, TITLES

    level = row.get("severity")
    if level not in SEVERITIES:
        return False
    title = TITLES.get(row.get("kind"), "Money Crossed Hyperliquid's Edge")
    if row.get("retro"):
        title += " (historical)"
    usd = row.get("amount_usd")
    amount = f"${float(usd):,.0f}" if usd is not None else "unknown (not priced)"
    body = (
        f"{address_line(row.get('hl_account') or '', 'Hyperliquid account')}\n"
        f"{address_line(row.get('counterparty') or row.get('counterparty_raw') or '', 'Address')}\n"
        f"Route: {row.get('source')} on {row.get('chain')} ({row.get('direction')} of Hyperliquid)\n"
        f"Amount: {amount}\nWhen: {_when(row.get('ts'))}\nReference: {row.get('ref')}\n"
        f"His world: {row.get('role') or 'outside'} - {row.get('member_why') or 'n/a'}\n\n"
        f"The protocol's own record names both ends. A transfer is still not\n"
        f"ownership: check the account on Hyperliquid, then who funded it.\n")
    return _send_with_cooldown(f"boundary_{row.get('key')}", 168,
                               f"[EZEKIEL] {level}: {title}", body)


def alert_provenance_hit(row: dict) -> bool:
    """A new or reactivated Hyperliquid account's money came from his world."""
    from src.boundary.attribution import SEVERITIES

    level = row.get("severity")
    if level not in SEVERITIES:
        return False
    usd = row.get("usd")
    body = (
        f"{address_line(row.get('account') or '', 'Hyperliquid account')}\n"
        f"{address_line(row.get('member') or '', 'Funded from')}\n"
        f"Role of the source: {row.get('role')} - {row.get('member_why') or 'n/a'}\n"
        f"Hops from the account: {row.get('hop')}"
        f"{' via ' + str(row.get('via')) if row.get('via') else ''}\n"
        f"Entry route: {row.get('route')}; amount "
        f"{'$' + format(float(usd), ',.0f') if usd is not None else 'unknown'} at {_when(row.get('ts'))}\n\n"
        f"Traced backwards from the account's own funding. Hop 1 is a direct\n"
        f"transfer; hop 2 runs through one wallet in between.\n")
    return _send_with_cooldown(f"provenance_{row.get('key')}", 168,
                               f"[EZEKIEL] {level}: A Hyperliquid Account Was Funded From His World",
                               body)


def alert_perimeter_hl_account(member: dict, reading: dict) -> bool:
    """An address holding his money (or that funded him) is a Hyperliquid account."""
    body = (
        f"{address_line(member.get('address') or '', 'Address')}\n"
        f"Why it is in his world: {member.get('role')} - {member.get('why')}\n"
        f"Hyperliquid: account value ${float(reading.get('account_value') or 0):,.0f}, "
        f"all-time volume ${float(reading.get('all_time_volume') or 0):,.0f}, "
        f"first funded {reading.get('birth') or 'unknown'}\n\n"
        f"Not one of his configured wallets. Check whether it trades like him.\n")
    return _send_with_cooldown(f"perimeter_hl_{(member.get('address') or '').lower()}", 168,
                               "[EZEKIEL] HIGH: An Address Holding His Money Trades On Hyperliquid",
                               body)
```

Check the imports at the top of `src/alerts.py` already provide `datetime`, `UTC` and `address_line`; add `from src.links import address_line` there if it is not imported.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_alerts.py tests/test_alert_severity_vocabulary.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/alerts.py tests/test_boundary_alerts.py
git commit -m "feat(alerts): boundary, provenance and perimeter-account alerts"
```

---

### Task 8: Build the perimeter and close the loop (`scripts/build_perimeter.py`)

**Files:**
- Create: `scripts/build_perimeter.py`
- Test: `tests/test_build_perimeter.py`

**Interfaces:**
- Consumes: `perimeter.build/associates/exchange_families/Index`, `src.trace.store.load`, `chain.activity.ActivityCache` (read-only, `max_lookups=0`), `cctp_feed.strict_post`, `hl_budget.ReadBudget`, `alerts.alert_perimeter_hl_account`.
- Produces: `data/perimeter/latest.json` = perimeter doc + `"associates"`, `"substrate_at"`, `"closed": {"checked", "unreadable", "active"}`, `"alerted": [addr]`; functions `main(argv=None, *, post=None, substrate=None, now=None) -> int`, `hl_reading(address, post) -> dict` (`{"checked_at","read_ok","account_value","all_time_volume","max_value","birth","active", ["error"]}`), `load_services(config, data_dir) -> (services:set, hot:set)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_build_perimeter.py`:

```python
"""The perimeter step: measured inputs in, one table out, loop closed on HL."""

import json

import pytest

import scripts.build_perimeter as bp
from src import utils

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
F = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SINK = "0x" + "a" * 40
HOT = "0xee7ae85f2fe2239e27d9c1e23fffe168d63b4055"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "load_config",
                        lambda: {"target_wallet": T, "known_self_wallets": [TR, F]})
    (tmp_path / "deposit_sentinels").mkdir()
    (tmp_path / "deposit_sentinels" / "latest.json").write_text(
        json.dumps({"sentinels": {S: {"reason": "conduit"}}}))
    (tmp_path / "trace" / "registry").mkdir(parents=True)
    (tmp_path / "trace" / "registry" / "aa.json").write_text(json.dumps(
        {SINK: {"class": "quiet_eoa", "his_money": {"in_usd": 5e5, "share": 1.0}}}))
    (tmp_path / "trace" / "latest.json").write_text(json.dumps({"funders": [], "deposit_addresses": []}))
    sent = []
    monkeypatch.setattr("src.alerts.alert_perimeter_hl_account",
                        lambda member, reading: sent.append(member["address"]) or True)
    return tmp_path, sent


def _post(active=()):
    def post(body):
        user = body["user"]
        if body["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "25000.0" if user in active else "0.0"}}
        if body["type"] == "spotClearinghouseState":
            return {"balances": []}
        if body["type"] == "portfolio":
            hist = [[1_780_000_000_000, "0.0"], [1_781_000_000_000, "25000.0"]] if user in active else []
            return [["allTime", {"accountValueHistory": hist, "vlm": "2000000.0" if user in active else "0.0"}]]
        raise AssertionError(body)
    return post


def _substrate(addresses):
    return {a: ([{"src": S, "dst": HOT, "amount_usd": 5e6, "chain": "arbitrum"}] if a == S else [])
            for a in addresses}


def test_the_perimeter_is_written_with_roles_families_and_readings(sandbox):
    tmp, sent = sandbox
    assert bp.main([], post=_post(active={SINK}), substrate=_substrate,
                   now="2026-10-06T00:00:00+00:00", is_hot=lambda a: a == HOT) == 0
    doc = json.loads((tmp / "perimeter" / "latest.json").read_text())
    roles = {a: m["role"] for a, m in doc["members"].items()}
    assert roles == {T: "core", TR: "core", F: "core", S: "deposit", SINK: "sink"}
    assert doc["exchange_families"] == {f"deposit:{S}": [HOT]}
    assert doc["members"][SINK]["hl"]["active"] is True and doc["members"][S]["hl"]["active"] is False
    assert sent == [SINK]
    assert doc["closed"]["active"] == [SINK]


def test_an_active_member_alerts_once(sandbox):
    tmp, sent = sandbox
    for _ in range(2):
        bp.main([], post=_post(active={SINK}), substrate=_substrate,
                now="2026-10-06T00:00:00+00:00", is_hot=lambda a: a == HOT, close_every_s=0)
    assert sent == [SINK]


def test_a_failed_read_is_unreadable_not_inactive(sandbox):
    tmp, sent = sandbox

    def down(body):
        raise RuntimeError("info endpoint unavailable")
    bp.main([], post=down, substrate=_substrate, now="2026-10-06T00:00:00+00:00",
            is_hot=lambda a: False)
    doc = json.loads((tmp / "perimeter" / "latest.json").read_text())
    assert doc["members"][SINK]["hl"]["read_ok"] is False
    assert SINK in doc["closed"]["unreadable"] and not sent


def test_dry_run_writes_only_into_its_directory(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    out = tmp_path_factory.mktemp("dry")
    bp.main(["--dry-run", str(out)], post=_post(active={SINK}), substrate=_substrate,
            now="2026-10-06T00:00:00+00:00", is_hot=lambda a: False)
    assert (out / "latest.json").exists() and not (tmp / "perimeter").exists() and not sent
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_build_perimeter.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.build_perimeter'`

- [ ] **Step 3: Implement** — `scripts/build_perimeter.py`:

```python
#!/usr/bin/env python3
"""Build his perimeter (data/perimeter/latest.json) and close the loop on Hyperliquid.

    python scripts/build_perimeter.py                # the trace.yml step
    python scripts/build_perimeter.py --dry-run DIR  # read-only: writes only DIR, no alerts

Every role comes from a file another detector wrote (spec §4). The substrate pass
that finds associates and exchange families is the slow part, so it runs once a
day and its result is carried in the file between runs. Single writer:
data/perimeter/.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import utils  # noqa: E402

CLOSE_PER_RUN = 25
CLOSE_EVERY_S = 24 * 3600
SUBSTRATE_EVERY_S = 24 * 3600
ACTIVE_VALUE_USD = 10_000.0
ACTIVE_VOLUME_USD = 100_000.0


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _age_s(stamp, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(str(stamp))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def load_services(config: dict, data_dir: Path) -> tuple[set, set]:
    """(services, labelled exchange hot wallets), read at call time."""
    from src.chain.labels import SERVICE_CATEGORIES, load_registry
    registry = load_registry(Path(data_dir) / "labels" / "entities.json")
    services = {a for a, e in registry.items()
                if e.get("category") in SERVICE_CATEGORIES - {"cex_deposit", "cex_deposit_sweep"}}
    services |= {(a or "").lower() for a in (config.get("known_service_addresses") or [])
                 + (config.get("excluded_addresses") or [])}
    services |= {(w.get("wallet") or "").lower()
                 for w in _read(Path(data_dir) / "roster" / "latest.json").get("wallets") or []
                 if w.get("tier") == "INFRASTRUCTURE"}
    hot = {a for a, e in registry.items() if e.get("category") == "cex_hot"}
    return services - {""}, hot


def measured(data_dir: Path):
    """(is_contract, is_busy) from the shared caches, never spending a lookup."""
    from src.chain.activity import MEASURABLE_CHAINS, ActivityCache, is_busy
    cache = ActivityCache(Path(data_dir) / "labels" / "address_activity.json", max_lookups=0)
    code = _read(Path(data_dir) / "labels" / "code_cache.json")

    def readings(a):
        return [r for r in (cache.cached(a, c) for c in MEASURABLE_CHAINS) if isinstance(r, dict)]

    def contract(a):
        return (any(code.get(f"{c}:{a}") is True for c in MEASURABLE_CHAINS)
                or any(r.get("is_contract") for r in readings(a)))

    def busy(a):
        return any(is_busy(r) for r in readings(a))
    return contract, busy


def hl_reading(address: str, post) -> dict:
    """Three strict reads. A failure is unreadable, never an empty account (rule 5)."""
    checked = datetime.now(UTC).isoformat()
    try:
        state = post({"type": "clearinghouseState", "user": address}) or {}
        post({"type": "spotClearinghouseState", "user": address})
        portfolio = post({"type": "portfolio", "user": address}) or []
    except Exception as exc:  # noqa: BLE001 - transport or budget, reported
        return {"checked_at": checked, "read_ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    value = float((state.get("marginSummary") or {}).get("accountValue") or 0)
    volume, peak, birth = 0.0, 0.0, None
    for name, body in portfolio if isinstance(portfolio, list) else []:
        if name != "allTime":
            continue
        volume = float((body or {}).get("vlm") or 0)
        points = [(t, float(v)) for t, v in (body or {}).get("accountValueHistory") or []
                  if float(v) != 0]
        if points:
            peak = max(v for _, v in points)
            birth = datetime.fromtimestamp(points[0][0] / 1000, tz=UTC).date().isoformat()
    active = (value >= ACTIVE_VALUE_USD or peak >= ACTIVE_VALUE_USD
              or volume >= ACTIVE_VOLUME_USD)
    return {"checked_at": checked, "read_ok": True, "account_value": value,
            "all_time_volume": volume, "max_value": peak, "birth": birth, "active": active}


def default_post():
    from scripts.run_trace_engine import paced_post
    from src.hl_budget import ReadBudget
    budget = ReadBudget(seconds=120, weight_per_minute=400)
    return paced_post(budget)


def main(argv=None, *, post=None, substrate=None, now=None, is_hot=None,
         close_every_s: float = CLOSE_EVERY_S) -> int:
    from src.boundary import perimeter as pm
    from src.trace import store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", metavar="DIR")
    args = parser.parse_args(argv)
    config = utils.load_config()
    data = utils.DATA_DIR
    now_dt = datetime.fromisoformat(now) if now else datetime.now(UTC)
    now_iso = now_dt.isoformat()
    out_dir = Path(args.dry_run) if args.dry_run else data / "perimeter"
    previous = _read(data / "perimeter" / "latest.json")

    sentinels = _read(data / "deposit_sentinels" / "latest.json").get("sentinels") or {}
    trace_report = _read(data / "trace" / "latest.json")
    notes = []
    try:
        registry = store.load(data / "trace" / "registry")
    except RuntimeError as exc:
        registry, notes = {}, [f"trace registry unreadable: {exc}"]
    solana = _read(data / "labels" / "solana_addresses.json").get("addresses") or {}
    services, hot = load_services(config, data)
    contract, busy = measured(data)
    hot_test = is_hot or (lambda a: a in hot or busy(a) or contract(a))
    core = {(config.get("target_wallet") or "").lower()} | {
        (w or "").lower() for w in config.get("known_self_wallets") or []}
    core.discard("")
    deposits = set(sentinels) | {(r.get("address") or "").lower()
                                 for r in trace_report.get("deposit_addresses") or []}

    if _age_s(previous.get("substrate_at"), now_dt) >= SUBSTRATE_EVERY_S or not previous:
        if substrate is None:
            from src.chain.collect import records_by_wallet as substrate
        rows = substrate(sorted(core | deposits))
        records = [r for a in sorted(rows) for r in rows[a]]
        found = pm.associates([r for a in core for r in rows.get(a, [])], core,
                              is_contract=contract, is_busy=busy, services=services)
        families = pm.exchange_families(records, deposits, core, is_hot=hot_test)
        substrate_at = now_iso
    else:
        found = previous.get("associates") or {}
        families = previous.get("exchange_families") or {}
        substrate_at = previous.get("substrate_at")

    doc = pm.build(config=config, sentinels=sentinels, trace_report=trace_report,
                   trace_registry=registry, solana=solana, associates_found=found,
                   families=families, services=services, previous=previous, now_iso=now_iso)
    doc.update(associates=found, substrate_at=substrate_at, notes=notes,
               alerted=list(previous.get("alerted") or []))

    post = post or default_post()
    due = sorted((m for m in doc["members"].values()
                  if m["role"] != "core" and str(m["address"]).startswith("0x")
                  and _age_s((m.get("hl") or {}).get("checked_at"), now_dt) >= close_every_s),
                 key=lambda m: str((m.get("hl") or {}).get("checked_at") or ""))
    closed = {"checked": 0, "unreadable": [], "active": []}
    for m in due[:CLOSE_PER_RUN]:
        m["hl"] = hl_reading(m["address"], post)
        closed["checked"] += 1
        if not m["hl"]["read_ok"]:
            closed["unreadable"].append(m["address"])
    for m in doc["members"].values():
        if (m.get("hl") or {}).get("active") and m["role"] != "core":
            closed["active"].append(m["address"])
    doc["closed"] = closed

    if not args.dry_run:
        from src.alerts import alert_perimeter_hl_account
        for addr in closed["active"]:
            m = doc["members"][addr]
            if addr in doc["alerted"] or m["weight"] < 0.6:
                continue
            if alert_perimeter_hl_account(m, m["hl"]):
                doc["alerted"].append(addr)
    out_dir.mkdir(parents=True, exist_ok=True)
    utils.atomic_write_json(out_dir / "latest.json", doc)
    print(f"[perimeter] {len(doc['members'])} members {doc['counts']}; HL checked "
          f"{closed['checked']} ({len(closed['unreadable'])} unreadable), "
          f"{len(closed['active'])} active non-core")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_build_perimeter.py -q -p no:cacheprovider`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/build_perimeter.py tests/test_build_perimeter.py
git commit -m "feat(boundary): build the perimeter each trace run and close the loop on HL"
```

---

### Task 9: Attribution step (`scripts/check_boundary.py`, watch.yml)

**Files:**
- Create: `scripts/check_boundary.py`
- Test: `tests/test_check_boundary.py`

**Interfaces:**
- Consumes: Tasks 1–7; `src.trace.store.load` (hubs, read-only); `alerts.alert_foreign_destination` (existing; key `foreign_{kind}_{destination}` shared with `check_withdrawals.py`).
- Produces: `data/boundary/latest.json` = `{"computed_at","perimeter_fallback","head_block","withdrawal_cursor","blocks_read","withdrawals_read","read_error","core_withdrawals":{nonce:{user,destination,usd,ts,tx_hash}},"hl_edges_cursor","findings":[...],"alerted":[key],"undelivered":[row],"unit_checked":{addr:iso},"retro":{addr:{"bridge2":iso|None,"unit":iso|None}},"counts":{...},"errors":[...]}`; `main(argv=None, *, readers=None, now=None) -> int`; `default_readers() -> dict` with keys `head, withdrawals(lo,hi), user_withdrawals(user), payouts(member), tx_logs(tx), unit(addr)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_check_boundary.py`:

```python
"""The watch.yml attribution step, every reader injected (no network)."""

import json

import pytest

import scripts.check_boundary as cb
from src import utils
from src.boundary import bridge2

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SOL = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"
OUT, NEW = "0x" + "1" * 40, "0x" + "2" * 40
HUB = "0x" + "3" * 40


def fw(user, dest, usd, block, tx, nonce=1, index=0):
    """A FinalizedWithdrawal log in the shape getLogs returns."""
    data = ("0x" + "0" * 24 + dest[2:] + format(int(usd * 1e6), "064x")
            + format(nonce, "064x") + "ab" * 32)
    return {"address": bridge2.BRIDGE, "topics": [bridge2.TOPIC_FINALIZED_WITHDRAWAL,
                                                   bridge2.topic_address(user)],
            "data": data, "blockNumber": hex(block), "timeStamp": hex(1_790_000_000 + block),
            "logIndex": hex(index), "transactionHash": tx}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "load_config", lambda: {"target_wallet": T, "known_self_wallets": [TR]})
    (tmp_path / "perimeter").mkdir()
    (tmp_path / "perimeter" / "latest.json").write_text(json.dumps({"members": {
        T: {"address": T, "role": "core", "weight": 1.0, "why": "config"},
        TR: {"address": TR, "role": "core", "weight": 1.0, "why": "config"},
        S: {"address": S, "role": "deposit", "weight": 1.0, "why": "his Binance deposit address"},
        SOL: {"address": SOL, "role": "identity", "weight": 1.0, "why": "Solana", "raw": None}}}))
    sent = {"boundary": [], "foreign": []}
    monkeypatch.setattr("src.alerts.alert_boundary_finding",
                        lambda row: sent["boundary"].append(row) or True)
    monkeypatch.setattr("src.alerts.alert_foreign_destination",
                        lambda *a, **k: sent["foreign"].append(a) or True)
    return tmp_path, sent


def readers(live=(), user=None, payouts=None, tx_logs=None, unit=None, head=1_000, fail_at=None):
    def withdrawals(lo, hi):
        if fail_at is not None and hi >= fail_at:
            from src.boundary.logs import LogReadError
            raise LogReadError("blockscout: 429")
        return [log for log in live if lo <= int(log["blockNumber"], 16) <= hi]
    return {"head": lambda: head, "withdrawals": withdrawals,
            "user_withdrawals": lambda u: (user or {}).get(u, []),
            "payouts": lambda m: (payouts or {}).get(m, []),
            "tx_logs": lambda tx: (tx_logs or {}).get(tx, []),
            "unit": lambda a: (unit or {}).get(a, {"addresses": [], "operations": []})}


def _state(tmp):
    return json.loads((tmp / "boundary" / "latest.json").read_text())


def test_first_run_is_history_then_a_live_payment_to_his_deposit_address_pages(sandbox):
    tmp, sent = sandbox
    cb.main([], readers=readers(live=[fw(OUT, S, 250_000, 900, "0xold")]))
    st = _state(tmp)
    assert st["withdrawal_cursor"] == 1_000
    first = [f for f in st["findings"] if f["ref"] == "0xold"][0]
    assert first["retro"] and first["severity"] == "HIGH" and first["vote"] == "linkage"
    cb.main([], readers=readers(live=[fw(NEW, S, 300_000, 1_500, "0xnew")], head=2_000))
    st = _state(tmp)
    live = [f for f in st["findings"] if f["ref"] == "0xnew"][0]
    assert live["severity"] == "CRITICAL" and not live["retro"] and live["key"] in st["alerted"]
    assert [r["ref"] for r in sent["boundary"]] == ["0xold", "0xnew"]


def test_his_account_withdrawing_to_a_new_address_reuses_the_foreign_destination_alert(sandbox):
    tmp, sent = sandbox
    cb.main([], readers=readers(user={TR: [fw(TR, NEW, 1e6, 50, "0xtr", nonce=7)]}))
    st = _state(tmp)
    assert st["core_withdrawals"]["7"]["destination"] == NEW
    assert sent["foreign"] and sent["foreign"][0][1] == "withdraw3" and sent["foreign"][0][2] == NEW


def test_a_failed_read_keeps_the_cursor_and_the_other_sources_still_run(sandbox):
    tmp, sent = sandbox
    cb.main([], readers=readers(head=500))
    cb.main([], readers=readers(head=300_000, fail_at=200_000,
                                live=[fw(OUT, T, 9e5, 150_000, "0xa")]))
    st = _state(tmp)
    assert st["withdrawal_cursor"] == 100_500 and "429" in st["read_error"]
    assert any(f["ref"] == "0xa" for f in st["findings"])


def test_without_a_perimeter_file_the_config_wallets_are_still_watched(sandbox):
    tmp, sent = sandbox
    (tmp / "perimeter" / "latest.json").unlink()
    cb.main([], readers=readers(head=500))
    cb.main([], readers=readers(head=900, live=[fw(OUT, T, 5e5, 700, "0xb")]))
    st = _state(tmp)
    assert st["perimeter_fallback"] and any(f["severity"] == "CRITICAL" for f in st["findings"])


def test_an_undelivered_alert_is_retried_and_not_marked_seen(sandbox, monkeypatch):
    tmp, sent = sandbox
    cb.main([], readers=readers(head=500))
    monkeypatch.setattr("src.alerts.alert_boundary_finding", lambda row: False)
    cb.main([], readers=readers(head=900, live=[fw(OUT, S, 5e5, 700, "0xc")]))
    st = _state(tmp)
    assert len(st["undelivered"]) == 1 and not st["alerted"]
    monkeypatch.setattr("src.alerts.alert_boundary_finding", lambda row: True)
    cb.main([], readers=readers(head=950))
    st = _state(tmp)
    assert not st["undelivered"] and st["alerted"]


def test_unit_withdrawals_to_his_solana_wallet_page_high(sandbox):
    tmp, sent = sandbox
    op = {"opCreatedAt": "2026-10-05T10:00:00Z", "sourceChain": "hyperliquid",
          "destinationChain": "solana", "sourceAddress": OUT, "destinationAddress": SOL,
          "asset": "sol", "sourceAmount": "100", "state": "done", "sourceTxHash": "u1"}
    cb.main([], readers=readers(head=500, unit={SOL: {"addresses": [], "operations": [op]}}))
    f = [f for f in _state(tmp)["findings"] if f["source"] == "unit"][0]
    assert (f["severity"], f["role"], f["hl_account"]) == ("HIGH", "identity", OUT)


def test_retro_payouts_name_who_withdrew_to_his_address(sandbox):
    tmp, sent = sandbox
    payout = {"transactionHash": "0xpay", "blockNumber": hex(10), "logIndex": "0x0"}
    logs = [fw(OUT, S, 2e5, 10, "0xpay", index=1), fw(NEW, NEW, 9e5, 10, "0xpay", index=2)]
    cb.main([], readers=readers(head=500, payouts={S: [payout]}, tx_logs={"0xpay": logs}))
    st = _state(tmp)
    f = [f for f in st["findings"] if f["ref"] == "0xpay"]
    assert len(f) == 1 and f[0]["hl_account"] == OUT and f[0]["retro"]
    assert st["retro"][S]["bridge2"]


def test_core_ledger_sends_from_outside_accounts_count_but_hubs_do_not(sandbox):
    tmp, sent = sandbox
    (tmp / "trace" / "hl_edges").mkdir(parents=True)
    (tmp / "trace" / "registry").mkdir(parents=True)
    (tmp / "trace" / "registry" / "33.json").write_text(json.dumps({HUB: {"class": "hub"}}))
    (tmp / "trace" / "hl_edges" / "45.json").write_text(json.dumps({T: [
        {"id": "e1", "src": OUT, "dst": T, "amount_usd": 5e4, "ts": 10, "tx_hash": "0xh1"},
        {"id": "e2", "src": HUB, "dst": T, "amount_usd": 6e3, "ts": 11, "tx_hash": "0xh2"}]}))
    cb.main([], readers=readers(head=500))
    refs = {f["ref"] for f in _state(tmp)["findings"]}
    assert "0xh1" in refs and "0xh2" not in refs


def test_dry_run_writes_only_its_directory_and_sends_nothing(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    out = tmp_path_factory.mktemp("dry")
    cb.main(["--dry-run", str(out)], readers=readers(head=500, live=[fw(OUT, S, 5e5, 400, "0xd")]))
    assert (out / "latest.json").exists() and not (tmp / "boundary").exists()
    assert not sent["boundary"] and not sent["foreign"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_check_boundary.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.check_boundary'`

- [ ] **Step 3: Implement** — `scripts/check_boundary.py`:

```python
#!/usr/bin/env python3
"""Attribute value crossing Hyperliquid's edge to his world (spec §6). watch.yml.

    python scripts/check_boundary.py                # the watch.yml step
    python scripts/check_boundary.py --dry-run DIR  # read-only: writes only DIR, no alerts

Four sources, each independent of the others (a failing one costs only its own
reading): every Bridge2 withdrawal since the cursor; each core account's whole
Bridge2 withdrawal history (one call each — `user` is indexed); Unit operations
of perimeter members (rotating); and the core ledgers the trace engine already
walked. Plus retro, bounded and resumable: who ever withdrew to a member.
Single writer: data/boundary/.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import utils  # noqa: E402

FIRST_RUN_BLOCKS = 345_600      # ~1 day of Arbitrum at ~4 blocks/s
WALK_CALLS, WALK_SECONDS = 12, 60.0
UNIT_PER_RUN = 8
RETRO_MEMBERS_PER_RUN = 4
RETRO_TX_PER_MEMBER = 8
KEEP_FINDINGS = 500
RETRO_MIN_WEIGHT = 0.6


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def default_readers() -> dict:
    from src.boundary import bridge2, logs, readers, unit
    budget = readers.Budget(40, seconds=90)
    payout_topics = lambda m: {0: bridge2.TOPIC_TRANSFER, 1: bridge2.topic_address(bridge2.BRIDGE),  # noqa: E731
                               2: bridge2.topic_address(m)}
    return {
        "head": lambda: logs.head_block("arbitrum"),
        "withdrawals": lambda lo, hi: logs.read_logs("arbitrum", bridge2.BRIDGE,
                                                     bridge2.withdrawal_topics(), lo, hi),
        "user_withdrawals": lambda u: logs.read_logs("arbitrum", bridge2.BRIDGE,
                                                     bridge2.withdrawal_topics(u), 0, "latest"),
        "payouts": lambda m: logs.read_logs("arbitrum", bridge2.USDC, payout_topics(m), 0, "latest"),
        "tx_logs": lambda tx: readers.tx_logs("arbitrum", tx, budget=budget),
        "unit": lambda a: unit.read_operations(a),
    }


def hubs(data: Path) -> set:
    from src.trace import store
    try:
        registry = store.load(data / "trace" / "registry")
    except RuntimeError:
        return set()
    return {a for a, r in registry.items()
            if r.get("class") == "hub" or (r.get("hl") or {}).get("hub")}


def core_edges(data: Path, core: set) -> list[dict]:
    from src.trace import store
    try:
        stored = store.load(data / "trace" / "hl_edges")
    except RuntimeError:
        return []
    return [e for owner in sorted(core) for e in stored.get(owner, [])]


def main(argv=None, *, readers=None, now=None) -> int:
    from src import alerts
    from src.boundary import attribution as at
    from src.boundary import bridge2, logs
    from src.boundary import perimeter as pm
    from src.boundary import unit

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", metavar="DIR")
    args = parser.parse_args(argv)
    readers = readers or default_readers()
    config, data = utils.load_config(), utils.DATA_DIR
    now_iso = now or datetime.now(UTC).isoformat()
    out_dir = Path(args.dry_run) if args.dry_run else data / "boundary"
    previous = _read(data / "boundary" / "latest.json")
    doc = _read(data / "perimeter" / "latest.json")
    fallback = not doc.get("members")
    if fallback:
        doc = pm.core_only(config, now_iso)
    index = pm.Index(doc)
    errors, events = [], []

    # 1. Every Bridge2 withdrawal since the cursor.
    cursor = previous.get("withdrawal_cursor")
    walk = {"logs": [], "last_block": cursor, "error": None}
    head = start = None
    try:
        head = int(readers["head"]())
        start = int(cursor) + 1 if cursor is not None else max(0, head - FIRST_RUN_BLOCKS)
        walk = logs.walk(readers["withdrawals"], start, head, max_calls=WALK_CALLS,
                         seconds=WALK_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the head itself was unreadable
        walk["error"] = f"{type(exc).__name__}: {exc}"
    live = [r for r in (bridge2.decode_withdrawal(x) for x in walk["logs"]) if r]
    first_run = cursor is None
    events += [at.from_bridge2_withdrawal(r, retro=first_run) for r in live]

    # 2. Each core account's whole withdrawal history (exact, by nonce).
    known = dict(previous.get("core_withdrawals") or {})
    own_txs = set()
    for user in sorted(index.core):
        try:
            rows = [r for r in (bridge2.decode_withdrawal(x)
                                for x in readers["user_withdrawals"](user)) if r]
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "core_withdrawals", "address": user, "error": str(exc)[:200]})
            continue
        for r in rows:
            own_txs.add(r["tx_hash"])
            fresh = str(r["nonce"]) not in known
            known[str(r["nonce"])] = {k: r[k] for k in ("user", "destination", "usd", "ts", "tx_hash")}
            events.append(at.from_bridge2_withdrawal(r, retro=first_run or not fresh))

    # 3. Unit operations of perimeter members, least recently checked first.
    unit_checked = dict(previous.get("unit_checked") or {})
    members = sorted(index.members.values(),
                     key=lambda m: (unit_checked.get(m["address"]) or "", m["address"]))
    for m in [m for m in members if m["weight"] >= RETRO_MIN_WEIGHT][:UNIT_PER_RUN]:
        try:
            got = unit.events(readers["unit"](m["address"]))
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "unit", "address": m["address"], "error": str(exc)[:200]})
            continue
        seen_before = m["address"] in unit_checked
        unit_checked[m["address"]] = now_iso
        events += [{**e, "retro": not seen_before} for e in got]

    # 4. The core ledgers the trace engine walked (HL-native sends).
    edge_cursor = int(previous.get("hl_edges_cursor") or 0)
    edges = core_edges(data, index.core)
    for e in at.from_hl_edges(edges, index.core, exclude=hubs(data)):
        events.append({**e, "retro": not edge_cursor or int(e.get("ts") or 0) <= edge_cursor})
    new_edge_cursor = max([edge_cursor] + [int(e.get("ts") or 0) for e in edges])

    # 5. Retro: who ever withdrew to a member (bounded, resumable).
    retro = dict(previous.get("retro") or {})
    pending = [m for m in index.members.values()
               if m["weight"] >= RETRO_MIN_WEIGHT and str(m["address"]).startswith("0x")
               and m["role"] != "core" and not (retro.get(m["address"]) or {}).get("bridge2")]
    for m in sorted(pending, key=lambda m: m["address"])[:RETRO_MEMBERS_PER_RUN]:
        try:
            payouts = readers["payouts"](m["address"])
            for tx in sorted({(p.get("transactionHash") or "").lower() for p in payouts}
                             - own_txs)[:RETRO_TX_PER_MEMBER]:
                for r in (bridge2.decode_withdrawal(x) for x in readers["tx_logs"](tx)):
                    if r and r["destination"] == m["address"]:
                        events.append(at.from_bridge2_withdrawal(r, retro=True))
            retro.setdefault(m["address"], {})["bridge2"] = now_iso
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "retro", "address": m["address"], "error": str(exc)[:200]})

    # Judge, merge, alert.
    found = at.classify_all(events, index)
    by_key = {f["key"]: f for f in previous.get("findings") or []}
    new = [f for f in found if f["key"] not in by_key]
    for f in found:
        by_key[f["key"]] = f
    alerted = list(previous.get("alerted") or [])
    undelivered = []
    queue = [r for r in previous.get("undelivered") or [] if r.get("key") not in alerted]
    queue += [f for f in new if f["severity"] in at.SEVERITIES]
    for row in queue if not args.dry_run else []:
        if row["kind"] == at.KIND_HIS_ACCOUNT_PAID_OUTSIDE:
            when = (datetime.fromtimestamp(int(row["ts"]), tz=UTC).isoformat()
                    if row.get("ts") else None)
            ok = alerts.alert_foreign_destination(
                row["hl_account"], "withdraw3" if row["source"] == "bridge2" else f"{row['source']}_withdrawal",
                row["counterparty"], row.get("amount_usd"), row.get("asset") or "USDC", when,
                row.get("ref"), chain=row.get("chain"))
        else:
            ok = alerts.alert_boundary_finding(row)
        if ok:
            alerted.append(row["key"])
        else:
            undelivered.append(row)

    findings = sorted(by_key.values(), key=lambda f: -int(f.get("ts") or 0))[:KEEP_FINDINGS]
    counts = {"findings": len(findings), "new": len(new),
              "critical": sum(f["severity"] == "CRITICAL" for f in findings),
              "high": sum(f["severity"] == "HIGH" for f in findings),
              "events": len(events)}
    state = {"computed_at": now_iso, "perimeter_fallback": fallback, "head_block": head,
             "withdrawal_cursor": walk["last_block"],
             "blocks_read": (walk["last_block"] - start + 1
                             if start is not None and walk["last_block"] is not None else 0),
             "withdrawals_read": len(live), "read_error": walk.get("error"),
             "core_withdrawals": known, "hl_edges_cursor": new_edge_cursor,
             "findings": findings, "alerted": sorted(set(alerted)), "undelivered": undelivered,
             "unit_checked": unit_checked, "retro": retro, "counts": counts, "errors": errors[:50]}
    out_dir.mkdir(parents=True, exist_ok=True)
    utils.atomic_write_json(out_dir / "latest.json", state)
    print(f"[boundary] {len(live)} withdrawal(s) read to block {walk['last_block']}"
          f"{' (error: ' + walk['error'] + ')' if walk.get('error') else ''}; "
          f"{len(events)} event(s), {len(new)} new finding(s); {len(errors)} error(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`blocks_read` counts from the walk's own start (on a first run that is `FIRST_RUN_BLOCKS` back); the blind check in Task 15 reads it.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_check_boundary.py -q -p no:cacheprovider`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/check_boundary.py tests/test_check_boundary.py
git commit -m "feat(boundary): watch.yml attribution - Bridge2, core history, Unit, HL sends, retro"
```

---

### Task 10: Circle attribution — name the account that actually withdrew

**Why (found while writing this plan, 2026-10-06):** a HyperCore-native Circle withdrawal (`sendToEvmWithData`) is burned on HyperEVM by USDC's forwarder `0x6b9e7731…`, so the message's `messageSender` is the FORWARDER, not the account (verified on the target's own $6,000,000 withdrawal of 2026-09-15, mint tx `0x10ba0970…`: sender = forwarder, hook = target, recipient = target). `circle_flows.decode_sent` takes `messageSender` as the account, so the next Circle withdrawal by any cluster account to his own address would page a false CRITICAL "an outside account paid his address" — and a real outside withdrawer would be named as the forwarder. It has not fired only because his last one (09-15) predates the detector (09-17). The USDC system address `0x2000…0000` has a readable HyperCore ledger listing every account's send to it (≈ 2,700/day) with the exact amount the message carries, so the withdrawer is recovered by joining on amount; the hook account breaks ties.

**Files:**
- Modify: `src/circle_flows.py` (constants, `hook_account` on decoded rows, `system_sends`, `resolve_withdrawer`, `KIND_UNATTRIBUTED_PAID_HIM`, `classify`)
- Modify: `scripts/check_circle_flows.py` (resolve forwarder withdrawals before classifying; retry unreadable ones)
- Modify: `scripts/check_boundary.py` (retro Circle mints into core/deposit members)
- Modify: `src/alerts.py` (`alert_circle_flow` headline for the new kind, HIGH)
- Test: `tests/test_circle_withdrawer.py`, extend `tests/test_check_boundary.py`

**Interfaces:**
- Produces: `circle_flows.FORWARDER`, `circle_flows.SYSTEM_USDC`, `circle_flows.KIND_UNATTRIBUTED_PAID_HIM`; `system_sends(ledger_rows) -> [{"user","amount","ts_ms","hash"}]`; `resolve_withdrawer(row, sends, *, readable=True) -> (account|None, basis)` with basis in `{"message_sender","system_ledger","system_ledger+hook","unresolved","unreadable"}`; `apply_withdrawers(rows, sends, *, readable) -> rows` (sets `hl_account`, `hl_account_basis`); check_boundary reader `mints(member)` and `system_sends(start_ms)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_circle_withdrawer.py`:

```python
"""His own Circle withdrawal must not read as an outside account paying him."""

import json
from pathlib import Path

from src import circle_flows as cf

FIX = Path(__file__).parent / "fixtures" / "boundary"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
OUT = "0x" + "1" * 40


def _received():
    doc = json.loads((FIX / "bs_txlogs_cctp_mint.json").read_text())
    item = next(i for i in doc["items"] if i["address"]["hash"].lower() == cf.MESSAGE_TRANSMITTER_V2)
    return {"topics": item["topics"], "data": item["data"], "transactionHash": item["transaction_hash"],
            "blockNumber": hex(item["block_number"]), "logIndex": item["index"]}


def _as_sent(received: dict) -> dict:
    """Wrap the real burn body in a MessageSent envelope (header + body)."""
    h = received["data"][2:]
    off = int(h[128:192], 16) * 2
    body = h[off + 64:off + 64 + int(h[off:off + 64], 16) * 2]
    header = ("00000001" + format(19, "08x") + format(3, "08x") + "ab" * 32
              + "0" * 24 + "28b5a0e9c621a5badaa536219b3a228c8168cf5d" + "0" * 64 + "0" * 64
              + "000003e8" + "000007d0")
    message = header + body
    data = format(32, "064x") + format(len(message) // 2, "064x") + message
    data += "0" * ((64 - len(data) % 64) % 64)
    return {"topics": [cf.TOPIC_MESSAGE_SENT], "data": "0x" + data,
            "transactionHash": "0xsent", "blockNumber": "0x1", "logIndex": "0x0"}


def test_the_real_withdrawal_is_burned_by_the_forwarder_and_its_hook_names_him():
    row = cf.decode_received(_received())
    assert row["domain"] == 19 and row["message_sender"] == cf.FORWARDER
    assert row["hook_account"] == T and row["mint_recipient"] == T


def test_the_withdrawer_is_recovered_from_the_system_ledger():
    row = cf.decode_sent(_as_sent(_received()))
    assert row["message_sender"] == cf.FORWARDER and row["amount_usd"] == 6_000_000.0
    sends = [{"user": T, "amount": 6_000_000.0, "ts_ms": 1, "hash": "0xh"},
             {"user": OUT, "amount": 10_000.0, "ts_ms": 2, "hash": "0xi"}]
    assert cf.resolve_withdrawer(row, sends) == (T, "system_ledger")
    [fixed] = cf.apply_withdrawers([row], sends, readable=True)
    assert fixed["hl_account"] == T
    # His own withdrawal to his own address is no finding at all.
    assert cf.classify(fixed, {T}, set(), {T}) is None


def test_without_the_fix_it_would_have_paged_a_false_critical():
    row = cf.decode_sent(_as_sent(_received()))
    assert cf.classify(row, {T}, set(), {T}) == cf.KIND_OUTSIDE_PAID_HIM   # the latent bug


def test_ties_break_on_the_hook_and_otherwise_stay_unresolved():
    row = {**cf.decode_sent(_as_sent(_received()))}
    two = [{"user": T, "amount": 6_000_000.0, "ts_ms": 1, "hash": "a"},
           {"user": OUT, "amount": 6_000_000.0, "ts_ms": 2, "hash": "b"}]
    assert cf.resolve_withdrawer(row, two) == (T, "system_ledger+hook")
    assert cf.resolve_withdrawer({**row, "hook_account": None}, two) == (None, "unresolved")
    assert cf.resolve_withdrawer(row, [], readable=False) == (None, "unreadable")


def test_an_unattributed_withdrawal_to_his_address_is_its_own_kind():
    row = {**cf.decode_sent(_as_sent(_received())), "hl_account": None,
           "hl_account_basis": "unresolved"}
    assert cf.classify(row, {T}, set(), {T}) == cf.KIND_UNATTRIBUTED_PAID_HIM
    assert cf.classify({**row, "hl_account_basis": "unreadable"}, {T}, set(), {T}) is None


def test_a_hyperevm_native_burn_keeps_its_own_sender():
    row = {"direction": "out", "message_sender": OUT, "hl_account": OUT, "amount_usd": 5.0,
           "hook_account": None}
    assert cf.resolve_withdrawer(row, []) == (OUT, "message_sender")


def test_system_sends_reads_only_sends_into_the_usdc_system_address():
    ledger = [{"time": 5, "hash": "0x1", "delta": {"type": "send", "user": OUT,
                                                   "destination": cf.SYSTEM_USDC, "amount": "12.5"}},
              {"time": 6, "hash": "0x2", "delta": {"type": "spotTransfer", "user": cf.SYSTEM_USDC,
                                                   "destination": OUT, "amount": "7"}}]
    assert cf.system_sends(ledger) == [{"user": OUT, "amount": 12.5, "ts_ms": 5, "hash": "0x1"}]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_circle_withdrawer.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'src.circle_flows' has no attribute 'FORWARDER'`

- [ ] **Step 3: Implement in `src/circle_flows.py`**

Add below `KIND_HIS_ACCOUNT_WITHDREW_OUTSIDE`:

```python
KIND_UNATTRIBUTED_PAID_HIM = "unattributed_withdrawal_paid_his_address"

# USDC's linked contract on HyperEVM. A HyperCore-native Circle withdrawal
# (`sendToEvmWithData`) is burned BY it, so its message names it as sender and
# the account only in the hook. Measured on his own 2026-09-15 withdrawal.
FORWARDER = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"
# HyperCore's system address for USDC: every account's send into it (Circle
# withdrawals and moves to HyperEVM) is on its own ledger, with the amount the
# Circle message carries.
SYSTEM_USDC = "0x2000000000000000000000000000000000000000"
AMOUNT_MATCH_USD = 0.005
```

In `decode_received` and `decode_sent`, add to the returned dict:

```python
        "hook_account": _hook_account(body["hook"]),
```

(in `decode_sent`, import `_hook_account` alongside `CCTP_DOMAINS` from `src.chain.bridges`). Then add after `decode`:

```python
def system_sends(ledger_rows) -> list[dict]:
    """Every account's send into the USDC system address, from its ledger. Pure."""
    out = []
    for row in ledger_rows or []:
        d = (row or {}).get("delta") or {}
        if d.get("type") != "send" or (d.get("destination") or "").lower() != SYSTEM_USDC:
            continue
        user = (d.get("user") or "").lower()
        try:
            amount = float(d.get("amount"))
        except (TypeError, ValueError):
            continue
        if user and user != SYSTEM_USDC:
            out.append({"user": user, "amount": amount, "ts_ms": int(row.get("time") or 0),
                        "hash": row.get("hash")})
    return out


def resolve_withdrawer(row: dict, sends, *, readable: bool = True) -> tuple:
    """Who withdrew: the message sender, unless the forwarder burned it."""
    if row.get("direction") != "out" or (row.get("message_sender") or "").lower() != FORWARDER:
        return row.get("hl_account"), "message_sender"
    if not readable:
        return None, "unreadable"
    users = {s["user"] for s in sends or []
             if abs(float(s["amount"]) - float(row.get("amount_usd") or -1)) <= AMOUNT_MATCH_USD}
    if len(users) == 1:
        return next(iter(users)), "system_ledger"
    hook = (row.get("hook_account") or "").lower()
    if hook and hook in users:
        return hook, "system_ledger+hook"
    return None, "unresolved"


def apply_withdrawers(rows, sends, *, readable: bool) -> list[dict]:
    out = []
    for row in rows or []:
        account, basis = resolve_withdrawer(row, sends, readable=readable)
        out.append({**row, "hl_account": account, "hl_account_basis": basis})
    return out
```

Replace `classify` with:

```python
def classify(row: dict, his_evm: set, his_raw: set, cluster_accounts: set) -> str | None:
    """Which tripwire, if any, a decoded flow trips. Pure.

    A forwarder-burned withdrawal must have had its account resolved first
    (`apply_withdrawers`): its `messageSender` is the forwarder, never the
    account. One resolved to nobody that paid his address is its own kind;
    one that could not be read is never judged (it is retried).
    """
    theirs_is_his = ((row.get("counterparty") or "").lower() in his_evm
                     or (row.get("counterparty_raw") or "").lower() in his_raw)
    account = (row.get("hl_account") or "").lower()
    if not account:
        if (row.get("direction") == "out" and theirs_is_his
                and row.get("hl_account_basis") == "unresolved"):
            return KIND_UNATTRIBUTED_PAID_HIM
        return None
    account_is_his = account in cluster_accounts
    if row.get("direction") == "in" and theirs_is_his and not account_is_his:
        return KIND_FUNDED_OUTSIDE
    if row.get("direction") == "out" and theirs_is_his and not account_is_his:
        return KIND_OUTSIDE_PAID_HIM
    if row.get("direction") == "out" and account_is_his and not theirs_is_his \
            and (row.get("counterparty") or row.get("counterparty_raw")):
        return KIND_HIS_ACCOUNT_WITHDREW_OUTSIDE
    return None
```

`decode_sent` keeps setting `"hl_account": body["message_sender"]` and `counterparty` = the mint recipient; `apply_withdrawers` overwrites `hl_account` for forwarder-burned rows before `classify` runs.

- [ ] **Step 4: Wire into `scripts/check_circle_flows.py`** — after `rows, summary = …` and before the DiscoveryStore ingest, insert:

```python
    # A forwarder-burned withdrawal names the forwarder, not the account
    # (src/circle_flows.py). Resolve it from the USDC system address's ledger;
    # if that ledger cannot be read, hold the row for the next run rather than
    # judging it (rule 5).
    held = [r for r in previous.get("pending_resolution") or []
            if int(r.get("held_runs") or 0) < 12]
    forwarded = [r for r in rows if r.get("direction") == "out"
                 and (r.get("message_sender") or "").lower() == cf.FORWARDER] + held
    pending = []
    if forwarded:
        sends, readable = system_sends_since(hours=36)
        resolved = cf.apply_withdrawers(forwarded, sends, readable=readable)
        pending = [{**r, "held_runs": int(r.get("held_runs") or 0) + 1}
                   for r in resolved if r["hl_account_basis"] == "unreadable"]
        done = {cf.finding_key(r) for r in forwarded}
        rows = [r for r in rows if cf.finding_key(r) not in done] + [
            r for r in resolved if r["hl_account_basis"] != "unreadable"]
```

and add the helper near `his_identities`:

```python
def system_sends_since(hours: float, *, post=None, pages: int = 8) -> tuple[list, bool]:
    """Sends into the USDC system address over the last `hours`. (rows, readable)."""
    from src.cctp_feed import strict_post
    post = post or (lambda body: strict_post(body, timeout=30, retries=2))
    cursor = int((time.time() - hours * 3600) * 1000)
    ledger = []
    try:
        for _ in range(pages):
            page = post({"type": "userNonFundingLedgerUpdates", "user": cf.SYSTEM_USDC,
                         "startTime": cursor})
            if not isinstance(page, list):
                return [], False
            ledger += page
            if len(page) < 2000:
                break
            cursor = int(page[-1].get("time") or cursor) + 1
    except Exception:  # noqa: BLE001 - unreadable, held for the next run
        return [], False
    return cf.system_sends(ledger), True
```

and add `"pending_resolution": pending,` to the saved `report`.

In `src/alerts.py` `alert_circle_flow`, add the headline
`cf.KIND_UNATTRIBUTED_PAID_HIM: "A Hyperliquid Withdrawal Nobody Can Attribute Paid One Of His Addresses"` and make the level `HIGH` for that kind (`level = "HIGH" if kind == cf.KIND_UNATTRIBUTED_PAID_HIM else "CRITICAL"`; subject `f"[EZEKIEL] {level}: {headline}"`).

- [ ] **Step 5: Retro Circle mints in `scripts/check_boundary.py`**

Add two readers to `default_readers()`:

```python
        "mints": lambda m: logs.read_logs("arbitrum", bridge2.USDC,
                                          {0: bridge2.TOPIC_TRANSFER, 1: bridge2.topic_address("0x" + "0" * 40),
                                           2: bridge2.topic_address(m)}, 0, "latest"),
        "system_sends": lambda start_ms: _system_sends_from(start_ms),
```

with

```python
def _system_sends_from(start_ms: int) -> list:
    from scripts.check_circle_flows import system_sends_since
    import time as _t
    rows, readable = system_sends_since(hours=max(1.0, (_t.time() * 1000 - start_ms) / 3.6e6) + 1)
    if not readable:
        raise RuntimeError("USDC system ledger unreadable")
    return rows
```

and, in step 5 of `main` (retro), for members whose role is `core` or `deposit` and whose `retro[...]["circle"]` is unset, after the Bridge2 payouts:

```python
            if m["role"] in ("core", "deposit") and not (retro.get(m["address"]) or {}).get("circle"):
                own = {(r.get("payout_tx") or "").lower()
                       for r in (_read(data / "withdrawals" / "latest.json").get("cctp") or {}).get("landed") or []}
                for mint in [x for x in readers["mints"](m["address"])
                             if (x.get("transactionHash") or "").lower() not in own][:RETRO_TX_PER_MEMBER]:
                    for log in readers["tx_logs"](mint["transactionHash"]):
                        if (log.get("address") or "").lower() != cf.MESSAGE_TRANSMITTER_V2:
                            continue
                        msg = cf.decode_received(log)
                        if not msg or msg["domain"] != 19:
                            continue
                        msg["direction"] = "out"
                        start = (to_int(mint.get("timeStamp")) - 7200) * 1000 if mint.get("timeStamp") else 0
                        account, _basis = cf.resolve_withdrawer(msg, readers["system_sends"](start))
                        if account and account not in index.core:
                            events.append(at.event(source="circle", direction="out", hl_account=account,
                                                   counterparty=m["address"], chain="arbitrum",
                                                   amount_usd=msg["amount_usd"], ts=to_int(mint.get("timeStamp")),
                                                   ref=mint["transactionHash"], retro=True))
                retro.setdefault(m["address"], {})["circle"] = now_iso
```

(import `from src import circle_flows as cf` and `from src.boundary.logs import to_int` at the top of `main`; the member loop now also includes `core` members for Circle — change the `pending` filter to `m["role"] != "core" or not (retro.get(...) or {}).get("circle")` and guard the Bridge2 block with `if m["role"] != "core"`).

Add to `tests/test_check_boundary.py`:

```python
def test_retro_circle_mints_name_an_outside_withdrawer(sandbox):
    tmp, sent = sandbox
    import json as _json
    from pathlib import Path
    fx = _json.loads((Path(__file__).parent / "fixtures" / "boundary" / "bs_txlogs_cctp_mint.json").read_text())
    logs = [{"address": i["address"]["hash"].lower(), "topics": i["topics"], "data": i["data"],
             "logIndex": i["index"], "transactionHash": i["transaction_hash"],
             "blockNumber": i["block_number"]} for i in fx["items"]]
    mint = {"transactionHash": fx["_tx"], "timeStamp": hex(1_790_000_000), "logIndex": "0x0"}
    r = readers(head=500, tx_logs={fx["_tx"]: logs})
    r["mints"] = lambda m: [mint] if m == T else []
    r["system_sends"] = lambda start: [{"user": OUT, "amount": 6_000_000.0, "ts_ms": 1, "hash": "x"}]
    cb.main([], readers=r)
    f = [f for f in _state(tmp)["findings"] if f["source"] == "circle"]
    assert f and f[0]["hl_account"] == OUT and f[0]["counterparty"] == T and f[0]["retro"]
```

(and give the `readers()` helper default `"mints": lambda m: []` and `"system_sends": lambda s: []`).

- [ ] **Step 6: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_circle_withdrawer.py tests/test_check_boundary.py tests/test_circle_flows.py -q -p no:cacheprovider`
Expected: PASS (existing circle tests still pass — `classify` keeps its four-argument signature)

- [ ] **Step 7: Commit**

```bash
git add src/circle_flows.py scripts/check_circle_flows.py scripts/check_boundary.py src/alerts.py tests/test_circle_withdrawer.py tests/test_check_boundary.py tests/fixtures/boundary/bs_txlogs_cctp_mint.json
git commit -m "fix(circle): name the account behind forwarder-burned withdrawals; retro Circle mints"
```

---

### Task 11: Provenance resolver (`src/boundary/provenance.py`)

**Files:**
- Create: `src/boundary/provenance.py`
- Fixtures: `hl_ledger_dd53.json` (30 Bridge2 deposits), `hl_ledger_unit_btc.json` (two UBTC credits from Unit's operator), `hl_ledger_d475.json` (HL sends + Circle), plus Task 3/6 fixtures
- Test: `tests/test_boundary_provenance.py`

**Interfaces:**
- Consumes: `perimeter.Index/STRONG_ROLES/SYSTEM_PREFIXES/low`, `readers.ReadError`, `readers._transfer`, `circle_flows.FORWARDER`, `attribution.DUST_USD`.
- Produces: route constants `ROUTE_BRIDGE2|CIRCLE|UNIT|HYPEREVM|HL_SEND`; `EXCHANGE_CLASSES=("exchange","busy")`; `route_entries(ledger, account, *, unit_events=()) -> [entry]` (entry = `{"route","usd","ts","ref","source","source_chain",["via","unit_ref"]}`); `significant(entries, *, min_usd=10_000, limit=6)`; `aggregate(transfers) -> [source]`; `classify_source(address, chain, *, index, label_of, is_contract=False) -> {"class", ["role","weight","member_why","family"]}`; `resolve(account, *, ledger, unit_events, index, label_of, read_inbound, read_first_gas, read_ledger, circle_source, now_ts) -> record` (record = `{"account","resolved_at","entries","sources","unreadable","complete","exchange_sources","verdict"}`; source rows carry `hop`, `entry`, `route`, `class`, `address`, `chain`, `usd`, `first_ts`, `last_ts`, [`via`, `kind`]); `findings(record) -> [row]` (row = `{"kind":"provenance_touches_his_world","account","severity","vote","hop","role","member","member_why","via","route","usd","ts","entry_ref","key"}`); `shared_funders(records: dict) -> [{"funder","accounts"}]`; `verdict(record) -> "touches_his_world"|"same_exchange"|"exchange"|"unresolved"|"unrelated"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_provenance.py`:

```python
"""Spec §7: an account's money traced back to its first custody boundary."""

import json
from pathlib import Path

from src.boundary import perimeter, provenance, readers, unit

FIX = Path(__file__).parent / "fixtures" / "boundary"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
DD53 = "0xdd53c5297309130ab5fe5623dc905752e3342b13"
EE7 = "0xee7ae85f2fe2239e27d9c1e23fffe168d63b4055"
D7A = "0xd7a827fbaf38c98e8336c5658e4bcbcd20a4fd2d"
QUIET, NEW = "0x" + "4" * 40, "0x" + "5" * 40

INDEX = perimeter.Index(perimeter.build(
    config={"target_wallet": T, "known_self_wallets": []}, sentinels={S: {"reason": "x"}},
    trace_report={}, trace_registry={}, solana={}, associates_found={},
    families={f"deposit:{S}": [EE7]}, services=set(), previous=None, now_iso="2026-10-06"))


def _fx(name):
    return json.loads((FIX / name).read_text())


def label_of(address, chain):
    return {D7A: "busy", QUIET: "quiet"}.get(address)


def inbound_from_fixture(chain, address, *, since_ts, until_ts):
    return [t for t in (readers._transfer(i, chain) for i in _fx("bs_inbound_dd53.json")["items"])
            if since_ts <= t["ts"] <= until_ts]


def no_gas(chain, address):
    return None


def boom(*a, **k):
    raise AssertionError("not expected")


def test_bridge2_deposits_are_routed_and_the_biggest_kept():
    entries = provenance.route_entries(_fx("hl_ledger_dd53.json"), DD53)
    assert entries and {e["route"] for e in entries} == {provenance.ROUTE_BRIDGE2}
    top = provenance.significant(entries)
    assert len(top) == 6 and top[0]["usd"] >= top[-1]["usd"] >= 10_000
    assert all(e["source"] == DD53 and e["source_chain"] == "arbitrum" for e in top)


def test_unit_credits_are_routed_to_their_bitcoin_source():
    acct = "0x458d583dbfe5b0a143bf09f45acf29f0022aab3b"
    evs = unit.events(_fx("unit_ops_btc_deposit.json"))
    entries = provenance.route_entries(_fx("hl_ledger_unit_btc.json"), acct, unit_events=evs)
    assert entries and all(e["route"] == provenance.ROUTE_UNIT for e in entries)
    assert entries[0]["source"].startswith("bc1p") and entries[0]["source_chain"] == "bitcoin"


def test_hl_sends_and_circle_are_told_apart():
    acct = "0xd47587702a91731dc1089b5db0932cf820151a91"
    routes = {e["route"] for e in provenance.route_entries(_fx("hl_ledger_d475.json"), acct)}
    assert provenance.ROUTE_HL_SEND in routes


def test_an_account_funded_by_his_exchange_is_same_exchange_evidence_not_an_alert():
    rec = provenance.resolve(DD53, ledger=_fx("hl_ledger_dd53.json"), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=inbound_from_fixture,
                             read_first_gas=no_gas, read_ledger=boom, circle_source=boom,
                             now_ts=1_791_000_000)
    classes = {s["address"]: s["class"] for s in rec["sources"]}
    assert classes.get(EE7) == "exchange" and classes.get(D7A) == "busy"
    assert rec["verdict"] == "same_exchange" and provenance.findings(rec) == []


def _ledger_deposit(usd, ts_ms=1_790_000_000_000):
    return [{"time": ts_ms, "hash": "0xd", "delta": {"type": "deposit", "usdc": str(usd)}}]


def _inbound(table):
    def read(chain, address, *, since_ts, until_ts):
        return [{"from": a, "usd": u, "ts": until_ts - 60, "chain": chain, "from_is_contract": False,
                 "from_is_scam": False, "token": "0xusdc", "symbol": "USDC", "tx_hash": "0x1"}
                for a, u in table.get(address, [])]
    return read


def test_his_wallet_at_hop_one_is_critical_transfer():
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=_inbound({NEW: [(T, 2e6)]}),
                             read_first_gas=no_gas, read_ledger=boom, circle_source=boom,
                             now_ts=1_791_000_000)
    [f] = provenance.findings(rec)
    assert (f["severity"], f["vote"], f["hop"], f["member"]) == ("CRITICAL", "transfer", 1, T)
    assert rec["verdict"] == "touches_his_world"


def test_his_wallet_at_hop_two_is_high_evidence_only():
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of,
                             read_inbound=_inbound({NEW: [(QUIET, 2e6)], QUIET: [(T, 2e6)]}),
                             read_first_gas=no_gas, read_ledger=boom, circle_source=boom,
                             now_ts=1_791_000_000)
    [f] = provenance.findings(rec)
    assert (f["severity"], f["vote"], f["hop"], f["via"]) == ("HIGH", None, 2, QUIET)


def test_an_unreadable_hop_is_recorded_and_never_read_as_no_source():
    def down(chain, address, *, since_ts, until_ts):
        raise readers.ReadError("429")
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=down, read_first_gas=no_gas,
                             read_ledger=boom, circle_source=boom, now_ts=1_791_000_000)
    assert not rec["complete"] and rec["unreadable"] and rec["verdict"] == "unresolved"


def test_an_unresolved_circle_source_is_unreadable_too():
    ledger = [{"time": 1_790_000_000_000, "hash": "0xc", "delta": {
        "type": "send", "user": provenance.FORWARDER, "destination": NEW, "token": "USDC",
        "amount": "500000.0", "usdcValue": "500000.0"}}]
    rec = provenance.resolve(NEW, ledger=ledger, unit_events=[], index=INDEX, label_of=label_of,
                             read_inbound=boom, read_first_gas=no_gas, read_ledger=boom,
                             circle_source=lambda entry, account: None, now_ts=1_791_000_000)
    assert rec["entries"][0]["route"] == provenance.ROUTE_CIRCLE and not rec["complete"]


def test_dust_and_scam_senders_never_become_sources():
    rows = provenance.aggregate([
        {"from": "0xa", "usd": 0.00001, "ts": 1, "chain": "arbitrum", "from_is_scam": False},
        {"from": "0xb", "usd": 5e5, "ts": 2, "chain": "arbitrum", "from_is_scam": True},
        {"from": "0xc", "usd": 2e5, "ts": 3, "chain": "arbitrum", "from_is_scam": False},
        {"from": "0xc", "usd": None, "ts": 4, "chain": "arbitrum", "from_is_scam": False}])
    assert [(r["address"], r["usd"], r["unvalued"]) for r in rows] == [("0xc", 2e5, 1)]


def test_quiet_funders_shared_by_two_accounts_are_grouped():
    recs = {"0x1": {"sources": [{"hop": 1, "class": "quiet", "address": QUIET}]},
            "0x2": {"sources": [{"hop": 1, "class": "quiet", "address": QUIET}]},
            "0x3": {"sources": [{"hop": 1, "class": "busy", "address": D7A}]}}
    assert provenance.shared_funders(recs) == [{"funder": QUIET, "accounts": ["0x1", "0x2"]}]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_provenance.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'provenance'`

- [ ] **Step 3: Implement** — `src/boundary/provenance.py`:

```python
# src/boundary/provenance.py
"""Where did this account's money come from? Pure; every read is injected.

Spec §7. An HL account's ledger names how value entered it (Bridge2, Circle,
Unit, HyperEVM, or a send from another account); each entry is followed back
one hop to whoever funded it, and a quiet or unmeasured funder of real size one
hop further, classifying every source against the perimeter. Measured
2026-10-06 on the leads: 0x5b5d5120 and 0xb83de012 share their exchange hot
wallets with the target and one quiet funder with each other; 0xdd53c529 was
funded from the Binance hot wallet his own deposit address forwards into.
A source that could not be read is UNREADABLE, never "no source" (rule 5).
"""

from __future__ import annotations

from src.boundary.perimeter import STRONG_ROLES, SYSTEM_PREFIXES, low
from src.boundary.readers import HOSTS, ReadError

FORWARDER = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"
ROUTE_BRIDGE2, ROUTE_CIRCLE, ROUTE_UNIT = "bridge2", "circle", "unit"
ROUTE_HYPEREVM, ROUTE_HL_SEND = "hyperevm", "hl_send"
EXCHANGE_CLASSES = ("exchange", "busy")
MOVES = ("send", "spotTransfer", "internalTransfer", "subAccountTransfer", "vaultWithdraw")
MIN_ENTRY_USD = 10_000.0
MAX_ENTRIES = 6
MAX_SOURCES = 6
LOOKBACK_S = 30 * 86400
HOP2_MIN_SHARE, HOP2_MIN_USD = 0.2, 50_000.0
UNIT_MATCH_S = 3600
DUST_USD = 100.0
HIGH_WEIGHT = 0.6


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _usd(delta: dict, token: str) -> float | None:
    """Same rule as trace.hl_ledger: usdcValue, never a token quantity (rule 11)."""
    amount = _num(delta.get("amount")) if delta.get("amount") is not None else _num(delta.get("usdc"))
    value = _num(delta.get("usdcValue"))
    if value is not None:
        if value == 0 and (amount or 0) > 0 and token != "USDC":
            return None
        return abs(value)
    if token == "USDC" and amount is not None:
        return abs(amount)
    return None


def _entry(route, usd, ts, ref, source, chain, **extra) -> dict:
    return {"route": route, "usd": usd, "ts": ts, "ref": ref, "source": source,
            "source_chain": chain, **extra}


def _unit_match(token: str, ts: int, unit_in: list) -> dict | None:
    t = (token or "").upper()
    if len(t) < 2 or not t.startswith("U"):
        return None
    best = None
    for e in unit_in:
        if (e.get("asset") or "") != t[1:].lower() or not e.get("ts"):
            continue
        gap = abs(int(e["ts"]) - ts)
        if gap <= UNIT_MATCH_S and (best is None or gap < best[0]):
            best = (gap, e)
    return best[1] if best else None


def route_entries(ledger, account, *, unit_events=()) -> list[dict]:
    account = low(account)
    unit_in = [e for e in unit_events or [] if e.get("direction") == "in"
               and low(e.get("hl_account")) == account]
    out = []
    for row in ledger or []:
        delta = (row or {}).get("delta") or {}
        kind = delta.get("type")
        try:
            ts = int(row.get("time") or 0) // 1000
        except (TypeError, ValueError):
            continue
        ref = row.get("hash")
        if kind == "deposit":
            out.append(_entry(ROUTE_BRIDGE2, _num(delta.get("usdc")), ts, ref, account, "arbitrum"))
            continue
        if kind not in MOVES:
            continue
        if kind == "vaultWithdraw":
            src, dst, token = low(delta.get("vault")), low(delta.get("user")) or account, "USDC"
            usd = _num(delta.get("netWithdrawnUsd"))
            usd = usd if usd is not None else _num(delta.get("requestedUsd"))
        else:
            src, dst = low(delta.get("user")), low(delta.get("destination"))
            token = str(delta.get("token") or "USDC")
            usd = _usd(delta, token)
        if dst != account or not src or src == account:
            continue
        if src == FORWARDER:
            out.append(_entry(ROUTE_CIRCLE, usd, ts, ref, None, None))
        elif src.startswith(SYSTEM_PREFIXES):
            out.append(_entry(ROUTE_HYPEREVM, usd, ts, ref, account, "hyperevm"))
        else:
            op = _unit_match(token, ts, unit_in)
            if op:
                out.append(_entry(ROUTE_UNIT, usd, ts, ref, op["counterparty"], op["chain"],
                                  unit_ref=op.get("ref"), via=src))
            else:
                out.append(_entry(ROUTE_HL_SEND, usd, ts, ref, src, "hyperliquid"))
    return sorted(out, key=lambda e: e["ts"])


def significant(entries, *, min_usd: float = MIN_ENTRY_USD, limit: int = MAX_ENTRIES) -> list:
    keep = [e for e in entries or [] if (e["usd"] is not None and e["usd"] >= min_usd)
            or (e["usd"] is None and e["route"] == ROUTE_UNIT)]
    return sorted(keep, key=lambda e: -(e["usd"] or 0))[:limit]


def aggregate(transfers) -> list[dict]:
    """Inbound transfers grouped by sender. Dust (valued < $100) is the shape of
    address poisoning and a sender Blockscout flags as a scam is not a funder."""
    by: dict[str, dict] = {}
    for t in transfers or []:
        usd = t.get("usd")
        if t.get("from_is_scam") or not t.get("from") or (usd is not None and usd < DUST_USD):
            continue
        a = by.setdefault(t["from"], {"address": t["from"], "chain": t.get("chain"), "usd": 0.0,
                                      "unvalued": 0, "count": 0, "first_ts": t["ts"],
                                      "last_ts": t["ts"],
                                      "is_contract": bool(t.get("from_is_contract"))})
        a["count"] += 1
        if usd is None:
            a["unvalued"] += 1
        else:
            a["usd"] += float(usd)
        a["first_ts"], a["last_ts"] = min(a["first_ts"], t["ts"]), max(a["last_ts"], t["ts"])
    return sorted(by.values(), key=lambda a: -a["usd"])


def classify_source(address, chain, *, index, label_of, is_contract: bool = False) -> dict:
    member = index.get(address)
    if member:
        return {"class": "perimeter", "role": member["role"], "weight": member["weight"],
                "member_why": member.get("why")}
    out: dict = {}
    label = label_of(address, chain)
    family = index.families.get(low(address))
    if family:
        out["family"], label = family, "exchange"
    if label is None and is_contract:
        label = "contract"
    out["class"] = label or "unmeasured"
    return out


def _single(address, chain, usd, ts, kind) -> list[dict]:
    return [{"address": low(address), "chain": chain, "usd": usd, "count": 1, "unvalued": int(usd is None),
             "first_ts": ts, "last_ts": ts, "kind": kind}]


def _hop1(entry, account, read_inbound, read_first_gas, circle_source):
    route, ts = entry["route"], entry["ts"]
    if route == ROUTE_BRIDGE2:
        rows = aggregate(read_inbound("arbitrum", account, since_ts=ts - LOOKBACK_S, until_ts=ts))
        gas = read_first_gas("arbitrum", account)
        if gas:
            hit = next((r for r in rows if r["address"] == gas["from"]), None)
            if hit:
                hit["kind"] = "first_gas"
            else:
                rows += _single(gas["from"], "arbitrum", None, gas["ts"], "first_gas")
        return rows
    if route == ROUTE_HYPEREVM:
        return aggregate(read_inbound("hyperevm", account, since_ts=ts - LOOKBACK_S, until_ts=ts))
    if route == ROUTE_CIRCLE:
        src = circle_source(entry, account)
        return _single(src["address"], src["chain"], entry["usd"], ts, "circle_sender") if src else None
    if route == ROUTE_UNIT:
        return _single(entry["source"], entry["source_chain"], entry["usd"], ts, "unit_source")
    return _single(entry["source"], "hyperliquid", entry["usd"], ts, "hl_sender")


def _hop2(source, read_inbound, read_ledger) -> list[dict]:
    if source["chain"] == "hyperliquid":
        rows = []
        for e in significant(route_entries(read_ledger(source["address"]), source["address"])):
            if e["route"] in (ROUTE_HL_SEND, ROUTE_UNIT) and e["source"]:
                rows += _single(e["source"], e["source_chain"], e["usd"], e["ts"],
                                "hl_sender" if e["route"] == ROUTE_HL_SEND else "unit_source")
        return [r for r in rows if r["address"] != source["address"]]
    if source["chain"] not in HOSTS:
        raise ReadError(f"no keyless reader for {source['chain']}")
    return aggregate(read_inbound(source["chain"], source["address"],
                                  since_ts=(source.get("first_ts") or 0) - LOOKBACK_S,
                                  until_ts=source.get("last_ts") or source.get("first_ts") or 0))


def resolve(account, *, ledger, unit_events, index, label_of, read_inbound, read_first_gas,
            read_ledger, circle_source, now_ts) -> dict:
    account = low(account)
    record = {"account": account, "resolved_at": now_ts, "entries": [], "sources": [],
              "unreadable": [], "complete": True}
    record["entries"] = significant(route_entries(ledger, account, unit_events=unit_events))
    for entry in record["entries"]:
        try:
            hop1 = _hop1(entry, account, read_inbound, read_first_gas, circle_source)
        except ReadError as exc:
            hop1, why = None, str(exc)[:160]
        else:
            why = "source not resolved"
        if hop1 is None:
            record["unreadable"].append({"hop": 1, "entry": entry["ref"], "error": why})
            record["complete"] = False
            continue
        total = sum(s["usd"] for s in hop1 if s.get("usd"))
        for s in hop1[:MAX_SOURCES]:
            info = classify_source(s["address"], s["chain"], index=index, label_of=label_of,
                                   is_contract=s.get("is_contract", False))
            record["sources"].append({**s, **info, "hop": 1, "entry": entry["ref"],
                                      "route": entry["route"]})
            big = (s.get("usd") or 0) >= HOP2_MIN_USD and (not total or s["usd"] / total >= HOP2_MIN_SHARE)
            if info["class"] not in ("quiet", "unmeasured") or not big:
                continue
            try:
                hop2 = _hop2(s, read_inbound, read_ledger)
            except ReadError as exc:
                record["unreadable"].append({"hop": 2, "via": s["address"], "error": str(exc)[:160]})
                continue
            for t in hop2[:MAX_SOURCES]:
                info2 = classify_source(t["address"], t["chain"], index=index, label_of=label_of,
                                        is_contract=t.get("is_contract", False))
                record["sources"].append({**t, **info2, "hop": 2, "via": s["address"],
                                          "entry": entry["ref"], "route": entry["route"]})
    record["exchange_sources"] = sorted({s["address"] for s in record["sources"]
                                         if s["class"] in EXCHANGE_CLASSES})
    record["verdict"] = verdict(record)
    return record


def verdict(record: dict) -> str:
    sources = record.get("sources") or []
    if any(s["class"] == "perimeter" and s.get("weight", 0) >= HIGH_WEIGHT for s in sources):
        return "touches_his_world"
    if any(s.get("family") for s in sources):
        return "same_exchange"
    if not record.get("complete"):
        return "unresolved"
    if any(s["class"] in EXCHANGE_CLASSES for s in sources if s.get("hop") == 1):
        return "exchange"
    return "unrelated"


def findings(record: dict) -> list[dict]:
    entries = {e["ref"]: e for e in record.get("entries") or []}
    out, seen = [], set()
    for s in record.get("sources") or []:
        if s.get("class") != "perimeter":
            continue
        role, weight, hop = s["role"], s.get("weight", 0), s["hop"]
        if hop == 1 and role in STRONG_ROLES:
            severity, vote = "CRITICAL", "transfer"
        elif weight >= HIGH_WEIGHT:
            severity, vote = "HIGH", None
        else:
            severity, vote = None, None
        e = entries.get(s.get("entry")) or {}
        key = f"provenance:{record['account']}:{s['address']}:{s.get('entry')}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": "provenance_touches_his_world", "account": record["account"],
                    "severity": severity, "vote": vote, "hop": hop, "role": role,
                    "member": s["address"], "member_why": s.get("member_why"), "via": s.get("via"),
                    "route": s.get("route"), "usd": e.get("usd"), "ts": e.get("ts"),
                    "entry_ref": s.get("entry"), "key": key})
    return out


def shared_funders(records: dict) -> list[dict]:
    by: dict[str, set] = {}
    for account, rec in (records or {}).items():
        for s in (rec or {}).get("sources") or []:
            if s.get("hop") == 1 and s.get("class") == "quiet":
                by.setdefault(s["address"], set()).add(account)
    return [{"funder": f, "accounts": sorted(a)} for f, a in sorted(by.items()) if len(a) >= 2]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_provenance.py -q -p no:cacheprovider`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/boundary/provenance.py tests/test_boundary_provenance.py tests/fixtures/boundary/hl_ledger_*.json
git commit -m "feat(boundary): provenance - trace an account's money back to its first boundary"
```

---

### Task 12: Provenance step (`scripts/run_provenance.py`, trace.yml)

**Files:**
- Create: `scripts/run_provenance.py`
- Modify: `scripts/build_perimeter.py` — `measured()` returns `(contract, busy, known)` where `known(a)` is True when any activity reading exists
- Test: `tests/test_run_provenance.py`

**Interfaces:**
- Consumes: Tasks 1–11; `src.trace.store.load/save`; `cctp_feed.strict_post`; `hl_budget.ReadBudget`; `trace.hl_ledger.read`.
- Produces: `data/provenance/bridge_deposits.json` = `{"cursor","updated_at","deposits":[{"wallet","amount","ts","hash","via":"bridge"}]}` (30 days, ≥ $10K); `data/provenance/accounts/<xx>.json` (record cache, ≤ 2,000 accounts); `data/provenance/latest.json` = `{"computed_at","deposit_cursor","blocks_read","deposits_read","read_error","queue","attempted","resolved","unreadable_accounts","findings","member_findings","alerted","undelivered","shared_funders","recent","counts"}`; `main(argv=None, *, readers=None, now=None) -> int`; `make_label_of(config, data) -> callable`.

- [ ] **Step 1: Write the failing tests** — `tests/test_run_provenance.py`:

```python
"""The trace.yml provenance step, every reader injected."""

import json

import pytest

import scripts.run_provenance as rp
from src import utils
from src.boundary import bridge2

T = "0x45d26f28196d226497130c4bac709d808fed4029"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SINK = "0x" + "a" * 40
NEW, OTHER = "0x" + "5" * 40, "0x" + "6" * 40


def dep(depositor, usd, block, tx):
    return {"address": bridge2.USDC, "topics": [bridge2.TOPIC_TRANSFER, bridge2.topic_address(depositor),
                                                 bridge2.topic_address(bridge2.BRIDGE)],
            "data": "0x" + format(int(usd * 1e6), "064x"), "blockNumber": hex(block),
            "timeStamp": hex(1_790_000_000 + block), "logIndex": "0x0", "transactionHash": tx}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "load_config", lambda: {"target_wallet": T, "known_self_wallets": []})
    (tmp_path / "perimeter").mkdir()
    (tmp_path / "perimeter" / "latest.json").write_text(json.dumps({"members": {
        T: {"address": T, "role": "core", "weight": 1.0, "why": "config"},
        SINK: {"address": SINK, "role": "sink", "weight": 0.6, "why": "holds his money"},
        S: {"address": S, "role": "deposit", "weight": 1.0, "why": "deposit"}}}))
    sent = {"prov": [], "boundary": []}
    monkeypatch.setattr("src.alerts.alert_provenance_hit", lambda row: sent["prov"].append(row) or True)
    monkeypatch.setattr("src.alerts.alert_boundary_finding", lambda row: sent["boundary"].append(row) or True)
    return tmp_path, sent


def readers(deposits=(), ledgers=None, inbound=None, head=1_000):
    def ledger(a):
        return (ledgers or {}).get(a, [])

    def read_inbound(chain, address, *, since_ts, until_ts):
        return [{"from": f, "usd": u, "ts": until_ts - 5, "chain": chain, "from_is_contract": False,
                 "from_is_scam": False, "token": "0xusdc", "symbol": "USDC", "tx_hash": "0x1"}
                for f, u in (inbound or {}).get(address, [])]
    return {"head": lambda: head,
            "deposits": lambda lo, hi: [d for d in deposits if lo <= int(d["blockNumber"], 16) <= hi],
            "ledger": ledger, "unit": lambda a: {"addresses": [], "operations": []},
            "inbound": read_inbound, "first_gas": lambda chain, a: None,
            "circle_source": lambda entry, account: None,
            "label_of": lambda a, chain: None}


def _deposit_row(usd, ts_ms):
    return [{"time": ts_ms, "hash": "0xh", "delta": {"type": "deposit", "usdc": str(usd)}}]


def test_a_new_large_deposit_funded_by_his_wallet_pages_critical(sandbox):
    tmp, sent = sandbox
    r = readers(deposits=[dep(NEW, 2e6, 900, "0xd1")],
                ledgers={NEW: _deposit_row(2e6, 1_790_000_900_000)}, inbound={NEW: [(T, 2e6)]})
    assert rp.main([], readers=r, now="2026-10-06T00:00:00+00:00") == 0
    st = json.loads((tmp / "provenance" / "latest.json").read_text())
    assert st["resolved"] == 1 and st["findings"][0]["severity"] == "CRITICAL"
    assert sent["prov"] and sent["prov"][0]["account"] == NEW
    pool = json.loads((tmp / "provenance" / "bridge_deposits.json").read_text())
    assert pool["deposits"][0]["wallet"] == NEW and pool["cursor"] == 1_000


def test_a_perimeter_member_depositing_into_hl_is_a_member_finding(sandbox):
    tmp, sent = sandbox
    rp.main([], readers=readers(deposits=[dep(SINK, 2e5, 800, "0xd2")]),
            now="2026-10-06T00:00:00+00:00")
    st = json.loads((tmp / "provenance" / "latest.json").read_text())
    assert st["member_findings"][0]["kind"] == "perimeter_member_active_on_hl"
    assert sent["boundary"]


def test_a_resolved_account_is_cached_and_not_re_resolved_inside_its_ttl(sandbox):
    tmp, sent = sandbox
    r = readers(deposits=[dep(OTHER, 5e5, 900, "0xd3")],
                ledgers={OTHER: _deposit_row(5e5, 1_790_000_900_000)}, inbound={OTHER: []})
    rp.main([], readers=r, now="2026-10-06T00:00:00+00:00")
    r2 = readers(head=1_100, ledgers={OTHER: _deposit_row(5e5, 1_790_000_900_000)})
    calls = []
    r2["ledger"] = lambda a: calls.append(a) or []
    rp.main([], readers=r2, now="2026-10-06T01:00:00+00:00")
    assert calls == []


def test_dry_run_writes_only_its_directory(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    out = tmp_path_factory.mktemp("dry")
    rp.main(["--dry-run", str(out)],
            readers=readers(deposits=[dep(NEW, 2e6, 900, "0xd1")],
                            ledgers={NEW: _deposit_row(2e6, 1_790_000_900_000)},
                            inbound={NEW: [(T, 2e6)]}), now="2026-10-06T00:00:00+00:00")
    assert (out / "latest.json").exists() and not (tmp / "provenance").exists()
    assert not sent["prov"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_provenance.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.run_provenance'`

- [ ] **Step 3: Implement** — `scripts/run_provenance.py`:

```python
#!/usr/bin/env python3
"""Where did large new money into Hyperliquid accounts come from? (spec §7). trace.yml.

    python scripts/run_provenance.py                # the trace.yml step
    python scripts/run_provenance.py --dry-run DIR  # read-only: writes only DIR, no alerts

Owns the Bridge2 deposit feed (the correlator's complete bridge pool), queues
the accounts large money just entered plus every lead the project holds, and
resolves each one's funding back to its first boundary. Single writer:
data/provenance/.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import utils  # noqa: E402

FIRST_RUN_BLOCKS = 14 * 345_600      # 14 days of Arbitrum
POOL_DAYS, POOL_MIN_USD = 30, 10_000.0
QUEUE_MIN_USD = 100_000.0
ACCOUNTS_PER_RUN, SECONDS = 30, 150.0
CACHE_TTL_S = 7 * 86400
KEEP_ACCOUNTS, KEEP_FINDINGS = 2000, 500
EXTENSION = "0xa95d9c1f655341597c94393fddc30cf3c08e4fce"


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def make_label_of(config: dict, data: Path):
    from scripts.build_perimeter import load_services, measured
    from src.trace import store
    services, hot = load_services(config, data)
    contract, busy, known = measured(data)
    try:
        registry = store.load(data / "trace" / "registry")
    except RuntimeError:
        registry = {}

    def label_of(address: str, chain: str):
        a = (address or "").lower()
        if a in hot:
            return "exchange"
        if a in services:
            return "service"
        if chain == "hyperliquid":
            r = registry.get(a) or {}
            return "busy" if r.get("class") == "hub" or (r.get("hl") or {}).get("hub") else None
        if contract(a):
            return "contract"
        if busy(a):
            return "busy"
        return "quiet" if known(a) else None
    return label_of


def circle_source_reader(budget):
    """The burner behind a Circle deposit: the Arbitrum CCTP extension's own
    USDC transfer of the same amount in the 30 minutes before (keyless). A
    deposit from any other route stays unresolved here (recorded, not guessed)."""
    from src.boundary import bridge2, logs

    def resolve(entry: dict, account: str):
        usd, ts = entry.get("usd"), int(entry.get("ts") or 0)
        if not usd or not ts:
            return None
        try:
            lo = logs.block_at("arbitrum", ts - 1800)
            hi = logs.block_at("arbitrum", ts + 120, closest="after")
            rows = logs.read_logs("arbitrum", bridge2.USDC, {0: bridge2.TOPIC_TRANSFER,
                                                            2: bridge2.topic_address(EXTENSION)}, lo, hi)
        except logs.LogReadError:
            return None
        budget.spend()
        hits = [r for r in rows if abs(int(r["data"], 16) / 1e6 - usd) <= max(1.0, usd * 0.002)]
        if len(hits) != 1:
            return None
        return {"address": "0x" + hits[0]["topics"][1][-40:].lower(), "chain": "arbitrum"}
    return resolve


def default_readers(config: dict, data: Path, hl_budget) -> dict:
    from scripts.run_trace_engine import paced_post
    from src.boundary import bridge2, logs, readers, unit
    from src.trace import hl_ledger
    budget = readers.Budget(120, seconds=SECONDS)
    post = paced_post(hl_budget)

    def ledger(account):
        rows, error, _complete = hl_ledger.read(account, post, start_ms=0, max_pages=2)
        if error and not rows:
            raise readers.ReadError(error)
        return rows
    return {
        "head": lambda: logs.head_block("arbitrum"),
        "deposits": lambda lo, hi: logs.read_logs("arbitrum", bridge2.USDC, bridge2.deposit_topics(), lo, hi),
        "ledger": ledger,
        "unit": lambda a: unit.read_operations(a),
        "inbound": lambda chain, a, since_ts, until_ts: readers.inbound_transfers(
            chain, a, since_ts=since_ts, until_ts=until_ts, budget=budget),
        "first_gas": lambda chain, a: readers.first_gas(chain, a, budget=budget),
        "circle_source": circle_source_reader(budget),
        "label_of": make_label_of(config, data),
    }


def queue(data: Path, fresh: list, previous: dict, core: set) -> list[tuple]:
    """(rank, -usd, account, reason) — fresh money first, then the leads."""
    items: dict[str, tuple] = {}

    def add(account, rank, usd, reason):
        a = (account or "").lower()
        if not a.startswith("0x") or a in core:
            return
        cur = items.get(a)
        if cur is None or (rank, -usd) < cur[:2]:
            items[a] = (rank, -float(usd or 0), a, reason)
    for d in fresh:
        if d["amount"] >= QUEUE_MIN_USD:
            add(d["wallet"], 0, d["amount"], "bridge2 deposit")
    seen_ts = int(previous.get("cctp_seen_ts") or 0)
    for d in _read(data / "correlations" / "cctp_pool.json").get("deposits") or []:
        if int(d.get("ts") or 0) > seen_ts and float(d.get("amount") or 0) >= QUEUE_MIN_USD:
            add(d.get("wallet"), 0, d.get("amount"), "circle deposit")
    for n in _read(data / "newborn" / "latest.json").get("newborn") or []:
        add(n.get("wallet"), 1, n.get("account_value"), "newborn")
    for w in _read(data / "roster" / "latest.json").get("wallets") or []:
        if w.get("tier") in ("POSSIBLE", "PROBABLE"):
            add(w.get("wallet"), 2, 0, f"roster {w.get('tier')}")
    for a in (_read(data / "dormancy" / "latest.json").get("handoffs") or {}):
        add(a, 3, 0, "dormancy handoff")
    for m in _read(data / "correlations" / "latest.json").get("matches") or []:
        if m.get("route") == "route_unknown":
            add(m.get("wallet"), 4, m.get("deposit_amount_usd"), "correlation route unknown")
    return sorted(items.values())


def main(argv=None, *, readers=None, now=None) -> int:
    from src import alerts
    from src.boundary import attribution as at
    from src.boundary import bridge2, logs, provenance, unit
    from src.boundary import perimeter as pm
    from src.hl_budget import ReadBudget
    from src.trace import store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", metavar="DIR")
    args = parser.parse_args(argv)
    config, data = utils.load_config(), utils.DATA_DIR
    now_dt = datetime.fromisoformat(now) if now else datetime.now(UTC)
    now_ts, now_iso = int(now_dt.timestamp()), now_dt.isoformat()
    out_dir = Path(args.dry_run) if args.dry_run else data / "provenance"
    previous = _read(data / "provenance" / "latest.json")
    doc = _read(data / "perimeter" / "latest.json")
    index = pm.Index(doc if doc.get("members") else pm.core_only(config, now_iso))
    started = time.monotonic()

    with ReadBudget(seconds=SECONDS, weight_per_minute=500) as hl_budget:
        readers = readers or default_readers(config, data, hl_budget)

        # 1. The Bridge2 deposit feed -> the correlator's complete pool.
        pool = _read(data / "provenance" / "bridge_deposits.json")
        cursor = pool.get("cursor")
        walk, start = {"logs": [], "last_block": cursor, "error": None}, None
        try:
            head = int(readers["head"]())
            start = int(cursor) + 1 if cursor is not None else max(0, head - FIRST_RUN_BLOCKS)
            walk = logs.walk(readers["deposits"], start, head, max_calls=40, seconds=60)
        except Exception as exc:  # noqa: BLE001
            walk["error"] = f"{type(exc).__name__}: {exc}"
        fresh = [{"wallet": d["depositor"], "amount": d["usd"], "ts": d["ts"], "hash": d["tx_hash"],
                  "via": "bridge"}
                 for d in (bridge2.decode_deposit(x) for x in walk["logs"]) if d]
        horizon = now_ts - POOL_DAYS * 86400
        kept = {(d["hash"], d["wallet"]): d for d in (pool.get("deposits") or []) + fresh
                if d["amount"] >= POOL_MIN_USD and int(d["ts"]) >= horizon}
        pool = {"cursor": walk["last_block"], "updated_at": now_iso,
                "deposits": sorted(kept.values(), key=lambda d: d["ts"])}

        # 2. A perimeter member opening its own HL account.
        member_found = at.classify_all([at.from_bridge2_deposit(
            {"depositor": d["wallet"], "usd": d["amount"], "ts": d["ts"], "tx_hash": d["hash"],
             "log_index": 0}) for d in fresh], index)

        # 3. Queue, skipping fresh cache entries.
        cache = {}
        try:
            cache = store.load(data / "provenance" / "accounts")
        except RuntimeError as exc:
            print(f"[provenance] cache unreadable, starting empty: {exc}")
        fresh_wallets = {d["wallet"] for d in fresh}
        todo = [q for q in queue(data, fresh, previous, index.core)
                if q[2] in fresh_wallets or q[2] not in cache
                or now_ts - int((cache.get(q[2]) or {}).get("resolved_at") or 0) >= CACHE_TTL_S]

        # 4. Resolve.
        attempted, unreadable = 0, []
        for _rank, _usd, account, reason in todo:
            if attempted >= ACCOUNTS_PER_RUN or time.monotonic() - started >= SECONDS:
                break
            attempted += 1
            try:
                ledger = readers["ledger"](account)
                unit_events = []
                if any(str(((r or {}).get("delta") or {}).get("token") or "").upper().startswith("U")
                       for r in ledger):
                    unit_events = unit.events(readers["unit"](account))
                record = provenance.resolve(
                    account, ledger=ledger, unit_events=unit_events, index=index,
                    label_of=readers["label_of"], read_inbound=readers["inbound"],
                    read_first_gas=readers["first_gas"], read_ledger=readers["ledger"],
                    circle_source=readers["circle_source"], now_ts=now_ts)
            except Exception as exc:  # noqa: BLE001 - one account never stops the run
                unreadable.append({"account": account, "error": f"{type(exc).__name__}: {exc}"[:200]})
                continue
            record["reason"] = reason
            cache[account] = record

    # 5. Findings and alerts.
    found = [f for rec in cache.values() for f in provenance.findings(rec)]
    alerted = list(previous.get("alerted") or [])
    undelivered = []
    queue_rows = [r for r in previous.get("undelivered") or [] if r.get("key") not in alerted]
    queue_rows += [f for f in found + member_found
                   if f.get("severity") in at.SEVERITIES and f["key"] not in alerted]
    for row in queue_rows if not args.dry_run else []:
        ok = (alerts.alert_provenance_hit(row) if row.get("kind") == "provenance_touches_his_world"
              else alerts.alert_boundary_finding(row))
        (alerted.append(row["key"]) if ok else undelivered.append(row))

    # 6. Persist.
    keep = dict(sorted(cache.items(), key=lambda kv: -int(kv[1].get("resolved_at") or 0))[:KEEP_ACCOUNTS])
    recent = sorted(keep.values(), key=lambda r: -int(r.get("resolved_at") or 0))[:50]
    report = {
        "computed_at": now_iso, "deposit_cursor": walk["last_block"],
        "blocks_read": (walk["last_block"] - start + 1) if start is not None and walk["last_block"] is not None else 0,
        "deposits_read": len(fresh), "read_error": walk.get("error"), "queue": len(todo),
        "attempted": attempted, "resolved": attempted - len(unreadable),
        "unreadable_accounts": unreadable[:50],
        "findings": sorted(found, key=lambda f: -int(f.get("ts") or 0))[:KEEP_FINDINGS],
        "member_findings": member_found[:100],
        "alerted": sorted(set(alerted)), "undelivered": undelivered,
        "shared_funders": provenance.shared_funders(keep)[:200],
        "cctp_seen_ts": max([int(previous.get("cctp_seen_ts") or 0)] + [
            int(d.get("ts") or 0) for d in _read(data / "correlations" / "cctp_pool.json").get("deposits") or []]),
        "recent": [{"account": r["account"], "verdict": r.get("verdict"), "reason": r.get("reason"),
                    "routes": sorted({e["route"] for e in r.get("entries") or []}),
                    "usd": round(sum(e["usd"] or 0 for e in r.get("entries") or []), 2),
                    "hop1": [{k: s.get(k) for k in ("address", "class", "role", "family", "usd", "chain")}
                             for s in r.get("sources") or [] if s.get("hop") == 1][:4],
                    "resolved_at": r.get("resolved_at")} for r in recent],
        "counts": {"accounts_cached": len(keep), "findings": len(found),
                   "verdicts": {v: sum(r.get("verdict") == v for r in keep.values())
                                for v in ("touches_his_world", "same_exchange", "exchange",
                                          "unresolved", "unrelated")}},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    utils.atomic_write_json(out_dir / "bridge_deposits.json", pool)
    store.save(out_dir / "accounts", keep)
    utils.atomic_write_json(out_dir / "latest.json", report)
    print(f"[provenance] deposits {len(fresh)} read to block {walk['last_block']}; queue {len(todo)}, "
          f"resolved {report['resolved']}/{attempted}; findings {len(found)}; "
          f"verdicts {report['counts']['verdicts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Change `measured()` in `scripts/build_perimeter.py` to also return `known`:

```python
    def known(a):
        return bool(readings(a))
    return contract, busy, known
```

and its caller to `contract, busy, _known = measured(data)`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_provenance.py tests/test_build_perimeter.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/run_provenance.py scripts/build_perimeter.py tests/test_run_provenance.py
git commit -m "feat(boundary): provenance step - deposit feed, lead queue, resolve and alert"
```

---

### Task 13: Custody-gap correlator (`src/boundary/gaps.py`, `src/correlator.py`, `src/movements.py`)

**Files:**
- Create: `src/boundary/gaps.py`
- Modify: `src/movements.py` (`reconcile_movements(..., withdrawal_destinations=None)`), `src/correlator.py` (`collect_target_movements`, `collect_target_exits`, `get_recent_bridge_deposits`, `run_correlation`)
- Test: `tests/test_boundary_gaps.py`; existing `tests/test_correlator_pools.py`, `tests/test_migration_signals.py`, `tests/test_leaderboard_coverage.py`, `tests/test_movements.py` must stay green

**Interfaces:**
- Produces: `gaps.EXCHANGE_DEPOSIT|PERSON|HL_WITHDRAWAL|HL_WITHDRAWAL_UNKNOWN`; `gaps.exits(movements, *, index, classify_destination, min_amount) -> [exit]` (exit = `{"amount","ts","ref","id","event_ids","wallet","source"(kind),["destination"]}`); `gaps.route_consistent(match, record) -> (keep: bool, route: str)` with route in `{"unconstrained","route_unknown","same_exchange","exchange","no_exchange_source"}`; `correlator.bridge_pool_from_file(window_days, min_amount) -> (entries, error)`; findings gain `"route"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_boundary_gaps.py`:

```python
"""Spec §8: only money that crossed a custody gap is an exit; routes obey physics."""

from src.boundary import gaps, perimeter
from src.movements import reconcile_movements

T = "0x45d26f28196d226497130c4bac709d808fed4029"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
DEFI, HOT, PERSON, NEW = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40, "0x" + "4" * 40
INDEX = perimeter.Index(perimeter.build(
    config={"target_wallet": T, "known_self_wallets": []}, sentinels={S: {}}, trace_report={},
    trace_registry={}, solana={}, associates_found={}, families={f"deposit:{S}": [HOT]},
    services=set(), previous=None, now_iso="x"))


def classify(addr, chain):
    return {DEFI: "contract", HOT: "busy", PERSON: "eoa"}.get(addr, "unmeasured")


def mv(dest, amount=1e6, source="l1_outbound", resolved=False, **kw):
    return {"source": source, "destination": dest, "immediate_destination": dest, "amount": amount,
            "ts": 100, "ref": "0xr", "id": f"id-{dest}", "route_resolved": resolved,
            "source_wallet": T, "chain": "arbitrum", **kw}


def test_only_custody_gap_exits_survive():
    out = gaps.exits([mv(DEFI), mv(S), mv(HOT), mv(PERSON), mv(NEW), mv(T),
                      mv(PERSON, resolved=True), mv(PERSON, amount=10)],
                     index=INDEX, classify_destination=classify, min_amount=100_000)
    kinds = {e["destination"]: e["source"] for e in out}
    assert kinds == {S: gaps.EXCHANGE_DEPOSIT, HOT: gaps.EXCHANGE_DEPOSIT,
                     PERSON: gaps.PERSON, NEW: gaps.PERSON}


def test_hl_withdrawals_to_his_world_are_not_exits_and_unknown_ones_are():
    out = gaps.exits([mv(T, source="hl_withdraw"), mv(NEW, source="hl_withdraw"),
                      mv(None, source="hl_withdraw")],
                     index=INDEX, classify_destination=classify, min_amount=100_000)
    assert [e["source"] for e in out] == [gaps.HL_WITHDRAWAL, gaps.HL_WITHDRAWAL_UNKNOWN]


def _record(sources, complete=True, unreadable=()):
    return {"complete": complete, "unreadable": list(unreadable), "sources": sources}


def test_an_exchange_exit_needs_an_exchange_on_the_route():
    match = {"exit_source": gaps.EXCHANGE_DEPOSIT, "deposit_ts": 10_000, "gap_hours": 2}
    assert gaps.route_consistent(match, None) == (True, "route_unknown")
    assert gaps.route_consistent(match, _record([], complete=False)) == (True, "route_unknown")
    assert gaps.route_consistent(match, _record([], unreadable=[{"hop": 2}])) == (True, "route_unknown")
    fam = {"hop": 1, "class": "exchange", "family": ["deposit:x"], "last_ts": 9_000}
    assert gaps.route_consistent(match, _record([fam])) == (True, "same_exchange")
    busy = {"hop": 2, "class": "busy", "last_ts": 9_000}
    assert gaps.route_consistent(match, _record([busy])) == (True, "exchange")
    person = {"hop": 1, "class": "quiet", "last_ts": 9_000}
    assert gaps.route_consistent(match, _record([person])) == (False, "no_exchange_source")


def test_person_and_hl_exits_are_unconstrained():
    for kind in (gaps.PERSON, gaps.HL_WITHDRAWAL, "hl_cctp"):
        assert gaps.route_consistent({"exit_source": kind}, _record([])) == (True, "unconstrained")


def test_a_withdrawal_resolves_exactly_by_nonce():
    ledger = [{"hash": "0xw", "time": 5_000, "delta": {"type": "withdraw", "usdc": "1000000.0",
                                                     "nonce": 42}}]
    plain = reconcile_movements([], ledger, {T}, {})
    assert plain["unresolved_exits"][0]["destination"] is None
    exact = reconcile_movements([], ledger, {T}, {}, withdrawal_destinations={
        "42": {"destination": T}})
    assert exact["movements"][0]["resolution"] == "withdrawal_to_cluster"
    assert exact["unresolved_exits"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_gaps.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'gaps'`

- [ ] **Step 3: Implement `src/boundary/gaps.py`**

```python
# src/boundary/gaps.py
"""Custody-gap exits, and the route each one could re-emerge through (spec §8).

Measured 2026-10-06: the correlator's $3.39B of "unresolved exits" were mostly
DeFi positions he still held, bridges to himself and $370M of the target's own
withdrawals whose ledger row names no destination. Only money that crossed a
custody gap can re-emerge in a new account, and money that went into an
exchange can only come back out as that exchange's withdrawal.
"""

from __future__ import annotations

from src.boundary.perimeter import low

EXCHANGE_DEPOSIT, PERSON = "exchange_deposit", "person_transfer"
HL_WITHDRAWAL, HL_WITHDRAWAL_UNKNOWN = "hl_withdrawal_outside", "hl_withdrawal_unknown"
EXCHANGE_CLASSES = ("exchange", "busy")
ROUTE_SLACK_S = 86400


def exits(movements, *, index, classify_destination, min_amount: float) -> list[dict]:
    out = []
    for mv in movements or []:
        amount = float(mv.get("amount") or 0)
        if amount < min_amount:
            continue
        base = {"amount": amount, "ts": int(mv.get("ts") or 0), "ref": mv.get("ref"),
                "id": mv.get("id"), "event_ids": mv.get("event_ids") or [mv.get("id")],
                "wallet": mv.get("source_wallet")}
        if mv.get("source") == "hl_withdraw":
            dest = low(mv.get("destination"))
            if not dest:
                out.append({**base, "source": HL_WITHDRAWAL_UNKNOWN})
            elif index.get(dest) is None:
                out.append({**base, "source": HL_WITHDRAWAL, "destination": dest})
            continue
        if mv.get("source") != "l1_outbound" or mv.get("route_resolved"):
            continue
        dest = low(mv.get("destination") or mv.get("immediate_destination"))
        member = index.get(dest)
        if member is not None:
            if member["role"] == "deposit":
                out.append({**base, "source": EXCHANGE_DEPOSIT, "destination": dest})
            continue
        kind = classify_destination(dest, mv.get("chain"))
        if kind in ("eoa", "unmeasured"):
            out.append({**base, "source": PERSON, "destination": dest})
        elif kind == "busy":
            out.append({**base, "source": EXCHANGE_DEPOSIT, "destination": dest})
    return out


def route_consistent(match: dict, record: dict | None) -> tuple[bool, str]:
    if match.get("exit_source") != EXCHANGE_DEPOSIT:
        return True, "unconstrained"
    if not record or not record.get("complete") or record.get("unreadable"):
        return True, "route_unknown"
    end = int(match.get("deposit_ts") or 0)
    start = end - int(float(match.get("gap_hours") or 0) * 3600) - ROUTE_SLACK_S
    on_route = [s for s in record.get("sources") or [] if s.get("hop") in (1, 2)
                and s.get("class") in EXCHANGE_CLASSES
                and start <= int(s.get("last_ts") or end) <= end]
    if any(s.get("family") for s in on_route):
        return True, "same_exchange"
    if on_route:
        return True, "exchange"
    return False, "no_exchange_source"
```

- [ ] **Step 4: `src/movements.py`** — add the keyword parameter and use it in the withdrawal loop:

```python
def reconcile_movements(records: list[dict], ledger: list[dict], cluster: set[str],
                        bridge_decodes: dict, min_amount: float = 0,
                        withdrawal_destinations: dict | None = None) -> dict:
```

and, in the `for entry in ledger` withdrawal loop, after `destination = str(delta.get("destination") or "").lower()`:

```python
        exact = (withdrawal_destinations or {}).get(str(delta.get("nonce")))
        if exact and not pairs:
            # Bridge2's FinalizedWithdrawal names it by nonce (src/boundary/bridge2.py).
            destination = str(exact.get("destination") or "").lower()
```

- [ ] **Step 5: `src/correlator.py`** — four changes:

(a) `collect_target_movements`: read exact destinations and pass them through:

```python
    boundary = {}
    try:
        boundary = json.loads((directory / "boundary" / "latest.json").read_text())
    except (OSError, ValueError):
        pass
    exact = boundary.get("core_withdrawals") if isinstance(boundary, dict) else None
    result = reconcile_movements(records, ledger, cluster, decodes, min_amount,
                                 withdrawal_destinations=exact if isinstance(exact, dict) else None)
```

(b) `collect_target_exits` returns custody-gap exits:

```python
def collect_target_exits(target: str, min_amount: float) -> list[dict]:
    """Only money that crossed a custody gap (src/boundary/gaps.py)."""
    import json as _json

    from src.boundary import gaps
    from src.boundary import perimeter as pm

    config = load_config()
    moved = collect_target_movements(target, 0, config=config)
    try:
        doc = _json.loads((DATA_DIR / "perimeter" / "latest.json").read_text())
    except (OSError, ValueError):
        doc = {}
    index = pm.Index(doc if isinstance(doc, dict) and doc.get("members")
                     else pm.core_only(config, utc_now()))
    found = gaps.exits(moved["movements"], index=index,
                       classify_destination=_destination_classifier(config), min_amount=min_amount)
    found += [row for row in moved["unresolved_exits"] if row.get("source") == "hl_cctp"]
    return found


def _destination_classifier(config: dict):
    from scripts.build_perimeter import load_services, measured
    services, hot = load_services(config, DATA_DIR)
    contract, busy, known = measured(DATA_DIR)

    def classify(address: str, chain) -> str:
        a = (address or "").lower()
        if a in hot:
            return "busy"
        if a in services or contract(a):
            return "contract"
        if busy(a):
            return "busy"
        return "eoa" if known(a) else "unmeasured"
    return classify
```

and make `unpaired_cctp_exits` rows carry `"source": "hl_cctp"` (they already do) — they are kept as unconstrained exits.

(c) `get_recent_bridge_deposits`: the complete pool first.

```python
def bridge_pool_from_file(window_days: float, min_amount: float) -> tuple[list[dict], str | None]:
    """The Bridge2 deposit pool run_provenance.py keeps from Arbitrum logs.

    Complete by construction (a cursor that never skips); the Etherscan reader
    below hit its page ceiling ("candidate pool is incomplete", 2026-10-05).
    """
    import json as _json
    try:
        pool = _json.loads((DATA_DIR / "provenance" / "bridge_deposits.json").read_text())
    except (OSError, ValueError):
        return [], "bridge deposit pool not built yet"
    updated = pool.get("updated_at")
    try:
        age_h = (datetime.now(UTC) - datetime.fromisoformat(updated)).total_seconds() / 3600
    except (TypeError, ValueError):
        return [], "bridge deposit pool has no timestamp"
    if age_h > 12:
        return [], f"bridge deposit pool is {age_h:.0f}h old"
    horizon = time.time() - window_days * 86400
    return ([d for d in pool.get("deposits") or []
             if float(d.get("amount") or 0) >= min_amount and int(d.get("ts") or 0) >= horizon], None)
```

Rename the existing `get_recent_bridge_deposits` body to `_etherscan_bridge_deposits` and define:

```python
def get_recent_bridge_deposits(window_days: float, min_amount: float,
                               budget=None) -> tuple[list[dict], str | None]:
    entries, error = bridge_pool_from_file(window_days, min_amount)
    if error is None:
        return entries, None
    return _etherscan_bridge_deposits(window_days, min_amount, budget)
```

(d) `run_correlation`: after `findings = find_correlations(...)` add the route filter:

```python
        records = _provenance_records()
        kept = []
        for f in findings:
            ok, route = route_consistent(f, records.get((f.get("wallet") or "").lower()))
            f["route"] = route
            if ok:
                kept.append(f)
        findings = kept
```

with

```python
def _provenance_records() -> dict:
    from src.trace import store
    try:
        return store.load(DATA_DIR / "provenance" / "accounts")
    except (RuntimeError, OSError):
        return {}
```

and `from src.boundary.gaps import route_consistent` at the top of `run_correlation`.

- [ ] **Step 6: Run the gap tests and every correlator-adjacent suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_boundary_gaps.py tests/test_correlator_pools.py tests/test_migration_signals.py tests/test_leaderboard_coverage.py tests/test_movements.py tests/test_accounting.py -q -p no:cacheprovider`
Expected: PASS. If a `collect_target_exits` test fails because its destination had no reading, it is now a PERSON exit (still included) — the behaviour those tests pin is unchanged; a test that expected a CONTRACT destination to be an exit is the bug this task fixes and is updated with a comment citing spec §8.

- [ ] **Step 7: Commit**

```bash
git add src/boundary/gaps.py src/movements.py src/correlator.py tests/test_boundary_gaps.py tests/test_migration_signals.py
git commit -m "feat(correlator): custody-gap exits, the complete bridge pool, and route physics"
```

---

### Task 14: Roster votes and the route-index noise fix

**Files:**
- Modify: `src/roster.py` (after the Circle-flows block), `src/route_index.py` (`discover`)
- Test: `tests/test_roster_boundary.py`, extend `tests/test_route_index.py`

**Interfaces:**
- Consumes: `data/boundary/latest.json` `findings` (subject `hl_account`), `data/provenance/latest.json` `findings` (subject `account`) and `member_findings` (subject `hl_account`).
- Produces: roster rows gain `vectors` `transfer`/`linkage` per finding vote and `evidence["boundary"]` = list of `{kind, severity, vote, role, member, source, chain, amount_usd|usd, ts, ref|entry_ref, hop}`.

- [ ] **Step 1: Write the failing tests** — `tests/test_roster_boundary.py`:

```python
"""Boundary and provenance findings reach the roster as votes and evidence."""

import json

from src import roster, utils

T = "0x45d26f28196d226497130c4bac709d808fed4029"
OUT, NEW, SINKY = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40


def test_votes_and_evidence_come_from_each_findings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    (tmp_path / "boundary").mkdir()
    (tmp_path / "boundary" / "latest.json").write_text(json.dumps({"findings": [
        {"kind": "outside_account_paid_his_world", "hl_account": OUT, "severity": "CRITICAL",
         "vote": "linkage", "role": "deposit", "member": "0xdep", "source": "bridge2",
         "chain": "arbitrum", "amount_usd": 2e5, "ts": 1, "ref": "0xr"},
        {"kind": "outside_account_paid_his_world", "hl_account": SINKY, "severity": None,
         "vote": None, "role": "associate", "member": "0xassoc", "source": "bridge2",
         "chain": "arbitrum", "amount_usd": 2e5, "ts": 2, "ref": "0xs"}]}))
    (tmp_path / "provenance").mkdir()
    (tmp_path / "provenance" / "latest.json").write_text(json.dumps({"findings": [
        {"kind": "provenance_touches_his_world", "account": NEW, "severity": "CRITICAL",
         "vote": "transfer", "hop": 1, "role": "core", "member": T, "route": "bridge2",
         "usd": 2e6, "ts": 3, "entry_ref": "0xe"}], "member_findings": []}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    rows = {w["wallet"]: w for w in doc["wallets"]}
    assert "linkage" in rows[OUT]["vectors"] and rows[OUT]["evidence"]["boundary"]
    assert "transfer" in rows[NEW]["vectors"]
    assert rows[SINKY]["vectors"] == [] and rows[SINKY]["evidence"]["boundary"]
```

Add to `tests/test_route_index.py`:

```python
def test_mints_and_system_contracts_never_become_funding_route_candidates():
    from src.route_index import index_routes
    T = "0x45d26f28196d226497130c4bac709d808fed4029"
    recs = [{"id": f"r{i}", "src": src, "dst": T, "amount_usd": 1e6, "ts": 1, "chain": "polygon",
             "tx_hash": f"0x{i}"} for i, src in enumerate((
                 "0x0000000000000000000000000000000000000000",
                 "0x0000000000000000000000000000000000001010",
                 "0x2000000000000000000000000000000000000000"))]
    out = index_routes(recs, {}, [], {T})
    assert out["discoveries"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_roster_boundary.py tests/test_route_index.py -q -p no:cacheprovider`
Expected: FAIL — `KeyError: 'boundary'` and a non-empty `discoveries`

- [ ] **Step 3: Implement**

In `src/roster.py`, after the Circle-flows block (before `hl_transfers`):

```python
    # Hyperliquid's edge (src/boundary, spec 2026-10-06): an account that paid
    # his world, one his world funded, or a new account whose money came from
    # it. Each is an observed transfer named by the protocol's own record, so it
    # carries the vote its finding was given — `transfer` for his wallets,
    # `linkage` for his private deposit addresses — and none for an address that
    # only holds his money (evidence, never a vote; `graph_reach_only` rule).
    for path, subject, keys in (
            (DATA_DIR / "boundary" / "latest.json", "hl_account", ("findings",)),
            (DATA_DIR / "provenance" / "latest.json", "account", ("findings",)),
            (DATA_DIR / "provenance" / "latest.json", "hl_account", ("member_findings",))):
        for key in keys:
            for row in _read(path, key):
                a = (row.get(subject) or "").lower() if isinstance(row, dict) else ""
                if not a or a == target or a in known_self:
                    continue
                e = entry(a)
                if row.get("vote") == VECTOR_TRANSFER:
                    e["vectors"].add(VECTOR_TRANSFER)
                elif row.get("vote") == VECTOR_LINKAGE:
                    e["vectors"].add(VECTOR_LINKAGE)
                e["evidence"].setdefault("boundary", []).append(
                    {k: row.get(k) for k in ("kind", "severity", "vote", "role", "member", "source",
                                             "chain", "amount_usd", "usd", "ts", "ref",
                                             "entry_ref", "hop", "route")})
                reason = {"outside_account_paid_his_world": "Paid an address of his (protocol record)",
                          "his_world_funded_outside_account": "Funded by an address of his (protocol record)",
                          "provenance_touches_his_world": "Its money came from his world (traced back)",
                          "perimeter_member_active_on_hl": "An address holding his money, active on Hyperliquid",
                          }.get(row.get("kind"))
                if reason and reason not in e["reasons"]:
                    e["reasons"].append(reason)
```

In `src/route_index.py` `discover`, extend the guard:

```python
    def discover(wallet, route):
        # A mint (the zero address), a chain's native-token system contract or a
        # HyperCore system address is not a funder (data/candidates/0x000…0000.json
        # held hundreds of these).
        if (not wallet or wallet in cluster or wallet == "0x" + "0" * 40
                or wallet == "0x0000000000000000000000000000000000001010"
                or wallet.startswith(("0x20000000000000000000000000000000000000", "0x2222222222"))):
            return
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_roster_boundary.py tests/test_route_index.py tests/test_roster.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/roster.py src/route_index.py tests/test_roster_boundary.py tests/test_route_index.py
git commit -m "feat(roster): boundary and provenance votes; no funding routes from mints"
```

---

### Task 15: Feed health and workflows

**Files:**
- Modify: `src/feed_health.py`, `.github/workflows/watch.yml`, `.github/workflows/trace.yml`, `src/correlator.py` (`--no-etherscan` flag), `scripts/check_boundary.py` and `scripts/run_provenance.py` (`--perimeter PATH` for dry runs)
- Test: extend `tests/test_feed_health.py`; create `tests/test_boundary_workflows.py`

**Interfaces:**
- Produces: feeds `"boundary attribution"` (watch group), `"his perimeter"` and `"funding provenance"` (other group); `BLIND_CHECKS` entries `_boundary`, `_perimeter`, `_provenance`; `BOUNDARY_MIN_BLOCKS_FOR_ZERO = 20_000`; correlator `run_correlation(pools, *, etherscan_bridge=True)` and CLI `--no-etherscan`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_feed_health.py`:

```python
def test_the_boundary_feeds_have_blind_checks():
    assert fh.WATCH_FEEDS["boundary attribution"][0] == "boundary/latest.json"
    assert fh.OTHER_FEEDS["his perimeter"][0] == "perimeter/latest.json"
    assert fh.OTHER_FEEDS["funding provenance"][0] == "provenance/latest.json"
    b = fh.BLIND_CHECKS["boundary attribution"]
    assert b({"blocks_read": 50_000, "withdrawals_read": 0}).startswith("0 Bridge2")
    assert b({"blocks_read": 500, "withdrawals_read": 0}) is None
    assert "failing" in b({"read_error": "429", "withdrawals_read": 0})
    assert fh.BLIND_CHECKS["his perimeter"]({"counts": {}}) and \
        fh.BLIND_CHECKS["his perimeter"]({"counts": {"core": 3}}) is None
    p = fh.BLIND_CHECKS["funding provenance"]
    assert p({"attempted": 6, "resolved": 0}) and p({"attempted": 6, "resolved": 2}) is None
```

Create `tests/test_boundary_workflows.py`:

```python
"""The boundary steps are wired where their inputs exist and before their readers."""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _names(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    return re.findall(r"^      - name: (.+)$", text, re.MULTILINE), text


def test_trace_builds_the_perimeter_then_provenance_then_correlates_then_rosters():
    names, text = _names("trace.yml")
    order = ["Run the trace engine", "Build his perimeter", "Resolve funding provenance",
             "Correlate custody-gap exits", "Build wallet roster"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    assert "python src/correlator.py --pools bridge cctp --no-etherscan" in text
    assert "Correlate Circle deposits" not in names
    job = re.search(r"^    timeout-minutes: (\d+)$", text, re.MULTILINE)
    assert job and int(job.group(1)) >= 45


def test_watch_attributes_withdrawals_with_a_bounded_step():
    names, text = _names("watch.yml")
    assert "Attribute withdrawals to his world" in names
    block = text.split("- name: Attribute withdrawals to his world", 1)[1].split("- name:", 1)[0]
    assert "scripts/check_boundary.py" in block and "timeout-minutes: 4" in block
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_health.py tests/test_boundary_workflows.py -q -p no:cacheprovider`
Expected: FAIL — `KeyError: 'boundary attribution'`

- [ ] **Step 3: `src/feed_health.py`**

```python
WATCH_FEEDS = {
    "close watch": ("watchlist/latest.json", "computed_at", 360),
    "deposit-address sentinels": ("deposit_sentinels/latest.json", "computed_at", 360),
    "Circle flows": ("circle_flows/latest.json", "computed_at", 360),
    "boundary attribution": ("boundary/latest.json", "computed_at", 360),
}
```

add to `OTHER_FEEDS`:

```python
    "his perimeter": ("perimeter/latest.json", "computed_at", 720),
    "funding provenance": ("provenance/latest.json", "computed_at", 720),
```

and

```python
# ~1.4h of Arbitrum. Bridge2 paid ~130 withdrawals an hour when this was
# written (826 in 6.26h, 2026-10-06), so a read this long with none is blind.
BOUNDARY_MIN_BLOCKS_FOR_ZERO = 20_000


def _boundary(doc: dict):
    if doc.get("read_error") and not doc.get("withdrawals_read"):
        return f"Bridge2 reads failing: {str(doc['read_error'])[:120]}"
    blocks = doc.get("blocks_read")
    if isinstance(blocks, int) and blocks >= BOUNDARY_MIN_BLOCKS_FOR_ZERO \
            and not doc.get("withdrawals_read"):
        return f"0 Bridge2 withdrawals decoded in {blocks:,} blocks"
    return None


def _perimeter(doc: dict):
    return None if (doc.get("counts") or {}).get("core") else "the perimeter holds no core wallet"


def _provenance(doc: dict):
    attempted = doc.get("attempted")
    if isinstance(attempted, int) and attempted >= 5 and not doc.get("resolved"):
        return f"0 of {attempted} accounts resolved"
    if doc.get("read_error") and not doc.get("deposits_read"):
        return f"Bridge2 deposit reads failing: {str(doc['read_error'])[:120]}"
    return None
```

registered in `BLIND_CHECKS`: `"boundary attribution": _boundary, "his perimeter": _perimeter, "funding provenance": _provenance`.

- [ ] **Step 4: `src/correlator.py`** — `run_correlation(pools=POOLS, *, etherscan_bridge: bool = True)`; inside, `readers = {"bridge": (get_recent_bridge_deposits if etherscan_bridge else bridge_pool_from_file), "cctp": get_recent_cctp_deposits}`; and in `main`:

```python
    parser.add_argument("--no-etherscan", action="store_true",
                        help="bridge pool from data/provenance only (trace.yml: it was just built)")
    args = parser.parse_args(argv)
    run_correlation(tuple(args.pools), etherscan_bridge=not args.no_etherscan)
```

Update `test_main_passes_the_pools_flag_through` to the new call shape: `monkeypatch.setattr(correlator, "run_correlation", lambda pools, **kw: got.append(pools))`.

- [ ] **Step 5: `--perimeter PATH` for dry runs** — in `scripts/check_boundary.py` and `scripts/run_provenance.py` add `parser.add_argument("--perimeter", metavar="PATH")` and read `Path(args.perimeter) if args.perimeter else data / "perimeter" / "latest.json"`.

- [ ] **Step 6: `.github/workflows/watch.yml`** — after the "Watch Circle flows in and out of Hyperliquid" step, insert:

```yaml
      # Hyperliquid's own edge, read on Arbitrum: Bridge2's FinalizedWithdrawal
      # names the account that withdrew AND where the money went, for every
      # account (docs/superpowers/specs/2026-10-06-boundary-trace-design.md).
      # An outside account paying one of his addresses — a wallet, a private
      # deposit address, his Solana wallet through Unit — is the attribution
      # no other detector makes. Reads the perimeter trace.yml builds; with
      # none yet it watches the config wallets alone.
      - name: Attribute withdrawals to his world
        if: ${{ !cancelled() && steps.deps.conclusion == 'success' }}
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
          BREVO_SMTP_LOGIN: ${{ secrets.BREVO_SMTP_LOGIN }}
          BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
          ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
          GITHUB_TOKEN: ${{ github.token }}
        # Internal: 60s Bridge2 walk + ≤ 8 Unit + ≤ 40 Blockscout calls.
        timeout-minutes: 4
        run: python scripts/check_boundary.py
```

and update the job comment's sum to `(8 + 3 + 5 + 4 + 4 + 2 + 2 = 28)` under the existing 30.

- [ ] **Step 7: `.github/workflows/trace.yml`** — delete the "Correlate Circle deposits" step; after "Run the trace engine" insert:

```yaml
      # His world as one table (src/boundary/perimeter.py): config, deposit
      # sentinels, the engine's sinks and funders, his Solana identity, large
      # two-way counterparties; then each member put to Hyperliquid. After the
      # engine (its registry is an input), before provenance (which joins on it).
      - name: Build his perimeter
        if: ${{ !cancelled() && steps.deps.conclusion == 'success' }}
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
          BREVO_SMTP_LOGIN: ${{ secrets.BREVO_SMTP_LOGIN }}
          BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
          ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
          GITHUB_TOKEN: ${{ github.token }}
        # A daily substrate pass (~60s) + ≤ 25 HL readings under a 120s budget.
        timeout-minutes: 5
        run: python scripts/build_perimeter.py
      # Where large new money came from (src/boundary/provenance.py): the
      # Bridge2 deposit feed (the correlator's complete bridge pool), then the
      # accounts it and Circle just funded, the newborns and every lead, traced
      # back one or two hops and joined against the perimeter.
      - name: Resolve funding provenance
        if: ${{ !cancelled() && steps.deps.conclusion == 'success' }}
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
          BREVO_SMTP_LOGIN: ${{ secrets.BREVO_SMTP_LOGIN }}
          BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
          ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
          GITHUB_TOKEN: ${{ github.token }}
        # Internal: 150s, 30 accounts, 120 Blockscout calls.
        timeout-minutes: 6
        run: python scripts/run_provenance.py
      # Custody-gap exits only (src/boundary/gaps.py) against BOTH pools: the
      # bridge pool provenance just refreshed (no Etherscan) and Circle's
      # forwarder feed; a match whose route cannot have crossed an exchange is
      # dropped, one whose route is unread is kept and queued for provenance.
      - name: Correlate custody-gap exits
        if: ${{ !cancelled() && steps.deps.conclusion == 'success' }}
        env:
          BREVO_SMTP_LOGIN: ${{ secrets.BREVO_SMTP_LOGIN }}
          BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
          ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
          GITHUB_TOKEN: ${{ github.token }}
        timeout-minutes: 4          # Circle pool budget 120s; bridge pool is a file read
        run: python src/correlator.py --pools bridge cctp --no-etherscan
```

and raise the job `timeout-minutes: 35` to `45` with the comment line `# 45 since 2026-10-06: perimeter (≤5) and provenance (≤6) joined; healthy runs measured 21-25 min.`

- [ ] **Step 8: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_health.py tests/test_boundary_workflows.py tests/test_workflow_step_independence.py tests/test_correlator_pools.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/feed_health.py src/correlator.py scripts/check_boundary.py scripts/run_provenance.py .github/workflows/watch.yml .github/workflows/trace.yml tests/test_feed_health.py tests/test_boundary_workflows.py tests/test_correlator_pools.py
git commit -m "ci: wire boundary attribution (watch), perimeter and provenance (trace); feed health"
```

---

### Task 16: Dashboard — the Trace page

**Files:**
- Modify: `dashboard/src/lib/api.js` (fetcher, pure helpers, `DETECTOR_FEEDS`), `dashboard/src/lib/api.test.js`, `dashboard/src/lib/ui/Icon.svelte` (`trace` icon), `dashboard/src/routes/+layout.svelte` (nav), `dashboard/src/routes/transfers/+page.svelte` (known-wallet label)
- Create: `dashboard/src/routes/trace/+page.svelte`

**Interfaces:**
- Produces: `fetchTrace() -> {perimeter, boundary, provenance, engine}`; `traceFindings(boundary, provenance) -> [{origin, kind, label, severity, account, other, role, usd, ts, route, hop, retro}]` newest first; `perimeterRows(perimeter) -> [{address, role, weight, why, active, checked}]` by role order; `provenanceRows(provenance) -> [{account, verdict, verdictLabel, reason, routes, usd, hop1, when}]`; `TRACE_KIND_LABEL`, `VERDICT_LABEL`, `ROLE_ORDER`.

- [ ] **Step 1: Write the failing tests** — append to `dashboard/src/lib/api.test.js`:

```js
import { traceFindings, perimeterRows, provenanceRows, DETECTOR_FEEDS as FEEDS2 } from './api.js';

test('trace findings merge both files newest first and keep severity', () => {
	const boundary = { findings: [
		{ kind: 'outside_account_paid_his_world', severity: 'CRITICAL', hl_account: '0xa', counterparty: '0xb', role: 'deposit', amount_usd: 5, ts: 10, source: 'bridge2' },
		{ kind: 'perimeter_member_active_on_hl', severity: null, hl_account: '0xc', counterparty: '0xc', role: 'associate', ts: 30 }] };
	const provenance = { findings: [
		{ kind: 'provenance_touches_his_world', severity: 'HIGH', account: '0xd', member: '0xe', role: 'sink', usd: 7, ts: 20, hop: 2, route: 'bridge2' }] };
	const rows = traceFindings(boundary, provenance);
	assert.deepEqual(rows.map((r) => r.account), ['0xc', '0xd', '0xa']);
	assert.equal(rows[1].other, '0xe');
	assert.equal(rows[2].severity, 'CRITICAL');
	assert.ok(rows.every((r) => typeof r.label === 'string' && r.label.length));
	assert.deepEqual(traceFindings(null, undefined), []);
});

test('perimeter rows are ordered by role strength', () => {
	const rows = perimeterRows({ members: {
		'0x3': { address: '0x3', role: 'associate', weight: 0.3, why: 'two-way' },
		'0x1': { address: '0x1', role: 'core', weight: 1, why: 'config', hl: { active: true, checked_at: 'x' } },
		'0x2': { address: '0x2', role: 'deposit', weight: 1, why: 'binance' } } });
	assert.deepEqual(rows.map((r) => r.role), ['core', 'deposit', 'associate']);
	assert.equal(rows[0].active, true);
	assert.deepEqual(perimeterRows(null), []);
});

test('provenance rows carry a readable verdict', () => {
	const rows = provenanceRows({ recent: [{ account: '0x9', verdict: 'same_exchange', routes: ['bridge2'], usd: 1, hop1: [], resolved_at: 1 }] });
	assert.equal(rows[0].verdictLabel, 'Funded by his exchange');
	assert.deepEqual(provenanceRows({}), []);
});

test('the trace feeds are on the detector list', () => {
	const names = new Set(FEEDS2.map((f) => f.name.toLowerCase()));
	for (const n of ['boundary attribution', 'his perimeter', 'funding provenance']) assert.ok(names.has(n), n);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd dashboard && npm test`
Expected: FAIL — `traceFindings is not a function`

- [ ] **Step 3: Implement in `dashboard/src/lib/api.js`** (append, and add three rows to `DETECTOR_FEEDS`):

```js
	{ name: 'Boundary attribution', path: 'data/boundary/latest.json', key: 'computed_at', limitMin: 360 },
	{ name: 'His perimeter', path: 'data/perimeter/latest.json', key: 'computed_at', limitMin: 720 },
	{ name: 'Funding provenance', path: 'data/provenance/latest.json', key: 'computed_at', limitMin: 720 },
```

```js
// --- trace (src/boundary, spec 2026-10-06) ----------------------------------------

export async function fetchTrace() {
	const [perimeter, boundary, provenance, engine] = await Promise.all([
		fetchJSON('data/perimeter/latest.json'),
		fetchJSON('data/boundary/latest.json'),
		fetchJSON('data/provenance/latest.json'),
		fetchJSON('data/trace/latest.json')
	]);
	return { perimeter, boundary, provenance, engine };
}

export const TRACE_KIND_LABEL = {
	outside_account_paid_his_world: 'An outside account paid his address',
	his_world_funded_outside_account: 'His address funded an outside account',
	his_account_paid_outside_address: 'His account sent money to a new address',
	perimeter_member_active_on_hl: 'An address holding his money is active on Hyperliquid',
	provenance_touches_his_world: "A Hyperliquid account's money came from his world"
};

export const VERDICT_LABEL = {
	touches_his_world: 'Touches his world',
	same_exchange: 'Funded by his exchange',
	exchange: 'Funded by an exchange',
	unresolved: 'Could not be fully read',
	unrelated: 'Unrelated'
};

export const ROLE_ORDER = ['core', 'deposit', 'identity', 'sink', 'funder', 'associate'];

/** Attribution and provenance findings as one list, newest first. Pure. */
export function traceFindings(boundary, provenance) {
	const rows = [];
	for (const f of boundary?.findings || []) {
		rows.push({ origin: 'boundary', kind: f.kind, label: TRACE_KIND_LABEL[f.kind] || f.kind,
			severity: f.severity || null, account: f.hl_account, other: f.counterparty || f.counterparty_raw,
			role: f.role, usd: f.amount_usd ?? null, ts: f.ts || 0, route: f.source, hop: null,
			retro: !!f.retro });
	}
	for (const f of [...(provenance?.findings || []), ...(provenance?.member_findings || [])]) {
		rows.push({ origin: 'provenance', kind: f.kind, label: TRACE_KIND_LABEL[f.kind] || f.kind,
			severity: f.severity || null, account: f.account || f.hl_account, other: f.member || f.counterparty,
			role: f.role, usd: f.usd ?? f.amount_usd ?? null, ts: f.ts || 0, route: f.route || f.source,
			hop: f.hop ?? null, retro: !!f.retro });
	}
	return rows.sort((a, b) => b.ts - a.ts);
}

/** Perimeter members, strongest role first. Pure. */
export function perimeterRows(perimeter) {
	return Object.values(perimeter?.members || {})
		.map((m) => ({ address: m.address, role: m.role, weight: m.weight, why: m.why,
			active: m.hl?.active ?? null, checked: m.hl?.checked_at || null }))
		.sort((a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role)
			|| String(a.address).localeCompare(String(b.address)));
}

/** The newest resolved accounts with a readable verdict. Pure. */
export function provenanceRows(provenance) {
	return (provenance?.recent || []).map((r) => ({ account: r.account, verdict: r.verdict,
		verdictLabel: VERDICT_LABEL[r.verdict] || r.verdict, reason: r.reason, routes: r.routes || [],
		usd: r.usd, hop1: r.hop1 || [], when: r.resolved_at ? new Date(r.resolved_at * 1000) : null }));
}
```

- [ ] **Step 4: The page** — `dashboard/src/routes/trace/+page.svelte`:

```svelte
<script>
	import { onMount } from 'svelte';
	import { fetchTrace, traceFindings, perimeterRows, provenanceRows, formatUSD } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';

	let data = null;
	let loading = true;
	onMount(async () => { data = await fetchTrace(); loading = false; });

	const SEV_BADGE = { CRITICAL: 'badge-red', HIGH: 'badge-yellow' };
	const VERDICT_BADGE = { touches_his_world: 'badge-red', same_exchange: 'badge-yellow',
		exchange: 'badge-grey', unresolved: 'badge-grey', unrelated: 'badge-grey' };

	$: findings = data ? traceFindings(data.boundary, data.provenance).slice(0, 60) : [];
	$: members = data ? perimeterRows(data.perimeter) : [];
	$: recent = data ? provenanceRows(data.provenance).slice(0, 40) : [];
	$: stops = (data?.engine?.boundaries || []).slice(0, 20);
	$: counts = data?.provenance?.counts?.verdicts || {};
	function when(ts) { return ts ? new Date(ts * 1000).toLocaleString('en-GB', { hour12: false }) : '—'; }
	function iso(s) { const t = Date.parse(s); return Number.isNaN(t) ? '—' : new Date(t).toLocaleString('en-GB', { hour12: false }); }
</script>

<svelte:head><title>Trace · Ezekiel</title></svelte:head>

<h1>Trace</h1>
<p class="lede">
	Every way money enters or leaves a Hyperliquid account, joined against his world from both
	sides: <strong>who paid one of his addresses</strong>, and <strong>where each large new account's
	money came from</strong>. A finding names both ends from the protocol's own record; it is still
	not proof of ownership.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else}
	<section class="card">
		<h2>Feeds</h2>
		<table>
			<tbody>
				<tr><td>Bridge2 withdrawals read to block</td><td class="mono">{data.boundary?.withdrawal_cursor ?? '—'}</td>
					<td>{data.boundary?.read_error ? 'error: ' + data.boundary.read_error : 'ok'}</td><td>{iso(data.boundary?.computed_at)}</td></tr>
				<tr><td>Bridge2 deposits read to block</td><td class="mono">{data.provenance?.deposit_cursor ?? '—'}</td>
					<td>{data.provenance?.read_error ? 'error: ' + data.provenance.read_error : 'ok'}</td><td>{iso(data.provenance?.computed_at)}</td></tr>
				<tr><td>Accounts traced back (cached)</td><td>{data.provenance?.counts?.accounts_cached ?? 0}</td>
					<td colspan="2">{Object.entries(counts).map(([k, v]) => `${k.replaceAll('_', ' ')} ${v}`).join(' · ')}</td></tr>
				<tr><td>His world</td><td>{members.length} addresses</td>
					<td colspan="2">{Object.entries(data.perimeter?.counts || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}</td></tr>
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>Findings {#if findings.length === 0}<span class="badge badge-green">none</span>{/if}</h2>
		{#if findings.length}
			<table>
				<thead><tr><th>When</th><th>Severity</th><th>What</th><th>Account</th><th>His side</th><th>Amount</th><th>Route</th></tr></thead>
				<tbody>
					{#each findings as f}
						<tr>
							<td>{when(f.ts)}{#if f.retro} <span class="text-muted">(history)</span>{/if}</td>
							<td>{#if f.severity}<span class="badge {SEV_BADGE[f.severity]}">{f.severity}</span>{:else}<span class="text-muted">recorded</span>{/if}</td>
							<td>{f.label}{#if f.hop} · hop {f.hop}{/if}</td>
							<td><Addr address={f.account} /></td>
							<td><Addr address={f.other} /> <span class="text-muted">{f.role || ''}</span></td>
							<td>{f.usd == null ? 'unpriced' : formatUSD(f.usd)}</td>
							<td>{f.route || '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>

	<section class="card">
		<h2>Where new money came from</h2>
		<table>
			<thead><tr><th>Account</th><th>Verdict</th><th>Why queued</th><th>Routes</th><th>Entered</th><th>First sources</th></tr></thead>
			<tbody>
				{#each recent as r}
					<tr>
						<td><Addr address={r.account} /></td>
						<td><span class="badge {VERDICT_BADGE[r.verdict] || 'badge-grey'}">{r.verdictLabel}</span></td>
						<td>{r.reason || '—'}</td>
						<td>{r.routes.join(', ')}</td>
						<td>{formatUSD(r.usd)}</td>
						<td>{#each r.hop1 as s}<div><Addr address={s.address} /> <span class="text-muted">{s.class}{s.role ? ' · ' + s.role : ''}</span></div>{/each}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>His world</h2>
		<table>
			<thead><tr><th>Address</th><th>Role</th><th>Why</th><th>On Hyperliquid</th></tr></thead>
			<tbody>
				{#each members as m}
					<tr>
						<td><Addr address={m.address} /></td>
						<td>{m.role}</td>
						<td>{m.why}</td>
						<td>{m.active == null ? 'not read' : m.active ? 'active' : 'no'}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>Where his money stops</h2>
		<table>
			<thead><tr><th>Address</th><th>Class</th><th>His money in</th></tr></thead>
			<tbody>
				{#each stops as s}
					<tr><td><Addr address={s.address} /></td><td>{s.class}</td><td>{formatUSD(s.in_usd)}</td></tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>Measured, and no</h2>
		<ul>
			<li>All 412 large L1 counterparties of his three wallets asked of Hyperliquid (2026-10-06): 16 exist there, none trades.</li>
			<li>All 146 Bridge2 payouts to the target and all 4 to the treasury were their own withdrawals.</li>
			<li>Unit: no operations for his wallets, his Solana wallet or his Binance deposit address.</li>
		</ul>
	</section>
{/if}
```

- [ ] **Step 5: Nav, icon, transfers label**

`Icon.svelte` PATHS: `trace: 'M3 6h4l3 12h4l3-12h4M10 18h4'`. In `+layout.svelte`, add `{ href: \`${base}/trace\`, label: 'Trace', icon: 'trace' },` after Recovery under "Hunt". In `transfers/+page.svelte`, add `fetchJSON` to the import, load `const per = await fetchJSON('data/perimeter/latest.json')` in `onMount`, compute `$: core = new Set(Object.values(per?.members || {}).filter((m) => m.role === 'core').map((m) => m.address))`, and wherever the node's class label renders, render `Known wallet (config)` with `badge-grey` when `core.has(node.wallet)`.

- [ ] **Step 6: Run tests and build**

Run: `cd dashboard && npm test && npm run build`
Expected: tests PASS; build succeeds with no Svelte errors.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src
git commit -m "feat(dashboard): Trace page - findings, provenance verdicts, his world, where money stops"
```

---

### Task 17: Verify on production data, document, ship

**Files:**
- Modify: `CLAUDE.md` (vector table rows; "Where the hunt stands"), `docs/incident-log.md` (entry), `docs/superpowers/specs/2026-10-06-boundary-trace-design.md` (status line)

- [ ] **Step 1: Full suite and lint**

Run: `.venv/Scripts/python.exe -m ruff check src/ tests/ scripts/` then `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: ruff clean; all tests pass (baseline 2,074 + new).

- [ ] **Step 2: Read-only dry runs on production state** (scratch dir `$SP/dry`)

```bash
python scripts/build_perimeter.py --dry-run "$SP/dry/perimeter"
python scripts/check_boundary.py --dry-run "$SP/dry/boundary" --perimeter "$SP/dry/perimeter/latest.json"
python scripts/run_provenance.py --dry-run "$SP/dry/provenance" --perimeter "$SP/dry/perimeter/latest.json"
```

Record for each: elapsed, members by role, withdrawals read, findings (expect retro only; the 146 target and 4 treasury withdrawals all self), provenance verdicts for `0xdd53c529…`, `0x5b5d5120…`, `0xb83de012…`, `0xd4758770…`. Then measure the correlator's exits before/after offline (`collect_target_exits` count and $ against the 1,563 / $3.39B baseline). Any defect found → fix test-first before continuing.

- [ ] **Step 3: Docs**

CLAUDE.md vector table — add rows (one line each, no new sections):
`| Boundary attribution | src/boundary/attribution.py, scripts/check_boundary.py (watch.yml) | Bridge2's FinalizedWithdrawal names the account that withdrew and the destination, globally and keyless; plus each core account's whole history (topic1), Unit per member, core HL sends, retro payouts. An outside account paying his wallet or deposit address is CRITICAL; Circle's forwarder-burned withdrawals are attributed through the USDC system ledger |`
`| Funding provenance | src/boundary/provenance.py, scripts/run_provenance.py (trace.yml) | Large new money into any account traced back ≤ 2 hops to its first boundary and joined against the perimeter; owns the complete Bridge2 deposit pool |`
`| Perimeter | src/boundary/perimeter.py, scripts/build_perimeter.py (trace.yml) | His world as one table of measured roles (core, deposit, identity, sink, funder, associate), each put to HL |`
Update the Amount-correlation row: exits are custody-gap only; matches obey route physics.
"Where the hunt stands": add the depth-1 measured no (412 asked, 0 trade) and the funding-route split.

incident-log entry "Tracing measured, then rebuilt around Hyperliquid's edge (2026-10-06)": the §1 table, the Circle forwarder finding (latent false CRITICAL), the dry-run numbers, and the rule: **index the edge, not the graph — a table joined against a global feed scales; a forward walk does not.**

Check `CLAUDE.md` stays under 150,000 characters: `python -c "print(len(open('CLAUDE.md', encoding='utf-8').read()))"`.

- [ ] **Step 4: Ship**

```bash
git push -u origin feat/boundary-trace
gh pr create --title "Boundary tracing: attribute and trace every crossing of Hyperliquid's edge" \n  --body-file "$SP/pr_body.md"   # written in this step: what changed, the measurements, the dry-run numbers, the test plan
```

Wait for `test.yml` to pass; merge; dispatch `watch.yml` and `trace.yml` (`gh workflow run`), and verify on `main`: `data/boundary/latest.json`, `data/perimeter/latest.json`, `data/provenance/latest.json` written with sane counts, no unexpected alert, both runs green. Record the live numbers in the incident log entry and in memory.

---

## Self-Review

**Spec coverage.** §3 architecture → Tasks 1–12; §4 perimeter → Tasks 4, 8; §5 feeds → Tasks 1–3, 9, 12; §6 attribution (rules, retro, baseline) → Tasks 5, 9, 10; §7 provenance → Tasks 6, 11, 12; §8 correlator → Task 13; §9 dashboard → Task 16; §10 health/alerts/roster → Tasks 7, 14, 15; §11 small fixes → Tasks 13 (nonce), 14 (route index); §12 acceptance → tests across tasks + Task 17 dry runs; Circle forwarder attribution (found during planning) → Task 10.

**Type consistency.** Event shape is defined once (`attribution.event`) and produced by Unit (Task 3), Bridge2 (Task 5) and HL edges (Task 5); findings carry `key`, `severity`, `vote`, `role`, `member`; provenance findings use `account` (roster subject) and attribution findings `hl_account` — Task 14 reads each by its own subject. `perimeter.Index.get(address, raw)` and `.core`/`.families` are used identically in Tasks 5, 9, 11, 12, 13. `measured()` returns three callables from Task 12 on; Task 8's caller is updated in Task 12.

**Review Focus coverage.** Hub airdrops → Task 5 `test_hl_edges_become_events_for_the_other_account_and_skip_hubs` and Task 9 `test_core_ledger_sends_from_outside_accounts_count_but_hubs_do_not`; full page split block → Task 1; error-with-empty-result → Task 1; missing perimeter → Task 9; partial provenance in correlator → Task 13 `route_unknown` cases.


