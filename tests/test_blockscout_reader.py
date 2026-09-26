from src.chain.blockscout import fetch_kind
from src.chain.budget import CallBudget

A = "0x" + "1" * 40
B = "0x" + "2" * 40
TOKEN = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


def row(block=10):
    return {"block_number": block, "timestamp": "2026-09-01T00:00:00Z", "transaction_hash": f"tx{block}",
            "from": {"hash": A}, "to": {"hash": B}, "log_index": 0,
            "token": {"address_hash": TOKEN, "symbol": "USDC", "decimals": "6", "type": "ERC-20"},
            "total": {"value": "1000000", "decimals": "6"}}


def budget():
    return CallBudget(max_calls=5, seconds=100, clock=lambda: 0)


def test_keyset_pages_keep_contract_identity_and_complete_cursor():
    calls = []
    def get(url, params):
        calls.append(params)
        return {"items": [row(10)], "next_page_params": {"block_number": 10, "index": 0}} if len(calls) == 1 else {
            "items": [row(5)], "next_page_params": None}
    walk, error = fetch_kind(A, {"name": "base"}, "erc20", 0, budget(), get=get)
    assert error is None and not walk.truncated
    assert walk.last_block == 10
    assert calls[1]["block_number"] == 10
    assert walk.rows[0]["contractAddress"] == TOKEN


def test_partial_descending_page_never_advances_past_unread_older_records():
    walk, error = fetch_kind(A, {"name": "base"}, "erc20", 1, budget(), max_pages=1,
        get=lambda *a: {"items": [row(10)], "next_page_params": {"block_number": 10}})
    assert walk.rows and walk.truncated
    assert walk.last_block == 1
    assert error


def test_reader_cannot_price_a_forged_usdc_contract_as_usdc():
    from src.chain.collect import normalise_row
    payload = row()
    payload["token"]["address_hash"] = A
    walk, _ = fetch_kind(A, {"name": "base"}, "erc20", 0, budget(),
                         get=lambda *a: {"items": [payload], "next_page_params": None})
    result = normalise_row(walk.rows[0], {"name": "base", "chain_id": 8453, "native": "ETH"},
                           "erc20", lambda *a: None, canonical={("base", "USDC"): {TOKEN}})
    assert result["amount_usd"] is None


def test_missing_pagination_shape_is_failed_read_not_empty_history():
    walk, error = fetch_kind(A, {"name": "optimism"}, "erc20", 0, budget(), get=lambda *a: {})
    assert error and walk.truncated
