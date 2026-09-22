<script>
	// Sheet body for one close-watch wallet. An unreadable read says so, and is
	// never rendered as a card of dashes that looks like an empty wallet (rule 5).
	import Addr from '$lib/Addr.svelte';
	import { formatUSD, formatTime } from '$lib/api.js';
	import { ratio, ago, SIZE_BAND } from '$lib/review.js';

	export let w;
	export let now = Date.now();

	$: big = typeof w.size_ratio === 'number' && w.size_ratio >= SIZE_BAND;
	$: agents = w.agents || [];
	$: subs = (w.subaccounts || []).filter(Boolean);
</script>

<div class="detail">
	<Addr address={w.address} full className="mono addr" />

	{#if !w.read_ok}
		<p class="bad">Could not read this wallet on the last run.</p>
		{#if w.errors?.length}
			<ul class="errors">{#each w.errors as e}<li>{e}</li>{/each}</ul>
		{/if}
	{:else}
		<dl>
			<div><dt>Value</dt><dd>{formatUSD(w.account_value)}</dd></div>
			<div>
				<dt>vs his size</dt>
				<dd class:bad={big}>{ratio(w.size_ratio)}{#if big} · outgrew him{/if}</dd>
			</div>
			<div><dt>Last fill</dt><dd title={formatTime(w.last_fill_ms)}>{ago(w.last_fill_ms, now)}</dd></div>
			<div><dt>Watched because</dt><dd>{w.source === 'config' ? 'you named it' : w.source === 'roster' ? 'roster tier' : w.source || '—'}</dd></div>
		</dl>

		<h3>Agents <span class="n">{agents.length}</span></h3>
		{#each agents as a}<Addr address={a} full className="mono addr small" />{/each}

		<h3>Sub-accounts <span class="n">{subs.length}</span></h3>
		{#each subs as s}<Addr address={s} full className="mono addr small" />{/each}
	{/if}

	{#if w.why}
		<h3>Why it is watched</h3>
		<p class="why">{w.why}</p>
	{/if}
</div>

<style>
	.detail {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.detail :global(.addr) {
		display: block;
		word-break: break-all;
		padding: 11px 12px;
		min-height: 44px;
		box-sizing: border-box;
		background: var(--bg-secondary);
		border-radius: var(--radius-md);
		font-size: 0.8rem;
		line-height: 1.5;
	}
	.detail :global(.addr.small) {
		min-height: 0;
		padding: 8px 12px;
		font-size: 0.72rem;
	}
	dl {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
		margin: 0;
	}
	dt {
		font-size: 0.66rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-muted);
	}
	dd {
		margin: 2px 0 0;
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}
	h3 {
		font-size: 0.78rem;
		margin: 6px 0 0;
		color: var(--text-secondary);
	}
	.n {
		color: var(--text-muted);
		font-weight: 500;
		font-variant-numeric: tabular-nums;
	}
	.why {
		font-size: 0.85rem;
		line-height: 1.55;
		color: var(--text-secondary);
		margin: 0;
	}
	.bad { color: var(--accent-red); }
	.errors {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin: 0;
		padding-left: 18px;
	}
</style>
