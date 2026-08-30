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
