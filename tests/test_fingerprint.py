import pytest


def test_sig_preserves_a_tiny_position_ratio():
    """The target TWAPs a $24,000,000 account in ~$1,000 slices, so a fill is
    ~0.000041 of the account. round(x, 4) stored that as 0.0, and
    compare_position_sizing reads a non-positive ratio as "unavailable" and
    returns a neutral 0.5 for EVERY wallet — including the target against his
    own history — while the dimension kept its 0.12 weight."""
    from src.fingerprint import _sig
    assert _sig(0.0000414717) == pytest.approx(4.14717e-05, rel=1e-9)
    assert _sig(0.0000414717) > 0


def test_sig_keeps_ordinary_magnitudes_readable():
    from src.fingerprint import _sig
    assert _sig(0.5) == 0.5
    assert _sig(123.456789) == pytest.approx(123.457, rel=1e-9)


def test_sig_handles_zero_and_junk():
    from src.fingerprint import _sig
    assert _sig(0.0) == 0.0
    assert _sig(None) == 0.0
    assert _sig("nope") == 0.0
    assert _sig(float("nan")) == 0.0
    assert _sig(float("inf")) == 0.0


def test_position_sizing_ratio_survives_a_large_account(tmp_path):
    """End to end: a $24M account traded in $1,000 slices must not serialise a
    zero ratio."""
    from src.fingerprint import compute_position_sizing
    fills = [{"coin": "BTC", "px": "100", "sz": "10", "time": 1_760_000_000_000,
              "dir": "Open Long"} for _ in range(20)]
    positions = {"marginSummary": {"accountValue": "24000000"}}
    got = compute_position_sizing(fills, positions)
    ratio = got["size_to_account_ratio"]
    assert ratio["mean"] > 0, ratio
    assert ratio["median"] > 0, ratio


def test_a_positive_ratio_makes_the_dimension_discriminate():
    """With a real ratio the comparison stops returning the neutral 0.5."""
    from src.scanner import compare_position_sizing
    a = {"position_sizing": {"size_to_account_ratio": {"mean": 4.1e-05}}}
    b = {"position_sizing": {"size_to_account_ratio": {"mean": 4.1e-05}}}
    c = {"position_sizing": {"size_to_account_ratio": {"mean": 8.2e-03}}}
    assert compare_position_sizing(a, b) == pytest.approx(1.0)
    assert compare_position_sizing(a, c) < 0.3
    # The old stored shape still reads as unavailable, not as a match.
    zero = {"position_sizing": {"size_to_account_ratio": {"mean": 0.0}}}
    assert compare_position_sizing(a, zero) == 0.5
