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
def test_partial_backfill_resumes_and_new_head_is_not_skipped():
    from src.chain.blockscout import fetch_kind
    from src.chain.budget import CallBudget
    chain = {'name': 'base'}
    state, calls = {}, []

    def row(block):
        return {'block_number': block, 'timestamp': '2026-09-01T00:00:00Z',
                'hash': str(block), 'from': {'hash': '0x1'}, 'to': {'hash': '0x2'}, 'value': '1'}

    pages = {None: {'items': [row(30)], 'next_page_params': {'block_number': 30}},
             30: {'items': [row(20)], 'next_page_params': {'block_number': 20}},
             20: {'items': [row(10)], 'next_page_params': None}}

    def get(url, params):
        calls.append(params.copy())
        return pages[params.get('block_number')]

    first, _ = fetch_kind('0x1', chain, 'native', 0, CallBudget(20, 10),
                          max_pages=1, get=get, continuation=state)
    assert first.truncated and first.last_block == 0
    assert state['next_page_params'] == {'block_number': 30}
    pages[None] = {'items': [row(40)], 'next_page_params': {'block_number': 30}}
    second, _ = fetch_kind('0x1', chain, 'native', 0, CallBudget(20, 10),
                           max_pages=2, get=get, continuation=state)
    assert {r['hash'] for r in second.rows} == {'40', '20'}
    assert second.last_block == 0 and state['next_page_params'] == {'block_number': 20}
    third, error = fetch_kind('0x1', chain, 'native', 0, CallBudget(20, 10),
                              max_pages=1, get=get, continuation=state)
    assert error is None and not third.truncated
    assert third.last_block == 30 and not state  # 40 remains outside the committed watermark
    assert calls[-1] == {'block_number': 20}


def test_failed_backfill_page_keeps_its_resume_position():
    from src.chain.blockscout import fetch_kind
    from src.chain.budget import CallBudget
    state = {'start_block': 0, 'head_block': 30, 'next_page_params': {'block_number': 20}}
    def fail(*_):
        raise RuntimeError('temporary failure')
    walk, error = fetch_kind('x', {'name': 'base'}, 'native', 0, CallBudget(20, 10),
                             max_pages=1, get=fail, continuation=state)
    assert error and walk.truncated and walk.last_block == 0
    assert state['next_page_params'] == {'block_number': 20}


def test_collector_persists_resume_state_after_storing_transfers(tmp_path, monkeypatch):
    import json

    from src.chain import blockscout, collect
    monkeypatch.setattr(collect, 'TRANSFERS_DIR', tmp_path / 'transfers')
    monkeypatch.setattr(collect, 'SPAM_DIR', tmp_path / 'spam')
    monkeypatch.setattr(collect, 'CURSOR_PATH', tmp_path / 'cursors.json')
    monkeypatch.setattr(collect, 'KINDS', ('erc20',))
    monkeypatch.setattr(collect, 'newest_block', lambda *args: (10, None))
    def get(url, params):
        if params.get('block_number') == 10:
            return {'items': [row(5)], 'next_page_params': None}
        return {'items': [row(10)], 'next_page_params': {'block_number': 10}}
    monkeypatch.setattr(blockscout, '_get', get)
    chain = {'name': 'base', 'chain_id': 8453, 'native': 'ETH', 'reader': 'blockscout'}
    first = collect.sweep_wallet(A, [chain], budget(), cluster=True, max_pages=1, dust_usd=0)
    assert first['chains']['base']['truncated']
    assert json.loads((tmp_path / 'cursors.json').read_text())[f'base:{A}:erc20:backfill']['next_page_params']
    second = collect.sweep_wallet(A, [chain], budget(), cluster=True, max_pages=1, dust_usd=0)
    assert not second['chains']['base']['truncated']
    assert json.loads((tmp_path / 'cursors.json').read_text())[f'base:{A}:erc20'] == 10
    rows = [r for path in collect.substrate_files(tmp_path / 'transfers/base') for r in collect.decode_records(path)]
    assert {r['block'] for r in rows} == {5, 10}
