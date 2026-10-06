"""Bounded SQLite checkpoints and idempotent observation-only shard imports."""

import gzip
import json
import math
import os
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from itertools import groupby, islice
from pathlib import Path

from src.candidate_registry import valid_wallet
from src.discovery_store import DiscoveryStore

MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 48 * 1024 * 1024
MAX_FILL_ROWS = 250_000
PENDING_FILL_ROWS = 100_000
# Retention caps ROWS; the artifact budget is BYTES. With every table at its
# cap the checkpoint compressed to ~48 MiB, and from 2026-10-03 each snapshot
# was refused: discovery froze on a three-day-old checkpoint while observation
# shards piled up toward their 7-day expiry. The owner's checkpoint is trimmed,
# oldest replayable bulk first, until it fits.
TRIM_FRACTION = 0.15
TRIM_ROUNDS = 8


class OverBudget(ValueError):
    """A compressed checkpoint larger than its artifact budget."""


def _copy_bounded(source, destination, limit):
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise ValueError('discovery artifact exceeds its declared size budget')
        destination.write(chunk)
    return total


def snapshot(db_path, output_dir):
    """SQLite backup includes committed WAL pages; never upload a live database."""
    db_path, output_dir = Path(db_path), Path(output_dir)
    if not db_path.exists():
        raise FileNotFoundError('no discovery observations to snapshot')
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='checkpoint-', dir=output_dir) as tmp:
        copy = Path(tmp) / 'snapshot.sqlite3'
        with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(copy)) as destination:
            source.backup(destination)
            destination.execute('VACUUM')
        if copy.stat().st_size > MAX_DATABASE_BYTES:
            raise ValueError('discovery database exceeds checkpoint budget; compact before publishing')
        compressed = Path(tmp) / 'snapshot.sqlite3.gz'
        with copy.open('rb') as source, gzip.open(compressed, 'wb') as destination:
            _copy_bounded(source, destination, MAX_DATABASE_BYTES)
        if compressed.stat().st_size > MAX_ARCHIVE_BYTES:
            raise OverBudget('compressed discovery checkpoint exceeds artifact budget')
        result = output_dir / 'snapshot.sqlite3.gz'
        os.replace(compressed, result)
    return result


def trim_oldest(db_path, fraction=TRIM_FRACTION) -> dict:
    """Remove the oldest `fraction` of replayable bulk, for bytes.

    Fills outside active backfills (a backfill's prefix is what lets it resume)
    and generic observations other than authority snapshots, which cannot be
    replayed after revocation. A wallet that loses fills loses its coverage
    claim, as in compact().
    """
    out = {'fills': 0, 'observations': 0}
    with DiscoveryStore(db_path) as store:
        tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'fills' in tables:
            # NOT EXISTS, never NOT IN: with no active backfill the list is
            # empty, and `wallet NOT IN (NULL)` is never true - nothing trimmed.
            spared = ('WHERE NOT EXISTS (SELECT 1 FROM fill_progress p WHERE p.wallet=fills.wallet)'
                      if 'fill_progress' in tables else '')
            spare = store.db.execute(f'SELECT count(*) FROM fills {spared}').fetchone()[0]
            if spare:
                before = dict(store.db.execute('SELECT wallet,count(*) FROM fills GROUP BY wallet'))
                out['fills'] = store.db.execute(
                    f'DELETE FROM fills WHERE rowid IN (SELECT rowid FROM fills {spared} '
                    'ORDER BY ts LIMIT ?)', (math.ceil(spare * fraction),)).rowcount
                after = dict(store.db.execute('SELECT wallet,count(*) FROM fills GROUP BY wallet'))
                changed = [(w,) for w, n in before.items() if after.get(w, 0) != n]
                for table in ('fill_coverage', 'fill_progress'):
                    if table in tables:
                        store.db.executemany(f'DELETE FROM {table} WHERE wallet=?', changed)
        spare = store.db.execute("SELECT count(*) FROM discovery_observations "
                                 "WHERE source!='authority_actions'").fetchone()[0]
        if spare:
            out['observations'] = store.db.execute(
                'DELETE FROM discovery_observations WHERE rowid IN (SELECT rowid FROM '
                "discovery_observations WHERE source!='authority_actions' "
                'ORDER BY observed_at_ms LIMIT ?)', (math.ceil(spare * fraction),)).rowcount
        store.db.commit()
    return out


def snapshot_within_budget(db_path, output_dir, *, rounds=TRIM_ROUNDS, fraction=TRIM_FRACTION,
                           now_ms=None):
    """The scanner's checkpoint, trimmed oldest-first until it fits its budget.

    For the owner's state checkpoint only: its facts are already imported. An
    observation shard's facts are unseen, so a shard over budget must still
    fail (docs/discovery-operations.md). What was trimmed is recorded in
    storage_retention.byte_trimmed, inside the checkpoint itself.
    """
    total = {'fills': 0, 'observations': 0, 'rounds': 0}
    while True:
        try:
            return snapshot(db_path, output_dir)
        except OverBudget:
            if total['rounds'] >= rounds:
                raise
            got = trim_oldest(db_path, fraction)
            if not got['fills'] and not got['observations']:
                raise                       # nothing replayable is left to trim
            total = {'fills': total['fills'] + got['fills'],
                     'observations': total['observations'] + got['observations'],
                     'rounds': total['rounds'] + 1}
            with DiscoveryStore(db_path) as store:
                retention = store.meta('storage_retention', {}) or {}
                retention['byte_trimmed'] = {**total, 'at_ms': int(
                    now_ms if now_ms is not None else time.time() * 1000)}
                store.set_meta('storage_retention', retention)


def restore_archive(archive, destination):
    """Validate before atomic replacement; never extract arbitrary ZIP paths."""
    archive, destination = Path(archive), Path(destination)
    if archive.stat().st_size > MAX_ARCHIVE_BYTES + 1024 * 1024:
        raise ValueError('artifact archive is too large')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='restore-', dir=destination.parent) as tmp:
        staged = Path(tmp) / 'restored.sqlite3'
        with zipfile.ZipFile(archive) as zipped:
            if zipped.namelist() != ['snapshot.sqlite3.gz']:
                raise ValueError('unexpected artifact contents')
            if zipped.getinfo('snapshot.sqlite3.gz').file_size > MAX_ARCHIVE_BYTES:
                raise ValueError('compressed checkpoint too large')
            with zipped.open('snapshot.sqlite3.gz') as packed, gzip.GzipFile(fileobj=packed) as source, staged.open('wb') as out:
                _copy_bounded(source, out, MAX_DATABASE_BYTES)
        with closing(sqlite3.connect(staged.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('checkpoint integrity check failed')
        wal = Path(str(destination) + '-wal')
        if wal.exists() and wal.stat().st_size:
            raise ValueError('cannot replace a database with an active WAL')
        os.replace(staged, destination)


def import_observations(shard_path, db_path, artifact_id):
    """Merge facts only. Scheduling, cohorts and producer cursors stay with owner."""
    shard_path, db_path = Path(shard_path), Path(db_path)
    if shard_path.resolve() == db_path.resolve():
        raise ValueError('a database cannot import itself')
    with DiscoveryStore(db_path) as store:
        imported = store.meta('imported_artifacts', [])
        if str(artifact_id) in imported:
            return {'duplicate': True, 'observations': 0, 'fills': 0, 'trades': 0}
        counts = {'duplicate': False, 'observations': 0, 'fills': 0, 'trades': 0}
        with closing(sqlite3.connect(shard_path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            source.execute('PRAGMA trusted_schema=OFF')
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'discovery_observations' in tables:
                rows = source.execute('SELECT source,observed_at_ms,data FROM discovery_observations ORDER BY source,observed_at_ms')
                for (kind, stamp), group in groupby(rows, key=lambda r: (r[0], r[1])):
                    if kind not in ('authority_actions', 'circle', 'cctp_deposits', 'cctp_sources'):
                        continue
                    while batch := list(islice(group, 1000)):
                        store.ingest_observations(kind, [json.loads(r[2]) for r in batch], stamp)
                        counts['observations'] += len(batch)
            if 'market_events' in tables:
                for stamp, raw in source.execute('SELECT ts,raw FROM market_events ORDER BY ts'):
                    result = store.ingest_trades([json.loads(raw)], stamp)
                    counts['trades'] += result['inserted']
            if 'fills' in tables:
                store.db.execute('CREATE TABLE IF NOT EXISTS fills (wallet TEXT, event_id TEXT PRIMARY KEY, ts INTEGER, raw TEXT)')
                store.db.execute('CREATE INDEX IF NOT EXISTS fills_wallet_time ON fills(wallet,ts)')
                for wallet, identity, stamp, raw in source.execute('SELECT wallet,event_id,ts,raw FROM fills'):
                    valid_wallet(wallet)
                    json.loads(raw)
                    store.db.execute('INSERT OR IGNORE INTO fills VALUES (?,?,?,?)', (wallet, identity, stamp, raw))
                    counts['fills'] += 1
                store.db.commit()
        # A partly completed import is harmless: all fact IDs are idempotent,
        # and no artifact is acknowledged until every supported table finished.
        store.set_meta('imported_artifacts', (imported + [str(artifact_id)])[-5000:])
        store.record_observation('artifact_import', int(time.time() * 1000), artifact_id=str(artifact_id), **counts)
        return counts


def compact(db_path, now_ms=None):
    """Global storage bounds, with explicit retention metadata and cache reset."""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    with DiscoveryStore(db_path) as store:
        store.prune(now_ms - 30 * 86400_000, max_events=100_000)
        tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        protected, protected_rows = [], 0
        if 'fills' in tables:
            before = dict(store.db.execute('SELECT wallet,count(*) FROM fills GROUP BY wallet'))
            store.db.execute('DELETE FROM fills WHERE ts<?', (now_ms - 90 * 86400_000,))
            aged = dict(store.db.execute('SELECT wallet,count(*) FROM fills GROUP BY wallet'))
            if 'fill_progress' in tables:
                attempts = {}
                for phase in ('priority', 'leaderboard'):
                    for wallet, stamp in store.meta('scan_attempts:' + phase, {}).items():
                        attempts[wallet] = max(attempts.get(wallet, 0), stamp)
                pending = [r[0] for r in store.db.execute('SELECT wallet FROM fill_progress')]
                # Retain whole prefixes, oldest waiting attempt first. Keeping
                # only the newest fill timestamps erased every active backfill
                # in production, forcing the same pages to be fetched forever.
                for wallet in sorted(pending, key=lambda w: (attempts.get(w, 0), w)):
                    size = aged.get(wallet, 0)
                    if size and size == before[wallet] and protected_rows + size <= min(PENDING_FILL_ROWS, MAX_FILL_ROWS):
                        protected.append(wallet)
                        protected_rows += size
            placeholders = ','.join('?' for _ in protected) or 'NULL'
            store.db.execute('DELETE FROM fills WHERE rowid NOT IN (SELECT rowid FROM fills '
                             f'ORDER BY CASE WHEN wallet IN ({placeholders}) THEN 1 ELSE 0 END DESC, '
                             'ts DESC LIMIT ?)', (*protected, MAX_FILL_ROWS))
            after = dict(store.db.execute('SELECT wallet,count(*) FROM fills GROUP BY wallet'))
            if 'fill_coverage' in tables:
                store.db.executemany('DELETE FROM fill_coverage WHERE wallet=?',
                                     [(w,) for w, count in before.items() if after.get(w, 0) != count])
            if 'fill_progress' in tables:
                store.db.executemany('DELETE FROM fill_progress WHERE wallet=?',
                                     [(w,) for w, count in before.items() if after.get(w, 0) != count])
        store.db.execute('DELETE FROM discovery_observations WHERE rowid NOT IN '
                         '(SELECT rowid FROM discovery_observations ORDER BY observed_at_ms DESC LIMIT 100000)')
        store.db.commit()
        store.set_meta('storage_retention', {'at_ms': now_ms, 'market_events': 100000, 'market_days': 30,
            'fill_rows': MAX_FILL_ROWS, 'fill_days': 90, 'generic_observations': 100000,
            'pending_fill_row_budget': PENDING_FILL_ROWS, 'protected_fill_wallets': len(protected),
            'protected_fill_rows': protected_rows,
            'authority_days': 730, 'complete_history': False})
