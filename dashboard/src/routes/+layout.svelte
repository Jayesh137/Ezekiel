<script>
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { base } from '$app/paths';
	import { fetchIndex, getDataFreshnessMinutes, fetchAlertHealth,
         getAlertDelivery } from '$lib/api.js';

	const navItems = [
		{ href: `${base}/recovery`, label: 'Recovery', icon: 'R' },
		{ href: `${base}/transfers`, label: 'Transfers', icon: 'T' },
		{ href: `${base}/`, label: 'Dashboard', icon: 'D' },
		{ href: `${base}/fills`, label: 'Fills', icon: 'F' },
		{ href: `${base}/fingerprint`, label: 'Fingerprint', icon: 'P' },
		{ href: `${base}/scanner`, label: 'Scanner', icon: 'S' },
	];

	let freshnessMinutes = null;
	// Alert delivery health. This sits above everything else because if alerting
	// is down, nothing else on this dashboard can reach the operator in time to
	// matter — the whole product is an alert.
	let alertDelivery = null;
	// Mirrors heartbeat.STALE_AFTER_MINUTES. GitHub honours roughly 5% of this
	// repo's requested cron — measured median gap 83 min, p90 160, max 220 — so
	// the backend deliberately does not call collection stalled until 360 min.
	// This pill used 10/30, which painted it red at two hours and kept it red
	// essentially always: the one indicator for the failure mode that silently
	// loses unrecoverable history, trained to be ignored. Warn at the observed
	// p90, stale only where the backend would alert.
	$: freshnessStatus = freshnessMinutes === null ? 'ok'
		: freshnessMinutes > 360 ? 'stale'
		: freshnessMinutes > 160 ? 'warn'
		: 'ok';
	$: freshnessLabel = freshnessMinutes === null ? null
		: freshnessMinutes < 60 ? `${freshnessMinutes}m ago`
		: `${Math.floor(freshnessMinutes / 60)}h ago`;
	$: freshnessTitle = freshnessMinutes === null ? 'Collection age unknown'
		: freshnessStatus === 'stale'
			? `Collection has not run for ${Math.floor(freshnessMinutes / 60)}h. The backend `
				+ `treats this as stalled past 6h — while it is down a migration cannot be `
				+ `detected, and Hyperliquid serves only ~2000 recent records per endpoint.`
		: freshnessStatus === 'warn'
			? `Last collection ${freshnessMinutes} min ago — longer than the usual p90 gap `
				+ `of 160 min, but not yet the 6h stall threshold.`
		: `Last collection ${freshnessMinutes} min ago. Gaps of an hour or two are normal: `
			+ `GitHub honours only about 5% of the requested schedule.`;

	onMount(async () => {
		const [index, health] = await Promise.all([fetchIndex(), fetchAlertHealth()]);
		freshnessMinutes = getDataFreshnessMinutes(index);
		alertDelivery = getAlertDelivery(health);
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
				<span class="freshness-pill freshness-{freshnessStatus}" title={freshnessTitle}>
					{freshnessStatus === 'stale' ? 'STALLED' : 'Data'}: {freshnessLabel}
				</span>
			{/if}
		</div>
	</nav>
	<main class="main-content">
		{#if alertDelivery?.down}
			<!-- Deliberately the loudest thing on the page and above the content on
			     every route. A detection system whose output channel is dead looks
			     exactly like a quiet week; this is the only place that difference is
			     visible, because email cannot report its own failure. -->
			<div class="alert-down" role="alert">
				<strong>ALERTING IS DOWN</strong>
				<span>
					{alertDelivery.undelivered} alert{alertDelivery.undelivered === 1 ? '' : 's'}
					not delivered{alertDelivery.since ? ` since ${alertDelivery.since.slice(0, 16).replace('T', ' ')}` : ''}.
					You will not be emailed if the trader migrates — check this dashboard directly until it is fixed.
				</span>
				{#if alertDelivery.reason}
					<span class="alert-down-reason">{alertDelivery.reason}</span>
				{/if}
			</div>
		{/if}
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
	.alert-down {
		background: rgba(255, 51, 85, 0.14);
		border: 1px solid var(--accent-red);
		border-left-width: 4px;
		border-radius: 6px;
		padding: 12px 16px;
		margin-bottom: 20px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.alert-down strong {
		color: var(--accent-red);
		font-family: var(--font-mono);
		letter-spacing: 0.06em;
		font-size: 0.85rem;
	}
	.alert-down span { font-size: 0.85rem; color: var(--text-primary); }
	.alert-down-reason {
		font-family: var(--font-mono);
		font-size: 0.7rem !important;
		color: var(--text-secondary) !important;
	}
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
