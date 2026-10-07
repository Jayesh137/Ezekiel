// src/lib/study.js
// Pure helpers for the candidate study page (spec
// docs/superpowers/specs/2026-10-06-candidate-study-design.md §12). Plain ES
// module with no SvelteKit aliases, so node --test can import it.

export const VERDICT_LABEL = {
	for: 'Trades like him',
	against: 'Not his way of trading',
	mixed: 'Mixed evidence',
	neutral: 'No signal either way',
	uncalibrated: 'Not calibrated yet',
	insufficient: 'Not enough data yet',
	unreadable: 'Archive unreadable — needs repair'
};

export const VERDICT_MARK = {
	for: '✓', against: '✗', mixed: '±', neutral: '–', uncalibrated: '?', insufficient: '?',
	unreadable: '!'
};

export const FAMILY_LABEL = {
	tooling: 'Tooling', timing: 'Timing', lifecycle: 'Lifecycle', strategy: 'Strategy'
};

export const HABIT_LABEL = {
	ioc: 'IOC orders',
	frontend: 'Web-UI market orders',
	gtc: 'Resting (GTC)',
	alo: 'Post-only (ALO)',
	client_ids: 'Client order IDs',
	triggers: 'Trigger / TP / SL',
	maker: 'Maker posting',
	canceled: 'Cancelled'
};

/** One chip per family the row carries, in a fixed order. An unknown verdict renders as '?'. */
export function familyChips(row, order = ['tooling', 'timing', 'lifecycle']) {
	const families = row?.families || {};
	return order.filter((f) => families[f]).map((f) => {
		const verdict = families[f].verdict || 'insufficient';
		return { family: f, label: FAMILY_LABEL[f] || f, verdict,
			mark: VERDICT_MARK[verdict] || '?', text: VERDICT_LABEL[verdict] || verdict };
	});
}

const finite = (x) => typeof x === 'number' && Number.isFinite(x);

/** Rows by study rank, then size, then address. A missing rank sorts last (rule 6). */
export function studyRows(study) {
	const rank = (r) => (finite(r.rank) ? r.rank : -Infinity);
	const value = (r) => (finite(r.account_value) ? r.account_value : -Infinity);
	return [...(study?.wallets || [])].sort((a, b) =>
		(rank(b) - rank(a)) || (value(b) - value(a)) || String(a.wallet).localeCompare(String(b.wallet)));
}

/** Each panel against its pre-registered bar. */
export function panelStatus(study) {
	const p = study?.panels || {};
	const bars = p.bars || {};
	return [
		{ name: 'Strangers (habit census)', n: p.strangers ?? null, need: bars.strangers ?? 200 },
		{ name: 'Same-operator pairs (sub-account families)', n: p.family_pairs ?? null, need: bars.family_pairs ?? 40 },
		{ name: 'His own months', n: p.self_windows ?? null, need: bars.self_windows ?? 6 }
	].map((r) => ({ ...r, ok: finite(r.n) && r.n >= r.need }));
}

function shares(hist) {
	const counts = Array.isArray(hist) ? hist : [];
	const total = counts.reduce((a, b) => a + b, 0);
	return total ? counts.map((c) => c / total) : [];
}

/** The largest single-bin share across histograms, so overlays share one scale. */
export function maxShare(...hists) {
	return Math.max(0, ...hists.flatMap((h) => shares(h)));
}

/** An SVG path for a histogram as shares, scaled so `top` reaches the top edge. */
export function histogramPath(hist, width, height, top = null) {
	const s = shares(hist);
	if (!s.length) return '';
	const ceiling = top || Math.max(...s) || 1;
	const step = width / Math.max(1, s.length - 1);
	return s.map((v, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)},${(height - (v / ceiling) * height).toFixed(1)}`).join(' ');
}

/** Him against this wallet, one row per habit; a share never read stays null.
 *  His shares come from the study's `reference`, the wallet's from its dossier. */
export function habitRows(dossier, reference) {
	const it = dossier?.series?.shares || {};
	const his = reference?.shares || {};
	return Object.keys(HABIT_LABEL).map((key) => ({
		key, label: HABIT_LABEL[key],
		his: finite(his[key]) ? his[key] : null,
		it: finite(it[key]) ? it[key] : null
	}));
}

export function pct(x) {
	if (!finite(x)) return '—';
	return `${(x * 100).toFixed(x > 0 && x < 0.01 ? 2 : 1)}%`;
}
