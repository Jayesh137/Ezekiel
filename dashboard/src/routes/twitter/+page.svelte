<script>
	import { onMount } from 'svelte';
	import { fetchCorrelation, formatPct } from '$lib/api.js';

	let correlation = null;
	let loading = true;

	onMount(async () => {
		correlation = await fetchCorrelation();
		loading = false;
	});

	function getConfidenceColor(c) {
		if (c === 'HIGH') return 'text-green';
		if (c === 'MEDIUM') return 'text-yellow';
		if (c === 'LOW') return 'text-red';
		return 'text-muted';
	}

	function getConfidenceBadge(c) {
		if (c === 'HIGH') return 'badge-green';
		if (c === 'MEDIUM') return 'badge-yellow';
		if (c === 'LOW') return 'badge-red';
		return 'badge-blue';
	}
</script>

<div class="page-header">
	<h1>Twitter Intelligence</h1>
	<p class="text-muted">Correlation analysis: wallet trades vs. @GiganticRebirth / @GCRClassic tweets</p>
</div>

{#if loading}
	<div class="loading">Loading correlation data...</div>
{:else if !correlation}
	<div class="card" style="text-align:center;padding:48px">
		<p class="text-muted">No Twitter correlation data yet. Run the analyze workflow.</p>
		<p class="text-muted" style="margin-top:8px;font-size:0.85rem">
			The system monitors @GiganticRebirth and @GCRClassic via RSS bridges,
			then correlates tweet timestamps against wallet fills.
		</p>
	</div>
{:else}
	<div class="card" style="margin-bottom:24px">
		<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
			<h2 style="font-size:1.1rem">Hypothesis</h2>
			<span class="badge {getConfidenceBadge(correlation.confidence)}">
				{correlation.confidence}
			</span>
		</div>
		<p style="font-size:0.95rem">{correlation.hypothesis}</p>
	</div>

	<div class="grid-3" style="margin-bottom:24px">
		<div class="card">
			<div class="stat-value {getConfidenceColor(correlation.evidence?.timing_correlation?.score > 0.6 ? 'HIGH' : correlation.evidence?.timing_correlation?.score > 0.4 ? 'MEDIUM' : 'LOW')}">
				{correlation.evidence?.timing_correlation?.score ? (correlation.evidence.timing_correlation.score * 100).toFixed(1) + '%' : '—'}
			</div>
			<div class="stat-label">Timing Correlation</div>
			<div class="text-muted" style="font-size:0.75rem;margin-top:4px">
				{correlation.evidence?.timing_correlation?.matches ?? 0} / {correlation.evidence?.timing_correlation?.sample_size ?? 0} tweets matched to fills
			</div>
		</div>
		<div class="card">
			<div class="stat-value {getConfidenceColor(correlation.evidence?.direction_correlation?.score > 0.6 ? 'HIGH' : correlation.evidence?.direction_correlation?.score > 0.4 ? 'MEDIUM' : 'LOW')}">
				{correlation.evidence?.direction_correlation?.score ? (correlation.evidence.direction_correlation.score * 100).toFixed(1) + '%' : '—'}
			</div>
			<div class="stat-label">Direction Correlation</div>
			<div class="text-muted" style="font-size:0.75rem;margin-top:4px">
				{correlation.evidence?.direction_correlation?.matches ?? 0} / {correlation.evidence?.direction_correlation?.sample_size ?? 0} direction matches
			</div>
		</div>
		<div class="card">
			<div class="stat-value text-blue">{correlation.evidence?.sample_size ?? 0}</div>
			<div class="stat-label">Sample Size</div>
			<div class="text-muted" style="font-size:0.75rem;margin-top:4px">
				{correlation.evidence?.time_range || 'No data range'}
			</div>
		</div>
	</div>

	{#if correlation.notable_matches?.length > 0}
		<div class="card">
			<h2 style="margin-bottom:16px;font-size:1.1rem">Notable Matches</h2>
			<div class="matches-list">
				{#each correlation.notable_matches as match}
					<div class="match-item">
						<div class="match-tweet">
							<span class="badge badge-blue" style="margin-right:8px">{match.correlation_type}</span>
							{match.tweet}
						</div>
						<div class="match-trade">
							{match.trade}
							<span class="text-muted" style="margin-left:8px">({match.delay_minutes > 0 ? '+' : ''}{match.delay_minutes} min)</span>
						</div>
					</div>
				{/each}
			</div>
		</div>
	{/if}
{/if}

<style>
	.page-header { margin-bottom: 24px; }
	.page-header h1 { font-size: 1.6rem; font-weight: 700; }
	.loading { text-align: center; padding: 60px; color: var(--text-muted); }
	.matches-list { display: flex; flex-direction: column; gap: 16px; }
	.match-item {
		padding: 14px;
		background: var(--bg-secondary);
		border-radius: 8px;
		border-left: 3px solid var(--accent-cyan);
	}
	.match-tweet {
		font-size: 0.85rem;
		margin-bottom: 8px;
		line-height: 1.5;
	}
	.match-trade {
		font-family: var(--font-mono);
		font-size: 0.8rem;
		color: var(--text-secondary);
	}
</style>
