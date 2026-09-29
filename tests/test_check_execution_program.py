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
