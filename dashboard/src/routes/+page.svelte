<script>
	import { onMount } from 'svelte';
	import {
		fetchLatest, fetchFingerprint, fetchIndex, fetchScanResults,
		fetchPortfolio, fetchAllFunding,
		formatUSD, shortAddr
	} from '$lib/api.js';
	import Chart from 'chart.js/auto';
	import 'chartjs-adapter-date-fns';

	let positions = null;
	let spot = null;
	let hip3Xyz = null;
	let fingerprint = null;
	let index = null;
	let fees = null;
	let scan = null;
	let loading = true;

	let portfolio = null;
	let fundingData = [];
	let chartsLoading = true;

	let accountChartEl;
	let pnlChartEl;
	let fundingChartEl;
	let allocChartEl;
	let accountChart;
	let pnlChart;
	let fundingChart;
	let allocChart;

	onMount(async () => {
		[positions, spot, hip3Xyz, fingerprint, index, fees, scan] = await Promise.all([
			fetchLatest('positions'),
			fetchLatest('spot'),
			fetchLatest('positions_hip3_xyz'),
			fetchFingerprint(),
			fetchIndex(),
			fetchLatest('fees'),
			fetchScanResults(),
		]);
		loading = false;

		// Load chart data in background
		const [pf, fd] = await Promise.all([
			fetchPortfolio(),
			index ? fetchAllFunding(index) : Promise.resolve([]),
		]);
		portfolio = pf;
		fundingData = fd;
		chartsLoading = false;
		await renderCharts();
	});

	const CHART_COLORS = {
		cyan: 'rgba(0, 204, 221, 1)',
		cyanFill: 'rgba(0, 204, 221, 0.08)',
		green: 'rgba(0, 255, 136, 1)',
		greenFill: 'rgba(0, 255, 136, 0.08)',
		red: 'rgba(255, 51, 85, 1)',
		redFill: 'rgba(255, 51, 85, 0.08)',
		purple: 'rgba(170, 102, 255, 1)',
		purpleFill: 'rgba(170, 102, 255, 0.08)',
		yellow: 'rgba(255, 170, 0, 1)',
		blue: 'rgba(68, 136, 255, 1)',
		grid: 'rgba(42, 42, 74, 0.5)',
		gridZero: 'rgba(42, 42, 74, 0.8)',
		tick: 'rgba(136, 136, 160, 0.8)',
	};

	const baseScales = {
		x: {
			type: 'time',
			time: { unit: 'hour', displayFormats: { hour: 'MMM d HH:mm' } },
			grid: { color: CHART_COLORS.grid, drawBorder: false },
			ticks: { color: CHART_COLORS.tick, font: { family: "'JetBrains Mono', monospace", size: 10 }, maxTicksLimit: 8 },
			border: { display: false },
		},
		y: {
			grid: { color: CHART_COLORS.grid, drawBorder: false },
			ticks: { color: CHART_COLORS.tick, font: { family: "'JetBrains Mono', monospace", size: 10 } },
			border: { display: false },
		},
	};

	const basePlugins = {
		legend: { display: false },
		tooltip: {
			backgroundColor: 'rgba(18, 18, 26, 0.95)',
			borderColor: 'rgba(42, 42, 74, 0.8)',
			borderWidth: 1,
			titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
			bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
			padding: 10,
			cornerRadius: 6,
		},
	};

	async function renderCharts() {
		await new Promise(r => setTimeout(r, 0)); // wait for DOM

		const avh = portfolio?.accountValueHistory || [];
		const pnlh = portfolio?.pnlHistory || [];

		// Account Value chart (from portfolio API — hourly data)
		if (accountChartEl && avh.length > 0) {
			accountChart?.destroy();
			accountChart = new Chart(accountChartEl, {
				type: 'line',
				data: {
					datasets: [{
						data: avh.map(([ts, val]) => ({ x: ts, y: parseFloat(val) })),
						borderColor: CHART_COLORS.cyan,
						backgroundColor: CHART_COLORS.cyanFill,
						borderWidth: 2,
						fill: true,
						tension: 0.3,
						pointRadius: 0,
						pointHitRadius: 8,
					}]
				},
				options: {
					responsive: true,
					maintainAspectRatio: false,
					interaction: { intersect: false, mode: 'index' },
					scales: {
						...baseScales,
						y: {
							...baseScales.y,
							ticks: {
								...baseScales.y.ticks,
								callback: v => `$${(v / 1e6).toFixed(1)}M`,
							}
						}
					},
					plugins: {
						...basePlugins,
						tooltip: {
							...basePlugins.tooltip,
							callbacks: { label: ctx => `Account: ${formatUSD(ctx.parsed.y)}` },
						},
					},
				}
			});
		}

		// PnL chart (from portfolio API — cumulative PnL)
		if (pnlChartEl && pnlh.length > 0) {
			pnlChart?.destroy();
			pnlChart = new Chart(pnlChartEl, {
				type: 'line',
				data: {
					datasets: [{
						data: pnlh.map(([ts, val]) => ({ x: ts, y: parseFloat(val) })),
						borderColor: CHART_COLORS.green,
						backgroundColor: CHART_COLORS.greenFill,
						borderWidth: 2,
						fill: true,
						tension: 0.3,
						pointRadius: 0,
						pointHitRadius: 8,
						segment: {
							borderColor: ctx => ctx.p1.parsed.y < 0 ? CHART_COLORS.red : CHART_COLORS.green,
						},
					}]
				},
				options: {
					responsive: true,
					maintainAspectRatio: false,
					interaction: { intersect: false, mode: 'index' },
					scales: {
						...baseScales,
						y: {
							...baseScales.y,
							ticks: {
								...baseScales.y.ticks,
								callback: v => v >= 0 ? `+$${(v / 1e6).toFixed(2)}M` : `-$${(Math.abs(v) / 1e6).toFixed(2)}M`,
							},
						}
					},
					plugins: {
						...basePlugins,
						tooltip: {
							...basePlugins.tooltip,
							callbacks: {
								label: ctx => {
									const v = ctx.parsed.y;
									return `PnL: ${v >= 0 ? '+' : ''}${formatUSD(v)}`;
								}
							},
						},
					},
				}
			});
		}

		// Cumulative Funding chart
		if (fundingChartEl && fundingData.length > 0) {
			fundingChart?.destroy();
			let cumulative = 0;
			const fundingPoints = [];
			for (const f of fundingData) {
				cumulative += parseFloat(f.delta?.usdc || 0);
				fundingPoints.push({ x: f.time, y: cumulative });
			}
			// Downsample if too many points
			const step = fundingPoints.length > 200 ? Math.ceil(fundingPoints.length / 200) : 1;
			const sampled = fundingPoints.filter((_, i) => i % step === 0);

			fundingChart = new Chart(fundingChartEl, {
				type: 'line',
				data: {
					datasets: [{
						data: sampled,
						borderColor: CHART_COLORS.purple,
						backgroundColor: CHART_COLORS.purpleFill,
						borderWidth: 2,
						fill: true,
						tension: 0.3,
						pointRadius: 0,
						pointHitRadius: 8,
					}]
				},
				options: {
					responsive: true,
					maintainAspectRatio: false,
					interaction: { intersect: false, mode: 'index' },
					scales: {
						...baseScales,
						y: {
							...baseScales.y,
							ticks: {
								...baseScales.y.ticks,
								callback: v => `$${(v / 1e3).toFixed(1)}K`,
							},
						},
					},
					plugins: {
						...basePlugins,
						tooltip: {
							...basePlugins.tooltip,
							callbacks: { label: ctx => `Cumulative: ${formatUSD(ctx.parsed.y)}` },
						},
					},
				}
			});
		}

		// Position Allocation doughnut
		if (allocChartEl) {
			allocChart?.destroy();
			const openPos = getPositions(positions, hip3Xyz);
			if (openPos.length > 0) {
				const sorted = openPos
					.map(p => ({
						coin: p.coin,
						notional: Math.abs(parseFloat(p.positionValue || 0)),
					}))
					.sort((a, b) => b.notional - a.notional);

				const palette = [
					CHART_COLORS.cyan, CHART_COLORS.green, CHART_COLORS.purple,
					CHART_COLORS.yellow, CHART_COLORS.blue, CHART_COLORS.red,
					'rgba(0, 180, 180, 1)', 'rgba(180, 120, 255, 1)',
					'rgba(255, 200, 0, 1)', 'rgba(100, 200, 100, 1)',
				];

				allocChart = new Chart(allocChartEl, {
					type: 'doughnut',
					data: {
						labels: sorted.map(p => p.coin),
						datasets: [{
							data: sorted.map(p => p.notional),
							backgroundColor: sorted.map((_, i) => palette[i % palette.length]),
							borderColor: 'rgba(26, 26, 46, 1)',
							borderWidth: 2,
						}]
					},
					options: {
						responsive: true,
						maintainAspectRatio: false,
						cutout: '65%',
						plugins: {
							legend: {
								position: 'right',
								labels: {
									color: CHART_COLORS.tick,
									font: { family: "'JetBrains Mono', monospace", size: 10 },
									padding: 8,
									usePointStyle: true,
									pointStyleWidth: 8,
								},
							},
							tooltip: {
								...basePlugins.tooltip,
								callbacks: {
									label: ctx => {
										const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
										const pct = ((ctx.raw / total) * 100).toFixed(1);
										return ` ${ctx.label}: ${formatUSD(ctx.raw)} (${pct}%)`;
									},
								},
							},
						},
					},
				});
			}
		}
	}

	function getPositions(...datasets) {
		const all = [];
		for (const data of datasets) {
			if (!data) continue;
			const ap = data.assetPositions || data?.perp?.assetPositions || [];
			for (const a of ap) {
				if (a.position && parseFloat(a.position.szi) !== 0) {
					all.push(a.position);
				}
			}
		}
		return all.sort((a, b) => parseFloat(b.positionValue || 0) - parseFloat(a.positionValue || 0));
	}

	function getSpotBalances(data) {
		if (!data) return [];
		const balances = data.balances || [];
		return balances
			.filter(b => parseFloat(b.total || b.hold || 0) > 0)
			.sort((a, b) => parseFloat(b.entryNtl || 0) - parseFloat(a.entryNtl || 0));
	}

	function getAccountValue(data) {
		if (!data) return 0;
		const ms = data.marginSummary || data?.perp?.marginSummary || {};
		return parseFloat(ms.accountValue || 0);
	}

	function getTotalPnl(positions) {
		return positions.reduce((sum, p) => sum + parseFloat(p.unrealizedPnl || 0), 0);
	}

	function getMarginUsed(data) {
		if (!data) return 0;
		const ms = data.marginSummary || data?.perp?.marginSummary || {};
		return parseFloat(ms.totalMarginUsed || 0);
	}

	function getTotalNotional(data) {
		if (!data) return 0;
		const ms = data.marginSummary || data?.perp?.marginSummary || {};
		return parseFloat(ms.totalNtlPos || 0);
	}
</script>

<div class="page-header">
	<h1>Dashboard</h1>
	<p class="text-muted">
		Tracking <span class="mono text-blue">{shortAddr('0x45d26f28196d226497130c4bac709d808fed4029')}</span>
	</p>
</div>

{#if loading}
	<div class="loading">Loading data from GitHub...</div>
{:else}
	{@const openPositions = getPositions(positions, hip3Xyz)}
	{@const accountValue = getAccountValue(positions) + getAccountValue(hip3Xyz)}
	{@const totalPnl = getTotalPnl(openPositions)}
	{@const marginUsed = getMarginUsed(positions) + getMarginUsed(hip3Xyz)}
	{@const totalNotional = getTotalNotional(positions) + getTotalNotional(hip3Xyz)}
	{@const marginUtil = accountValue > 0 ? (marginUsed / accountValue * 100) : 0}

	<div class="grid-4 stats-row">
		<div class="card">
			<div class="stat-value text-blue">{formatUSD(accountValue)}</div>
			<div class="stat-label">Account Value</div>
		</div>
		<div class="card">
			<div class="stat-value" class:text-green={totalPnl >= 0} class:text-red={totalPnl < 0}>
				{totalPnl >= 0 ? '+' : ''}{formatUSD(totalPnl)}
			</div>
			<div class="stat-label">Unrealized PnL</div>
		</div>
		<div class="card">
			<div class="stat-value text-yellow">{openPositions.length}</div>
			<div class="stat-label">Open Positions</div>
		</div>
		<div class="card">
			<div class="stat-value text-purple">{formatUSD(totalNotional)}</div>
			<div class="stat-label">Total Notional</div>
		</div>
	</div>

	<div class="grid-4 stats-row">
		<div class="card">
			<div class="stat-value" class:text-green={marginUtil < 50} class:text-yellow={marginUtil >= 50 && marginUtil < 80} class:text-red={marginUtil >= 80}>
				{marginUtil.toFixed(1)}%
			</div>
			<div class="stat-label">Margin Utilization</div>
		</div>
		<div class="card">
			<div class="stat-value text-muted">{index?.stats?.total_fills?.toLocaleString() ?? '—'}</div>
			<div class="stat-label">Total Fills</div>
		</div>
		<div class="card">
			<div class="stat-value text-muted">{index?.stats?.total_funding?.toLocaleString() ?? '—'}</div>
			<div class="stat-label">Funding Events</div>
		</div>
		<div class="card">
			<div class="stat-value text-muted">{scan?.matches_found ?? '—'}</div>
			<div class="stat-label">Scanner Matches</div>
		</div>
	</div>

	<!-- Charts Section -->
	<div class="charts-section">
		<div class="chart-row">
			<div class="card chart-card">
				<div class="chart-header">
					<h2>Account Value</h2>
					{#if !chartsLoading && portfolio?.accountValueHistory?.length > 0}
						<span class="chart-badge">{portfolio.accountValueHistory.length} data points</span>
					{/if}
				</div>
				{#if chartsLoading}
					<div class="chart-loading">Loading chart data...</div>
				{:else if !portfolio?.accountValueHistory?.length}
					<div class="chart-loading">No snapshot data available</div>
				{/if}
				<div class="chart-container">
					<canvas bind:this={accountChartEl}></canvas>
				</div>
			</div>
			<div class="card chart-card chart-card-sm">
				<div class="chart-header">
					<h2>Position Allocation</h2>
				</div>
				<div class="chart-container">
					<canvas bind:this={allocChartEl}></canvas>
				</div>
			</div>
		</div>
		<div class="chart-row">
			<div class="card chart-card">
				<div class="chart-header">
					<h2>Unrealized PnL</h2>
				</div>
				<div class="chart-container">
					<canvas bind:this={pnlChartEl}></canvas>
				</div>
			</div>
			<div class="card chart-card">
				<div class="chart-header">
					<h2>Cumulative Funding</h2>
					{#if !chartsLoading && fundingData.length > 0}
						<span class="chart-badge">{fundingData.length} events</span>
					{/if}
				</div>
				<div class="chart-container">
					<canvas bind:this={fundingChartEl}></canvas>
				</div>
			</div>
		</div>
	</div>

	{#if openPositions.length > 0}
		<div class="card" style="margin-top:24px">
			<h2 style="margin-bottom:16px; font-size:1.1rem">Open Positions</h2>
			<table>
				<thead>
					<tr>
						<th>Coin</th>
						<th>Side</th>
						<th>Size</th>
						<th>Entry Price</th>
						<th>Mark Price</th>
						<th>Leverage</th>
						<th>Unrealized PnL</th>
						<th>Liq. Price</th>
					</tr>
				</thead>
				<tbody>
					{#each openPositions as pos}
						{@const size = parseFloat(pos.szi)}
						{@const pnl = parseFloat(pos.unrealizedPnl || 0)}
						<tr>
							<td><strong>{pos.coin}</strong></td>
							<td>
								<span class="badge" class:badge-green={size > 0} class:badge-red={size < 0}>
									{size > 0 ? 'LONG' : 'SHORT'}
								</span>
							</td>
							<td>{Math.abs(size).toFixed(4)}</td>
							<td>{formatUSD(parseFloat(pos.entryPx || 0))}</td>
							<td>{formatUSD(parseFloat(pos.positionValue || 0) / Math.abs(size) || 0)}</td>
							<td>{pos.leverage?.value || '—'}x</td>
							<td class:text-green={pnl >= 0} class:text-red={pnl < 0}>
								{pnl >= 0 ? '+' : ''}{formatUSD(pnl)}
							</td>
							<td class="text-muted">{pos.liquidationPx ? formatUSD(parseFloat(pos.liquidationPx)) : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<div class="card" style="margin-top:24px; text-align:center; padding:48px">
			<p class="text-muted">No open positions — wallet may be idle or data not yet collected.</p>
		</div>
	{/if}

	{@const spotBalances = getSpotBalances(spot)}
	{#if spotBalances.length > 0}
		<div class="card" style="margin-top:24px">
			<h2 style="margin-bottom:16px; font-size:1.1rem">Spot Positions</h2>
			<table>
				<thead>
					<tr>
						<th>Token</th>
						<th>Total</th>
						<th>Hold</th>
						<th>Entry Price</th>
					</tr>
				</thead>
				<tbody>
					{#each spotBalances as bal}
						<tr>
							<td><strong>{bal.coin}</strong></td>
							<td>{parseFloat(bal.total || 0).toFixed(4)}</td>
							<td>{parseFloat(bal.hold || 0).toFixed(4)}</td>
							<td>{bal.entryNtl ? formatUSD(parseFloat(bal.entryNtl) / parseFloat(bal.total || 1)) : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if fingerprint}
		<div class="card" style="margin-top:24px">
			<h2 style="margin-bottom:16px; font-size:1.1rem">Fingerprint Summary</h2>
			<div class="grid-3">
				<div>
					<div class="stat-label">Total Fills</div>
					<div class="mono">{fingerprint.data_range?.total_fills ?? '—'}</div>
				</div>
				<div>
					<div class="stat-label">Days Active</div>
					<div class="mono">{fingerprint.data_range?.total_days_active ?? '—'}</div>
				</div>
				<div>
					<div class="stat-label">Top Coins</div>
					<div class="mono">{fingerprint.asset_preferences?.top_5_by_volume?.join(', ') ?? '—'}</div>
				</div>
				<div>
					<div class="stat-label">Win Rate</div>
					<div class="mono">{fingerprint.entry_exit_style?.win_rate ? (fingerprint.entry_exit_style.win_rate * 100).toFixed(1) + '%' : '—'}</div>
				</div>
				<div>
					<div class="stat-label">Market/Limit Ratio</div>
					<div class="mono">
						{fingerprint.entry_exit_style?.order_type_ratio?.market ? (fingerprint.entry_exit_style.order_type_ratio.market * 100).toFixed(0) + '% / ' + (fingerprint.entry_exit_style.order_type_ratio.limit * 100).toFixed(0) + '%' : '—'}
					</div>
				</div>
				<div>
					<div class="stat-label">Computed</div>
					<div class="mono text-muted">{fingerprint.computed_at?.split('T')[0] ?? '—'}</div>
				</div>
			</div>
		</div>
	{/if}

	{#if scan?.results?.length > 0}
		<div class="card" style="margin-top:24px">
			<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
				<h2 style="font-size:1.1rem">Top Scanner Matches</h2>
				<a href="scanner" class="text-muted" style="font-size:0.8rem">View all →</a>
			</div>
			<table>
				<thead>
					<tr>
						<th>Wallet</th>
						<th>Score</th>
						<th>Assets</th>
						<th>Timing</th>
						<th>Leverage</th>
						<th>Style</th>
						<th>Duration</th>
					</tr>
				</thead>
				<tbody>
					{#each scan.results.slice(0, 5) as r}
						{@const s = r.score}
						<tr>
							<td>
								<a href="https://app.hyperliquid.xyz/explorer/address/{r.wallet}" target="_blank" class="text-blue">
									{shortAddr(r.wallet)}
								</a>
							</td>
							<td>
								<strong class:text-green={s >= 0.90} class:text-yellow={s >= 0.80 && s < 0.90} class:text-muted={s < 0.80}>
									{(s * 100).toFixed(1)}%
								</strong>
							</td>
							<td class="mono">{r.dimensions?.asset_preferences ? (r.dimensions.asset_preferences * 100).toFixed(0) + '%' : '—'}</td>
							<td class="mono">{r.dimensions?.timing_profile ? (r.dimensions.timing_profile * 100).toFixed(0) + '%' : '—'}</td>
							<td class="mono">{r.dimensions?.leverage_profile ? (r.dimensions.leverage_profile * 100).toFixed(0) + '%' : '—'}</td>
							<td class="mono">{r.dimensions?.entry_exit_style ? (r.dimensions.entry_exit_style * 100).toFixed(0) + '%' : '—'}</td>
							<td class="mono">{r.dimensions?.hold_duration ? (r.dimensions.hold_duration * 100).toFixed(0) + '%' : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if fees}
		<div class="card" style="margin-top:24px">
			<h2 style="margin-bottom:16px; font-size:1.1rem">Fee Schedule</h2>
			<div class="grid-3">
				<div>
					<div class="stat-label">Daily Volume</div>
					<div class="mono">{formatUSD(parseFloat(fees.dailyVlm || 0))}</div>
				</div>
				<div>
					<div class="stat-label">Maker Rate</div>
					<div class="mono">{fees.userMakerRate ? (parseFloat(fees.userMakerRate) * 100).toFixed(4) + '%' : '—'}</div>
				</div>
				<div>
					<div class="stat-label">Taker Rate</div>
					<div class="mono">{fees.userTakerRate ? (parseFloat(fees.userTakerRate) * 100).toFixed(4) + '%' : '—'}</div>
				</div>
			</div>
		</div>
	{/if}
{/if}

<style>
	.page-header {
		margin-bottom: 28px;
	}
	.page-header h1 {
		font-size: 1.6rem;
		font-weight: 700;
	}
	.loading {
		text-align: center;
		padding: 60px;
		color: var(--text-muted);
	}
	.stats-row {
		margin-bottom: 8px;
	}
	.charts-section {
		margin-top: 24px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.chart-row {
		display: flex;
		gap: 16px;
	}
	.chart-card {
		flex: 1;
		min-width: 0;
	}
	.chart-card-sm {
		flex: 0 0 340px;
	}
	.chart-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 16px;
	}
	.chart-header h2 {
		font-size: 1.1rem;
		font-weight: 600;
	}
	.chart-badge {
		font-family: var(--font-mono);
		font-size: 0.7rem;
		color: var(--text-muted);
		background: rgba(255,255,255,0.04);
		padding: 3px 8px;
		border-radius: 4px;
	}
	.chart-container {
		position: relative;
		height: 220px;
	}
	.chart-loading {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		color: var(--text-muted);
		font-size: 0.85rem;
		z-index: 1;
	}

	@media (max-width: 1024px) {
		.chart-row {
			flex-direction: column;
		}
		.chart-card-sm {
			flex: 1;
		}
	}
</style>
