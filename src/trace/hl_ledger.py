# src/trace/hl_ledger.py
"""A Hyperliquid account's non-funding ledger, read strictly and normalised.

An HL account's ledger names everyone who moved value into or out of it inside
Hyperliquid — the movement an L1-only search cannot see (CLAUDE.md, "Prefer the
HL-native vectors"). Until 2026-10-04 only the TARGET's ledger was ever read;
the treasury's and `0xf078969e…`'s had never been walked, and the first hop of
the latter's held an exchange deposit address nobody had seen.

Two rules this module exists to keep:

* **A failed read is never an empty ledger** (rule 5). `utils.hl_post` answers
  `[]` on failure for any "user" request, which a cursor walker would take for
  "nothing after this point". `read` therefore uses a strict poster and returns
  the error alongside whatever pages it did get.
* **A token quantity is never a dollar value** (rule 11). An edge is valued by
  `usdcValue` (or a USDC-denominated field); a token whose `usdcValue` is zero
  while its amount is not is UNPRICED (`None`), never $0 (rule 6).
"""

from __future__ import annotations

PAGE_ROWS = 2000
CHAIN = "hyperliquid"
SOURCE = "hl_ledger"

# Ledger types that move value between two different accounts. `deposit` and
# `withdraw` are the account's own bridge legs (the L1 side carries the
# counterparty), `accountClassTransfer`/`cStakingTransfer` stay inside one
# account, and `spotGenesis` is an airdrop with no sender.
TRANSFER_TYPES = ("send", "spotTransfer", "internalTransfer", "subAccountTransfer",
                  "vaultDeposit", "vaultWithdraw")


def _low(a) -> str:
    return (a or "").strip().lower()


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN is not a number we can use


def _usd(delta: dict, amount: float | None, token: str) -> float | None:
    """Dollar value of a transfer, or None when it cannot honestly be known."""
    usdc_value = _num(delta.get("usdcValue"))
    if usdc_value is not None:
        if usdc_value == 0 and (amount or 0) > 0 and token != "USDC":
            return None
        return abs(usdc_value)
    if token == "USDC" and amount is not None:
        return abs(amount)
    return None


def normalise(entries, wallet: str) -> list[dict]:
    """Transfer edges from one account's ledger rows. Pure.

    `wallet` is whose ledger this is; vault deposits name only the vault, so the
    owner is supplied from here.
    """
    wallet = _low(wallet)
    edges: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        delta = entry.get("delta") or {}
        kind = delta.get("type")
        if kind not in TRANSFER_TYPES:
            continue
        try:
            ms = int(entry.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if kind == "vaultDeposit":
            src, dst = wallet, _low(delta.get("vault"))
            token, amount = "USDC", _num(delta.get("usdc"))
            usd = abs(amount) if amount is not None else None
        elif kind == "vaultWithdraw":
            src, dst = _low(delta.get("vault")), _low(delta.get("user")) or wallet
            token = "USDC"
            amount = _num(delta.get("netWithdrawnUsd"))
            if amount is None:
                amount = _num(delta.get("requestedUsd"))
            usd = abs(amount) if amount is not None else None
        elif kind in ("internalTransfer", "subAccountTransfer"):
            src, dst = _low(delta.get("user")), _low(delta.get("destination"))
            token, amount = "USDC", _num(delta.get("usdc"))
            usd = abs(amount) if amount is not None else None
        else:  # send / spotTransfer
            src, dst = _low(delta.get("user")), _low(delta.get("destination"))
            token = str(delta.get("token") or "USDC")
            amount = _num(delta.get("amount"))
            if amount is None:
                amount = _num(delta.get("usdc"))
            usd = _usd(delta, amount, token)
        if not src or not dst or src == dst:
            continue
        tx_hash = entry.get("hash") or ""
        edges.append({
            "id": f"{CHAIN}:{tx_hash}:{kind}:{src}:{dst}:{ms}",
            "chain": CHAIN, "ts": ms // 1000, "time_ms": ms,
            "tx_hash": tx_hash, "src": src, "dst": dst, "kind": kind,
            "asset": token, "amount": amount, "amount_usd": usd,
            "discovery_source": SOURCE,
        })
    return edges


def read(address: str, post, *, start_ms: int = 0, max_pages: int = 5):
    """Every ledger row from `start_ms`, paged. Returns (rows, error, complete).

    `post(body) -> list` must RAISE on failure (see `cctp_feed.strict_post`);
    anything else that is not a list is reported as an error, never as empty.
    Rows come back oldest first; a full page continues from its last row's
    millisecond (+1), and the row key dedupes a page boundary that splits a
    millisecond.
    """
    rows, seen, cursor, error = [], set(), int(start_ms), None
    for _ in range(max(1, max_pages)):
        try:
            page = post({"type": "userNonFundingLedgerUpdates", "user": address,
                         "startTime": cursor})
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed as empty
            error = f"{type(exc).__name__}: {exc}"[:200]
            break
        if not isinstance(page, list):
            error = f"unexpected ledger answer: {type(page).__name__}"
            break
        for row in page:
            key = (row.get("time"), row.get("hash"), str(row.get("delta")))
            if key not in seen:
                seen.add(key)
                rows.append(row)
        if len(page) < PAGE_ROWS:
            return rows, None, True
        cursor = int(page[-1].get("time") or cursor) + 1
    return rows, error, False
