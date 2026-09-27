from scripts import check_dormancy as check
from src.hl_budget import ReadBudget

WALLETS = ['0x' + str(i) * 40 for i in range(1, 4)]
DAY = 86400_000
TARGET = [{'time': day * DAY} for day in [10, 12, 14, 16, 18, 20, 48]]


def portfolio(day):
    return {'ok': True, 'data': [['allTime', {'accountValueHistory': [[day * DAY, '100']]}]]}


def test_old_account_is_ruled_out_without_paging_fills():
    calls = []
    def fetch(body):
        calls.append(body['type'])
        return portfolio(1)
    candidates, coverage = check.collect_candidates(WALLETS[:1], TARGET, {}, fetch=fetch)
    assert calls == ['portfolio'] and coverage['checked'] == WALLETS[:1]
    assert check.build_report(TARGET, candidates)['handoffs'] == {}


def test_plausible_birth_requires_observed_trades_and_rejects_older_activity():
    calls = []
    def fetch(body):
        calls.append(body['type'])
        return portfolio(21) if body['type'] == 'portfolio' else {'ok': True, 'data': [{'time': DAY}]}
    candidates, coverage = check.collect_candidates(WALLETS[:1], TARGET, {}, fetch=fetch)
    assert calls == ['portfolio', 'userFills'] and coverage['status'] == 'ok'
    assert check.build_report(TARGET, candidates)['handoffs'] == {}
    candidates, _ = check.collect_candidates(WALLETS[:1], TARGET, {}, fetch=lambda b:
        portfolio(21) if b['type'] == 'portfolio' else {'ok': True, 'data': [{'time': 22 * DAY}]})
    assert WALLETS[0] in check.build_report(TARGET, candidates)['handoffs']


def test_throttled_batch_publishes_partial_coverage_and_rotates_next_run():
    calls = []
    def fetch(body):
        calls.append(body['user'])
        budget.stopped_reason = 'rate_limited'
        return {'ok': False, 'error': 'rate limited'}
    with ReadBudget(seconds=100) as budget:
        candidates, coverage = check.collect_candidates(WALLETS, TARGET, {}, fetch=fetch)
    assert not candidates and len(calls) == 1
    assert coverage['status'] == 'partial' and coverage['deferred'] == 2
    again = []
    check.collect_candidates(WALLETS, TARGET, {'collection': coverage},
                             fetch=lambda b: again.append(b['user']) or portfolio(1))
    assert again[-1] == calls[0]


def test_failed_reads_keep_prior_handoffs_stale_and_cannot_realert(monkeypatch, tmp_path):
    import json
    wallet = WALLETS[0]
    old = {'handoffs': {wallet: {'score': .9, 'delay_days': 1, 'gap_length': 28}},
           'computed_at': '2026-09-01T00:00:00Z'}
    directory = tmp_path / 'dormancy'
    directory.mkdir()
    (directory / 'latest.json').write_text(json.dumps(old))
    monkeypatch.setattr(check, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(check, 'load_config', lambda: {})
    monkeypatch.setattr(check, 'load_fills', lambda: TARGET)
    monkeypatch.setattr(check, 'candidate_wallets', lambda _: [wallet])
    monkeypatch.setattr(check, 'collect_candidates', lambda *a, **k:
        ({}, {'status': 'partial', 'checked': [], 'attempted': 1, 'deferred': 0}))
    saved, alerts = [], []
    monkeypatch.setattr(check, 'save', saved.append)
    monkeypatch.setattr(check, 'alert_dormancy_handoff', lambda *a: alerts.append(a))
    monkeypatch.setattr(check, 'alert_target_dormant', lambda *a: None)
    assert check.main() == 0
    assert saved[0]['handoffs'][wallet]['stale'] is True and not alerts
