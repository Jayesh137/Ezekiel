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


def test_outbound_transfers_skips_malformed_amount_usd_rather_than_raising(tmp_path, monkeypatch):
    """amount_usd is only ever produced internally as a float or None, but a
    hand-edited or truncated file in data/transfers/ should degrade to
    skipping one bad record, not crash the whole sweep."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", id="a", dst="0xbad", amount_usd="not-a-number"),
        substrate_record(chain="arbitrum", id="b", dst="0xreal"),
    ]))

    assert [r["to"] for r in tracer.trace_outbound_transfers("0xtarget")] == ["0xreal"]


# --- build_finding: asset/chain are additive, existing keys never renamed ------

def test_build_finding_adds_asset_and_chain_without_renaming_existing_keys():
    f = tracer.build_finding("0xw", "0xd", 1234.5, "0xhash", "outbound_transfer",
                             1, False, asset="USDT", chain="base")
    assert f["asset"] == "USDT"
    assert f["chain"] == "base"
    assert f["amount_usdc"] == "1,234.50"      # key name unchanged
    assert f["amount_usdc_raw"] == 1234.5      # key name unchanged
    assert f["source"] == "0xw"
    assert f["destination"] == "0xd"


def test_build_finding_defaults_asset_and_chain_to_usdc_arbitrum():
    """The hop-2/hop-3 findings in trace_fund_flow don't pass asset/chain at
    all — they're still genuinely Arbitrum USDC, sourced from
    get_usdc_transfers rather than the substrate — so the defaults must stay
    correct for them."""
    f = tracer.build_finding("0xw", "0xd", 1234.5, "0xhash", "fund_trace_2hop", 2, True)
    assert f["asset"] == "USDC"
    assert f["chain"] == "arbitrum"


# --- trace_fund_flow: the asset and chain must come from the row, not be assumed --

def _no_op_hop_followup(monkeypatch):
    """Every trace_fund_flow test below only exercises hop 1 (the substrate row
    itself); find_hl_deposits and get_usdc_transfers are stubbed so the
    hop-2/hop-3 Etherscan-backed loops never run and never reach the network."""
    monkeypatch.setattr(tracer, "find_hl_deposits", lambda addr: [])
    monkeypatch.setattr(tracer, "get_usdc_transfers", lambda addr, start_block=0: [])


def test_trace_fund_flow_labels_a_non_usdc_asset_correctly(tmp_path, monkeypatch):
    """Before this task every row reaching build_finding/alert_fund_movement WAS
    USDC by construction (get_usdc_transfers filtered on the USDC contract).
    assets.py's STABLES set prices USDT at par with no price lookup needed, so
    a genuine USDT transfer now reaches the same path with a real amount_usd —
    and must not be mislabelled "USDC" in the finding or the alert call."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    _no_op_hop_followup(monkeypatch)

    calls = []
    monkeypatch.setattr(tracer, "alert_fund_movement",
                        lambda *a, **k: calls.append((a, k)) or True)

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", asset="USDT", amount=5000.0, amount_usd=5000.0)]))

    findings = tracer.trace_fund_flow("0xtarget")

    assert len(findings) == 1
    assert findings[0]["asset"] == "USDT"
    assert findings[0]["chain"] == "arbitrum"
    assert findings[0]["amount_usdc"] == "5,000.00"     # key unchanged, real USD value
    assert findings[0]["amount_usdc_raw"] == 5000.0     # key unchanged

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs.get("asset") == "USDT"


def test_trace_fund_flow_carries_a_non_arbitrum_chain_through(tmp_path, monkeypatch):
    """The alert used to be unambiguous about chain too, because collection was
    Arbitrum-only. It now spans six chains, so the finding (and the alert)
    must say where the transfer happened, not assume Arbitrum."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    _no_op_hop_followup(monkeypatch)

    calls = []
    monkeypatch.setattr(tracer, "alert_fund_movement",
                        lambda *a, **k: calls.append((a, k)) or True)

    d = tmp_path / "transfers" / "base"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="base", amount=5000.0, amount_usd=5000.0)]))

    findings = tracer.trace_fund_flow("0xtarget")

    assert len(findings) == 1
    assert findings[0]["chain"] == "base"
    assert findings[0]["asset"] == "USDC"          # unrelated dimension, unchanged

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs.get("chain") == "base"


def test_trace_fund_flow_usdc_arbitrum_path_is_unchanged(tmp_path, monkeypatch):
    """The pre-existing USDC-on-Arbitrum path must keep every key and value it
    had before this fix — asset/chain are additive, not a replacement."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    _no_op_hop_followup(monkeypatch)

    calls = []
    monkeypatch.setattr(tracer, "alert_fund_movement",
                        lambda *a, **k: calls.append((a, k)) or True)

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", amount=5000.0, amount_usd=5000.0)]))

    findings = tracer.trace_fund_flow("0xtarget")

    assert len(findings) == 1
    f = findings[0]
    assert f["asset"] == "USDC"
    assert f["chain"] == "arbitrum"
    assert f["source"] == "0xtarget"
    assert f["destination"] == "0xdest"
    assert f["amount_usdc"] == "5,000.00"
    assert f["amount_usdc_raw"] == 5000.0
    assert f["tx_hash"] == "0xh"
    assert f["method"] == "outbound_transfer"
    assert f["hop_count"] == 1
    assert f["deposited_to_hl"] is False
    assert f["status"] == "PENDING_HL_DEPOSIT"
    assert f["bridge_tx_hash"] is None
    assert "id" in f and "detected_at" in f and "confidence" in f

    assert len(calls) == 1
    args, kwargs = calls[0]
    # Dollar-qualified even for USDC: round 3 made this unconditional so the
    # wording never again depends on which asset happens to be passing through.
    assert args[1] == "$5,000.00"
    assert kwargs.get("asset") == "USDC"
    assert kwargs.get("chain") == "arbitrum"


def test_trace_fund_flow_print_is_unambiguous_about_dollars_for_a_non_usdc_asset(
        tmp_path, monkeypatch, capsys):
    """value_raw/1e6 is a USD figure regardless of asset — it only reads
    correctly as a bare number today because every asset reaching this path is
    priced at par (assets.py's STABLES). The day a MAJORS price_lookup exists,
    "5,000.00 ETH" would mean 5,000 ETH, not $5,000 of it. The OUTBOUND print
    must say "of <asset>" with an explicit "$", not just interpolate the asset
    symbol next to a bare number."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tracer, "alert_fund_movement", lambda *a, **k: True)
    _no_op_hop_followup(monkeypatch)

    d = tmp_path / "transfers" / "base"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="base", asset="USDT", amount=5000.0, amount_usd=5000.0)]))

    tracer.trace_fund_flow("0xtarget")

    out = capsys.readouterr().out
    assert "[tracer] OUTBOUND: $5,000.00 of USDT on base -> 0xdest" in out
    assert "5,000.00 USDT" not in out          # the old, ambiguous "N ASSET" form
