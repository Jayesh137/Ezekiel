from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return (ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')


def test_scan_is_checkpoint_owner_and_discovery_precedes_enrichment():
    text = workflow('scan.yml')
    assert text.index('discovery_artifacts.py restore') < text.index('collect_market_discovery.py')
    assert text.index('index_discovery_routes.py') < text.index('python src/scanner.py')
    assert text.index('python src/scanner.py') < text.index('check_successor_hypotheses.py')
    assert "steps.discovery_state.conclusion == 'success'" in text
    assert text.index('actions/upload-artifact@v5') < text.index('discovery_artifacts.py cleanup')
    assert 'data/discovery/' in text and 'data/movements/' in text


def test_observer_jobs_publish_shards_and_never_restore_or_overwrite_checkpoint():
    for name in ('watch.yml', 'trace.yml'):
        text = workflow(name)
        assert 'discovery_artifacts.py snapshot --kind observations' in text
        assert 'discovery_artifacts.py restore' not in text
        assert 'discovery_artifacts.py cleanup' not in text
        assert 'collect_market_discovery.py' not in text
    assert 'group: watch-data' in workflow('watch.yml')


def test_historical_validation_restores_cohort_read_only():
    text = workflow('analyze.yml')
    assert 'discovery_artifacts.py restore --read-only' in text
    assert 'snapshot --kind state' not in text
    assert 'snapshot --kind observations --db data/.local/analysis-observations.sqlite3' in text
    assert 'DISCOVERY_OBSERVATION_DB: data/.local/analysis-observations.sqlite3' in text


def test_observer_publication_guard_is_inside_commit_step_before_staging():
    for name in ('watch.yml', 'trace.yml', 'analyze.yml'):
        text = workflow(name)
        commit = text.split('- name: Commit and push')[1]
        assert commit.index('discovery_cursor_guard.py') < commit.index('git add')
        assert "steps.observation_upload.conclusion" in commit
        assert "steps.observation_upload.outputs.artifact-id" in commit
