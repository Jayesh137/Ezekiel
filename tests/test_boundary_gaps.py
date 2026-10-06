"""Spec §8: only money that crossed a custody gap is an exit; routes obey physics."""

from src.boundary import gaps, perimeter
from src.movements import reconcile_movements

T = "0x45d26f28196d226497130c4bac709d808fed4029"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
DEFI, HOT, PERSON, NEW = "0x" + "1" * 40, "0x" + "8" * 40, "0x" + "3" * 40, "0x" + "4" * 40
INDEX = perimeter.Index(perimeter.build(
    config={"target_wallet": T, "known_self_wallets": []}, sentinels={S: {}}, trace_report={},
    trace_registry={}, solana={}, associates_found={}, families={f"deposit:{S}": [HOT]},
    services=set(), previous=None, now_iso="x"))


def classify(addr, chain):
    return {DEFI: "contract", HOT: "busy", PERSON: "eoa"}.get(addr, "unmeasured")


def mv(dest, amount=1e6, source="l1_outbound", resolved=False, **kw):
    return {"source": source, "destination": dest, "immediate_destination": dest, "amount": amount,
            "ts": 100, "ref": "0xr", "id": f"id-{dest}", "route_resolved": resolved,
            "source_wallet": T, "chain": "arbitrum", **kw}


def test_only_custody_gap_exits_survive():
    out = gaps.exits([mv(DEFI), mv(S), mv(HOT), mv(PERSON), mv(NEW), mv(T),
                      mv(PERSON, resolved=True), mv(PERSON, amount=10)],
                     index=INDEX, classify_destination=classify, min_amount=100_000)
    kinds = {e["destination"]: e["gap"] for e in out}
    assert {e["source"] for e in out} == {"l1_outbound"}      # the movement kind is kept
    assert kinds == {S: gaps.EXCHANGE_DEPOSIT, HOT: gaps.EXCHANGE_DEPOSIT,
                     PERSON: gaps.PERSON, NEW: gaps.PERSON}


def test_hl_withdrawals_to_his_world_are_not_exits_and_unknown_ones_are():
    out = gaps.exits([mv(T, source="hl_withdraw"), mv(NEW, source="hl_withdraw"),
                      mv(None, source="hl_withdraw")],
                     index=INDEX, classify_destination=classify, min_amount=100_000)
    assert [e["gap"] for e in out] == [gaps.HL_WITHDRAWAL, gaps.HL_WITHDRAWAL_UNKNOWN]


def _record(sources, complete=True, unreadable=()):
    return {"complete": complete, "unreadable": list(unreadable), "sources": sources}


def test_an_exchange_exit_needs_an_exchange_on_the_route():
    match = {"exit_gap": gaps.EXCHANGE_DEPOSIT, "deposit_ts": 10_000, "gap_hours": 2}
    assert gaps.route_consistent(match, None) == (True, "route_unknown")
    assert gaps.route_consistent(match, _record([], complete=False)) == (True, "route_unknown")
    assert gaps.route_consistent(match, _record([], unreadable=[{"hop": 2}])) == (True, "route_unknown")
    fam = {"hop": 1, "class": "exchange", "family": ["deposit:x"], "last_ts": 9_000}
    assert gaps.route_consistent(match, _record([fam])) == (True, "same_exchange")
    busy = {"hop": 2, "class": "busy", "last_ts": 9_000}
    assert gaps.route_consistent(match, _record([busy])) == (True, "exchange")
    person = {"hop": 1, "class": "quiet", "last_ts": 9_000}
    assert gaps.route_consistent(match, _record([person])) == (False, "no_exchange_source")


def test_person_and_hl_exits_are_unconstrained():
    for kind in (gaps.PERSON, gaps.HL_WITHDRAWAL, "hl_cctp"):
        assert gaps.route_consistent({"exit_gap": kind}, _record([])) == (True, "unconstrained")


def test_a_withdrawal_resolves_exactly_by_nonce():
    ledger = [{"hash": "0xw", "time": 5_000, "delta": {"type": "withdraw", "usdc": "1000000.0",
                                                     "nonce": 42}}]
    plain = reconcile_movements([], ledger, {T}, {})
    assert plain["unresolved_exits"][0]["destination"] is None
    exact = reconcile_movements([], ledger, {T}, {}, withdrawal_destinations={
        "42": {"destination": T}})
    assert exact["movements"][0]["resolution"] == "withdrawal_to_cluster"
    assert exact["unresolved_exits"] == []


def test_correlation_findings_carry_the_exit_gap():
    from src.correlator import find_correlations
    exits = [{"amount": 1e6, "ts": 1_000, "source": "l1_outbound", "gap": gaps.EXCHANGE_DEPOSIT,
              "ref": "0xe", "id": "e"}]
    entries = [{"wallet": NEW, "amount": 1e6, "ts": 1_000 + 3600, "hash": "0xd"}]
    [f] = find_correlations(exits, entries, min_amount=100_000, min_confidence=0)
    assert f["exit_gap"] == gaps.EXCHANGE_DEPOSIT and f["exit_source"] == "l1_outbound"
