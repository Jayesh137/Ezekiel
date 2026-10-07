# tests/test_graph_hl_presence.py
"""The graph calls a wallet a Hyperliquid trader on its TRADING, not its perp margin.

`webData2`'s `accountValue` is perp margin only, and the transfer graph decided which
of its wallets "trade on Hyperliquid" from it (role `user`, margin above zero). That
set protects a wallet from the conduit pass, becomes node `evidence.trades_on_hl`,
adds the +0.08 corroboration and drives the `funded_before_trading` signal.

Measured 2026-10-07, read-only against live Hyperliquid: of 299 graph nodes, 19 were
Hyperliquid users whose margin read 0, and five of them traded in the last 30 days -
`0x84abc08c0e...` ($80.0M, with $9.37M sitting in spot) among them - while the
CONFIRMED config wallet `0x1419e75330...` holds $56.7M there and traded nothing.
Holding value is not trading, and trading is not margin.

The identity row now stores `portfolio`'s `month_volume` (the last 30 days) and
`total_value` (spot + perp). A row probed before those fields existed carries
neither, and is judged on perp margin as it always was.
"""

import json

import pytest

from src import transfer_graph as tg


def addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def identity(**fields) -> dict:
    """An identity row as `hl_identity.probe` writes it: a readable `user` with no perp
    margin. `month_volume` and `total_value` are absent unless a case sets them, which
    is what a row probed before those fields existed looks like."""
    return {"read_ok": True, "errors": [], "role": "user", "account_value": 0.0, **fields}


def active_from(tmp_path, monkeypatch, identities: dict) -> set:
    """Run the real loader over an identity file holding `identities`."""
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    (tmp_path / "identity").mkdir(parents=True, exist_ok=True)
    (tmp_path / "identity" / "latest.json").write_text(json.dumps({"identities": identities}))
    _scores, active = tg._load_behavioural_scores()
    return active


# (id, identity row). The loader must call each of these a Hyperliquid trader.
TRADES = [
    # 1. Collateral in spot, 0 perp margin, $80M in 30 days: 0x84abc08c0e...
    ("1_spot_collateral_trader", identity(total_value=9.3e6, month_volume=80_039_479.44)),
    # 2. A sub-account trades too; the old rule asked for the role `user`.
    ("2_sub_account_with_margin", identity(role="subAccount", account_value=5_000.0)),
    # 4. Probed before `month_volume` existed: perp margin is all there is to read.
    ("4_old_row_with_margin", identity(account_value=10.0)),
    # The portfolio read failed (None, not 0): None must not veto the margin.
    ("portfolio_unread_with_margin",
     identity(account_value=10.0, total_value=None, month_volume=None)),
    # The union keeps what the old rule protected: open margin in a quiet month.
    ("margin_in_a_quiet_month",
     identity(account_value=10.0, total_value=10.0, month_volume=0.0)),
    ("sub_account_trading_on_spot", identity(role="subAccount", month_volume=250_000.0)),
]

# (id, identity row). The loader must NOT call any of these a Hyperliquid trader.
IDLE = [
    # 3. 0x1419e75330...: $56.7M on Hyperliquid and nothing traded in 30 days.
    ("3_holds_value_does_not_trade", identity(total_value=56_685_930.0, month_volume=0.0)),
    # 5. Probed before the fields existed, no margin: nothing to call trading.
    ("5_old_row_without_margin", identity()),
    # 7. Measured: no volume and no margin.
    ("7_measured_no_trading_no_margin", identity(month_volume=0.0)),
    # 8. A bool is not a reading (True would pass `> 0`).
    ("8_a_bool_is_not_a_reading", identity(month_volume=True)),
    ("portfolio_unread_no_margin",
     identity(account_value=None, total_value=None, month_volume=None)),
    ("a_string_is_not_a_reading", identity(month_volume="80000000.0")),
    ("nan_is_not_a_reading", identity(month_volume=float("nan"))),
    ("inf_is_not_a_reading", identity(month_volume=float("inf"))),
    ("negative_volume_is_not_trading", identity(month_volume=-5.0)),
]


@pytest.mark.parametrize("row", [pytest.param(r, id=i) for i, r in TRADES])
def test_a_trading_account_is_active(tmp_path, monkeypatch, row):
    assert addr(1) in active_from(tmp_path, monkeypatch, {addr(1): row})


@pytest.mark.parametrize("row", [pytest.param(r, id=i) for i, r in IDLE])
def test_an_account_that_is_not_trading_is_not_active(tmp_path, monkeypatch, row):
    assert addr(1) not in active_from(tmp_path, monkeypatch, {addr(1): row})


def test_each_case_keeps_its_own_verdict_in_one_identity_file(tmp_path, monkeypatch):
    rows = {addr(n): row for n, (_, row) in enumerate(TRADES + IDLE, start=1)}

    active = active_from(tmp_path, monkeypatch, rows)

    assert active == {addr(n) for n in range(1, len(TRADES) + 1)}


# 6. Only a trading role counts: a vault, an agent, an address Hyperliquid has never
#    seen, or one whose role could not be read is not a trader whatever else it shows.
@pytest.mark.parametrize("role", ["missing", "vault", "agent", None])
def test_a_role_that_is_not_a_trading_account_is_never_active(tmp_path, monkeypatch, role):
    row = identity(role=role, account_value=5_000.0, month_volume=1e6)

    assert active_from(tmp_path, monkeypatch, {addr(1): row}) == set()


def test_an_identity_that_could_not_be_read_is_never_active(tmp_path, monkeypatch):
    row = identity(read_ok=False, account_value=5_000.0, total_value=9.3e6, month_volume=1e6)

    assert active_from(tmp_path, monkeypatch, {addr(1): row}) == set()


def test_the_active_set_is_lower_cased(tmp_path, monkeypatch):
    mixed = "0x" + "AbCdEf" * 6 + "0123"
    assert len(mixed) == 42

    # Active under the old rule too (it has margin): this pins the lower-casing alone.
    active = active_from(tmp_path, monkeypatch,
                         {mixed: identity(account_value=10.0, month_volume=1.0)})

    assert active == {mixed.lower()}


@pytest.mark.parametrize("body", [
    "", "{not json", "[1, 2, 3]", '"a string"', "null", "{}",
    '{"identities": "x"}', '{"identities": []}',
    '{"identities": {"0xabc": "not a dict"}}',
    '{"identities": {"0xabc": null}}',
    '{"identities": {"0xabc": [1]}}',
])
def test_a_wrongly_shaped_identity_file_reads_as_nobody_and_does_not_raise(
        tmp_path, monkeypatch, body):
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    (tmp_path / "identity").mkdir(parents=True)
    (tmp_path / "identity" / "latest.json").write_text(body)

    scores, active = tg._load_behavioural_scores()

    assert scores == {} and active == set()


# --- the pure helper -----------------------------------------------------------

def test_the_helper_answers_a_bool_and_survives_what_a_file_can_hold():
    for junk in (None, [], "x", 5, True):
        assert tg.trades_on_hl(junk) is False
    assert tg.trades_on_hl(identity(month_volume=1.0)) is True
    assert tg.trades_on_hl(identity()) is False


def test_an_unreadable_margin_does_not_hide_real_volume():
    assert tg.trades_on_hl(identity(account_value=None, month_volume=3.0)) is True
    assert tg.trades_on_hl(identity(account_value=float("nan"), month_volume=3.0)) is True


def test_a_reading_is_a_finite_int_or_float_and_never_a_bool():
    for good in (0, 1, -1, 0.0, 2.5, 1e308):
        assert tg._number(good) is True
    for bad in (True, False, None, "5", [5], {}, float("nan"), float("inf"), -float("inf"),
                10**400):          # an int past float's range must not raise out of the loader
        assert tg._number(bad) is False


def test_an_int_past_floats_range_is_no_reading_and_does_not_kill_the_loader(
        tmp_path, monkeypatch):
    # json.dumps writes 10**400 as a bare 401-digit integer and json.load reads it back
    # as an int; math.isfinite() raises OverflowError on one, which the loader's
    # `except` does not name.
    row = identity(month_volume=10**400)

    assert active_from(tmp_path, monkeypatch, {addr(1): row}) == set()
