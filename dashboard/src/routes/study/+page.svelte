<script>
	import { onMount } from 'svelte';
	import { fetchStudy, fetchStudyDossier, formatUSD, formatTime, shortAddr } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';
	import {
		familyChips, studyRows, panelStatus, byTestLine, habitRows, histogramPath, maxShare, pct,
		runFaults, faultSummary, partialNote, unreadableMessage, isUnreadable, againstLine,
		statisticLine, sourceLabel, rhythmSeries, rhythmLabel, VERDICT_LABEL
	} from '$lib/study.js';

	let study = null;
	let loading = true;
	let open = null;
	let dossier = null;
	let dossierLoading = false;

	onMount(async () => {
		study = await fetchStudy();
		loading = false;
	});

	// Every row has its detail row in the table from the start (hidden until opened), so
	// the button's aria-controls always names an element that exists.
	const detailId = (wallet) => `study-detail-${wallet}`;

	async function toggle(row) {
		const wallet = row.wallet;
		if (open === wallet) {
			open = null;
			dossier = null;
			return;
		}
		open = wallet;
		dossier = null;
		// An unreadable archive leaves the previous run's dossier file in place, which
		// would show older tests as current. The fault is shown instead and the file is
		// never read.
		if (isUnreadable(row)) {
			dossierLoading = false;
			return;
		}
		dossierLoading = true;
		const doc = await fetchStudyDossier(wallet);
		if (open === wallet) {
			dossier = doc;
			dossierLoading = false;
		}
	}

	$: rows = studyRows(study);
	$: panels = panelStatus(study);
	$: byTest = byTestLine(study);
	$: faults = runFaults(study);
	$: faultParts = faultSummary(faults);
	$: top = dossier ? maxShare(dossier.series?.cadence, study?.reference?.cadence) : 0;
</script>

<svelte:head><title>Study · Ezekiel</title></svelte:head>

<h1>Candidate study</h1>
<p class="lede">
	Every identified Hyperliquid candidate under continuous study, compared with how he makes
	orders. A verdict waits for its panels to be calibrated; until then the raw numbers are
	shown and nothing is called for or against. Evidence that a wallet is not his never changes
	its tier.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else if !study}
	<p class="text-muted">
		<strong>Study unavailable.</strong> The summary (<code>data/study/latest.json</code>) could
		not be read. It is written by <code>scripts/run_study.py</code> (study.yml, every 6 hours).
	</p>
{:else}
	<div class="panels">
		{#each panels as p}
			<span class="badge {p.ok ? 'badge-cyan' : 'badge-grey'}" title={p.title}>
				{p.name}: {p.n ?? '—'} / {p.need}
			</span>
		{/each}
	</div>
	{#if byTest}
		<p class="text-muted by-test">{byTest}</p>
	{/if}

	{#if faultParts.length}
		<div class="faults" class:has-unreadable={faults.unreadable.length > 0}>
			<details>
				<summary>{faultParts.join(' · ')}</summary>
				<ul>
					{#each faults.unreadable as u}
						<li><Addr address={u.wallet} /> unreadable: {u.error || 'no error recorded'}</li>
					{/each}
					{#each faults.partial as p}
						<li>
							<Addr address={p.wallet} /> partial read of {p.source || 'an unknown source'}:
							{p.error || 'no error recorded'}
						</li>
					{/each}
					{#if faults.stopped}
						<li>
							The run stopped on its read budget before reading every wallet; the Last read
							column shows when each was last read.
						</li>
					{/if}
				</ul>
			</details>
		</div>
	{/if}

	<div class="table-wrap">
		<table>
			<thead>
				<tr>
					<th>Wallet</th>
					<th>Why studied</th>
					<th class="num">HL value</th>
					<th>Tooling</th>
					<th class="num">Covered days</th>
					<th>Last read</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as r (r.wallet)}
					{@const isOpen = open === r.wallet}
					{@const note = partialNote(faults, r.wallet)}
					<tr class="row" on:click={() => toggle(r)}>
						<td class="wallet">
							<button type="button" class="twisty" aria-expanded={isOpen}
								aria-controls={detailId(r.wallet)} aria-label="Dossier for {shortAddr(r.wallet)}"
								on:click|stopPropagation={() => toggle(r)}>
								<span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
							</button>
							<Addr address={r.wallet} stopPropagation />
						</td>
						<td>{sourceLabel(r.source)}</td>
						<td class="num">{formatUSD(r.account_value)}</td>
						<td>
							{#each familyChips(r, ['tooling']) as c}
								<span class="chip v-{c.verdict}" title={c.title}>{c.mark} {c.text}</span>
							{/each}
							{#if note}
								<span class="partial">{note}</span>
							{/if}
						</td>
						<td class="num">{r.coverage_days ?? '—'}</td>
						<td>{r.last_read_ms ? formatTime(r.last_read_ms) : '—'}</td>
					</tr>
					<tr class="detail" id={detailId(r.wallet)} hidden={!isOpen}>
						<td colspan="6">
							{#if isOpen}
								{#if isUnreadable(r)}
									<p class="fault-message" role="alert">{unreadableMessage(faults, r.wallet)}</p>
								{:else if dossierLoading}
									<p class="text-muted">Loading the dossier…</p>
								{:else if !dossier}
									<p class="text-muted">No dossier for this wallet yet.</p>
								{:else}
									{@const [his, it] = rhythmSeries(study.reference, dossier)}
									{#if dossier.left_ms}
										<p class="caveat">Not studied since {formatTime(dossier.left_ms)}.</p>
									{/if}
									<div class="grid">
										<section>
											<h3>How orders are made</h3>
											<table class="habits">
												<thead>
													<tr><th></th><th class="num">Him</th><th class="num">This wallet</th></tr>
												</thead>
												<tbody>
													{#each habitRows(dossier, study.reference) as h}
														<tr><td>{h.label}</td><td class="num">{pct(h.his)}</td><td class="num">{pct(h.it)}</td></tr>
													{/each}
												</tbody>
											</table>
										</section>
										<section>
											<h3>Slicing rhythm</h3>
											<svg viewBox="0 0 300 120" class="rhythm" role="img"
												aria-label={rhythmLabel(study.reference, dossier)}>
												<path d={histogramPath(study.reference?.cadence, 300, 110, top)} class="his" />
												<path d={histogramPath(dossier.series?.cadence, 300, 110, top)} class="it" />
											</svg>
											<p class="legend">
												{#if his.gaps}
													<span class="his">— him</span>
												{:else}
													<span>him: no in-run gaps measured</span>
												{/if}
												{#if it.gaps}
													<span class="it">— this wallet</span>
												{:else}
													<span>this wallet: no in-run gaps measured</span>
												{/if}
												<span>· 0 to 5 s between slices</span>
											</p>
										</section>
									</div>
									<ul class="tests">
										{#each ['T1', 'T2', 'T3'] as t}
											{#if dossier.tests?.[t]}
												{@const test = dossier.tests[t]}
												{@const stat = statisticLine(t, test.statistic)}
												{@const against = t === 'T1' ? againstLine(test) : null}
												<li>
													<strong>{t}</strong> {test.status}
													{#if stat}
														· {stat}
													{/if}
													{#if test.judgement}
														· {VERDICT_LABEL[test.judgement.status] || test.judgement.status}
														{#if test.judgement.stranger_n}
															· strangers matching {test.judgement.stranger_k} of {test.judgement.stranger_n}
														{/if}
													{/if}
													{#if against}
														<div class="against">{against}</div>
													{/if}
												</li>
											{/if}
										{/each}
									</ul>
								{/if}
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	<p class="text-muted">
		{study.studied} studied · {study.read} read this run · built {formatTime(study.computed_at)}
	</p>
{/if}

<style>
	.lede { max-width: 60rem; }
	.panels { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0 0.5rem; }
	.by-test { font-size: 0.8rem; margin: 0 0 1rem; }
	.faults {
		margin: 0 0 1rem; padding: 0.5rem 0.75rem; font-size: 0.85rem;
		border-radius: var(--radius-sm); border-left: 4px solid var(--accent-yellow);
		background: var(--tint-yellow);
	}
	.faults.has-unreadable { border-left-color: var(--accent-red); background: var(--tint-red); }
	.faults summary { cursor: pointer; font-weight: 600; }
	.faults ul { margin: 0.4rem 0 0 1.1rem; }
	.table-wrap { overflow-x: auto; }
	table { width: 100%; border-collapse: collapse; }
	th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }
	th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
	.row { cursor: pointer; }
	.row:hover { background: rgba(255, 255, 255, 0.03); }
	.wallet { white-space: nowrap; }
	.twisty {
		width: 1.5rem; height: 1.5rem; margin-right: 0.35rem; padding: 0; line-height: 1;
		font-size: 1.1rem; vertical-align: middle; background: none; border: 0; border-radius: 4px;
		color: var(--text-muted); cursor: pointer;
	}
	.twisty:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.06); }
	.chip {
		font-size: 0.75rem; padding: 0.1rem 0.45rem; border-radius: 0.25rem;
		background: rgba(255, 255, 255, 0.06); white-space: nowrap;
	}
	.v-for { color: var(--accent-green); background: var(--tint-green); }
	.v-against { color: var(--accent-red); background: var(--tint-red); }
	.v-unreadable { color: var(--accent-red); background: var(--tint-red); }
	.v-mixed { color: var(--accent-yellow); }
	.v-neutral, .v-uncalibrated, .v-insufficient { color: var(--text-muted); }
	.partial { margin-left: 0.4rem; font-size: 0.75rem; color: var(--accent-yellow); white-space: nowrap; }
	.detail td { background: rgba(255, 255, 255, 0.02); }
	.fault-message { color: var(--accent-red); }
	.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
	.habits td, .habits th { padding: 0.2rem 0.4rem; font-size: 0.85rem; }
	.rhythm { width: 100%; max-width: 420px; height: auto; }
	.rhythm path { fill: none; stroke-width: 1.5; }
	.rhythm .his, .legend .his { stroke: var(--accent-cyan); color: var(--accent-cyan); }
	.rhythm .it, .legend .it { stroke: var(--accent-yellow); color: var(--accent-yellow); }
	.legend { display: flex; flex-wrap: wrap; gap: 0 0.75rem; font-size: 0.8rem; opacity: 0.8; }
	.tests { margin: 0.5rem 0 0 1.1rem; font-size: 0.85rem; }
	.against { margin-top: 0.1rem; opacity: 0.9; }
	.caveat { font-size: 0.85rem; opacity: 0.75; }
	h3 { margin: 0.25rem 0 0.5rem; font-size: 0.95rem; }
</style>
