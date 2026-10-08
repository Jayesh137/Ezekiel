"""The casebook's files under data/casebook/ (spec 2026-10-08 §5, §11).

scripts/update_casebook.py is the only writer; scripts/backfill_casebook.py
writes the same tree by hand, once. Every write is atomic (a temp file beside
the target, then os.replace) so a killed step leaves the previous file whole,
and tests/conftest.py can watch the tree by directory mtimes. A case file that
is present but unreadable is never overwritten (rule 5): it is reported and left
for a human, because the casebook is the one place its history lives.

Layout:
    cases/<address>.json      one case per suspect (casebook-case/1)
    events/<YYYY-MM-DD>.jsonl  every change, one JSON object a line, append-only
    latest.json                the ranked view (casebook-index/1), derived each run
    state.json                 the last roster consumed, sticky rejections, the last run
    state.unreadable-<time>.json  a state.json that could not be read, set aside whole
    README.md                  the format, for a reader in a year (written by hand)
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from src import utils
from src.candidate_registry import valid_wallet


def root(data_dir=None) -> Path:
    return Path(data_dir or utils.DATA_DIR) / "casebook"


def case_path(address: str, data_dir=None) -> Path:
    return root(data_dir) / "cases" / f"{valid_wallet(address)}.json"


def _case_text(case: dict) -> str:
    return json.dumps(case, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _parse_case(text: str, address: str) -> dict | None:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if (not isinstance(doc, dict) or str(doc.get("address", "")).lower() != address
            or not isinstance(doc.get("evidence"), dict)):
        return None
    return doc


def load_cases(data_dir=None) -> tuple[dict, list]:
    """(address -> case, unreadable). A file present but unreadable is reported, never
    read as an empty case (rule 5)."""
    folder = root(data_dir) / "cases"
    cases, unreadable = {}, []
    for path in sorted(folder.glob("0x*.json")) if folder.exists() else []:
        address = path.stem.lower()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            unreadable.append({"address": address, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        doc = _parse_case(text, address)
        if doc is None:
            unreadable.append({"address": address, "error": "not a readable case file for this address"})
            continue
        cases[address] = doc
    return cases, unreadable


def write_cases(cases: dict, data_dir=None, *, skip=frozenset()) -> int:
    """Write every case whose serialisation changed; returns how many were written.
    Never overwrites a file that exists but does not read as this address's case."""
    written = 0
    for address, case in sorted(cases.items()):
        if address in skip:
            continue
        path = case_path(address, data_dir)
        text = _case_text(case)
        try:
            existing = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = None
        if existing == text:
            continue
        if existing is not None and _parse_case(existing, address) is None:
            continue
        write_atomic(path, text)
        written += 1
    return written


def append_events(events: list, data_dir=None) -> int:
    """Append events to their day's file (events/<YYYY-MM-DD>.jsonl), atomically. The
    existing bytes are kept as they are, so a damaged line is never lost or rewritten."""
    by_day: dict[str, list] = {}
    for e in events:
        by_day.setdefault(str(e.get("at", ""))[:10] or "undated", []).append(e)
    folder = root(data_dir) / "events"
    for day, rows in sorted(by_day.items()):
        path = folder / f"{day}.jsonl"
        try:
            existing = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        lines = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)
        write_atomic(path, existing + lines)
    return len(events)


def read_events(data_dir=None, *, since: str | None = None, address: str | None = None) -> list:
    """Every readable event, oldest day first; damaged lines are skipped."""
    folder = root(data_dir) / "events"
    wanted = address.lower() if address else None
    out = []
    for path in sorted(folder.glob("*.jsonl")) if folder.exists() else []:
        if since and path.stem < since[:10]:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if since and str(row.get("at", "")) < since:
                continue
            if wanted and row.get("address") != wanted:
                continue
            out.append(row)
    return out


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_state(data_dir=None) -> tuple[dict, str | None]:
    """(state, None); ({}, None) before the first run; ({}, why) when state.json is
    present but unreadable, which the writer must never take for a first run (rule 5)."""
    try:
        doc = json.loads((root(data_dir) / "state.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"[:200]
    return (doc, None) if isinstance(doc, dict) else ({}, "not a state object")


def read_state(data_dir=None) -> dict:
    return load_state(data_dir)[0]


def set_aside_state(data_dir=None, *, now_ms: int) -> Path:
    """Move an unreadable state.json to state.unreadable-<UTC time>.json, whole, for a
    human: the sticky rejections it holds exist nowhere else."""
    path = root(data_dir) / "state.json"
    stamp = datetime.fromtimestamp(now_ms / 1000, UTC).strftime("%Y%m%dT%H%M%SZ")
    aside = path.with_name(f"state.unreadable-{stamp}.json")
    os.replace(path, aside)
    return aside


def write_state(state: dict, data_dir=None) -> None:
    write_atomic(root(data_dir) / "state.json", json.dumps(state, indent=1, sort_keys=True) + "\n")


def read_index(data_dir=None) -> dict | None:
    """The ranked view, or None when absent or unreadable (a reader degrades; never raises)."""
    doc = _read_json(root(data_dir) / "latest.json", None)
    return doc if isinstance(doc, dict) else None


def write_index(index: dict, data_dir=None) -> None:
    write_atomic(root(data_dir) / "latest.json",
                 json.dumps(index, separators=(",", ":"), ensure_ascii=False) + "\n")


def clear(data_dir=None) -> None:
    """Remove the casebook's own generated files (cases, events, index, state) and
    nothing else: README.md and anything a human put there stay."""
    base = root(data_dir)
    for name in ("cases", "events"):
        if (base / name).exists():
            shutil.rmtree(base / name)
    for name in ("latest.json", "state.json"):
        if (base / name).exists():
            (base / name).unlink()
