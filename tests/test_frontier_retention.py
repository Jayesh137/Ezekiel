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
    assert cfg["max_expanded_ledger"] == 2000


# --- resume across process runs --------------------------------------------

def test_persisted_frontier_resumes_on_the_following_run(monkeypatch):
    """Run 1 caps out; run 2 receives only what was persisted and continues."""
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    a, b, c = addr("a"), addr("b"), addr("c")
    pages = {a: [l1(a, b, 950_000, 4, "0xab")], b: [l1(b, c, 900_000, 3, "0xbc")],
             c: []}
    monkeypatch.setattr("src.tracer.get_usdc_transfers",
                        lambda w, start_block=0: list(pages.get(w.lower(), [])))
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
