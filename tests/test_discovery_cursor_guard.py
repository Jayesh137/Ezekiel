import json

import pytest


@pytest.mark.parametrize('snapshot,upload,artifact', [
    ('failure', 'skipped', ''), ('success', 'failure', ''),
    ('success', 'skipped', ''), ('success', 'success', ''),
])
def test_failed_publication_restores_only_replay_cursors(tmp_path, snapshot, upload, artifact):
    from scripts.discovery_cursor_guard import guard
    circle = tmp_path / 'data/circle_flows/latest.json'
    circle.parent.mkdir(parents=True)
    circle.write_text(json.dumps({'last_block': 200, 'findings': ['fresh'], 'alerted': ['sent']}))
    pool = tmp_path / 'data/correlations/cctp_pool.json'
    pool.parent.mkdir(parents=True)
    pool.write_text(json.dumps({'cursor_ms': 200, 'forwarder': 'new', 'deposits': ['fresh']}))
    old = {'data/circle_flows/latest.json': {'last_block': 100},
           'data/correlations/cctp_pool.json': {'cursor_ms': 100, 'forwarder': 'old'}}
    result = guard(tmp_path, snapshot, upload, artifact, read_baseline=lambda path: old.get(path, {}))
    assert not result['acknowledged']
    assert json.loads(circle.read_text()) == {'last_block': 100, 'findings': ['fresh'], 'alerted': ['sent']}
    assert json.loads(pool.read_text()) == {'cursor_ms': 100, 'forwarder': 'old', 'deposits': ['fresh']}


def test_durable_upload_preserves_progress_and_missing_baseline_replays(tmp_path):
    from scripts.discovery_cursor_guard import guard
    path = tmp_path / 'data/circle_flows/latest.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"last_block":200,"findings":["fresh"]}')
    result = guard(tmp_path, 'success', 'success', '123', read_baseline=lambda _: {})
    assert result['acknowledged'] and json.loads(path.read_text())['last_block'] == 200
    guard(tmp_path, 'failure', 'skipped', '', read_baseline=lambda _: {})
    assert json.loads(path.read_text()) == {'findings': ['fresh']}


def test_failed_authority_upload_keeps_snapshots_for_retry(tmp_path):
    from scripts.discovery_cursor_guard import PENDING, guard
    from src.discovery_store import DiscoveryStore
    db = tmp_path / 'data/.local/discovery.sqlite3'
    row = {'event_id': 'agent:1', 'account': '0x' + '1' * 40, 'ts_ms': 100, 'agents': []}
    with DiscoveryStore(db) as store:
        store.ingest_observations('authority_actions', [row], 100)
    guard(tmp_path, 'success', 'failure', '', read_baseline=lambda _: {})
    pending = tmp_path / PENDING
    assert json.loads(pending.read_text()) == [row]
    # Watch has its own concurrency lane and cannot clear trace's pending file.
    guard(tmp_path, 'success', 'success', '12', producer='circle')
    assert json.loads(pending.read_text()) == [row]
    guard(tmp_path, 'success', 'success', '13', producer='cctp')
    assert json.loads(pending.read_text()) == []
