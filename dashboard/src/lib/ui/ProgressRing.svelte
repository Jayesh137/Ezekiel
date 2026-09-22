<script>
	// Review progress (coss Meter / Progress). Measures YOUR review, never a
	// wallet's likelihood.
	export let done = 0;
	export let total = 0;
	export let size = 56;

	const stroke = 6;
	$: r = (size - stroke) / 2;
	$: c = 2 * Math.PI * r;
	$: pct = total > 0 ? Math.min(1, done / total) : 1;
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 {size} {size}"
	role="progressbar"
	aria-valuemin="0"
	aria-valuemax={total}
	aria-valuenow={done}
	aria-label="{done} of {total} reviewed"
>
	<circle cx={size / 2} cy={size / 2} {r} fill="none" stroke="var(--border)" stroke-width={stroke} />
	<circle
		cx={size / 2}
		cy={size / 2}
		{r}
		fill="none"
		stroke="var(--accent-cyan)"
		stroke-width={stroke}
		stroke-linecap="round"
		stroke-dasharray={c}
		stroke-dashoffset={c * (1 - pct)}
		transform="rotate(-90 {size / 2} {size / 2})"
	/>
</svg>

<style>
	svg { flex: none; }
</style>
