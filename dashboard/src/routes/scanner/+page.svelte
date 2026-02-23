<script>
	import { onMount } from 'svelte';
	import { fetchScanResults, fetchFingerprint, fetchScanHistory, fetchIndex, formatPct, shortAddr } from '$lib/api.js';
	import Chart from 'chart.js/auto';

	let scan = null;
	let targetFp = null;
	let scanHistory = [];
	let loading = true;
	let expandedWallet = null;
	let radarChartEl;
	let radarChart;
	let timingChartEl;
	let timingChart;

	const DIM_LABELS = {
		asset_preferences: 'Assets',
		timing_profile: 'Timing',
		leverage_profile: 'Leverage',
		entry_exit_style: 'Style',
		hold_duration: 'Duration',
	};
	const DIM_KEYS = Object.keys(DIM_LABELS);

	onMount(async () => {
		const [scanData, fp, index] = await Promise.all([
			fetchScanResults(),
			fetchFingerprint(),
			fetchIndex(),
		]);
		scan = scanData;
		targetFp = fp;
		if (index) {
			scanHistory = await fetchScanHistory(index);
		}
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

	function getWalletHistory(wallet) {
		const history = [];
		for (const scanRun of scanHistory) {
			if (!scanRun?.results) continue;
			const match = scanRun.results.find(r => r.wallet === wallet);
			if (match) {
				history.push({
					date: scanRun.scan_time?.split('T')[0] || '?',
					score: match.score,
				});
			}
		}
		return history;
	}

	function getScoreTrend(wallet) {
		const h = getWalletHistory(wallet);
		if (h.length < 2) return { trend: 'new', appearances: h.length };
		const first = h[0].score;
		const last = h[h.length - 1].score;
		const diff = last - first;
		return {
			trend: diff > 0.02 ? 'up' : diff < -0.02 ? 'down' : 'stable',
			appearances: h.length,
			delta: diff,
		};
	}

	async function toggleExpand(wallet) {
		if (expandedWallet === wallet) {
			expandedWallet = null;
			radarChart?.destroy();
			timingChart?.destroy();
			return;
		}
		expandedWallet = wallet;

		// Wait for DOM
		await new Promise(r => setTimeout(r, 50));
		renderComparisonCharts(wallet);
	}

	function renderComparisonCharts(wallet) {
		const result = scan.results.find(r => r.wallet === wallet);
		if (!result) return;

		const dims = result.dimensions || {};
		const candidateScores = DIM_KEYS.map(k => (dims[k] || 0) * 100);

		// Radar chart
		const el = document.getElementById('radar-' + wallet.slice(0, 8));
		if (el) {
			radarChart?.destroy();
			radarChart = new Chart(el, {
				type: 'radar',
				data: {
					labels: DIM_KEYS.map(k => DIM_LABELS[k]),
					datasets: [
						{
							label: 'Target',
							data: [100, 100, 100, 100, 100],
							borderColor: 'rgba(0, 204, 221, 0.9)',
							backgroundColor: 'rgba(0, 204, 221, 0.1)',
							borderWidth: 2,
							pointRadius: 3,
							pointBackgroundColor: 'rgba(0, 204, 221, 1)',
						},
						{
							label: 'Candidate',
							data: candidateScores,
							borderColor: 'rgba(255, 170, 0, 0.9)',
							backgroundColor: 'rgba(255, 170, 0, 0.1)',
							borderWidth: 2,
							pointRadius: 3,
							pointBackgroundColor: 'rgba(255, 170, 0, 1)',
						}
					]
				},
				options: {
					responsive: true,
					maintainAspectRatio: false,
					scales: {
						r: {
							min: 0,
							max: 100,
							ticks: {
								stepSize: 25,
								color: 'rgba(136, 136, 160, 0.6)',
								backdropColor: 'transparent',
								font: { family: "'JetBrains Mono', monospace", size: 9 },
							},
							grid: { color: 'rgba(42, 42, 74, 0.5)' },
							angleLines: { color: 'rgba(42, 42, 74, 0.5)' },
							pointLabels: {
								color: 'rgba(224, 224, 232, 0.9)',
								font: { family: "'JetBrains Mono', monospace", size: 11, weight: 500 },
							},
						}
					},
					plugins: {
						legend: {
							labels: {
								color: 'rgba(136, 136, 160, 0.8)',
								font: { family: "'JetBrains Mono', monospace", size: 10 },
								usePointStyle: true,
								pointStyleWidth: 8,
							},
						},
						tooltip: {
							backgroundColor: 'rgba(18, 18, 26, 0.95)',
							borderColor: 'rgba(42, 42, 74, 0.8)',
							borderWidth: 1,
							titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
							bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
							callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.raw.toFixed(1)}%` },
						},
					},
				}
			});
		}

		// Timing comparison chart
		const fp = result.fingerprint;
		const targetHourly = targetFp?.timing_profile?.hourly_distribution;
		const candidateHourly = fp?.timing_profile?.hourly_distribution;

		if (targetHourly && candidateHourly) {
			const tel = document.getElementById('timing-' + wallet.slice(0, 8));
			if (tel) {
				timingChart?.destroy();
				timingChart = new Chart(tel, {
					type: 'bar',
					data: {
						labels: Array.from({length: 24}, (_, i) => `${i}h`),
						datasets: [
							{
								label: 'Target',
								data: targetHourly.map(v => v * 100),
								backgroundColor: 'rgba(0, 204, 221, 0.6)',
								borderRadius: 2,
							},
							{
								label: 'Candidate',
								data: candidateHourly.map(v => v * 100),
								backgroundColor: 'rgba(255, 170, 0, 0.6)',
								borderRadius: 2,
							}
						]
					},
					options: {
						responsive: true,
						maintainAspectRatio: false,
						scales: {
							x: {
								grid: { display: false },
								ticks: { color: 'rgba(136, 136, 160, 0.6)', font: { size: 8, family: "'JetBrains Mono', monospace" } },
								border: { display: false },
							},
							y: {
								grid: { color: 'rgba(42, 42, 74, 0.3)' },
								ticks: {
									color: 'rgba(136, 136, 160, 0.6)',
									font: { size: 9, family: "'JetBrains Mono', monospace" },
									callback: v => v.toFixed(0) + '%',
								},
								border: { display: false },
							}
						},
						plugins: {
							legend: {
								labels: {
									color: 'rgba(136, 136, 160, 0.8)',
									font: { size: 10, family: "'JetBrains Mono', monospace" },
									usePointStyle: true,
									pointStyleWidth: 8,
								},
							},
							tooltip: {
								backgroundColor: 'rgba(18, 18, 26, 0.95)',
								borderColor: 'rgba(42, 42, 74, 0.8)',
								borderWidth: 1,
							},
						},
					}
				});
			}
		}
	}

	function getOverlappingCoins(fp) {
		if (!fp || !targetFp) return { overlap: [], targetOnly: [], candidateOnly: [] };
		const targetCoins = new Set(targetFp.asset_preferences?.coins_traded || []);
		const candidateCoins = new Set(fp.asset_preferences?.coins_traded || []);
		return {
			overlap: [...targetCoins].filter(c => candidateCoins.has(c)),
			targetOnly: [...targetCoins].filter(c => !candidateCoins.has(c)),
			candidateOnly: [...candidateCoins].filter(c => !targetCoins.has(c)),
		};
	}
</script>

<div class="page-header">
	<h1>Wallet Scanner</h1>
	<p class="text-muted">Behavioral fingerprint matching — click a wallet to compare</p>
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
			<div class="results-list">
				{#each scan.results as r}
					{@const trend = getScoreTrend(r.wallet)}
					{@const isExpanded = expandedWallet === r.wallet}
					{@const hasFp = !!r.fingerprint}

					<!-- svelte-ignore a11y-click-events-have-key-events -->
					<!-- svelte-ignore a11y-no-static-element-interactions -->
					<div class="result-row" class:expanded={isExpanded} on:click={() => hasFp && toggleExpand(r.wallet)}>
						<div class="result-main">
							<div class="result-wallet">
								<a href="https://app.hyperliquid.xyz/explorer/address/{r.wallet}" target="_blank" on:click|stopPropagation>{shortAddr(r.wallet)}</a>
								{#if hasFp}
									<span class="expand-hint">{isExpanded ? '▾' : '▸'}</span>
								{/if}
							</div>
							<div class="result-score">
								<strong class:text-red={r.score >= 0.70} class:text-yellow={r.score >= 0.50 && r.score < 0.70} class:text-muted={r.score < 0.50}>
									{(r.score * 100).toFixed(1)}%
								</strong>
								<span class="badge {getConfidenceClass(r.score)}">{getConfidenceLabel(r.score)}</span>
							</div>
							<div class="result-dims">
								{#each DIM_KEYS as k}
									<div class="dim-bar-wrap" title="{DIM_LABELS[k]}: {r.dimensions?.[k] ? (r.dimensions[k] * 100).toFixed(0) + '%' : '—'}">
										<div class="dim-bar" style="width:{(r.dimensions?.[k] || 0) * 100}%"></div>
										<span class="dim-label">{DIM_LABELS[k]}</span>
									</div>
								{/each}
							</div>
							<div class="result-meta">
								<span class="mono">{r.fills_count} fills</span>
								{#if trend.appearances > 1}
									<span class="trend-badge" class:trend-up={trend.trend === 'up'} class:trend-down={trend.trend === 'down'} class:trend-stable={trend.trend === 'stable'}>
										{trend.trend === 'up' ? '↑' : trend.trend === 'down' ? '↓' : '→'} {trend.appearances}x
									</span>
								{:else}
									<span class="trend-badge trend-new">new</span>
								{/if}
							</div>
						</div>

						{#if isExpanded && r.fingerprint}
							{@const coins = getOverlappingCoins(r.fingerprint)}
							<!-- svelte-ignore a11y-click-events-have-key-events -->
							<!-- svelte-ignore a11y-no-static-element-interactions -->
							<div class="comparison-panel" on:click|stopPropagation>
								<div class="comparison-grid">
									<div class="comparison-chart">
										<h3>Dimension Comparison</h3>
										<div class="chart-wrap">
											<canvas id="radar-{r.wallet.slice(0, 8)}"></canvas>
										</div>
									</div>
									<div class="comparison-chart">
										<h3>Activity Hours (UTC)</h3>
										<div class="chart-wrap">
											<canvas id="timing-{r.wallet.slice(0, 8)}"></canvas>
										</div>
									</div>
								</div>

								<div class="comparison-details">
									<div class="detail-section">
										<h4>Shared Assets ({coins.overlap.length})</h4>
										<div class="coin-tags">
											{#each coins.overlap as coin}
												<span class="coin-tag coin-match">{coin}</span>
											{/each}
											{#if coins.overlap.length === 0}
												<span class="text-muted">None</span>
											{/if}
										</div>
									</div>
									<div class="detail-section">
										<h4>Target Only ({coins.targetOnly.length})</h4>
										<div class="coin-tags">
											{#each coins.targetOnly as coin}
												<span class="coin-tag coin-target">{coin}</span>
											{/each}
										</div>
									</div>
									<div class="detail-section">
										<h4>Candidate Only ({coins.candidateOnly.length})</h4>
										<div class="coin-tags">
											{#each coins.candidateOnly as coin}
												<span class="coin-tag coin-candidate">{coin}</span>
											{/each}
										</div>
									</div>
								</div>

								<div class="comparison-details" style="margin-top:12px">
									<div class="detail-section">
										<h4>Leverage</h4>
										<div class="mono" style="font-size:0.8rem">
											Target: avg {targetFp?.leverage_profile?.overall?.mean?.toFixed(1) ?? '?'}x
											| Candidate: avg {r.fingerprint.leverage_profile?.overall?.mean?.toFixed(1) ?? '?'}x
										</div>
									</div>
									<div class="detail-section">
										<h4>Execution</h4>
										<div class="mono" style="font-size:0.8rem">
											Target: {targetFp?.entry_exit_style?.order_type_ratio?.market ? (targetFp.entry_exit_style.order_type_ratio.market * 100).toFixed(0) : '?'}% market, win rate {targetFp?.entry_exit_style?.win_rate ? (targetFp.entry_exit_style.win_rate * 100).toFixed(1) : '?'}%
											<br>Candidate: {r.fingerprint.entry_exit_style?.order_type_ratio?.market ? (r.fingerprint.entry_exit_style.order_type_ratio.market * 100).toFixed(0) : '?'}% market, win rate {r.fingerprint.entry_exit_style?.win_rate ? (r.fingerprint.entry_exit_style.win_rate * 100).toFixed(1) : '?'}%
										</div>
									</div>
									<div class="detail-section">
										<h4>Hold Duration</h4>
										<div class="mono" style="font-size:0.8rem">
											Target: avg {targetFp?.hold_duration?.overall_minutes?.mean?.toFixed(1) ?? '?'} min
											| Candidate: avg {r.fingerprint.hold_duration?.overall_minutes?.mean?.toFixed(1) ?? '?'} min
										</div>
									</div>
								</div>
							</div>
						{/if}
					</div>
				{/each}
			</div>
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

	.results-list {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.result-row {
		border-radius: 8px;
		cursor: pointer;
		transition: background 0.15s;
	}
	.result-row:hover {
		background: var(--bg-card-hover);
	}
	.result-row.expanded {
		background: var(--bg-card-hover);
	}

	.result-main {
		display: grid;
		grid-template-columns: 160px 120px 1fr 120px;
		align-items: center;
		gap: 16px;
		padding: 10px 14px;
	}

	.result-wallet a {
		font-family: var(--font-mono);
		font-size: 0.85rem;
	}
	.expand-hint {
		color: var(--text-muted);
		font-size: 0.7rem;
		margin-left: 4px;
	}

	.result-score {
		display: flex;
		align-items: center;
		gap: 8px;
		font-family: var(--font-mono);
		font-size: 0.9rem;
	}

	.result-dims {
		display: flex;
		gap: 8px;
	}
	.dim-bar-wrap {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.dim-bar {
		height: 4px;
		background: var(--accent-cyan);
		border-radius: 2px;
		opacity: 0.7;
		transition: width 0.3s;
	}
	.dim-label {
		font-size: 0.55rem;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-family: var(--font-mono);
	}

	.result-meta {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 2px;
		font-size: 0.75rem;
		color: var(--text-muted);
	}

	.trend-badge {
		font-size: 0.65rem;
		padding: 1px 6px;
		border-radius: 4px;
		font-family: var(--font-mono);
	}
	.trend-up { background: rgba(0,255,136,0.15); color: var(--accent-green); }
	.trend-down { background: rgba(255,51,85,0.15); color: var(--accent-red); }
	.trend-stable { background: rgba(68,136,255,0.15); color: var(--accent-blue); }
	.trend-new { background: rgba(136,136,160,0.15); color: var(--text-muted); }

	/* Comparison panel */
	.comparison-panel {
		padding: 16px 14px 20px;
		border-top: 1px solid var(--border);
		cursor: default;
	}

	.comparison-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 16px;
		margin-bottom: 16px;
	}

	.comparison-chart h3 {
		font-size: 0.85rem;
		font-weight: 600;
		margin-bottom: 8px;
		color: var(--text-secondary);
	}

	.chart-wrap {
		height: 220px;
		position: relative;
	}

	.comparison-details {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 12px;
	}

	.detail-section h4 {
		font-size: 0.75rem;
		color: var(--text-secondary);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		margin-bottom: 6px;
	}

	.coin-tags {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}

	.coin-tag {
		font-family: var(--font-mono);
		font-size: 0.7rem;
		padding: 2px 6px;
		border-radius: 4px;
	}
	.coin-match { background: rgba(0,255,136,0.15); color: var(--accent-green); }
	.coin-target { background: rgba(0,204,221,0.15); color: var(--accent-cyan); }
	.coin-candidate { background: rgba(255,170,0,0.15); color: var(--accent-yellow); }

	@media (max-width: 1024px) {
		.result-main {
			grid-template-columns: 1fr;
			gap: 8px;
		}
		.comparison-grid {
			grid-template-columns: 1fr;
		}
		.comparison-details {
			grid-template-columns: 1fr;
		}
	}
</style>
