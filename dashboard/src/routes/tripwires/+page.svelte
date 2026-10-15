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
	$: pendingDecisions = data?.decisions?.pending || [];
	$: book = Object.entries(data?.positions?.aggregate || {});
	$: bookChanges = Object.entries(data?.positions?.changes || {});
	$: history = data?.cofireHistory;
	$: historyHits = (history?.hits || []).slice(0, 15);
	$: historyControls = history?.controls || [];
	$: replica = data?.replica;
	$: replicaWallets = Object.values(replica?.wallets || {}).filter((w) => (w.actions || 0) > 0)
		.sort((a, b) => (b.actions || 0) - (a.actions || 0));
	$: replicaReused = replica?.agent_reused || [];
	$: treasury = data?.treasury;
	$: treasuryKnown = Object.entries(treasury?.known_wallets || {});
	$: treasuryTop = (treasury?.top || []).slice(0, 10);

	function size(x) {
		const v = Math.abs(Number(x) || 0);
		return v >= 100 ? v.toLocaleString('en-GB', { maximumFractionDigits: 0 }) : String(v);
	}

	function describeChange(c) {
		const a = c.after, b = c.before;
		if (c.kind === 'opened') return `OPENED ${a.side} ${c.coin} ${size(a.size)} (${formatUSD(a.value)})`;
		if (c.kind === 'closed') return `CLOSED ${b.side} ${c.coin} ${size(b.size)} (${formatUSD(b.value)})`;
		if (c.kind === 'flipped') return `FLIPPED ${c.coin} ${b.side} ${size(b.size)} → ${a.side} ${size(a.size)}`;
		return `${a.side.toUpperCase()} ${c.coin} ${size(b.size)} → ${size(a.size)} (${formatUSD(a.value)})`;
	}

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
						{#if gate?.tape}{gate.tape.sketched?.toLocaleString()} wallets sketched over {gate.tape.windows} windows, {gate.tape.hits} program hit(s){#if data?.tape?.source === 'sqd'}; fed continuously ({Math.round((data.tape.window_seconds || 0) / 60)} min last run){:else}; websocket sample{/if}{#if data?.tape?.cofire}; {data.tape.cofire.hits} co-firing hit(s) over {data.tape.cofire.his_windows} window(s) with him{/if}{:else}no window folded yet{/if}
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
		<h2>
			Needs your ruling
			{#if pendingDecisions.length}<span class="badge badge-yellow">{pendingDecisions.length} waiting</span>{:else}<span class="badge badge-green">queue empty</span>{/if}
		</h2>
		<p class="text-muted">
			Questions the pipeline cannot settle: a suspect that is not a Hyperliquid account can only be
			joined to him by you, and a wallet watched as a question stays one until ruled. A ruling is
			ground truth (<code>config.json</code>); record it with the command shown.
		</p>
		{#if !pendingDecisions.length}
			<p class="text-muted">Nothing waits on you.</p>
		{:else}
			<ul class="decisions">
				{#each pendingDecisions as d}
					<li>
						<div><Addr address={d.address} />
							{#if d.kind === 'suspect'}<span class="badge badge-grey">{(100 * d.p_now).toFixed(2)}% · {d.on_hl ? 'on HL' : 'not on HL'}</span>
							{:else}<span class="badge badge-grey">watched {d.age_days ?? '?'} d</span>{/if}
						</div>
						<div class="why">{d.why}</div>
						<div class="text-muted">{d.ask}</div>
						<code>{d.command}</code>
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<section class="card">
		<h2>His book now</h2>
		<p class="text-muted">
			Every position across every wallet of his the close watch reads (the target, his known
			wallets, roster CONFIRMED), aggregated the way his FTX "all subaccounts" view was. A change
			between two readings is pushed to the phone as one notice per run.
			{#if data?.positions?.computed_at}Read {when(data.positions.computed_at)}.{/if}
		</p>
		{#if !book.length}
			<p class="text-muted">No book read yet — written by <code>scripts/check_watchlist.py</code> on the watch job.</p>
		{:else}
			<table>
				<thead><tr><th>Market</th><th>Side</th><th>Size</th><th>Net value</th><th>Wallets</th></tr></thead>
				<tbody>
					{#each book as [coin, p]}
						<tr>
							<td>{coin}</td>
							<td><span class="badge {p.side === 'long' ? 'badge-green' : 'badge-red'}">{p.side}</span></td>
							<td>{size(p.size)}</td>
							<td>{formatUSD(Math.abs(p.net_value))}</td>
							<td>{p.wallets.length}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if bookChanges.length}
				<h3>Changed on the last reading</h3>
				<ul>
					{#each bookChanges as [wallet, changes]}
						{#each changes as c}<li><Addr address={wallet} /> — {describeChange(c)}</li>{/each}
					{/each}
				</ul>
			{/if}
		{/if}
	</section>

	<section class="card">
		<h2>
			Co-firing over his history
			{#if history}<span class="badge badge-grey">{history.coverage?.sessions_read ?? 0}/{history.coverage?.sessions ?? 0} sessions</span>{/if}
		</h2>
		<p class="text-muted">
			Who opened his bursts WITH him, to the second, over every session he has traded since
			February — read from SQD's fill portal around each stored session, chance-corrected by each
			wallet's own order rate. His known wallets are scored like strangers: the positive controls.
			A hit leads or ties him on several bursts; a wallet that only follows is a copier.
		</p>
		{#if !history}
			<p class="text-muted">No census yet — written by <code>scripts/census_cofire_history.py</code> on the tape job.</p>
		{:else}
			<p>
				Base rate: {history.base_rate?.hits ?? 0} of {history.base_rate?.wallets ?? 0} wallets with ≥{history.base_rate?.min_events} events clear the bar
				({typeof history.base_rate?.hit_rate === 'number' ? `${(100 * history.base_rate.hit_rate).toFixed(2)}%` : 'unmeasured below 20 wallets'}).
				{#each historyControls as c}
					· control <Addr address={c.wallet} />: {c.seen ? `${c.events} events, lift ${c.lift}, lead share ${c.lead_share}, ${c.hit ? 'HIT' : 'no hit'}` : 'never on his tape'}
				{/each}
			</p>
			{#if historyHits.length}
				<table>
					<thead><tr><th>Wallet</th><th>Events</th><th>Lift</th><th>z</th><th>Lead / tie / follow</th><th>Bursts</th><th>Coins</th></tr></thead>
					<tbody>
						{#each historyHits as h}
							<tr>
								<td><Addr address={h.wallet} /></td>
								<td>{h.events}</td>
								<td>{h.lift}</td>
								<td>{h.z}</td>
								<td>{h.lead} / {h.tie} / {h.follow}</td>
								<td>{h.bursts}</td>
								<td>{(h.coins || []).slice(0, 6).join(', ')}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="text-muted">No wallet opens his bursts with him beyond chance so far.</p>
			{/if}
		{/if}
	</section>

	<section class="card">
		<h2>
			His world's action history (SQD replica)
			{#if replica?.dataset}<span class="badge badge-grey">{replica.dataset.behind_blocks != null ? `${Math.round(replica.dataset.behind_blocks / 50400)}h behind the dataset` : 'no cursor'}</span>{/if}
		</h2>
		<p class="text-muted">
			Every non-trading action of every wallet in his world since 2026-01-21 — agents approved,
			Circle recipients, builders, referral codes, failed attempts — from SQD's keyless
			replica-command feed, which trails the chain by about ten days. The explorer shows 300
			actions and rolls a payload out within a day. The one page here: an agent of his that
			Hyperliquid now resolves to another account.
		</p>
		{#if !replica}
			<p class="text-muted">No sweep yet — written by <code>scripts/sweep_replica_actions.py</code> on the trace job.</p>
		{:else}
			<p>
				{replica.users?.length ?? 0} wallets asked · {replica.agents_total ?? 0} agents ever approved ·
				{replica.outside_total ?? 0} destinations outside his perimeter ·
				{replicaReused.length} agent{replicaReused.length === 1 ? '' : 's'} reused by another account
				{#if replica.dataset?.backfill_complete}· history complete{:else}· history {replica.dataset?.backfill_segments_done ?? 0} segments in{/if}
			</p>
			{#each replicaReused as r}
				<p class="text-danger">AGENT REUSED: <Addr address={r.agent} /> approved by <Addr address={r.approved_by} /> now resolves to <Addr address={r.owner} /></p>
			{/each}
			{#if replicaWallets.length}
				<table>
					<thead><tr><th>Wallet</th><th>Actions</th><th>Agents</th><th>Builders</th><th>Circle</th><th>Outside</th><th>Failed</th><th>Last</th></tr></thead>
					<tbody>
						{#each replicaWallets as w}
							<tr>
								<td><Addr address={w.address} /></td>
								<td>{w.actions}</td>
								<td>{(w.agents || []).length}</td>
								<td>{(w.builders || []).length}</td>
								<td>{Object.values(w.circle_recipients || {}).reduce((n, v) => n + v.length, 0)}</td>
								<td>{(w.outside_destinations || []).length}</td>
								<td>{Object.values(w.failed || {}).reduce((n, v) => n + v, 0)}</td>
								<td>{w.last ? w.last.slice(0, 10) : '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		{/if}
	</section>

	<section class="card">
		<h2>
			Treasury-session co-occurrence
			{#if treasury}<span class="badge badge-grey">{treasury.sessions_read ?? 0}/{treasury.sessions_total ?? 0} sessions</span>{/if}
		</h2>
		<p class="text-muted">
			Who does treasury work when he does: every account with a send, withdrawal, Circle send,
			class transfer or agent approval inside the padded window of one of his non-trading
			sessions, counted once per session and only for actions that do not touch him, against
			its own rate on ±7-day control windows. His known wallets are scored like strangers — the
			positive controls. A hit is in ≥{treasury?.bar?.min_sessions ?? 4} sessions at lift ≥{treasury?.bar?.min_lift ?? 5}
			and p ≤ {treasury?.bar?.max_p ?? 0.001}; a hit registers as a candidate and casts no vote.
		</p>
		{#if !treasury}
			<p class="text-muted">No census yet — written by <code>scripts/census_treasury_sessions.py</code> on the tape job.</p>
		{:else}
			<p>
				{treasury.summary?.wallets_seen ?? 0} accounts seen · {treasury.summary?.wallets_with_2plus ?? 0} in two or more of his sessions ·
				{treasury.summary?.controls ?? 0} control windows · {(treasury.hits || []).length} hit(s)
				{#each treasuryKnown as [w, r]}
					· control <Addr address={w} />: {r.sessions_with} session(s), {r.controls_with} control(s){r.lift != null ? `, lift ${r.lift}` : ''}{r.hit ? ', HIT' : ''}
				{/each}
			</p>
			{#if treasuryTop.length}
				<table>
					<thead><tr><th>Wallet</th><th>Sessions with him</th><th>Controls</th><th>Lift</th><th>p</th><th>What it did</th></tr></thead>
					<tbody>
						{#each treasuryTop as r}
							<tr class:row-hit={r.hit}>
								<td><Addr address={r.wallet} /></td>
								<td>{r.sessions_with}/{r.sessions}</td>
								<td>{r.controls_with}/{r.controls}</td>
								<td>{r.lift ?? '—'}</td>
								<td>{r.p ?? '—'}</td>
								<td>{Object.entries(r.kinds || {}).map(([k, n]) => `${k} ×${n}`).join(', ')}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="text-muted">Nobody seen in his sessions yet.</p>
			{/if}
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
	.decisions { list-style: none; padding: 0; margin: 0; }
	.decisions li { padding: 8px 0; border-bottom: 1px solid var(--border); display: grid; gap: 4px; font-size: 0.9rem; }
	.decisions .why { font-weight: 500; }
	.decisions code { font-size: 0.8rem; word-break: break-all; }
</style>
