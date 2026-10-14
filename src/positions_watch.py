"""What his book is doing: position changes on every wallet of his, for the owner.

The owner copy-trades him by hand. The close watch reads each of his wallets
every ~10 minutes and, until 2026-10-14, kept only the account value — the
one thing a copy-trader acts on, the BOOK, was read and thrown away. This
module keeps it: the positions of the target, his known wallets and every
roster-CONFIRMED wallet, across the main dex and every HIP-3 dex, and the
changes between two readings (opened, closed, flipped, resized).

Pure functions. `scripts/check_watchlist.py` reads, stores the books in
`data/watchlist/positions.json` (watch.yml is the single writer) and sends ONE
notice per run through `alerts.send_notice` — never `send_alert`: this is the
owner's working signal, not a detection, and it must touch neither the severity
gate nor delivery health. A wallet's first reading is a baseline, never news.
"""

from __future__ import annotations

# A size change below this share of the larger side is a top-up, not news.
RESIZE_SHARE = 0.25
# Positions smaller than this are dust from a partial fill of a close.
MIN_NOTIONAL_USD = 100.0
KINDS = ("opened", "closed", "flipped", "resized")


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def book(state: dict | None) -> dict:
    """{coin: {size, side, entry, value, leverage}} from a clearinghouse state
    whose `assetPositions` may already merge every dex (`account_value` does).

    Signed size is kept as the venue gives it (negative = short). An entry
    without a readable size is dropped rather than priced at zero (rule 6).
    """
    out: dict = {}
    for row in (state or {}).get("assetPositions") or []:
        pos = (row or {}).get("position") if isinstance(row, dict) else None
        if not isinstance(pos, dict):
            continue
        coin = str(pos.get("coin") or "")
        size = _num(pos.get("szi"))
        if not coin or size is None or size == 0:
            continue
        value = _num(pos.get("positionValue"))
        if value is not None and abs(value) < MIN_NOTIONAL_USD:
            continue
        lev = pos.get("leverage") if isinstance(pos.get("leverage"), dict) else {}
        out[coin] = {"size": size, "side": "long" if size > 0 else "short",
                     "entry": _num(pos.get("entryPx")), "value": value,
                     "leverage": _num(lev.get("value")), "mode": lev.get("type")}
    return out


def diff(previous: dict | None, current: dict) -> list[dict]:
    """Changes from one book to the next. `previous` None → baseline, no changes."""
    if previous is None:
        return []
    changes = []
    for coin in sorted(set(previous) | set(current)):
        before, after = previous.get(coin), current.get(coin)
        if before is None and after is not None:
            changes.append(_change("opened", coin, before, after))
        elif before is not None and after is None:
            changes.append(_change("closed", coin, before, after))
        elif before["side"] != after["side"]:
            changes.append(_change("flipped", coin, before, after))
        else:
            larger = max(abs(before["size"]), abs(after["size"]))
            if larger > 0 and abs(after["size"] - before["size"]) / larger >= RESIZE_SHARE:
                changes.append(_change("resized", coin, before, after))
    return changes


def _change(kind: str, coin: str, before: dict | None, after: dict | None) -> dict:
    return {"kind": kind, "coin": coin,
            "before": None if before is None else {"size": before["size"], "side": before["side"],
                                                   "value": before.get("value")},
            "after": None if after is None else {"size": after["size"], "side": after["side"],
                                                 "value": after.get("value"),
                                                 "entry": after.get("entry"),
                                                 "leverage": after.get("leverage")}}


def _money(value) -> str:
    value = _num(value)
    if value is None:
        return "?"
    value = abs(value)
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _size(value) -> str:
    value = abs(float(value))
    return f"{value:,.0f}" if value >= 100 else f"{value:g}"


def describe(change: dict) -> str:
    """One line a phone can show: what moved, which way, how big."""
    coin, kind = change["coin"], change["kind"]
    before, after = change.get("before"), change.get("after")
    if kind == "opened":
        lev = f" {after['leverage']:g}x" if _num((after or {}).get("leverage")) else ""
        return f"OPENED {after['side']} {coin} {_size(after['size'])} ({_money(after['value'])}{lev})"
    if kind == "closed":
        return f"CLOSED {before['side']} {coin} {_size(before['size'])} ({_money(before['value'])})"
    if kind == "flipped":
        return (f"FLIPPED {coin} {before['side']} {_size(before['size'])} -> {after['side']} "
                f"{_size(after['size'])} ({_money(after['value'])})")
    direction = "up" if abs(after["size"]) > abs(before["size"]) else "down"
    return (f"{after['side'].upper()} {coin} {direction} {_size(before['size'])} -> "
            f"{_size(after['size'])} ({_money(after['value'])})")


def render(changes_by_wallet: dict, labels: dict | None = None) -> tuple[str, str] | None:
    """(title, body) for one notice covering every wallet that moved, or None."""
    labels = labels or {}
    lines = []
    total = 0
    for wallet, changes in changes_by_wallet.items():
        if not changes:
            continue
        name = labels.get(wallet) or f"{wallet[:6]}…{wallet[-4:]}"
        lines.append(f"{name}:")
        for change in changes:
            lines.append(f"  {describe(change)}")
            total += 1
    if not lines:
        return None
    wallets = sum(1 for c in changes_by_wallet.values() if c)
    title = (f"He moved: {total} position change(s)"
             + (f" across {wallets} wallets" if wallets > 1 else ""))
    return title, "\n".join(lines)


def summarise(books: dict) -> dict:
    """His aggregate book across every wallet: the FTX 'all subaccounts' view."""
    agg: dict = {}
    for wallet, positions in (books or {}).items():
        for coin, pos in (positions or {}).items():
            slot = agg.setdefault(coin, {"size": 0.0, "value": 0.0, "wallets": []})
            slot["size"] += float(pos.get("size") or 0.0)
            value = _num(pos.get("value"))
            if value is not None:
                slot["value"] += abs(value) * (1 if float(pos.get("size") or 0) > 0 else -1)
            slot["wallets"].append(wallet)
    out = {}
    for coin, slot in agg.items():
        if slot["size"] == 0:
            continue
        out[coin] = {"size": slot["size"], "side": "long" if slot["size"] > 0 else "short",
                     "net_value": round(slot["value"], 2), "wallets": sorted(set(slot["wallets"]))}
    return dict(sorted(out.items(), key=lambda kv: -abs(kv[1]["net_value"])))
