// src/lib/api.js
// Data fetching utility — reads JSON from GitHub raw content

const OWNER = 'Jayesh137';
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
 * Fetch scanner results.
 */
export async function fetchScanResults() {
	return fetchJSON('data/scans/latest.json');
}

/**
 * Fetch persisted candidate wallet watchlist.
 */
export async function fetchCandidates() {
	return fetchJSON('data/candidates/latest.json');
}

/**
 * Fetch fund-flow tracing findings.
 */
export async function fetchFundFlows() {
	return fetchJSON('data/fund_flows/latest.json');
}

/**
 * Fetch scan history across all dates to track wallet score trends.
 * @param {object} index - Data index with files.scans dates
 */
export async function fetchScanHistory(index) {
	const dates = index?.files?.scans || [];
	if (!dates.length) return [];
	const all = await Promise.all(dates.map(d => fetchDaily('scans', d)));
	return all.filter(Boolean).flat();
}

/**
 * Fetch portfolio data (hourly account value + PnL history from HL API).
 */
export async function fetchPortfolio() {
	const data = await fetchJSON('data/portfolio/latest.json');
	if (!data || !Array.isArray(data)) return null;
	// Structure: [["day", { accountValueHistory: [[ts, val], ...], pnlHistory: [[ts, val], ...] }]]
	const entry = data.find(d => d[0] === 'day');
	if (!entry) return null;
	return entry[1];
}

/**
 * Fetch account snapshots across all dates using index.account_snapshots.
 * Fetches in batches to avoid overwhelming GitHub.
 * @param {object} index - The data index with account_snapshots map
 * @returns {Promise<Array<{time, accountValue, totalPnl, marginUsed, totalNotional}>>}
 */
export async function fetchAccountHistory(index) {
	const snapMap = index?.account_snapshots;
	if (!snapMap) return [];

	const tasks = [];
	for (const [date, files] of Object.entries(snapMap)) {
		// Sample: take every Nth snapshot to keep requests reasonable
		const step = files.length > 20 ? Math.ceil(files.length / 20) : 1;
		for (let i = 0; i < files.length; i += step) {
			tasks.push({ date, file: files[i] });
		}
	}

	const results = await Promise.all(
		tasks.map(async ({ date, file }) => {
			const data = await fetchJSON(`data/account/${date}/${file}`);
			if (!data) return null;
			const perp = data.perp || data;
			const ms = perp.marginSummary || perp.crossMarginSummary || {};
			const positions = perp.assetPositions || [];
			const totalPnl = positions.reduce((sum, ap) => {
				return sum + parseFloat(ap?.position?.unrealizedPnl || 0);
			}, 0);
			const [hh, mm] = file.replace('.json', '').split('-');
			return {
				time: new Date(`${date}T${hh}:${mm}:00Z`).getTime(),
				accountValue: parseFloat(ms.accountValue || 0),
				totalPnl,
				marginUsed: parseFloat(ms.totalMarginUsed || 0),
				totalNotional: parseFloat(ms.totalNtlPos || 0),
			};
		})
	);
	return results.filter(Boolean).sort((a, b) => a.time - b.time);
}

/**
 * Fetch all funding data across all available dates.
 * @param {object} index - The data index object
 */
export async function fetchAllFunding(index) {
	const dates = index?.files?.funding || [];
	if (!dates.length) return [];
	const all = await Promise.all(dates.map(d => fetchDaily('funding', d)));
	return all.flat().filter(Boolean).sort((a, b) => (a.time || 0) - (b.time || 0));
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

/**
 * Compute how many minutes ago data/index.json was last updated.
 * @param {object|null} index
 * @returns {number|null}
 */
export function getDataFreshnessMinutes(index) {
	if (!index?.last_updated) return null;
	return Math.max(0, Math.round((Date.now() - new Date(index.last_updated).getTime()) / 60000));
}

/**
 * Derive the global alert state from fund flows and candidates.
 * Returns null when everything is normal, or an object { level, msg, wallet? }.
 * @param {object|null} fundFlows
 * @param {object|null} candidates
 */
export function getAlertState(fundFlows, candidates) {
	const findings = fundFlows?.findings || [];
	const cands = candidates?.candidates || [];

	if (findings.some(f => f.deposited_to_hl)) {
		return { level: 'critical', msg: 'Fund trace found a wallet that deposited to Hyperliquid.' };
	}
	if (findings.length > 0) {
		return { level: 'warn', msg: 'Outbound USDC transfers detected from target wallet.' };
	}
	const top = cands[0];
	if (top?.best_score >= 0.90) {
		return { level: 'high', msg: `Confirmed behavioral match at ${(top.best_score * 100).toFixed(1)}%`, wallet: top.wallet };
	}
	if (top?.best_score >= 0.80) {
		return { level: 'medium', msg: `Strong behavioral lead at ${(top.best_score * 100).toFixed(1)}%`, wallet: top.wallet };
	}
	return null;
}

export { RAW_BASE };
