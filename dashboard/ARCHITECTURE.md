# Ezekiel Review: phone app architecture

> A read-only, installable phone view of the wallets the detectors have
> identified. Every address opens its Hypurrscan wallet page.
> Scope: the `/review` route of this dashboard and the PWA shell that installs it.
> The rest of the dashboard is described in [`../docs/architecture.md`](../docs/architecture.md).

---

## 1. Purpose

The owner copy-trades the target by hand, from a phone. When the detectors
surface a wallet that might be his, the owner needs to see it **where they
are**, judge it in seconds, and open it on Hypurrscan to watch it trade.

What the app is for, in priority order:

1. **Every address is one tap from its Hypurrscan wallet page**
   (`https://hypurrscan.io/address/<full address>`).
2. **It installs on the home screen** and opens full-screen like an app.
3. **It shows what changed**: new wallets, tier changes and demotions since the
   owner last looked.
4. **It never presents stale or missing data as a current answer.**

What it is deliberately **not**:

- **Not a writer.** No buttons feed decisions back to the pipeline, so the
  phone holds no GitHub token and has no auth surface. Promoting a wallet stays
  an edit to `config.json`.
- **Not a live Hyperliquid client.** Hypurrscan already shows live state one
  tap away, so a second implementation would duplicate it.
- **Not an alert channel.** ntfy already does that, and duplicating it would
  split the operator's attention.

---

## 2. Stack

**Svelte on Vite, via SvelteKit.** This is the stack the dashboard already
runs, so the app is a route inside it rather than a second project:

| Piece | Version | Role |
|---|---|---|
| Svelte | 5 | Components. Existing pages use legacy syntax (`export let`, `$:`), and the new route matches them. |
| Vite | 6 | Dev server and bundler (`npm run dev` / `npm run build`) |
| SvelteKit | 2 | Routing, `$app/paths` base handling, built-in service-worker bundling |
| `@sveltejs/adapter-static` | 3 | Emits a static site with the `index.html` fallback |
| GitHub Pages | — | Hosting, deployed by `.github/workflows/deploy-dashboard.yml` on push to `dashboard/**` |
| `node --test` | built in | Unit tests (`npm test`), network-free, like the existing `api.test.js` |

**No new dependencies.** A second standalone Vite + Svelte app was considered
and rejected. It would duplicate `api.js`, the `Addr` component and the deploy
workflow, and the two copies of the Hypurrscan link logic would drift. That
logic exists as a single function precisely because inline copies once put a
shortened label into an href.

---

## 3. System context

```
 GitHub Actions (Python detectors)
   trace.yml ──► data/roster/latest.json        (tiers, vectors, evidence)
   watch.yml ──► data/watchlist/latest.json     (≤6 close-watch wallets)
        │  committed to main
        ▼
 raw.githubusercontent.com/Jayesh137/Ezekiel/main/data/...   ◄── fetch() at open
        ▲
        │
 iPhone home-screen icon ──► jayesh137.github.io/Ezekiel/review   (GitHub Pages)
        │                         │
        │                         └─ service worker: caches the APP SHELL only
        ▼
 tap an address ──► hypurrscan.io/address/0x…   (opens outside the app)
```

### Backend: none, deliberately

The app needs no server of its own, because the "backend" already exists and
already runs for free:

- **Compute**: the Python detectors on GitHub Actions write the roster and the
  watch.
- **Storage and API**: those JSON files are committed to `main` and served by
  `raw.githubusercontent.com` with CORS open, so the phone can `fetch()` them
  directly.
- **Hosting**: GitHub Pages serves the static app.

Adding a server (Firebase, Supabase, a VPS, or Cloudflare Workers) would add
an account, an uptime burden and a second copy of the data, all to return the
same JSON the repo already serves. Each item below turns a server from
overhead into a requirement, and each is a separate decision (§12):

| If you later want… | You need | Right choice here |
|---|---|---|
| Buttons that write back ("watch this", "not him") | Something holding a GitHub token off the phone | A Cloudflare Worker (free tier) that calls `workflow_dispatch`. Alternatively, reuse the Google Apps Script relay that already exists. |
| Native push notifications to the PWA | A push server with VAPID keys | The same Worker. ntfy already covers this, so probably never. |
| A **private** list | Auth in front of the data | Move the data out of the public repo. That is a whole-project change, not an app change (§11). |

The data is **not** baked into the build. The app fetches the JSON when it
opens, so it is exactly as fresh as the last committed run and needs no
redeploy when the roster changes. That is the existing dashboard's model,
reused as-is.

---

## 4. Files

```
dashboard/
├── ARCHITECTURE.md                    this file
├── static/
│   ├── manifest.webmanifest           NEW  PWA manifest
│   ├── icon-180.png                   NEW  apple-touch-icon (iOS home screen)
│   ├── icon-192.png                   NEW  manifest icon
│   └── icon-512.png                   NEW  manifest icon / splash
├── scripts/
│   └── make_icons.mjs                 NEW  generates the three PNGs (node:zlib, no deps)
└── src/
    ├── app.html                       EDIT manifest link, apple-* meta tags, theme-color
    ├── service-worker.js              NEW  app-shell cache (SvelteKit auto-registers it)
    ├── lib/
    │   ├── api.js                     reuse fetchRoster, fetchWatchlist, addressUrl, formatUSD
    │   ├── Addr.svelte                reuse: the one component that renders an address
    │   ├── review.js                  NEW  pure logic: filter, sort, diff, freshness
    │   └── review.test.js             NEW  node --test for review.js
    └── routes/
        ├── +layout.svelte             EDIT drop the desktop sidebar on /review
        └── review/
            ├── +page.svelte           NEW  page: fetch, compose, local "last seen"
            ├── WatchCard.svelte       NEW  one close-watch wallet
            └── WalletCard.svelte      NEW  one roster wallet
```

**The boundary that matters:** `review.js` holds **all** the decisions (what is
shown, in what order, what counts as new, what counts as stale) as pure
functions over plain objects. The `.svelte` files only render and handle taps.
That keeps every rule testable without a browser, the same split `api.js`
already follows.

---

## 5. Components

### 5.1 `review/+page.svelte`, the composer

- On mount it calls `fetchRoster()` and `fetchWatchlist()` in parallel.
- It reads the previous snapshot from `localStorage` (see §6.3), computes the
  diff with `review.js`, renders, and **then** writes the new snapshot. It
  writes only after a successful roster read: a failed fetch must never
  overwrite "last seen" with an empty list, or the next open would announce
  every wallet as new.
- Layout, top to bottom:
  1. **Freshness bar**: "Roster updated 3h ago · Watch updated 12m ago".
  2. **Close watch**: `WatchCard` × ≤6.
  3. **Tier chips**: Confirmed · Probable · Possible (default on), Watch (off),
     with counts.
  4. **Wallet list**: `WalletCard` × n.
- Pull-to-refresh is not built. A **Refresh** button in the freshness bar
  re-runs the fetch, which is enough for a standalone PWA where the browser's
  reload gesture is absent.

### 5.2 `WalletCard.svelte`

One roster row as a card, laid out for a ~390px wide screen:

- **Full address** in monospace, wrapped, rendered through `<Addr full>`, so
  the href is always the full address and a malformed one renders as plain
  text rather than a dead link. The whole address line is the tap target, at
  least 44px high (Apple's minimum).
- **Copy** button (`navigator.clipboard.writeText`) beside it.
- **Tier badge**, plus "was PROBABLE" when `tier_dropped_from` is set, and a
  **NEW** or **CHANGED** badge from the diff.
- **Vectors in plain English**, using the same labels as `routes/roster`. The
  map moves into `review.js` so both pages share one copy.
- **Figures**: HL account value, received from and sent to the target, chains,
  HL birth date. Any absent figure renders **"—", never "$0"** (rule 6).
- **Reasons**: collapsed. Tapping expands the `reasons[]` list.

### 5.3 `WatchCard.svelte`

One `data/watchlist` wallet:

- Address (as above), account value, **size vs the target** as `0.69x`, turning
  red at or above `1.15x`, the watch's own `outgrew_target` band.
- Agent and sub-account counts, each address linked through `Addr`.
- Last fill, as "3h ago".
- The `why` note, collapsed.
- `read_ok: false` renders **"could not read"** with the errors, never a card of
  dashes that looks like an empty wallet (rule 5).

### 5.4 `+layout.svelte` (edit)

On `/review` the desktop sidebar and header are hidden and the page is
full-bleed with safe-area padding (`env(safe-area-inset-*)`), because
`viewport-fit=cover` lets content run under the notch. The rest of the
dashboard is unchanged.

---

## 6. Logic (`src/lib/review.js`)

Every function is pure and covered by `review.test.js`.

### 6.1 Selection and order

```
visibleWallets(roster, { tiers }) →
    roster.wallets
      minus  tier == INFRASTRUCTURE      (never shown)
      minus  is_service                  (never shown)
      minus  wallet == roster.target     (he is followed already)
      keep   tier ∈ tiers                (default CONFIRMED, PROBABLE, POSSIBLE)
    sorted by
      1. tier rank        CONFIRMED > PROBABLE > POSSIBLE > WATCH
      2. flag             NEW / CHANGED first within a tier
      3. vector_count     desc
      4. rank_strength    desc, with absent treated as lowest (not 0.0)
      5. wallet           asc, only as a deterministic last resort
```

`rank_strength` is only used to **order** the list. It is never displayed as
a confidence, because it is the maximum of scores on different scales.
`roster.py` says the same.

### 6.2 Freshness

```
freshness(computed_at, now) → { minutes, level }
    level = 'ok'    ≤ 160 min
          = 'warn'  ≤ 360 min
          = 'stale' > 360 min    (the thresholds +layout.svelte already uses)
          = 'unknown' when computed_at is absent or unparseable
```

`unknown` renders as a warning, never as fresh.

### 6.3 "New since you last looked"

Stored under the `localStorage` key `ezekiel.review.lastSeen.v1`:

```json
{ "seen_at": "2026-09-22T08:00:00Z",
  "roster_computed_at": "...",
  "wallets": { "0xabc…": "POSSIBLE", "0xdef…": "CONFIRMED" } }
```

```
diff(previous, roster) → Map<wallet, 'new' | 'changed'>
    previous absent         → empty map. The first open is a baseline, not
                              "everything is new". This is the
                              extraAgents seeding rule.
    wallet not in previous  → 'new'
    tier differs            → 'changed'   (the card shows old → new)
    wallet in previous, gone from roster → listed once in a
                              "Dropped off the list" line, since a
                              disappearance is news too
```

Only visible tiers are stored, so a WATCH wallet rising to POSSIBLE reads as
new to the list. Every `localStorage` access is wrapped in `try/catch`. If
storage is unavailable, as in iOS private mode, the page renders without
badges and says "can't remember last visit". It never crashes and never
fabricates a baseline.

### 6.4 Formatting

`formatUSD` and `formatTime` are reused from `api.js`. `review.js` adds
`ago(ms, now)` ("3h ago", "2d ago") and `ratio(x)` ("0.69x"). Every formatter
returns `'—'` for `null` or `undefined`, and nothing coerces a missing value to
0.

---

## 7. Installability (PWA)

### 7.1 Why a PWA

iOS offers two ways to put an app on a phone. The App Store costs $99 a year,
requires review, and is pointless for a private tool. **Add to Home Screen**
(Safari → Share → Add to Home Screen) is free and immediate, and with the tags
below it launches full-screen with its own icon, no browser chrome, and its
own entry in the app switcher.

### 7.2 Manifest (`static/manifest.webmanifest`)

```json
{
  "name": "Ezekiel Review",
  "short_name": "Ezekiel",
  "start_url": "/Ezekiel/review",
  "scope": "/Ezekiel/",
  "display": "standalone",
  "background_color": "#0d1117",
  "theme_color": "#0d1117",
  "icons": [
    { "src": "/Ezekiel/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/Ezekiel/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

The paths carry the `/Ezekiel` base because Pages serves the site under it
(`svelte.config.js` → `paths.base`). `start_url` opens straight on `/review`,
not the desktop dashboard.

### 7.3 `app.html` additions

```html
<link rel="manifest" href="%sveltekit.assets%/manifest.webmanifest" />
<link rel="apple-touch-icon" href="%sveltekit.assets%/icon-180.png" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="Ezekiel" />
<meta name="theme-color" content="#0d1117" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
```

iOS ignores manifest icons for the home screen and reads only
`apple-touch-icon`, which is why that tag is required.

### 7.4 Icons

`scripts/make_icons.mjs` draws the three PNGs (a solid dark tile with a light
"E") using a minimal PNG encoder on `node:zlib`, so no image library is
added. The PNGs are committed, and the script is run by hand, not in CI.

### 7.5 Service worker (`src/service-worker.js`)

SvelteKit bundles and registers it automatically. It follows one rule:

- **The app shell is cache-first**: the `build` and `files` lists from
  `$service-worker`, keyed on `version`, with the old cache deleted on
  `activate`. The app opens instantly, even with a poor signal.
- **Data is never cached by the worker.** Requests to
  `raw.githubusercontent.com` pass straight to the network. Serving a cached
  roster offline would present a stale list as the current one, which is the
  exact failure the freshness bar exists to prevent. Offline, the page says
  "offline, can't reach the data" instead.

The worker's scope is `/Ezekiel/`, so it also speeds up the desktop pages. It
never affects their data for the same reason.

### 7.6 Links out

Hypurrscan links use `target="_blank" rel="noopener noreferrer"`, as `Addr`
already does. In a standalone iOS PWA this opens Safari's in-app browser over
the app, and **Done** returns to the list at the same scroll position.

---

## 8. Data contract

The app reads these fields. Anything else in the files is ignored, so the
Python side can add fields freely. Renaming or removing one of these is a
breaking change for the phone.

**`data/roster/latest.json`**

| Field | Use |
|---|---|
| `computed_at` | freshness |
| `target` | excluded from the list |
| `wallets[].wallet` | address → Hypurrscan |
| `wallets[].tier`, `tier_dropped_from` | badge, "was X" |
| `wallets[].vectors[]`, `vector_count` | plain-English chips, sort |
| `wallets[].rank_strength` | sort only |
| `wallets[].is_service`, `known_self` | exclusion, "his (config)" label |
| `wallets[].reasons[]` | expandable detail |
| `wallets[].evidence.hl_account_value` | figure |
| `wallets[].evidence.totals.received_from_target_usd`, `sent_to_target_usd` | figures |
| `wallets[].evidence.chains[]`, `hl_birth_ms` | figures |

**`data/watchlist/latest.json`**

| Field | Use |
|---|---|
| `computed_at` | freshness |
| `wallets[].address`, `read_ok`, `errors[]` | card, rule-5 state |
| `wallets[].account_value`, `target_value`, `size_ratio` | size vs target |
| `wallets[].agents[]`, `subaccounts[]` | counts and links |
| `wallets[].last_fill_ms` | "last traded" |
| `wallets[].why`, `source` | note, "roster" or "config" origin |

---

## 9. Failure behaviour

| Situation | Shows |
|---|---|
| Roster fetch fails | "Couldn't load the roster", plus Retry. Last-seen is untouched. |
| Watchlist fetch fails | Roster still renders, and the watch section says it could not load. The two are independent. |
| `computed_at` over 6h old | Amber or red freshness bar. The list still renders, clearly dated. |
| A watched wallet `read_ok: false` | "Could not read" with errors, never an empty-looking card |
| Figure absent | `—` |
| Malformed address | Plain text, no link (`addressUrl` returns null) |
| `localStorage` unavailable | No NEW/CHANGED badges, plus a one-line notice |
| Offline | Shell loads from cache, and the data section says offline |

---

## 10. Testing

- **`src/lib/review.test.js`** (`node --test`, network-free):
  - Selection excludes infrastructure, services and the target.
  - Order puts tier first, flagged wallets next, and absent `rank_strength`
    last rather than equal to 0.
  - Diff treats the first open as a baseline and detects new, changed and
    dropped wallets.
  - Freshness thresholds hold, and an absent timestamp reads as `unknown`.
  - Formatters return `—` for null and undefined, and `0` stays `$0`.
- **`api.test.js`**: existing `addressUrl` coverage already pins the
  Hypurrscan URL and refuses a shortened label.
- **Build check**: `npm run build`, then assert that `build/manifest.webmanifest`,
  the three icons and `build/service-worker.js` exist, and that `start_url`
  carries the `/Ezekiel` base.
- **Rendered check**: headless Chrome at 390×844 against `vite dev` and the live
  `main` data, the method recorded in memory as working here. Screenshot the
  list, confirm an address href is the full Hypurrscan URL, and confirm no
  horizontal scroll.
- **On the phone**: after deploy, Add to Home Screen, then confirm it launches
  standalone on `/review` and that a tapped address opens Hypurrscan.

---

## 11. Privacy

The Pages site and the repo are **public**. Anyone with the URL can read this
list, which is equally true of the existing Roster page and of the raw JSON
itself. The app adds no new exposure, but it does not hide anything either. If
that ever needs to change, the whole data model (public raw JSON) has to move,
not just this page.

---

## 12. Later, if wanted

Not built now, and each is a separate decision:

- **Write-back**: "watch this" or "not him", via a fine-grained GitHub token
  and `workflow_dispatch`.
- **Live HL snapshot** on a card, from the public `info` endpoint, if
  Hypurrscan proves too slow to glance at.
- **Web Push**: iOS 16.4+ supports push for installed PWAs, which could
  replace ntfy, but it needs a push server this project does not have.
