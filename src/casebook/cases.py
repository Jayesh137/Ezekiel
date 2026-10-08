"""Case-file merge (spec 2026-10-08 §5, §6.3). Pure: dicts in, dicts and events out.

A case is never closed and nothing in it is deleted. Each evidence item carries
when it was first and last seen and what it is now:

  current      in the latest roster reading
  standing     no longer reported, but a protocol fact (it cannot un-happen)
  lapsed       observed live, then absent 24 h or more, cause unknown
  refuted      absent after a detector re-checked the wallet and found nothing
  historical   from the git-history backfill and not current when the casebook went live

A 24 h debounce keeps a flapping item from becoming a stream of events.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.casebook import extract, model

SCHEMA = "casebook-case/1"
DEBOUNCE_MS = 24 * 3_600_000
TIER_ORDER = {"CONFIRMED": 0, "PROBABLE": 1, "POSSIBLE": 2, "WATCH": 3, "INFRASTRUCTURE": 4}
MAX_TIERS = 400
MAX_REASONS = 8
MAX_SCORE_DAYS = 400
# The central estimate moving this far (log10 odds, about x3) since the last such
# event is news about the case.
SCORE_MOVE = 0.5
HL_FROM_ROSTER = (("role", "hl_role"), ("birth_ms", "hl_birth_ms"), ("total_value", "hl_total_value"),
                  ("account_value", "hl_account_value"), ("month_volume", "hl_month_volume"),
                  ("frontend_agent", "frontend_agent"))
LINK_KEYS = ("operator_group", "referral_pairs", "subaccount_of", "explicit_links")


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def parse_ms(text) -> int | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return int(when.timestamp() * 1000)


def event(at_ms: int, address: str, kind: str, origin: str, **detail) -> dict:
    return {"at": iso(at_ms), "address": address, "kind": kind, "origin": origin,
            "detail": {k: v for k, v in detail.items() if v is not None}}


def new_case(address: str, at_ms: int, why: list, origin: str) -> tuple[dict, list]:
    case = {"schema": SCHEMA, "address": address, "opened_at": iso(at_ms),
            "opened_by": list(why)[:8], "origin": origin, "known": None, "pinned": None,
            "ruling": None, "excluded": None,
            "roster": {"tier": None, "peak_tier": None, "peak_at": None, "tiers": [],
                       "last_in_roster": None, "reasons": [], "reasons_at": None},
            "evidence": {}, "hl": {}, "links": {}, "score": None, "score_days": [],
            "score_event_central": None, "last_change": iso(at_ms)}
    return case, [event(at_ms, address, "case_opened", origin, why=list(why)[:8])]


def _scored(kind) -> bool:
    return kind not in model.CONTEXT_KINDS


def _ended_status(entry: dict, origin: str) -> str:
    if entry.get("kind") in model.STANDING_KINDS:
        return "standing"
    if entry.get("refuted_seen"):
        return "refuted"
    if origin == "backfill" or not entry.get("live"):
        return "historical"
    return "lapsed"


def _observe(entry: dict, item: dict, at_ms: int) -> None:
    """The latest observation is the item's facts; the strongest is kept beside it."""
    strength = item.get("strength")
    entry["strength"], entry["facts"], entry["summary"] = strength, item.get("facts"), item.get("summary")
    peak = entry.get("peak_strength")
    if strength is not None and (peak is None or abs(strength) > abs(peak)):
        entry["peak_strength"], entry["peak_summary"], entry["peak_at"] = (
            strength, item.get("summary"), iso(at_ms))


def merge_items(case: dict, items: list, refutes: set, *, at_ms: int, origin: str) -> list:
    """Fold one reading of this wallet into its case. Returns the events."""
    events, seen = [], set()
    address, today, live = case["address"], day(at_ms), origin == "live"
    evidence = case.setdefault("evidence", {})
    for item in items:
        key = item["key"]
        seen.add(key)
        entry = evidence.get(key)
        if entry is None:
            entry = evidence[key] = {
                "kind": item["kind"], "status": "current", "origin": origin, "live": live,
                "first_seen": iso(at_ms), "last_seen": today, "seen_days": 1,
                "absent_since": None, "ended_at": None, "refuted_seen": False,
                "strength": None, "facts": None, "summary": None,
                "peak_strength": None, "peak_summary": None, "peak_at": None}
            _observe(entry, item, at_ms)
            if _scored(item["kind"]):
                events.append(event(at_ms, address, "evidence_new", origin, key=key,
                                    summary=item.get("summary")))
            continue
        if entry.get("last_seen") != today:
            entry["seen_days"] = int(entry.get("seen_days") or 0) + 1
            entry["last_seen"] = today
        entry["absent_since"], entry["refuted_seen"] = None, False
        entry["live"] = bool(entry.get("live")) or live
        was = entry.get("status")
        if was != "current":
            entry["status"], entry["ended_at"] = "current", None
            if _scored(item["kind"]):
                events.append(event(at_ms, address, "evidence_returned", origin, key=key,
                                    was=was, summary=item.get("summary")))
        _observe(entry, item, at_ms)
    for key, entry in evidence.items():
        if key in seen or entry.get("status") != "current":
            continue
        if key in refutes:
            entry["refuted_seen"] = True
        since = parse_ms(entry.get("absent_since"))
        if since is None:
            entry["absent_since"] = iso(at_ms)
            continue
        if at_ms - since < DEBOUNCE_MS:
            continue
        status = _ended_status(entry, origin)
        entry["status"], entry["ended_at"] = status, iso(at_ms)
        if _scored(entry.get("kind")):
            events.append(event(at_ms, address, f"evidence_{status}", origin, key=key,
                                summary=entry.get("summary")))
    return events


# Items that rest on another address: the evidence holds only while that address is
# what it was taken for (a quiet funder, a quiet payee, a private deposit address).
COUNTERPART_KEYS = {"quiet_first_funder": "funder", "quiet_payee": "via",
                    "hl_deposit_address": "via", "private_deposit_address": "sentinel"}


def effective_status(entry: dict) -> str | None:
    """An item resting on an address today's filters reject counts as invalidated,
    whatever its own status; the status itself is kept for the record."""
    return "invalidated" if entry.get("invalid_reason") else entry.get("status")


def counterpart(entry: dict) -> str | None:
    key = COUNTERPART_KEYS.get(entry.get("kind"))
    facts = entry.get("facts") if isinstance(entry.get("facts"), dict) else {}
    value = facts.get(key) if key else None
    return value.lower() if isinstance(value, str) else None


def rejudge(case: dict, verdicts: dict, *, at_ms: int, origin: str) -> list:
    """Re-judge the items that rest on another address against today's measurements
    (spec §6.3 `invalidated`): a quiet funder since measured busy, a payee since shown
    to be a contract. Reversible, because a measurement can be corrected.

    `verdicts` maps an address to why it cannot be what the item took it for, or to
    None when it was measured and passes. An address absent from it was not measured
    today and keeps its stored verdict: "we could not tell" never clears one (rule 5)."""
    events = []
    for key, entry in (case.get("evidence") or {}).items():
        other = counterpart(entry)
        if other is None or other not in verdicts:
            continue
        reason = verdicts[other]
        if reason and entry.get("invalid_reason") != reason:
            entry["invalid_reason"] = str(reason)[:200]
            events.append(event(at_ms, case["address"], "evidence_invalidated", origin, key=key,
                                counterpart=other, reason=entry["invalid_reason"]))
        elif not reason and entry.get("invalid_reason"):
            entry.pop("invalid_reason")
            events.append(event(at_ms, case["address"], "evidence_revalidated", origin, key=key,
                                counterpart=other))
    return events


# The rosters of 2026-09-10 (the first in git, 06:27-15:33 UTC) were all computed before that
# day's fixes: counterfeit tokens priced as real (6d215db6c4, 07:37 UTC); token quantities
# booked as dollars (1,030,689,918 MAX as $1.03B), records counted up to three times and
# contracts read as private deposit addresses (8026d6fadf, 17:54 UTC). The next roster
# (2026-09-11 15:59 UTC) ran the fixed code, so an item last seen that day is one only the
# faulty rosters reported. Kinds those faults could not touch keep what the day reported.
PRE_FIX_DAY = "2026-09-10"
PRE_FIX_KINDS = frozenset(k for k, spec in model.KINDS.items() if spec["family"] == "money") | {
    "linkage_graph", "amount_correlation"}
PRE_FIX_REASON = ("reported only by the 2026-09-10 rosters, built before that day's fixes (counterfeit "
                  "tokens priced as real, token quantities as dollars, records counted 3x, contracts as "
                  "deposit addresses)")


def void_pre_fix(case: dict, *, at_ms: int, origin: str) -> list:
    """Evidence only the 2026-09-10 rosters reported stops counting, and a peak taken from them
    is dropped (its event keeps the figure). Reversible like `rejudge`: a later report of the
    item lifts the reason, and a reason this rule did not give is never touched."""
    events = []
    for key, entry in (case.get("evidence") or {}).items():
        if not isinstance(entry, dict) or entry.get("kind") not in PRE_FIX_KINDS:
            continue
        reason = entry.get("invalid_reason")
        if entry.get("last_seen") == PRE_FIX_DAY:
            if not reason:
                entry["invalid_reason"] = PRE_FIX_REASON
                events.append(event(at_ms, case["address"], "evidence_invalidated", origin, key=key,
                                    reason=PRE_FIX_REASON))
        elif reason == PRE_FIX_REASON:
            entry.pop("invalid_reason")
            events.append(event(at_ms, case["address"], "evidence_revalidated", origin, key=key))
        peak_at = entry.get("peak_at")
        if isinstance(peak_at, str) and peak_at[:10] == PRE_FIX_DAY:
            events.append(event(at_ms, case["address"], "evidence_peak_voided", origin, key=key,
                                summary=entry.get("peak_summary"), strength=entry.get("peak_strength"),
                                taken_at=peak_at, reason=f"taken from a {PRE_FIX_DAY} roster"))
            entry["peak_strength"] = entry["peak_summary"] = entry["peak_at"] = None
    return events


def exclude_forgery(case: dict, cluster: set, *, at_ms: int, origin: str) -> list:
    """A case whose address is made to look like one of his declared wallets is address
    poisoning, never a suspect. Run on every case each run: the roster only re-grades
    the wallets it still lists, and five forgeries from its history listed nowhere today
    still ranked (2026-10-08)."""
    if case.get("known") or case.get("excluded"):
        return []
    reason = extract.forgery_of(case["address"], cluster)
    if not reason:
        return []
    case["excluded"] = {"reason": reason, "since": iso(at_ms)}
    return [event(at_ms, case["address"], "case_excluded", origin, reason=reason)]


def update_roster(case: dict, row: dict, at_ms: int, origin: str) -> list:
    """The roster's own view of the wallet: tier by day, peak, reasons, HL facts, links."""
    events: list = []
    roster = case.setdefault("roster", {})
    tiers = roster.setdefault("tiers", [])
    today = day(at_ms)
    tier = row.get("tier")
    if tier in TIER_ORDER:
        if tiers and tiers[-1][0] == today:
            best = min(tiers[-1][1], tier, key=lambda t: TIER_ORDER.get(t, 9))
            if best != tiers[-1][1]:
                events.append(event(at_ms, case["address"], "tier_changed", origin,
                                    frm=tiers[-1][1], to=best))
                tiers[-1][1] = best
        elif not tiers or tiers[-1][1] != tier:
            if tiers:
                events.append(event(at_ms, case["address"], "tier_changed", origin,
                                    frm=tiers[-1][1], to=tier))
            tiers.append([today, tier])
        if len(tiers) > MAX_TIERS:
            del tiers[1:len(tiers) - MAX_TIERS + 1]
        roster["tier"] = tier
        ranked = [t for t in (roster.get("peak_tier"), tier, row.get("peak_tier"))
                  if t in TIER_ORDER and t != "INFRASTRUCTURE"]
        if ranked:
            best = min(ranked, key=TIER_ORDER.get)
            if best != roster.get("peak_tier"):
                roster["peak_tier"], roster["peak_at"] = best, today
    roster["last_in_roster"] = today
    reasons = [str(r)[:200] for r in (row.get("reasons") or []) if r][:MAX_REASONS]
    if reasons:
        roster["reasons"], roster["reasons_at"] = reasons, today
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    hl = case.setdefault("hl", {}).setdefault("roster", {})
    for key, source in HL_FROM_ROSTER:
        value = evidence.get(source)
        if value is not None:
            hl[key] = extract.bound(value)
    if hl:
        hl["at"] = today
    links = case.setdefault("links", {})
    for key in LINK_KEYS:
        value = evidence.get(key)
        if value:
            links[key] = extract.bound(value)
    return events


def apply_config(case: dict, config: dict, *, at_ms: int, origin: str) -> list:
    """The operator's own facts about the wallet: ground truth, pins and rulings."""
    pin = extract.pinned(config).get(case["address"])
    case["pinned"] = pin
    case["known"] = pin if pin == "config:known_self" else None
    ruling = extract.ruling_of(config, case["address"])
    ruling = extract.bound(ruling) if ruling else None
    if ruling != case.get("ruling"):
        case["ruling"] = ruling
        return [event(at_ms, case["address"], "ruling_changed", origin,
                      verdict=(ruling or {}).get("verdict"), note=(ruling or {}).get("note"))]
    return []


def _thin_score_days(days: list) -> list:
    if len(days) <= MAX_SCORE_DAYS:
        return days
    older, recent = days[:-MAX_SCORE_DAYS], days[-MAX_SCORE_DAYS:]
    weekly: dict[str, list] = {}
    for row in older:
        try:
            year, week, _ = datetime.strptime(row[0], "%Y-%m-%d").isocalendar()
        except (TypeError, ValueError):
            continue
        weekly[f"{year}-{week:02d}"] = row
    return sorted(weekly.values(), key=lambda r: r[0]) + recent


def record_score(case: dict, score: dict, *, at_ms: int, origin: str) -> list:
    """Store the case's score; one history row a day when it changed; an event when
    the central estimate moved SCORE_MOVE or more since the last such event. A model
    change re-bases silently: every case moving at once is not news about any one."""
    previous_model = (case.get("score") or {}).get("model")
    case["score"] = {k: score.get(k) for k in ("model", "now", "central", "ceiling", "p_now", "p_central",
                                               "p_ceiling", "cluster", "cluster_size", "solo_central",
                                               "families")}
    today = day(at_ms)
    row = [today, score.get("now"), score.get("central"), score.get("ceiling")]
    days = case.setdefault("score_days", [])
    if not days or days[-1][1:] != row[1:]:
        if days and days[-1][0] == today:
            days[-1] = row
        else:
            days.append(row)
    case["score_days"] = _thin_score_days(days)
    central = score.get("central")
    last = case.get("score_event_central")
    if last is None or previous_model != score.get("model"):
        case["score_event_central"] = central
        return []
    if isinstance(central, (int, float)) and abs(central - last) >= SCORE_MOVE:
        case["score_event_central"] = central
        return [event(at_ms, case["address"], "score_moved", origin, frm=last, to=central,
                      p=score.get("p_central"))]
    return []


def touch(cases: dict, events: list) -> None:
    """Each case's last_change becomes its newest event."""
    newest: dict[str, str] = {}
    for e in events:
        address, at = e.get("address"), e.get("at") or ""
        if address and at > newest.get(address, ""):
            newest[address] = at
    for address, at in newest.items():
        case = cases.get(address)
        if case is not None and at > (case.get("last_change") or ""):
            case["last_change"] = at
