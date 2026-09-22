<script>
	// Bottom sheet on native <dialog>. showModal() gives focus trapping, Esc to
	// close and an inert page for free, which is most of the design-system
	// checklist's Modal row. It opens with a slide (rare, explains where it came
	// from) and closes instantly: exits should be faster than entrances.
	import { createEventDispatcher } from 'svelte';

	export let open = false;
	export let title = '';

	const dispatch = createEventDispatcher();
	const id = `sheet-${Math.random().toString(36).slice(2, 9)}`;
	let dialog;

	$: if (dialog) {
		if (open && !dialog.open) dialog.showModal();
		else if (!open && dialog.open) dialog.close();
	}

	function onClose() {
		open = false;
		dispatch('close');
	}

	// A click whose target is the <dialog> element itself landed on the
	// backdrop: the panel fills the dialog's box, so it catches every other click.
	function onClick(e) {
		if (e.target === dialog) dialog.close();
	}
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
<dialog bind:this={dialog} class="sheet" aria-labelledby={id} on:close={onClose} on:click={onClick}>
	<div class="panel">
		<div class="grabber" aria-hidden="true"></div>
		<header>
			<h2 {id}>{title}</h2>
			<button class="close" on:click={() => dialog.close()} aria-label="Close">✕</button>
		</header>
		<div class="body"><slot /></div>
	</div>
</dialog>

<style>
	dialog.sheet {
		margin: auto 0 0;
		width: 100%;
		max-width: 100%;
		max-height: 88dvh;
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--text-primary);
		overflow: visible;
	}
	dialog.sheet::backdrop {
		background: rgba(0, 0, 0, 0.55);
	}
	.panel {
		max-width: 560px;
		margin: 0 auto;
		max-height: 88dvh;
		overflow-y: auto;
		overscroll-behavior: contain;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-bottom: 0;
		border-radius: var(--radius-lg) var(--radius-lg) 0 0;
		box-shadow: var(--shadow-sheet);
		padding: 8px 18px calc(20px + env(safe-area-inset-bottom, 0px));
		box-sizing: border-box;
	}
	dialog[open] .panel {
		animation: sheet-in var(--dur-slow) var(--ease-out);
	}
	@keyframes sheet-in {
		from { transform: translateY(100%); }
		to { transform: translateY(0); }
	}
	.grabber {
		width: 40px;
		height: 5px;
		border-radius: 3px;
		background: var(--border);
		margin: 0 auto 6px;
	}
	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	h2 {
		font-size: 1.05rem;
		margin: 0;
		text-wrap: balance;
	}
	.close {
		min-width: 44px;
		min-height: 44px;
		border: 0;
		background: transparent;
		color: var(--text-secondary);
		font-size: 1.1rem;
		border-radius: 50%;
		cursor: pointer;
	}
	.body {
		padding-top: 6px;
	}
</style>
