from src.route_index import index_routes

A = "0x" + "1" * 40
B = "0x" + "2" * 40
ROUTER = "0x" + "3" * 40


def test_exact_source_decode_finds_small_treasury_funded_hl_account():
    record = {"id": "fund", "chain": "base", "tx_hash": "tx", "src": A, "dst": ROUTER,
              "amount_usd": 20, "ts": 100}
    result = index_routes([record], {"base:tx": {"protocol": "cctp_extension", "hl_account": B,
                                               "recipient": ROUTER, "chain": "hyperevm"}}, [], {A})
    assert result["discoveries"][0]["wallet"] == B
    assert result["discoveries"][0]["parent_event_ids"] == ["fund"]
    assert result["routes"][0]["original_funder"] == A
    assert result["routes"][0]["assertion"] == "funding_instruction"


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
                "amount_usd": amount} for amount in (10, 20)]
    decodes = {"tx": {"protocol": "cctp"}}
    messages = [{"event_id": str(amount), "source_domain": 6, "source_tx_hash": "tx", "protocol": "cctp",
                 "protocol_message_id": f"6:{amount}", "hl_account": address, "amount_usd": amount}
                for amount, address in ((10, B), (20, ROUTER))]
    with DiscoveryStore(tmp_path / "db") as store:
        store.ingest_observations("cctp_sources", messages, 1000)
        enriched, decoded, _ = resolve_source_routes(records, decodes, store)
        result = index_routes(enriched, decoded, [], {A})
        by_id = {r["id"]: r for r in result["routes"]}
        assert by_id["r10"]["hl_account"] == B
        assert by_id["r20"]["hl_account"] == ROUTER
