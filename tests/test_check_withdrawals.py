# tests/test_check_withdrawals.py
"""The withdrawal runner end to end, offline: the bridge pairing it always did
plus the Circle pairing, and the alerts the Circle side raises. Every read is
faked; nothing touches the network or the real data/ tree."""

import importlib

from src import alerts
from src import withdrawals as wd

cw = importlib.import_module("scripts.check_withdrawals")

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
ZERO = "0x0000000000000000000000000000000000000000"
SYS = "0x2000000000000000000000000000000000000000"
STRANGER = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
CONFIG = {"target_wallet": T, "known_self_wallets": [TR],
          "hl_bridge_contract": "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"}


def _send(h, t_ms, amount):
    return {"time": t_ms, "hash": h, "delta": {"type": "send", "user": T, "destination": SYS,
                                                "token": "USDC", "amount": str(amount),
                                                "usdcValue": str(amount), "fee": "0.0"}}


def _mint(ts, usd, dst, chain="arbitrum", tx="0xm"):
    return {"chain": chain, "ts": ts, "src": ZERO, "dst": dst, "kind": "erc20", "asset": "USDC",
            "amount": usd, "amount_usd": usd, "spam": False, "tx_hash": tx}


def _wire(monkeypatch, tmp_path, *, ledger, records, payloads):
    """Every I/O the runner does, answered from memory."""
    monkeypatch.setattr(cw, "load_config", lambda: CONFIG)
    monkeypatch.setattr(cw, "records_for", lambda w: records.get(w, []))
    monkeypatch.setattr(cw, "load_all_records", lambda d: ledger if d.endswith("ledger")
                        else list(payloads.values()))
    monkeypatch.setattr(cw, "_window_fetcher", lambda config: (lambda t: ([], None)))
    monkeypatch.setattr(wd, "WITHDRAWALS_DIR", tmp_path / "withdrawals")
    foreign, unresolved = [], []
    monkeypatch.setattr(cw, "alert_foreign_destination",
                        lambda *a, **k: foreign.append((a, k)) or True)
    monkeypatch.setattr(cw, "alert_unresolved_cctp_withdrawal",
                        lambda *a, **k: unresolved.append(a) or True)
    return foreign, unresolved


def test_circle_withdrawals_paired_at_any_cluster_address_alert_nothing(monkeypatch, tmp_path, capsys):
    old = 1_700_000_000
    ledger = [_send("0x1", old * 1000, 7_000_000), _send("0x2", (old + 100) * 1000, 2_500_000)]
    records = {T: [_mint(old + 9, 6_999_999.8, T, tx="0xa")],
               TR: [_mint(old + 130, 2_499_999.8, TR, chain="ethereum", tx="0xb")]}
    foreign, unresolved = _wire(monkeypatch, tmp_path, ledger=ledger, records=records, payloads={})

    assert cw.main() == 0

    assert foreign == [] and unresolved == []
    out = capsys.readouterr().out
    assert "circle: 2 CCTP withdrawal(s), $9,500,000: 2 minted at a cluster address" in out
    saved = (tmp_path / "withdrawals" / "latest.json").read_text()
    assert '"matched_to_cluster_mint": 2' in saved


def test_a_circle_withdrawal_to_a_stranger_alerts_critical_with_the_chain(monkeypatch, tmp_path):
    old = 1_700_000_000
    ledger = [_send("0x1", old * 1000, 2_500_000)]
    payloads = {"0x1": {"_key": "0x1", "hash": "0x1", "type": "sendToEvmWithData",
                        "destination": STRANGER, "destination_chain": "base"}}
    foreign, unresolved = _wire(monkeypatch, tmp_path, ledger=ledger, records={}, payloads=payloads)

    cw.main()

    assert unresolved == []
    ((args, kwargs),) = foreign
    assert args[:3] == (T, "sendToEvmWithData", STRANGER)
    assert args[3] == 2_500_000.0 and args[4] == "USDC"
    assert args[5].startswith("2023-11-14T22:13:20")          # the ledger time, as ISO
    assert kwargs == {"chain": "base"}


def test_a_circle_withdrawal_nobody_can_name_alerts_high_once(monkeypatch, tmp_path, capsys):
    old = 1_700_000_000
    ledger = [_send("0x1", old * 1000, 4_000_000)]
    foreign, unresolved = _wire(monkeypatch, tmp_path, ledger=ledger, records={}, payloads={})

    cw.main()

    assert foreign == []
    assert unresolved == [(T, 4_000_000.0, "2023-11-14T22:13:20+00:00", "0x1")]
    assert "circle UNRESOLVED $4,000,000.00" in capsys.readouterr().out


def test_the_unresolved_alert_is_high_and_keyed_on_the_withdrawal(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append((key, hours, subject, body)) or True)
    assert alerts.alert_unresolved_cctp_withdrawal(T, 4_000_000.0, "2026-09-12T00:00:00+00:00", "0xAB")
    key, hours, subject, body = sent[0]
    assert alerts._severity_of(subject) == "HIGH"
    assert key == "cctp_unresolved_0xab" and hours >= 24 * 7
    assert "4000000.0 USDC" in body and "sendToEvmWithData" in body


def test_iso_never_raises_on_a_bad_timestamp():
    assert cw._iso(None) is None and cw._iso("x") is None
    assert cw._iso(0) == "1970-01-01T00:00:00+00:00"
