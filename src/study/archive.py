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
