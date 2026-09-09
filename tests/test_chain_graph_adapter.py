# tests/test_chain_graph_adapter.py
import json

from src import transfer_graph as tg


def record(**kw):
    base = {
        "id": "base:0xhash:erc20:0", "chain": "base", "chain_id": 8453,
        "block": 100, "ts": 1781000000, "timestamp": "2026-06-16T00:00:00+00:00",
        "tx_hash": "0xhash", "src": "0xtarget", "dst": "0xdest", "kind": "erc20",
        "asset": "USDC", "token_address": "0xaf88", "amount": 5_000_000.0,
        "amount_usd": 5_000_000.0, "value_basis": "stable_par",
        "spam": False, "spam_reason": None,
    }
    base.update(kw)
    return base


def test_a_normalised_record_becomes_a_graph_edge_on_its_own_chain():
    edge = tg.normalise_transfer_record(record())
    assert edge["src"] == "0xtarget" and edge["dst"] == "0xdest"
    assert edge["chain"] == "base"
    assert edge["asset"] == "USDC"
    assert edge["amount_usd"] == 5_000_000.0
    assert edge["ts"] == 1781000000
    assert edge["discovery_source"] == tg.SRC_L1


def test_a_quarantined_record_never_becomes_an_edge():
    assert tg.normalise_transfer_record(record(spam=True, spam_reason="lookalike")) is None


def test_an_unpriced_record_never_becomes_an_edge():
    """An unpriced token must not be able to satisfy a value threshold."""
    assert tg.normalise_transfer_record(
        record(amount_usd=None, value_basis="unpriced", asset="SCAM")) is None


def test_a_self_transfer_never_becomes_an_edge():
    assert tg.normalise_transfer_record(record(dst="0xtarget")) is None


def test_the_same_transfer_from_legacy_and_new_storage_collapses_to_one_edge():
    """data/l1_transactions is the only copy of some history, so both readers
    stay live. The same movement must not be counted twice."""
    legacy = tg.normalise_l1_transfer({
        "from": "0xtarget", "to": "0xdest", "value": "5000000000000",
        "timeStamp": "1781000000", "hash": "0xhash", "tokenSymbol": "USDC"})
    fresh = tg.normalise_transfer_record(record(chain="arbitrum", chain_id=42161))
    assert legacy["id"] == fresh["id"]
    assert len(tg.dedupe_edges([legacy, fresh])) == 1


def test_collect_known_edges_reads_every_chain_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    for chain in ("arbitrum", "base"):
        d = tmp_path / "transfers" / chain
        d.mkdir(parents=True)
        (d / "2026-08-28.json").write_text(json.dumps([
            record(chain=chain, id=f"{chain}:0xh:erc20:0", tx_hash=f"0xh{chain}")]))

    edges = tg.collect_known_edges()
    assert {e["chain"] for e in edges} == {"arbitrum", "base"}


def test_collect_known_edges_still_reads_legacy_l1_transactions(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    legacy = tmp_path / "l1_transactions"
    legacy.mkdir(parents=True)
    (legacy / "2026-06-16.json").write_text(json.dumps([{
        "from": "0xtarget", "to": "0xlegacy", "value": "5000000000000",
        "timeStamp": "1781000000", "hash": "0xold", "tokenSymbol": "USDC"}]))

    edges = tg.collect_known_edges()
    assert any(e["dst"] == "0xlegacy" for e in edges)


# --- fund_flows findings: must carry their own asset/chain, not the old --------
# --- hardcoded Arbitrum-USDC assumption -----------------------------------------

def _finding(**kw):
    base = {"source": "0xtarget", "destination": "0xdest", "tx_hash": "0xh",
            "amount_usdc_raw": 5000.0, "hop_count": 1,
            "detected_at": "2026-06-16T00:00:00+00:00"}
    base.update(kw)
    return base


def test_a_fund_flow_finding_carries_its_own_asset_and_chain(tmp_path, monkeypatch):
    """Before round 2, every fund_flows finding WAS Arbitrum USDC by
    construction. build_finding now records the real asset/chain on the
    finding — this reader must use them instead of re-hardcoding the old
    assumption, or a genuine USDT-on-Base finding is written into the graph
    as USDC-on-Arbitrum."""
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    ff_dir = tmp_path / "fund_flows"
    ff_dir.mkdir(parents=True)
    (ff_dir / "latest.json").write_text(json.dumps(
        {"findings": [_finding(asset="USDT", chain="base")]}))

    edges = tg.collect_known_edges()
    assert len(edges) == 1
    assert edges[0]["asset"] == "USDT"
    assert edges[0]["chain"] == "base"


def test_a_fund_flow_finding_without_asset_or_chain_defaults_to_usdc_arbitrum(
        tmp_path, monkeypatch):
    """A finding written before build_finding recorded asset/chain — or one
    built by trace_fund_flow's hop-2/hop-3 paths, which are still genuinely
    Arbitrum USDC and never pass these fields — has neither key. The fallback
    here must match build_finding's own default, not silently drop the
    record or invent a different assumption."""
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    ff_dir = tmp_path / "fund_flows"
    ff_dir.mkdir(parents=True)
    (ff_dir / "latest.json").write_text(json.dumps({"findings": [_finding()]}))

    edges = tg.collect_known_edges()
    assert len(edges) == 1
    assert edges[0]["asset"] == "USDC"
    assert edges[0]["chain"] == "arbitrum"


def test_a_fund_flow_finding_still_dedupes_with_its_substrate_edge(tmp_path, monkeypatch):
    """edge_id is chain-scoped, so hardcoding chain="arbitrum" on every
    finding-derived edge silently broke dedup against that same transfer's own
    substrate edge for any chain other than Arbitrum: the two carried
    different ids and both survived dedupe_edges as if they were two separate
    movements. Reading the finding's own chain/asset fixes this — the same
    real transfer, reachable through both the substrate and a fund_flows
    finding, must still collapse to one edge, on every chain, not just
    Arbitrum."""
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)

    d = tmp_path / "transfers" / "base"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([record(chain="base", tx_hash="0xh")]))

    ff_dir = tmp_path / "fund_flows"
    ff_dir.mkdir(parents=True)
    (ff_dir / "latest.json").write_text(json.dumps(
        {"findings": [_finding(chain="base", asset="USDC")]}))

    edges = tg.collect_known_edges()
    assert len(edges) == 2                      # one substrate edge, one finding edge
    assert len(tg.dedupe_edges(edges)) == 1      # ...but they are the same movement
