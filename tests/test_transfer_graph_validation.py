# tests/test_transfer_graph_validation.py
"""End-to-end validation of the transfer graph against the migration-detection goal.

Complements test_transfer_graph.py (unit-level classification rules) with:
  - the L1 expansion path exercised offline from recorded fixtures
  - double-counting, recency decay and service-strengthening guards
  - graph-health / observability assertions
"""

import json
import time
from pathlib import Path

import pytest

from src import transfer_graph as tg
from src.transfer_graph import (
    CLASS_DIRECT_RECIPIENT,
    CLASS_MIGRATION_CANDIDATE,
    CLASS_SERVICE,
    build_graph,
    edge_id,
    expand_frontier,
    normalise_l1_transfer,
    recency_factor,
)

FIXTURE = Path(__file__).parent / "fixtures" / "etherscan_l1.json"
DAY = 86400


@pytest.fixture
def l1():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def fake_etherscan(monkeypatch, l1):
    """Serve recorded Etherscan responses so the L1 path runs without a key."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    calls = []

    def _get_usdc_transfers(address, start_block=0):
        calls.append(address.lower())
        return l1["responses"].get(address.lower(), {}).get("result", [])

    monkeypatch.setattr("src.tracer.get_usdc_transfers", _get_usdc_transfers)
    return calls


def node_for(graph, wallet):
    return next((n for n in graph["nodes"] if n["wallet"] == wallet.lower()), None)


# --- L1 expansion path, offline -------------------------------------------------

def test_l1_expansion_runs_from_fixtures_and_reaches_depth_two(l1, fake_etherscan):
    """With a key present the graph must expand past depth 1. This is the CI
    behaviour, exercised here without touching the network."""
    target = l1["target"]
    seed = [normalise_l1_transfer(tx)
            for tx in l1["responses"][target]["result"]]

    edges, diag = expand_frontier(seed, target, tg.DEFAULTS)
    assert diag["status"] == "ok"
    assert diag["lookups"] >= 1
    assert diag["new_edges"] > 0
    assert diag["degraded_sources"] == []

    graph = build_graph(edges, target, known_services={l1["bridge"]},
                        expansion=diag)
    assert graph["max_depth_reached"] >= 2, "expansion did not reach a second hop"
    w2 = node_for(graph, "0x2222222222222222222222222222222222222222")
    assert w2 is not None and w2["depth"] == 2
    assert w2["path"][0] == target.lower()
    assert graph["health"]["expansion"]["status"] == "ok"


def test_missing_api_key_degrades_explicitly_not_silently(monkeypatch, l1):
    """A depth-1 graph must be distinguishable from 'we never looked'."""
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    target = l1["target"]
    seed = [normalise_l1_transfer(tx) for tx in l1["responses"][target]["result"]]

    edges, diag = expand_frontier(seed, target, tg.DEFAULTS)
    assert edges == seed
    assert diag["status"] == "skipped_no_api_key"
    assert "arbitrum_l1" in diag["degraded_sources"]

    graph = build_graph(edges, target, expansion=diag)
    assert graph["health"]["frontier_incomplete"] is True
    assert graph["health"]["degraded_sources"] == ["arbitrum_l1"]


def test_expansion_failure_keeps_partial_results(monkeypatch, l1):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    target = l1["target"]
    seed = [normalise_l1_transfer(tx) for tx in l1["responses"][target]["result"]]

    def boom(address, start_block=0):
        raise OSError("etherscan unreachable")

    monkeypatch.setattr("src.tracer.get_usdc_transfers", boom)
    edges, diag = expand_frontier(seed, target, tg.DEFAULTS)
    assert diag["status"] == "failed"
    assert diag["error"]
    assert len(edges) >= len(seed), "partial results must survive a failure"
    assert build_graph(edges, target, expansion=diag)["health"]["frontier_incomplete"]


def test_expansion_respects_lookup_budget(l1, fake_etherscan):
    target = l1["target"]
    seed = [normalise_l1_transfer(tx) for tx in l1["responses"][target]["result"]]
    budget = {**tg.DEFAULTS, "max_expansions": 1}
    _, diag = expand_frontier(seed, target, budget)
    assert diag["lookups"] <= 1
    assert diag["status"] in ("ok", "budget_exhausted")


# --- no double counting ----------------------------------------------------------

def test_same_transfer_from_two_sources_counts_once(l1):
    """One movement reaches this module via l1_transactions (block timeStamp) and
    via a fund_flows finding (detected_at). Keying the edge on the timestamp minted
    two ids and doubled transfer_count from a single transaction."""
    target = l1["target"]
    w = "0x1111111111111111111111111111111111111111"
    tx = l1["responses"][target]["result"][0]

    from_table = normalise_l1_transfer(tx)
    from_finding = dict(from_table)
    from_finding["ts"] = int(tx["timeStamp"]) + 9999          # different clock
    from_finding["id"] = edge_id(target, w, tg.CHAIN_ARBITRUM, tx["hash"])

    assert from_table["id"] == from_finding["id"], "same transfer must share an id"

    graph = build_graph([from_table, from_finding], target)
    n = node_for(graph, w)
    assert graph["edge_count"] == 1
    assert n["evidence"]["transfer_count"] == 1
    assert not any("Repeated transfers" in r for r in n["confidence_reasons"])


def test_distinct_transfers_in_one_tx_still_count_separately(l1):
    """Deduping must not collapse genuinely different transfers sharing a hash."""
    target = l1["target"]
    a = normalise_l1_transfer({"from": target, "to": "0x" + "1" * 40,
                               "value": "100000000", "timeStamp": "1784000000",
                               "hash": "0xshared", "tokenSymbol": "USDC"})
    b = normalise_l1_transfer({"from": target, "to": "0x" + "2" * 40,
                               "value": "200000000", "timeStamp": "1784000000",
                               "hash": "0xshared", "tokenSymbol": "USDC"})
    assert a["id"] != b["id"]
    assert build_graph([a, b], target)["edge_count"] == 2


# --- recency decay ---------------------------------------------------------------

def test_recency_factor_is_monotonic_and_floored():
    assert recency_factor(None) == 1.0
    assert recency_factor(0) == 1.0
    assert recency_factor(tg.RECENCY_FRESH_DAYS) == 1.0
    mid = recency_factor(tg.RECENCY_FRESH_DAYS + tg.RECENCY_DECAY_DAYS / 2)
    assert tg.RECENCY_FLOOR < mid < 1.0
    assert recency_factor(10_000) == tg.RECENCY_FLOOR
    prev = 1.1
    for d in (0, 30, 90, 180, 365, 730, 3650):
        f = recency_factor(d)
        assert f <= prev
        prev = f


def test_old_transfer_scores_below_a_recent_one(l1):
    target = l1["target"]
    w = "0x" + "1" * 40
    now = time.time()

    def graph_at(age_days, ref):
        e = normalise_l1_transfer({
            "from": target, "to": w, "value": "900000000",
            "timeStamp": str(int(now - age_days * DAY)),
            "hash": ref, "tokenSymbol": "USDC"})
        return build_graph([e], target, now_ts=now)

    recent = node_for(graph_at(2, "0xnew"), w)
    old = node_for(graph_at(3 * 365, "0xold"), w)
    assert old["confidence"] < recent["confidence"]
    assert any("aged to" in r for r in old["confidence_reasons"])
    assert old["confidence"] > 0, "an old link must fade, not vanish"


def test_behavioural_evidence_does_not_decay(l1):
    """Trading like the target is independent of when money last moved."""
    target = l1["target"]
    w = "0x" + "1" * 40
    now = time.time()
    e = normalise_l1_transfer({
        "from": target, "to": w, "value": "900000000",
        "timeStamp": str(int(now - 3 * 365 * DAY)),
        "hash": "0xold", "tokenSymbol": "USDC"})

    plain = node_for(build_graph([e], target, now_ts=now), w)
    with_behaviour = node_for(
        build_graph([e], target, now_ts=now, behavioural={w: 0.88}, hl_active={w}), w)
    assert with_behaviour["confidence"] > plain["confidence"] + 0.15


# --- services cannot strengthen ownership ----------------------------------------

def test_routing_through_a_service_does_not_strengthen_a_wallet(l1):
    """Volume flowing via an exchange must not raise anyone's confidence."""
    target = l1["target"]
    cex = "0xcccccccccccccccccccccccccccccccccccccccc"
    w = "0x" + "1" * 40
    direct = normalise_l1_transfer({"from": target, "to": w, "value": "900000000",
                                    "timeStamp": "1784000000", "hash": "0xd",
                                    "tokenSymbol": "USDC"})
    via_cex = [
        normalise_l1_transfer({"from": target, "to": cex, "value": "9000000000",
                               "timeStamp": "1784000001", "hash": "0xc1",
                               "tokenSymbol": "USDC"}),
        normalise_l1_transfer({"from": cex, "to": w, "value": "9000000000",
                               "timeStamp": "1784000002", "hash": "0xc2",
                               "tokenSymbol": "USDC"}),
    ]
    base = node_for(build_graph([direct], target, known_services={cex}), w)
    routed = node_for(build_graph([direct, *via_cex], target, known_services={cex}), w)
    assert routed["confidence"] <= base["confidence"] + 1e-9
    svc = node_for(build_graph([direct, *via_cex], target, known_services={cex}), cex)
    assert svc["classification"] == CLASS_SERVICE and svc["confidence"] == 0.0


# --- funded-then-trading is a lead, not proof ------------------------------------

def test_recipient_that_starts_trading_is_flagged_but_not_proof(l1):
    """"Received funds then began trading" must read as a lead until an
    independent vector corroborates it."""
    target = l1["target"]
    w = "0x" + "1" * 40
    e = normalise_l1_transfer({"from": target, "to": w, "value": "900000000",
                               "timeStamp": str(int(time.time() - 2 * DAY)),
                               "hash": "0xf", "tokenSymbol": "USDC"})

    trading_only = node_for(build_graph([e], target, hl_active={w}), w)
    assert trading_only["classification"] == CLASS_DIRECT_RECIPIENT
    assert trading_only["classification"] != CLASS_MIGRATION_CANDIDATE
    assert any("started trading" in r or "Actively trading" in r
               for r in trading_only["confidence_reasons"])

    corroborated = node_for(build_graph(
        [e], target, hl_active={w}, behavioural={w: 0.86},
        correlations={w: {"confidence": 0.9, "gap_hours": 3.0}}), w)
    assert corroborated["classification"] == CLASS_MIGRATION_CANDIDATE


# --- graph health / observability -------------------------------------------------

def test_graph_health_reports_budget_and_evidence_window(l1):
    target = l1["target"]
    edges = [normalise_l1_transfer(tx) for tx in l1["responses"][target]["result"]]
    edges += [normalise_l1_transfer(tx)
              for tx in l1["responses"]["0x1111111111111111111111111111111111111111"]["result"]]
    diag = {"status": "ok", "lookups": 2, "new_edges": 3, "degraded_sources": [],
            "completed_at": "2026-07-27T12:00:00+00:00"}
    h = build_graph(edges, target, expansion=diag, max_nodes=2)["health"]

    assert h["node_budget"] == 2
    assert h["node_budget_exhausted"] is True
    assert h["frontier_incomplete"] is True
    assert h["oldest_evidence"] is not None
    assert h["newest_evidence"] >= h["oldest_evidence"]
    assert "l1_transfer" in h["discovery_sources"]
    assert h["expansion"]["status"] == "ok"


def test_clean_full_exploration_reports_complete_frontier(l1):
    target = l1["target"]
    edges = [normalise_l1_transfer(l1["responses"][target]["result"][0])]
    diag = {"status": "ok", "lookups": 1, "new_edges": 0, "degraded_sources": []}
    h = build_graph(edges, target, expansion=diag, max_depth=3, max_nodes=100)["health"]
    assert h["frontier_incomplete"] is False
    assert h["depth_limited"] is False
    assert h["node_budget_exhausted"] is False


def test_volume_alone_never_promotes_a_wallet(l1):
    """The known linked wallet moves ~$79M in and ~$82M out across 46 transfers.
    That volume must NOT be what classifies it — the promotion has to come from an
    independent structural signal (here: two-way flow entirely inside Hyperliquid).
    """
    target = l1["target"]
    w = "0x" + "9" * 40
    now = time.time()
    ts = str(int(now - 5 * DAY))

    # Huge one-way volume across many transfers, and nothing else.
    one_way = [normalise_l1_transfer({
        "from": target, "to": w, "value": str(20_000_000 * 10**6),
        "timeStamp": ts, "hash": f"0xvol{i}", "tokenSymbol": "USDC"})
        for i in range(46)]
    n = node_for(build_graph(one_way, target, now_ts=now), w)
    assert n["totals"]["received_from_target_usd"] > 900_000_000
    assert n["evidence"]["transfer_count"] == 46
    assert n["classification"] != CLASS_MIGRATION_CANDIDATE, (
        "raw volume must not reach migration-candidate status")

    # Add the structural signal the real known-linked wallet has: two-way flow
    # that never touches L1.
    hl_out = tg.normalise_hl_ledger_entry({
        "time": int((now - 4 * DAY) * 1000), "hash": "0xhlout",
        "delta": {"type": "internalTransfer", "user": target,
                  "destination": w, "usdc": "5000000"}})
    hl_in = tg.normalise_hl_ledger_entry({
        "time": int((now - 3 * DAY) * 1000), "hash": "0xhlin",
        "delta": {"type": "internalTransfer", "user": w,
                  "destination": target, "usdc": "4000000"}})
    promoted = node_for(build_graph([*one_way, hl_out, hl_in], target, now_ts=now), w)
    assert promoted["evidence"]["bidirectional"] is True
    assert promoted["evidence"]["hl_native"] is True
    assert promoted["classification"] == CLASS_MIGRATION_CANDIDATE
    assert promoted["confidence"] > n["confidence"]
