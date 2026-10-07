"""The study's files under data/study/ (spec §6.3, §10). Only scripts/run_study.py
calls the writers, so data/study/ has exactly one writer.

Daily records live in archive/<wallet>/<YYYY-MM-DD>.json until their month ends;
then they are packed into <YYYY-MM>.jsonl.gz beside them, read back and compared
before any daily file is deleted. The archive cannot be re-fetched — Hyperliquid
keeps about two weeks of an account like his — so nothing here deletes what it
has not verified, and an archive that will not read is never overwritten.

This module's own files (day files, month archives, state.json) are read strictly:
a missing file is absent, but a file that is present but cannot be read is
Unreadable (never overwritten). read_json is the lenient reader for other
detectors' files (Task 10).
"""

from __future__ import annotations

import gzip
import io
import json
import os
import zlib
from pathlib import Path

from src import utils

DAILY = ".json"
MONTHLY = ".jsonl.gz"


class Unreadable(Exception):
    """A file of the study's own archive or state is present but cannot be read.
    Never treated as absent (rule 5): the caller stops for that wallet, and
    nothing here overwrites it."""


def root(data_dir=None) -> Path:
    return Path(data_dir or utils.DATA_DIR) / "study"


def wallet_dir(wallet: str, data_dir=None) -> Path:
    return root(data_dir) / "archive" / wallet.lower()


def _dumps(doc) -> str:
    """Serialise a doc with the same format write_compact uses for comparison."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def _read_strict(path: Path):
    """Read a file of the study's own archive: (False, None) when absent;
    (True, doc) when it parses; Unreadable when present but unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return (True, json.load(f))
    except FileNotFoundError:
        return (False, None)
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{path}: {exc}") from exc


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
            f.write(_dumps(doc))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_month(path: Path) -> dict[str, dict] | None:
    """The records in a month archive, or None when it cannot be read.
    Returns None for zero-byte, corrupt, or partly-valid archives."""
    try:
        if path.stat().st_size == 0:
            return None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError, EOFError, zlib.error):
        return None
    # Validate every row: must be dict with non-empty str "day"
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("day"), str) or not row["day"]:
            return None
    return {row["day"]: row for row in rows}


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
            raise Unreadable(str(path))
        days.update({d: r for d, r in found.items() if first_day <= d <= last_day})
    for path in sorted(folder.glob("*" + DAILY)):
        day = path.name[: -len(DAILY)]
        if not first_day <= day <= last_day:
            continue
        exists, record = _read_strict(path)
        if not exists:
            continue
        if not isinstance(record, dict) or record.get("day") != day:
            raise Unreadable(f"{path}: not the record for {day}")
        days[day] = record
    return days


def save_days(days: dict[str, dict], data_dir=None) -> int:
    """Write each changed day; an unchanged one is not rewritten."""
    written = 0
    for day, record in sorted(days.items()):
        if record.get("day") != day:
            raise ValueError(f"record day {record.get('day')!r} != key {day!r}")
        path = wallet_dir(record["wallet"], data_dir) / f"{day}{DAILY}"
        exists, existing = _read_strict(path)
        if exists:
            # Compare serialized content
            if _dumps(existing) == _dumps(record):
                continue
        # File is absent or content differs
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
        merged: list[Path] = []
        for path in paths:
            try:
                exists, record = _read_strict(path)
            except Unreadable:
                print(f"[study] day file unreadable, kept: {path}")
                continue
            if not exists or not isinstance(record, dict) or record.get("day") != path.name[: -len(DAILY)]:
                if not exists:
                    continue
                print(f"[study] day file unreadable, kept: {path}")
                continue
            records[record["day"]] = record
            merged.append(path)
        if not merged:
            # No day file merged for this month
            continue
        payload = "".join(_dumps(records[d]) + "\n"
                          for d in sorted(records)).encode("utf-8")
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
            gz.write(payload)
        tmp = folder / f".{target.name}.{os.getpid()}.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(buffer.getvalue())
                f.flush()
                os.fsync(f.fileno())
            if _read_month(tmp) != records:
                print(f"[study] month archive failed verification, daily files kept: {target}")
                continue
            os.replace(tmp, target)
            for path in merged:
                path.unlink()
            rolled += 1
        finally:
            if tmp.exists():
                tmp.unlink()
    return rolled


def load_state(data_dir=None) -> dict:
    exists, state = _read_strict(root(data_dir) / "state.json")
    if not exists:
        return {}
    if not isinstance(state, dict):
        raise Unreadable(f"{root(data_dir) / 'state.json'}: not a dict")
    return state


def save_state(state: dict, data_dir=None) -> None:
    write_compact(root(data_dir) / "state.json", state)


def write_if_changed(path: Path, doc) -> bool:
    """Write if changed by serialised content. Uses read_json (lenient) for
    comparison, so tuples and int keys in stored JSON read as lists and strings
    (spec §10: rewritten only when content hash changes)."""
    existing = read_json(path, None)
    if existing is not None and _dumps(existing) == _dumps(doc):
        return False
    write_compact(path, doc)
    return True
