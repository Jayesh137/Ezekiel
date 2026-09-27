"""Keep producer cursors replayable unless their observation upload succeeded.

Uses only the standard library so commit safety survives dependency failures.
"""

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path

CHECKPOINTS = {'data/circle_flows/latest.json': ('last_block',),
               'data/correlations/cctp_pool.json': ('cursor_ms', 'forwarder')}
PENDING = 'data/agent_links/pending_observations.json'


def baseline(root, relative):
    exists = subprocess.run(['git', 'ls-tree', '--name-only', 'HEAD', '--', relative],
                            cwd=root, capture_output=True, text=True, check=True)
    if not exists.stdout.strip():
        return {}
    result = subprocess.run(['git', 'show', f'HEAD:{relative}'], cwd=root,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.guard-tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def guard(root, snapshot, upload, artifact, *, read_baseline=None, db_path=None, producer=None):
    root = Path(root)
    acknowledged = snapshot == upload == 'success' and str(artifact).isdigit()
    pending_path = root / PENDING
    if acknowledged:
        if producer != 'circle' and pending_path.exists():
            write(pending_path, [])
        return {'acknowledged': True, 'replay': []}
    read_baseline = read_baseline or (lambda relative: baseline(root, relative))
    restored = []
    for relative, keys in CHECKPOINTS.items():
        if producer and (('circle_flows' in relative) != (producer == 'circle')):
            continue
        path = root / relative
        if not path.exists():
            continue
        current, old = json.loads(path.read_text(encoding='utf-8')), read_baseline(relative)
        for key in keys:
            if key in old:
                current[key] = old[key]
            else:
                current.pop(key, None)
        write(path, current)
        restored.append(relative)
    # Authority snapshots cannot be recovered after an agent is revoked. Keep
    # a small JSON outbox across failed uploads, then include it in the retry.
    db_path = Path(db_path or root / 'data/.local/discovery.sqlite3')
    if producer != 'circle' and db_path.exists():
        prior = json.loads(pending_path.read_text()) if pending_path.exists() else []
        rows = {r['event_id']: r for r in prior}
        with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_observations'").fetchone():
                for (raw,) in db.execute("SELECT data FROM discovery_observations WHERE source='authority_actions'"):
                    row = json.loads(raw)
                    rows[row['event_id']] = row
        if rows:
            write(pending_path, list(rows.values()))
    return {'acknowledged': False, 'replay': restored}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', default='')
    parser.add_argument('--upload', default='')
    parser.add_argument('--artifact', default='')
    parser.add_argument('--db', type=Path)
    parser.add_argument('--producer', choices=['circle', 'cctp'])
    args = parser.parse_args()
    print(json.dumps(guard(Path.cwd(), args.snapshot, args.upload, args.artifact,
                           db_path=args.db, producer=args.producer)))


if __name__ == '__main__':
    main()
