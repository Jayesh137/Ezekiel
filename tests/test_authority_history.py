from src.authority_history import index_authority

A = "0x" + "1" * 40
B = "0x" + "2" * 40
OPERATOR = "0x" + "3" * 40


def event(account, kind, ts):
    return {"event_id": f"{account}:{kind}:{ts}", "account": account, "authority": OPERATOR,
            "kind": kind, "ts_ms": ts, "success": True}


def test_revoked_authority_does_not_overlap_later_account():
    result = index_authority([event(A, "approve_agent", 10), event(A, "revoke_agent", 20),
                              event(B, "approve_agent", 30)], as_of_ms=40)
    assert result["shared_authority"] == []
    assert result["intervals"][0]["valid_to_ms"] == 20


def test_shared_delegate_is_operational_relation_not_ownership():
    result = index_authority([event(A, "approve_agent", 10), event(B, "approve_agent", 15)], as_of_ms=40)
    assert result["shared_authority"][0]["assertion"] == "shared_operator"
    assert not result["shared_authority"][0]["confirms_owner"]


def test_future_revocation_and_failed_approval_do_not_leak_into_past():
    result = index_authority([event(A, "approve_agent", 10), event(A, "revoke_agent", 30),
                              {**event(B, "approve_agent", 15), "success": False}], as_of_ms=20)
    assert result["intervals"][0]["valid_to_ms"] is None
    assert len(result["intervals"]) == 1


def test_expiration_and_repeated_event_are_respected():
    approval = {**event(A, "approve_agent", 10), "valid_until_ms": 12}
    result = index_authority([approval, approval, event(B, "approve_agent", 15)], as_of_ms=20)
    assert len(result["intervals"]) == 2
    assert not result["shared_authority"]


def test_successful_snapshots_preserve_censored_authority_history():
    snapshots = [{"event_id": "s1", "kind": "agent_snapshot", "account": A, "ts_ms": 10,
                  "success": True, "agents": [{"address": OPERATOR}], "complete": True},
                 {"event_id": "s2", "kind": "agent_snapshot", "account": A, "ts_ms": 20,
                  "success": True, "agents": [], "complete": True}]
    result = index_authority(snapshots, as_of_ms=25)
    assert result["intervals"][0]["valid_from_ms"] == 10
    assert result["intervals"][0]["valid_to_ms"] == 20
    assert result["intervals"][0]["boundaries_observed_only"] is True


def test_double_normalisation_does_not_drop_agent_expiration():
    from src.agent_links import agent_index, normalise_agents
    rows = normalise_agents(normalise_agents([{"address": OPERATOR, "validUntil": 10}]))
    assert rows[0]["valid_until"] == 10
    assert agent_index({A: rows}, as_of_ms=20) == {}


def test_snapshot_renewal_preserves_extended_authority_interval():
    snapshots = [{'event_id': str(ts), 'kind': 'agent_snapshot', 'account': A, 'ts_ms': ts,
                  'success': True, 'complete': True, 'agents': [{'address': OPERATOR, 'validUntil': expiry}]}
                 for ts, expiry in [(10, 20), (15, 40)]]
    result = index_authority(snapshots + [event(B, 'approve_agent', 30)], as_of_ms=35)
    assert result['shared_authority']
    assert result['intervals'][1]['valid_to_ms'] == 40
