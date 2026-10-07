"""Alert policy for the execution-program detector.

A clip-table match is a BEHAVIOUR signal. On its own, once measured rare, it is a
lead worth investigating (HIGH — our inference). Reproducing his program AND
already carrying an independent financial or protocol vector is two independent
vectors agreeing, which is the strongest thing this project produces (CRITICAL).
An unmeasured or common match is recorded, never routed (rules 4, 9).
"""

from scripts import check_execution_program as chk


def _match(wallet, ratio, matched, compared, **extra):
    return {"wallet": wallet, "signature": {
        "clip_table": {f"C{i}": {"size": i + 1, "share": 1.0, "count": 40} for i in range(compared)},
        "program_runs": 5, **extra}}


def _target(n):
    return {"clip_table": {f"C{i}": {"size": i + 1, "share": 1.0, "count": 40} for i in range(n)},
            "program_runs": 5}


def test_build_report_flags_discriminating_and_independent():
    target = _target(6)
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3}
    rows = [{"wallet": "0xhit", "signature": target}]
    report = chk.build_report(target, rows, census, roster_vectors={"0xhit": {"transfer"}})
    assert report["census_present"] is True
    m = report["matches"][0]
    assert m["clip_match_ratio"] == 1.0
    assert m["discriminating"] is True
    assert m["has_independent_vector"] is True


def test_a_roster_documents_wallet_key_reaches_the_independent_vector_check():
    # Every row of data/roster/latest.json is keyed `wallet` (2,635 of 2,635 when
    # measured, none `address`). The map read `address`, so it was always empty and a
    # match beside an independent vector could never be CRITICAL. The other tests hand
    # build_report a ready-made map; this one goes through the roster document's shape.
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3}
    target = _target(6)
    roster = {"wallets": [{"wallet": "0xHit", "tier": "POSSIBLE", "vectors": ["transfer"]}]}
    report = chk.build_report(target, [{"wallet": "0xhit", "signature": target}], census,
                              chk.roster_vector_map(roster))
    assert report["matches"][0]["has_independent_vector"] is True
    assert [severity for severity, _ in chk.decide_alerts(report)] == ["CRITICAL"]


def test_a_roster_row_keyed_address_is_still_read():
    roster = {"wallets": [{"address": "0xABC", "vectors": ["linkage"]},
                          {"vectors": ["transfer"]}]}
    assert chk.roster_vector_map(roster) == {"0xabc": {"linkage"}}


def test_no_census_records_the_match_but_flags_it_undiscriminating():
    target = _target(6)
    report = chk.build_report(target, [{"wallet": "0xhit", "signature": target}], None, {})
    assert report["census_present"] is False
    assert report["matches"][0]["discriminating"] is False


def test_alert_is_critical_when_an_independent_vector_agrees():
    match = {"wallet": "0xabc", "discriminating": True, "has_independent_vector": True,
             "clip_match_ratio": 1.0, "clips_matched": 6}
    assert chk.decide_alerts({"matches": [match]}) == [("CRITICAL", match)]


def test_alert_is_high_for_a_rare_match_alone():
    match = {"wallet": "0xabc", "discriminating": True, "has_independent_vector": False,
             "clip_match_ratio": 1.0, "clips_matched": 6}
    assert chk.decide_alerts({"matches": [match]}) == [("HIGH", match)]


def test_an_undiscriminating_match_is_not_routed():
    match = {"wallet": "0xabc", "discriminating": False, "has_independent_vector": True,
             "clip_match_ratio": 0.5, "clips_matched": 3}
    assert chk.decide_alerts({"matches": [match]}) == []


def test_the_target_never_alerts_against_itself():
    target = _target(6)
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3}
    report = chk.build_report(target, [{"wallet": "0xTARGET", "signature": target}],
                             census, {"0xTARGET": {"transfer"}}, target_wallet="0xtarget")
    assert report["matches"] == []


def test_the_execution_alert_reaches_a_channel_at_a_routable_severity(monkeypatch):
    from src import alerts
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(subject) or True)
    match = {"clip_match_ratio": 1.0, "clips_matched": 6, "clips_compared": 6,
             "cadence_agreement": True, "offset_agreement": True}
    for severity in ("CRITICAL", "HIGH"):
        sent.clear()
        alerts.alert_execution_program_match("0xabc", match, severity)
        assert alerts._severity_of(sent[0]) == severity
        assert alerts._severity_of(sent[0]) in alerts.ESCALATING_SEVERITIES


def test_an_invented_severity_is_clamped_to_a_routable_one(monkeypatch):
    from src import alerts
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(subject) or True)
    alerts.alert_execution_program_match("0xabc", {"clips_matched": 6, "clips_compared": 6}, "ELEVATED")
    assert alerts._severity_of(sent[0]) in alerts.ESCALATING_SEVERITIES


def test_select_candidates_reserves_room_for_fresh_wallets_on_his_coins():
    roster = [f"0x{i:040x}" for i in range(10)]
    discovery = {"candidates": [
        {"wallet": "0xfresh1", "markets": {"NEAR": 3}, "trade_count": 3},
        {"wallet": "0xfresh2", "markets": {"ZEC": 1}, "trade_count": 1},
        {"wallet": "0xoffcoin", "markets": {"WIF": 9}, "trade_count": 9},  # not a clip coin
    ]}
    clip_coins = {"NEAR", "ZEC", "BTC"}
    picked = chk.select_candidates(roster, discovery, clip_coins, cap=6)
    assert "0xfresh1" in picked and "0xfresh2" in picked   # fresh wallets on his coins kept
    assert "0xoffcoin" not in picked                        # off-coin discovery ignored
    assert len(picked) == 6                                 # capped
    assert picked[0] in roster                              # roster leads still lead


def test_select_candidates_dedupes_and_survives_missing_discovery():
    roster = ["0xaaa", "0xbbb"]
    assert chk.select_candidates(roster, None, {"NEAR"}, cap=10) == ["0xaaa", "0xbbb"]
    dup = {"candidates": [{"wallet": "0xaaa", "markets": {"NEAR": 1}}]}
    assert chk.select_candidates(roster, dup, {"NEAR"}, cap=10) == ["0xaaa", "0xbbb"]


def test_unread_and_oldest_wallets_are_read_first():
    wallets = ["0xa", "0xb", "0xc", "0xd"]
    last = {"0xa": "2026-09-30T02:00:00+00:00", "0xb": "2026-09-30T01:00:00+00:00"}
    # Never-read in selection order, then the longest unread.
    assert chk.order_by_staleness(wallets, last) == ["0xc", "0xd", "0xb", "0xa"]


def test_the_budget_rotates_through_the_whole_list():
    wallets = [f"0x{i}" for i in range(10)]
    last, seen = {}, set()
    for run in range(4):  # a budget of 3 reads a run reaches all 10 in 4 runs
        for wallet in chk.order_by_staleness(wallets, last)[:3]:
            last[wallet] = f"2026-09-30T0{run}:00:0{len(seen) % 10}"
            seen.add(wallet)
    assert seen == set(wallets)


def test_an_unread_wallet_keeps_its_match_but_is_not_re_alerted():
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3}
    target = _target(6)
    old = chk.build_report(target, [{"wallet": "0xhit", "signature": target}], census, {})
    carried = chk.carry_forward(old, ["0xhit", "0xother"], read_wallets={"0xother"}, census=census)
    assert [m["wallet"] for m in carried] == ["0xhit"]
    assert carried[0]["discriminating"] is True and carried[0]["carried_forward"] is True
    assert chk.decide_alerts({"matches": carried}) == []
    assert "0xhit" in chk.roster_execution_matches({"matches": carried})


def test_a_reread_or_dropped_wallet_is_not_carried():
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3}
    target = _target(6)
    old = chk.build_report(target, [{"wallet": "0xhit", "signature": target}], census, {})
    assert chk.carry_forward(old, ["0xhit"], read_wallets={"0xhit"}, census=census) == []
    assert chk.carry_forward(old, ["0xnew"], read_wallets=set(), census=census) == []
    assert chk.carry_forward(None, ["0xhit"], read_wallets=set(), census=census) == []


def test_the_read_budget_fits_inside_the_step_timeout():
    # The budget ran 600s inside a 4-minute step, so every trace run was killed.
    import re
    from pathlib import Path
    workflow = (Path(__file__).parent.parent / ".github/workflows/trace.yml").read_text()
    step = workflow.split("name: Match execution program", 1)[1].split("- name:", 1)[0]
    minutes = int(re.search(r"timeout-minutes:\s*(\d+)", step).group(1))
    assert chk.READ_BUDGET_SECONDS + 60 < minutes * 60
