# tests/test_circle_flows.py
"""Circle's own events on HyperEVM name both ends of every transfer in and out
of Hyperliquid. The fixtures are two real logs read on 2026-09-17 from
MessageTransmitterV2 (`0x81d40f21…`): a Solana deposit crediting
`0xac14c0b3…`, and a withdrawal from `0x0a0758d9…` to a Solana address.
"""

import json

import scripts.check_circle_flows as script
from src import circle_flows as cf

RECEIVED = {
    "topics": [cf.TOPIC_MESSAGE_RECEIVED,
               "0x000000000000000000000000b21d281dedb17ae5b501f6aa8256fe38c4e45757",
               "0x329f6a48f58fbe337e7959949f62acffa31642855fa7669e2d776feeac1ae520",
               "0x00000000000000000000000000000000000000000000000000000000000003e8"],
    "data": ("0x0000000000000000000000000000000000000000000000000000000000000005"
             "a65fc81d0fefa8860cb3b83f089b0224be8a6687b7ae49f594c0b9b4d7e93893"
             "0000000000000000000000000000000000000000000000000000000000000060"
             "000000000000000000000000000000000000000000000000000000000000011c"
             "00000001c6fa7af3bedbad3a3d65f36aabc97431b1bbe4c2d2f6e0e47ca60203452f5d61"
             "000000000000000000000000b21d281dedb17ae5b501f6aa8256fe38c4e45757"
             "0000000000000000000000000000000000000000000000000000000006050f53"
             "3817d4e03809227c4afd1537482f91a9c2ee312f1fefb56351364d1274c8b935"
             "0000000000000000000000000000000000000000000000000000000000040ac3"
             "0000000000000000000000000000000000000000000000000000000000040ac3"
             "0000000000000000000000000000000000000000000000000000000002c0eac4"
             "636374702d666f72776172640000000000000000000000000000000000000018"
             "ac14c0b3ceded821496a96bc0663fd3dc7a253ba0000000000000000"),
    "transactionHash": "0x29838479fc56c67dfe4dd57c6cb63146a533f58d64e73f180d1c4551ad4a608a",
    "blockNumber": "0x2bf994c",
}

SENT = {
    "topics": [cf.TOPIC_MESSAGE_SENT],
    "data": ("0x0000000000000000000000000000000000000000000000000000000000000020"
             "0000000000000000000000000000000000000000000000000000000000000178"
             "000000010000001300000005"
             "0000000000000000000000000000000000000000000000000000000000000000"
             "00000000000000000000000028b5a0e9c621a5badaa536219b3a228c8168cf5d"
             "a65fc81d0fefa8860cb3b83f089b0224be8a6687b7ae49f594c0b9b4d7e93893"
             "0000000000000000000000000000000000000000000000000000000000000000"
             "000007d000000000"
             "00000001000000000000000000000000b88339cb7199b77e23db6e890353e22632ba630f"
             "ca98946d3f267e0fc1d1a844707ca3944af4a9ed69f8bbb912e5ead61dd92392"
             "000000000000000000000000000000000000000000000000000000000088909a"
             "0000000000000000000000000a0758d937d1059c356d4714e57f5df0239bce1a"
             "0000000000000000000000000000000000000000000000000000000000000000"
             "0000000000000000000000000000000000000000000000000000000000000000"
             "0000000000000000000000000000000000000000000000000000000000000000"),
    "transactionHash": "0x0526b5a6b75862cea4bd8e6a05dd09e7f541b66741e7b96f535c9a81b13d0b82",
    "blockNumber": "0x2bf998a",
}

HIS = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
SOLANA_HEX = "0x1d2473d6af069a24485d049ff6bf90622e37969f3af1cffc826264304eaff447"


def test_a_real_deposit_names_the_source_chain_sender_amount_and_account():
    row = cf.decode(RECEIVED)
    assert row["direction"] == "in" and row["chain"] == "solana" and row["domain"] == 5
    assert row["counterparty"] is None, "a Solana pubkey is not an EVM address"
    assert row["counterparty_raw"] == "0x3817d4e03809227c4afd1537482f91a9c2ee312f1fefb56351364d1274c8b935"
    assert row["hl_account"] == "0xac14c0b3ceded821496a96bc0663fd3dc7a253ba"
    assert row["amount_usd"] == 100.994899
    assert row["block"] == 0x2bf994c


def test_a_real_withdrawal_names_the_account_destination_and_amount():
    row = cf.decode(SENT)
    assert row["direction"] == "out" and row["chain"] == "solana"
    assert row["hl_account"] == "0x0a0758d937d1059c356d4714e57f5df0239bce1a"
    assert row["counterparty_raw"] == "0xca98946d3f267e0fc1d1a844707ca3944af4a9ed69f8bbb912e5ead61dd92392"
    assert row["amount_usd"] == 8.949914


def test_unrelated_or_malformed_logs_decode_to_nothing():
    assert cf.decode({"topics": ["0x" + "12" * 32], "data": "0x"}) is None
    assert cf.decode({"topics": [cf.TOPIC_MESSAGE_RECEIVED], "data": "0x00"}) is None
    assert cf.decode({"topics": [], "data": "0x"}) is None


def test_base58_matches_the_hex_his_solana_wallet_is_registered_under():
    assert cf.base58_to_hex("2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv") == SOLANA_HEX
    assert cf.base58_to_hex("not-base58!") is None


def _row(direction, counterparty=None, raw=None, account="0x" + "ac" * 20):
    return {"direction": direction, "chain": "monad", "counterparty": counterparty, "counterparty_raw": raw,
            "hl_account": account, "amount_usd": 1_000_000.0, "tx_hash": "0xabc", "block": 1}


def test_his_wallet_funding_an_outside_account_trips():
    assert cf.classify(_row("in", counterparty=HIS), {HIS}, set(), {T, HIS}) == cf.KIND_FUNDED_OUTSIDE
    assert cf.classify(_row("in", raw=SOLANA_HEX), {HIS}, {SOLANA_HEX}, {T}) == cf.KIND_FUNDED_OUTSIDE


def test_an_outside_account_paying_his_address_trips():
    assert cf.classify(_row("out", counterparty=HIS), {HIS}, set(), {T, HIS}) == cf.KIND_OUTSIDE_PAID_HIM


def test_his_account_withdrawing_to_an_outside_address_trips():
    assert cf.classify(_row("out", counterparty="0x" + "99" * 20, account=T), {HIS, T}, set(),
                       {T, HIS}) == cf.KIND_HIS_ACCOUNT_WITHDREW_OUTSIDE


def test_his_own_round_trip_and_strangers_do_not_trip():
    assert cf.classify(_row("in", counterparty=HIS, account=T), {HIS, T}, set(), {T, HIS}) is None
    assert cf.classify(_row("out", counterparty=HIS, account=T), {HIS, T}, set(), {T, HIS}) is None
    assert cf.classify(_row("in", counterparty="0x" + "77" * 20), {HIS}, set(), {T}) is None
    assert cf.classify(_row("in", counterparty=HIS, account=None), {HIS}, set(), {T}) is None


# --- the walkers -----------------------------------------------------------------

def test_etherscan_no_records_is_an_answer_and_a_failure_is_not():
    empty = script.etherscan_logs(1, 2, cf.TOPIC_MESSAGE_RECEIVED,
                                  get=lambda p, chain_id=None: {"status": "0", "message": "No records found", "result": []})
    assert empty == []
    try:
        script.etherscan_logs(1, 2, cf.TOPIC_MESSAGE_RECEIVED,
                              get=lambda p, chain_id=None: {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"})
    except script.RpcError:
        pass
    else:
        raise AssertionError("a failed read serialised as an empty range")


def test_etherscan_pages_until_a_short_page(monkeypatch):
    monkeypatch.setattr(script, "ETHERSCAN_PAGE", 2)
    pages = {1: [RECEIVED, RECEIVED], 2: [SENT]}
    got = script.etherscan_logs(1, 2, "x", get=lambda p, chain_id=None: {
        "status": "1", "message": "OK", "result": pages[p["page"]]})
    assert len(got) == 3


def test_a_failed_chunk_stops_the_walk_without_advancing_the_cursor(monkeypatch):
    monkeypatch.setattr(script, "ETHERSCAN_CHUNK_BLOCKS", 100)
    calls = []

    def get(params, chain_id=None):
        if params.get("action") == "eth_blockNumber":
            return {"result": hex(1_300)}
        calls.append(params["fromBlock"])
        if params["fromBlock"] >= 1_200:
            return {"status": "0", "message": "NOTOK", "result": "rate limit"}
        return {"status": "0", "message": "No records found", "result": []}

    rows, summary = script.walk_etherscan({"last_block": 999}, get=get)
    assert summary["last_block"] == 1_199, "the cursor moved past a range nobody read"
    assert summary["error"] and summary["source"] == "etherscan"


def test_the_rpc_walk_stops_on_a_failed_window(monkeypatch):
    def call(method, params):
        if method == "eth_blockNumber":
            return hex(5_000)
        if int(params[0]["fromBlock"], 16) >= 3_000:
            raise script.RpcError("rate limited")
        return [RECEIVED]

    rows, summary = script.walk({"last_block": 999}, call=call, sleep=lambda s: None)
    assert summary["last_block"] == 2_999 and len(rows) == 2 and summary["error"]


# --- main: alert once, retry the undelivered ------------------------------------------

def test_main_alerts_a_finding_once_and_retries_an_undelivered_one(monkeypatch):
    import src.alerts as alerts
    from src import utils

    monkeypatch.setattr(script, "load_config", lambda: {"target_wallet": T, "known_self_wallets": [HIS]})
    monkeypatch.setattr("scripts.check_deposit_sentinels.hl_state", lambda a, post=None: {"read_ok": False})
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    funded = _row("in", counterparty=HIS)
    rows = {"now": [funded]}
    monkeypatch.setattr(script, "walk", lambda previous: (list(rows["now"]), {
        "head_block": 10, "last_block": 10, "windows_read": 1, "lag_blocks": 0,
        "error": None, "source": "rpc"}))
    outcome = {"ok": False}
    sent = []
    monkeypatch.setattr(alerts, "alert_circle_flow",
                        lambda kind, row, state: sent.append(kind) or outcome["ok"])

    script.main()
    doc = json.loads((utils.DATA_DIR / "circle_flows" / "latest.json").read_text())
    assert sent == [cf.KIND_FUNDED_OUTSIDE] and doc["undelivered"], "an undelivered alert was dropped"

    rows["now"] = []
    outcome["ok"] = True
    script.main()
    assert sent == [cf.KIND_FUNDED_OUTSIDE] * 2, "the undelivered alert was not retried"
    doc = json.loads((utils.DATA_DIR / "circle_flows" / "latest.json").read_text())
    assert doc["undelivered"] == []

    rows["now"] = [funded]
    script.main()
    assert len(sent) == 2, "a delivered finding alerted again"


def test_the_alert_routes_as_critical(monkeypatch):
    import src.alerts as alerts
    captured = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: captured.append(subject) or True)
    assert alerts.alert_circle_flow(cf.KIND_OUTSIDE_PAID_HIM, _row("out", counterparty=HIS), None)
    assert alerts._severity_of(captured[0]) == "CRITICAL"


def test_the_roster_gives_a_circle_counterparty_a_transfer_vote(tmp_path, monkeypatch):
    import src.roster as roster
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "circle_flows").mkdir(parents=True)
    funded = {**_row("in", counterparty=HIS), "kind": cf.KIND_FUNDED_OUTSIDE}
    withdrew = {**_row("out", counterparty="0x" + "99" * 20, account=T),
                "kind": cf.KIND_HIS_ACCOUNT_WITHDREW_OUTSIDE}
    (tmp_path / "circle_flows" / "latest.json").write_text(json.dumps({"findings": [funded, withdrew]}))
    rows = {r["wallet"]: r for r in roster.build_roster({"target_wallet": T})["wallets"]}
    assert rows["0x" + "ac" * 20]["vectors"] == ["transfer"]
    assert rows["0x" + "ac" * 20]["evidence"]["circle_flows"][0]["chain"] == "monad"
    assert T not in rows


def test_a_walk_says_how_many_blocks_it_read():
    """Feed health needs the span: a long read with no transfers is blind, a short one is not."""
    def get(params, chain_id=None):
        if params.get("action") == "eth_blockNumber":
            return {"result": hex(1_300)}
        return {"status": "0", "message": "No records found", "result": []}

    _rows, summary = script.walk_etherscan({"last_block": 999}, get=get)
    assert summary["blocks_read"] == 301 and summary["last_block"] == 1_300

    def call(method, params):
        return hex(999) if method == "eth_blockNumber" else []

    _rows, summary = script.walk({"last_block": 999}, call=call, sleep=lambda s: None)
    assert summary["blocks_read"] == 0 and summary["windows_read"] == 0
