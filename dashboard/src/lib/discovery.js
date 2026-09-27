// Optional, bounded reports. A missing observation must never become a zero.
const count = value => Number.isFinite(value) ? value : null;
const age = (stamp, now) => Number.isFinite(stamp) ? Math.max(0, (now - stamp) / 3600_000) : null;
const array = value => Array.isArray(value) ? value : [];
const counted = (n, label) => `${n} ${label}${n === 1 ? '' : 's'}`;

export function rosterTierLabel(row) {
  if (row?.known_self) return 'Trusted seed';
  return {CONFIRMED: 'Legacy inference', PROBABLE: 'Priority lead', POSSIBLE: 'Possible lead',
    WATCH: 'Watch', INFRASTRUCTURE: 'Service'}[row?.tier] || row?.tier || 'Unrated';
}

export function discoveryView(reports = {}, now = Date.now()) {
  const {discovery, state, investigations, quality, routes, accounting} = reports;
  const lastSuccessAgeHours = age(discovery?.last_successful_read_ms, now);
  const failed = discovery?.last_run?.status === 'error';
  const collection = !discovery ? {level: 'unknown', label: 'Not collected'}
    : failed ? {level: 'warn', label: 'Latest collection failed'}
    : lastSuccessAgeHours === null ? {level: 'unknown', label: 'Successful read time unknown'}
    : lastSuccessAgeHours > 12 ? {level: 'warn', label: 'Collection overdue'}
    : {level: 'ok', label: discovery.last_run?.status === 'partial' ? 'Partial snapshot coverage' : 'Snapshot coverage'};
  const warnings = [];
  const scanner = quality?.scanner_coverage;
  if (scanner?.status === 'partial') {
    const deferred = Object.values(scanner.phases || {}).reduce((sum, phase) => sum + (count(phase?.deferred) || 0), 0);
    const reason = scanner.stopped_reason === 'rate_limited' ? 'the public data rate limit' : 'the bounded collection window or incomplete reads';
    warnings.push(`Wallet enrichment is partial due to ${reason}. ${deferred} checks are deferred; saved progress resumes on a later scan.`);
  }
  if (state?.status === 'restore_error') warnings.push('History restore failed. Discovery enrichment is paused until it can be recovered.');
  if (['state_lost', 'cold_start'].includes(state?.status)) warnings.push('No previous checkpoint was available. Earlier raw observations may be missing.');
  if (state?.pending_shards) warnings.push(state.pending_shards === 1
    ? '1 observation batch still awaits import.' : `${state.pending_shards} observation batches still await import.`);
  if (state?.expired_shards) warnings.push(`${state.expired_shards} observation batches expired before import; history has gaps.`);
  if (state?.listing_truncated) warnings.push('Only part of the stored observation list was checked. More batches may be pending.');
  if (state?.errors?.length) warnings.push(`${state.errors.length} history operations failed; inspect the persistence report.`);
  if (age(investigations?.computed_at_ms, now) > 12) warnings.push('The investigation queue is more than 12 hours old.');
  const leads = array(investigations?.investigations).map(row => {
    const reasons = [];
    for (const [key, label] of [['return_routes', 'return route'], ['funding_routes', 'funding route'],
      ['authority_links', 'authority link'], ['position_handoffs', 'position handoff']]) {
      if (array(row[key]).length) reasons.push(counted(row[key].length, label));
    }
    if (row.independent_sessions) reasons.push(counted(row.independent_sessions, 'observed session'));
    return {...row, reasons, identityConfirmed: false,
      nextCheck: row.next_measurement || 'Collect dated fills and position snapshots.',
      hasFactualRoute: !!(array(row.return_routes).length || array(row.funding_routes).length || array(row.authority_links).length),
      confounders: array(row.confounders), contradictions: array(row.contradictions),
      parent_event_ids: array(row.parent_event_ids)};
  });
  return {collection, lastSuccessAgeHours, warnings, leads,
    walletsObserved: count(discovery?.wallets_observed), eventsRetained: count(discovery?.events_retained),
    unresolvedCount: count(quality?.unresolved_route_count) ?? count(routes?.counts?.unresolved),
    unresolved: array(routes?.unresolved).slice(0, 30),
    discovered: array(discovery?.candidates).slice(0, 30),
    evaluationLabel: quality?.evaluation_status === 'reported_research' ? 'Research replay only' : 'Not evaluated',
    classifiedUsd: count(accounting?.destination_classified_usd),
    controlledUsd: count(accounting?.controlled_recipient_usd)};
}
