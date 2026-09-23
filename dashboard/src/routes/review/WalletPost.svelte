<script>
	// One roster wallet as a feed post. The full address is the biggest tap
	// target on the card and opens Hypurrscan; opening it counts as reviewing,
	// because looking at the wallet IS the review.
	import { createEventDispatcher } from 'svelte';
	import { addressUrl, formatUSD, shortAddr } from '$lib/api.js';
	import { TIER_LABEL, vectorLabel, bornLabel } from '$lib/review.js';
	import Avatar from '$lib/ui/Avatar.svelte';
	import Pips from '$lib/ui/Pips.svelte';

	/** A feed row: a roster wallet plus its live `status`. */
	export let w;
	/** The tier it had when you last reviewed it, for "was X". */
	export let reviewedTier = null;

	const dispatch = createEventDispatcher();

	$: href = addressUrl(w.wallet);
	$: ev = w.evidence || {};
	$: totals = ev.totals || {};
	$: unread = w.status !== 'reviewed';
	$: count = w.vector_count || 0;
	$: chains = Array.isArray(ev.chains) ? ev.chains.length : null;

	const opened = () => dispatch('reviewed', w);
</script>

<article class="post" class:unread aria-label="Wallet {w.wallet}">
	<header>
		<Avatar address={w.wallet} size={42} />
		<div class="who">
			<span class="mono short">{shortAddr(w.wallet)}</span>
			<span class="meta">
				{w.known_self ? 'his (config) · ' : ''}HL born {bornLabel(ev.hl_birth_ms)}
			</span>
		</div>
		<div class="tags">
			<span class="tier tier-{String(w.tier).toLowerCase()}">{TIER_LABEL[w.tier] || w.tier}</span>
			{#if w.status === 'new'}
				<span class="flag new">New</span>
			{:else if w.status === 'changed'}
				<span class="flag changed">was {TIER_LABEL[reviewedTier] || reviewedTier}</span>
			{/if}
		</div>
	</header>

	<div class="evidence">
		<Pips {count} />
		<span>
			{count} independent vector{count === 1 ? '' : 's'}
			{#if w.tier_dropped_from}<span class="peak">· peaked {TIER_LABEL[w.tier_dropped_from] || w.tier_dropped_from}</span>{/if}
		</span>
	</div>
	{#if w.vectors?.length}
		<div class="chips">
			{#each w.vectors as v}<span class="chip">{vectorLabel(v)}</span>{/each}
		</div>
	{/if}

	<dl class="stats">
		<div><dt>HL value</dt><dd>{formatUSD(ev.hl_account_value)}</dd></div>
		<div><dt>From him</dt><dd>{formatUSD(totals.received_from_target_usd)}</dd></div>
		<div><dt>To him</dt><dd>{formatUSD(totals.sent_to_target_usd)}</dd></div>
		<div><dt>Chains</dt><dd>{chains ?? '—'}</dd></div>
	</dl>

	{#if href}
		<a class="address mono" {href} target="_blank" rel="noopener noreferrer" on:click={opened}>
			{w.wallet}<span class="ext" aria-hidden="true"> ↗</span>
		</a>
	{:else}
		<span class="address mono plain">{w.wallet}</span>
	{/if}

	<footer>
		{#if href}
			<a class="btn primary" {href} target="_blank" rel="noopener noreferrer" on:click={opened}>Hypurrscan ↗</a>
		{/if}
		<button class="btn" on:click={() => dispatch('copy', w.wallet)}>Copy</button>
		<button class="btn" on:click={() => dispatch('why', w)}>Why?</button>
		<button class="btn done" class:is-done={!unread} disabled={!unread} on:click={() => dispatch('reviewed', w)}>
			{unread ? 'Mark ✓' : 'Reviewed'}
		</button>
	</footer>
</article>

<style>
	.post {
		position: relative;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		padding: 14px 14px 12px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.post.unread {
		border-color: color-mix(in srgb, var(--accent-cyan) 45%, var(--border));
	}
	.post.unread::before {
		content: '';
		position: absolute;
		left: -5px;
		top: 29px;
		width: 10px;
		height: 10px;
		border-radius: 50%;
		background: var(--accent-cyan);
		box-shadow: 0 0 0 3px var(--bg-primary);
	}
	header {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.who {
		display: flex;
		flex-direction: column;
		min-width: 0;
		flex: 1;
	}
	.short {
		font-weight: 600;
		font-size: 0.95rem;
	}
	.meta {
		font-size: 0.76rem;
		color: var(--text-muted);
	}
	.tags {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 4px;
	}
	.tier,
	.flag {
		font-size: 0.68rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		padding: 3px 8px;
		border-radius: 999px;
		white-space: nowrap;
	}
	.tier-confirmed { background: color-mix(in srgb, var(--accent-red) 15%, transparent); color: var(--accent-red); }
	.tier-probable { background: color-mix(in srgb, var(--accent-yellow) 15%, transparent); color: var(--accent-yellow); }
	.tier-possible { background: color-mix(in srgb, var(--accent-cyan) 15%, transparent); color: var(--accent-cyan); }
	.tier-watch { background: color-mix(in srgb, var(--accent-grey) 15%, transparent); color: var(--text-secondary); }
	.flag.new { background: var(--accent-cyan); color: var(--bg-primary); }
	.flag.changed { background: var(--accent-yellow); color: var(--bg-primary); }
	.evidence {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.84rem;
		color: var(--text-secondary);
	}
	.peak { color: var(--accent-yellow); }
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chip {
		font-size: 0.76rem;
		padding: 4px 10px;
		border-radius: 999px;
		background: var(--bg-secondary);
		border: 1px solid var(--border);
		color: var(--text-secondary);
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 8px;
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
		font-size: 0.84rem;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.address {
		display: block;
		padding: 11px 12px;
		min-height: 44px;
		box-sizing: border-box;
		background: var(--bg-secondary);
		border-radius: var(--radius-md);
		/* 0.72rem fits all 42 characters on one line at iPhone width. */
		font-size: 0.72rem;
		line-height: 1.6;
		word-break: break-all;
		color: var(--accent-blue);
		text-decoration: none;
	}
	.address.plain { color: var(--text-secondary); }
	.ext { color: var(--text-muted); }
	footer {
		display: grid;
		grid-template-columns: 1.5fr 1fr 1fr 1.2fr;
		gap: 6px;
	}
	.btn {
		min-height: 44px;
		display: grid;
		place-items: center;
		padding: 0 6px;
		box-sizing: border-box;
		border-radius: var(--radius-md);
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-primary);
		font: inherit;
		font-size: 0.84rem;
		font-weight: 600;
		text-decoration: none;
		cursor: pointer;
		white-space: nowrap;
		transition: transform var(--dur-fast) var(--ease-out);
	}
	.btn:active { transform: scale(0.97); }
	.btn.primary {
		background: var(--accent-cyan);
		border-color: var(--accent-cyan);
		color: var(--bg-primary);
	}
	.btn.is-done {
		color: var(--accent-green);
		border-color: transparent;
		cursor: default;
	}
	.btn:disabled:active { transform: none; }
</style>
