<script>
	import { onMount } from 'svelte';
	import {
		fetchTransferGraph, fetchCandidates, fetchScanResults,
		formatUSD, shortAddr, formatTime, getThresholds, explorerTx
	} from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';

	let graph = null;
	let candidates = null;
	let scan = null;
	let loading = true;
	let expanded = null;
	let showServices = false;

	onMount(async () => {
		[graph, candidates, scan] = await Promise.all([
			fetchTransferGraph(),
			fetchCandidates(),
			fetchScanResults()
		]);
		loading = false;
	});

	// Strongest first; services are noise and collapse behind a toggle.
	$: nodes = (graph?.nodes || [])
		.filter((n) => showServices || n.classification !== 'SERVICE')
		.filter(matchesFilter);
	$: thresholds = getThresholds(scan);
	$: edgesById = Object.fromEntries((graph?.edges || []).map((e) => [e.id, e]));

	$: counts = (graph?.nodes || []).reduce((acc, n) => {
		acc[n.classification] = (acc[n.classification] || 0) + 1;
		return acc;
	}, {});

	const CLASS_LABEL = {
		MIGRATION_CANDIDATE: 'Migration candidate',
		POSSIBLE_LINKED_WALLET: 'Possible linked wallet',
		OPERATIONAL_COUNTERPARTY: 'Operational counterparty',
		DIRECT_RECIPIENT: 'Direct recipient',
		SERVICE: 'Exchange / bridge / service'
	};

	const CLASS_BADGE = {
		MIGRATION_CANDIDATE: 'badge-red',
		POSSIBLE_LINKED_WALLET: 'badge-yellow',
		OPERATIONAL_COUNTERPARTY: 'badge-cyan',
		DIRECT_RECIPIENT: 'badge-grey',
		SERVICE: 'badge-grey'
	};

	// --- wallet continuity -------------------------------------------------------
	// Wording is deliberate: these are fund-flow LEADS, never ownership claims.
	const LIFECYCLE_LABEL = {
		HIGH_CONFIDENCE_SUCCESSOR: 'High-confidence continuity lead',
		POSSIBLE_SUCCESSOR: 'Possible successor',
		TRADING_STARTED: 'Funded, now trading',
		FUNDED_BY_TARGET: 'Fund-flow-linked',
		OBSERVED: 'Observed',
		LEAD: 'Lead',
		DORMANT: 'Dormant',
		REJECTED_SERVICE: 'Exchange / bridge / service'
	};
	const LIFECYCLE_BADGE = {
		HIGH_CONFIDENCE_SUCCESSOR: 'badge-red',
		POSSIBLE_SUCCESSOR: 'badge-yellow',
		TRADING_STARTED: 'badge-yellow',
		FUNDED_BY_TARGET: 'badge-cyan',
		OBSERVED: 'badge-grey',
		LEAD: 'badge-grey',
		DORMANT: 'badge-grey',
		REJECTED_SERVICE: 'badge-grey'
	};

	let filterMode = 'all';
	const FILTERS = [
		['all', 'All'],
		['high', 'High confidence'],
		['successors', 'Possible successors'],
		['trading', 'Active traders'],
		['incomplete', 'Incomplete paths'],
		['services', 'Services']
	];

	$: chainById = Object.fromEntries((graph?.chains || []).map((c) => [c.id, c]));

	function chainFor(n) {
		return n?.chain_id ? (chainById[n.chain_id] ?? null) : null;
	}

	function lifecycleOf(n) {
		return n?.lifecycle?.state ?? null;
	}

	/** Reasons this wallet cannot be promoted, including contradictions. */
	function blockersOf(n) {
		return [...(n?.lifecycle?.blockers ?? []), ...(n?.continuity?.blockers ?? [])]
			.filter((b, i, all) => all.indexOf(b) === i);
	}

	/** Per-signal contributions behind the continuity score. */
	function contributionsOf(n) {
		return n?.continuity?.reasons ?? [];
	}

	function matchesFilter(n) {
		const life = lifecycleOf(n);
		const ch = chainFor(n);
		if (filterMode === 'high') return life === 'HIGH_CONFIDENCE_SUCCESSOR';
		if (filterMode === 'successors')
			return life === 'POSSIBLE_SUCCESSOR' || life === 'HIGH_CONFIDENCE_SUCCESSOR';
		if (filterMode === 'trading') return !!n.evidence?.trades_on_hl;
		if (filterMode === 'incomplete')
			return !!(ch?.breaks?.length || n.path_truncated || ch?.complete === false);
		if (filterMode === 'services') return n.classification === 'SERVICE';
		return true;
	}

	/** "+12" / "−4" points of change since the previous run, or null. */
	function deltaLabel(d) {
		if (d == null || Math.abs(d) < 0.005) return null;
		const pts = Math.round(d * 100);
		return `${pts > 0 ? '+' : '−'}${Math.abs(pts)}`;
	}

	/** One-line answer to "why does this wallet matter?" */
	function whyItMatters(n) {
		const life = lifecycleOf(n);
		const ch = chainFor(n);
		const hops = ch?.hop_count ?? n.depth;
		const retained =
			ch?.value_retained != null
				? ` retaining ${(ch.value_retained * 100).toFixed(0)}% of the value`
				: '';
		if (life === 'HIGH_CONFIDENCE_SUCCESSOR')
			return `Target funds reached this wallet across ${hops} hop(s)${retained}, and it trades like the target. Strongest continuity lead — still a lead, not proof of ownership.`;
		if (life === 'POSSIBLE_SUCCESSOR')
			return `Fund-flow-linked across ${hops} hop(s)${retained} with corroborating evidence, and trading. Worth watching.`;
		if (life === 'TRADING_STARTED')
			return `Received target funds and began trading afterwards${retained}. Needs a second independent signal to promote.`;
		if (life === 'FUNDED_BY_TARGET')
			return `Target funds reached this wallet over an unbroken path of ${hops} hop(s)${retained}.`;
		if (life === 'REJECTED_SERVICE')
			return 'Exchange, bridge or high-fan-degree address. Excluded from continuity scoring by design.';
		return 'Appears on a fund-flow path from the target. Insufficient evidence to say more.';
	}

	// --- graph health -----------------------------------------------------------
	$: health = graph?.health ?? null;
	$: expansion = health?.expansion ?? null;

	const EXPANSION_LABEL = {
		ok: 'Completed',
		budget_exhausted: 'Stopped at budget',
		skipped_no_api_key: 'Skipped — no ETHERSCAN_API_KEY',
		failed: 'Failed',
		disabled: 'Disabled for this run',
		not_attempted: 'Not attempted'
	};

	function fmtDate(iso) {
		if (!iso) return '—';
		const d = new Date(iso);
		return isNaN(d) ? '—' : d.toLocaleString();
	}

	/** Behavioural watchlist score for a wallet, if the scanner has one. */
	function behaviouralScore(wallet) {
		const c = (candidates?.candidates || []).find(
			(x) => x.wallet?.toLowerCase() === wallet?.toLowerCase()
		);
		return c ? c.best_score : null;
	}

	function edgesFor(node) {
		return (node.edge_ids || [])
			.map((id) => edgesById[id])
			.filter(Boolean)
			.sort((a, b) => (b.ts || 0) - (a.ts || 0));
	}

	function toggle(wallet) {
		expanded = expanded === wallet ? null : wallet;
	}
</script>

<svelte:head><title>Transfers — Ezekiel</title></svelte:head>

<div class="page">
	<header class="page-head">
		<h1>Transfer Graph</h1>
		<p class="text-muted sub">
			Every transfer in and out of the target, normalised across Arbitrum and
			Hyperliquid, walked outward to find related wallets. A transfer is a
			relationship, not proof of ownership — classification is graded and every
			conclusion keeps its evidence.
		</p>
	</header>

	{#if loading}
		<div class="card">Loading transfer graph…</div>
	{:else if !graph}
		<div class="card">
			<strong>No transfer graph yet.</strong>
			<p class="text-muted">
				It is built by the trace workflow (<code>src/transfer_graph.py</code>).
				Once that has run, discovered wallets appear here.
			</p>
		</div>
	{:else}
		<div class="stat-row">
			<div class="stat">
				<div class="stat-label">Wallets found</div>
				<div class="stat-value mono">{graph.node_count}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Transfers</div>
				<div class="stat-value mono">{graph.edge_count}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Max depth</div>
				<div class="stat-value mono">{graph.max_depth_reached}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Services excluded</div>
				<div class="stat-value mono">{graph.service_count}</div>
			</div>
			<div class="stat">
				<div class="stat-label">Migration candidates</div>
				<div class="stat-value mono" class:text-red={counts.MIGRATION_CANDIDATE > 0}>
					{counts.MIGRATION_CANDIDATE || 0}
				</div>
			</div>
		</div>

		{#if health}
			<div class="card health" class:health-warn={health.frontier_incomplete}>
				<div class="health-head">
					<strong>Graph health</strong>
					{#if health.frontier_incomplete}
						<span class="badge badge-yellow">frontier incomplete</span>
					{:else}
						<span class="badge badge-cyan">fully explored</span>
					{/if}
				</div>
				<p class="health-note text-muted">
					{#if health.frontier_incomplete}
						This graph may be smaller than reality — read an absent link as “not
						looked for”, not “not there”.
					{:else}
						Every reachable wallet within budget was explored.
					{/if}
				</p>
				<div class="health-grid">
					<div>
						<span class="hl">L1 expansion</span>
						<span
							class="mono"
							class:text-red={expansion?.status === 'failed'}
							class:text-yellow={expansion &&
								!['ok', 'failed'].includes(expansion.status)}
							>{EXPANSION_LABEL[expansion?.status] ?? expansion?.status ?? '—'}</span
						>
					</div>
					<div>
						<span class="hl">Last successful expansion</span>
						<span class="mono"
							>{fmtDate(
								expansion?.status === 'ok'
									? expansion?.completed_at
									: expansion?.last_successful
							)}</span
						>
					</div>
					<div>
						<span class="hl">Explored</span>
						<span class="mono"
							>{health.nodes_explored} nodes / {health.edges_explored} edges</span
						>
					</div>
					<div>
						<span class="hl">Depth</span>
						<span class="mono"
							>{health.max_depth_reached} of {health.max_depth_configured}
							{#if health.depth_limited}<span class="text-yellow">(capped)</span>{/if}</span
						>
					</div>
					<div>
						<span class="hl">Node budget</span>
						<span class="mono"
							>{health.node_budget}
							{#if health.node_budget_exhausted}<span class="text-yellow"
									>(exhausted)</span
								>{/if}</span
						>
					</div>
					<div>
						<span class="hl">L1 lookups used</span>
						<span class="mono"
							>{expansion?.lookups ?? 0} / {expansion?.lookup_budget ?? '—'}
							{#if expansion?.frontier_remaining}<span class="text-yellow"
									>({expansion.frontier_remaining} wallet(s) still queued)</span
								>{/if}</span
						>
					</div>
					<div>
						<span class="hl">Frontier</span>
						<span class="mono">
							{#if expansion?.frontier_eligible}
								<span class="text-yellow"
									>{expansion.frontier_retained ?? expansion.frontier_eligible} retained
									of {expansion.frontier_eligible} eligible</span
								>
								{#if expansion?.frontier_cap}<span class="text-muted"
										>(cap {expansion.frontier_cap})</span
									>{/if}
							{:else if expansion?.frontier_remaining}
								<span class="text-yellow"
									>{expansion.frontier_remaining} queued for the next run</span
								>
							{:else}
								drained
							{/if}
							{#if expansion?.frontier_truncated}<span class="text-red"
									>· {expansion.frontier_truncated} dropped permanently (lowest chase
									priority)</span
								>{/if}
						</span>
					</div>
					<div>
						<span class="hl">Already expanded</span>
						<span class="mono"
							>{(expansion?.expanded_ledger || []).length} wallet(s) — not re-fetched</span
						>
					</div>
					<div>
						<span class="hl">Evidence window</span>
						<span class="mono"
							>{fmtDate(health.oldest_evidence)} → {fmtDate(health.newest_evidence)}</span
						>
					</div>
					<div>
						<span class="hl">Sources</span>
						<span class="mono">{(health.discovery_sources || []).join(', ') || '—'}</span>
					</div>
				</div>
				{#if expansion?.stopped_reason}
					<p class="health-degraded">
						Walk stopped early: <span class="mono">{expansion.stopped_reason}</span>. The
						queued wallets are carried into the next run.
					</p>
				{/if}
				{#if expansion?.partial_failures?.length}
					<p class="health-degraded">
						{expansion.partial_failures.length} lookup(s) failed and were re-queued rather
						than recorded as explored.
					</p>
				{/if}
				{#if health.degraded_sources?.length}
					<p class="health-degraded">
						Degraded source{health.degraded_sources.length > 1 ? 's' : ''}:
						<span class="mono">{health.degraded_sources.join(', ')}</span>
						{#if expansion?.status === 'skipped_no_api_key'}
							— set the <span class="mono">ETHERSCAN_API_KEY</span> secret to enable
							multi-hop L1 tracing.
						{:else if expansion?.error}
							— <span class="mono">{expansion.error}</span>
						{/if}
					</p>
				{/if}
			</div>
		{/if}

		<div class="filters">
			{#each FILTERS as [key, label]}
				<button
					class="filter-btn"
					class:active={filterMode === key}
					onclick={() => (filterMode = key)}>{label}</button
				>
			{/each}
		</div>

		<div class="toolbar">
			<span class="text-muted">
				Updated {graph.computed_at ? new Date(graph.computed_at).toLocaleString() : '—'}
			</span>
			<label class="toggle">
				<input type="checkbox" bind:checked={showServices} />
				Show exchange / bridge addresses
			</label>
		</div>

		{#if !nodes.length}
			<div class="card">No related wallets discovered yet.</div>
		{/if}

		{#each nodes as n (n.wallet)}
			{@const bScore = behaviouralScore(n.wallet)}
			<div class="card node" class:is-candidate={n.classification === 'MIGRATION_CANDIDATE'}>
				<button class="node-head" onclick={() => toggle(n.wallet)}>
					<div class="node-id">
						<span class="badge {CLASS_BADGE[n.classification]}">
							{CLASS_LABEL[n.classification] || n.classification}
						</span>
						<Addr address={n.wallet} className="mono addr" />
						{#if n.multi_hop}<span class="badge badge-grey">{n.depth} hops</span>{/if}
						{#each n.chains ?? [] as c}<span class="badge badge-grey">{c}</span>{/each}
						{#if n.is_new}<span class="badge badge-cyan">new</span>{/if}
					</div>
					<div class="node-conf">
						<span class="text-muted">confidence</span>
						<strong class="mono">{((n.confidence ?? 0) * 100).toFixed(0)}%</strong>
						{#if deltaLabel(n.confidence_delta)}
							<span
								class="delta mono"
								class:text-red={n.confidence_delta > 0}
								class:text-muted={n.confidence_delta < 0}
								title="change since the previous run"
								>{deltaLabel(n.confidence_delta)}</span
							>
						{/if}
						<span class="chev">{expanded === n.wallet ? '▾' : '▸'}</span>
					</div>
				</button>

				<div class="meter" aria-hidden="true">
					<div class="meter-fill" style="width:{Math.max(2, (n.confidence ?? 0) * 100)}%"></div>
				</div>

				<div class="path mono">
					{#each n.path ?? [] as hop, i}
						{#if i > 0}<span class="arrow">→</span>{/if}<Addr
							address={hop}
							className={i === 0 ? 'hop hop-target' : 'hop'} />
					{/each}
					{#if n.path_truncated}
						<span class="text-yellow"
							>· path unverified beyond <Addr address={n.path_truncated_at} className="" /></span
						>
					{/if}
				</div>

				<div class="flows">
					<span>Received from target <strong class="mono"
							>{formatUSD(n.totals?.received_from_target_usd ?? 0)}</strong
						></span>
					<span>Sent to target <strong class="mono"
							>{formatUSD(n.totals?.sent_to_target_usd ?? 0)}</strong
						></span>
					<span>Transfers <strong class="mono">{n.totals?.edge_count ?? 0}</strong></span>
					{#if bScore != null}
						<span>
							Behavioural
							<strong
								class="mono"
								class:text-green={bScore >= thresholds.high}
								class:text-yellow={bScore >= thresholds.medium && bScore < thresholds.high}
								>{(bScore * 100).toFixed(0)}%</strong
							>
							{#if bScore >= thresholds.high}
								<span class="badge badge-red">alerting tier</span>
							{:else if bScore >= thresholds.low}
								<span class="badge badge-cyan">watchlist</span>
							{/if}
						</span>
					{:else}
						<span class="text-muted">Not on behavioural watchlist</span>
					{/if}
					{#if n.evidence?.trades_on_hl}
						<span class="badge badge-yellow">trading on HL</span>
					{/if}
				</div>

				{#if lifecycleOf(n)}
					<div class="lifecycle">
						<span class="badge {LIFECYCLE_BADGE[lifecycleOf(n)] || 'badge-grey'}"
							>{LIFECYCLE_LABEL[lifecycleOf(n)] || lifecycleOf(n)}</span
						>
						{#if n.previous_lifecycle && n.previous_lifecycle !== lifecycleOf(n)}
							<span class="text-muted"
								>was {LIFECYCLE_LABEL[n.previous_lifecycle] ?? n.previous_lifecycle}</span
							>
						{/if}
						{#if n.lifecycle?.dormant}
							<span class="badge badge-grey"
								>silent {Math.round(n.lifecycle.days_inactive ?? 0)}d</span
							>
						{/if}
						{#if n.continuity}
							<span class="text-muted">continuity</span>
							<strong class="mono"
								>{((n.continuity.confidence ?? 0) * 100).toFixed(0)}%</strong
							>
							{#if deltaLabel(n.continuity_delta)}
								<span
									class="delta mono"
									class:text-red={n.continuity_delta > 0}
									class:text-muted={n.continuity_delta < 0}
									title="change since the previous run"
									>{deltaLabel(n.continuity_delta)}</span
								>
							{/if}
						{/if}
						{#if n.continuity?.families?.length}
							<span class="text-muted"
								>{n.continuity.families.length} evidence famil{n.continuity
									.families.length === 1
									? 'y'
									: 'ies'}: {n.continuity.families.join(', ')}</span
							>
						{/if}
					</div>
					<p class="why">{whyItMatters(n)}</p>
					{#if contributionsOf(n).length}
						<details class="contrib">
							<summary>What the continuity score is made of</summary>
							<ul>
								{#each contributionsOf(n) as r}<li>{r}</li>{/each}
							</ul>
						</details>
					{/if}
					{#if blockersOf(n).length}
						<ul class="blockers">
							{#each blockersOf(n) as b}<li>{b}</li>{/each}
						</ul>
					{/if}
				{/if}

				{#if chainFor(n)}
					{@const ch = chainFor(n)}
					<div class="chain">
						<div class="chain-head">
							<strong>Fund-flow path</strong>
							<span class="text-muted"
								>{ch.hop_count ?? (ch.hops ?? []).length} hop(s) · {(
									(ch.value_retained ?? 0) * 100
								).toFixed(0)}% of value retained · {ch.elapsed_hours ?? 0}h
								{#if ch.relay_hops?.length}· {ch.relay_hops.length} relay hop(s){/if}</span
							>
						</div>
						{#each ch.hops ?? [] as h, i}
							<div class="hop-row">
								<span class="hop-n mono">{i + 1}</span>
								<span class="mono"
									><Addr address={h.src} /> → <Addr address={h.dst} /></span
								>
								<span class="mono">{formatUSD(h.amount_usd)}</span>
								<span class="text-muted">{h.chain}</span>
								<span class="text-muted">{h.ts ? formatTime(h.ts * 1000) : '—'}</span>
								{#if explorerTx(h)}
									<a href={explorerTx(h)} target="_blank" rel="noopener noreferrer"
										>{shortAddr(h.ref)}</a
									>
								{/if}
							</div>
						{/each}
						{#each ch.breaks ?? [] as b}
							<div class="chain-break">
								Path break at <Addr address={b.at} />: {b.reason}
							</div>
						{/each}
					</div>
				{/if}

				<ul class="reasons">
					{#each n.confidence_reasons ?? [] as r}
						<li>{r}</li>
					{/each}
				</ul>

				{#if expanded === n.wallet}
					<div class="detail">
						<div class="detail-meta">
							<span>First seen <span class="mono">{n.first_seen || '—'}</span></span>
							<span>Last seen <span class="mono">{n.last_seen || '—'}</span></span>
							<span>Discovered via <span class="mono"
									>{(n.discovery_sources || []).join(', ')}</span
								></span>
						</div>
						<h4>Transfers ({edgesFor(n).length})</h4>
						<div class="table-scroll">
							<table>
								<thead>
									<tr>
										<th>Time</th><th>Chain</th><th>Asset</th>
										<th class="num">Amount</th><th>Direction</th><th>Reference</th>
									</tr>
								</thead>
								<tbody>
									{#each edgesFor(n).slice(0, 40) as e}
										<tr>
											<td class="mono">{e.ts ? formatTime(e.ts * 1000) : '—'}</td>
											<td>{e.chain}</td>
											<td class="mono">{e.asset}</td>
											<td class="num mono">{formatUSD(e.amount_usd)}</td>
											<td class="mono dir"
												><Addr address={e.src} /> → <Addr address={e.dst} /></td
											>
											<td class="mono ref"
												>{#if explorerTx(e)}<a
														href={explorerTx(e)}
														target="_blank"
														rel="noopener noreferrer">{shortAddr(e.ref)}</a
													>{:else}—{/if}</td
											>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
						{#if edgesFor(n).length > 40}
							<p class="text-muted">Showing 40 of {edgesFor(n).length} transfers.</p>
						{/if}
						{#if n.classification === 'DIRECT_RECIPIENT' || n.classification === 'OPERATIONAL_COUNTERPARTY'}
							<p class="caveat">
								A transfer relationship is not proof of common ownership. This wallet
								is a lead for review, not an identification.
							</p>
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	{/if}
</div>

<style>
	.page {
		max-width: 1100px;
	}
	.page-head h1 {
		margin: 0 0 4px;
	}
	.sub {
		max-width: 70ch;
		font-size: 0.85rem;
		line-height: 1.5;
		margin: 0 0 20px;
	}
	.stat-row {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		margin-bottom: 16px;
	}
	.stat {
		flex: 1 1 130px;
		background: var(--bg-card, #12121a);
		border: 1px solid var(--border, #2a2a4a);
		border-radius: 8px;
		padding: 10px 14px;
	}
	.stat-label {
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-muted, #8888a0);
	}
	.stat-value {
		font-size: 1.35rem;
		font-weight: 600;
	}
	.toolbar {
		display: flex;
		justify-content: space-between;
		align-items: center;
		flex-wrap: wrap;
		gap: 10px;
		margin-bottom: 12px;
		font-size: 0.75rem;
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
		color: var(--text-muted, #8888a0);
	}
	.node {
		margin-bottom: 12px;
	}
	.node.is-candidate {
		border-color: var(--accent-red, #ef4444);
	}
	.node-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
		width: 100%;
		background: none;
		border: 0;
		padding: 0;
		color: inherit;
		font: inherit;
		cursor: pointer;
		text-align: left;
		flex-wrap: wrap;
	}
	.node-id {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.addr {
		font-size: 0.9rem;
		font-weight: 600;
	}
	.node-conf {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.8rem;
	}
	.chev {
		color: var(--text-muted, #8888a0);
	}
	.meter {
		height: 3px;
		background: var(--border, #2a2a4a);
		border-radius: 2px;
		margin: 10px 0;
		overflow: hidden;
	}
	.meter-fill {
		height: 100%;
		background: var(--accent-cyan, #00ccdd);
	}
	.is-candidate .meter-fill {
		background: var(--accent-red, #ef4444);
	}
	.path {
		font-size: 0.75rem;
		margin-bottom: 8px;
		overflow-x: auto;
		white-space: nowrap;
		padding-bottom: 2px;
	}
	.hop-target {
		color: var(--accent-cyan, #00ccdd);
	}
	.arrow {
		color: var(--text-muted, #8888a0);
		margin: 0 6px;
	}
	.flows {
		display: flex;
		flex-wrap: wrap;
		gap: 16px;
		font-size: 0.75rem;
		margin-bottom: 8px;
		align-items: center;
	}
	.reasons {
		margin: 0;
		padding-left: 18px;
		font-size: 0.75rem;
		color: var(--text-secondary, #b0b0c8);
	}
	.reasons li {
		margin-bottom: 2px;
	}
	.detail {
		margin-top: 14px;
		padding-top: 12px;
		border-top: 1px solid var(--border, #2a2a4a);
	}
	.detail-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 16px;
		font-size: 0.7rem;
		color: var(--text-muted, #8888a0);
		margin-bottom: 10px;
	}
	.detail h4 {
		margin: 0 0 8px;
		font-size: 0.8rem;
	}
	.table-scroll {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.7rem;
	}
	th,
	td {
		text-align: left;
		padding: 5px 8px;
		border-bottom: 1px solid var(--border, #2a2a4a);
		white-space: nowrap;
	}
	th {
		color: var(--text-muted, #8888a0);
		font-weight: 500;
		text-transform: uppercase;
		font-size: 0.6rem;
		letter-spacing: 0.05em;
	}
	.num {
		text-align: right;
	}
	.dir,
	.ref {
		color: var(--text-secondary, #b0b0c8);
	}
	.caveat {
		font-size: 0.72rem;
		color: var(--text-muted, #8888a0);
		border-left: 2px solid var(--border, #2a2a4a);
		padding-left: 10px;
		margin: 12px 0 0;
	}
	.badge {
		display: inline-block;
		padding: 2px 7px;
		border-radius: 4px;
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		font-weight: 600;
	}
	.badge-red {
		background: rgba(239, 68, 68, 0.15);
		color: #ef4444;
	}
	.badge-yellow {
		background: rgba(245, 158, 11, 0.15);
		color: #f59e0b;
	}
	.badge-cyan {
		background: rgba(0, 204, 221, 0.15);
		color: #00ccdd;
	}
	.badge-grey {
		background: rgba(136, 136, 160, 0.15);
		color: #8888a0;
	}
	.text-red {
		color: #ef4444;
	}
	.text-green {
		color: #10b981;
	}
	.text-yellow {
		color: #f59e0b;
	}
	@media (max-width: 640px) {
		.node-head {
			align-items: flex-start;
		}
	}

	.health {
		margin-bottom: 14px;
		border-left: 3px solid var(--accent-cyan, #00ccdd);
	}
	.health.health-warn {
		border-left-color: var(--accent-yellow, #f59e0b);
	}
	.health-head {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-bottom: 4px;
	}
	.health-note {
		font-size: 0.72rem;
		margin: 0 0 10px;
	}
	.health-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
		gap: 8px 18px;
		font-size: 0.72rem;
	}
	.health-grid > div {
		display: flex;
		flex-direction: column;
		gap: 1px;
		min-width: 0;
	}
	.hl {
		color: var(--text-muted, #8888a0);
		font-size: 0.62rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.health-grid .mono {
		overflow-wrap: anywhere;
	}
	.health-degraded {
		margin: 10px 0 0;
		font-size: 0.72rem;
		color: var(--accent-yellow, #f59e0b);
	}

	.filters {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		margin-bottom: 10px;
	}
	.filter-btn {
		font-size: 0.66rem;
		padding: 3px 10px;
		border-radius: 4px;
		border: 1px solid var(--border, #2a2a4a);
		background: transparent;
		color: var(--text-muted, #8888a0);
		cursor: pointer;
		font-family: inherit;
	}
	.filter-btn.active,
	.filter-btn:hover {
		color: var(--accent-cyan, #00ccdd);
		border-color: var(--accent-cyan, #00ccdd);
	}
	.lifecycle {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		font-size: 0.72rem;
		margin: 8px 0 4px;
	}
	.why {
		font-size: 0.74rem;
		color: var(--text-secondary, #b0b0c8);
		margin: 0 0 8px;
		line-height: 1.45;
	}
	.blockers {
		margin: 0 0 8px;
		padding-left: 18px;
		font-size: 0.7rem;
		color: var(--accent-yellow, #f59e0b);
	}
	.chain {
		border-left: 2px solid var(--accent-cyan, #00ccdd);
		padding: 6px 10px;
		margin: 8px 0;
		background: rgba(0, 204, 221, 0.04);
		font-size: 0.7rem;
		overflow-x: auto;
	}
	.chain-head {
		display: flex;
		gap: 10px;
		flex-wrap: wrap;
		margin-bottom: 4px;
	}
	.hop-row {
		display: flex;
		gap: 10px;
		align-items: center;
		white-space: nowrap;
		padding: 1px 0;
	}
	.hop-n {
		color: var(--text-muted, #8888a0);
		min-width: 1.2em;
	}
	.chain-break {
		color: var(--accent-yellow, #f59e0b);
		margin-top: 4px;
	}
	.delta {
		font-size: 0.66rem;
		padding: 1px 5px;
		border-radius: 3px;
		background: rgba(255, 255, 255, 0.06);
	}
	.contrib {
		font-size: 0.7rem;
		margin: 0 0 8px;
		color: var(--text-secondary, #b0b0c8);
	}
	.contrib summary {
		cursor: pointer;
		color: var(--text-muted, #8888a0);
	}
	.contrib ul {
		margin: 4px 0 0;
		padding-left: 18px;
	}
</style>
