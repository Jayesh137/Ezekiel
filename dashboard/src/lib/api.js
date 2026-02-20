// src/lib/api.js
// Data fetching utility — reads JSON from GitHub raw content

const OWNER = 'jayeshxcode';
const REPO = 'Ezekiel';
const BRANCH = 'main';
const RAW_BASE = `https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}`;

/**
 * Fetch a JSON file from the repo.
 * @param {string} path - Path relative to repo root (e.g., "data/fills/2026-02-19.json")
 * @returns {Promise<any>}
 */
export async function fetchJSON(path) {
	const url = `${RAW_BASE}/${path}`;
	try {
		const resp = await fetch(url);
		if (!resp.ok) return null;
		return await resp.json();
	} catch {
		return null;
	}
}

/**
 * Fetch the data index which lists all available data files.
 * @returns {Promise<object|null>}
 */
export async function fetchIndex() {
	return fetchJSON('data/index.json');
}

/**
 * Fetch latest snapshot for a data type.
 * @param {string} dataType - e.g., "positions", "account", "scans"
 */
export async function fetchLatest(dataType) {
	return fetchJSON(`data/${dataType}/latest.json`);
}

/**
 * Fetch daily records for a data type and date.
 * @param {string} dataType - e.g., "fills", "funding"
 * @param {string} date - YYYY-MM-DD
 */
export async function fetchDaily(dataType, date) {
	return fetchJSON(`data/${dataType}/${date}.json`);
}

/**
 * Fetch the behavioral fingerprint.
 */
export async function fetchFingerprint() {
	return fetchJSON('profile/fingerprint.json');
}

/**
 * Fetch the trader profile.
 */
export async function fetchProfile() {
	return fetchJSON('profile/trader_profile.json');
}

/**
 * Fetch Twitter correlation data.
 */
export async function fetchCorrelation() {
	return fetchJSON('data/twitter/correlation/latest.json');
}

/**
 * Fetch scanner results.
 */
export async function fetchScanResults() {
	return fetchJSON('data/scans/latest.json');
}

/**
 * Format a USD value.
 * @param {number} val
 * @returns {string}
 */
export function formatUSD(val) {
	if (val == null) return '—';
	if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(2)}M`;
	if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(1)}K`;
	return `$${val.toFixed(2)}`;
}

/**
 * Format a percentage.
 * @param {number} val - Decimal (e.g., 0.85)
 * @returns {string}
 */
export function formatPct(val) {
	if (val == null) return '—';
	return `${(val * 100).toFixed(1)}%`;
}

/**
 * Shorten a wallet address.
 * @param {string} addr
 * @returns {string}
 */
export function shortAddr(addr) {
	if (!addr || addr.length < 10) return addr || '—';
	return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

/**
 * Format a timestamp (ms) to readable date/time.
 * @param {number} ms
 * @returns {string}
 */
export function formatTime(ms) {
	if (!ms) return '—';
	return new Date(ms).toLocaleString('en-US', {
		month: 'short', day: 'numeric',
		hour: '2-digit', minute: '2-digit',
		hour12: false
	});
}

export { RAW_BASE };
