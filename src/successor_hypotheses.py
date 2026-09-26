"""Bounded, explainable research leads. No hypothesis asserts account ownership."""

import hashlib
import json

from src.comovement import score as timing_score
from src.episodes import (
    build_episodes,
    compare_episode_profiles,
    episode_profile,
    number,
    regime_profiles,
)

DAY = 86400_000
EXACT_ROUTES = {'observed_transfer', 'funding_instruction', 'protocol_route'}
GROUP_RELATIONS = {'subaccount', 'shared_authority', 'observed_funding_route'}


def _ref(row, prefix):
    return row.get('id') or row.get('event_id') or prefix + ':' + hashlib.sha256(
        json.dumps(row, sort_keys=True).encode()).hexdigest()[:24]


def _states(account, cutoff):
    return sorted([row for row in account.get('positions_history', [])
                   if isinstance(row, dict) and number(row.get('ts_ms')) is not None
                   and row['ts_ms'] <= cutoff and isinstance(row.get('positions'), dict)
                   and row.get('complete', True)], key=lambda row: row['ts_ms'])


def _changes(account, cutoff, opening):
    output = []
    states = _states(account, cutoff)
    if len(states) < 2:
        seen = set()
        for fill in account.get('fills', []):
            ts, before, size = number(fill.get('time')), number(fill.get('startPosition')), number(fill.get('sz'))
            if ts is None or ts > cutoff or before is None or size is None or size <= 0 or fill.get('side') not in ('A', 'B'):
                continue
            identity = f"fill:{account['wallet']}:{fill.get('coin')}:{ts}:{fill.get('tid', _ref(fill, 'fill'))}"
            if identity in seen:
                continue
            seen.add(identity)
            after = before + (size if fill['side'] == 'B' else -size)
            change = abs(after) - abs(before)
            if before * after < 0 or (opening and change <= 0) or (not opening and change >= 0):
                continue
            output.append({'coin': fill.get('coin'), 'direction': 1 if (after if opening else before) > 0 else -1,
                           'quantity': abs(change), 'ts_ms': ts, 'interval_start_ms': ts,
                           'parent_event_ids': [identity]})
        # A sliced exit/entry is one observed quantity change, not ten leads.
        grouped = []
        active = {}
        for row in sorted(output, key=lambda r: r['ts_ms']):
            key = (row['coin'], row['direction'])
            previous = active.get(key)
            if previous and row['ts_ms'] - previous['ts_ms'] <= 30 * 60_000:
                previous['quantity'] += row['quantity']
                previous['ts_ms'] = row['ts_ms']
                previous['parent_event_ids'].extend(row['parent_event_ids'])
            else:
                active[key] = row
                grouped.append(row)
        return grouped
    for previous, current in zip(states, states[1:], strict=False):
        if current['ts_ms'] <= previous['ts_ms'] or current['ts_ms'] - previous['ts_ms'] > 3 * DAY:
            continue
        for coin in set(previous['positions']) | set(current['positions']):
            before = number(previous['positions'].get(coin, 0))
            after = number(current['positions'].get(coin, 0))
            if before is None or after is None:
                continue
            # Sign reversals are ambiguous handoffs; only same-direction reductions/additions.
            if before * after < 0:
                continue
            change = abs(after) - abs(before)
            if (opening and change <= 0) or (not opening and change >= 0):
                continue
            output.append({'coin': coin, 'direction': 1 if (after if opening else before) > 0 else -1,
                           'quantity': abs(change), 'ts_ms': current['ts_ms'],
                           'interval_start_ms': previous['ts_ms'],
                           'parent_event_ids': [_ref(previous, account['wallet']), _ref(current, account['wallet'])]})
    return output


def _handoffs(target, candidate, context, cutoff):
    exits, entries = _changes(target, cutoff, False), _changes(candidate, cutoff, True)
    possibilities = []
    for i, exit_row in enumerate(exits):
        for j, entry in enumerate(entries):
            if (entry['coin'], entry['direction']) != (exit_row['coin'], exit_row['direction']):
                continue
            delay = entry['ts_ms'] - exit_row['ts_ms']
            ratio = entry['quantity'] / exit_row['quantity']
            if 0 <= delay <= 3 * DAY and .2 <= ratio <= 1.5:
                common = any(event.get('coin') in (None, entry['coin'])
                             and number(event.get('ts_ms')) is not None
                             and event['ts_ms'] <= cutoff
                             and abs(event['ts_ms'] - exit_row['ts_ms']) <= 2 * 3600_000
                             for event in context.get('market_events', []))
                possibilities.append((abs(1 - ratio), delay, i, j, {
                    'coin': entry['coin'], 'direction': entry['direction'],
                    'quantity_ratio': round(ratio, 4), 'delay_hours': round(delay / 3600_000, 4),
                    'common_market_event': common, 'timing_is_interval_censored': True,
                    'market_control_available': bool(context.get('market_events')),
                    'parent_event_ids': exit_row['parent_event_ids'] + entry['parent_event_ids']}))
    # No exit or opening can corroborate multiple handoffs for the same account.
    used_exits, used_entries, output = set(), set(), []
    for _, _, i, j, row in sorted(possibilities, key=lambda p: p[:4]):
        if i not in used_exits and j not in used_entries:
            used_exits.add(i)
            used_entries.add(j)
            output.append(row)
    return output


def _group_accounts(candidates, groups, cutoff):
    by_wallet = {row['wallet']: row for row in candidates}
    output = []
    for link in groups:
        members = sorted(set(link.get('wallets', [])))
        if (link.get('relationship') not in GROUP_RELATIONS or not link.get('parent_event_ids')
                or not 2 <= len(members) <= 4 or any(w not in by_wallet for w in members)
                or (number(link.get('observed_at_ms')) or 0) > cutoff):
            continue
        histories = {w: _states(by_wallet[w], cutoff) for w in members}
        states = []
        for ts in sorted({row['ts_ms'] for rows in histories.values() for row in rows}):
            selected = [next((r for r in reversed(histories[w]) if r['ts_ms'] <= ts), None) for w in members]
            if any(r is None or ts - r['ts_ms'] > 5 * 60_000 for r in selected):
                continue
            positions = {}
            for row in selected:
                for coin, qty in row['positions'].items():
                    if number(qty) is not None:
                        positions[coin] = positions.get(coin, 0) + float(qty)
            states.append({'ts_ms': ts, 'positions': positions,
                           'id': 'group-state:' + ':'.join(_ref(r, w) for w, r in zip(members, selected, strict=True))})
        output.append({'wallet': 'group:' + '+'.join(members), 'group_members': members,
                       'group_relationship': link['relationship'], 'parent_event_ids': link['parent_event_ids'],
                       'fills': [], 'positions_history': states})
    return output


def _cohort(target, candidate, context, cutoff):
    boundary = number(context.get('migration_ms'))
    if boundary is None or boundary > cutoff:
        return {'status': 'insufficient_data', 'reason': 'no dated migration boundary', 'promotable': False}
    before = [f for f in target.get('fills', []) if (number(f.get('time')) or 0) < boundary]
    successor = [f for f in candidate.get('fills', []) if boundary <= (number(f.get('time')) or 0) <= cutoff]
    followers = []
    for follower in context.get('copier_cohort', [])[:30]:
        fills = follower.get('fills', [])
        prior = timing_score(before, [f for f in fills if (number(f.get('time')) or 0) < boundary])
        if prior['verdict'] != 'copier' or prior.get('independent_sessions', 0) < 5:
            continue
        after = timing_score(successor, [f for f in fills if boundary <= (number(f.get('time')) or 0) <= cutoff])
        followers.append({'wallet': follower.get('wallet'), 'before': prior, 'after': after})
    enough = len(followers) >= 3 and sum(f['after']['verdict'] == 'copier' for f in followers) >= 3
    return {'status': 'research' if enough else 'insufficient_data', 'promotable': False,
            'demonstrated_followers': len(followers), 'followers': followers,
            'confounders': ['shared_signal_source', 'followers_may_follow_each_other']}


def find_successor_hypotheses(target: dict, candidates: list[dict], context: dict | None = None) -> list[dict]:
    context = context or {}
    cutoff = number(context.get('as_of_ms'))
    cutoff = cutoff if cutoff is not None else float('inf')
    cluster = set(context.get('cluster', [])) | {target['wallet']}
    services = set(context.get('services', []))
    target_fills = [f for f in target.get('fills', []) if number(f.get('time')) is not None and float(f['time']) <= cutoff]
    target_episodes = build_episodes(target_fills)
    target_profile = episode_profile(target_episodes)
    regimes = regime_profiles(target_episodes)
    results = []
    accounts = candidates + _group_accounts(candidates, context.get('linked_groups', []), cutoff)
    for candidate in accounts:
        wallet = candidate['wallet']
        fills = [f for f in candidate.get('fills', []) if number(f.get('time')) is not None and float(f['time']) <= cutoff]
        episodes = build_episodes([{**f, 'wallet': wallet} for f in fills])
        profile = episode_profile(episodes)
        comparison = compare_episode_profiles(target_profile, profile)
        early = episode_profile([e for e in episodes if e['start_ms'] <= episodes[0]['start_ms'] + 7 * DAY]) if episodes else profile
        regime_scores = [{'start_ms': r['start_ms'], 'comparison': compare_episode_profiles(r['profile'], early)} for r in regimes]
        members = set(candidate.get('group_members') or [wallet])
        routes = [r for r in context.get('routes', []) if r.get('assertion') in EXACT_ROUTES
                  and r.get('parent_event_ids') and (number(r.get('observed_at_ms', r.get('ts_ms'))) or 0) <= cutoff
                  and r.get('original_funder') not in services and r.get('recipient') not in services]
        returns = [r for r in routes if r.get('original_funder') in members and r.get('recipient') in cluster]
        funding = [r for r in routes if r.get('original_funder') in cluster and r.get('recipient') in members]
        authorities = [a for a in context.get('authority_links', [])
                       if set(a.get('accounts', [])) & members and set(a.get('accounts', [])) & cluster
                       and a.get('parent_event_ids') and (number(a.get('observed_at_ms')) or 0) <= cutoff]
        handoffs = _handoffs(target, candidate, context, cutoff)
        disclosures = [d for d in context.get('disclosures', []) if d.get('wallet') in members
                       and number(d.get('observed_at_ms')) is not None and d['observed_at_ms'] <= cutoff
                       and str(d.get('url', '')).startswith(('https://', 'http://'))]
        confounders = set(comparison['confounders'])
        if any(h['common_market_event'] for h in handoffs):
            confounders.add('common_market_event')
        if handoffs and not context.get('market_events'):
            confounders.add('market_control_missing')
        contradictions = []
        if comparison.get('similarity') is not None and comparison['similarity'] < .4:
            contradictions.append('observed_execution_style_differs; factual_routes_retained')
        if profile['coverage']['position_gaps']:
            contradictions.append('missing_position_history')
        ordered_times = sorted(float(f['time']) for f in fills)
        gaps = [{'previous_ms': a, 'resumed_ms': b, 'gap_days': round((b - a) / DAY, 2),
                 'inactivity_confirmed': False} for a, b in zip(ordered_times, ordered_times[1:], strict=False) if b - a >= 7 * DAY]
        own_regimes = regime_profiles(episodes)
        shifts = [{'start_ms': b['start_ms'], 'comparison': compare_episode_profiles(a['profile'], b['profile'])}
                  for a, b in zip(own_regimes, own_regimes[1:], strict=False)]
        sequences = []
        if ordered_times:
            for route in funding:
                ts = number(route.get('ts_ms'))
                if ts is not None and ts <= ordered_times[0] <= ts + 3 * DAY:
                    sequences.append({'kind': 'funding_then_first_observed_trade', 'route_id': route.get('id'),
                                      'first_observed_trade_ms': ordered_times[0], 'first_ever_trade_known': False,
                                      'parent_event_ids': route['parent_event_ids'] + profile['parent_event_ids'][:1]})
        parent_ids = set(candidate.get('parent_event_ids', []))
        for row in returns + funding + authorities + handoffs + sequences:
            parent_ids.update(row.get('parent_event_ids', []))
        parent_ids.update(profile['parent_event_ids'])
        next_measurement = ('inspect route counterparty and confirm destination credit' if funding or returns else
                            'collect candidate fills and dated position snapshots across independent sessions')
        # Evidence families, not repeated observations, drive the research queue.
        priority = 4 * bool(returns) + 3 * bool(funding) + 2 * bool(authorities) + bool(handoffs)
        priority += min(profile['session_count'], 10) / 10
        results.append({'wallet': wallet, 'group_members': candidate.get('group_members', []),
                        'identity_confirmed': False, 'promotable': False, 'research_only': True,
                        'priority': priority, 'score_kind': 'investigation_utility_not_probability',
                        'episode_similarity': comparison, 'early_observed_regimes': regime_scores,
                        'position_handoffs': handoffs, 'return_routes': returns, 'funding_routes': funding,
                        'authority_links': authorities, 'operational_sequences': sequences,
                        'reactivations': gaps, 'style_changes': shifts, 'disclosures': disclosures,
                        'copier_cohort': _cohort(target, candidate, context, cutoff),
                        'confounders': sorted(confounders), 'contradictions': contradictions,
                        'next_measurement': next_measurement, 'coverage': profile['coverage'],
                        'independent_sessions': profile['session_count'], 'parent_event_ids': sorted(parent_ids)})
    return sorted(results, key=lambda row: (-row['priority'], row['wallet']))
