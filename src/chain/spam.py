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


def counterparty_volume(records: list[dict], wallet: str) -> dict[str, float]:
    """Total priced USD moved with `wallet`, per counterparty address.

    Replaces the flat "is this address real?" set. Magnitude is what
    distinguishes a forgery from the address it forges: the attack only makes
    sense against a counterparty richer than the attacker's own address, so
    comparing volumes tells the two apart where a membership test cannot.
    """
    w = (wallet or "").lower()
    volume: dict[str, float] = {}
    for rec in records:
        usd = rec.get("amount_usd")
        # Skip unpriced records — they are not evidence of value.
        if usd is None:
            continue
        src, dst = (rec.get("src") or "").lower(), (rec.get("dst") or "").lower()
        # Identify the counterparty: the side that is not the wallet.
        counterparty = dst if src == w else src
        if counterparty and counterparty != w:
            volume[counterparty] = volume.get(counterparty, 0.0) + float(usd)
    return volume


def is_lookalike(addr: str, volume, *, prefix: int = 4, suffix: int = 4,
                 dust_usd: float = 1.0) -> str | None:
    """The real address this one is forging, or None.

    Returned rather than a bool because *which* address is being mimicked is
    itself intelligence: forgers target addresses that received large sums, so
    the mimic list points at the counterparties that matter.

    An address A is a forgery of R only if R has moved strictly more value
    with the wallet than A has. This ordering distinguishes a forgery from
    the address it forges: forgers attack addresses richer than themselves.
    """
    a = (addr or "").lower()
    if not a.startswith("0x") or len(a) != 42:
        return None
    a_volume = volume.get(a, 0.0)
    head, tail = a[2:2 + prefix], a[-suffix:]
    for real in volume:
        r = (real or "").lower()
        if r == a or len(r) != 42:
            continue
        r_volume = volume.get(r, 0.0)
        # Anchors must have moved real value to be worth forging.
        if r_volume < dust_usd:
            continue
        # Match only if the real address moved strictly more than the candidate.
        if r_volume > a_volume and r[2:2 + prefix] == head and r[-suffix:] == tail:
            return r
    return None


def derive_real_counterparties(records: list[dict], wallet: str,
                               dust_usd: float = 1.0) -> set[str]:
    """Addresses that moved priced value >= dust_usd with `wallet`.

    Deliberately runs before spam classification, on valued records: the
    lookalike rule needs anchors that cleared the dust bar. This returns
    addresses >= dust_usd, which is a weaker statement than "genuine" — a
    forgery can pay >= dust_usd and land here. The forgery/original distinction
    is made by volume ordering in is_lookalike, which checks that the anchor
    has moved strictly more value than the candidate.
    """
    vol = counterparty_volume(records, wallet)
    return {a for a, v in vol.items() if v >= dust_usd}


def classify_spam(record: dict, volume, *, dust_usd: float = 1.0,
                  prefix: int = 4, suffix: int = 4) -> str | None:
    """Why this record is noise, or None if it is real money.

    Order is deliberate. The lookalike check runs before the dust check because
    a forgery is almost always sub-dust, and reporting it as "dust" would throw
    away the mimic relationship that makes it worth recording.
    """
    for side in ((record.get("src") or ""), (record.get("dst") or "")):
        s = side.lower()
        if is_lookalike(s, volume, prefix=prefix, suffix=suffix,
                        dust_usd=dust_usd):
            return "lookalike"

    amount = record.get("amount")
    if amount is not None and float(amount) == 0.0:
        return "zero_value"

    if record.get("value_basis") == "price_unavailable":
        # A known major we could not price is not noise. Quarantining it would
        # discard a potentially large real transfer on the strength of a price
        # outage — and quarantined records never reach the substrate at all,
        # so the loss is permanent once the cursor has advanced past them.
        return None

    usd = record.get("amount_usd")
    if usd is None:
        return "unpriced_token"
    if float(usd) < dust_usd:
        return "dust"
    return None


def rollup(records: list[dict], wallet: str) -> list[dict]:
    """Aggregate quarantined records to one entry per address.

    Stored instead of the records themselves: 1,842 junk rows must not live in
    git forever to prove a count. `asset`/`token_address` are retained so a
    legitimate token the registry does not yet know is visible and can be added
    to assets.py, rather than silently discarded on every future run.
    """
    w = (wallet or "").lower()
    by_addr: dict[str, dict] = {}
    for rec in records:
        if not rec.get("spam"):
            continue
        # `forged` is set by the classifier, which knows which side matched.
        # Re-deriving it here from `mimics` cannot work: a poisoning transfer
        # arrives in both directions, so "the side that is not the mimicked
        # address" is the target's own wallet half the time.
        src = (rec.get("src") or "").lower()
        dst = (rec.get("dst") or "").lower()
        counterparty = dst if src == w else src
        addr = (rec.get("forged") or counterparty or dst or "").lower()
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
