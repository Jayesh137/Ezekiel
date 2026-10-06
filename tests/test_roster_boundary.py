"""Boundary and provenance findings reach the roster as votes and evidence."""

import json

from src import roster

T = "0x45d26f28196d226497130c4bac709d808fed4029"
OUT, NEW, ASSOCY = "0x" + "1" * 40, "0x" + "7" * 40, "0x" + "3" * 40


def test_votes_and_evidence_come_from_each_findings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "boundary").mkdir()
    (tmp_path / "boundary" / "latest.json").write_text(json.dumps({"findings": [
        {"kind": "outside_account_paid_his_world", "hl_account": OUT, "severity": "CRITICAL",
         "vote": "linkage", "role": "deposit", "member": "0xdep", "source": "bridge2",
         "chain": "arbitrum", "amount_usd": 2e5, "ts": 1, "ref": "0xr"},
        {"kind": "outside_account_paid_his_world", "hl_account": ASSOCY, "severity": None,
         "vote": None, "role": "associate", "member": "0xassoc", "source": "bridge2",
         "chain": "arbitrum", "amount_usd": 2e5, "ts": 2, "ref": "0xs"}]}))
    (tmp_path / "provenance").mkdir()
    (tmp_path / "provenance" / "latest.json").write_text(json.dumps({"findings": [
        {"kind": "provenance_touches_his_world", "account": NEW, "severity": "CRITICAL",
         "vote": "transfer", "hop": 1, "role": "core", "member": T, "route": "bridge2",
         "usd": 2e6, "ts": 3, "entry_ref": "0xe"}], "member_findings": []}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    rows = {w["wallet"]: w for w in doc["wallets"]}
    assert "linkage" in rows[OUT]["vectors"] and rows[OUT]["evidence"]["boundary"]
    assert "transfer" in rows[NEW]["vectors"]
    assert rows[ASSOCY]["vectors"] == [] and rows[ASSOCY]["evidence"]["boundary"]
    assert T not in rows


def test_a_boundary_vote_needs_the_transfer_evidence_bar(tmp_path, monkeypatch):
    # CLAUDE.md: a transfer vote needs an observed transfer of >= $1K
    # (SELF_FLOW_MIN_USD). An unpriced send or a few hundred dollars is kept as
    # evidence and casts nothing — the first dry run handed `transfer` to two
    # airdroppers of spam tokens.
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "boundary").mkdir()
    row = {"kind": "his_world_funded_outside_account", "severity": "HIGH", "vote": "transfer",
           "role": "core", "member": T, "source": "hl_send", "chain": "hyperliquid", "ts": 1}
    (tmp_path / "boundary" / "latest.json").write_text(json.dumps({"findings": [
        {**row, "hl_account": OUT, "amount_usd": None, "ref": "0x1"},
        {**row, "hl_account": NEW, "amount_usd": 400.0, "ref": "0x2"},
        {**row, "hl_account": ASSOCY, "amount_usd": 1_500.0, "ref": "0x3"}]}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    rows = {w["wallet"]: w for w in doc["wallets"]}
    assert rows[OUT]["vectors"] == [] and rows[OUT]["evidence"]["boundary"]
    assert rows[NEW]["vectors"] == []
    assert "transfer" in rows[ASSOCY]["vectors"]
