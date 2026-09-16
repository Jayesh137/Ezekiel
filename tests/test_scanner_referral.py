"""The scanner's referral and vault checks read the payload the API returns.

`_check_referral_link` read `referrerAddress` and `referredUsers`. The
`referral` endpoint returns neither: the referrer is `referredBy.referrer` and
the accounts on a code are `referrerState.data.referralStates[].user`
(measured 2026-09-16). So the check could never find a link, however real,
and it swallowed every failure as "no link" besides. `src/referral.py` already
parsed the real shape correctly; the scanner now uses it.
"""

import json

from src import scanner

TARGET_REFERRER = "0x4c78a97cef589b01bb91dbf893fffa14243d2444"
REFERRED = "0x3b2d7db2e70b4f4ea83043a8dc00f7e40198b5cc"
CAND = "0x7fdafde5cfb5465924316eced2d3715494c517d1"
HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
OTHER_VAULT = "0x1111111111111111111111111111111111111111"


def _reading(referrer=None, referred=()):
    doc = {"referredBy": {"referrer": referrer, "code": "X"} if referrer else None,
           "referrerState": {"stage": "needToCreateCode"}}
    if referred:
        doc["referrerState"] = {"stage": "ready", "data": {
            "code": "MINE", "referralStates": [{"user": u} for u in referred]}}
    return doc


def test_the_targets_referral_network_is_read_from_the_real_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    (tmp_path / "referral").mkdir()
    (tmp_path / "referral" / "latest.json").write_text(
        json.dumps(_reading(TARGET_REFERRER, [REFERRED])))
    assert scanner._load_target_referral_addresses() == {TARGET_REFERRER, REFERRED}


def test_a_candidate_referred_by_the_targets_network_is_linked(monkeypatch):
    monkeypatch.setattr(scanner, "hl_post", lambda body: _reading(TARGET_REFERRER))
    assert scanner._check_referral_link(CAND, {TARGET_REFERRER}) is True


def test_a_candidate_whose_code_names_the_network_is_linked(monkeypatch):
    monkeypatch.setattr(scanner, "hl_post", lambda body: _reading(None, [REFERRED]))
    assert scanner._check_referral_link(CAND, {REFERRED}) is True


def test_a_failed_referral_read_is_reported_not_silent(monkeypatch, capsys):
    monkeypatch.setattr(scanner, "hl_post", lambda body: {})
    assert scanner._check_referral_link(CAND, {TARGET_REFERRER}) is False
    assert "unreadable" in capsys.readouterr().out


def test_a_shared_vault_is_not_overlap(monkeypatch):
    """Rule 9: HLP is shared by everyone, so sharing it identifies nobody."""
    monkeypatch.setattr(scanner, "hl_post", lambda body: [
        {"vaultAddress": HLP}, {"vaultAddress": OTHER_VAULT}])
    monkeypatch.setattr(scanner, "load_config",
                        lambda: {"hl_shared_destinations": [HLP]})
    assert scanner._check_vault_overlap(CAND, {HLP, OTHER_VAULT}) == [OTHER_VAULT]
