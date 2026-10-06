# src/boundary/logs.py
"""Event logs read strictly: Blockscout (keyless) first, Etherscan V2 as fallback.

Two rules this module exists to keep:

* A failed read is never "no logs" (rule 5). Blockscout answers an empty range
  with status "0" and "No logs found", Etherscan with "No records found". Only
  those answers are empty. A 429 from Blockscout is status "0" with a null
  result (measured 2026-10-06 on this machine after a heavy session), and
  `utils.etherscan_get` reports a transport failure as status "0" with an empty
  list — the same shape as an empty answer — so anything else RAISES.
* A cursor never passes a block it did not finish reading. A page that comes
  back full (1,000 rows) may have cut its last block in half, so the walk
  resumes AT that block and dedupes by (transaction, log index).
"""

from __future__ import annotations

import os
import time
from functools import partial
from itertools import combinations

import requests

BLOCKSCOUT_API = {
    "arbitrum": "https://arbitrum.blockscout.com/api",
    "ethereum": "https://eth.blockscout.com/api",
    "base": "https://base.blockscout.com/api",
    "optimism": "https://optimism.blockscout.com/api",
    "polygon": "https://polygon.blockscout.com/api",
}
ETHERSCAN_CHAIN_IDS = {"arbitrum": 42161, "ethereum": 1, "base": 8453, "optimism": 10,
                       "polygon": 137, "hyperevm": 999}
PAGE = 1_000
CHUNK_BLOCKS = 100_000       # ~7h of Arbitrum; one call stays fast
PACE_SECONDS = 0.35
BACKOFF_SECONDS = (2.0, 5.0, 10.0)
HOLE_SECONDS = 120          # a block-by-time answer further off than this is a hole


class LogReadError(RuntimeError):
    """A read that returned no answer. Never to be caught as "no logs"."""


def to_int(value) -> int:
    s = str(value if value is not None else "0").strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _http_get(url: str, params: dict, *, tries: int = 4, sleep=time.sleep,
              deadline: float | None = None, clock=time.monotonic) -> dict:
    """GET JSON. Backs off on 429 (Blockscout throttles a busy IP), briefly.

    A throttle that outlasts BACKOFF_SECONDS outlasts the run too: on the
    2026-10-06 dry run each throttled call slept 10+20+30+40s, the last after
    its final try, and the watch step ran 441s against its 240s limit. Never
    sleeps past `deadline` (a `clock` time), and never after the last try.
    """
    last = None
    for attempt in range(tries):
        if deadline is not None and clock() + PACE_SECONDS >= deadline:
            raise LogReadError(f"run deadline reached ({last or 'before the first try'})")
        sleep(PACE_SECONDS)
        try:
            response = requests.get(url, params=params, timeout=(10, 60))
        except requests.RequestException as exc:
            last = exc
        else:
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            last = RuntimeError("rate limited (HTTP 429)")
        if attempt + 1 < tries:
            pause = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            if deadline is not None and clock() + pause >= deadline:
                raise LogReadError(f"run deadline reached: {last}")
            sleep(pause)
    raise LogReadError(f"unavailable after {tries} tries: {last}")


def _params(address: str, topics: dict, from_block, to_block) -> dict:
    params = {"module": "logs", "action": "getLogs", "address": address,
              "fromBlock": from_block, "toBlock": to_block}
    keys = sorted(topics)
    for i in keys:
        params[f"topic{i}"] = topics[i]
    for a, b in combinations(keys, 2):     # every pair: Blockscout requires each
        params[f"topic{a}_{b}_opr"] = "and"
    return params


def rows_of(doc, source: str) -> list[dict]:
    """The log rows of one answer; [] only for a genuine empty answer."""
    if not isinstance(doc, dict):
        raise LogReadError(f"{source}: unexpected answer {type(doc).__name__}")
    result = doc.get("result")
    if str(doc.get("status")) == "1" and isinstance(result, list):
        return result
    message = str(doc.get("message") or "").lower()
    if isinstance(result, list) and not result and ("no logs" in message
                                                    or "no records" in message):
        return []
    raise LogReadError(f"{source}: {doc.get('message')} {str(result)[:160]}")


def blockscout_logs(chain: str, address: str, topics: dict, from_block, to_block,
                    *, get=None, deadline: float | None = None) -> list[dict]:
    get = get or partial(_http_get, deadline=deadline)
    try:
        doc = get(BLOCKSCOUT_API[chain], _params(address, topics, from_block, to_block))
    except LogReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport: a failed read, reported
        raise LogReadError(f"blockscout: {type(exc).__name__}: {exc}") from exc
    return rows_of(doc, "blockscout")


def etherscan_logs(chain: str, address: str, topics: dict, from_block, to_block,
                   *, get=None) -> list[dict]:
    from src.utils import etherscan_get
    get = get or etherscan_get
    params = {**_params(address, topics, from_block, to_block), "page": 1, "offset": PAGE}
    return rows_of(get(params, chain_id=ETHERSCAN_CHAIN_IDS[chain]), "etherscan")


def read_logs(chain: str, address: str, topics: dict, from_block, to_block, *,
              blockscout=None, etherscan=None, has_key=None,
              deadline: float | None = None) -> list[dict]:
    """Etherscan first when a key is set (CI); Blockscout keyless, or as fallback.

    Blockscout's index has holes: on 2026-10-06 its Arbitrum instance answered
    "Not found" for blocks 507,912,970 to at least 508,500,000, and getLogs
    over a hole answers "No logs found" — exactly what an empty range says, so
    no reader can tell the two apart. Etherscan indexes every block. Without a
    key (a local run) Blockscout is all there is.
    """
    blockscout = blockscout or partial(blockscout_logs, deadline=deadline)
    etherscan = etherscan or etherscan_logs
    has_key = bool(os.environ.get("ETHERSCAN_API_KEY")) if has_key is None else has_key
    order = ([etherscan] if has_key and chain in ETHERSCAN_CHAIN_IDS else []) + \
        ([blockscout] if chain in BLOCKSCOUT_API else [])
    if not order:
        raise LogReadError(f"no log reader for {chain}")
    failures = []
    for read in order:
        try:
            return read(chain, address, topics, from_block, to_block)
        except LogReadError as exc:
            failures.append(str(exc))
    raise LogReadError("; then ".join(failures))


def read_history(chain: str, address: str, topics: dict, *,
                 deadline: float | None = None) -> list[dict]:
    """Every log matching `topics`, genesis to head, in ONE page — or a
    LogReadError: a full page is a history cut short, never all of it (rule 5)."""
    rows = read_logs(chain, address, topics, 0, "latest", deadline=deadline)
    if len(rows) >= PAGE:
        raise LogReadError(f"{len(rows)} logs: this history needs paging; refusing a cut-short read")
    return rows


def head_block(chain: str, *, get=None, deadline: float | None = None, has_key=None) -> int:
    """The chain head: Etherscan's proxy when a key is set, else Blockscout's."""
    has_key = bool(os.environ.get("ETHERSCAN_API_KEY")) if has_key is None else has_key
    failures = []
    if has_key and chain in ETHERSCAN_CHAIN_IDS:
        from src.utils import etherscan_get
        result = etherscan_get({"module": "proxy", "action": "eth_blockNumber"},
                               chain_id=ETHERSCAN_CHAIN_IDS[chain]).get("result")
        if isinstance(result, str) and result.startswith("0x"):
            return int(result, 16)
        failures.append(f"etherscan: {result}")
    if chain not in BLOCKSCOUT_API:
        raise LogReadError(f"head of {chain} unreadable: {'; '.join(failures)}")
    get = get or partial(_http_get, deadline=deadline)
    try:
        return to_int(get(BLOCKSCOUT_API[chain],
                          {"module": "block", "action": "eth_block_number"})["result"])
    except Exception as exc:  # noqa: BLE001
        failures.append(f"blockscout: {exc}")
        raise LogReadError(f"head of {chain} unreadable: {'; '.join(failures)}") from exc


def blockscout_block_ts(chain: str, number: int, *, get=None,
                        deadline: float | None = None) -> int | None:
    """A block's own timestamp from Blockscout v2; None when it is not indexed."""
    from datetime import datetime
    get = get or partial(_http_get, deadline=deadline)
    try:
        doc = get(f"{BLOCKSCOUT_API[chain][:-len('/api')]}/api/v2/blocks/{int(number)}", {})
    except requests.HTTPError:
        return None                          # 404: the block is not in the index
    stamp = (doc or {}).get("timestamp") if isinstance(doc, dict) else None
    if not stamp:
        return None
    return int(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())


def block_at(chain: str, ts: int, *, closest: str = "before", get=None,
             deadline: float | None = None, block_ts=None, has_key=None) -> int:
    """The block at a unix time (getblocknobytime): Etherscan's when a key is set.

    A Blockscout answer is checked against the block's own timestamp: inside an
    index hole it answers the last block before the hole — asked for 2026-09-23
    19:41 and 20:13 it said 507,912,969 for both, a block from 22 hours earlier.
    """
    params = {"module": "block", "action": "getblocknobytime", "timestamp": int(ts),
              "closest": closest}
    has_key = bool(os.environ.get("ETHERSCAN_API_KEY")) if has_key is None else has_key
    failures = []
    if has_key and chain in ETHERSCAN_CHAIN_IDS:
        from src.utils import etherscan_get
        doc = etherscan_get(params, chain_id=ETHERSCAN_CHAIN_IDS[chain])
        try:
            if str(doc.get("status")) == "1":
                return to_int(doc.get("result"))
            failures.append(f"etherscan: {doc.get('message')} {doc.get('result')}")
        except (TypeError, ValueError) as exc:
            failures.append(f"etherscan: {exc}")
    if chain not in BLOCKSCOUT_API:
        raise LogReadError(f"block at {ts} on {chain} unreadable: {'; '.join(failures)}")
    get = get or partial(_http_get, deadline=deadline)
    block_ts = block_ts or partial(blockscout_block_ts, deadline=deadline)
    try:
        result = get(BLOCKSCOUT_API[chain], params).get("result")
        if isinstance(result, dict):          # Blockscout: {"blockNumber": "…"}
            result = result.get("blockNumber")
        number = to_int(result)
        stamp = block_ts(chain, number)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"blockscout: {exc}")
        raise LogReadError(f"block at {ts} on {chain} unreadable: {'; '.join(failures)}") from exc
    if stamp is None or abs(stamp - int(ts)) > HOLE_SECONDS:
        raise LogReadError(f"Blockscout index hole near {ts} on {chain}: it answered block "
                           f"{number}, stamped {stamp}")
    return number


def log_key(row: dict) -> tuple:
    return ((row.get("transactionHash") or "").lower(), str(row.get("logIndex")))


def walk(read, start_block: int, head: int, *, max_calls: int = 40, seconds: float = 60.0,
         chunk: int = CHUNK_BLOCKS, clock=time.monotonic) -> dict:
    """Read [start_block, head] in order without ever skipping a block.

    `read(lo, hi) -> rows` raises LogReadError on failure. `last_block` is the
    last block FULLY read (start_block - 1 when nothing was): the cursor a
    caller stores.
    """
    started = clock()
    found, seen = [], set()
    cursor, lo, calls, error = start_block - 1, start_block, 0, None
    while lo <= head:
        if calls >= max_calls or clock() - started >= seconds:
            break
        hi = min(lo + chunk - 1, head)
        try:
            rows = read(lo, hi)
        except LogReadError as exc:
            error = str(exc)
            break
        calls += 1
        for row in rows:
            key = log_key(row)
            if key not in seen:
                seen.add(key)
                found.append(row)
        if len(rows) >= PAGE:
            last = max(to_int(r.get("blockNumber")) for r in rows)
            if last <= lo:
                error = f"more than {PAGE} logs in block {lo}; refusing to read a partial block"
                break
            cursor, lo = last - 1, last
            continue
        cursor, lo = hi, hi + 1
    return {"logs": found, "last_block": cursor, "calls": calls,
            "complete": cursor >= head and error is None, "error": error}
