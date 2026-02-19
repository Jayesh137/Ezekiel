# Ezekiel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a complete trader intelligence system that logs all Hyperliquid wallet activity, builds a behavioral fingerprint, traces fund flows, scans for matching wallets, monitors Twitter, and presents everything in a dashboard.

**Architecture:** Python backend scripts run on GitHub Actions (cron). Data stored as JSON in git. SvelteKit dashboard on GitHub Pages reads data via raw GitHub URLs. Twitter monitored via RSS bridges.

**Tech Stack:** Python 3.12, requests, numpy, scipy, python-docx, PyPDF2 | SvelteKit 2.x, Svelte 5, Vite 6, Chart.js, adapter-static | GitHub Actions, GitHub Pages, Brevo SMTP, Etherscan V2 API

---

## Phase 1: Foundation & Data Collection (URGENT)

> **Priority: CRITICAL.** Hyperliquid only keeps ~2000 recent fills. Every minute we delay, historical data may be expiring. Get collection running ASAP.

---

### Task 1: Project Initialization

**Files:**
- Create: `config.json`
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `data/state/.gitkeep`
- Create: `data/positions/.gitkeep`
- Create: `data/fills/.gitkeep`
- Create: `data/orders/.gitkeep`
- Create: `data/funding/.gitkeep`
- Create: `data/ledger/.gitkeep`
- Create: `data/account/.gitkeep`
- Create: `data/subaccounts/.gitkeep`
- Create: `data/vaults/.gitkeep`
- Create: `data/fees/.gitkeep`
- Create: `data/referral/.gitkeep`
- Create: `data/rate_limit/.gitkeep`
- Create: `data/l1_transactions/.gitkeep`
- Create: `data/scans/.gitkeep`
- Create: `data/twitter/tweets/.gitkeep`
- Create: `data/twitter/archive/.gitkeep`
- Create: `data/twitter/correlation/.gitkeep`
- Create: `profile/.gitkeep`
- Create: `reports/daily/.gitkeep`

**Step 1: Initialize git repo**

```bash
cd /mnt/c/Users/jayes/Documents/Ezekiel
git init
```

**Step 2: Create .gitignore**

```
__pycache__/
*.pyc
.env
node_modules/
dashboard/build/
dashboard/.svelte-kit/
.DS_Store
*.swp
```

**Step 3: Create config.json**

```json
{
  "target_wallet": "0x45d26f28196d226497130c4bac709d808fed4029",
  "trader_codename": "Ezekiel",
  "hyperliquid_api": "https://api.hyperliquid.xyz/info",
  "leaderboard_url": "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
  "etherscan_v2_base": "https://api.etherscan.io/v2/api",
  "arbitrum_chain_id": 42161,
  "hl_bridge_contract": "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7",
  "usdc_contract_arbitrum": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
  "twitter_accounts": ["GiganticRebirth", "GCRClassic"],
  "alert_thresholds": {
    "similarity_high": 0.85,
    "similarity_medium": 0.70,
    "similarity_low": 0.55
  },
  "scanner": {
    "max_leaderboard_wallets": 500,
    "fills_lookback_days": 7,
    "min_fills_for_comparison": 20
  }
}
```

**Step 4: Create requirements.txt**

```
requests>=2.31.0
numpy>=1.26.0
scipy>=1.12.0
python-docx>=1.1.0
PyPDF2>=3.0.0
feedparser>=6.0.0
```

**Step 5: Create all data directories with .gitkeep files**

```bash
mkdir -p data/{state,positions,fills,orders,funding,ledger,account,subaccounts,vaults,fees,referral,rate_limit,l1_transactions,scans,twitter/tweets,twitter/archive,twitter/correlation} profile reports/daily
touch data/state/.gitkeep data/positions/.gitkeep data/fills/.gitkeep data/orders/.gitkeep data/funding/.gitkeep data/ledger/.gitkeep data/account/.gitkeep data/subaccounts/.gitkeep data/vaults/.gitkeep data/fees/.gitkeep data/referral/.gitkeep data/rate_limit/.gitkeep data/l1_transactions/.gitkeep data/scans/.gitkeep data/twitter/tweets/.gitkeep data/twitter/archive/.gitkeep data/twitter/correlation/.gitkeep profile/.gitkeep reports/daily/.gitkeep
```

**Step 6: Commit**

```bash
git add -A
git commit -m "init: project structure, config, and dependencies"
```

---

### Task 2: Core Utilities (utils.py)

**Files:**
- Create: `src/utils.py`
- Create: `tests/test_utils.py`

**Step 1: Write tests for core utility functions**

```python
# tests/test_utils.py
import json
import os
import tempfile
import pytest
from src.utils import (
    read_cursor, write_cursor, append_records,
    load_all_records, deduplicate_by_key, today_str, now_hhmm
)

def test_read_cursor_missing():
    assert read_cursor("nonexistent", base="/tmp/test_state") == 0

def test_write_and_read_cursor():
    with tempfile.TemporaryDirectory() as d:
        write_cursor("test_cursor", 1700000000000, base=d)
        assert read_cursor("test_cursor", base=d) == 1700000000000

def test_append_records_dedup():
    with tempfile.TemporaryDirectory() as d:
        records = [
            {"hash": "0xaaa", "coin": "BTC"},
            {"hash": "0xbbb", "coin": "ETH"},
        ]
        added = append_records(d, records, key_field="hash")
        assert added == 2

        # Append again with one duplicate and one new
        records2 = [
            {"hash": "0xaaa", "coin": "BTC"},  # duplicate
            {"hash": "0xccc", "coin": "SOL"},   # new
        ]
        added2 = append_records(d, records2, key_field="hash")
        assert added2 == 1

        # Verify total
        all_records = load_all_records(d)
        assert len(all_records) == 3

def test_deduplicate_by_key():
    records = [
        {"id": 1, "val": "a"},
        {"id": 2, "val": "b"},
        {"id": 1, "val": "a"},  # dup
    ]
    deduped = deduplicate_by_key(records, "id")
    assert len(deduped) == 2

def test_today_str_format():
    s = today_str()
    assert len(s) == 10  # YYYY-MM-DD
    assert s[4] == "-" and s[7] == "-"

def test_now_hhmm_format():
    s = now_hhmm()
    assert len(s) == 5  # HH-MM
    assert s[2] == "-"
```

**Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/Users/jayes/Documents/Ezekiel
python -m pytest tests/test_utils.py -v
```
Expected: FAIL (module not found)

**Step 3: Implement utils.py**

```python
# src/utils.py
"""Core utilities for the Ezekiel trader intelligence system."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config.json"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

# --- Hyperliquid API ---

def hl_post(request_body: dict) -> dict | list:
    """POST to Hyperliquid info endpoint."""
    config = load_config()
    resp = requests.post(
        config["hyperliquid_api"],
        json=request_body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

# --- Etherscan V2 API ---

def etherscan_get(params: dict) -> dict:
    """GET from Etherscan V2 API for Arbitrum."""
    config = load_config()
    base_params = {
        "chainid": config["arbitrum_chain_id"],
        "apikey": os.environ.get("ETHERSCAN_API_KEY", ""),
    }
    base_params.update(params)
    time.sleep(0.25)  # Rate limit: 5 req/sec
    resp = requests.get(
        config["etherscan_v2_base"],
        params=base_params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

# --- Cursor Management ---

def read_cursor(name: str, base: str | None = None) -> int:
    """Read a timestamp cursor. Returns 0 if file doesn't exist."""
    base_path = Path(base) if base else DATA_DIR / "state"
    cursor_file = base_path / f"{name}.txt"
    if cursor_file.exists():
        return int(cursor_file.read_text().strip())
    return 0

def write_cursor(name: str, value: int, base: str | None = None) -> None:
    """Write a timestamp cursor."""
    base_path = Path(base) if base else DATA_DIR / "state"
    base_path.mkdir(parents=True, exist_ok=True)
    cursor_file = base_path / f"{name}.txt"
    cursor_file.write_text(str(value))

# --- Date Helpers ---

def today_str() -> str:
    """Return today's date as YYYY-MM-DD in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def now_hhmm() -> str:
    """Return current time as HH-MM in UTC."""
    return datetime.now(timezone.utc).strftime("%H-%M")

def now_ms() -> int:
    """Return current time as Unix milliseconds."""
    return int(time.time() * 1000)

# --- File I/O ---

def deduplicate_by_key(records: list[dict], key_field: str) -> list[dict]:
    """Remove duplicates from a list of dicts based on a key field."""
    seen = set()
    result = []
    for r in records:
        key = str(r.get(key_field, ""))
        if key and key not in seen:
            seen.add(key)
            result.append(r)
    return result

def append_records(directory: str, records: list[dict], key_field: str) -> int:
    """Append records to today's JSON file with deduplication. Returns count added."""
    if not records:
        return 0

    dir_path = Path(directory)
    dir_path.mkdir(parents=True, exist_ok=True)
    filepath = dir_path / f"{today_str()}.json"

    existing = []
    if filepath.exists():
        with open(filepath) as f:
            existing = json.load(f)

    existing_keys = {str(r.get(key_field, "")) for r in existing}
    new_records = [
        r for r in records
        if str(r.get(key_field, "")) not in existing_keys
    ]

    if new_records:
        combined = existing + new_records
        with open(filepath, "w") as f:
            json.dump(combined, f, indent=2)

    return len(new_records)

def save_snapshot(directory: str, data: dict | list) -> str:
    """Save a timestamped snapshot. Returns the filepath."""
    dir_path = Path(directory) / today_str()
    dir_path.mkdir(parents=True, exist_ok=True)
    filepath = dir_path / f"{now_hhmm()}.json"
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    return str(filepath)

def save_latest(directory: str, data: dict | list) -> str:
    """Save data as latest.json, overwriting previous."""
    dir_path = Path(directory)
    dir_path.mkdir(parents=True, exist_ok=True)
    filepath = dir_path / "latest.json"
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    return str(filepath)

def load_all_records(directory: str) -> list[dict]:
    """Load and merge all JSON files in a directory (daily files)."""
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    all_records = []
    for filepath in sorted(dir_path.glob("*.json")):
        if filepath.name == "latest.json":
            continue
        with open(filepath) as f:
            data = json.load(f)
            if isinstance(data, list):
                all_records.extend(data)
    return all_records

def update_index() -> None:
    """Update data/index.json with manifest of all available data files."""
    index = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "wallet": load_config()["target_wallet"],
        "files": {},
        "stats": {},
    }
    for data_type in ["positions", "fills", "orders", "funding", "ledger",
                       "account", "scans", "l1_transactions"]:
        type_dir = DATA_DIR / data_type
        if type_dir.exists():
            dates = sorted([
                f.stem for f in type_dir.glob("*.json")
                if f.name != "latest.json"
            ])
            index["files"][data_type] = dates
            if data_type in ["fills", "funding", "ledger"]:
                all_recs = load_all_records(str(type_dir))
                index["stats"][f"total_{data_type}"] = len(all_recs)

    index_path = DATA_DIR / "index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
```

**Step 4: Create __init__.py files for imports**

```bash
touch src/__init__.py tests/__init__.py
```

**Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_utils.py -v
```
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/utils.py src/__init__.py tests/test_utils.py tests/__init__.py
git commit -m "feat: core utilities — API helpers, cursors, file I/O, dedup"
```

---

### Task 3: Data Collector (collector.py)

**Files:**
- Create: `src/collector.py`

**Step 1: Implement the collector**

This is the most critical module. It polls all Hyperliquid endpoints and stores the results.

```python
# src/collector.py
"""Collects trading data from Hyperliquid API for the target wallet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, hl_post, read_cursor, write_cursor,
    append_records, save_snapshot, save_latest, update_index,
    now_ms, DATA_DIR
)


def collect_positions(wallet: str) -> None:
    """Snapshot current positions and account state."""
    state = hl_post({"type": "clearinghouseState", "user": wallet})
    save_snapshot(str(DATA_DIR / "positions"), state)
    save_latest(str(DATA_DIR / "positions"), state)

    # Also snapshot spot state
    spot = hl_post({"type": "spotClearinghouseState", "user": wallet})
    save_snapshot(str(DATA_DIR / "account"), {"perp": state, "spot": spot})
    save_latest(str(DATA_DIR / "account"), {"perp": state, "spot": spot})


def collect_fills(wallet: str) -> int:
    """Collect new fills since last cursor. Returns count of new fills."""
    last_ts = read_cursor("last_fill_time")
    start = last_ts + 1 if last_ts else 0

    body = {"type": "userFillsByTime", "user": wallet, "startTime": start}
    fills = hl_post(body)

    if not fills:
        return 0

    added = append_records(str(DATA_DIR / "fills"), fills, key_field="hash")

    # Update cursor to the latest fill timestamp
    max_ts = max(f["time"] for f in fills)
    write_cursor("last_fill_time", max_ts)

    return added


def collect_orders(wallet: str) -> None:
    """Collect open orders and recent historical orders."""
    # Open orders
    open_orders = hl_post({"type": "openOrders", "user": wallet})
    save_latest(str(DATA_DIR / "orders"), {"open": open_orders})

    # Frontend open orders (has more detail)
    frontend_orders = hl_post({"type": "frontendOpenOrders", "user": wallet})
    save_snapshot(str(DATA_DIR / "orders"), {
        "open": open_orders,
        "frontend": frontend_orders,
    })

    # Historical orders
    historical = hl_post({"type": "historicalOrders", "user": wallet})
    append_records(
        str(DATA_DIR / "orders"),
        [{"oid": o["order"]["oid"], **o} for o in historical],
        key_field="oid",
    )


def collect_funding(wallet: str) -> int:
    """Collect new funding payments since last cursor."""
    last_ts = read_cursor("last_funding_time")
    start = last_ts + 1 if last_ts else 0

    body = {"type": "userFunding", "user": wallet, "startTime": start}
    funding = hl_post(body)

    if not funding:
        return 0

    added = append_records(str(DATA_DIR / "funding"), funding, key_field="hash")

    max_ts = max(f["time"] for f in funding)
    write_cursor("last_funding_time", max_ts)

    return added


def collect_ledger(wallet: str) -> int:
    """Collect non-funding ledger updates (deposits, withdrawals, transfers)."""
    last_ts = read_cursor("last_ledger_time")
    start = last_ts + 1 if last_ts else 0

    body = {"type": "userNonFundingLedgerUpdates", "user": wallet, "startTime": start}
    ledger = hl_post(body)

    if not ledger:
        return 0

    added = append_records(str(DATA_DIR / "ledger"), ledger, key_field="hash")

    max_ts = max(e["time"] for e in ledger)
    write_cursor("last_ledger_time", max_ts)

    return added


def collect_fees(wallet: str) -> None:
    """Collect fee schedule and rate info."""
    fees = hl_post({"type": "userFees", "user": wallet})
    save_latest(str(DATA_DIR / "fees"), fees)
    append_records(str(DATA_DIR / "fees"), [{"_ts": now_ms(), **fees}], key_field="_ts")


def collect_rate_limit(wallet: str) -> None:
    """Collect rate limit / cumulative volume info."""
    rl = hl_post({"type": "userRateLimit", "user": wallet})
    save_latest(str(DATA_DIR / "rate_limit"), rl)
    append_records(str(DATA_DIR / "rate_limit"), [{"_ts": now_ms(), **rl}], key_field="_ts")


def collect_subaccounts(wallet: str) -> None:
    """Check for subaccounts (directly reveals linked wallets)."""
    subs = hl_post({"type": "subAccounts", "user": wallet})
    save_latest(str(DATA_DIR / "subaccounts"), subs)


def collect_vault_equities(wallet: str) -> None:
    """Check vault deposits."""
    vaults = hl_post({"type": "userVaultEquities", "user": wallet})
    save_latest(str(DATA_DIR / "vaults"), vaults)


def collect_referral(wallet: str) -> None:
    """Collect referral chain data."""
    ref = hl_post({"type": "referral", "user": wallet})
    save_latest(str(DATA_DIR / "referral"), ref)


def collect_portfolio(wallet: str) -> None:
    """Collect portfolio (historical account value + PnL)."""
    portfolio = hl_post({"type": "portfolio", "user": wallet})
    save_latest(str(DATA_DIR / "account"), portfolio)


def main():
    config = load_config()
    wallet = config["target_wallet"]

    print(f"[collector] Starting collection for {wallet}")

    # Every 5 min: positions, fills, orders, account
    print("[collector] Collecting positions...")
    collect_positions(wallet)

    print("[collector] Collecting fills...")
    new_fills = collect_fills(wallet)
    print(f"[collector] {new_fills} new fills")

    print("[collector] Collecting orders...")
    collect_orders(wallet)

    # Every 15 min: funding, ledger, fees, rate limit
    print("[collector] Collecting funding...")
    new_funding = collect_funding(wallet)
    print(f"[collector] {new_funding} new funding events")

    print("[collector] Collecting ledger...")
    new_ledger = collect_ledger(wallet)
    print(f"[collector] {new_ledger} new ledger events")

    print("[collector] Collecting fees...")
    collect_fees(wallet)

    print("[collector] Collecting rate limit...")
    collect_rate_limit(wallet)

    # Every hour: subaccounts, vaults, referral, portfolio
    print("[collector] Collecting subaccounts...")
    collect_subaccounts(wallet)

    print("[collector] Collecting vault equities...")
    collect_vault_equities(wallet)

    print("[collector] Collecting referral data...")
    collect_referral(wallet)

    print("[collector] Collecting portfolio...")
    collect_portfolio(wallet)

    # Update index
    print("[collector] Updating index...")
    update_index()

    print("[collector] Collection complete.")


if __name__ == "__main__":
    main()
```

**Step 2: Run collector locally to verify it works**

```bash
python src/collector.py
```
Expected: Data files created in data/ directories. Verify with `ls data/fills/ data/positions/`

**Step 3: Commit**

```bash
git add src/collector.py
git commit -m "feat: data collector — polls all 14 Hyperliquid endpoints"
```

---

### Task 4: Historical Backfill (backfill.py)

**Files:**
- Create: `src/backfill.py`

**Step 1: Implement the backfill script**

Paginates through all historical data using time windows.

```python
# src/backfill.py
"""One-time historical backfill — grabs ALL available data before it expires."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, hl_post, append_records, write_cursor,
    save_latest, update_index, DATA_DIR
)


def backfill_fills(wallet: str) -> int:
    """Backfill all available fills using time-windowed pagination."""
    total = 0
    # Start from epoch, move forward in 24h windows
    window_ms = 24 * 60 * 60 * 1000  # 24 hours
    start_time = 0
    max_time = int(time.time() * 1000)

    print(f"[backfill] Backfilling fills from epoch to now...")

    while start_time < max_time:
        end_time = min(start_time + window_ms, max_time)
        body = {
            "type": "userFillsByTime",
            "user": wallet,
            "startTime": start_time,
            "endTime": end_time,
        }
        fills = hl_post(body)

        if fills:
            added = append_records(str(DATA_DIR / "fills"), fills, key_field="hash")
            total += added
            print(f"[backfill]   window {start_time} -> {end_time}: {len(fills)} fills ({added} new)")

            # If we got exactly 2000 (max), there might be more in this window
            # Use the last fill's timestamp as the new start
            if len(fills) >= 2000:
                last_ts = max(f["time"] for f in fills)
                start_time = last_ts + 1
                continue

        start_time = end_time + 1
        time.sleep(0.1)  # Be gentle on the API

    # Update cursor to latest fill
    all_fills = []
    for fp in sorted((DATA_DIR / "fills").glob("*.json")):
        if fp.name == "latest.json":
            continue
        import json
        with open(fp) as f:
            all_fills.extend(json.load(f))

    if all_fills:
        max_ts = max(f["time"] for f in all_fills)
        write_cursor("last_fill_time", max_ts)

    return total


def backfill_funding(wallet: str) -> int:
    """Backfill all funding payments using 7-day windows."""
    total = 0
    window_ms = 7 * 24 * 60 * 60 * 1000  # 7 days
    start_time = 0
    max_time = int(time.time() * 1000)

    print(f"[backfill] Backfilling funding from epoch to now...")

    while start_time < max_time:
        end_time = min(start_time + window_ms, max_time)
        body = {
            "type": "userFunding",
            "user": wallet,
            "startTime": start_time,
            "endTime": end_time,
        }
        funding = hl_post(body)

        if funding:
            added = append_records(str(DATA_DIR / "funding"), funding, key_field="hash")
            total += added

            if len(funding) >= 500:
                last_ts = max(f["time"] for f in funding)
                start_time = last_ts + 1
                continue

        start_time = end_time + 1
        time.sleep(0.1)

    if total > 0:
        import json
        all_funding = []
        for fp in sorted((DATA_DIR / "funding").glob("*.json")):
            if fp.name == "latest.json":
                continue
            with open(fp) as f:
                all_funding.extend(json.load(f))
        if all_funding:
            write_cursor("last_funding_time", max(f["time"] for f in all_funding))

    return total


def backfill_ledger(wallet: str) -> int:
    """Backfill all non-funding ledger updates (deposits, withdrawals, transfers)."""
    total = 0
    window_ms = 7 * 24 * 60 * 60 * 1000
    start_time = 0
    max_time = int(time.time() * 1000)

    print(f"[backfill] Backfilling ledger from epoch to now...")

    while start_time < max_time:
        end_time = min(start_time + window_ms, max_time)
        body = {
            "type": "userNonFundingLedgerUpdates",
            "user": wallet,
            "startTime": start_time,
            "endTime": end_time,
        }
        ledger = hl_post(body)

        if ledger:
            added = append_records(str(DATA_DIR / "ledger"), ledger, key_field="hash")
            total += added

            if len(ledger) >= 500:
                last_ts = max(e["time"] for e in ledger)
                start_time = last_ts + 1
                continue

        start_time = end_time + 1
        time.sleep(0.1)

    if total > 0:
        import json
        all_ledger = []
        for fp in sorted((DATA_DIR / "ledger").glob("*.json")):
            if fp.name == "latest.json":
                continue
            with open(fp) as f:
                all_ledger.extend(json.load(f))
        if all_ledger:
            write_cursor("last_ledger_time", max(e["time"] for e in all_ledger))

    return total


def backfill_orders(wallet: str) -> int:
    """Capture historical orders (limited to last 2000)."""
    print("[backfill] Capturing historical orders (last 2000)...")
    historical = hl_post({"type": "historicalOrders", "user": wallet})
    added = append_records(
        str(DATA_DIR / "orders"),
        [{"oid": o["order"]["oid"], **o} for o in historical],
        key_field="oid",
    )
    return added


def backfill_current_state(wallet: str) -> None:
    """Snapshot all current state endpoints."""
    print("[backfill] Capturing current state snapshots...")

    state = hl_post({"type": "clearinghouseState", "user": wallet})
    save_latest(str(DATA_DIR / "positions"), state)

    spot = hl_post({"type": "spotClearinghouseState", "user": wallet})
    save_latest(str(DATA_DIR / "account"), {"perp": state, "spot": spot})

    for endpoint, directory in [
        ("subAccounts", "subaccounts"),
        ("userVaultEquities", "vaults"),
        ("referral", "referral"),
        ("userFees", "fees"),
        ("userRateLimit", "rate_limit"),
        ("portfolio", "account"),
    ]:
        data = hl_post({"type": endpoint, "user": wallet})
        save_latest(str(DATA_DIR / directory), data)
        print(f"[backfill]   {endpoint}: saved")


def main():
    config = load_config()
    wallet = config["target_wallet"]

    print(f"[backfill] === HISTORICAL BACKFILL for {wallet} ===")
    print(f"[backfill] This captures ALL available data before it expires.")
    print()

    total_fills = backfill_fills(wallet)
    print(f"[backfill] Total fills captured: {total_fills}")
    print()

    total_funding = backfill_funding(wallet)
    print(f"[backfill] Total funding events captured: {total_funding}")
    print()

    total_ledger = backfill_ledger(wallet)
    print(f"[backfill] Total ledger events captured: {total_ledger}")
    print()

    total_orders = backfill_orders(wallet)
    print(f"[backfill] Total historical orders captured: {total_orders}")
    print()

    backfill_current_state(wallet)

    update_index()

    print()
    print(f"[backfill] === BACKFILL COMPLETE ===")
    print(f"[backfill] Fills: {total_fills}")
    print(f"[backfill] Funding: {total_funding}")
    print(f"[backfill] Ledger: {total_ledger}")
    print(f"[backfill] Orders: {total_orders}")


if __name__ == "__main__":
    main()
```

**Step 2: Run backfill locally to verify**

```bash
python src/backfill.py
```
Expected: Historical data pulled and stored. This may take several minutes.

**Step 3: Commit**

```bash
git add src/backfill.py
git commit -m "feat: historical backfill — paginated pull of all available data"
```

---

### Task 5: GitHub Actions Workflows (Deploy Collection 24/7)

**Files:**
- Create: `.github/workflows/collect.yml`
- Create: `.github/workflows/backfill.yml`

**Step 1: Create collect.yml**

```yaml
name: Collect Trading Data
on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:

jobs:
  collect:
    runs-on: ubuntu-latest
    timeout-minutes: 4
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/collector.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "data: collect trading data [automated]"
          file_pattern: "data/**"
```

**Step 2: Create backfill.yml**

```yaml
name: Historical Backfill
on:
  workflow_dispatch:

jobs:
  backfill:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/backfill.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "data: historical backfill [automated]"
          file_pattern: "data/**"
```

**Step 3: Commit and push to GitHub**

```bash
git add .github/
git commit -m "ci: GitHub Actions — collect every 5 min + manual backfill"
```

**Step 4: Create GitHub repo and push**

```bash
gh repo create Ezekiel --public --source=. --push
```

**Step 5: Trigger backfill manually**

Go to GitHub repo > Actions > "Historical Backfill" > "Run workflow"

**Step 6: Verify collection is running**

Wait 5-10 minutes, check Actions tab for "Collect Trading Data" runs.

---

## Phase 2: Fund Tracing & Alerts

---

### Task 6: Email Alerts (alerts.py)

**Files:**
- Create: `src/alerts.py`

**Step 1: Implement email alert system**

Uses Python stdlib `smtplib` + Brevo SMTP. Falls back to printing if SMTP credentials aren't set (for local testing).

```python
# src/alerts.py
"""Email alert system via Brevo SMTP."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_alert(subject: str, body: str, html_body: str | None = None) -> bool:
    """Send an email alert. Returns True if sent, False if skipped/failed."""
    smtp_key = os.environ.get("BREVO_SMTP_KEY")
    alert_email = os.environ.get("ALERT_EMAIL")

    if not smtp_key or not alert_email:
        print(f"[alerts] SMTP not configured. Alert: {subject}")
        print(f"[alerts] {body[:200]}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "Ezekiel Alerts <ezekiel@alerts.dev>"
    msg["To"] = alert_email

    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as server:
            server.starttls()
            server.login("apikey", smtp_key)
            server.sendmail(msg["From"], [alert_email], msg.as_string())
        print(f"[alerts] Sent: {subject}")
        return True
    except Exception as e:
        print(f"[alerts] Failed to send: {e}")
        return False


def alert_fund_movement(wallet: str, amount: str, destination: str, tx_hash: str) -> bool:
    subject = "[EZEKIEL] CRITICAL: Fund Movement Detected"
    body = (
        f"Wallet: {wallet}\n"
        f"Event: Withdrawal of {amount} USDC\n"
        f"Destination: {destination}\n"
        f"TX Hash: {tx_hash}\n"
        f"\nTracing destination wallet..."
    )
    return send_alert(subject, body)


def alert_new_wallet_found(source_wallet: str, new_wallet: str, method: str, confidence: float) -> bool:
    subject = f"[EZEKIEL] {'CRITICAL' if method == 'fund_trace' else 'HIGH'}: New Wallet Detected"
    body = (
        f"New Wallet: {new_wallet}\n"
        f"Detection Method: {method}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Source Wallet: {source_wallet}\n"
    )
    return send_alert(subject, body)


def alert_behavioral_match(candidate: str, score: float, dimensions: dict) -> bool:
    subject = f"[EZEKIEL] HIGH: Potential Ezekiel Wallet ({score:.0%} match)"
    dim_lines = "\n".join(
        f"  - {k}: {v:.2f}" for k, v in sorted(dimensions.items(), key=lambda x: -x[1])
    )
    body = (
        f"Candidate Wallet: {candidate}\n"
        f"Similarity Score: {score:.2f} / 1.00\n\n"
        f"Matching Dimensions:\n{dim_lines}\n"
    )
    return send_alert(subject, body)
```

**Step 2: Commit**

```bash
git add src/alerts.py
git commit -m "feat: email alerts via Brevo SMTP"
```

---

### Task 7: Fund Flow Tracer (tracer.py)

**Files:**
- Create: `src/tracer.py`

**Step 1: Implement the Arbitrum L1 fund flow tracer**

```python
# src/tracer.py
"""Traces fund flows on Arbitrum L1 to detect wallet migrations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, etherscan_get, read_cursor, write_cursor,
    append_records, save_latest, DATA_DIR
)
from src.alerts import alert_fund_movement, alert_new_wallet_found


def get_usdc_transfers(address: str, start_block: int = 0) -> list[dict]:
    """Get all USDC token transfers for an address on Arbitrum."""
    config = load_config()
    result = etherscan_get({
        "module": "account",
        "action": "tokentx",
        "address": address,
        "contractaddress": config["usdc_contract_arbitrum"],
        "startblock": start_block,
        "endblock": 99999999,
        "page": 1,
        "offset": 1000,
        "sort": "desc",
    })
    if result.get("status") == "1" and result.get("result"):
        return result["result"]
    return []


def get_normal_transactions(address: str, start_block: int = 0) -> list[dict]:
    """Get all normal transactions for an address on Arbitrum."""
    result = etherscan_get({
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "page": 1,
        "offset": 1000,
        "sort": "desc",
    })
    if result.get("status") == "1" and result.get("result"):
        return result["result"]
    return []


def check_if_hl_deposit(address: str) -> bool:
    """Check if an address has deposited to the Hyperliquid bridge."""
    config = load_config()
    transfers = get_usdc_transfers(address)
    bridge = config["hl_bridge_contract"].lower()
    return any(
        t["to"].lower() == bridge for t in transfers
    )


def trace_outbound_transfers(wallet: str) -> list[dict]:
    """Find USDC transfers OUT from the tracked wallet. Returns new transfers."""
    last_block = read_cursor("last_l1_block")
    transfers = get_usdc_transfers(wallet, start_block=last_block)

    if not transfers:
        return []

    # Filter: only outbound (from = our wallet)
    outbound = [
        t for t in transfers
        if t.get("from", "").lower() == wallet.lower()
    ]

    # Store all transfers
    append_records(str(DATA_DIR / "l1_transactions"), transfers, key_field="hash")

    # Update cursor
    if transfers:
        max_block = max(int(t.get("blockNumber", 0)) for t in transfers)
        write_cursor("last_l1_block", max_block)

    return outbound


def trace_fund_flow(wallet: str) -> None:
    """Main tracing logic: detect outbound transfers and follow the money."""
    config = load_config()
    print(f"[tracer] Checking fund flows for {wallet}")

    outbound = trace_outbound_transfers(wallet)

    if not outbound:
        print("[tracer] No new outbound transfers detected.")
        return

    for transfer in outbound:
        destination = transfer["to"]
        value_raw = int(transfer.get("value", 0))
        value_usdc = value_raw / 1e6  # USDC has 6 decimals
        tx_hash = transfer.get("hash", "unknown")

        print(f"[tracer] OUTBOUND: {value_usdc:.2f} USDC -> {destination}")

        # Alert on fund movement
        alert_fund_movement(wallet, f"{value_usdc:,.2f}", destination, tx_hash)

        # Check if destination deposited to Hyperliquid
        print(f"[tracer] Checking if {destination} deposited to Hyperliquid...")
        if check_if_hl_deposit(destination):
            print(f"[tracer] !!! NEW WALLET FOUND: {destination} deposited to HL !!!")
            alert_new_wallet_found(wallet, destination, "fund_trace", 1.0)

            # Save the finding
            finding = {
                "source": wallet,
                "destination": destination,
                "amount_usdc": value_usdc,
                "tx_hash": tx_hash,
                "method": "direct_fund_trace",
                "deposited_to_hl": True,
            }
            save_latest(str(DATA_DIR / "scans"), {"fund_trace_findings": [finding]})
        else:
            # Follow one more hop
            print(f"[tracer] Destination hasn't deposited to HL. Checking next hop...")
            next_transfers = get_usdc_transfers(destination)
            for nt in next_transfers[:5]:  # Check first 5 outbound from destination
                next_dest = nt["to"]
                if next_dest.lower() != destination.lower():
                    if check_if_hl_deposit(next_dest):
                        print(f"[tracer] !!! NEW WALLET FOUND (2-hop): {next_dest} !!!")
                        alert_new_wallet_found(wallet, next_dest, "fund_trace_2hop", 0.9)


def main():
    config = load_config()
    trace_fund_flow(config["target_wallet"])
    print("[tracer] Trace complete.")


if __name__ == "__main__":
    main()
```

**Step 2: Create trace.yml GitHub Actions workflow**

```yaml
# .github/workflows/trace.yml
name: Trace Fund Flows
on:
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch:

jobs:
  trace:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/tracer.py
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
          BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
          ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "data: trace fund flows [automated]"
          file_pattern: "data/**"
```

**Step 3: Commit**

```bash
git add src/tracer.py src/alerts.py .github/workflows/trace.yml
git commit -m "feat: fund flow tracer + email alerts"
```

---

## Phase 3: Intelligence & Analysis

---

### Task 8: Behavioral Fingerprint Builder (fingerprint.py)

**Files:**
- Create: `src/fingerprint.py`

This is the core intelligence module. It reads ALL collected data and computes the 10-dimension behavioral fingerprint. Implementation is complex — computes statistics across fills, positions, funding, and orders to build the fingerprint JSON as specified in PRD Section 7.

Key computations:
- Asset frequency distribution from fills
- Leverage profile from position snapshots
- Position sizing ratios from positions + account value
- Hourly/daily timing distribution from fill timestamps
- Hold duration from matching open/close fills
- Entry/exit style from order types, cancel rates
- Risk management from margin utilization, max drawdown
- Trade sequencing from fill ordering patterns
- Account characteristics from account value + volume

**Step 1: Implement fingerprint.py** (full implementation)
**Step 2: Create analyze.yml workflow**
**Step 3: Run locally to verify fingerprint.json is generated**
**Step 4: Commit**

---

### Task 9: Leaderboard Scanner (scanner.py)

**Files:**
- Create: `src/scanner.py`

Fetches leaderboard, queries each candidate wallet's data, computes similarity score against the Ezekiel fingerprint, flags matches.

**Step 1: Implement scanner.py**
**Step 2: Create scan.yml workflow**
**Step 3: Test with a small number of wallets**
**Step 4: Commit**

---

## Phase 4: Twitter Intelligence

---

### Task 10: Twitter Monitor (twitter_monitor.py)

**Files:**
- Create: `src/twitter_monitor.py`

Fetches tweets via RSS bridge (rss.app or nitter), extracts trading signals (mentioned coins, sentiment, direction), stores in `data/twitter/`.

**Step 1: Implement twitter_monitor.py**
**Step 2: Test with both accounts**
**Step 3: Commit**

---

### Task 11: Twitter Correlator (twitter_correlator.py)

**Files:**
- Create: `src/twitter_correlator.py`

Correlates tweet timestamps against fill timestamps. Computes timing correlation, direction correlation, outputs confidence score.

**Step 1: Implement twitter_correlator.py**
**Step 2: Test correlation logic with sample data**
**Step 3: Commit**

---

## Phase 5: Research Ingestion

---

### Task 12: Profile Builder (profile_builder.py)

**Files:**
- Create: `src/profile_builder.py`

Parses `research/GCR.docx` and `research/Trade Reviews.pdf`, extracts any trading patterns, strategies, personality traits. Outputs to `profile/trader_profile.json`.

**Step 1: Implement profile_builder.py**
**Step 2: Run against existing research files**
**Step 3: Commit**

---

## Phase 6: Dashboard (SvelteKit)

---

### Task 13: Dashboard Scaffold

**Step 1: Create SvelteKit project in dashboard/**

```bash
cd /mnt/c/Users/jayes/Documents/Ezekiel
npx sv create dashboard
# Select: Skeleton project, TypeScript, ESLint, Prettier
cd dashboard
npm install
npm install -D @sveltejs/adapter-static
npm install chart.js svelte-chartjs chartjs-adapter-date-fns
```

**Step 2: Configure adapter-static + GitHub Pages**
**Step 3: Create layout with dark theme + sidebar**
**Step 4: Create data fetching utility (lib/api.js)**
**Step 5: Commit**

---

### Task 14: Dashboard Pages

**Step 1: Home page** — Positions, account summary, recent fills
**Step 2: Fills page** — Sortable/filterable trade history table
**Step 3: Fingerprint page** — Radar chart + dimension detail cards
**Step 4: Scanner page** — Wallet match results table
**Step 5: Fund Flow page** — L1 transaction timeline
**Step 6: Twitter page** — Correlation analysis view
**Step 7: Reports page** — Daily report viewer
**Step 8: Deploy workflow (deploy-dashboard.yml)**
**Step 9: Commit and deploy**

---

## Implementation Order Summary

| Order | Task | Priority | Estimated Effort |
|-------|------|----------|-----------------|
| 1 | Project initialization | CRITICAL | 5 min |
| 2 | Core utilities (utils.py) | CRITICAL | 20 min |
| 3 | Data collector (collector.py) | CRITICAL | 15 min |
| 4 | Historical backfill (backfill.py) | CRITICAL | 15 min |
| 5 | GitHub Actions (collect + backfill) | CRITICAL | 10 min |
| 6 | Email alerts (alerts.py) | HIGH | 10 min |
| 7 | Fund flow tracer (tracer.py) | HIGH | 20 min |
| 8 | Fingerprint builder (fingerprint.py) | HIGH | 30 min |
| 9 | Leaderboard scanner (scanner.py) | HIGH | 25 min |
| 10 | Twitter monitor | MEDIUM | 15 min |
| 11 | Twitter correlator | MEDIUM | 20 min |
| 12 | Profile builder | MEDIUM | 15 min |
| 13 | Dashboard scaffold | MEDIUM | 15 min |
| 14 | Dashboard pages (7 pages) | MEDIUM | 60 min |

**Total: ~4-5 hours of implementation**
