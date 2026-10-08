<script>
	// One casebook suspect as a feed post on the phone's Ranked tab. Read-only: the
	// address opens Hypurrscan, and Copy hands the address to the page's copy helper.
	import { createEventDispatcher } from 'svelte';
	import { addressUrl, shortAddr } from '$lib/api.js';
	import { pct, bandText, familyChips, hlLine } from '$lib/casebook.js';
	import Avatar from '$lib/ui/Avatar.svelte';

	export let c;
	export let fresh = false;

	const dispatch = createEventDispatcher();
	$: href = addressUrl(c.address);
	$: chips = familyChips(c).slice(0, 3);
</script>

<article class="post">
	<header>
		<Avatar address={c.address} size={42} />
		<div class="who">
			{#if href}
				<a class="address mono" {href} target="_blank" rel="noopener noreferrer">{shortAddr(c.address)}</a>
			{:else}
				<span class="address mono">{shortAddr(c.address)}</span>
			{/if}
			<span class="rank">#{c.rank}{#if c.cluster_size > 1} · one of {c.cluster_size} linked accounts{/if}</span>
		</div>
		{#if fresh}<span class="dot" role="img" aria-label="Changed since you last looked"></span>{/if}
		<div class="likely">
			<strong>{pct(c.p)}</strong>
			<span>{bandText(c)}</span>
		</div>
	</header>
	<p class="why">{c.headline}</p>
	{#if chips.length}
		<div class="chips">
			{#each chips as ch}
				<span class="chip" class:down={ch.dir === 'down'} class:faded={ch.faded}>
					{ch.dir === 'down' ? '↓' : '↑'} {ch.label}
				</span>
			{/each}
		</div>
	{/if}
	<footer>
		<span class="hl">{hlLine(c)}</span>
		<button class="btn small" on:click={() => dispatch('copy', c.address)}>Copy</button>
	</footer>
</article>

<style>
	.post {
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		background: var(--bg-secondary);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	header { display: flex; align-items: center; gap: 12px; }
	.who { display: flex; flex-direction: column; min-width: 0; flex: 1; }
	.address { font-weight: 700; color: var(--text-primary); text-decoration: none; }
	.rank { font-size: 0.76rem; color: var(--text-muted); }
	.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent-cyan); flex: none; }
	.likely { display: flex; flex-direction: column; align-items: flex-end; flex: none; }
	.likely strong { font-size: 1.1rem; font-variant-numeric: tabular-nums; }
	.likely span { font-size: 0.72rem; color: var(--text-muted); }
	.why { margin: 0; font-size: 0.88rem; line-height: 1.45; color: var(--text-secondary); }
	.chips { display: flex; flex-wrap: wrap; gap: 6px; }
	.chip {
		font-size: 0.74rem; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--border);
		color: var(--accent-green);
	}
	.chip.down { color: var(--accent-red); }
	.chip.faded { opacity: 0.55; }
	footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
	.hl { font-size: 0.8rem; color: var(--text-muted); }
	.btn {
		min-height: 36px; padding: 0 12px; border-radius: var(--radius-md); border: 1px solid var(--border);
		background: transparent; color: var(--text-primary); font: inherit; font-size: 0.8rem; cursor: pointer;
	}
</style>
