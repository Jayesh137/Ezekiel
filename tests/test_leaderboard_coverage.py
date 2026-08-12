# tests/test_leaderboard_coverage.py
"""The scanned population must contain wallets the target could plausibly be.

Measured against the live leaderboard on 2026-08-12:

  * the endpoint returns 41,589 rows in NO meaningful order — consecutive
    accountValues run 61M, 59M, 15M, 82M, 10M;
  * the scanner took `leaderboard[:500]`, an arbitrary slice;
  * the TARGET HIMSELF sat at raw position 1,686 and was therefore never
    scanned, despite ranking 53rd of 41,589 by account value;
  * of the 124 wallets within 0.5x-2x his account size — the most plausible
    successor profile — only 24 fell inside the scanned slice. 19.4%.

Two consequences. A successor wallet holding the target's capital would very
likely sit outside an arbitrary slice, so the behavioural vector could not
discover it — only confirm one another vector had already found. And the
backtest draws its "strangers" from that same slice, so `account_size`
separates trivially against randomly-sized wallets, inflating the reported
margin with an artefact of who happened to be scanned.

Ordering by account value fixes both: the population becomes the largest N
accounts, which contains the target's own band, and the stranger set becomes
size-comparable, which makes the self-match harder and more honest.
"""

import pytest

from src.scanner import select_leaderboard_wallets

TARGET_AV = 35_452_887.0


def _rows(values):
    return [{"ethAddress": f"0x{i:040x}", "accountValue": str(v)}
            for i, v in enumerate(values)]


def test_selection_is_ordered_by_account_value_not_api_order():
    """The API's order is arbitrary; taking its first N is taking noise."""
    rows = _rows([1_000, 90_000_000, 5_000, 50_000_000, 20_000_000])
    picked = select_leaderboard_wallets(rows, 3)
    got = [float(r["accountValue"]) for r in picked]
    assert got == [90_000_000, 50_000_000, 20_000_000]


def test_the_target_own_size_band_is_inside_the_scanned_population():
    """The decisive property. A scanner whose population excludes wallets the
    size of the trader it tracks cannot discover his successor."""
    # 600 small wallets listed first, the target-sized one buried late — exactly
    # the live shape, where he sat at raw position 1,686.
    values = [10_000.0] * 600 + [TARGET_AV] + [5_000.0] * 600
    picked = select_leaderboard_wallets(_rows(values), 500)
    avs = [float(r["accountValue"]) for r in picked]
    assert TARGET_AV in avs, "the target's own size band must be scanned"
    assert avs[0] == TARGET_AV


def test_comparable_wallets_are_preferred_over_arbitrary_ones():
    """Coverage of the plausible-successor band should be near total, not 19%."""
    import random
    rng = random.Random(7)
    comparable = [TARGET_AV * rng.uniform(0.5, 2.0) for _ in range(124)]
    tiny = [rng.uniform(100, 50_000) for _ in range(5_000)]
    rows = _rows(tiny[:2_000] + comparable + tiny[2_000:])  # buried mid-list
    picked = select_leaderboard_wallets(rows, 500)
    avs = {float(r["accountValue"]) for r in picked}
    covered = sum(1 for c in comparable if c in avs)
    assert covered == len(comparable), f"only {covered}/{len(comparable)} comparable wallets scanned"


def test_malformed_and_missing_account_values_do_not_crash():
    """Live API data is untrusted: missing, null and non-numeric all appear."""
    rows = [
        {"ethAddress": "0xa", "accountValue": "50000000"},
        {"ethAddress": "0xb"},
        {"ethAddress": "0xc", "accountValue": None},
        {"ethAddress": "0xd", "accountValue": "not-a-number"},
        {"ethAddress": "0xe", "accountValue": "75000000"},
    ]
    picked = select_leaderboard_wallets(rows, 10)
    assert [r["ethAddress"] for r in picked[:2]] == ["0xe", "0xa"]
    assert len(picked) == 5, "unparseable entries sort last, they are not dropped"


def test_limit_is_respected_and_zero_is_safe():
    rows = _rows([float(i) for i in range(50)])
    assert len(select_leaderboard_wallets(rows, 10)) == 10
    assert select_leaderboard_wallets(rows, 0) == []
    assert select_leaderboard_wallets([], 10) == []


def test_selection_is_deterministic():
    """Two runs over the same input must scan the same wallets, or the
    calibration population and the backtest's stranger set wander between runs."""
    rows = _rows([5.0, 5.0, 5.0, 9.0, 1.0])
    assert ([r["ethAddress"] for r in select_leaderboard_wallets(rows, 4)]
            == [r["ethAddress"] for r in select_leaderboard_wallets(rows, 4)])


@pytest.mark.parametrize("key", ["accountValue", "account_value"])
def test_accepts_either_field_spelling(key):
    rows = [{"ethAddress": "0xa", key: "10"}, {"ethAddress": "0xb", key: "99"}]
    assert select_leaderboard_wallets(rows, 1)[0]["ethAddress"] == "0xb"
