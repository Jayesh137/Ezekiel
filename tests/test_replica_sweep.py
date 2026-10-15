"""His world's action history: user list, dedupe, per-wallet report, agent-owner check."""

from src import replica_sweep as rs

T = "0x" + "1" * 40
K = "0x" + "2" * 40
D = "0x" + "d" * 40      # a perimeter deposit address
OUT = "0x" + "e" * 40    # an outsider
AG = "0x" + "a" * 40     # an agent


def _row(user=T, kind="usdSend", block=None, index=0, status="ok", nonce=1_700_000_000_000, **payload):
    action = {"type": kind, **payload}
    block = nonce if block is None else block          # one block per action unless a test says otherwise
    return {"user": user, "type": kind, "action": action, "nonce": nonce, "vault_address": None,
            "status": status, "block": block, "index": index, "time": nonce}


def test_world_users_unions_every_source_and_keeps_only_hex_addresses():
    config = {"target_wallet": T.upper(), "known_self_wallets": [K],
              "watch_wallets": [{"address": "0x" + "3" * 40}, "0x" + "4" * 40]}
    perimeter = {"members": {D: {"role": "deposit"}, "2xm4bb8K": {"role": "identity"}}}
    roster = {"wallets": [{"wallet": "0x" + "5" * 40, "tier": "PROBABLE"},
                          {"wallet": "0x" + "6" * 40, "tier": "POSSIBLE"}]}
    users = rs.world_users(config, perimeter, roster)
    assert users == sorted({T, K, "0x" + "3" * 40, "0x" + "4" * 40, D, "0x" + "5" * 40})
    assert rs.world_users({"target_wallet": T}, None, None) == [T]


def test_dedupe_folds_the_explorer_and_replica_views_of_one_action():
    rows = rs.by_wallet([_row(kind="approveAgent", agentAddress=AG, nonce=5, agentName="bot-1")])[T]
    explorer_copy = [{**rows[0], "hash": "0xabc"}]
    assert len(rs.dedupe(rows + explorer_copy)) == 1
    # Different nonces in different blocks are different actions.
    other = rs.by_wallet([_row(kind="approveAgent", agentAddress=AG, nonce=6, index=1, block=101)])[T]
    assert len(rs.dedupe(rows + other)) == 2
    # A kind the explorer stores without a nonce folds with the replica row by block.
    explorer_l1 = {"type": "vaultTransfer", "action": {"type": "vaultTransfer", "usd": 5}, "block": 300,
                   "time": 7, "hash": "0xdef"}
    replica_l1 = rs.by_wallet([_row(kind="vaultTransfer", usd=5, nonce=9, block=300, index=2)])[T]
    assert len(rs.dedupe([explorer_l1] + replica_l1)) == 1


def test_wallet_report_collects_agents_builders_referrals_circle_and_outsiders():
    rows = [
        _row(kind="approveAgent", agentAddress=AG, agentName="bot-1", nonce=10, index=0,
             signatureChainId="0x66eee"),
        _row(kind="approveAgent", agentAddress=AG, agentName=None, nonce=20, index=1),
        _row(kind="approveBuilderFee", builder="0x" + "b" * 40, maxFeeRate="0.1%", nonce=30, index=2),
        _row(kind="setReferrer", code="GCR", nonce=40, index=3),
        _row(kind="sendToEvmWithData", destinationRecipient=OUT, destinationChainId=6, amount="1000",
             token="USDC", nonce=50, index=4),
        _row(kind="usdSend", destination=D, amount="5", nonce=60, index=5),         # perimeter: not outside
        _row(kind="usdSend", destination=K, amount="5", nonce=70, index=6),         # cluster: not outside
        _row(kind="withdraw3", destination=OUT, amount="99", nonce=80, index=7, status="err"),
    ]
    actions = rs.by_wallet(rows)[T]
    rep = rs.wallet_report(T, actions, {D}, {T, K})
    assert rep["actions"] == 8 and rep["kinds"]["approveAgent"] == 2 and rep["failed"] == {"withdraw3": 1}
    assert rep["agents"] == [{"address": AG, "name": "bot-1", "first": 10, "last": 20, "approvals": 2,
                              "signature_chain_id": "0x66eee"}]
    assert rep["builders"][0]["address"] == "0x" + "b" * 40 and rep["builders"][0]["max_fee_rate"] == "0.1%"
    assert rep["referrals"] == [{"type": "setReferrer", "code": "GCR", "time": 40}]
    assert rep["circle_recipients"] == {"base": [{"recipient": OUT, "amount": "1000", "token": "USDC",
                                                 "time": 50}]}
    # Outside his perimeter: the Circle recipient. The agent is his (reported above, not an
    # outsider); the failed withdrawal is an intended destination, never money that moved.
    assert {(r["type"], r["destination"]) for r in rep["outside_destinations"]} == {
        ("sendToEvmWithData", OUT)}
    assert rep["failed_destinations"] == [{"type": "withdraw3", "destination": OUT, "amount": "99",
                                           "time": 80, "chain": None}]


def test_outside_addresses_keeps_the_earliest_naming_row_and_only_hex():
    reports = {T: {"outside_destinations": [{"destination": OUT, "time": 9, "type": "usdSend"},
                                            {"destination": "9xSolana", "time": 1, "type": "x"}]},
               K: {"outside_destinations": [{"destination": OUT, "time": 3, "type": "spotSend"}]}}
    out = rs.outside_addresses(reports)
    assert list(out) == [OUT] and out[OUT]["time"] == 3 and out[OUT]["from"] == K


def test_agents_due_a_read_are_the_unread_and_the_stale_oldest_first():
    reports = {T: {"agents": [{"address": AG}, {"address": "0x" + "c" * 40}, {"address": "0x" + "f" * 40}]}}
    now = 10 * rs.AGENT_RECHECK_MS
    previous = {AG: {"checked_ms": now - 1},                       # fresh
                "0x" + "c" * 40: {"checked_ms": now - 2 * rs.AGENT_RECHECK_MS}}   # stale
    assert rs.agents_to_check(reports, previous, now) == [("0x" + "f" * 40, T), ("0x" + "c" * 40, T)]
    assert rs.agents_to_check(reports, previous, now, limit=1) == [("0x" + "f" * 40, T)]


def test_check_agent_owners_finds_a_reused_key_and_records_unreadable_as_unread():
    answers = {
        AG: {"ok": True, "data": {"role": "agent", "data": {"user": OUT.upper()}}},
        "0x" + "c" * 40: {"ok": True, "data": {"role": "agent", "data": {"user": T}}},
        "0x" + "f" * 40: {"ok": False, "data": None, "error": "timeout"},
        "0x" + "9" * 40: {"ok": True, "data": {"role": "missing"}},
    }
    pairs = [(a, T) for a in answers]
    readings, findings = rs.check_agent_owners(pairs, {T, K}, lambda body: answers[body["user"]], 5)
    assert [f["owner"] for f in findings] == [OUT] and findings[0]["approved_by"] == T
    assert readings[AG]["owner"] == OUT and readings["0x" + "c" * 40]["owner"] == T
    assert readings["0x" + "f" * 40]["read_ok"] is False and readings["0x" + "f" * 40]["error"] == "timeout"
    assert readings["0x" + "9" * 40]["role"] == "missing" and readings["0x" + "9" * 40]["owner"] is None


def test_build_report_totals_and_dataset_position():
    reports = {T: rs.wallet_report(T, rs.by_wallet([_row(kind="approveAgent", agentAddress=AG),
                                                    _row(kind="usdSend", destination=OUT, nonce=2)])[T],
                                   set(), {T})}
    doc = rs.build_report(reports, state={"swept_to": 900, "segments_done": [[1, 2]]}, head=1000,
                          users=[T], agent_readings={}, agent_findings=[], registered=[AG],
                          now_iso="now")
    assert doc["dataset"]["behind_blocks"] == 100 and doc["dataset"]["backfill_segments_done"] == 1
    assert doc["agents_total"] == 1 and doc["outside_total"] == 1
    assert doc["kinds_total"] == {"approveAgent": 1, "usdSend": 1}
