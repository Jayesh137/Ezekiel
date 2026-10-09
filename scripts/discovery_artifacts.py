"""Branch-scoped discovery artifact handoff. Scan is the sole checkpoint writer."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.discovery_state import (
    MAX_ARCHIVE_BYTES,
    compact,
    import_observations,
    restore_archive,
    snapshot,
    snapshot_within_budget,
)
from src.discovery_store import DiscoveryStore
from src.utils import DATA_DIR, atomic_write_json


def prefix(kind, branch):
    digest = hashlib.sha256(branch.encode()).hexdigest()[:12]
    return f'discovery-{kind}-v1-{digest}-'


class GitHubArtifacts:
    def __init__(self):
        repository = os.environ.get('GITHUB_REPOSITORY', '')
        token = os.environ.get('GITHUB_TOKEN', '')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository) or not token:
            raise ValueError('artifact handoff requires the workflow repository and its GITHUB_TOKEN')
        self.base = f'https://api.github.com/repos/{repository}/actions/artifacts'
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                                     'X-GitHub-Api-Version': '2026-03-10'})
        self.truncated = False

    def artifacts(self):
        result = []
        for page in range(1, 11):
            response = self.session.get(self.base, params={'per_page': 100, 'page': page}, timeout=(10, 30))
            if response.status_code != 200:
                raise RuntimeError(f'artifact listing HTTP {response.status_code}')
            rows = response.json().get('artifacts')
            if not isinstance(rows, list):
                raise ValueError('invalid artifact listing')
            result.extend(rows)
            if len(rows) < 100:
                return result
        self.truncated = True
        return result

    def download(self, artifact, destination):
        if artifact.get('size_in_bytes', 0) > MAX_ARCHIVE_BYTES + 1024 * 1024:
            raise ValueError('artifact exceeds download budget')
        artifact_id = int(artifact['id'])
        # requests removes Authorization on cross-host redirects to blob storage.
        with self.session.get(f'{self.base}/{artifact_id}/zip', stream=True, timeout=(10, 30)) as response:
            if response.status_code != 200:
                raise RuntimeError(f'artifact download HTTP {response.status_code}')
            size = 0
            with Path(destination).open('wb') as out:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES + 1024 * 1024:
                        raise ValueError('artifact exceeds streaming download budget')
                    out.write(chunk)

    def delete(self, artifact):
        response = self.session.delete(f"{self.base}/{int(artifact['id'])}", timeout=(10, 30))
        if response.status_code not in (204, 404):
            raise RuntimeError(f'artifact cleanup HTTP {response.status_code}')


def restore_state(client, db_path, state_path, branch, *, read_only=False, import_limit=40):
    db_path, state_path = Path(db_path), Path(state_path)
    now = int(time.time() * 1000)
    report = {'computed_at_ms': now, 'ready': False, 'status': 'restore_error', 'branch': branch,
              'writer': 'scan', 'imported': [], 'errors': [], 'continuous': False}
    try:
        artifacts = client.artifacts()
        checkpoints = sorted([a for a in artifacts if a['name'].startswith(prefix('state', branch))
                              and not a.get('expired')], key=lambda a: a['id'], reverse=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='handoff-', dir=db_path.parent) as tmp:
            archive = Path(tmp) / 'artifact.zip'
            if checkpoints:
                checkpoint = checkpoints[0]
                client.download(checkpoint, archive)
                restore_archive(archive, db_path)
                report.update(status='restored', restored_from=checkpoint['id'], checkpoint_created_at=checkpoint.get('created_at'))
            elif client.truncated:
                raise ValueError('artifact listing truncated; cannot safely conclude that no checkpoint exists')
            else:
                prior = {}
                if state_path.exists():
                    try:
                        prior = json.loads(state_path.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        pass
                report['status'] = 'state_lost' if prior.get('restored_from') or prior.get('checkpoint_prepared_at_ms') else 'cold_start'
                report['coverage_gap'] = 'No retained checkpoint; earlier raw observations are unavailable.'
                with DiscoveryStore(db_path):
                    pass
            if not read_only:
                with DiscoveryStore(db_path) as store:
                    imported = set(store.meta('imported_artifacts', []))
                shards = sorted([a for a in artifacts if a['name'].startswith(prefix('observations', branch))
                                 and not a.get('expired') and str(a['id']) not in imported], key=lambda a: a['id'])
                for artifact in shards[:max(0, min(import_limit, 100))]:
                    try:
                        client.download(artifact, archive)
                        shard = Path(tmp) / 'observations.sqlite3'
                        restore_archive(archive, shard)
                        counts = import_observations(shard, db_path, str(artifact['id']))
                        report['imported'].append({'artifact_id': artifact['id'], **counts})
                    except Exception as exc:  # noqa: BLE001 - isolate bad shards; preserve the valid checkpoint
                        report['errors'].append({'artifact_id': artifact['id'], 'error': type(exc).__name__})
                report['pending_shards'] = len(shards) - len(report['imported'])
                report['expired_shards'] = sum(a.get('expired', False) and str(a['id']) not in imported
                    for a in artifacts if a['name'].startswith(prefix('observations', branch)))
            report.update(ready=True, listing_truncated=client.truncated, read_only=read_only)
    except Exception as exc:  # noqa: BLE001 - emit failure without token-bearing transport details
        report.update(status='restore_error', ready=False)
        report['errors'].append({'error': type(exc).__name__})
    atomic_write_json(state_path, report)
    return report


# GitHub's artifact listing can trail an upload made seconds before it: three scan runs
# failed on that, and each paged HIGH, before the next run recovered (2026-10-08/09).
ACK_ATTEMPTS = 4
ACK_DELAY_SECONDS = 10


def cleanup(client, db_path, branch, uploaded_id, *, attempts=ACK_ATTEMPTS,
            delay=ACK_DELAY_SECONDS, sleep=time.sleep):
    for attempt in range(attempts):
        artifacts = client.artifacts()
        checkpoints = sorted([a for a in artifacts if a['name'].startswith(prefix('state', branch))
                              and not a.get('expired')], key=lambda a: a['id'], reverse=True)
        if any(a['id'] == int(uploaded_id) for a in checkpoints):
            break
        if attempt + 1 < attempts:
            sleep(delay)
    else:
        raise ValueError('new checkpoint not acknowledged; retaining every prior artifact')
    with DiscoveryStore(db_path) as store:
        imported = set(store.meta('imported_artifacts', []))
    removable = checkpoints[2:] + [a for a in artifacts
        if a['name'].startswith(prefix('observations', branch)) and str(a['id']) in imported]
    for artifact in removable[:200]:
        client.delete(artifact)
    return {'deleted': min(200, len(removable)), 'retained_checkpoints': min(2, len(checkpoints))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['restore', 'snapshot', 'cleanup'])
    parser.add_argument('--db', type=Path, default=DATA_DIR / '.local' / 'discovery.sqlite3')
    parser.add_argument('--state', type=Path, default=DATA_DIR / 'discovery' / 'state.json')
    parser.add_argument('--output-dir', type=Path, default=DATA_DIR / '.local' / 'artifact')
    parser.add_argument('--branch', default=os.environ.get('GITHUB_REF_NAME', 'local'))
    parser.add_argument('--kind', choices=['state', 'observations'], default='state')
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--artifact-id', type=int)
    args = parser.parse_args()
    if args.action == 'restore':
        report = restore_state(GitHubArtifacts(), args.db, args.state, args.branch, read_only=args.read_only)
        print(json.dumps(report))
        return 0 if report['ready'] else 1
    if args.action == 'cleanup':
        if args.artifact_id is None:
            parser.error('--artifact-id is required for cleanup')
        print(json.dumps(cleanup(GitHubArtifacts(), args.db, args.branch, args.artifact_id)))
        return 0
    pending = DATA_DIR / 'agent_links' / 'pending_observations.json'
    if args.kind == 'observations' and pending.exists():
        with DiscoveryStore(args.db) as store:
            store.ingest_observations('authority_actions', json.loads(pending.read_text()), int(time.time() * 1000))
    if not args.db.exists():
        print('No observation store was created; no artifact to publish.')
        return 0
    compact(args.db)
    # The owner's checkpoint is trimmed to fit its byte budget; a shard's facts
    # are unseen and are never trimmed - an oversized shard still fails.
    path = (snapshot_within_budget(args.db, args.output_dir) if args.kind == 'state'
            else snapshot(args.db, args.output_dir))
    name = prefix(args.kind, args.branch) + os.environ.get('GITHUB_RUN_ID', 'local') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(f'artifact_name={name}\nartifact_path={path}\n')
    if args.kind == 'state':
        report = json.loads(args.state.read_text()) if args.state.exists() else {}
        report['checkpoint_prepared_at_ms'] = int(time.time() * 1000)
        report['checkpoint_bytes'] = path.stat().st_size
        with DiscoveryStore(args.db) as store:
            report['byte_trimmed'] = (store.meta('storage_retention', {}) or {}).get('byte_trimmed')
        atomic_write_json(args.state, report)
    print(json.dumps({'artifact_name': name, 'bytes': path.stat().st_size}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
