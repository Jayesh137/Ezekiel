"""The perimeter step: measured inputs in, one table out, loop closed on HL."""

import json

import pytest

import scripts.build_perimeter as bp
from src import utils

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
F = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SINK = "0x" + "a" * 40
HOT = "0xee7ae85f2fe2239e27d9c1e23fffe168d63b4055"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "load_config",
                        lambda: {"target_wallet": T, "known_self_wallets": [TR, F]})
    (tmp_path / "deposit_sentinels").mkdir()
    (tmp_path / "deposit_sentinels" / "latest.json").write_text(
        json.dumps({"sentinels": {S: {"reason": "conduit"}}}))
    (tmp_path / "trace" / "registry").mkdir(parents=True)
    (tmp_path / "trace" / "registry" / "aa.json").write_text(json.dumps(
        {SINK: {"class": "quiet_eoa", "his_money": {"in_usd": 5e5, "share": 1.0}}}))
    (tmp_path / "trace" / "latest.json").write_text(json.dumps({"funders": [], "deposit_addresses": []}))
    sent = []
    monkeypatch.setattr("src.alerts.alert_perimeter_hl_account",
                        lambda member, reading: sent.append(member["address"]) or True)
    return tmp_path, sent


def _post(active=()):
    def post(body):
        user = body["user"]
        if body["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "25000.0" if user in active else "0.0"}}
        if body["type"] == "spotClearinghouseState":
            return {"balances": []}
        if body["type"] == "portfolio":
            hist = [[1_780_000_000_000, "0.0"], [1_781_000_000_000, "25000.0"]] if user in active else []
            return [["allTime", {"accountValueHistory": hist, "vlm": "2000000.0" if user in active else "0.0"}]]
        raise AssertionError(body)
    return post


def _substrate(addresses):
    return {a: ([{"src": S, "dst": HOT, "amount_usd": 5e6, "chain": "arbitrum"}] if a == S else [])
            for a in addresses}


def test_the_perimeter_is_written_with_roles_families_and_readings(sandbox):
    tmp, sent = sandbox
    assert bp.main([], post=_post(active={SINK}), substrate=_substrate,
                   now="2026-10-06T00:00:00+00:00", is_hot=lambda a: a == HOT) == 0
    doc = json.loads((tmp / "perimeter" / "latest.json").read_text())
    roles = {a: m["role"] for a, m in doc["members"].items()}
    assert roles == {T: "core", TR: "core", F: "core", S: "deposit", SINK: "sink"}
    assert doc["exchange_families"] == {f"deposit:{S}": [HOT]}
    assert doc["members"][SINK]["hl"]["active"] is True and doc["members"][S]["hl"]["active"] is False
    assert sent == [SINK]
    assert doc["closed"]["active"] == [SINK]


def test_his_deposit_addresses_stay_members_when_the_roster_calls_them_infrastructure(sandbox):
    # Production shape: the roster tiers every conduit INFRASTRUCTURE, and his
    # private deposit addresses are conduits. Spec §12: the perimeter contains
    # the sentinels.
    tmp, sent = sandbox
    (tmp / "roster").mkdir()
    (tmp / "roster" / "latest.json").write_text(json.dumps({"wallets": [
        {"wallet": S, "tier": "INFRASTRUCTURE"}]}))
    bp.main(["--dry-run", str(tmp / "dry")], post=_post(), substrate=_substrate,
            now="2026-10-06T00:00:00+00:00", is_hot=lambda a: a == HOT)
    doc = json.loads((tmp / "dry" / "latest.json").read_text())
    assert doc["members"][S]["role"] == "deposit"


def test_an_active_member_alerts_once(sandbox):
    tmp, sent = sandbox
    for _ in range(2):
        bp.main([], post=_post(active={SINK}), substrate=_substrate,
                now="2026-10-06T00:00:00+00:00", is_hot=lambda a: a == HOT, close_every_s=0)
    assert sent == [SINK]


def test_a_failed_read_is_unreadable_not_inactive(sandbox):
    tmp, sent = sandbox

    def down(body):
        raise RuntimeError("info endpoint unavailable")
    bp.main([], post=down, substrate=_substrate, now="2026-10-06T00:00:00+00:00",
            is_hot=lambda a: False)
    doc = json.loads((tmp / "perimeter" / "latest.json").read_text())
    assert doc["members"][SINK]["hl"]["read_ok"] is False
    assert SINK in doc["closed"]["unreadable"] and not sent


def test_dry_run_writes_only_into_its_directory(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    out = tmp_path_factory.mktemp("dry")
    bp.main(["--dry-run", str(out)], post=_post(active={SINK}), substrate=_substrate,
            now="2026-10-06T00:00:00+00:00", is_hot=lambda a: False)
    assert (out / "latest.json").exists() and not (tmp / "perimeter").exists() and not sent


def test_spot_usdc_counts_toward_a_members_value():
    # CLAUDE.md: ask an account's whole surface - a spot-only account is still
    # an account. The spot state was read and then thrown away.
    def post(body):
        if body["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "0.0"}}
        if body["type"] == "spotClearinghouseState":
            return {"balances": [{"coin": "USDC", "total": "50000.0"},
                                 {"coin": "SPAM", "total": "9999999.0"}]}
        return []
    reading = bp.hl_reading(SINK, post)
    assert reading["account_value"] == 50_000.0 and reading["active"] is True


def test_a_hypercore_deposit_address_is_recorded_but_never_alerted_as_active(sandbox):
    # 0x4aecac3b is an exchange's account ON Hyperliquid: being active there is
    # what it is, not news.
    tmp, sent = sandbox
    hd = "0x4aecac3b90dd0ad50d274c19221874e5ba8a4d45"
    (tmp / "trace" / "latest.json").write_text(json.dumps({"funders": [], "deposit_addresses": [
        {"address": hd, "kind": "hl_deposit", "hub": "0x" + "1" * 40}]}))
    bp.main([], post=_post(active={hd}), substrate=_substrate, now="2026-10-06T00:00:00+00:00",
            is_hot=lambda a: a == HOT)
    doc = json.loads((tmp / "perimeter" / "latest.json").read_text())
    assert doc["members"][hd]["hl"]["active"] is True and hd not in sent


def test_a_step_killed_during_the_substrate_pass_still_leaves_a_perimeter(sandbox):
    # The daily substrate pass is the slow part (211-378s locally, step limit
    # 300s): a kill there must not cost the whole perimeter, run after run.
    tmp, sent = sandbox

    def killed(addresses):
        raise KeyboardInterrupt("step timeout")
    with pytest.raises(KeyboardInterrupt):
        bp.main([], post=_post(), substrate=killed, now="2026-10-06T00:00:00+00:00",
                is_hot=lambda a: a == HOT)
    doc = json.loads((tmp / "perimeter" / "latest.json").read_text())
    assert {a for a, m in doc["members"].items() if m["role"] in ("core", "deposit")} == {T, TR, F, S}
