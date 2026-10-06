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
    from scripts.discovery_artifacts import cleanup, prefix
    class Client:
        truncated = False
        deleted = []
        def artifacts(self):
            return [{'id': i, 'name': prefix('state', 'main') + str(i), 'expired': False} for i in [1, 2, 3]]
        def delete(self, artifact):
            self.deleted.append(artifact['id'])
    db = tmp_path / 'db'
    with DiscoveryStore(db):
        pass
    client = Client()
    with pytest.raises(ValueError):
        cleanup(client, db, 'main', 999)
    assert not client.deleted
    cleanup(client, db, 'main', 3)
    assert client.deleted == [1]


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
