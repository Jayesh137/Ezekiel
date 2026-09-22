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

/** The feed as the owner sees it within one session: which wallets and in what
 *  order is frozen at load (`orderState`), while each row's status is live
 *  (`state`). Otherwise marking a post reviewed would re-sort the feed under
 *  their finger, and a reviewed demotion would vanish mid-read. */
export function sessionFeed(roster, orderState, state, tab = 'review') {
	return feed(roster, orderState, tab).map((r) => ({ ...r, status: status(r, state) }));
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
	// The second hue sits 90-210° away, so every avatar carries two clearly
	// different colours and neighbours do not blur into one family.
	const b = (a + 90 + ((h >>> 9) % 120)) % 360;
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

// --- review state ------------------------------------------------------------
// Lives only in this phone's localStorage. The game measures YOUR review,
// never the wallet's likelihood (ARCHITECTURE.md §5.1).

export const STORAGE_KEY = 'ezekiel.review.v1';

export function markReviewed(state, rows) {
	const reviewed = { ...(state?.reviewed || {}) };
	for (const r of rows) reviewed[walletKey(r.wallet)] = r.tier;
	return { ...state, reviewed };
}

/** Reviewed wallets that are no longer eligible: gone, or became a service.
 *  A disappearance is news too, so it is listed until dismissed. */
export function dropped(roster, state) {
	const live = new Set(eligible(roster).map((w) => walletKey(w.wallet)));
	return Object.entries(state?.reviewed || {})
		.filter(([k]) => !live.has(k))
		.map(([wallet, tier]) => ({ wallet, tier }))
		.sort((x, y) => (x.wallet < y.wallet ? -1 : 1));
}

export function dismissDropped(state, wallet) {
	const reviewed = { ...(state?.reviewed || {}) };
	delete reviewed[walletKey(wallet)];
	return { ...state, reviewed };
}

export function progress(rows) {
	let done = 0;
	let fresh = 0;
	let changed = 0;
	for (const r of rows) {
		if (r.status === 'reviewed') done++;
		else if (r.status === 'changed') changed++;
		else fresh++;
	}
	return { done, total: rows.length, fresh, changed };
}

// --- streak ------------------------------------------------------------------

export function localDay(date = new Date()) {
	const p = (n) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}`;
}

function dayAfter(day) {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || '');
	if (!m) return null;
	return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + 1)).toISOString().slice(0, 10);
}

/** Called only on a successful load: opening offline shows you nothing, so it
 *  does not count as checking in. */
export function checkIn(streak, today) {
	if (streak?.day === today) return streak;
	if (streak && dayAfter(streak.day) === today) {
		return { day: today, count: (streak.count || 0) + 1 };
	}
	return { day: today, count: 1 };
}

// --- stories -----------------------------------------------------------------

/** last_fill_ms is deliberately absent: it changes on nearly every run, so a
 *  ring keyed on it would always be lit, and an always-lit ring means nothing. */
export function storyKey(w) {
	const big = typeof w.size_ratio === 'number' && w.size_ratio >= SIZE_BAND ? 1 : 0;
	return `${(w.agents || []).length}|${(w.subaccounts || []).length}|${big}|${w.read_ok ? 1 : 0}`;
}

/** 'alert' (unreadable, or outgrew the target) | 'unseen' | 'seen'. */
export function storyRing(w, state) {
	if (!w.read_ok || (typeof w.size_ratio === 'number' && w.size_ratio >= SIZE_BAND)) return 'alert';
	return state?.stories?.[walletKey(w.address)] === storyKey(w) ? 'seen' : 'unseen';
}

export function markStorySeen(state, w) {
	return { ...state, stories: { ...(state?.stories || {}), [walletKey(w.address)]: storyKey(w) } };
}

/** The watch file's age: computed_at, else its newest per-wallet read. */
export function watchFreshIso(watchlist) {
	if (!watchlist) return null;
	if (watchlist.computed_at) return watchlist.computed_at;
	const times = (watchlist.wallets || []).map((x) => x.checked_at).filter(Boolean).sort();
	return times.length ? times[times.length - 1] : null;
}

// --- storage -----------------------------------------------------------------

function isPlainObject(v) {
	return v !== null && typeof v === 'object' && !Array.isArray(v);
}

/** { state, ok }. ok=false means storage is UNAVAILABLE (iOS private mode,
 *  blocked site data), which is different from empty; the page says so. */
export function readState(storage) {
	if (!storage) return { state: emptyState(), ok: false };
	let raw;
	try {
		raw = storage.getItem(STORAGE_KEY);
	} catch {
		return { state: emptyState(), ok: false };
	}
	if (raw == null) return { state: emptyState(), ok: true };
	try {
		const v = JSON.parse(raw);
		if (!isPlainObject(v)) return { state: emptyState(), ok: true };
		return {
			state: {
				reviewed: isPlainObject(v.reviewed) ? v.reviewed : {},
				stories: isPlainObject(v.stories) ? v.stories : {},
				streak: isPlainObject(v.streak) ? v.streak : null
			},
			ok: true
		};
	} catch {
		return { state: emptyState(), ok: true };
	}
}

export function writeState(storage, state) {
	if (!storage) return false;
	try {
		storage.setItem(STORAGE_KEY, JSON.stringify(state));
		return true;
	} catch {
		return false;
	}
}
