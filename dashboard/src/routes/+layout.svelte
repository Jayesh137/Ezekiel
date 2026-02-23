<script>
	import '../app.css';
	import { page } from '$app/stores';
	import { base } from '$app/paths';

	const navItems = [
		{ href: `${base}/`, label: 'Dashboard', icon: '⬡' },
		{ href: `${base}/fills`, label: 'Fills', icon: '⬢' },
		{ href: `${base}/fingerprint`, label: 'Fingerprint', icon: '⬣' },
		{ href: `${base}/scanner`, label: 'Scanner', icon: '◎' },
	];
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
		</div>
	</nav>
	<main class="main-content">
		<slot />
	</main>
</div>

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
	}
	.sidebar-footer {
		padding: 16px 20px;
		border-top: 1px solid var(--border);
		margin-top: auto;
	}
	.main-content {
		flex: 1;
		margin-left: 220px;
		padding: 32px 40px;
		max-width: 1400px;
	}

	@media (max-width: 768px) {
		.sidebar { display: none; }
		.main-content { margin-left: 0; padding: 16px; }
	}
</style>
