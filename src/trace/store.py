# src/trace/store.py
"""The engine's state on disk: sharded so a run rewrites only what it touched.

The git pack was 1.7 GiB on 2026-10-04 and the 28 MB transfer-graph file is
rewritten whole every run. Here a record lives in the shard named by its
address's first byte (`registry/4a.json`), and a shard whose content did not
change is not rewritten — so git stores nothing new for it.

    data/trace/registry/<xx>.json   address -> record
    data/trace/hl_edges/<xx>.json   ledger owner -> normalised HL transfer edges
    data/trace/latest.json          the run report (single writer: the engine)
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _shard(address: str) -> str:
    a = (address or "").lower()
    return a[2:4] if a.startswith("0x") and len(a) >= 4 else "xx"


def load(directory: Path) -> dict:
    out: dict = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            # A corrupt shard is reported, never silently read as empty state:
            # empty state would re-announce everything it held as new.
            raise RuntimeError(f"unreadable trace shard {path}: {exc}") from exc
        if isinstance(doc, dict):
            out.update(doc)
    return out


def _write(path: Path, text: str) -> None:
    # Write-then-rename, so a killed step leaves a shard wholly old or wholly
    # new (utils.atomic_write_json does the same, but always indents, which
    # would make "unchanged" undetectable and rewrite every shard every run).
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def save(directory: Path, table: dict) -> list[str]:
    """Write every shard whose content changed; return the shards written."""
    directory = Path(directory)
    shards: dict[str, dict] = {}
    for address, value in table.items():
        shards.setdefault(_shard(address), {})[address] = value
    written = []
    for name, content in sorted(shards.items()):
        path = directory / f"{name}.json"
        text = json.dumps(content, sort_keys=True, separators=(",", ":"))
        try:
            if path.read_text() == text:
                continue
        except OSError:
            pass
        _write(path, text)
        written.append(name)
    for stale in directory.glob("*.json") if directory.exists() else []:
        if stale.stem not in shards:
            stale.unlink()
            written.append(stale.stem)
    return written
