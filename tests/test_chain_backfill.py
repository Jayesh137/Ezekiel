# tests/test_chain_backfill.py
import json

from scripts import backfill_transfers as bf


def test_cluster_wallets_is_the_target_plus_known_self_wallets():
    cfg = {"target_wallet": "0xTARGET",
           "known_self_wallets": ["0xSELF", "0xtarget"]}
    assert bf.cluster_wallets(cfg) == ["0xtarget", "0xself"]


def test_backfill_resets_cursors_so_history_is_re_read(tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    (tmp_path / "cursors.json").write_text(json.dumps({"arbitrum:0xtarget:erc20": 477345405}))

    bf.reset_cursors(["0xtarget"])
    assert collect.read_cursors() == {}


def test_backfill_only_resets_the_wallets_it_was_asked_for(tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    (tmp_path / "cursors.json").write_text(json.dumps({
        "arbitrum:0xtarget:erc20": 100, "arbitrum:0xother:erc20": 200}))

    bf.reset_cursors(["0xtarget"])
    assert collect.read_cursors() == {"arbitrum:0xother:erc20": 200}


def test_main_writes_sweep_health(tmp_path, monkeypatch):
    from src.chain import collect

    # main() resets cursors before sweeping. Without this the test writes to the
    # real data/state/transfer_cursors.json — conftest's backstop only watches
    # the alert-health file, so this one would slip through into a commit.
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget",
        "chains": {"arbitrum": {"records": 7, "spam": 905, "calls": 3, "cursor": 9,
                                "gaps": [], "truncated": False, "error": None,
                                "probed_inactive": False}},
        "degraded_sources": []})
    monkeypatch.setattr(bf, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(bf, "load_config", lambda: {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})

    assert bf.main([]) == 0
    health = json.loads((tmp_path / "transfers" / "latest.json").read_text())
    assert health["records"] == 7
    assert health["spam_suppressed"] == 905
    assert health["degraded_sources"] == []
