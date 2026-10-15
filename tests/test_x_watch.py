"""His X accounts: profile and counter changes, read from keyless mirrors, one notice."""

from datetime import UTC, datetime, timedelta

from src import x_watch as xw

ID = "1289071298556170240"
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def fx_doc(**over):
    user = {"screen_name": "GiganticRebirth", "id": ID, "followers": 254_498, "following": 472,
            "likes": 586, "media_count": 132, "tweets": 714, "name": "GCR", "description": "",
            "location": "", "banner_url": "https://pbs.twimg.com/profile_banners/1/1",
            "avatar_url": "https://pbs.twimg.com/profile_images/1/a_normal.jpg",
            "joined": "Fri Jul 31 05:32:08 +0000 2020", "protected": True, "website": None,
            "verification": {"verified": True, "type": "individual"}}
    user.update(over)
    return {"code": 200, "message": "OK", "user": user}


def vx_doc(**over):
    doc = {"created_at": "Fri Jul 31 05:32:08 +0000 2020", "description": "", "followers_count": 254_498,
           "following_count": 472, "id": int(ID), "location": "", "name": "GCR",
           "profile_image_url": "https://pbs.twimg.com/profile_images/1/a_normal.jpg",
           "protected": True, "screen_name": "GiganticRebirth", "tweet_count": 714}
    doc.update(over)
    return doc


def read(fx=None, vx=None, fx_status=200, vx_status=200):
    return xw.pick(xw.parse_fx(fx_status, fx) if fx is not None or fx_status != 200 else ("unreadable", None),
                   xw.parse_vx(vx_status, vx) if vx is not None or vx_status != 200 else ("unreadable", None))


def run(stored, reading, now=T0):
    return xw.apply(ID, "GiganticRebirth", stored, reading, now)


def baseline(**over):
    state, changes = run(None, read(fx=fx_doc(**over)))
    assert changes == []
    return state


def test_parsers_read_both_mirrors_and_leave_missing_fields_none():
    status, p = xw.parse_fx(200, fx_doc())
    assert status == "ok" and p["id"] == ID and p["likes"] == 586 and p["media"] == 132
    assert p["verified"] is True and p["website"] == "" and p["protected"] is True
    status, v = xw.parse_vx(200, vx_doc())
    assert status == "ok" and v["tweets"] == 714 and v["id"] == ID
    assert v["likes"] is None and v["banner"] is None and v["verified"] is None


def test_parsers_tell_missing_from_unreadable():
    assert xw.parse_fx(302, None) == ("missing", None)
    assert xw.parse_fx(200, {"code": 404, "message": "Not found"}) == ("missing", None)
    assert xw.parse_fx(500, None) == ("unreadable", None)
    assert xw.parse_fx(200, {"code": 500}) == ("unreadable", None)
    assert xw.parse_vx(404, {"error": "User not found."}) == ("missing", None)
    assert xw.parse_vx(200, "<html>") == ("unreadable", None)


def test_one_mirror_saying_missing_is_not_a_missing_account():
    # fx answers a spurious 404 for a live account on ~1 read in 4
    assert xw.pick(("missing", None), ("unreadable", None))["status"] == "unreadable"
    assert xw.pick(("unreadable", None), ("missing", None))["status"] == "unreadable"
    assert xw.pick(("missing", None), ("missing", None))["status"] == "missing"
    assert xw.pick(("unreadable", None), ("unreadable", None))["status"] == "unreadable"
    assert xw.pick(("unreadable", None), xw.parse_vx(200, vx_doc()))["source"] == "vx"


def test_first_reading_is_a_baseline():
    state = baseline()
    assert state["status"] == "ok" and state["followers_alerted"] == 254_498


def test_every_profile_field_he_controls_is_reported():
    state = baseline()
    _, changes = run(state, read(fx=fx_doc(
        description="new bio", name="Ezekiel", location="Dubai", website="gcr.xyz",
        avatar_url="https://pbs.twimg.com/profile_images/2/b_normal.jpg",
        banner_url="https://pbs.twimg.com/profile_banners/1/2",
        verification={"verified": False, "type": None})), T0 + timedelta(minutes=10))
    fields = {c["field"] for c in changes}
    assert fields == {"bio", "name", "location", "website", "avatar", "banner", "verified",
                      "verification_type"}
    bio = next(c for c in changes if c["field"] == "bio")
    assert xw.describe(bio) == '@GiganticRebirth bio: (empty) -> "new bio"'
    avatar = next(c for c in changes if c["field"] == "avatar")
    assert "b_400x400.jpg" in xw.describe(avatar)


def test_counters_report_posts_likes_follows_and_deletions():
    state = baseline()
    _, changes = run(state, read(fx=fx_doc(tweets=716, likes=585, following=473, media_count=133)),
                     T0 + timedelta(minutes=10))
    lines = sorted(xw.describe(c) for c in changes)
    assert lines == [
        "@GiganticRebirth followed 1 (following 472 -> 473)",
        "@GiganticRebirth posted 2 (tweets 714 -> 716) (private: text not readable)",
        "@GiganticRebirth posted media 1 (media 132 -> 133) (private: text not readable)",
        "@GiganticRebirth unliked 1 (likes 586 -> 585)",
    ]


def test_followers_alert_only_on_a_big_move_and_re_anchor():
    state = baseline()
    state, changes = run(state, read(fx=fx_doc(followers=254_498 + 1_999)), T0 + timedelta(minutes=10))
    assert changes == []
    state, changes = run(state, read(fx=fx_doc(followers=254_498 + 2_600)), T0 + timedelta(minutes=20))
    assert [c["field"] for c in changes] == ["followers"] and changes[0]["delta"] == 2_600
    assert state["followers_alerted"] == 257_098
    _, changes = run(state, read(fx=fx_doc(followers=257_098 + 100)), T0 + timedelta(minutes=30))
    assert changes == []


def test_a_fallback_read_never_diffs_fields_it_does_not_carry():
    state = baseline()
    state, changes = run(state, read(vx=vx_doc()), T0 + timedelta(minutes=10))
    assert changes == []
    assert state["profile"]["likes"] == 586 and state["source"] == "vx"


def test_unreadable_keeps_the_stored_profile_and_reports_nothing():
    state = baseline()
    after, changes = run(state, read(), T0 + timedelta(minutes=10))
    assert changes == [] and after["profile"] == state["profile"] and after["status"] == "ok"
    assert after["last_read"] == "unreadable" and after["unreadable_since"]


def test_mirror_lag_back_to_a_recent_value_is_not_news():
    state = baseline()                                                   # fx: 714
    state, changes = run(state, read(fx=fx_doc(tweets=715)), T0 + timedelta(minutes=10))
    assert [c["field"] for c in changes] == ["tweets"]
    # vx lags at 714: back to a value fx showed 20 minutes ago, from the other mirror
    state, changes = run(state, read(vx=vx_doc(tweet_count=714)), T0 + timedelta(minutes=20))
    assert changes == []
    # the same value from the SAME mirror is a deletion
    state = baseline()
    state, _ = run(state, read(fx=fx_doc(tweets=715)), T0 + timedelta(minutes=10))
    _, changes = run(state, read(fx=fx_doc(tweets=714)), T0 + timedelta(minutes=20))
    assert xw.describe(changes[0]).startswith("@GiganticRebirth deleted 1")


def gone():
    return read(fx_status=302, vx_status=404, vx={"error": "User not found."})


def test_missing_account_needs_two_runs_is_reported_once_and_its_return_too():
    state = baseline()
    state, changes = run(state, gone(), T0 + timedelta(minutes=10))
    assert changes == []                                                  # one double miss
    state, changes = run(state, read(), T0 + timedelta(minutes=15))      # an outage keeps the streak
    assert changes == []
    state, changes = run(state, gone(), T0 + timedelta(minutes=20))
    assert [c["kind"] for c in changes] == ["missing"]
    assert f"https://x.com/i/user/{ID}" in xw.describe(changes[0])
    state, changes = run(state, read(), T0 + timedelta(minutes=30))
    assert changes == []
    state, changes = run(state, gone(), T0 + timedelta(minutes=40))
    assert changes == []
    state, changes = run(state, read(fx=fx_doc()), T0 + timedelta(minutes=50))
    assert [c["kind"] for c in changes] == ["returned"]


def test_a_single_double_miss_then_a_good_read_says_nothing():
    state = baseline()
    state, _ = run(state, gone(), T0 + timedelta(minutes=10))
    state, changes = run(state, read(fx=fx_doc()), T0 + timedelta(minutes=20))
    assert changes == [] and "missing_runs" not in state
    _, changes = run(state, gone(), T0 + timedelta(minutes=30))
    assert changes == []


def test_a_handle_now_held_by_someone_else_is_never_merged():
    state = baseline()
    after, changes = run(state, read(fx=fx_doc(id="999")), T0 + timedelta(minutes=10))
    assert [c["kind"] for c in changes] == ["id_mismatch"]
    assert after["profile"] == state["profile"]
    _, again = run(after, read(fx=fx_doc(id="999")), T0 + timedelta(minutes=20))
    assert again == []


def test_going_public_is_its_own_line():
    state = baseline()
    _, changes = run(state, read(fx=fx_doc(protected=False)), T0 + timedelta(minutes=10))
    assert xw.describe(changes[0]) == "@GiganticRebirth is now PUBLIC: posts are readable"


def timeline(*ids, code=200):
    return {"code": code, "results": [
        {"id": i, "created_at": "Thu Oct 15 12:21:35 +0000 2026", "text": f"post {i}",
         "url": f"https://x.com/GiganticRebirth/status/{i}",
         "author": {"screen_name": "GiganticRebirth"}, "reposted_by": None, "replying_to": None}
        for i in ids]}


def test_timeline_first_read_is_a_baseline_then_new_posts_alert_oldest_first():
    state = baseline()
    assert xw.parse_timeline({"code": 500, "results": []}) is None
    state, changes = xw.new_posts(ID, "GiganticRebirth", state, xw.parse_timeline(timeline("3", "2")), T0)
    assert changes == [] and state["seen_posts"] == ["3", "2"]
    state, changes = xw.new_posts(ID, "GiganticRebirth", state, None, T0)
    assert changes == []
    state, changes = xw.new_posts(ID, "GiganticRebirth", state,
                                  xw.parse_timeline(timeline("5", "4", "3")), T0)
    assert [c["after"] for c in changes] == ["4", "5"]
    assert xw.describe(changes[0]) == ("@GiganticRebirth posted: post 4 "
                                       "https://x.com/GiganticRebirth/status/4")


def test_render_is_one_notice_with_profile_links():
    state = baseline()
    _, changes = run(state, read(fx=fx_doc(tweets=715)), T0 + timedelta(minutes=10))
    title, body = xw.render(changes)
    assert title.startswith("X: @GiganticRebirth posted 1")
    assert body.endswith("https://x.com/GiganticRebirth")
    assert xw.render([]) is None
    title, _ = xw.render(changes * 2)
    assert title == "X: 2 changes on @GiganticRebirth"
