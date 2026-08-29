"""Separating the target's money from the noise sprayed at it.

Measured on the live data before this module was written: 905 of the 1,000
stored transfer records moved less than a dollar, and a single address —
0x1419b0d7…2d5f, a vanity forgery of the target's own known self-wallet
0x1419e753…2d5f — accounted for 510 of them. The target has five real
counterparties. Collection was not short of capacity; spam had evicted the
signal from a fixed-size window.

Address poisoning works by generating an address matching a real
counterparty's first and last characters, then sending a zero-value transfer so
it lands in the victim's history and gets copied out of it later. That leaves a
signature this module matches exactly: at 4 leading and 4 trailing hex
characters, 11 of 14 dust-only counterparties are forgeries of either the
self-wallet or the Hyperliquid bridge.

Pure — no IO, no config reads.
"""


def is_lookalike(addr: str, real_addrs, *, prefix: int = 4,
                 suffix: int = 4) -> str | None:
    """The real address this one is forging, or None.

    Returned rather than a bool because *which* address is being mimicked is
    itself intelligence: forgers target addresses that received large sums, so
    the mimic list points at the counterparties that matter.
    """
    a = (addr or "").lower()
    if not a.startswith("0x") or len(a) != 42:
        return None
    head, tail = a[2:2 + prefix], a[-suffix:]
    for real in real_addrs:
        r = (real or "").lower()
        if r == a or len(r) != 42:
            continue
        if r[2:2 + prefix] == head and r[-suffix:] == tail:
            return r
    return None


def derive_real_counterparties(records: list[dict], wallet: str,
                               dust_usd: float = 1.0) -> set[str]:
    """Addresses that moved real money with `wallet`.

    Deliberately runs before spam classification, on valued records: the
    lookalike rule is defined against addresses that moved real money, so
    valuation has to happen first. There is no circularity — a forgery sends
    zero value, so it can never qualify as real.
    """
    w = (wallet or "").lower()
    real: set[str] = set()
    for rec in records:
        usd = rec.get("amount_usd")
        if usd is None or usd < dust_usd:
            continue
        src, dst = (rec.get("src") or "").lower(), (rec.get("dst") or "").lower()
        other = dst if src == w else src
        if other and other != w:
            real.add(other)
    return real


def classify_spam(record: dict, real_counterparties, *, dust_usd: float = 1.0,
                  prefix: int = 4, suffix: int = 4) -> str | None:
    """Why this record is noise, or None if it is real money.

    Order is deliberate. The lookalike check runs before the dust check because
    a forgery is almost always sub-dust, and reporting it as "dust" would throw
    away the mimic relationship that makes it worth recording.
    """
    for side in ((record.get("src") or ""), (record.get("dst") or "")):
        if is_lookalike(side, real_counterparties, prefix=prefix, suffix=suffix):
            return "lookalike"

    amount = record.get("amount")
    if amount is not None and float(amount) == 0.0:
        return "zero_value"

    usd = record.get("amount_usd")
    if usd is None:
        return "unpriced_token"
    if float(usd) < dust_usd:
        return "dust"
    return None


def rollup(records: list[dict]) -> list[dict]:
    """Aggregate quarantined records to one entry per address.

    Stored instead of the records themselves: 1,842 junk rows must not live in
    git forever to prove a count. `asset`/`token_address` are retained so a
    legitimate token the registry does not yet know is visible and can be added
    to assets.py, rather than silently discarded on every future run.
    """
    by_addr: dict[str, dict] = {}
    for rec in records:
        if not rec.get("spam"):
            continue
        # `forged` is set by the classifier, which knows which side matched.
        # Re-deriving it here from `mimics` cannot work: a poisoning transfer
        # arrives in both directions, so "the side that is not the mimicked
        # address" is the target's own wallet half the time.
        addr = (rec.get("forged") or rec.get("dst") or "").lower()
        mimics = rec.get("mimics")
        if not addr:
            continue
        ts = int(rec.get("ts", 0) or 0)
        entry = by_addr.get(addr)
        if entry is None:
            by_addr[addr] = {
                "address": addr,
                "reason": rec.get("spam_reason"),
                "mimics": mimics,
                "asset": rec.get("asset"),
                "token_address": rec.get("token_address"),
                "count": 1,
                "first_seen": ts,
                "last_seen": ts,
            }
            continue
        entry["count"] += 1
        entry["first_seen"] = min(entry["first_seen"], ts)
        entry["last_seen"] = max(entry["last_seen"], ts)
        entry["mimics"] = entry["mimics"] or mimics
    return sorted(by_addr.values(), key=lambda e: -e["count"])
