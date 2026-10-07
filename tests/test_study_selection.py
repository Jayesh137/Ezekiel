"""Which wallets the study reads (spec §5)."""

from src.study import selection as sel

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B, C, D = ("0x" + c * 40 for c in "abcd")
X, Y, Z = ("0x" + c * 40 for c in "123")
DAY = sel.DAY_MS
NOW = 1_790_000_000_000


def row(wallet, tier="WATCH", value=1e6, role="user", dropped=None, service=False,
        total=None, volume=None, strength=None):
    """A roster row. `value` is the perp margin the identity probe read from webData2;
    `total` and `volume` are the portfolio fields (absent: a row probed before they
    existed); `strength` is a dormancy score, which evidence_strength ranks by."""
    evidence = {"hl_role": role, "hl_account_value": value}
    if total is not None:
        evidence["hl_total_value"] = total
    if volume is not None:
        evidence["hl_month_volume"] = volume
    if strength is not None:
        evidence["dormancy_handoff"] = {"score": strength}
    return {"wallet": wallet, "tier": tier, "tier_dropped_from": dropped, "is_service": service,
            "evidence": evidence}


def trader(**kwargs):
    must_trade = kwargs.pop("must_trade", False)
    return sel.hl_trader(row(A, **kwargs), must_trade=must_trade)


def test_sources_in_priority_order_and_the_target_never_studied():
    config = {"target_wallet": T, "watch_wallets": [A], "study_wallets": [T]}
    roster = {"wallets": [row(B, "POSSIBLE"), row(C, "WATCH", dropped="PROBABLE"),
                          row(T, "CONFIRMED")]}
    sources, _ = sel.by_source(config, roster, [D, T], {}, NOW)
    assert sources == {"pinned": [A], "roster_lead": [B], "decayed_lead": [C], "detector": [D]}


def test_pinned_wallets_are_read_from_config_entries_as_well_as_strings():
    # config.json writes watch_wallets as {"address", "why", "added"} entries.
    entry = {"address": "0x" + "A" * 40, "why": "a question asked by hand", "added": "2026-09-11"}
    config = {"target_wallet": T, "watch_wallets": [entry, {"why": "no address"}],
              "study_wallets": [B]}
    sources, _ = sel.by_source(config, {"wallets": []}, [], {}, NOW)
    assert sources["pinned"] == [A, B]


def test_wallets_hyperliquid_does_not_know_as_traders_are_not_studied():
    roster = {"wallets": [row(A, "POSSIBLE", role="missing"), row(B, "POSSIBLE", value=54.0),
                          row(C, "POSSIBLE", value=None)]}
    sources, _ = sel.by_source({"target_wallet": T}, roster, [], {}, NOW)
    assert sources["roster_lead"] == []


def test_services_are_never_studied_even_when_a_detector_names_them():
    roster = {"wallets": [row(A, "INFRASTRUCTURE"), row(B, "POSSIBLE", service=True)]}
    sources, _ = sel.by_source({"target_wallet": T}, roster, [A, B], {}, NOW)
    assert all(not wallets for wallets in sources.values())


def test_a_decayed_lead_is_kept_for_sixty_days_then_released():
    roster = {"wallets": [row(A, "WATCH", dropped="POSSIBLE")]}
    sources, seen = sel.by_source({"target_wallet": T}, roster, [], {}, NOW)
    assert sources["decayed_lead"] == [A] and seen == {A: NOW}
    sources, seen = sel.by_source({"target_wallet": T}, roster, [], seen, NOW + 61 * DAY)
    assert sources["decayed_lead"] == [] and seen == {A: NOW}


def test_sticky_members_stay_for_fourteen_days_unless_a_higher_source_needs_the_slot():
    previous = {A: {"source": "detector", "since_ms": NOW - 3 * DAY}}
    sources = {"pinned": [], "roster_lead": [B], "decayed_lead": [], "detector": [C]}
    members = sel.choose(sources, previous, NOW, max_wallets=2)
    assert [m["wallet"] for m in members] == [B, A]
    assert members[1]["since_ms"] == NOW - 3 * DAY and members[0]["since_ms"] == NOW
    crowded = {**sources, "roster_lead": [B, D]}
    assert [m["wallet"] for m in sel.choose(crowded, previous, NOW, max_wallets=2)] == [B, D]


def test_a_member_older_than_fourteen_days_competes_like_anyone_else():
    previous = {A: {"source": "detector", "since_ms": NOW - 15 * DAY}}
    sources = {"pinned": [], "roster_lead": [], "decayed_lead": [], "detector": [C]}
    assert [m["wallet"] for m in sel.choose(sources, previous, NOW, max_wallets=1)] == [C]


def test_a_sticky_member_that_became_blocked_is_dropped():
    previous = {T: {"source": "pinned", "since_ms": NOW - DAY}}
    sources = {"pinned": [], "roster_lead": [], "decayed_lead": [], "detector": []}
    assert sel.choose(sources, previous, NOW, blocked={T}) == []


# --- Hyperliquid presence is total value and 30-day volume, never perp margin -------------

def test_a_wallet_is_a_trader_by_total_value_and_month_volume_not_perp_margin():
    """Measured 2026-10-07: a POSSIBLE lead with $0 perp margin held $9.37M in spot and
    traded $80.0M in 30 days; webData2's accountValue could not see either."""
    assert trader(value=0.0, volume=80_039_479.12)          # perp 0, traded: in
    assert trader(value=0.0, total=0.0, volume=0.01)        # any volume at all is a trade
    assert trader(value=0.0, total=10_000.0, volume=0.0)    # holds exactly the floor: in
    assert trader(value=0.0, total=10_000, volume=0.0)      # an int is a number too
    assert not trader(value=0.0, total=9_999.99, volume=0.0)  # a cent under: out
    assert not trader(value=0.0, total=0.0, volume=0.0)
    assert not trader(value=5e6, total=0.0, volume=0.0)     # the measured path never asks perp margin
    assert trader(value=0.0, total=10_000.0)                # volume unread: holding is enough
    assert not trader(value=0.0, total=9_999.99)
    assert trader(value=0.0, total=10_000.0, volume=0.0, role="subAccount")
    for role in ("vault", "missing", "agent", None):
        assert not trader(value=0.0, total=9e6, volume=5e6, role=role)


def test_a_bool_is_not_a_reading():
    stamped = row(A, value=0.0)
    stamped["evidence"].update(hl_month_volume=True, hl_total_value=True)
    assert not sel.hl_trader(stamped)      # unread, so judged on perp margin, which is 0
    stamped["evidence"]["hl_account_value"] = 10_000.0
    assert sel.hl_trader(stamped)          # and it is the perp margin that now decides


def test_a_row_probed_before_the_portfolio_fields_existed_is_judged_on_perp_margin():
    assert trader(value=10_000.0)
    assert not trader(value=9_999.99)
    assert not trader(value=None)
    assert not trader(value=10_000.0, role="missing")
    assert trader(value=10_000.0, role="subAccount")
    # The roster writes both keys, None when the identity row lacks them.
    stamped = row(A, value=10_000.0)
    stamped["evidence"].update(hl_total_value=None, hl_month_volume=None)
    assert sel.hl_trader(stamped)
    # The legacy rule cannot tell whether it traded, so must_trade changes nothing.
    assert sel.hl_trader(stamped, must_trade=True)
    assert trader(value=10_000.0, must_trade=True)
    assert not trader(value=9_999.99, must_trade=True)


def test_must_trade_wants_a_trade_not_a_balance():
    assert not trader(value=0.0, total=57_000_000.0, volume=0.0, must_trade=True)
    assert not trader(value=0.0, total=57_000_000.0, must_trade=True)
    assert trader(value=0.0, total=57_000_000.0, volume=1_000.0, must_trade=True)
    assert trader(value=0.0, volume=0.01, must_trade=True)
    assert not trader(value=0.0, volume=0.0, must_trade=True, role="vault")


# --- config wallets that do not trade on Hyperliquid are never studied (spec §5) -----------

def test_a_config_wallet_that_holds_money_but_does_not_trade_is_not_studied():
    """`0x1419e75330…` is a CONFIRMED config wallet holding $57.0M that traded $0 in 30 days."""
    config = {"target_wallet": T, "known_self_wallets": [A]}
    parked = {"wallets": [row(A, "CONFIRMED", value=0.0, total=57_000_000.0, volume=0.0)]}
    assert A in sel.blocked_wallets(config, parked)
    sources, _ = sel.by_source(config, parked, [A], {}, NOW)
    assert all(A not in wallets for wallets in sources.values())
    pinned = {**config, "watch_wallets": [A]}
    sources, _ = sel.by_source(pinned, parked, [], {}, NOW)
    assert sources["pinned"] == []       # blocked wins over a pin


def test_a_config_wallet_that_trades_is_a_roster_lead_and_the_target_stays_blocked():
    config = {"target_wallet": T, "known_self_wallets": [A, T]}
    trading = {"wallets": [row(A, "CONFIRMED", value=0.0, total=57_000_000.0, volume=1_000.0),
                           row(T, "CONFIRMED", value=0.0, total=9e9, volume=9e9)]}
    assert sel.blocked_wallets(config, trading) == {T}
    sources, _ = sel.by_source(config, trading, [], {}, NOW)
    assert sources["roster_lead"] == [A]
    # A service stays blocked whatever it traded.
    service = {"wallets": [row(A, "INFRASTRUCTURE", value=0.0, total=1e6, volume=1e6),
                           row(B, "CONFIRMED", value=0.0, total=1e6, volume=1e6, service=True)]}
    assert sel.blocked_wallets({**config, "known_self_wallets": [A, B]}, service) == {A, B, T}


def test_a_config_wallet_with_no_roster_row_is_not_known_to_trade_and_is_not_studied():
    config = {"target_wallet": T, "known_self_wallets": [A]}
    for roster in (None, {}, {"wallets": []}, {"wallets": [row(B, "POSSIBLE")]}):
        assert sel.blocked_wallets(config, roster) == {A, T}
    assert sel.blocked_wallets({"target_wallet": T}, None) == {T}
    # An entry is a bare address or {"address": ...}, as roster.pinned_wallets reads it.
    entries = {"target_wallet": T, "known_self_wallets": [{"address": "0x" + "A" * 40},
                                                          {"why": "no address"}, None]}
    assert sel.blocked_wallets(entries, None) == {A, T}


# --- detector finds: the roster's role gates them, the caller's measurement does not -------

def test_a_detector_find_is_dropped_only_when_the_roster_says_it_is_not_a_trading_account():
    roster = {"wallets": [row(A, value=0.0, role="user"),       # perp 0, no total or volume
                          row(B, role="vault"), row(C, role="missing"),
                          row(Y, value=0.0, role="subAccount"), row(Z, role="agent")]}
    sources, _ = sel.by_source({"target_wallet": T}, roster, [A, B, C, D, Y, Z], {}, NOW)
    # D has no roster row at all: unknown is not no. No value test either: the detector
    # measured the account itself, fresher than the roster's weekly perp read.
    assert sources["detector"] == [A, D, Y]


def test_a_detector_find_whose_roster_row_never_read_a_role_is_not_yet_hl_present():
    # Spec §5: HL-present is the roster's hl_role. A row exists but names no role (the
    # identity probe has not reached it, or its userRole read failed), so it waits.
    unread = row(A, role=None)
    never_probed = row(B)
    del never_probed["evidence"]["hl_role"]
    sources, _ = sel.by_source({"target_wallet": T},
                               {"wallets": [unread, never_probed]}, [A, B, C], {}, NOW)
    assert sources["detector"] == [C]


def test_detector_finds_are_ordered_by_evidence_strength_and_ties_keep_the_callers_order():
    config = {"target_wallet": T}
    only_z = {"wallets": [row(Z, strength=0.43)]}
    sources, _ = sel.by_source(config, only_z, [X, Y, Z], {}, NOW)
    assert sources["detector"] == [Z, X, Y]
    graded = {"wallets": [row(X, strength=0.2), row(Z, strength=0.43)]}
    sources, _ = sel.by_source(config, graded, [X, Y, Z], {}, NOW)
    assert sources["detector"] == [Z, X, Y]       # strongest first, the unscored last
    sources, _ = sel.by_source(config, {"wallets": []}, [Y, X, Z], {}, NOW)
    assert sources["detector"] == [Y, X, Z]       # no strength anywhere: the caller's order
    sources, _ = sel.by_source(config, only_z, [Y, X, Z, Z, Y], {}, NOW)
    assert sources["detector"] == [Z, Y, X]       # and a repeat is listed once


# --- order within a source ------------------------------------------------------------------

def test_roster_leads_are_ordered_by_strength_then_value_with_total_before_perp_margin():
    roster = {"wallets": [
        row(A, "POSSIBLE", value=5e6, strength=0.2),
        row(B, "POSSIBLE", value=1e6, strength=0.6),                      # strongest, least valuable
        row(C, "POSSIBLE", value=0.0, total=9e6, volume=1.0, strength=0.2),  # total 9M, perp 0
        row(D, "POSSIBLE", value=2e6, strength=0.2)]}
    sources, _ = sel.by_source({"target_wallet": T}, roster, [], {}, NOW)
    assert sources["roster_lead"] == [B, C, A, D]
    # Equal on both: the address decides, so the order never depends on the file.
    twins = {"wallets": [row(B, "POSSIBLE", value=1e6), row(A, "POSSIBLE", value=1e6)]}
    sources, _ = sel.by_source({"target_wallet": T}, twins, [], {}, NOW)
    assert sources["roster_lead"] == [A, B]


# --- the two clocks -------------------------------------------------------------------------

def test_a_decayed_lead_seen_exactly_sixty_days_ago_is_kept_and_a_millisecond_older_is_released():
    config, roster = {"target_wallet": T}, {"wallets": [row(A, "WATCH", dropped="POSSIBLE")]}
    sources, seen = sel.by_source(config, roster, [], {A: NOW - 60 * DAY}, NOW)
    assert sources["decayed_lead"] == [A] and seen == {A: NOW - 60 * DAY}
    sources, seen = sel.by_source(config, roster, [], {A: NOW - 60 * DAY - 1}, NOW)
    assert sources["decayed_lead"] == [] and seen == {A: NOW - 60 * DAY - 1}


def test_a_member_exactly_fourteen_days_old_is_not_sticky_and_one_a_millisecond_younger_is():
    sources = {"pinned": [], "roster_lead": [], "decayed_lead": [], "detector": [C]}
    exactly = {A: {"source": "detector", "since_ms": NOW - 14 * DAY}}
    younger = {A: {"source": "detector", "since_ms": NOW - 14 * DAY + 1}}
    assert [m["wallet"] for m in sel.choose(sources, exactly, NOW, max_wallets=1)] == [C]
    assert [m["wallet"] for m in sel.choose(sources, younger, NOW, max_wallets=1)] == [A]


def test_a_watch_row_that_never_held_a_lead_tier_is_not_a_decayed_lead():
    # A lead tier in tier_dropped_from is the fact; sitting at WATCH, or any fall, is not.
    roster = {"wallets": [row(A, "WATCH", dropped=None), row(B, "WATCH", dropped="WATCH")]}
    sources, seen = sel.by_source({"target_wallet": T}, roster, [], {}, NOW)
    assert sources["decayed_lead"] == [] and seen == {}


def test_a_decayed_lead_keeps_its_clock_while_its_role_is_unreadable():
    config = {"target_wallet": T}
    decayed = row(A, "WATCH", dropped="POSSIBLE")
    unread = row(A, "WATCH", dropped="POSSIBLE", role=None)
    # Inside its sixty days: out of the source while unread, back with the SAME clock.
    sources, seen = sel.by_source(config, {"wallets": [unread]}, [], {A: NOW}, NOW + 10 * DAY)
    assert sources["decayed_lead"] == [] and seen == {A: NOW}
    sources, seen = sel.by_source(config, {"wallets": [decayed]}, [], seen, NOW + 11 * DAY)
    assert sources["decayed_lead"] == [A] and seen == {A: NOW}
    # Past its sixty days: an unread run must not wipe the clock, or the next readable
    # run would admit it for a fresh sixty.
    sources, seen = sel.by_source(config, {"wallets": [unread]}, [], {A: NOW}, NOW + 61 * DAY)
    assert sources["decayed_lead"] == [] and seen == {A: NOW}
    sources, seen = sel.by_source(config, {"wallets": [decayed]}, [], seen, NOW + 62 * DAY)
    assert sources["decayed_lead"] == [] and seen == {A: NOW}


def test_a_blocked_decayed_lead_has_no_clock():
    roster = {"wallets": [row(A, "INFRASTRUCTURE", dropped="POSSIBLE"),
                          row(B, "WATCH", dropped="POSSIBLE", service=True)]}
    sources, seen = sel.by_source({"target_wallet": T}, roster, [], {A: NOW - DAY}, NOW)
    assert sources["decayed_lead"] == [] and seen == {}


# --- previous members are matched by normalised address -------------------------------------

def test_previous_members_are_matched_by_normalised_address():
    none = {"pinned": [], "roster_lead": [], "decayed_lead": [], "detector": []}
    shouting = {T.upper(): {"source": "pinned", "since_ms": NOW - DAY}}
    assert sel.choose(none, shouting, NOW, blocked={T}) == []
    mixed = "0x" + "AbCd" * 10
    previous = {mixed: {"source": "detector", "since_ms": NOW - 3 * DAY}}
    assert sel.choose(none, previous, NOW) == [
        {"wallet": "0x" + "abcd" * 10, "source": "detector", "since_ms": NOW - 3 * DAY}]
    # The same wallet named by a source keeps the clock it was stored under.
    named = {**none, "detector": ["0x" + "abcd" * 10]}
    assert sel.choose(named, previous, NOW)[0]["since_ms"] == NOW - 3 * DAY
    # A key that is no address is skipped, not studied.
    for junk in ("junk", "", "0x12"):
        assert sel.choose(none, {junk: {"source": "pinned", "since_ms": NOW}}, NOW) == []


# --- pinned wallets -------------------------------------------------------------------------

def test_a_pinned_wallet_is_studied_whatever_hyperliquid_knows_of_it_unless_blocked():
    config = {"target_wallet": T, "watch_wallets": [A, B, C]}
    roster = {"wallets": [row(A, "WATCH", role="missing"),
                          row(B, "INFRASTRUCTURE"), row(C, "WATCH", value=0.0, volume=0.0)]}
    sources, _ = sel.by_source(config, roster, [], {}, NOW)
    assert sources["pinned"] == [A, C]
