# tests/test_chain_tracer_substrate.py
import json

import pytest

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


def _gate_already_initialised(tmp_path, wallet="0xtarget"):
    """Declare this a run AFTER the incremental gate's first-ever run.

    The first run against an existing substrate seeds the marker to everything
    already stored and alerts on nothing (see untraced_outbound), so a test
    exercising the tracing path has to say it is not that run. Requires
    tracer.DATA_DIR to have been pointed at tmp_path / "data" already.
    """
    state = tmp_path / "data" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / tracer.TRACED_MARKER).write_text(
        json.dumps({wallet.lower(): {"traced": []}}))


def _traced(tmp_path, wallet="0xtarget") -> set:
    doc = json.loads((tmp_path / "data" / "state" / tracer.TRACED_MARKER).read_text())
    return set(doc[wallet.lower()]["traced"])


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
    _gate_already_initialised(tmp_path)

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
    _gate_already_initialised(tmp_path)

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
    _gate_already_initialised(tmp_path)

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
    _gate_already_initialised(tmp_path)

    d = tmp_path / "transfers" / "base"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="base", asset="USDT", amount=5000.0, amount_usd=5000.0)]))

    tracer.trace_fund_flow("0xtarget")

    out = capsys.readouterr().out
    assert "[tracer] OUTBOUND: $5,000.00 of USDT on base -> 0xdest" in out
    assert "5,000.00 USDT" not in out          # the old, ambiguous "N ASSET" form


# --- the incremental gate ------------------------------------------------------
#
# Before the substrate landed, novelty came from read_cursor("last_l1_block").
# records_for() has no such notion, so without a gate every scheduled run
# (cron '*/30 * * * *') re-traces the whole stored history forever.

def _flow_fixture(tmp_path, monkeypatch, records):
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: None)
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    _no_op_hop_followup(monkeypatch)

    alerts = []
    monkeypatch.setattr(tracer, "alert_fund_movement",
                        lambda *a, **k: alerts.append(a[2]) or True)

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-08-28.json").write_text(json.dumps(records))
    return alerts


def test_the_first_run_seeds_the_marker_instead_of_alerting_on_all_history(
        tmp_path, monkeypatch, capsys):
    """Deliberate choice: on the first run the substrate already holds months of
    recovered history, and alerting on it would mean up to MAX_DESTINATIONS
    CRITICAL emails about June 2026 transfers. The records are not discarded —
    they stay in data/transfers/ and on the graph — only the email is."""
    alerts = _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id="old-1", dst="0xd1", amount_usd=9000.0),
        substrate_record(chain="arbitrum", id="old-2", dst="0xd2", amount_usd=8000.0),
    ])

    assert tracer.trace_fund_flow("0xtarget") == []
    assert alerts == []
    assert _traced(tmp_path) == {"old-1", "old-2"}

    out = capsys.readouterr().out
    assert "First run of the incremental gate" in out          # never silent
    doc = json.loads((tmp_path / "data" / "state" / tracer.TRACED_MARKER).read_text())
    assert doc["0xtarget"]["seeded"] == 2 and doc["0xtarget"]["seeded_at"]


def test_an_already_traced_record_is_not_re_alerted_on_the_next_run(
        tmp_path, monkeypatch):
    """The live failure: 30-minute cron, a 24-hour per-tx_hash alert cooldown as
    the only brake, and every stored record offered up on every run."""
    alerts = _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id="rec-1", dst="0xd1", amount_usd=9000.0)])
    _gate_already_initialised(tmp_path)

    assert len(tracer.trace_fund_flow("0xtarget")) == 1
    assert alerts == ["0xd1"]
    assert _traced(tmp_path) == {"rec-1"}          # the marker advanced

    assert tracer.trace_fund_flow("0xtarget") == []
    assert alerts == ["0xd1"]                      # not alerted a second time


def test_a_genuinely_new_record_is_still_traced_after_the_gate(tmp_path, monkeypatch):
    alerts = _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id="rec-1", dst="0xd1", amount_usd=9000.0)])
    _gate_already_initialised(tmp_path)
    tracer.trace_fund_flow("0xtarget")

    (tmp_path / "transfers" / "arbitrum" / "2026-08-28.json").write_text(json.dumps([
        substrate_record(chain="arbitrum", id="rec-1", dst="0xd1", amount_usd=9000.0),
        substrate_record(chain="arbitrum", id="rec-2", dst="0xd2", amount_usd=42.0,
                         tx_hash="0xnew")]))

    findings = tracer.trace_fund_flow("0xtarget")
    assert [f["destination"] for f in findings] == ["0xd2"]
    assert alerts == ["0xd1", "0xd2"]
    assert _traced(tmp_path) == {"rec-1", "rec-2"}


def test_the_no_new_outbound_message_is_accurate_again(tmp_path, monkeypatch, capsys):
    """It used to print only for a wallet that had never sent anything, because
    records_for returns all history. Rows that can never be traced (zero value,
    self-transfer) must be marked too, or the wallet stays permanently dirty."""
    _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id="zero", dst="0xd1", amount_usd=0.0),
        substrate_record(chain="arbitrum", id="self", dst="0xtarget", amount_usd=500.0),
        substrate_record(chain="arbitrum", id="real", dst="0xd2", amount_usd=500.0),
    ])
    _gate_already_initialised(tmp_path)

    tracer.trace_fund_flow("0xtarget")
    capsys.readouterr()

    assert tracer.trace_fund_flow("0xtarget") == []
    assert "No new outbound transfers detected since the last run." in capsys.readouterr().out
    assert _traced(tmp_path) == {"zero", "self", "real"}


def test_a_destination_deferred_by_the_per_run_cap_is_traced_on_the_next_run(
        tmp_path, monkeypatch):
    """unique_destinations' cap was a spam guard against NEW transfers. Against
    all history it would be a permanent ceiling: once MAX_DESTINATIONS
    historical destinations outranked a genuinely new smaller movement, that
    movement would never be traced at all. Deferred destinations must stay
    unmarked so the backlog drains instead of blocking."""
    n = tracer.MAX_DESTINATIONS + 1
    alerts = _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id=f"rec-{i}", dst=f"0xd{i}",
                         tx_hash=f"0xh{i}", amount_usd=float(n - i))
        for i in range(n)])
    _gate_already_initialised(tmp_path)

    tracer.trace_fund_flow("0xtarget")
    assert len(alerts) == tracer.MAX_DESTINATIONS
    assert f"rec-{n - 1}" not in _traced(tmp_path)        # the smallest, deferred

    tracer.trace_fund_flow("0xtarget")
    assert alerts[-1] == f"0xd{n - 1}"                    # picked up next run
    assert len(_traced(tmp_path)) == n


def test_the_marker_never_grows_past_the_records_it_tracks(tmp_path, monkeypatch):
    """Bounded by the substrate: an id for a record that is no longer stored is
    dropped rather than accumulating in a file committed every 30 minutes."""
    _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id="rec-1", dst="0xd1", amount_usd=9000.0)])
    state = tmp_path / "data" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / tracer.TRACED_MARKER).write_text(
        json.dumps({"0xtarget": {"traced": ["long-gone-1", "long-gone-2"]}}))

    tracer.trace_fund_flow("0xtarget")
    assert _traced(tmp_path) == {"rec-1"}


def test_an_unreadable_substrate_never_erases_the_marker(tmp_path, monkeypatch):
    """known_ids is ignored when empty: intersecting against a transiently
    unreadable substrate would wipe the marker and re-alert everything."""
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    tracer.mark_traced("0xtarget", ["a", "b"])
    tracer.mark_traced("0xtarget", [], known_ids=set())
    assert _traced(tmp_path) == {"a", "b"}


# --- a corrupt marker is a fault, not a first run -------------------------------
#
# _load_traced used to catch a parse error and return {}, indistinguishable
# from "this wallet has never been traced". untraced_outbound would then seed
# the marker to everything currently stored and alert on nothing — silently
# suppressing that run's real alerts on the strength of a fault, not a clean
# absence. Reproduced here the same way the re-reviewer found it live: invalid
# JSON written into traced_outbound.json.

def test_load_traced_distinguishes_absent_from_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")

    assert tracer._load_traced() == {}         # absent: quiet, not an error

    state = tmp_path / "data" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / tracer.TRACED_MARKER).write_text("{not valid json")
    with pytest.raises(tracer.TracedMarkerCorrupt):
        tracer._load_traced()

    (state / tracer.TRACED_MARKER).write_text("[]")     # valid JSON, wrong shape
    with pytest.raises(tracer.TracedMarkerCorrupt):
        tracer._load_traced()


def test_a_corrupt_marker_alerts_instead_of_seeding_quietly(tmp_path, monkeypatch, capsys):
    """The live failure: a wallet with real outbound history and a corrupt
    marker must alert on that history, not silently mark it all as already-seen."""
    alerts = _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id="rec-1", dst="0xd1", amount_usd=9000.0)])
    state = tmp_path / "data" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / tracer.TRACED_MARKER).write_text("{not valid json")

    findings = tracer.trace_fund_flow("0xtarget")

    assert len(findings) == 1                  # alerted, not silently seeded
    assert alerts == ["0xd1"]
    out = capsys.readouterr().out
    assert "CORRUPT" in out
    assert "First run of the incremental gate" not in out   # not mistaken for one


def test_a_corrupt_marker_self_heals_by_the_end_of_the_run(tmp_path, monkeypatch):
    """mark_traced runs after tracing regardless of the corruption, so the same
    run that hits the fault also leaves a valid marker behind for the next one."""
    _flow_fixture(tmp_path, monkeypatch, [
        substrate_record(chain="arbitrum", id="rec-1", dst="0xd1", amount_usd=9000.0)])
    state = tmp_path / "data" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / tracer.TRACED_MARKER).write_text("{not valid json")

    tracer.trace_fund_flow("0xtarget")

    doc = json.loads((state / tracer.TRACED_MARKER).read_text())    # parses: healed
    assert doc["0xtarget"]["traced"] == ["rec-1"]


def test_an_interrupted_write_leaves_the_previous_marker_intact(tmp_path, monkeypatch):
    """Mirrors test_interrupted_write_leaves_the_previous_file_intact for
    save_latest (test_continuity_adversarial.py): _save_traced now goes
    through the same write-then-rename helper, for the same reason. A job
    killed mid-write — the exact scenario this branch's job timeouts exist to
    bound — must not leave a truncated, unparseable marker; a plain
    path.write_text (the old implementation) would have manufactured exactly
    the corruption the tests above have to recover from."""
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    tracer.mark_traced("0xtarget", ["good-1"])

    class Boom(Exception):
        pass

    def dying_dump(*a, **k):
        raise Boom("killed mid-write")

    monkeypatch.setattr(json, "dump", dying_dump)
    try:
        tracer.mark_traced("0xtarget", ["partial-1"])
    except Boom:
        pass
    monkeypatch.undo()

    state = tmp_path / "data" / "state"
    survived = json.loads((state / tracer.TRACED_MARKER).read_text())
    assert survived["0xtarget"]["traced"] == ["good-1"], (
        "an interrupted write corrupted traced_outbound.json")
    assert not list(state.glob(f".{tracer.TRACED_MARKER}.*.tmp")), (
        "temp file must be cleaned up")


# --- the scheduled path must write the blindness record ------------------------

def test_the_tracer_writes_sweep_health_so_an_outage_is_visible(tmp_path, monkeypatch):
    """data/transfers/latest.json is spec section 10's degradation record and
    the README's "blindness is reported, never inferred". It was produced by
    the manually-dispatched backfill script alone, so on the scheduled path a
    chain outage produced no record anywhere."""
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget", "status": "ok", "degraded_sources": ["bsc"],
        "chains": {"bsc": {"records": 0, "spam": 0, "calls": 1, "cursor": 0,
                           "gaps": [], "truncated": False,
                           "error": "Max rate limit reached",
                           "probed_inactive": False}}})

    tracer.trace_outbound_transfers("0xtarget")

    health = json.loads((tmp_path / "transfers" / "latest.json").read_text())
    assert health["degraded_sources"] == ["bsc"]
    assert health["per_wallet"][0]["address"] == "0xtarget"


def test_a_skipped_sweep_is_recorded_rather_than_reading_as_a_quiet_run(
        tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tracer, "sweep_wallet", lambda *a, **k: {
        "address": "0xtarget", "status": "skipped_no_api_key",
        "degraded_sources": ["arbitrum"],
        "chains": {"arbitrum": {"records": 0, "spam": 0, "calls": 0, "cursor": 0,
                                "gaps": [], "truncated": False,
                                "error": "skipped_no_api_key",
                                "probed_inactive": False}}})

    assert tracer.trace_outbound_transfers("0xtarget") == []
    health = json.loads((tmp_path / "transfers" / "latest.json").read_text())
    assert health["degraded_sources"] == ["arbitrum"]


# --- the cluster sweep prices majors instead of leaving them unpriced forever --

def test_trace_outbound_transfers_wires_a_real_coingecko_price_lookup_into_the_sweep(
        tmp_path, monkeypatch):
    """Before this task price_lookup defaulted to `lambda s, d: None`
    (src/chain/collect.py), so every ETH/WBTC/... transfer the target ever
    made was stored price_unavailable and could never become a graph edge or
    fire alert_fund_movement. sweep_wallet is stubbed here (this must stay
    network-free) but the captured price_lookup's own identity proves it is
    the real src.chain.prices machinery, not merely a non-None placeholder --
    it is never CALLED here, since that would attempt a genuine HTTP request."""
    from src.chain import collect
    from src.chain.prices import coingecko_price_lookup

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path / "data")

    captured = {}

    def fake_sweep(address, chains, budget, *, cluster=False, price_lookup=None, **kw):
        captured["price_lookup"] = price_lookup
        return {"address": address, "status": "ok", "degraded_sources": [], "chains": {}}

    monkeypatch.setattr(tracer, "sweep_wallet", fake_sweep)

    tracer.trace_outbound_transfers("0xtarget")

    price_lookup = captured.get("price_lookup")
    assert price_lookup is not None
    assert callable(price_lookup)
    assert price_lookup.__module__ == coingecko_price_lookup.__module__
    assert price_lookup.__name__ == "price_lookup"
