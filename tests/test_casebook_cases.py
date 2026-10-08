"""Case-file merge: nothing is forgotten, and a lapse is told apart from a refutation (spec §6.3)."""

from src.casebook import cases

A = "0x" + "a" * 40
H = 3_600_000
T0 = 1_790_000_000_000      # 2026-09-21T13:33:20Z


def item(kind, strength=None, summary="s", **facts):
    return {"key": kind, "kind": kind, "strength": strength, "facts": facts, "summary": summary}


def opened(origin="live"):
    case, events = cases.new_case(A, T0, ["tier:POSSIBLE"], origin)
    assert [e["kind"] for e in events] == ["case_opened"]
    return case


def test_a_new_item_is_current_and_announced_once():
    case = opened()
    events = cases.merge_items(case, [item("direct_transfer", 1000.0)], set(), at_ms=T0, origin="live")
    entry = case["evidence"]["direct_transfer"]
    assert entry["status"] == "current" and entry["live"] is True and entry["seen_days"] == 1
    assert [e["kind"] for e in events] == ["evidence_new"]
    assert cases.merge_items(case, [item("direct_transfer", 1000.0)], set(), at_ms=T0 + H, origin="live") == []
    assert entry["seen_days"] == 1
    cases.merge_items(case, [item("direct_transfer", 1000.0)], set(), at_ms=T0 + 30 * H, origin="live")
    assert entry["seen_days"] == 2


def test_context_items_are_kept_without_events():
    case = opened()
    assert cases.merge_items(case, [item("graph_reach")], set(), at_ms=T0, origin="live") == []
    assert "graph_reach" in case["evidence"]


def test_latest_facts_are_kept_with_the_strongest_beside_them():
    case = opened()
    cases.merge_items(case, [item("amount_correlation", 0.9, "strong")], set(), at_ms=T0, origin="live")
    cases.merge_items(case, [item("amount_correlation", 0.4, "weak")], set(), at_ms=T0 + H, origin="live")
    entry = case["evidence"]["amount_correlation"]
    assert entry["strength"] == 0.4 and entry["summary"] == "weak"
    assert entry["peak_strength"] == 0.9 and entry["peak_summary"] == "strong"


def test_a_short_absence_is_not_a_lapse():
    case = opened()
    cases.merge_items(case, [item("direct_transfer")], set(), at_ms=T0, origin="live")
    assert cases.merge_items(case, [], set(), at_ms=T0 + H, origin="live") == []
    entry = case["evidence"]["direct_transfer"]
    assert entry["status"] == "current" and entry["absent_since"]
    cases.merge_items(case, [item("direct_transfer")], set(), at_ms=T0 + 2 * H, origin="live")
    assert entry["absent_since"] is None and entry["status"] == "current"


def test_a_live_item_gone_a_day_lapses_and_can_return():
    case = opened()
    cases.merge_items(case, [item("direct_transfer")], set(), at_ms=T0, origin="live")
    cases.merge_items(case, [], set(), at_ms=T0 + H, origin="live")
    events = cases.merge_items(case, [], set(), at_ms=T0 + 26 * H, origin="live")
    assert case["evidence"]["direct_transfer"]["status"] == "lapsed"
    assert [e["kind"] for e in events] == ["evidence_lapsed"]
    events = cases.merge_items(case, [item("direct_transfer")], set(), at_ms=T0 + 50 * H, origin="live")
    assert case["evidence"]["direct_transfer"]["status"] == "current"
    assert events[0]["kind"] == "evidence_returned" and events[0]["detail"]["was"] == "lapsed"


def test_a_backfilled_item_that_ends_is_historical():
    case = opened("backfill")
    cases.merge_items(case, [item("direct_transfer")], set(), at_ms=T0, origin="backfill")
    cases.merge_items(case, [], set(), at_ms=T0 + H, origin="backfill")
    events = cases.merge_items(case, [], set(), at_ms=T0 + 30 * H, origin="backfill")
    assert case["evidence"]["direct_transfer"]["status"] == "historical"
    assert events[0]["kind"] == "evidence_historical"


def test_an_item_never_seen_live_is_historical_even_when_it_ends_live():
    case = opened("backfill")
    cases.merge_items(case, [item("direct_transfer")], set(), at_ms=T0, origin="backfill")
    cases.merge_items(case, [], set(), at_ms=T0 + H, origin="live")
    cases.merge_items(case, [], set(), at_ms=T0 + 30 * H, origin="live")
    assert case["evidence"]["direct_transfer"]["status"] == "historical"


def test_a_recheck_that_finds_nothing_is_a_refutation():
    case = opened()
    cases.merge_items(case, [item("dormancy_handoff", 0.5)], set(), at_ms=T0, origin="live")
    cases.merge_items(case, [], {"dormancy_handoff"}, at_ms=T0 + H, origin="live")
    cases.merge_items(case, [], set(), at_ms=T0 + 30 * H, origin="live")
    assert case["evidence"]["dormancy_handoff"]["status"] == "refuted"


def test_protocol_facts_stand():
    case = opened()
    cases.merge_items(case, [item("shared_agent")], set(), at_ms=T0, origin="live")
    cases.merge_items(case, [], set(), at_ms=T0 + H, origin="live")
    events = cases.merge_items(case, [], set(), at_ms=T0 + 30 * H, origin="live")
    assert case["evidence"]["shared_agent"]["status"] == "standing"
    assert events[0]["kind"] == "evidence_standing"


def test_tier_days_record_changes_only_and_the_best_tier_of_a_day():
    case = opened()
    assert cases.update_roster(case, {"tier": "POSSIBLE"}, T0, "live") == []
    assert cases.update_roster(case, {"tier": "POSSIBLE"}, T0 + 30 * H, "live") == []
    events = cases.update_roster(case, {"tier": "PROBABLE"}, T0 + 31 * H, "live")
    assert [e["detail"] for e in events] == [{"frm": "POSSIBLE", "to": "PROBABLE"}]
    assert cases.update_roster(case, {"tier": "WATCH"}, T0 + 32 * H, "live") == []   # same day: best kept
    events = cases.update_roster(case, {"tier": "WATCH"}, T0 + 60 * H, "live")
    assert events[0]["detail"] == {"frm": "PROBABLE", "to": "WATCH"}
    roster = case["roster"]
    assert [t for _, t in roster["tiers"]] == ["POSSIBLE", "PROBABLE", "WATCH"]
    assert roster["peak_tier"] == "PROBABLE" and roster["tier"] == "WATCH"


def test_roster_reasons_and_hyperliquid_facts_survive_the_roster_forgetting_them():
    case = opened()
    cases.update_roster(case, {"tier": "POSSIBLE", "reasons": ["Paid his deposit address"],
                               "evidence": {"hl_role": "user", "hl_total_value": 5.0}}, T0, "live")
    cases.update_roster(case, {"tier": "WATCH", "reasons": [],
                               "evidence": {"hl_role": "user", "hl_total_value": None}},
                        T0 + 30 * H, "live")
    assert case["roster"]["reasons"] == ["Paid his deposit address"]
    assert case["hl"]["roster"]["total_value"] == 5.0        # rule 6: None never overwrites a reading


def test_config_marks_known_wallets_and_rulings():
    case = opened()
    config = {"known_self_wallets": [A], "casebook_rulings": {A: {"verdict": "not_him", "note": "mm"}}}
    events = cases.apply_config(case, config, at_ms=T0, origin="live")
    assert case["known"] == "config:known_self" and case["ruling"]["verdict"] == "not_him"
    assert [e["kind"] for e in events] == ["ruling_changed"]
    assert cases.apply_config(case, config, at_ms=T0 + H, origin="live") == []


def test_score_history_is_one_row_a_day_and_moves_are_events():
    case = opened()
    score = {"model": "m1", "now": -3.0, "central": -2.0, "ceiling": -1.0, "p_central": 0.01,
             "families": {}}
    assert cases.record_score(case, score, at_ms=T0, origin="live") == []
    assert cases.record_score(case, {**score, "central": -1.8}, at_ms=T0 + H, origin="live") == []
    assert len(case["score_days"]) == 1 and case["score_days"][0][2] == -1.8
    events = cases.record_score(case, {**score, "central": -1.2}, at_ms=T0 + 30 * H, origin="live")
    assert events[0]["kind"] == "score_moved" and events[0]["detail"]["frm"] == -2.0
    assert len(case["score_days"]) == 2
    # A model change re-bases silently: every case moving at once is not news about any of them.
    assert cases.record_score(case, {**score, "model": "m2", "central": 1.0}, at_ms=T0 + 60 * H,
                              origin="live") == []


def test_touch_keeps_the_latest_change():
    case = opened()
    cases.touch({A: case}, [cases.event(T0 + 5 * H, A, "hl_woke", "live")])
    assert case["last_change"] == cases.iso(T0 + 5 * H)


def test_long_tier_and_score_histories_are_bounded():
    case = opened()
    tiers = ["POSSIBLE", "WATCH"]
    for i in range(cases.MAX_TIERS + 50):
        cases.update_roster(case, {"tier": tiers[i % 2]}, T0 + i * 25 * H, "live")
    assert len(case["roster"]["tiers"]) == cases.MAX_TIERS
    assert case["roster"]["tiers"][0][1] == "POSSIBLE"         # the first entry is kept
    for i in range(cases.MAX_SCORE_DAYS + 200):
        cases.record_score(case, {"model": "m", "now": -3.0, "central": float(i % 3), "ceiling": 0.0,
                                  "families": {}}, at_ms=T0 + i * 25 * H, origin="live")
    assert len(case["score_days"]) < cases.MAX_SCORE_DAYS + 60
