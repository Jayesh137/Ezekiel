<script>
	import { onMount } from 'svelte';
	import { fetchRoster, formatUSD, formatTime } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';

	let roster = null;
	let loading = true;
	let showInfrastructure = false;
	let expanded = null;

	onMount(async () => {
		roster = await fetchRoster();
		loading = false;
	});

	// Infrastructure is the bulk of the list and none of it is a candidate, so it
	// collapses behind a toggle rather than burying the wallets that matter.
	$: rows = (roster?.wallets || []).filter(
		(w) => showInfrastructure || w.tier !== 'INFRASTRUCTURE'
	);

	const TIER_LABEL = {
		CONFIRMED: 'Confirmed',
		PROBABLE: 'Probable',
		POSSIBLE: 'Possible',
		WATCH: 'Watch',
		INFRASTRUCTURE: 'Exchange / bridge / service'
	};

	const TIER_BADGE = {
		CONFIRMED: 'badge-red',
		PROBABLE: 'badge-yellow',
		POSSIBLE: 'badge-cyan',
		WATCH: 'badge-grey',
		INFRASTRUCTURE: 'badge-grey'
	};

	// Each vector is a different way of being right, so the label says what was
	// actually observed rather than repeating a score.
	const VECTOR_LABEL = {
		transfer: 'Observed transfer',
		linkage: 'Shared funder / deposit address',
		correlation: 'Exit amount re-appeared as a deposit',
		behavioural: 'Trades like the target',
		hl_native: 'Two-way flow inside Hyperliquid'
	};

	function toggle(wallet) {
		expanded = expanded === wallet ? null : wallet;
	}
</script>

<svelte:head><title>Roster · Ezekiel</title></svelte:head>

<h1>Wallet roster</h1>
<p class="lede">
	Every detector's output merged into one list, tiered on <strong>how many
	independent vectors agree</strong> — not on any single score. Independent
	agreement is the strongest evidence here, because the ways the vectors can be
	fooled do not overlap.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else if !roster}
	<p class="text-muted">No roster yet. It is built by <code>src/roster.py</code> on the trace job.</p>
{:else}
	<div class="tiers">
		{#each Object.entries(roster.tier_counts || {}) as [tier, count]}
			<span class="badge {TIER_BADGE[tier] || 'badge-grey'}">
				{TIER_LABEL[tier] || tier}: {count}
			</span>
		{/each}
	</div>

	{#if roster.behavioural_counts_as_a_vector === false}
		<p class="caveat">
			The behavioural scorer has not passed its self-match backtest, so trading
			style is shown as context but does <strong>not</strong> count as a vector.
			A scorer that cannot pick the target out of a lineup is not evidence that
			some other wallet is him.
		</p>
	{/if}

	<label class="toggle">
		<input type="checkbox" bind:checked={showInfrastructure} />
		Show infrastructure ({(roster.tier_counts || {}).INFRASTRUCTURE || 0})
	</label>

	<table>
		<thead>
			<tr>
				<th>Wallet</th>
				<th>Tier</th>
				<th class="num">Vectors</th>
				<th class="num" title="Transfer-graph confidence. A wallet the graph never scored has no reading here and shows — , never 0%.">Confidence</th>
				<th class="num" title="Chase priority: the best score any vector gives this wallet. Different scales, so it orders the queue and is not a confidence.">Strength</th>
				<th>Supported by</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as w (w.wallet)}
				<tr class="row" on:click={() => toggle(w.wallet)}>
					<td><Addr addr={w.wallet} /></td>
					<td><span class="badge {TIER_BADGE[w.tier] || 'badge-grey'}">{TIER_LABEL[w.tier] || w.tier}</span></td>
					<td class="num">{w.vector_count}</td>
					<!-- Rule 6 in the UI: `confidence` is produced by the transfer graph
					     alone, so a wallet reached by correlation, dormancy or an explicit
					     link has no reading here. Rendering that as 0% prices a missing
					     value as a measured zero, and put a 0.9974 correlation lead on
					     screen showing "0%" at rank 2. -->
					<td class="num">{w.confidence ? (w.confidence * 100).toFixed(0) + '%' : '—'}</td>
					<td class="num">{w.rank_strength ? (w.rank_strength * 100).toFixed(0) + '%' : '—'}</td>
					<td class="vectors">
						{#each w.vectors as v}<span class="chip">{VECTOR_LABEL[v] || v}</span>{/each}
						{#if !w.vectors.length}<span class="text-muted">—</span>{/if}
					</td>
				</tr>
				{#if expanded === w.wallet}
					<tr class="detail">
						<td colspan="6">
							{#if w.known_self}
								<p><strong>Known wallet of the target</strong> (from config, operator ground truth).</p>
							{/if}
							{#if w.evidence?.service_reason}
								<p class="text-muted">Excluded as infrastructure: {w.evidence.service_reason}</p>
							{/if}
							{#if w.reasons?.length}
								<ul>{#each w.reasons as r}<li>{r}</li>{/each}</ul>
							{/if}
							{#if w.evidence?.totals?.received_from_target_usd !== undefined}
								<p>
									Received from target: {formatUSD(w.evidence.totals.received_from_target_usd)} ·
									Sent to target: {formatUSD(w.evidence.totals.sent_to_target_usd)} ·
									Transfers: {w.evidence.totals.edge_count ?? 0}
								</p>
							{/if}
							{#if w.evidence?.correlation_confidence}
								<p>
									Amount correlation {(w.evidence.correlation_confidence * 100).toFixed(0)}%,
									gap {w.evidence.correlation_gap_hours}h,
									competing deposits {w.evidence.competing_deposits ?? '?'}
								</p>
							{/if}
							{#if w.evidence?.behavioural_score}
								<p>
									Behavioural similarity {(w.evidence.behavioural_score * 100).toFixed(0)}%
									{#if w.evidence.style_vetoes?.length}
										— <strong>style veto:</strong> {w.evidence.style_vetoes.join('; ')}
									{/if}
								</p>
							{/if}
							{#if w.evidence?.hyperevm_nonce !== undefined && w.evidence?.hyperevm_nonce !== null}
								<p class="text-muted">
									HyperEVM transactions sent: {w.evidence.hyperevm_nonce}
								</p>
							{/if}
							<p class="caveat">
								A tier is a lead for review, not an identification. Only the target
								wallet itself is known with certainty.
							</p>
						</td>
					</tr>
				{/if}
			{/each}
		</tbody>
	</table>

	<p class="text-muted">
		{roster.wallet_count} wallet(s) · built {formatTime(roster.computed_at)}
	</p>
{/if}

<style>
	.lede { max-width: 60rem; }
	.tiers { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0; }
	.toggle { display: block; margin: 0.75rem 0; font-size: 0.9rem; }
	table { width: 100%; border-collapse: collapse; }
	th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border, #222); }
	th.num, td.num { text-align: right; }
	.row { cursor: pointer; }
	.row:hover { background: rgba(255,255,255,0.03); }
	.vectors { display: flex; gap: 0.3rem; flex-wrap: wrap; }
	.chip {
		font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 0.25rem;
		background: rgba(255,255,255,0.06); white-space: nowrap;
	}
	.detail td { background: rgba(255,255,255,0.02); }
	.detail ul { margin: 0.3rem 0 0.5rem 1.1rem; }
	.caveat { font-size: 0.85rem; opacity: 0.75; }
</style>
