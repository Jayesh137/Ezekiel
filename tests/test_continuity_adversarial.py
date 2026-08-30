# tests/test_continuity_adversarial.py
"""Adversarial regressions for the wallet-continuity tracker.

Each test here encodes a way the tracker could be fooled, starved or corrupted —
by an evasive trader, by messy public data, or by its own persistence. They are
deliberately hostile: the point is not that the happy path works, but that the
unhappy paths cannot manufacture confidence, lose evidence, or burn the API
budget on noise.

Vocabulary note: nothing below asserts ownership. "Successor" states are leads.
"""

import json
import time

from src import continuity as ct
from src import transfer_graph as tg
from src.transfer_graph import build_graph, normalise_l1_transfer
from src.utils import save_latest

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B, C, D, E = ("0x" + ch * 40 for ch in "abcde")
HUB = "0x" + "7" * 40
NOW = time.time()


def l1(src, dst, usd, hours_ago, ref):
    return {"from": src, "to": dst, "value": str(int(usd * 1e6)),
            "timeStamp": str(int(NOW - hours_ago * 3600)),
            "hash": ref, "tokenSymbol": "USDC"}


def edges(*rows):
    return [normalise_l1_transfer(r) for r in rows]


def node(graph, addr):
    return next((n for n in graph["nodes"] if n["wallet"] == addr.lower()), None)


def chain_to(graph, addr):
    hits = [c for c in graph["chains"] if c["endpoint"] == addr.lower()]
    return max(hits, key=lambda c: c["hop_count"]) if hits else None


def as_substrate_record(row, chain="arbitrum"):
    """A raw Etherscan row (this file's `l1()` shape) as src/chain/collect.py
    now produces it — expand_frontier reads records_for(), not raw rows."""
    usd = int(row.get("value", 0) or 0) / 1e6
    ts = int(row.get("timeStamp", 0) or 0)
    return {
        "id": f"{chain}:{row.get('hash', '')}:erc20:0",
        "chain": chain, "chain_id": 42161,
        "block": int(row.get("blockNumber", 0) or 0),
        "ts": ts, "timestamp": None,
        "tx_hash": row.get("hash", ""),
        "src": (row.get("from") or "").lower(),
        "dst": (row.get("to") or "").lower(),
        "kind": "erc20", "asset": row.get("tokenSymbol") or "USDC",
        "token_address": None,
        "amount": usd, "amount_usd": usd, "value_basis": "stable_par",
        "spam": False, "spam_reason": None,
    }


def api(pages):
    """A fake substrate reader whose responses are keyed by address. Patches
    both halves of the seam: sweep_wallet is a no-op (it exists only to
    populate the substrate; in a test the substrate is whatever records_for
    returns) and records_for serves the per-wallet pages as substrate records."""
    def _records_for(wallet, **kw):
        return [as_substrate_record(r) for r in pages.get(wallet.lower(), [])]
    return _records_for


def patch_api(monkeypatch, pages):
    """Install `api(pages)` as the frontier's fetch seam."""
    monkeypatch.setattr("src.chain.collect.sweep_wallet", lambda *a, **kw: None)
    monkeypatch.setattr("src.chain.collect.records_for", api(pages))


# --- 1. cyclic flow ---------------------------------------------------------

def test_cycle_does_not_create_infinite_path_or_duplicate_nodes():
    """A -> B -> A must terminate with one node each and no repeated hop."""
    g = build_graph(edges(
        l1(T, A, 1_000_000, 10, "0x1"),
        l1(A, B, 990_000, 8, "0x2"),
        l1(B, A, 985_000, 6, "0x3"),
    ), T)
    wallets = [n["wallet"] for n in g["nodes"]]
    assert len(wallets) == len(set(wallets)), "cycle must not duplicate nodes"
    for ch in g["chains"]:
        seq = [ch["hops"][0]["src"]] + [h["dst"] for h in ch["hops"]]
        assert len(seq) == len(set(seq)), f"cycle leaked into a path: {seq}"


def test_self_loop_edge_is_never_ingested():
    assert normalise_l1_transfer(l1(A, A, 500_000, 1, "0xself")) is None


def test_recirculating_the_same_funds_does_not_inflate_confidence():
    """The classic fake-volume attack: bounce one pot back and forth.

    Three round trips and thirty must score identically — otherwise a trader
    could manufacture a HIGH_CONFIDENCE lead with a single wallet and no new
    money.
    """
    def build(loops):
        rows = [l1(T, A, 1_000_000, 200, "0xseed")]
        for i in range(loops):
            rows.append(l1(A, B, 990_000, 100 - i, f"0xa{i}"))
            rows.append(l1(B, A, 985_000, 99 - i, f"0xb{i}"))
        return build_graph(edges(*rows), T, hl_active={B})

    few, many = build(3), build(30)
    b_few, b_many = node(few, B), node(many, B)
    assert b_few["continuity"]["confidence"] == b_many["continuity"]["confidence"]
    assert b_few["lifecycle"]["state"] == b_many["lifecycle"]["state"]
    # And the traced value must reflect one pot, not the running total.
    assert b_many["traced_value_usd"] <= 1_000_000


def test_circulation_alone_cannot_reach_high_confidence():
    rows = [l1(T, A, 1_000_000, 200, "0xseed")]
    for i in range(40):
        rows.append(l1(A, B, 990_000, 150 - i, f"0xa{i}"))
        rows.append(l1(B, A, 985_000, 149 - i, f"0xb{i}"))
    g = build_graph(edges(*rows), T, hl_active={B})
    assert node(g, B)["lifecycle"]["state"] != ct.LIFECYCLE_HIGH_CONFIDENCE


# --- 2. duplicate ingestion -------------------------------------------------

def test_same_transaction_from_two_sources_is_one_edge():
    """The tracer and the ledger both report one movement, with different
    timestamps and different discovery sources."""
    from_l1 = normalise_l1_transfer(l1(T, A, 500_000, 5, "0xdup"))
    from_ledger = dict(from_l1)
    from_ledger["ts"] = from_l1["ts"] + 90      # block time vs detected_at
    from_ledger["timestamp"] = None
    from_ledger["discovery_source"] = tg.SRC_HL_LEDGER
    from_ledger["id"] = tg.edge_id(T, A, tg.CHAIN_ARBITRUM, "0xdup", from_ledger["ts"])

    assert from_ledger["id"] == from_l1["id"], "edge id must ignore timestamp"
    merged = tg.dedupe_edges([from_l1, from_ledger, from_l1])
    assert len(merged) == 1
    g = build_graph([from_l1, from_ledger], T)
    assert node(g, A)["evidence"]["transfer_count"] == 1
    assert node(g, A)["evidence"]["only_single_transfer"] is True


def test_duplicate_ingestion_does_not_fire_repeated_transfers_signal():
    rows = [normalise_l1_transfer(l1(T, A, 500_000, 5, "0xone"))] * 6
    g = build_graph(rows, T)
    assert "repeated_transfers" not in node(g, A)["signals"]


# --- 3. four-hop traversal and two-run resume -------------------------------

CHAIN_PAGES = {
    A: [l1(A, B, 960_000, 9, "0xab")],
    B: [l1(B, C, 940_000, 8, "0xbc")],
    C: [l1(C, D, 920_000, 7, "0xcd")],
    D: [],
}


def test_four_hops_are_genuinely_walked(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, CHAIN_PAGES)
    seed = edges(l1(T, A, 1_000_000, 10, "0xta"))
    out, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)
    assert diag["deepest_expanded"] >= 3
    g = build_graph(out, T)
    assert node(g, D) is not None, "hop-4 wallet must exist in the graph"
    assert node(g, D)["depth"] == 4
    ch = chain_to(g, D)
    assert ch["hop_count"] == 4
    assert [h["src"] for h in ch["hops"]] == [T, A.lower(), B.lower(), C.lower()]


def test_frontier_resumes_across_two_separate_process_runs(monkeypatch):
    """Run 1 is capped at one lookup. Run 2 gets only run 1's persisted queue
    and must continue from there rather than restarting."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, CHAIN_PAGES)
    seed = edges(l1(T, A, 1_000_000, 10, "0xta"))
    tight = {**tg.DEFAULTS, "max_expansions": 1}

    e1, d1 = tg.expand_frontier(seed, T, tight, now_ts=NOW)
    assert d1["wallets_expanded"] == [A.lower()]
    assert d1["status"] == "budget_exhausted"
    assert {q["wallet"] for q in d1["frontier_queue"]} == {B.lower()}

    # --- process boundary: only what was persisted crosses it ---
    state = json.loads(json.dumps(
        {"queue": d1["frontier_queue"], "ledger": d1["expanded_ledger"]}))

    e2, d2 = tg.expand_frontier(e1, T, tight, resume=state["queue"],
                                already_expanded=state["ledger"], now_ts=NOW)
    assert d2["wallets_expanded"] == [B.lower()], "run 2 must continue, not restart"
    assert A.lower() not in d2["wallets_expanded"], "finished work must not repeat"
    assert d2["skipped_already_expanded"] == 1
    assert {q["wallet"] for q in d2["frontier_queue"]} == {C.lower()}

    g = build_graph(e2, T)
    assert node(g, C) is not None, "two runs must reach further than one"
    assert node(g, C)["depth"] == 3


def test_interrupted_run_resumes_without_losing_or_repeating_work(monkeypatch):
    """Run 1 dies mid-walk. Run 2 must retry the failed wallet and skip the
    successful one."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    seed = edges(l1(T, A, 1_000_000, 10, "0xta"), l1(T, B, 900_000, 10, "0xtb"))
    state = {"dead": True}
    collected = {}

    def flaky_sweep(wallet, *args, **kw):
        a = wallet.lower()
        if a == B.lower() and state["dead"]:
            raise OSError("connection reset mid-run")
        collected[a] = [as_substrate_record(r) for r in CHAIN_PAGES.get(a, [])]

    monkeypatch.setattr("src.chain.collect.sweep_wallet", flaky_sweep)
    monkeypatch.setattr("src.chain.collect.records_for",
                        lambda wallet, **kw: collected.get(wallet.lower(), []))
    e1, d1 = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)
    assert d1["status"] == "partial"
    assert A.lower() in d1["expanded_ledger"]
    assert B.lower() not in d1["expanded_ledger"], "a failed lookup is not finished"
    assert B.lower() in {q["wallet"] for q in d1["frontier_queue"]}

    state["dead"] = False
    _, d2 = tg.expand_frontier(e1, T, tg.DEFAULTS,
                               resume=d1["frontier_queue"],
                               already_expanded=d1["expanded_ledger"], now_ts=NOW)
    assert B.lower() in d2["wallets_expanded"], "failed wallet must be retried"
    assert A.lower() not in d2["wallets_expanded"], "succeeded wallet must not repeat"


def test_repeated_runs_over_static_data_stop_spending_budget(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, CHAIN_PAGES)
    seed = edges(l1(T, A, 1_000_000, 10, "0xta"))
    e, d = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)
    assert d["status"] == "ok"
    _, d2 = tg.expand_frontier(e, T, tg.DEFAULTS, resume=d["frontier_queue"],
                               already_expanded=d["expanded_ledger"], now_ts=NOW)
    assert d2["lookups"] == 0, "a drained frontier must cost nothing to re-run"
    assert d2["status"] == "ok"
    assert d2["frontier_remaining"] == 0


def test_fully_explored_is_only_claimed_when_no_eligible_frontier_remains(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, CHAIN_PAGES)
    seed = edges(l1(T, A, 1_000_000, 10, "0xta"))
    _, capped = tg.expand_frontier(seed, T, {**tg.DEFAULTS, "max_expansions": 1},
                                   now_ts=NOW)
    assert capped["status"] != "ok"
    assert capped["frontier_remaining"] > 0
    g = build_graph(seed, T, expansion=capped)
    assert g["health"]["frontier_incomplete"] is True


# --- 4. fan-out, dust and services ------------------------------------------

def test_high_fan_degree_service_is_not_expanded_and_does_not_explode_frontier(monkeypatch):
    """A hub with 60 counterparties must be suppressed, not walked."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [l1(T, HUB, 1_000_000, 10, "0xth")]
    for i in range(60):
        rows.append(l1(HUB, "0x" + f"{i:040x}", 5_000, 9, f"0xout{i}"))
        rows.append(l1("0x" + f"{i + 500:040x}", HUB, 5_000, 11, f"0xin{i}"))
    seed = edges(*rows)

    called = []

    def spy(wallet, *args, **kw):
        called.append(wallet.lower())

    monkeypatch.setattr("src.chain.collect.sweep_wallet", spy)
    monkeypatch.setattr("src.chain.collect.records_for", lambda *a, **kw: [])
    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)
    assert HUB.lower() not in called, "a service hub must never be expanded"
    suppressed = [d for d in diag["decisions"] if d["action"] == "suppressed"]
    assert any(d["wallet"] == HUB.lower() for d in suppressed)
    assert diag["frontier_remaining"] == 0, "a suppressed hub must not queue its 60 recipients"


def test_configured_service_addresses_are_suppressed_during_expansion(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    seed = edges(l1(T, HUB, 1_000_000, 10, "0xth"))
    called = []
    monkeypatch.setattr("src.chain.collect.sweep_wallet",
                        lambda wallet, *a, **kw: called.append(wallet.lower()))
    monkeypatch.setattr("src.chain.collect.records_for", lambda *a, **kw: [])
    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW,
                                 known_services={HUB.upper()})
    assert HUB.lower() not in called
    assert diag["lookups"] == 0


def test_dust_recipients_never_consume_the_lookup_budget(monkeypatch):
    """Address-poisoning clones send sub-dollar transfers to look like real
    counterparties. On the live 2026-07-28 graph they took 6 of 11 lookups."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    poison = ["0x" + f"{i:040x}" for i in range(6)]
    rows = [l1(T, A, 1_000_000, 10, "0xreal")]
    rows += [l1(T, p, 0.01, 9, f"0xdust{i}") for i, p in enumerate(poison)]
    called = []
    monkeypatch.setattr("src.chain.collect.sweep_wallet",
                        lambda wallet, *a, **kw: called.append(wallet.lower()))
    monkeypatch.setattr("src.chain.collect.records_for", lambda *a, **kw: [])
    _, diag = tg.expand_frontier(edges(*rows), T, tg.DEFAULTS, now_ts=NOW)
    assert called == [A.lower()], f"budget leaked to dust: {called}"
    assert diag["lookups"] == 1


def test_dust_discovered_mid_walk_is_not_queued(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, {
        A: [l1(A, B, 0.02, 8, "0xdust"), l1(A, C, 900_000, 8, "0xreal")],
    })
    _, diag = tg.expand_frontier(edges(l1(T, A, 1_000_000, 10, "0xta")), T,
                                 {**tg.DEFAULTS, "max_expansions": 1}, now_ts=NOW)
    queued = {q["wallet"] for q in diag["frontier_queue"]}
    assert queued == {C.lower()}, f"dust was queued for a lookup: {queued}"


def test_service_never_transmits_ownership_confidence():
    """Everything is reachable through an exchange, so a service is a wall."""
    g = build_graph(edges(
        l1(T, HUB, 1_000_000, 20, "0x1"),
        l1(HUB, A, 990_000, 10, "0x2"),
    ), T, known_services={HUB}, hl_active={A})
    hub = node(g, HUB)
    assert hub["continuity"]["confidence"] == 0.0
    assert hub["lifecycle"]["state"] == ct.LIFECYCLE_REJECTED_SERVICE
    # A is behind the service and is never reached at all — not reached with a
    # low score, but not reached.
    assert node(g, A) is None, "traversal must not pass through a service"
    assert all(ch["endpoint"] != A.lower() for ch in g["chains"])


def test_rejected_service_cannot_promote_without_reclassification():
    ev = {"is_service": True, "trades_on_hl": True, "direct_from_target": True,
          "transfer_count": 9, "behavioural_score": 0.95}
    life = ct.lifecycle_state(is_service=True, on_path=True, transfer_count=9,
                              funded_by_target=True, has_unbroken_path=True,
                              trades_after_funding=True, confidence=0.99,
                              families=[ct.FAMILY_FLOW, ct.FAMILY_FUNDING],
                              disposition_alert=True)
    assert life["state"] == ct.LIFECYCLE_REJECTED_SERVICE
    # Re-evaluated with new evidence that it is NOT a service, it may promote.
    ev["is_service"] = False
    again = ct.lifecycle_state(is_service=False, on_path=True, transfer_count=9,
                               funded_by_target=True, has_unbroken_path=True,
                               trades_after_funding=True, confidence=0.99,
                               families=[ct.FAMILY_FLOW, ct.FAMILY_FUNDING],
                               disposition_alert=True)
    assert again["state"] == ct.LIFECYCLE_HIGH_CONFIDENCE


# --- 5. split / merge and bridges -------------------------------------------

def test_genuine_n_way_split_reconverges():
    out = [{"amount_usd": 300_000, "ts": 1000, "ref": "0xs1"},
           {"amount_usd": 350_000, "ts": 1200, "ref": "0xs2"},
           {"amount_usd": 340_000, "ts": 1400, "ref": "0xs3"}]
    merged = ct.reconcile_split_merge(
        out, [{"amount_usd": 975_000, "ts": 1400 + 3600 * 24, "ref": "0xm"}])
    assert merged is not None
    assert merged["parts"] == 3
    assert merged["confidence"] > 0.0


def test_split_tolerates_fees_and_out_of_order_timestamps():
    out = [{"amount_usd": 340_000, "ts": 1400, "ref": "0xs3"},
           {"amount_usd": 300_000, "ts": 1000, "ref": "0xs1"},
           {"amount_usd": 350_000, "ts": 1200, "ref": "0xs2"}]
    assert ct.reconcile_split_merge(
        out, [{"amount_usd": 960_000, "ts": 1400 + 7200, "ref": "0xm"}]) is not None


def test_unrelated_similar_amount_transfers_do_not_reconcile():
    """Two people happening to move ~1M within a week is not a link."""
    out = [{"amount_usd": 500_000, "ts": 1000, "ref": "0xs1"},
           {"amount_usd": 500_000, "ts": 1100, "ref": "0xs2"}]
    # 25% off: outside tolerance despite similar magnitude.
    assert ct.reconcile_split_merge(
        out, [{"amount_usd": 750_000, "ts": 1100 + 3600, "ref": "0xm"}]) is None
    # Right amount, but months later.
    assert ct.reconcile_split_merge(
        out, [{"amount_usd": 1_000_000, "ts": 1100 + 3600 * 24 * 90, "ref": "0xm"}]) is None
    # Right amount, but BEFORE the split — settlement cannot precede the send.
    assert ct.reconcile_split_merge(
        out, [{"amount_usd": 1_000_000, "ts": 500, "ref": "0xm"}]) is None


def test_partial_transfer_does_not_reconcile_as_a_full_merge():
    out = [{"amount_usd": 500_000, "ts": 1000, "ref": "0xs1"},
           {"amount_usd": 500_000, "ts": 1100, "ref": "0xs2"}]
    assert ct.reconcile_split_merge(
        out, [{"amount_usd": 600_000, "ts": 1200, "ref": "0xm"}]) is None


def test_delayed_settlement_within_window_still_reconciles():
    out = [{"amount_usd": 400_000, "ts": 0, "ref": "0xs1"},
           {"amount_usd": 400_000, "ts": 60, "ref": "0xs2"}]
    late = {"amount_usd": 790_000, "ts": 60 + int(3600 * 24 * 6.5), "ref": "0xm"}
    assert ct.reconcile_split_merge(out, [late]) is not None


def test_bridge_amount_mismatch_is_not_correlated():
    assert ct.correlate_bridge(1_000_000, 800_000, 2.0) is None    # 20% gap
    assert ct.correlate_bridge(1_000_000, 950_000, 2.0) is None    # 5% > 3% fees
    assert ct.correlate_bridge(1_000_000, 999_000, 400.0) is None  # too slow
    assert ct.correlate_bridge(1_000_000, 999_000, -5.0) is None   # arrives first
    assert ct.correlate_bridge(1_000_000, 999_000, None) is None   # unknown timing
    # Within fee tolerance and promptly settled: correlated, but never certain.
    assert ct.correlate_bridge(1_000_000, 985_000, 2.0)["confidence"] < 1.0


def test_bridge_correlation_stays_explicitly_uncertain():
    hit = ct.correlate_bridge(1_000_000, 999_000, 2.0)
    assert hit is not None
    assert hit["confidence"] < 1.0
    assert hit["break"]["type"] == ct.BREAK_BRIDGE
    assert "custody boundary" in hit["break"]["reason"]


def test_bridge_path_can_never_be_unbroken_or_high_confidence():
    brk = [{"at": B, "reason": "bridge crossing", "type": ct.BREAK_BRIDGE}]
    life = ct.lifecycle_state(
        on_path=True, transfer_count=8, funded_by_target=True,
        has_unbroken_path=False, trades_after_funding=True, confidence=0.95,
        families=[ct.FAMILY_FLOW, ct.FAMILY_FUNDING, ct.FAMILY_BEHAVIOUR],
        breaks=brk, disposition_alert=True)
    assert life["state"] != ct.LIFECYCLE_HIGH_CONFIDENCE
    assert any("break" in b for b in life["blockers"])


# --- 6. path integrity ------------------------------------------------------

def test_missing_hop_truncates_the_path_rather_than_splicing_it():
    """B is reachable only by a REVERSE edge. The chain must stop at A, and B
    must not inherit a contiguous-looking 100%-retained path."""
    g = build_graph(edges(
        l1(T, A, 1_000_000, 10, "0x1"),
        l1(B, A, 990_000, 8, "0x2"),     # B -> A, not A -> B
    ), T)
    b = node(g, B)
    assert b["chain_id"] is None, "no forward path to B exists"
    assert b["path_truncated"] is True
    for ch in g["chains"]:
        seq = [ch["hops"][0]["src"]] + [h["dst"] for h in ch["hops"]]
        assert ch["endpoint"] == seq[-1], "endpoint must be where value arrived"
        for i in range(len(ch["hops"]) - 1):
            assert ch["hops"][i]["dst"] == ch["hops"][i + 1]["src"], "non-contiguous hop"


def test_path_ids_and_signatures_are_stable_between_runs():
    rows = edges(l1(T, A, 1_000_000, 10, "0x1"), l1(A, B, 980_000, 8, "0x2"))
    one, two = build_graph(rows, T), build_graph(list(reversed(rows)), T)
    c1, c2 = chain_to(one, B), chain_to(two, B)
    assert c1["id"] == c2["id"]
    assert c1["signature"] == c2["signature"]


def test_signature_survives_rediscovery_with_new_transaction_refs():
    """A resumed frontier re-finds the same route via different transactions."""
    run1 = ct.path_signature([{"src": T, "dst": A, "ref": "0x1"},
                              {"src": A, "dst": B, "ref": "0x2"}])
    run2 = ct.path_signature([{"src": T, "dst": A, "ref": "0xAAA"},
                              {"src": A, "dst": B, "ref": "0xBBB"}])
    assert run1 == run2, "route identity must not depend on which tx carried it"
    assert ct.path_id([{"src": T, "dst": A, "ref": "0x1"}]) != run1


def test_out_of_order_timestamps_do_not_break_path_assembly():
    g = build_graph(edges(
        l1(A, B, 980_000, 20, "0x2"),      # later hop, earlier in the list
        l1(T, A, 1_000_000, 10, "0x1"),    # and with an EARLIER timestamp
    ), T)
    ch = chain_to(g, B)
    assert ch is not None and ch["hop_count"] == 2
    assert ch["elapsed_hours"] >= 0, "elapsed time must never be negative"


def test_partial_pagination_yields_a_shorter_path_not_a_wrong_one(monkeypatch):
    """Etherscan caps a busy address at N rows; the onward hop is simply absent."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, {A: [], B: []})
    out, diag = tg.expand_frontier(edges(l1(T, A, 1_000_000, 10, "0xta")), T,
                                   tg.DEFAULTS, now_ts=NOW)
    g = build_graph(out, T, expansion=diag)
    assert node(g, B) is None, "an unseen hop must not be invented"
    assert chain_to(g, A)["hop_count"] == 1


# --- 7. scoring invariants --------------------------------------------------

BASE = {"direct_from_target": True, "first_gas": True, "behavioural": 0.9}


def test_confidence_is_monotonic_in_hops_value_and_age():
    ref = ct.score_continuity(BASE, hop_count=2, value_retained=0.95, age_days=10)
    assert ct.score_continuity(BASE, hop_count=4, value_retained=0.95,
                               age_days=10)["confidence"] <= ref["confidence"]
    assert ct.score_continuity(BASE, hop_count=2, value_retained=0.20,
                               age_days=10)["confidence"] <= ref["confidence"]
    assert ct.score_continuity(BASE, hop_count=2, value_retained=0.95,
                               age_days=800)["confidence"] <= ref["confidence"]


def test_every_contribution_is_explainable():
    s = ct.score_continuity(BASE, hop_count=2, value_retained=0.9)
    assert s["reasons"], "a score with no stated reasons is not auditable"
    for name in BASE:
        assert any(name.replace("_", " ") in r for r in s["reasons"])


def test_family_cap_blocks_corroboration_by_correlated_signals():
    """Four FLOW signals are one kind of evidence, however many there are."""
    flow_only = ct.score_continuity({
        "amount_similarity": 1.0, "temporal_proximity": 1.0,
        "split_merge": 1.0, "value_retained": 1.0})
    assert flow_only["families"] == [ct.FAMILY_FLOW]
    assert flow_only["confidence"] <= ct.FAMILY_CAP
    assert any("needs 2 independent families" in b for b in flow_only["blockers"])


def test_single_transfer_or_volume_alone_never_promotes():
    g = build_graph(edges(l1(T, A, 50_000_000, 5, "0xbig")), T)
    a = node(g, A)
    assert a["evidence"]["only_single_transfer"] is True
    assert a["lifecycle"]["state"] not in (ct.LIFECYCLE_POSSIBLE,
                                           ct.LIFECYCLE_HIGH_CONFIDENCE)
    huge = build_graph(edges(l1(T, A, 999_000_000, 5, "0xhuge")), T)
    assert node(huge, A)["continuity"]["confidence"] == a["continuity"]["confidence"], \
        "size of a transfer is not evidence of continuity"


def test_one_behavioural_match_alone_never_promotes():
    s = ct.score_continuity({"behavioural": 1.0})
    assert s["families"] == [ct.FAMILY_BEHAVIOUR]
    assert s["blockers"]
    life = ct.lifecycle_state(on_path=True, funded_by_target=True,
                              has_unbroken_path=True, trades_after_funding=True,
                              confidence=s["confidence"],
                              families=s["families"], disposition_alert=True)
    assert life["state"] == ct.LIFECYCLE_TRADING


def test_relay_activity_alone_never_promotes_and_only_drives_priority():
    hot = ct.relay_likelihood(1_000_000, 990_000, 1.0, 1)
    cold = ct.relay_likelihood(1_000_000, 10_000, 300.0, 12)
    assert hot > cold, "relay likelihood must rank chase priority"
    # ...and contributes nothing to ownership confidence.
    assert "relay" not in ct.SIGNALS
    assert ct.score_continuity({"relay": 1.0})["confidence"] == 0.0


def test_contradictory_behaviour_blocks_promotion():
    s = ct.score_continuity(BASE, vetoes=["trades a disjoint market set"])
    assert any("contradictory" in b for b in s["blockers"])
    life = ct.lifecycle_state(
        on_path=True, funded_by_target=True, has_unbroken_path=True,
        trades_after_funding=True, confidence=0.99,
        families=[ct.FAMILY_FLOW, ct.FAMILY_FUNDING, ct.FAMILY_BEHAVIOUR],
        vetoes=["trades a disjoint market set"], disposition_alert=True)
    assert life["state"] == ct.LIFECYCLE_TRADING
    assert "contradictory behavioural evidence" in life["blockers"]


def test_lifecycle_is_deterministic_and_idempotent():
    kw = dict(on_path=True, transfer_count=4, funded_by_target=True,
              has_unbroken_path=True, trades_after_funding=True, confidence=0.72,
              families=[ct.FAMILY_FLOW, ct.FAMILY_FUNDING], disposition_alert=True)
    runs = [ct.lifecycle_state(**kw) for _ in range(5)]
    assert all(r == runs[0] for r in runs)
    assert runs[0]["state"] == ct.LIFECYCLE_HIGH_CONFIDENCE


def test_dormant_wallet_reactivates_when_it_moves_again():
    """DORMANT is a state, not a grave."""
    kw = dict(on_path=True, transfer_count=4, funded_by_target=True,
              has_unbroken_path=True, trades_after_funding=True, confidence=0.20,
              families=[ct.FAMILY_FLOW], disposition_alert=False)
    quiet = ct.lifecycle_state(**kw, days_inactive=200)
    assert quiet["state"] == ct.LIFECYCLE_DORMANT
    assert quiet["dormant"] is True

    revived = ct.lifecycle_state(**kw, days_inactive=1)
    assert revived["state"] == ct.LIFECYCLE_TRADING, "DORMANT must not be terminal"
    assert revived["dormant"] is False
    # Reactivation reads as a promotion, so it can alert.
    assert (ct.LIFECYCLE_ORDER[revived["state"]]
            > ct.LIFECYCLE_ORDER[quiet["state"]])


def test_dormancy_is_reported_even_when_it_does_not_demote():
    """A corroborated lead going quiet must not be erased — but a migration
    tracker that hides the silence misleads about where the trader is now."""
    kw = dict(on_path=True, transfer_count=4, funded_by_target=True,
              has_unbroken_path=True, trades_after_funding=True, confidence=0.72,
              families=[ct.FAMILY_FLOW, ct.FAMILY_FUNDING], disposition_alert=True)
    quiet = ct.lifecycle_state(**kw, days_inactive=200)
    assert quiet["state"] == ct.LIFECYCLE_HIGH_CONFIDENCE, "a strong lead survives"
    assert quiet["dormant"] is True and quiet["days_inactive"] == 200
    assert "no activity for 200 days" in quiet["reason"]


def test_funded_by_target_requires_traced_value_not_mere_reachability():
    """B is reachable in the graph but the value that got there did not come
    from the target."""
    g = build_graph(edges(
        l1(T, A, 1_000_000, 30, "0x1"),
        l1(C, A, 40_000, 20, "0x2"),      # unrelated inbound
        l1(A, B, 5_000, 10, "0x3"),       # only 0.5% of the target's money
    ), T, hl_active={B})
    b = node(g, B)
    assert b["chain_id"] is not None
    assert b["value_retained"] < 0.5
    assert b["lifecycle"]["state"] not in (
        ct.LIFECYCLE_FUNDED, ct.LIFECYCLE_TRADING,
        ct.LIFECYCLE_POSSIBLE, ct.LIFECYCLE_HIGH_CONFIDENCE)


def test_trading_evidence_belongs_to_the_discovered_wallet_not_the_target():
    rows = edges(l1(T, A, 1_000_000, 10, "0x1"), l1(A, B, 980_000, 8, "0x2"))
    g = build_graph(rows, T, hl_active={A})     # only A trades
    assert node(g, A)["evidence"]["trades_on_hl"] is True
    assert node(g, B)["evidence"]["trades_on_hl"] is False
    assert node(g, B)["lifecycle"]["state"] != ct.LIFECYCLE_TRADING


def test_known_linked_wallet_keeps_its_classification():
    """Guard against the audit silently downgrading an established finding."""
    g = build_graph(edges(
        l1(T, A, 1_000_000, 30, "0x1"),
        l1(A, T, 400_000, 25, "0x2"),
        l1(T, A, 800_000, 20, "0x3"),
        l1(A, T, 300_000, 15, "0x4"),
    ), T, behavioural={A: 0.88}, hl_active={A},
        correlations={A: {"confidence": 0.9, "gap_hours": 3.0}})
    a = node(g, A)
    assert a["classification"] == tg.CLASS_MIGRATION_CANDIDATE
    assert a["confidence"] >= 0.7
    assert a["evidence"]["bidirectional"] is True


# --- 8. persistence, migration and alert state ------------------------------

V1_GRAPH = {
    "computed_at": "2026-07-01T00:00:00+00:00",
    "target": T,
    "node_count": 1,
    "edge_count": 1,
    "nodes": [{"wallet": A, "classification": tg.CLASS_DIRECT_RECIPIENT,
               "confidence": 0.42, "confidence_reasons": ["received funds"],
               "depth": 1, "path": [T, A], "multi_hop": False,
               "totals": {"received_from_target_usd": 1.0,
                          "sent_to_target_usd": 0.0, "edge_count": 1},
               "edge_ids": ["abc123"], "evidence": {"depth": 1}}],
    "edges": [{"id": "abc123", "src": T, "dst": A, "chain": "arbitrum",
               "amount_usd": 1.0, "ts": 1, "discovery_source": "l1_transfer"}],
    "services": {},
    "health": {"expansion": {"status": "ok", "lookups": 3}},
}


def test_v1_graph_migrates_without_losing_anything():
    m = tg.migrate_graph(V1_GRAPH)
    assert m["schema_version"] == tg.SCHEMA_VERSION
    assert m["migrated_from_schema"] == 1
    assert m["nodes"][0]["wallet"] == A
    assert m["nodes"][0]["confidence"] == 0.42
    assert m["nodes"][0]["edge_ids"] == ["abc123"]      # evidence references kept
    assert m["edges"] == V1_GRAPH["edges"]
    assert m["health"]["expansion"]["lookups"] == 3     # history preserved
    assert m["chains"] == []                            # v2 container added empty
    assert m["nodes"][0]["lifecycle"] is None
    assert m["health"]["expansion"]["expanded_ledger"] == []


def test_migration_is_idempotent():
    once = tg.migrate_graph(V1_GRAPH)
    twice = tg.migrate_graph(json.loads(json.dumps(once)))
    assert twice == once
    thrice = tg.migrate_graph(json.loads(json.dumps(twice)))
    assert thrice == once


def test_migration_is_non_destructive_of_the_original():
    snapshot = json.loads(json.dumps(V1_GRAPH))
    tg.migrate_graph(V1_GRAPH)
    assert V1_GRAPH == snapshot, "migration must not mutate its input"


def test_migrating_garbage_does_not_raise():
    assert tg.migrate_graph(None) == {}
    assert tg.migrate_graph([]) == {}
    assert tg.migrate_graph({})["schema_version"] == tg.SCHEMA_VERSION


def test_production_graph_file_still_loads(tmp_path):
    """The real file on main is v1. It must survive migration untouched."""
    from pathlib import Path
    live = Path(__file__).resolve().parents[1] / "data" / "transfer_graph" / "latest.json"
    if not live.exists():
        return
    raw = json.loads(live.read_text())
    m = tg.migrate_graph(raw)
    assert len(m["nodes"]) == len(raw["nodes"])
    assert len(m["edges"]) == len(raw["edges"])
    for before, after in zip(raw["nodes"], m["nodes"], strict=True):
        assert after["wallet"] == before["wallet"]
        assert after["confidence"] == before["confidence"]
        assert after["edge_ids"] == before["edge_ids"]


def test_interrupted_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    d = tmp_path / "graph"
    save_latest(str(d), {"schema_version": 2, "nodes": ["good"]})

    class Boom(Exception):
        pass

    real_dump = json.dump

    def dying_dump(obj, fp, **kw):
        real_dump(obj, fp, **kw)
        raise Boom("killed mid-write")

    monkeypatch.setattr(json, "dump", dying_dump)
    try:
        save_latest(str(d), {"schema_version": 2, "nodes": ["partial"]})
    except Boom:
        pass
    monkeypatch.undo()

    survived = json.loads((d / "latest.json").read_text())
    assert survived["nodes"] == ["good"], "an interrupted write corrupted latest.json"
    assert not list(d.glob(".latest.json.*.tmp")), "temp file must be cleaned up"


def test_corrupt_previous_graph_degrades_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    (tmp_path / "transfer_graph").mkdir(parents=True)
    (tmp_path / "transfer_graph" / "latest.json").write_text('{"nodes": [trunca')
    assert tg._read_previous_graph() == {}


# --- 9. alert transitions ---------------------------------------------------

def _promoted_graph():
    g = build_graph(edges(
        l1(T, A, 1_000_000, 30, "0x1"),
        l1(A, T, 400_000, 25, "0x2"),
        l1(T, A, 800_000, 20, "0x3"),
    ), T, behavioural={A: 0.88}, hl_active={A},
        correlations={A: {"confidence": 0.9, "gap_hours": 3.0}})
    return g


def test_rebuilding_an_unchanged_graph_alerts_nothing():
    g = _promoted_graph()
    first = tg.select_alerts(g, None)
    assert first, "the first discovery must alert"
    tg.advance_alert_state(g, None, first, undelivered=[])
    for _ in range(3):
        assert tg.select_alerts(g, g) == [], "a rebuild is not a transition"


def test_lifecycle_promotion_alerts():
    before = build_graph(edges(l1(T, A, 1_000_000, 30, "0x1")), T)
    after = _promoted_graph()
    tg.advance_alert_state(before, None, [], undelivered=[])
    picked = tg.select_alerts(after, before)
    assert any("lifecycle" in r for a in picked for r in a["trigger_reasons"])


def test_failed_delivery_does_not_advance_state_and_retry_then_succeeds():
    g = _promoted_graph()
    alerts = tg.select_alerts(g, None)
    assert alerts

    # Delivery fails: no lifecycle or route mark may be recorded.
    tg.advance_alert_state(g, None, alerts, undelivered=[A])
    assert g["alerted_lifecycle"] == {}
    assert g["alerted_paths"] == []
    g["undelivered_alerts"] = [A]

    retry = tg.select_alerts(g, g)
    assert len(retry) == 1
    assert retry[0]["trigger_reasons"] == [
        "retry: previously selected but not delivered"]

    # Retry succeeds: now state advances and the alert retires.
    tg.advance_alert_state(g, g, retry, undelivered=[])
    assert g["alerted_lifecycle"][A.lower()]
    g["undelivered_alerts"] = []
    assert tg.select_alerts(g, g) == []


def test_equivalent_route_does_not_realert_after_resume():
    g = _promoted_graph()
    alerts = tg.select_alerts(g, None)
    tg.advance_alert_state(g, None, alerts, undelivered=[])
    signatures = set(g["alerted_paths"])
    assert signatures, "a delivered path alert must record its route signature"

    # The next run rediscovers the same route through different transactions.
    again = build_graph(edges(
        l1(T, A, 1_000_000, 29, "0x9"),
        l1(A, T, 400_000, 24, "0x8"),
        l1(T, A, 800_000, 19, "0x7"),
    ), T, behavioural={A: 0.88}, hl_active={A},
        correlations={A: {"confidence": 0.9, "gap_hours": 3.0}})
    again["alerted_paths"] = g["alerted_paths"]
    again["alerted_lifecycle"] = g["alerted_lifecycle"]
    assert {c["signature"] for c in again["chains"]} & signatures
    assert tg.select_alerts(again, again) == []


def test_service_and_uncorroborated_leads_never_alert():
    g = build_graph(edges(
        l1(T, HUB, 1_000_000, 10, "0x1"),
        l1(HUB, A, 990_000, 8, "0x2"),
    ), T, known_services={HUB})
    picked = {a["node"]["wallet"] for a in tg.select_alerts(g, None)}
    assert HUB.lower() not in picked, "a service must never alert"
    # A sits behind the service, so it is not on any traced path and cannot alert.
    assert A.lower() not in picked
    assert node(g, A) is None


# --- 10. dashboard data contract --------------------------------------------
#
# The dashboard reads these fields directly. Renaming or dropping one breaks the
# page silently at runtime, so the contract is pinned here rather than trusted.

def test_graph_exposes_every_field_the_dashboard_reads():
    g = _promoted_graph()
    tg.annotate_changes(g, None)
    tg.advance_alert_state(g, None, [], undelivered=[])

    for key in ("schema_version", "chains", "nodes", "edges", "health",
                "services", "computed_at", "target"):
        assert key in g, f"dashboard reads graph.{key}"

    exp = g["health"]["expansion"]
    for key in ("status", "lookups", "lookup_budget", "frontier_remaining",
                "frontier_truncated", "expanded_ledger"):
        assert key in exp, f"dashboard reads health.expansion.{key}"

    for key in ("node_budget", "node_budget_exhausted", "depth_limited",
                "frontier_incomplete", "max_depth_configured", "max_depth_reached",
                "discovery_sources", "degraded_sources"):
        assert key in g["health"], f"dashboard reads health.{key}"

    n = node(g, A)
    for key in ("wallet", "classification", "confidence", "confidence_reasons",
                "depth", "path", "multi_hop", "totals", "chains", "edge_ids",
                "evidence", "continuity", "lifecycle", "chain_id",
                "value_retained", "traced_value_usd", "path_truncated",
                "confidence_delta", "continuity_delta", "previous_lifecycle",
                "is_new"):
        assert key in n, f"dashboard reads node.{key}"

    for key in ("state", "reason", "blockers", "dormant", "days_inactive"):
        assert key in n["lifecycle"], f"dashboard reads node.lifecycle.{key}"
    for key in ("confidence", "families", "reasons", "blockers"):
        assert key in n["continuity"], f"dashboard reads node.continuity.{key}"

    ch = g["chains"][0]
    for key in ("id", "signature", "endpoint", "hops", "hop_count",
                "elapsed_hours", "value_retained", "relay_hops", "breaks",
                "complete"):
        assert key in ch, f"dashboard reads chain.{key}"
    for key in ("src", "dst", "chain", "amount_usd", "ts", "ref"):
        assert key in ch["hops"][0], f"dashboard reads chain.hops[].{key}"


def test_confidence_change_is_reported_against_the_previous_run():
    weak = build_graph(edges(l1(T, A, 1_000_000, 30, "0x1")), T)
    strong = _promoted_graph()
    tg.annotate_changes(strong, weak)
    a = node(strong, A)
    assert a["is_new"] is False
    assert a["confidence_delta"] > 0, "a strengthening finding must show as rising"
    assert a["previous_lifecycle"] == node(weak, A)["lifecycle"]["state"]

    tg.annotate_changes(weak, None)
    assert node(weak, A)["is_new"] is True
    assert node(weak, A)["confidence_delta"] is None


def test_migrated_v1_graph_has_no_field_the_dashboard_would_crash_on():
    """Old files must render, not explode: every array the page iterates and
    every object it dereferences has to exist after migration."""
    m = tg.migrate_graph(V1_GRAPH)
    assert isinstance(m["chains"], list)
    for n in m["nodes"]:
        assert isinstance(n.get("confidence_reasons") or [], list)
        assert isinstance(n.get("path") or [], list)
        assert isinstance(n.get("chains") or [], list)
        assert n["continuity"] is None and n["lifecycle"] is None
        assert n["chain_id"] is None
        assert n["path_truncated"] is False
    assert isinstance(m["health"]["expansion"]["frontier_queue"], list)
    assert isinstance(m["health"]["expansion"]["expanded_ledger"], list)


def test_expansion_never_writes_into_the_working_tree(monkeypatch, tmp_path):
    """A dry run must leave no production file behind.

    expand_frontier used to write data/state/transfer_graph_last_expansion_ms.txt
    itself, so merely running the unit tests dirtied the repository. Cursor
    writes belong to the I/O wrapper, not the traversal.
    """
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    state = repo / "data" / "state"
    before = {p.name: p.read_bytes() for p in state.glob("*")} if state.exists() else {}

    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, CHAIN_PAGES)
    tg.expand_frontier(edges(l1(T, A, 1_000_000, 10, "0xta")), T, tg.DEFAULTS,
                       now_ts=NOW)

    after = {p.name: p.read_bytes() for p in state.glob("*")} if state.exists() else {}
    assert after == before, "expand_frontier mutated files in the working tree"


# --- 11. every traversal bound actually binds -------------------------------

def test_branching_limit_caps_wallets_expanded_per_level(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    fan = ["0x" + f"{i:040x}" for i in range(20)]
    seed = edges(*[l1(T, w, 500_000, 10, f"0x{i}") for i, w in enumerate(fan)])
    called = []
    monkeypatch.setattr("src.chain.collect.sweep_wallet",
                        lambda wallet, *a, **kw: called.append(wallet.lower()))
    monkeypatch.setattr("src.chain.collect.records_for", lambda *a, **kw: [])
    budget = {**tg.DEFAULTS, "max_branching": 3, "max_expansions": 99}
    _, diag = tg.expand_frontier(seed, T, budget, now_ts=NOW)
    assert len(called) == 3, f"branching limit ignored: {len(called)} lookups"
    assert diag["frontier_remaining"] == 17, "the rest must be queued, not dropped"


def test_time_budget_stops_the_walk_and_queues_the_remainder(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    fan = ["0x" + f"{i:040x}" for i in range(8)]
    seed = edges(*[l1(T, w, 500_000, 10, f"0x{i}") for i, w in enumerate(fan)])
    monkeypatch.setattr("src.chain.collect.sweep_wallet", lambda *a, **kw: None)
    monkeypatch.setattr("src.chain.collect.records_for", lambda *a, **kw: [])
    # A budget already in the past: the deadline is breached immediately.
    _, diag = tg.expand_frontier(seed, T,
                                 {**tg.DEFAULTS, "time_budget_seconds": -1},
                                 now_ts=NOW)
    assert diag["status"] == "budget_exhausted"
    assert "time budget" in diag["stopped_reason"]
    assert diag["lookups"] == 0
    assert diag["frontier_remaining"] == 8, "nothing may be silently discarded"


def test_depth_limit_is_never_exceeded(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, CHAIN_PAGES)
    _, diag = tg.expand_frontier(edges(l1(T, A, 1_000_000, 10, "0xta")), T,
                                 {**tg.DEFAULTS, "max_depth": 2}, now_ts=NOW)
    assert diag["deepest_expanded"] <= 2
    assert all(q["depth"] <= 2 for q in diag["frontier_queue"])


def test_frontier_queue_truncation_is_reported_not_hidden(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    n = tg.MAX_FRONTIER_QUEUE + 25
    fan = ["0x" + f"{i:040x}" for i in range(n)]
    seed = edges(*[l1(T, w, 500_000, 10, f"0x{i}") for i, w in enumerate(fan)])
    monkeypatch.setattr("src.chain.collect.sweep_wallet", lambda *a, **kw: None)
    monkeypatch.setattr("src.chain.collect.records_for", lambda *a, **kw: [])
    _, diag = tg.expand_frontier(seed, T, {**tg.DEFAULTS, "max_expansions": 0},
                                 now_ts=NOW)
    assert len(diag["frontier_queue"]) == tg.MAX_FRONTIER_QUEUE
    assert diag["frontier_truncated"] == 25, "dropped work must be declared"
    assert diag["frontier_remaining"] == n


def test_one_wallet_reachable_at_two_depths_counts_once(monkeypatch):
    """Diamond: T->A->C and T->B->C. C is one unit of remaining work, not two."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    patch_api(monkeypatch, {
        A: [l1(A, C, 400_000, 8, "0xac")],
        B: [l1(B, C, 400_000, 8, "0xbc")],
    })
    seed = edges(l1(T, A, 500_000, 10, "0xta"), l1(T, B, 500_000, 10, "0xtb"),
                 l1(T, C, 600_000, 10, "0xtc"))
    _, diag = tg.expand_frontier(seed, T, {**tg.DEFAULTS, "max_expansions": 2},
                                 now_ts=NOW)
    queued = [q["wallet"] for q in diag["frontier_queue"]]
    assert len(queued) == len(set(queued)), f"duplicate frontier entries: {queued}"


def test_successful_retry_of_a_multi_hop_alert_marks_its_route_delivered():
    """A retry that finally lands must retire the ROUTE too, not just the
    wallet — otherwise the same chain alerts again on the following run."""
    g = build_graph(edges(
        l1(T, A, 1_000_000, 30, "0x1"),
        l1(A, B, 980_000, 20, "0x2"),
    ), T, behavioural={B: 0.9}, hl_active={B},
        correlations={B: {"confidence": 0.9, "gap_hours": 2.0}})
    first = tg.select_alerts(g, None)
    assert first
    tg.advance_alert_state(g, None, first, undelivered=[B])
    g["undelivered_alerts"] = [B]
    assert g["alerted_paths"] == [], "a failed send records nothing"

    retry = tg.select_alerts(g, g)
    assert any(a["node"]["wallet"] == B.lower() for a in retry)
    assert all("path_signature" in a for a in retry)
    tg.advance_alert_state(g, g, retry, undelivered=[])
    g["undelivered_alerts"] = []

    b_chain = next(c for c in g["chains"] if c["endpoint"] == B.lower())
    assert b_chain["signature"] in g["alerted_paths"]
    assert tg.select_alerts(g, g) == [], "the retired route must not re-alert"
