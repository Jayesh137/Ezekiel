<script>
	import { onMount } from 'svelte';
	import { fetchLatest, fetchIndex, fetchDaily, formatUSD, shortAddr, formatTime } from '$lib/api.js';

	let transactions = [];
	let findings = null;
	let loading = true;

	onMount(async () => {
		const [scanData, index] = await Promise.all([
			fetchLatest('scans'),
			fetchIndex(),
		]);

		findings = scanData?.fund_trace_findings || [];

		if (index?.files?.l1_transactions) {
			const dates = index.files.l1_transactions;
			const latest = dates[dates.length - 1];
			if (latest) {
				const data = await fetchDaily('l1_transactions', latest);
				transactions = Array.isArray(data) ? data : [];
			}
		}
		loading = false;
	});
</script>

<div class="page-header">
	<h1>Fund Flow Tracing</h1>
	<p class="text-muted">Monitoring GCR's Arbitrum L1 transactions for wallet migrations</p>
</div>

{#if loading}
	<div class="loading">Loading fund flow data...</div>
{:else}
	{#if findings && findings.length > 0}
		<div class="card" style="margin-bottom:24px;border-color:var(--accent-red)">
			<h2 style="margin-bottom:12px;font-size:1.1rem;color:var(--accent-red)">Fund Trace Findings</h2>
			{#each findings as f}
				<div class="finding">
					<div class="grid-2">
						<div>
							<span class="stat-label">Source</span>
							<div class="mono">{shortAddr(f.source)}</div>
						</div>
						<div>
							<span class="stat-label">Destination</span>
							<div class="mono text-yellow">{shortAddr(f.destination)}</div>
						</div>
						<div>
							<span class="stat-label">Amount</span>
							<div class="mono">{formatUSD(f.amount_usdc)}</div>
						</div>
						<div>
							<span class="stat-label">Method</span>
							<div><span class="badge badge-red">{f.method}</span></div>
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}

	{#if transactions.length > 0}
		<div class="card">
			<h2 style="margin-bottom:16px;font-size:1.1rem">Recent L1 Transactions</h2>
			<table>
				<thead>
					<tr>
						<th>Block</th>
						<th>From</th>
						<th>To</th>
						<th>Value</th>
						<th>TX Hash</th>
					</tr>
				</thead>
				<tbody>
					{#each transactions.slice(0, 50) as tx}
						{@const value = parseInt(tx.value || 0) / 1e6}
						<tr>
							<td class="text-muted">{tx.blockNumber}</td>
							<td>{shortAddr(tx.from)}</td>
							<td>{shortAddr(tx.to)}</td>
							<td>{formatUSD(value)}</td>
							<td>
								<a href="https://arbiscan.io/tx/{tx.hash}" target="_blank" class="text-blue">
									{shortAddr(tx.hash)}
								</a>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else}
		<div class="card" style="text-align:center;padding:48px">
			<p class="text-muted">No L1 transactions recorded yet. Run the tracer workflow.</p>
		</div>
	{/if}
{/if}

<style>
	.page-header { margin-bottom: 24px; }
	.page-header h1 { font-size: 1.6rem; font-weight: 700; }
	.loading { text-align: center; padding: 60px; color: var(--text-muted); }
	.finding {
		padding: 16px;
		background: rgba(255,51,85,0.05);
		border-radius: 8px;
		margin-bottom: 12px;
	}
</style>
