# src/chain/client.py
"""Reading transfers from Etherscan V2, on any chain, under a budget.

Three record kinds are collected per address, not one. `txlistinternal` matters
most: a contract-mediated transfer — which is what every bridge and router
emits — appears in neither `tokentx` nor `txlist`, so collecting only those two
leaves a hole exactly the shape of a bridged migration.

Every function distinguishes "this address has no such records" from "we could
not read". Those must never serialise the same way: one is knowledge, the other
is blindness.
"""

import os

from src.chain.budget import BudgetExhausted, CallBudget
from src.chain.pagination import WalkResult, walk_blocks
from src.utils import etherscan_get

ACTIONS = {
    "erc20": "tokentx",
    "native": "txlist",
    "internal": "txlistinternal",
}

EMPTY_MESSAGES = ("no transactions found", "no records found")


def _rows_or_error(payload: dict) -> tuple[list[dict], str | None]:
    """Split an Etherscan payload into rows and an error string."""
    result = payload.get("result")
    if payload.get("status") == "1":
        if isinstance(result, list):
            return result, None
        return [], f"unexpected result: {result!r}"
    message = str(payload.get("message", "") or "")
    if message.lower() in EMPTY_MESSAGES:
        return [], None
    if isinstance(result, str) and result:
        return [], result
    return [], message or "unknown etherscan error"


def fetch_kind(address: str, chain: dict, kind: str, start_block: int,
               budget: CallBudget, *, page_size: int = 1000,
               max_pages: int = 50) -> tuple[WalkResult, str | None]:
    """Every record of one kind for one address on one chain, from start_block.

    Returns whatever was collected plus an error string when the sweep did not
    finish. A partial result is still returned — discarding it would throw away
    real history to report a failure that is already reported.
    """
    action = ACTIONS[kind]
    error: str | None = None

    def fetch(start: int, size: int) -> list[dict]:
        nonlocal error
        if error is not None:
            return []
        try:
            budget.spend()
        except BudgetExhausted as exc:
            error = f"budget_exhausted:{exc}"
            return []
        payload = etherscan_get({
            "module": "account",
            "action": action,
            "address": address,
            "startblock": start,
            "endblock": 99999999,
            "page": 1,
            "offset": size,
            "sort": "asc",
        }, chain_id=chain["chain_id"])
        rows, err = _rows_or_error(payload)
        if err:
            error = err
        return rows

    result = walk_blocks(fetch, start_block, page_size=page_size, max_pages=max_pages)
    return result, error


def probe_activity(address: str, chain: dict, budget: CallBudget) -> tuple[bool, str | None]:
    """Has this address ever transacted on this chain? One call.

    Returns (active, error). A caller must treat a non-None error as "we could
    not tell", never as "inactive" — a failed probe that reads as an empty chain
    is the silent all-clear this whole phase exists to prevent.
    """
    try:
        budget.spend()
    except BudgetExhausted as exc:
        return False, f"budget_exhausted:{exc}"
    payload = etherscan_get({
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": 1,
        "sort": "asc",
    }, chain_id=chain["chain_id"])
    rows, err = _rows_or_error(payload)
    return bool(rows), err


def fetch_code(address: str, chain: dict, budget: CallBudget) -> str | None:
    """The address's deployed bytecode, or None if it could not be read.

    "0x" means an externally owned account. Anything longer is a contract, and a
    contract is never a person.
    """
    # Every other live-lookup path in the codebase checks this before firing a
    # request (src/linkage.py, src/chain/collect.py, src/transfer_graph.py) —
    # without a key every call returns "Invalid API Key", which would spend up
    # to CODE_LOOKUP_SECONDS doing nothing rather than skipping cleanly. A
    # failed lookup already resolves safely (has_code's `is True` guard), but
    # firing doomed requests still breaks convention.
    if not os.environ.get("ETHERSCAN_API_KEY"):
        return None
    try:
        budget.spend()
    except BudgetExhausted:
        return None
    payload = etherscan_get({
        "module": "proxy",
        "action": "eth_getCode",
        "address": address,
        "tag": "latest",
    }, chain_id=chain["chain_id"])
    code = payload.get("result")
    return code if isinstance(code, str) and code.startswith("0x") else None
