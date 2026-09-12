<script>
	import { onMount } from 'svelte';
	import Chart from 'chart.js/auto';
	import Addr from '$lib/Addr.svelte';
	import {
		fetchLatest,
		fetchIndex,
		fetchScanResults,
		fetchCandidates,
		fetchFundFlows,
		fetchHlTransfers,
		fetchRisk,
		fetchCorrelations,
		fetchWatchlist,
		fetchWithdrawals,
		formatUSD,
		shortAddr,
		currentScore,
		getThresholds
	} from '$lib/api.js';

	const TARGET = '0x45d26f28196d226497130c4bac709d808fed4029';

	let positions = null;
	let hip3Xyz = null;
	let index = null;
	let scan = null;
	let candidates = null;
	let fundFlows = null;
	let hlTransfers = null;
	let risk = null;
	let correlations = null;
	let closeWatch = null;
	let withdrawals = null;
	let loading = true;

	let timelineChartEl;
	let timelineChart;

	onMount(async () => {
		[positions, hip3Xyz, index, scan, candidates, fundFlows, hlTransfers, risk, correlations, closeWatch, withdrawals] = await Promise.all([
			fetchLatest('positions'),
			fetchLatest('positions_hip3_xyz'),
			fetchIndex(),
			fetchScanResults(),
			fetchCandidates(),
			fetchFundFlows(),
			fetchHlTransfers(),
			fetchRisk(),
			fetchCorrelations(),
			fetchWatchlist(),
			fetchWithdrawals(),
		]);
		loading = false;
		await new Promise(r => setTimeout(r, 0));
		const tlData = buildTimeline(index, candidates);
		if (tlData.candidateScores.some(s => s !== null)) {
			renderTimeline(tlData);
		}
	});

	function buildTimeline(idx, cands) {
		const fillDates = new Set(idx?.files?.fills || []);
		const topCandidate = (cands?.candidates || [])[0];
		const scoreHistory = topCandidate?.score_history || [];

		const end = new Date();
		const dates = [];
		for (let i = 59; i >= 0; i--) {
			const d = new Date(end);
			d.setDate(d.getDate() - i);
			dates.push(d.toISOString().split('T')[0]);
		}

		const fillActivity = dates.map(d => fillDates.has(d) ? 1 : 0);

		const scoreByDate = {};
		for (const h of scoreHistory) {
			const date = h.scan_time?.split('T')[0];
			if (date) scoreByDate[date] = Math.max(scoreByDate[date] ?? 0, h.score);
		}
		const candidateScores = dates.map(d => scoreByDate[d] ?? null);

		return { dates, fillActivity, candidateScores, wallet: topCandidate?.wallet };
	}

	function renderTimeline(data) {
		if (!timelineChartEl || !data) return;
		timelineChart?.destroy();
		timelineChart = new Chart(timelineChartEl, {
			data: {
				labels: data.dates.map(d => d.slice(5)),
				datasets: [
					{
						type: 'bar',
						label: 'Target Active',
						data: data.fillActivity,
						backgroundColor: 'rgba(0, 204, 221, 0.2)',
						borderColor: 'rgba(0, 204, 221, 0.45)',
						borderWidth: 1,
						yAxisID: 'y2',
						order: 2,
					},
					{
						type: 'line',
						label: `Top Candidate Score${data.wallet ? ' (' + data.wallet.slice(0, 8) + '...)' : ''}`,
						data: data.candidateScores,
						borderColor: 'rgba(255, 170, 0, 0.9)',
						backgroundColor: 'rgba(255, 170, 0, 0.07)',
						borderWidth: 2,
						fill: true,
						pointRadius: 2,
						tension: 0.3,
						spanGaps: true,
						yAxisID: 'y',
						order: 1,
					}
				]
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				interaction: { mode: 'index', intersect: false },
				scales: {
					x: {
						ticks: { color: 'rgba(136,136,160,0.6)', font: { size: 8, family: "'JetBrains Mono', monospace" }, maxTicksLimit: 15 },
						grid: { display: false },
						border: { display: false },
					},
					y: {
						min: 0, max: 1, position: 'left',
						ticks: { color: 'rgba(255,170,0,0.7)', font: { size: 9, family: "'JetBrains Mono', monospace" }, callback: v => (v * 100).toFixed(0) + '%' },
						grid: { color: 'rgba(42,42,74,0.3)' },
						border: { display: false },
					},
					y2: { min: 0, max: 1, position: 'right', display: false },
				},
				plugins: {
					legend: {
						labels: { color: 'rgba(136,136,160,0.8)', font: { size: 10, family: "'JetBrains Mono', monospace" }, usePointStyle: true, pointStyleWidth: 8 }
					},
					tooltip: {
						backgroundColor: 'rgba(18,18,26,0.95)',
						borderColor: 'rgba(42,42,74,0.8)',
						borderWidth: 1,
						titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
						bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
						callbacks: {
							label: ctx => {
								if (ctx.datasetIndex === 0) return ` Target: ${ctx.raw === 1 ? 'Active' : 'Silent'}`;
								return ` Candidate: ${ctx.raw != null ? (ctx.raw * 100).toFixed(1) + '%' : '—'}`;
							}
						}
					}
				}
			}
		});
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
		return all;
	}

	function getAccountValue(data) {
		if (!data) return 0;
		const ms = data.marginSummary || data?.perp?.marginSummary || {};
		return parseFloat(ms.accountValue || 0);
	}

	function getLastSeenTime() {
		const lastUpdated = index?.last_updated;
		if (!lastUpdated) return null;
		return new Date(lastUpdated).getTime();
	}

	function minutesSinceLastSeen() {
		const t = getLastSeenTime();
		if (!t) return null;
		return Math.max(0, Math.round((Date.now() - t) / 60000));
	}

	function getStatusLabel(openPositions, flowFindings) {
		if (flowFindings.some(f => f.deposited_to_hl)) return 'Linked wallet evidence';
		if (flowFindings.length > 0) return 'Funds moved';
		if (openPositions.length === 0) return 'No open positions';
		return 'Tracking live';
	}

	function getStatusClass(openPositions, flowFindings) {
		if (flowFindings.some(f => f.deposited_to_hl)) return 'text-green';
		if (flowFindings.length > 0) return 'text-yellow';
		if (openPositions.length === 0) return 'text-yellow';
		return 'text-blue';
	}

	function tierClass(tier) {
		if (tier === 'CONFIRMED_CANDIDATE') return 'badge-green';
		if (tier === 'WATCH_CLOSELY') return 'badge-yellow';
		return 'badge-blue';
	}

	function scorePct(score) {
		return score == null ? '-' : `${(score * 100).toFixed(1)}%`;
	}

	function riskClass(level) {
		if (level === 'CRITICAL') return 'risk-critical';
		if (level === 'ELEVATED') return 'risk-elevated';
		if (level === 'GUARDED') return 'risk-guarded';
		return 'risk-low';
	}

	// The band src/watchlist.py OUTGREW_TARGET alerts on. Named the same on
	// both sides so a reader of either finds the other.
	const OUTGREW_TARGET = 1.15;

	function sinceMs(ms) {
		if (!ms) return '-';
		const h = (Date.now() - ms) / 3600000;
		if (h < 1) return `${Math.max(0, Math.round(h * 60))}m`;
		if (h < 48) return `${h.toFixed(1)}h`;
		return `${(h / 24).toFixed(1)}d`;
	}

	// Rule 6 applies to the display too: a list that could not be read must
	// not render as 0, or an outage looks like an all-clear.
	function countOf(v) {
		return Array.isArray(v) ? v.length : '?';
	}

	function topLeads() {
		// Tier against the thresholds the backend actually resolved, never a
		// literal. This filtered at 0.65 — ABOVE the scorer's own self-match
		// ceiling, which is what the real trader scores against his own history
		// (0.5365 when this was found). Nothing could ever reach it, so the one
		// page you open when the trader may have migrated showed zero leads,
		// permanently, while the backend held 2 wallets above the alert threshold
		// and 27 above the low threshold.
		const th = getThresholds(scan);
		return (scan?.results || [])
			.filter(r => r.score >= th.low)
			.sort((a, b) => (b.score || 0) - (a.score || 0))
			.slice(0, 10);
	}
</script>

<div class="page-header">
	<h1>Recovery</h1>
	<p class="text-muted">Wallet continuity, fund movement, and candidate leads for <Addr address={TARGET} /></p>
</div>

{#if loading}
	<div class="loading">Loading recovery state...</div>
{:else}
	{@const openPositions = getPositions(positions, hip3Xyz)}
	{@const accountValue = getAccountValue(positions) + getAccountValue(hip3Xyz)}
	{@const flowFindings = fundFlows?.findings || []}
	{@const watchlist = candidates?.candidates || []}
	{@const linkedWallets = (hlTransfers?.counterparties || []).filter(c => c.total_out_usd > 0 || c.known_self)}
	{@const lastSeenMinutes = minutesSinceLastSeen()}

	{#if risk}
		<section class="card risk-banner {riskClass(risk.level)}">
			<div class="risk-gauge">
				<div class="risk-score-num">{Math.round(risk.score)}</div>
				<div class="risk-score-den">/100</div>
			</div>
			<div class="risk-body">
				<div class="risk-head">
					<span class="section-kicker">Unified Migration Risk</span>
					<span class="risk-level-badge {riskClass(risk.level)}">{risk.level}</span>
				</div>
				{#if (risk.factors || []).length > 0}
					<div class="risk-factors">
						{#each risk.factors.slice(0, 6) as f}
							<span class="risk-factor">{f.label} <b>+{f.points}</b></span>
						{/each}
					</div>
				{:else}
					<p class="text-muted compact">No active migration signals. Baseline monitoring.</p>
				{/if}
			</div>
		</section>
	{/if}

	{#if closeWatch?.wallets?.length}
		{@const changed = closeWatch.changes || {}}
		{@const touched = closeWatch.contacts || {}}
		<section class="card" style="margin-bottom:16px">
			<div class="panel-title">
				<div>
					<div class="section-kicker">Read Every Run</div>
					<h2>Close Watch</h2>
				</div>
				<span class="count-pill">{closeWatch.wallets.length}</span>
			</div>
			<p class="text-muted" style="font-size:0.78rem;margin-bottom:12px">
				Wallets read per-run by <span class="mono">watch.yml</span> — the operator's own list plus every roster
				CONFIRMED/PROBABLE. <strong>Size</strong> is this wallet against the target's whole account (perp, every
				HIP-3 dex and spot), both sides read by one function at the same moment; it alerts on crossing
				<span class="mono">{OUTGREW_TARGET}x</span>, a band rather than parity because both are live books.
				A <strong>contact</strong> with his world is an observed connection, not an inference.
			</p>
			<table>
				<thead>
					<tr>
						<th>Wallet</th><th>Source</th><th>Value</th><th>Size vs target</th>
						<th>Last fill</th><th>Agents</th><th>Subs</th><th>Withdrew to</th><th>EVM nonce</th>
					</tr>
				</thead>
				<tbody>
					{#each closeWatch.wallets as w}
						<tr>
							<td><Addr address={w.address} className="" /></td>
							<td class="text-muted mono" style="font-size:0.72rem">{w.source || 'config'}</td>
							<td class="mono">{w.account_value == null ? 'unreadable' : formatUSD(w.account_value)}</td>
							<td class="mono">
								{#if w.size_ratio == null}
									<span class="text-muted">unknown</span>
								{:else}
									<span class="badge" class:badge-red={w.size_ratio >= OUTGREW_TARGET}
										class:badge-yellow={w.size_ratio < OUTGREW_TARGET}>{w.size_ratio.toFixed(2)}x</span>
								{/if}
							</td>
							<td class="mono text-muted">{sinceMs(w.last_fill_ms)}</td>
							<td class="mono">{countOf(w.agents)}</td>
							<td class="mono">{countOf(w.subaccounts)}</td>
							<td class="mono">{countOf(w.withdrawal_destinations)}</td>
							<td class="mono">{w.hyperevm_nonce == null ? '?' : w.hyperevm_nonce}</td>
						</tr>
						{#if w.why}
							<tr><td colspan="9" class="text-muted watch-note">{w.why}</td></tr>
						{/if}
						{#if (touched[w.address] || []).length}
							<tr><td colspan="9" class="watch-note">
								<span class="badge badge-red">CONTACT</span>
								{#each touched[w.address] as c}
									<span class="mono" style="margin-left:6px">{shortAddr(c.address)} — {c.is}</span>
								{/each}
							</td></tr>
						{/if}
						{#if (changed[w.address] || []).length}
							<tr><td colspan="9" class="watch-note">
								{#each changed[w.address] as ch}
									<span class="badge badge-yellow" style="margin-right:6px">{ch.kind}</span><span
										class="text-muted" style="margin-right:10px">{ch.detail}</span>
								{/each}
							</td></tr>
						{/if}
						{#if w.read_ok === false}
							<tr><td colspan="9" class="text-muted watch-note">
								Partial read — this row is what we could see, not an all-clear.
							</td></tr>
						{/if}
					{/each}
				</tbody>
			</table>
			{#if (closeWatch.unreadable || []).length}
				<p class="text-muted compact" style="margin-top:8px">
					{closeWatch.unreadable.length} wallet(s) unreadable this run — unknown, not clear.
				</p>
			{/if}
		</section>
	{/if}

	{#if withdrawals?.cctp}
		{@const c = withdrawals.cctp}
		{@const foreignCount = (withdrawals.unmatched || 0) + (c.foreign || []).length}
		<section class="card" style="margin-bottom:16px">
			<div class="panel-title">
				<div>
					<div class="section-kicker">Where The Money Left</div>
					<h2>Withdrawal Pairing</h2>
				</div>
				<span class="count-pill">{(withdrawals.withdrawals || 0) + (c.withdrawals || 0)}</span>
			</div>
			<p class="text-muted" style="font-size:0.78rem;margin-bottom:12px">
				Every Hyperliquid withdrawal matched to where it actually landed. Two routes out: the Arbitrum
				<strong>bridge</strong>, and <strong>Circle/CCTP</strong> (<span class="mono">sendToEvmWithData</span>, which
				the ledger shows only as a send to <span class="mono">0x2000…0000</span> and which can name a recipient on
				any Circle chain). Anything unpaired is money leaving to an address this system does not sweep.
			</p>
			<div class="status-metrics">
				<div>
					<span class="metric-value">{withdrawals.matched_to_own_address ?? '?'}/{withdrawals.withdrawals ?? '?'}</span>
					<span class="metric-label">Bridge paired</span>
				</div>
				<div>
					<span class="metric-value">{c.matched_to_cluster_mint ?? '?'}/{c.withdrawals ?? '?'}</span>
					<span class="metric-label">Circle paired</span>
				</div>
				<div>
					<span class="metric-value">{foreignCount}</span>
					<span class="metric-label">Foreign</span>
				</div>
				<div>
					<span class="metric-value">{(c.unresolved || []).length}</span>
					<span class="metric-label">Unresolved</span>
				</div>
			</div>
			{#if (c.foreign || []).length}
				<table style="margin-top:12px">
					<thead><tr><th>Circle recipient</th><th>Chain</th><th>Amount</th></tr></thead>
					<tbody>
						{#each c.foreign as f}
							<tr>
								<td><Addr address={f.destination} className="" /></td>
								<td class="mono">{f.chain || 'unknown'}</td>
								<td class="mono">{formatUSD(f.net_usd)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>
	{/if}

	<div class="recovery-grid">
		<section class="card recovery-status">
			<div>
				<div class="section-kicker">Target Wallet</div>
				<h2 class={getStatusClass(openPositions, flowFindings)}>
					{getStatusLabel(openPositions, flowFindings)}
				</h2>
			</div>
			<div class="status-metrics">
				<div>
					<span class="metric-value">{formatUSD(accountValue)}</span>
					<span class="metric-label">Account Value</span>
				</div>
				<div>
					<span class="metric-value">{openPositions.length}</span>
					<span class="metric-label">Open Positions</span>
				</div>
				<div>
					<span class="metric-value">{lastSeenMinutes == null ? '-' : `${lastSeenMinutes}m`}</span>
					<span class="metric-label">Data Age</span>
				</div>
			</div>
		</section>

		<section class="card">
			<div class="section-kicker">Immediate Evidence</div>
			<h2>Fund Flow</h2>
			{#if flowFindings.length > 0}
				<div class="flow-list">
					{#each flowFindings.slice(0, 4) as f}
						{@const linkedCandidate = watchlist.find(c => c.wallet?.toLowerCase() === f.destination?.toLowerCase())}
						<div class="flow-item">
							<div>
								<Addr address={f.destination} className="" />
								<span class="badge" class:badge-green={f.deposited_to_hl} class:badge-yellow={!f.deposited_to_hl}>
									{f.deposited_to_hl ? 'HL deposit' : 'pending'}
								</span>
								{#if linkedCandidate}
									<span class="badge badge-red">Behavioral match {scorePct(currentScore(linkedCandidate))}</span>
								{/if}
							</div>
							<div class="text-muted mono">{f.amount_usdc || formatUSD(f.amount_usdc_raw || 0)} USDC | {f.method}{#if f.hop_count > 1} ({f.hop_count}-hop){/if}</div>
						</div>
					{/each}
				</div>
			{:else}
				<p class="text-muted compact">No outbound USDC flow has been recorded yet.</p>
			{/if}
		</section>
	</div>

	{#if linkedWallets.length > 0}
		<section class="card" style="margin-bottom:16px">
			<div class="panel-title">
				<div>
					<div class="section-kicker">In-Platform Money Trail</div>
					<h2>Linked Wallets (HL-native transfers)</h2>
				</div>
				<span class="count-pill">{linkedWallets.length}</span>
			</div>
			<p class="text-muted" style="font-size:0.78rem;margin-bottom:12px">
				Wallets the target moved funds to/from <strong>entirely inside Hyperliquid</strong> — the most likely migration path, invisible to L1 tracing. A wallet that receives large outbound funds and then starts trading is the prime new-wallet candidate.
			</p>
			<table>
				<thead>
					<tr>
						<th>Wallet</th>
						<th>Sent to</th>
						<th>Received from</th>
						<th>Link</th>
						<th>Last transfer</th>
					</tr>
				</thead>
				<tbody>
					{#each linkedWallets.slice(0, 10) as c}
						<tr>
							<td>
								<Addr address={c.wallet} className="" />
								{#if c.known_self}
									<span class="badge badge-blue" style="font-size:0.6rem">known linked</span>
								{/if}
								{#if c.bidirectional}
									<span class="badge badge-green" style="font-size:0.6rem">two-way</span>
								{/if}
							</td>
							<td class="mono">{formatUSD(c.total_out_usd)}</td>
							<td class="mono text-muted">{formatUSD(c.total_in_usd)}</td>
							<td class="mono">{c.transfer_count}×</td>
							<td class="text-muted mono" style="font-size:0.72rem">{c.last_seen?.split('T')[0] || '-'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>
	{/if}

	{#if (correlations?.matches || []).length > 0}
		<section class="card" style="margin-bottom:16px">
			<div class="panel-title">
				<div>
					<div class="section-kicker">Cross-Gap Re-Linking</div>
					<h2>Deposit / Withdrawal Correlations</h2>
				</div>
				<span class="count-pill">{correlations.matches.length}</span>
			</div>
			<p class="text-muted" style="font-size:0.78rem;margin-bottom:12px">
				Fresh wallets whose Hyperliquid deposit closely matches a target exit in <strong>amount + timing</strong> — consistent with cashing out and re-entering on a new wallet through a CEX. Odd (non-round) amounts score highest.
			</p>
			<table>
				<thead>
					<tr>
						<th>Wallet</th>
						<th>Confidence</th>
						<th>Deposit ≈ Exit</th>
						<th>Gap</th>
						<th>Exit via</th>
					</tr>
				</thead>
				<tbody>
					{#each correlations.matches.slice(0, 10) as m}
						<tr>
							<td><Addr address={m.wallet} className="" /></td>
							<td class="mono"><span class="badge" class:badge-red={m.confidence >= 0.7} class:badge-yellow={m.confidence < 0.7}>{scorePct(m.confidence)}</span></td>
							<td class="mono">{formatUSD(m.deposit_amount_usd)} ≈ {formatUSD(m.exit_amount_usd)}</td>
							<td class="mono text-muted">{m.gap_hours}h</td>
							<td class="text-muted mono" style="font-size:0.72rem">{m.exit_source}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>
	{/if}

	<div class="grid-2 main-panels">
		<section class="card">
			<div class="panel-title">
				<div>
					<div class="section-kicker">Persistent Leads</div>
					<h2>Candidate Watchlist</h2>
				</div>
				<span class="count-pill">{watchlist.length}</span>
			</div>
			{#if watchlist.length > 0}
				<table>
					<thead>
						<tr>
							<th>Wallet</th>
							<th>Best</th>
							<th>Latest</th>
							<th>Tier</th>
						</tr>
					</thead>
					<tbody>
						{#each watchlist.slice(0, 8) as c}
							<tr>
								<td>
									<Addr address={c.wallet} className="" />
									{#if c.status === 'COOLING'}
										<span class="badge badge-yellow" style="font-size:0.6rem">cooling</span>
									{/if}
								</td>
								<td class="mono">{scorePct(c.best_score)}</td>
								<td class="mono">{scorePct(c.latest_score)}</td>
								<td><span class="badge {tierClass(c.latest_tier)}">{c.latest_tier || 'WATCH'}</span></td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="text-muted compact">No candidates have crossed the watchlist threshold yet.</p>
			{/if}
		</section>

		<section class="card">
			<div class="panel-title">
				<div>
					<div class="section-kicker">Behavioral Leads</div>
					<h2>Strongest Current Matches</h2>
				</div>
				<span class="count-pill">{topLeads().length}</span>
			</div>
			{#if topLeads().length > 0}
				<div class="lead-list">
					{#each topLeads() as r}
						<div class="lead-row">
							<div class="lead-main">
								<Addr address={r.wallet} className="" />
								<span class="badge {tierClass(r.evidence?.tier)}">{r.evidence?.tier || 'LEAD'}</span>
							</div>
							<div class="lead-score mono">{scorePct(r.score)}</div>
							<div class="evidence">
								{#each (r.evidence?.reasons || []).slice(0, 3) as reason}
									<span>{reason}</span>
								{/each}
								{#if (r.evidence?.warnings || []).length > 0}
									<span class="warning">{r.evidence.warnings[0]}</span>
								{/if}
							</div>
						</div>
					{/each}
				</div>
			{:else}
				<p class="text-muted compact">No behavioral leads above the current 65% threshold.</p>
			{/if}
		</section>
	</div>

	{#if watchlist.length > 0}
		<section class="card" style="margin-bottom:16px">
			<div class="panel-title">
				<div>
					<div class="section-kicker">Migration Correlation</div>
					<h2>Target Activity vs Candidate Score</h2>
				</div>
				<span class="text-muted" style="font-size:0.72rem">last 60 days</span>
			</div>
			<p class="text-muted" style="font-size:0.78rem;margin-bottom:12px">
				Cyan bars = target active days. Orange line = top candidate similarity. Correlation between target silence and rising score indicates migration.
			</p>
			<div style="height:180px;position:relative">
				<canvas bind:this={timelineChartEl} role="img" aria-label="Candidate wallet score history over time"></canvas>
			</div>
		</section>
	{/if}

	<section class="card">
		<div class="panel-title">
			<div>
				<div class="section-kicker">Current Exposure</div>
				<h2>Open Positions</h2>
			</div>
			<span class="count-pill">{openPositions.length}</span>
		</div>
		{#if openPositions.length > 0}
			<table>
				<thead>
					<tr>
						<th>Coin</th>
						<th>Side</th>
						<th>Size</th>
						<th>Value</th>
						<th>PnL</th>
					</tr>
				</thead>
				<tbody>
					{#each openPositions as pos}
						{@const size = parseFloat(pos.szi || 0)}
						{@const pnl = parseFloat(pos.unrealizedPnl || 0)}
						<tr>
							<td><strong>{pos.coin}</strong></td>
							<td><span class="badge" class:badge-green={size > 0} class:badge-red={size < 0}>{size > 0 ? 'LONG' : 'SHORT'}</span></td>
							<td>{Math.abs(size).toFixed(4)}</td>
							<td>{formatUSD(parseFloat(pos.positionValue || 0))}</td>
							<td class:text-green={pnl >= 0} class:text-red={pnl < 0}>{pnl >= 0 ? '+' : ''}{formatUSD(pnl)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<p class="text-muted compact">No open positions in the latest snapshot.</p>
		{/if}
	</section>
{/if}

<style>
	.watch-note {
		padding-top: 0;
		border-top: none;
		font-size: 0.72rem;
	}

	.page-header { margin-bottom: 24px; }
	.page-header h1 { font-size: 1.6rem; font-weight: 700; }

	.risk-banner {
		display: flex;
		align-items: center;
		gap: 20px;
		margin-bottom: 16px;
		border-left: 4px solid var(--border);
	}
	.risk-banner.risk-critical { border-left-color: var(--accent-red, #ff4d4d); }
	.risk-banner.risk-elevated { border-left-color: var(--accent-yellow, #ffaa00); }
	.risk-banner.risk-guarded { border-left-color: var(--accent-cyan, #00ccdd); }
	.risk-banner.risk-low { border-left-color: var(--border); }

	.risk-gauge {
		display: flex;
		align-items: baseline;
		font-family: var(--font-mono);
		min-width: 92px;
	}
	.risk-score-num { font-size: 2.4rem; font-weight: 700; line-height: 1; }
	.risk-critical .risk-score-num { color: var(--accent-red, #ff4d4d); }
	.risk-elevated .risk-score-num { color: var(--accent-yellow, #ffaa00); }
	.risk-guarded .risk-score-num { color: var(--accent-cyan, #00ccdd); }
	.risk-score-den { font-size: 0.9rem; color: var(--text-muted); margin-left: 2px; }

	.risk-body { flex: 1; }
	.risk-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
	.risk-level-badge {
		font-family: var(--font-mono);
		font-size: 0.68rem;
		font-weight: 700;
		padding: 2px 8px;
		border-radius: 999px;
		letter-spacing: 0.05em;
	}
	.risk-level-badge.risk-critical { background: rgba(255,77,77,0.15); color: var(--accent-red, #ff4d4d); }
	.risk-level-badge.risk-elevated { background: rgba(255,170,0,0.15); color: var(--accent-yellow, #ffaa00); }
	.risk-level-badge.risk-guarded { background: rgba(0,204,221,0.12); color: var(--accent-cyan, #00ccdd); }
	.risk-level-badge.risk-low { background: rgba(136,136,160,0.12); color: var(--text-muted); }

	.risk-factors { display: flex; flex-wrap: wrap; gap: 6px; }
	.risk-factor {
		font-size: 0.72rem;
		color: var(--text-secondary);
		background: rgba(255,255,255,0.04);
		border-radius: 4px;
		padding: 2px 7px;
	}
	.risk-factor b { color: var(--text-primary, #e8e8f0); }
	.loading { text-align: center; padding: 60px; color: var(--text-muted); }

	.recovery-grid {
		display: grid;
		grid-template-columns: 1.3fr 1fr;
		gap: 16px;
		margin-bottom: 16px;
	}

	.recovery-status {
		display: flex;
		justify-content: space-between;
		gap: 24px;
		align-items: center;
	}

	.section-kicker {
		font-size: 0.7rem;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.08em;
		margin-bottom: 4px;
	}

	h2 {
		font-size: 1.15rem;
		margin: 0;
	}

	.status-metrics {
		display: grid;
		grid-template-columns: repeat(3, minmax(90px, 1fr));
		gap: 12px;
		min-width: 360px;
	}

	.metric-value {
		display: block;
		font-family: var(--font-mono);
		font-weight: 700;
		font-size: 1.1rem;
	}

	.metric-label {
		display: block;
		color: var(--text-muted);
		font-size: 0.7rem;
		text-transform: uppercase;
		margin-top: 2px;
	}

	.main-panels {
		margin-bottom: 16px;
	}

	.panel-title {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 14px;
	}

	.count-pill {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		color: var(--text-secondary);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 2px 8px;
	}

	.compact {
		margin-top: 12px;
		font-size: 0.9rem;
	}

	.flow-list,
	.lead-list {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.flow-item,
	.lead-row {
		border-top: 1px solid rgba(42, 42, 74, 0.7);
		padding-top: 10px;
	}

	.flow-item:first-child,
	.lead-row:first-child {
		border-top: none;
		padding-top: 0;
	}

	.flow-item > div:first-child,
	.lead-main {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.lead-row {
		display: grid;
		grid-template-columns: 1fr auto;
		gap: 6px 12px;
	}

	.evidence {
		grid-column: 1 / -1;
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.evidence span {
		font-size: 0.72rem;
		color: var(--text-secondary);
		background: rgba(255,255,255,0.04);
		border-radius: 4px;
		padding: 2px 6px;
	}

	.evidence .warning {
		color: var(--accent-yellow);
		background: rgba(255,170,0,0.1);
	}

	.lead-score {
		font-weight: 700;
		color: var(--accent-cyan);
	}

	@media (max-width: 1100px) {
		.recovery-grid,
		.grid-2 {
			grid-template-columns: 1fr;
		}
		.recovery-status {
			align-items: flex-start;
			flex-direction: column;
		}
		.status-metrics {
			min-width: 0;
			width: 100%;
		}
	}
</style>
