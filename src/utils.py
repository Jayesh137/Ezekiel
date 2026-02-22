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

def hl_post(request_body: dict, retries: int = 3) -> dict | list:
    """POST to Hyperliquid info endpoint with retry on rate limit."""
    config = load_config()
    for attempt in range(retries):
        resp = requests.post(
            config["hyperliquid_api"],
            json=request_body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)
            print(f"[api] Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
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
                       "account", "spot", "portfolio", "scans", "l1_transactions"]:
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
