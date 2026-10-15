"""His X accounts: every change to what they show, for the owner.

Not a detection vector. A bio edit, a burst of posts or likes, a follow, or an
account going public is a signal about the person the owner copy-trades, and
identifies no Hyperliquid wallet — so it casts no vote, touches no tier, no
severity gate and no delivery health. `scripts/check_x_profiles.py` reads,
stores `data/x_watch/latest.json` (watch.yml is the single writer) and sends ONE
notice per run through `alerts.send_notice`, like position notices.

Both accounts are protected (measured 2026-10-15), so post text is unreadable;
what the keyless mirrors (api.fxtwitter.com, api.vxtwitter.com) still show —
the profile and its counters — captures nearly everything he does: a post moves
`tweets`, a like `likes`, a follow `following`. If an account goes public its
timeline becomes readable and each new post is reported with its text.

Pure functions. Rules this module keeps:
- a failed read is unreadable, never "nothing changed" (rule 5): the stored
  profile stands and nothing is diffed;
- a field a mirror does not carry is None and is never diffed (rule 6);
- an account's first reading, and a timeline's first reading, are baselines;
- accounts are pinned by numeric id, because neither mirror resolves an id and
  a handle can be renamed or reused by someone else.
"""

from __future__ import annotations

from datetime import datetime, timedelta

TEXT_FIELDS = ("name", "bio", "location", "website", "avatar", "banner")
FLAG_FIELDS = ("protected", "verified", "verification_type")
COUNTERS = ("tweets", "likes", "media", "following")
PROFILE_FIELDS = ("id", "handle", "joined", *TEXT_FIELDS, *FLAG_FIELDS, *COUNTERS, "followers")

# Followers move with other people's actions every few minutes; only a move this
# large since the last follower alert is news (operator's choice, 2026-10-15).
FOLLOWER_ABS = 2_000
FOLLOWER_SHARE = 0.01
# A counter change read from the other mirror, back to a value seen this
# recently, is one mirror's cache lagging the other — not him.
LAG_MINUTES = 60
MISSING_RUNS = 2
MAX_CHANGES = 200
MAX_SEEN_POSTS = 500

LABEL = {"name": "name", "bio": "bio", "location": "location", "website": "website",
         "avatar": "profile picture", "banner": "banner", "verified": "verified",
         "verification_type": "verification"}
VERBS = {"tweets": ("posted", "deleted"), "likes": ("liked", "unliked"),
         "media": ("posted media", "deleted media"), "following": ("followed", "unfollowed")}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str(value) -> str | None:
    return None if value is None else str(value)


def parse_fx(http_status: int | None, doc) -> tuple[str, dict | None]:
    """(status, profile) from api.fxtwitter.com/<handle>.

    `missing` when the mirror says the handle does not exist (it answers a
    302 to its homepage, or `code: 404`); `unreadable` for anything else that
    is not a profile.
    """
    if http_status in (301, 302, 404) or (isinstance(doc, dict) and doc.get("code") == 404):
        return "missing", None
    user = doc.get("user") if isinstance(doc, dict) and doc.get("code") == 200 else None
    if not isinstance(user, dict) or not user.get("id"):
        return "unreadable", None
    verification = user.get("verification") if isinstance(user.get("verification"), dict) else {}
    return "ok", {
        "id": str(user["id"]), "handle": _str(user.get("screen_name")),
        "joined": _str(user.get("joined")),
        "name": _str(user.get("name")), "bio": _str(user.get("description")),
        "location": _str(user.get("location")), "website": _website(user.get("website")),
        "avatar": _str(user.get("avatar_url")), "banner": _str(user.get("banner_url")),
        "protected": user.get("protected") if isinstance(user.get("protected"), bool) else None,
        "verified": verification.get("verified") if isinstance(verification.get("verified"), bool) else None,
        # The block is present, so a null type is "no verification", not unknown.
        "verification_type": (_str(verification.get("type")) or "") if verification else None,
        "tweets": _int(user.get("tweets")), "likes": _int(user.get("likes")),
        "media": _int(user.get("media_count")), "following": _int(user.get("following")),
        "followers": _int(user.get("followers")),
    }


def _website(value) -> str | None:
    if isinstance(value, dict):
        return _str(value.get("display_url") or value.get("url"))
    return "" if value is None else str(value)


def parse_vx(http_status: int | None, doc) -> tuple[str, dict | None]:
    """(status, profile) from api.vxtwitter.com/<handle>. It carries no likes,
    media, banner, website or verification: those are None, never diffed."""
    if http_status == 404 or (isinstance(doc, dict) and doc.get("error") == "User not found."):
        return "missing", None
    if not isinstance(doc, dict) or not doc.get("id") or "screen_name" not in doc:
        return "unreadable", None
    profile = dict.fromkeys(PROFILE_FIELDS)
    profile.update({
        "id": str(doc["id"]), "handle": _str(doc.get("screen_name")),
        "joined": _str(doc.get("created_at")), "name": _str(doc.get("name")),
        "bio": _str(doc.get("description")), "location": _str(doc.get("location")),
        "avatar": _str(doc.get("profile_image_url")),
        "protected": doc.get("protected") if isinstance(doc.get("protected"), bool) else None,
        "tweets": _int(doc.get("tweet_count")), "following": _int(doc.get("following_count")),
        "followers": _int(doc.get("followers_count")),
    })
    return "ok", profile


def pick(fx: tuple[str, dict | None], vx: tuple[str, dict | None]) -> dict:
    """One reading from the two mirrors: fx preferred (it carries every field),
    vx the fallback. `missing` only when BOTH say the handle does not exist:
    fx answers a spurious `404 User not found` for a live account on about one
    read in four (measured 2026-10-15: 5 of 20; vx 0 of 20), so its word alone
    is an unreadable read, never a deleted account."""
    if fx[0] == "ok":
        return {"status": "ok", "profile": fx[1], "source": "fx"}
    if vx[0] == "ok":
        return {"status": "ok", "profile": vx[1], "source": "vx"}
    if fx[0] == "missing" and vx[0] == "missing":
        return {"status": "missing", "profile": None, "source": None}
    return {"status": "unreadable", "profile": None, "source": None}


def _iso(now: datetime) -> str:
    return now.isoformat()


def _recent(history: list | None, now: datetime) -> list:
    cutoff = now - timedelta(minutes=LAG_MINUTES)
    out = []
    for value, at in history or []:
        try:
            if datetime.fromisoformat(at) >= cutoff:
                out.append([value, at])
        except (TypeError, ValueError):
            continue
    return out


def apply(account_id: str, handle: str, stored: dict | None, reading: dict,
          now: datetime) -> tuple[dict, list[dict]]:
    """(new stored account, changes) for one account's reading this run."""
    state = dict(stored or {})
    state.setdefault("handle", handle)
    prev_status = state.get("status")
    prev = state.get("profile")
    stamp = _iso(now)
    state["attempted_at"] = stamp
    base = {"at": stamp, "id": account_id, "handle": state.get("handle") or handle}

    def change(field, kind, before=None, after=None, **extra):
        return {**base, "field": field, "kind": kind, "before": before, "after": after, **extra}

    # `status` is the last DEFINITE answer (ok / missing / id_mismatch), which an
    # unreadable run leaves alone, so an outage between two "missing" readings
    # never re-announces the account as gone; `last_read` is this run's.
    status = reading.get("status")
    state["last_read"] = status
    if status == "unreadable":
        state.setdefault("unreadable_since", stamp)
        return state, []
    state.pop("unreadable_since", None)
    if status == "missing":
        # Announced once it has held for MISSING_RUNS definite readings in a
        # row: one double miss on a mirror pair this flaky is not a deletion.
        runs = state.get("missing_runs", 0) + 1
        state["missing_runs"] = runs
        if runs < MISSING_RUNS or prev_status == "missing":
            return state, []
        state["status"] = "missing"
        return state, [change("account", "missing")]
    state.pop("missing_runs", None)

    profile = reading["profile"]
    if profile.get("id") != account_id:
        state["status"] = "id_mismatch"
        state["mismatch_id"] = profile.get("id")
        if prev_status == "id_mismatch":
            return state, []
        return state, [change("account", "id_mismatch", after=profile.get("id"))]

    changes: list[dict] = []
    if prev_status in ("missing", "id_mismatch"):
        changes.append(change("account", "returned"))
    state["status"] = "ok"
    state.pop("mismatch_id", None)
    state["read_at"] = stamp
    prev_source = state.get("source")
    source = reading.get("source")
    state["source"] = source
    recent = {f: _recent((state.get("recent") or {}).get(f), now) for f in (*COUNTERS, "followers")}

    if prev is None:
        merged = dict(profile)
        state["followers_alerted"] = profile.get("followers")
    else:
        merged = {f: profile.get(f) if profile.get(f) is not None else prev.get(f)
                  for f in PROFILE_FIELDS}
        if (profile.get("handle") and prev.get("handle")
                and profile["handle"] != prev["handle"]):
            changes.append(change("handle", "text", prev["handle"], profile["handle"]))
        for f in (*TEXT_FIELDS, *FLAG_FIELDS):
            old, new = prev.get(f), profile.get(f)
            if old is not None and new is not None and old != new:
                changes.append(change(f, "flag" if f in FLAG_FIELDS else "text", old, new))
        for f in COUNTERS:
            old, new = prev.get(f), profile.get(f)
            if old is None or new is None or old == new:
                continue
            lagging = source != prev_source and any(v == new for v, _ in recent[f])
            if not lagging:
                changes.append(change(f, "counter", old, new, delta=new - old))
        followers, anchor = profile.get("followers"), state.get("followers_alerted")
        if followers is not None:
            if anchor is None:
                state["followers_alerted"] = followers
            elif abs(followers - anchor) >= max(FOLLOWER_ABS, FOLLOWER_SHARE * anchor):
                changes.append(change("followers", "counter", anchor, followers,
                                      delta=followers - anchor))
                state["followers_alerted"] = followers
    for f in recent:
        if profile.get(f) is not None:
            recent[f].append([profile[f], stamp])
    state["recent"] = recent
    state["profile"] = merged
    if merged.get("handle"):
        state["handle"] = merged["handle"]
    return state, changes


def parse_timeline(doc) -> list[dict] | None:
    """Posts from api.fxtwitter.com/2/profile/<handle>/statuses, or None when the
    read did not produce a timeline (a protected account answers 500 and empty
    results; a flaky read 404) — unread, never "no posts"."""
    if not isinstance(doc, dict) or doc.get("code") != 200 or not isinstance(doc.get("results"), list):
        return None
    posts = []
    for row in doc["results"]:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        author = (row.get("author") or {}).get("screen_name")
        reposter = (row.get("reposted_by") or {}).get("screen_name") if isinstance(row.get("reposted_by"), dict) else None
        reply = row.get("replying_to")
        posts.append({"id": str(row["id"]), "posted_at": _str(row.get("created_at")),
                      "text": _str(row.get("text")) or "", "url": _str(row.get("url")),
                      "author": author, "repost": bool(reposter),
                      "reply_to": reply if isinstance(reply, str) else None})
    return posts


def new_posts(account_id: str, handle: str, stored: dict, posts: list[dict] | None,
              now: datetime) -> tuple[dict, list[dict]]:
    """(stored account, one change per post not seen before). The first
    timeline read is a baseline; an unread timeline changes nothing."""
    state = dict(stored)
    if posts is None:
        return state, []
    seen = state.get("seen_posts")
    state["timeline_read_at"] = _iso(now)
    ids = [p["id"] for p in posts]
    if seen is None:
        state["seen_posts"] = ids[:MAX_SEEN_POSTS]
        return state, []
    known = set(seen)
    fresh = [p for p in posts if p["id"] not in known]
    state["seen_posts"] = (ids + [i for i in seen if i not in set(ids)])[:MAX_SEEN_POSTS]
    stamp = _iso(now)
    return state, [{"at": stamp, "id": account_id, "handle": state.get("handle") or handle,
                    "field": "post", "kind": "post", "before": None, "after": p["id"],
                    "text": p["text"], "url": p["url"], "repost": p["repost"],
                    "reply_to": p["reply_to"], "author": p["author"]}
                   for p in reversed(fresh)]


def _quote(value) -> str:
    text = "" if value is None else str(value)
    return f'"{text}"' if text else "(empty)"


def describe(c: dict) -> str:
    """One phone-readable line for a change."""
    h = f"@{c['handle']}"
    field, kind = c["field"], c["kind"]
    if kind == "missing":
        return (f"{h} no longer resolves: renamed, deactivated or suspended. "
                f"Open by id: https://x.com/i/user/{c['id']}")
    if kind == "returned":
        return f"{h} resolves again"
    if kind == "id_mismatch":
        return (f"{h} now belongs to a DIFFERENT account (id {c['after']}): he renamed. "
                f"Open his by id: https://x.com/i/user/{c['id']}")
    if kind == "post":
        what = "reposted" if c.get("repost") else ("replied" if c.get("reply_to") else "posted")
        text = (c.get("text") or "").replace("\n", " ")
        if len(text) > 280:
            text = text[:277] + "..."
        return f"{h} {what}: {text} {c.get('url') or ''}".rstrip()
    if field == "protected":
        return (f"{h} is now PUBLIC: posts are readable" if c["after"] is False
                else f"{h} went private again")
    if field == "handle":
        return f"{h} renamed @{c['before']} -> @{c['after']}"
    if kind == "counter":
        delta = c["delta"]
        if field == "followers":
            return f"{h} followers {c['before']:,} -> {c['after']:,} ({delta:+,})"
        verb = VERBS[field][0 if delta > 0 else 1]
        note = " (private: text not readable)" if field in ("tweets", "media") and delta > 0 else ""
        return f"{h} {verb} {abs(delta):,} ({field} {c['before']:,} -> {c['after']:,}){note}"
    if field in ("avatar", "banner"):
        url = str(c["after"]).replace("_normal.", "_400x400.")
        return f"{h} new {LABEL[field]}: {url}"
    return f"{h} {LABEL.get(field, field)}: {_quote(c['before'])} -> {_quote(c['after'])}"


def render(changes: list[dict]) -> tuple[str, str] | None:
    """ONE notice for every change this run, or None when nothing changed."""
    if not changes:
        return None
    handles = sorted({c["handle"] for c in changes})
    if len(changes) == 1:
        title = f"X: {describe(changes[0])}"[:120]
    else:
        title = f"X: {len(changes)} changes on @{', @'.join(handles)}"
    lines = [describe(c) for c in changes]
    lines.append("")
    lines += [f"https://x.com/{h}" for h in handles]
    return title, "\n".join(lines)


def trim(changes: list[dict]) -> list[dict]:
    return changes[-MAX_CHANGES:]
