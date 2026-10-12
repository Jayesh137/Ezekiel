<script>
	import { onMount } from 'svelte';
	import { fetchTripwires, formatUSD } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';

	let data = null;
	let loading = true;

	onMount(async () => {
		data = await fetchTripwires();
		loading = false;
	});

	const STATUS_BADGE = { fresh: 'badge-green', stale: 'badge-red', missing: 'badge-red', blind: 'badge-red' };

	const KIND_LABEL = {
		his_wallet_funded_outside_account: 'His wallet funded an outside account',
		outside_account_paid_his_address: 'An outside account paid his address',
		his_account_withdrew_to_outside_address: 'His account withdrew to an outside address'
	};

	function age(min) {
		if (min == null) return '—';
		if (min < 90) return `${min} min`;
		return `${(min / 60).toFixed(1)} h`;
	}

	function when(iso) {
		const t = Date.parse(iso);
		return Number.isNaN(t) ? '—' : new Date(t).toLocaleString('en-GB', { hour12: false });
	}

	$: staleCount = (data?.feeds || []).filter((f) => f.status !== 'fresh').length;
	$: sentinels = Object.entries(data?.sentinels?.sentinels || {});
	$: sharers = data?.sentinels?.sharers || [];
	$: circle = data?.circle;
	$: findings = [...(circle?.findings || [])].reverse().slice(0, 25);
	$: clusterFlows = [...(circle?.cluster_flows || [])].reverse().slice(0, 25);
	$: pendingPayees = data?.watch?.l1_pending || [];
	$: gate = data?.gate;
	$: execMatches = (data?.execution?.matches || []).filter((m) => m.discriminating);
	$: tapeHandoffs = data?.tape?.newcomer_handoffs || [];
	$: digestLines = (data?.digest?.body || '').split('\n').filter(Boolean);

	function pct(x) {
		return typeof x === 'number' ? `${(100 * x).toFixed(1)}%` : 'unmeasured';
	}
</script>

<svelte:head><title>Tripwires · Ezekiel</title></svelte:head>

<h1>Tripwires</h1>
<p class="lede">
	The detectors that catch a move <strong>as it happens</strong>, and whether each is still
	producing readings. A dead detector writes the same nothing as a quiet target, so its
	freshness is shown first — and a detector that writes on time while every read fails is
	shown as <strong>blind</strong>.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else}
	<section class="card">
		<h2>
			Detector health
			{#if staleCount > 0}
				<span class="badge badge-red">{staleCount} not producing readings</span>
			{:else}
				<span class="badge badge-green">all fresh</span>
			{/if}
		</h2>
		<table>
			<thead><tr><th>Detector</th><th>Last reading</th><th>Limit</th><th>Status</th></tr></thead>
			<tbody>
				{#each data.feeds as f}
					<tr>
						<td>{f.name}</td>
						<td>{age(f.ageMin)}</td>
						<td>{age(f.limitMin)}</td>
						<td>
							<span class="badge {STATUS_BADGE[f.status]}">{f.status}</span>
							{#if f.status === 'blind'}<span class="text-muted">reads failing for {age(f.blindMin)}</span>{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>

	<section class="card">
		<h2>
			Execution-program vote gate
			{#if gate?.open}
				<span class="badge badge-green">open · {gate.path}</span>
			{:else}
				<span class="badge badge-grey">closed</span>
			{/if}
		</h2>
		<p class="text-muted">
			His SDK slicer's clip table votes only once the census has MEASURED a match rare (rule 4):
			{gate?.why ?? '—'}. Two populations, the larger ceiling wins.
		</p>
		<table>
			<thead><tr><th>Population</th><th>Read</th><th>Comparable</th><th>False-match ceiling</th><th></th></tr></thead>
			<tbody>
				<tr>
					<td>Leaderboard census</td>
					<td>{gate?.leaderboard?.attempted?.toLocaleString() ?? '—'}</td>
					<td>{gate?.leaderboard?.comparable ?? '—'}</td>
					<td>{pct(gate?.leaderboard?.ceiling)}</td>
					<td class="text-muted">analyze.yml</td>
				</tr>
				<tr>
					<td>Tape census (takers of his markets)</td>
					<td>{gate?.tape?.attempted?.toLocaleString() ?? '—'}</td>
					<td>{gate?.tape?.comparable ?? '—'}</td>
					<td>{pct(gate?.tape?.ceiling)}</td>
					<td class="text-muted">
						{#if gate?.tape}{gate.tape.sketched?.toLocaleString()} wallets sketched over {gate.tape.windows} windows, {gate.tape.hits} program hit(s){:else}no window folded yet{/if}
					</td>
				</tr>
			</tbody>
		</table>
		{#if execMatches.length}
			<h3>Rare matches among candidates read</h3>
			<ul>
				{#each execMatches as m}
					<li><Addr address={m.wallet} /> — {m.clips_matched}/{m.clips_compared} clips ({(m.clips_matched_coins || []).join(', ')}), rho {m.notional_structure_rho ?? '—'}, judged by {m.rarity_path}</li>
				{/each}
			</ul>
		{:else}
			<p class="text-muted">No candidate read reproduces his program rarely enough to vote.</p>
		{/if}
		{#if tapeHandoffs.length}
			<h3>Newcomers born in his silence (tape)</h3>
			<ul>
				{#each tapeHandoffs as h}
					<li><Addr address={h.wallet} /> — score {h.score}, {h.delay_days} d into a {h.gap_length} d silence{h.open ? ' (open now)' : ''}, on {(h.rare_markets || []).join(', ')}</li>
				{/each}
			</ul>
		{/if}
	</section>

	<section class="card">
		<h2>Daily digest</h2>
		{#if !data.digest}
			<p class="text-muted">Not sent yet — written by <code>scripts/send_digest.py</code> at 07:00 UTC.</p>
		{:else}
			<p class="text-muted">{data.digest.title} · rendered {when(data.digest.computed_at)}</p>
			<ul class="digest">
				{#each digestLines as line}<li>{line}</li>{/each}
			</ul>
		{/if}
	</section>

	<section class="card">
		<h2>Circle flows in and out of Hyperliquid</h2>
		{#if !circle}
			<p class="text-muted">No reading yet — written by <code>scripts/check_circle_flows.py</code> on the watch job.</p>
		{:else}
			<p class="text-muted">
				Read to HyperEVM block {circle.last_block?.toLocaleString()} of {circle.head_block?.toLocaleString()}
				({circle.lag_blocks?.toLocaleString()} behind) via {circle.source} at {when(circle.computed_at)}.
				Last read: {circle.deposits_read} deposit(s) {formatUSD(circle.deposit_usd)},
				{circle.withdrawals_read} withdrawal(s) {formatUSD(circle.withdrawal_usd)}.
				{#if circle.error}<span class="badge badge-red">stopped: {circle.error}</span>{/if}
			</p>
			<h3>Findings</h3>
			{#if findings.length === 0}
				<p class="text-muted">None. No wallet of his has funded an outside account, and no outside account has paid him, through Circle.</p>
			{:else}
				<table>
					<thead><tr><th>What</th><th>Chain</th><th>HL account</th><th>Other end</th><th>Amount</th></tr></thead>
					<tbody>
						{#each findings as r}
							<tr>
								<td><span class="badge badge-red">{KIND_LABEL[r.kind] || r.kind}</span></td>
								<td>{r.chain}</td>
								<td><Addr address={r.hl_account} /></td>
								<td>{#if r.counterparty}<Addr address={r.counterparty} />{:else}<code>{r.counterparty_raw?.slice(0, 12)}…</code>{/if}</td>
								<td>{formatUSD(r.amount_usd)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
			<h3>Recent Circle flows touching his accounts</h3>
			{#if clusterFlows.length === 0}
				<p class="text-muted">None recorded yet.</p>
			{:else}
				<table>
					<thead><tr><th>Direction</th><th>Chain</th><th>His account</th><th>Other end</th><th>Amount</th></tr></thead>
					<tbody>
						{#each clusterFlows as r}
							<tr>
								<td>{r.direction === 'in' ? 'into HL' : 'out of HL'}</td>
								<td>{r.chain}</td>
								<td><Addr address={r.hl_account} /></td>
								<td>{#if r.counterparty}<Addr address={r.counterparty} />{:else}<code>{r.counterparty_raw?.slice(0, 12)}…</code>{/if}</td>
								<td>{formatUSD(r.amount_usd)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		{/if}
	</section>

	<section class="card">
		<h2>His private exchange deposit addresses</h2>
		{#if !data.sentinels}
			<p class="text-muted">No reading yet — written by <code>scripts/check_deposit_sentinels.py</code>.</p>
		{:else}
			<p class="text-muted">
				A deposit address belongs to one exchange account, so a new outside sender is his
				account funded by a wallet nobody knew. Read {when(data.sentinels.computed_at)}.
			</p>
			<table>
				<thead><tr><th>Deposit address</th><th>Paid in by his cluster</th><th>Watched since</th></tr></thead>
				<tbody>
					{#each sentinels as [addr, s]}
						<tr>
							<td><Addr address={addr} /></td>
							<td>{formatUSD(s.cluster_paid_usd)}</td>
							<td>{when(s.since)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			<h3>Outside senders on record</h3>
			{#if sharers.length === 0}
				<p class="text-muted">None.</p>
			{:else}
				<table>
					<thead><tr><th>Sender</th><th>Into</th><th>Paid</th><th>Measured</th></tr></thead>
					<tbody>
						{#each sharers as r}
							<tr>
								<td><Addr address={r.address} /></td>
								<td><Addr address={r.sentinel} /></td>
								<td>{formatUSD(r.usd)}</td>
								<td><span class="badge {r.class === 'quiet' ? 'badge-yellow' : 'badge-grey'}">{r.class}</span></td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		{/if}
	</section>

	<section class="card">
		<h2>New payees of his wallets</h2>
		<p class="text-muted">
			Payments from the treasury or <code>0xf078969e…</code> to an address no cluster wallet has
			touched. {data.watch?.l1_seen?.length?.toLocaleString() ?? '—'} addresses on record as seen.
		</p>
		{#if pendingPayees.length === 0}
			<p class="text-muted">Nothing awaiting a reading.</p>
		{:else}
			<p>Awaiting a whole-chain reading before they can be judged:</p>
			<ul>
				{#each pendingPayees as a}<li><Addr address={a} /></li>{/each}
			</ul>
		{/if}
	</section>
{/if}

<style>
	h2 { display: flex; gap: 10px; align-items: center; font-size: 1.1rem; margin-bottom: 12px; }
	h3 { font-size: 0.95rem; margin: 16px 0 8px; }
	section { margin-bottom: 20px; }
	table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
	th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
	.digest { list-style: none; padding: 0; margin: 0; font-size: 0.9rem; }
	.digest li { padding: 4px 0; border-bottom: 1px solid var(--border); }
</style>
