<script>
	// Hyperliquid's edge, from both sides (src/boundary, spec
	// docs/superpowers/specs/2026-10-06-boundary-trace-design.md): who paid one of
	// his addresses, and where every large new account's money came from.
	import { onMount } from 'svelte';
	import { fetchTrace, traceFindings, perimeterRows, provenanceRows, formatUSD } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';

	let data = null;
	let loading = true;

	onMount(async () => {
		data = await fetchTrace();
		loading = false;
	});

	const SEV_BADGE = { CRITICAL: 'badge-red', HIGH: 'badge-yellow' };
	const VERDICT_BADGE = {
		touches_his_world: 'badge-red', same_exchange: 'badge-yellow',
		exchange: 'badge-grey', unresolved: 'badge-grey', unrelated: 'badge-grey'
	};

	$: findings = data ? traceFindings(data.boundary, data.provenance).slice(0, 60) : [];
	$: alerting = findings.filter((f) => f.severity).length;
	$: members = data ? perimeterRows(data.perimeter) : [];
	$: recent = data ? provenanceRows(data.provenance).slice(0, 40) : [];
	$: stops = (data?.engine?.boundaries || []).slice(0, 20);
	$: verdicts = Object.entries(data?.provenance?.counts?.verdicts || {});
	$: roles = Object.entries(data?.perimeter?.counts || {});

	function when(ts) {
		return ts ? new Date(ts * 1000).toLocaleString('en-GB', { hour12: false }) : '—';
	}

	function iso(s) {
		const t = Date.parse(s);
		return Number.isNaN(t) ? '—' : new Date(t).toLocaleString('en-GB', { hour12: false });
	}
</script>

<svelte:head><title>Trace · Ezekiel</title></svelte:head>

<h1>Trace</h1>
<p class="lede">
	Every way money enters or leaves a Hyperliquid account, checked against his world from both
	sides: <strong>who paid one of his addresses</strong>, and <strong>where each large new
	account's money came from</strong>. A finding names both ends from the protocol's own record —
	it is still not proof of ownership.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else}
	<section class="card">
		<h2>Feeds</h2>
		<table>
			<tbody>
				<tr>
					<td>Bridge2 withdrawals read to block</td>
					<td class="mono">{data.boundary?.withdrawal_cursor ?? '—'}</td>
					<td>{data.boundary?.read_error ? 'error: ' + data.boundary.read_error : 'ok'}</td>
					<td>{iso(data.boundary?.computed_at)}</td>
				</tr>
				<tr>
					<td>Bridge2 deposits read to block</td>
					<td class="mono">{data.provenance?.deposit_cursor ?? '—'}</td>
					<td>{data.provenance?.read_error ? 'error: ' + data.provenance.read_error : 'ok'}</td>
					<td>{iso(data.provenance?.computed_at)}</td>
				</tr>
				<tr>
					<td>Accounts traced back</td>
					<td>{data.provenance?.counts?.accounts_cached ?? 0}</td>
					<td colspan="2">{verdicts.map(([k, v]) => `${k.replaceAll('_', ' ')} ${v}`).join(' · ') || '—'}</td>
				</tr>
				<tr>
					<td>His world</td>
					<td>{members.length} addresses</td>
					<td colspan="2">{roles.map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}</td>
				</tr>
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>
			Findings
			{#if alerting}
				<span class="badge badge-red">{alerting} alerted</span>
			{:else}
				<span class="badge badge-green">nothing alerted</span>
			{/if}
		</h2>
		{#if findings.length}
			<table>
				<thead>
					<tr><th>When</th><th>Severity</th><th>What</th><th>Account</th><th>His side</th><th>Amount</th><th>Route</th></tr>
				</thead>
				<tbody>
					{#each findings as f}
						<tr>
							<td>{when(f.ts)}{#if f.retro} <span class="text-muted">(history)</span>{/if}</td>
							<td>
								{#if f.severity}<span class="badge {SEV_BADGE[f.severity]}">{f.severity}</span>
								{:else}<span class="text-muted">recorded</span>{/if}
							</td>
							<td>{f.label}{#if f.hop} · hop {f.hop}{/if}</td>
							<td><Addr address={f.account} /></td>
							<td><Addr address={f.other} /> <span class="text-muted">{f.role || ''}</span></td>
							<td>{f.usd == null ? 'unpriced' : formatUSD(f.usd)}</td>
							<td>{f.route || '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<p class="text-muted">No account outside his cluster has paid, or been funded by, an address of his.</p>
		{/if}
	</section>

	<section class="card">
		<h2>Where new money came from</h2>
		{#if recent.length}
			<table>
				<thead>
					<tr><th>Account</th><th>Verdict</th><th>Why traced</th><th>Routes</th><th>Entered</th><th>First sources</th></tr>
				</thead>
				<tbody>
					{#each recent as r}
						<tr>
							<td><Addr address={r.account} /></td>
							<td><span class="badge {VERDICT_BADGE[r.verdict] || 'badge-grey'}">{r.verdictLabel}</span></td>
							<td>{r.reason || '—'}</td>
							<td>{r.routes.join(', ') || '—'}</td>
							<td>{formatUSD(r.usd)}</td>
							<td>
								{#each r.hop1 as s}
									<div><Addr address={s.address} /> <span class="text-muted">{s.class}{s.role ? ' · ' + s.role : ''}</span></div>
								{/each}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<p class="text-muted">No account traced back yet.</p>
		{/if}
	</section>

	<section class="card">
		<h2>His world</h2>
		<table>
			<thead><tr><th>Address</th><th>Role</th><th>Why</th><th>On Hyperliquid</th></tr></thead>
			<tbody>
				{#each members as m}
					<tr>
						<td><Addr address={m.address} /></td>
						<td>{m.role}</td>
						<td>{m.why}</td>
						<td>{m.active == null ? 'not read' : m.active ? 'active' : 'no'}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>Where his money stops</h2>
		<p class="text-muted">The trace engine's boundaries: exchanges, protocols and bridges his money entered.</p>
		<table>
			<thead><tr><th>Address</th><th>Class</th><th>His money in</th></tr></thead>
			<tbody>
				{#each stops as s}
					<tr><td><Addr address={s.address} /></td><td>{s.class}</td><td>{formatUSD(s.in_usd)}</td></tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>Measured, and no</h2>
		<ul>
			<li>All 412 large L1 counterparties of his three wallets asked of Hyperliquid (2026-10-06): 16 exist there, none trades.</li>
			<li>All 146 Bridge2 payouts to the target and all 4 to the treasury were their own withdrawals.</li>
			<li>Unit: no operations for his wallets, his Solana wallet or his Binance deposit address.</li>
		</ul>
	</section>
{/if}
