<script>
	// The review queue as a clearable goal. Counts YOUR review, never evidence.
	import { createEventDispatcher } from 'svelte';
	import ProgressRing from '$lib/ui/ProgressRing.svelte';

	/** { done, total, fresh, changed } */
	export let p;

	const dispatch = createEventDispatcher();
	$: clear = p.total > 0 && p.done === p.total;
</script>

<section class="progress" aria-label="Review progress">
	{#if p.total === 0}
		<div class="text">
			<p class="title">Nothing to review</p>
			<p class="sub">No wallet is Possible or better right now.</p>
		</div>
	{:else if clear}
		<!-- The one delight animation: rare by construction, since it plays only
		     when the queue empties. -->
		<svg class="check" viewBox="0 0 52 52" width="46" height="46" aria-hidden="true">
			<circle cx="26" cy="26" r="24" fill="none" stroke="var(--accent-green)" stroke-width="3" />
			<path d="M15 27 l8 8 l15 -17" fill="none" stroke="var(--accent-green)" stroke-width="4"
			      stroke-linecap="round" stroke-linejoin="round" />
		</svg>
		<div class="text">
			<p class="title">You're all caught up</p>
			<p class="sub">{p.total} wallet{p.total === 1 ? '' : 's'} reviewed</p>
		</div>
	{:else}
		<ProgressRing done={p.done} total={p.total} size={52} />
		<div class="text">
			<p class="title">{p.done} / {p.total} reviewed</p>
			<p class="sub">
				{p.fresh} new{#if p.changed} · <span class="changed">{p.changed} changed</span>{/if}
			</p>
		</div>
		<button class="all" on:click={() => dispatch('markall')}>Mark all ✓</button>
	{/if}
</section>

<style>
	.progress {
		display: flex;
		align-items: center;
		gap: 14px;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		padding: 14px 16px;
	}
	.text {
		flex: 1;
		min-width: 0;
	}
	.title {
		margin: 0;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.sub {
		margin: 2px 0 0;
		font-size: 0.82rem;
		color: var(--text-secondary);
		font-variant-numeric: tabular-nums;
	}
	.changed { color: var(--accent-yellow); }
	.all {
		min-height: 44px;
		padding: 0 14px;
		border-radius: var(--radius-md);
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-primary);
		font: inherit;
		font-weight: 600;
		font-size: 0.85rem;
		cursor: pointer;
		white-space: nowrap;
		transition: transform var(--dur-fast) var(--ease-out);
	}
	.all:active { transform: scale(0.97); }
	.check { flex: none; }
	/* The resting state is DRAWN and the animation starts from hidden, so a
	   browser that never runs the animation still shows the tick. */
	.check path {
		stroke-dasharray: 40;
		stroke-dashoffset: 0;
		animation: draw 400ms var(--ease-out) 120ms backwards;
	}
	@keyframes draw {
		from { stroke-dashoffset: 40; }
		to { stroke-dashoffset: 0; }
	}
</style>
