"""Every fill on Hyperliquid, by user, from SQD's public portal — keyless.

Measured 2026-10-14 (`docs/incident-log.md`): `portal.sqd.dev` serves the
`hyperliquid-fills` dataset as JSON lines, filtered server-side by coin or user,
real-time (its head is the venue's current block), back to block 750,000,000
(~October 2025), with no key. Twenty minutes of every fill on his 43 markets —
63,318 fills, 2,718 distinct takers, 15.6 MB — came back in ONE request in one
second. Hyperliquid's own API caps any user at 2,000 fills and names takers only
on the live `trades` websocket, which the tape could hold open for 150 s in 20
min. This feed makes the venue-wide behavioural net CONTINUOUS at zero
Hyperliquid API cost.

Two rules carried over from `cctp_feed`:

* **Strict.** A failed read raises `FeedError`; it never returns an empty page,
  because to a cursor walker an empty page means "you have reached the present".
* **A cursor never skips an unread range.** `stream` returns `last_block`, the
  last block whose fills were read in full; the caller persists it and resumes
  from the next one. An overloaded portal (HTTP 529, `retry-after`) is waited on
  a bounded number of times and then reported as incomplete, cursor unmoved.

`taker_orders` turns fills into the shape `tape.taker_orders` produces from the
websocket, so everything downstream (sketches, census, newcomers) is unchanged.
"""

from __future__ import annotations

import json
import time

PORTAL = "https://portal.sqd.dev/datasets/hyperliquid-fills"
FILL_FIELDS = {"user": True, "coin": True, "px": True, "sz": True, "time": True,
               "tid": True, "side": True, "hash": True, "crossed": True}
BLOCK_FIELDS = {"number": True, "timestamp": True}
# Measured: ~14 L1 blocks a second, so a window in minutes is ~840 blocks a minute.
BLOCKS_PER_MINUTE = 840
OVERLOAD_STATUS = 529
DEFAULT_RETRY_AFTER = 10
MAX_OVERLOADS = 6
REQUEST_TIMEOUT = (10, 90)


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
    """The portal's current block number. Raises when it cannot be read."""
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


def normalise_fill(raw: dict, block: int | None = None) -> dict | None:
    """A fill as this project stores them: lower-cased user, floats, ints."""
    if not isinstance(raw, dict):
        return None
    user = str(raw.get("user") or "").lower()
    coin = raw.get("coin")
    try:
        px, sz, ts = float(raw.get("px")), float(raw.get("sz")), int(raw.get("time"))
    except (TypeError, ValueError):
        return None
    if not user.startswith("0x") or not coin or sz <= 0 or px <= 0:
        return None
    return {"user": user, "coin": str(coin), "px": px, "sz": sz, "time": ts,
            "side": raw.get("side"), "hash": raw.get("hash"), "tid": raw.get("tid"),
            "crossed": bool(raw.get("crossed")), "block": block}


def parse_lines(text: str) -> tuple[list[dict], int | None, int | None]:
    """(fills, last block number, last block timestamp ms) from a JSON-lines body."""
    fills: list[dict] = []
    last_block = last_ts = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        header = doc.get("header") or {}
        number = header.get("number")
        if isinstance(number, int):
            last_block = number
            last_ts = header.get("timestamp")
        for raw in doc.get("fills") or []:
            fill = normalise_fill(raw, number if isinstance(number, int) else None)
            if fill:
                fills.append(fill)
    return fills, last_block, last_ts


def stream(from_block: int, to_block: int, *, coins=None, users=None, post=None,
           sleep=time.sleep, max_requests: int = 60, max_bytes: int = 120_000_000,
           max_overloads: int = MAX_OVERLOADS) -> dict:
    """Every fill from `from_block` to `to_block` inclusive, bounded.

    Returns {"fills", "last_block" (the last block read in full, or None when
    nothing was read), "last_ts", "requests", "overloads", "bytes", "complete"}.
    `complete` is False when a bound stopped the walk short of `to_block`; the
    caller resumes from `last_block + 1` next run. Raises `FeedError` on any
    answer that is not fills.
    """
    post = post or _post()
    selector: dict = {}
    if coins:
        selector["coin"] = sorted({str(c) for c in coins})
    if users:
        selector["user"] = sorted({str(u).lower() for u in users})
    body_base = {"type": "hyperliquidFills", "fields": {"fill": FILL_FIELDS, "block": BLOCK_FIELDS},
                 "fills": [selector]}
    fills: list[dict] = []
    cursor = int(from_block)
    last_block = last_ts = None
    requests_made = overloads = total_bytes = 0
    complete = False
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
        if status == OVERLOAD_STATUS:
            overloads += 1
            if overloads > max_overloads:
                break
            try:
                wait = int((getattr(r, "headers", {}) or {}).get("retry-after", DEFAULT_RETRY_AFTER))
            except (TypeError, ValueError):
                wait = DEFAULT_RETRY_AFTER
            sleep(max(1, min(60, wait)))
            continue
        if status != 200:
            raise FeedError(f"stream answered {status}: {str(getattr(r, 'text', ''))[:160]}")
        text = r.text
        total_bytes += len(text)
        page, page_last, page_ts = parse_lines(text)
        if page_last is None:
            # No blocks in the answer: the portal has nothing more for this range.
            complete = True
            break
        fills.extend(page)
        last_block, last_ts = page_last, page_ts
        if page_last >= to_block:
            complete = True
            break
        cursor = page_last + 1
    return {"fills": fills, "last_block": last_block, "last_ts": last_ts,
            "requests": requests_made, "overloads": overloads, "bytes": total_bytes,
            "complete": complete}


def taker_orders(fills: list[dict]) -> list[dict]:
    """Group a user's crossing fills into taker orders, in `tape.taker_orders` shape.

    `crossed` is the fill's own statement that it took liquidity; fills of one
    taker order share its L1 hash. Maker fills are left out, exactly as the
    websocket tape only ever saw the aggressor.
    """
    groups: dict = {}
    for fill in fills or []:
        if not fill.get("crossed"):
            continue
        key = (fill.get("hash"), fill["user"], fill["coin"])
        order = groups.setdefault(key, {"wallet": fill["user"], "coin": fill["coin"],
                                        "side": fill.get("side"), "base_size": 0.0,
                                        "t": fill["time"], "first_px": fill["px"], "taker": True})
        order["base_size"] += fill["sz"]
        if fill["time"] < order["t"]:
            order["t"], order["first_px"] = fill["time"], fill["px"]
    for order in groups.values():
        order["base_size"] = round(order["base_size"], 10)
    return sorted(groups.values(), key=lambda o: o["t"])


def blocks_for_minutes(minutes: float) -> int:
    return max(1, int(minutes * BLOCKS_PER_MINUTE))
