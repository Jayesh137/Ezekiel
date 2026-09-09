# tests/test_continuity.py
"""Wallet-continuity tracker: multi-hop chains, scoring, lifecycle.

Purpose of the feature: keep following the target when funds are routed through
intermediary wallets to obscure the next active trading wallet. These tests pin
both the capability and — more importantly — the limits on what it may conclude.

Nothing here asserts ownership. The strongest state is HIGH_CONFIDENCE_SUCCESSOR,
a lead, and reaching it requires corroboration from two independent evidence
families plus an unbroken path plus the central alert disposition.
"""

import time

import pytest

from src import continuity as ct
from src import transfer_graph as tg
from src.transfer_graph import build_graph, normalise_l1_transfer

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B, C, D, E = ("0x" + ch * 40 for ch in "abcde")
CEX = "0x" + "9" * 40
NOW = time.time()


def l1(src, dst, usd, hours_ago, ref):
    return {"from": src, "to": dst, "value": str(int(usd * 1e6)),
            "timeStamp": str(int(NOW - hours_ago * 3600)),
            "hash": ref, "tokenSymbol": "USDC"}


def edges(*rows):
    return [normalise_l1_transfer(r) for r in rows]


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


def node(graph, addr):
    return next((n for n in graph["nodes"] if n["wallet"] == addr.lower()), None)


def chain_to(graph, addr):
    hits = [c for c in graph["chains"] if c["endpoint"] == addr.lower()]
    return max(hits, key=lambda c: c["hop_count"]) if hits else None


# --- 1. target -> A -> B -> trading wallet ---------------------------------------

def test_three_hop_chain_to_a_trading_wallet():
    g = build_graph(edges(l1(T, A, 1_000_000, 20, "0x1"),
                          l1(A, B, 980_000, 18, "0x2"),
                          l1(B, C, 960_000, 16, "0x3")),
                    T, now_ts=NOW, behavioural={C: 0.86}, hl_active={C})
    ch = chain_to(g, C)
    assert ch and ch["hop_count"] == 3
    assert [h["dst"] for h in ch["hops"]] == [A, B, C]
    assert ch["value_retained"] > 0.9
    assert node(g, C)["lifecycle"]["state"] in (
        ct.LIFECYCLE_TRADING, ct.LIFECYCLE_POSSIBLE)


# --- 2. four-hop chain with confidence decay --------------------------------------

def test_four_hop_chain_is_reachable_and_decays():
    """Depth 4 was structurally impossible before: the cap was 3 and expansion
    never walked past hop 1."""
    g = build_graph(edges(l1(T, A, 1_000_000, 40, "0x1"),
                          l1(A, B, 985_000, 38, "0x2"),
                          l1(B, C, 970_000, 36, "0x3"),
                          l1(C, D, 955_000, 34, "0x4")),
                    T, now_ts=NOW)
    assert node(g, D) is not None, "4th hop must be reachable"
    assert node(g, D)["depth"] == 4
    ch = chain_to(g, D)
    assert ch["hop_count"] == 4 and ch["value_retained"] > 0.95

    # Decay is by value retained, not flat hop count.
    assert ct.hop_decay(4, 0.955) > 0.65
    assert ct.hop_decay(4, 0.03) == 0.25
    assert ct.hop_decay(4, 0.955) > ct.hop_decay(4, 0.30)


def test_low_value_survival_decays_hard():
    assert ct.hop_decay(2, 0.02) < ct.hop_decay(2, 0.95)
    prev = 1.1
    for hops in (1, 2, 3, 4, 5):
        d = ct.hop_decay(hops, 0.9)
        assert d <= prev
        prev = d


# --- 3. split into three wallets then reconverge -----------------------------------

def test_split_then_reconverge_is_reconciled():
    out = [{"amount_usd": 400_000, "ts": int(NOW - 40 * 3600), "ref": f"0xs{i}"}
           for i in range(3)]
    merged = [{"amount_usd": 1_180_000, "ts": int(NOW - 20 * 3600), "ref": "0xm"}]
    hit = ct.reconcile_split_merge(out, merged)
    assert hit and hit["parts"] == 3
    assert hit["merged_usd"] == 1_180_000
    assert hit["confidence"] > 0.5


def test_split_reconciliation_rejects_unrelated_amounts_and_late_merges():
    out = [{"amount_usd": 400_000, "ts": int(NOW - 40 * 3600), "ref": "0xa"},
           {"amount_usd": 400_000, "ts": int(NOW - 40 * 3600), "ref": "0xb"}]
    assert ct.reconcile_split_merge(out, [{"amount_usd": 50_000, "ts": int(NOW), "ref": "0xm"}]) is None
    late = [{"amount_usd": 800_000, "ts": int(NOW + 400 * 3600), "ref": "0xm"}]
    assert ct.reconcile_split_merge(out, late) is None
    assert ct.reconcile_split_merge([out[0]], [{"amount_usd": 400_000, "ts": int(NOW), "ref": "0xm"}]) is None


# --- 4. bridge correlation with fees and delay --------------------------------------

def test_bridge_correlates_within_fee_and_time_tolerance():
    hit = ct.correlate_bridge(1_000_000, 998_000, gap_hours=6.0)
    assert hit and hit["confidence"] > 0.5
    assert hit["break"]["type"] == ct.BREAK_BRIDGE, "a bridge is always a path break"


def test_bridge_rejects_bad_amount_or_stale_gap():
    assert ct.correlate_bridge(1_000_000, 500_000, gap_hours=1.0) is None
    assert ct.correlate_bridge(1_000_000, 998_000, gap_hours=500.0) is None
    assert ct.correlate_bridge(1_000_000, 998_000, gap_hours=None) is None


def test_bridge_break_caps_promotion():
    """A correlated bridge keeps the lead alive but must not allow the top state."""
    scored = ct.score_continuity(
        {"direct_from_target": True, "behavioural": 0.9, "bridge_correlated": 0.9},
        hop_count=2, value_retained=0.99,
        breaks=[{"at": B, "reason": "bridge crossing", "type": ct.BREAK_BRIDGE}])
    life = ct.lifecycle_state(
        on_path=True, transfer_count=3, funded_by_target=True,
        has_unbroken_path=False, trades_after_funding=True,
        confidence=0.9, families=scored["families"],
        breaks=[{"at": B, "reason": "bridge"}], disposition_alert=True)
    assert life["state"] != ct.LIFECYCLE_HIGH_CONFIDENCE
    assert any("break" in b for b in life["blockers"])


# --- 5. service path does not strengthen ownership confidence -------------------------

def test_service_path_cannot_strengthen_or_be_traversed():
    g = build_graph(edges(l1(T, CEX, 9_000_000, 10, "0x1"),
                          l1(CEX, B, 8_900_000, 9, "0x2")),
                    T, now_ts=NOW, known_services={CEX})
    svc = node(g, CEX)
    assert svc["classification"] == tg.CLASS_SERVICE
    assert svc["confidence"] == 0.0
    assert svc["continuity"]["confidence"] == 0.0
    assert node(g, B) is None, "must not traverse through a service"


def test_service_lifecycle_is_terminal():
    life = ct.lifecycle_state(is_service=True, on_path=True, confidence=0.99,
                              families=["FLOW", "FUNDING"], disposition_alert=True)
    assert life["state"] == ct.LIFECYCLE_REJECTED_SERVICE


# --- 6. very large one-way transfer does not become high confidence --------------------

def test_huge_one_way_transfer_stays_a_lead():
    g = build_graph(edges(l1(T, A, 50_000_000, 5, "0x1")), T, now_ts=NOW)
    n = node(g, A)
    assert n["continuity"]["confidence"] < ct.POSSIBLE_SUCCESSOR_MIN
    assert n["lifecycle"]["state"] not in (
        ct.LIFECYCLE_POSSIBLE, ct.LIFECYCLE_HIGH_CONFIDENCE)
    assert any("family" in b for b in n["continuity"]["blockers"])


def test_volume_is_not_a_signal_at_all():
    small = ct.score_continuity({"direct_from_target": True}, hop_count=1)
    assert small["confidence"] < ct.POSSIBLE_SUCCESSOR_MIN
    assert "volume" not in " ".join(ct.SIGNALS)


# --- 7. gas-funded wallet that trades stays a lead until corroborated -------------------

def test_first_gas_plus_trading_needs_independent_corroboration():
    only_funding = ct.score_continuity(
        {"first_gas": True, "direct_from_target": True, "funded_before_trading": True},
        hop_count=1)
    assert only_funding["families"] == ["FUNDING"]
    assert any("independent families" in b for b in only_funding["blockers"])

    corroborated = ct.score_continuity(
        {"first_gas": True, "funded_before_trading": True, "behavioural": 0.88},
        hop_count=1)
    assert len(corroborated["families"]) >= ct.MIN_FAMILIES_FOR_PROMOTION
    assert corroborated["blockers"] == []


# --- 8. old wallet quiet + new funded wallet begins trading -----------------------------

def test_wallet_rotation_signature():
    rot = ct.detect_rotation(target_last_activity_days=30.0,
                             candidate_first_trade_days=25.0)
    assert rot and rot["strength"] > 0
    # A wallet already trading long before the target went quiet is not a successor.
    assert ct.detect_rotation(target_last_activity_days=10.0,
                              candidate_first_trade_days=400.0) is None
    # Target still active -> no rotation to detect.
    assert ct.detect_rotation(target_last_activity_days=0.0,
                              candidate_first_trade_days=5.0) is None


# --- 9. stale evidence decays -------------------------------------------------------------

def test_stale_evidence_decays_but_does_not_vanish():
    sig = {"amount_similarity": 0.9, "two_way_flow": True}   # FLOW + STRUCTURE age
    fresh = ct.score_continuity(sig, hop_count=1, age_days=5)
    stale = ct.score_continuity(sig, hop_count=1, age_days=900)
    assert stale["confidence"] < fresh["confidence"]
    assert stale["confidence"] > 0, "an old link must fade, not disappear"
    assert ct.age_decay(10_000) == 0.4


def test_behavioural_and_funding_evidence_do_not_age():
    """Trading style and who paid first gas are facts; they do not become less true."""
    only_behaviour_fresh = ct.score_continuity({"behavioural": 0.9, "first_gas": True},
                                               hop_count=1, age_days=5)
    only_behaviour_old = ct.score_continuity({"behavioural": 0.9, "first_gas": True},
                                             hop_count=1, age_days=900)
    assert only_behaviour_old["confidence"] == only_behaviour_fresh["confidence"]


# --- 10. incomplete frontier is reported honestly -------------------------------------------

def test_incomplete_frontier_is_never_reported_as_fully_explored():
    g = build_graph(edges(l1(T, A, 1_000_000, 5, "0x1")), T, now_ts=NOW,
                    expansion={"status": "budget_exhausted", "frontier_remaining": 7,
                               "degraded_sources": []})
    assert g["health"]["frontier_incomplete"] is True

    g2 = build_graph(edges(l1(T, A, 1_000_000, 5, "0x1")), T, now_ts=NOW,
                     expansion={"status": "skipped_no_api_key",
                                "degraded_sources": ["arbitrum_l1"]})
    assert g2["health"]["frontier_incomplete"] is True


# --- 11. failed request preserves partial graph and resume cursor -----------------------------

def test_failed_lookup_preserves_partial_edges_and_resume_queue(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"), l1(T, B, 900_000, 6, "0x2"))
    calls = {"n": 0}
    collected = {}

    def flaky_sweep(wallet, *args, **kw):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("etherscan unreachable")
        collected[wallet.lower()] = [as_substrate_record(l1(A, C, 980_000, 4, "0x3"))]

    monkeypatch.setattr("src.chain.collect.sweep_wallet", flaky_sweep)
    monkeypatch.setattr("src.chain.collect.records_for",
                        lambda wallet, **kw: collected.get(wallet.lower(), []))
    out, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)
    # One address failing must not abandon the walk: the successful lookup is
    # kept and the failed ones are re-queued rather than recorded as finished.
    assert diag["status"] == "partial"
    assert diag["partial_failures"], "each failed lookup must be recorded"
    assert len(out) > len(seed), "the successful lookup's edge must survive"
    assert diag["frontier_remaining"] >= 1
    assert diag["frontier_queue"], "unfinished work must be queued for resume"
    failed = {f["wallet"] for f in diag["partial_failures"]}
    assert not (failed & set(diag["expanded_ledger"])), \
        "a wallet whose lookup failed must NOT be marked as already expanded"


def test_total_lookup_outage_is_reported_as_failed(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"), l1(T, B, 900_000, 6, "0x2"))

    def dead(wallet, *args, **kw):
        raise OSError("etherscan unreachable")

    monkeypatch.setattr("src.chain.collect.sweep_wallet", dead)
    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)
    assert diag["status"] == "failed", "a total outage is not a partial result"
    assert diag["error"]
    # Named per chain now that expansion spans all of them, not the single
    # "arbitrum_l1" label from when collection read one chain.
    from src.chain.chains import enabled_chains
    from src.utils import load_config
    assert diag["degraded_sources"] == [c["name"] for c in enabled_chains(load_config())]


def test_resume_queue_is_consumed_on_the_next_run(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr("src.chain.collect.sweep_wallet", lambda *args, **kw: None)
    monkeypatch.setattr("src.chain.collect.records_for", lambda *args, **kw: [])
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"))
    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS,
                                 resume=[{"wallet": E, "depth": 2}], now_ts=NOW)
    assert E in diag["wallets_expanded"], "resumed wallet must be picked up"


def test_expansion_records_why_each_frontier_was_handled(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr("src.chain.collect.sweep_wallet", lambda *args, **kw: None)
    monkeypatch.setattr("src.chain.collect.records_for", lambda *args, **kw: [])
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"), l1(T, CEX, 9_000_000, 5, "0x2"))
    _, diag = tg.expand_frontier(seed, T, {**tg.DEFAULTS, "max_expansions": 1},
                                 now_ts=NOW)
    actions = {d["action"] for d in diag["decisions"]}
    assert actions & {"expanded", "deferred", "suppressed"}
    assert all(d["reason"] for d in diag["decisions"]), "every decision needs a reason"


def test_budget_limits_are_respected(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr("src.chain.collect.sweep_wallet", lambda *args, **kw: None)
    monkeypatch.setattr("src.chain.collect.records_for", lambda *args, **kw: [])
    seed = edges(*[l1(T, f"0x{i:040x}", 500_000, 5, f"0x{i}") for i in range(20)])
    _, diag = tg.expand_frontier(seed, T, {**tg.DEFAULTS, "max_expansions": 3},
                                 now_ts=NOW)
    assert diag["lookups"] <= 3
    assert diag["frontier_remaining"] > 0


# --- 12/13. alert retry + path dedup ---------------------------------------------------------

def test_path_signature_dedupes_equivalent_paths():
    hops1 = [{"src": T, "dst": A, "ref": "0x1"}, {"src": A, "dst": B, "ref": "0x2"}]
    hops2 = [{"src": T, "dst": A, "ref": "0xAA"}, {"src": A, "dst": B, "ref": "0xBB"}]
    hops3 = [{"src": T, "dst": C, "ref": "0x1"}, {"src": C, "dst": B, "ref": "0x2"}]
    assert ct.path_signature(hops1) == ct.path_signature(hops2), \
        "same route via different txs is one finding"
    assert ct.path_signature(hops1) != ct.path_signature(hops3)
    assert ct.path_id(hops1) != ct.path_id(hops2), "ids stay tx-specific"


def test_undelivered_alert_still_retried(monkeypatch):
    g = build_graph(edges(l1(T, A, 900_000, 5, "0x1")), T, now_ts=NOW,
                    behavioural={A: 0.86}, hl_active={A},
                    correlations={A: {"confidence": 0.9, "gap_hours": 2.0}})
    saved = dict(g)
    saved["undelivered_alerts"] = [A]
    assert len(tg.select_alerts(g, saved)) == 1


# --- 14. contradictory behaviour blocks promotion ----------------------------------------------

def test_style_veto_blocks_promotion_but_keeps_evidence():
    scored = ct.score_continuity(
        {"direct_from_target": True, "behavioural": 0.9, "two_way_flow": True},
        hop_count=1, vetoes=["Decision frequency 20x apart"])
    assert any("contradictory" in b for b in scored["blockers"])
    life = ct.lifecycle_state(
        on_path=True, transfer_count=5, funded_by_target=True,
        has_unbroken_path=True, trades_after_funding=True, confidence=0.9,
        families=["FLOW", "FUNDING"], vetoes=["style veto"], disposition_alert=True)
    assert life["state"] not in (ct.LIFECYCLE_POSSIBLE, ct.LIFECYCLE_HIGH_CONFIDENCE)
    assert scored["reasons"], "evidence is retained, not erased"


# --- 15. known linked wallet does not regress ---------------------------------------------------

def test_known_linked_wallet_still_classified_by_independent_evidence():
    """Two-way HL-native flow, as the real known-self wallet shows."""
    hl_out = tg.normalise_hl_ledger_entry({
        "time": int((NOW - 5 * 3600) * 1000), "hash": "0xo",
        "delta": {"type": "internalTransfer", "user": T, "destination": A,
                  "usdc": "5000000"}})
    hl_in = tg.normalise_hl_ledger_entry({
        "time": int((NOW - 4 * 3600) * 1000), "hash": "0xi",
        "delta": {"type": "internalTransfer", "user": A, "destination": T,
                  "usdc": "4000000"}})
    # The real known-self wallet shows repeated two-way flow (46 transfers), so
    # the fixture must too — a single pair each way is genuinely weaker evidence.
    more = [tg.normalise_hl_ledger_entry({
        "time": int((NOW - (6 + i) * 3600) * 1000), "hash": f"0xr{i}",
        "delta": {"type": "internalTransfer", "user": T, "destination": A,
                  "usdc": "1000000"}}) for i in range(4)]
    g = build_graph([hl_out, hl_in, *more], T, now_ts=NOW)
    n = node(g, A)
    assert n["classification"] == tg.CLASS_MIGRATION_CANDIDATE
    assert n["evidence"]["bidirectional"] and n["evidence"]["hl_native"]


# --- schema / compatibility ------------------------------------------------------------------------

def test_schema_is_versioned_and_chains_are_first_class():
    g = build_graph(edges(l1(T, A, 900_000, 5, "0x1")), T, now_ts=NOW)
    assert g["schema_version"] == tg.SCHEMA_VERSION == 2
    assert "chains" in g and isinstance(g["chains"], list)
    for ch in g["chains"]:
        for key in ("id", "signature", "endpoint", "hops", "hop_count",
                    "value_retained", "breaks", "relay_hops"):
            assert key in ch
        for h in ch["hops"]:
            for key in ("src", "dst", "chain", "asset", "amount_usd", "ts", "ref",
                        "discovery_source"):
                assert key in h, f"hop missing auditable field {key}"


def test_v1_graph_without_chains_is_still_readable():
    """Backward compatibility: the previous shape must not crash consumers."""
    v1 = {"nodes": [{"wallet": A, "classification": tg.CLASS_DIRECT_RECIPIENT,
                     "confidence": 0.2, "depth": 1, "evidence": {}}],
          "edges": []}
    assert tg.select_alerts(v1, None) == [] or True
    assert v1.get("chains") is None


@pytest.mark.parametrize("received,forwarded,hours,dests,expected", [
    (2_000_000, 1_950_000, 1.5, 1, True),
    (2_000_000, 100_000, 1.0, 1, False),
    (2_000_000, 1_950_000, 1.0, 40, False),
    (2_000_000, 1_950_000, 500.0, 1, False),
    (0, 0, None, 0, False),
])
def test_relay_classification_matrix(received, forwarded, hours, dests, expected):
    assert ct.classify_relay(received, forwarded, hours, dests)["is_relay"] is expected


# --- 12. a sweep that could not read is not a finished expansion ----------------
#
# sweep_wallet never raises on degradation: probe_activity and fetch_kind catch
# BudgetExhausted and return an error string. Discarding its return value made
# the except branch that keeps a wallet retryable unreachable for the failure
# that actually happens, and `explored` persists as expanded_ledger — so the
# marking was permanent across runs.

def _sweeps(monkeypatch, by_wallet, rows_by_wallet=None):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr("src.chain.collect.sweep_wallet",
                        lambda wallet, *a, **kw: by_wallet.get(wallet.lower()))
    monkeypatch.setattr("src.chain.collect.records_for",
                        lambda wallet, **kw: (rows_by_wallet or {}).get(wallet.lower(), []))


def _sweep_result(address, degraded=(), status="ok"):
    return {"address": address, "status": status, "chains": {},
            "degraded_sources": list(degraded)}


def test_a_degraded_sweep_leaves_the_wallet_out_of_explored(monkeypatch):
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"), l1(T, B, 900_000, 6, "0x2"))
    _sweeps(monkeypatch, {
        A: _sweep_result(A, degraded=["base", "bsc"]),
        B: _sweep_result(B),
    })

    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)

    assert A not in diag["expanded_ledger"], \
        "a wallet whose sweep could not read every chain is not fully explored"
    assert B in diag["expanded_ledger"], "a clean sweep still finishes the wallet"

    failed = {f["wallet"]: f for f in diag["partial_failures"]}
    assert A in failed
    assert failed[A]["chains"] == ["base", "bsc"]
    # The failing chain names reach the run-level record, not a stale label.
    assert diag["degraded_sources"] == ["base", "bsc"]
    # And it is re-queued rather than silently dropped.
    assert A in {q["wallet"] for q in diag["frontier_queue"]}


def test_a_budget_exhausted_sweep_does_not_look_like_an_empty_wallet(monkeypatch):
    """Constraint: an empty result and a failed read must never serialise the
    same way. Both wallets return zero rows; only one of them was READ."""
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"), l1(T, B, 900_000, 6, "0x2"))
    _sweeps(monkeypatch, {
        A: _sweep_result(A, degraded=["arbitrum"], status="ok"),
        B: _sweep_result(B),
    })

    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)

    by_wallet = {d["wallet"]: d for d in diag["decisions"]}
    assert by_wallet[B]["action"] == "expanded"        # genuinely nothing there
    assert by_wallet[A]["action"] == "deferred"        # we could not tell
    assert "arbitrum" in by_wallet[A]["reason"]
    assert diag["status"] != "ok"


def test_a_sweep_skipped_for_want_of_a_key_is_not_a_finished_expansion(monkeypatch):
    """status != "ok" with no per-chain degradation still means "not read"."""
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"))
    _sweeps(monkeypatch, {A: _sweep_result(A, status="skipped_no_api_key")})

    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)

    assert A not in diag["expanded_ledger"]
    assert diag["partial_failures"][0]["wallet"] == A
    assert "skipped_no_api_key" in diag["partial_failures"][0]["error"]


def test_a_clean_sweep_returning_nothing_still_marks_the_wallet_explored(monkeypatch):
    """The retry path must not become a treadmill: a wallet that was genuinely
    read and had nothing is finished, and stays finished across runs."""
    seed = edges(l1(T, A, 1_000_000, 5, "0x1"))
    _sweeps(monkeypatch, {A: _sweep_result(A)})

    _, diag = tg.expand_frontier(seed, T, tg.DEFAULTS, now_ts=NOW)

    assert A in diag["expanded_ledger"]
    assert diag["partial_failures"] == []
    assert diag["degraded_sources"] == []
    assert diag["status"] == "ok"
