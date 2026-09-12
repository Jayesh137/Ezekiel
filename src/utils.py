# src/utils.py
"""Core utilities for the Ezekiel trader intelligence system."""

import json
import math
import os
import time
from datetime import UTC, datetime
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
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.post(
                config["hyperliquid_api"],
                json=request_body,
                headers={"Content-Type": "application/json"},
                # (connect, read) — same reasoning as etherscan_get below.
                timeout=(10, 30),
            )
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"[api] Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            last_error = f"Timeout on attempt {attempt + 1}"
            print(f"[api] {last_error} for {request_body.get('type', 'unknown')}")
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            print(f"[api] Error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    print(f"[api] All {retries} attempts failed for {request_body.get('type', 'unknown')}: {last_error}")
    return [] if "user" in str(request_body.get("type", "")) else {}

# --- Etherscan V2 API ---

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
            # (connect, read), not one scalar doing both jobs. The frontier
            # slices its clock per lookup, but a slice cannot interrupt a
            # request already in flight — an internal budget is checked BETWEEN
            # calls — so this argument is the only bound on a stalled socket.
            # A connect that has not landed in 10s will not land.
            timeout=(10, 30),
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[etherscan] API error: {e}")
        return {"status": "0", "message": str(e), "result": []}

# --- Cursor Management ---

def cursor_filename(name: str) -> str:
    """A cursor name reduced to characters every filesystem accepts.

    Cursor names are built from alert keys, and an alert key is built from
    whatever describes the alert — on 2026-09-11 that included the kind
    `bridge:cctp->solana`, so CI wrote
    `data/state/alert_foreign_bridge:cctp->solana_0x1d24….txt`, committed it,
    and `git checkout main` then failed outright on Windows: `:` and `>` are
    not legal in an NTFS filename. A repository that cannot be checked out on
    the operator's own machine is a worse outcome than any alert.

    Collisions are not a concern here: the substituted characters are
    punctuation inside otherwise distinct keys, and a cursor is a timestamp
    watermark, not a claim about identity.
    """
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(name))
    return safe or "cursor"


def read_cursor(name: str, base: str | None = None) -> int:
    """Read a timestamp cursor. Returns 0 if file doesn't exist."""
    base_path = Path(base) if base else DATA_DIR / "state"
    cursor_file = base_path / f"{cursor_filename(name)}.txt"
    if cursor_file.exists():
        return int(cursor_file.read_text().strip())
    return 0

def write_cursor(name: str, value: int, base: str | None = None) -> None:
    """Write a timestamp cursor. See cursor_filename for why the name is sanitised."""
    base_path = Path(base) if base else DATA_DIR / "state"
    base_path.mkdir(parents=True, exist_ok=True)
    cursor_file = base_path / f"{cursor_filename(name)}.txt"
    cursor_file.write_text(str(value))


def read_cursor_text(name: str, base: str | None = None) -> str | None:
    """Read a cursor that holds text rather than a timestamp.

    Returns None when it has never been written, which callers must be able to
    tell from an empty string: "we have no baseline yet" and "the baseline is
    empty" lead to opposite decisions — the first is a first reading to seed,
    the second is a real state to diff against.
    """
    base_path = Path(base) if base else DATA_DIR / "state"
    cursor_file = base_path / f"{cursor_filename(name)}.txt"
    if cursor_file.exists():
        return cursor_file.read_text().strip()
    return None


def write_cursor_text(name: str, value: str, base: str | None = None) -> None:
    """Write a text cursor. See cursor_filename for why the name is sanitised."""
    base_path = Path(base) if base else DATA_DIR / "state"
    base_path.mkdir(parents=True, exist_ok=True)
    cursor_file = base_path / f"{cursor_filename(name)}.txt"
    cursor_file.write_text(str(value))

# --- Date Helpers ---

def today_str() -> str:
    """Return today's date as YYYY-MM-DD in UTC."""
    return datetime.now(UTC).strftime("%Y-%m-%d")

def now_hhmm() -> str:
    """Return current time as HH-MM in UTC."""
    return datetime.now(UTC).strftime("%H-%M")

def now_ms() -> int:
    """Return current time as Unix milliseconds."""
    return int(time.time() * 1000)

# --- Candidate records ---

def account_value_components(latest: dict | None) -> dict:
    """What the account is worth, split by where the value sits.

    `data/account/latest.json` holds `{"perp": ..., "spot": ..., "hip3": ...}`
    and the drawdown signal read only `perp.marginSummary.accountValue`. That
    is not the account: on 2026-09-11 the perp value fell from $29.4M to
    $14.2M and fired a "52% drop — possible liquidation" alert while the
    total was flat, because he had moved the money to spot and bridged $7M to
    HyperEVM. An alert that calls an internal transfer a liquidation is how an
    operator learns to ignore the one that is real.

    `total` is perp + every HIP-3 dex + spot USDC. Non-USDC spot tokens are
    counted separately and NOT in the total: pricing them needs a source this
    does not have, and a guess would move the number the drawdown threshold
    reads. Returns None for a component that could not be read, never 0.0.
    """
    def _val(state) -> float | None:
        if not isinstance(state, dict):
            return None
        try:
            return float((state.get("marginSummary") or {}).get("accountValue"))
        except (TypeError, ValueError):
            return None

    if not isinstance(latest, dict):
        return {"perp": None, "hip3": None, "spot_usdc": None, "total": None,
                "spot_other_tokens": 0}

    perp = _val(latest.get("perp", latest))
    hip3_states = latest.get("hip3") if isinstance(latest.get("hip3"), dict) else {}
    hip3_values = [v for v in (_val(s) for s in hip3_states.values()) if v is not None]
    hip3 = sum(hip3_values) if hip3_values else (0.0 if hip3_states == {} else None)

    spot_usdc, others = None, 0
    spot = latest.get("spot")
    if isinstance(spot, dict) and isinstance(spot.get("balances"), list):
        spot_usdc = 0.0
        for b in spot["balances"]:
            if not isinstance(b, dict):
                continue
            try:
                total = float(b.get("total") or 0)
            except (TypeError, ValueError):
                continue
            if total <= 0:
                continue
            if str(b.get("coin", "")).upper() == "USDC":
                spot_usdc += total
            else:
                others += 1

    parts = [p for p in (perp, hip3, spot_usdc) if p is not None]
    return {"perp": perp, "hip3": hip3, "spot_usdc": spot_usdc,
            "total": round(sum(parts), 2) if perp is not None else None,
            "spot_other_tokens": others}


def candidate_current_score(candidate: dict) -> float:
    """How well this candidate matches the target *now*.

    `best_score` is a high-water mark that only ever ratchets up, so reading it
    to answer "does this wallet match?" keeps asserting a peak the wallet may
    have left weeks ago. Three separate callers did exactly that — the tracer's
    combined alert, the risk score and the transfer graph — and all three could
    report a wallet at 0.75 while it currently scored 0.13.

    `best_score` remains the right field for ranking discovery history; it is
    simply not an answer to a present-tense question.
    """
    latest = candidate.get("latest_score")
    if latest is None:
        latest = candidate.get("best_score")
    try:
        value = float(latest or 0.0)
    except (TypeError, ValueError):
        return 0.0
    # json.load accepts NaN and Infinity, and every NaN comparison is False, so a
    # corrupt score slipped through range clamps as though it were the maximum.
    # A value that is not a real number is no evidence, not perfect evidence.
    return value if math.isfinite(value) else 0.0

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
    existing_keys.discard("")
    new_records = []
    for r in records:
        key = str(r.get(key_field, ""))
        if key and key in existing_keys:
            continue
        if key:
            existing_keys.add(key)  # also dedupe within this batch
        new_records.append(r)

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

def atomic_write_json(path: str | Path, data: dict | list, *, sort_keys: bool = False) -> None:
    """Write JSON to `path` atomically: write-then-rename, not truncate-in-place.

    A kill during a plain `open(path, "w")` leaves a truncated file on disk. For
    state a later run makes decisions from — not just reports — that truncated
    file is worse than no file: a reader that treats "exists but won't parse"
    the same as "absent" silently mistakes a fault for a clean first run (see
    src/tracer.py's traced-outbound marker). os.replace is atomic on POSIX and
    Windows, so a reader always sees either the whole previous file or the
    whole new one, never a partial write.

    Shared rather than reimplemented per caller: save_latest below and
    src/tracer.py's marker both need exactly this, and a second, slightly
    different hand-rolled version is how one of them would eventually drift.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=sort_keys)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def save_latest(directory: str, data: dict | list) -> str:
    """Save data as latest.json, atomically.

    `latest.json` carries state the next run depends on — the transfer graph's
    unfinished frontier queue and its undelivered-alert list — and the
    workflows that write it run under a 10-minute job timeout. See
    atomic_write_json's docstring for why this is write-then-rename rather
    than truncate-in-place.
    """
    filepath = Path(directory) / "latest.json"
    atomic_write_json(filepath, data)
    return str(filepath)

def record_key(directory_name: str, rec: dict):
    """The identity of one stored record, by data type. None when unkeyed.

    `append_records` dedupes only within the day file it writes, and it files a
    record under the COLLECTION date, not the record's own date. A record
    re-fetched on a later day (after a cursor reset, a backfill, or an outage
    catch-up) therefore lands in a second file carrying a key the first file
    already holds, and nothing downstream could tell. Measured 2026-09-10:
    data/ledger held 1,430 rows for 500 unique (hash, time) pairs — every entry
    stored three times — and data/fills held 188,299 rows for 162,818 unique
    tids. Every HL-native counterparty total was triple the truth, every exit
    was offered to the correlator three times, and the two backtest windows
    were duplicated unevenly.

    Funding is keyed on (time, coin), never on `hash`: Hyperliquid reports the
    zero hash on every funding row, so a hash key collapses the whole history
    to one record.
    """
    if not isinstance(rec, dict):
        return None
    if directory_name == "fills":
        return rec.get("tid")
    if directory_name == "ledger":
        return (rec.get("hash"), rec.get("time"))
    if directory_name == "funding":
        return (rec.get("time"), (rec.get("delta") or {}).get("coin"))
    if directory_name == "orders":
        return rec.get("oid")
    return None


def load_all_records(directory: str, *, dedupe: bool = True) -> list[dict]:
    """Load and merge all JSON files in a directory (daily files).

    Deduplicated across files by `record_key` for the data types that carry
    one, keeping the earliest copy. Lossless: only an exact key match is
    dropped, and a record with no key is always kept.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    name = dir_path.name
    all_records = []
    seen: set = set()
    for filepath in sorted(dir_path.glob("*.json")):
        if filepath.name == "latest.json":
            continue
        with open(filepath) as f:
            data = json.load(f)
        if not isinstance(data, list):
            continue
        if not dedupe:
            all_records.extend(data)
            continue
        for rec in data:
            key = record_key(name, rec)
            if key is not None and None not in (key if isinstance(key, tuple) else (key,)):
                if key in seen:
                    continue
                seen.add(key)
            all_records.append(rec)
    return all_records

def update_index() -> None:
    """Update data/index.json with manifest of all available data files."""
    index = {
        "last_updated": datetime.now(UTC).isoformat(),
        "wallet": load_config()["target_wallet"],
        "files": {},
        "stats": {},
    }

    # Data types that use daily JSON files (date.json)
    daily_types = ["fills", "orders", "funding", "ledger", "fees",
                   "rate_limit", "scans", "l1_transactions", "fund_flows"]

    # Data types that use dated subdirectories (date/HH-MM.json snapshots)
    snapshot_types = ["positions", "account", "spot", "portfolio",
                      "positions_hip3_xyz"]

    # Types that keep only a rolling latest.json (no dated snapshots).
    # Maps data type -> the payload key to surface as a stat in the index.
    singleton_latest_types = {
        "hl_transfers": "counterparty_count",
        "correlations": "match_count",
        "risk": "score",
        "agents": None,
    }

    singleton_types = ["candidates"]

    for data_type in daily_types:
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

    for data_type in snapshot_types:
        type_dir = DATA_DIR / data_type
        if type_dir.exists():
            # Find dated subdirectories (e.g. 2026-02-20/)
            dates = sorted([
                d.name for d in type_dir.iterdir()
                if d.is_dir() and len(d.name) == 10  # YYYY-MM-DD
            ])
            snapshot_count = sum(
                len(list((type_dir / d).glob("*.json"))) for d in dates
            )
            index["files"][data_type] = dates
            index["stats"][f"total_{data_type}_snapshots"] = snapshot_count

            # Archived days: rolled into one gzipped JSONL each by
            # scripts/compact_data.py. Advertised so readers know the day exists
            # and where to find it, rather than 404-ing on the old snapshot path.
            archive_dir = type_dir / "archive"
            if archive_dir.exists():
                archived = sorted(f.stem.replace(".jsonl", "")
                                  for f in archive_dir.glob("*.jsonl.gz"))
                if archived:
                    index.setdefault("archived", {})[data_type] = archived

            # Include per-date snapshot filenames for account data (used by dashboard charts)
            if data_type == "account":
                snapshots_by_date = {}
                for d in dates:
                    files = sorted([
                        f.name for f in (type_dir / d).glob("*.json")
                    ])
                    if files:
                        snapshots_by_date[d] = files
                index["account_snapshots"] = snapshots_by_date
                # Compact per-day account series for archived days — a few KB each
                # and directly fetchable, so chart history survives compaction.
                daily_dir = type_dir / "daily"
                if daily_dir.exists():
                    index["account_daily"] = sorted(
                        f.stem for f in daily_dir.glob("*.json"))

    for data_type in singleton_types:
        type_dir = DATA_DIR / data_type
        if type_dir.exists():
            files = sorted([
                f.name for f in type_dir.glob("*.json")
                if f.name != "latest.json"
            ])
            index["files"][data_type] = files
            index["stats"][f"total_{data_type}"] = len(files)

    for data_type, stat_key in singleton_latest_types.items():
        latest = DATA_DIR / data_type / "latest.json"
        if latest.exists():
            index["files"][data_type] = ["latest.json"]
            if stat_key:
                try:
                    with open(latest) as f:
                        payload = json.load(f)
                    index["stats"][f"{data_type}_{stat_key}"] = payload.get(stat_key, 0)
                except Exception:
                    pass

    index_path = DATA_DIR / "index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
