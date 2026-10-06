"""Which wallets the study reads (spec 2026-10-06 §5). Pure.

Four sources in priority order: wallets the operator pinned, roster leads that
Hyperliquid knows as traders, leads that decayed out of their tier (kept 60 days
from when the study first saw them decayed), and other detectors' finds. A
member added under 14 days ago keeps its place ahead of newcomers from its own
or a lower source, so its history can build. The target is never studied: he
is the reference every test compares against.
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


def address(value) -> str:
    text = str(value or "").strip().lower()
    return text if len(text) == 42 and text.startswith("0x") else ""


def hl_trader(row: dict) -> bool:
    """Hyperliquid knows it as a trading account of real size."""
    evidence = row.get("evidence") or {}
    value = evidence.get("hl_account_value")
    return (evidence.get("hl_role") in ("user", "subAccount")
            and isinstance(value, (int, float)) and value >= MIN_HL_VALUE)


def blocked_wallets(config: dict, roster: dict | None) -> set[str]:
    """Never studied: the target, and anything the roster measured as a service."""
    rows = [r for r in (roster or {}).get("wallets") or [] if isinstance(r, dict)]
    blocked = {address(config.get("target_wallet"))}
    blocked |= {address(r.get("wallet")) for r in rows
                if r.get("tier") == "INFRASTRUCTURE" or r.get("is_service")}
    return blocked - {""}


def by_source(config: dict, roster: dict | None, detectors: list, decayed_seen: dict,
              now_ms: int) -> tuple[dict, dict]:
    """Candidate wallets per source, each in priority order, and the clock that
    releases a decayed lead 60 days after the study first saw it decayed."""
    rows = [r for r in (roster or {}).get("wallets") or [] if isinstance(r, dict)]
    blocked = blocked_wallets(config, roster)

    def rank(row):
        value = (row.get("evidence") or {}).get("hl_account_value")
        return (-evidence_strength(row), -(value if isinstance(value, (int, float)) else 0.0),
                address(row.get("wallet")))

    ordered = sorted(rows, key=rank)
    # config.json writes watch_wallets as {"address", "why", "added"} entries;
    # a bare address string is accepted too (roster.pinned_wallets does the same).
    pinned = [address(w.get("address") if isinstance(w, dict) else w)
              for w in [*(config.get("watch_wallets") or []),
                        *(config.get("study_wallets") or [])]]
    leads = [address(r.get("wallet")) for r in ordered
             if r.get("tier") in LEAD_TIERS and hl_trader(r)]
    decayed_now = [address(r.get("wallet")) for r in ordered
                   if r.get("tier_dropped_from") in LEAD_TIERS and hl_trader(r)]
    seen = {w: (decayed_seen or {}).get(w, now_ms) for w in decayed_now if w}
    decayed = [w for w in decayed_now if w and now_ms - seen[w] <= DECAYED_KEEP_MS]
    sources = {"pinned": pinned, "roster_lead": leads, "decayed_lead": decayed,
               "detector": [address(w) for w in detectors or []]}
    return ({name: list(dict.fromkeys(w for w in wallets if w and w not in blocked))
             for name, wallets in sources.items()}, seen)


def choose(sources: dict, previous: dict | None, now_ms: int, *, blocked=(),
           max_wallets: int = MAX_WALLETS) -> list[dict]:
    """The study set, at most `max_wallets` long."""
    previous = previous or {}
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
