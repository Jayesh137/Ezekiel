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
