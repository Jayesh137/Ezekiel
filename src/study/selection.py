"""Which wallets the study reads (spec 2026-10-06 §5). Pure.

Four sources in priority order: wallets the operator pinned, roster leads that
Hyperliquid knows as traders, leads that decayed out of their tier (kept 60 days
from when the study first saw them decayed), and other detectors' finds. A
member added under 14 days ago keeps its place ahead of newcomers from its own
or a lower source, so its history can build. The target is never studied: he
is the reference every test compares against. Neither is a service, nor a config
wallet that does not trade on Hyperliquid.

Hyperliquid-present is judged on an account's TOTAL value (spot + perp) and its
30-day volume, never on perp margin: the roster's `hl_account_value` is
`webData2`'s margin summary, which reads 0 for a wallet whose money sits in spot
or on a HIP-3 dex. Measured 2026-10-07, a POSSIBLE lead held $9.37M in spot USDC
and traded $80.0M in 30 days against $0 of perp margin. A roster row whose
identity probe predates `hl_total_value` and `hl_month_volume` carries neither,
and is judged on perp margin until it is probed again (every 7 days).
"""

from __future__ import annotations

from src.roster import evidence_strength

DAY_MS = 86_400_000
MAX_WALLETS = 40
STICKY_MS = 14 * DAY_MS
DECAYED_KEEP_MS = 60 * DAY_MS
MIN_HL_VALUE = 10_000.0
SOURCES = ("pinned", "roster_lead", "decayed_lead", "detector")
LEAD_TIERS = ("CONFIRMED", "PROBABLE", "POSSIBLE")
HL_ROLES = ("user", "subAccount")


def address(value) -> str:
    text = str(value or "").strip().lower()
    return text if len(text) == 42 and text.startswith("0x") else ""


def _entry_address(entry) -> str:
    """A config entry's address: a bare string, or a dict with an "address" key
    (config.json writes watch_wallets as {"address", "why", "added"} entries;
    roster.pinned_wallets reads both forms too)."""
    return address(entry.get("address") if isinstance(entry, dict) else entry)


def _number(value) -> int | float | None:
    """The value when it is a number (an int or float, never a bool), else None."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def hl_trader(row: dict, *, must_trade: bool = False) -> bool:
    """Hyperliquid knows it as a trading account of real size.

    The role must be user or subAccount. Then, from the identity probe's
    `portfolio` read, it is a trader when it traded in the last 30 days or, unless
    `must_trade`, holds at least MIN_HL_VALUE in total (spot + perp). `must_trade`
    is for a config wallet, where a balance alone proves nothing: `0x1419e75330…`
    holds $57.0M and traded $0 in 30 days. A row probed before those two fields
    existed is judged on perp margin, as it always was, whatever `must_trade` says:
    that reading never saw volume.
    """
    evidence = row.get("evidence") or {}
    if evidence.get("hl_role") not in HL_ROLES:
        return False
    total = _number(evidence.get("hl_total_value"))
    volume = _number(evidence.get("hl_month_volume"))
    if total is None and volume is None:
        margin = _number(evidence.get("hl_account_value"))
        return margin is not None and margin >= MIN_HL_VALUE
    traded = volume is not None and volume > 0
    holds = total is not None and total >= MIN_HL_VALUE
    return traded or (holds and not must_trade)


def blocked_wallets(config: dict, roster: dict | None) -> set[str]:
    """Never studied: the target, anything the roster measured as a service, and a
    config wallet that does not trade on Hyperliquid (one with no roster row is not
    known to)."""
    rows = [r for r in (roster or {}).get("wallets") or [] if isinstance(r, dict)]
    blocked = {address(config.get("target_wallet"))}
    blocked |= {address(r.get("wallet")) for r in rows
                if r.get("tier") == "INFRASTRUCTURE" or r.get("is_service")}
    trading = {address(r.get("wallet")) for r in rows if hl_trader(r, must_trade=True)}
    blocked |= {_entry_address(e) for e in config.get("known_self_wallets") or []} - trading
    return blocked - {""}


def by_source(config: dict, roster: dict | None, detectors: list, decayed_seen: dict,
              now_ms: int) -> tuple[dict, dict]:
    """Candidate wallets per source, each in priority order, and the clock that
    releases a decayed lead 60 days after the study first saw it decayed."""
    rows = [r for r in (roster or {}).get("wallets") or [] if isinstance(r, dict)]
    blocked = blocked_wallets(config, roster)

    def value(row):
        """Total value (spot + perp) when read, else perp margin, else nothing."""
        evidence = row.get("evidence") or {}
        for key in ("hl_total_value", "hl_account_value"):
            reading = _number(evidence.get(key))
            if reading is not None:
                return reading
        return 0.0

    def rank(row):
        return (-evidence_strength(row), -value(row), address(row.get("wallet")))

    ordered = sorted(rows, key=rank)
    # config.json writes watch_wallets as {"address", "why", "added"} entries;
    # a bare address string is accepted too (roster.pinned_wallets does the same).
    # Pinned wallets are studied whatever Hyperliquid knows of them; only a blocked
    # wallet is refused.
    pinned = [_entry_address(w)
              for w in [*(config.get("watch_wallets") or []),
                        *(config.get("study_wallets") or [])]]
    leads = [address(r.get("wallet")) for r in ordered
             if r.get("tier") in LEAD_TIERS and hl_trader(r)]
    # The decay clock starts the first run that sees the lead decayed, whether or not
    # Hyperliquid could be read for it that run. Forget it on one unread run and an
    # expired lead is admitted again for a fresh 60 days.
    decayed_rows = [(address(r.get("wallet")), r) for r in ordered
                    if r.get("tier_dropped_from") in LEAD_TIERS]
    decayed_rows = [(w, r) for w, r in decayed_rows if w and w not in blocked]
    seen = {w: (decayed_seen or {}).get(w, now_ms) for w, _ in decayed_rows}
    decayed = [w for w, r in decayed_rows
               if hl_trader(r) and now_ms - seen[w] <= DECAYED_KEEP_MS]
    # A detector measured the account itself, fresher than the roster's weekly read,
    # so there is no value test. The roster's role still gates a find it has a row
    # for; a wallet it has no row for is unknown, which is not no.
    by_wallet = {address(r.get("wallet")): r for r in rows}
    found = []
    for wallet in detectors or []:
        w = address(wallet)
        known = by_wallet.get(w)
        if w and (known is None or (known.get("evidence") or {}).get("hl_role") in HL_ROLES):
            found.append((w, known))
    found.sort(key=lambda pair: -evidence_strength(pair[1] or {}))   # stable: caller's order ties
    sources = {"pinned": pinned, "roster_lead": leads, "decayed_lead": decayed,
               "detector": [w for w, _ in found]}
    return ({name: list(dict.fromkeys(w for w in wallets if w and w not in blocked))
             for name, wallets in sources.items()}, seen)


def choose(sources: dict, previous: dict | None, now_ms: int, *, blocked=(),
           max_wallets: int = MAX_WALLETS) -> list[dict]:
    """The study set, at most `max_wallets` long. Previous members are matched by
    normalised address, so a key stored in another case still finds its clock and
    still meets `blocked`."""
    previous = {address(w): row for w, row in (previous or {}).items() if address(w)}
    entries, position = [], 0
    for rank, source in enumerate(SOURCES):
        for wallet in sources.get(source, []):
            entries.append((rank, 1, position, wallet, source))
            position += 1
    for wallet, row in previous.items():
        source, since = (row or {}).get("source"), (row or {}).get("since_ms")
        if source in SOURCES and isinstance(since, int) and now_ms - since < STICKY_MS:
            entries.append((SOURCES.index(source), 0, position, wallet, source))
            position += 1
    blocked = set(blocked)
    members, taken = [], set()
    for _rank, _sticky, _position, wallet, source in sorted(entries):
        if wallet in taken or wallet in blocked or len(members) >= max_wallets:
            continue
        taken.add(wallet)
        since = (previous.get(wallet) or {}).get("since_ms")
        members.append({"wallet": wallet, "source": source,
                        "since_ms": since if isinstance(since, int) else now_ms})
    return members
