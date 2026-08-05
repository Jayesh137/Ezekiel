# tests/test_account_latest_freshness.py
"""data/account/latest.json must exist, be current, and have the shape its
readers expect.

Found live on 2026-08-05. collect_positions snapshots the composite
{perp, spot, hip3} into the dated account/ directory but never called
save_latest for it, so account/latest.json was a fossil left by an older
version of the collector: a portfolio payload, which is a LIST.

Both readers do `data.get("perp", data)`, which raises AttributeError on a list,
and both swallow it with a bare except. So two features were silently dead in
production for as long as that file has been stale:

  * risk._gather_signals -> drawdown_pct pinned at 0.0, forfeiting 12 of the
    100 risk points;
  * collector.check_account_value_drop -> the "Possible Liquidation" alert never
    fired at all.

A wiped trader starting again on a fresh wallet is one of the migration
precursors this system exists to catch, so both mattered.
"""

import json

import pytest

from src import collector, risk

WALLET = "0x45d26f28196d226497130c4bac709d808fed4029"


def _state(account_value: str, ntl: str = "1000000"):
    return {"marginSummary": {"accountValue": account_value, "totalNtlPos": ntl,
                              "totalMarginUsed": "100"},
            "assetPositions": []}


@pytest.fixture
def collected(tmp_path, monkeypatch):
    """Run the real collector against stubbed API responses."""
    monkeypatch.setattr(collector, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collector, "load_config", lambda: {"hip3_dexes": ["xyz"]})

    def fake_post(body, *a, **k):
        if body.get("type") == "spotClearinghouseState":
            return {"balances": [{"coin": "USDC", "total": "500000"}]}
        if body.get("dex"):
            return _state("2000000")
        return _state("8000000")

    monkeypatch.setattr(collector, "hl_post", fake_post)
    collector.collect_positions(WALLET)
    return tmp_path


def test_collector_writes_account_latest(collected):
    """It was only ever snapshotted into a dated folder, never published as
    latest, so every reader saw whatever happened to be there already."""
    path = collected / "account" / "latest.json"
    assert path.exists(), "collect_positions must publish account/latest.json"


def test_account_latest_has_the_shape_its_readers_expect(collected):
    payload = json.loads((collected / "account" / "latest.json").read_text())
    assert isinstance(payload, dict), "readers do data.get('perp', data); a list raises"
    assert payload["perp"]["marginSummary"]["accountValue"] == "8000000"
    assert "spot" in payload and "hip3" in payload


def test_account_latest_matches_the_dated_snapshot(collected):
    """The composite already went to the dated directory. latest must be the
    same thing, not a different shape from a different endpoint."""
    latest = json.loads((collected / "account" / "latest.json").read_text())
    day = next(d for d in (collected / "account").iterdir() if d.is_dir())
    snap = json.loads(next(day.glob("*.json")).read_text())
    assert latest == snap


def test_drawdown_is_measured_once_account_latest_is_current(tmp_path, monkeypatch):
    """The end the operator sees: a real high-water mark and a real current
    value must produce a real drawdown."""
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(risk, "load_all_records", lambda d: [])
    (tmp_path / "account").mkdir(parents=True)
    (tmp_path / "account" / "latest.json").write_text(json.dumps(
        {"perp": _state("400000"), "spot": {}, "hip3": {}}))
    cursors = {"account_high_water_cents": 100_000_000, "last_fill_time": 0}
    monkeypatch.setattr(risk, "read_cursor", lambda n: cursors.get(n, 0))

    assert risk._gather_signals()["drawdown_pct"] == pytest.approx(0.6, abs=0.01)


def test_high_water_mark_gets_established(tmp_path, monkeypatch):
    """The whole chain was dead, not just one link. account_high_water_cents is
    written INSIDE check_account_value_drop, which returned early on the stale
    file, so the cursor was never created — and risk.py needs it to measure a
    drawdown at all. Production's data/state/ has no such cursor to this day.
    """
    from src import utils
    monkeypatch.setattr(collector, "DATA_DIR", tmp_path)
    # write_cursor resolves DATA_DIR from utils at call time, so patching only
    # collector.DATA_DIR writes cursors into the REAL data/state.
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    (tmp_path / "account").mkdir(parents=True)
    (tmp_path / "account" / "latest.json").write_text(json.dumps(
        {"perp": _state("9000000"), "spot": {}, "hip3": {}}))

    collector.check_account_value_drop()

    assert (tmp_path / "state" / "account_high_water_cents.txt").exists(), \
        "a readable account state must establish the high-water mark"
    assert utils.read_cursor("account_high_water_cents",
                             base=str(tmp_path / "state")) == 900_000_000


def test_unreadable_account_state_is_reported_not_swallowed(tmp_path, monkeypatch, capsys):
    """The bug hid behind `except Exception: pass`. A drawdown of 0.0 must mean
    "measured, no drawdown", never "could not read the file"."""
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(risk, "load_all_records", lambda d: [])
    (tmp_path / "account").mkdir(parents=True)
    # exactly the fossil shape found in production: a portfolio payload
    (tmp_path / "account" / "latest.json").write_text(json.dumps(
        [["day", {"accountValueHistory": [[1782944400053, "32668521.62"]]}]]))
    cursors = {"account_high_water_cents": 100_000_000, "last_fill_time": 0}
    monkeypatch.setattr(risk, "read_cursor", lambda n: cursors.get(n, 0))

    signals = risk._gather_signals()
    assert signals["drawdown_pct"] == 0.0
    out = capsys.readouterr().out
    assert "account" in out.lower() and ("could not" in out.lower() or "warning" in out.lower()), \
        "an unreadable account state must be announced, not silently scored as no drawdown"
