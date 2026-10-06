"""Which wallets the study reads (spec §5)."""

from src.study import selection as sel

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B, C, D = ("0x" + c * 40 for c in "abcd")
DAY = sel.DAY_MS
NOW = 1_790_000_000_000


def row(wallet, tier="WATCH", value=1e6, role="user", dropped=None, service=False):
    return {"wallet": wallet, "tier": tier, "tier_dropped_from": dropped, "is_service": service,
            "evidence": {"hl_role": role, "hl_account_value": value}}


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
