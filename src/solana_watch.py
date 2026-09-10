# src/solana_watch.py
"""A tripwire on the cluster's Solana addresses.

Decoding the target's CCTP transfers on 2026-09-10 found 23 burns, $22.75M,
minting to one Solana address: 2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv.
It was still active on 2026-08-21. Solana reaches Hyperliquid through the
same Circle route (domain 19), so it is a funding path for a brand-new
account that no EVM sweep would ever see.

The public RPC answers `getSignaturesForAddress` without a key. The watch is
a last-seen signature per address; anything newer is a movement to look at.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

RPC_URL = "https://api.mainnet-beta.solana.com"
LABELS_PATH = DATA_DIR / "labels" / "solana_addresses.json"
WATCH_DIR = DATA_DIR / "solana"


def load_addresses(path: Path | None = None) -> dict:
    try:
        with open(path or LABELS_PATH) as f:
            doc = json.load(f)
        return doc.get("addresses") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def fetch_signatures(address: str, *, post=None, limit: int = 25,
                     timeout: float = 30.0) -> tuple[list, str | None]:
    """Recent signatures, newest first. (rows, error)."""
    import requests
    post = post or requests.post
    try:
        r = post(RPC_URL, json={"jsonrpc": "2.0", "id": 1,
                                "method": "getSignaturesForAddress",
                                "params": [address, {"limit": limit}]}, timeout=timeout)
        doc = r.json()
    except Exception as exc:                          # noqa: BLE001 - transport
        return [], f"{type(exc).__name__}: {exc}"
    if not isinstance(doc, dict) or "result" not in doc:
        return [], str((doc or {}).get("error") or "unexpected payload")
    rows = doc.get("result")
    return (rows if isinstance(rows, list) else []), None


def new_since(rows: list, last_signature: str | None) -> list:
    """Signatures newer than the one recorded last time. Pure."""
    out = []
    for row in rows or []:
        sig = row.get("signature") if isinstance(row, dict) else None
        if not sig or sig == last_signature:
            break
        out.append(row)
    return out


def build_report(addresses: dict, readings: dict, previous: dict) -> dict:
    """`readings`: address -> (rows, error). `previous`: address -> last signature."""
    wallets = []
    for addr, meta in addresses.items():
        rows, err = readings.get(addr, ([], "not read"))
        last = previous.get(addr)
        fresh = new_since(rows, last) if not err else []
        newest = rows[0].get("signature") if rows and isinstance(rows[0], dict) else last
        latest_time = None
        for r in rows:
            if isinstance(r, dict) and r.get("blockTime"):
                latest_time = datetime.fromtimestamp(int(r["blockTime"]), tz=UTC).isoformat()
                break
        wallets.append({"address": addr, "role": meta.get("role"),
                        "provenance": meta.get("provenance"), "read_ok": err is None,
                        "error": err, "last_signature": newest,
                        "last_activity": latest_time,
                        "new_signatures": [r.get("signature") for r in fresh],
                        "first_run": last is None})
    return {"computed_at": datetime.now(UTC).isoformat(), "watched": len(addresses),
            "wallets": wallets}


def save(report: dict) -> None:
    save_latest(str(WATCH_DIR), report)
