# tests/test_referral.py
"""The referral tripwire. Shapes: the target's live reading on 2026-09-12
(no code, referred by nobody) and the documented `ready` shape."""

from src import alerts, referral

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

NONE = {"referredBy": None, "cumVlm": "689287317.23", "unclaimedRewards": "0.0",
        "claimedRewards": "0.0", "builderRewards": "0.0",
        "referrerState": {"stage": "needToCreateCode"}, "rewardHistory": []}

READY = {"referredBy": None, "cumVlm": "700000000.0",
         "referrerState": {"stage": "ready", "data": {
             "code": "REBIRTH", "nReferrals": 2, "referredVlm": "1.0", "referredCumVlm": "1.0",
             "referralStates": [
                 {"user": A.upper(), "cumVlm": "5.0", "cumRewardedFeesSinceReferred": "0.1"},
                 {"user": B, "cumVlm": "6.0", "cumRewardedFeesSinceReferred": "0.2"}]}}}


def test_the_baseline_reads_as_no_code_and_nobody():
    assert referral.stage(NONE) == "needToCreateCode"
    assert referral.code(NONE) is None
    assert referral.referred(NONE) == [] and referral.referred_by(NONE) is None


def test_a_ready_state_names_its_code_and_every_referred_account():
    assert referral.stage(READY) == "ready" and referral.code(READY) == "REBIRTH"
    assert [r["address"] for r in referral.referred(READY)] == [A, B]


def test_creating_a_code_and_referring_accounts_are_changes():
    got = referral.changes(NONE, READY)
    assert got[0] == {"kind": "referral_code",
                      "before": {"stage": "needToCreateCode", "code": None},
                      "after": {"stage": "ready", "code": "REBIRTH"}}
    assert [(c["kind"], c["address"]) for c in got[1:]] == [
        ("referred_account", A), ("referred_account", B)]


def test_only_the_new_referred_account_is_reported_the_second_time():
    later = {**READY, "referrerState": {"stage": "ready", "data": {
        **READY["referrerState"]["data"],
        "referralStates": READY["referrerState"]["data"]["referralStates"] + [
            {"user": "0xcccccccccccccccccccccccccccccccccccccccc", "cumVlm": "1"}]}}}
    got = referral.changes(READY, later)
    assert [(c["kind"], c.get("address")) for c in got] == [
        ("referred_account", "0xcccccccccccccccccccccccccccccccccccccccc")]


def test_a_first_reading_and_a_failed_reading_are_not_changes():
    assert referral.changes(None, READY) == []
    assert referral.changes(READY, []) == []          # hl_post's failure shape
    assert referral.changes(READY, None) == []
    assert referral.changes(READY, READY) == []


def test_being_referred_is_a_change_with_the_referrer():
    got = referral.changes(NONE, {**NONE, "referredBy": {"referrer": T.upper(), "code": "X"}})
    assert got == [{"kind": "referred_by", "referrer": T}]


def test_the_alert_is_high_names_the_code_and_lists_the_accounts(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append((key, hours, subject, body)) or True)
    assert alerts.alert_referral_change(T, referral.changes(NONE, READY))
    key, hours, subject, body = sent[0]
    assert alerts._severity_of(subject) == "HIGH"
    assert "REBIRTH" in body and A in body and B in body
    assert key == "referral_change_0x45d26f28196d226497130c4bac709d808fed4029"


def test_no_changes_means_no_alert(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown", lambda *a: sent.append(a) or True)
    assert alerts.alert_referral_change(T, []) is False and sent == []
