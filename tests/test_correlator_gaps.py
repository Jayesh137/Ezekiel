"""The correlator on custody-gap exits, the complete bridge pool and route physics."""

import json
import time
from datetime import UTC, datetime

from src import correlator
from src.chain import collect

T = "0x45d26f28196d226497130c4bac709d808fed4029"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
DEFI, NEW = "0x" + "1" * 40, "0x" + "5" * 40


def _sandbox(tmp_path, monkeypatch, records=(), ledger=()):
    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(correlator, "load_config", lambda: {
        "target_wallet": T, "known_self_wallets": [], "excluded_addresses": [],
        "known_service_addresses": [], "hyperliquid_api": "http://unused",
        "correlation": {"min_amount_usd": 100_000, "window_days": 14, "tolerance_pct": 0.03,
                        "min_confidence": 0.55, "alert_confidence": 0.7}})
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps(list(records)))
    (tmp_path / "ledger").mkdir()
    (tmp_path / "ledger" / "2026-08-28.json").write_text(json.dumps(list(ledger)))
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "code_cache.json").write_text(json.dumps({f"arbitrum:{DEFI}": True}))
    (tmp_path / "perimeter").mkdir()
    (tmp_path / "perimeter" / "latest.json").write_text(json.dumps({"members": {
        T: {"address": T, "role": "core", "weight": 1.0},
        S: {"address": S, "role": "deposit", "weight": 1.0}}}))


def _rec(rid, dst, usd):
    return {"id": rid, "chain": "arbitrum", "src": T, "dst": dst, "tx_hash": f"0x{rid}",
            "ts": 1000, "amount_usd": usd, "value_basis": "stable_par", "spam": False}


def test_exits_are_custody_gap_exits_only(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, records=[_rec("a", DEFI, 5e6), _rec("b", S, 2e6),
                                             _rec("c", NEW, 3e5)])
    exits = correlator.collect_target_exits(T, min_amount=100_000)
    assert {e["destination"]: e["gap"] for e in exits} == {S: "exchange_deposit",
                                                           NEW: "person_transfer"}


def test_a_withdrawal_to_himself_is_resolved_by_nonce_and_not_an_exit(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, ledger=[
        {"delta": {"type": "withdraw", "usdc": "300000", "nonce": 9}, "time": 1_700_000_000_000,
         "hash": "0xw"}])
    (tmp_path / "boundary").mkdir()
    (tmp_path / "boundary" / "latest.json").write_text(json.dumps(
        {"core_withdrawals": {"9": {"destination": T}}}))
    assert correlator.collect_target_exits(T, min_amount=100_000) == []


def _pool(tmp_path, age_h, deposits):
    (tmp_path / "provenance").mkdir(exist_ok=True)
    updated = datetime.fromtimestamp(time.time() - age_h * 3600, UTC).isoformat()
    (tmp_path / "provenance" / "bridge_deposits.json").write_text(json.dumps(
        {"cursor": 1, "updated_at": updated, "deposits": deposits}))


def test_the_bridge_pool_comes_from_the_complete_feed_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    now = time.time()
    _pool(tmp_path, 1, [{"wallet": NEW, "amount": 5e5, "ts": now - 86400, "hash": "0x1"},
                        {"wallet": "0xold", "amount": 5e5, "ts": now - 40 * 86400, "hash": "0x2"},
                        {"wallet": "0xsmall", "amount": 5e4, "ts": now - 86400, "hash": "0x3"}])
    monkeypatch.setattr(correlator, "_etherscan_bridge_deposits",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Etherscan")))
    entries, error = correlator.get_recent_bridge_deposits(14, 100_000)
    assert error is None and [e["wallet"] for e in entries] == [NEW]


def test_a_stale_or_missing_pool_falls_back_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    assert correlator.bridge_pool_from_file(14, 100_000)[1] == "bridge deposit pool not built yet"
    _pool(tmp_path, 30, [])
    assert "old" in correlator.bridge_pool_from_file(14, 100_000)[1]
    monkeypatch.setattr(correlator, "_etherscan_bridge_deposits",
                        lambda window_days, min_amount, budget=None: ([{"wallet": "0xes"}], None))
    assert correlator.get_recent_bridge_deposits(14, 100_000)[0] == [{"wallet": "0xes"}]


def test_a_match_whose_route_never_crossed_an_exchange_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(correlator, "load_config", lambda: {
        "target_wallet": T, "known_self_wallets": [],
        "correlation": {"min_amount_usd": 100_000, "window_days": 14, "tolerance_pct": 0.03,
                        "min_confidence": 0.0, "alert_confidence": 2.0}})
    monkeypatch.setattr(correlator, "collect_target_exits", lambda target, min_amount: [
        {"amount": 1_234_567.0, "ts": 1_000_000, "source": "l1_outbound",
         "gap": "exchange_deposit", "ref": "0xexit"}])
    entries = [{"wallet": NEW, "amount": 1_234_567.0, "ts": 1_000_000 + 3600},
               {"wallet": "0x" + "9" * 40, "amount": 1_234_000.0, "ts": 1_000_000 + 7200}]
    monkeypatch.setattr(correlator, "get_recent_bridge_deposits", lambda w, m: (entries, None))
    monkeypatch.setattr(correlator, "get_recent_cctp_deposits", lambda w, m: ([], None))
    (tmp_path / "provenance" / "accounts").mkdir(parents=True)
    (tmp_path / "provenance" / "accounts" / "55.json").write_text(json.dumps({NEW: {
        "complete": True, "unreadable": [],
        "sources": [{"hop": 1, "class": "quiet", "address": "0xq", "last_ts": 1_000_100}]}}))
    result = correlator.run_correlation(pools=("bridge",))
    routes = {m["wallet"]: m["route"] for m in result["matches"]}
    assert NEW not in routes and routes["0x" + "9" * 40] == "route_unknown"
