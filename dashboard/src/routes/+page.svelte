<script>
	import { onMount } from 'svelte';
	import { fetchLatest, fetchFingerprint, fetchIndex, formatUSD, shortAddr } from '$lib/api.js';

	let positions = null;
	let spot = null;
	let fingerprint = null;
	let index = null;
	let loading = true;

	onMount(async () => {
		[positions, spot, fingerprint, index] = await Promise.all([
			fetchLatest('positions'),
			fetchLatest('spot'),
			fetchFingerprint(),
			fetchIndex(),
		]);
		loading = false;
	});

	function getPositions(data) {
		if (!data) return [];
		const ap = data.assetPositions || data?.perp?.assetPositions || [];
		return ap
			.map(a => a.position)
			.filter(p => p && parseFloat(p.szi) !== 0);
	}

	function getSpotBalances(data) {
		if (!data) return [];
		const balances = data.balances || [];
		return balances.filter(b => parseFloat(b.total || b.hold || 0) > 0);
	}

	function getAccountValue(data) {
		if (!data) return 0;
		const ms = data.marginSummary || data?.perp?.marginSummary || {};
		return parseFloat(ms.accountValue || 0);
	}

	function getTotalPnl(positions) {
		return positions.reduce((sum, p) => sum + parseFloat(p.unrealizedPnl || 0), 0);
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
	{@const openPositions = getPositions(positions)}
	{@const accountValue = getAccountValue(positions)}
	{@const totalPnl = getTotalPnl(openPositions)}

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
			<div class="stat-value text-purple">{index?.stats?.total_fills ?? '—'}</div>
			<div class="stat-label">Total Fills</div>
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
</style>
