// His X accounts (data/x_watch/latest.json, written by scripts/check_x_profiles.py).
// Hidden until something changes: the card shows only when a change landed in the
// last RECENT_DAYS; otherwise one quiet line says the watch is alive. Pure.

export const RECENT_DAYS = 7;

/** Changes from the last `days`, newest first. Each carries the notice's own `line`. */
export function recentXChanges(doc, now = Date.now(), days = RECENT_DAYS) {
	const cutoff = now - days * 86_400_000;
	return (doc?.changes || [])
		.filter((c) => {
			const t = Date.parse(c?.at);
			return !Number.isNaN(t) && t >= cutoff;
		})
		.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

/** {handle, url, protected, tweets, last_read} per account, in config order. */
export function xAccounts(doc) {
	return Object.entries(doc?.accounts || {}).map(([id, a]) => ({
		id,
		handle: a?.handle || a?.profile?.handle || id,
		url: a?.handle ? `https://x.com/${a.handle}` : `https://x.com/i/user/${id}`,
		protected: a?.profile?.protected ?? null,
		tweets: a?.profile?.tweets ?? null,
		status: a?.status || null,
		lastRead: a?.last_read || null
	}));
}

/** One quiet line: is the watch alive, and is anything unreadable? Never "quiet" on no file. */
export function xQuietLine(doc, now = Date.now()) {
	if (!doc?.computed_at) return 'X watch: no reading yet';
	const t = Date.parse(doc.computed_at);
	if (Number.isNaN(t)) return 'X watch: undated reading';
	const min = Math.max(0, Math.round((now - t) / 60_000));
	const age = min < 90 ? `${min} min ago` : `${(min / 60).toFixed(1)} h ago`;
	const unread = (doc.unreadable || []).length;
	const n = (doc.accounts_watched ?? Object.keys(doc.accounts || {}).length) || 0;
	if (unread && unread >= n) return `X watch: both mirrors unreadable · tried ${age}`;
	return `X watch: quiet · read ${age}${unread ? ` · ${unread} unreadable` : ''}`;
}
