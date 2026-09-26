from src.episodes import build_episodes, compare_episode_profiles, episode_profile


def fill(tid, ts, side="B", size=1, before=0, oid=None, coin="BTC"):
    row = {"tid": tid, "time": ts, "side": side, "sz": str(size), "px": "100",
           "coin": coin, "oid": oid or tid, "crossed": True}
    if before is not None:
        row["startPosition"] = str(before)
    return row


def test_partial_fills_make_one_order_in_a_complete_position_episode():
    rows = [fill(1, 1000, size=1, before=0, oid=42), fill(2, 2000, size=2, before=1, oid=42),
            fill(3, 3000, side="A", size=3, before=3)]
    episodes = build_episodes(rows + [rows[0]])
    assert len(episodes) == 1
    assert episodes[0]["order_count"] == 2
    assert episodes[0]["fill_count"] == 3
    assert not episodes[0]["censored_start"] and not episodes[0]["censored_end"]
    assert len(episodes[0]["parent_event_ids"]) == 3
    assert episodes[0]['entry_steps'] == 1  # one order, two execution slices


def test_unknown_start_position_is_censored_not_invented_flat():
    episode = build_episodes([fill(1, 1000, before=None)])[0]
    assert episode["censored_start"] and episode["censored_end"]
    assert episode["position_before"] is None


def test_snapshot_discontinuity_splits_episodes_and_marks_missing_history():
    episodes = build_episodes([fill(1, 1000, before=0), fill(2, 2000, before=10)])
    assert len(episodes) == 2
    assert episodes[1]["censored_start"]
    assert episodes[1]["coverage_gap"]


def test_many_slices_from_one_session_do_not_become_independent_decisions():
    episodes = build_episodes([fill(i, 1000 + i * 1000, before=i) for i in range(30)])
    profile = episode_profile(episodes)
    comparison = compare_episode_profiles(profile, profile)
    assert profile["session_count"] == 1
    assert comparison["status"] == "insufficient_data"
    assert comparison["promotable"] is False


def test_capital_normalisation_requires_observed_equity():
    profile = episode_profile(build_episodes([fill(1, 1000)]))
    assert profile["capital_normalised_size"] is None
    sized = episode_profile(build_episodes([{**fill(1, 1000), "account_value_usd": 1000}]))
    assert sized["capital_normalised_size"] == .1


def test_string_timestamps_are_sorted_numerically_and_unknown_rows_do_not_fill_gaps():
    rows = [fill(2, '10000', before=1), fill(1, '2000', before=0)]
    result = build_episodes(rows)
    assert len(result) == 1
    assert result[0]['start_ms'] == 2000


def test_candidate_fingerprint_carries_experimental_episodes_without_new_score_weight():
    from src.scanner import build_candidate_fingerprint, compute_similarity
    profile = build_candidate_fingerprint([fill(1, 1000)], {})
    assert profile['episode_profile']['session_count'] == 1
    _, dimensions, evidence = compute_similarity(profile, profile, market_freq={})
    assert 'episodes' not in dimensions
    assert evidence['episodes']['promotable'] is False
