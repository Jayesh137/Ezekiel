# tests/test_frontier_retention.py
"""What survives frontier truncation, and why.

The defect these pin: truncation sorted pending wallets by (depth, address) and
kept the first N, so retention was decided alphabetically. On the live graph
that discarded 1,168 of 1,368 pending wallets — including, by construction, any
high-value relay whose address happened to sort late.

Truncation is permanent, not deferred: a wallet is queued only because its
parent was expanded, and that parent is now in `expanded_ledger` and will never
be expanded again to rediscover it. So retention order IS chase coverage.
"""

import json
import time

from src import transfer_graph as tg
from src.transfer_graph import normalise_l1_transfer

T = "0x45d26f28196d226497130c4bac709d808fed4029"
NOW = time.time()


def l1(src, dst, usd, hours_ago, ref):
    return {"from": src, "to": dst, "value": str(int(usd * 1e6)),
            "timeStamp": str(int(NOW - hours_ago * 3600)),
            "hash": ref, "tokenSymbol": "USDC"}


def edges(*rows):
    return [normalise_l1_transfer(r) for r in rows]


def addr(seed: str) -> str:
    """A well-formed address whose leading hex controls alphabetical order."""
    return "0x" + (seed * 40)[:40]


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


LOW_ALPHA = addr("0")     # sorts FIRST alphabetically
HIGH_ALPHA = addr("f")    # sorts LAST alphabetically


def run(seed_edges, budget=None, **kw):
    b = {**tg.DEFAULTS, "max_expansions": 0, **(budget or {})}
    return tg.expand_frontier(seed_edges, T, b, now_ts=NOW, **kw)


def queued(diag):
    return [q["wallet"] for q in diag["frontier_queue"]]


# --- priority beats alphabet ------------------------------------------------

def test_high_priority_wallet_is_retained_when_the_queue_exceeds_the_cap(monkeypatch):
    """A big, fast, recent relay must survive a cap of 1 against 40 rivals."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [l1(T, HIGH_ALPHA, 5_000_000, 2, "0xbig"),
            l1(HIGH_ALPHA, addr("9"), 4_950_000, 1, "0xfwd")]
    rows += [l1(T, addr(f"{i:x}") if i < 16 else "0x" + f"{i:040x}",
                2_000, 900, f"0xsmall{i}") for i in range(40)]
    _, diag = run(edges(*rows), {"max_frontier_queue": 1})
    assert diag["frontier_truncated"] > 0, "the cap must actually bite"
    assert queued(diag) == [HIGH_ALPHA], \
        f"highest-value relay was dropped; kept {queued(diag)}"


def test_alphabetical_order_cannot_override_chase_priority(monkeypatch):
    """Same depth; the alphabetically-first wallet is the weakest."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [
        l1(T, LOW_ALPHA, 500, 2000, "0xweak"),          # tiny, ancient
        l1(T, HIGH_ALPHA, 3_000_000, 1, "0xstrong"),    # large, fresh
        l1(HIGH_ALPHA, addr("7"), 2_900_000, 1, "0xf2"),  # and forwards on
    ]
    _, diag = run(edges(*rows), {"max_frontier_queue": 1})
    kept = queued(diag)
    assert kept == [HIGH_ALPHA], f"alphabetical order won: kept {kept}"
    assert LOW_ALPHA not in kept

    # Both are retained when the cap allows, and the STRONGER one ranks first.
    _, wide = run(edges(*rows), {"max_frontier_queue": 10})
    assert wide["frontier_truncated"] == 0
    assert queued(wide)[0] == HIGH_ALPHA, "ranking order must be strongest-first"


def test_lower_priority_wallet_is_discarded_first(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    tiers = [("a", 5_000_000, 1), ("b", 900_000, 5), ("c", 40_000, 200),
             ("d", 800, 3000)]
    rows = []
    for i, (seed, usd, age) in enumerate(tiers):
        onward = "0x" + f"{0xFF00 + i:040x}"   # distinct downstream wallet
        rows.append(l1(T, addr(seed), usd, age, f"0x{seed}"))
        rows.append(l1(addr(seed), onward, usd * 0.98, max(1, age - 1), f"0x{seed}f"))
    e = edges(*rows)
    assert all(x is not None for x in e), "fixture built a self-loop"
    order = []
    for cap in (1, 2, 3, 4):
        _, d = run(e, {"max_frontier_queue": cap})
        order.append(queued(d))
    # Each larger cap is a strict prefix-extension: nothing already retained is
    # displaced by widening the cap.
    for smaller, larger in zip(order, order[1:], strict=False):
        assert larger[:len(smaller)] == smaller, f"unstable order: {order}"
    assert order[0] == [addr("a")], "strongest must be retained first"
    assert addr("d") in order[-1], "weakest is retained only last"


# --- determinism ------------------------------------------------------------

def test_ordering_is_deterministic_when_priorities_tie(monkeypatch):
    """Identical wallets in every measurable respect fall back to the address —
    the only place alphabetical order is allowed to decide anything."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    same = [addr(c) for c in "37bd"]
    rows = [l1(T, w, 1_000_000, 10, f"0x{i}") for i, w in enumerate(same)]
    e = edges(*rows)
    runs = [queued(run(e, {"max_frontier_queue": 3})[1]) for _ in range(5)]
    assert all(r == runs[0] for r in runs), f"non-deterministic: {runs}"
    assert runs[0] == sorted(same)[:3], "exact ties must break on address"


def test_repeated_runs_over_identical_input_produce_identical_frontier(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [l1(T, "0x" + f"{i:040x}", 1_000 * (i + 1), i + 1, f"0x{i}")
            for i in range(60)]
    e = edges(*rows)
    a = run(e, {"max_frontier_queue": 25})[1]
    b = run(e, {"max_frontier_queue": 25})[1]
    assert a["frontier_queue"] == b["frontier_queue"]
    assert a["frontier_truncated"] == b["frontier_truncated"] == 35


# --- dedup, exclusion, counts ----------------------------------------------

def test_duplicate_frontier_entries_are_collapsed_before_ranking(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    w = addr("5")
    e = edges(l1(T, w, 900_000, 3, "0x1"), l1(T, w, 800_000, 4, "0x2"),
              l1(T, w, 700_000, 5, "0x3"))
    _, diag = run(e)
    assert queued(diag).count(w) == 1
    assert diag["frontier_eligible"] == 1

    # Duplicates arriving via the resume list are collapsed too.
    _, resumed = run(e, resume=[{"wallet": w, "depth": 1},
                                {"wallet": w, "depth": 2},
                                {"wallet": w.upper(), "depth": 1}])
    assert queued(resumed).count(w) == 1
    assert queued(resumed) == [w], "one wallet is one unit of pending work"


def test_already_expanded_wallets_are_never_requeued(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    done_w, live_w = addr("a"), addr("b")
    e = edges(l1(T, done_w, 5_000_000, 1, "0x1"), l1(T, live_w, 100, 500, "0x2"))
    _, diag = run(e, already_expanded=[done_w])
    assert done_w not in queued(diag), "finished work must not return to the queue"
    assert live_w in queued(diag)
    assert diag["skipped_already_expanded"] == 1
    # Even though done_w outranks live_w by a wide margin, it is excluded, not
    # merely deprioritised.
    assert diag["frontier_eligible"] == 1


def test_retained_and_truncated_counts_are_accurate(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [l1(T, "0x" + f"{i:040x}", 10_000, 5, f"0x{i}") for i in range(75)]
    _, diag = run(edges(*rows), {"max_frontier_queue": 30})
    assert diag["frontier_eligible"] == 75
    assert diag["frontier_retained"] == 30 == len(diag["frontier_queue"])
    assert diag["frontier_truncated"] == 45
    assert diag["frontier_cap"] == 30
    assert diag["frontier_eligible"] == (
        diag["frontier_retained"] + diag["frontier_truncated"])
    assert diag["frontier_remaining"] == 75, "remaining is eligible, not retained"


def test_no_truncation_is_reported_when_the_frontier_fits(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [l1(T, "0x" + f"{i:040x}", 10_000, 5, f"0x{i}") for i in range(12)]
    _, diag = run(edges(*rows))
    assert diag["frontier_truncated"] == 0
    assert diag["frontier_retained"] == diag["frontier_eligible"] == 12


# --- production-sized caps --------------------------------------------------

def test_cap_2000_retains_a_production_sized_1368_frontier_untruncated(monkeypatch):
    """The live frontier was 1,368. At the shipped cap it must fit entirely."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [l1(T, "0x" + f"{i:040x}", 5_000 + i, (i % 300) + 1, f"0x{i}")
            for i in range(1368)]
    _, diag = run(edges(*rows))
    assert diag["frontier_cap"] == 2000
    assert diag["frontier_eligible"] == 1368
    assert diag["frontier_retained"] == 1368
    assert diag["frontier_truncated"] == 0, "the live frontier must not be cut"


def test_queue_above_the_cap_retains_exactly_the_highest_priority_2000(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    n = 2400
    # Value ascends with index, so the strongest wallets are the LAST created —
    # and, being high-index hex, also the alphabetically last.
    rows = [l1(T, "0x" + f"{i:040x}", 1_000 * (i + 1), 5, f"0x{i}") for i in range(n)]
    _, diag = run(edges(*rows))
    assert diag["frontier_retained"] == 2000
    assert diag["frontier_truncated"] == 400
    kept = set(queued(diag))
    strongest = {"0x" + f"{i:040x}" for i in range(n - 2000, n)}
    assert kept == strongest, "retention did not follow value"
    assert "0x" + f"{0:040x}" not in kept, "weakest must be the one dropped"


def test_decisions_list_stays_bounded(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    rows = [l1(T, "0x" + f"{i:040x}", 10_000, 5, f"0x{i}") for i in range(400)]
    _, diag = run(edges(*rows), {"max_decisions": 50, "max_expansions": 0})
    assert len(diag["decisions"]) <= 50
    assert diag["decisions_truncated"] >= 0
    assert len(diag["decisions"]) + diag["decisions_truncated"] >= 1


# --- config defaults --------------------------------------------------------

def test_missing_config_keys_fall_back_to_safe_defaults(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    bare = {k: v for k, v in tg.DEFAULTS.items()
            if k not in ("max_frontier_queue", "max_decisions",
                         "max_expanded_ledger")}
    bare["max_expansions"] = 0
    _, diag = tg.expand_frontier(
        edges(l1(T, addr("3"), 900_000, 2, "0x1")), T, bare, now_ts=NOW)
    assert diag["frontier_cap"] == tg.MAX_FRONTIER_QUEUE == 2000


def test_invalid_cap_values_fall_back_rather_than_disabling_the_ceiling():
    for bad in (None, 0, -5, "", "abc", [], {}):
        assert tg._positive_int(bad, 2000) == 2000
    assert tg._positive_int(7, 2000) == 7
    assert tg._positive_int("15", 2000) == 15


def test_shipped_config_declares_the_caps():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config.json").read_text())["transfer_graph"]
    assert cfg["max_frontier_queue"] == 2000
    assert cfg["max_decisions"] == 3000
    assert cfg["max_expanded_ledger"] == 20000


# --- resume across process runs --------------------------------------------

def test_persisted_frontier_resumes_on_the_following_run(monkeypatch):
    """Run 1 caps out; run 2 receives only what was persisted and continues."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    a, b, c = addr("a"), addr("b"), addr("c")
    pages = {a: [l1(a, b, 950_000, 4, "0xab")], b: [l1(b, c, 900_000, 3, "0xbc")],
             c: []}
    monkeypatch.setattr("src.chain.collect.sweep_wallet", lambda *args, **kw: None)
    monkeypatch.setattr(
        "src.chain.collect.records_for",
        lambda w, **kw: [as_substrate_record(r) for r in pages.get(w.lower(), [])])
    seed = edges(l1(T, a, 1_000_000, 5, "0xta"))
    tight = {**tg.DEFAULTS, "max_expansions": 1}

    e1, d1 = tg.expand_frontier(seed, T, tight, now_ts=NOW)
    assert d1["wallets_expanded"] == [a]
    # Only serialisable state crosses the process boundary.
    carried = json.loads(json.dumps({"q": d1["frontier_queue"],
                                     "l": d1["expanded_ledger"]}))
    assert carried["q"], "the frontier must persist"

    _, d2 = tg.expand_frontier(e1, T, tight, resume=carried["q"],
                               already_expanded=carried["l"], now_ts=NOW)
    assert d2["wallets_expanded"] == [b], "run 2 must continue, not restart"
    assert d2["skipped_already_expanded"] == 1
    assert c in queued(d2)


def test_priority_field_survives_a_serialisation_round_trip(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    _, diag = run(edges(l1(T, addr("4"), 900_000, 2, "0x1")))
    revived = json.loads(json.dumps(diag["frontier_queue"]))
    assert revived == diag["frontier_queue"]
    assert all({"wallet", "depth", "priority"} <= set(q) for q in revived)


# --- frontier candidates are deliberately not priced ------------------------
#
# See the comment at this exact call site in src/transfer_graph.py: this job
# shares trace.yml's ~39s of slack with src/tracer.py's cluster sweep, which
# already prices the TARGET's own transfers. Frontier wallets matter less
# (topology, not alerting), so giving this call its own second CoinGecko
# budget was deliberately left out -- a regression guard, not just a report
# claim, so a later change re-does the arithmetic instead of wiring it in by
# accident.

def test_expand_frontier_does_not_pass_a_price_lookup_override(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    a = addr("a")
    captured = {}

    def fake_sweep(wallet, chains, budget, **kw):
        captured["kw"] = kw
        return None

    monkeypatch.setattr("src.chain.collect.sweep_wallet", fake_sweep)
    monkeypatch.setattr("src.chain.collect.records_for", lambda w, **kw: [])

    seed = edges(l1(T, a, 1_000_000, 5, "0xta"))
    tight = {**tg.DEFAULTS, "max_expansions": 1}
    tg.expand_frontier(seed, T, tight, now_ts=NOW)

    assert "kw" in captured, "sweep_wallet must have been called"
    assert captured["kw"].get("price_lookup") is None


# --- migration of real schema-v2 evidence -----------------------------------

def test_schema_v2_evidence_survives_migration_unchanged():
    """A COPY of a production-shaped v2 graph — never the real file."""
    live = {
        "schema_version": 2, "target": T, "computed_at": "2026-07-29T13:13:39+00:00",
        "node_count": 2, "edge_count": 2, "chain_count": 1,
        "nodes": [{"wallet": addr("a"), "classification": "MIGRATION_CANDIDATE",
                   "confidence": 0.81, "edge_ids": ["e1"], "depth": 1,
                   "path": [T, addr("a")], "continuity": {"confidence": 0.62,
                   "families": ["FLOW", "FUNDING"], "reasons": [], "blockers": []},
                   "lifecycle": {"state": "FUNDED_BY_TARGET", "reason": "r",
                                 "blockers": [], "dormant": False,
                                 "days_inactive": 3.0},
                   "chain_id": "abc123"}],
        "edges": [{"id": "e1", "src": T, "dst": addr("a"), "chain": "arbitrum",
                   "amount_usd": 1000.0, "ts": 1, "discovery_source": "l1_transfer"}],
        "chains": [{"id": "abc123", "signature": "sig1", "endpoint": addr("a"),
                    "hops": [{"src": T, "dst": addr("a"), "amount_usd": 1000.0}],
                    "hop_count": 1, "value_retained": 1.0, "breaks": [],
                    "relay_hops": [], "complete": True}],
        "services": {addr("9"): "configured service address"},
        "alerted_paths": ["sig1"], "alerted_lifecycle": {addr("a"): "FUNDED_BY_TARGET"},
        "undelivered_alerts": [addr("a")],
        "health": {"expansion": {"status": "partial", "lookups": 27,
                                 "frontier_queue": [{"wallet": addr("b"), "depth": 2}],
                                 "expanded_ledger": [addr("a"), addr("c")],
                                 "frontier_remaining": 1368,
                                 "frontier_truncated": 1168}},
    }
    snapshot = json.loads(json.dumps(live))
    m = tg.migrate_graph(live)

    assert live == snapshot, "migration must not mutate its input"
    assert m["schema_version"] == 2
    assert "migrated_from_schema" not in m, "v2 is not a downlevel migration"
    # Migration is ADDITIVE: it may introduce absent optional containers, but
    # every value the stored graph already carried must survive byte-identical.
    for before, after in zip(snapshot["nodes"], m["nodes"], strict=True):
        for key, value in before.items():
            assert after[key] == value, f"migration altered node.{key}"
    added = set(m["nodes"][0]) - set(snapshot["nodes"][0])
    assert all(m["nodes"][0][k] in (None, [], False) for k in added), \
        f"migration invented non-empty node data: {added}"
    assert m["edges"] == live["edges"]
    assert m["chains"] == live["chains"]
    assert m["services"] == live["services"]
    # Resume and delivered-alert state must all survive.
    exp = m["health"]["expansion"]
    assert exp["frontier_queue"] == live["health"]["expansion"]["frontier_queue"]
    assert exp["expanded_ledger"] == [addr("a"), addr("c")]
    assert exp["lookups"] == 27
    assert m["alerted_paths"] == ["sig1"]
    assert m["alerted_lifecycle"] == {addr("a"): "FUNDED_BY_TARGET"}
    assert m["undelivered_alerts"] == [addr("a")]
    # New reporting fields appear with safe values, and migration alone never
    # invents retention it did not perform.
    assert exp["frontier_eligible"] == 1
    assert exp["frontier_retained"] == 1
    assert exp["decisions_truncated"] == 0


def test_migration_of_v2_is_idempotent():
    g = {"schema_version": 2, "nodes": [], "edges": [], "chains": [],
         "health": {"expansion": {"status": "ok", "frontier_queue": [],
                                  "expanded_ledger": ["0x" + "a" * 40]}}}
    once = tg.migrate_graph(g)
    twice = tg.migrate_graph(json.loads(json.dumps(once)))
    assert twice == once
    assert once["health"]["expansion"]["expanded_ledger"] == ["0x" + "a" * 40]


# --- alert summary accounting ----------------------------------------------

def test_failed_alerts_are_never_reported_as_sent(capsys, monkeypatch, tmp_path):
    """The old summary said "0/4 discovery alert(s) sent" when nothing was."""
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tg, "collect_known_edges",
                        lambda: edges(l1(T, addr("a"), 900_000, 5, "0x1"),
                                      l1(addr("a"), T, 400_000, 4, "0x2"),
                                      l1(T, addr("a"), 800_000, 3, "0x3")))
    monkeypatch.setattr(tg, "_load_behavioural_scores", lambda: ({addr("a"): 0.9},
                                                                {addr("a")}))
    monkeypatch.setattr(tg, "_load_linkage_evidence", lambda: {})
    monkeypatch.setattr(tg, "_load_correlations", lambda: {})
    # Delivery always fails, exactly as an SMTP outage behaves.
    monkeypatch.setattr("src.alerts.alert_transfer_graph_discovery",
                        lambda *a, **k: False)

    graph = tg.run_transfer_graph(expand=False)
    out = capsys.readouterr().out

    assert graph["alerts_fired"] == 0
    assert graph["undelivered_alerts"], "failed alerts must be queued"
    n = len(graph["undelivered_alerts"])
    assert f"{n} failed" in out
    assert "0 delivered" in out
    assert f"{n} queued for retry" in out
    assert "attempted" in out
    # The misleading phrasing must be gone.
    assert "discovery alert(s) sent" not in out
    assert not any(line.strip().endswith("alert(s) sent")
                   for line in out.splitlines())


def test_successful_delivery_reports_delivered_not_queued(capsys, monkeypatch,
                                                          tmp_path):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tg, "collect_known_edges",
                        lambda: edges(l1(T, addr("a"), 900_000, 5, "0x1"),
                                      l1(addr("a"), T, 400_000, 4, "0x2"),
                                      l1(T, addr("a"), 800_000, 3, "0x3")))
    monkeypatch.setattr(tg, "_load_behavioural_scores", lambda: ({addr("a"): 0.9},
                                                                {addr("a")}))
    monkeypatch.setattr(tg, "_load_linkage_evidence", lambda: {})
    monkeypatch.setattr(tg, "_load_correlations", lambda: {})
    monkeypatch.setattr("src.alerts.alert_transfer_graph_discovery",
                        lambda *a, **k: True)

    graph = tg.run_transfer_graph(expand=False)
    out = capsys.readouterr().out
    assert graph["alerts_fired"] > 0
    assert graph["undelivered_alerts"] == []
    assert "0 failed" in out and "0 queued for retry" in out
    assert f"{graph['alerts_fired']} delivered" in out


# --- discovery liveness ----------------------------------------------------
# The frontier stopped expanding on 2026-09-09 and nothing noticed for two days.
# The graph rebuilt cleanly from known edges every run, so every other signal —
# node counts, roster tiers, the daily report — looked entirely normal.

def _previous_graph(tmp_path, expansion: dict) -> None:
    d = tmp_path / "transfer_graph"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps({
        "schema_version": 2, "nodes": [], "edges": [], "chains": [],
        "health": {"expansion": expansion}}))


def _quiet_graph_run(monkeypatch, tmp_path):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tg, "collect_known_edges",
                        lambda: edges(l1(T, addr("a"), 900_000, 5, "0x1")))
    monkeypatch.setattr(tg, "_load_behavioural_scores", lambda: ({}, set()))
    monkeypatch.setattr(tg, "_load_linkage_evidence", lambda: {})
    monkeypatch.setattr(tg, "_load_correlations", lambda: {})
    monkeypatch.setattr("src.alerts.alert_transfer_graph_discovery",
                        lambda *a, **k: True)


def test_a_frontier_that_has_not_moved_in_days_alerts(monkeypatch, tmp_path):
    fired = {}
    _quiet_graph_run(monkeypatch, tmp_path)
    monkeypatch.setattr("src.alerts.alert_discovery_stalled",
                        lambda **kw: fired.update(kw) or True)
    _previous_graph(tmp_path, {
        "status": "failed", "wallets_expanded": [],
        "last_expansion_at": "2026-09-09T18:56:29+00:00",
        "error": "sweep ok: could not read base, optimism, bsc",
        "frontier_remaining": 17,
        "frontier_queue": [{"wallet": addr("f"), "depth": 1, "priority": 0.91}],
    })

    tg.run_transfer_graph(expand=False)

    assert fired, "a stalled frontier must reach the operator"
    assert fired["hours"] > tg.STALL_HOURS
    assert fired["last_expansion_at"] == "2026-09-09T18:56:29+00:00"
    # What the outage is costing, not just that there is one.
    assert fired["queued"] == 17
    assert fired["top_queued"] == addr("f")


def test_a_frontier_that_moved_recently_stays_quiet(monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta
    fired = {}
    _quiet_graph_run(monkeypatch, tmp_path)
    monkeypatch.setattr("src.alerts.alert_discovery_stalled",
                        lambda **kw: fired.update(kw) or True)
    recent = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _previous_graph(tmp_path, {"status": "partial", "wallets_expanded": [],
                               "last_expansion_at": recent})

    tg.run_transfer_graph(expand=False)

    assert not fired, "a walk that moved 2h ago is healthy, not stalled"


def test_a_graph_with_no_history_never_alerts(monkeypatch, tmp_path):
    """A fresh checkout has no clock to read. Alerting here would cry wolf on
    the first run every time, which is how an operator learns to ignore it."""
    fired = {}
    _quiet_graph_run(monkeypatch, tmp_path)
    monkeypatch.setattr("src.alerts.alert_discovery_stalled",
                        lambda **kw: fired.update(kw) or True)

    tg.run_transfer_graph(expand=False)

    assert not fired


# --- the expanded ledger remembers the NEWEST work, not the lowest addresses -

def test_the_expanded_ledger_evicts_the_oldest_not_the_highest_address(monkeypatch):
    """A full ledger must drop the work finished longest ago.

    The same defect this file was opened for, in the second place that
    truncates: `expanded_ledger` was written as `sorted(explored)[:cap]`, so a
    saturated ledger kept the alphabetically LOWEST addresses and silently
    discarded everything above them. Because the ledger is what the next run
    seeds `already_expanded` from, a wallet whose address sorts high was
    forgotten the moment it was expanded and re-walked on every subsequent run
    — forever, and for free, out of a lookup budget that never drained.

    Measured on the live graph 2026-09-12: the ledger held exactly 2,000
    entries topping out at `0x7bfa…`, and ALL NINE wallets expanded that run
    sorted above it.
    """
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    # A full ledger in the order the work was actually done — oldest first.
    prior = ["0x" + f"{i:040x}" for i in range(2000)]
    newest = "0x" + "f" * 40          # sorts above every entry in `prior`
    _, diag = run(edges(l1(T, addr("1"), 900_000, 2, "0x1")),
                  {"max_expanded_ledger": 2000},
                  already_expanded=prior + [newest])

    ledger = diag["expanded_ledger"]
    assert len(ledger) == 2000, "the cap still bounds the file"
    assert newest in ledger, "the most recent work must survive eviction"
    assert prior[0] not in ledger, "eviction drops the oldest entry, not the highest"


def test_the_expanded_ledger_keeps_its_order_across_a_round_trip(monkeypatch):
    """Recency order is the ledger's meaning, so it must survive being stored.

    Sorting on write would restore the defect: the order IS the age record.
    """
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    prior = ["0x" + "f" * 40, "0x" + "a" * 40, "0x" + "c" * 40]
    _, diag = run(edges(l1(T, addr("1"), 900_000, 2, "0x1")),
                  {"max_expanded_ledger": 10}, already_expanded=prior)
    assert diag["expanded_ledger"] == prior, "stored order is preserved, not sorted"


def test_a_wallet_expanded_this_run_is_not_forgotten_by_the_cap(monkeypatch):
    """The end-to-end property: finishing work must reduce future work."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    prior = ["0x" + f"{i:040x}" for i in range(50)]
    high = "0x" + "e" * 40
    _, diag = run(edges(l1(T, addr("1"), 900_000, 2, "0x1")),
                  {"max_expanded_ledger": 50},
                  already_expanded=prior + [high])
    # Seed the next run from what the first one stored.
    _, diag2 = run(edges(l1(T, addr("1"), 900_000, 2, "0x1")),
                   {"max_expanded_ledger": 50},
                   already_expanded=diag["expanded_ledger"])
    assert high in diag2["expanded_ledger"], (
        "a wallet expanded once must still be known as expanded on the next run")


def test_the_ledger_cap_exceeds_the_frontier_it_must_remember():
    """A cap below the working set thrashes however correctly it evicts.

    Live on 2026-09-12 the graph had explored 2,017 wallets against a cap of
    2,000, so the ledger shed work on every single run no matter which end it
    dropped. Eviction order decides WHICH work is forgotten; only headroom
    stops work being forgotten at all. The ledger is ~44 bytes an address
    inside a 71MB graph file, so the headroom is close to free.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config.json").read_text())["transfer_graph"]
    assert cfg["max_expanded_ledger"] >= 20_000
    assert cfg["max_expanded_ledger"] > cfg["max_frontier_queue"], (
        "every wallet that passes through the queue eventually needs remembering")
    assert tg.MAX_EXPANDED_LEDGER == cfg["max_expanded_ledger"], (
        "the fallback default must not silently re-impose the old ceiling")


def test_the_frontier_learns_a_plan_refusal_once_per_run(monkeypatch):
    """A refused chain must cost the walk one answer, not one per wallet.

    The frontier is bounded by TIME, not by its lookup budget: live on
    2026-09-12 it stopped at 10 of 40 allowed lookups on "time budget (150s)
    exhausted" with 197 wallets still queued. Every call it spends on a chain
    Etherscan's free tier refuses is taken straight out of the only vector that
    reaches an address nobody has seen.
    """
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    seen = []

    def fake_sweep(wallet, chains, budget, **kw):
        seen.append(kw.get("plan_refused"))
        return {"status": "ok", "degraded_sources": [], "unsupported_sources": []}

    # expand_frontier imports these inside the function, so patch the source.
    from src.chain import collect as chain_collect
    monkeypatch.setattr(chain_collect, "sweep_wallet", fake_sweep)
    monkeypatch.setattr(chain_collect, "records_for", lambda w: [])
    rows = [l1(T, addr(c), 900_000, 2, f"0x{c}") for c in "123"]
    run(edges(*rows), {"max_expansions": 3})

    assert len(seen) == 3, "three wallets were swept"
    assert all(s is not None for s in seen), "the frontier must pass a refusal record"
    assert len({id(s) for s in seen}) == 1, (
        "every wallet in one walk must share ONE record, or nothing is learned")


def test_each_lookup_reports_where_the_walk_is(monkeypatch, capsys):
    """A silent step cannot be diagnosed, only guessed at.

    trace.yml's graph step failed on its 6-minute cap four runs running, having
    printed nothing between "N wallet(s) already expanded" and the timeout — so
    369 seconds were unaccounted for against a 150s internal budget, and the
    only way to tell whether the time went to network, to disk or to local
    compute was to bisect it by hand. This is the same blindness that made the
    earlier JOB timeout unreadable: "stuck at 699s having printed nothing at
    all".

    One line per lookup, carrying the wallet and the elapsed clock, turns the
    next occurrence into a reading instead of an investigation.
    """
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")

    from src.chain import collect as chain_collect
    monkeypatch.setattr(chain_collect, "sweep_wallet", lambda *a, **k: {
        "status": "ok", "degraded_sources": [], "unsupported_sources": []})
    monkeypatch.setattr(chain_collect, "records_for", lambda w: [])

    run(edges(l1(T, addr("1"), 900_000, 2, "0x1")), {"max_expansions": 1})

    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if "lookup 1/" in ln]
    assert lines, f"expected a per-lookup progress line, got:\n{out}"
    assert addr("1")[:10] in lines[0], "the line must name the wallet being swept"
    assert "s elapsed" in lines[0], "and say how much of the budget is gone"


def test_every_graph_phase_reports_its_own_clock(capsys):
    """`_phase` names each phase and how long it took.

    The graph step runs expansion, bytecode labelling, whole-chain activity
    verification and deposit inference, and three of the four make external
    calls under separate budgets. When the step hit its cap there was no way to
    say which of them spent the time.
    """
    with tg._phase("doing a thing"):
        pass
    out = capsys.readouterr().out
    assert "doing a thing" in out
    assert "s]" in out, "a phase line must carry its elapsed time"


def test_a_phase_that_raises_still_reports_its_clock(capsys):
    """The slow phase is the one most likely to die, so it must still report."""
    try:
        with tg._phase("exploding"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert "exploding" in capsys.readouterr().out


# --- one slow wallet must not be allowed to eat the whole walk --------------

def test_one_lookup_cannot_spend_the_whole_remaining_budget(monkeypatch):
    """A single wallet gets a SLICE of the clock, not everything that is left.

    Measured in production 2026-09-12 (run 34680963132): lookups 1-8 completed
    in 33.7s, then lookup 9 — `0x892785f3…` at depth 4 — started and never
    returned, and the step died on its 6-minute cap 326 seconds later. The
    frontier handed that one wallet every second remaining in the budget, and
    CLAUDE.md already states why nothing could take it back: an internal
    `time_budget_seconds` is checked BETWEEN calls and cannot interrupt one that
    never returns.

    Two costs, and the second is the one that matters. The walk stops dead — the
    other 195 queued wallets get nothing. And because the STEP is killed, the
    graph is never written, so the frontier never advances past the wallet that
    hung it: the same wallet is retried on every run, forever. That is the
    `expanded_ledger` livelock again, arriving by a different road.

    Slicing the clock per lookup bounds the damage to one slice and lets the
    run finish and PERSIST, which is what carries the frontier past it.
    """
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    seen = []

    def fake_sweep(wallet, chains, budget, **kw):
        seen.append(budget.seconds)
        return {"status": "ok", "degraded_sources": [], "unsupported_sources": []}

    from src.chain import collect as chain_collect
    monkeypatch.setattr(chain_collect, "sweep_wallet", fake_sweep)
    monkeypatch.setattr(chain_collect, "records_for", lambda w: [])

    rows = [l1(T, addr(c), 900_000, 2, f"0x{c}") for c in "123"]
    run(edges(*rows), {"max_expansions": 3, "time_budget_seconds": 150})

    assert seen, "expected at least one sweep"
    assert all(s <= tg.LOOKUP_SECONDS for s in seen), (
        f"no single lookup may hold more than LOOKUP_SECONDS, got {seen}")


def test_a_lookup_slice_never_outlives_the_walk_deadline():
    """Near the end of the walk the slice shrinks; it never extends the walk."""
    assert tg.lookup_seconds(remaining=1000.0) == tg.LOOKUP_SECONDS
    assert tg.lookup_seconds(remaining=5.0) == 5.0
    assert tg.lookup_seconds(remaining=0.0) == 0.0
    assert tg.lookup_seconds(remaining=-3.0) == 0.0


def test_the_slice_is_smaller_than_the_walk_and_the_walk_fits_the_step():
    """The three clocks have to nest, or the outer one is the only real one.

    lookup slice < frontier time budget < the graph step's timeout-minutes in
    trace.yml. Pinning it here because the numbers live in three files and only
    their ORDER is the invariant.
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config.json").read_text())["transfer_graph"]
    wf = (root / ".github" / "workflows" / "trace.yml").read_text()
    step = wf.split("Rebuild transfer graph", 1)[1]
    step_seconds = int(re.search(r"timeout-minutes:\s*(\d+)", step).group(1)) * 60

    assert tg.LOOKUP_SECONDS < cfg["time_budget_seconds"]
    assert cfg["time_budget_seconds"] < step_seconds, (
        "the walk must finish inside the step, or the step timeout IS the budget")
