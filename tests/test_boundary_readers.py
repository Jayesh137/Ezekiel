"""Blockscout v2, read strictly and on a budget (spec §6.3, §7.2)."""

import json
from pathlib import Path

import pytest

from src.boundary import bridge2, readers

FIX = Path(__file__).parent / "fixtures" / "boundary"
DD53 = "0xdd53c5297309130ab5fe5623dc905752e3342b13"


def _fx(name):
    return json.loads((FIX / name).read_text())


def test_inbound_transfers_are_valued_by_contract_rate_and_bounded_in_time():
    doc = _fx("bs_inbound_dd53.json")
    got = readers.inbound_transfers("arbitrum", DD53, since_ts=0, until_ts=2_000_000_000,
                                    budget=readers.Budget(5), get=lambda url, params: doc, pages=1)
    assert len(got) == len(doc["items"])
    senders = {t["from"] for t in got}
    assert "0xee7ae85f2fe2239e27d9c1e23fffe168d63b4055" in senders
    big = max(got, key=lambda t: t["usd"] or 0)
    assert big["usd"] > 1_000_000 and big["symbol"] == "USDC" and big["ts"] > 1_700_000_000
    dust = [t for t in got if t["from"] == "0x2df1df582d0a1efc7178fd78b2bcd9aa08a73df7"]
    assert dust and all(t["usd"] < 1 and t["from_is_contract"] for t in dust)


def test_inbound_transfers_stop_at_the_window_start():
    doc = _fx("bs_inbound_dd53.json")
    newest = max(readers._ts(i["timestamp"]) for i in doc["items"])
    got = readers.inbound_transfers("arbitrum", DD53, since_ts=newest, until_ts=newest,
                                    budget=readers.Budget(5), get=lambda url, params: doc)
    assert got and all(t["ts"] == newest for t in got)


def test_first_gas_comes_from_the_oldest_value_bearing_incoming_transaction():
    answers = {"counters": _fx("bs_counters_dd53.json"), "transactions": _fx("bs_txs_to_dd53.json")}

    def get(url, params):
        return answers["counters"] if url.endswith("/counters") else answers["transactions"]
    gas = readers.first_gas("arbitrum", DD53, budget=readers.Budget(5), get=get)
    assert gas["from"] == "0xd7a827fbaf38c98e8336c5658e4bcbcd20a4fd2d" and gas["fresh"]


def test_first_gas_is_not_read_for_a_busy_address():
    def get(url, params):
        return {"transactions_count": "5000", "token_transfers_count": "9000"}
    assert readers.first_gas("arbitrum", DD53, budget=readers.Budget(5), get=get) is None


def test_tx_logs_come_back_in_etherscan_shape_and_decode():
    doc = _fx("bs_txlogs_payout.json")
    rows = readers.tx_logs("arbitrum", "0xc0758212634d6d92262e4c9d1bf25b51c8da13e1b30bb060bdf21387703761ad",
                           budget=readers.Budget(2), get=lambda url, params: doc)
    payouts = [r for r in (bridge2.decode_withdrawal(x) for x in rows) if r]
    assert any(p["user"] == "0x1419e75330c71ce463102e6a1eb62fe80b412d5f" for p in payouts)


def test_a_spent_budget_or_failed_read_raises_never_empty():
    b = readers.Budget(1)
    b.spend()
    with pytest.raises(readers.ReadError):
        b.spend()

    def down(url, params):
        raise OSError("down")
    with pytest.raises(readers.ReadError):
        readers.inbound_transfers("arbitrum", DD53, since_ts=0, until_ts=1,
                                  budget=readers.Budget(3), get=down)
    with pytest.raises(readers.ReadError):
        readers.inbound_transfers("monad", DD53, since_ts=0, until_ts=1, budget=readers.Budget(3))


def test_tx_logs_come_back_with_hex_block_numbers_so_every_decoder_applies():
    from src import circle_flows as cf
    doc = _fx("bs_txlogs_cctp_mint.json")
    rows = readers.tx_logs("arbitrum", doc["_tx"], budget=readers.Budget(2),
                           get=lambda url, params: doc)
    received = [cf.decode_received(r) for r in rows
                if r["address"] == cf.MESSAGE_TRANSMITTER_V2]
    assert received and received[0]["domain"] == 19
    assert all(str(r["blockNumber"]).startswith("0x") for r in rows)


USDC_ARB = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
ACCT2, PAYER = "0x" + "5" * 40, "0x" + "6" * 40


def _es(rows_by_action):
    calls = []

    def get(params, chain_id=None):
        calls.append(params.get("action"))
        rows = rows_by_action.get(params.get("action"))
        if rows is None:
            return {"status": "0", "message": "NOTOK", "result": "Free API access is not supported"}
        if isinstance(rows, dict):
            return {"jsonrpc": "2.0", "result": rows}
        return {"status": "1", "message": "OK", "result": rows} if rows else \
            {"status": "0", "message": "No transactions found", "result": []}
    return get, calls


def test_with_a_key_inbound_comes_from_etherscan_valued_by_contract():
    # Blockscout's v2 API answered 403 to the GitHub runner on the first live
    # run, and its index has holes; with the key, Etherscan is read first.
    get, calls = _es({"tokentx": [
        {"timeStamp": "1500", "hash": "0xa", "from": PAYER, "to": ACCT2, "value": str(250_000 * 10**6),
         "tokenDecimal": "6", "tokenSymbol": "USDC", "contractAddress": USDC_ARB},
        {"timeStamp": "1400", "hash": "0xb", "from": PAYER, "to": ACCT2, "value": str(10**6),
         "tokenDecimal": "6", "tokenSymbol": "USDC", "contractAddress": "0x" + "9" * 40},
        {"timeStamp": "100", "hash": "0xc", "from": PAYER, "to": ACCT2, "value": "1",
         "tokenDecimal": "6", "tokenSymbol": "USDC", "contractAddress": USDC_ARB}]})

    def blockscout(*a, **k):
        raise AssertionError("Blockscout read with a key set")
    rows = readers.inbound_transfers("arbitrum", ACCT2, since_ts=1_000, until_ts=2_000,
                                     budget=readers.Budget(5), get=blockscout, has_key=True,
                                     etherscan=get)
    assert [(r["tx_hash"], r["usd"]) for r in rows] == [("0xa", 250_000.0), ("0xb", None)]
    assert calls == ["tokentx"]


def test_an_etherscan_refusal_falls_back_to_blockscout():
    get, _ = _es({})
    page = {"items": [], "next_page_params": None}
    rows = readers.inbound_transfers("base", ACCT2, since_ts=0, until_ts=10,
                                     budget=readers.Budget(5), get=lambda url, params: page,
                                     has_key=True, etherscan=get)
    assert rows == []


def test_with_a_key_tx_logs_and_first_gas_come_from_etherscan():
    receipt = {"logs": [{"address": USDC_ARB, "topics": ["0xt"], "data": "0x01",
                         "blockNumber": "0x10", "logIndex": "0x", "transactionHash": "0xh"}]}
    get, _ = _es({"eth_getTransactionReceipt": receipt, "txlist": [
        {"timeStamp": "900", "hash": "0xg", "from": PAYER, "to": ACCT2, "value": "5000", "isError": "0"}]})

    def blockscout(*a, **k):
        raise AssertionError("Blockscout read with a key set")
    logs_ = readers.tx_logs("arbitrum", "0xh", budget=readers.Budget(5), get=blockscout,
                            has_key=True, etherscan=get)
    assert logs_[0]["address"] == USDC_ARB and logs_[0]["logIndex"] == "0x"
    gas = readers.first_gas("arbitrum", ACCT2, budget=readers.Budget(5), get=blockscout,
                            has_key=True, etherscan=get)
    assert (gas["from"], gas["ts"], gas["fresh"]) == (PAYER, 900, True)
