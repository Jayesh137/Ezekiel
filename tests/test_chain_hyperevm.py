# tests/test_chain_hyperevm.py
"""Reading HyperEVM without mistaking a failed read for an all-clear.

The target sent $23,000,000 to the HyperCore system address for USDC across five
transfers (2026-06-12 to 2026-08-28) and nothing returned that way. His nonce on
HyperEVM is 0, which rules the chain out as a migration path taken so far — but
only because we read it. A rate-limited run that reported the same all-clear
without reading anything would be the silent miss this project exists to prevent,
so every test here is about keeping "0" and "unknown" apart.
"""

import pytest

from src.chain import hyperevm as hx

ADDR = "0x45d26f28196d226497130c4bac709d808fed4029"
TOKEN = "0x9fdbda0a5e284c32744d2f17ee5c74b284993463"


def _ok(result):
    return lambda method, params: {"jsonrpc": "2.0", "id": 1, "result": result}


def _err(message):
    return lambda method, params: {"jsonrpc": "2.0", "id": 1,
                                   "error": {"code": 3, "message": message}}


def test_rpc_call_returns_a_result():
    assert hx.rpc_call("eth_blockNumber", [], caller=_ok("0x2b64695")) == "0x2b64695"


def test_rpc_call_retries_a_rate_limit_then_succeeds():
    calls = []

    def flaky(method, params):
        calls.append(method)
        if len(calls) < 3:
            return {"error": {"message": "rate limited"}}
        return {"result": "0x5"}

    assert hx.rpc_call("eth_blockNumber", [], caller=flaky,
                       sleep=lambda s: None) == "0x5"
    assert len(calls) == 3


def test_a_revert_is_not_retried():
    """A revert is a real reply about the request. Retrying it spends the budget
    to be told the same thing three times."""
    calls = []

    def reverting(method, params):
        calls.append(method)
        return {"error": {"code": 3, "message": "execution reverted"}}

    with pytest.raises(hx.RpcUnavailable):
        hx.rpc_call("eth_call", [], caller=reverting, sleep=lambda s: None)
    assert len(calls) == 1


def test_persistent_rate_limiting_raises_rather_than_returning_none():
    with pytest.raises(hx.RpcUnavailable):
        hx.rpc_call("eth_blockNumber", [], caller=_err("rate limited"),
                    sleep=lambda s: None)


def test_transport_failure_raises():
    def boom(method, params):
        raise TimeoutError("connection reset")

    with pytest.raises(hx.RpcUnavailable):
        hx.rpc_call("eth_blockNumber", [], caller=boom, sleep=lambda s: None)


def test_account_activity_reads_a_fresh_address():
    def caller(method, params):
        return {"result": {"eth_getTransactionCount": "0x0",
                           "eth_getBalance": "0x0",
                           "eth_getCode": "0x"}[method]}

    act = hx.account_activity(ADDR, caller=caller)
    assert act["nonce"] == 0
    assert act["native_wei"] == 0
    assert act["has_code"] is False
    assert act["errors"] == []
    assert hx.never_acted(act) is True


def test_account_activity_reads_an_active_address():
    def caller(method, params):
        return {"result": {"eth_getTransactionCount": "0x11",
                           "eth_getBalance": "0xde0b6b3a7640000",
                           "eth_getCode": "0x6080"}[method]}

    act = hx.account_activity(ADDR, caller=caller)
    assert act["nonce"] == 17
    assert act["native_wei"] == 10**18
    assert act["has_code"] is True
    assert hx.never_acted(act) is False


def test_an_unreadable_nonce_is_never_an_all_clear():
    """The load-bearing assertion of this file. never_acted must say False when
    the nonce could not be read, or a rate-limited run reports that the target
    has not moved when nobody actually looked."""
    act = hx.account_activity(ADDR, caller=_err("rate limited"),
                              sleep=lambda s: None)
    assert act["nonce"] is None
    assert act["errors"]
    assert hx.never_acted(act) is False
    assert "unreadable" in hx.summarise(act)


def test_a_partial_read_keeps_the_fields_it_got():
    def caller(method, params):
        if method == "eth_getTransactionCount":
            return {"result": "0x3"}
        return {"error": {"message": "execution reverted"}}

    act = hx.account_activity(ADDR, caller=caller)
    assert act["nonce"] == 3
    assert act["native_wei"] is None
    assert len(act["errors"]) == 2


def test_token_balance_reads_a_zero_balance():
    assert hx.token_balance(ADDR, TOKEN, caller=_ok("0x" + "0" * 64)) == 0


def test_token_balance_reads_a_real_balance():
    assert hx.token_balance(ADDR, TOKEN, caller=_ok(hex(1234567))) == 1234567


def test_a_reverting_token_reads_as_unknown_not_zero():
    """USDC's linked contract reverts on balanceOf while UBTC's answers. Zero
    here would invent an empty balance for a token we cannot query at all."""
    assert hx.token_balance(ADDR, TOKEN, caller=_err("execution reverted"),
                            sleep=lambda s: None) is None


def test_summarise_distinguishes_all_three_states():
    assert "never transacted" in hx.summarise({"nonce": 0})
    assert "has sent 4" in hx.summarise({"nonce": 4})
    assert "unreadable" in hx.summarise({"nonce": None})


def test_the_log_range_limit_is_recorded():
    """Documented so a future sweep is not written against an imagined limit."""
    assert hx.MAX_LOG_RANGE == 1000
    assert "1000" in hx.describe_log_limits()
