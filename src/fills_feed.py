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
# Below this many blocks (~2 minutes) a refused range is waited on, not halved.
MIN_NARROW_BLOCKS = 1_500
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
    # Measured 2026-10-14: "overloaded" (529) is answered per request SHAPE —
    # the same range refused at one width is served at a narrower one in the
    # same second — so a refusal first halves the range asked for, and only
    # waits the retry-after once the range is already small. Narrowing is safe:
    # the cursor advances only past blocks read in full.
    narrow = None
    while cursor <= to_block:
        if requests_made >= max_requests or total_bytes >= max_bytes:
            break
        stop = int(to_block) if narrow is None else min(int(to_block), cursor + narrow)
        body = {**body_base, "fromBlock": cursor, "toBlock": stop}
        try:
            r = post(f"{PORTAL}/stream", body)
        except Exception as exc:  # noqa: BLE001 - transport
            raise FeedError(f"stream unreadable: {type(exc).__name__}: {exc}") from exc
        requests_made += 1
        status = getattr(r, "status_code", None)
        if status == OVERLOAD_STATUS:
            span = stop - cursor + 1
            if span > MIN_NARROW_BLOCKS:
                narrow = max(MIN_NARROW_BLOCKS, span // 2)
                continue
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
        if narrow is not None:
            # Served: widen again, so one refusal does not price the whole walk.
            narrow = narrow * 2 if narrow * 2 < int(to_block) - cursor else None
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


# --- time -> block ----------------------------------------------------------
#
# The portal is addressed by block, this project's records by millisecond. With
# an empty filter the portal answers header-only lines (block number and its
# timestamp) for a range, in ~0.1 s, so a block for a time is found by
# interpolation search on cheap header reads. Checkpoints are cached and
# monotone; the caller persists them (`data/state/sqd_block_index.json`).
START_BLOCK = 750_000_000
# The dataset's first block and its timestamp (2025-10-02 16:24:56 UTC), measured
# 2026-10-14; the search is seeded with it so the first guess interpolates from
# the real start rather than from zero.
START_TS_MS = 1_759_422_296_717
# How close a found block must sit to the asked time. The history census pads
# every window by minutes, so a few seconds either way change nothing.
BLOCK_TOLERANCE_MS = 20_000
MAX_SEARCH_STEPS = 40


def header_at(block: int, *, post=None, span: int = 400, sleep=time.sleep) -> tuple[int, int]:
    """(block number, timestamp ms) of the first block the portal reports at or
    after `block`. Raises FeedError when nothing can be read there.

    Measured 2026-10-14: the portal's "overloaded" (529) answer is per request
    shape, not per client — the same block answered 529 at span 400 and 200 at
    span 10 in the same second — so a refusal is retried with a different span
    and a small shift BEFORE waiting the retry-after it asks for.
    """
    post = post or _post()
    shapes = [(0, span), (0, 10), (3, 25), (11, 60), (0, 10)]
    r = None
    waited = 0
    for attempt in range(len(shapes) * (MAX_OVERLOADS + 1)):
        shift, width = shapes[attempt % len(shapes)]
        body = {"type": "hyperliquidFills", "fromBlock": int(block) + shift,
                "toBlock": int(block) + shift + width,
                "fields": {"block": BLOCK_FIELDS}, "fills": [{"coin": ["__NONE__"]}]}
        try:
            r = post(f"{PORTAL}/stream", body)
        except Exception as exc:  # noqa: BLE001 - transport
            raise FeedError(f"header unreadable: {type(exc).__name__}: {exc}") from exc
        if getattr(r, "status_code", None) != OVERLOAD_STATUS:
            break
        if attempt % len(shapes) == len(shapes) - 1:
            if waited >= MAX_OVERLOADS:
                break
            waited += 1
            try:
                wait = int((getattr(r, "headers", {}) or {}).get("retry-after", DEFAULT_RETRY_AFTER))
            except (TypeError, ValueError):
                wait = DEFAULT_RETRY_AFTER
            sleep(max(1, min(60, wait)))
    if getattr(r, "status_code", None) != 200:
        raise FeedError(f"header answered {getattr(r, 'status_code', '?')} at {block}")
    for line in str(r.text).splitlines():
        line = line.strip()
        if not line:
            continue
        header = (json.loads(line).get("header") or {})
        number, ts = header.get("number"), header.get("timestamp")
        if isinstance(number, int) and isinstance(ts, int) and ts > 0:
            return number, ts
    raise FeedError(f"no header between {block} and {block + span}")


class BlockIndex:
    """Monotone (block, timestamp) checkpoints with interpolation search."""

    def __init__(self, points=None):
        self.points: list[tuple[int, int]] = [(START_BLOCK, START_TS_MS)]
        for p in points or []:
            try:
                self.add(int(p[0]), int(p[1]))
            except (TypeError, ValueError, IndexError):
                continue

    def add(self, block: int, ts: int) -> None:
        if block <= 0 or ts <= 0:
            return
        self.points.append((int(block), int(ts)))
        self.points = sorted(set(self.points))

    def bracket(self, ts: int) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        lo = hi = None
        for p in self.points:
            if p[1] <= ts:
                lo = p
            elif hi is None:
                hi = p
                break
        return lo, hi

    def guess(self, ts: int, head: tuple[int, int]) -> int:
        lo, hi = self.bracket(ts)
        if lo is not None and lo[1] == ts:
            return lo[0]
        if hi is None:
            hi = head
        if lo is None:
            lo = (START_BLOCK, START_TS_MS)
        if lo[0] >= hi[0] or lo[1] >= hi[1]:
            return max(START_BLOCK, min(lo[0], hi[0]))
        frac = (ts - lo[1]) / (hi[1] - lo[1])
        block = lo[0] + int(frac * (hi[0] - lo[0]))
        return max(lo[0], min(hi[0], block))

    def block_at(self, ts: int, *, post=None, head: tuple[int, int] | None = None,
                 tolerance_ms: int = BLOCK_TOLERANCE_MS) -> int:
        """The block whose timestamp is within `tolerance_ms` of `ts` (at or just
        before it when the bracket is tight). Raises FeedError when the portal
        cannot be read; never guesses silently."""
        ts = int(ts)
        if head is None:
            head_block = head_number(post=post)
            head = header_at(max(START_BLOCK, head_block - 50), post=post)
            self.add(*head)
        if ts >= head[1]:
            return head[0]
        for _ in range(MAX_SEARCH_STEPS):
            lo, hi = self.bracket(ts)
            if lo is not None and ts - lo[1] <= tolerance_ms:
                return lo[0]
            if lo is not None and hi is not None and hi[0] - lo[0] <= 1:
                return lo[0]
            block = self.guess(ts, head)
            if lo is not None and block <= lo[0]:
                block = lo[0] + 1
            if hi is not None and block >= hi[0]:
                block = max(lo[0] + 1 if lo else START_BLOCK, hi[0] - 1)
            found = header_at(block, post=post)
            if found in self.points:
                # The portal reported the same checkpoint again: step past it.
                found = header_at(found[0] + 1, post=post)
            self.add(*found)
        raise FeedError(f"block for {ts} not found in {MAX_SEARCH_STEPS} steps")


def head_number(*, post=None) -> int:
    """The head block through the same transport the search uses."""
    return head()
