# tests/test_transfer_graph.py
"""Deterministic tests for the transfer graph and linked-wallet discovery.

Covers every scenario the graph must get right, including the ones where the
correct answer is "this is NOT the same owner":

  direct transfer, multi-hop path, split amounts, repeated funding, an HL deposit
  shortly after an L1 transfer, exchange/bridge false-positive protection,
  duplicate event ingestion, a lone transfer that must not imply common ownership,
  and a fund-flow lead later strengthened by behavioural similarity.
"""

from src import transfer_graph as tg
from src.transfer_graph import (
    CLASS_DIRECT_RECIPIENT,
    CLASS_MIGRATION_CANDIDATE,
    CLASS_OPERATIONAL,
    CLASS_POSSIBLE_LINKED,
    CLASS_SERVICE,
    build_graph,
    classify_node,
    dedupe_edges,
    detect_services,
    find_split_correlation,
    normalise_gas_funding,
    normalise_hl_ledger_entry,
    normalise_l1_transfer,
    score_confidence,
    select_alerts,
)

T = "0x45d26f28196d226497130c4bac709d808fed4029"
W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"
W3 = "0x3333333333333333333333333333333333333333"
CEX = "0xcccccccccccccccccccccccccccccccccccccccc"
BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"

DAY = 86400
BASE_TS = 1_784_000_000


def l1(src, dst, usdc, ts, ref=None):
    """An Etherscan tokentx row (USDC, 6 decimals)."""
    return {
        "from": src, "to": dst, "value": str(int(usdc * 1_000_000)),
        "timeStamp": str(ts), "hash": ref or f"0xtx{src[-4:]}{dst[-4:]}{ts}",
        "tokenSymbol": "USDC",
    }


def hl_send(src, dst, usdc, ts_ms, ref=None, dtype="internalTransfer"):
    return {
        "time": ts_ms, "hash": ref or f"0xhl{src[-4:]}{dst[-4:]}{ts_ms}",
        "delta": {"type": dtype, "user": src, "destination": dst, "usdc": str(usdc)},
    }


def edges_from(*rows):
    out = []
    for r in rows:
        e = normalise_l1_transfer(r) if "value" in r else normalise_hl_ledger_entry(r)
        assert e is not None, f"failed to normalise {r}"
        out.append(e)
    return out


def node_for(graph, wallet):
    for n in graph["nodes"]:
        if n["wallet"] == wallet.lower():
            return n
    return None


def class_rank(node):
    """Ordinal strength of a node's classification, for upgrade assertions."""
    return tg.CLASS_ORDER[node["classification"]]


# --- 1. direct wallet transfer ---------------------------------------------------

def test_direct_transfer_is_discovered_as_recipient_only():
    """A single transfer makes a wallet a DIRECT_RECIPIENT — a lead, not an owner."""
    graph = build_graph(edges_from(l1(T, W1, 500_000, BASE_TS)), T)
    n = node_for(graph, W1)
    assert n is not None
    assert n["depth"] == 1
    assert n["classification"] == CLASS_DIRECT_RECIPIENT
    assert n["totals"]["received_from_target_usd"] == 500_000
    assert n["path"] == [T.lower(), W1.lower()]
    assert "USDC" in n["assets"]
    assert n["chains"] == ["arbitrum"]


def test_single_transfer_must_not_be_labelled_same_owner():
    """The rule that keeps this system honest: funds moving A->B is not identity."""
    graph = build_graph(edges_from(l1(T, W1, 2_500_000, BASE_TS)), T)
    n = node_for(graph, W1)
    assert n["classification"] not in (CLASS_MIGRATION_CANDIDATE, CLASS_POSSIBLE_LINKED)
    assert n["confidence"] < 0.40
    assert any("NOT treated as same owner" in r for r in n["confidence_reasons"])


# --- 2. multi-hop -----------------------------------------------------------------

def test_multi_hop_path_is_reconstructed_and_explained():
    graph = build_graph(edges_from(
        l1(T, W1, 900_000, BASE_TS),
        l1(W1, W2, 880_000, BASE_TS + 3600),
        l1(W2, W3, 850_000, BASE_TS + 7200),
    ), T)
    n3 = node_for(graph, W3)
    assert n3 is not None
    assert n3["depth"] == 3
    assert n3["multi_hop"] is True
    assert n3["path"] == [T.lower(), W1.lower(), W2.lower(), W3.lower()]
    assert any("hops from target" in r for r in n3["confidence_reasons"])
    # Confidence must decay with distance.
    assert n3["confidence"] <= node_for(graph, W1)["confidence"]


def test_depth_limit_is_respected():
    graph = build_graph(edges_from(
        l1(T, W1, 900_000, BASE_TS),
        l1(W1, W2, 880_000, BASE_TS + 1),
        l1(W2, W3, 870_000, BASE_TS + 2),
    ), T, max_depth=2)
    assert node_for(graph, W2) is not None
    assert node_for(graph, W3) is None
    assert graph["max_depth_reached"] <= 2


def test_node_budget_is_respected():
    rows = [l1(T, f"0x{i:040x}", 50_000, BASE_TS + i) for i in range(50)]
    graph = build_graph(edges_from(*rows), T, max_nodes=10)
    assert graph["node_count"] < 50


# --- 3. split transfers -----------------------------------------------------------

def test_split_transfer_amounts_are_correlated():
    """$1.2M out, re-entering as four ~$300k deposits, defeats single-amount matching."""
    inbound = [
        {"amount_usd": 300_000, "ref": "0xa"},
        {"amount_usd": 300_000, "ref": "0xb"},
        {"amount_usd": 300_000, "ref": "0xc"},
        {"amount_usd": 300_000, "ref": "0xd"},
    ]
    hit = find_split_correlation(1_200_000, inbound)
    assert hit is not None
    assert hit["parts"] == 4
    assert hit["total_usd"] == 1_200_000
    assert hit["confidence"] > 0.9


def test_split_correlation_rejects_unrelated_amounts():
    assert find_split_correlation(1_200_000, [{"amount_usd": 10, "ref": "0xa"}]) is None
    assert find_split_correlation(0, [{"amount_usd": 500, "ref": "0xa"}]) is None
    # A single exact part is not a "split" — needs >= 2 fragments.
    assert find_split_correlation(500_000, [{"amount_usd": 500_000, "ref": "0xa"}]) is None


# --- 4. repeated funding ----------------------------------------------------------

def test_repeated_funding_upgrades_to_operational():
    rows = [l1(T, W1, 120_000, BASE_TS + i * DAY, ref=f"0xr{i}") for i in range(4)]
    graph = build_graph(edges_from(*rows), T)
    n = node_for(graph, W1)
    assert n["classification"] == CLASS_OPERATIONAL
    assert n["evidence"]["transfer_count"] == 4
    assert any("Repeated transfers" in r for r in n["confidence_reasons"])


def test_bidirectional_flow_is_stronger_than_one_way():
    one_way = build_graph(edges_from(l1(T, W1, 500_000, BASE_TS)), T)
    two_way = build_graph(edges_from(
        l1(T, W2, 500_000, BASE_TS),
        l1(W2, T, 400_000, BASE_TS + DAY),
    ), T)
    assert node_for(two_way, W2)["confidence"] > node_for(one_way, W1)["confidence"]
    assert node_for(two_way, W2)["evidence"]["bidirectional"] is True


def test_gas_funding_relationship_is_recorded_as_evidence():
    edges = edges_from(l1(T, W1, 200_000, BASE_TS))
    edges.append(normalise_gas_funding(W1, T, BASE_TS - 60, amount_eth=0.01))
    graph = build_graph(edges, T)
    n = node_for(graph, W1)
    assert n["evidence"]["gas_funded_by_target"] is True
    assert any("First gas paid by the target" in r for r in n["confidence_reasons"])


# --- 5. HL deposit shortly after an L1 transfer ------------------------------------

def test_hl_native_transfer_is_captured_and_weighted():
    """HL-native movement never touches L1, so the tracer cannot see it."""
    graph = build_graph(edges_from(hl_send(T, W1, 750_000, BASE_TS * 1000)), T)
    n = node_for(graph, W1)
    assert n["chains"] == ["hyperliquid"]
    assert n["evidence"]["hl_native"] is True
    assert any("entirely inside Hyperliquid" in r for r in n["confidence_reasons"])


def test_l1_transfer_then_prompt_hl_trading_reaches_migration_candidate():
    """The scenario that matters most: funds leave the target, land on a fresh
    wallet, and that wallet promptly starts trading like the target."""
    edges = edges_from(
        l1(T, W1, 1_000_000, BASE_TS),
        l1(W1, BRIDGE, 990_000, BASE_TS + 3600),   # deposits to Hyperliquid
    )
    graph = build_graph(
        edges, T,
        known_services={BRIDGE},
        behavioural={W1: 0.82},
        hl_active={W1},
        correlations={W1: {"confidence": 0.9, "gap_hours": 1.0, "split": False}},
    )
    n = node_for(graph, W1)
    assert n["classification"] == CLASS_MIGRATION_CANDIDATE
    assert n["confidence"] >= 0.60
    joined = " ".join(n["confidence_reasons"])
    assert "Trades like the target" in joined
    assert "after a target exit" in joined


def test_bridge_events_do_not_become_wallet_nodes():
    """deposit/withdraw ledger rows have no counterparty wallet."""
    e = normalise_hl_ledger_entry(
        {"time": BASE_TS * 1000, "hash": "0xdep",
         "delta": {"type": "deposit", "usdc": "500000"}})
    assert e is not None and e["bridge_event"] is True
    graph = build_graph([e], T)
    assert graph["node_count"] == 0  # no phantom "deposit" wallet


# --- 6. exchange / bridge false-positive protection --------------------------------

def test_configured_service_address_can_never_be_linked():
    graph = build_graph(edges_from(
        l1(T, BRIDGE, 5_000_000, BASE_TS),
        l1(BRIDGE, T, 4_000_000, BASE_TS + DAY),
    ), T, known_services={BRIDGE})
    n = node_for(graph, BRIDGE)
    assert n["classification"] == CLASS_SERVICE
    assert n["confidence"] == 0.0
    assert any("Excluded" in r for r in n["confidence_reasons"])


def test_many_to_many_address_detected_as_service_without_config():
    """An exchange deposit address receives from many unrelated wallets and pays
    out to many more. It must be classified as infrastructure on behaviour alone."""
    rows = [l1(T, CEX, 400_000, BASE_TS)]
    for i in range(60):
        rows.append(l1(f"0x{i:040x}", CEX, 10_000, BASE_TS + i))
        rows.append(l1(CEX, f"0x{i + 500:040x}", 9_000, BASE_TS + 1000 + i))
    graph = build_graph(edges_from(*rows), T, max_nodes=500)
    assert node_for(graph, CEX)["classification"] == CLASS_SERVICE


def test_traversal_does_not_expand_through_a_service():
    """Everything is reachable via an exchange; hopping through one would make
    the graph meaningless. W2 is only reachable via CEX and must not appear."""
    rows = [l1(T, CEX, 900_000, BASE_TS), l1(CEX, W2, 880_000, BASE_TS + DAY)]
    graph = build_graph(edges_from(*rows), T, known_services={CEX})
    assert node_for(graph, CEX)["classification"] == CLASS_SERVICE
    assert node_for(graph, W2) is None


def test_high_fanin_alone_marks_a_service():
    svc = detect_services(
        edges_from(*[l1(f"0x{i:040x}", CEX, 1000, BASE_TS + i) for i in range(60)]),
        set())
    assert CEX.lower() in svc
    assert "fan-in" in svc[CEX.lower()]


# --- 7. duplicate event ingestion --------------------------------------------------

def test_duplicate_ingestion_is_idempotent():
    """Collectors re-read overlapping windows, so the same transfer arrives
    repeatedly. Counting it twice would inflate the repeated-transfer signal."""
    row = l1(T, W1, 500_000, BASE_TS, ref="0xsame")
    once = build_graph(edges_from(row), T)
    thrice = build_graph(edges_from(row, row, row), T)
    assert thrice["edge_count"] == once["edge_count"] == 1
    assert node_for(thrice, W1)["evidence"]["transfer_count"] == 1
    assert node_for(thrice, W1)["confidence"] == node_for(once, W1)["confidence"]


def test_dedupe_keeps_distinct_transfers_in_same_tx():
    """One tx hash can carry several token transfers between different pairs."""
    a = normalise_l1_transfer(l1(T, W1, 100_000, BASE_TS, ref="0xshared"))
    b = normalise_l1_transfer(l1(T, W2, 200_000, BASE_TS, ref="0xshared"))
    assert a["id"] != b["id"]
    assert len(dedupe_edges([a, b, a, b])) == 2


def test_dust_transfers_are_ignored():
    """Address-poisoning dust moves no funds and must not create relationships."""
    graph = build_graph(edges_from(l1(T, W1, 0.0, BASE_TS)), T)
    assert node_for(graph, W1) is None


# --- 8. behavioural strengthening of a fund-flow lead ------------------------------

def test_fund_flow_lead_strengthened_by_behavioural_similarity():
    """Same transfer evidence; adding behavioural similarity must upgrade both the
    confidence and the classification."""
    edges = edges_from(l1(T, W1, 800_000, BASE_TS))
    weak = build_graph(edges, T)
    strong = build_graph(edges, T, behavioural={W1: 0.88}, hl_active={W1},
                         linkage={W1: {"shared_deposit_addresses": [CEX]}})
    wn, sn = node_for(weak, W1), node_for(strong, W1)
    assert sn["confidence"] > wn["confidence"]
    assert class_rank(sn) > class_rank(wn)
    assert any("address reuse" in r for r in sn["confidence_reasons"])


def test_low_behavioural_score_does_not_upgrade():
    edges = edges_from(l1(T, W1, 800_000, BASE_TS))
    graph = build_graph(edges, T, behavioural={W1: 0.30})
    n = node_for(graph, W1)
    assert n["classification"] == CLASS_DIRECT_RECIPIENT
    assert any("weak on its own" in r for r in n["confidence_reasons"])


# --- 9. alert selection ------------------------------------------------------------

def test_new_wallet_and_upgrades_alert_but_unchanged_state_does_not():
    edges = edges_from(l1(T, W1, 900_000, BASE_TS))
    first = build_graph(edges, T, behavioural={W1: 0.85},
                        correlations={W1: {"confidence": 0.8, "gap_hours": 2.0}})
    assert len(select_alerts(first, None)) == 1          # new discovery
    assert select_alerts(first, first) == []             # unchanged -> silent

    # Behavioural evidence appears later -> confidence rises -> re-alert.
    weak = build_graph(edges, T)
    upgraded = select_alerts(first, weak)
    assert len(upgraded) == 1
    assert any("confidence rose" in r or "upgraded from" in r
               for r in upgraded[0]["trigger_reasons"])


def test_services_never_alert():
    graph = build_graph(edges_from(l1(T, BRIDGE, 9_000_000, BASE_TS)), T,
                        known_services={BRIDGE})
    assert select_alerts(graph, None) == []


def test_wallet_that_starts_trading_triggers_alert():
    edges = edges_from(l1(T, W1, 900_000, BASE_TS), l1(W1, T, 100_000, BASE_TS + DAY))
    before = build_graph(edges, T)
    after = build_graph(edges, T, hl_active={W1}, behavioural={W1: 0.70})
    triggers = select_alerts(after, before)
    assert triggers
    assert any("started trading" in r or "confidence rose" in r or "upgraded" in r
               for r in triggers[0]["trigger_reasons"])


# --- scoring guards ----------------------------------------------------------------

def test_confidence_is_bounded_and_service_short_circuits():
    everything = {
        "direct_from_target": True, "funded_target": True, "bidirectional": True,
        "transfer_count": 20, "hl_native": True, "shared_funder": True,
        "shared_deposit_address": True, "gas_funded_by_target": True,
        "reentry_gap_hours": 1.0, "amount_correlation": 1.0,
        "behavioural_score": 0.99, "trades_on_hl": True, "depth": 1,
    }
    score, _ = score_confidence(everything)
    assert 0.0 <= score <= 1.0
    assert score > 0.9

    svc, reasons = score_confidence({**everything, "is_service": True,
                                     "service_reason": "bridge"})
    assert svc == 0.0
    assert classify_node({**everything, "is_service": True}, svc) == CLASS_SERVICE


def test_empty_graph_is_safe():
    graph = build_graph([], T)
    assert graph["node_count"] == 0
    assert graph["edge_count"] == 0
    assert select_alerts(graph, None) == []
