# tests/test_graph_edge_persistence.py
"""The stored graph must fit in a git push, without lying about what it holds.

`data/transfer_graph/latest.json` reached 107 MB and GitHub rejects any file over
100 MiB, so from 2026-09-12 14:02 UTC EVERY "Trace Fund Flows" run computed the
graph and then threw it away at the push step — the pre-receive hook declined it
and the whole job failed. Discovery is the only vector that reaches an address
nobody has seen, and it had stopped being able to save its work.

81.4 MB of that was `edges`: 212,457 of them, one node alone carrying 44,196
edge_ids. Nothing reads the stored array back — `collect_known_edges()` rebuilds
it from `data/transfers/` on every run, and `annotate_changes`, `select_alerts`,
`advance_alert_state` and `migrate_graph` never touch it. Its only consumers are
the alert body, which takes the earliest 15 of a node's edges from the IN-MEMORY
graph before the save, and the dashboard, which sorts a node's edges newest-first
and shows 40.

So the file was carrying 212,457 edges to serve a maximum of 40. It is capped at
save time only — the graph `fire_alerts` sees is untouched — and the cap is
declared in the file, because a truncated list that looks complete is rule 5.
`edge_count` and `node.totals.edge_count` stay the TRUE totals; they are computed
from the full set and are what a reader should believe.
"""

from src import transfer_graph as tg


def _graph(nodes, edges):
    return {"edge_count": len(edges), "nodes": nodes, "edges": edges}


def _edge(eid, ts, amount=1.0):
    return {"id": eid, "ts": ts, "amount_usd": amount, "src": "0xa", "dst": "0xb"}


def test_an_edge_no_node_references_is_not_stored():
    g = _graph([{"wallet": "0xa", "edge_ids": ["keep"], "totals": {"edge_count": 1}}],
               [_edge("keep", 100), _edge("orphan", 200)])
    out = tg.trim_edges_for_storage(g, cap=10)
    assert [e["id"] for e in out["edges"]] == ["keep"]


def test_a_node_over_the_cap_keeps_its_most_RECENT_edges():
    """Newest-first is what the dashboard shows, so keep exactly those."""
    edges = [_edge(f"e{i}", ts=i) for i in range(10)]
    g = _graph([{"wallet": "0xa", "edge_ids": [e["id"] for e in edges],
                 "totals": {"edge_count": 10}}], edges)
    out = tg.trim_edges_for_storage(g, cap=3)
    assert sorted(e["id"] for e in out["edges"]) == ["e7", "e8", "e9"]


def test_the_true_edge_count_is_never_trimmed_with_the_list():
    """How many exist is a fact; which ones we kept is a storage decision."""
    edges = [_edge(f"e{i}", ts=i) for i in range(10)]
    g = _graph([{"wallet": "0xa", "edge_ids": [e["id"] for e in edges],
                 "totals": {"edge_count": 10}}], edges)
    out = tg.trim_edges_for_storage(g, cap=2)
    assert out["edge_count"] == 10
    assert out["nodes"][0]["totals"]["edge_count"] == 10
    assert len(out["nodes"][0]["edge_ids"]) == 10


def test_a_truncated_file_says_so():
    edges = [_edge(f"e{i}", ts=i) for i in range(10)]
    g = _graph([{"wallet": "0xa", "edge_ids": [e["id"] for e in edges],
                 "totals": {"edge_count": 10}}], edges)
    out = tg.trim_edges_for_storage(g, cap=2)
    assert out["edges_truncated"] is True
    assert out["edges_per_node_cap"] == 2
    assert out["edges_stored"] == 2


def test_a_graph_under_the_cap_is_not_marked_truncated():
    g = _graph([{"wallet": "0xa", "edge_ids": ["e1"], "totals": {"edge_count": 1}}],
               [_edge("e1", 5)])
    out = tg.trim_edges_for_storage(g, cap=10)
    assert out["edges_truncated"] is False
    assert [e["id"] for e in out["edges"]] == ["e1"]


def test_an_edge_shared_by_two_nodes_is_stored_once():
    shared = _edge("shared", 1)
    g = _graph([{"wallet": "0xa", "edge_ids": ["shared"], "totals": {"edge_count": 1}},
                {"wallet": "0xb", "edge_ids": ["shared"], "totals": {"edge_count": 1}}],
               [shared])
    out = tg.trim_edges_for_storage(g, cap=10)
    assert [e["id"] for e in out["edges"]] == ["shared"]


def test_trimming_never_mutates_the_graph_the_alerts_used():
    """fire_alerts runs BEFORE the save and must keep every edge."""
    edges = [_edge(f"e{i}", ts=i) for i in range(10)]
    g = _graph([{"wallet": "0xa", "edge_ids": [e["id"] for e in edges],
                 "totals": {"edge_count": 10}}], edges)
    tg.trim_edges_for_storage(g, cap=1)
    assert len(g["edges"]) == 10
    assert "edges_truncated" not in g


def test_an_edge_with_no_timestamp_is_still_storable():
    g = _graph([{"wallet": "0xa", "edge_ids": ["a", "b"], "totals": {"edge_count": 2}}],
               [{"id": "a", "ts": None}, {"id": "b", "ts": 5}])
    out = tg.trim_edges_for_storage(g, cap=1)
    assert [e["id"] for e in out["edges"]] == ["b"]


def test_the_cap_is_far_above_every_consumer():
    """15 in an alert body, 40 on the dashboard. The cap must not bind on them."""
    assert tg.PERSISTED_EDGES_PER_NODE >= 100
