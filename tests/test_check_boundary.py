"""The watch.yml attribution step, every reader injected (no network)."""

import json

import pytest

import scripts.check_boundary as cb
from src import utils
from src.boundary import bridge2

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SOL = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"
OUT, NEW = "0x" + "1" * 40, "0x" + "7" * 40
HUB = "0x" + "3" * 40


def fw(user, dest, usd, block, tx, nonce=1, index=0):
    """A FinalizedWithdrawal log in the shape getLogs returns."""
    data = ("0x" + "0" * 24 + dest[2:] + format(int(usd * 1e6), "064x")
            + format(nonce, "064x") + "ab" * 32)
    return {"address": bridge2.BRIDGE, "topics": [bridge2.TOPIC_FINALIZED_WITHDRAWAL,
                                                   bridge2.topic_address(user)],
            "data": data, "blockNumber": hex(block), "timeStamp": hex(1_790_000_000 + block),
            "logIndex": hex(index), "transactionHash": tx}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "load_config", lambda: {"target_wallet": T, "known_self_wallets": [TR]})
    (tmp_path / "perimeter").mkdir()
    (tmp_path / "perimeter" / "latest.json").write_text(json.dumps({"members": {
        T: {"address": T, "role": "core", "weight": 1.0, "why": "config"},
        TR: {"address": TR, "role": "core", "weight": 1.0, "why": "config"},
        S: {"address": S, "role": "deposit", "weight": 1.0, "why": "his Binance deposit address"},
        SOL: {"address": SOL, "role": "identity", "weight": 1.0, "why": "Solana", "raw": None}}}))
    sent = {"boundary": [], "foreign": []}
    monkeypatch.setattr("src.alerts.alert_boundary_finding",
                        lambda row: sent["boundary"].append(row) or True)
    monkeypatch.setattr("src.alerts.alert_foreign_destination",
                        lambda *a, **k: sent["foreign"].append(a) or True)
    return tmp_path, sent


def readers(live=(), user=None, payouts=None, tx_logs=None, unit=None, head=1_000, fail_at=None):
    def withdrawals(lo, hi):
        if fail_at is not None and hi >= fail_at:
            from src.boundary.logs import LogReadError
            raise LogReadError("blockscout: 429")
        return [log for log in live if lo <= int(log["blockNumber"], 16) <= hi]
    return {"head": lambda: head, "withdrawals": withdrawals,
            "user_withdrawals": lambda u: (user or {}).get(u, []),
            "payouts": lambda m: (payouts or {}).get(m, []),
            "tx_logs": lambda tx: (tx_logs or {}).get(tx, []),
            "unit": lambda a: (unit or {}).get(a, {"addresses": [], "operations": []}),
            "mints": lambda m: [], "system_sends": lambda start_ms: []}


def _state(tmp):
    return json.loads((tmp / "boundary" / "latest.json").read_text())


def test_first_run_is_history_then_a_live_payment_to_his_deposit_address_pages(sandbox):
    tmp, sent = sandbox
    cb.main([], readers=readers(live=[fw(OUT, S, 250_000, 900, "0xold")]))
    st = _state(tmp)
    assert st["withdrawal_cursor"] == 1_000
    first = [f for f in st["findings"] if f["ref"] == "0xold"][0]
    assert first["retro"] and first["severity"] == "HIGH" and first["vote"] == "linkage"
    cb.main([], readers=readers(live=[fw(NEW, S, 300_000, 1_500, "0xnew")], head=2_000))
    st = _state(tmp)
    live = [f for f in st["findings"] if f["ref"] == "0xnew"][0]
    assert live["severity"] == "CRITICAL" and not live["retro"] and live["key"] in st["alerted"]
    assert [r["ref"] for r in sent["boundary"]] == ["0xold", "0xnew"]


def test_his_account_withdrawing_to_a_new_address_reuses_the_foreign_destination_alert(sandbox):
    tmp, sent = sandbox
    cb.main([], readers=readers(user={TR: [fw(TR, NEW, 1e6, 50, "0xtr", nonce=7)]}))
    st = _state(tmp)
    assert st["core_withdrawals"]["7"]["destination"] == NEW
    assert sent["foreign"] and sent["foreign"][0][1] == "withdraw3" and sent["foreign"][0][2] == NEW


def test_a_failed_read_keeps_the_cursor_and_the_other_sources_still_run(sandbox):
    tmp, sent = sandbox
    cb.main([], readers=readers(head=500))
    cb.main([], readers=readers(head=300_000, fail_at=200_000,
                                live=[fw(OUT, T, 9e5, 90_000, "0xa")],
                                user={TR: [fw(TR, NEW, 1e6, 60, "0xtr", nonce=8)]}))
    st = _state(tmp)
    assert st["withdrawal_cursor"] == 100_500 and "429" in st["read_error"]
    refs = {f["ref"] for f in st["findings"]}
    assert "0xa" in refs and "0xtr" in refs


def test_without_a_perimeter_file_the_config_wallets_are_still_watched(sandbox):
    tmp, sent = sandbox
    (tmp / "perimeter" / "latest.json").unlink()
    cb.main([], readers=readers(head=500))
    cb.main([], readers=readers(head=900, live=[fw(OUT, T, 5e5, 700, "0xb")]))
    st = _state(tmp)
    assert st["perimeter_fallback"] and any(f["severity"] == "CRITICAL" for f in st["findings"])


def test_an_undelivered_alert_is_retried_and_not_marked_seen(sandbox, monkeypatch):
    tmp, sent = sandbox
    cb.main([], readers=readers(head=500))
    monkeypatch.setattr("src.alerts.alert_boundary_finding", lambda row: False)
    cb.main([], readers=readers(head=900, live=[fw(OUT, S, 5e5, 700, "0xc")]))
    st = _state(tmp)
    assert len(st["undelivered"]) == 1 and not st["alerted"]
    monkeypatch.setattr("src.alerts.alert_boundary_finding", lambda row: True)
    cb.main([], readers=readers(head=950))
    st = _state(tmp)
    assert not st["undelivered"] and st["alerted"]


def test_unit_withdrawals_to_his_solana_wallet_page_high(sandbox):
    tmp, sent = sandbox
    op = {"opCreatedAt": "2026-10-05T10:00:00Z", "sourceChain": "hyperliquid",
          "destinationChain": "solana", "sourceAddress": OUT, "destinationAddress": SOL,
          "asset": "sol", "sourceAmount": "100", "state": "done", "sourceTxHash": "u1"}
    cb.main([], readers=readers(head=500, unit={SOL: {"addresses": [], "operations": [op]}}))
    f = [f for f in _state(tmp)["findings"] if f["source"] == "unit"][0]
    assert (f["severity"], f["role"], f["hl_account"]) == ("HIGH", "identity", OUT)


def test_retro_payouts_name_who_withdrew_to_his_address(sandbox):
    tmp, sent = sandbox
    payout = {"transactionHash": "0xpay", "blockNumber": hex(10), "logIndex": "0x0"}
    logs = [fw(OUT, S, 2e5, 10, "0xpay", index=1), fw(NEW, NEW, 9e5, 10, "0xpay", index=2)]
    cb.main([], readers=readers(head=500, payouts={S: [payout]}, tx_logs={"0xpay": logs}))
    st = _state(tmp)
    f = [f for f in st["findings"] if f["ref"] == "0xpay"]
    assert len(f) == 1 and f[0]["hl_account"] == OUT and f[0]["retro"]
    assert st["retro"][S]["bridge2"]


def test_core_ledger_sends_from_outside_accounts_count_but_hubs_do_not(sandbox):
    tmp, sent = sandbox
    (tmp / "trace" / "hl_edges").mkdir(parents=True)
    (tmp / "trace" / "registry").mkdir(parents=True)
    (tmp / "trace" / "registry" / "33.json").write_text(json.dumps({HUB: {"class": "hub"}}))
    (tmp / "trace" / "hl_edges" / "45.json").write_text(json.dumps({T: [
        {"id": "e1", "src": OUT, "dst": T, "amount_usd": 5e4, "ts": 10, "tx_hash": "0xh1"},
        {"id": "e2", "src": HUB, "dst": T, "amount_usd": 6e3, "ts": 11, "tx_hash": "0xh2"}]}))
    cb.main([], readers=readers(head=500))
    refs = {f["ref"] for f in _state(tmp)["findings"]}
    assert "0xh1" in refs and "0xh2" not in refs


def test_dry_run_writes_only_its_directory_and_sends_nothing(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    out = tmp_path_factory.mktemp("dry")
    cb.main(["--dry-run", str(out)], readers=readers(head=500, live=[fw(OUT, S, 5e5, 400, "0xd")]))
    assert (out / "latest.json").exists() and not (tmp / "boundary").exists()
    assert not sent["boundary"] and not sent["foreign"]


def test_retro_circle_mints_name_an_outside_withdrawer(sandbox):
    tmp, sent = sandbox
    from pathlib import Path

    from src.boundary import readers as rd
    fx = json.loads((Path(__file__).parent / "fixtures" / "boundary" / "bs_txlogs_cctp_mint.json").read_text())
    logs = rd.tx_logs("arbitrum", fx["_tx"], budget=rd.Budget(2), get=lambda url, params: fx)
    mint = {"transactionHash": fx["_tx"], "timeStamp": hex(1_790_000_000), "logIndex": "0x0"}
    r = readers(head=500, tx_logs={fx["_tx"]: logs})
    r["mints"] = lambda m: [mint] if m == T else []
    r["system_sends"] = lambda start: [{"user": OUT, "amount": 6_000_000.0, "ts_ms": 1, "hash": "x"}]
    cb.main([], readers=r)
    st = _state(tmp)
    f = [f for f in st["findings"] if f["source"] == "circle"]
    assert f and f[0]["hl_account"] == OUT and f[0]["counterparty"] == T and f[0]["retro"]
    assert st["retro"][T]["circle"]


def test_his_own_retro_circle_withdrawal_is_not_a_finding(sandbox):
    tmp, sent = sandbox
    from pathlib import Path

    from src.boundary import readers as rd
    fx = json.loads((Path(__file__).parent / "fixtures" / "boundary" / "bs_txlogs_cctp_mint.json").read_text())
    logs = rd.tx_logs("arbitrum", fx["_tx"], budget=rd.Budget(2), get=lambda url, params: fx)
    mint = {"transactionHash": fx["_tx"], "timeStamp": hex(1_790_000_000), "logIndex": "0x0"}
    r = readers(head=500, tx_logs={fx["_tx"]: logs})
    r["mints"] = lambda m: [mint] if m == T else []
    r["system_sends"] = lambda start: [{"user": T, "amount": 6_000_000.0, "ts_ms": 1, "hash": "x"}]
    cb.main([], readers=r)
    assert not [f for f in _state(tmp)["findings"] if f["source"] == "circle"]


def test_a_dry_run_can_read_a_perimeter_from_anywhere(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    other = tmp_path_factory.mktemp("per") / "latest.json"
    other.write_text(json.dumps({"members": {
        NEW: {"address": NEW, "role": "core", "weight": 1.0, "why": "test"}}}))
    out = tmp_path_factory.mktemp("dry")
    cb.main(["--dry-run", str(out), "--perimeter", str(other)], readers=readers(head=500))
    assert json.loads((out / "latest.json").read_text())["perimeter_fallback"] is False
