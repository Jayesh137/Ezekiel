"""Unit is a keyless feed in both directions (spec §5)."""

import json
from pathlib import Path

import pytest

from src.boundary import unit

FIX = Path(__file__).parent / "fixtures" / "boundary"


def _doc(name):
    return json.loads((FIX / name).read_text())


def test_a_bitcoin_deposit_is_inbound_from_its_source_address():
    evs = unit.events(_doc("unit_ops_btc_deposit.json"))
    assert len(evs) == 2
    e = evs[0]
    assert e["source"] == "unit" and e["direction"] == "in"
    assert e["hl_account"] == "0x458d583dbfe5b0a143bf09f45acf29f0022aab3b"
    assert e["counterparty"] == "bc1pg64kvzamkafsld07tsrqpk402pdwfe5jyxr9tnfgaku9guh998nqeguj7u"
    assert e["chain"] == "bitcoin" and e["asset"] == "btc"
    assert e["amount_usd"] is None and e["ts"] > 1_700_000_000 and e["event_id"]


def test_withdrawals_name_their_destination():
    outs = [e for e in unit.events(_doc("unit_ops_mixed.json")) if e["direction"] == "out"]
    assert outs and all(e["hl_account"] == "0x92fca16b23ec24dd7206552be0286eb06ca83cd2"
                        for e in outs)


def test_no_operations_is_empty_not_unreadable():
    assert unit.events(_doc("unit_ops_target_empty.json")) == []


def test_a_failed_read_raises():
    def down(url):
        raise OSError("down")
    with pytest.raises(unit.UnitReadError):
        unit.read_operations("0xabc", get=down)
    with pytest.raises(unit.UnitReadError):
        unit.read_operations("0xabc", get=lambda url: {"error": "x"})


def test_failed_operations_drop_and_non_evm_case_is_kept():
    op = {"sourceChain": "solana", "destinationChain": "hyperliquid",
          "sourceAddress": "5tzFkiKscXHK", "state": "failed", "sourceTxHash": "x",
          "destinationAddress": "0xABC0000000000000000000000000000000000001"}
    assert unit.normalise(op) is None
    e = unit.normalise({**op, "state": "done"})
    assert e["counterparty"] == "5tzFkiKscXHK"
    assert e["hl_account"] == "0xabc0000000000000000000000000000000000001"


def test_hyperliquid_to_hyperliquid_is_not_an_edge_event():
    assert unit.normalise({"sourceChain": "hyperliquid", "destinationChain": "hyperliquid",
                           "sourceAddress": "0x1", "destinationAddress": "0x2"}) is None
