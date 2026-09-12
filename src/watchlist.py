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

# How much bigger than the target a watched wallet must be before its size is
# news. A migration shows up as capital LEAVING him and appearing elsewhere, so
# the relative size of the two accounts is the plainest reading of how far one
# has gone — and nothing here compared them until 2026-09-12.
#
# The comparison that motivated this was done BY HAND and was wrong, which is
# the best argument for it existing in code. Summing his perp and `xyz` from
# `data/account/latest.json` gave $24.1M against the watched wallet's $53.2M —
# "more than twice the account it is a candidate for". It omitted **$44.7M of
# spot USDC**. Read properly, both sides through one function, he is worth
# $62.5M and the watched wallet $54.1M: **0.86x, and nothing fires.** Whoever
# next reaches for a quick ratio from the stored account file: that file is
# perp + hip3 + spot in three separate places, and leaving one out moves the
# answer by a factor of three.
#
# A band, not parity, because both are live trading books: he fell 42% in a
# single day on a $138M notional short while `withdrawable` stayed $0.00, which
# is the market marking him, not money moving. At parity the pair would flap
# across the line on noise like that; 15% clear of it takes a real difference.
OUTGREW_TARGET = 1.15


def _ratio(value, target):
    """`value` as a multiple of `target`, or None if either is not a reading.

    Rule 6: never price a missing value as 0.0. A target of zero is not a
    wallet infinitely outgrown, it is a wallet with nothing in it, and dividing
    by it would report the largest ratio the system can express.
    """
    if not isinstance(value, (int, float)) or not isinstance(target, (int, float)):
        return None
    if target <= 0:
        return None
    return round(value / target, 4)


# The cap exists to protect the CADENCE, which is the whole value of this
# watch. Measured 2026-09-12: a run reads roughly a dozen endpoints per wallet
# and took 60-73s for one, against an 8-minute job ceiling and a 10-minute
# dispatch interval. A wallet's first L1 sweep can spend the full
# SWEEP_SECONDS=45 budget; afterwards it resumes from a cursor and costs
# ~20-30s. Six is therefore about three minutes in the steady state and stays
# inside the ceiling even on a run where several wallets are swept for the
# first time — while being twice what the roster grades highly today.
MAX_WATCHED = 6

# Tiers worth a per-run read, strongest first. POSSIBLE is deliberately out:
# there are 140 of them and the tier is an inference from a single weak vector,
# so spending the fast job on them would cost the wallets that matter.
WATCHED_TIERS = ("CONFIRMED", "PROBABLE")


def watched(config: dict, roster: dict | None = None) -> list[dict]:
    """Every wallet the close watch should read this run.

    Two sources, and the order matters. `config.watch_wallets` is the
    operator's own list and always comes first. The roster supplies the rest:
    any wallet it grades CONFIRMED or PROBABLE is a wallet we believe is his,
    and until 2026-09-12 nothing read those per-wallet at all — `0xf078969e…`
    sat CONFIRMED at 0.84, two-way with him at $155M/$135M and sharing his
    Binance deposit address, while no job asked it for an agent, a sub-account,
    a withdrawal destination or a HyperEVM nonce.

    Auto-added wallets are marked `source: "roster"`. That keeps the
    distinction this project insists on: being watched is a QUESTION under
    observation, never ground truth, and nothing here may leak into
    `known_self_wallets` or the cluster.

    The target himself is never included — the collector reads him every run,
    and watching him too would put a second writer on `data/actions/`.
    """
    out, seen = [], set()
    target = ((config or {}).get("target_wallet") or "").strip().lower()
    # Operator ground truth. A wallet on this list is already his, so a contact
    # with his world settles nothing — see `contacts_are_news`.
    known_self = {(w or "").strip().lower()
                  for w in (config or {}).get("known_self_wallets") or [] if w}

    for entry in (config or {}).get("watch_wallets") or []:
        if isinstance(entry, str):
            entry = {"address": entry}
        if not isinstance(entry, dict):
            continue
        address = (entry.get("address") or "").strip().lower()
        if address and address != target and address not in seen:
            seen.add(address)
            out.append({**entry, "address": address,
                        "settled": address in known_self})

    rows = (roster or {}).get("wallets")
    candidates = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        address = (row.get("wallet") or "").strip().lower()
        tier = row.get("tier")
        if not address or address == target or address in seen:
            continue
        if tier not in WATCHED_TIERS or row.get("is_service"):
            continue
        try:
            confidence = float(row.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        candidates.append((WATCHED_TIERS.index(tier), -confidence, address, row, tier))

    # CONFIRMED before PROBABLE, then by confidence, so the cap can only ever
    # drop the weakest evidence.
    for _tier_rank, _negconf, address, row, tier in sorted(candidates, key=lambda c: c[:3]):
        if len(out) >= MAX_WATCHED:
            break
        reasons = [r for r in (row.get("reasons") or []) if isinstance(r, str)]
        why = f"roster {tier} ({row.get('confidence')})"
        if reasons:
            why += ": " + "; ".join(reasons[:3])
        seen.add(address)
        # CONFIRMED is an answer; PROBABLE is still a question, and a contact
        # with his world is exactly what would settle it.
        out.append({"address": address, "why": why, "source": "roster",
                    "tier": tier,
                    "settled": tier == "CONFIRMED" or address in known_self})

    return out[:MAX_WATCHED]


def snapshot(address: str, *, account_value=None, last_fill_ms=None,
             agents=None, subaccounts=None, withdrawal_destinations=None,
             hyperevm_nonce=None, role=None, master=None, owner=None,
             staking_link=None, vaults_led=None, dexes=None,
             target_value=None, read_ok: bool = True, errors=None) -> dict:
    """One reading of a watched wallet. Absent fields stay None, never 0.

    `master`, `owner` and `staking_link` are the fields that can NAME another
    account: Hyperliquid answering that this address is somebody's sub-account,
    somebody's agent, or paired with a staking wallet. Each is an act its owner
    performed, so each confirms on its own — and none of them was being stored,
    let alone diffed, until 2026-09-11.

    `target_value` is what the TARGET was worth at the same moment, read the
    same way, so `size_ratio` compares like with like. It stays None whenever
    either side could not be read: a ratio against an unreadable account is
    "we could not tell", never "he has nothing".
    """
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
        "master": (master or "").lower() or None,
        "owner": (owner or "").lower() or None,
        "staking_link": (staking_link or "").lower() or None,
        # None, not [], when the field was not read: an empty list would say
        # "it leads no vaults" and the next reading would look like news.
        "vaults_led": None if vaults_led is None else sorted(
            {(v or "").lower() for v in vaults_led if v}),
        "dexes": None if dexes is None else sorted(
            {str(d).lower() for d in dexes if d}),
        "target_value": target_value,
        "size_ratio": _ratio(account_value, target_value),
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

    # Outgrowing him. Reported on the crossing only, like every other change
    # here — but an ABSENT previous ratio counts as below the band rather than
    # as a first reading to be skipped. The wallet crossed weeks before this
    # was built and no stored record carries the field, so treating it the way
    # `vaults_led` treats a first reading would mean the operator is never
    # told at all. The cost of the other error is bounded: `alert_watchlist_change`
    # cools down 24h on the same kind, so a re-read after an outage repeats at
    # most once a day, while a missed crossing is the mission failing quietly.
    was_ratio, now_ratio = previous.get("size_ratio"), current.get("size_ratio")
    if isinstance(now_ratio, (int, float)) and now_ratio >= OUTGREW_TARGET:
        if not (isinstance(was_ratio, (int, float)) and was_ratio >= OUTGREW_TARGET):
            out.append({"kind": "outgrew_target",
                        "detail": f"worth {now_ratio:.2f}x the target: "
                                  f"${current.get('account_value'):,.0f} against "
                                  f"${current.get('target_value'):,.0f}"})

    for field, kind, label in (("agents", "new_agent", "authorised an agent"),
                               ("subaccounts", "new_subaccount", "created a sub-account"),
                               ("withdrawal_destinations", "new_withdrawal_destination",
                                "withdrew to a new destination")):
        fresh = [x for x in current.get(field) or [] if x not in (previous.get(field) or [])]
        out.extend({"kind": kind, "detail": f"{label}: {x}", "address": x} for x in fresh)

    # The strongest thing that can happen to a watched wallet is Hyperliquid
    # naming its owner. `userRole` answers agent -> owner and sub-account ->
    # master; `userFees.stakingLink` pairs a staking wallet with a trading one.
    # Each CONFIRMs alone, so each is reported the moment it appears or moves.
    for field, kind in (("master", "explicit_link_master"),
                        ("owner", "explicit_link_owner"),
                        ("staking_link", "explicit_link_staking")):
        before_link, after_link = previous.get(field), current.get(field)
        if after_link and after_link != before_link:
            out.append({"kind": kind, "address": after_link,
                        "detail": f"Hyperliquid now reports {field} = {after_link}"
                                  + (f" (was {before_link})" if before_link else "")})

    # A role change is the same news arriving by another route: a wallet that
    # was `user` and is now `agent` is being signed for by somebody else.
    before_role, after_role = previous.get("role"), current.get("role")
    if after_role and before_role and after_role != before_role:
        out.append({"kind": "role_change",
                    "detail": f"userRole changed: {before_role} -> {after_role}"})

    for field, kind, label in (("vaults_led", "new_vault_led", "now leads a vault"),
                               ("dexes", "new_dex", "opened a book on a new dex")):
        fresh = [x for x in current.get(field) or [] if x not in (previous.get(field) or [])]
        # A first reading of the field on an existing wallet is not an event.
        if fresh and previous.get(field) is not None:
            out.extend({"kind": kind, "detail": f"{label}: {x}", "address": x}
                       for x in fresh)

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


def contacts(counterparties, world: dict, wallet: str | None = None) -> list[dict]:
    """Counterparties of a watched wallet that belong to the target's world.

    `wallet` is the wallet being watched, and it is excluded: a watched wallet
    is itself in `world` (that is usually WHY it is watched), so without this
    every read reported the wallet in contact with itself. Two of the 39 alerts
    fired on 2026-09-12 were exactly that.

    `world` maps an address to what it is ("the target", "a known wallet of
    his", "a private deposit address of his", "roster: PROBABLE"). An observed
    transfer or action between the two is a vector the correlation and
    dormancy readings cannot supply, because both of those are inferences
    about coincidence and this is a thing that happened.
    """
    subject = (wallet or "").strip().lower()
    out = []
    for raw in counterparties or []:
        a = (raw or "").lower()
        if a and a != subject and a in world:
            out.append({"address": a, "is": world[a]})
    return sorted(out, key=lambda c: c["address"])


def contacts_are_news(entry: dict) -> bool:
    """Whether a contact between this watched wallet and his world is news.

    The contact vector exists to SETTLE a question. `alert_watchlist_contact`
    says so in as many words: the wallet is watched because two inferences
    agreed about it, and an observed transfer is the third vector that decides
    it. A wallet we already believe is his has no such question left — its
    counterparties ARE his world, by construction, and reporting that is a
    tautology with a siren attached.

    What a settled wallet touches is still recorded, and still reaches the
    dashboard and the stored reading. Only the buzz stops — the same policy
    INFO alerts already follow.

    Absent means unsettled. The operator's own `watch_wallets` entries carry no
    tier, and a question is the conservative reading for a wake-someone signal.
    """
    return not bool((entry or {}).get("settled"))


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


CONFIRMING_KINDS = ("explicit_link_master", "explicit_link_owner",
                    "explicit_link_staking")


def explicit_links(deltas: list[dict]) -> list[dict]:
    """The changes Hyperliquid itself declares — each CONFIRMs on its own."""
    return [d for d in deltas or [] if d.get("kind") in CONFIRMING_KINDS]


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
