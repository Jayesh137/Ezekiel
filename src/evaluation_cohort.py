"""Fixed, score-blind held-out membership with dated local profile observations."""

import hashlib
import json
import sqlite3
from pathlib import Path

from src.candidate_registry import valid_wallet
from src.discovery_store import DiscoveryStore
from src.thresholds import SCORING_SCHEMA
from src.utils import DATA_DIR


def _path(path):
    return Path(path or DATA_DIR / '.local' / 'discovery.sqlite3')


def record_profiles(rows, observed_at_ms, db_path=None):
    with DiscoveryStore(_path(db_path)) as store:
        store.db.execute('CREATE TABLE IF NOT EXISTS evaluation_profiles '
                         '(wallet TEXT, ts INTEGER, schema TEXT, profile TEXT, PRIMARY KEY(wallet,ts))')
        for row in rows:
            if row.get('source') != 'leaderboard' or not isinstance(row.get('fingerprint'), dict):
                continue
            wallet = valid_wallet(row['wallet'])
            store.db.execute('INSERT OR IGNORE INTO evaluation_profiles VALUES (?,?,?,?)',
                             (wallet, observed_at_ms, SCORING_SCHEMA, json.dumps(row['fingerprint'])))
        # Bounded observations: retain 90 days and at most 10,000 profiles total.
        store.db.execute('DELETE FROM evaluation_profiles WHERE ts < ?', (observed_at_ms - 90 * 86400_000,))
        store.db.execute('DELETE FROM evaluation_profiles WHERE rowid NOT IN '
                         '(SELECT rowid FROM evaluation_profiles ORDER BY ts DESC LIMIT 10000)')
        store.db.commit()


def freeze_cohort(db_path=None, observed_at_ms=0, minimum=20, maximum=50, excluded=()):
    with DiscoveryStore(_path(db_path)) as store:
        previous = store.meta('evaluation_cohort')
        if previous:
            return previous
        try:
            wallets = [row[0] for row in store.db.execute('SELECT DISTINCT wallet FROM evaluation_profiles WHERE ts<=?', (observed_at_ms,))
                       if row[0] not in excluded]
        except sqlite3.OperationalError:
            wallets = []
        if len(wallets) < minimum:
            return {'wallets': [], 'eligible': False, 'reason': 'insufficient independent observed wallets'}
        wallets.sort(key=lambda w: hashlib.sha256(('discovery-heldout-v1:' + w).encode()).hexdigest())
        cohort = {'wallets': wallets[:maximum], 'frozen_at_ms': observed_at_ms, 'selection': 'fixed_address_hash_v1',
                  'selection_uses_score': False, 'schema_at_freeze': SCORING_SCHEMA,
                  'identity_labels': 'unverified_controls', 'eligible': True}
        store.set_meta('evaluation_cohort', cohort)
        return cohort


def cohort_wallets(db_path=None):
    path = _path(db_path)
    if not path.exists():
        return []
    with DiscoveryStore(path) as store:
        return store.meta('evaluation_cohort', {}).get('wallets', [])


def load_cohort_asof(db_path, cutoff_ms, *, trial_start_ms):
    path = _path(db_path)
    if not path.exists():
        return [], {'eligible': False, 'reason': 'no held-out observation store'}
    with DiscoveryStore(path) as store:
        cohort = store.meta('evaluation_cohort', {})
        if not cohort.get('wallets') or cohort.get('frozen_at_ms', cutoff_ms + 1) > trial_start_ms:
            return [], {**cohort, 'eligible': False, 'reason': 'cohort was not fixed before trial window'}
        rows = []
        for wallet in cohort['wallets']:
            row = store.db.execute('SELECT ts,profile FROM evaluation_profiles WHERE wallet=? AND ts<=? '
                                   'AND ts>=? AND schema=? ORDER BY ts DESC LIMIT 1',
                                   (wallet, cutoff_ms, cutoff_ms - 21 * 86400_000, SCORING_SCHEMA)).fetchone()
            if row:
                rows.append({'wallet': wallet, 'as_of_ms': row[0], 'fingerprint': json.loads(row[1])})
        return rows, {**cohort, 'eligible': True, 'profiles_available': len(rows),
                      'profile_cutoff_ms': cutoff_ms, 'max_profile_age_days': 21}
