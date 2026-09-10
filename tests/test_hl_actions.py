# tests/test_hl_actions.py
"""The explorer's action ledger: destinations, approvals, and what counts as
the account's own. Shapes captured live from the treasury on 2026-09-10."""

from src import hl_actions as ha

TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"

ROWS = [
    {"time": 1, "user": TR, "block": 1, "hash": "0x01", "error": None,
     "action": {"type": "order", "orders": [{"a": 1}], "grouping": "na"}},
    {"time": 2, "user": TR, "block": 2, "hash": "0x02", "error": None,
     "action": {"type": "sendAsset", "signatureChainId": "0xa4b1",
                "destination": T.upper(), "token": "HYPE:0x0d01", "amount": "2000"}},
    {"time": 3, "user": TR, "block": 3, "hash": "0x03", "error": None,
     "action": {"type": "approveAgent", "signatureChainId": "0x1",
                "agentAddress": "0xE4F36D8C44564183557DB6E8EEC78EC6E2D3C9BD", "agentName": None}},
    {"time": 4, "user": TR, "block": 4, "hash": "0x04", "error": None,
     "action": {"type": "withdraw3", "signatureChainId": "0x1",
                "destination": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "amount": "939039.02"}},
    {"time": 5, "user": TR, "block": 5, "hash": "0x05", "error": None,
     "action": {"type": "vaultTransfer", "vaultAddress": HLP, "isDeposit": True, "usd": 500000000000}},
    {"time": 6, "user": TR, "block": 6, "hash": "0x06", "error": None,
     "action": {"type": "vaultTransfer", "vaultAddress": "0xothervault", "isDeposit": False, "usd": 1}},
    # A validator attesting the treasury's deposit: mentions it, not its own act.
    {"time": 7, "user": "0xef2364db5db6f5539aa0bc111771a94ee47637fc", "block": 7, "hash": "0x07",
     "error": None, "action": {"type": "VoteEthDepositAction", "user": TR, "usd": 1}},
    # A stranger airdropping to it: not its own act either.
    {"time": 8, "user": "0x3d855cf5fff6eec436123d8f49e7afeac2b2a242", "block": 8, "hash": "0x08",
     "error": None, "action": {"type": "spotSend", "destination": TR, "token": "SENT:0x2c", "amount": "6000"}},
    {"time": 9, "user": TR, "block": 9, "hash": "0x09",
     "error": "Cannot switch leverage type with open position.",
     "action": {"type": "updateLeverage", "asset": 231, "isCross": True, "leverage": 3}},
    {"time": 10, "user": TR, "block": 10, "hash": "0x10", "error": "insufficient balance",
     "action": {"type": "usdSend", "destination": "0xfeedfeedfeedfeedfeedfeedfeedfeedfeedfeed", "amount": "1"}},
]


def test_own_actions_keep_the_accounts_non_trading_acts_only():
    acts = ha.own_actions(ROWS, TR.upper())
    kinds = [a["type"] for a in acts]
    assert kinds == ["sendAsset", "approveAgent", "withdraw3", "vaultTransfer",
                     "vaultTransfer", "usdSend"]
    by_hash = {a["hash"]: a for a in acts}
    assert by_hash["0x02"]["destination"] == T                 # lowercased
    assert by_hash["0x03"]["destination"] == "0xe4f36d8c44564183557db6e8eec78ec6e2d3c9bd"
    assert by_hash["0x03"]["signature_chain_id"] == "0x1"
    assert by_hash["0x04"]["amount"] == "939039.02"
    assert by_hash["0x05"]["amount"] == 500000000000
    assert by_hash["0x10"]["error"] == "insufficient balance"


def test_foreign_destinations_are_outside_the_cluster_and_not_infrastructure():
    acts = ha.own_actions(ROWS, TR)
    foreign = ha.foreign_destinations(acts, cluster={TR, T}, ignore={HLP})
    kinds = [(f["type"], f["destination"]) for f in foreign]
    # sendAsset to the target: cluster, not foreign. HLP deposit: ignored.
    # Withdrawal from another vault: money coming back, not leaving. Failed
    # usdSend: never happened. Left: the withdrawal and the agent approval.
    assert kinds == [("approveAgent", "0xe4f36d8c44564183557db6e8eec78ec6e2d3c9bd"),
                     ("withdraw3", "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef")]


def test_summary_counts_kinds_chain_ids_and_agents():
    acts = ha.own_actions(ROWS, TR)
    foreign = ha.foreign_destinations(acts, cluster={TR, T}, ignore={HLP})
    s = ha.summarise(TR, acts, foreign, None)
    assert s["read_ok"] is True
    assert s["kinds"] == {"sendAsset": 1, "approveAgent": 1, "withdraw3": 1,
                          "vaultTransfer": 2, "usdSend": 1}
    assert s["signature_chain_ids"] == {"0xa4b1": 1, "0x1": 2}
    assert s["agents_approved"] == ["0xe4f36d8c44564183557db6e8eec78ec6e2d3c9bd"]
    assert [f["type"] for f in s["foreign_destinations"]] == ["approveAgent", "withdraw3"]


def test_fetch_reports_failures_as_errors_not_empty():
    class R:
        status_code = 500

        def json(self):
            return {}

    rows, err = ha.fetch_actions(TR, post=lambda *a, **k: R())
    assert rows == [] and err == "HTTP 500"

    class OK:
        status_code = 200

        def json(self):
            return {"type": "userDetails", "txs": ROWS}

    rows, err = ha.fetch_actions(TR, post=lambda *a, **k: OK())
    assert err is None and len(rows) == len(ROWS)


def test_record_appends_once_per_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(ha, "ACTIONS_DIR", tmp_path / "actions")
    acts = ha.own_actions(ROWS, TR)
    assert ha.record(TR, acts) == 6
    assert ha.record(TR, acts) == 0
