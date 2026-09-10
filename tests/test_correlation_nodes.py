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


def test_frontier_value_score_still_separates_above_a_million():
    """The value signal used to be min(1.0, value / 1_000_000), so every
    destination above $1M scored exactly 1.0. The largest single outflow this
    project has seen — $509,366,847 to one address — therefore ranked
    identically to a $1,000,000 one, lost the remaining budget on tie-breaks and
    was never expanded. Its stored records showed one sender and no recipients:
    not a dead end, just a wallet nobody had looked at."""
    import math

    import src.transfer_graph as tg

    def score(v):
        return min(1.0, math.log10(1.0 + v) / math.log10(1.0 + tg.VALUE_SATURATION_USD))

    small, mid, huge = score(1_000_000), score(58_000_000), score(509_000_000)
    assert small < mid < huge
    assert huge < 1.0            # still bounded
    assert score(0.0) < small


def test_frontier_priority_prefers_the_larger_destination():
    """End to end through the real ranking function."""
    import src.transfer_graph as tg

    now = 1_760_000_000
    def edge(dst, usd):
        return {"src": TARGET, "dst": dst, "amount_usd": usd, "ts": now - 3600,
                "chain": "arbitrum", "asset": "USDC",
                "discovery_source": tg.SRC_L1, "bridge_event": False}

    big, small = "0x" + "aa" * 20, "0x" + "bb" * 20
    edges = [edge(big, 509_000_000.0), edge(small, 1_000_000.0)]
    pr_big, _ = tg._frontier_priority(big, 1, edges, now)
    pr_small, _ = tg._frontier_priority(small, 1, edges, now)
    assert pr_big > pr_small


def _e(src, dst, usd):
    import src.transfer_graph as tg
    return {"src": src, "dst": dst, "amount_usd": usd, "ts": 1,
            "chain": "arbitrum", "asset": "USDC", "discovery_source": tg.SRC_L1,
            "bridge_event": False}


def test_a_pass_through_wallet_is_detected_as_a_conduit():
    """Every remaining lead turned out to be this shape: receives millions,
    forwards nearly all of it to infrastructure, and never looks like a service
    itself because its fan degree is one or two."""
    import src.transfer_graph as tg
    exch = "0x" + "ee" * 20
    conduit = "0x" + "cc" * 20
    edges = [_e(TARGET, conduit, 58_000_000.0), _e(conduit, exch, 57_000_000.0)]
    got = tg.detect_conduits(edges, {exch})
    assert conduit in got
    assert "conduit" in got[conduit]


def test_a_wallet_that_keeps_its_money_is_not_a_conduit():
    """The guard that matters: calling a genuine destination a conduit ends the
    trail at the wallet we are looking for."""
    import src.transfer_graph as tg
    exch = "0x" + "ee" * 20
    holder = "0x" + "dd" * 20
    edges = [_e(TARGET, holder, 58_000_000.0), _e(holder, exch, 1_000_000.0)]
    assert tg.detect_conduits(edges, {exch}) == {}


def test_forwarding_to_a_non_service_is_not_a_conduit():
    """Passing money to another WALLET is exactly the trail worth following."""
    import src.transfer_graph as tg
    edges = [_e(TARGET, WALLET, 58_000_000.0), _e(WALLET, "0x" + "ab" * 20, 57_000_000.0)]
    assert tg.detect_conduits(edges, set()) == {}


def test_a_small_pass_through_is_ignored():
    import src.transfer_graph as tg
    exch = "0x" + "ee" * 20
    small = "0x" + "cc" * 20
    edges = [_e(TARGET, small, 5_000.0), _e(small, exch, 5_000.0)]
    assert tg.detect_conduits(edges, {exch}) == {}


def test_a_wallet_spending_its_own_funds_is_not_a_conduit():
    """Out far exceeding in means the money did not pass through from here."""
    import src.transfer_graph as tg
    exch = "0x" + "ee" * 20
    spender = "0x" + "cc" * 20
    edges = [_e(TARGET, spender, 1_000_000.0), _e(spender, exch, 50_000_000.0)]
    assert tg.detect_conduits(edges, {exch}) == {}


def test_a_known_service_is_not_relabelled_as_a_conduit():
    import src.transfer_graph as tg
    exch = "0x" + "ee" * 20
    other = "0x" + "ff" * 20
    edges = [_e(TARGET, exch, 58_000_000.0), _e(exch, other, 57_000_000.0)]
    assert exch not in tg.detect_conduits(edges, {exch, other})


def test_depositing_to_the_bridge_is_not_being_a_conduit():
    """The scenario this project exists to catch: funds leave the target, land
    on a fresh wallet, and that wallet deposits to Hyperliquid. Counting the
    bridge as an exit reclassified that wallet as infrastructure and buried the
    migration."""
    import src.transfer_graph as tg
    bridge = "0x" + "bb" * 20
    fresh = "0x" + "cc" * 20
    edges = [_e(TARGET, fresh, 1_000_000.0), _e(fresh, bridge, 990_000.0)]
    assert tg.detect_conduits(edges, {bridge}) != {}          # without the guard
    assert tg.detect_conduits(edges, {bridge},
                              not_exits=frozenset({bridge})) == {}


def test_a_wallet_that_trades_is_never_a_conduit():
    """A conduit does nothing with the money. A wallet that trades on
    Hyperliquid is a participant, and the most interesting thing this system
    can find."""
    import src.transfer_graph as tg
    exch = "0x" + "ee" * 20
    trader = "0x" + "cc" * 20
    edges = [_e(TARGET, trader, 58_000_000.0), _e(trader, exch, 57_000_000.0)]
    assert trader in tg.detect_conduits(edges, {exch})
    assert tg.detect_conduits(edges, {exch}, never=frozenset({trader})) == {}


def test_conduit_detection_is_deliberately_one_pass():
    """Cascading conduit detection resolves chains — the head of a three-link
    chain stays an unexplained lead otherwise — but marking a conduit as a
    service stops the frontier traversing it, and cascading cost a whole hop of
    reachability in the L1 expansion test. Losing depth to tidy a row is a bad
    trade for a system whose job is to follow money, so the middle link resolves
    and the head deliberately does not."""
    import src.transfer_graph as tg
    exch = "0x" + "ee" * 20
    head = "0x" + "11" * 20
    mid = "0x" + "22" * 20
    edges = [_e(TARGET, head, 100_000_000.0),
             _e(head, mid, 100_000_000.0),
             _e(mid, exch, 100_000_000.0)]
    got = tg.detect_conduits(edges, {exch})
    assert mid in got
    assert head not in got

