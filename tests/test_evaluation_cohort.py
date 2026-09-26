from src.evaluation_cohort import freeze_cohort, load_cohort_asof, record_profiles

A, B, C = ['0x' + char * 40 for char in '123']


def test_cohort_membership_is_fixed_and_not_selected_by_score(tmp_path):
    db = tmp_path / 'db.sqlite3'
    rows = [{'wallet': w, 'fingerprint': {'value': i}, 'source': 'leaderboard'} for i, w in enumerate([A, B])]
    record_profiles(rows, 100, db)
    first = freeze_cohort(db, 100, minimum=2)
    record_profiles([{'wallet': C, 'fingerprint': {'value': 999}, 'source': 'leaderboard'}], 200, db)
    assert freeze_cohort(db, 200, minimum=2)['wallets'] == first['wallets']
    assert C not in first['wallets']


def test_asof_never_reads_future_profiles_or_a_cohort_frozen_after_trial_start(tmp_path):
    db = tmp_path / 'db.sqlite3'
    record_profiles([{'wallet': A, 'fingerprint': {'value': 1}, 'source': 'leaderboard'}], 100, db)
    freeze_cohort(db, 100, minimum=1)
    record_profiles([{'wallet': A, 'fingerprint': {'value': 999}, 'source': 'leaderboard'}], 1000, db)
    rows, meta = load_cohort_asof(db, 500, trial_start_ms=200)
    assert rows[0]['fingerprint']['value'] == 1
    assert meta['eligible'] is True
    assert load_cohort_asof(db, 500, trial_start_ms=50)[1]['eligible'] is False


def test_matched_calibration_uses_same_features_and_excludes_the_candidate(tmp_path, monkeypatch):
    from src import calibration, scanner
    monkeypatch.setattr(calibration, 'POPULATION_PATH', tmp_path / 'population.json')
    calibration.record_population_scores([
        {'wallet': A, 'score': .8, 'feature_mask': ['activity']},
        {'wallet': B, 'score': .2, 'feature_mask': ['activity']},
        {'wallet': C, 'score': .9, 'feature_mask': ['activity', 'orders']}])
    population = scanner.matched_population({'wallet': A, 'dimensions': {'activity': .8, 'orders': None}})
    assert population == [.2]
