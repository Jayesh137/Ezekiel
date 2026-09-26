"""Chronological discovery replay over actual ingestion, selection and ranking."""

import tempfile
from pathlib import Path

from src.candidate_registry import iter_candidates, observe_candidate
from src.discovery_store import DiscoveryStore
from src.scanner import select_discovery_targets
from src.successor_hypotheses import find_successor_hypotheses
from src.thresholds import SCORING_SCHEMA
from src.utils import DATA_DIR


def evaluate_replay(events: list[dict], scenarios: list[dict], cutoff_ms: int | None = None) -> dict:
    cutoff = cutoff_ms if cutoff_ms is not None else max((e.get('observed_at_ms', 0) for e in events), default=0)
    available = sorted([e for e in events if isinstance(e.get('observed_at_ms'), (int, float))
                        and e['observed_at_ms'] <= cutoff], key=lambda e: e['observed_at_ms'])
    parent = DATA_DIR / '.local' / 'replays'
    parent.mkdir(parents=True, exist_ok=True)
    output = []
    for scenario in scenarios:
        with tempfile.TemporaryDirectory(prefix='evaluation-', dir=parent) as directory:
            root = Path(directory)
            config = {'target_wallet': scenario['target_wallet'], 'known_self_wallets': [],
                      'discovery': {'scan_budget': scenario.get('scan_budget', 20)}}
            expected = set(scenario.get('expected_wallets', []))
            stages = {name: set() for name in ('observed', 'selected', 'enriched', 'ranked')}
            fills, routes, selected, first_ranked, first_observed = {}, [], set(), {}, {}
            top5, top20, errors = set(), set(), []
            with DiscoveryStore(root / 'replay.sqlite3') as store:
                for event in available:
                    now = event['observed_at_ms']
                    if now < scenario.get('start_ms', 0):
                        continue
                    kind, data = event.get('kind'), event.get('data')
                    if kind == 'trade':
                        result = store.ingest_trades([data], now)
                        if result['rejected']:
                            errors.append({'at_ms': now, 'reason': 'invalid_public_trade'})
                        else:
                            for wallet in data['users']:
                                stages['observed'].add(wallet)
                                first_observed.setdefault(wallet, now)
                    elif kind == 'deposit':
                        store.ingest_observations('cctp_deposits', [data], now)
                    elif kind == 'route' and data.get('parent_event_ids') and data.get('assertion') in (
                            'observed_transfer', 'funding_instruction', 'protocol_route'):
                        route = {**data, 'observed_at_ms': now}
                        routes.append(route)
                        wallet = data.get('recipient')
                        if wallet and wallet != config['target_wallet']:
                            observe_candidate(wallet, {'source': 'funding_route', 'positive': True,
                                'parent_event_ids': data['parent_event_ids'], 'event_id': data.get('id'),
                                'observed_at': str(now)}, root)
                            stages['observed'].add(wallet)
                            first_observed.setdefault(wallet, now)
                            selected.add(wallet)
                            stages['enriched'].add(wallet)
                    elif kind == 'fills':
                        wallet = event.get('wallet')
                        fills[wallet] = [f for f in (data or []) if isinstance(f.get('time'), (int, float)) and f['time'] <= now]
                    elif kind == 'coverage_gap':
                        errors.append({'at_ms': now, 'reason': event.get('reason', 'retention_or_read_gap')})
                    for wallet, meta in select_discovery_targets(store, config, now).items():
                        stages['observed'].add(wallet)
                        first_observed.setdefault(wallet, now)
                        selected.add(wallet)
                        observe_candidate(wallet, {'source': meta['source'], 'positive': True, 'observed_at': str(now)}, root)
                        store.mark_checked(wallet, now, 'ok' if fills.get(wallet) else 'error')
                    stages['selected'] |= selected
                    stages['enriched'] |= {w for w in selected if fills.get(w)}
                    candidates = [{'wallet': row['wallet'], 'fills': fills.get(row['wallet'], [])} for row in iter_candidates(root)]
                    target = {'wallet': config['target_wallet'], 'fills': fills.get(config['target_wallet'], [])}
                    ranked = find_successor_hypotheses(target, candidates, {'routes': routes, 'as_of_ms': now})
                    # A sparse, un-enriched record is observable but not a tested
                    # retrieval. Record it at its earlier failed pipeline stage.
                    ranked_wallets = [r['wallet'] for r in ranked if r['wallet'] in stages['enriched']]
                    stages['ranked'].update(ranked_wallets[:20])
                    top5.update(ranked_wallets[:5])
                    top20.update(ranked_wallets[:20])
                    for wallet in ranked_wallets[:20]:
                        first_ranked.setdefault(wallet, now)
            misses = {}
            for wallet in expected:
                for stage, label in [('observed', 'observation'), ('selected', 'selection'), ('enriched', 'enrichment'), ('ranked', 'ranking')]:
                    if wallet not in stages[stage]:
                        misses[wallet] = label
                        break
            denominator = len(expected)
            output.append({'id': scenario['id'], 'kind': scenario.get('kind', 'unverified'),
                           'ground_truth_eligible': scenario.get('kind') == 'verified_relationship'
                               and bool(scenario.get('verification_refs')),
                           'stages': {k: sorted(v & expected) for k, v in stages.items()},
                           'stage_recall': {k: len(v & expected) / denominator if denominator else None for k, v in stages.items()},
                           'recall_at_5': len(top5 & expected) / denominator if denominator else None,
                           'recall_at_20': len(top20 & expected) / denominator if denominator else None,
                           'first_investigation_latency_ms': {w: first_ranked[w] - scenario.get('start_ms', first_observed[w])
                                                              for w in expected if w in first_ranked},
                           'misses': misses, 'false_identity_alerts': 0, 'coverage_gaps': errors,
                           'metric_kind': 'ever_retrieved_by_cutoff', 'candidate_count': len(stages['observed'])})
    return {'scoring_schema': SCORING_SCHEMA, 'cutoff_ms': cutoff, 'scenarios': output,
            'events_replayed': len(available), 'future_events_excluded': len(events) - len(available),
            'production_alerts_sent': 0, 'promotion_validated': False,
            'caveats': ['Synthetic scenarios test plumbing, not real-world identification accuracy.',
                        'Verified protocol relationships do not prove beneficial ownership.',
                        'False identity alert count is structural: experimental promotion is disabled.',
                        'Recall is conditional on supplied observations; upstream missed trades cannot be recovered.']}
