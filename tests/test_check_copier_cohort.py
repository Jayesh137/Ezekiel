"""The cohort report is well-formed and ranks real followers above coincidences."""

from scripts import check_copier_cohort as chk

HOUR = 3600_000
COINS = ["NEAR", "ZEC", "AAVE", "LINK", "ONDO", "XRP", "SOL", "ADA", "DOGE", "SUI"]


def _openings(coins, direction, t0, step_h=10, lag_h=0):
    return [(c, direction, t0 + i * step_h * HOUR + lag_h * HOUR) for i, c in enumerate(coins)]


def test_build_report_puts_a_follower_in_the_cohort_and_a_stranger_out():
    target = _openings(COINS, -1, 1_000_000)
    follower = {"wallet": "0xfollow", "openings": _openings(COINS, -1, 1_000_000, lag_h=1)}
    stranger = {"wallet": "0xstray", "openings": _openings(["BTC", "ETH"], 1, 5_000_000)}
    report = chk.build_report(target, [follower, stranger], freq={})
    assert report["cohort_size"] == 1
    assert report["cohort"][0]["wallet"] == "0xfollow"
    assert "0xstray" not in [c["wallet"] for c in report["cohort"]]


def test_a_candidate_with_no_overlap_is_counted_insufficient_not_a_non_follower():
    target = _openings(COINS, -1, 1_000_000)
    no_overlap = {"wallet": "0xgap", "openings": [("NEAR", -1, 9_000_000_000)],
                  "observed_window": (9_000_000_000, 9_000_100_000)}
    report = chk.build_report(target, [no_overlap], freq={})
    assert report["candidates_measured"] == 0
    assert report["insufficient_overlap"] == 1
    assert report["cohort_size"] == 0


def test_empty_candidates_make_an_empty_cohort():
    report = chk.build_report(_openings(COINS, -1, 1000), [], freq={})
    assert report["cohort_size"] == 0 and report["candidates_measured"] == 0
