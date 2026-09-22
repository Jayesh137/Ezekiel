# Ezekiel Review: phone app architecture

> A read-only, installable phone app for reviewing the wallets the detectors
> have identified. It is feed-shaped like a social app and has a small review
> loop like a gamified one, with a clean design. Every address opens its
> Hypurrscan wallet page.
> Scope: the `/review` route of this dashboard, its UI kit, and the PWA shell
> that installs it. The rest of the dashboard is described in
> [`../docs/architecture.md`](../docs/architecture.md).

---

## 1. Purpose

The owner copy-trades the target by hand, from a phone. When the detectors
surface a wallet that might be his, the owner needs to see it **where they
are**, judge it in seconds, and open it on Hypurrscan to watch it trade.

What the app is for, in priority order:

1. **Every address is one tap from its Hypurrscan wallet page**
   (`https://hypurrscan.io/address/<full address>`).
2. **It installs on the home screen** and opens full-screen like an app.
3. **It makes checking a habit**: a feed of what changed, a review queue you
   can clear, and a daily streak.
4. **It never presents stale or missing data as a current answer.**

What it is deliberately **not**:

- **Not a writer.** Review marks live only in the phone's browser storage.
  Nothing feeds decisions back to the pipeline, so the phone holds no GitHub
  token and has no auth surface. Promoting a wallet stays an edit to
  `config.json`.
- **Not a live Hyperliquid client.** Hypurrscan already shows live state one
  tap away.
- **Not an alert channel.** ntfy already does that.

---

## 2. Stack

**Svelte on Vite, via SvelteKit**, the stack the dashboard already runs. The
app is a route inside it, not a second project.

| Piece | Version | Role |
|---|---|---|
| Svelte | 5 | Components. Existing pages use legacy syntax (`export let`, `$:`), and the new code matches them. |
| Vite | 6 | Dev server and bundler (`npm run dev` / `npm run build`) |
| SvelteKit | 2 | Routing, `$app/paths` base handling, built-in service-worker bundling |
| `@sveltejs/adapter-static` | 3 | Emits a static site with the `index.html` fallback |
| `svelte/transition`, `svelte/easing` | built in | All motion. No animation library. |
| GitHub Pages | — | Hosting, via `.github/workflows/deploy-dashboard.yml` on push to `dashboard/**` |
| `node --test` | built in | Unit tests (`npm test`), network-free |

**No new dependencies**, including no Tailwind; see §13 for why.

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

The "backend" already exists and already runs for free:

- **Compute**: the Python detectors on GitHub Actions.
- **Storage and API**: the committed JSON, served by `raw.githubusercontent.com`
  with CORS open.
- **Hosting**: GitHub Pages.

A server would add an account, an uptime burden and a second copy of the data,
all to return the same JSON. Each item below turns a server from overhead into
a requirement:

| If you later want… | You need | Right choice here |
|---|---|---|
| Buttons that write back ("watch this", "not him") | Something holding a GitHub token off the phone | A Cloudflare Worker (free tier) that calls `workflow_dispatch`, or the existing Google Apps Script relay |
| Native push notifications to the PWA | A push server with VAPID keys | The same Worker. ntfy covers this, so probably never. |
| Review marks shared across devices | A small key-value store | The same Worker plus KV |
| A **private** list | Auth in front of the data | Move the data out of the public repo. That is a whole-project change (§11). |

The data is **not** baked into the build. It is fetched when the app opens, so
it is always as fresh as the last committed run.

---

## 4. Files

```
dashboard/
├── ARCHITECTURE.md
├── scripts/make_icons.mjs             NEW  draws the PNG icons (node:zlib, no deps)
├── scripts/check_pwa_build.mjs        NEW  asserts the build is installable (npm run check:pwa)
├── static/
│   ├── manifest.webmanifest           NEW
│   ├── icon-180.png                   NEW  apple-touch-icon
│   ├── icon-192.png                   NEW
│   └── icon-512.png                   NEW
└── src/
    ├── app.html                       EDIT manifest + apple-* meta, viewport-fit=cover
    ├── app.css                        EDIT motion, radius and elevation tokens; reduced motion
    ├── service-worker.js              NEW  app-shell cache
    ├── lib/
    │   ├── api.js                     reuse: fetchRoster, fetchWatchlist, addressUrl, formatUSD, shortAddr
    │   ├── Addr.svelte                reuse
    │   ├── review.js                  NEW  pure logic: selection, order, review state, streak, avatars
    │   ├── review.test.js             NEW
    │   └── ui/                        NEW  the app's UI kit (§13), usable by any page
    │       ├── Avatar.svelte               identicon from the address
    │       ├── Sheet.svelte                bottom sheet on native <dialog>
    │       ├── Segmented.svelte            segmented control
    │       ├── ProgressRing.svelte         review progress
    │       ├── Pips.svelte                 evidence meter (vector count)
    │       ├── Skeleton.svelte             loading placeholder
    │       ├── Toast.svelte + toast.js     toast host + store (with Undo)
    │       └── toast.test.js
    └── routes/
        ├── +layout.svelte             EDIT no sidebar / bottom nav on /review
        └── review/
            ├── +page.svelte           the composer
            ├── Stories.svelte         close-watch row
            ├── WatchDetail.svelte     sheet body for one watched wallet
            ├── ReviewProgress.svelte  progress ring card + "all caught up"
            └── WalletPost.svelte      one roster wallet as a feed post
```

**The boundary that matters:** `review.js` holds **all** the decisions (what
is shown, in what order, what counts as new, what the streak is) as pure
functions over plain objects and an injected clock. Components only render
and handle taps, so every rule is testable without a browser.

---

## 5. Experience

### 5.1 Principles

Borrowed from social apps:
- **A feed** of wallets as posts, with an unread dot on anything new or changed.
- **Stories** for the close watch: a ring lights up when something about the
  wallet changed since you last tapped it.
- **Avatars**: each address gets a deterministic gradient identicon, so you
  recognise "the purple one" before reading hex.
- **Bottom sheets** for detail, so you never lose your place in the feed.

Borrowed from gamified apps:
- **A clearable queue**: a progress ring shows how many of the wallets needing
  review you've reviewed, finishing on "You're all caught up".
- **A streak**: consecutive days you've checked in.
- **Instant feedback**: a toast with Undo on every mark.

The one rule gamification must not break: **the game measures YOUR review,
never the wallet's likelihood.** There are no points, XP or loot-rarity tiers
on evidence. Making a wallet feel more or less likely because of how it is
dressed up is the same failure as fitting a tier to the story. Tiers keep
their sober colours, and the evidence meter shows a count of independent
vectors, not a score.

Clean means:
- One accent colour per tier, and nothing else coloured.
- Generous spacing.
- System font for text, mono only for addresses and figures, with
  `tabular-nums` on figures.
- Motion only where it explains something (§5.4).

### 5.2 Screen

```
┌──────────────────────────────────────┐
│ Ezekiel               🔥 4    ⟳       │  sticky, blurred header
│ Roster 3h ago · Watch 12m ago        │  freshness (amber >160m, red >360m)
├──────────────────────────────────────┤
│ (◉) (◉) (○)                          │  Stories: close watch
│ 0xdd53… 0xf078… 0x5b5d…              │
├──────────────────────────────────────┤
│  ◔ 12 / 38 reviewed                   │  ReviewProgress
│    3 new · 1 changed    Mark all ✓   │
├──────────────────────────────────────┤
│ [ For review 38 | Watch 149 | All ]  │  Segmented
├──────────────────────────────────────┤
│ ● (av) 0xf078…f19e     CONFIRMED  NEW │  WalletPost
│   ●●○  2 vectors agree               │
│   Shared funder/deposit · Transfer   │
│   $54  ·  ⇄ $161M / $141M  ·  4 chains│
│   0xf078969e55cabf9ae3f26afeb5ec62…  │  ← tap: Hypurrscan
│   [↗ Hypurrscan] [⧉ Copy] [✓ Reviewed]│
│   Why? ›                              │
├──────────────────────────────────────┤
│ 2 wallets left the list  ›            │  dropped notice
└──────────────────────────────────────┘
```

### 5.3 Interactions

| Action | Result |
|---|---|
| Tap the address or **↗ Hypurrscan** | Opens Hypurrscan **and marks the wallet reviewed**. Looking at it IS the review. |
| **✓ Reviewed** | Marks reviewed. Toast "Marked reviewed · Undo". |
| **⧉ Copy** | Copies the full address. Toast "Copied". |
| **Why? ›** | Opens a bottom sheet with every reason and the full evidence figures |
| Tap a story | Opens a sheet with `WatchDetail` and marks the story seen |
| **Mark all ✓** | Marks every visible post reviewed. Toast with Undo. |
| **⟳** | Refetches both files. The streak is counted once per day, on a successful load. |
| Segmented | Switches instantly, with no animation (§5.4) |

### 5.4 Motion (Emil Kowalski's rules, applied)

- **Frequent actions get no animation**: tab switches, marking reviewed,
  expanding "Why?". They happen dozens of times a session, and animation
  there reads as lag.
- **Everything else stays under 300ms**, `ease-out`
  (`cubic-bezier(0.23, 1, 0.32, 1)`):
  - Sheet: 240ms slide up. It closes instantly, which is native `<dialog>` behaviour, and exits should be faster than entrances anyway.
  - Toast: 180ms.
  - Pressed buttons: `scale(0.97)` at 100ms, which is feedback rather than
    decoration.
- **Delight only where it is rare**: the "all caught up" check draws once
  (400ms), when the queue empties.
- `prefers-reduced-motion: reduce` sets every duration to 0.

---

## 6. Logic (`src/lib/review.js`)

Every function is pure and covered by `review.test.js`.

### 6.1 Selection and order

```
eligible(roster)
    roster.wallets
      minus tier == INFRASTRUCTURE, minus is_service, minus wallet == roster.target

feed(roster, state, tab)
    'review' → eligible ∩ (tier ∈ {CONFIRMED, PROBABLE, POSSIBLE}  OR  status == 'changed')
    'watch'  → eligible ∩ tier == WATCH
    'all'    → eligible
    sorted by
      1. unread first         (status new | changed)
      2. tier rank            CONFIRMED > PROBABLE > POSSIBLE > WATCH
      3. vector_count         desc
      4. rank_strength        desc; absent sorts last, never as 0.0
      5. wallet               asc, deterministic last resort
```

A POSSIBLE wallet demoted to WATCH stays in "For review" as **changed** until
you review it, because a demotion is news. `rank_strength` only orders the
list and is never displayed; it is a maximum over different scales.

**Order is frozen for the session** (`sessionFeed(roster, orderState, state,
tab)`). Which wallets appear, and in what order, is computed from the state
at load time. Each row's status is live. Without this, marking a post would
re-sort the feed under your finger, and reviewing a demoted WATCH wallet
would make it vanish mid-read. **⟳** starts a new session.

### 6.2 Review state

A single `localStorage` key, `ezekiel.review.v1`:

```json
{ "reviewed": { "0xabc…": "POSSIBLE" },
  "stories":  { "0xdd5…": "1|0|0|1" },
  "streak":   { "day": "2026-09-22", "count": 4 } }
```

```
status(wallet, state)
    not in reviewed           → 'new'
    reviewed tier ≠ current   → 'changed'  (the post shows "was X")
    else                      → 'reviewed'

markReviewed(state, rows) → new state (immutable; Undo restores the old one)

dropped(roster, state)
    wallets in `reviewed` that are no longer eligible (gone, or became
    infrastructure). They are listed once in "N wallets left the list", and
    dismissing one removes it from `reviewed`.

progress(rows, state) → { done, total, fresh, changed }
    computed over the 'review' tab
```

The first open has an empty `reviewed` map, so everything shows as new. That
is deliberate: it is a genuine backlog to work through, and **Mark all ✓**
clears it in one tap.

Every storage access is wrapped in `try/catch`. If storage is unavailable, as
in iOS private mode, a one-line notice says "can't remember your reviews on
this device" and the feed still renders with no dots. The state is written
**only after a successful roster load**, so a failed fetch can never erase
it.

### 6.3 Streak

```
checkIn(streak, today)
    same day            → unchanged
    day after last      → count + 1
    any other           → count = 1
```

`today` is the local calendar date (`YYYY-MM-DD`), injected for tests. The
streak only moves on a successful load: opening the app offline does not
count, because you did not see anything.

### 6.4 Stories

```
storyKey(w) = `${agents.length}|${subaccounts.length}|${size_ratio ≥ 1.15 ? 1 : 0}|${read_ok ? 1 : 0}`
storyRing(w, state)
    read_ok false or size_ratio ≥ 1.15  → 'alert'  (red)
    storyKey ≠ stories[address]         → 'unseen' (accent gradient)
    else                                → 'seen'   (grey)
```

`last_fill_ms` is deliberately **not** in the key. It changes on nearly every
run, so a ring keyed on it would always be lit, and an always-lit ring means
nothing.

### 6.5 Avatars

`avatar(address) → { a, b, angle }`: two HSL hues and a gradient angle
derived from an FNV-1a hash of the lowercased address. The output is
deterministic and pure.

### 6.6 Freshness and formatting

- `freshness(iso, nowMs)` returns `{ minutes, level }` where `level` is one of
  `ok ≤160`, `warn ≤360`, `stale`, or `unknown`, the thresholds
  `+layout.svelte` already uses. `unknown` renders as a warning.
- `ago(ms, nowMs)` gives "3h ago", and `ratio(x)` gives "0.69x".
- Every formatter returns `'—'` for null. `formatUSD` from `api.js` is
  reused, and nothing coerces a missing value to 0.

---

## 7. Installability (PWA)

### 7.1 Why a PWA

The App Store costs $99 a year plus review, which is pointless for a private
tool. **Safari → Share → Add to Home Screen** is free and immediate, and with
the tags below the app launches full-screen with its own icon and its own
place in the app switcher.

### 7.2 Manifest

```json
{
  "name": "Ezekiel Review",
  "short_name": "Ezekiel",
  "start_url": "/Ezekiel/review",
  "scope": "/Ezekiel/",
  "display": "standalone",
  "background_color": "#0a0a0f",
  "theme_color": "#0a0a0f",
  "icons": [
    { "src": "/Ezekiel/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/Ezekiel/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

Paths carry the `/Ezekiel` base (`svelte.config.js` → `paths.base`).

### 7.3 `app.html`

```html
<link rel="manifest" href="%sveltekit.assets%/manifest.webmanifest" />
<link rel="apple-touch-icon" href="%sveltekit.assets%/icon-180.png" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="Ezekiel" />
<meta name="theme-color" content="#0a0a0f" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
```

iOS reads only `apple-touch-icon` for the home screen.

### 7.4 Icons

`scripts/make_icons.mjs` draws an opaque square tile (iOS rounds the corners
itself) with the story ring and an "E", using a minimal PNG encoder on
`node:zlib`. The ring sits inside the maskable safe zone. The PNGs are
committed, and the script is run by hand.

### 7.5 Service worker

- **App shell is cache-first**, keyed on `$service-worker`'s `version`, with
  old caches deleted on `activate`.
- **Data is never cached.** Requests to `raw.githubusercontent.com` go
  straight to the network. A cached roster shown offline would present a
  stale list as current. Offline, the page says so.

### 7.6 Links out

`target="_blank" rel="noopener noreferrer"`. In a standalone iOS PWA this
opens Safari's in-app browser over the app, and **Done** returns to the same
scroll position.

---

## 8. Data contract

The app reads only these fields. Renaming or removing one breaks the phone.

**`data/roster/latest.json`**: `computed_at`, `target`, and for each of
`wallets[]`: `.wallet`, `.tier`, `.tier_dropped_from`, `.vectors[]`,
`.vector_count`, `.rank_strength`, `.is_service`, `.known_self`, `.reasons[]`,
and the `.evidence` fields `hl_account_value`, `totals.received_from_target_usd`,
`totals.sent_to_target_usd`, `chains[]` and `hl_birth_ms`.

**`data/watchlist/latest.json`**: `computed_at` (with each wallet's
`checked_at` as fallback), and for each of `wallets[]`: `.address`,
`.read_ok`, `.errors[]`, `.account_value`, `.target_value`, `.size_ratio`,
`.agents[]`, `.subaccounts[]`, `.last_fill_ms`, `.why` and `.source`.

**Vector labels** cover every name `src/roster.py` defines: `transfer`,
`linkage`, `correlation`, `behavioural`, `hl_native`, `shared_agent`,
`explicit_link`, `dormancy_handoff` and `referral`. An unknown name renders
raw rather than disappearing.

---

## 9. Failure behaviour

| Situation | Shows |
|---|---|
| Roster fetch fails | "Couldn't load the roster", plus Retry. State is untouched. |
| Watchlist fetch fails | The feed still renders, and the stories row says it could not load |
| Data over 6h old | Red freshness line. The feed still renders, clearly dated. |
| Watched wallet `read_ok: false` | Red story ring, and the sheet says "could not read" with the errors |
| Figure absent | `—` |
| Malformed address | Plain text, no link |
| Storage unavailable | No unread dots and no streak, plus a one-line notice |
| Offline | Shell loads from cache, and the feed area says offline |
| Alert delivery is down (`data/alerts/latest.json`) | The layout's ALERTING IS DOWN banner, kept on the phone shell. It is the one fault that means ntfy will not reach this device. |

---

## 10. Testing

- **`review.test.js`**: selection exclusions, order, the tab rule for demoted
  wallets, status, dropped, progress, streak (same day, next day, gap, month
  boundary), story ring, avatar determinism, freshness thresholds, and
  formatters returning `—` for null.
- **`toast.test.js`**: queue, dismiss, Undo callback.
- **`api.test.js`** (existing) already pins `addressUrl`.
- **Build check**: `npm run check:pwa` (also a CI step in `test.yml`). It
  checks that the manifest, icons and `service-worker.js` are in `build/`,
  that `start_url` carries `/Ezekiel`, that every manifest icon exists, and
  that the prerendered pages carry the iOS tags.
- **Rendered check**, as done on 2026-09-22. Three traps apply:
  - **Chrome on Windows will not make a window narrower than ~500px.**
    `--window-size=390` screenshots a 500px layout cropped to 390, which looks
    like overflow and is not. Load the page in a 390px `<iframe>` on a probe
    page, and measure `scrollWidth` and each element's right edge there.
  - **Headless `--virtual-time-budget` does not advance CSS animations or
    resolve service-worker and Cache API promises.** A tick drawn by an
    animation looks missing, and a SW probe never returns. Drive Chrome in
    real time over CDP (`--remote-debugging-port` plus Node's built-in
    `WebSocket`) for anything involving the service worker.
  - **`vite preview` does not behave like Pages.** It answers unknown files
    with the app's 404 route. Serve `build/` with a small server that maps
    `/Ezekiel/x` to `x.html`, which is what GitHub Pages does.
- **Measured at build**: 0 elements past 390px; 70 of 70 Hypurrscan hrefs are
  full 40-hex addresses; every `$0.00` shown is a real 0 in the JSON; mark,
  why, story and mark-all all behave; the SW is active at `/Ezekiel/` with 57
  shell entries; an offline reload shows the cached shell and "You're
  offline", with review state untouched.
- **On the phone**: Add to Home Screen, confirm it launches standalone on
  `/review`, and confirm an address opens Hypurrscan.

---

## 11. Privacy

The Pages site and the repo are **public**. Anyone with the URL can read this
list, just as they can the existing Roster page and the raw JSON. Review marks
never leave the phone.

---

## 12. Later, if wanted

- **Write-back** ("watch this" / "not him") via a Worker and `workflow_dispatch`.
- **Swipe to mark reviewed** (beUI's Swipeable List pattern). The button comes
  first, and a gesture can follow if the button proves slow.
- **Live Hyperliquid snapshot** on a card.
- **Web Push**, which needs a push server.

---

## 13. UI kit: where each piece comes from

The owner pointed at ten sources. Measured on 2026-09-22:

| Source | What it is | Tech | Usable here? |
|---|---|---|---|
| ui.shadcn.com | 70+ primitives | React + Tailwind (shadcn CLI) | Patterns only. Its Svelte port is shadcn-svelte, see below. |
| beui.dev | 124 motion components (Bottom Sheet, Dock, Swipeable List, Notification Stack, Number Animation…) | React 19, Tailwind 4, Framer Motion | Patterns only |
| rareui.com | 20+ novelty components (Fluid Orb, Folder, Duration Picker…) | React, Motion, shadcn CLI | Not needed |
| reui.io | 1,149 shadcn compositions (Data Grid, Timeline, Kanban…) | React, shadcn | Patterns only |
| coss.com/ui | 55 components on Base UI (incl. **Segmented Control**, **Meter**) | React | Patterns only |
| transitions.dev | 43 transitions (Success check, Skeleton reveal, Toast, Sheet, Notification badge…) | CSS / React | CSS ports |
| beautifului.dev | 21 AI-agent patterns (Chat Composer, Thinking, Approval Card…) | copy-paste | Not applicable: no chat here |
| ui-skills.com | Agent skills plus a playbook | — | **Adopted as rules**: 44px touch targets, `tabular-nums`, matched nested radius, scale-on-press, aspect ratios against layout shift |
| designsystemchecklist.com | A checklist (Foundations, Design language, 29 core components with required states) | — | **Adopted as the acceptance bar** for each kit component |
| emilkowal.ski/ui/you-dont-need-animations | Animation rules | — | **Adopted**: §5.4 |

**Decision: hand-port the eight patterns this app needs into
`src/lib/ui/`, in Svelte, with no dependencies.** The alternatives were:

- **Adopt shadcn-svelte plus Tailwind v4 plus bits-ui.** This is the only real
  Svelte route into the shadcn ecosystem, and it is rejected for this app.
  Tailwind's preflight resets base styles globally, so it would restyle all
  eight existing dashboard pages, which are built on plain CSS variables.
  It would also pull in three dependencies for eight small components. If the
  whole dashboard is ever redesigned, this becomes the right call, and the kit
  below keeps shadcn's component names so a swap is mechanical.
- **Wrap React components.** Rejected outright: it means two frameworks in one
  bundle on a phone.

| Kit component | Pattern taken from | Checklist states it must meet |
|---|---|---|
| `Avatar` | shadcn/coss Avatar, with a generated identicon in place of an image | sizes, shape, accessible label (the address) |
| `Sheet` | beUI Bottom Sheet / shadcn Sheet, using native `<dialog>` for free focus trap and Esc | close action, focus trapping, keyboard, title labelling, reduced motion |
| `Segmented` | coss Segmented Control | selected state, keyboard (arrow keys), 44px targets, `role="tablist"` |
| `ProgressRing` | coss Meter / Progress | label, `role="progressbar"` with values |
| `Pips` | coss Meter | accessible label ("2 of 3 vectors") |
| `Skeleton` | shadcn Skeleton plus transitions.dev skeleton reveal | shapes, reduced motion |
| `Toast` | shadcn Sonner / transitions.dev toast | timeout (4s), stacking (max 3), supplementary action (Undo), reduced motion, `role="status"` |
| Success check | transitions.dev Success check | reduced motion |

Tokens are added to `app.css`, not a new file, so every page shares them:
`--radius-sm/md/lg`, `--dur-fast` (100ms), `--dur` (180ms), `--dur-slow`
(240ms), `--ease-out` and `--shadow-sheet`.
