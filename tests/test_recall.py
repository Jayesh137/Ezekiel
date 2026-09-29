"""Recall: would we catch HIM if he migrated to a fresh wallet?

The mission's core question, asked of his own data. Split his history at a cutoff;
treat the pre-cutoff fills as the reference we would already hold and the
post-cutoff fills as a fresh wallet running the same script. If the
execution-program vector does not recognise the second half as the first, the
project cannot catch a migration that keeps his tooling — the single most likely
kind. This is the pseudo-migration test the review asked for, on real behaviour.
"""

from src import recall


def _fill(oid, t, coin, side, sz, px=4.0):
    return {"oid": oid, "time": t, "coin": coin, "side": side, "sz": str(sz),
            "px": str(px), "crossed": True, "tid": oid * 1000 + t % 1000}


def _program(coin, side, clip, n, t0, oid0):
    return [_fill(oid0 + i, t0 + i * 1750, coin, side, clip) for i in range(n)]


def _history(t0):
    # Two coins with stable clips, enough orders each side of any cutoff.
    return (_program("NEAR", "A", 250, 40, t0, 1)
            + _program("ZEC", "A", 1, 40, t0 + 100_000, 100)
            + _program("BTC", "A", 0.1, 40, t0 + 200_000, 200))


def test_split_partitions_by_cutoff():
    fills = [_fill(1, 1000, "BTC", "A", 0.1), _fill(2, 3000, "BTC", "A", 0.1)]
    before, after = recall.split_fills(fills, cutoff_ms=2000)
    assert [f["oid"] for f in before] == [1]
    assert [f["oid"] for f in after] == [2]


def test_a_kept_script_is_recognised_as_him():
    fills = _history(1_000_000) + _program("NEAR", "A", 250, 40, 5_000_000, 900) \
            + _program("ZEC", "A", 1, 40, 5_200_000, 950) + _program("BTC", "A", 0.1, 40, 5_400_000, 990)
    result = recall.self_migration_recall(fills, cutoff_ms=3_000_000, census=None)
    assert result["status"] == "measured"
    assert result["clip_match_ratio"] == 1.0
    assert result["clips_matched"] >= 3


def test_no_census_reports_recognised_but_cannot_yet_vote():
    fills = _history(1_000_000) + _program("NEAR", "A", 250, 40, 5_000_000, 900) \
            + _program("ZEC", "A", 1, 40, 5_200_000, 950) + _program("BTC", "A", 0.1, 40, 5_400_000, 990)
    result = recall.self_migration_recall(fills, cutoff_ms=3_000_000, census=None)
    assert result["recognised"] is True
    assert result["would_vote"] is False          # rule 4: no census, no vote
    assert result["caught"] is False               # honest: recognised != caught yet


def test_with_a_census_a_kept_script_is_caught():
    fills = _history(1_000_000) + _program("NEAR", "A", 250, 40, 5_000_000, 900) \
            + _program("ZEC", "A", 1, 40, 5_200_000, 950) + _program("BTC", "A", 0.1, 40, 5_400_000, 990)
    census = {"population": 300, "ratio_p99": 0.34, "min_clips": 3}
    result = recall.self_migration_recall(fills, cutoff_ms=3_000_000, census=census)
    assert result["would_vote"] is True
    assert result["caught"] is True


def test_too_little_after_the_cutoff_is_insufficient_not_a_failure():
    fills = _history(1_000_000) + [_fill(999, 5_000_000, "NEAR", "A", 250)]
    result = recall.self_migration_recall(fills, cutoff_ms=3_000_000, census=None)
    assert result["status"] == "insufficient_data"
    assert result["caught"] is False
