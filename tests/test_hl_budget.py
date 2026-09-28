from src import utils


class Clock:
    value = 0
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class Response:
    def __init__(self, status=200, data=None):
        self.status_code, self.data = status, data or []
    def raise_for_status(self):
        pass
    def json(self):
        return self.data


def test_weighted_reads_charge_large_fill_pages_before_next_request(monkeypatch):
    from src.hl_budget import ReadBudget
    clock, calls = Clock(), []
    def post(*args, **kwargs):
        calls.append(clock.now())
        return Response(data=[{}] * 2000)
    monkeypatch.setattr(utils.requests, 'post', post)
    with ReadBudget(seconds=60, clock=clock.now, sleep=clock.sleep) as budget:
        assert utils.hl_read({'type': 'userFillsByTime', 'user': 'a'})['ok']
        assert utils.hl_read({'type': 'userFillsByTime', 'user': 'b'})['ok']
    assert calls[1] - calls[0] >= 12  # 20 base + 100 response weight at 600/min
    assert budget.report()['weight'] == 240


def test_rate_limit_stops_the_read_batch_without_retry_storm(monkeypatch):
    from src.hl_budget import ReadBudget
    clock, calls = Clock(), []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(429)
    monkeypatch.setattr(utils.requests, 'post', post)
    with ReadBudget(seconds=60, clock=clock.now, sleep=clock.sleep) as budget:
        first = utils.hl_read({'type': 'userFillsByTime', 'user': 'a'})
        second = utils.hl_read({'type': 'userFillsByTime', 'user': 'b'})
    assert not first['ok'] and not second['ok']
    assert calls == [1] and budget.report()['stopped_reason'] == 'rate_limited'


def test_deadline_preserves_time_for_checkpoint_instead_of_another_request(monkeypatch):
    from src.hl_budget import ReadBudget
    clock, calls = Clock(), []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(data=[{}] * 2000)
    monkeypatch.setattr(utils.requests, 'post', post)
    with ReadBudget(seconds=5, clock=clock.now, sleep=clock.sleep) as budget:
        utils.hl_read({'type': 'userFillsByTime'})
        assert not utils.hl_read({'type': 'clearinghouseState'})['ok']
    assert calls == [1] and clock.value < 5
    assert budget.report()['stopped_reason'] == 'time_budget'


def test_priority_rotation_reserves_exploration_and_visits_unattempted_wallets_first():
    from src.scan_progress import priority_order
    rows = {f'f{i}': {'source': 'funding_route'} for i in range(10)}
    rows.update({f'p{i}': {'source': 'public_trades', 'discovery': True} for i in range(4)})
    first = priority_order(rows, {}, limit=5)
    assert any(rows[w].get('discovery') for w in first)
    second = priority_order(rows, {w: 100 for w in first}, limit=5)
    assert set(first).isdisjoint(second)


def test_unfinished_histories_get_resume_slots_without_excluding_new_wallets():
    from src.scan_progress import priority_order
    rows = {f'w{i}': {'source': 'funding_route'} for i in range(8)}
    pending = {'w6', 'w7'}
    attempts = {'w6': 100, 'w7': 101}
    scheduled = priority_order(rows, attempts, limit=4, pending=pending)
    assert scheduled == ['w6', 'w0', 'w7', 'w1']


def test_priority_failure_is_checkpointed_and_next_run_visits_unattempted_wallet(tmp_path, monkeypatch):
    from src import scanner
    from src.hl_budget import ReadBudget
    from src.scan_progress import ScanProgress
    wallets = ['0x' + str(i) * 40 for i in range(2, 5)]
    config = {'target_wallet': '0x' + '1' * 40, 'scanner': {}}
    monkeypatch.setattr(scanner, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(scanner, 'get_recent_bridge_depositors', lambda: wallets)
    monkeypatch.setattr(scanner, 'discovery_targets', lambda _: {})
    calls = []
    def scan(wallet, *a, **k):
        calls.append(wallet)
        budget.stopped_reason = 'rate_limited'
    monkeypatch.setattr(scanner, 'scan_specific_wallet', scan)
    for _ in range(2):
        with ReadBudget() as budget:
            assert scanner.scan_priority_targets({}, config, {'low': .65}) == []
        assert budget.report()['phases']['priority']['deferred'] == 2
    assert len(set(calls)) == 2
    assert len(ScanProgress(tmp_path / '.local' / 'discovery.sqlite3', 'priority').attempts) == 2


def test_partial_fill_history_is_visible_in_budget_report(tmp_path, monkeypatch):
    from src import history, scanner
    from src.hl_budget import ReadBudget
    monkeypatch.setattr(scanner, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(history, 'cached_fill_history', lambda *a, **k: {'fills': [], 'status': 'partial'})
    with ReadBudget() as budget:
        scanner.get_candidate_fills('0x' + '1' * 40)
    assert budget.report()['status'] == 'partial'
    assert budget.report()['incomplete_histories'] == 1


def test_stopped_scanner_still_publishes_report_and_preserves_next_run_rotation(tmp_path, monkeypatch):
    import json

    from src import calibration, scanner
    from src.history import FillBatch
    from src.hl_budget import ReadBudget
    wallets = ['0x' + str(i) * 40 for i in range(2, 5)]
    config = {'target_wallet': '0x' + '1' * 40, 'scanner': {'max_leaderboard_wallets': 3,
        'min_fills_for_comparison': 10, 'fills_lookback_days': 21}, 'alert_thresholds': {}}
    data = tmp_path / 'data'
    monkeypatch.setattr(scanner, 'DATA_DIR', data)
    monkeypatch.setattr(calibration, 'POPULATION_PATH', data / 'calibration' / 'population.json')
    monkeypatch.setattr(calibration, 'MARKET_FREQ_PATH', data / 'calibration' / 'market_frequency.json')
    monkeypatch.setattr(scanner, 'load_config', lambda: config)
    monkeypatch.setattr(scanner, '_effective_thresholds', lambda _: {'low': .65})
    monkeypatch.setattr(scanner, 'build_fingerprint', lambda: {})
    monkeypatch.setattr(scanner, 'scan_priority_targets', lambda *a: [])
    monkeypatch.setattr(scanner, 'read_cursor', lambda _: None)
    monkeypatch.setattr(scanner, 'fetch_leaderboard', lambda: [{'ethAddress': w} for w in wallets])
    calls = []
    def fills(wallet, *a):
        calls.append(wallet)
        budget.stopped_reason = 'rate_limited'
        return FillBatch({'fills': [], 'status': 'error', 'error': 'rate limited'})
    monkeypatch.setattr(scanner, 'get_candidate_fills', fills)
    for _ in range(2):
        with ReadBudget() as budget:
            result = scanner._scan_leaderboard()
        saved = json.loads((data / 'scans' / 'latest.json').read_text())
        assert saved['collection']['status'] == 'partial'
        assert saved['collection']['phases']['leaderboard']['deferred'] == 2
        assert result['wallets_scanned'] == 1
    assert len(set(calls)) == 2
