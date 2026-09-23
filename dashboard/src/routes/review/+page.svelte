<script>
	// The phone review app. Read-only: it fetches the committed roster and watch
	// files, and review marks live only in this device's localStorage.
	// Every rule lives in $lib/review.js; this file wires it to taps.
	import { onMount } from 'svelte';
	import { fetchRoster, fetchWatchlist, shortAddr } from '$lib/api.js';
	import {
		sessionFeed, tabCounts, progress, dropped, dismissDropped, markReviewed, markStorySeen,
		checkIn, localDay, freshness, watchFreshIso, readState, writeState, emptyState,
		walletKey, vectorLabel, TIER_LABEL
	} from '$lib/review.js';
	import { toast } from '$lib/ui/toast.js';
	import Toast from '$lib/ui/Toast.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import Segmented from '$lib/ui/Segmented.svelte';
	import Skeleton from '$lib/ui/Skeleton.svelte';
	import Stories from './Stories.svelte';
	import WatchDetail from './WatchDetail.svelte';
	import ReviewProgress from './ReviewProgress.svelte';
	import WalletPost from './WalletPost.svelte';

	let roster = null;
	let watch = null;
	let loading = true;
	let failed = false;
	let offline = false;
	let now = Date.now();
	let state = emptyState();
	// The state at load time. It freezes feed order and membership for the
	// session, so marking a post never moves it out from under your finger.
	let orderState = emptyState();
	let storageOk = true;
	let tab = 'likely';
	/** @type {null | {kind: 'why'|'watch'|'dropped', w?: any}} */
	let sheet = null;

	function storage() {
		try {
			return window.localStorage;
		} catch {
			return null;
		}
	}

	function persist() {
		storageOk = writeState(storage(), state);
	}

	async function load() {
		loading = true;
		const [r, wl] = await Promise.all([fetchRoster(), fetchWatchlist()]);
		now = Date.now();
		loading = false;
		if (!r) {
			// Rule 5: a failed read changes nothing: not the list on screen, not
			// the review marks, not the streak.
			failed = true;
			offline = typeof navigator !== 'undefined' && navigator.onLine === false;
			return;
		}
		failed = false;
		offline = false;
		roster = r;
		watch = wl;
		const read = readState(storage());
		storageOk = read.ok;
		state = { ...read.state, streak: checkIn(read.state.streak, localDay(new Date())) };
		orderState = state;
		if (storageOk) persist();
	}

	onMount(load);

	function mark(rows, label) {
		const todo = rows.filter((r) => r.status !== 'reviewed');
		if (!todo.length) return;
		const prev = state;
		state = markReviewed(state, todo);
		persist();
		toast(label, {
			action: {
				label: 'Undo',
				run: () => {
					state = prev;
					persist();
				}
			}
		});
	}

	function copy(addr) {
		if (!navigator.clipboard) {
			toast("Couldn't copy on this device");
			return;
		}
		navigator.clipboard.writeText(addr).then(
			() => toast('Address copied'),
			() => toast("Couldn't copy on this device")
		);
	}

	function openStory(w) {
		state = markStorySeen(state, w);
		persist();
		sheet = { kind: 'watch', w };
	}

	function dismissDrop(wallet) {
		state = dismissDropped(state, wallet);
		persist();
	}

	function fresh(f) {
		if (f.level === 'unknown') return 'age unknown';
		if (f.minutes < 1) return 'just now';
		if (f.minutes < 60) return `${f.minutes}m ago`;
		if (f.minutes < 48 * 60) return `${Math.floor(f.minutes / 60)}h ago`;
		return `${Math.floor(f.minutes / 1440)}d ago`;
	}

	// The queue is whichever tab is open: reviewing the strong wallets and
	// reviewing the weak leads are two different jobs with their own finish line.
	$: rows = roster ? sessionFeed(roster, orderState, state, tab) : [];
	$: prog = progress(rows);
	$: counts = roster ? tabCounts(roster, orderState) : null;
	// Today the Likely tab holds only his known wallets. Saying so stops two
	// familiar addresses reading as new finds.
	$: allKnown = tab === 'likely' && rows.length > 0 && rows.every((r) => r.known_self);
	$: drops = roster ? dropped(roster, state) : [];
	$: rosterFresh = freshness(roster?.computed_at, now);
	$: watchFresh = freshness(watchFreshIso(watch), now);
	$: streak = storageOk ? state.streak?.count || 0 : 0;
	$: tabs = [
		{ value: 'likely', label: 'Likely', count: counts?.likely ?? null },
		{ value: 'leads', label: 'Leads', count: counts?.leads ?? null }
	];
	$: sheetTitle = sheet?.kind === 'watch' ? 'Close watch'
		: sheet?.kind === 'dropped' ? 'Left the list'
		: sheet?.kind === 'why' ? `Why ${shortAddr(sheet.w.wallet)}`
		: '';
</script>

<svelte:head>
	<title>Review · Ezekiel</title>
</svelte:head>

<div class="review">
	<header class="top">
		<div class="bar">
			<span class="brand">Ezekiel</span>
			<span class="spacer"></span>
			{#if streak > 0}
				<span class="streak" title="Days in a row you've checked in" aria-label="{streak} day streak">
					<span aria-hidden="true">🔥</span> {streak}
				</span>
			{/if}
			<button class="icon" class:spin={loading} on:click={load} aria-label="Refresh" disabled={loading}>⟳</button>
		</div>
		{#if roster}
			<p class="fresh">
				<span class="lvl-{rosterFresh.level}">Roster {fresh(rosterFresh)}</span>
				<span aria-hidden="true">·</span>
				<span class="lvl-{watch ? watchFresh.level : 'unknown'}">Watch {watch ? fresh(watchFresh) : 'unavailable'}</span>
			</p>
		{/if}
	</header>

	{#if loading && !roster}
		<div class="stack" aria-busy="true" aria-label="Loading">
			<Skeleton height="96px" radius="var(--radius-lg)" />
			<Skeleton height="80px" radius="var(--radius-lg)" />
			<Skeleton height="52px" radius="var(--radius-md)" />
			<Skeleton height="260px" radius="var(--radius-lg)" />
			<Skeleton height="260px" radius="var(--radius-lg)" />
		</div>
	{:else if failed && !roster}
		<div class="empty">
			<p class="title">{offline ? "You're offline" : "Couldn't load the roster"}</p>
			<p class="sub">Nothing is shown rather than an old list presented as current.</p>
			<button class="btn" on:click={load}>Try again</button>
		</div>
	{:else if roster}
		{#if failed}
			<p class="warn" role="alert">Refresh failed. This is still the list from {fresh(rosterFresh)}.</p>
		{/if}
		{#if !storageOk}
			<p class="warn">Can't remember your reviews on this device (private browsing?).</p>
		{/if}
		{#if rosterFresh.level === 'stale'}
			<p class="warn stale" role="alert">
				The roster is {fresh(rosterFresh)}. The detectors may have stopped; anything newer is not here.
			</p>
		{/if}

		<Stories {watch} {state} on:open={(e) => openStory(e.detail)} />

		<Segmented options={tabs} bind:value={tab} label="Wallet list" />

		<p class="tabnote">
			{#if tab === 'likely'}
				Two or more independent vectors agree. That is the strongest evidence
				this project produces, because the ways the vectors can be fooled do
				not overlap.
			{:else}
				One vector only. A lead, not a candidate: a single signal is the one
				that has repeatedly turned out to be an exchange, a bot or a
				coincidence.
			{/if}
		</p>

		{#if allKnown}
			<p class="warn">
				Every Likely wallet here is one you already know is his. Nothing new has
				reached two agreeing vectors.
			</p>
		{/if}

		<ReviewProgress p={prog} on:markall={() => mark(rows, `${prog.total - prog.done} marked reviewed`)} />

		<div class="stack">
			{#each rows as w (w.wallet)}
				<WalletPost
					{w}
					reviewedTier={orderState.reviewed[walletKey(w.wallet)] ?? null}
					on:reviewed={(e) => mark([e.detail], 'Marked reviewed')}
					on:copy={(e) => copy(e.detail)}
					on:why={(e) => (sheet = { kind: 'why', w: e.detail })}
				/>
			{:else}
				<div class="empty">
					{#if tab === 'likely'}
						<p class="title">No unknown wallet is likely today</p>
						<p class="sub">
							Nothing outside the wallets you already know as his reaches two
							agreeing vectors. That is a real finding, not an empty screen:
							a tier nobody can defend would put your attention on the wrong
							wallet.
						</p>
						<button class="btn" on:click={() => (tab = 'leads')}>
							See the {counts?.leads ?? 0} one-vector lead{counts?.leads === 1 ? '' : 's'}
						</button>
					{:else}
						<p class="sub">No leads in this tab.</p>
					{/if}
				</div>
			{/each}
		</div>

		{#if drops.length}
			<button class="drops" on:click={() => (sheet = { kind: 'dropped' })}>
				{drops.length} wallet{drops.length === 1 ? '' : 's'} you reviewed left the list ›
			</button>
		{/if}

		<p class="foot">
			{#if counts?.hidden}
				{counts.hidden} further wallets carry no vector at all.
			{/if}
			{#if counts?.offHl}
				{counts.offHl} are not on Hyperliquid, so you could not follow them there.
			{/if}
			{#if counts?.hidden || counts?.offHl}
				Both are watched by the pipeline and not listed here.
				<br />
			{/if}
			Read-only. Reviews are stored on this device only. Tiers come from how many
			independent vectors agree, never from one score.
		</p>
	{/if}
</div>

<Sheet open={!!sheet} title={sheetTitle} on:close={() => (sheet = null)}>
	{#if sheet?.kind === 'watch'}
		<WatchDetail w={sheet.w} {now} />
	{:else if sheet?.kind === 'why'}
		{#if sheet.w.vectors?.length}
			<div class="chips">
				{#each sheet.w.vectors as v}<span class="chip">{vectorLabel(v)}</span>{/each}
			</div>
		{/if}
		<ul class="reasons">
			{#each sheet.w.reasons || [] as r}<li>{r}</li>{:else}<li class="muted">No reasons recorded.</li>{/each}
		</ul>
	{:else if sheet?.kind === 'dropped'}
		<p class="sub">
			Wallets you reviewed that are no longer on the roster, or are now graded as a
			service. A disappearance is news too.
		</p>
		<ul class="drop-list">
			{#each drops as d (d.wallet)}
				<li>
					<span class="mono">{shortAddr(d.wallet)}</span>
					<span class="sub">was {TIER_LABEL[d.tier] || d.tier}</span>
					<button class="btn small" on:click={() => dismissDrop(d.wallet)}>Dismiss</button>
				</li>
			{:else}
				<li class="muted">Nothing left to dismiss.</li>
			{/each}
		</ul>
	{/if}
</Sheet>

<Toast />

<style>
	.review {
		max-width: 560px;
		margin: 0 auto;
		padding: 0 16px calc(96px + env(safe-area-inset-bottom, 0px));
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.top {
		position: sticky;
		top: 0;
		z-index: 10;
		margin: 0 -16px;
		padding: calc(10px + env(safe-area-inset-top, 0px)) 16px 10px;
		background: color-mix(in srgb, var(--bg-primary) 84%, transparent);
		backdrop-filter: blur(16px);
		-webkit-backdrop-filter: blur(16px);
		border-bottom: 1px solid var(--border);
	}
	.bar {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.brand {
		font-weight: 800;
		font-size: 1.3rem;
		letter-spacing: -0.02em;
	}
	.spacer { flex: 1; }
	.streak {
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		padding: 6px 10px;
		border-radius: 999px;
		background: color-mix(in srgb, var(--accent-yellow) 12%, transparent);
		color: var(--accent-yellow);
	}
	.icon {
		min-width: 44px;
		min-height: 44px;
		border-radius: 50%;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-primary);
		font-size: 1.15rem;
		cursor: pointer;
		transition: transform var(--dur-fast) var(--ease-out);
	}
	.icon:active { transform: scale(0.94); }
	.icon:disabled { color: var(--text-muted); }
	.icon.spin { animation: spin 0.9s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }
	.fresh {
		margin: 4px 0 0;
		font-size: 0.76rem;
		color: var(--text-muted);
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.lvl-ok { color: var(--text-secondary); }
	.lvl-warn,
	.lvl-unknown { color: var(--accent-yellow); }
	.lvl-stale { color: var(--accent-red); }
	.stack {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.empty {
		text-align: center;
		padding: 40px 12px;
	}
	.title {
		font-weight: 700;
		margin: 0 0 4px;
	}
	.sub {
		color: var(--text-secondary);
		font-size: 0.85rem;
		line-height: 1.5;
	}
	.muted { color: var(--text-muted); }
	.tabnote {
		margin: -6px 0 0;
		font-size: 0.76rem;
		line-height: 1.5;
		color: var(--text-muted);
	}
	.warn {
		margin: 0;
		font-size: 0.82rem;
		line-height: 1.45;
		color: var(--accent-yellow);
	}
	.warn.stale { color: var(--accent-red); }
	.btn {
		min-height: 44px;
		padding: 0 18px;
		border-radius: var(--radius-md);
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text-primary);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}
	.btn.small {
		min-height: 36px;
		padding: 0 12px;
		font-size: 0.8rem;
	}
	.drops {
		min-height: 48px;
		border-radius: var(--radius-lg);
		border: 1px dashed var(--border);
		background: transparent;
		color: var(--text-secondary);
		font: inherit;
		cursor: pointer;
	}
	.foot {
		margin: 8px 0 0;
		text-align: center;
		font-size: 0.74rem;
		line-height: 1.5;
		color: var(--text-muted);
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-bottom: 4px;
	}
	.chip {
		font-size: 0.76rem;
		padding: 4px 10px;
		border-radius: 999px;
		background: var(--bg-secondary);
		border: 1px solid var(--border);
		color: var(--text-secondary);
	}
	.reasons {
		padding-left: 18px;
		margin: 8px 0 0;
		line-height: 1.55;
		font-size: 0.9rem;
	}
	.reasons li + li { margin-top: 6px; }
	.drop-list {
		list-style: none;
		padding: 0;
		margin: 8px 0 0;
	}
	.drop-list li {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--border);
	}
	.drop-list .sub { flex: 1; }
</style>
