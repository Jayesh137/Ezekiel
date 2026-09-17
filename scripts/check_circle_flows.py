#!/usr/bin/env python3
"""Walk Circle's MessageTransmitterV2 logs on HyperEVM and check both ends.

See src/circle_flows.py for what the events carry and the tripwires they feed.

Two sources. With an Etherscan key (CI has one) the walk reads HyperEVM's logs
through Etherscan V2 (chainid 999) in wide chunks. Without one it falls back to
the public HyperEVM RPC, which caps eth_getLogs at 1,000 blocks and allows about
one call a minute, so that walk is paced and backs off. Both resume from a
stored block cursor, and a range that could not be read stops the walk rather
than being skipped — a skipped window is a hole that reads exactly like a quiet
hour (rule 5). Runs in watch.yml.

Single writer: data/circle_flows/.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import circle_flows as cf
from src import utils
from src.utils import load_config, save_latest

RPC_URL = "https://rpc.hyperliquid.xyz/evm"
WINDOW_BLOCKS = 1_000
# The first run starts at the present: the public RPC allows about one
# getLogs a minute (measured 2026-09-17), so a day of backfill would never
# catch up, and what this feed is FOR is the next transfer, not last week's.
FIRST_RUN_BLOCKS = 1_000
MAX_WINDOWS = 40
# Etherscan V2 serves HyperEVM (chainid 999) with the key CI already holds and
# takes wide ranges: ~4,500 Circle deposits a day, so a 5,000-block chunk
# (~1.4h) stays far under its 1,000-row page.
HYPEREVM_CHAIN_ID = 999
ETHERSCAN_CHUNK_BLOCKS = 5_000
ETHERSCAN_PAGE = 1_000
ETHERSCAN_MAX_PAGES = 10
MAX_CHUNKS = 30
READ_SECONDS = 120.0
PACE_SECONDS = 0.35
KEEP_FINDINGS = 200
KEEP_ALERTED = 2_000


class RpcError(RuntimeError):
    pass


def rpc(method: str, params: list, *, post=None, sleep=time.sleep, tries: int = 6):
    """One JSON-RPC call; backs off on the rate limit; RAISES on failure."""
    post = post or requests.post
    last = None
    for attempt in range(tries):
        try:
            doc = post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                      "params": params}, timeout=30).json()
        except Exception as exc:                      # noqa: BLE001 - transport
            last = exc
            sleep(2 * (attempt + 1))
            continue
        error = (doc or {}).get("error")
        if error:
            last = error
            if error.get("code") == -32005:          # rate limited
                sleep(2 * (attempt + 1))
                continue
            raise RpcError(str(error))
        return doc.get("result")
    raise RpcError(f"{method} failed after {tries} tries: {last}")


def his_identities(config: dict) -> tuple[set, set, set]:
    """(his EVM addresses, his raw bytes32 identities, his Hyperliquid accounts)."""
    cluster = {(config.get("target_wallet") or "").lower()}
    cluster |= {(w or "").lower() for w in config.get("known_self_wallets") or []}
    cluster.discard("")
    evm = set(cluster)
    try:
        with open(utils.DATA_DIR / "roster" / "latest.json") as f:
            evm |= {(r.get("wallet") or "").lower() for r in json.load(f).get("wallets") or []
                    if isinstance(r, dict) and r.get("tier") == "CONFIRMED"
                    and not r.get("is_service")}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(utils.DATA_DIR / "deposit_sentinels" / "latest.json") as f:
            evm |= {(a or "").lower() for a in (json.load(f).get("sentinels") or {})}
    except (OSError, ValueError, AttributeError):
        pass
    raw = set()
    try:
        with open(utils.DATA_DIR / "labels" / "solana_addresses.json") as f:
            for address, rec in (json.load(f).get("addresses") or {}).items():
                if (rec or {}).get("role") != "cluster":
                    continue
                for h in (cf.base58_to_hex(address), rec.get("mint_recipient_hex")):
                    if h:
                        raw.add(h.lower())
    except (OSError, ValueError, AttributeError):
        pass
    evm.discard("")
    return evm, raw, cluster


def read_window(start: int, end: int, *, call=rpc) -> list[dict]:
    logs = call("eth_getLogs", [{
        "fromBlock": hex(start), "toBlock": hex(end),
        "address": cf.MESSAGE_TRANSMITTER_V2,
        "topics": [[cf.TOPIC_MESSAGE_RECEIVED, cf.TOPIC_MESSAGE_SENT]],
    }])
    return [row for row in (cf.decode(log) for log in logs or []) if row]


def etherscan_logs(from_block: int, to_block: int, topic: str, *, get=None) -> list:
    """Every log for one topic in a range, paged. RAISES on anything but an answer.

    `utils.etherscan_get` reports a failure as status "0" with an empty result —
    the same shape as a real "No records found". Only that exact message is an
    empty answer here; anything else stops the walk (rule 5).
    """
    get = get or utils.etherscan_get
    out, page = [], 1
    while True:
        doc = get({"module": "logs", "action": "getLogs", "fromBlock": from_block,
                   "toBlock": to_block, "address": cf.MESSAGE_TRANSMITTER_V2,
                   "topic0": topic, "page": page, "offset": ETHERSCAN_PAGE},
                  chain_id=HYPEREVM_CHAIN_ID)
        if str(doc.get("status")) != "1":
            if "no records" in str(doc.get("message") or "").lower():
                return out
            raise RpcError(f"etherscan getLogs: {doc.get('message')} {str(doc.get('result'))[:120]}")
        rows = doc.get("result") or []
        out.extend(rows)
        if len(rows) < ETHERSCAN_PAGE:
            return out
        if page >= ETHERSCAN_MAX_PAGES:
            raise RpcError(f"more than {ETHERSCAN_MAX_PAGES * ETHERSCAN_PAGE} logs in "
                           f"blocks {from_block}-{to_block}; refusing to read a partial range")
        page += 1


def etherscan_head(*, get=None) -> int:
    get = get or utils.etherscan_get
    doc = get({"module": "proxy", "action": "eth_blockNumber"}, chain_id=HYPEREVM_CHAIN_ID)
    result = doc.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RpcError(f"etherscan eth_blockNumber: {doc}")
    return int(result, 16)


def walk_etherscan(previous: dict | None, *, get=None, clock=time.monotonic,
                   max_chunks=MAX_CHUNKS, seconds=READ_SECONDS) -> tuple[list, dict]:
    head = etherscan_head(get=get)
    cursor = (previous or {}).get("last_block")
    start = (int(cursor) + 1) if cursor else max(0, head - FIRST_RUN_BLOCKS)
    first = start
    deadline = clock() + seconds
    rows, chunks, error = [], 0, None
    while start <= head and chunks < max_chunks and clock() < deadline:
        end = min(start + ETHERSCAN_CHUNK_BLOCKS - 1, head)
        try:
            logs = (etherscan_logs(start, end, cf.TOPIC_MESSAGE_RECEIVED, get=get)
                    + etherscan_logs(start, end, cf.TOPIC_MESSAGE_SENT, get=get))
        except Exception as exc:                      # noqa: BLE001 - stop, never skip
            error = f"{type(exc).__name__}: {exc}"
            break
        rows.extend(row for row in (cf.decode(log) for log in logs) if row)
        cursor, start, chunks = end, end + 1, chunks + 1
    return rows, {"head_block": head, "last_block": cursor, "windows_read": chunks,
                  "blocks_read": (int(cursor) - first + 1) if chunks else 0,
                  "lag_blocks": head - int(cursor or head), "error": error,
                  "source": "etherscan"}


def walk(previous: dict | None, *, call=rpc, clock=time.monotonic, sleep=time.sleep,
         max_windows=MAX_WINDOWS, seconds=READ_SECONDS) -> tuple[list, dict]:
    """Read forward from the cursor. Returns (rows, walk summary)."""
    head = int(call("eth_blockNumber", []), 16)
    cursor = (previous or {}).get("last_block")
    start = (int(cursor) + 1) if cursor else max(0, head - FIRST_RUN_BLOCKS)
    first = start
    deadline = clock() + seconds
    rows, windows, error = [], 0, None
    while start <= head and windows < max_windows and clock() < deadline:
        end = min(start + WINDOW_BLOCKS - 1, head)
        try:
            rows.extend(read_window(start, end, call=call))
        except Exception as exc:                      # noqa: BLE001 - stop, never skip
            error = f"{type(exc).__name__}: {exc}"
            break
        cursor = end
        start = end + 1
        windows += 1
        sleep(PACE_SECONDS)
    return rows, {"head_block": head, "last_block": cursor, "windows_read": windows,
                  "blocks_read": (int(cursor) - first + 1) if windows else 0,
                  "lag_blocks": head - int(cursor or head), "error": error, "source": "rpc"}


def main() -> int:
    from scripts.check_deposit_sentinels import hl_state
    from src.alerts import alert_circle_flow

    config = load_config()
    path = utils.DATA_DIR / "circle_flows" / "latest.json"
    try:
        with open(path) as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = {}
    his_evm, his_raw, cluster = his_identities(config)

    import os
    try:
        if os.environ.get("ETHERSCAN_API_KEY"):
            rows, summary = walk_etherscan(previous)
        else:
            rows, summary = walk(previous)
    except Exception as exc:                          # noqa: BLE001 - head unreadable
        print(f"[circle] HyperEVM unreadable: {type(exc).__name__}: {exc} — cursor kept")
        return 0

    found = []
    for row in rows:
        kind = cf.classify(row, his_evm, his_raw, cluster)
        if kind:
            found.append({**row, "kind": kind})
    alerted = list(previous.get("alerted") or [])
    alerted_set = set(alerted)
    retry = [r for r in previous.get("undelivered") or []]
    undelivered = []
    for row in retry + found:
        key = cf.finding_key(row)
        if key in alerted_set:
            continue
        state = hl_state(row["hl_account"])
        print(f"[circle] {row.get('kind')}: {row.get('direction')} {row.get('chain')} "
              f"{row.get('counterparty') or row.get('counterparty_raw')} <-> HL "
              f"{row.get('hl_account')} ${float(row.get('amount_usd') or 0):,.2f} "
              f"tx {row.get('tx_hash')}")
        if alert_circle_flow(row["kind"], row, state):
            alerted.append(key)
            alerted_set.add(key)
        else:
            undelivered.append(row)

    ins = [r for r in rows if r["direction"] == "in"]
    outs = [r for r in rows if r["direction"] == "out"]
    report = {
        "computed_at": datetime.now(UTC).isoformat(),
        **summary,
        "deposits_read": len(ins), "withdrawals_read": len(outs),
        "deposit_usd": round(sum(r["amount_usd"] for r in ins), 2),
        "withdrawal_usd": round(sum(r["amount_usd"] for r in outs), 2),
        "his_identities": {"evm": len(his_evm), "raw": len(his_raw), "accounts": len(cluster)},
        # Every flow touching one of his Hyperliquid accounts, kept as a record
        # of who funds him and where he withdraws to, whether or not it tripped.
        "cluster_flows": ((previous.get("cluster_flows") or [])
                          + [r for r in rows if (r.get("hl_account") or "") in cluster])[-KEEP_FINDINGS:],
        "findings": ((previous.get("findings") or []) + found)[-KEEP_FINDINGS:],
        "alerted": alerted[-KEEP_ALERTED:],
        "undelivered": undelivered,
    }
    save_latest(str(path.parent), report)
    print(f"[circle] read {summary['windows_read']} window(s) to block {summary['last_block']} "
          f"(head {summary['head_block']}, {summary['lag_blocks']} behind)"
          + (f", stopped: {summary['error']}" if summary["error"] else "")
          + f"; {len(ins)} deposit(s) ${report['deposit_usd']:,.0f}, {len(outs)} withdrawal(s) "
          f"${report['withdrawal_usd']:,.0f}; {len(found)} finding(s), {len(undelivered)} undelivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
