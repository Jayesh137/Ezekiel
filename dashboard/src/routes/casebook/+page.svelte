<script>
	import { onMount } from 'svelte';
	import { fetchCasebook, fetchCase, formatTime } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';
	import {
		caseRows, familyChips, hlLine, pct, bandText, money, calibrationLines, waterfall, timeline,
		lifeChart, reasonLine, STATUS_LABEL
	} from '$lib/casebook.js';

	let index = null;
	let loading = true;
	let hlOnly = true;
	let includeKnown = true;
	let query = '';
	let open = null;
	let doc = null;
	let docLoading = false;

	onMount(async () => {
		index = await fetchCasebook();
		loading = false;
	});

	const detailId = (a) => `case-detail-${a}`;

	async function toggle(row) {
		if (open === row.address) {
			open = null;
			doc = null;
			return;
		}
		open = row.address;
		doc = null;
		docLoading = true;
		const got = await fetchCase(row.address);
		if (open === row.address) {
			doc = got;
			docLoading = false;
		}
	}

	function meter(central) {
		if (typeof central !== 'number') return 0;
		return Math.max(2, Math.min(100, ((central + 6) / 8) * 100));
	}

	$: rows = caseRows(index, { hlOnly, includeKnown, query });
	$: counts = index?.counts || {};
	$: model = index?.model || {};
	$: kinds = model.kinds || {};
	$: gaps = index?.target?.anomalous_gaps || [];
	$: calibration = calibrationLines(index);
	$: prior = typeof model.prior_log10_odds === 'number' ? model.prior_log10_odds : -3;
</script>

<svelte:head><title>Casebook · Ezekiel</title></svelte:head>

<h1>Casebook</h1>
<p class="lede">
	Every wallet the project has ever suspected, kept for good and ranked by how likely each is
	his. Evidence that lapses is marked, never deleted, and every suspect is re-read on
	Hyperliquid so one waking up is caught. The likelihood is a Bayesian estimate over a 1-in-1,000
	prior from mostly declared likelihood ratios (the model below says which are measured): it
	orders the list; it does not prove ownership.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else if !index}
	<p class="text-muted">
		<strong>Casebook unavailable.</strong> <code>data/casebook/latest.json</code> could not be read.
		It is written by <code>scripts/update_casebook.py</code> (trace.yml, after the roster). This is
		not the same as having no suspects.
	</p>
{:else}
	<div class="stats">
		<span class="badge badge-grey">{counts.unknown ?? '—'} suspects</span>
		<span class="badge badge-grey">{counts.on_hl ?? '—'} on Hyperliquid</span>
		<span class="badge badge-grey">{counts.followable ?? '—'} followable now</span>
		<span class="badge badge-grey">{counts.lapsed_only ?? '—'} rest on history</span>
		<span class="badge badge-grey">{counts.known ?? '—'} known (config)</span>
		<span class="badge badge-grey">{counts.excluded ?? '—'} excluded</span>
	</div>
	{#each calibration as line}<p class="calibration">{line}</p>{/each}

	<div class="filters">
		<label><input type="checkbox" bind:checked={hlOnly} /> Hyperliquid accounts only</label>
		<label><input type="checkbox" bind:checked={includeKnown} /> Show his known wallets</label>
		<input type="search" placeholder="Search an address" bind:value={query} aria-label="Search an address" />
	</div>

	<div class="table-wrap">
		<table>
			<thead>
				<tr>
					<th class="num">#</th><th>Wallet</th><th>Likelihood</th><th>Evidence</th>
					<th>Hyperliquid</th><th>Peak tier</th><th>First seen</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as r (r.address)}
					{@const isOpen = open === r.address}
					<tr class="row" class:known={r.known} on:click={() => toggle(r)}>
						<td class="num">{r.rank ?? (r.known ? 'K' : '—')}</td>
						<td class="wallet">
							<button type="button" class="twisty" aria-expanded={isOpen}
								aria-controls={detailId(r.address)} aria-label="Case file"
								on:click|stopPropagation={() => toggle(r)}>
								<span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
							</button>
							<Addr address={r.address} stopPropagation />
							{#if r.cluster_size > 1}
								<span class="tag" title="One operator with {r.cluster_size - 1} other account(s)">
									cluster of {r.cluster_size}
								</span>
							{/if}
						</td>
						<td class="likely">
							{#if r.known}
								<span class="badge badge-cyan">known</span>
							{:else}
								<strong>{pct(r.p)}</strong>
								<span class="band">{bandText(r)}</span>
								<span class="meter" aria-hidden="true"><span style="width:{meter(r.central)}%"></span></span>
							{/if}
						</td>
						<td>
							{#each familyChips(r) as c}
								<span class="chip" class:down={c.dir === 'down'} class:faded={c.faded}
									title="{c.label}: log10 {c.central.toFixed(2)} ({c.state})">
									{c.dir === 'down' ? '↓' : '↑'} {c.label}
								</span>
							{:else}
								<span class="text-muted">—</span>
							{/each}
							<div class="headline">{reasonLine(r)}</div>
						</td>
						<td class="hl">{hlLine(r)}</td>
						<td class="date">{r.peak_tier ?? '—'}</td>
						<td class="date">{(r.opened_at || '').slice(0, 10) || '—'}</td>
					</tr>
					<tr class="detail" id={detailId(r.address)} hidden={!isOpen}>
						<td colspan="7">
							{#if isOpen}
								{#if docLoading}
									<p class="text-muted">Loading the case file…</p>
								{:else if !doc}
									<p class="text-muted">The case file could not be read.</p>
								{:else}
									{@const chart = lifeChart(doc, gaps, 600, 120)}
									<div class="grid">
										<section>
											<h3>Why this rank</h3>
											<table class="steps">
												<tbody>
													{#each waterfall(doc, prior) as s}
														<tr>
															<td>{s.label}</td>
															<td class="num">{s.delta == null ? '' : `${s.delta > 0 ? '+' : ''}${s.delta.toFixed(2)}`}</td>
															<td class="num">{s.total.toFixed(2)}</td>
														</tr>
													{/each}
												</tbody>
											</table>
											<p class="caveat">log10 odds: 0 is even odds, each +1 ten times more likely.</p>
										</section>
										<section>
											<h3>Life on Hyperliquid</h3>
											{#if chart}
												<svg viewBox="0 0 600 120" class="life" role="img"
													aria-label="Account value over time, with his unusual silences shaded">
													{#each chart.silences as s}
														<rect x={s.x} y="0" width={s.w} height="120" class="silence" />
													{/each}
													<path d={chart.path} class="value" />
													{#if chart.birthX != null}
														<line x1={chart.birthX} x2={chart.birthX} y1="0" y2="120" class="birth" />
													{/if}
												</svg>
												<p class="legend">
													<span class="value">— value (peak {money(chart.top)})</span>
													<span class="silence-key">▮ his unusual silences</span>
													<span class="birth">| birth</span>
												</p>
											{:else}
												<p class="text-muted">No value history read yet.</p>
											{/if}
										</section>
									</div>
									<h3>Evidence, oldest first</h3>
									<ul class="timeline">
										{#each timeline(doc, kinds) as t}
											<li class="s-{t.status}">
												<span class="status">{STATUS_LABEL[t.status] || t.status}</span>
												<strong>{t.label}</strong> — {t.summary}
												<span class="dates">{t.first} → {t.last ?? '—'}{t.days ? ` · ${t.days} day(s)` : ''}</span>
												{#if t.peak}<div class="peak">strongest: {t.peak}</div>{/if}
											</li>
										{:else}
											<li class="text-muted">No evidence items.</li>
										{/each}
									</ul>
									{#if doc.roster?.tiers?.length}
										<p class="tiers">Roster tiers: {doc.roster.tiers.map(([d, t]) => `${d} ${t}`).join(' → ')}</p>
									{/if}
									{#if (doc.roster?.reasons || []).length}
										<p class="tiers">The roster said ({doc.roster.reasons_at}): {doc.roster.reasons.join('; ')}</p>
									{/if}
									<p class="tiers">
										Opened {doc.opened_at} because: {(doc.opened_by || []).join(', ')}. Everything:
										<code>python scripts/casebook.py show {r.address}</code>
									</p>
								{/if}
							{/if}
						</td>
					</tr>
				{:else}
					<tr><td colspan="7" class="text-muted">No case matches.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	<details class="model">
		<summary>The model ({model.version})</summary>
		<p>{model.reading}</p>
		<div class="table-wrap">
			<table>
				<thead><tr><th>Evidence</th><th>Family</th><th>log10 LR low / mid / high</th><th>Basis</th><th>Why</th></tr></thead>
				<tbody>
					{#each Object.entries(kinds) as [, k]}
						<tr>
							<td>{k.label}</td><td>{k.family}</td>
							<td>{Array.isArray(k.band) ? k.band.join(' / ') : k.band}</td>
							<td>{k.basis}</td><td class="why">{k.why}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>

	<p class="text-muted">
		Built {formatTime(Date.parse(index.computed_at))} · roster {index.run?.roster_status ?? '—'}
		{#if index.run?.probes?.attempted != null}
			· {index.run.probes.ok} of {index.run.probes.attempted} Hyperliquid reads this run
		{/if}
	</p>
{/if}

<style>
	.lede { max-width: 60rem; }
	.stats { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0 0.5rem; }
	.calibration { font-size: 0.82rem; margin: 0.15rem 0; opacity: 0.85; }
	.filters { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; margin: 1rem 0; font-size: 0.85rem; }
	.filters input[type='search'] {
		min-width: 14rem; padding: 0.35rem 0.6rem; border-radius: var(--radius-sm);
		border: 1px solid var(--border); background: var(--bg-secondary); color: var(--text-primary);
	}
	.table-wrap { overflow-x: auto; }
	table { width: 100%; border-collapse: collapse; }
	th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
	th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
	.row { cursor: pointer; }
	.row:hover { background: rgba(255, 255, 255, 0.03); }
	.row.known { background: var(--tint-cyan, rgba(34, 211, 238, 0.06)); }
	.wallet { white-space: nowrap; }
	.twisty {
		width: 1.5rem; height: 1.5rem; margin-right: 0.35rem; padding: 0; line-height: 1;
		font-size: 1.1rem; vertical-align: middle; background: none; border: 0; border-radius: 4px;
		color: var(--text-muted); cursor: pointer;
	}
	.twisty:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.06); }
	.tag { margin-left: 0.4rem; font-size: 0.72rem; color: var(--text-muted); }
	.likely { white-space: nowrap; }
	.likely strong { font-variant-numeric: tabular-nums; }
	.band { display: block; font-size: 0.72rem; color: var(--text-muted); }
	.meter { display: block; width: 5.5rem; height: 4px; margin-top: 0.25rem; border-radius: 2px; background: var(--border); }
	.meter span { display: block; height: 100%; border-radius: 2px; background: var(--accent-cyan); }
	.chip {
		display: inline-block; margin: 0 0.25rem 0.25rem 0; font-size: 0.72rem; padding: 0.1rem 0.45rem;
		border-radius: 0.25rem; background: rgba(255, 255, 255, 0.06); white-space: nowrap; color: var(--accent-green);
	}
	.chip.down { color: var(--accent-red); }
	.chip.faded { opacity: 0.55; }
	.headline { font-size: 0.8rem; color: var(--text-secondary); max-width: 34rem; }
	.hl { font-size: 0.82rem; }
	.date { white-space: nowrap; }
	.detail td { background: rgba(255, 255, 255, 0.02); }
	.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
	.steps td { padding: 0.2rem 0.4rem; font-size: 0.85rem; }
	.caveat { font-size: 0.78rem; opacity: 0.75; }
	.life { width: 100%; max-width: 640px; height: auto; }
	.life .value { fill: none; stroke: var(--accent-cyan); stroke-width: 1.5; }
	.life .silence { fill: var(--accent-yellow); opacity: 0.18; }
	.life .birth { stroke: var(--accent-green); stroke-width: 1.5; }
	.legend { display: flex; flex-wrap: wrap; gap: 0 0.75rem; font-size: 0.78rem; opacity: 0.85; }
	.legend .value { color: var(--accent-cyan); }
	.legend .silence-key { color: var(--accent-yellow); }
	.legend .birth { color: var(--accent-green); }
	.timeline { margin: 0.25rem 0 0.5rem 1.1rem; font-size: 0.85rem; line-height: 1.5; }
	.timeline .status {
		display: inline-block; min-width: 5.5rem; font-size: 0.72rem; text-transform: uppercase;
		letter-spacing: 0.03em; color: var(--text-muted);
	}
	.timeline .s-current .status, .timeline .s-standing .status { color: var(--accent-green); }
	.timeline .s-lapsed .status { color: var(--accent-yellow); }
	.timeline .s-refuted .status, .timeline .s-invalidated .status { color: var(--accent-red); }
	.timeline .dates { margin-left: 0.4rem; font-size: 0.75rem; color: var(--text-muted); }
	.timeline .peak { font-size: 0.78rem; color: var(--text-muted); }
	.tiers { font-size: 0.8rem; color: var(--text-secondary); }
	.model { margin: 1.5rem 0 1rem; font-size: 0.85rem; }
	.model summary { cursor: pointer; font-weight: 600; }
	.model .why { max-width: 32rem; }
	h3 { margin: 0.5rem 0; font-size: 0.95rem; }
</style>
