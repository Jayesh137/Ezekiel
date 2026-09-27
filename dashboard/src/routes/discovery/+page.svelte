<script>
	import { onMount } from 'svelte';
	import { base } from '$app/paths';
	import { fetchDiscovery, formatUSD, formatTime } from '$lib/api.js';
	import { discoveryView } from '$lib/discovery.js';
	import Addr from '$lib/Addr.svelte';

	let reports = {};
	let loading = true;
	let query = '';
	let filter = 'all';
	let now = Date.now();
	async function refresh() {
		loading = true;
		reports = await fetchDiscovery();
		now = Date.now();
		loading = false;
	}
	onMount(refresh);
	$: view = discoveryView(reports, now);
	$: leads = view.leads.filter(row => row.wallet.toLowerCase().includes(query.toLowerCase().trim())
		&& (filter === 'all' || (filter === 'routes' && row.hasFactualRoute)
			|| (filter === 'sparse' && (row.independent_sessions || 0) < 5)));
	const words = value => String(value).replaceAll('_', ' ');
	const date = value => value ? formatTime(value) : 'Not observed';
</script>

<svelte:head><title>Investigations · Ezekiel</title></svelte:head>

<div class="heading">
	<div><h1>Wallet investigations</h1><p class="lede">Follow the evidence to the next useful check.</p></div>
	<button class="btn" on:click={refresh} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
</div>
<p class="text-muted">These are leads for review. Shared funding, trading style and authority can each have several explanations.</p>

{#if !loading}
	<div class="metrics">
		<article><span>Public accounts observed</span><strong>{view.walletsObserved?.toLocaleString() ?? '—'}</strong><small>{view.collection.label}</small></article>
		<article><span>Last successful market read</span><strong>{view.lastSuccessAgeHours === null ? '—' : view.lastSuccessAgeHours < 1 ? '< 1h ago' : `${Math.floor(view.lastSuccessAgeHours)}h ago`}</strong><small>Attempt: {date(reports.discovery?.computed_at_ms)}</small></article>
		<article><span>Unresolved routes</span><strong>{view.unresolvedCount?.toLocaleString() ?? '—'}</strong><small>Each needs another observation</small></article>
		<article><span>Discovery evaluation</span><strong class="status-text">{view.evaluationLabel}</strong><small>Real identification accuracy remains unknown</small></article>
	</div>
	<div class="coverage">
		<p><strong>Coverage:</strong> polling samples recent trades. Activity between polls may be missed, and a missing trade does not establish inactivity.</p>
		{#if view.collection.level === 'warn'}<p class="warning">{view.collection.label}</p>{/if}
		{#each view.warnings as warning}<p class="warning">{warning}</p>{/each}
		{#if view.classifiedUsd !== null}
			<p>First destination classified: <strong>{formatUSD(view.classifiedUsd)}</strong>. Sent to configured controlled recipients: <strong>{formatUSD(view.controlledUsd)}</strong>. Classifying a bridge or exchange does not identify the next wallet.</p>
		{/if}
	</div>

	<div class="queue-heading"><h2>Next investigations</h2><a href="{base}/roster">Open full roster →</a></div>
	<p class="text-muted">Ordered by investigation priority, not probability of ownership. Built {date(reports.investigations?.computed_at_ms)}.</p>
	<div class="filters">
		<label>Find a wallet<input type="search" placeholder="Wallet address" bind:value={query} /></label>
		<label>Show<select bind:value={filter}><option value="all">All leads</option><option value="routes">Funding or authority links</option><option value="sparse">Needs more observed sessions</option></select></label>
	</div>
	{#if !reports.investigations}
		<p class="empty">No investigation report has been collected yet. The first scan will build this queue from available observations.</p>
	{:else if !leads.length}
		<p class="empty">No leads match this view. Check coverage and unresolved routes below before interpreting an empty queue.</p>
	{:else}
		<div class="leads">
			{#each leads as row, i (row.wallet)}
				<article class="lead">
					<div class="lead-title"><span class="position">{i + 1}</span>
						{#if row.group_members?.length}<span>Linked group · {row.group_members.length} accounts</span>{:else}<Addr address={row.wallet} />{/if}
						<span class="badge badge-grey">Research lead</span>
					</div>
					<p>{row.reasons.join(' · ') || 'Sparse observations; more evidence needed'}</p>
					<p class="next"><strong>Next check</strong> {row.nextCheck}</p>
					<details><summary>Evidence and alternative explanations</summary>
						{#each row.group_members || [] as wallet}<Addr address={wallet} />{/each}
						{#if row.contradictions.length}<p class="warning">Conflicting or incomplete evidence: {row.contradictions.map(words).join('; ')}</p>{/if}
						{#if row.confounders.length}<p>Alternative explanations / limits: {row.confounders.map(words).join('; ')}</p>{/if}
						{#if row.episode_similarity?.similarity != null}<p>Execution similarity: {row.episode_similarity.similarity.toFixed(2)} (research score; validation required).</p>{/if}
						{#each [...(row.return_routes || []), ...(row.funding_routes || []), ...(row.authority_links || []), ...(row.position_handoffs || [])].slice(0, 12) as evidence}
							<pre>{JSON.stringify(evidence, null, 2)}</pre>
						{/each}
						<p class="text-muted">{row.parent_event_ids.length} supporting observation references</p>
						{#if row.parent_event_ids.length}<pre>{row.parent_event_ids.slice(0, 32).join('\n')}</pre>{/if}
					</details>
				</article>
			{/each}
		</div>
	{/if}

	<h2 class="section-title">Routes awaiting a next step</h2>
	<p class="text-muted">An unresolved route stays open. Similar amounts alone cannot settle it.</p>
	{#each view.unresolved as route}
		<details class="route"><summary>{words(route.reason || 'Route unresolved')}</summary>
			<p><strong>Next query</strong></p><pre>{JSON.stringify(route.next_query || 'Inspect the source and destination transaction receipts.', null, 2)}</pre>
			<pre>{JSON.stringify(route, null, 2)}</pre>
		</details>
	{:else}<p class="empty">{reports.routes ? 'No unresolved routes in the published sample.' : 'Route history has not been collected yet.'}</p>{/each}
	<details class="section-title"><summary>Recently observed public accounts</summary>
		<p class="text-muted">Public market participation seeds discovery; it is not a connection to the target.</p>
		{#each view.discovered as row}<p><Addr address={row.wallet} /> · last observed {date(row.last_seen_ms)}</p>{/each}
		{#if !view.discovered.length}<p class="empty">No public accounts in the current sample.</p>{/if}
	</details>
{/if}

<style>
	.heading, .queue-heading, .lead-title { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
	.heading, .queue-heading { justify-content: space-between; }
	.lede { color: var(--text-secondary); margin: .4rem 0; }
	.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; margin: 1.5rem 0; }
	.metrics article, .lead, .route { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 1.15rem; }
	.metrics span, .metrics small { display: block; color: var(--text-secondary); }
	.metrics strong { display: block; font-size: 1.6rem; margin: .6rem 0; }
	.metrics .status-text { font-size: 1.05rem; }
	.coverage { border-left: 3px solid var(--accent); padding-left: 1rem; margin-bottom: 2rem; }
	p { margin: .65rem 0; }
	.warning { color: var(--accent-yellow); }
	.filters { display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }
	.filters label { display: grid; gap: .35rem; color: var(--text-secondary); flex: 1; min-width: 180px; }
	.filters input, .filters select { width: 100%; min-height: 44px; }
	.leads { display: grid; gap: .8rem; }
	.position { color: var(--text-muted); font-variant-numeric: tabular-nums; }
	.next strong { color: var(--accent); margin-right: .5rem; }
	summary { cursor: pointer; padding: .45rem 0; color: var(--text-secondary); }
	pre { max-height: 18rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg-secondary); padding: .75rem; border-radius: var(--radius-xs); font-size: .75rem; margin: .75rem 0; }
	.empty { color: var(--text-muted); padding: 1rem 0; }
	.section-title { margin-top: 2rem; }
	.route { margin: .65rem 0; }
	@media (max-width: 1050px) { .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
	@media (max-width: 540px) {
		.metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .6rem; }
		.metrics article { padding: .85rem; }
		.metrics strong { font-size: 1.3rem; }
		.metrics .status-text { font-size: 1rem; }
		.metrics span, .metrics small { font-size: .78rem; }
		.lead { padding: .9rem; } .lead-title { gap: .65rem; }
	}
</style>
