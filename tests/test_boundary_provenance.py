"""Spec §7: an account's money traced back to its first custody boundary."""

import json
from pathlib import Path

from src.boundary import perimeter, provenance, readers, unit

FIX = Path(__file__).parent / "fixtures" / "boundary"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
DD53 = "0xdd53c5297309130ab5fe5623dc905752e3342b13"
EE7 = "0xee7ae85f2fe2239e27d9c1e23fffe168d63b4055"
D7A = "0xd7a827fbaf38c98e8336c5658e4bcbcd20a4fd2d"
QUIET, NEW = "0x" + "4" * 40, "0x" + "5" * 40

INDEX = perimeter.Index(perimeter.build(
    config={"target_wallet": T, "known_self_wallets": []}, sentinels={S: {"reason": "x"}},
    trace_report={}, trace_registry={}, solana={}, associates_found={},
    families={f"deposit:{S}": [EE7]}, services=set(), previous=None, now_iso="2026-10-06"))


def _fx(name):
    return json.loads((FIX / name).read_text())


def label_of(address, chain):
    return {D7A: "busy", QUIET: "quiet"}.get(address)


def inbound_from_fixture(chain, address, *, since_ts, until_ts):
    return [t for t in (readers._transfer(i, chain) for i in _fx("bs_inbound_dd53.json")["items"])
            if since_ts <= t["ts"] <= until_ts]


def no_gas(chain, address):
    return None


def boom(*a, **k):
    raise AssertionError("not expected")


def test_bridge2_deposits_are_routed_and_the_biggest_kept():
    entries = provenance.route_entries(_fx("hl_ledger_dd53.json"), DD53)
    assert entries and {e["route"] for e in entries} == {provenance.ROUTE_BRIDGE2}
    top = provenance.significant(entries)
    assert len(top) == 6 and top[0]["usd"] >= top[-1]["usd"] >= 10_000
    assert all(e["source"] == DD53 and e["source_chain"] == "arbitrum" for e in top)


def test_unit_credits_are_routed_to_their_bitcoin_source():
    acct = "0x458d583dbfe5b0a143bf09f45acf29f0022aab3b"
    evs = unit.events(_fx("unit_ops_btc_deposit.json"))
    entries = provenance.route_entries(_fx("hl_ledger_unit_btc.json"), acct, unit_events=evs)
    assert entries and all(e["route"] == provenance.ROUTE_UNIT for e in entries)
    assert entries[0]["source"].startswith("bc1p") and entries[0]["source_chain"] == "bitcoin"


def test_hl_sends_and_bridge2_deposits_are_told_apart():
    acct = "0xd47587702a91731dc1089b5db0932cf820151a91"
    entries = provenance.route_entries(_fx("hl_ledger_d475.json"), acct)
    routes = {e["route"] for e in entries}
    assert routes == {provenance.ROUTE_HL_SEND, provenance.ROUTE_BRIDGE2}
    senders = {e["source"] for e in entries if e["route"] == provenance.ROUTE_HL_SEND}
    assert senders == {"0xd048870caa5a3037f507583b4762a7598251a2fc"}


def test_an_account_funded_by_his_exchange_is_same_exchange_evidence_not_an_alert():
    rec = provenance.resolve(DD53, ledger=_fx("hl_ledger_dd53.json"), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=inbound_from_fixture,
                             read_first_gas=no_gas, read_ledger=boom, circle_source=boom,
                             now_ts=1_791_000_000)
    classes = {s["address"]: s["class"] for s in rec["sources"]}
    assert classes.get(EE7) == "exchange" and classes.get(D7A) == "busy"
    assert rec["verdict"] == "same_exchange" and provenance.findings(rec) == []


def _ledger_deposit(usd, ts_ms=1_790_000_000_000):
    return [{"time": ts_ms, "hash": "0xd", "delta": {"type": "deposit", "usdc": str(usd)}}]


def _inbound(table):
    def read(chain, address, *, since_ts, until_ts):
        return [{"from": a, "usd": u, "ts": until_ts - 60, "chain": chain, "from_is_contract": False,
                 "from_is_scam": False, "token": "0xusdc", "symbol": "USDC", "tx_hash": "0x1"}
                for a, u in table.get(address, [])]
    return read


def test_his_wallet_at_hop_one_is_critical_transfer():
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=_inbound({NEW: [(T, 2e6)]}),
                             read_first_gas=no_gas, read_ledger=boom, circle_source=boom,
                             now_ts=1_791_000_000)
    [f] = provenance.findings(rec)
    assert (f["severity"], f["vote"], f["hop"], f["member"]) == ("CRITICAL", "transfer", 1, T)
    assert rec["verdict"] == "touches_his_world"


def test_his_wallet_at_hop_two_is_high_evidence_only():
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of,
                             read_inbound=_inbound({NEW: [(QUIET, 2e6)], QUIET: [(T, 2e6)]}),
                             read_first_gas=no_gas, read_ledger=boom, circle_source=boom,
                             now_ts=1_791_000_000)
    [f] = provenance.findings(rec)
    assert (f["severity"], f["vote"], f["hop"], f["via"]) == ("HIGH", None, 2, QUIET)


def test_an_unreadable_hop_is_recorded_and_never_read_as_no_source():
    def down(chain, address, *, since_ts, until_ts):
        raise readers.ReadError("429")
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=down, read_first_gas=no_gas,
                             read_ledger=boom, circle_source=boom, now_ts=1_791_000_000)
    assert not rec["complete"] and rec["unreadable"] and rec["verdict"] == "unresolved"


def test_an_unresolved_circle_source_is_unreadable_too():
    ledger = [{"time": 1_790_000_000_000, "hash": "0xc", "delta": {
        "type": "send", "user": provenance.FORWARDER, "destination": NEW, "token": "USDC",
        "amount": "500000.0", "usdcValue": "500000.0"}}]
    rec = provenance.resolve(NEW, ledger=ledger, unit_events=[], index=INDEX, label_of=label_of,
                             read_inbound=boom, read_first_gas=no_gas, read_ledger=boom,
                             circle_source=lambda entry, account: None, now_ts=1_791_000_000)
    assert rec["entries"][0]["route"] == provenance.ROUTE_CIRCLE and not rec["complete"]


def test_dust_and_scam_senders_never_become_sources():
    rows = provenance.aggregate([
        {"from": "0xa", "usd": 0.00001, "ts": 1, "chain": "arbitrum", "from_is_scam": False},
        {"from": "0xb", "usd": 5e5, "ts": 2, "chain": "arbitrum", "from_is_scam": True},
        {"from": "0xc", "usd": 2e5, "ts": 3, "chain": "arbitrum", "from_is_scam": False},
        {"from": "0xc", "usd": None, "ts": 4, "chain": "arbitrum", "from_is_scam": False}])
    assert [(r["address"], r["usd"], r["unvalued"]) for r in rows] == [("0xc", 2e5, 1)]


def test_quiet_funders_shared_by_two_accounts_are_grouped():
    recs = {"0x1": {"sources": [{"hop": 1, "class": "quiet", "address": QUIET}]},
            "0x2": {"sources": [{"hop": 1, "class": "quiet", "address": QUIET}]},
            "0x3": {"sources": [{"hop": 1, "class": "busy", "address": D7A}]}}
    assert provenance.shared_funders(recs) == [{"funder": QUIET, "accounts": ["0x1", "0x2"]}]


def test_a_transient_failure_asks_for_a_retry_and_a_missing_reader_does_not():
    # A 429 or a spent budget is this run's problem: caching that record for the
    # 7-day TTL would hide the account for a week. A chain with no reader is
    # the same answer next run, and is kept.
    def throttled(chain, address, *, since_ts, until_ts):
        raise readers.ReadError("429")
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=throttled, read_first_gas=no_gas,
                             read_ledger=boom, circle_source=boom, now_ts=1_791_000_000)
    assert rec["retry"] is True

    def no_reader(chain, address, *, since_ts, until_ts):
        raise readers.NoReader("no keyless reader for hyperevm")
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=no_reader, read_first_gas=no_gas,
                             read_ledger=boom, circle_source=boom, now_ts=1_791_000_000)
    assert rec["retry"] is False and not rec["complete"]


def test_a_circle_sender_matches_his_solana_wallet_by_its_raw_form():
    from src.circle_flows import base58_to_hex
    sol = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"
    idx = perimeter.Index(perimeter.build(
        config={"target_wallet": T, "known_self_wallets": []}, sentinels={},
        trace_report={}, trace_registry={}, solana={sol: {"role": "cluster"}},
        associates_found={}, families={}, services=set(), previous=None, now_iso="2026-10-06"))
    ledger = [{"time": 1_790_000_000_000, "hash": "0xc", "delta": {
        "type": "send", "user": provenance.FORWARDER, "destination": NEW, "token": "USDC",
        "amount": "500000.0", "usdcValue": "500000.0"}}]
    raw = base58_to_hex(sol)
    rec = provenance.resolve(NEW, ledger=ledger, unit_events=[], index=idx, label_of=label_of,
                             read_inbound=boom, read_first_gas=no_gas, read_ledger=boom,
                             circle_source=lambda e, a: {"address": raw, "chain": "solana",
                                                         "raw": raw},
                             now_ts=1_791_000_000)
    [f] = provenance.findings(rec)
    assert (f["severity"], f["role"], f["member"]) == ("CRITICAL", "identity", sol)


def test_an_entry_whose_funder_was_not_found_is_unresolved_never_unrelated():
    # USDC that left an address arrived there from somewhere. Nothing in the
    # lookback (older, or inside an index hole - Blockscout's Arbitrum index is
    # missing 2026-09-22 21:41 to 09-24 20:07) is an unknown, not a clean "no".
    def nothing(chain, address, *, since_ts, until_ts):
        return []
    rec = provenance.resolve(NEW, ledger=_ledger_deposit(2e6), unit_events=[], index=INDEX,
                             label_of=label_of, read_inbound=nothing, read_first_gas=no_gas,
                             read_ledger=boom, circle_source=boom, now_ts=1_791_000_000)
    assert not rec["complete"] and rec["verdict"] == "unresolved" and rec["retry"] is False
