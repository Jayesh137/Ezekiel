// src/lib/review.js
// Every decision the phone review app makes, as pure functions over plain
// objects. Components render; this module decides. See dashboard/ARCHITECTURE.md §6.
//
// Plain ES module with no SvelteKit aliases, so node --test can import it.

export const TIER_RANK = { CONFIRMED: 0, PROBABLE: 1, POSSIBLE: 2, WATCH: 3 };
export const REVIEW_TIERS = ['CONFIRMED', 'PROBABLE', 'POSSIBLE'];
/** The watch's own outgrew-target band (scripts/check_watchlist.py). */
export const SIZE_BAND = 1.15;

export const TIER_LABEL = {
	CONFIRMED: 'Confirmed',
	PROBABLE: 'Probable',
	POSSIBLE: 'Possible',
	WATCH: 'Watch',
	INFRASTRUCTURE: 'Service'
};

// Every vector src/roster.py defines. Each label says what was OBSERVED rather
// than repeating a score.
export const VECTOR_LABEL = {
	transfer: 'Observed transfer',
	linkage: 'Shared funder / deposit address',
	correlation: 'Exit amount re-appeared as a deposit',
	behavioural: 'Trades like the target',
	hl_native: 'Two-way flow inside Hyperliquid',
	shared_agent: 'Shares an agent',
	explicit_link: 'Explicit link (sub-account / role)',
	dormancy_handoff: 'Born inside his silence',
	referral: 'Referral link'
};

/** An unknown vector renders raw rather than disappearing. */
export function vectorLabel(v) {
	return VECTOR_LABEL[v] || v;
}

export function walletKey(addr) {
	return String(addr || '').toLowerCase();
}

export function emptyState() {
	return { reviewed: {}, stories: {}, streak: null };
}

/** Wallets the app may show at all: no services, no infrastructure, not him. */
export function eligible(roster) {
	const target = walletKey(roster?.target);
	return (roster?.wallets || []).filter(
		(w) => w && w.wallet && w.tier !== 'INFRASTRUCTURE' && !w.is_service
			&& walletKey(w.wallet) !== target
	);
}

/** 'new' | 'changed' | 'reviewed', against the tier you last reviewed it at. */
export function status(w, state) {
	const seen = state?.reviewed?.[walletKey(w.wallet)];
	if (seen === undefined) return 'new';
	return seen === w.tier ? 'reviewed' : 'changed';
}

// rank_strength orders the queue only. An absent one sorts LAST, never as 0.0:
// most roster wallets reached by a non-graph vector carry no graph score, and
// reading that absence as a measured zero is rule 6.
function strength(w) {
	const v = w.rank_strength;
	return typeof v === 'number' && Number.isFinite(v) ? v : -Infinity;
}

function compare(x, y) {
	const ux = x.status === 'reviewed' ? 1 : 0;
	const uy = y.status === 'reviewed' ? 1 : 0;
	if (ux !== uy) return ux - uy;
	const tx = TIER_RANK[x.tier] ?? 9;
	const ty = TIER_RANK[y.tier] ?? 9;
	if (tx !== ty) return tx - ty;
	const vx = x.vector_count || 0;
	const vy = y.vector_count || 0;
	if (vx !== vy) return vy - vx;
	const sx = strength(x);
	const sy = strength(y);
	if (sx !== sy) return sy > sx ? 1 : -1;
	const ax = walletKey(x.wallet);
	const ay = walletKey(y.wallet);
	return ax < ay ? -1 : ax > ay ? 1 : 0;
}

// A wallet demoted out of the review tiers stays in "For review" as changed
// until it is reviewed: a demotion is news.
const TAB_FILTER = {
	review: (r) => REVIEW_TIERS.includes(r.tier) || r.status === 'changed',
	watch: (r) => r.tier === 'WATCH',
	all: () => true
};

/** The rows for one tab, each a copy carrying its `status`, in feed order. */
export function feed(roster, state, tab = 'review') {
	const pick = TAB_FILTER[tab] || TAB_FILTER.review;
	return eligible(roster)
		.map((w) => ({ ...w, status: status(w, state) }))
		.filter(pick)
		.sort(compare);
}

export function tabCounts(roster, state) {
	return {
		review: feed(roster, state, 'review').length,
		watch: feed(roster, state, 'watch').length,
		all: feed(roster, state, 'all').length
	};
}

/** Deterministic gradient identicon: FNV-1a over the lowercased address. */
export function avatar(address) {
	let h = 0x811c9dc5;
	for (const ch of walletKey(address)) {
		h ^= ch.charCodeAt(0);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	const a = h % 360;
	const b = (a + 40 + ((h >>> 9) % 80)) % 360;
	const angle = (h >>> 17) % 360;
	return { a, b, angle };
}

/** Same thresholds as the collection pill in +layout.svelte. */
export function freshness(iso, nowMs = Date.now()) {
	const t = iso ? Date.parse(iso) : NaN;
	if (!Number.isFinite(t)) return { minutes: null, level: 'unknown' };
	const minutes = Math.max(0, Math.round((nowMs - t) / 60000));
	const level = minutes <= 160 ? 'ok' : minutes <= 360 ? 'warn' : 'stale';
	return { minutes, level };
}

export function ago(ms, nowMs = Date.now()) {
	if (ms == null || !Number.isFinite(ms)) return '—';
	const s = Math.max(0, (nowMs - ms) / 1000);
	if (s < 60) return 'just now';
	if (s < 3600) return `${Math.floor(s / 60)}m ago`;
	if (s < 48 * 3600) return `${Math.floor(s / 3600)}h ago`;
	return `${Math.floor(s / 86400)}d ago`;
}

export function ratio(x) {
	return typeof x === 'number' && Number.isFinite(x) ? `${x.toFixed(2)}x` : '—';
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function bornLabel(ms) {
	if (ms == null || !Number.isFinite(ms)) return '—';
	const d = new Date(ms);
	return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
