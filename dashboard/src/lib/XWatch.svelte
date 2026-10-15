<script>
	// His X accounts. Hidden until something changes: a card only when a change
	// landed in the last week, otherwise one quiet line (src/lib/xwatch.js).
	import { recentXChanges, xAccounts, xQuietLine, RECENT_DAYS } from '$lib/xwatch.js';

	export let doc = null;
	export let now = Date.now();
	export let compact = false;
	// The phone stays fully hidden when quiet; the Tripwires page shows the liveness line.
	export let quiet = true;

	$: recent = recentXChanges(doc, now);
	$: accounts = xAccounts(doc);
	$: shown = compact ? recent.slice(0, 6) : recent.slice(0, 40);

	function when(iso) {
		const t = Date.parse(iso);
		if (Number.isNaN(t)) return '—';
		const min = Math.round((now - t) / 60_000);
		if (min < 60) return `${Math.max(min, 0)}m ago`;
		if (min < 48 * 60) return `${Math.round(min / 60)}h ago`;
		return new Date(t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
	}

	// Lines carry URLs (new avatar, a post, x.com/i/user/<id>); make them tappable.
	function parts(line) {
		return String(line || '').split(/(https?:\/\/\S+)/g).map((text, i) => ({ text, url: i % 2 === 1 }));
	}
</script>

{#if recent.length}
	<section class="xw" class:compact aria-label="His X accounts">
		<h2>
			<span class="dot" aria-hidden="true"></span>
			His X accounts
			<span class="count">{recent.length} change{recent.length === 1 ? '' : 's'} in {RECENT_DAYS} days</span>
		</h2>
		<ul>
			{#each shown as c}
				<li>
					<span class="line">
						{#each parts(c.line || `@${c.handle} ${c.field} changed`) as p}
							{#if p.url}<a href={p.url} target="_blank" rel="noopener noreferrer">{p.text}</a>{:else}{p.text}{/if}
						{/each}
					</span>
					<time datetime={c.at}>{when(c.at)}</time>
				</li>
			{/each}
		</ul>
		{#if recent.length > shown.length}
			<p class="more">+{recent.length - shown.length} more on the Tripwires page</p>
		{/if}
		<p class="links">
			{#each accounts as a, i}
				{#if i}<span aria-hidden="true"> · </span>{/if}
				<a href={a.url} target="_blank" rel="noopener noreferrer">@{a.handle}</a>
				{#if a.protected === false}<span class="public">public</span>{/if}
			{/each}
		</p>
	</section>
{:else if quiet}
	<p class="quiet">{xQuietLine(doc, now)}</p>
{/if}

<style>
	.xw {
		background: var(--bg-card);
		border: 1px solid color-mix(in srgb, var(--accent-cyan) 45%, var(--border));
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-card);
		padding: 16px 18px;
		margin: 0 0 16px;
	}
	h2 {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		font-size: 1rem;
		margin: 0 0 10px;
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--accent-cyan);
		box-shadow: 0 0 0 4px var(--tint-cyan);
	}
	.count {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 8px;
	}
	li {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 12px;
		font-size: 0.88rem;
		line-height: 1.45;
		border-top: 1px solid var(--border-subtle);
		padding-top: 8px;
	}
	li:first-child { border-top: 0; padding-top: 0; }
	.line {
		min-width: 0;
		overflow-wrap: anywhere;
		color: var(--text-primary);
	}
	time {
		flex: none;
		font-size: 0.76rem;
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
	}
	a { color: var(--accent); }
	.more, .links {
		margin: 10px 0 0;
		font-size: 0.8rem;
		color: var(--text-muted);
	}
	.public {
		margin-left: 4px;
		padding: 1px 6px;
		border-radius: 999px;
		font-size: 0.7rem;
		background: var(--tint-cyan);
		color: var(--accent-cyan);
	}
	.quiet {
		margin: 0 0 12px;
		font-size: 0.78rem;
		color: var(--text-muted);
	}
	.compact { padding: 14px 16px; }
	.compact li { flex-direction: column; gap: 2px; }
</style>
