"""His own Circle withdrawal must not read as an outside account paying him.

A HyperCore-native Circle withdrawal (`sendToEvmWithData`) is burned on HyperEVM
by USDC's forwarder, so its message names the FORWARDER as sender (measured on
his own $6,000,000 withdrawal of 2026-09-15, mint tx 0x10ba0970…).
"""

import json
from pathlib import Path

from src import circle_flows as cf

FIX = Path(__file__).parent / "fixtures" / "boundary"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
OUT = "0x" + "1" * 40


def _received():
    doc = json.loads((FIX / "bs_txlogs_cctp_mint.json").read_text())
    item = next(i for i in doc["items"] if i["address"]["hash"].lower() == cf.MESSAGE_TRANSMITTER_V2)
    return {"topics": item["topics"], "data": item["data"], "transactionHash": item["transaction_hash"],
            "blockNumber": hex(item["block_number"]), "logIndex": item["index"]}


def _as_sent(received: dict) -> dict:
    """Wrap the real burn body in a MessageSent envelope (header + body)."""
    h = received["data"][2:]
    off = int(h[128:192], 16) * 2
    body = h[off + 64:off + 64 + int(h[off:off + 64], 16) * 2]
    header = ("00000001" + format(19, "08x") + format(3, "08x") + "ab" * 32
              + "0" * 24 + "28b5a0e9c621a5badaa536219b3a228c8168cf5d" + "0" * 64 + "0" * 64
              + "000003e8" + "000007d0")
    message = header + body
    data = format(32, "064x") + format(len(message) // 2, "064x") + message
    data += "0" * ((64 - len(data) % 64) % 64)
    return {"topics": [cf.TOPIC_MESSAGE_SENT], "data": "0x" + data,
            "transactionHash": "0xsent", "blockNumber": "0x1", "logIndex": "0x0"}


def test_the_real_withdrawal_is_burned_by_the_forwarder_and_its_hook_names_him():
    row = cf.decode_received(_received())
    assert row["domain"] == 19 and row["message_sender"] == cf.FORWARDER
    assert row["hook_account"] == T and row["mint_recipient"] == T


def test_the_withdrawer_is_recovered_from_the_system_ledger():
    row = cf.decode_sent(_as_sent(_received()))
    assert row["message_sender"] == cf.FORWARDER and row["amount_usd"] == 6_000_000.0
    sends = [{"user": T, "amount": 6_000_000.0, "ts_ms": 1, "hash": "0xh"},
             {"user": OUT, "amount": 10_000.0, "ts_ms": 2, "hash": "0xi"}]
    assert cf.resolve_withdrawer(row, sends) == (T, "system_ledger")
    [fixed] = cf.apply_withdrawers([row], sends, readable=True)
    assert fixed["hl_account"] == T
    # His own withdrawal to his own address is no finding at all.
    assert cf.classify(fixed, {T}, set(), {T}) is None


def test_without_the_fix_it_would_have_paged_a_false_critical():
    row = cf.decode_sent(_as_sent(_received()))
    assert cf.classify(row, {T}, set(), {T}) == cf.KIND_OUTSIDE_PAID_HIM   # the latent bug


def test_ties_break_on_the_hook_and_otherwise_stay_unresolved():
    row = {**cf.decode_sent(_as_sent(_received()))}
    two = [{"user": T, "amount": 6_000_000.0, "ts_ms": 1, "hash": "a"},
           {"user": OUT, "amount": 6_000_000.0, "ts_ms": 2, "hash": "b"}]
    assert cf.resolve_withdrawer(row, two) == (T, "system_ledger+hook")
    assert cf.resolve_withdrawer({**row, "hook_account": None}, two) == (None, "unresolved")
    assert cf.resolve_withdrawer(row, [], readable=False) == (None, "unreadable")


def test_an_unattributed_withdrawal_to_his_address_is_its_own_kind():
    row = {**cf.decode_sent(_as_sent(_received())), "hl_account": None,
           "hl_account_basis": "unresolved"}
    assert cf.classify(row, {T}, set(), {T}) == cf.KIND_UNATTRIBUTED_PAID_HIM
    assert cf.classify({**row, "hl_account_basis": "unreadable"}, {T}, set(), {T}) is None


def test_a_hyperevm_native_burn_keeps_its_own_sender():
    row = {"direction": "out", "message_sender": OUT, "hl_account": OUT, "amount_usd": 5.0,
           "hook_account": None}
    assert cf.resolve_withdrawer(row, []) == (OUT, "message_sender")


def test_system_sends_reads_only_sends_into_the_usdc_system_address():
    ledger = [{"time": 5, "hash": "0x1", "delta": {"type": "send", "user": OUT,
                                                   "destination": cf.SYSTEM_USDC, "amount": "12.5"}},
              {"time": 6, "hash": "0x2", "delta": {"type": "spotTransfer", "user": cf.SYSTEM_USDC,
                                                   "destination": OUT, "amount": "7"}}]
    assert cf.system_sends(ledger) == [{"user": OUT, "amount": 12.5, "ts_ms": 5, "hash": "0x1"}]


def _main_env(monkeypatch, rows, sends):
    import scripts.check_circle_flows as script
    import src.alerts as alerts
    monkeypatch.setattr(script, "load_config", lambda: {"target_wallet": T, "known_self_wallets": []})
    monkeypatch.setattr("scripts.check_deposit_sentinels.hl_state",
                        lambda a, post=None: {"read_ok": False})
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setattr(script, "walk", lambda previous: (list(rows["now"]), {
        "head_block": 10, "last_block": 10, "windows_read": 1, "lag_blocks": 0,
        "error": None, "source": "rpc"}))
    monkeypatch.setattr(script, "system_sends_since", lambda hours, **kw: sends["now"])
    sent = []
    monkeypatch.setattr(alerts, "alert_circle_flow", lambda kind, r, state: sent.append(kind) or True)
    return script, sent


def _saved():
    from src import utils
    return json.loads((utils.DATA_DIR / "circle_flows" / "latest.json").read_text())


def test_main_resolves_his_own_forwarder_withdrawal_and_raises_nothing(monkeypatch):
    rows = {"now": [cf.decode_sent(_as_sent(_received()))]}
    sends = {"now": ([{"user": T, "amount": 6_000_000.0, "ts_ms": 1, "hash": "h"}], True)}
    script, sent = _main_env(monkeypatch, rows, sends)
    script.main()
    assert sent == [] and _saved()["pending_resolution"] == []


def test_main_holds_an_unreadable_one_then_judges_it_when_the_ledger_reads(monkeypatch):
    rows = {"now": [cf.decode_sent(_as_sent(_received()))]}
    sends = {"now": ([], False)}
    script, sent = _main_env(monkeypatch, rows, sends)
    script.main()
    assert sent == [] and len(_saved()["pending_resolution"]) == 1
    rows["now"] = []
    sends["now"] = ([{"user": OUT, "amount": 6_000_000.0, "ts_ms": 1, "hash": "h"}], True)
    script.main()
    assert sent == [cf.KIND_OUTSIDE_PAID_HIM] and _saved()["pending_resolution"] == []


def test_an_unattributed_withdrawal_alerts_high_not_critical(monkeypatch):
    import src.alerts as alerts
    captured = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: captured.append(subject) or True)
    row = {**cf.decode_sent(_as_sent(_received())), "hl_account": None,
           "hl_account_basis": "unresolved"}
    assert alerts.alert_circle_flow(cf.KIND_UNATTRIBUTED_PAID_HIM, row, None)
    assert alerts._severity_of(captured[0]) == "HIGH" and "Nobody Can Attribute" in captured[0]
