<script>
	import { onMount } from 'svelte';
	import { fetchIndex, fetchDaily, formatUSD, formatTime } from '$lib/api.js';

	let fills = [];
	let loading = true;
	let selectedDate = '';
	let availableDates = [];

	onMount(async () => {
		const index = await fetchIndex();
		if (index?.files?.fills) {
			availableDates = [...index.files.fills].reverse();
			if (availableDates.length > 0) {
				selectedDate = availableDates[0];
				await loadDate(selectedDate);
			}
		}
		loading = false;
	});

	async function loadDate(date) {
		loading = true;
		const data = await fetchDaily('fills', date);
		fills = Array.isArray(data) ? data.sort((a, b) => (b.time || 0) - (a.time || 0)) : [];
		loading = false;
	}

	function handleDateChange(e) {
		selectedDate = e.target.value;
		loadDate(selectedDate);
	}
</script>

<div class="page-header">
	<h1>Trade Fills</h1>
	<div style="display:flex;align-items:center;gap:12px;margin-top:8px">
		<label class="text-muted" for="date-select">Date:</label>
		<select id="date-select" value={selectedDate} on:change={handleDateChange} class="date-select">
			{#each availableDates as d}
				<option value={d}>{d}</option>
			{/each}
		</select>
		<span class="text-muted">{fills.length} fills</span>
	</div>
</div>

{#if loading}
	<div class="loading">Loading fills...</div>
{:else if fills.length === 0}
	<div class="card" style="text-align:center;padding:48px">
		<p class="text-muted">No fills data available yet. Run the collector or backfill first.</p>
	</div>
{:else}
	<div class="card">
		<table>
			<thead>
				<tr>
					<th>Time</th>
					<th>Coin</th>
					<th>Side</th>
					<th>Direction</th>
					<th>Price</th>
					<th>Size</th>
					<th>Notional</th>
					<th>Fee</th>
					<th>Closed PnL</th>
				</tr>
			</thead>
			<tbody>
				{#each fills as fill}
					{@const notional = parseFloat(fill.px || 0) * parseFloat(fill.sz || 0)}
					{@const pnl = parseFloat(fill.closedPnl || 0)}
					<tr>
						<td>{formatTime(fill.time)}</td>
						<td><strong>{fill.coin}</strong></td>
						<td>
							<span class="badge" class:badge-green={fill.side === 'B'} class:badge-red={fill.side === 'A'}>
								{fill.side === 'B' ? 'BUY' : 'SELL'}
							</span>
						</td>
						<td class="text-muted">{fill.dir || '—'}</td>
						<td>{formatUSD(parseFloat(fill.px || 0))}</td>
						<td>{parseFloat(fill.sz || 0).toFixed(4)}</td>
						<td>{formatUSD(notional)}</td>
						<td class="text-muted">{formatUSD(parseFloat(fill.fee || 0))}</td>
						<td class:text-green={pnl > 0} class:text-red={pnl < 0}>
							{pnl !== 0 ? (pnl > 0 ? '+' : '') + formatUSD(pnl) : '—'}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<style>
	.page-header { margin-bottom: 24px; }
	.page-header h1 { font-size: 1.6rem; font-weight: 700; }
	.loading { text-align: center; padding: 60px; color: var(--text-muted); }
	.date-select {
		background: var(--bg-card);
		border: 1px solid var(--border);
		color: var(--text-primary);
		padding: 6px 12px;
		border-radius: 6px;
		font-family: var(--font-mono);
		font-size: 0.85rem;
	}
</style>
