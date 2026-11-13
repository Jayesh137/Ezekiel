import gzip
import sqlite3
import zipfile

import pytest

from src.discovery_state import import_observations, restore_archive, snapshot
from src.discovery_store import DiscoveryStore

A, B = ['0x' + char * 40 for char in '12']


def test_cache_pressure_preserves_bounded_backfill_prefix_and_resume(tmp_path, monkeypatch):
    from src import discovery_state as state
    from src import history
    db = tmp_path / 'active.sqlite3'
    monkeypatch.setattr(state, 'MAX_FILL_ROWS', 6, raising=False)
    monkeypatch.setattr(state, 'PENDING_FILL_ROWS', 4, raising=False)
    monkeypatch.setattr(history, 'PAGE_SIZE', 3)
    old = [{'time': 100 + i, 'tid': i, 'coin': 'BTC'} for i in range(3)]
    history.cached_fill_history(A, 0, 1000, db_path=db, fetch=lambda _: old, max_pages=1)
    history.cached_fill_history(B, 0, 1000, db_path=db,
        fetch=lambda body: [{'time': 500 + i, 'tid': i, 'coin': 'BTC'} for i in range(6)] if body['startTime'] == 0 else [])
    state.compact(db, now_ms=1000)
    calls = []
    resumed = history.cached_fill_history(A, 0, 1000, db_path=db,
        fetch=lambda body: calls.append(body['startTime']) or [old[-1]])
    assert calls == [102] and resumed['status'] == 'ok'
    assert len(resumed['fills']) == 3
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM fills').fetchone()[0] == 6


def test_oversized_pending_history_cannot_exceed_global_cache_budget(tmp_path, monkeypatch):
    from src import discovery_state as state
    from src import history
    db = tmp_path / 'active.sqlite3'
    monkeypatch.setattr(state, 'MAX_FILL_ROWS', 6, raising=False)
    monkeypatch.setattr(state, 'PENDING_FILL_ROWS', 2, raising=False)
    monkeypatch.setattr(history, 'PAGE_SIZE', 3)
    history.cached_fill_history(A, 0, 1000, db_path=db,
        fetch=lambda _: [{'time': 100 + i, 'tid': i, 'coin': 'BTC'} for i in range(3)], max_pages=1)
    history.cached_fill_history(B, 0, 1000, db_path=db,
        fetch=lambda body: [{'time': 500 + i, 'tid': i, 'coin': 'BTC'} for i in range(6)] if body['startTime'] == 0 else [])
    state.compact(db, now_ms=1000)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM fills').fetchone()[0] == 6
        assert not conn.execute('SELECT * FROM fill_progress WHERE wallet=?', (A,)).fetchone()


def test_snapshot_captures_committed_wal_and_restores_independent_database(tmp_path):
    original = tmp_path / 'live.sqlite3'
    with DiscoveryStore(original) as store:
        store.ingest_observations('authority_actions', [{'event_id': 'a', 'account': A}], 1000)
        packed = snapshot(original, tmp_path / 'export')
    archive = tmp_path / 'artifact.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.write(packed, 'snapshot.sqlite3.gz')
    restored = tmp_path / 'restored.sqlite3'
    restore_archive(archive, restored)
    with DiscoveryStore(restored) as store:
        assert store.observations('authority_actions')[0]['event_id'] == 'a'


def test_import_is_idempotent_and_does_not_overwrite_owner_scheduling_state(tmp_path):
    shard, main = tmp_path / 'shard.sqlite3', tmp_path / 'main.sqlite3'
    with DiscoveryStore(shard) as store:
        store.ingest_observations('circle', [{'event_id': 'tx'}], 1000)
        store.set_meta('evaluation_cohort', {'wallets': [B]})
    with DiscoveryStore(main) as store:
        store.set_meta('evaluation_cohort', {'wallets': [A]})
    import_observations(shard, main, 'artifact:1')
    import_observations(shard, main, 'artifact:1')
    with DiscoveryStore(main) as store:
        assert len(store.observations('circle')) == 1
        assert store.meta('evaluation_cohort')['wallets'] == [A]
        assert store.meta('imported_artifacts') == ['artifact:1']


def test_corrupt_or_oversized_archive_cannot_replace_valid_state(tmp_path):
    dest = tmp_path / 'live.sqlite3'
    with sqlite3.connect(dest) as db:
        db.execute('CREATE TABLE kept (value TEXT)')
    before = dest.read_bytes()
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('snapshot.sqlite3.gz', gzip.compress(b'not sqlite'))
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        restore_archive(archive, dest)
    assert dest.read_bytes() == before
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../escape', b'bad')
    with pytest.raises(ValueError):
        restore_archive(archive, dest)
    assert not (tmp_path.parent / 'escape').exists()


def test_observer_import_preserves_fill_ids_and_snapshot_capture_time(tmp_path):
    shard, main = tmp_path / 'shard.sqlite3', tmp_path / 'main.sqlite3'
    with sqlite3.connect(shard) as db:
        db.execute('CREATE TABLE fills (wallet TEXT, event_id TEXT PRIMARY KEY, ts INTEGER, raw TEXT)')
        db.execute('INSERT INTO fills VALUES (?,?,?,?)', (A, 'fill-1', 1000, '{"time":1000,"coin":"BTC"}'))
    import_observations(shard, main, 'artifact:2')
    with sqlite3.connect(main) as db:
        assert db.execute('SELECT event_id,ts FROM fills').fetchone() == ('fill-1', 1000)


def test_recent_snapshot_pruning_retains_older_explicit_authority(tmp_path):
    now = 100 * 86400_000
    with DiscoveryStore(tmp_path / 'db') as store:
        store.ingest_observations('authority_actions', [{'event_id': 'old', 'kind': 'approve_agent'}], 1)
        store.ingest_observations('circle', [{'event_id': 'old-transfer'}], 1)
        store.prune(now - 30 * 86400_000)
        assert store.observations('authority_actions')
        assert not store.observations('circle')


def test_artifact_restore_is_branch_scoped_and_failure_is_explicit(tmp_path):
    from scripts.discovery_artifacts import prefix, restore_state
    class Client:
        truncated = False
        def artifacts(self):
            return [{'id': 1, 'name': prefix('state', 'main') + '1', 'expired': False},
                    {'id': 2, 'name': prefix('state', 'feature') + '2', 'expired': False}]
        def download(self, artifact, destination):
            assert artifact['id'] == 1
            raise OSError('unavailable')
    report = restore_state(Client(), tmp_path / 'db', tmp_path / 'state.json', 'main')
    assert report['status'] == 'restore_error'
    assert report['ready'] is False
    assert not (tmp_path / 'db').exists()


def test_artifact_cleanup_requires_acknowledged_checkpoint_and_keeps_two(tmp_path):
    from scripts.discovery_artifacts import ACK_ATTEMPTS, ACK_DELAY_SECONDS, cleanup, prefix
    class Client:
        truncated = False
        def __init__(self):
            self.calls, self.deleted = 0, []
        def artifacts(self):
            self.calls += 1
            return [{'id': i, 'name': prefix('state', 'main') + str(i), 'expired': False} for i in [1, 2, 3]]
        def delete(self, artifact):
            self.deleted.append(artifact['id'])
    db = tmp_path / 'db'
    with DiscoveryStore(db):
        pass
    client, naps = Client(), []
    with pytest.raises(ValueError):
        cleanup(client, db, 'main', 999, sleep=naps.append)
    # Refused only after every listing, with a nap between each and none after the last.
    assert client.calls == ACK_ATTEMPTS and naps == [ACK_DELAY_SECONDS] * (ACK_ATTEMPTS - 1)
    assert not client.deleted
    client, naps = Client(), []
    assert cleanup(client, db, 'main', 3, sleep=naps.append) == {
        'deleted': 1, 'retained_checkpoints': 2, 'listings': 1}
    assert client.deleted == [1] and naps == []


def test_artifact_cleanup_waits_for_the_listing_to_show_a_fresh_upload(tmp_path):
    # Measured 2026-10-08/09: three scan runs failed (two paging HIGH) because GitHub's
    # artifact listing had not yet caught up with the checkpoint uploaded seconds before.
    from scripts.discovery_artifacts import ACK_DELAY_SECONDS, cleanup, prefix
    class Lagging:
        truncated = False
        def __init__(self):
            self.calls, self.deleted = 0, []
        def artifacts(self):
            self.calls += 1
            ids = [1, 2] if self.calls == 1 else [1, 2, 3]
            return [{'id': i, 'name': prefix('state', 'main') + str(i), 'expired': False} for i in ids]
        def delete(self, artifact):
            self.deleted.append(artifact['id'])
    db = tmp_path / 'db'
    with DiscoveryStore(db):
        pass
    naps, client = [], Lagging()
    report = cleanup(client, db, 'main', 3, sleep=naps.append)
    assert client.calls == 2 and naps == [ACK_DELAY_SECONDS] and client.deleted == [1]
    # The step's JSON says the lag was absorbed: a live run with listings > 1 is the fix working.
    assert report['listings'] == 2


@pytest.mark.parametrize('corrupt_zip', [False, True])
def test_bad_observer_shard_does_not_disable_valid_checkpoint(tmp_path, corrupt_zip):
    from scripts.discovery_artifacts import prefix, restore_state
    original = tmp_path / 'original.sqlite3'
    with DiscoveryStore(original):
        pass
    packed = snapshot(original, tmp_path / 'export')
    class Client:
        truncated = False
        def artifacts(self):
            return [{'id': 1, 'name': prefix('state', 'main') + '1'},
                    {'id': 2, 'name': prefix('observations', 'main') + '2'}]
        def download(self, artifact, destination):
            if artifact['id'] == 2 and corrupt_zip:
                destination.write_bytes(b'not zip')
                return
            with zipfile.ZipFile(destination, 'w') as z:
                if artifact['id'] == 1:
                    z.write(packed, 'snapshot.sqlite3.gz')
                else:
                    z.writestr('snapshot.sqlite3.gz', gzip.compress(b'not sqlite'))
    report = restore_state(Client(), tmp_path / 'restored', tmp_path / 'state.json', 'main')
    assert report['ready'] is True
    assert report['pending_shards'] == 1
    assert report['errors'][0]['artifact_id'] == 2


def _bulky_store(db, *, fills=400, circle=300, authority=5, pending=None):
    """Old-to-new fills and observations whose text does not compress away."""
    import hashlib
    import json as _json

    def noise(i):
        return hashlib.sha256(str(i).encode()).hexdigest() * 4
    with DiscoveryStore(db) as store:
        store.db.execute('CREATE TABLE IF NOT EXISTS fills (wallet TEXT, event_id TEXT PRIMARY KEY, ts INTEGER, raw TEXT)')
        store.db.execute('CREATE TABLE IF NOT EXISTS fill_progress (wallet TEXT PRIMARY KEY, start_ms INTEGER, cursor_ms INTEGER)')
        store.db.execute('CREATE TABLE IF NOT EXISTS fill_coverage (wallet TEXT, start_ms INTEGER, end_ms INTEGER)')
        for i in range(fills):
            wallet = (pending if pending and i < 20 else A if i % 2 else B)
            store.db.execute('INSERT INTO fills VALUES (?,?,?,?)',
                             (wallet, f'f{i}', 1_000 + i, _json.dumps({'tid': i, 'pad': noise(i)})))
        if pending:
            store.db.execute('INSERT INTO fill_progress VALUES (?,?,?)', (pending, 0, 500))
        store.db.commit()
        store.ingest_observations('authority_actions',
                                  [{'event_id': f'auth{i}', 'pad': noise(-i)} for i in range(authority)], 1)
        for i in range(circle):
            store.ingest_observations('circle', [{'event_id': f'c{i}', 'pad': noise(10_000 + i)}], 2 + i)


def test_a_checkpoint_over_its_byte_budget_is_trimmed_oldest_first_until_it_fits(tmp_path, monkeypatch):
    # 2026-10-03 onward: retention caps rows, the artifact budget is bytes, and
    # once the tables reached their caps every snapshot was refused - discovery
    # froze on a three-day-old checkpoint while 465 shards piled up unimported.
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    _bulky_store(db)
    plain = state.snapshot(db, tmp_path / 'probe').stat().st_size
    monkeypatch.setattr(state, 'MAX_ARCHIVE_BYTES', int(plain * 0.6))
    with pytest.raises(state.OverBudget):
        state.snapshot(db, tmp_path / 'refused')
    path = state.snapshot_within_budget(db, tmp_path / 'out')
    assert path.stat().st_size <= state.MAX_ARCHIVE_BYTES
    with sqlite3.connect(db) as conn:
        newest = conn.execute('SELECT max(ts) FROM fills').fetchone()[0]
        oldest = conn.execute('SELECT min(ts) FROM fills').fetchone()[0]
        assert newest == 1_399 and oldest > 1_000
    with DiscoveryStore(db) as store:
        trimmed = store.meta('storage_retention', {}).get('byte_trimmed') or {}
        assert trimmed.get('fills', 0) > 0 and trimmed.get('rounds', 0) >= 1


def test_active_backfills_and_authority_are_never_trimmed_for_bytes(tmp_path, monkeypatch):
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    pend = '0x' + '9' * 40
    _bulky_store(db, pending=pend)
    plain = state.snapshot(db, tmp_path / 'probe').stat().st_size
    monkeypatch.setattr(state, 'MAX_ARCHIVE_BYTES', int(plain * 0.6))
    state.snapshot_within_budget(db, tmp_path / 'out')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM fills WHERE wallet=?', (pend,)).fetchone()[0] == 20
        assert conn.execute('SELECT * FROM fill_progress WHERE wallet=?', (pend,)).fetchone()
    with DiscoveryStore(db) as store:
        assert len(store.observations('authority_actions')) == 5


def test_a_budget_that_cannot_be_met_still_fails_loudly(tmp_path, monkeypatch):
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    _bulky_store(db, fills=50, circle=20, authority=200)
    monkeypatch.setattr(state, 'MAX_ARCHIVE_BYTES', 512)
    with pytest.raises(state.OverBudget):
        state.snapshot_within_budget(db, tmp_path / 'out')


def _agent_snapshot(account, ts, agents, *, complete=True):
    return {"event_id": f"agents:{account}:{ts}", "kind": "agent_snapshot", "account": account,
            "ts_ms": ts, "success": True, "complete": complete,
            "agents": [{"address": a, "name": None, "validUntil": None} for a in agents]}


def test_identical_consecutive_agent_snapshots_collapse_without_changing_authority(tmp_path):
    # 2026-10-27: check_agents wrote one agent_snapshot per wallet per trace run
    # and nothing ever folded them: 88,678 authority rows (50 MB) in 20 days, all
    # exempt from byte trimming, so every checkpoint since 2026-10-21 was over
    # its artifact budget and discovery froze on a six-day-old checkpoint.
    from src import discovery_state as state
    from src.authority_history import _snapshot_changes
    db = tmp_path / 'scan.sqlite3'
    agent, other = '0x' + 'a' * 40, '0x' + 'b' * 40
    rows = [_agent_snapshot(A, 1, [agent]), _agent_snapshot(A, 2, [agent]),
            _agent_snapshot(A, 3, [agent]), _agent_snapshot(A, 4, [agent, other]),
            _agent_snapshot(A, 5, [agent, other]), _agent_snapshot(A, 6, [agent]),
            _agent_snapshot(A, 7, [agent], complete=False),
            _agent_snapshot(B, 1, [other]), _agent_snapshot(B, 2, [other]),
            {"event_id": "approve-1", "kind": "approve_agent", "account": B, "authority": agent,
             "ts_ms": 3, "success": True}]
    with DiscoveryStore(db) as store:
        store.ingest_observations('authority_actions', rows, 9)
        before = _snapshot_changes(store.observations('authority_actions'), 10**12)
    assert state.collapse_agent_snapshots(db) == 4            # A@2, A@3, A@5, B@2
    with DiscoveryStore(db) as store:
        kept = store.observations('authority_actions')
        after = _snapshot_changes(kept, 10**12)
    assert sorted(r['event_id'] for r in kept) == sorted(
        [f'agents:{A}:1', f'agents:{A}:4', f'agents:{A}:6', f'agents:{A}:7', f'agents:{B}:1', 'approve-1'])
    key = lambda r: (r['event_id'], r['kind'], r['account'], r.get('authority'))  # noqa: E731
    assert sorted(map(key, after)) == sorted(map(key, before))
    assert state.collapse_agent_snapshots(db) == 0            # idempotent


def test_compact_collapses_snapshots_and_records_it(tmp_path):
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    agent = '0x' + 'a' * 40
    with DiscoveryStore(db) as store:
        store.ingest_observations('authority_actions',
                                  [_agent_snapshot(A, t, [agent]) for t in range(1, 6)], 9)
    state.compact(db, now_ms=10)
    with DiscoveryStore(db) as store:
        assert len(store.observations('authority_actions')) == 1
        assert store.meta('storage_retention', {}).get('collapsed_snapshots') == 4


# --- deposit seeds are bounded by what the queue can reach (2026-11-13) -------
# 264,669 seeds, 706 ever checked, about a third of the 48 MiB checkpoint, and
# no trimmer could touch them: the scan failed five runs in eight once trace
# began delivering deposits again.

DAY_MS = 86400_000


def _seed(store, wallet, last_seen, checked=0):
    store.db.execute('INSERT INTO deposit_seeds(wallet,first_seen_ms,last_seen_ms,last_checked_ms,next_check_ms) '
                     'VALUES (?,?,?,?,?)', (wallet, last_seen, last_seen, checked, checked))


def _w(i):
    return '0x' + f'{i:040x}'


def test_seeds_the_queue_cannot_select_are_dropped_and_checked_ones_kept(tmp_path):
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    now = 100 * DAY_MS
    with DiscoveryStore(db) as store:
        _seed(store, _w(1), now - 40 * DAY_MS)                    # too old to be selected
        _seed(store, _w(2), now - 40 * DAY_MS, checked=now - 39 * DAY_MS)   # old but checked: state
        _seed(store, _w(3), now - 2 * DAY_MS)
        store.db.commit()
    state.compact(db, now_ms=now)
    with DiscoveryStore(db) as store:
        assert [r[0] for r in store.db.execute('SELECT wallet FROM deposit_seeds ORDER BY wallet')] == [_w(2), _w(3)]
        retention = store.meta('storage_retention', {})
        assert retention['deposit_seeds_dropped'] == 1 and retention['deposit_seed_days'] == 30


def test_the_seed_cap_keeps_the_newest_which_is_the_order_the_queue_reads():
    import sqlite3

    from src import discovery_state as state
    from src.discovery_store import DiscoveryStore as Store
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE deposit_seeds (wallet TEXT PRIMARY KEY, first_seen_ms INTEGER, last_seen_ms INTEGER, '
               'last_checked_ms INTEGER DEFAULT 0, next_check_ms INTEGER DEFAULT 0)')
    now = 100 * DAY_MS
    for i in range(10):
        db.execute('INSERT INTO deposit_seeds(wallet,first_seen_ms,last_seen_ms) VALUES (?,?,?)',
                   (_w(i), now - i * 1000, now - i * 1000))
    db.execute('UPDATE deposit_seeds SET last_checked_ms=5 WHERE wallet=?', (_w(9),))   # oldest, but checked
    dropped = state.prune_deposit_seeds(db, {'deposit_seeds'}, now, keep=3)
    kept = [r[0] for r in db.execute('SELECT wallet FROM deposit_seeds ORDER BY last_seen_ms DESC')]
    assert kept == [_w(0), _w(1), _w(2), _w(9)] and dropped == 6
    assert state.prune_deposit_seeds(db, {'deposit_seeds'}, now, keep=3) == 0     # idempotent
    assert Store is not None


def test_what_the_scanner_would_read_next_survives_the_prune(tmp_path):
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    now = 100 * DAY_MS
    with DiscoveryStore(db) as store:
        for i in range(50):
            _seed(store, _w(i), now - i * 60_000)
        store.db.commit()
        before = [r['wallet'] for r in store.deposit_candidates(limit=5, now_ms=now)]
    state.compact(db, now_ms=now)
    with DiscoveryStore(db) as store:
        assert [r['wallet'] for r in store.deposit_candidates(limit=5, now_ms=now)] == before


def test_a_store_without_the_table_is_left_alone():
    import sqlite3

    from src import discovery_state as state
    assert state.prune_deposit_seeds(sqlite3.connect(':memory:'), set(), 0) == 0


# --- paused backfills: expiry and who gets protected (2026-11-13) --------------
# Eight backfills last attempted 2026-09-28/29 held 97,915 of the 100,000
# protected fill rows six weeks later, while the five being worked held none.

def _backfill_store(db, rows):
    """rows: (wallet, n_fills, cursor_ms, last_attempt_ms)."""
    with DiscoveryStore(db) as store:
        store.db.execute('CREATE TABLE IF NOT EXISTS fills (wallet TEXT, event_id TEXT PRIMARY KEY, ts INTEGER, raw TEXT)')
        store.db.execute('CREATE TABLE IF NOT EXISTS fill_progress (wallet TEXT PRIMARY KEY, start_ms INTEGER, cursor_ms INTEGER)')
        store.db.execute('CREATE TABLE IF NOT EXISTS fill_coverage (wallet TEXT PRIMARY KEY, start_ms INTEGER, end_ms INTEGER)')
        attempts = {}
        for wallet, n, cursor, attempt in rows:
            for i in range(n):
                store.db.execute('INSERT INTO fills VALUES (?,?,?,?)', (wallet, f'{wallet}:{i}', cursor - i, '{}'))
            store.db.execute('INSERT INTO fill_progress VALUES (?,?,?)', (wallet, cursor - 21 * DAY_MS, cursor))
            attempts[wallet] = attempt
        store.db.commit()
        store.set_meta('scan_attempts:priority', attempts)


def test_a_backfill_no_scanner_read_can_resume_is_expired_and_its_fills_stay(tmp_path):
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    now = 200 * DAY_MS
    dead, live = _w(1), _w(2)
    _backfill_store(db, [(dead, 5, now - 46 * DAY_MS, now - 45 * DAY_MS),
                         (live, 5, now - 1 * DAY_MS, now - 1 * DAY_MS)])
    state.compact(db, now_ms=now)
    with DiscoveryStore(db) as store:
        pending = {r[0] for r in store.db.execute('SELECT wallet FROM fill_progress')}
        assert pending == {live}
        assert store.db.execute('SELECT count(*) FROM fills WHERE wallet=?', (dead,)).fetchone()[0] == 5
        retention = store.meta('storage_retention', {})
        assert retention['backfills_expired'] == 1 and retention['backfill_expiry_days'] == 42


def test_a_backfill_inside_the_widest_read_window_is_kept():
    import sqlite3

    from src import discovery_state as state
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fill_progress (wallet TEXT PRIMARY KEY, start_ms INTEGER, cursor_ms INTEGER)')
    now = 200 * DAY_MS
    db.execute('INSERT INTO fill_progress VALUES (?,?,?)', ('a', 0, now - 41 * DAY_MS))
    db.execute('INSERT INTO fill_progress VALUES (?,?,?)', ('b', 0, now - 43 * DAY_MS))
    assert state.expire_backfills(db, {'fill_progress'}, now) == 1
    assert [r[0] for r in db.execute('SELECT wallet FROM fill_progress')] == ['a']


def test_protection_goes_to_the_backfill_attempted_most_recently(tmp_path, monkeypatch):
    from src import discovery_state as state
    db = tmp_path / 'scan.sqlite3'
    now = 200 * DAY_MS
    stale, working = _w(3), _w(4)
    # Both inside the read window; the budget protects only one of them.
    _backfill_store(db, [(stale, 4, now - 20 * DAY_MS, now - 20 * DAY_MS),
                         (working, 4, now - 1 * DAY_MS, now - 1 * DAY_MS)])
    monkeypatch.setattr(state, 'PENDING_FILL_ROWS', 4)
    monkeypatch.setattr(state, 'MAX_FILL_ROWS', 5)
    state.compact(db, now_ms=now)
    with DiscoveryStore(db) as store:
        assert store.db.execute('SELECT count(*) FROM fills WHERE wallet=?', (working,)).fetchone()[0] == 4
        assert store.db.execute('SELECT * FROM fill_progress WHERE wallet=?', (working,)).fetchone()
        assert not store.db.execute('SELECT * FROM fill_progress WHERE wallet=?', (stale,)).fetchone()
