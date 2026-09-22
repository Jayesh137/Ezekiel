<script>
	// Loading placeholder shaped like the content it stands in for, so the page
	// does not jump when data lands. The shimmer stops under reduced motion.
	export let width = '100%';
	export let height = '16px';
	export let radius = 'var(--radius-sm)';
</script>

<span class="skeleton" style="width:{width};height:{height};border-radius:{radius}" aria-hidden="true"></span>

<style>
	.skeleton {
		display: block;
		background: linear-gradient(90deg, var(--bg-card) 0%, var(--bg-card-hover) 50%, var(--bg-card) 100%);
		background-size: 200% 100%;
		animation: shimmer 1.4s linear infinite;
	}
	@keyframes shimmer {
		from { background-position: 200% 0; }
		to { background-position: -200% 0; }
	}
</style>
