<script>
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { base } from '$app/paths';
	import { fetchIndex, getDataFreshnessMinutes } from '$lib/api.js';

	const navItems = [
		{ href: `${base}/recovery`, label: 'Recovery', icon: 'R' },
		{ href: `${base}/`, label: 'Dashboard', icon: 'D' },
		{ href: `${base}/fills`, label: 'Fills', icon: 'F' },
		{ href: `${base}/fingerprint`, label: 'Fingerprint', icon: 'P' },
		{ href: `${base}/scanner`, label: 'Scanner', icon: 'S' },
	];

	let freshnessMinutes = null;
	$: freshnessStatus = freshnessMinutes === null ? 'ok'
		: freshnessMinutes > 30 ? 'stale'
		: freshnessMinutes > 10 ? 'warn'
		: 'ok';
	$: freshnessLabel = freshnessMinutes === null ? null
		: freshnessMinutes < 60 ? `${freshnessMinutes}m ago`
		: `${Math.floor(freshnessMinutes / 60)}h ago`;

	onMount(async () => {
		const index = await fetchIndex();
		freshnessMinutes = getDataFreshnessMinutes(index);
	});
</script>

<div class="app-shell">
	<nav class="sidebar">
		<div class="sidebar-header">
			<span class="logo">EZK</span>
			<span class="logo-sub">EZEKIEL</span>
		</div>
		<ul class="nav-list">
			{#each navItems as item}
				<li>
					<a
						href={item.href}
						class:active={$page.url.pathname === item.href || ($page.url.pathname === `${base}` && item.href === `${base}/`)}
					>
						<span class="nav-icon">{item.icon}</span>
						{item.label}
					</a>
				</li>
			{/each}
		</ul>
		<div class="sidebar-footer">
			<span class="text-muted" style="font-size:0.7rem">Trader Intelligence</span>
			{#if freshnessLabel}
				<span class="freshness-pill freshness-{freshnessStatus}">Data: {freshnessLabel}</span>
			{/if}
		</div>
	</nav>
	<main class="main-content">
		<slot />
	</main>
</div>

<nav class="mobile-nav">
	{#each navItems as item}
		<a
			href={item.href}
			class:active={$page.url.pathname === item.href || ($page.url.pathname === `${base}` && item.href === `${base}/`)}
		>
			<span class="mobile-nav-icon">{item.icon}</span>
			<span class="mobile-nav-label">{item.label}</span>
		</a>
	{/each}
</nav>

<style>
	.app-shell {
		display: flex;
		min-height: 100vh;
	}
	.sidebar {
		width: 220px;
		background: var(--bg-secondary);
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		padding: 20px 0;
		position: fixed;
		top: 0;
		left: 0;
		bottom: 0;
		z-index: 10;
	}
	.sidebar-header {
		padding: 0 20px 24px;
		border-bottom: 1px solid var(--border);
		margin-bottom: 16px;
	}
	.logo {
		font-family: var(--font-mono);
		font-size: 1.6rem;
		font-weight: 700;
		color: var(--accent-cyan);
		letter-spacing: 0.1em;
	}
	.logo-sub {
		display: block;
		font-size: 0.65rem;
		color: var(--text-muted);
		letter-spacing: 0.3em;
		margin-top: 2px;
	}
	.nav-list {
		list-style: none;
		flex: 1;
	}
	.nav-list a {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 20px;
		color: var(--text-secondary);
		font-size: 0.9rem;
		font-weight: 500;
		transition: all 0.15s;
		border-left: 3px solid transparent;
	}
	.nav-list a:hover {
		color: var(--text-primary);
		background: rgba(255,255,255,0.03);
		text-decoration: none;
	}
	.nav-list a.active {
		color: var(--accent-cyan);
		background: rgba(0,204,221,0.08);
		border-left-color: var(--accent-cyan);
	}
	.nav-icon {
		font-size: 1rem;
		width: 20px;
		text-align: center;
		font-family: var(--font-mono);
		font-weight: 700;
	}
	.sidebar-footer {
		padding: 16px 20px;
		border-top: 1px solid var(--border);
		margin-top: auto;
	}
	.freshness-pill {
		display: inline-block;
		margin-top: 6px;
		font-size: 0.65rem;
		font-family: var(--font-mono);
		padding: 2px 7px;
		border-radius: 4px;
	}
	.freshness-ok { background: rgba(0,255,136,0.12); color: var(--accent-green); }
	.freshness-warn { background: rgba(255,170,0,0.12); color: var(--accent-yellow); }
	.freshness-stale { background: rgba(255,51,85,0.12); color: var(--accent-red); }
	.main-content {
		flex: 1;
		margin-left: 220px;
		padding: 32px 40px;
		max-width: 1400px;
	}

	@media (max-width: 768px) {
		.sidebar { display: none; }
		.main-content { margin-left: 0; padding: 16px; padding-bottom: 80px; }
	}

	.mobile-nav {
		display: none;
		position: fixed;
		bottom: 0;
		left: 0;
		right: 0;
		z-index: 20;
		background: var(--bg-secondary);
		border-top: 1px solid var(--border);
		padding: 6px 0 env(safe-area-inset-bottom, 6px);
		justify-content: space-around;
		align-items: center;
	}
	@media (max-width: 768px) {
		.mobile-nav { display: flex; }
	}
	.mobile-nav a {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 2px;
		color: var(--text-secondary);
		padding: 4px 10px;
		border-radius: 6px;
		text-decoration: none;
		min-width: 44px;
	}
	.mobile-nav a:hover { text-decoration: none; }
	.mobile-nav a.active { color: var(--accent-cyan); }
	.mobile-nav-icon {
		font-size: 1rem;
		font-family: var(--font-mono);
		font-weight: 700;
	}
	.mobile-nav-label {
		font-size: 0.6rem;
		font-family: var(--font-mono);
		letter-spacing: 0.02em;
	}
</style>
