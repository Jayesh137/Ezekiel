<script>
	import { onMount } from 'svelte';
	import {
		fetchTransferGraph, fetchCandidates, fetchScanResults,
		formatUSD, shortAddr, formatTime, getThresholds
	} from '$lib/api.js';

	let graph = null;
	let candidates = null;
	let scan = null;
	let loading = true;
	let expanded = null;
	let showServices = false;

	onMount(async () => {
		[graph, candidates, scan] = await Promise.all([
			fetchTransferGraph(),
			fetchCandidates(),
			fetchScanResults()
		]);
		loading = false;
	});

	// Strongest first; services are noise and collapse behind a toggle.
	$: nodes = (graph?.nodes || []).filter(
		(n) => showServices || n.classification !== 'SERVICE'
	);
	$: thresholds = getThresholds(scan);
	$: edgesById = Object.fromEntries((graph?.edges || []).map((e) => [e.id, e]));

	$: counts = (graph?.nodes || []).reduce((acc, n) => {
		acc[n.classification] = (acc[n.classification] || 0) + 1;
		return acc;
	}, {});

	const CLASS_LABEL = {
		MIGRATION_CANDIDATE: 'Migration candidate',
		POSSIBLE_LINKED_WALLET: 'Possible linked wallet',
		OPERATIONAL_COUNTERPARTY: 'Operational counterparty',
		DIRECT_RECIPIENT: 'Direct recipient',
		SERVICE: 'Exchange / bridge / service'
	};

	const CLASS_BADGE = {
		MIGRATION_CANDIDATE: 'badge-red',
		POSSIBLE_LINKED_WALLET: 'badge-yellow',
		OPERATIONAL_COUNTERPARTY: 'badge-cyan',
		DIRECT_RECIPIENT: 'badge-grey',
		SERVICE: 'badge-grey'
	};

	/** Behavioural watchlist score for a wallet, if the scanner has one. */
	function behaviouralScore(wallet) {
		const c = (candidates?.candidates || []).find(
			(x) => x.wallet?.toLowerCase() === wallet?.toLowerCase()
		);
		return c ? c.best_score : null;
	}

	function edgesFor(node) {
		return (node.edge_ids || [])
			.map((id) => edgesById[id])
			.filter(Boolean)
			.sort((a, b) => (b.ts || 0) - (a.ts || 0));
	}

	function toggle(wallet) {
		expanded = expanded === wallet ? null : wallet;
	}
</script>

<svelte:head><title>Transfers — Ezekiel</title></svelte:head>

<div class="page">
	<header class="page-head">
		<h1>Transfer Graph</h1>
		<p class="text-muted sub">
			Every transfer in and out of the target, normalised across Arbitrum and
			Hyperliquid, walked outward to find related wallets. A transfer is a
			relationship, not proof of ownership — classification is graded and every
			conclusion keeps its evidence.
		</p>
	</header>

	{#if loading}
		<div class="card">Loading transfer graph…</div>
	{:else if !graph}
		<div class="card">
			<strong>No transfer graph yet.</strong>
			<p class="text-muted">
				It is built by the trace workflow (<code>src/transfer_graph.py</code>).
				Once that has run, discovered wallets appear here.
			</p>
		</div>
	{:else}
		<div class="stat-row">
			<div class="stat">
				<div class="stat-label">Wallets found</div>
				<div class="stat-value mono">{graph.node_count}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Transfers</div>
				<div class="stat-value mono">{graph.edge_count}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Max depth</div>
				<div class="stat-value mono">{graph.max_depth_reached}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Services excluded</div>
				<div class="stat-value mono">{graph.service_count}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Migration candidates</div>
				<div class="stat-value mono" class:text-red={counts.MIGRATION_CANDIDATE > 0}>
					{counts.MIGRATION_CANDIDATE || 0}
				</div>
			</div>
		</div>

		<div class="toolbar">
			<span class="text-muted">
				Updated {graph.computed_at ? new Date(graph.computed_at).toLocaleString() : '—'}
			</span>
			<label class="toggle">
				<input type="checkbox" bind:checked={showServices} />
				Show exchange / bridge addresses
			</label>
		</div>

		{#if !nodes.length}
			<div class="card">No related wallets discovered yet.</div>
		{/if}

		{#each nodes as n (n.wallet)}
			{@const bScore = behaviouralScore(n.wallet)}
			<div class="card node" class:is-candidate={n.classification === 'MIGRATION_CANDIDATE'}>
				<button class="node-head" onclick={() => toggle(n.wallet)}>
					<div class="node-id">
						<span class="badge {CLASS_BADGE[n.classification]}">
							{CLASS_LABEL[n.classification] || n.classification}
						</span>
						<span class="mono addr">{shortAddr(n.wallet)}</span>
						{#if n.multi_hop}<span class="badge badge-grey">{n.depth} hops</span>{/if}
						{#each n.chains as c}<span class="badge badge-grey">{c}</span>{/each}
					</div>
					<div class="node-conf">
						<span class="text-muted">confidence</span>
						<strong class="mono">{(n.confidence * 100).toFixed(0)}%</strong>
						<span class="chev">{expanded === n.wallet ? '▾' : '▸'}</span>
					</div>
				</button>

				<div class="meter" aria-hidden="true">
					<div class="meter-fill" style="width:{Math.max(2, n.confidence * 100)}%"></div>
				</div>

				<div class="path mono">
					{#each n.path as hop, i}
						{#if i > 0}<span class="arrow">→</span>{/if}<span
							class="hop"
							class:hop-target={i === 0}>{shortAddr(hop)}</span>
					{/each}
				</div>

				<div class="flows">
					<span>Received from target <strong class="mono"
							>{formatUSD(n.totals.received_from_target_usd)}</strong
						></span>
					<span>Sent to target <strong class="mono"
							>{formatUSD(n.totals.sent_to_target_usd)}</strong
						></span>
					<span>Transfers <strong class="mono">{n.totals.edge_count}</strong></span>
					{#if bScore != null}
						<span>
							Behavioural
							<strong
								class="mono"
								class:text-green={bScore >= thresholds.high}
								class:text-yellow={bScore >= thresholds.medium && bScore < thresholds.high}
								>{(bScore * 100).toFixed(0)}%</strong
							>
							{#if bScore >= thresholds.high}
								<span class="badge badge-red">alerting tier</span>
							{:else if bScore >= thresholds.low}
								<span class="badge badge-cyan">watchlist</span>
							{/if}
						</span>
					{:else}
						<span class="text-muted">Not on behavioural watchlist</span>
					{/if}
					{#if n.evidence?.trades_on_hl}
						<span class="badge badge-yellow">trading on HL</span>
					{/if}
				</div>

				<ul class="reasons">
					{#each n.confidence_reasons as r}
						<li>{r}</li>
					{/each}
				</ul>

				{#if expanded === n.wallet}
					<div class="detail">
						<div class="detail-meta">
							<span>First seen <span class="mono">{n.first_seen || '—'}</span></span>
							<span>Last seen <span class="mono">{n.last_seen || '—'}</span></span>
							<span>Discovered via <span class="mono"
									>{(n.discovery_sources || []).join(', ')}</span
								></span>
						</div>
						<h4>Transfers ({edgesFor(n).length})</h4>
						<div class="table-scroll">
							<table>
								<thead>
									<tr>
										<th>Time</th><th>Chain</th><th>Asset</th>
										<th class="num">Amount</th><th>Direction</th><th>Reference</th>
									</tr>
								</thead>
								<tbody>
									{#each edgesFor(n).slice(0, 40) as e}
										<tr>
											<td class="mono">{e.ts ? formatTime(e.ts * 1000) : '—'}</td>
											<td>{e.chain}</td>
											<td class="mono">{e.asset}</td>
											<td class="num mono">{formatUSD(e.amount_usd)}</td>
											<td class="mono dir"
												>{shortAddr(e.src)} → {shortAddr(e.dst)}</td
											>
											<td class="mono ref">{e.ref ? shortAddr(e.ref) : '—'}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
						{#if edgesFor(n).length > 40}
							<p class="text-muted">Showing 40 of {edgesFor(n).length} transfers.</p>
						{/if}
						{#if n.classification === 'DIRECT_RECIPIENT' || n.classification === 'OPERATIONAL_COUNTERPARTY'}
							<p class="caveat">
								A transfer relationship is not proof of common ownership. This wallet
								is a lead for review, not an identification.
							</p>
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	{/if}
</div>

<style>
	.page {
		max-width: 1100px;
	}
	.page-head h1 {
		margin: 0 0 4px;
	}
	.sub {
		max-width: 70ch;
		font-size: 0.85rem;
		line-height: 1.5;
		margin: 0 0 20px;
	}
	.stat-row {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		margin-bottom: 16px;
	}
	.stat {
		flex: 1 1 130px;
		background: var(--bg-card, #12121a);
		border: 1px solid var(--border, #2a2a4a);
		border-radius: 8px;
		padding: 10px 14px;
	}
	.stat-label {
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-muted, #8888a0);
	}
	.stat-value {
		font-size: 1.35rem;
		font-weight: 600;
	}
	.toolbar {
		display: flex;
		justify-content: space-between;
		align-items: center;
		flex-wrap: wrap;
		gap: 10px;
		margin-bottom: 12px;
		font-size: 0.75rem;
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
		color: var(--text-muted, #8888a0);
	}
	.node {
		margin-bottom: 12px;
	}
	.node.is-candidate {
		border-color: var(--accent-red, #ef4444);
	}
	.node-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
		width: 100%;
		background: none;
		border: 0;
		padding: 0;
		color: inherit;
		font: inherit;
		cursor: pointer;
		text-align: left;
		flex-wrap: wrap;
	}
	.node-id {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.addr {
		font-size: 0.9rem;
		font-weight: 600;
	}
	.node-conf {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.8rem;
	}
	.chev {
		color: var(--text-muted, #8888a0);
	}
	.meter {
		height: 3px;
		background: var(--border, #2a2a4a);
		border-radius: 2px;
		margin: 10px 0;
		overflow: hidden;
	}
	.meter-fill {
		height: 100%;
		background: var(--accent-cyan, #00ccdd);
	}
	.is-candidate .meter-fill {
		background: var(--accent-red, #ef4444);
	}
	.path {
		font-size: 0.75rem;
		margin-bottom: 8px;
		overflow-x: auto;
		white-space: nowrap;
		padding-bottom: 2px;
	}
	.hop-target {
		color: var(--accent-cyan, #00ccdd);
	}
	.arrow {
		color: var(--text-muted, #8888a0);
		margin: 0 6px;
	}
	.flows {
		display: flex;
		flex-wrap: wrap;
		gap: 16px;
		font-size: 0.75rem;
		margin-bottom: 8px;
		align-items: center;
	}
	.reasons {
		margin: 0;
		padding-left: 18px;
		font-size: 0.75rem;
		color: var(--text-secondary, #b0b0c8);
	}
	.reasons li {
		margin-bottom: 2px;
	}
	.detail {
		margin-top: 14px;
		padding-top: 12px;
		border-top: 1px solid var(--border, #2a2a4a);
	}
	.detail-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 16px;
		font-size: 0.7rem;
		color: var(--text-muted, #8888a0);
		margin-bottom: 10px;
	}
	.detail h4 {
		margin: 0 0 8px;
		font-size: 0.8rem;
	}
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.7rem;
	}
	th,
	td {
		text-align: left;
		padding: 5px 8px;
		border-bottom: 1px solid var(--border, #2a2a4a);
		white-space: nowrap;
	}
	th {
		color: var(--text-muted, #8888a0);
		font-weight: 500;
		text-transform: uppercase;
		font-size: 0.6rem;
		letter-spacing: 0.05em;
	}
	.num {
		text-align: right;
	}
	.dir,
	.ref {
		color: var(--text-secondary, #b0b0c8);
	}
	.caveat {
		font-size: 0.72rem;
		color: var(--text-muted, #8888a0);
		border-left: 2px solid var(--border, #2a2a4a);
		padding-left: 10px;
		margin: 12px 0 0;
	}
	.badge {
		display: inline-block;
		padding: 2px 7px;
		border-radius: 4px;
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 600;
	}
	.badge-red {
		background: rgba(239, 68, 68, 0.15);
		color: #ef4444;
	}
	.badge-yellow {
		background: rgba(245, 158, 11, 0.15);
		color: #f59e0b;
	}
	.badge-cyan {
		background: rgba(0, 204, 221, 0.15);
		color: #00ccdd;
	}
	.badge-grey {
		background: rgba(136, 136, 160, 0.15);
		color: #8888a0;
	}
	.text-red {
		color: #ef4444;
	}
	.text-green {
		color: #10b981;
	}
	.text-yellow {
		color: #f59e0b;
	}
	@media (max-width: 640px) {
		.node-head {
			align-items: flex-start;
		}
	}
</style>
