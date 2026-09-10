# tests/test_bridges.py
"""Bridge calldata names the destination. Shapes captured from Blockscout on
2026-09-10 for the target's own transactions."""

from src.chain import bridges as br

T = "0x45d26f28196d226497130c4bac709d808fed4029"
FORWARDER = "0xb21d281dedb17ae5b501f6aa8256fe38c4e45757"
SOL = "0x1d2473d6af069a24485d049ff6bf90622e37969f3af1cffc826264304eaff447"
HOOK = "0x636374702d666f7277617264" + "0" * 38 + "18" + T[2:] + "00000000"


def _tx(method, params, to_name="", raw=""):
    return {"to": {"name": to_name},
            "decoded_input": {"method_call": method,
                              "parameters": [{"name": k, "value": v} for k, v in params.items()]},
            "raw_input": raw}


def test_cctp_extension_names_the_hyperliquid_account_in_the_hook():
    tx = _tx("batchDepositForBurnWithAuth(...)", {
        "_receiveWithAuthorizationData": ["3500000000000", "1", "2", "0x39", "28", "0xba", "0x3b"],
        "_depositForBurnData": ["3500000000000", "19",
                                "0x000000000000000000000000" + FORWARDER[2:],
                                "0x000000000000000000000000" + FORWARDER[2:],
                                "200000", "1000", HOOK]})
    got = br.decode_transaction(tx, T)
    assert got["protocol"] == "cctp_extension"
    assert got["chain"] == "hyperevm" and got["domain"] == 19
    assert got["recipient"] == FORWARDER
    assert got["hl_account"] == T


def test_cctp_v2_to_ethereum_names_himself():
    tx = _tx("depositForBurn(...)", {
        "amount": "999999000000", "destinationDomain": "0",
        "mintRecipient": "0x000000000000000000000000" + T[2:],
        "burnToken": "0xaf88", "destinationCaller": "0x" + "0" * 64,
        "maxFee": "0", "minFinalityThreshold": "2000"})
    got = br.decode_transaction(tx, T)
    assert got == {"protocol": "cctp", "domain": 0, "chain": "ethereum",
                   "recipient": T, "hl_account": None}


def test_cctp_v1_to_solana_keeps_the_32_byte_recipient():
    tx = _tx("depositForBurn(...)", {
        "amount": "999999000000", "destinationDomain": "5",
        "mintRecipient": SOL, "burnToken": "0xaf88"})
    got = br.decode_transaction(tx, T)
    assert got["chain"] == "solana" and got["recipient"] == SOL


def test_socket_receiver_is_read_by_heuristic():
    raw = ("0x000001aa3ca7f5bc"
           + "000000000000000000000000000000000000000000000000000000e8d4a51000"
           + "0000000000000000000000000000000000000000000000000000000000002710"
           + "000000000000000000000000" + T[2:]
           + "000000000000000000000000af88d065e77c8cc2239327c5edb3a432268e5831")
    got = br.decode_transaction({"to": {"name": "SocketGateway"}, "raw_input": raw}, T,
                                token="0xaf88d065e77c8cc2239327c5edb3a432268e5831")
    assert got["protocol"] == "socket" and got["recipient"] == T and got["heuristic"]


def test_unknown_contract_decodes_to_nothing():
    assert br.decode_transaction({"to": {"name": "Something"}, "raw_input": "0x"}, T) is None


def test_decode_transfers_marks_foreign_and_caches_per_hash(tmp_path):
    cache = br.DecodeCache(tmp_path / "decodes.json")
    calls = []

    def fetch(h, chain):
        calls.append(h)
        if h == "0xself":
            return _tx("depositForBurn(...)", {"destinationDomain": "0",
                                               "mintRecipient": "0x" + "0" * 24 + T[2:]})
        if h == "0xother":
            return _tx("batchDepositForBurnWithAuth(...)", {
                "_depositForBurnData": ["1", "19", "0x" + "0" * 24 + FORWARDER[2:],
                                        "0x0", "0", "0",
                                        "0x636374702d666f7277617264" + "0" * 38 + "18"
                                        + "ab" * 20 + "00000000"]})
        return None

    recs = [
        {"src": T, "dst": "0xbridge", "tx_hash": "0xself", "chain": "arbitrum",
         "amount_usd": 5.0, "ts": 2},
        {"src": T, "dst": "0xbridge", "tx_hash": "0xself", "chain": "arbitrum",
         "amount_usd": 5.0, "ts": 2},                       # same tx, second row
        {"src": T, "dst": "0xbridge", "tx_hash": "0xother", "chain": "arbitrum",
         "amount_usd": 7.0, "ts": 1},
        {"src": T, "dst": "0xbridge", "tx_hash": "0xdead", "chain": "arbitrum",
         "amount_usd": 1.0, "ts": 0},
        {"src": "0xstranger", "dst": "0xbridge", "tx_hash": "0xnope", "chain": "arbitrum",
         "amount_usd": 1.0, "ts": 0},
    ]
    rows, spent = br.decode_transfers(recs, {"0xbridge"}, {T}, cache, fetch=fetch)
    assert spent == 3 and calls == ["0xself", "0xother", "0xdead"]
    by = {r["tx_hash"]: r for r in rows}
    assert by["0xself"]["foreign"] is False and by["0xself"]["destination_chain"] == "ethereum"
    assert by["0xother"]["foreign"] is True
    assert by["0xother"]["hl_account"] == "0x" + "ab" * 20
    assert by["0xdead"]["status"] == "unreadable"
    assert "0xnope" not in by
    # Second pass spends nothing: decodes are permanent.
    rows2, spent2 = br.decode_transfers(recs, {"0xbridge"}, {T},
                                        br.DecodeCache(tmp_path / "decodes.json"), fetch=fetch)
    assert spent2 == 1   # only the unreadable one is retried
    summary = br.summarise(rows)
    assert summary["decoded"] == 2 and summary["unreadable"] == 1
    assert [f["tx_hash"] for f in summary["foreign"]] == ["0xother"]


def test_lookup_budget_leaves_the_rest_pending(tmp_path):
    cache = br.DecodeCache(tmp_path / "d.json")
    recs = [{"src": T, "dst": "0xb", "tx_hash": f"0x{i}", "chain": "arbitrum",
             "amount_usd": 1.0, "ts": i} for i in range(3)]
    rows, spent = br.decode_transfers(recs, {"0xb"}, {T}, cache, fetch=lambda h, c: None,
                                      max_lookups=1)
    assert spent == 1
    assert sorted(r["status"] for r in rows) == ["pending", "pending", "unreadable"]
