<script>
	import '../app.css';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { base } from '$app/paths';
	import { fetchIndex, getDataFreshnessMinutes, fetchAlertHealth,
         getAlertDelivery } from '$lib/api.js';

	import Icon from '$lib/ui/Icon.svelte';

	// Grouped by the question each page answers: where he might have gone, what
	// he is doing now, and who else trades like him.
	const navGroups = [
		{ label: 'Hunt', items: [
			{ href: `${base}/recovery`, label: 'Recovery', icon: 'crosshair' },
			{ href: `${base}/transfers`, label: 'Transfers', icon: 'transfers' },
			{ href: `${base}/roster`, label: 'Roster', icon: 'users' },
			{ href: `${base}/tripwires`, label: 'Tripwires', icon: 'tripwire' },
		] },
		{ label: 'Target', items: [
			{ href: `${base}/`, label: 'Dashboard', icon: 'grid' },
			{ href: `${base}/fills`, label: 'Fills', icon: 'list' },
			{ href: `${base}/fingerprint`, label: 'Fingerprint', icon: 'pulse' },
		] },
		{ label: 'Discovery', items: [
			{ href: `${base}/scanner`, label: 'Scanner', icon: 'scan' },
		] },
	];
	const navItems = navGroups.flatMap(g => g.items);

	$: path = $page.url.pathname;
	$: isActive = (href) => path === href
		|| (path === `${base}` && href === `${base}/`)
		|| (href !== `${base}/` && path === `${href}/`);

	$: bare = $page.url.pathname.startsWith(`${base}/review`);

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

<!-- The phone review app (/review) is its own full-screen PWA surface: no
     sidebar and no bottom nav, which would steal a fifth of an iPhone screen. -->
{#if bare}
	{#if alertDelivery?.down}
		<!-- Kept on the phone too: it is the one fault that means ntfy will not
		     reach this device, so the app is the only place left to see it. -->
		<div class="alert-down alert-down-bare" role="alert">
			<strong>ALERTING IS DOWN</strong>
			<span>
				{alertDelivery.undelivered} alert{alertDelivery.undelivered === 1 ? '' : 's'} not delivered.
				Check this app directly until it is fixed.
			</span>
		</div>
	{/if}
	<slot />
{:else}
	<div class="app-shell">
		<nav class="sidebar">
			<a class="brand" href="{base}/" aria-label="Ezekiel home">
				<span class="brand-mark" aria-hidden="true">
					<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><path d="M17 5H7v14h10M7 12h8" /></svg>
				</span>
				<span class="brand-text">
					<span class="brand-name">Ezekiel</span>
					<span class="brand-sub">Trader intelligence</span>
				</span>
			</a>
			<div class="nav-scroll">
				{#each navGroups as group}
					<div class="nav-group">
						<span class="nav-group-label">{group.label}</span>
						<ul class="nav-list">
							{#each group.items as item}
								<li>
									<a href={item.href} class:active={isActive(item.href)} aria-current={isActive(item.href) ? 'page' : undefined}>
										<Icon name={item.icon} size={17} />
										{item.label}
									</a>
								</li>
							{/each}
						</ul>
					</div>
				{/each}
				<div class="nav-group">
					<span class="nav-group-label">Apps</span>
					<ul class="nav-list">
						<li>
							<a href="{base}/review">
								<Icon name="phone" size={17} />
								Phone review
							</a>
						</li>
					</ul>
				</div>
			</div>
			<div class="sidebar-footer">
				<span class="footer-label">System</span>
				{#if freshnessLabel}
					<span class="freshness-pill freshness-{freshnessStatus}" title={freshnessTitle}>
						<span class="pulse-dot" aria-hidden="true"></span>
						{freshnessStatus === 'stale' ? 'STALLED' : 'Data'}: {freshnessLabel}
					</span>
				{/if}
				<!-- Deliberately muted, and deliberately NOT part of the ALERTING IS
				     DOWN banner: these alerts were withheld on purpose, so this is
				     information, not a fault. It is here at all because a policy that
				     withholds alerts should not also hide how much it is withholding. -->
				{#if alertDelivery?.withheld > 0}
					<span class="withheld-pill"
					      title="INFO-level alerts are recorded in the data and shown on this dashboard, but deliberately not pushed to ntfy, Telegram or GitHub issues — only CRITICAL and HIGH are, so low-confidence discoveries cannot bury the two that matter. Set NTFY_INCLUDE_INFO=1 to receive them.">
						{alertDelivery.withheld} INFO withheld
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

	<nav class="mobile-nav" aria-label="Pages">
		{#each navItems as item}
			<a href={item.href} class:active={isActive(item.href)} aria-current={isActive(item.href) ? 'page' : undefined}>
				<Icon name={item.icon} size={19} />
				<span class="mobile-nav-label">{item.label}</span>
			</a>
		{/each}
	</nav>
{/if}

<style>
	.app-shell {
		display: flex;
		min-height: 100vh;
	}

	/* --- Sidebar --- */
	.sidebar {
		width: 232px;
		background: color-mix(in srgb, var(--bg-secondary) 86%, transparent);
		backdrop-filter: blur(12px);
		-webkit-backdrop-filter: blur(12px);
		border-right: 1px solid var(--border-subtle);
		display: flex;
		flex-direction: column;
		position: fixed;
		top: 0;
		left: 0;
		bottom: 0;
		z-index: 10;
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 11px;
		padding: 22px 20px 20px;
		color: var(--text-primary);
	}
	.brand:hover { color: var(--text-primary); }
	.brand-mark {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		border-radius: 9px;
		color: #0b0b1a;
		background: linear-gradient(140deg, #c3c7ff 0%, var(--accent) 48%, #6d5dfc 100%);
		box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.14) inset, 0 6px 18px -6px color-mix(in srgb, var(--accent) 70%, transparent);
	}
	.brand-text { display: flex; flex-direction: column; line-height: 1.25; }
	.brand-name { color: var(--text-primary); font-weight: 650; font-size: 0.98rem; letter-spacing: -0.02em; }
	.brand-sub { font-size: 0.72rem; color: var(--text-muted); }

	.nav-scroll {
		flex: 1;
		overflow-y: auto;
		padding: 4px 12px 12px;
	}
	.nav-group + .nav-group { margin-top: 20px; }
	.nav-group-label {
		display: block;
		padding: 0 10px 6px;
		font-size: 0.68rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--text-muted);
	}
	.nav-list {
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	/* No transition: navigation is the most frequent action on the page. */
	.nav-list a {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 7px 10px;
		border-radius: var(--radius-sm);
		color: var(--text-secondary);
		font-size: 0.875rem;
		font-weight: 500;
		transition: none;
	}
	.nav-list a :global(svg) { color: var(--text-muted); flex: none; }
	.nav-list a:hover {
		color: var(--text-primary);
		background: rgba(255, 255, 255, 0.035);
	}
	.nav-list a.active {
		color: var(--text-primary);
		background: var(--bg-card-hover);
		box-shadow: 0 0 0 1px var(--border) inset, 0 1px 0 0 var(--highlight) inset;
	}
	.nav-list a.active :global(svg) { color: var(--accent); }

	.sidebar-footer {
		padding: 14px 20px 18px;
		border-top: 1px solid var(--border-subtle);
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 7px;
	}
	.footer-label {
		font-size: 0.68rem;
		font-weight: 600;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--text-muted);
	}
	.freshness-pill {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		font-size: 0.74rem;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
		padding: 3px 10px 3px 9px;
		border-radius: 999px;
		cursor: help;
	}
	.pulse-dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: currentColor;
		box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 22%, transparent);
	}
	.freshness-ok { background: var(--tint-green); color: var(--accent-green); }
	.freshness-warn { background: var(--tint-yellow); color: var(--accent-yellow); }
	.freshness-stale { background: var(--tint-red); color: var(--accent-red); }
	/* Same geometry as the freshness pill, no status colour: withheld is not a
	   state of health, so it must not read as green, amber or red. */
	.withheld-pill {
		display: inline-flex;
		font-size: 0.74rem;
		font-variant-numeric: tabular-nums;
		padding: 2px 10px;
		border-radius: 999px;
		border: 1px solid var(--border);
		color: var(--text-muted);
		cursor: help;
	}

	/* --- Alerting down: deliberately the loudest element on every route --- */
	.alert-down {
		background: linear-gradient(90deg, color-mix(in srgb, var(--accent-red) 18%, transparent), color-mix(in srgb, var(--accent-red) 7%, transparent));
		border: 1px solid color-mix(in srgb, var(--accent-red) 55%, transparent);
		border-left: 4px solid var(--accent-red);
		border-radius: var(--radius-sm);
		padding: 14px 18px;
		margin-bottom: 24px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		box-shadow: 0 10px 30px -14px color-mix(in srgb, var(--accent-red) 70%, transparent);
	}
	.alert-down-bare {
		margin: calc(8px + env(safe-area-inset-top, 0px)) 16px 0;
	}
	.alert-down strong {
		color: var(--accent-red);
		letter-spacing: 0.08em;
		font-size: 0.8rem;
		font-weight: 700;
	}
	.alert-down span { font-size: 0.875rem; color: var(--text-primary); }
	.alert-down-reason {
		font-family: var(--font-mono);
		font-size: 0.72rem !important;
		color: var(--text-secondary) !important;
	}

	.main-content {
		flex: 1;
		min-width: 0;
		margin-left: 232px;
		padding: 40px 48px 72px;
		max-width: calc(1400px + 232px);
	}

	@media (max-width: 768px) {
		.sidebar { display: none; }
		.main-content { margin-left: 0; padding: 20px 16px calc(88px + env(safe-area-inset-bottom, 0px)); }
	}

	/* --- Mobile tab bar --- */
	.mobile-nav {
		display: none;
		position: fixed;
		bottom: 0;
		left: 0;
		right: 0;
		z-index: 20;
		background: color-mix(in srgb, var(--bg-secondary) 80%, transparent);
		backdrop-filter: blur(18px) saturate(1.4);
		-webkit-backdrop-filter: blur(18px) saturate(1.4);
		border-top: 1px solid var(--border-subtle);
		padding: 6px 4px calc(6px + env(safe-area-inset-bottom, 0px));
		justify-content: space-around;
		align-items: center;
		overflow-x: auto;
	}
	@media (max-width: 768px) {
		.mobile-nav { display: flex; }
	}
	.mobile-nav a {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 3px;
		color: var(--text-muted);
		padding: 5px 6px;
		border-radius: var(--radius-sm);
		min-width: 44px;
		min-height: 44px;
	}
	.mobile-nav a.active { color: var(--text-primary); }
	.mobile-nav a.active :global(svg) { color: var(--accent); }
	.mobile-nav-label {
		font-size: 0.6rem;
		font-weight: 500;
	}
</style>
