"""Build a bounded investigation queue from local observations; no network or alerts."""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.candidate_registry import iter_candidates
from src.successor_hypotheses import find_successor_hypotheses
from src.utils import DATA_DIR, atomic_write_json, load_all_records, load_config


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def cached_fills(path, wallets, cutoff):
    results = {w: [] for w in wallets}
    if not Path(path).exists():
        return results, 'missing'
    try:
        with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
            for wallet in wallets:
                rows = db.execute('SELECT raw FROM fills WHERE wallet=? AND ts<=? ORDER BY ts DESC LIMIT 10000',
                                  (wallet, cutoff))
                results[wallet] = [json.loads(row[0]) for row in rows]
        return results, 'ok'
    except (sqlite3.Error, ValueError):
        return results, 'unreadable_or_no_fill_table'


def _bounded(row):
    output = {**row}
    for key in ('parent_event_ids', 'position_handoffs', 'return_routes', 'funding_routes', 'authority_links',
                'operational_sequences', 'reactivations', 'early_observed_regimes', 'style_changes'):
        values = output.get(key, [])
        output[key + '_count'] = len(values)
        output[key] = values[:256 if key == 'parent_event_ids' else 20]
    output['episode_similarity'] = {**row['episode_similarity'],
                                     'parent_event_ids': row['episode_similarity'].get('parent_event_ids', [])[:256]}
    for key in ('early_observed_regimes', 'style_changes'):
        output[key] = [{**r, 'comparison': {**r['comparison'], 'parent_event_ids': r['comparison'].get('parent_event_ids', [])[:32]}}
                       for r in output[key]]
    return output


def run(config, data_dir=DATA_DIR, *, as_of_ms=None, input_data=None, disclosures=None, limit=100, db_path=None):
    data_dir = Path(data_dir)
    cutoff = int(as_of_ms if as_of_ms is not None else time.time() * 1000)
    if input_data is not None:
        target, candidates = input_data['target'], input_data['candidates']
        context, cache_status = input_data.get('context', {}), 'supplied_fixture'
        candidate_total = len(candidates)
        candidates = candidates[:max(1, min(limit, 200))]
    else:
        registry = iter_candidates(data_dir)
        candidate_total = len(registry)
        # Prioritise independent factual sources, then oldest investigation.
        registry.sort(key=lambda r: (-bool(set(r.get('discovery_sources', [])) & {'funding_route', 'authority_history'}),
                                     r.get('last_investigated_ms', 0), r['wallet']))
        registry = registry[:max(1, min(limit, 200))]
        histories, cache_status = cached_fills(db_path or data_dir / '.local' / 'discovery.sqlite3',
                                               [r['wallet'] for r in registry], cutoff)
        target = {'wallet': config['target_wallet'].lower(),
                  'fills': load_all_records(str(data_dir / 'fills'))}
        candidates = [{'wallet': r['wallet'], 'fills': histories[r['wallet']]} for r in registry]
        route_report = read(data_dir / 'routes' / 'latest.json', {})
        authority = route_report.get('authority', {})
        context = {'routes': route_report.get('routes', []),
                   'authority_links': authority.get('shared_authority', []),
                   'linked_groups': [{'wallets': r['accounts'], 'relationship': 'shared_authority',
                                      'parent_event_ids': r['parent_event_ids'], 'observed_at_ms': r['overlap_start_ms']}
                                     for r in authority.get('shared_authority', [])],
                   'cluster': config.get('known_self_wallets', [])}
    context = {**context, 'as_of_ms': cutoff, 'disclosures': disclosures or context.get('disclosures', [])}
    rows = find_successor_hypotheses(target, candidates, context)
    report = {'computed_at_ms': cutoff, 'research_only': True, 'identity_confirmed': False,
              'investigations': [_bounded(row) for row in rows[:200]],
              'coverage': {'candidate_total': candidate_total, 'candidates_examined': len(candidates),
                           'candidates_with_fills': sum(bool(row.get('fills')) for row in candidates),
                           'cached_fills_status': cache_status, 'max_cached_fills_per_candidate': 10000,
                           'historical_coverage_complete': False,
                           'market_control_available': bool(context.get('market_events'))}}
    atomic_write_json(data_dir / 'investigations' / 'latest.json', report)
    # A cursor only for queue fairness; it is not a successful chain observation.
    if input_data is None:
        for row in registry:
            path = data_dir / 'candidates' / f"{row['wallet']}.json"
            atomic_write_json(path, {**row, 'last_investigated_ms': cutoff})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--input', type=Path, help='offline JSON object: target, candidates, context')
    parser.add_argument('--disclosures', type=Path, help='manual dated public claims; never identity ground truth')
    parser.add_argument('--as-of-ms', type=int)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    report = run(load_config(), args.data_dir, db_path=args.db, as_of_ms=args.as_of_ms, limit=args.limit,
                 input_data=read(args.input, {}) if args.input else None,
                 disclosures=read(args.disclosures, []) if args.disclosures else None)
    print(json.dumps(report['coverage']))


if __name__ == '__main__':
    main()
