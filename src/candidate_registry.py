"""Persistent investigation records; the display shortlist is only a view."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from src.utils import DATA_DIR, atomic_write_json


def valid_wallet(wallet: str) -> str:
    wallet = str(wallet).lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", wallet):
        raise ValueError("Expected a 20-byte wallet address")
    return wallet


def _read(path: Path) -> dict:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return row if isinstance(row, dict) else {}
    except (OSError, ValueError):
        return {}


def iter_candidates(data_dir: Path | None = None) -> list[dict]:
    directory = Path(data_dir or DATA_DIR) / "candidates"
    rows = {}
    for row in _read(directory / "latest.json").get("candidates", []):
        if not isinstance(row, dict):
            continue
        try:
            rows[valid_wallet(row.get("wallet"))] = row
        except ValueError:
            continue
    for path in sorted(directory.glob("0x*.json")):
        row = _read(path)
        try:
            wallet = valid_wallet(row.get("wallet"))
        except ValueError:
            continue
        if path.stem.lower() == wallet:
            rows[wallet] = {**row, "wallet": wallet}
    return [rows[w] for w in sorted(rows)]


def observe_candidate(wallet: str, evidence: dict, data_dir: Path | None = None) -> dict:
    wallet = valid_wallet(wallet)
    path = Path(data_dir or DATA_DIR) / "candidates" / f"{wallet}.json"
    row = _read(path)
    stamp = evidence.get("observed_at") or datetime.now(UTC).isoformat()
    row.update(wallet=wallet, last_checked=stamp,
               last_read_status=evidence.get("status", "ok"))
    row.setdefault("first_seen", stamp)
    sources = set(row.get("discovery_sources") or [])
    if evidence.get("source"):
        sources.add(evidence["source"])
    row["discovery_sources"] = sorted(sources)
    if row["last_read_status"] == "ok":
        row["last_successful_read"] = stamp
        row.pop("last_read_error", None)
        if evidence.get("positive"):
            row["last_positive_evidence"] = stamp
    elif evidence.get("error"):
        row["last_read_error"] = str(evidence["error"])[:300]
    observation = {**evidence, "observed_at": stamp}
    if row['last_read_status'] != 'ok':
        observation['positive'] = False
    observations = row.get("observations") or []
    event = evidence.get("event_id")
    if event:
        observations = [o for o in observations
                        if (o.get("source"), o.get("event_id")) != (evidence.get("source"), event)
                        or (o.get('positive') and not observation.get('positive'))]
    # Keep positive facts separately from a bounded log of empty/failed checks.
    facts = [o for o in observations if o.get("positive")]
    attempts = [o for o in observations if not o.get("positive")]
    (facts if observation.get("positive") else attempts).append(observation)
    row["observations"] = facts[-200:] + attempts[-20:]
    if evidence.get("coverage") is not None:
        row["last_read_coverage"] = evidence["coverage"]
    atomic_write_json(path, row)
    return row
