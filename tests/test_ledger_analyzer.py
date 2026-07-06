# tests/test_ledger_analyzer.py
"""Tests for HL-native transfer counterparty extraction — the in-platform
migration detector. Covers direction, self/exclusion filtering, value parsing,
bidirectional weighting, and the new-outbound alert cursor."""

from src import ledger_analyzer as la

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
OTHER = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
SYSTEM = "0x2000000000000000000000000000000000000000"


def _entry(delta_type, user, dest, value, ts=1_700_000_000_000, token="USDC", value_key="usdc"):
    return {"time": ts, "hash": f"0x{ts}", "delta": {"type": delta_type, "user": user,
            "destination": dest, value_key: str(value), "token": token}}


def test_delta_usd_prefers_usdc_then_usdcvalue():
    assert la._delta_usd({"usdc": "1000.5"}) == 1000.5
    assert la._delta_usd({"usdcValue": "42.0"}) == 42.0
    assert la._delta_usd({"usdc": "0", "usdcValue": "7"}) == 7.0  # skips zero usdc
    assert la._delta_usd({"amount": "5"}) == 0.0  # no usd field


def test_outbound_and_inbound_aggregated():
    ledger = [
        _entry("internalTransfer", TARGET, OTHER, 1_000_000),
        _entry("send", OTHER, TARGET, 400_000, value_key="usdcValue", token="HYPE"),
    ]
    cps = la.build_counterparties(ledger, TARGET, excluded=set(), known_self=set())
    assert len(cps) == 1
    c = cps[0]
    assert c["wallet"] == OTHER
    assert c["total_out_usd"] == 1_000_000
    assert c["total_in_usd"] == 400_000
    assert c["bidirectional"] is True
    assert c["transfer_count"] == 2
    assert "HYPE" in c["tokens"]


def test_excluded_and_self_transfers_dropped():
    ledger = [
        _entry("send", TARGET, SYSTEM, 9_000_000),          # excluded system addr
        _entry("internalTransfer", TARGET, TARGET, 5_000),  # self-transfer
        _entry("accountClassTransfer", TARGET, "", 1_000),  # non-counterparty type
        _entry("internalTransfer", TARGET, OTHER, 2_000),   # real
    ]
    cps = la.build_counterparties(ledger, TARGET, excluded={SYSTEM}, known_self=set())
    assert [c["wallet"] for c in cps] == [OTHER]


def test_min_track_floor_but_known_self_always_kept():
    known = "0xabc0000000000000000000000000000000000000"
    ledger = [
        _entry("internalTransfer", TARGET, OTHER, 50),   # below floor -> dropped
        _entry("internalTransfer", TARGET, known, 10),   # below floor but known-self -> kept
    ]
    cps = la.build_counterparties(ledger, TARGET, excluded=set(),
                                  known_self={known}, min_track=1000)
    wallets = {c["wallet"] for c in cps}
    assert known in wallets
    assert OTHER not in wallets


def test_bidirectional_ranks_above_larger_oneway():
    one_way = "0x1111111111111111111111111111111111111111"
    two_way = "0x2222222222222222222222222222222222222222"
    ledger = [
        _entry("internalTransfer", TARGET, one_way, 1_000_000),  # bigger, one-way
        _entry("internalTransfer", TARGET, two_way, 500_000),    # smaller but two-way
        _entry("send", two_way, TARGET, 500_000, value_key="usdcValue"),
    ]
    cps = la.build_counterparties(ledger, TARGET, excluded=set(), known_self=set())
    assert cps[0]["wallet"] == two_way  # bidirectional 1.5x boost wins


def test_check_new_outbound_alerts_once_then_cursor_blocks(monkeypatch):
    sent = []
    cursors = {}
    monkeypatch.setattr(la, "read_cursor", lambda name: cursors.get(name, 0))
    monkeypatch.setattr(la, "write_cursor", lambda name, v: cursors.__setitem__(name, v))
    monkeypatch.setattr(la, "load_config", lambda: {"hl_transfer": {"min_usdc_alert": 50000}})

    # check_new_outbound_transfers does `from src.alerts import alert_hl_native_transfer`
    monkeypatch.setattr("src.alerts.alert_hl_native_transfer",
                        lambda *a, **k: sent.append(a[0]) or True)

    result = {"counterparties": [
        {"wallet": OTHER, "total_out_usd": 1_000_000, "total_in_usd": 0,
         "bidirectional": False, "tokens": ["USDC"], "known_self": False,
         "last_seen_ms": 1_700_000_000_000},
    ]}
    first = la.check_new_outbound_transfers(result)
    assert len(first) == 1 and sent == [OTHER]
    # Second run with same timestamp is below/at cursor -> no re-alert
    second = la.check_new_outbound_transfers(result)
    assert second == []
