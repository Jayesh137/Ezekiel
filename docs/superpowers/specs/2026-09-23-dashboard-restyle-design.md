# Dashboard restyle — design (2026-09-23)

**Goal.** Make the whole dashboard one elegant product, using the ten sources the
owner named (surveyed in `dashboard/ARCHITECTURE.md` §13) as the pattern library.
Presentation only: no page changes what it computes, shows, or caveats.

**Approach.** Tokens first, then the shared primitives, then a page sweep. No new
dependencies, no Tailwind (§13's reasoning still holds). Chosen over a CSS-only
restyle (leaves per-page duplicates) and a big-bang rewrite (unreviewable, and the
pages carry rule-5 caveats a rewrite would lose).

## Visual direction

Quiet shadcn/Linear dark. Zinc neutrals, one indigo interaction accent,
desaturated status colours that carry meaning only (tier, severity, delta).
Inter for text and figures (`tabular-nums` everywhere), JetBrains Mono only for
addresses and hashes. A faint accent glow at the top of the canvas; hairline
borders with a 1px top highlight on cards instead of flat fills.

## Layers

1. **Tokens** (`src/app.css`). Surfaces, borders, text, accents, soft tints
   (`color-mix`), radii, spacing, shadows, motion. The legacy names
   (`--bg-card`, `--accent-green`, …) remain as the public API so every page and
   the phone app pick up the palette without edits. Text contrast re-measured
   against the lightest surface it sits on; AA floor kept.
2. **Global primitives** (`src/app.css`): `.card`, `.badge-*` (with status dot),
   `table` (sticky, quiet header, hover row), `.stat-*`, `.page-header`, buttons,
   inputs/selects, `.kicker`. These are the component layer the eight pages
   already consume by class name.
3. **Chart theme** (`src/lib/ui/chartTheme.js`): one palette + scale/tooltip
   factory read by every Chart.js call, plus `Chart.defaults` set once.
4. **Shell** (`+layout.svelte`): grouped sidebar with inline-SVG icons, brand
   mark, health pills restyled but thresholds and wording unchanged; mobile nav
   with the same icons. The ALERTING IS DOWN banner stays the loudest element.
5. **Page sweep**: replace hard-coded neon rgba/hex with tokens; fold per-page
   one-offs onto the primitives where they are the same thing.

## Motion (Emil Kowalski)

No motion on frequent actions (nav, tabs, sort). Hover colour transitions
≤150ms ease-out. Reduced motion already zeroes durations globally.

## Acceptance

- `npm test` and `npm run build` pass.
- Before/after headless screenshots of all eight pages plus `/review`.
- Phone data contract (ARCHITECTURE.md §8) untouched — no JS field changes.
- Every text colour ≥4.5:1 on `--bg-card-hover`.
