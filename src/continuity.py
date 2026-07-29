# src/continuity.py
"""Wallet-continuity model: ordered paths, signal scoring, lifecycle.

Purpose: keep following the target when funds are routed through intermediary
wallets to obscure the next active trading wallet. This module surfaces LEADS.
It never asserts ownership — the vocabulary throughout is "fund-flow-linked",
"possible successor", "high-confidence continuity lead".

Pure by construction: every function takes plain data and returns plain data, so
the whole model is testable offline. All I/O lives in transfer_graph.py.

Why this exists rather than more logic in transfer_graph.py: the previous scorer
applied a flat 0.25-per-hop penalty, so a four-hop chain retaining 97% of the
target's money scored 0.0 at depths 2 and 3 — precisely the evasion pattern it
was supposed to catch. Continuity has to be judged on how much value survived
and how fast, not on hop count alone.
"""

import hashlib

# --- lifecycle -------------------------------------------------------------------

LIFECYCLE_REJECTED_SERVICE = "REJECTED_SERVICE"
LIFECYCLE_LEAD = "LEAD"
LIFECYCLE_OBSERVED = "OBSERVED"
LIFECYCLE_FUNDED = "FUNDED_BY_TARGET"
LIFECYCLE_TRADING = "TRADING_STARTED"
LIFECYCLE_POSSIBLE = "POSSIBLE_SUCCESSOR"
LIFECYCLE_HIGH_CONFIDENCE = "HIGH_CONFIDENCE_SUCCESSOR"
LIFECYCLE_DORMANT = "DORMANT"

LIFECYCLE_ORDER = {
    LIFECYCLE_REJECTED_SERVICE: -1,
    LIFECYCLE_LEAD: 0,
    LIFECYCLE_OBSERVED: 1,
    LIFECYCLE_FUNDED: 2,
    LIFECYCLE_TRADING: 3,
    LIFECYCLE_POSSIBLE: 4,
    LIFECYCLE_HIGH_CONFIDENCE: 5,
    LIFECYCLE_DORMANT: 1,
}

DORMANT_AFTER_DAYS = 60.0

# --- signal families -------------------------------------------------------------
#
# Promotion requires corroboration from two or more DISTINCT families. That single
# rule is the main false-positive guard: no amount of flow evidence, on its own,
# can make a wallet a successor.

FAMILY_FLOW = "FLOW"            # amounts, timing, split/merge, value retained
FAMILY_FUNDING = "FUNDING"      # first gas, funded-before-trading, direct funding
FAMILY_BEHAVIOUR = "BEHAVIOUR"  # fingerprint similarity, wallet rotation
FAMILY_STRUCTURE = "STRUCTURE"  # repeated transfers, two-way flow, shared route
FAMILY_PLATFORM = "PLATFORM"    # HL-native, independent L1, bridge correlation

# (weight, family). Capped per family so one family cannot dominate.
SIGNALS = {
    "amount_similarity":      (0.14, FAMILY_FLOW),
    "temporal_proximity":     (0.10, FAMILY_FLOW),
    "split_merge":            (0.16, FAMILY_FLOW),
    "value_retained":         (0.10, FAMILY_FLOW),
    "first_gas":              (0.16, FAMILY_FUNDING),
    "funded_before_trading":  (0.14, FAMILY_FUNDING),
    "direct_from_target":     (0.10, FAMILY_FUNDING),
    "behavioural":            (0.22, FAMILY_BEHAVIOUR),
    "rotation":               (0.18, FAMILY_BEHAVIOUR),
    "repeated_transfers":     (0.10, FAMILY_STRUCTURE),
    "two_way_flow":           (0.12, FAMILY_STRUCTURE),
    "shared_route":           (0.16, FAMILY_STRUCTURE),
    "hl_native":              (0.10, FAMILY_PLATFORM),
    "bridge_correlated":      (0.14, FAMILY_PLATFORM),
}
FAMILY_CAP = 0.32

MIN_FAMILIES_FOR_PROMOTION = 2
POSSIBLE_SUCCESSOR_MIN = 0.40
HIGH_CONFIDENCE_MIN = 0.65

# --- relay detection ---------------------------------------------------------------
#
# The fingerprint of the evasion: a wallet that receives and promptly forwards most
# of it to very few destinations is a relay, not a destination.

RELAY_MIN_FORWARDED = 0.70
RELAY_MAX_HOURS = 72.0
RELAY_MAX_DESTINATIONS = 3

# --- path breaks ---------------------------------------------------------------------

BREAK_SERVICE = "service"
BREAK_BRIDGE = "bridge"
BREAK_MIXER = "mixer"
BREAK_INCOMPLETE = "incomplete_data"


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# --- relay ----------------------------------------------------------------------------

def classify_relay(received_usd: float, forwarded_usd: float,
                   hours_to_forward: float | None,
                   destination_count: int) -> dict:
    """Is this wallet a pass-through relay rather than a destination?

    Pure. Returns the verdict plus the measurements behind it, so a chase decision
    can always be explained.
    """
    if received_usd <= 0:
        return {"is_relay": False, "forwarded_fraction": 0.0,
                "reason": "no inbound value"}
    fraction = forwarded_usd / received_usd
    fast = hours_to_forward is not None and hours_to_forward <= RELAY_MAX_HOURS
    narrow = 0 < destination_count <= RELAY_MAX_DESTINATIONS
    is_relay = fraction >= RELAY_MIN_FORWARDED and fast and narrow

    if is_relay:
        reason = (f"forwarded {fraction:.0%} of ${received_usd:,.0f} within "
                  f"{hours_to_forward:.1f}h to {destination_count} destination(s)")
    elif fraction < RELAY_MIN_FORWARDED:
        reason = f"retained {1 - fraction:.0%} — behaves as a destination"
    elif not fast:
        reason = "forwarded too long after receipt to read as a relay"
    else:
        reason = f"fanned out to {destination_count} destinations — not a narrow relay"

    return {"is_relay": is_relay, "forwarded_fraction": round(fraction, 4),
            "hours_to_forward": hours_to_forward,
            "destination_count": destination_count, "reason": reason}


def relay_likelihood(received_usd: float, forwarded_usd: float,
                     hours_to_forward: float | None,
                     destination_count: int) -> float:
    """0-1 score used to prioritise which frontier wallets are worth API budget."""
    if received_usd <= 0:
        return 0.0
    fraction = _clamp(forwarded_usd / received_usd)
    speed = 1.0 if hours_to_forward is None else _clamp(
        1.0 - (hours_to_forward / (RELAY_MAX_HOURS * 2)))
    narrowness = 1.0 / max(1, destination_count)
    return round(_clamp(fraction * 0.5 + speed * 0.25 + narrowness * 0.25), 4)


# --- decay -----------------------------------------------------------------------------

def hop_decay(hop_count: int, value_retained: float) -> float:
    """Value-aware hop decay, replacing the old flat 0.25-per-hop penalty.

    A chain is weakened by length, but far less so when the money actually
    survived it. Retaining 94% across four hops decays to ~0.71; retaining 3%
    across the same four hops decays to the 0.25 floor.
    """
    if hop_count <= 1:
        return 1.0
    retained = _clamp(value_retained)
    return round(max(0.25, (retained ** 0.5) * (0.9 ** (hop_count - 1))), 4)


def age_decay(age_days: float | None, fresh_days: float = 90.0,
              decay_days: float = 365.0, floor: float = 0.4) -> float:
    """Flat while fresh, then linear to a floor. Mirrors transfer_graph.recency_factor."""
    if age_days is None:
        return 1.0
    if age_days <= fresh_days:
        return 1.0
    return round(max(floor, min(1.0, 1.0 - (age_days - fresh_days) / decay_days)), 4)


# --- scoring ------------------------------------------------------------------------------

def score_continuity(signals: dict, *, hop_count: int = 1,
                     value_retained: float = 1.0,
                     age_days: float | None = None,
                     is_service: bool = False,
                     vetoes: list | None = None,
                     breaks: list | None = None) -> dict:
    """Score a candidate continuity path. Pure.

    `signals` maps signal name -> strength in 0..1 (or True for binary signals).
    Returns confidence, the families that contributed, readable reasons and any
    blockers. Never asserts ownership.
    """
    if is_service:
        return {"confidence": 0.0, "families": [], "reasons": [],
                "blockers": ["service address — excluded from continuity scoring"],
                "hop_decay": 0.0, "age_decay": 0.0}

    reasons: list[str] = []
    blockers: list[str] = []
    per_family: dict[str, float] = {}

    for name, raw in (signals or {}).items():
        if name not in SIGNALS or not raw:
            continue
        weight, family = SIGNALS[name]
        strength = 1.0 if raw is True else _clamp(float(raw))
        contribution = weight * strength
        per_family[family] = per_family.get(family, 0.0) + contribution
        reasons.append(f"{name.replace('_', ' ')} ({family.lower()}, "
                       f"+{contribution:.3f})")

    # Cap each family so one kind of evidence cannot carry a promotion alone.
    capped = {f: min(v, FAMILY_CAP) for f, v in per_family.items()}
    for f, v in per_family.items():
        if v > FAMILY_CAP:
            reasons.append(f"{f.lower()} contribution capped at {FAMILY_CAP}")

    base = sum(capped.values())
    hd = hop_decay(hop_count, value_retained)
    ad = age_decay(age_days)
    # Age erodes flow and structure; funding and behaviour are facts that do not
    # become less true with time.
    ageable = sum(v for f, v in capped.items()
                  if f in (FAMILY_FLOW, FAMILY_STRUCTURE, FAMILY_PLATFORM))
    timeless = base - ageable
    confidence = _clamp((ageable * ad + timeless) * hd)

    families = sorted(capped)
    if len(families) < MIN_FAMILIES_FOR_PROMOTION:
        blockers.append(
            f"only {len(families)} evidence family present "
            f"({', '.join(families) or 'none'}) — needs "
            f"{MIN_FAMILIES_FOR_PROMOTION} independent families")
    if vetoes:
        blockers.append(f"contradictory behaviour: {'; '.join(vetoes)}")
    blockers.extend(
        f"path break at {str(b.get('at', '?'))[:12]}…: {b.get('reason')}"
        for b in breaks or [])

    if hop_count > 1:
        reasons.append(f"{hop_count} hops, {value_retained:.0%} of value retained "
                       f"(decay x{hd})")
    if ad < 1.0:
        reasons.append(f"evidence aged {age_days:.0f} days (decay x{ad})")

    return {"confidence": round(confidence, 4), "families": families,
            "reasons": reasons, "blockers": blockers,
            "hop_decay": hd, "age_decay": ad}


# --- lifecycle ----------------------------------------------------------------------------

def lifecycle_state(*, is_service: bool = False, on_path: bool = False,
                    transfer_count: int = 0, runs_seen: int = 1,
                    funded_by_target: bool = False,
                    has_unbroken_path: bool = False,
                    trades_after_funding: bool = False,
                    confidence: float = 0.0, families: list | None = None,
                    vetoes: list | None = None, breaks: list | None = None,
                    days_inactive: float | None = None,
                    disposition_alert: bool = False) -> dict:
    """Resolve a wallet's continuity lifecycle state, with the reason for it.

    Promotion past TRADING_STARTED always needs >=2 independent evidence families,
    an unbroken path, no contradictory behaviour, and — for the top state — the
    central alert disposition to agree.
    """
    families = families or []
    reasons: list[str] = []

    if is_service:
        return {"state": LIFECYCLE_REJECTED_SERVICE,
                "reason": "exchange / bridge / high-fan-degree service address",
                "blockers": ["services are never continuity candidates"],
                "dormant": False, "days_inactive": None}

    if not on_path:
        return {"state": LIFECYCLE_LEAD, "reason": "not currently on a path from "
                "the target", "blockers": [], "dormant": False,
                "days_inactive": None}

    state = LIFECYCLE_LEAD
    reasons.append("appears on a fund-flow path from the target")

    if transfer_count >= 2 or runs_seen >= 2:
        state = LIFECYCLE_OBSERVED
        reasons.append(f"{transfer_count} transfer(s) across {runs_seen} run(s)")

    early_blockers: list[str] = []
    if funded_by_target and has_unbroken_path:
        state = LIFECYCLE_FUNDED
        reasons.append("received target funds over a path with no break")
    elif funded_by_target:
        reasons.append("received target funds, but the path contains a break")
        # Surface the break here too. Without this, a break that stops promotion
        # early leaves no explanation at all — the wallet just silently sits at
        # OBSERVED with no stated reason.
        early_blockers.extend(
            f"path break at {str(b.get('at', '?'))[:12]}…: {b.get('reason')} "
            f"— blocks promotion beyond {LIFECYCLE_OBSERVED}"
            for b in breaks or [])

    if state == LIFECYCLE_FUNDED and trades_after_funding:
        state = LIFECYCLE_TRADING
        reasons.append("began trading on Hyperliquid after being funded")

    blockers: list[str] = list(early_blockers)
    enough_families = len(families) >= MIN_FAMILIES_FOR_PROMOTION
    unbroken = not breaks
    clean = not vetoes

    if state == LIFECYCLE_TRADING:
        if not enough_families:
            blockers.append(f"needs {MIN_FAMILIES_FOR_PROMOTION} evidence families, "
                            f"has {len(families)}")
        if not clean:
            blockers.append("contradictory behavioural evidence")
        if confidence < POSSIBLE_SUCCESSOR_MIN:
            blockers.append(f"continuity {confidence:.2f} below "
                            f"{POSSIBLE_SUCCESSOR_MIN}")
        if enough_families and clean and confidence >= POSSIBLE_SUCCESSOR_MIN:
            state = LIFECYCLE_POSSIBLE
            reasons.append(f"continuity {confidence:.2f} across "
                           f"{len(families)} evidence families")

    if state == LIFECYCLE_POSSIBLE:
        if not unbroken:
            blockers.append("path break prevents high-confidence promotion")
        if confidence < HIGH_CONFIDENCE_MIN:
            blockers.append(f"continuity {confidence:.2f} below "
                            f"{HIGH_CONFIDENCE_MIN}")
        if not disposition_alert:
            blockers.append("central alert disposition did not promote")
        if unbroken and clean and confidence >= HIGH_CONFIDENCE_MIN and disposition_alert:
            state = LIFECYCLE_HIGH_CONFIDENCE
            reasons.append("unbroken path, corroborated, disposition promoted")

    # Dormancy is reported for EVERY state, but only demotes the weaker ones. A
    # corroborated lead that has gone quiet is still the best lead there is —
    # erasing it would discard the finding — yet a migration tracker that shows
    # it without saying "silent for 200 days" is misleading about where the
    # trader is now.
    dormant = bool(days_inactive is not None and days_inactive >= DORMANT_AFTER_DAYS)
    if dormant:
        reasons.append(f"no activity for {days_inactive:.0f} days")
        if LIFECYCLE_ORDER[state] <= LIFECYCLE_ORDER[LIFECYCLE_TRADING]:
            state = LIFECYCLE_DORMANT

    return {"state": state, "reason": "; ".join(reasons), "blockers": blockers,
            "dormant": dormant,
            "days_inactive": round(days_inactive, 1) if days_inactive is not None
            else None}


# --- paths -------------------------------------------------------------------------------

def path_id(hops: list[dict]) -> str:
    """Stable identity for an ordered path, keyed on its hop references."""
    raw = "|".join(f"{h.get('src','')}>{h.get('dst','')}@{h.get('ref','')}"
                   for h in hops).lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def path_signature(hops: list[dict]) -> str:
    """Dedup key for alerts: the ordered wallet sequence, ignoring which specific
    transactions carried the value. Two runs that rediscover the same route
    produce the same signature."""
    if not hops:
        return ""
    seq = [hops[0].get("src", "")] + [h.get("dst", "") for h in hops]
    return hashlib.sha1("|".join(a.lower() for a in seq).encode()).hexdigest()[:16]


def build_path(hops: list[dict], *, breaks: list | None = None,
               relay_hops: list | None = None) -> dict:
    """Assemble an ordered path with its value-flow and timing summary.

    Retains every hop's full evidence so a conclusion can be audited later.
    """
    hops = list(hops or [])
    if not hops:
        return {}
    amounts = [float(h.get("amount_usd", 0) or 0) for h in hops]
    stamps = [int(h.get("ts", 0) or 0) for h in hops if h.get("ts")]
    start, end = (amounts[0] or 0.0), (amounts[-1] or 0.0)
    retained = _clamp(end / start) if start > 0 else 0.0
    elapsed = ((max(stamps) - min(stamps)) / 3600.0) if len(stamps) > 1 else 0.0
    return {
        "id": path_id(hops),
        "signature": path_signature(hops),
        "endpoint": hops[-1].get("dst"),
        "hops": hops,
        "hop_count": len(hops),
        "elapsed_hours": round(elapsed, 2),
        "value_start_usd": round(start, 2),
        "value_end_usd": round(end, 2),
        "value_retained": round(retained, 4),
        "relay_hops": sorted(set(relay_hops or [])),
        "breaks": list(breaks or []),
    }


def reconcile_split_merge(outflows: list[dict], inflows: list[dict],
                          tolerance: float = 0.08,
                          window_hours: float = 168.0) -> dict | None:
    """Detect an amount split across several wallets that reconverges.

    Splitting one exit into three transfers that later rejoin is a deliberate
    obfuscation, so reconvergence is treated as FLOW evidence rather than noise.
    Tolerance is wider than the single-amount matcher because each leg pays fees.
    """
    if len(outflows) < 2 or not inflows:
        return None
    out_total = sum(float(o.get("amount_usd", 0) or 0) for o in outflows)
    if out_total <= 0:
        return None
    out_stamps = [int(o.get("ts", 0) or 0) for o in outflows if o.get("ts")]

    best = None
    for inflow in inflows:
        amt = float(inflow.get("amount_usd", 0) or 0)
        if amt <= 0:
            continue
        diff = abs(out_total - amt) / out_total
        if diff > tolerance:
            continue
        ts = int(inflow.get("ts", 0) or 0)
        if out_stamps and ts:
            gap_h = (ts - max(out_stamps)) / 3600.0
            if gap_h < 0 or gap_h > window_hours:
                continue
        else:
            gap_h = None
        cand = {
            "parts": len(outflows),
            "split_total_usd": round(out_total, 2),
            "merged_usd": round(amt, 2),
            "diff_pct": round(diff * 100, 3),
            "gap_hours": round(gap_h, 2) if gap_h is not None else None,
            "confidence": round(_clamp(1.0 - diff / tolerance), 4),
            "refs": [o.get("ref") for o in outflows],
            "merged_ref": inflow.get("ref"),
        }
        if best is None or cand["confidence"] > best["confidence"]:
            best = cand
    return best


def correlate_bridge(withdraw_usd: float, deposit_usd: float,
                     gap_hours: float | None,
                     fee_tolerance: float = 0.03,
                     max_gap_hours: float = 72.0) -> dict | None:
    """Carry a lead across a bridge only when amount and timing correlate.

    A bridge is always recorded as a path break — it is a custody boundary, not a
    wallet-to-wallet transfer — but a well-correlated crossing keeps the lead
    alive at reduced confidence instead of ending the trail.
    """
    if withdraw_usd <= 0 or deposit_usd <= 0:
        return None
    diff = abs(withdraw_usd - deposit_usd) / withdraw_usd
    if diff > fee_tolerance:
        return None
    if gap_hours is None or gap_hours < 0 or gap_hours > max_gap_hours:
        return None
    amount_score = 1.0 - diff / fee_tolerance
    time_score = 1.0 - gap_hours / max_gap_hours
    return {
        "withdraw_usd": round(withdraw_usd, 2),
        "deposit_usd": round(deposit_usd, 2),
        "diff_pct": round(diff * 100, 3),
        "gap_hours": round(gap_hours, 2),
        "confidence": round(_clamp(0.7 * amount_score + 0.3 * time_score), 4),
        "break": {"reason": "bridge crossing — custody boundary, correlated by "
                            "amount and timing", "type": BREAK_BRIDGE},
    }


def detect_rotation(target_last_activity_days: float | None,
                    candidate_first_trade_days: float | None,
                    max_overlap_days: float = 14.0) -> dict | None:
    """Old wallet goes quiet, new wallet starts trading — the rotation signature.

    Requires the candidate to have STARTED trading around or after the target went
    quiet. A wallet already trading long before is an independent trader, not a
    successor.
    """
    if target_last_activity_days is None or candidate_first_trade_days is None:
        return None
    if target_last_activity_days <= 0:
        return None
    # Positive => candidate started trading after the target's last activity.
    lead_days = target_last_activity_days - candidate_first_trade_days
    if lead_days < -max_overlap_days:
        return None
    closeness = _clamp(1.0 - abs(lead_days) / max(1.0, target_last_activity_days))
    return {
        "target_quiet_days": round(target_last_activity_days, 1),
        "candidate_trading_days": round(candidate_first_trade_days, 1),
        "overlap_days": round(lead_days, 1),
        "strength": round(closeness, 4),
        "reason": (f"target quiet {target_last_activity_days:.0f}d; candidate began "
                   f"trading {candidate_first_trade_days:.0f}d ago"),
    }


def amount_similarity(sent_usd: float, received_usd: float,
                      fee_tolerance: float = 0.05) -> float:
    """How closely a received amount matches a sent amount after expected fees.

    Returns 0 when the gap exceeds tolerance, so a coincidental amount cannot
    contribute.
    """
    if sent_usd <= 0 or received_usd <= 0:
        return 0.0
    diff = abs(sent_usd - received_usd) / sent_usd
    if diff > fee_tolerance:
        return 0.0
    return round(_clamp(1.0 - diff / fee_tolerance), 4)


def temporal_proximity(gap_hours: float | None, window_hours: float = 72.0) -> float:
    """Closer in time is stronger, reaching zero at the window edge."""
    if gap_hours is None or gap_hours < 0 or gap_hours > window_hours:
        return 0.0
    return round(_clamp(1.0 - gap_hours / window_hours), 4)
