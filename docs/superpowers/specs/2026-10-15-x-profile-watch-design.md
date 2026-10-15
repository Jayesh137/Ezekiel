# X profile watch — design (2026-10-15)

**Ask (operator):** constant monitoring of his two X accounts, @GCRClassic and
@GiganticRebirth, with an alert on any and all changes; mostly hidden in the app
until something fires.

**What it is for.** Owner awareness. A bio change, a burst of posts or likes, a
follow, or the account going public is a signal about the person the owner
copy-trades. It identifies no Hyperliquid wallet, so it is **not a detection
vector**: it casts no vote, touches no tier, no severity gate and no delivery
health. It is delivered the way position notices are (`send_notice`).

## What X shows keylessly (measured 2026-10-15)

`api.fxtwitter.com/<handle>` and `api.vxtwitter.com/<handle>` answer without key
or login. Both accounts are **protected** (`protected: true`), so post text is
unreadable; reading it would need a follower account, which this project does not
build. What remains visible captures nearly everything he does:

| Field | Source | A change means |
|---|---|---|
| name, bio, location, website | fx (vx lacks website) | he edited his profile |
| avatar, banner URL | fx (vx lacks banner) | new picture |
| tweets | fx, vx | he posted (+n) or deleted (−n) |
| likes | fx | he liked / unliked |
| media | fx | posted or deleted media |
| following | fx, vx | he followed / unfollowed |
| protected, verification | fx (vx lacks verification) | going public / verification change |
| followers | fx, vx | other people; alert only on ≥1% or ≥2,000 since the last follower alert |

Accounts are pinned by numeric id (`1484112393361891328` GCRClassic,
`1289071298556170240` GiganticRebirth). Neither mirror resolves an id, so a
handle that stops resolving on both mirrors (vx 404 `User not found.`, fx 302)
is reported as *renamed, deactivated or suspended* with an `x.com/i/user/<id>`
link, which opens the account by id in a browser. A read whose id differs from
the pinned one is a handle reused by someone else: reported, never merged.

**Posts when public.** `api.fxtwitter.com/2/profile/<handle>/statuses?count=20`
returns a public account's timeline (measured: 200 with 20 results for a public
account; 500/empty for his protected one; flaky — 404 on some reads). When an
account reads `protected: false` the watch also reads the timeline and reports
each post id it has not seen (text, link, repost/reply marked). First timeline
read is a baseline. A failed timeline read is unread, never "no posts".

## Rules

- **Empty is not blind (rules 1, 5).** Both mirrors failing is `unreadable`; the
  stored profile is kept and nothing is diffed. A field a mirror does not carry
  is `None` and never diffed against a value (rule 6).
- **First reading is a baseline**, per account and per field.
- **Mirror lag.** fx is preferred, vx the fallback. A counter change read from a
  different mirror than the previous reading, whose new value equals a value seen
  in the last 60 minutes, is mirror lag: stored silently, not reported.
- **One notice per run**, `send_notice(priority="high")` so the phone sounds.
  `X_NOTICES=0` withholds it. Telegram sounds for anything above `low`.
- **Blind feed.** `X profiles` joins the watch feed group in
  `src/feed_health.py`: stale after 360 min; blind when every account is
  unreadable for `BLIND_HOURS`.

## Pieces

- `src/x_watch.py` — pure: `parse_fx`, `parse_vx`, `merge`, `diff`, `render`,
  `new_posts`, `parse_timeline`.
- `scripts/check_x_profiles.py` — fetch (timeout `(10, 30)`), apply, write
  `data/x_watch/latest.json` (single writer: watch.yml), send notice.
- `config.json` — `x_accounts: {id: handle}`.
- `watch.yml` — a step after the watchlist, `timeout-minutes: 2`, same guard.
- Dashboard: `fetchTripwires` reads `data/x_watch/latest.json`; Tripwires page
  and the phone review app show a **"His X accounts"** card only when a change
  landed in the last 7 days (before → after, time, link), otherwise one muted
  line. `X profiles` is listed in `DETECTOR_FEEDS`.

## State file `data/x_watch/latest.json`

```
{computed_at, accounts: {id: {handle, profile:{...}, source, read_at,
  status: ok|unreadable|missing|id_mismatch, followers_alerted,
  recent: {field: [[value, iso], ...]}, seen_posts: [ids], timeline_read_at}},
 changes: [{at, id, handle, field, kind, before, after, delta?, text?, url?}]  # newest 200
 last_change_at}
```

## Tests (network-free)

Baseline sends nothing; each field change; tweets ±; follower threshold and
re-anchoring; None fields never diffed; unreadable keeps state; mirror-lag
suppression; missing handle; id mismatch; going public; new posts from a
timeline and timeline baseline; render text.
