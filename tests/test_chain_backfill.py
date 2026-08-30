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


def _stub_common(monkeypatch, tmp_path, config):
    from src.chain import collect

    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    monkeypatch.setattr(bf, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(bf, "load_config", lambda: config)


def test_main_reset_flag_clears_cursors(tmp_path, monkeypatch):
    from src.chain import collect

    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})
    (tmp_path / "cursors.json").write_text(json.dumps({"arbitrum:0xtarget:erc20": 12345}))
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget", "chains": {}, "degraded_sources": []})

    assert bf.main(["--reset"]) == 0
    assert collect.read_cursors() == {}


def test_main_without_reset_flag_keeps_cursors(tmp_path, monkeypatch):
    """Regression guard: resume is the default, so a truncated run makes forward
    progress instead of looping forever back to block 0."""
    from src.chain import collect

    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})
    (tmp_path / "cursors.json").write_text(json.dumps({"arbitrum:0xtarget:erc20": 12345}))
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget", "chains": {}, "degraded_sources": []})

    assert bf.main([]) == 0
    assert collect.read_cursors() == {"arbitrum:0xtarget:erc20": 12345}


def test_main_prefers_backfill_budget_over_collection(tmp_path, monkeypatch):
    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10},
        "backfill": {"max_calls_per_run": 20000, "time_budget_seconds": 3300}})
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget", "chains": {}, "degraded_sources": []})

    captured = {}

    class FakeBudget:
        def __init__(self, max_calls, seconds, **kwargs):
            captured["max_calls"] = max_calls
            captured["seconds"] = seconds

    monkeypatch.setattr(bf, "CallBudget", FakeBudget)

    assert bf.main([]) == 0
    assert captured == {"max_calls": 20000, "seconds": 3300}


def test_main_falls_back_to_collection_budget_when_backfill_absent(tmp_path, monkeypatch):
    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget", "chains": {}, "degraded_sources": []})

    captured = {}

    class FakeBudget:
        def __init__(self, max_calls, seconds, **kwargs):
            captured["max_calls"] = max_calls
            captured["seconds"] = seconds

    monkeypatch.setattr(bf, "CallBudget", FakeBudget)

    assert bf.main([]) == 0
    assert captured == {"max_calls": 10, "seconds": 10}


def test_main_reports_truncation_naming_the_chain(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget",
        "chains": {"arbitrum": {"records": 1, "spam": 0, "calls": 1, "cursor": 5,
                                "gaps": [], "truncated": True, "error": None,
                                "probed_inactive": False}},
        "degraded_sources": []})

    assert bf.main([]) == 0
    out = capsys.readouterr().out
    assert "TRUNCATED" in out
    assert "arbitrum" in out


def test_main_reports_truncation_for_a_gap_even_without_the_flag(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget",
        "chains": {"base": {"records": 1, "spam": 0, "calls": 1, "cursor": 5,
                            "gaps": [[100, 200]], "truncated": False, "error": None,
                            "probed_inactive": False}},
        "degraded_sources": []})

    assert bf.main([]) == 0
    out = capsys.readouterr().out
    assert "TRUNCATED" in out
    assert "base" in out


def test_main_reports_no_truncation_when_clean(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": [],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})
    monkeypatch.setattr(bf, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget",
        "chains": {"arbitrum": {"records": 1, "spam": 0, "calls": 1, "cursor": 5,
                                "gaps": [], "truncated": False, "error": None,
                                "probed_inactive": False}},
        "degraded_sources": []})

    assert bf.main([]) == 0
    out = capsys.readouterr().out
    assert "TRUNCATED" not in out


def test_main_wallet_flag_is_repeatable_and_bypasses_the_cluster(tmp_path, monkeypatch):
    """--wallet was untested at the argparse level: this proves repeated flags
    both parse into the list (not just a single manually-set args.wallet) and
    that supplying it skips cluster_wallets entirely."""
    _stub_common(monkeypatch, tmp_path, {
        "target_wallet": "0xtarget", "known_self_wallets": ["0xself"],
        "collection": {"max_calls_per_run": 10, "time_budget_seconds": 10}})

    swept = []

    def fake_sweep(address, *a, **k):
        swept.append(address)
        return {"address": address, "chains": {}, "degraded_sources": []}

    monkeypatch.setattr(bf, "sweep_wallet", fake_sweep)

    assert bf.main(["--wallet", "0xDEADBEEF", "--wallet", "0xFEEDFACE"]) == 0
    assert swept == ["0xdeadbeef", "0xfeedface"]
