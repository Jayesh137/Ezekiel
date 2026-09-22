<script>
	// Toast host (shadcn Sonner / transitions.dev toast). Always enters from the
	// same edge, so where feedback appears is predictable.
	import { fly } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import { toasts, dismiss, runAction } from './toast.js';

	const reduced = typeof matchMedia === 'function'
		&& matchMedia('(prefers-reduced-motion: reduce)').matches;
	const enter = { y: 16, duration: reduced ? 0 : 180, easing: cubicOut };
</script>

<div class="host" role="status" aria-live="polite">
	{#each $toasts as item (item.id)}
		<div class="toast" transition:fly={enter}>
			<span class="msg">{item.message}</span>
			{#if item.action}
				<button class="action" on:click={() => runAction(item.id)}>{item.action.label}</button>
			{:else}
				<button class="action muted" on:click={() => dismiss(item.id)} aria-label="Dismiss">✕</button>
			{/if}
		</div>
	{/each}
</div>

<style>
	.host {
		position: fixed;
		left: 0;
		right: 0;
		bottom: calc(16px + env(safe-area-inset-bottom, 0px));
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 8px;
		pointer-events: none;
		z-index: 50;
		padding: 0 16px;
	}
	.toast {
		pointer-events: auto;
		display: flex;
		align-items: center;
		gap: 12px;
		max-width: 520px;
		width: 100%;
		box-sizing: border-box;
		background: var(--text-primary);
		color: var(--bg-primary);
		border-radius: var(--radius-md);
		padding: 4px 4px 4px 16px;
		font-weight: 600;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
	}
	.msg { flex: 1; }
	.action {
		min-height: 44px;
		min-width: 44px;
		padding: 0 14px;
		border: 0;
		border-radius: var(--radius-sm);
		background: transparent;
		color: #2b5fd9;
		font: inherit;
		font-weight: 700;
		cursor: pointer;
	}
	.action.muted { color: #55556a; }
</style>
