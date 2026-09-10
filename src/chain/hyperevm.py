# src/chain/hyperevm.py
"""Reading HyperEVM, the one chain the target can reach without touching L1.

Why this exists: the target has sent $23,000,000 to `0x2000...0000` — the
HyperCore system address for spot token index 0 (USDC) — across five transfers
between 2026-06-12 and 2026-08-28, and nothing has come back that way. That is
real money leaving into a chain no other module in this project can read.

## What is and is not feasible here

The public RPC (`rpc.hyperliquid.xyz/evm`, chain 999) caps `eth_getLogs` at a
1000-block range and rate-limits aggressively. HyperEVM produces a block roughly
every second, so ninety days of history is ~7.8 million blocks — about 7,800
rate-limited requests. Scanning history by logs is not viable on the free tier,
and pretending otherwise would build a sweep that never finishes inside a job.

What IS cheap is asking about an ACCOUNT: its nonce, its native balance, and its
balance in any linked token. Those are O(addresses), not O(blocks).

The nonce is the valuable one. An address that has never sent a transaction on
HyperEVM cannot have moved anything from itself to another wallet there — no
transfer, no swap, no bridge back. Measured 2026-09-10: the target's nonce is 0,
which rules HyperEVM out as a migration path taken so far, and turns a standing
blind spot into a tripwire that costs one request to check.

## Unreadable is not zero

Every field is `None` when the read failed and a number when it succeeded. A
nonce of 0 means "we asked, and this address has never transacted"; a nonce of
`None` means "we could not tell". Collapsing those would let a rate-limited run
report the all-clear this module exists to make trustworthy.
"""

import os
import time
from typing import Any

import requests

# Chain 999. Overridable so a private endpoint (no 1000-block cap, no shared
# rate limit) can be dropped in without touching callers.
RPC_URL = os.environ.get("HYPEREVM_RPC_URL", "https://rpc.hyperliquid.xyz/evm")

# Confirmed against the live endpoint on 2026-09-10, not taken from docs.
MAX_LOG_RANGE = 1000

ERC20_BALANCE_OF = "0x70a08231"


class RpcUnavailable(Exception):
    """We could not get an answer. Never raised for a well-formed 'no'."""


def _default_caller(method: str, params: list, *, timeout: float = 30.0) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    response = requests.post(RPC_URL, json=payload, timeout=timeout)
    return response.json()


def rpc_call(method: str, params: list, *, caller=None, retries: int = 3,
             sleep=time.sleep) -> Any:
    """One JSON-RPC call, retrying only the rate limit.

    Raises RpcUnavailable for anything that is not an answer. An `error` that is
    NOT a rate limit — `execution reverted`, say — is a real reply about the
    request and is raised immediately rather than retried: retrying a revert
    just spends the budget to be told the same thing.
    """
    caller = caller or _default_caller
    last = ""
    for attempt in range(max(1, retries)):
        try:
            payload = caller(method, params)
        except Exception as exc:                      # noqa: BLE001 - transport
            last = f"{type(exc).__name__}: {exc}"
            sleep(1.5 * (attempt + 1))
            continue
        if not isinstance(payload, dict):
            raise RpcUnavailable(f"unexpected payload: {type(payload).__name__}")
        if "result" in payload:
            return payload["result"]
        error = payload.get("error") or {}
        message = str(error.get("message") or error)
        if "rate limited" in message.lower():
            last = message
            sleep(1.5 * (attempt + 1))
            continue
        raise RpcUnavailable(message or "unknown rpc error")
    raise RpcUnavailable(last or "exhausted retries")


def _to_int(value) -> int | None:
    if not isinstance(value, str) or not value.startswith("0x") or value == "0x":
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def account_activity(address: str, *, caller=None, sleep=time.sleep) -> dict:
    """Has this address ever acted on HyperEVM, and what does it hold natively?

    `nonce` is the migration-relevant field: 0 means it has never sent a
    transaction, so nothing can have left it. `None` in any field means the read
    failed — never read that as zero.
    """
    out = {"address": (address or "").lower(), "nonce": None,
           "native_wei": None, "has_code": None, "errors": []}
    for key, method, params in (
        ("nonce", "eth_getTransactionCount", [address, "latest"]),
        ("native_wei", "eth_getBalance", [address, "latest"]),
        ("has_code", "eth_getCode", [address, "latest"]),
    ):
        try:
            raw = rpc_call(method, params, caller=caller, sleep=sleep)
        except RpcUnavailable as exc:
            out["errors"].append(f"{method}: {exc}")
            continue
        if key == "has_code":
            out["has_code"] = bool(isinstance(raw, str) and raw not in ("", "0x"))
        else:
            out[key] = _to_int(raw)
            if out[key] is None:
                out["errors"].append(f"{method}: unparseable result {raw!r}")
    return out


def token_balance(address: str, token: str, *, caller=None,
                  sleep=time.sleep) -> int | None:
    """Raw `balanceOf` for one linked token, or None if it could not be read.

    Not every address Hyperliquid lists as a token's `evmContract` answers the
    standard calls: USDC's linked contract reverts on `balanceOf`, `symbol` and
    `totalSupply` alike while UBTC's answers normally. A revert is a failed read,
    so it returns None — reporting 0 would invent an empty balance for a token
    we simply cannot query.
    """
    data = ERC20_BALANCE_OF + "0" * 24 + (address or "")[2:].lower()
    try:
        raw = rpc_call("eth_call", [{"to": token, "data": data}, "latest"],
                       caller=caller, sleep=sleep)
    except RpcUnavailable:
        return None
    return _to_int(raw)


def never_acted(activity: dict) -> bool:
    """True only when we CONFIRMED the address has never sent a transaction.

    Deliberately false when the nonce could not be read. This is the predicate
    behind an all-clear, and an all-clear derived from a failed read is exactly
    the silent miss this project is built to avoid.
    """
    return activity.get("nonce") == 0


def summarise(activity: dict) -> str:
    if activity.get("nonce") is None:
        return "HyperEVM state unreadable — not an all-clear"
    if activity["nonce"] == 0:
        return "never transacted on HyperEVM (nothing can have left this address there)"
    return f"has sent {activity['nonce']} transaction(s) on HyperEVM"


def describe_log_limits() -> str:
    """Why there is no historical sweep here, in one line for a health report."""
    return (f"eth_getLogs capped at {MAX_LOG_RANGE} blocks with ~1s block times: "
            f"history scanning is not viable on the public RPC")


__all__ = ["RPC_URL", "MAX_LOG_RANGE", "RpcUnavailable", "rpc_call",
           "account_activity", "token_balance", "never_acted", "summarise",
           "describe_log_limits"]
