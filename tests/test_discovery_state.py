import gzip
import sqlite3
import zipfile

import pytest

from src.discovery_state import import_observations, restore_archive, snapshot
from src.discovery_store import DiscoveryStore

A, B = ['0x' + char * 40 for char in '12']


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
