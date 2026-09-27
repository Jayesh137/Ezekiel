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


def test_prospective_controls_gain_orders_without_future_leakage_or_failure_overwrite(tmp_path):
    from src.evaluation_cohort import refresh_cohort_orders
    from src.scanner import compare_order_profile
    db = tmp_path / 'db'
    rows = [{'wallet': w, 'source': 'leaderboard', 'fingerprint': {'order_profile': {}}} for w in (A, B)]
    record_profiles(rows, 100, db)
    freeze_cohort(db, 100, minimum=2)
    orders = [{'status': 'filled', 'statusTimestamp': 150,
               'order': {'oid': 1, 'timestamp': 150, 'orderType': 'Limit', 'tif': 'Gtc'}}]
    first = refresh_cohort_orders(rows, db, fetch=lambda w, timeout: orders,
                                   now=lambda: 200, clock=lambda: 0, limit=1)
    second = refresh_cohort_orders(rows, db, fetch=lambda w, timeout: orders,
                                    now=lambda: 300, clock=lambda: 0, limit=1)
    assert first['updated'] == second['updated'] == 1
    old, _ = load_cohort_asof(db, 150, trial_start_ms=100)
    assert all(not r['fingerprint']['order_profile'] for r in old)
    current, _ = load_cohort_asof(db, 350, trial_start_ms=100)
    assert all(r['fingerprint']['order_profile']['orders'] == 1 for r in current)
    assert compare_order_profile(current[0]['fingerprint'], current[1]['fingerprint']) is not None
    def fail(*args):
        raise TimeoutError('offline')
    failed = refresh_cohort_orders(rows, db, fetch=fail, now=lambda: 400, clock=lambda: 0)
    after, _ = load_cohort_asof(db, 450, trial_start_ms=100)
    assert failed['failed'] == 2 and after == current


def test_control_empty_order_read_is_explicit_and_outside_cohort_is_never_queried(tmp_path):
    from src.evaluation_cohort import refresh_cohort_orders
    db = tmp_path / 'db'
    rows = [{'wallet': w, 'source': 'leaderboard', 'fingerprint': {}} for w in (A, B)]
    record_profiles(rows[:1], 100, db)
    freeze_cohort(db, 100, minimum=1)
    calls = []
    def empty(wallet, timeout):
        calls.append(wallet)
        return []
    refresh_cohort_orders(rows, db, fetch=empty, now=lambda: 200, clock=lambda: 0)
    assert calls == [A]
    current, _ = load_cohort_asof(db, 250, trial_start_ms=100)
    assert current[0]['fingerprint']['order_profile']['orders'] == 0
    assert current[0]['fingerprint']['order_observation']['status'] == 'ok'


def test_frequent_control_refreshes_keep_daily_history_and_exclude_new_nonmembers(tmp_path):
    from src.discovery_store import DiscoveryStore
    db = tmp_path / 'db'
    def rows(wallet, value):
        return [{'wallet': wallet, 'source': 'leaderboard', 'fingerprint': {'value': value}}]
    record_profiles(rows(A, 1), 100, db)
    freeze_cohort(db, 100, minimum=1)
    for stamp in (200, 300, 400):
        record_profiles(rows(A, stamp) + rows(B, stamp), stamp, db)
    record_profiles(rows(A, 500), 86400_100, db)
    with DiscoveryStore(db) as store:
        saved = store.db.execute('SELECT wallet,ts FROM evaluation_profiles ORDER BY ts').fetchall()
    assert [tuple(row) for row in saved] == [(A, 100), (A, 400), (A, 86400_100)]
