// Run with: node --test src/lib/  (from dashboard/).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { recentXChanges, xAccounts, xQuietLine } from './xwatch.js';

const NOW = Date.parse('2026-10-15T12:00:00Z');
const doc = {
	computed_at: '2026-10-15T11:56:00Z',
	accounts_watched: 2,
	unreadable: [],
	accounts: {
		1484112393361891328: { handle: 'GCRClassic', status: 'ok', profile: { protected: true, tweets: 746 } },
		1289071298556170240: { handle: 'GiganticRebirth', status: 'ok', profile: { protected: true, tweets: 714 } }
	},
	changes: [
		{ at: '2026-10-01T00:00:00Z', line: 'old' },
		{ at: '2026-10-14T09:00:00Z', line: 'older recent' },
		{ at: '2026-10-15T11:50:00Z', line: 'newest' },
		{ at: 'garbage', line: 'undated' }
	]
};

test('only the last seven days show, newest first, and undated rows never', () => {
	assert.deepEqual(recentXChanges(doc, NOW).map((c) => c.line), ['newest', 'older recent']);
	assert.deepEqual(recentXChanges(null, NOW), []);
});

test('accounts link to their profile', () => {
	const a = xAccounts(doc);
	assert.equal(a.length, 2);
	assert.ok(a.some((x) => x.url === 'https://x.com/GCRClassic' && x.protected === true));
});

test('the quiet line never calls a missing or blind feed quiet', () => {
	assert.equal(xQuietLine(doc, NOW), 'X watch: quiet · read 4 min ago');
	assert.equal(xQuietLine(null, NOW), 'X watch: no reading yet');
	assert.match(xQuietLine({ ...doc, unreadable: ['a', 'b'] }, NOW), /both mirrors unreadable/);
	assert.match(xQuietLine({ ...doc, unreadable: ['a'] }, NOW), /1 unreadable/);
});
