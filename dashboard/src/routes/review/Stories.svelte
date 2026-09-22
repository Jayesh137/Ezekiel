<script>
	// The close watch as a stories row. A ring lights when something about the
	// wallet changed since you last tapped it; red when it could not be read or
	// has outgrown the target.
	import { createEventDispatcher } from 'svelte';
	import { shortAddr } from '$lib/api.js';
	import { storyRing, ratio } from '$lib/review.js';
	import Avatar from '$lib/ui/Avatar.svelte';

	/** data/watchlist/latest.json, or null when it could not be loaded. */
	export let watch = null;
	export let state;

	const dispatch = createEventDispatcher();
	$: wallets = watch?.wallets || [];
</script>

<section class="stories" aria-label="Close watch">
	<h2 class="label">Close watch</h2>
	{#if !watch}
		<p class="note">The close watch could not be loaded.</p>
	{:else if !wallets.length}
		<p class="note">Nothing under close watch.</p>
	{:else}
		<div class="row">
			{#each wallets as w (w.address)}
				{@const ring = storyRing(w, state)}
				<button
					class="story"
					on:click={() => dispatch('open', w)}
					aria-label="{w.address}{ring === 'unseen' ? ', changed since you last looked' : ring === 'alert' ? ', needs attention' : ''}"
				>
					<Avatar address={w.address} size={62} {ring} />
					<span class="mono name">{shortAddr(w.address)}</span>
					<span class="sub" class:bad={ring === 'alert'}>
						{w.read_ok ? `${ratio(w.size_ratio)} his size` : 'unread'}
					</span>
				</button>
			{/each}
		</div>
	{/if}
</section>

<style>
	.label {
		margin: 0 0 8px;
		font-size: 0.72rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-muted);
	}
	.row {
		display: flex;
		gap: 12px;
		overflow-x: auto;
		padding: 2px 2px 4px;
		margin: 0 -16px;
		padding-inline: 16px;
		scrollbar-width: none;
		scroll-snap-type: x proximity;
	}
	.row::-webkit-scrollbar { display: none; }
	.story {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 4px;
		background: none;
		border: 0;
		color: inherit;
		padding: 0;
		min-width: 76px;
		cursor: pointer;
		scroll-snap-align: start;
		transition: transform var(--dur-fast) var(--ease-out);
	}
	.story:active { transform: scale(0.95); }
	.name {
		font-size: 0.72rem;
		color: var(--text-secondary);
	}
	.sub {
		font-size: 0.66rem;
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
	}
	.sub.bad { color: var(--accent-red); }
	.note {
		color: var(--text-muted);
		font-size: 0.85rem;
		margin: 0;
	}
</style>
