# tests/test_chain_client.py
import pytest

from src.chain import client
from src.chain.budget import CallBudget

ARB = {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0}


def budget(calls=100):
    return CallBudget(max_calls=calls, seconds=1000, clock=lambda: 0.0)


def test_each_kind_maps_to_its_etherscan_action():
    assert client.ACTIONS == {
        "erc20": "tokentx", "native": "txlist", "internal": "txlistinternal"}


def test_fetch_kind_passes_the_chain_id_and_walks_pages(monkeypatch):
    seen = []

    def fake_get(params, chain_id=None):
        seen.append((params["action"], params["startblock"], chain_id))
        if params["startblock"] == 0:
            return {"status": "1", "result": [
                {"blockNumber": "5", "hash": "a"}, {"blockNumber": "9", "hash": "b"}]}
        return {"status": "1", "result": [{"blockNumber": "12", "hash": "c"}]}

    monkeypatch.setattr(client, "etherscan_get", fake_get)
    result, error = client.fetch_kind("0xabc", ARB, "erc20", 0, budget(), page_size=2)

    assert error is None
    assert [r["hash"] for r in result.rows] == ["a", "b", "c"]
    assert seen == [("tokentx", 0, 42161), ("tokentx", 9, 42161)]


def test_no_transactions_found_is_an_empty_result_not_an_error(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "No transactions found", "result": []})
    result, error = client.fetch_kind("0xabc", ARB, "native", 0, budget())
    assert result.rows == []
    assert error is None


def test_a_real_api_error_is_reported_and_does_not_look_like_empty(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "Max rate limit reached", "result": []})
    result, error = client.fetch_kind("0xabc", ARB, "native", 0, budget())
    assert result.rows == []
    assert error == "Max rate limit reached"


def test_a_non_list_result_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "1", "result": "Invalid API Key"})
    result, error = client.fetch_kind("0xabc", ARB, "erc20", 0, budget())
    assert result.rows == []
    assert error and "Invalid API Key" in error


def test_exhausted_budget_stops_the_walk_and_reports_it(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "1", "result": [{"blockNumber": "5", "hash": "a"},
                                  {"blockNumber": "9", "hash": "b"}]})
    b = budget(calls=1)
    result, error = client.fetch_kind("0xabc", ARB, "erc20", 0, b, page_size=2)
    assert [r["hash"] for r in result.rows] == ["a", "b"]   # first page retained
    assert error == "budget_exhausted:call_budget"
    assert b.calls_used == 1


def test_probe_activity_costs_one_call_and_answers_yes_or_no(monkeypatch):
    calls = []

    def fake_get(params, chain_id=None):
        calls.append(params)
        return {"status": "1", "result": [{"blockNumber": "1", "hash": "a"}]}

    monkeypatch.setattr(client, "etherscan_get", fake_get)
    b = budget()
    active, error = client.probe_activity("0xabc", ARB, b)
    assert active is True
    assert error is None
    assert b.calls_used == 1
    assert calls[0]["offset"] == 1


def test_probe_activity_is_false_when_the_address_never_transacted(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "No transactions found", "result": []})
    active, error = client.probe_activity("0xabc", ARB, budget())
    assert active is False
    assert error is None


def test_probe_activity_reports_a_read_error_instead_of_looking_inactive(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "0", "message": "Max rate limit reached", "result": []})
    active, error = client.probe_activity("0xabc", ARB, budget())
    assert active is False
    assert error == "Max rate limit reached"


def test_probe_activity_reports_budget_exhaustion_as_an_error(monkeypatch):
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "status": "1", "result": [{"blockNumber": "1", "hash": "a"}]})
    b = CallBudget(max_calls=0, seconds=1000, clock=lambda: 0.0)
    active, error = client.probe_activity("0xabc", ARB, b)
    assert active is False
    assert error == "budget_exhausted:call_budget"


def test_fetch_code_returns_the_bytecode_string(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "jsonrpc": "2.0", "result": "0x60806040"})
    assert client.fetch_code("0xabc", ARB, budget()) == "0x60806040"


def test_fetch_code_returns_none_when_the_budget_is_gone(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {"result": "0x"})
    b = CallBudget(max_calls=0, seconds=1000, clock=lambda: 0.0)
    assert client.fetch_code("0xabc", ARB, b) is None


def test_fetch_code_treats_a_non_hex_error_string_as_unreadable(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    monkeypatch.setattr(client, "etherscan_get", lambda p, chain_id=None: {
        "jsonrpc": "2.0", "result": "Max rate limit reached"})
    assert client.fetch_code("0xabc", ARB, budget()) is None


def test_fetch_code_returns_none_without_an_api_key_and_never_calls_the_network(monkeypatch):
    """Every sibling live-lookup path (src/linkage.py, src/chain/collect.py,
    src/transfer_graph.py) skips cleanly without a key instead of firing a
    doomed request; fetch_code must too."""
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setattr(client, "etherscan_get",
                        lambda *a, **k: pytest.fail("must not call the API without a key"))
    assert client.fetch_code("0xabc", ARB, budget()) is None


def test_etherscan_get_defaults_to_arbitrum_and_honours_an_override(monkeypatch):
    from src import utils
    seen = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "1", "result": []}

    def fake_request_get(url, params=None, timeout=None):
        seen.update(params)
        return FakeResp()

    monkeypatch.setattr(utils.requests, "get", fake_request_get)
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)

    utils.etherscan_get({"module": "account"})
    assert seen["chainid"] == 42161
    utils.etherscan_get({"module": "account"}, chain_id=8453)
    assert seen["chainid"] == 8453
