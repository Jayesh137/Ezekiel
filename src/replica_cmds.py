"""Every L1 action on Hyperliquid, with its signing user, from SQD's portal — keyless.

Measured 2026-10-15 (`docs/incident-log.md`): `portal.sqd.dev` serves the
`hyperliquid-replica-cmds` dataset as JSON lines — every action the chain
executed (orders, cancels, sends, withdrawals, Circle sends, agent and builder
approvals, referrals, staking, sub-account and vault transfers, multi-sig),
each with `user`, the full `action` payload, `nonce`, `vaultAddress` and an
ok/err `status` — filtered server-side by `user` and `actionType`, from block
868,700,000 (2026-01-21 16:40 UTC) to a head that trails the chain by ~10 days.
Not real-time, so it is a HISTORY instrument, not a tripwire: the explorer's
`userDetails` shows the last 300 actions and rolls a payload out within a day;
this shows all of them, for any account, with no key and no Hyperliquid API
weight.

Measured shape: a user-filtered request answers ~2,300 blocks (~3 minutes of
chain) in ~0.6 s whatever the number of users named, so one request covers his
whole world at the cost of one wallet; eight concurrent requests are served and
the rest refused with 529 and `retry-after: 10`. An `actionType` read is
data-bound instead (~2,900 blocks of a dozen rare kinds in 0.9 s).

Rules carried over from `fills_feed`:

* **Strict.** A failed read raises `FeedError`; it never returns an empty page,
  because to a cursor walker an empty page means "you have reached the present".
* **A cursor never skips an unread range.** `stream` returns `last_block`, the
  last block read in full; the caller persists it and resumes from the next one.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

PORTAL = "https://portal.sqd.dev/datasets/hyperliquid-replica-cmds"
START_BLOCK = 868_700_000
START_TS_MS = 1_769_011_189_885
ACTION_FIELDS = {"actionIndex": True, "user": True, "action": True, "nonce": True,
                 "vaultAddress": True, "status": True}
BLOCK_FIELDS = {"number": True, "timestamp": True}
BLOCKS_PER_MINUTE = 840
# Measured: a filtered request is served ~2,300 blocks at a time.
BLOCKS_PER_REQUEST = 2_300
OVERLOAD_STATUS = 529
RATE_LIMIT_STATUS = 429
# Measured 2026-10-15: a refused request (529) is served again seconds later at
# the SAME shape — the portal answers ~2,300 blocks whatever range is asked, so
# narrowing buys nothing here and each retry counts against a per-second rate
# limit (429 "Too Many Requests", hit by six workers re-asking at once). So a
# refusal is waited on briefly and the bound is on CONSECUTIVE refusals.
OVERLOAD_WAIT = 2
RATE_LIMIT_WAIT = 5
MAX_OVERLOADS = 8
REQUEST_TIMEOUT = (10, 120)

# Everything that moves value, hands out control or marks a choice. `noop` is
# a nonce invalidation (38,618 of 39,607 rare-kind actions in one sample) and
# orders/cancels are the fills' business, so neither is ever asked for.
TREASURY_TYPES = ("usdSend", "spotSend", "withdraw3", "sendToEvmWithData", "usdClassTransfer",
                  "subAccountTransfer", "subAccountSpotTransfer", "vaultTransfer", "sendAsset",
                  "cDeposit", "cWithdraw", "tokenDelegate")
CONTROL_TYPES = ("approveAgent", "approveBuilderFee", "createSubAccount", "setReferrer",
                 "registerReferrer", "convertToMultiSigUser", "multiSig", "evmUserModify",
                 "userSetAbstraction", "userDexAbstraction", "linkStakingUser", "vaultCreate",
                 "vaultModify", "vaultDistribute", "agentModify")
NON_TRADING_TYPES = TREASURY_TYPES + CONTROL_TYPES


class FeedError(RuntimeError):
    """The portal did not answer. Never an empty answer (rule 5)."""


def _post():
    import requests

    def post(url, body):
        return requests.post(url, json=body, timeout=REQUEST_TIMEOUT)
    return post


def _get():
    import requests

    def get(url):
        return requests.get(url, timeout=REQUEST_TIMEOUT)
    return get


def head(*, get=None) -> int:
    """The dataset's current block number. Raises when it cannot be read."""
    get = get or _get()
    try:
        r = get(f"{PORTAL}/head")
    except Exception as exc:  # noqa: BLE001 - transport
        raise FeedError(f"head unreadable: {type(exc).__name__}: {exc}") from exc
    if getattr(r, "status_code", None) != 200:
        raise FeedError(f"head answered {getattr(r, 'status_code', '?')}")
    try:
        number = int(r.json()["number"])
    except (ValueError, TypeError, KeyError) as exc:
        raise FeedError(f"head malformed: {exc}") from exc
    if number <= 0:
        raise FeedError(f"head is {number}")
    return number


def block_at(ts: int, *, get=None, sleep=time.sleep, max_overloads: int = MAX_OVERLOADS) -> int:
    """The first block at or after `ts` (seconds; milliseconds are folded), from
    the portal's own timestamp index. A refusal (529/429) is waited on a bounded
    number of times; anything else raises."""
    get = get or _get()
    ts = int(ts)
    if ts > 10_000_000_000:
        ts //= 1000
    r = None
    for attempt in range(max_overloads + 1):
        try:
            r = get(f"{PORTAL}/timestamps/{ts}/block")
        except Exception as exc:  # noqa: BLE001 - transport
            raise FeedError(f"block index unreadable: {type(exc).__name__}: {exc}") from exc
        if getattr(r, "status_code", None) not in (OVERLOAD_STATUS, RATE_LIMIT_STATUS):
            break
        if attempt >= max_overloads:
            break
        sleep(OVERLOAD_WAIT if r.status_code == OVERLOAD_STATUS else RATE_LIMIT_WAIT)
    if getattr(r, "status_code", None) != 200:
        raise FeedError(f"block index answered {getattr(r, 'status_code', '?')} for {ts}")
    try:
        number = int(r.json()["block_number"])
    except (ValueError, TypeError, KeyError) as exc:
        raise FeedError(f"block index malformed: {exc}") from exc
    if number <= 0:
        raise FeedError(f"block index is {number}")
    return number


def normalise_action(raw: dict, block: int | None, ts: int | None) -> dict | None:
    """One action as this project stores them: lower-cased user, typed payload."""
    if not isinstance(raw, dict):
        return None
    user = str(raw.get("user") or "").lower()
    action = raw.get("action")
    if not user.startswith("0x") or not isinstance(action, dict) or not action.get("type"):
        return None
    vault = raw.get("vaultAddress")
    status = str(raw.get("status") or "").lower() or None
    try:
        index = int(raw.get("actionIndex"))
    except (TypeError, ValueError):
        index = None
    try:
        nonce = int(raw.get("nonce")) if raw.get("nonce") is not None else None
    except (TypeError, ValueError):
        nonce = None
    return {"user": user, "type": str(action["type"]), "action": action, "nonce": nonce,
            "vault_address": str(vault).lower() if isinstance(vault, str) else None,
            "status": status, "block": block, "index": index, "time": ts}


def as_explorer_row(row: dict) -> dict:
    """The explorer `userDetails` tx shape, so `hl_actions.own_actions` reads a
    replica row unchanged. The key is `replica:<block>:<index>` (the dataset
    carries no hash); a failed action carries its status as `error`."""
    status = row.get("status")
    return {"user": row["user"], "action": row["action"], "block": row.get("block"),
            "time": row.get("time"), "hash": f"replica:{row.get('block')}:{row.get('index')}",
            "error": None if status in (None, "ok") else str(status),
            "nonce": row.get("nonce"), "vaultAddress": row.get("vault_address")}


def parse_lines(text: str) -> tuple[list[dict], int | None, int | None]:
    """(actions, last block number, last block timestamp ms) from a JSON-lines body."""
    rows: list[dict] = []
    last_block = last_ts = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        if "error" in doc and "header" not in doc:
            raise FeedError(f"portal error in body: {str(doc.get('error'))[:160]}")
        header = doc.get("header") or {}
        number = header.get("number")
        if isinstance(number, int):
            last_block = number
            last_ts = header.get("timestamp")
        for raw in doc.get("actions") or []:
            row = normalise_action(raw, number if isinstance(number, int) else None,
                                   last_ts if isinstance(last_ts, int) else None)
            if row:
                rows.append(row)
    return rows, last_block, last_ts


def selector(users=None, action_types=None, status=None) -> dict:
    """The portal's filter object. `status` is a single string ("ok"/"err"):
    measured 2026-10-15, a list is refused with "invalid type: sequence"."""
    sel: dict = {}
    if users:
        sel["user"] = sorted({str(u).lower() for u in users if u})
    if action_types:
        sel["actionType"] = sorted({str(t) for t in action_types if t})
    if status:
        sel["status"] = str(status)
    return sel


def stream(from_block: int, to_block: int, *, users=None, action_types=None, status=None,
           post=None, sleep=time.sleep, max_requests: int = 400, max_bytes: int = 200_000_000,
           max_overloads: int = MAX_OVERLOADS) -> dict:
    """Every matching action from `from_block` to `to_block` inclusive, bounded.

    Returns {"actions", "last_block" (the last block read in full, or None when
    nothing was read), "last_ts", "requests", "overloads", "bytes", "complete"}.
    `complete` is False when a bound stopped the walk short of `to_block`; the
    caller resumes from `last_block + 1`. Raises `FeedError` on any answer that
    is not actions.
    """
    post = post or _post()
    body_base = {"type": "hyperliquidReplicaCmds",
                 "fields": {"action": ACTION_FIELDS, "block": BLOCK_FIELDS},
                 "actions": [selector(users, action_types, status)]}
    actions: list[dict] = []
    cursor = int(from_block)
    last_block = last_ts = None
    requests_made = overloads = total_bytes = 0
    complete = False
    consecutive = 0
    while cursor <= to_block:
        if requests_made >= max_requests or total_bytes >= max_bytes:
            break
        body = {**body_base, "fromBlock": cursor, "toBlock": int(to_block)}
        try:
            r = post(f"{PORTAL}/stream", body)
        except Exception as exc:  # noqa: BLE001 - transport
            raise FeedError(f"stream unreadable: {type(exc).__name__}: {exc}") from exc
        requests_made += 1
        status = getattr(r, "status_code", None)
        if status in (OVERLOAD_STATUS, RATE_LIMIT_STATUS):
            overloads += 1
            consecutive += 1
            if consecutive > max_overloads:
                break
            default = RATE_LIMIT_WAIT if status == RATE_LIMIT_STATUS else OVERLOAD_WAIT
            try:
                wait = int((getattr(r, "headers", {}) or {}).get("retry-after", default))
            except (TypeError, ValueError):
                wait = default
            sleep(max(1, min(60, wait)))
            continue
        if status != 200:
            raise FeedError(f"stream answered {status}: {str(getattr(r, 'text', ''))[:160]}")
        consecutive = 0
        text = r.text
        total_bytes += len(text)
        page, page_last, page_ts = parse_lines(text)
        if page_last is None:
            complete = True
            break
        actions.extend(page)
        last_block, last_ts = page_last, page_ts
        if page_last >= to_block:
            complete = True
            break
        cursor = page_last + 1
    return {"actions": actions, "last_block": last_block, "last_ts": last_ts,
            "requests": requests_made, "overloads": overloads, "bytes": total_bytes,
            "complete": complete}


def segments(from_block: int, to_block: int, size: int) -> list[tuple[int, int]]:
    """Fixed pieces covering [from_block, to_block] exactly."""
    out = []
    start = int(from_block)
    while start <= int(to_block):
        stop = min(int(to_block), start + int(size) - 1)
        out.append((start, stop))
        start = stop + 1
    return out


def sweep_parallel(from_block: int, to_block: int, *, segment_blocks: int, workers: int = 6,
                   users=None, action_types=None, status=None, post=None, sleep=time.sleep,
                   max_requests_per_segment: int = 100_000, max_overloads: int = MAX_OVERLOADS,
                   skip: set | None = None, on_segment=None, newest_first: bool = False) -> dict:
    """Read fixed segments concurrently; each segment is a serial `stream`.

    Returns {"actions", "done": [(a, b)...], "incomplete": [(a, b)...], "requests",
    "overloads", "bytes"}. A segment is `done` only when read to its end, so a
    caller may persist `done` and resume with the rest — nothing read short is
    ever marked read. `on_segment(seg, result)` is called as each finishes.
    """
    post = post or _post()
    todo = [s for s in segments(from_block, to_block, segment_blocks) if s not in (skip or set())]
    if newest_first:
        todo.reverse()
    actions: list[dict] = []
    done: list[tuple[int, int]] = []
    incomplete: list[tuple[int, int]] = []
    totals = {"requests": 0, "overloads": 0, "bytes": 0}

    def one(seg):
        try:
            res = stream(seg[0], seg[1], users=users, action_types=action_types, status=status,
                         post=post, sleep=sleep, max_requests=max_requests_per_segment,
                         max_overloads=max_overloads)
        except FeedError as exc:
            res = {"actions": [], "complete": False, "requests": 0, "overloads": 0, "bytes": 0,
                   "error": str(exc)}
        return seg, res

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for seg, res in pool.map(one, todo):
            actions.extend(res.get("actions") or [])
            for k in totals:
                totals[k] += int(res.get(k) or 0)
            (done if res.get("complete") else incomplete).append(seg)
            if on_segment is not None:
                on_segment(seg, res)
    return {"actions": actions, "done": done, "incomplete": incomplete, **totals}


def blocks_for_minutes(minutes: float) -> int:
    return max(1, int(float(minutes) * BLOCKS_PER_MINUTE))
