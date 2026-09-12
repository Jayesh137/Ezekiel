# tests/test_correlator_pools.py
"""The correlator with two candidate pools and two exit routes.

The bridge pool costs hundreds of Etherscan calls and runs daily; the Circle
pool is incremental and keyless and runs every trace. Neither run may erase
the other's matches, and a Circle withdrawal is an exit only when it did not
land at one of his own addresses."""

import json

from src import correlator

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
ZERO = "0x0000000000000000000000000000000000000000"
SYS = "0x2000000000000000000000000000000000000000"


def _send(h, t_ms, amount):
    return {"time": t_ms, "hash": h, "delta": {"type": "send", "user": T, "destination": SYS,
                                                "token": "USDC", "amount": str(amount),
                                                "usdcValue": str(amount), "fee": "0.0"}}


def _sandbox(tmp_path, monkeypatch, *, ledger, mints):
    from src.chain import collect

    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    (tmp_path / "ledger").mkdir(parents=True)
    (tmp_path / "ledger" / "2026-09-11.json").write_text(json.dumps(ledger))
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-09-11.json").write_text(json.dumps([
        {"id": f"m{i}", "chain": "arbitrum", "src": ZERO, "dst": dst, "kind": "erc20",
         "asset": "USDC", "amount": usd, "amount_usd": usd, "tx_hash": f"0xm{i}", "ts": ts,
         "value_basis": "stable_par", "spam": False}
        for i, (ts, usd, dst) in enumerate(mints)]))


def test_only_unpaired_circle_withdrawals_are_exits(tmp_path, monkeypatch):
    old = 1_700_000_000
    ledger = [_send("0xpaired", old * 1000, 7_000_000),          # minted at his address
              _send("0xgone", (old + 100) * 1000, 2_500_000),    # minted nowhere we read
              _send("0xsmall", (old + 200) * 1000, 50_000)]      # under the floor
    _sandbox(tmp_path, monkeypatch, ledger=ledger, mints=[(old + 9, 6_999_999.8, T)])

    exits = correlator.collect_target_exits(T, min_amount=100_000)

    assert [(e["source"], e["ref"], e["amount"]) for e in exits] == [
        ("hl_cctp", "0xgone", 2_500_000.0)]
    assert exits[0]["ts"] == old + 100


def test_a_circle_withdrawal_minted_at_a_known_wallet_of_his_is_not_an_exit(tmp_path, monkeypatch):
    old = 1_700_000_000
    _sandbox(tmp_path, monkeypatch, ledger=[_send("0xtr", old * 1000, 1_000_000)],
             mints=[(old + 30, 999_999.8, TR)])
    monkeypatch.setattr(correlator, "load_config", lambda: {
        "target_wallet": T, "known_self_wallets": [TR], "correlation": {}})
    assert correlator.collect_target_exits(T, min_amount=100_000) == []


def _wire_pools(monkeypatch, tmp_path, *, bridge, cctp, stored=None):
    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(correlator, "load_config", lambda: {
        "target_wallet": T, "known_self_wallets": [], "excluded_addresses": [],
        "hyperliquid_api": "http://unused",
        "correlation": {"min_amount_usd": 100_000, "window_days": 14, "tolerance_pct": 0.03,
                        "min_confidence": 0.55, "alert_confidence": 0.7}})
    monkeypatch.setattr(correlator, "collect_target_exits", lambda target, min_amount: [
        {"amount": 1_234_567.0, "ts": 1_000_000, "source": "hl_withdraw", "ref": "0xexit"}])
    monkeypatch.setattr(correlator, "get_recent_bridge_deposits",
                        lambda window_days, min_amount: bridge)
    monkeypatch.setattr(correlator, "get_recent_cctp_deposits",
                        lambda window_days, min_amount: cctp)
    alerts = []
    import src.alerts as al
    monkeypatch.setattr(al, "alert_deposit_correlation", lambda *a, **k: alerts.append((a, k)) or True)
    if stored is not None:
        (tmp_path / "correlations").mkdir(parents=True, exist_ok=True)
        (tmp_path / "correlations" / "latest.json").write_text(json.dumps(stored))
    return alerts


def test_a_circle_only_run_keeps_the_stored_bridge_matches(tmp_path, monkeypatch):
    stored = {"pools": {"bridge": {"computed_at": "yesterday", "candidate_pool_error": None,
                                   "candidates_considered": 801,
                                   "matches": [{"wallet": "0xbridgehit", "confidence": 0.61,
                                                "via": "bridge"}]}}}
    alerts = _wire_pools(monkeypatch, tmp_path, bridge=([], None),
                         cctp=([{"wallet": "0xfresh", "amount": 1_234_567.0, "ts": 1_000_000 + 3600,
                                 "via": "cctp"}], None), stored=stored)

    result = correlator.run_correlation(pools=("cctp",))

    assert result["pools_read"] == ["cctp"]
    assert result["pools"]["bridge"] == stored["pools"]["bridge"]           # untouched
    assert [m["wallet"] for m in result["pools"]["cctp"]["matches"]] == ["0xfresh"]
    assert result["pools"]["cctp"]["matches"][0]["via"] == "cctp"
    assert [m["wallet"] for m in result["matches"]] == ["0xfresh", "0xbridgehit"]
    assert result["match_count"] == 2 and result["candidates_considered"] == 802
    ((args, kwargs),) = alerts
    assert args[0] == "0xfresh" and kwargs == {"via": "cctp"}
    saved = json.loads((tmp_path / "correlations" / "latest.json").read_text())
    assert saved["pools"]["bridge"]["matches"][0]["wallet"] == "0xbridgehit"


def test_a_full_run_reads_both_pools_and_reports_each_pools_error(tmp_path, monkeypatch):
    alerts = _wire_pools(
        monkeypatch, tmp_path,
        bridge=([{"wallet": "0xviabridge", "amount": 1_234_567.0, "ts": 1_000_000 + 7200}],
                "read 5 rows and hit the page ceiling"),
        cctp=([], "call budget (1) exhausted"))

    result = correlator.run_correlation()

    assert result["pools_read"] == ["bridge", "cctp"]
    assert [m["via"] for m in result["matches"]] == ["bridge"]
    assert "bridge: read 5 rows" in result["candidate_pool_error"]
    assert "cctp: call budget" in result["candidate_pool_error"]
    assert alerts and alerts[0][1] == {"via": "bridge"}


def test_a_whole_pool_reports_no_error(tmp_path, monkeypatch):
    _wire_pools(monkeypatch, tmp_path, bridge=([], None), cctp=([], None))
    assert correlator.run_correlation()["candidate_pool_error"] is None


def test_the_circle_pool_is_filtered_to_the_window_and_floor(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(correlator.time, "time", lambda: now)
    seen = {}

    def fake_refresh(post, *, excluded, max_calls, seconds):
        seen.update(excluded=excluded, max_calls=max_calls, seconds=seconds)
        return ([{"wallet": "0xa", "amount": 150_000.0, "ts": now - 86400, "hash": "0x1"},
                 {"wallet": "0xb", "amount": 150_000.0, "ts": now - 20 * 86400, "hash": "0x2"},
                 {"wallet": "0xc", "amount": 60_000.0, "ts": now - 86400, "hash": "0x3"}],
                "partial")

    monkeypatch.setattr(correlator, "refresh_pool", fake_refresh)
    monkeypatch.setattr(correlator, "load_config", lambda: {
        "target_wallet": T, "known_self_wallets": [TR], "excluded_addresses": [ZERO],
        "correlation": {"cctp_max_calls": 7, "cctp_time_budget_seconds": 9}})

    deposits, err = correlator.get_recent_cctp_deposits(14, 100_000)

    assert [d["wallet"] for d in deposits] == ["0xa"] and err == "partial"
    assert seen == {"excluded": {T, TR, ZERO}, "max_calls": 7, "seconds": 9.0}


def test_main_passes_the_pools_flag_through(monkeypatch):
    got = []
    monkeypatch.setattr(correlator, "run_correlation", lambda pools: got.append(pools))
    correlator.main(["--pools", "cctp"])
    correlator.main([])
    assert got == [("cctp",), ("bridge", "cctp")]


def test_the_scanner_priority_set_includes_circle_depositors(monkeypatch):
    from src import scanner as sc

    monkeypatch.setattr(correlator, "get_recent_bridge_deposits",
                        lambda window_days, min_amount: ([{"wallet": "0xbridge", "amount": 70_000.0}], None))
    monkeypatch.setattr(correlator, "get_recent_cctp_deposits",
                        lambda window_days, min_amount: ([{"wallet": "0xcircle", "amount": 90_000.0},
                                                          {"wallet": "0xbridge", "amount": 60_000.0}], None))
    assert sc.get_recent_bridge_depositors(max_wallets=5) == ["0xcircle", "0xbridge"]


def test_the_scanner_still_scans_circle_depositors_without_an_etherscan_key(monkeypatch):
    from src import scanner as sc

    monkeypatch.setattr(correlator, "get_recent_bridge_deposits",
                        lambda window_days, min_amount: ([], "skipped_no_api_key"))
    monkeypatch.setattr(correlator, "get_recent_cctp_deposits",
                        lambda window_days, min_amount: ([{"wallet": "0xcircle", "amount": 90_000.0}], None))
    assert sc.get_recent_bridge_depositors(max_wallets=5) == ["0xcircle"]


def test_a_failing_circle_reader_never_loses_the_bridge_set(monkeypatch):
    from src import scanner as sc

    def boom(window_days, min_amount):
        raise RuntimeError("down")
    monkeypatch.setattr(correlator, "get_recent_bridge_deposits",
                        lambda window_days, min_amount: ([{"wallet": "0xbridge", "amount": 70_000.0}], None))
    monkeypatch.setattr(correlator, "get_recent_cctp_deposits", boom)
    assert sc.get_recent_bridge_depositors(max_wallets=5) == ["0xbridge"]


def test_a_circle_only_run_keeps_matches_from_a_PRE_POOLS_file(tmp_path, monkeypatch):
    """The bridge matches written before pools existed must survive the split.

    Measured live 2026-09-12: every run up to 02:14 carried 7-10 matches under a
    flat `matches` list and no `pools` key at all, because that shape predates
    per-pool storage. The first `--pools cctp` run after the split found no
    `pools` dict to preserve, wrote only its own (incomplete) Circle block, and
    the correlator went to 0 matches — not because the bridge pool said no, but
    because nobody asked it and its last answer had been overwritten. Recovery
    needs analyze.yml, a daily cron that is not locally dispatched.

    A flat `matches` list IS the bridge pool's stored answer; read it as one.
    """
    stored = {"computed_at": "yesterday", "match_count": 1, "candidates_considered": 801,
              "candidate_pool_error": None,
              "matches": [{"wallet": "0xbridgehit", "confidence": 0.61}]}
    _wire_pools(monkeypatch, tmp_path, bridge=([], None), cctp=([], None), stored=stored)

    result = correlator.run_correlation(pools=("cctp",))

    assert [m["wallet"] for m in result["matches"]] == ["0xbridgehit"]
    assert result["pools"]["bridge"]["matches"][0]["wallet"] == "0xbridgehit"
    assert result["pools"]["bridge"]["matches"][0]["via"] == "bridge"
    assert result["candidates_considered"] == 801


def test_migrating_a_legacy_file_happens_once_and_never_doubles(tmp_path, monkeypatch):
    """Once the bridge block exists, the flat list must not be folded in again.

    The same trap the alert shards carry: `latest.json` is rewritten with both
    shapes present, so a migration keyed on the legacy field rather than on the
    absence of the new one re-reads its own output every run.
    """
    stored = {"matches": [{"wallet": "0xbridgehit", "confidence": 0.61, "via": "bridge"}],
              "pools": {"bridge": {"computed_at": "today", "candidate_pool_error": None,
                                   "candidates_considered": 0, "matches": []}}}
    _wire_pools(monkeypatch, tmp_path, bridge=([], None), cctp=([], None), stored=stored)

    result = correlator.run_correlation(pools=("cctp",))

    assert result["matches"] == []
    assert result["pools"]["bridge"]["matches"] == []
