# src/watchlist.py
"""Close watch on a wallet that is probably his but is not confirmed.

The roster tiers wallets; it does not follow them. `0xdd53c529…` sits at
PROBABLE on two independent vectors — it opened at zero two days into a
six-day silence of the target's and ran to $51.3M in three weeks, and it
matches his exits on amount and timing — and until now nothing in this system
watched it specifically. It had never been swept on L1, had no action ledger,
no account-value series and no HyperEVM nonce. A withdrawal from it to an
address the target also uses is the strongest evidence this project can
produce, and it would have passed unseen.

A watched wallet is NOT a `known_self_wallet`. That list is operator ground
truth and outranks measurement; this one is a question being kept under
observation. Keeping them separate is what stops a lead quietly promoting
itself into the cluster it is being compared against.

Two kinds of finding come out of a watch, and they are not the same:

  * CONTACT — the wallet touched the target's world: him, a wallet believed
    to be his, or one of his private deposit addresses. That is an observed
    connection, a third vector, and the thing worth waking someone for.
  * CHANGE — it emptied, doubled, went quiet, approved an agent, created a
    sub-account, or started using HyperEVM. Evidence about what it is doing,
    reported once per change.

Everything here is pure: `snapshot` turns readings into a record, `changes`
diffs two records, `contacts` intersects counterparties with the target's
world. scripts/check_watchlist.py does the I/O.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

WATCHLIST_DIR = DATA_DIR / "watchlist"

# A move of this share of account value, either way, is worth saying out loud.
# Chosen to match the existing account-drop alert rather than invent a second
# notion of "a lot".
VALUE_MOVE = 0.40
# Below this the wallet has effectively been emptied, whatever the ratio says.
EMPTY_USD = 10_000.0
# It trades continuously — 164 decisions in a day when this was written — so a
# silence of days is a change of state, not a quiet weekend.
QUIET_DAYS = 3.0


def watched(config: dict) -> list[dict]:
    """Normalise `config.watch_wallets`, accepting plain addresses or objects."""
    out = []
    for entry in (config or {}).get("watch_wallets") or []:
        if isinstance(entry, str):
            entry = {"address": entry}
        if not isinstance(entry, dict):
            continue
        address = (entry.get("address") or "").strip().lower()
        if address:
            out.append({**entry, "address": address})
    return out


def snapshot(address: str, *, account_value=None, last_fill_ms=None,
             agents=None, subaccounts=None, withdrawal_destinations=None,
             hyperevm_nonce=None, role=None, read_ok: bool = True,
             errors=None) -> dict:
    """One reading of a watched wallet. Absent fields stay None, never 0."""
    return {
        "address": (address or "").lower(),
        "checked_at": datetime.now(UTC).isoformat(),
        "read_ok": bool(read_ok),
        "errors": list(errors or []),
        "role": role,
        "account_value": account_value,
        "last_fill_ms": last_fill_ms,
        "agents": sorted({(a or "").lower() for a in (agents or []) if a}),
        "subaccounts": sorted({(s or "").lower() for s in (subaccounts or []) if s}),
        "withdrawal_destinations": sorted(
            {(d or "").lower() for d in (withdrawal_destinations or []) if d}),
        "hyperevm_nonce": hyperevm_nonce,
    }


def changes(previous: dict | None, current: dict, now_ms: int | None = None) -> list[dict]:
    """What is materially different since the last reading. Pure.

    Returns [] on the first ever reading: a baseline is not news. A field that
    could not be read this time is skipped rather than compared, so an outage
    never manufactures a change.
    """
    if not current.get("read_ok"):
        return []
    if not previous:
        return []
    out: list[dict] = []

    before, after = previous.get("account_value"), current.get("account_value")
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        if before > EMPTY_USD and after <= EMPTY_USD:
            out.append({"kind": "emptied", "detail": f"${before:,.0f} -> ${after:,.0f}"})
        elif before > 0:
            move = (after - before) / before
            if abs(move) >= VALUE_MOVE:
                direction = "grew" if move > 0 else "fell"
                out.append({"kind": "value_move",
                            "detail": f"{direction} {abs(move):.0%}: "
                                      f"${before:,.0f} -> ${after:,.0f}"})

    for field, kind, label in (("agents", "new_agent", "authorised an agent"),
                               ("subaccounts", "new_subaccount", "created a sub-account"),
                               ("withdrawal_destinations", "new_withdrawal_destination",
                                "withdrew to a new destination")):
        fresh = [x for x in current.get(field) or [] if x not in (previous.get(field) or [])]
        out.extend({"kind": kind, "detail": f"{label}: {x}", "address": x} for x in fresh)

    was, now = previous.get("hyperevm_nonce"), current.get("hyperevm_nonce")
    if was == 0 and isinstance(now, int) and now > 0:
        out.append({"kind": "hyperevm_activated",
                    "detail": f"first HyperEVM transaction(s): nonce {was} -> {now}"})

    last = current.get("last_fill_ms")
    if isinstance(last, int) and last > 0:
        reference = now_ms if now_ms is not None else int(
            datetime.now(UTC).timestamp() * 1000)
        quiet_days = (reference - last) / 86_400_000
        was_quiet = False
        prev_last = previous.get("last_fill_ms")
        if isinstance(prev_last, int) and prev_last > 0:
            was_quiet = (reference - prev_last) / 86_400_000 >= QUIET_DAYS
        # Only on the transition into silence, so a dormant wallet does not
        # report the same silence every half hour.
        if quiet_days >= QUIET_DAYS and not was_quiet:
            out.append({"kind": "went_quiet",
                        "detail": f"no fills for {quiet_days:.1f} days"})
    return out


def contacts(counterparties, world: dict) -> list[dict]:
    """Counterparties of a watched wallet that belong to the target's world.

    `world` maps an address to what it is ("the target", "a known wallet of
    his", "a private deposit address of his", "roster: PROBABLE"). An observed
    transfer or action between the two is a vector the correlation and
    dormancy readings cannot supply, because both of those are inferences
    about coincidence and this is a thing that happened.
    """
    out = []
    for raw in counterparties or []:
        a = (raw or "").lower()
        if a and a in world:
            out.append({"address": a, "is": world[a]})
    return sorted(out, key=lambda c: c["address"])


def shared_infrastructure(hits: list[dict], busy: dict) -> tuple[list, list]:
    """Split contacts into (people, infrastructure) on whole-chain activity.

    Two wallets both paying Binance are not connected, and the roster can be
    wrong about that on its own: `0xd7a827fb…` funded the watched wallet with
    $43.1M and sat at POSSIBLE while carrying 590,833 transactions on
    Arbitrum, so the first CONTACT this vector ever fired was an exchange.
    `busy` maps an address to True (busy anywhere it was measured), False, or
    None. **None still alerts** — unmeasured is "we could not tell", and the
    conservative direction for a wake-someone signal is to wake them.
    """
    people, infra = [], []
    for hit in hits or []:
        (infra if busy.get(hit.get("address")) is True else people).append(hit)
    return people, infra


def contact_severity(what: str) -> str:
    """CRITICAL only for his own addresses; a roster tier is an inference."""
    return "HIGH" if str(what or "").startswith("roster:") else "CRITICAL"


def build_report(snapshots: list[dict], findings: dict) -> dict:
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "watched": len(snapshots),
        "unreadable": sorted(s["address"] for s in snapshots if not s["read_ok"]),
        "wallets": snapshots,
        "changes": findings.get("changes") or {},
        "contacts": findings.get("contacts") or {},
    }


def save(report: dict) -> None:
    save_latest(str(WATCHLIST_DIR), report)
