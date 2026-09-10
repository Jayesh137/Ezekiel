# src/hl_actions.py
"""The account's own actions, from the Hyperliquid explorer.

The info API's ledger says that money moved; it does not say WHERE a
withdrawal went, WHICH agent was approved, or that a sub-account was created.
The explorer does. `POST https://rpc.hyperliquid.xyz/explorer` with
`{"type": "userDetails", "user": ...}` returns the last 300 L1 actions with
their full payloads — verified 2026-09-10 on the treasury: 18 `approveAgent`
(each with the wallet's signing chain id), 4 `withdraw3` carrying a
`destination`, `sendAsset`/`spotSend`/`usdSend` with destinations,
`vaultTransfer`, `tokenDelegate`, `cDeposit`, and the error text of actions
that failed.

Three things this module is careful about:

  * The window is 300 actions and cannot be paged. On a heavy trading day
    that is a few hours, so the collector polls every run and everything
    non-order is kept forever, keyed by hash.
  * The explorer also returns actions by OTHER users that merely mention the
    account (validators attesting its deposits, strangers airdropping to it).
    Only actions whose `user` IS the account are the account's own.
  * A withdrawal, send or sub-account transfer to an address outside the
    cluster is the event this project exists to catch, and it is alerted the
    moment it is seen. Everything else is evidence.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, append_records, save_latest

EXPLORER_URL = "https://rpc.hyperliquid.xyz/explorer"
ACTIONS_DIR = DATA_DIR / "actions"

# Actions that are routine trading and are not stored: the fills and orders
# already capture them, and at ~1,800 a day they would swamp the record.
TRADING_ACTIONS = {"order", "cancel", "cancelByCloid", "modify", "batchModify",
                   "updateLeverage", "updateIsolatedMargin", "twapOrder",
                   "twapCancel", "scheduleCancel", "noop"}

# Actions that move value to, or hand control to, another address.
DESTINATION_FIELDS = {
    "withdraw3": "destination",
    "usdSend": "destination",
    "spotSend": "destination",
    "sendAsset": "destination",
    "subAccountTransfer": "subAccountUser",
    "subAccountSpotTransfer": "subAccountUser",
    "vaultTransfer": "vaultAddress",
    "approveAgent": "agentAddress",
    "approveBuilderFee": "builder",
    "createSubAccount": None,
    "setReferrer": "code",
    "tokenDelegate": "validator",
    "linkStakingUser": "stakingUser",
    "cDeposit": None,
    "cWithdraw": None,
    "evmUserModify": None,
    "agentSetAbstraction": None,
    "usdClassTransfer": None,
    "vaultCreate": None,
}


def fetch_actions(address: str, *, post=None, timeout: float = 30.0) -> tuple[list, str | None]:
    """The explorer's last 300 actions mentioning `address`. (rows, error)."""
    import requests
    post = post or requests.post
    try:
        r = post(EXPLORER_URL, json={"type": "userDetails", "user": address}, timeout=timeout)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"
        doc = r.json()
    except Exception as exc:                          # noqa: BLE001 - transport
        return [], f"{type(exc).__name__}: {exc}"
    txs = doc.get("txs") if isinstance(doc, dict) else None
    if not isinstance(txs, list):
        return [], "unexpected payload"
    return txs, None


def own_actions(rows: list, address: str) -> list[dict]:
    """Normalised non-trading actions the account itself performed. Pure."""
    a = (address or "").lower()
    out = []
    for tx in rows or []:
        if not isinstance(tx, dict):
            continue
        if (tx.get("user") or "").lower() != a:
            continue
        action = tx.get("action") if isinstance(tx.get("action"), dict) else {}
        kind = action.get("type")
        if not kind or kind in TRADING_ACTIONS:
            continue
        field = DESTINATION_FIELDS.get(kind)
        dest = action.get(field) if field else None
        if isinstance(dest, str):
            dest = dest.strip().lower()
        out.append({
            "hash": tx.get("hash"),
            "time": tx.get("time"),
            "block": tx.get("block"),
            "user": a,
            "type": kind,
            "destination": dest if isinstance(dest, str) else None,
            "signature_chain_id": action.get("signatureChainId"),
            "amount": action.get("amount") if action.get("amount") is not None
            else action.get("usd") if action.get("usd") is not None
            else action.get("wei"),
            "token": action.get("token"),
            "error": tx.get("error"),
            "action": action,
        })
    return out


def foreign_destinations(actions: list[dict], cluster: set,
                         ignore: set | None = None) -> list[dict]:
    """Value or control handed to an address outside the cluster. Pure.

    `ignore` holds known infrastructure (the HLP vault, validators, system
    addresses) that receives from everyone and identifies nobody.
    """
    cluster = {(c or "").lower() for c in cluster}
    ignore = {(c or "").lower() for c in (ignore or set())}
    kinds = {"withdraw3", "usdSend", "spotSend", "sendAsset",
             "subAccountTransfer", "subAccountSpotTransfer", "vaultTransfer",
             "approveAgent", "linkStakingUser"}
    out = []
    for act in actions or []:
        if act.get("type") not in kinds or act.get("error"):
            continue
        dest = (act.get("destination") or "").lower()
        if not dest or dest in cluster or dest in ignore:
            continue
        if act.get("type") == "vaultTransfer" and not (act.get("action") or {}).get("isDeposit", True):
            continue  # a withdrawal FROM a vault comes back to the account
        out.append(act)
    return out


def record(address: str, actions: list[dict]) -> int:
    """Append the account's non-trading actions, keyed by hash. Returns added."""
    rows = [{**a, "_key": f"{a.get('hash')}"} for a in actions if a.get("hash")]
    return append_records(str(ACTIONS_DIR / (address or "").lower()), rows, key_field="_key")


def summarise(address: str, actions: list[dict], foreign: list[dict],
              error: str | None) -> dict:
    kinds: dict[str, int] = {}
    for a in actions:
        kinds[a["type"]] = kinds.get(a["type"], 0) + 1
    chain_ids: dict[str, int] = {}
    for a in actions:
        c = a.get("signature_chain_id")
        if c:
            chain_ids[str(c)] = chain_ids.get(str(c), 0) + 1
    return {
        "address": (address or "").lower(),
        "computed_at": datetime.now(UTC).isoformat(),
        "read_ok": error is None,
        "error": error,
        "non_trading_actions_in_window": len(actions),
        "kinds": kinds,
        "signature_chain_ids": chain_ids,
        "agents_approved": sorted({a["destination"] for a in actions
                                   if a["type"] == "approveAgent" and a.get("destination")}),
        "foreign_destinations": [{"type": f["type"], "destination": f["destination"],
                                  "amount": f.get("amount"), "token": f.get("token"),
                                  "time": f.get("time"), "hash": f.get("hash")}
                                 for f in foreign],
    }


def save(report: dict) -> None:
    save_latest(str(ACTIONS_DIR), report)
