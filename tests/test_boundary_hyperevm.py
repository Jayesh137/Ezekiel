"""Where a credit through Circle's deposit wallet came from, read on HyperEVM.

Fixtures are mainnet logs captured 2026-10-06 from the public RPC: the dry
run's "unresolved" $15.3M account depositing its own HyperEVM USDC (block
46,703,637), and a Circle message from Arbitrum forwarded into an account
(block 46,703,460).
"""

import json
from pathlib import Path

import pytest

from src.boundary import hyperevm

FX = json.loads((Path(__file__).parent / "fixtures" / "boundary"
                 / "hyperevm_core_deposits.json").read_text())
HOLDER = "0x1cb5b5c2d32f384da2de2c7c8216868bf23136d9"
CCTP_ACCOUNT = "0x1d5fa8332b34c261cb90c6216b86f18cf2228fd2"
EXTENSION = "0xa95d9c1f655341597c94393fddc30cf3c08e4fce"


def chain(case, *, extra_logs=()):
    """A fake HyperEVM: blocks 0.9837 s apart, anchored on the fixture block."""
    anchor, anchor_ts = FX[case]["block"], FX[case]["block_ts"]
    calls = []

    def ts_of(block):
        return anchor_ts + round((block - anchor) * hyperevm.SECONDS_PER_BLOCK)

    def call(method, params):
        calls.append(method)
        if method == "eth_blockNumber":
            return hex(anchor + 2_000_000)
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(ts_of(int(params[0], 16)))}
        if method == "eth_getLogs":
            q = params[0]
            lo, hi = int(q["fromBlock"], 16), int(q["toBlock"], 16)
            return [x for x in FX[case]["transfers"] + list(extra_logs)
                    if lo <= int(x["blockNumber"], 16) <= hi]
        if method == "eth_getTransactionReceipt":
            return {"logs": FX[case].get("receipt_logs") or []}
        raise AssertionError(method)
    return call, calls


def test_a_deposit_paid_from_a_hyperevm_address_names_that_address():
    call, calls = chain("holder")
    src = hyperevm.deposit_source(HOLDER, 10_000_000.0, FX["holder"]["block_ts"], call=call)
    assert (src["address"], src["chain"], src["kind"]) == (HOLDER, "hyperevm", "hyperevm_payer")
    assert "eth_getTransactionReceipt" not in calls


def test_a_circle_message_names_its_source_domain_and_sender():
    call, _ = chain("cctp")
    src = hyperevm.deposit_source(CCTP_ACCOUNT, 29_942.04, FX["cctp"]["block_ts"], call=call)
    assert (src["address"], src["chain"], src["domain"], src["kind"]) == \
        (EXTENSION, "arbitrum", 3, "circle_message")
    assert src["message_usd"] == pytest.approx(29_942.24)


def test_two_deposits_of_the_same_size_are_not_guessed():
    twin = {**FX["holder"]["transfers"][0], "transactionHash": "0x" + "ab" * 32,
            "topics": [FX["holder"]["transfers"][0]["topics"][0],
                       "0x" + "0" * 24 + "77" * 20, FX["holder"]["transfers"][0]["topics"][2]]}
    call, _ = chain("holder", extra_logs=[twin])
    assert hyperevm.deposit_source(HOLDER, 10_000_000.0, FX["holder"]["block_ts"],
                                   call=call) is None


def test_no_deposit_of_that_size_is_none_not_an_error():
    call, _ = chain("holder")
    assert hyperevm.deposit_source(HOLDER, 123_456.0, FX["holder"]["block_ts"], call=call) is None


def test_the_block_search_converges_from_the_estimate_in_a_few_reads():
    call, calls = chain("holder")
    assert hyperevm.block_at(FX["holder"]["block_ts"], call=call) == FX["holder"]["block"]
    assert calls.count("eth_getBlockByNumber") <= 4


def test_a_failed_read_raises_and_is_never_read_as_no_deposit():
    def down(method, params):
        raise RuntimeError("rate limited")
    with pytest.raises(hyperevm.EvmReadError):
        hyperevm.deposit_source(HOLDER, 10_000_000.0, FX["holder"]["block_ts"], call=down)


ACCT = "0x" + "5" * 40
HIM = "0x45d26f28196d226497130c4bac709d808fed4029"
HYPE_TOKEN = "0x5555555555555555555555555555555555555555"


def _tokentx(frm, to, value, decimals, contract, ts, symbol="USDC"):
    """One Etherscan V2 `account/tokentx` row, in its documented shape."""
    return {"blockNumber": "100", "timeStamp": str(ts), "hash": "0x" + "1" * 64, "from": frm,
            "contractAddress": contract, "to": to, "value": str(value), "tokenName": symbol,
            "tokenSymbol": symbol, "tokenDecimal": str(decimals)}


def test_hyperevm_inbound_values_only_native_usdc_and_keeps_the_window():
    def get(params, chain_id=None):
        assert chain_id == 999
        if params["action"] == "tokentx":
            return {"status": "1", "message": "OK", "result": [
                _tokentx(HIM, ACCT, 2_000_000 * 10**6, 6, hyperevm.USDC, 1_000),
                _tokentx(HIM, ACCT, 5 * 10**18, 18, HYPE_TOKEN, 1_100, "WHYPE"),
                _tokentx(ACCT, HIM, 7 * 10**6, 6, hyperevm.USDC, 1_200),        # outbound
                _tokentx(HIM, ACCT, 9 * 10**6, 6, hyperevm.USDC, 99)]}           # before the window
        return {"status": "1", "message": "OK", "result": [
            {"timeStamp": "1050", "hash": "0x" + "2" * 64, "from": HIM, "to": ACCT,
             "value": str(3 * 10**18), "isError": "0"}]}
    rows = hyperevm.inbound_transfers(ACCT, since_ts=500, until_ts=2_000, get=get)
    assert {(r["from"], r["usd"], r["symbol"]) for r in rows} == {
        (HIM, 2_000_000.0, "USDC"), (HIM, None, "WHYPE"), (HIM, None, "HYPE")}


def test_hyperevm_inbound_tells_an_empty_answer_from_a_failed_one():
    def empty(params, chain_id=None):
        return {"status": "0", "message": "No transactions found", "result": []}
    assert hyperevm.inbound_transfers(ACCT, since_ts=0, until_ts=1, get=empty) == []

    def failed(params, chain_id=None):
        return {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
    with pytest.raises(hyperevm.EvmReadError):
        hyperevm.inbound_transfers(ACCT, since_ts=0, until_ts=1, get=failed)
