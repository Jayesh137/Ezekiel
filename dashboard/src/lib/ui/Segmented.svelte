<script>
	// Segmented control (coss Segmented Control). Switching is instant, with no
	// sliding indicator: tabs are the most frequent action in the app, and
	// motion there reads as lag.
	import { createEventDispatcher } from 'svelte';

	/** @type {{value: string, label: string, count?: number|null}[]} */
	export let options = [];
	export let value = '';
	export let label = '';

	const dispatch = createEventDispatcher();
	let buttons = [];

	function select(v) {
		value = v;
		dispatch('change', v);
	}

	function onKey(e, i) {
		if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
		e.preventDefault();
		const n = options.length;
		const j = (i + (e.key === 'ArrowRight' ? 1 : n - 1)) % n;
		select(options[j].value);
		buttons[j]?.focus();
	}
</script>

<div class="segmented" role="tablist" aria-label={label}>
	{#each options as o, i (o.value)}
		<button
			role="tab"
			bind:this={buttons[i]}
			aria-selected={value === o.value}
			tabindex={value === o.value ? 0 : -1}
			class:selected={value === o.value}
			on:click={() => select(o.value)}
			on:keydown={(e) => onKey(e, i)}
		>
			{o.label}{#if o.count != null}<span class="count">{o.count}</span>{/if}
		</button>
	{/each}
</div>

<style>
	.segmented {
		display: flex;
		gap: 4px;
		padding: 4px;
		background: var(--bg-secondary);
		border: 1px solid var(--border);
		border-radius: var(--radius-md);
	}
	button {
		flex: 1;
		min-height: 44px;
		border: 0;
		border-radius: calc(var(--radius-md) - 4px);
		background: transparent;
		color: var(--text-secondary);
		font: inherit;
		font-weight: 600;
		font-size: 0.9rem;
		cursor: pointer;
		white-space: nowrap;
	}
	button.selected {
		background: var(--bg-card-hover);
		color: var(--text-primary);
	}
	.count {
		margin-left: 6px;
		font-variant-numeric: tabular-nums;
		color: var(--text-muted);
		font-weight: 500;
	}
</style>
