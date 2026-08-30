# tests/test_chain_tracer_substrate.py
import json

from src import tracer


def substrate_record(**kw):
    base = {"id": "base:0xh:erc20:0", "chain": "base", "chain_id": 8453,
            "block": 1, "ts": 1781000000, "timestamp": "2026-06-16T00:00:00+00:00",
            "tx_hash": "0xh", "src": "0xtarget", "dst": "0xdest", "kind": "erc20",
            "asset": "USDC", "amount": 100.0, "amount_usd": 100.0,
            "value_basis": "stable_par", "spam": False, "spam_reason": None}
    base.update(kw)
    return base


def test_outbound_transfers_keep_the_etherscan_row_shape(tmp_path, monkeypatch):
    """unique_destinations and build_finding read `to`, `value` and `hash`.
    Those keys must survive the switch or the alert path breaks silently."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", dst="0xdest", amount=250.0, amount_usd=250.0)]))

    out = tracer.trace_outbound_transfers("0xtarget")
    assert len(out) == 1
    assert out[0]["to"] == "0xdest"
    assert out[0]["hash"] == "0xh"
    assert int(out[0]["value"]) == 250_000_000        # 250 USDC at 6 decimals
    assert tracer.unique_destinations(out, "0xtarget")[0]["to"] == "0xdest"


def test_outbound_transfers_exclude_quarantined_records(tmp_path, monkeypatch):
    """905 of 1000 live records are poisoning. If they reached this function
    they would each raise a fund-movement alert."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", id="a", dst="0xpoison",
                         spam=True, spam_reason="lookalike"),
        substrate_record(chain="arbitrum", id="b", dst="0xreal"),
    ]))

    assert [r["to"] for r in tracer.trace_outbound_transfers("0xtarget")] == ["0xreal"]


def test_outbound_transfers_exclude_inbound_ones(tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", id="a", src="0xfunder", dst="0xtarget"),
        substrate_record(chain="arbitrum", id="b", src="0xtarget", dst="0xreal"),
    ]))

    assert [r["to"] for r in tracer.trace_outbound_transfers("0xtarget")] == ["0xreal"]


def test_outbound_transfers_exclude_unpriced_majors(tmp_path, monkeypatch):
    """amount_usd is None when value_basis is price_unavailable — a known asset
    (e.g. ETH) whose price we could not fetch this run. spam.classify_spam
    deliberately keeps this record un-quarantined (see src/chain/spam.py) rather
    than lose a potentially large real transfer to a price-source hiccup.

    _as_etherscan_row must not turn that None into a fabricated "$0" row: that
    would silently drop a potentially large transfer below unique_destinations'
    dust filter, which is exactly the "zero is invisible" failure mode
    src/chain/assets.py's value_usd docstring documents and guards against at
    the collection layer. The record must instead be excluded from this run's
    trace outright, and a real, still-priced transfer alongside it must still
    come through untouched.
    """
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", id="a", dst="0xunpriced", asset="ETH",
                         amount=500.0, amount_usd=None, value_basis="price_unavailable"),
        substrate_record(chain="arbitrum", id="b", dst="0xreal"),
    ]))

    out = tracer.trace_outbound_transfers("0xtarget")
    assert [r["to"] for r in out] == ["0xreal"]
