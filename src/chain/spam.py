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

    `volume` is a counterparty map, so this only answers the question for a
    counterparty. The swept wallet is deliberately absent from that map
    (see counterparty_volume), so passing it here reads as volume 0.0 and any
    counterparty sharing its head/tail beats it — the wallet would be judged a
    forgery of its own counterparty. Callers must exclude the swept wallet
    themselves; `forged_side` does it for them.
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


def forged_side(record: dict, volume, *, wallet: str | None = None,
                dust_usd: float = 1.0, prefix: int = 4,
                suffix: int = 4,
                protected: set | None = None) -> tuple[str, str] | None:
    """(the forgery on this record, the address it forges), or None.

    The single definition of "which side of this record is a forgery". The
    classifier and the rollup both need it, and deriving it twice is how the
    quarantine reason and the address it is filed under drift apart.

    `wallet` is the address being swept, and is never a candidate. It is absent
    from `volume` by construction — counterparty_volume excludes it — so
    evaluating it reads as $0.00 of volume, which any counterparty sharing its
    first-4/last-4 hex and clearing dust_usd beats on the strict-ordering test.
    A single ~$1 transfer from a vanity forgery would therefore convict the
    swept wallet of forging its own counterparty, and quarantine every record
    of the sweep — real money silently reclassified as noise, with the cursor
    advancing past it and only an address-keyed count left behind.

    `protected` is the operator's ground truth — `config.target_wallet` and
    `known_self_wallets` — and extends that same immunity to an address that is
    merely a COUNTERPARTY of this sweep. Guarding only the swept wallet was not
    enough: on the live ledger the TARGET carried 796 quarantined records of
    canonical Arbitrum USDC, convicted of forging `0x45d2e417…`, because the
    twin outspent him inside somebody else's sweep. On the chain he has 9,625
    token transfers against the twin's 698 — a local count answering a global
    question, which is rule 9 reaching a different file.

    One-sided on purpose: it stops a protected address being called the
    forgery, and does nothing to stop the address forging IT from being caught.
    These are precisely the addresses worth poisoning, so the net stays up.
    """
    w = (wallet or "").lower()
    safe = {(a or "").lower() for a in (protected or ())}
    for side in ((record.get("src") or ""), (record.get("dst") or "")):
        s = side.lower()
        if not s or s == w or s in safe:
            continue
        mimicked = is_lookalike(s, volume, prefix=prefix, suffix=suffix,
                                dust_usd=dust_usd)
        if mimicked:
            return s, mimicked
    return None


def ground_truth_addresses(config: dict) -> set[str]:
    """Addresses the OPERATOR has declared, lowercased. Never an inference.

    `config.target_wallet` plus `config.known_self_wallets`, and deliberately
    nothing else. A roster CONFIRMED tier is measurement, not the operator
    speaking — `roster.assign_tier` draws exactly that line, "known_self is
    operator ground truth from config and outranks measurement" — and letting
    a tier grant forgery immunity would let one detector's inference silence
    another detector.
    """
    out = {(config.get("target_wallet") or "").lower()}
    out |= {(a or "").lower() for a in (config.get("known_self_wallets") or ())}
    return {a for a in out if a}


def classify_spam(record: dict, volume, *, wallet: str | None = None,
                  dust_usd: float = 1.0,
                  prefix: int = 4, suffix: int = 4,
                  protected: set | None = None) -> str | None:
    """Why this record is noise, or None if it is real money.

    Order is deliberate. The lookalike check runs before the dust check because
    a forgery is almost always sub-dust, and reporting it as "dust" would throw
    away the mimic relationship that makes it worth recording.

    Pass `wallet` — the address being swept — so it is never judged a forgery
    of its own counterparty. See forged_side.
    """
    if forged_side(record, volume, wallet=wallet, dust_usd=dust_usd,
                   prefix=prefix, suffix=suffix, protected=protected):
        return "lookalike"

    amount = record.get("amount")
    if amount is not None and float(amount) == 0.0:
        return "zero_value"

    # WHY there is no number decides this, never the absence of the number.
    # Three different states used to collapse into one `amount_usd is None`
    # test, and two of them are real money:
    #
    #   price_unavailable — a known major we could not price today. A price
    #     outage is not evidence about the transfer.
    #   unpriced          — a token we do not value at all. Still an observed
    #     movement between two addresses, and discovery runs on edges, not on
    #     dollars. Measured: 332,636 records were destroyed this way, only 14%
    #     of them carrying an advertising-shaped symbol.
    #   impostor_token    — we have PROVEN this is a forgery wearing a
    #     stablecoin's ticker. That is a finding, and it is quarantined.
    #
    # Keeping the first two is rule 6 at the other end of the pipe: a missing
    # value must not be priced as 0.0, and must not be grounds for deletion
    # either. Quarantined records never reach the substrate and the cursor
    # advances past them, so the loss is permanent.
    basis = record.get("value_basis")
    if basis == "impostor_token":
        return "impostor_token"
    if basis in ("price_unavailable", "unpriced"):
        return None

    usd = record.get("amount_usd")
    if usd is None:
        # No basis recorded at all — an older record, or a caller that did not
        # go through value_usd. Kept for the same reason as `unpriced`: we
        # cannot tell, and "we cannot tell" is not "it is not there" (rule 5).
        return None
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
