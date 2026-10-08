// src/lib/casebook.js
// The casebook page and the phone's Ranked tab, as pure functions over the committed
// index (data/casebook/latest.json) and case files. Plain ES module so node --test can
// import it. Spec: docs/superpowers/specs/2026-10-08-casebook-design.md.

export const FAMILY_LABEL = {
	control: 'Protocol control',
	money: 'Money with his wallets',
	infrastructure: 'Shared private infrastructure',
	gap: 'Amount match across a custody gap',
	lifecycle: 'Born in his silence',
	tooling: 'Makes orders like him',
	behaviour: 'Trading style',
	association: 'Referral',
	coactivity: 'Trade timing',
	ruling: 'Operator ruling'
};

export const STATUS_LABEL = {
	current: 'current',
	standing: 'standing (protocol fact)',
	lapsed: 'lapsed',
	refuted: 'refuted',
	historical: 'historical',
	invalidated: 'invalidated'
};

const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
const DAY = 86_400_000;

/** A probability as a short percentage; '—' when there is none (rule 6). */
export function pct(p) {
	if (!isNum(p)) return '—';
	if (p >= 0.995) return '>99%';
	if (p >= 0.1) return `${Math.round(p * 100)}%`;
	if (p >= 0.01) return `${(p * 100).toFixed(1)}%`;
	if (p >= 0.001) return `${(p * 100).toFixed(2)}%`;
	return '<0.1%';
}

/** "0.10%–40%": the defensible floor to the ceiling. */
export function bandText(row) {
	if (!row) return '—';
	return `${pct(row.p_now)}–${pct(row.p_ceiling)}`;
}

/** Compact dollars for a card. */
export function money(x) {
	if (!isNum(x)) return '—';
	const a = Math.abs(x);
	if (a >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
	if (a >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
	if (a >= 1e3) return `$${(x / 1e3).toFixed(0)}K`;
	return `$${x.toFixed(0)}`;
}

/** The index's rows, filtered as the page asks. Index order IS the ranking. */
export function caseRows(index, { hlOnly = true, includeKnown = true, includeExcluded = false, query = '' } = {}) {
	const rows = Array.isArray(index?.cases) ? index.cases : [];
	const q = String(query || '').trim().toLowerCase();
	return rows.filter((r) => {
		if (!r || typeof r.address !== 'string') return false;
		if (q && !r.address.includes(q)) return false;
		if (r.known) return includeKnown;
		if (r.excluded) return includeExcluded;
		if (hlOnly && r.hl?.on_hl !== true) return false;
		return true;
	});
}

/** A row's families as chips, strongest first; down for evidence against. */
export function familyChips(row) {
	const fams = row?.families && typeof row.families === 'object' ? row.families : {};
	return Object.entries(fams)
		.map(([family, v]) => {
			const central = isNum(v?.[1]) ? v[1] : 0;
			const ceiling = isNum(v?.[2]) ? v[2] : 0;
			const state = v?.[3] || 'current';
			const lead = central || ceiling;
			return { family, label: FAMILY_LABEL[family] || family, central, ceiling, state,
				dir: lead < 0 ? 'down' : 'up', faded: !['current', 'standing', 'cluster'].includes(state) };
		})
		.sort((a, b) => Math.abs(b.central || b.ceiling) - Math.abs(a.central || a.ceiling));
}

/** Why it ranks where it does, without the Hyperliquid line the card shows apart.
 *  An index written before `why` existed still reads, through its full headline. */
export function reasonLine(row) {
	return row?.why || row?.headline || '';
}

/** One line about the account on Hyperliquid. */
export function hlLine(row) {
	const hl = row?.hl || {};
	if (!hl.probed_at) return 'Not read on Hyperliquid yet';
	if (hl.on_hl === false) return 'No Hyperliquid account';
	if (hl.on_hl !== true) return 'Hyperliquid read failed';
	const parts = [`${money(hl.value)} on Hyperliquid`];
	if (isNum(hl.month_volume)) {
		parts.push(hl.month_volume > 0 ? `${money(hl.month_volume)} traded in 30d` : 'no trades in 30d');
	}
	return parts.join(' · ');
}

/** The two calibration checks (spec §7.6) as sentences. */
export function calibrationLines(index) {
	const cal = index?.calibration || {};
	const lines = [];
	for (const r of cal.recall || []) {
		lines.push(`A known wallet of his (${String(r.address).slice(0, 10)}…) ranks ${r.rank_among_unknown} ` +
			`among ${cal.unknown_cases} unknown cases on its evidence alone.`);
	}
	if (isNum(cal.sum_p_central_unknown)) {
		lines.push(`The likelihoods of the unknown cases sum to ${cal.sum_p_central_unknown.toFixed(2)}; ` +
			'a calibrated model expects about 0.5 to 3.');
	}
	return lines;
}

/** Prior → each family → posterior, for the case panel. Families in one category count
 *  half after the first, so a last step shows that discount when there is one. */
export function waterfall(caseDoc, prior = -3) {
	const fams = caseDoc?.score?.families && typeof caseDoc.score.families === 'object' ? caseDoc.score.families : {};
	const steps = [{ label: 'Prior (1 in 1,000)', delta: null, total: prior }];
	let total = prior;
	const entries = Object.entries(fams)
		.filter(([, v]) => isNum(v?.central) && v.central !== 0)
		.sort((a, b) => Math.abs(b[1].central) - Math.abs(a[1].central));
	for (const [family, v] of entries) {
		total += v.central;
		steps.push({ label: FAMILY_LABEL[family] || family, delta: v.central, total });
	}
	const posterior = caseDoc?.score?.central;
	if (isNum(posterior) && Math.abs(posterior - total) > 1e-6) {
		steps.push({ label: 'Dependence discount (a second family of the same kind counts half)',
			delta: posterior - total, total: posterior });
	}
	return steps;
}

/** A case's evidence, oldest first, with its status. */
export function timeline(caseDoc, kinds = {}) {
	const ev = caseDoc?.evidence && typeof caseDoc.evidence === 'object' ? caseDoc.evidence : {};
	return Object.entries(ev)
		.map(([key, e]) => ({
			key, kind: e?.kind, label: kinds?.[e?.kind]?.label || e?.kind || key,
			status: e?.invalid_reason ? 'invalidated' : (e?.status || 'current'),
			invalid: e?.invalid_reason || null, first: String(e?.first_seen || '').slice(0, 10),
			last: e?.last_seen || null, days: e?.seen_days ?? null, summary: e?.summary || '',
			peak: e?.peak_summary && e.peak_summary !== e.summary ? e.peak_summary : null
		}))
		.sort((a, b) => (a.first < b.first ? -1 : a.first > b.first ? 1 : 0));
}

/** The life series as an SVG path, with his unusual silences shaded and birth marked. */
export function lifeChart(caseDoc, gaps = [], width = 600, height = 120) {
	const life = (caseDoc?.hl?.life || []).filter((p) => Array.isArray(p) && isNum(p[0]) && isNum(p[1]));
	if (life.length < 2) return null;
	const t0 = life[0][0];
	const t1 = life[life.length - 1][0];
	const span = Math.max(1, t1 - t0);
	const top = Math.max(...life.map((p) => p[1]), 1);
	const x = (t) => ((t - t0) / span) * width;
	const y = (v) => height - (Math.max(0, v) / top) * height;
	const path = life.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ');
	const silences = (Array.isArray(gaps) ? gaps : [])
		.map((g) => ({ start: g?.start_day * DAY, end: g?.end_day * DAY }))
		.filter((g) => isNum(g.start) && isNum(g.end) && g.end >= t0 && g.start <= t1)
		.map((g) => ({ x: x(Math.max(g.start, t0)), w: Math.max(1, x(Math.min(g.end, t1)) - x(Math.max(g.start, t0))) }));
	const birth = caseDoc?.hl?.birth_ms;
	return { path, silences, birthX: isNum(birth) && birth >= t0 && birth <= t1 ? x(birth) : null, top, t0, t1 };
}

/** The phone's Ranked tab: unknown suspects Hyperliquid knows, best first. */
export function rankedFeed(index, limit = 25) {
	const rows = Array.isArray(index?.cases) ? index.cases : [];
	return rows
		.filter((r) => r && typeof r.address === 'string' && !r.known && !r.excluded && r.ruling !== 'not_him'
			&& r.rank != null && r.hl?.on_hl === true)
		.slice(0, limit);
}

/** Changed since the tab was last opened (ISO strings compare in time order). */
export function isNew(row, seenIso) {
	return Boolean(row?.last_change && (!seenIso || row.last_change > seenIso));
}

export const SEEN_KEY = 'ezekiel.casebook.seen.v1';

export function readSeen(storage) {
	try {
		return storage?.getItem(SEEN_KEY) || null;
	} catch {
		return null;
	}
}

export function writeSeen(storage, iso) {
	try {
		storage?.setItem(SEEN_KEY, String(iso));
		return Boolean(storage);
	} catch {
		return false;
	}
}
