<script>
	import { onMount } from 'svelte';
	import { fetchScanResults, formatPct, shortAddr } from '$lib/api.js';

	let scan = null;
	let loading = true;

	onMount(async () => {
		scan = await fetchScanResults();
		loading = false;
	});

	function getConfidenceClass(score) {
		if (score >= 0.70) return 'badge-red';
		if (score >= 0.50) return 'badge-yellow';
		return 'badge-blue';
	}

	function getConfidenceLabel(score) {
		if (score >= 0.70) return 'HIGH';
		if (score >= 0.50) return 'MEDIUM';
		return 'LOW';
	}
</script>

<div class="page-header">
	<h1>Wallet Scanner</h1>
	<p class="text-muted">Behavioral fingerprint matching against leaderboard wallets</p>
</div>

{#if loading}
	<div class="loading">Loading scan results...</div>
{:else if !scan}
	<div class="card" style="text-align:center;padding:48px">
		<p class="text-muted">No scan results yet. Run the scanner workflow.</p>
	</div>
{:else}
	<div class="grid-3" style="margin-bottom:24px">
		<div class="card">
			<div class="stat-value text-blue">{scan.wallets_scanned ?? 0}</div>
			<div class="stat-label">Wallets Scanned</div>
		</div>
		<div class="card">
			<div class="stat-value text-yellow">{scan.matches_found ?? 0}</div>
			<div class="stat-label">Matches Found</div>
		</div>
		<div class="card">
			<div class="stat-value text-muted">{scan.scan_time?.split('T')[0] ?? '—'}</div>
			<div class="stat-label">Last Scan</div>
		</div>
	</div>

	{#if scan.results?.length > 0}
		<div class="card">
			<h2 style="margin-bottom:16px;font-size:1.1rem">Matching Wallets</h2>
			<table>
				<thead>
					<tr>
						<th>Wallet</th>
						<th>Score</th>
						<th>Confidence</th>
						<th>Fills</th>
						<th>Assets</th>
						<th>Timing</th>
						<th>Leverage</th>
						<th>Style</th>
						<th>Duration</th>
					</tr>
				</thead>
				<tbody>
					{#each scan.results as r}
						<tr>
							<td><a href="https://app.hyperliquid.xyz/explorer/address/{r.wallet}" target="_blank">{shortAddr(r.wallet)}</a></td>
							<td><strong>{(r.score * 100).toFixed(1)}%</strong></td>
							<td><span class="badge {getConfidenceClass(r.score)}">{getConfidenceLabel(r.score)}</span></td>
							<td>{r.fills_count}</td>
							<td>{r.dimensions?.asset_preferences ? (r.dimensions.asset_preferences * 100).toFixed(0) + '%' : '—'}</td>
							<td>{r.dimensions?.timing_profile ? (r.dimensions.timing_profile * 100).toFixed(0) + '%' : '—'}</td>
							<td>{r.dimensions?.leverage_profile ? (r.dimensions.leverage_profile * 100).toFixed(0) + '%' : '—'}</td>
							<td>{r.dimensions?.entry_exit_style ? (r.dimensions.entry_exit_style * 100).toFixed(0) + '%' : '—'}</td>
							<td>{r.dimensions?.hold_duration ? (r.dimensions.hold_duration * 100).toFixed(0) + '%' : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<div class="card" style="text-align:center;padding:48px">
			<p class="text-muted">No matches found above the similarity threshold.</p>
		</div>
	{/if}
{/if}

<style>
	.page-header { margin-bottom: 24px; }
	.page-header h1 { font-size: 1.6rem; font-weight: 700; }
	.loading { text-align: center; padding: 60px; color: var(--text-muted); }
</style>
