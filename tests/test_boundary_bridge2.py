"""Bridge2's own events name the account that withdrew (spec §1, §5)."""

import json
from pathlib import Path

from src.boundary import bridge2

FIX = Path(__file__).parent / "fixtures" / "boundary"
TREASURY = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"


def _rows(name):
    return json.loads((FIX / name).read_text())["result"]


def test_the_treasury_payout_names_the_account_that_withdrew():
    rows = [bridge2.decode_withdrawal(log) for log in _rows("bridge2_withdrawals_treasury.json")]
    assert len(rows) == 4 and all(rows)
    first = next(r for r in rows if r["tx_hash"] ==
                 "0xc0758212634d6d92262e4c9d1bf25b51c8da13e1b30bb060bdf21387703761ad")
    assert first["user"] == first["destination"] == TREASURY
    assert first["usd"] == 1_999_999.0 and first["nonce"] == 1742999426126000
    assert {r["usd"] for r in rows} == {1_999_999.0, 499_999.0, 999_999.0, 939_038.02}
    assert all(r["ts"] > 1_700_000_000 and r["block"] > 0 for r in rows)


def test_live_withdrawals_decode_and_some_go_to_another_address():
    rows = [bridge2.decode_withdrawal(log) for log in _rows("bridge2_withdrawals_sample.json")]
    assert rows and all(rows)
    assert any(r["user"] != r["destination"] for r in rows)


def test_deposits_name_the_depositor():
    rows = [bridge2.decode_deposit(log) for log in _rows("bridge2_deposits_sample.json")]
    assert rows and all(r and r["depositor"].startswith("0x") and r["usd"] > 0 for r in rows)


def test_other_events_and_contracts_are_not_decoded():
    log = _rows("bridge2_withdrawals_treasury.json")[0]
    assert bridge2.decode_withdrawal({**log, "address": "0x" + "1" * 40}) is None
    assert bridge2.decode_withdrawal({**log, "topics": ["0x" + "2" * 64] + log["topics"][1:]}) is None
    assert bridge2.decode_withdrawal({**log, "data": "0x1234"}) is None
    assert bridge2.decode_deposit(log) is None


def test_topics_filter_on_the_indexed_user():
    t = bridge2.withdrawal_topics("0x1419E75330C71ce463102e6A1Eb62FE80b412D5f")
    assert t == {0: bridge2.TOPIC_FINALIZED_WITHDRAWAL,
                 1: "0x000000000000000000000000" + TREASURY[2:]}
    assert bridge2.withdrawal_topics() == {0: bridge2.TOPIC_FINALIZED_WITHDRAWAL}
    assert bridge2.deposit_topics() == {0: bridge2.TOPIC_TRANSFER,
                                        2: "0x000000000000000000000000" + bridge2.BRIDGE[2:]}


def test_event_ids_are_stable():
    row = bridge2.decode_withdrawal(_rows("bridge2_withdrawals_treasury.json")[0])
    assert bridge2.event_id(row) == f"arbitrum:{row['tx_hash']}:{row['log_index']}"
