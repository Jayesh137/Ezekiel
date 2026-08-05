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
 * Fetch Hyperliquid-native transfer counterparties (send / internalTransfer /
 * spotTransfer). These are wallets the target moved funds to/from entirely inside
 * Hyperliquid — the most likely migration path, invisible to L1 tracing.
 */
export async function fetchHlTransfers() {
	return fetchJSON('data/hl_transfers/latest.json');
}

/**
 * Fetch the transfer graph: normalised transfer edges plus classified and
 * confidence-scored wallets discovered outward from the target.
 */
export async function fetchTransferGraph() {
	return fetchJSON('data/transfer_graph/latest.json');
}

/**
 * Fetch the unified migration risk score (0-100 with contributing factors).
 */
export async function fetchRisk() {
	return fetchJSON('data/risk/latest.json');
}

/**
 * Fetch deposit/withdrawal correlation matches (re-linked across a CEX gap).
 */
export async function fetchCorrelations() {
	return fetchJSON('data/correlations/latest.json');
}

/**
 * Fetch scan history across all dates to track wallet score trends.
 * @param {object} index - Data index with files.scans dates
 */
export async function fetchScanHistory(index) {
	const dates = index?.files?.scans || [];
	if (!dates.length) return [];
	const all = await mapLimit(dates, (d) => fetchDaily('scans', d));
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
/**
 * Run async tasks with bounded concurrency.
 *
 * The previous fetchAccountHistory issued one Promise.all over every task —
 * ~2,600 simultaneous fetches — while its comment claimed it batched. Anything
 * fanning out over dated files must go through here.
 * @template T,R
 * @param {T[]} items
 * @param {(item: T) => Promise<R>} fn
 * @param {number} limit
 * @returns {Promise<R[]>}
 */
async function mapLimit(items, fn, limit = 8) {
	const results = new Array(items.length);
	let next = 0;
	const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
		while (next < items.length) {
			const i = next++;
			results[i] = await fn(items[i]);
		}
	});
	await Promise.all(workers);
	return results;
}

/**
 * Fetch account value history.
 *
 * Days older than the live window are compacted into `data/account/daily/{date}.json`
 * (a few KB each) by scripts/compact_data.py; recent days still have per-minute
 * snapshots. Prefers the compact form and falls back to sampling snapshots.
 * @param {object} index - The data index
 * @returns {Promise<Array<{time:number, accountValue:number, totalPnl:number, marginUsed:number, totalNotional:number}>>}
 */
export async function fetchAccountHistory(index) {
	const out = [];

	// 1. Compact daily series for archived days.
	const dailyDates = index?.account_daily || [];
	const dailyRows = await mapLimit(dailyDates, async (date) => {
		const rows = await fetchJSON(`data/account/daily/${date}.json`);
		if (!Array.isArray(rows)) return [];
		return rows.map((r) => {
			const [hh, mm] = String(r.t).split('-');
			return {
				time: new Date(`${date}T${hh}:${mm}:00Z`).getTime(),
				accountValue: r.av ?? 0,
				totalPnl: r.pnl ?? 0,
				marginUsed: r.mu ?? 0,
				totalNotional: r.ntl ?? 0
			};
		});
	});
	for (const rows of dailyRows) out.push(...rows);

	// 2. Live (un-archived) days still stored as per-minute snapshots.
	const snapMap = index?.account_snapshots || {};
	const tasks = [];
	for (const [date, files] of Object.entries(snapMap)) {
		if (dailyDates.includes(date)) continue; // already covered by the compact series
		const step = files.length > 20 ? Math.ceil(files.length / 20) : 1;
		for (let i = 0; i < files.length; i += step) tasks.push({ date, file: files[i] });
	}
	const snapRows = await mapLimit(tasks, async ({ date, file }) => {
		const data = await fetchJSON(`data/account/${date}/${file}`);
		if (!data) return null;
		const perp = data.perp || data;
		const ms = perp.marginSummary || perp.crossMarginSummary || {};
		const totalPnl = (perp.assetPositions || []).reduce(
			(sum, ap) => sum + parseFloat(ap?.position?.unrealizedPnl || 0),
			0
		);
		const [hh, mm] = file.replace('.json', '').split('-');
		return {
			time: new Date(`${date}T${hh}:${mm}:00Z`).getTime(),
			accountValue: parseFloat(ms.accountValue || 0),
			totalPnl,
			marginUsed: parseFloat(ms.totalMarginUsed || 0),
			totalNotional: parseFloat(ms.totalNtlPos || 0)
		};
	});
	for (const r of snapRows) if (r) out.push(r);

	return out.sort((a, b) => a.time - b.time);
}

/**
 * Fetch all funding data across all available dates.
 * @param {object} index - The data index object
 */
export async function fetchAllFunding(index) {
	const dates = index?.files?.funding || [];
	if (!dates.length) return [];
	const all = await mapLimit(dates, (d) => fetchDaily('funding', d));
	return all.flat().filter(Boolean).sort((a, b) => (a.time || 0) - (b.time || 0));
}

/**
 * Effective match thresholds, as resolved by the backend for the latest scan.
 *
 * The backend adapts thresholds to the self-match ceiling from the backtest
 * (typically ~0.51/0.46/0.41, not the raw 0.90/0.80/0.65 in config.json). The
 * dashboard used to hardcode 0.90/0.80, so a wallet that emailed as a HIGH match
 * rendered as unremarkable grey. Always tier through these.
 * @param {object|null} scan - data/scans/latest.json
 * @returns {{high:number, medium:number, low:number, source:string}}
 */
/**
 * Which validation policy produced this sweep's dispositions.
 *
 * CURRENT_VALIDATED             — self-match passed; thresholds proven now.
 * CARRIED_FORWARD               — self-match inconclusive; reusing the last
 *                                 proven ceiling (same scoring schema).
 * POPULATION_WATCHLIST_FALLBACK — unvalidated; candidates ranked against the
 *                                 measured population, capped at WATCHLIST
 *                                 unless independently corroborated.
 * OBSERVING                     — unvalidated and calibration too small; evidence
 *                                 retained, nothing alerts.
 * @param {object|null} scan - data/scans/latest.json
 */
export function getPolicy(scan) {
	const policy = scan?.policy ?? null;
	const detail = scan?.policy_detail ?? {};
	const LABEL = {
		CURRENT_VALIDATED: 'Validated',
		CARRIED_FORWARD: 'Carried forward',
		POPULATION_WATCHLIST_FALLBACK: 'Population fallback',
		OBSERVING: 'Observing'
	};
	const ALERTS_ENABLED = {
		CURRENT_VALIDATED: true,
		CARRIED_FORWARD: true,
		POPULATION_WATCHLIST_FALLBACK: 'corroborated-only',
		OBSERVING: false
	};
	return {
		policy,
		label: LABEL[policy] ?? policy ?? 'unknown',
		behaviouralAlerts: ALERTS_ENABLED[policy] ?? false,
		schema: detail.scoring_schema ?? null,
		validatedAt: detail.validated_at ?? null,
		provenance: detail.provenance ?? null,
		carryForwardRejected: detail.carry_forward_rejected ?? null
	};
}

/**
 * How well a candidate matches the target *now*.
 *
 * Mirrors utils.candidate_current_score in the backend. `best_score` is a
 * high-water mark that only ratchets up, so tiering on it made the dashboard
 * assert a peak the wallet had already left — live data had a candidate showing
 * "75.1%" whose current score was 61.2%. The backend scores, grades and alerts
 * on the current value, and the displayed tier has to be the emailed tier.
 *
 * `best_score` is still the right thing to show as a labelled peak (the Recovery
 * table shows both); it is just not the answer to "does this wallet match?".
 * @param {{latest_score?:number, best_score?:number}|null|undefined} c
 * @returns {number}
 */
export function currentScore(c) {
	const v = c?.latest_score ?? c?.best_score;
	return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

/**
 * The candidate that matches best right now.
 *
 * candidates/latest.json is persisted sorted by best_score, so [0] is the
 * all-time leader rather than the current one. Re-rank instead of trusting the
 * file order, or the dashboard names a different wallet than risk.py does.
 * @param {Array<object>|null|undefined} cands
 * @returns {object|null}
 */
export function topCandidate(cands) {
	if (!Array.isArray(cands) || cands.length === 0) return null;
	return cands.reduce((best, c) => (currentScore(c) > currentScore(best) ? c : best));
}

export function getThresholds(scan) {
	const t = scan?.thresholds;
	if (t && typeof t.high === 'number') return t;
	// Pre-compaction scans stored raw config keys; fall back so old data still renders.
	if (t && typeof t.similarity_high === 'number') {
		return {
			high: t.similarity_high,
			medium: t.similarity_medium,
			low: t.similarity_low,
			source: 'legacy'
		};
	}
	return { high: 0.9, medium: 0.8, low: 0.65, source: 'default' };
}

/**
 * Tier label for a score under the given thresholds. Mirrors src/thresholds.py.
 * @param {number} score
 * @param {{high:number, medium:number, low:number}} th
 * @returns {'CONFIRMED'|'WATCH'|'WEAK'|'BACKGROUND'}
 */
export function tierFor(score, th) {
	if (score >= th.high) return 'CONFIRMED';
	if (score >= th.medium) return 'WATCH';
	if (score >= th.low) return 'WEAK';
	return 'BACKGROUND';
}

/**
 * Badge class for a score, tiered against the backend's effective thresholds.
 * @param {number} score
 * @param {{high:number, medium:number, low:number}} th
 */
export function badgeFor(score, th) {
	const tier = tierFor(score, th);
	if (tier === 'CONFIRMED') return 'badge-green';
	if (tier === 'WATCH') return 'badge-yellow';
	if (tier === 'WEAK') return 'badge-cyan';
	return 'badge-grey';
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

// --- explorer links ----------------------------------------------------------
// Single source of truth. Wallet addresses go to Hypurrscan; building the URL
// inline at each call site is how a shortened LABEL ends up in an href.

const HYPURRSCAN = 'https://hypurrscan.io';

/**
 * Is this a complete, well-formed EVM address?
 * A shortened label such as "0x45d2...4029" fails here by design.
 * @param {unknown} addr
 * @returns {boolean}
 */
export function isAddress(addr) {
	return typeof addr === 'string' && /^0x[0-9a-fA-F]{40}$/.test(addr.trim());
}

/**
 * Hypurrscan address page for a wallet, or null when the value is empty,
 * partial or malformed — callers must render plain text rather than a dead link.
 * Casing is preserved so a checksummed address stays checksummed.
 * @param {string} addr
 * @returns {string|null}
 */
export function addressUrl(addr) {
	if (!isAddress(addr)) return null;
	return `${HYPURRSCAN}/address/${encodeURIComponent(addr.trim())}`;
}

/**
 * Transaction link. Transactions are NOT addresses: Hyperliquid hashes resolve
 * on Hypurrscan, Arbitrum ones only on Arbiscan.
 * @param {{ref?: string, chain?: string}|string} hop
 * @param {string} [chain]
 * @returns {string|null}
 */
export function explorerTx(hop, chain) {
	const ref = typeof hop === 'string' ? hop : hop?.ref;
	const on = (typeof hop === 'string' ? chain : hop?.chain) || '';
	if (!ref || !/^0x[0-9a-fA-F]{6,}$/.test(String(ref).trim())) return null;
	const hash = encodeURIComponent(String(ref).trim());
	return on === 'arbitrum'
		? `https://arbiscan.io/tx/${hash}`
		: `${HYPURRSCAN}/tx/${hash}`;
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
 * Derive the global alert state from fund flows, candidates, and HL-native transfers.
 * Returns null when everything is normal, or an object { level, msg, wallet? }.
 * @param {object|null} fundFlows
 * @param {object|null} candidates
 * @param {object|null} hlTransfers
 */
export function getAlertState(fundFlows, candidates, hlTransfers, scan = null) {
	const findings = fundFlows?.findings || [];
	const cands = candidates?.candidates || [];
	// Tier against the backend's effective thresholds. Hardcoding 0.90/0.80 here
	// meant the banner stayed silent on wallets the backend had already emailed
	// about as HIGH matches (live example: a 0.749 top candidate).
	const th = getThresholds(scan);

	if (findings.some(f => f.deposited_to_hl)) {
		return { level: 'critical', msg: 'Fund trace found a wallet that deposited to Hyperliquid.' };
	}
	// Large in-platform outbound transfer to a wallet that isn't already known-linked.
	const freshOut = (hlTransfers?.counterparties || [])
		.filter(c => !c.known_self && c.total_out_usd >= 50000)
		.sort((a, b) => b.total_out_usd - a.total_out_usd)[0];
	if (freshOut) {
		return { level: 'critical', msg: `Target sent ${formatUSD(freshOut.total_out_usd)} to a new wallet inside Hyperliquid.`, wallet: freshOut.wallet };
	}
	if (findings.length > 0) {
		return { level: 'warn', msg: 'Outbound USDC transfers detected from target wallet.' };
	}
	const top = topCandidate(cands);
	const score = top ? currentScore(top) : null;
	if (typeof score === 'number' && score > 0) {
		const pct = (score * 100).toFixed(1);
		if (score >= th.high) {
			return { level: 'high', msg: `Confirmed behavioral match at ${pct}%`, wallet: top.wallet };
		}
		if (score >= th.medium) {
			return { level: 'medium', msg: `Strong behavioral lead at ${pct}%`, wallet: top.wallet };
		}
		if (score >= th.low) {
			return { level: 'watch', msg: `Watchlisted behavioral lead at ${pct}%`, wallet: top.wallet };
		}
	}
	return null;
}

export { RAW_BASE };
