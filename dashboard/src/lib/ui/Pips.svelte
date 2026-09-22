<script>
	// A count of independent vectors, NOT a score. Two agreeing vectors is the
	// project's strongest evidence, so the meter shows agreement and nothing else.
	export let count = 0;
	export let max = 3;

	$: n = Math.max(max, count);
</script>

<span class="pips" role="img" aria-label="{count} independent vector{count === 1 ? '' : 's'}">
	{#each Array(n) as _, i}<span class="pip" class:on={i < count}></span>{/each}
</span>

<style>
	.pips {
		display: inline-flex;
		gap: 4px;
		vertical-align: middle;
	}
	.pip {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--border);
	}
	.pip.on { background: var(--accent-cyan); }
</style>
