# tests/test_trace_engine.py
"""Acceptance: from the config cluster alone, the engine finds what was found by
hand on 2026-10-03/04 (docs/incident-log.md) — and the pipeline never had.

The world below is built from the real transfers (amounts and gaps as observed;
HL ledgers are the live rows in fixtures/trace/hl_ledgers.json). Only IO is
faked: what the substrate holds appears only once the engine sweeps a wallet,
exactly as `records_for` behaves on an unswept address (rule 1).
"""

import json
from pathlib import Path

import pytest

from src.trace import engine

LEDGERS = json.loads((Path(__file__).parent / "fixtures" / "trace" / "hl_ledgers.json").read_text())

F07 = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
BINANCE14 = "0x28c6c06298d514db089934071355e5743bf21d60"
BINANCE16 = "0xdfd5293d8e347dfe59e90efd55b2956a1343963d"
DEPOSIT_841 = "0x841b9e4f5b5edf84e71da3aae2db8d4b3fb2eed4"
FRESH_687 = "0x68797748dd0151819841df908adb93891997b711"
PAYEE_734 = "0x734c92135eb462b8ba5a5edfe4000c5d7f6a6ec4"
COFUNDER_793 = "0x793a3e8a1f0e19719bfa39288436958bcd475dc4"
APOLLOX = "0x604dd02d620633ae427888d41bfd15e38483736e"
HL_DEP = "0x4aecac3b90dd0ad50d274c19221874e5ba8a4d45"
HL_HUB = "0x1f6093d33db935b2ebd81d23312da5f11759973e"
HL_TRADER = "0x" + "7" * 40
NOW = 1_790_000_000
T23 = 1_692_360_000   # 2023-08-18
T25 = 1_758_362_000   # 2025-09-20


_IDS = iter(range(1, 10_000))


def rec(src, dst, usd, ts, chain="ethereum"):
    return {"id": f"{chain}:0x{next(_IDS):x}:erc20:0", "src": src, "dst": dst, "amount_usd": usd,
            "ts": ts, "chain": chain, "asset": "USDT", "spam": False}


RECORDS = [
    # 0x841b9e4f: his Binance deposit address, emptied to Binance 14 in minutes.
    rec(F07, DEPOSIT_841, 3_945_000, T23), rec(DEPOSIT_841, BINANCE14, 3_950_000, T23 + 960),
    rec(F07, DEPOSIT_841, 8_008_498, T23 + 7 * 86400), rec(DEPOSIT_841, BINANCE14, 8_008_498, T23 + 7 * 86400 + 300),
    # 0x68797748: Binance 16 -> fresh wallet -> him.
    rec(BINANCE16, FRESH_687, 2_999_993.55, T23 - 36 * 86400), rec(BINANCE16, FRESH_687, 2_999_993.55, T23 - 36 * 86400 + 360),
    rec(FRESH_687, F07, 5_999_987.10, T23 - 1000),
    # 0x734c9213: co-funded by 0x793a3e8a and him within two hours, into ApolloX.
    rec(COFUNDER_793, PAYEE_734, 2_000_000, T25), rec(COFUNDER_793, PAYEE_734, 1_500_000, T25 + 2900),
    rec(F07, PAYEE_734, 2_000_000, T25 + 6500), rec(PAYEE_734, APOLLOX, 5_500_000, T25 + 6600),
    # An HL account his money reaches through one quiet hop on Arbitrum.
    rec(F07, HL_TRADER, 250_000, T25, chain="arbitrum"),
]


class World:
    def __init__(self, failing_hl=()):
        self.swept = set()
        self.hl_reads = []
        self.failing_hl = set(failing_hl)
        self.activity_reads = 0
        self.readings = {
            DEPOSIT_841: {"is_contract": False, "txs": 12, "token_transfers": 18},
            FRESH_687: {"is_contract": False, "txs": 3, "token_transfers": 7},
            PAYEE_734: {"is_contract": False, "txs": 5, "token_transfers": 8},
            COFUNDER_793: {"is_contract": False, "txs": 161, "token_transfers": 372},
            HL_TRADER: {"is_contract": False, "txs": 40, "token_transfers": 60},
            APOLLOX: {"is_contract": True, "txs": 217_964, "token_transfers": 159_852},
            BINANCE14: {"is_contract": False, "txs": 9_000_000, "token_transfers": 9_000_000},
            BINANCE16: {"is_contract": False, "txs": 9_000_000, "token_transfers": 9_000_000},
        }
        self.measured = {}

    # substrate: a wallet's records exist once it (or a cluster wallet) was swept
    def l1_records_for(self, addresses):
        return {a: [r for r in RECORDS if a in (r["src"], r["dst"])] for a in addresses
                if a == F07 or a in self.swept}

    def sweep(self, address):
        self.swept.add(address)
        return {"chains": {"ethereum": {}, "arbitrum": {}}, "degraded_sources": [],
                "unsupported_sources": ["bsc"]}

    def hl_post(self, body):
        user = body["user"]
        self.hl_reads.append(user)
        if user in self.failing_hl:
            raise RuntimeError("info endpoint unavailable: rate limited")
        if user == HL_TRADER:
            rows = [{"time": T25 * 1000 + 5, "hash": "0xd", "delta": {"type": "deposit", "usdc": "250000"}}]
        else:
            rows = LEDGERS.get(user, [])
        return [r for r in rows if int(r["time"]) >= int(body.get("startTime") or 0)]

    # chain.activity.ActivityCache surface
    def cached(self, address, chain):
        return self.measured.get(address)

    def get(self, address, chain):
        self.activity_reads += 1
        got = self.readings.get(address)
        if got is not None:
            self.measured[address] = got
        return got


def run(world, registry=None, hl_store=None, previous=None, **budgets):
    registry = {} if registry is None else registry
    hl_store = {} if hl_store is None else hl_store
    report = engine.run(cluster={F07}, registry=registry, hl_store=hl_store, previous=previous,
                        l1_records_for=world.l1_records_for, hl_post=world.hl_post,
                        activity=world, sweep=world.sweep, services={BINANCE14, BINANCE16},
                        inferred=set(), cex_hot={BINANCE14, BINANCE16},
                        budgets=budgets or None, now_ts=NOW)
    return report, registry, hl_store


@pytest.fixture
def first_run():
    world = World()
    return (world, *run(world))


def test_his_second_binance_deposit_address_is_found(first_run):
    _world, report, registry, _ = first_run
    found = {row["address"]: row for row in report["deposit_addresses"]}
    assert found[DEPOSIT_841]["kind"] == engine.CEX_DEPOSIT
    assert registry[DEPOSIT_841]["class"] == engine.CEX_DEPOSIT


def test_his_deposit_address_inside_hyperliquid_is_found_with_its_hub(first_run):
    _world, report, registry, hl_store = first_run
    found = {row["address"]: row for row in report["deposit_addresses"]}
    assert found[HL_DEP] == {"address": HL_DEP, "kind": engine.HL_DEPOSIT, "hub": HL_HUB}


def test_the_hub_behind_a_deposit_address_is_never_read_or_stored(first_run):
    # The deposit address is already a boundary, so its exchange's hub (504
    # counterparties, 2,000-row ledger) is never spent on.
    world, _report, _registry, hl_store = first_run
    assert HL_HUB not in world.hl_reads
    assert HL_HUB not in hl_store


def test_a_hub_reached_directly_is_recognised_and_not_stored():
    world = World()
    RECORDS.append(rec(F07, HL_HUB, 50_000, T25, chain="arbitrum"))
    try:
        _report, registry, hl_store = run(world)
    finally:
        RECORDS.pop()
    assert registry[HL_HUB]["hl"]["hub"] is True
    assert registry[HL_HUB]["class"] == engine.HUB
    assert HL_HUB not in hl_store


def test_the_co_funder_of_a_quiet_wallet_is_linked(first_run):
    _world, report, _registry, _ = first_run
    links = {(row["kind"], row["wallet"], row["via"]) for row in report["links"]}
    assert ("shared_payee", COFUNDER_793, PAYEE_734) in links


def test_a_fresh_wallet_that_funded_him_from_binance_is_surfaced(first_run):
    _world, report, _registry, _ = first_run
    row = next(r for r in report["funders"] if r["address"] == FRESH_687)
    assert row["class"] == engine.QUIET and row["swept"] is True
    assert {"address": BINANCE16, "class": engine.SERVICE} in row["funded_by"]


def test_an_hl_account_his_money_reaches_is_reported(first_run):
    _world, report, _registry, _ = first_run
    reached = {row["address"]: row for row in report["reached_hl_accounts"]}
    assert reached[HL_TRADER]["in_usd"] == 250_000
    assert reached[HL_TRADER]["parents"] == [F07]


def test_a_contract_and_an_exchange_are_never_swept(first_run):
    world, _report, registry, _ = first_run
    assert APOLLOX not in world.swept and BINANCE14 not in world.swept
    assert registry[APOLLOX]["class"] == engine.CONTRACT


def test_a_failed_hl_read_is_recorded_unreadable_and_blocks_nothing_else():
    world = World(failing_hl={F07})
    report, registry, hl_store = run(world)
    assert registry[F07]["hl"]["read_ok"] is False
    assert "rate limited" in registry[F07]["hl"]["error"]
    assert F07 not in hl_store                       # unreadable, not empty
    assert any(e["unit"] == "hl" and e["address"] == F07 for e in report["errors"])
    # L1 units carried on regardless.
    assert DEPOSIT_841 in world.swept and PAYEE_734 in world.swept


def test_a_second_run_announces_nothing_it_already_reported():
    world = World()
    first, registry, hl_store = run(world)
    second, _, _ = run(world, registry=registry, hl_store=hl_store, previous=first)
    assert first["new_links"] and second["new_links"] == []
    assert first["new_reached"] and second["new_reached"] == []


def test_ledgers_are_read_incrementally_not_from_the_start_each_run():
    world = World()
    _first, registry, hl_store = run(world)
    cursor = registry[F07]["hl"]["cursor_ms"]
    assert cursor > 0
    rows_before = len(hl_store[F07])
    _second, _, _ = run(world, registry=registry, hl_store=hl_store, hl_refresh_s=0)
    assert len(hl_store[F07]) == rows_before          # nothing new, nothing duplicated


def test_budgets_bound_every_source():
    world = World()
    report, _registry, _ = run(world, hl_reads=1, classify_reads=1, l1_sweeps=1)
    assert report["units"] == {"hl": 1, "classify": 1, "l1": 1}
    assert len(world.swept) == 1 and world.activity_reads == 1


# --- the roster reads the engine's own file --------------------------------------

def test_the_engine_s_links_vote_linkage_and_reach_is_evidence_only(tmp_path, monkeypatch):
    from src import roster

    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    sharer = "0x" + "8" * 40
    (tmp_path / "trace").mkdir(parents=True)
    (tmp_path / "trace" / "latest.json").write_text(json.dumps({
        "links": [
            {"kind": "shared_payee", "wallet": COFUNDER_793, "via": PAYEE_734,
             "outsider_usd": 3_500_000, "cluster_usd": 2_000_000, "gap_hours": 1.0},
            {"kind": "shared_hl_deposit", "wallet": sharer, "via": HL_DEP, "hub": HL_HUB,
             "outsider_usd": 900.0, "cluster_usd": 24_429.03, "ts": T25},
        ],
        "reached_hl_accounts": [{"address": HL_TRADER, "in_usd": 250_000, "share": 1.0,
                                 "depth": 1, "parents": [F07], "hl_rows": 1}],
    }))
    rows = {r["wallet"]: r for r in roster.build_roster(
        {"target_wallet": "0x45d26f28196d226497130c4bac709d808fed4029",
         "known_self_wallets": [F07]})["wallets"]}
    assert rows[COFUNDER_793]["vectors"] == ["linkage"]
    assert rows[COFUNDER_793]["evidence"]["shared_quiet_payee"]["via"] == PAYEE_734
    assert rows[sharer]["vectors"] == ["linkage"]
    assert rows[sharer]["evidence"]["shared_hl_deposit_address"]["via"] == HL_DEP
    # Reached by his money is a route to check, not a vote (rule 8).
    assert rows[HL_TRADER]["vectors"] == []
    assert rows[HL_TRADER]["evidence"]["trace_reach"]["in_usd"] == 250_000


# --- found by the first production dry run (2026-10-04) ---------------------------

def test_a_contract_with_hyperliquid_rows_is_not_a_reached_account():
    # 0x5d3a1ff2 ($68M from 0xf078969e) holds HL airdrop rows; on Ethereum it is a
    # token contract. HL rows alone must never make an L1 address a person.
    token = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    world = World()
    RECORDS.append(rec(F07, token, 68_000_000, T25))
    LEDGERS[token] = [{"time": T25 * 1000, "hash": "0xa", "delta": {
        "type": "spotTransfer", "token": "USDE", "amount": "5", "usdcValue": "5",
        "user": "0x" + "6" * 40, "destination": token}}]
    try:
        report = engine.run(
            cluster={F07}, registry={}, hl_store={}, previous=None,
            l1_records_for=world.l1_records_for, hl_post=world.hl_post, activity=world,
            sweep=world.sweep, services=set(), inferred=set(), cex_hot={BINANCE14},
            code={f"ethereum:{token}": True}, now_ts=NOW)
    finally:
        RECORDS.pop()
        LEDGERS.pop(token)
    assert token not in {r["address"] for r in report["reached_hl_accounts"]}


def test_hl_rows_alone_do_not_make_an_l1_address_quiet():
    record = {"hl": {"read_ok": True, "rows": 3}}
    cls = engine.classify("0x" + "1" * 40, record, cluster=set(), services=set(),
                          inferred=set(), activity=[], l1_seen=True)
    assert cls == engine.UNKNOWN
    hl_only = engine.classify("0x" + "1" * 40, record, cluster=set(), services=set(),
                              inferred=set(), activity=[], l1_seen=False)
    assert hl_only == engine.QUIET


def test_a_wallet_swept_by_someone_else_is_read_from_the_substrate():
    # 0x841b9e4f swept earlier by the frontier: its forwards are already stored.
    world = World()
    world.swept.add(DEPOSIT_841)
    report = engine.run(
        cluster={F07}, registry={}, hl_store={}, previous=None,
        l1_records_for=world.l1_records_for, hl_post=world.hl_post, activity=world,
        sweep=lambda a: pytest.fail(f"swept {a} again"), services={BINANCE14, BINANCE16},
        inferred=set(), cex_hot={BINANCE14, BINANCE16}, already_swept={DEPOSIT_841},
        budgets={"classify_reads": 0}, now_ts=NOW)
    assert DEPOSIT_841 in {r["address"] for r in report["deposit_addresses"]}


# --- what reaches the operator -----------------------------------------------------

def test_new_findings_route_high_and_known_ones_stay_silent(monkeypatch):
    import scripts.run_trace_engine as runner
    from src import alerts

    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda subject, body, html_body=None:
                        sent.append(subject) or True)
    monkeypatch.setattr(alerts, "_cooldown_ok", lambda key, hours: True)
    monkeypatch.setattr(alerts, "write_cursor", lambda *a, **k: None)
    report = {
        "new_reached": [{"address": HL_TRADER, "in_usd": 250_000, "share": 1.0, "depth": 1,
                         "parents": [F07], "hl_rows": 3}],
        "new_links": [{"kind": "shared_hl_deposit", "wallet": "0x" + "8" * 40, "via": HL_DEP,
                       "hub": HL_HUB, "outsider_usd": 900.0, "ts": T25},
                      {"kind": "shared_payee", "wallet": COFUNDER_793, "via": PAYEE_734}],
    }
    assert runner.alert_new(report) == 2
    assert [alerts._severity_of(s) for s in sent] == ["HIGH", "HIGH"]
    assert all(alerts._severity_of(s) in alerts.ESCALATING_SEVERITIES for s in sent)
