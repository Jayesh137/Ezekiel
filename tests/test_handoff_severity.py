"""A handoff's severity is the capital the wallet has held.

Measured 2026-11-14 over 397 random venue takers (299 born inside his history):
27 (9.0%) scored as a dormancy handoff by chance and each paged CRITICAL, down
to accounts holding nothing; 2 of 299 had ever held $100K, 0 of 299 $1M.
"""

from src import alerts, dormancy


def _portfolio(points):
    return [["day", {"accountValueHistory": points[-1:], "vlm": "0"}],
            ["allTime", {"accountValueHistory": points, "vlm": "0"}]]


def test_capital_reads_the_newest_value_and_the_peak():
    got = dormancy.capital(_portfolio([[1, "0"], [2, "5000000.5"], [3, "1200.0"]]))
    assert got == {"value": 1200.0, "peak_value": 5000000.5}


def test_an_unreadable_portfolio_has_no_capital_reading_never_zero():
    assert dormancy.capital(None) == {"value": None, "peak_value": None}
    assert dormancy.capital([["allTime", {"accountValueHistory": [["x", "y"]]}]]) == {
        "value": None, "peak_value": None}


def test_severity_follows_the_most_it_has_held():
    assert dormancy.handoff_severity({"peak_value": 51_000_000.0}) == "CRITICAL"
    assert dormancy.handoff_severity({"peak_value": 1_000_000.0}) == "CRITICAL"
    assert dormancy.handoff_severity({"peak_value": 250_000.0}) == "HIGH"
    assert dormancy.handoff_severity({"peak_value": 2_000.0}) == "INFO"
    assert dormancy.handoff_severity({"peak_value": 0.0}) == "INFO"


def test_a_wallet_whose_value_was_not_read_is_never_assumed_harmless():
    assert dormancy.handoff_severity({}) == "HIGH"
    assert dormancy.handoff_severity({"peak_value": None}) == "HIGH"
    assert dormancy.handoff_severity({"peak_value": True}) == "HIGH"


def test_the_alert_subject_carries_that_severity(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown", lambda key, hours, subject, body: sent.append((subject, body)) or True)
    base = {"delay_days": 1, "gap_length": 13, "score": 0.81}
    alerts.alert_dormancy_handoff("0x" + "a" * 40, {**base, "value": 3.0, "peak_value": 1900.0})
    alerts.alert_dormancy_handoff("0x" + "b" * 40, {**base, "value": 4.9e7, "peak_value": 5.1e7})
    assert "] INFO: Dormancy Handoff" in sent[0][0]
    assert "] CRITICAL: Dormancy Handoff" in sent[1][0]
    assert "the most it has held is $51,000,000" in sent[1][1]
    assert alerts._severity_of(sent[0][0]) not in alerts.ESCALATING_SEVERITIES
    assert alerts._severity_of(sent[1][0]) == "CRITICAL"


def test_a_wallet_that_fills_with_capital_pages_again_at_each_step(monkeypatch):
    keys = []
    monkeypatch.setattr(alerts, "_send_with_cooldown", lambda key, hours, subject, body: keys.append(key) or True)
    w = "0x" + "c" * 40
    base = {"delay_days": 1, "gap_length": 13, "score": 0.81}
    for peak in (999.0, 5_000_000.0):
        alerts.alert_dormancy_handoff(w, {**base, "peak_value": peak})
    assert keys == [f"handoff_{w}_info", f"handoff_{w}"]


# --- a sub-account is judged on its master's birth -----------------------------
# Two of the four best-funded handoff leads on 2026-11-14 were sub-accounts of
# firms that long predate the silence they were "born" in.

DAY = 86_400_000


def _answers(role, master_birth=None):
    def fetch(body):
        if body["type"] == "userRole":
            return role
        if master_birth is None:
            return {"ok": False, "error": "timeout"}
        return {"ok": True, "data": _portfolio([[master_birth, "5"]])}
    return fetch


def test_an_ordinary_account_is_judged_on_its_own_birth():
    got = dormancy.operator_birth("0xa", 200 * DAY, _answers({"ok": True, "data": {"role": "user"}}))
    assert got == {"birth_ms": 200 * DAY, "master": None}


def test_a_sub_account_takes_its_masters_earlier_birth():
    role = {"ok": True, "data": {"role": "subAccount", "data": {"master": "0xMASTER"}}}
    got = dormancy.operator_birth("0xa", 200 * DAY, _answers(role, master_birth=20 * DAY))
    assert got == {"birth_ms": 20 * DAY, "master": "0xmaster"}


def test_an_unreadable_role_or_master_raises_and_is_never_a_verdict():
    import pytest
    with pytest.raises(ValueError):
        dormancy.operator_birth("0xa", 1, _answers({"ok": False, "error": "rate limited"}))
    with pytest.raises(ValueError):
        dormancy.operator_birth("0xa", 1, _answers({"ok": True, "data": []}))
    role = {"ok": True, "data": {"role": "subAccount", "data": {"master": "0xmaster"}}}
    with pytest.raises(ValueError):
        dormancy.operator_birth("0xa", 1, _answers(role, master_birth=None))


def test_a_sub_account_of_an_old_master_is_not_a_handoff_in_the_collector():
    from scripts import check_dormancy as check
    target = [{"time": d * DAY} for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 40)]      # a 30-day silence from day 10
    sub = "0x" + "5" * 40

    def fetch(body):
        if body["type"] == "userRole":
            return {"ok": True, "data": {"role": "subAccount", "data": {"master": "0x" + "6" * 40}}}
        if body["type"] == "portfolio":
            born = 11 * DAY if body["user"] == sub else 2 * DAY                    # the master is old
            return {"ok": True, "data": _portfolio([[born, "5000000"]])}
        return {"ok": True, "data": [{"time": 11 * DAY}]}

    candidates, coverage = check.collect_candidates([sub], target, {}, fetch=fetch)
    assert coverage["masters"] == {sub: "0x" + "6" * 40}
    assert check.build_report(target, candidates)["handoffs"] == {}
