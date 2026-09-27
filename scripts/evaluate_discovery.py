"""Offline discovery replay and operational quality reporting. Never sends alerts."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.candidate_registry import iter_candidates
from src.discovery_evaluation import evaluate_replay
from src.evaluation_cohort import cohort_wallets
from src.utils import DATA_DIR, atomic_write_json


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def quality_report(data_dir=DATA_DIR):
    data_dir = Path(data_dir)
    candidates = iter_candidates(data_dir)
    routes = read(data_dir / 'routes' / 'latest.json')
    discovery = read(data_dir / 'discovery' / 'latest.json')
    investigations = read(data_dir / 'investigations' / 'latest.json')
    evaluation = read(data_dir / 'discovery_evaluation' / 'latest.json')
    scan = read(data_dir / 'scans' / 'latest.json')
    unresolved = routes.get('unresolved', [])
    report = {'computed_at_ms': int(time.time() * 1000), 'promotion_validated': False,
              'candidate_count': len(candidates),
              'successful_reads': sum(bool(c.get('last_successful_read')) for c in candidates),
              'failed_latest_reads': sum(c.get('last_read_status') in ('error', 'partial') for c in candidates),
              'candidates_with_positive_observations': sum(bool(c.get('last_positive_evidence')) for c in candidates),
              'heldout_wallet_count': len(cohort_wallets(data_dir / '.local' / 'discovery.sqlite3')),
              'discovery_coverage': discovery.get('coverage', {}),
              'investigation_coverage': investigations.get('coverage', {}),
              'scanner_coverage': {**scan.get('collection', {}), 'scan_time': scan.get('scan_time')},
              'unresolved_route_count': routes.get('counts', {}).get('unresolved', len(unresolved)),
              'next_route_queries': [{'id': r.get('id'), 'next_query': r.get('next_query'),
                                      'reason': r.get('reason')} for r in unresolved[:20]],
              'evaluation_status': 'reported_research' if evaluation.get('scenarios') else 'not_evaluated',
              'real_identification_accuracy': None,
              'limitations': ['Public polling observes snapshots, not a complete trade stream.',
                             'A failed read does not mean an account is inactive.',
                             'Identification accuracy needs verified independent held-out cases.']}
    atomic_write_json(data_dir / 'quality' / 'latest.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, help='JSON array of dated observed events')
    parser.add_argument('--scenarios', type=Path, help='JSON array with explicit synthetic/verified provenance')
    parser.add_argument('--cutoff-ms', type=int)
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--quality-only', action='store_true')
    args = parser.parse_args()
    if args.quality_only:
        report = quality_report(args.data_dir)
    else:
        if not args.events or not args.scenarios:
            parser.error('--events and --scenarios are required for replay; use --quality-only for operational coverage')
        events, scenarios = read(args.events), read(args.scenarios)
        if not isinstance(events, list) or not isinstance(scenarios, list):
            parser.error('events and scenarios must be JSON arrays')
        report = evaluate_replay(events, scenarios, args.cutoff_ms)
        atomic_write_json(args.output or args.data_dir / 'discovery_evaluation' / 'latest.json', report)
    print(json.dumps({k: v for k, v in report.items() if k in ('events_replayed', 'future_events_excluded',
                                                            'evaluation_status', 'promotion_validated')}))


if __name__ == '__main__':
    main()
