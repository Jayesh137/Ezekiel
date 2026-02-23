<script>
	import { onMount } from 'svelte';
	import { fetchFingerprint, formatPct } from '$lib/api.js';
	import Chart from 'chart.js/auto';

	let fp = null;
	let loading = true;
	let radarEl;
	let radarChart;
	let dowEl;
	let dowChart;

	onMount(async () => {
		fp = await fetchFingerprint();
		loading = false;
		if (fp) {
			await new Promise(r => setTimeout(r, 0));
			renderCharts();
		}
	});

	const DIMS = [
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

	function getDimensions(fp) {
		if (!fp) return [];
		return DIMS.map(d => ({ ...d, data: fp[d.key] || {} }));
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

	function renderCharts() {
		// Dimension weight radar chart
		if (radarEl) {
			const weights = DIMS.map(d => d.weight * 100);
			radarChart = new Chart(radarEl, {
				type: 'radar',
				data: {
					labels: DIMS.map(d => d.name.replace('/', '/\n')),
					datasets: [{
						label: 'Weight %',
						data: weights,
						borderColor: 'rgba(0, 204, 221, 0.9)',
						backgroundColor: 'rgba(0, 204, 221, 0.15)',
						borderWidth: 2,
						pointRadius: 4,
						pointBackgroundColor: 'rgba(0, 204, 221, 1)',
						pointBorderColor: 'rgba(0, 204, 221, 1)',
					}]
				},
				options: {
					responsive: true,
					maintainAspectRatio: false,
					scales: {
						r: {
							min: 0,
							max: 20,
							ticks: {
								stepSize: 5,
								color: 'rgba(136, 136, 160, 0.5)',
								backdropColor: 'transparent',
								font: { family: "'JetBrains Mono', monospace", size: 9 },
							},
							grid: { color: 'rgba(42, 42, 74, 0.5)' },
							angleLines: { color: 'rgba(42, 42, 74, 0.5)' },
							pointLabels: {
								color: 'rgba(224, 224, 232, 0.9)',
								font: { family: "'JetBrains Mono', monospace", size: 10 },
							},
						}
					},
					plugins: {
						legend: { display: false },
						tooltip: {
							backgroundColor: 'rgba(18, 18, 26, 0.95)',
							borderColor: 'rgba(42, 42, 74, 0.8)',
							borderWidth: 1,
							titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
							bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
							callbacks: { label: ctx => ` Weight: ${ctx.raw}%` },
						},
					},
				}
			});
		}

		// Day of week chart
		const dow = fp?.timing_profile?.day_of_week_distribution;
		if (dowEl && dow?.length === 7) {
			const dayLabels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
			dowChart = new Chart(dowEl, {
				type: 'bar',
				data: {
					labels: dayLabels,
					datasets: [{
						data: dow.map(v => v * 100),
						backgroundColor: dow.map(v => v > 0.1 ? 'rgba(0, 204, 221, 0.7)' : 'rgba(0, 204, 221, 0.3)'),
						borderRadius: 3,
					}]
				},
				options: {
					responsive: true,
					maintainAspectRatio: false,
					scales: {
						x: {
							grid: { display: false },
							ticks: { color: 'rgba(136, 136, 160, 0.7)', font: { family: "'JetBrains Mono', monospace", size: 10 } },
							border: { display: false },
						},
						y: {
							grid: { color: 'rgba(42, 42, 74, 0.3)' },
							ticks: {
								color: 'rgba(136, 136, 160, 0.5)',
								font: { family: "'JetBrains Mono', monospace", size: 9 },
								callback: v => v.toFixed(0) + '%',
							},
							border: { display: false },
						}
					},
					plugins: {
						legend: { display: false },
						tooltip: {
							backgroundColor: 'rgba(18, 18, 26, 0.95)',
							borderColor: 'rgba(42, 42, 74, 0.8)',
							borderWidth: 1,
							bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
							callbacks: { label: ctx => ` ${ctx.raw.toFixed(1)}% of trades` },
						},
					},
				}
			});
		}
	}

	function formatVal(val) {
		if (val === null || val === undefined) return '—';
		if (typeof val === 'boolean') return val ? 'Yes' : 'No';
		if (typeof val === 'number') {
			if (Number.isInteger(val)) return val.toLocaleString();
			return val.toFixed(4);
		}
		if (typeof val === 'object') {
			const s = JSON.stringify(val);
			return s.length > 100 ? s.slice(0, 100) + '...' : s;
		}
		return String(val);
	}
</script>

<div class="page-header">
	<h1>Behavioral Fingerprint</h1>
	<p class="text-muted">Multi-dimensional trading pattern analysis — the signature used to find linked wallets</p>
</div>

{#if loading}
	<div class="loading">Loading fingerprint...</div>
{:else if !fp}
	<div class="card" style="text-align:center;padding:48px">
		<p class="text-muted">No fingerprint computed yet. Run the analyze workflow.</p>
	</div>
{:else}
	<div class="grid-3" style="margin-bottom:24px">
		<div class="card">
			<div class="stat-value text-blue">{fp.data_range?.total_fills?.toLocaleString() ?? '—'}</div>
			<div class="stat-label">Total Fills Analyzed</div>
		</div>
		<div class="card">
			<div class="stat-value text-yellow">{fp.data_range?.total_days_active ?? '—'}</div>
			<div class="stat-label">Days Active</div>
		</div>
		<div class="card">
			<div class="stat-value text-muted">{fp.asset_preferences?.total_unique_coins ?? '—'}</div>
			<div class="stat-label">Unique Coins Traded</div>
		</div>
	</div>

	<!-- Radar + Key stats -->
	<div class="chart-row" style="margin-bottom:24px">
		<div class="card" style="flex:1">
			<h2 style="margin-bottom:12px;font-size:1.1rem">Dimension Weights</h2>
			<p class="text-muted" style="font-size:0.75rem;margin-bottom:12px">Scanner uses these weights to compare wallets. Higher weight = more influence on match score.</p>
			<div style="height:280px;position:relative">
				<canvas bind:this={radarEl}></canvas>
			</div>
		</div>
		<div class="card" style="flex:1">
			<h2 style="margin-bottom:12px;font-size:1.1rem">Key Behavioral Traits</h2>
			<div class="traits-list">
				<div class="trait">
					<span class="trait-label">Top Assets</span>
					<span class="trait-value">{fp.asset_preferences?.top_5_by_volume?.join(', ') ?? '—'}</span>
				</div>
				<div class="trait">
					<span class="trait-label">Avg Leverage</span>
					<span class="trait-value">{fp.leverage_profile?.overall?.mean?.toFixed(1) ?? '—'}x (max {fp.leverage_profile?.overall?.max ?? '—'}x)</span>
				</div>
				<div class="trait">
					<span class="trait-label">Order Type</span>
					<span class="trait-value">{fp.entry_exit_style?.order_type_ratio?.market ? (fp.entry_exit_style.order_type_ratio.market * 100).toFixed(0) : '?'}% market / {fp.entry_exit_style?.order_type_ratio?.limit ? (fp.entry_exit_style.order_type_ratio.limit * 100).toFixed(0) : '?'}% limit</span>
				</div>
				<div class="trait">
					<span class="trait-label">Win Rate</span>
					<span class="trait-value" class:text-green={fp.entry_exit_style?.win_rate > 0.5}>{fp.entry_exit_style?.win_rate ? (fp.entry_exit_style.win_rate * 100).toFixed(1) + '%' : '—'}</span>
				</div>
				<div class="trait">
					<span class="trait-label">Avg Hold</span>
					<span class="trait-value">{fp.hold_duration?.overall_minutes?.mean?.toFixed(1) ?? '—'} min</span>
				</div>
				<div class="trait">
					<span class="trait-label">Margin Usage</span>
					<span class="trait-value">{fp.risk_management?.margin_utilization ? (fp.risk_management.margin_utilization * 100).toFixed(1) + '%' : '—'}</span>
				</div>
				<div class="trait">
					<span class="trait-label">Buy/Sell Ratio</span>
					<span class="trait-value">{fp.trade_sequencing?.buy_sell_ratio?.buy_pct ? (fp.trade_sequencing.buy_sell_ratio.buy_pct * 100).toFixed(0) : '?'}% buy / {fp.trade_sequencing?.buy_sell_ratio?.sell_pct ? (fp.trade_sequencing.buy_sell_ratio.sell_pct * 100).toFixed(0) : '?'}% sell</span>
				</div>
				<div class="trait">
					<span class="trait-label">Active Hours</span>
					<span class="trait-value">{fp.timing_profile?.most_active_hours_utc?.map(h => h + ':00').join(', ') ?? '—'} UTC</span>
				</div>
				<div class="trait">
					<span class="trait-label">Timezone</span>
					<span class="trait-value">UTC{fp.timing_profile?.inferred_timezone_offset >= 0 ? '+' : ''}{fp.timing_profile?.inferred_timezone_offset ?? '?'}</span>
				</div>
			</div>
		</div>
	</div>

	<!-- Timing charts -->
	<div class="chart-row" style="margin-bottom:24px">
		{#if fp.timing_profile?.hourly_distribution}
			<div class="card" style="flex:2">
				<h2 style="margin-bottom:16px;font-size:1.1rem">Hourly Activity (UTC)</h2>
				<div class="hour-chart">
					{#each getHourlyBars(fp.timing_profile.hourly_distribution) as bar}
						<div class="hour-bar-wrapper" title="{bar.hour}:00 — {(bar.value * 100).toFixed(1)}%">
							<div class="hour-bar" style="height:{bar.height}%"></div>
							<span class="hour-label">{bar.hour}</span>
						</div>
					{/each}
				</div>
			</div>
		{/if}
		<div class="card" style="flex:1">
			<h2 style="margin-bottom:16px;font-size:1.1rem">Day of Week</h2>
			<div style="height:140px;position:relative">
				<canvas bind:this={dowEl}></canvas>
			</div>
		</div>
	</div>

	<!-- Dimension Cards -->
	<h2 style="font-size:1.1rem;margin-bottom:16px">All Dimensions</h2>
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
							<span class="mono">{formatVal(val)}</span>
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

	.chart-row {
		display: flex;
		gap: 16px;
	}

	.traits-list {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.trait {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 6px 0;
		border-bottom: 1px solid rgba(42, 42, 74, 0.3);
	}
	.trait:last-child { border-bottom: none; }
	.trait-label {
		font-size: 0.8rem;
		color: var(--text-secondary);
	}
	.trait-value {
		font-family: var(--font-mono);
		font-size: 0.8rem;
	}

	.hour-chart {
		display: flex;
		align-items: flex-end;
		gap: 3px;
		height: 140px;
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

	@media (max-width: 1024px) {
		.chart-row { flex-direction: column; }
	}
</style>
