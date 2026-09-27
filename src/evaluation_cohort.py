"""Fixed, score-blind held-out membership with dated local profile observations."""

import hashlib
import json
import sqlite3
import time
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
        members = set(store.meta('evaluation_cohort', {}).get('wallets', []))
        for row in rows:
            if row.get('source') != 'leaderboard' or not isinstance(row.get('fingerprint'), dict):
                continue
            wallet = valid_wallet(row['wallet'])
            if members and wallet not in members:
                continue
            store.db.execute('INSERT OR IGNORE INTO evaluation_profiles VALUES (?,?,?,?)',
                             (wallet, observed_at_ms, SCORING_SCHEMA, json.dumps(row['fingerprint'])))
        # Bounded observations: retain 90 days and at most 10,000 profiles total.
        store.db.execute('DELETE FROM evaluation_profiles WHERE ts < ?', (observed_at_ms - 90 * 86400_000,))
        # Keep daily boundary observations rather than let high scan frequency
        # evict the entire prospective trial history from the global row cap.
        store.db.execute('DELETE FROM evaluation_profiles WHERE (wallet,schema,ts) NOT IN ('
                         'SELECT wallet,schema,MIN(ts) FROM evaluation_profiles GROUP BY wallet,schema,ts/86400000 '
                         'UNION SELECT wallet,schema,MAX(ts) FROM evaluation_profiles GROUP BY wallet,schema,ts/86400000)')
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


def refresh_cohort_orders(rows, db_path=None, *, fetch=None, limit=20, seconds=60,
                           now=None, clock=time.monotonic):
    """Date complete control observations; failures never replace good profiles.

    Fixed membership is selected before this call. Scheduling uses only prior
    attempts, not resemblance scores. Empty successful reads remain unsupported.
    """
    from src.fingerprint import compute_order_profile, normalise_orders
    now = now or (lambda: int(time.time() * 1000))
    if fetch is None:
        import requests

        from src.utils import load_config
        url = load_config()['hyperliquid_api']

        def fetch(wallet, timeout):
            response = requests.post(url, json={'type': 'historicalOrders', 'user': wallet}, timeout=timeout)
            response.raise_for_status()
            return response.json()

    path = _path(db_path)
    with DiscoveryStore(path) as store:
        members = set(store.meta('evaluation_cohort', {}).get('wallets', []))
        attempts = store.meta('cohort_order_attempts', {})
    available = {row['wallet']: row for row in rows if row.get('wallet') in members}
    scheduled = sorted(available, key=lambda wallet: (attempts.get(wallet, 0), wallet))[:max(0, limit)]
    deadline = clock() + seconds
    report = {'attempted': 0, 'updated': 0, 'failed': 0, 'members': len(members),
              'profiles_available': len(available), 'errors': []}
    for wallet in scheduled:
        remaining = deadline - clock()
        if remaining <= 0:
            break
        report['attempted'] += 1
        try:
            raw = fetch(wallet, max(.1, min(10, remaining)))
            if not isinstance(raw, list) or any(not isinstance(r, dict) or not isinstance(r.get('order'), dict) for r in raw):
                raise ValueError('invalid historicalOrders response')
            observed = now()
            profile = {**available[wallet]['fingerprint'],
                       'order_profile': compute_order_profile(normalise_orders(raw, end_ms=observed)),
                       'order_observation': {'status': 'ok', 'observed_at_ms': observed, 'raw_rows': len(raw)}}
            record_profiles([{'wallet': wallet, 'source': 'leaderboard', 'fingerprint': profile}], observed, path)
            report['updated'] += 1
        except Exception as exc:
            report['failed'] += 1
            report['errors'].append({'wallet': wallet, 'error': type(exc).__name__})
        attempts[wallet] = now()
    with DiscoveryStore(path) as store:
        store.set_meta('cohort_order_attempts', {w: attempts[w] for w in members if w in attempts})
        store.set_meta('cohort_order_coverage', {**report, 'observed_at_ms': now()})
    return report


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
