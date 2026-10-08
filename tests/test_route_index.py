from src.route_index import index_routes

A = "0x" + "1" * 40
B = "0x" + "56" * 20  # not 0x2222…2222 (HYPE system address) nor 0x5555…5555 (WHYPE)
ROUTER = "0x" + "3" * 40
TOKEN = "0x" + "4" * 40


def bound_record(record, decoded):
    from src.route_binding import transfer_binding
    record = {'token_address': TOKEN, 'amount': 20, **record}
    return {**record, 'route_decode': {**decoded, 'transfer_binding': transfer_binding(record)}}


def test_exact_source_decode_finds_small_treasury_funded_hl_account():
    record = {"id": "fund", "chain": "base", "tx_hash": "tx", "src": A, "dst": ROUTER,
              "amount_usd": 20, "ts": 100}
    record = bound_record(record, {'protocol': 'cctp_extension', 'hl_account': B})
    result = index_routes([record], {"base:tx": {"protocol": "cctp_extension", "hl_account": B,
                                               "recipient": ROUTER, "chain": "hyperevm"}}, [], {A})
    assert result["discoveries"][0]["wallet"] == B
    assert result["discoveries"][0]["parent_event_ids"] == ["fund"]
    assert result["routes"][0]["original_funder"] == A
    assert result["routes"][0]["assertion"] == "funding_instruction"


def test_route_index_output_reaches_successor_ranking_in_both_directions():
    from src.successor_hypotheses import find_successor_hypotheses
    records = [{"id": "fund", "chain": "base", "tx_hash": "tx", "src": A, "dst": ROUTER,
                "amount_usd": 20, "ts": 100},
               {"id": "return", "chain": "base", "tx_hash": "back", "src": B, "dst": A,
                "amount_usd": 5, "ts": 200}]
    records[0] = bound_record(records[0], {'protocol': 'cctp_extension', 'hl_account': B})
    index = index_routes(records, {"base:tx": {"protocol": "cctp_extension", "hl_account": B}}, [], {A})
    rows = find_successor_hypotheses({'wallet': A}, [{'wallet': B}],
                                    {'routes': index['routes'], 'as_of_ms': 300000})
    assert rows[0]['funding_routes'][0]['id'] == 'fund'
    assert rows[0]['return_routes'][0]['id'] == 'return'
    assert rows[0]['priority'] >= 7
    assert rows[0]['identity_confirmed'] is False


def test_shared_router_and_similar_amount_cannot_create_exact_route():
    result = index_routes([{"id": "fund", "chain": "base", "tx_hash": "source", "src": A,
                            "dst": ROUTER, "amount_usd": 1000}], {},
                         [{"event_id": "receive", "direction": "in", "counterparty": ROUTER,
                           "hl_account": B, "amount_usd": 1000, "tx_hash": "dest"}], {A})
    assert result["discoveries"] == []
    assert result["unresolved"][0]["next_query"]


def test_protocol_message_identity_joins_without_claiming_router_is_funder():
    source = {"id": "burn", "chain": "base", "tx_hash": "tx", "src": A, "dst": ROUTER,
              "amount_usd": 100, "protocol_message_id": "6:nonce", "protocol_id_verified": True}
    source = bound_record(source, {'protocol_message_id': '6:nonce', 'protocol_id_verified': True})
    dest = {"event_id": "mint", "direction": "in", "protocol_message_id": "6:nonce",
            "protocol_id_verified": True, "hl_account": B, "counterparty": ROUTER,
            "amount_usd": 99, "tx_hash": "receive"}
    result = index_routes([source], {}, [dest], {A})
    assert result["routes"][0]["original_funder"] == A
    assert result["routes"][0]["message_sender"] == ROUTER
    assert set(result["discoveries"][0]["parent_event_ids"]) == {"burn", "mint"}


def test_conflicting_recipients_stay_unresolved():
    source = {"id": "burn", "chain": "base", "tx_hash": "tx", "src": A,
              "dst": ROUTER, "protocol_message_id": "6:n", "protocol_id_verified": True}
    source = bound_record(source, {'protocol_message_id': '6:n', 'protocol_id_verified': True})
    dests = [{"event_id": w, "protocol_message_id": "6:n", "protocol_id_verified": True,
              "direction": "in", "hl_account": w} for w in (B, ROUTER)]
    result = index_routes([source], {}, dests, {A})
    assert not result["discoveries"]
    assert result["unresolved"][0]["reason"] == "conflicting_recipients"


def test_cctp_hook_preserves_zero_bytes_and_validates_framing():
    from src.chain.bridges import _hook_account
    recipient = "12" * 18 + "0000"
    prefix = b"cctp-forward".hex().ljust(48, "0") + "00000000" + "00000018"
    assert _hook_account("0x" + prefix + recipient + "00000000") == "0x" + recipient
    assert _hook_account("0x" + "ff" * 32 + recipient) is None
    assert _hook_account("0x" + prefix + recipient + "00000001" + "abcdef") == "0x" + recipient


def test_circle_retains_message_roles_ids_and_raw_token():
    from src import circle_flows
    from tests.test_circle_flows import RECEIVED
    row = circle_flows.decode(RECEIVED)
    assert row["protocol_message_id"] == "5:" + RECEIVED["topics"][2].lower()
    assert row["message_sender"] == row["counterparty"]
    assert row["original_funder"] is None
    assert row["burn_token"]


def test_generic_observation_store_keeps_noncluster_tiny_routes(tmp_path):
    from src.discovery_store import DiscoveryStore
    with DiscoveryStore(tmp_path / "db") as store:
        row = {"event_id": "event", "hl_account": B, "amount_usd": .5}
        store.ingest_observations("circle", [row], 1000)
        store.ingest_observations("circle", [row], 1000)
        assert store.observations("circle") == [row]


def test_source_message_decoder_verifies_transaction_domain_and_recipient():
    from src.circle_flows import _hex, _word
    from src.route_index import decode_source_messages
    from tests.test_circle_flows import RECEIVED
    raw = _hex(RECEIVED["data"])
    offset = int(_word(raw, 2), 16) * 2
    size = int(raw[offset:offset + 64], 16) * 2
    body = raw[offset + 64:offset + 64 + size]
    header = "00000001" + "00000005" + "00000013" + RECEIVED["topics"][2][2:] + "0" * (64 * 3 + 16)
    payload = {"sourceTxHash": "source", "messages": [{"message": "0x" + header + body, "status": "complete"}]}
    rows = decode_source_messages(payload, "source", 5)
    assert rows[0]["protocol_message_id"] == "5:" + RECEIVED["topics"][2]
    assert rows[0]["hl_account"] == "0xac14c0b3ceded821496a96bc0663fd3dc7a253ba"
    assert decode_source_messages(payload, "different", 5) == []
    assert decode_source_messages(payload, "source", 6) == []


def test_batched_burns_do_not_assign_last_decoded_recipient_to_every_transfer(tmp_path):
    from src.discovery_store import DiscoveryStore
    from src.route_index import resolve_source_routes
    records = [{"id": f"r{amount}", "chain": "base", "tx_hash": "tx", "src": A, "dst": ROUTER,
                "amount_usd": amount, "amount": amount, "token_address": TOKEN} for amount in (10, 20)]
    decodes = {"tx": {"protocol": "cctp"}}
    messages = [{"event_id": str(amount), "source_domain": 6, "source_tx_hash": "tx", "protocol": "cctp",
                 "protocol_message_id": f"6:{amount}", "hl_account": address, "amount_usd": amount,
                 "burn_token": "0x" + "0" * 24 + TOKEN[2:], "message_sender": A}
                for amount, address in ((10, B), (20, ROUTER))]
    with DiscoveryStore(tmp_path / "db") as store:
        store.ingest_observations("cctp_sources", messages, 1000)
        enriched, decoded, _ = resolve_source_routes(records, decodes, store)
        result = index_routes(enriched, decoded, [], {A})
        by_id = {r["id"]: r for r in result["routes"]}
        assert by_id["r10"]["hl_account"] == B
        assert by_id["r20"]["hl_account"] == ROUTER


def test_same_dollar_amount_on_another_token_or_sender_cannot_bind_a_burn(tmp_path):
    from src.discovery_store import DiscoveryStore
    from src.route_index import resolve_source_routes
    records = [{'id': key, 'chain': 'base', 'tx_hash': 'tx', 'src': A, 'dst': ROUTER,
                'token_address': token, 'amount': 100, 'amount_usd': 100}
               for key, token in [('right-token-wrong-sender', TOKEN), ('wrong-token', B)]]
    message = {'event_id': 'message', 'source_domain': 6, 'source_tx_hash': 'tx',
               'protocol': 'cctp', 'protocol_message_id': '6:1', 'amount_usd': 100,
               'burn_token': '0x' + '0' * 24 + TOKEN[2:], 'message_sender': B, 'hl_account': B}
    with DiscoveryStore(tmp_path / 'db') as store:
        store.ingest_observations('cctp_sources', [message], 1000)
        enriched, decoded, _ = resolve_source_routes(records, {'tx': {'protocol': 'cctp', 'hl_account': B}}, store)
        report = index_routes(enriched, decoded, [], {A})
        assert not report['discoveries']
        assert len(report['unresolved']) == 2


def test_one_message_cannot_resolve_two_matching_transfer_legs(tmp_path):
    from src.discovery_store import DiscoveryStore
    from src.route_index import resolve_source_routes
    records = [{'id': str(i), 'chain': 'base', 'tx_hash': 'tx', 'src': A, 'dst': ROUTER,
                'token_address': TOKEN, 'amount': 100, 'amount_usd': 100} for i in range(2)]
    message = {'event_id': 'message', 'source_domain': 6, 'source_tx_hash': 'tx',
               'protocol': 'cctp', 'protocol_message_id': '6:1', 'amount_usd': 100,
               'burn_token': '0x' + '0' * 24 + TOKEN[2:], 'message_sender': A, 'hl_account': B}
    with DiscoveryStore(tmp_path / 'db') as store:
        store.ingest_observations('cctp_sources', [message], 1000)
        enriched, decoded, _ = resolve_source_routes(records, {'tx': {'protocol': 'cctp', 'hl_account': B}}, store)
        report = index_routes(enriched, decoded, [], {A})
        assert not report['discoveries']
        assert len(report['unresolved']) == 2


def test_transaction_decode_never_resolves_unbound_transfer_legs(tmp_path):
    from src.discovery_store import DiscoveryStore
    from src.movements import reconcile_movements
    from src.route_index import resolve_source_routes
    records = [{'id': str(amount), 'chain': 'base', 'tx_hash': 'batch', 'src': A,
                'dst': ROUTER, 'amount': amount, 'amount_usd': amount,
                'token_address': TOKEN, 'ts': 100} for amount in (1000, 500000)]
    decodes = {'batch': {'protocol': 'cctp_extension', 'hl_account': A}}
    with DiscoveryStore(tmp_path / 'db') as store:
        for fetch, budget in [(lambda *_: {}, 20), (None, 0)]:
            enriched, decoded, _ = resolve_source_routes(records, decodes, store,
                                                         fetch=fetch, max_queries=budget)
            assert len(index_routes(enriched, decoded, [], {A})['unresolved']) == 2
            result = reconcile_movements(enriched, [], {A}, decoded)
            assert len(result['unresolved_exits']) == 2


def test_bound_message_resolves_only_its_leg_in_both_consumers(tmp_path):
    from src.discovery_store import DiscoveryStore
    from src.movements import reconcile_movements
    from src.route_index import resolve_source_routes
    records = [{'id': str(amount), 'chain': 'base', 'tx_hash': 'batch', 'src': A,
                'dst': ROUTER, 'amount': amount, 'amount_usd': amount,
                'token_address': TOKEN, 'ts': 100} for amount in (1000, 500000)]
    message = {'event_id': 'message', 'source_domain': 6, 'source_tx_hash': 'batch',
               'protocol': 'cctp', 'protocol_message_id': '6:1', 'amount_usd': 1000,
               'burn_token': '0x' + '0' * 24 + TOKEN[2:], 'message_sender': A, 'hl_account': B}
    with DiscoveryStore(tmp_path / 'db') as store:
        store.ingest_observations('cctp_sources', [message], 1000)
        enriched, decoded, _ = resolve_source_routes(records, {'batch': {'protocol': 'cctp'}}, store,
                                                     max_queries=0)
    routes = index_routes(enriched, decoded, [], {A})
    movements = reconcile_movements(enriched, [], {A}, decoded)
    assert [r['id'] for r in routes['routes']] == ['1000']
    assert [r['id'] for r in movements['resolved']] == ['1000']
    assert [r['id'] for r in movements['unresolved_exits']] == ['500000']
    # Reusing a bound decode on another transfer/chain must fail closed.
    changed = {**enriched[0], 'chain': 'arbitrum'}
    assert not reconcile_movements([changed], [], {A}, decoded)['resolved']


def test_duplicate_copies_of_one_transfer_do_not_make_binding_ambiguous(tmp_path):
    from src.discovery_store import DiscoveryStore
    from src.route_index import resolve_source_routes
    record = {'id': 'one', 'chain': 'base', 'tx_hash': 'batch', 'src': A, 'dst': ROUTER,
              'amount': 1000, 'amount_usd': 1000, 'token_address': TOKEN, 'ts': 100}
    message = {'event_id': 'message', 'source_domain': 6, 'source_tx_hash': 'batch',
               'protocol': 'cctp', 'protocol_message_id': '6:1', 'amount_usd': 1000,
               'burn_token': '0x' + '0' * 24 + TOKEN[2:], 'message_sender': A, 'hl_account': B}
    with DiscoveryStore(tmp_path / 'db') as store:
        store.ingest_observations('cctp_sources', [message], 1000)
        enriched, decoded, _ = resolve_source_routes([record, record], {'batch': {'protocol': 'cctp'}},
                                                     store, max_queries=0)
    assert len(index_routes(enriched, decoded, [], {A})['routes']) == 1


def test_unavailable_old_source_message_does_not_starve_other_transactions(tmp_path):
    from src.discovery_store import DiscoveryStore
    from src.route_index import resolve_source_routes
    records = [{'id': tx, 'chain': 'base', 'tx_hash': tx, 'ts': 100} for tx in ('a', 'b')]
    decodes = {tx: {'protocol': 'cctp'} for tx in ('a', 'b')}
    calls = []
    def unavailable(domain, tx):
        calls.append(tx)
        return {}
    with DiscoveryStore(tmp_path / 'db') as store:
        for _ in range(2):
            resolve_source_routes(records, decodes, store, fetch=unavailable, max_queries=1)
    assert set(calls) == {'a', 'b'}


def test_protocol_join_cannot_reuse_a_binding_from_another_transfer():
    record = bound_record({'id': 'burn', 'chain': 'base', 'tx_hash': 'tx', 'src': A, 'dst': ROUTER,
                           'protocol_message_id': '6:n', 'protocol_id_verified': True},
                          {'protocol_message_id': '6:n', 'protocol_id_verified': True})
    record['amount'] = 100000
    received = {'protocol_message_id': '6:n', 'protocol_id_verified': True, 'direction': 'in', 'hl_account': B}
    report = index_routes([record], {}, [received], {A})
    assert not report['routes'] and len(report['unresolved']) == 1


def test_mints_and_system_contracts_never_become_funding_route_candidates():
    from src.route_index import index_routes
    T = "0x45d26f28196d226497130c4bac709d808fed4029"
    recs = [{"id": f"r{i}", "src": src, "dst": T, "amount_usd": 1e6, "ts": 1, "chain": "polygon",
             "tx_hash": f"0x{i}"} for i, src in enumerate((
                 "0x0000000000000000000000000000000000000000",
                 "0x0000000000000000000000000000000000001010",
                 "0x2000000000000000000000000000000000000000"))]
    out = index_routes(recs, {}, [], {T})
    assert out["discoveries"] == []


def test_a_token_contract_never_becomes_a_funding_route_candidate():
    from src.route_index import index_routes
    T = "0x45d26f28196d226497130c4bac709d808fed4029"
    recs = [{"id": "r1", "src": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "dst": T,
             "amount_usd": 1e6, "ts": 1, "chain": "base", "tx_hash": "0x1"}]
    out = index_routes(recs, {}, [], {T})
    assert out["discoveries"] == []
