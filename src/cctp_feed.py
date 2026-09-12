# src/cctp_feed.py
"""Every Circle deposit into Hyperliquid, read from one account's ledger.

The correlator's candidate pool was the Arbitrum bridge contract: every USDC
transfer into `0x2df1c51e…` is a deposit into some Hyperliquid account. That
is only one of the two ways in. A deposit through Circle's CCTP — from
Ethereum, Base, Solana, or any other Circle chain — never touches the bridge.
It is minted on HyperEVM to USDC's linked contract and forwarded on HyperCore
by that contract's own account, so the recipient's ledger shows a spot `send`
FROM `0x6b9e7731…` — which is exactly the shape of the target's own 18 Circle
deposits ($66.46M, decoded 2026-09-10 from the Arbitrum side and matched here
from the Hyperliquid side on 2026-09-12).

That makes the forwarder's ledger a complete feed of every Circle deposit
into every Hyperliquid account: `userNonFundingLedgerUpdates` on it, walked
by `startTime`, names the recipient and the amount, with no API key and no
page ceiling. A fresh wallet funded from a CEX on Ethereum or Base and
deposited through Circle — the CEX-gap migration this project fears most —
enters the candidate pool through this file and nowhere else.

Measured 2026-09-12: about 4,400 sends a day, in pages of 2,000 rows (half
of them the `spotTransfer` credits that precede each send), ascending by
time, `endTime` honoured, an empty list past the present. So the walk is
INCREMENTAL: a stored cursor, a bounded number of calls per run, and a pool
pruned to the window. A first fill of thirty days is ~130 calls; every run
after it is a handful.

Two rules carried over from the bridge reader:

  * An error is RETURNED, never swallowed. A pool that could not be read to
    the present is reported incomplete, and the cursor stays where the read
    stopped so the next run continues rather than skips.
  * A non-list answer is a failed read, not an empty feed. `[]` past the
    present is the only honest empty.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import utils
from src.utils import DATA_DIR, atomic_write_json, load_config

# USDC's `evmContract` on 2026-09-12, from `spotMeta` token index 0. Its
# HyperCore account is what forwards every Circle deposit. Re-resolved live
# on every run; this is the answer when the venue cannot be asked.
FORWARDER_FALLBACK = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"

# Observed page ceiling. A page this long may have more behind it.
PAGE_ROWS = 2000

# Sends below this are never stored. The pool is rewritten every run and
# lives in git history, so it holds only what a consumer can use: the
# correlator's floor is $100k, and a smaller deposit never reaches the
# scanner's 120-wallet priority cap among thousands of larger ones.
STORE_FLOOR_USD = 100_000.0

# Seconds between pages. The info endpoint is weight-limited per minute and
# a 2,000-row ledger page carries extra weight for its size (measured
# 2026-09-12: an unpaced walk was refused after ~20 pages). Six seconds a
# page is ~20 pages in a 120s budget, so a thirty-day first fill completes
# over a handful of runs and every run after it reads a page or two.
PACE_SECONDS = 6.0

# Kept in the pool. The correlator's window is 14 days and the scanner's is
# 30; the pool holds the longer and each consumer filters to its own.
POOL_DAYS = 30

POOL_DIR = DATA_DIR / "correlations"
POOL_NAME = "cctp_pool.json"

ZERO = "0x0000000000000000000000000000000000000000"
SYSTEM_USDC = "0x2000000000000000000000000000000000000000"


def strict_post(body: dict, *, timeout: float = 60.0, retries: int = 4, sleep=time.sleep):
    """POST to the info endpoint and RAISE on anything that is not an answer.

    `utils.hl_post` returns `[]` when every attempt fails, which is the right
    shape for a caller that treats an empty list as "nothing here" and the
    wrong one for this walker, where an empty page means "you have reached the
    present". A transport failure read as the present would advance the
    cursor past deposits that were never seen.
    """
    import requests

    url = load_config()["hyperliquid_api"]
    last = None
    for attempt in range(max(1, retries)):
        try:
            r = requests.post(url, json=body, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            last = exc
            sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            last = RuntimeError("rate limited")
            sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"info endpoint unavailable: {last}")


def resolve_forwarder(post, fallback: str = FORWARDER_FALLBACK) -> tuple[str, str | None]:
    """USDC's linked-contract address, which is the forwarder. (address, note).

    Asked of the venue every run because a re-linked token would move it. A
    failed read falls back to the last known address and SAYS so.
    """
    try:
        meta = post({"type": "spotMeta"})
        for tok in (meta or {}).get("tokens") or []:
            if tok.get("index") == 0:
                addr = ((tok.get("evmContract") or {}).get("address") or "").lower()
                if addr.startswith("0x") and len(addr) == 42:
                    return addr, None
        return fallback.lower(), "spotMeta did not name USDC's evmContract; using the stored address"
    except Exception as exc:                          # noqa: BLE001 - transport
        return fallback.lower(), f"spotMeta unreadable ({type(exc).__name__}); using the stored address"


def parse_page(rows: list, forwarder: str, excluded, floor_usd: float = STORE_FLOOR_USD) -> list[dict]:
    """The Circle deposits in one ledger page. Pure.

    A deposit is a spot `send` BY the forwarder, in USDC, to an account that
    is not excluded (the target and his known wallets are not fresh wallets;
    system addresses are not accounts). The `spotTransfer` credits that
    precede each send are the same money arriving at the forwarder and are
    skipped.
    """
    f = (forwarder or "").lower()
    ex = {(a or "").lower() for a in excluded or [] if a} | {f, ZERO, SYSTEM_USDC}
    out = []
    for e in rows or []:
        if not isinstance(e, dict):
            continue
        d = e.get("delta") or {}
        if d.get("type") != "send" or (d.get("user") or "").lower() != f:
            continue
        if str(d.get("token") or "").upper() != "USDC":
            continue
        dest = (d.get("destination") or "").lower()
        if not dest or dest in ex:
            continue
        try:
            amount = float(d.get("usdcValue") or d.get("amount") or 0)
            ts = int(e.get("time") or 0) // 1000
        except (TypeError, ValueError):
            continue
        if amount < floor_usd or not ts:
            continue
        out.append({"wallet": dest, "amount": amount, "ts": ts,
                    "hash": e.get("hash"), "via": "cctp"})
    return out


def walk(post, forwarder: str, start_ms: int, end_ms: int, *, excluded, max_calls: int,
         floor_usd: float = STORE_FLOOR_USD, seconds: float | None = None,
         pace_seconds: float = 0.0, sleep=time.sleep,
         clock=time.monotonic) -> tuple[list[dict], int, str | None]:
    """Read the forwarder's ledger from `start_ms` towards `end_ms`.

    Returns (deposits, cursor_ms, error). The cursor is the first millisecond
    NOT yet read: `end_ms` when the present was reached, else where the read
    stopped. `error` is set whenever the cursor did not reach `end_ms`.
    """
    deposits: list[dict] = []
    cursor = int(start_ms)
    calls = 0
    started = clock()
    while cursor < end_ms:
        if calls >= max_calls:
            return deposits, cursor, (f"call budget ({max_calls}) exhausted with the read "
                                      f"stopped at {cursor}: the pool is incomplete")
        if seconds is not None and clock() - started >= seconds:
            return deposits, cursor, (f"time budget ({seconds:.0f}s) exhausted with the read "
                                      f"stopped at {cursor}: the pool is incomplete")
        try:
            rows = post({"type": "userNonFundingLedgerUpdates", "user": forwarder,
                         "startTime": cursor, "endTime": int(end_ms)})
        except Exception as exc:                      # noqa: BLE001 - transport
            return deposits, cursor, f"unreadable at {cursor}: {type(exc).__name__}: {exc}"
        calls += 1
        if not isinstance(rows, list):
            return deposits, cursor, f"unreadable at {cursor}: unexpected payload"
        deposits.extend(parse_page(rows, forwarder, excluded, floor_usd))
        if len(rows) < PAGE_ROWS:
            return deposits, int(end_ms), None
        newest = max(int(e.get("time") or 0) for e in rows if isinstance(e, dict))
        # Always move forward, even if a whole page shared one timestamp.
        cursor = max(cursor + 1, newest + 1)
        if pace_seconds and cursor < end_ms:
            sleep(pace_seconds)
    return deposits, int(end_ms), None


def load_pool(path: Path) -> dict:
    try:
        with open(path) as f:
            pool = json.load(f)
        if isinstance(pool, dict):
            return pool
    except (OSError, ValueError):
        pass
    return {}


def merge(existing: list[dict], fresh: list[dict], cutoff_s: int) -> list[dict]:
    """Union by hash, pruned to the window, oldest first. Pure."""
    by_hash: dict = {}
    for d in list(existing or []) + list(fresh or []):
        if not isinstance(d, dict) or int(d.get("ts") or 0) < cutoff_s:
            continue
        key = d.get("hash") or f"{d.get('wallet')}:{d.get('ts')}:{d.get('amount')}"
        by_hash[key] = d
    return sorted(by_hash.values(), key=lambda d: (int(d.get("ts") or 0), str(d.get("hash"))))


def refresh_pool(post, *, excluded, now_ms: int | None = None, pool_days: int = POOL_DAYS,
                 max_calls: int = 120, seconds: float | None = None,
                 path: Path | None = None, floor_usd: float = STORE_FLOOR_USD,
                 forwarder_fallback: str = FORWARDER_FALLBACK,
                 pace_seconds: float = PACE_SECONDS, sleep=time.sleep) -> tuple[list[dict], str | None]:
    """Bring the stored pool up to the present, bounded. (deposits, error).

    The first run walks the whole window from `cutoff`; later runs resume
    from the stored cursor. A forwarder that changed since the pool was
    written invalidates it: rows read from a different account are a
    different feed.
    """
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff_s = now_ms // 1000 - pool_days * 86400
    # Resolved at call time through utils.DATA_DIR, which the test suite
    # repoints: a module-level path captured at import would write the real
    # pool from a test, as this one did once before the guard existed.
    path = Path(path or (utils.DATA_DIR / "correlations" / POOL_NAME))
    pool = load_pool(path)

    forwarder, note = resolve_forwarder(post, forwarder_fallback)
    existing = pool.get("deposits") or []
    cursor = int(pool.get("cursor_ms") or 0)
    if (pool.get("forwarder") or "").lower() != forwarder:
        existing, cursor = [], 0
    start = max(cursor, cutoff_s * 1000)

    fresh, new_cursor, error = walk(post, forwarder, start, now_ms, excluded=excluded,
                                    max_calls=max_calls, floor_usd=floor_usd, seconds=seconds,
                                    pace_seconds=pace_seconds, sleep=sleep)
    deposits = merge(existing, fresh, cutoff_s)
    if note:
        error = f"{note}; {error}" if error else note

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {
        "forwarder": forwarder,
        "cursor_ms": new_cursor,
        "pool_days": pool_days,
        "floor_usd": floor_usd,
        "updated_at": datetime.now(UTC).isoformat(),
        "read_error": error,
        "deposits": deposits,
    })
    return deposits, error


__all__ = ["FORWARDER_FALLBACK", "PAGE_ROWS", "STORE_FLOOR_USD", "PACE_SECONDS", "POOL_DAYS", "POOL_DIR",
           "POOL_NAME", "strict_post", "resolve_forwarder", "parse_page", "walk", "load_pool",
           "merge", "refresh_pool"]
