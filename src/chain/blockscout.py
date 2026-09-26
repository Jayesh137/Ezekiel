"""Keyless per-instance transfer reads; incomplete pages never advance a cursor."""

import json
import time
from datetime import datetime

import requests

from src.chain.bridges import HOSTS
from src.chain.pagination import WalkResult, default_row_key

PATHS = {"erc20": "token-transfers", "native": "transactions", "internal": "internal-transactions"}


def _get(url, params):
    time.sleep(.35)
    response = requests.get(url, params=params, timeout=(10, 20))
    response.raise_for_status()
    return response.json()


def normalise(row, kind):
    if row.get("status") in ("error", "reverted") or row.get("success") is False:
        return None
    timestamp = int(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp())
    result = {"blockNumber": str(int(row["block_number"])), "timeStamp": str(timestamp),
              "hash": row.get("transaction_hash") or row.get("hash"),
              "from": (row.get("from") or {}).get("hash", "").lower(),
              "to": (row.get("to") or {}).get("hash", "").lower(),
              "value": str(row.get("value", "0"))}
    if kind == "erc20":
        token, total = row["token"], row["total"]
        if token.get("type") != "ERC-20":
            return None
        result.update(contractAddress=(token.get("address_hash") or token.get("address") or "").lower(),
                      tokenSymbol=token.get("symbol", ""), tokenDecimal=str(token.get("decimals", total.get("decimals"))),
                      value=str(total["value"]), logIndex=str(row["log_index"]))
        if not result["contractAddress"] or not 0 <= int(result["tokenDecimal"]) <= 36:
            raise ValueError("unreadable token identity/decimals")
    elif kind == "internal":
        result["traceId"] = str(row["index"])
    if not result["hash"]:
        raise ValueError("missing transaction identity")
    return result


def fetch_kind(address, chain, kind, start_block, budget, *, max_pages=50, get=None, **unused):
    get = get or _get
    host = HOSTS.get(chain["name"])
    if not host:
        return WalkResult([], start_block, 0, True, []), "unsupported_public_reader"
    url = f"{host}/api/v2/addresses/{address}/{PATHS[kind]}"
    params = {"type": "ERC-20"} if kind == "erc20" else {}
    seen_pages, rows, pages, error, complete = set(), {}, 0, None, False
    highest = start_block
    for _ in range(max_pages):
        try:
            budget.spend()
            doc = get(url, params)
            pages += 1
            if not isinstance(doc, dict) or not isinstance(doc.get("items"), list) or "next_page_params" not in doc:
                raise ValueError("invalid Blockscout page")
            crossed_start = False
            for raw in doc["items"]:
                block = int(raw["block_number"])
                if block < start_block:
                    crossed_start = True
                    continue
                highest = max(highest, block)
                row = normalise(raw, kind)
                if row:
                    rows[default_row_key(row)] = row
            next_page = doc["next_page_params"]
            if not next_page or crossed_start:
                complete = True
                break
            if not isinstance(next_page, dict):
                raise ValueError("invalid pagination cursor")
            identity = json.dumps(next_page, sort_keys=True)
            if identity in seen_pages:
                raise ValueError("repeated pagination cursor")
            seen_pages.add(identity)
            params = {**params, **next_page}
        except Exception as exc:
            error = f"blockscout:{type(exc).__name__}:{exc}"
            break
    if not complete and not error:
        error = "blockscout:page_budget; older records unread"
    ordered = sorted(rows.values(), key=lambda r: (int(r["blockNumber"]), default_row_key(r)))
    return WalkResult(ordered, highest if complete else start_block, pages, not complete, []), error


def newest_block(address, chain, kind, budget):
    try:
        budget.spend()
        payload = _get(f"{HOSTS[chain['name']]}/api/v2/addresses/{address}/{PATHS[kind]}",
                       {"type": "ERC-20"} if kind == "erc20" else {})
        items = payload["items"]
        return max((int(r["block_number"]) for r in items), default=0), None
    except Exception as exc:
        return None, f"blockscout:{exc}"
