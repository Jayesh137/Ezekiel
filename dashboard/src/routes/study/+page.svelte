<script>
	import { onMount } from 'svelte';
	import { fetchStudy, fetchStudyDossier, formatUSD, formatTime } from '$lib/api.js';
	import Addr from '$lib/Addr.svelte';
	import {
		familyChips, studyRows, panelStatus, habitRows, histogramPath, maxShare, pct,
		VERDICT_LABEL
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

	async function toggle(wallet) {
		if (open === wallet) {
			open = null;
			dossier = null;
			return;
		}
		open = wallet;
		dossier = null;
		dossierLoading = true;
		const doc = await fetchStudyDossier(wallet);
		if (open === wallet) {
			dossier = doc;
			dossierLoading = false;
		}
	}

	$: rows = studyRows(study);
	$: panels = panelStatus(study);
	$: top = dossier ? maxShare(dossier.series?.cadence, study?.reference?.cadence) : 0;
</script>

<svelte:head><title>Study · Ezekiel</title></svelte:head>

<h1>Candidate study</h1>
<p class="lede">
	Every identified Hyperliquid candidate under continuous study, compared with how he makes
	orders. A verdict waits for its panels to be calibrated; until then the raw numbers are
	shown and nothing is called for or against. Evidence against him never changes a tier.
</p>

{#if loading}
	<p class="text-muted">Loading…</p>
{:else if !study}
	<p class="text-muted">
		No study yet. It is written by <code>scripts/run_study.py</code> (study.yml, every 6 hours).
	</p>
{:else}
	<div class="panels">
		{#each panels as p}
			<span class="badge {p.ok ? 'badge-cyan' : 'badge-grey'}" title="Needs {p.need}">
				{p.name}: {p.n ?? '—'} / {p.need}
			</span>
		{/each}
	</div>

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
				<tr class="row" on:click={() => toggle(r.wallet)}>
					<td><Addr address={r.wallet} stopPropagation /></td>
					<td>{r.source}</td>
					<td class="num">{formatUSD(r.account_value)}</td>
					<td>
						{#each familyChips(r, ['tooling']) as c}
							<span class="chip v-{c.verdict}" title={c.text}>{c.mark} {c.text}</span>
						{/each}
					</td>
					<td class="num">{r.coverage_days ?? '—'}</td>
					<td>{r.last_read_ms ? formatTime(r.last_read_ms) : '—'}</td>
				</tr>
				{#if open === r.wallet}
					<tr class="detail">
						<td colspan="6">
							{#if dossierLoading}
								<p class="text-muted">Loading the dossier…</p>
							{:else if !dossier}
								<p class="text-muted">No dossier for this wallet yet.</p>
							{:else}
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
											aria-label="Gap between slices: his and this wallet's, 0 to 5 seconds">
											<path d={histogramPath(study.reference?.cadence, 300, 110, top)} class="his" />
											<path d={histogramPath(dossier.series?.cadence, 300, 110, top)} class="it" />
										</svg>
										<p class="legend"><span class="his">— him</span> <span class="it">— this wallet</span> · 0 to 5 s between slices</p>
									</section>
								</div>
								<ul class="tests">
									{#each ['T1', 'T2', 'T3'] as t}
										{#if dossier.tests?.[t]}
											<li>
												<strong>{t}</strong> {dossier.tests[t].status}
												{#if dossier.tests[t].judgement}
													· {VERDICT_LABEL[dossier.tests[t].judgement.status] || dossier.tests[t].judgement.status}
													{#if dossier.tests[t].judgement.stranger_n}
														· strangers matching {dossier.tests[t].judgement.stranger_k} of {dossier.tests[t].judgement.stranger_n}
													{/if}
												{/if}
											</li>
										{/if}
									{/each}
								</ul>
							{/if}
						</td>
					</tr>
				{/if}
			{/each}
		</tbody>
	</table>

	<p class="text-muted">
		{study.studied} studied · {study.read} read this run · built {formatTime(study.computed_at)}
	</p>
{/if}

<style>
	.lede { max-width: 60rem; }
	.panels { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0; }
	table { width: 100%; border-collapse: collapse; }
	th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }
	th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
	.row { cursor: pointer; }
	.row:hover { background: rgba(255, 255, 255, 0.03); }
	.chip {
		font-size: 0.75rem; padding: 0.1rem 0.45rem; border-radius: 0.25rem;
		background: rgba(255, 255, 255, 0.06); white-space: nowrap;
	}
	.v-for { color: var(--accent-green); background: var(--tint-green); }
	.v-against { color: var(--accent-red); background: var(--tint-red); }
	.v-unreadable { color: var(--accent-red); background: var(--tint-red); }
	.v-mixed { color: var(--accent-yellow); }
	.v-neutral, .v-uncalibrated, .v-insufficient { color: var(--text-muted); }
	.detail td { background: rgba(255, 255, 255, 0.02); }
	.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
	.habits td, .habits th { padding: 0.2rem 0.4rem; font-size: 0.85rem; }
	.rhythm { width: 100%; max-width: 420px; height: auto; }
	.rhythm path { fill: none; stroke-width: 1.5; }
	.rhythm .his, .legend .his { stroke: var(--accent-cyan); color: var(--accent-cyan); }
	.rhythm .it, .legend .it { stroke: var(--accent-yellow); color: var(--accent-yellow); }
	.legend { font-size: 0.8rem; opacity: 0.8; }
	.tests { margin: 0.5rem 0 0 1.1rem; font-size: 0.85rem; }
	.caveat { font-size: 0.85rem; opacity: 0.75; }
	h3 { margin: 0.25rem 0 0.5rem; font-size: 0.95rem; }
</style>
