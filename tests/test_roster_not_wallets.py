"""The roster grades an address with no key as INFRASTRUCTURE, which frees detector slots."""

import json

from src import roster

T = "0x45d26f28196d226497130c4bac709d808fed4029"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
EOA = "0x" + "ab" * 20


def test_a_token_contract_with_route_observations_is_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    for wallet in (USDC_BASE, EOA):
        (candidates / f"{wallet}.json").write_text(json.dumps({
            "wallet": wallet, "observations": [
                {"source": "funding_route", "positive": True, "status": "ok",
                 "parent_event_ids": [f"base:0x{i:064x}:erc20:1"], "event_id": f"e{i}"}
                for i in range(3)]}))
    doc = roster.build_roster({"target_wallet": T, "known_self_wallets": []})
    rows = {r["wallet"]: r for r in doc["wallets"]}
    assert rows[USDC_BASE]["tier"] == "INFRASTRUCTURE"
    assert rows[USDC_BASE]["evidence"]["service_reason"].startswith("not a wallet: token contract")
    assert rows[EOA]["tier"] != "INFRASTRUCTURE"
    picked = roster.detector_candidates({"target_wallet": T}, doc, 40, casebook={})
    assert USDC_BASE not in picked and EOA in picked


def test_a_lookalike_of_his_declared_wallet_is_infrastructure(tmp_path, monkeypatch):
    # 0xf078170f...f19e reached POSSIBLE on 2026-10-08 on spoofed transfers "from" the target.
    self_wallet = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
    forgery = "0xf078170f3993bcbd76c3234724314ae8ba59f19e"
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    for wallet in (forgery, EOA):
        (candidates / f"{wallet}.json").write_text(json.dumps({
            "wallet": wallet, "observations": [
                {"source": "funding_route", "positive": True, "status": "ok",
                 "parent_event_ids": [f"base:0x{i:064x}:erc20:1"], "event_id": f"e{i}"}
                for i in range(3)]}))
    config = {"target_wallet": T, "known_self_wallets": [self_wallet]}
    doc = roster.build_roster(config)
    rows = {r["wallet"]: r for r in doc["wallets"]}
    assert rows[forgery]["tier"] == "INFRASTRUCTURE"
    assert rows[forgery]["evidence"]["service_reason"].startswith("forgery of his wallet 0xf078969e")
    assert rows[EOA]["tier"] != "INFRASTRUCTURE"
    assert forgery not in roster.detector_candidates(config, doc, 40, casebook={})
