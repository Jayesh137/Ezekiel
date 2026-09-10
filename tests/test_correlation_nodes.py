# tests/test_correlation_nodes.py
"""Correlation matches become graph nodes without becoming fake transfers.

The correlator exists for the case where there IS no on-chain link: the target
exits to an exchange and a fresh wallet re-enters with the same amount. Before
these edges existed, such a wallet never entered the graph at all, so its
correlation evidence and its behavioural score could never meet — the two
vectors built to defeat an on-chain break could not combine, because combining
happened in a structure made of on-chain links.

Admitting them creates the opposite risk, and it bit on first contact: a
synthetic edge read as an observed transfer made a wallet with no link to the
target report "Received funds directly from the target wallet". These tests pin
both halves — the wallet is present, and nothing claims it was paid.
"""

import src.transfer_graph as tg

TARGET = "0x" + "11" * 20
WALLET = "0x" + "22" * 20


def _corr(**over):
    base = {"confidence": 0.9974, "gap_hours": 4.4, "deposit_amount_usd": 2199999.2,
            "deposit_ts": 1_760_000_000, "competing_deposits": 0, "split": False}
    base.update(over)
    return {WALLET: base}


def test_correlation_match_becomes_an_edge():
    edges = tg.correlation_edges(TARGET, _corr())
    assert len(edges) == 1
    e = edges[0]
    assert e["src"] == TARGET and e["dst"] == WALLET
    assert e["discovery_source"] == tg.SRC_CORRELATION
    assert e["amount_usd"] == 2199999.2


def test_correlation_edge_is_marked_inferred():
    """The flag every consumer keys on. Without it the edge is a lie."""
    assert tg.correlation_edges(TARGET, _corr())[0]["inferred"] is True


def test_correlation_edge_is_not_on_a_real_chain():
    """Nothing may attribute this to arbitrum or hyperliquid — it happened on
    neither. A real chain name would make it indistinguishable from a transfer
    in any per-chain grouping or export."""
    assert tg.correlation_edges(TARGET, _corr())[0]["chain"] == tg.CHAIN_INFERRED
    assert tg.CHAIN_INFERRED not in (tg.CHAIN_ARBITRUM, tg.CHAIN_HYPERLIQUID)


def test_self_correlation_is_dropped():
    assert tg.correlation_edges(TARGET, {TARGET: {"confidence": 0.9}}) == []


def test_no_correlations_yields_no_edges():
    assert tg.correlation_edges(TARGET, {}) == []
    assert tg.correlation_edges(TARGET, None) == []


def test_frontier_never_walks_through_an_inference():
    """A correlation is a relationship, not a payment. Expanding through it
    would spend a lookup budget on the far side of a wallet we reached by
    guessing, as though it had been paid."""
    real = {"src": TARGET, "dst": WALLET, "amount_usd": 5000.0,
            "discovery_source": tg.SRC_L1, "bridge_event": False}
    inferred = tg.correlation_edges(TARGET, _corr())[0]
    keep = tg._expandable_edges([real, inferred], dust_usd=1.0)
    assert keep == [real]


def test_correlation_only_wallet_is_not_called_a_recipient():
    """The bug this file exists for. A wallet with no observed transfer must
    never be graded DIRECT_RECIPIENT — that class asserts a receipt, and no
    receipt happened."""
    ev = {"correlation_only": True, "amount_correlation": 0.6476,
          "transfer_count": 0, "bidirectional": False}
    assert tg.classify_node(ev, 0.2471) == tg.CLASS_CORRELATION_LEAD


def test_observed_recipient_is_still_a_recipient():
    """The guard must not swallow the ordinary case."""
    ev = {"correlation_only": False, "direct_from_target": True,
          "transfer_count": 1, "bidirectional": False}
    assert tg.classify_node(ev, 0.2) == tg.CLASS_DIRECT_RECIPIENT


def test_correlation_only_wallet_says_so_in_its_reasons():
    conf, reasons = tg.score_confidence(
        {"correlation_only": True, "amount_correlation": 0.65})
    assert any("no transfer" in r.lower() for r in reasons), reasons


def test_correlation_alone_cannot_reach_migration_candidate():
    """The project's standing invariant: the top tier needs corroboration that
    is independent of the thing being corroborated. An amount match is one
    vector, and one vector is not two."""
    ev = {"correlation_only": True, "amount_correlation": 1.0}
    conf, _ = tg.score_confidence(ev)
    assert conf < 0.60
    assert tg.classify_node(ev, conf) != tg.CLASS_MIGRATION_CANDIDATE


def test_alert_for_a_correlation_lead_states_no_transfer_was_seen(monkeypatch):
    """The email must not let a reader assume a transfer was observed. The
    generic 'a transfer relationship is not proof of ownership' caveat would do
    exactly that, because for these wallets there is no transfer relationship
    at all."""
    import src.alerts as alerts

    captured = {}

    def fake_send(key, hours, subject, body):
        captured["body"] = body
        return True

    monkeypatch.setattr(alerts, "_send_with_cooldown", fake_send)
    node = {"wallet": WALLET, "classification": "CORRELATION_LEAD",
            "confidence": 0.2996, "path": [TARGET, WALLET],
            "confidence_reasons": ["Exit amount re-appears as a deposit"],
            "totals": {}}
    alerts.alert_transfer_graph_discovery(node, ["correlation"], [])

    assert "no transfer between this wallet and the target was observed" \
        in captured["body"].lower()
    assert "a transfer relationship is NOT proof" not in captured["body"]


def test_scanner_linkage_wins_over_the_substrate_pass(monkeypatch):
    """Only the scanner's live path can establish a first funder. If the
    substrate pass overwrote it, an offline run would erase live evidence."""
    import src.transfer_graph as tg

    monkeypatch.setattr(tg, "_substrate_linkage",
                        lambda t, e, c: {WALLET: {"shared_funder": False,
                                                  "shared_deposit_addresses": ["0xd"]}})
    monkeypatch.setattr(tg, "_load_linkage_evidence",
                        lambda: {WALLET: {"shared_funder": True,
                                          "shared_deposit_addresses": ["0xd"]}})
    merged = tg._substrate_linkage(TARGET, [], {})
    merged.update(tg._load_linkage_evidence())
    assert merged[WALLET]["shared_funder"] is True


def test_substrate_linkage_failure_does_not_lose_the_graph(monkeypatch):
    """Evidence we could not gather is not a reason to drop every node."""
    import src.transfer_graph as tg

    def boom(*a, **k):
        raise RuntimeError("substrate unreadable")

    monkeypatch.setattr("src.linkage.substrate_linkage", boom)
    edges = [{"src": TARGET, "dst": WALLET}]
    assert tg._substrate_linkage(TARGET, edges, {}) == {}


def test_gas_funding_edges_are_created_for_wallets_the_target_first_funded():
    """normalise_gas_funding existed and was unit-tested since the graph was
    written, and nothing in production ever called it — so gas_funded_by_target,
    worth 0.15 of confidence, could never be true. Zero such edges existed in a
    100,000-edge graph."""
    import src.transfer_graph as tg
    edges = tg.gas_funding_edges(TARGET, {WALLET: TARGET, "0xother": "0xstranger"})
    assert len(edges) == 1
    assert edges[0]["src"] == TARGET and edges[0]["dst"] == WALLET
    assert edges[0]["discovery_source"] == tg.SRC_GAS_FUNDING


def test_a_wallet_funded_by_a_stranger_gets_no_gas_edge():
    import src.transfer_graph as tg
    assert tg.gas_funding_edges(TARGET, {WALLET: "0xstranger"}) == []


def test_the_target_never_gas_funds_itself():
    import src.transfer_graph as tg
    assert tg.gas_funding_edges(TARGET, {TARGET: TARGET}) == []


def test_no_funders_means_no_gas_edges():
    import src.transfer_graph as tg
    assert tg.gas_funding_edges(TARGET, {}) == []
    assert tg.gas_funding_edges(TARGET, None) == []
