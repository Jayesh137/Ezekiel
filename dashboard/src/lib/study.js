// src/lib/study.js
// Pure helpers for the candidate study page (spec
// docs/superpowers/specs/2026-10-06-candidate-study-design.md §12). Plain ES
// module with no SvelteKit aliases, so node --test can import it.

import { shortAddr } from './api.js';

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
	canceled: 'Cancelled',
	taker: 'Taker (crossed the book)'
};

/** Why a wallet is studied (src/study/selection.py SOURCES). */
export const SOURCE_LABEL = {
	pinned: 'Pinned',
	roster_lead: 'Roster lead',
	decayed_lead: 'Lead that decayed',
	detector: 'Detector find'
};

const finite = (x) => typeof x === 'number' && Number.isFinite(x);
const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const sameWallet = (a, b) => String(a || '').toLowerCase() === String(b || '').toLowerCase();

/** A source as the operator reads it. Anything not in the table renders as written. */
export function sourceLabel(source) {
	if (typeof source !== 'string' || !source) return '—';
	return has(SOURCE_LABEL, source) ? SOURCE_LABEL[source] : source;
}

/** One chip per family the row carries, in a fixed order. An unknown verdict renders as '?'.
 *  `title` is the tooltip: the verdict, then which tests decided it and on what basis. */
export function familyChips(row, order = ['tooling', 'timing', 'lifecycle']) {
	const families = row?.families || {};
	return order.filter((f) => families[f]).map((f) => {
		const verdict = families[f].verdict || 'insufficient';
		const text = VERDICT_LABEL[verdict] || verdict;
		const by = (Array.isArray(families[f].by) ? families[f].by : []).filter(Boolean);
		const basis = families[f].basis;
		const title = [text, by.length ? `decided by ${by.join(', ')}` : null, basis ? `basis ${basis}` : null]
			.filter(Boolean).join(' · ');
		return { family: f, label: FAMILY_LABEL[f] || f, verdict,
			mark: VERDICT_MARK[verdict] || '?', text, title };
	});
}

/** A row whose study archive could not be read: a fault to repair, not a verdict. */
export const isUnreadable = (row) => row?.families?.tooling?.verdict === 'unreadable';

/** Rows with an unreadable archive first (a fault needs attention), then by study rank,
 *  size and address. A missing rank sorts last (rule 6). */
export function studyRows(study) {
	const rank = (r) => (finite(r.rank) ? r.rank : -Infinity);
	const value = (r) => (finite(r.account_value) ? r.account_value : -Infinity);
	return [...(study?.wallets || [])].sort((a, b) =>
		(Number(isUnreadable(b)) - Number(isUnreadable(a))) || (rank(b) - rank(a))
			|| (value(b) - value(a)) || String(a.wallet).localeCompare(String(b.wallet)));
}

/** The run's own record of what it could not read (rule 5): a missing key is empty. */
export function runFaults(study) {
	const entries = (x) => (Array.isArray(x) ? x.filter((e) => e && typeof e === 'object') : []);
	return { unreadable: entries(study?.unreadable), partial: entries(study?.partial),
		stopped: Boolean(study?.stopped) };
}

/** The status strip's parts, in order; empty when nothing failed. */
export function faultSummary(faults) {
	const parts = [];
	const unreadable = faults?.unreadable?.length || 0;
	if (unreadable) parts.push(`${plural(unreadable, 'wallet')} unreadable`);
	const partial = faults?.partial || [];
	if (partial.length) {
		const each = partial.map((p) => `${shortAddr(p.wallet)}: ${p.source || 'unknown source'}`).join(', ');
		parts.push(`${plural(partial.length, 'partial read')} (${each})`);
	}
	if (faults?.stopped) parts.push('run stopped on its budget');
	return parts;
}

/** "partial read: orders, ledger" for a wallet some of whose reads failed, else null. */
export function partialNote(faults, wallet) {
	const mine = (faults?.partial || []).filter((p) => sameWallet(p.wallet, wallet));
	if (!mine.length) return null;
	const sources = [...new Set(mine.map((p) => p.source).filter(Boolean))];
	return sources.length ? `partial read: ${sources.join(', ')}` : 'partial read';
}

/** What an unreadable row shows instead of a dossier: the run's error for that wallet. */
export function unreadableMessage(faults, wallet) {
	const found = (faults?.unreadable || []).find((u) => sameWallet(u.wallet, wallet));
	const base = "This wallet's study archive could not be read";
	return found?.error ? `${base}: ${found.error}` : `${base}.`;
}

/** Each panel against its pre-registered bar. T1's counts: the badges say so, and why
 *  either same-operator bar is enough (spec §8.1). */
export function panelStatus(study) {
	const p = study?.panels || {};
	const bars = p.bars || {};
	const need = { strangers: bars.strangers ?? 200, pairs: bars.family_pairs ?? 40,
		months: bars.self_windows ?? 6 };
	const either = `Either ${need.pairs} family pairs or ${need.months} of his own months calibrates a test.`;
	const measurable = finite(p.measurable_strangers)
		? ` Measurable strangers in all: ${p.measurable_strangers}.` : '';
	return [
		{ name: 'Strangers T1 judges on (decided style)', n: p.strangers ?? null, need: need.strangers,
			title: `Needs ${need.strangers}.${measurable}` },
		{ name: 'Same-operator pairs (sub-account families)', n: p.family_pairs ?? null, need: need.pairs,
			title: `Needs ${need.pairs}. ${either}` },
		{ name: 'His own months', n: p.self_windows ?? null, need: need.months,
			title: `Needs ${need.months}. ${either}` }
	].map((r) => ({ ...r, ok: finite(r.n) && r.n >= r.need }));
}

/** T2 and T3 judge on their own counts (panels.by_test); null for a file without them. */
export function byTestLine(study) {
	const by = study?.panels?.by_test;
	const count = (x, one, many) => (finite(x) ? plural(x, one, many) : `— ${many}`);
	const parts = ['T2', 'T3'].filter((t) => by?.[t] && typeof by[t] === 'object').map((t) => {
		const c = by[t];
		return `${t}: ${count(c.strangers, 'stranger', 'strangers')} · ${count(c.family_pairs, 'pair', 'pairs')}`
			+ ` · ${finite(c.self_windows) ? c.self_windows : '—'} of his months`;
	});
	return parts.length ? parts.join('; ') : null;
}

const traitName = (t) => (has(HABIT_LABEL, t) ? HABIT_LABEL[t] : String(t));
/** Two decimals from 0.1 up, two significant figures below it (0.0436 reads 0.044, never 0.04). */
const compact = (x) => (x === 0 || Math.abs(x) >= 0.1 ? x.toFixed(2) : x.toPrecision(2));

/** One value when every trait carries it, else "Trait value, Trait value". */
function perTrait(map) {
	const entries = Object.entries(map && typeof map === 'object' ? map : {}).filter(([, v]) => v != null);
	if (!entries.length) return null;
	const values = [...new Set(entries.map(([, v]) => String(v)))];
	return values.length === 1 ? values[0] : entries.map(([t, v]) => `${traitName(t)} ${v}`).join(', ');
}

/** T1's against result as one line (the family verdict comes from it), or null when it
 *  has nothing to say. `t1` is the dossier's tests.T1. */
export function againstLine(t1) {
	const a = t1?.against;
	if (!a || typeof a !== 'object') return null;
	const traits = (Array.isArray(a.traits) ? a.traits : []).filter((t) => typeof t === 'string');
	const names = traits.map(traitName).join(', ');
	if (a.status === 'against') {
		const parts = [names ? `against: ${names}` : 'against'];
		const mismatch = perTrait(a.same_op_mismatch);
		if (mismatch) parts.push(`same-operator mismatch ${mismatch}`);
		if (finite(a.lr)) parts.push(`LR ${compact(a.lr)}`);
		return parts.join(' · ');
	}
	if (a.status === 'uncalibrated' && traits.length) {
		const parts = [`against, not calibrated yet: ${names}`];
		const pairs = perTrait(a.pairs);
		if (pairs) parts.push(`same-operator pairs ${pairs}`);
		if (finite(a.strangers)) parts.push(`strangers ${a.strangers}`);
		return parts.join(' · ');
	}
	return null;
}

/** A test's raw number when it has one: T2's rhythm distance (seconds), T3's clip match
 *  strength. T1's statistic is a yes/no, so it has no line; a missing number is no line. */
export function statisticLine(test, statistic) {
	if (!finite(statistic)) return null;
	if (test === 'T2') return `rhythm distance ${compact(statistic)} s`;
	if (test === 'T3') return `clip match ${statistic.toFixed(2)}`;
	return null;
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

const gapCount = (hist) => (Array.isArray(hist) ? hist.reduce((a, b) => a + (finite(b) ? b : 0), 0) : 0);

/** His rhythm (the study's reference) and this wallet's, each with its in-run gap count. */
export function rhythmSeries(reference, dossier) {
	return [
		{ key: 'his', label: 'him', gaps: gapCount(reference?.cadence) },
		{ key: 'it', label: 'this wallet', gaps: gapCount(dossier?.series?.cadence) }
	];
}

/** The chart's text alternative: both gap counts, so an empty line is heard as empty. */
export function rhythmLabel(reference, dossier) {
	const [his, it] = rhythmSeries(reference, dossier);
	const gaps = (n) => `${n.toLocaleString('en-US')} ${n === 1 ? 'gap' : 'gaps'}`;
	return `Slicing rhythm: his ${gaps(his.gaps)}, this wallet ${gaps(it.gaps)}, 0 to 5 s`;
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
