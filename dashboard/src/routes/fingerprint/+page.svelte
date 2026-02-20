<script>
	import { onMount } from 'svelte';
	import { fetchFingerprint, formatPct } from '$lib/api.js';

	let fp = null;
	let loading = true;

	onMount(async () => {
		fp = await fetchFingerprint();
		loading = false;
	});

	function getDimensions(fp) {
		if (!fp) return [];
		const dims = [
			{ name: 'Asset Preferences', key: 'asset_preferences', weight: 0.15 },
			{ name: 'Leverage Profile', key: 'leverage_profile', weight: 0.15 },
			{ name: 'Position Sizing', key: 'position_sizing', weight: 0.12 },
			{ name: 'Timing Profile', key: 'timing_profile', weight: 0.15 },
			{ name: 'Hold Duration', key: 'hold_duration', weight: 0.10 },
			{ name: 'Entry/Exit Style', key: 'entry_exit_style', weight: 0.10 },
			{ name: 'Risk Management', key: 'risk_management', weight: 0.08 },
			{ name: 'Trade Sequencing', key: 'trade_sequencing', weight: 0.08 },
			{ name: 'Account Characteristics', key: 'account_characteristics', weight: 0.07 },
		];
		return dims.map(d => ({ ...d, data: fp[d.key] || {} }));
	}

	function getHourlyBars(dist) {
		if (!dist || dist.length !== 24) return [];
		const max = Math.max(...dist);
		return dist.map((v, i) => ({
			hour: i,
			value: v,
			height: max > 0 ? (v / max) * 100 : 0,
		}));
	}
</script>

<div class="page-header">
	<h1>Behavioral Fingerprint</h1>
	<p class="text-muted">Multi-dimensional trading pattern analysis</p>
</div>

{#if loading}
	<div class="loading">Loading fingerprint...</div>
{:else if !fp}
	<div class="card" style="text-align:center;padding:48px">
		<p class="text-muted">No fingerprint computed yet. Run the analyze workflow.</p>
	</div>
{:else}
	<div class="card" style="margin-bottom:24px">
		<div class="grid-3">
			<div>
				<div class="stat-label">Version</div>
				<div class="mono">{fp.version}</div>
			</div>
			<div>
				<div class="stat-label">Computed</div>
				<div class="mono">{fp.computed_at?.split('T')[0]}</div>
			</div>
			<div>
				<div class="stat-label">Data Range</div>
				<div class="mono">{fp.data_range?.total_fills ?? '?'} fills over {fp.data_range?.total_days_active ?? '?'} days</div>
			</div>
		</div>
	</div>

	<!-- Timing Profile -->
	{#if fp.timing_profile?.hourly_distribution}
		<div class="card" style="margin-bottom:24px">
			<h2 style="margin-bottom:16px;font-size:1.1rem">Timing Profile (UTC)</h2>
			<div class="hour-chart">
				{#each getHourlyBars(fp.timing_profile.hourly_distribution) as bar}
					<div class="hour-bar-wrapper" title="{bar.hour}:00 — {(bar.value * 100).toFixed(1)}%">
						<div class="hour-bar" style="height:{bar.height}%"></div>
						<span class="hour-label">{bar.hour}</span>
					</div>
				{/each}
			</div>
			<p class="text-muted" style="margin-top:12px;font-size:0.8rem">
				Most active: {fp.timing_profile.most_active_hours_utc?.map(h => h + ':00').join(', ')}
				| Inferred TZ offset: UTC{fp.timing_profile.inferred_timezone_offset >= 0 ? '+' : ''}{fp.timing_profile.inferred_timezone_offset}
			</p>
		</div>
	{/if}

	<!-- Dimension Cards -->
	<div class="grid-2">
		{#each getDimensions(fp) as dim}
			<div class="card">
				<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
					<h3 style="font-size:1rem">{dim.name}</h3>
					<span class="badge badge-blue">Weight: {dim.weight}</span>
				</div>
				<div class="dim-details">
					{#each Object.entries(dim.data).filter(([k]) => k !== 'weight') as [key, val]}
						<div class="dim-row">
							<span class="text-muted">{key}:</span>
							<span class="mono">
								{#if typeof val === 'object' && val !== null}
									{JSON.stringify(val).slice(0, 80)}{JSON.stringify(val).length > 80 ? '...' : ''}
								{:else}
									{val}
								{/if}
							</span>
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
{/if}

<style>
	.page-header { margin-bottom: 24px; }
	.page-header h1 { font-size: 1.6rem; font-weight: 700; }
	.loading { text-align: center; padding: 60px; color: var(--text-muted); }
	.hour-chart {
		display: flex;
		align-items: flex-end;
		gap: 3px;
		height: 120px;
		padding: 0 4px;
	}
	.hour-bar-wrapper {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		height: 100%;
		justify-content: flex-end;
	}
	.hour-bar {
		width: 100%;
		background: var(--accent-cyan);
		border-radius: 2px 2px 0 0;
		min-height: 2px;
		opacity: 0.8;
		transition: opacity 0.2s;
	}
	.hour-bar-wrapper:hover .hour-bar { opacity: 1; }
	.hour-label {
		font-size: 0.6rem;
		color: var(--text-muted);
		margin-top: 4px;
		font-family: var(--font-mono);
	}
	.dim-details {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.8rem;
	}
	.dim-row {
		display: flex;
		gap: 8px;
		overflow: hidden;
	}
	.dim-row .mono {
		font-size: 0.75rem;
		word-break: break-all;
	}
</style>
