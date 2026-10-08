"""The index a phone, a dashboard and a reader in a year all read (spec §11)."""

from src.casebook import cases, report, score

A, B, C = ("0x" + c * 40 for c in "abc")


def case(address, evidence=None, **extra):
    base = {"address": address, "evidence": evidence or {}, "links": {}, "known": None, "excluded": None,
            "ruling": None, "opened_at": "2026-09-10T00:00:00Z", "last_change": "2026-10-01T00:00:00Z",
            "opened_by": ["tier:POSSIBLE"],
            "roster": {"tier": "WATCH", "peak_tier": "PROBABLE", "reasons": ["old reason"]}, "hl": {}}
    base.update(extra)
    return base


def entry(kind, status="current", summary="s", strength=None, **facts):
    return {"kind": kind, "status": status, "summary": summary, "strength": strength, "facts": facts}


def build(cases, known=frozenset()):
    scores = score.score_all(cases, set(known))
    return report.build_index(cases, scores, rejected={}, run={"roster_status": "consumed"},
                              target={"silent_days": 2}, calibration=score.calibration(cases, scores),
                              now_iso="2026-10-08T00:00:00Z")


def test_known_first_then_ranked_unknown_then_excluded():
    cases = {A: case(A, {"two_way_flow": entry("two_way_flow")}, known="config:known_self"),
             B: case(B, {"private_deposit_address": entry("private_deposit_address", summary="Paid his deposit")}),
             C: case(C, {"shared_agent": entry("shared_agent")}, excluded={"reason": "service: busy"})}
    index = build(cases, {A})
    rows = index["cases"]
    assert [r["address"] for r in rows] == [A, B, C]
    assert rows[0]["rank"] is None and rows[0]["known"] == "config:known_self"
    assert rows[1]["rank"] == 1 and rows[2]["rank"] is None and rows[2]["excluded"] == "service: busy"
    counts = index["counts"]
    assert (counts["cases"], counts["known"], counts["unknown"], counts["excluded"]) == (3, 1, 1, 1)
    assert index["schema"] == "casebook-index/1" and index["model"]["version"]
    assert index["calibration"]["recall"][0]["address"] == A


def test_a_headline_says_why_and_where_it_stands_on_hyperliquid():
    c = case(B, {"private_deposit_address": entry("private_deposit_address",
                                                  summary="Paid his deposit address $5")},
             hl={"on_hl": True, "total_value": 52_061_448.0, "month_volume": 121e6, "last_ok_at": "x"})
    row = build({B: c})["cases"][0]
    assert row["headline"].startswith("Paid his deposit address $5")
    assert "$52.1M on Hyperliquid" in row["headline"]
    assert row["followable"] is True and row["hl"]["value"] == 52_061_448.0


def test_a_case_with_only_history_says_so():
    c = case(B, {"amount_correlation": entry("amount_correlation", "historical", summary="old match",
                                             strength=0.9)})
    row = build({B: c})["cases"][0]
    assert row["lapsed_only"] is True and row["statuses"] == {"historical": 1}
    assert row["headline"].startswith("old match (historical)")
    assert any("history" in check for check in report.next_checks(c))


def test_a_case_with_nothing_scored_falls_back_to_the_roster():
    row = build({B: case(B, {"graph_reach": entry("graph_reach")})})["cases"][0]
    assert row["headline"].startswith("old reason") and row["statuses"] == {}


def test_the_family_table_in_a_row_is_compact():
    c = case(B, {"two_way_flow": entry("two_way_flow"), "portfolio_overlap": entry("portfolio_overlap")})
    fams = build({B: c})["cases"][0]["families"]
    assert list(fams) == ["money"] and fams["money"][1] == 1.0 and fams["money"][3] == "current"


def test_not_followable_without_an_account_or_activity():
    assert report.followable({"hl": {"on_hl": False}}) is False
    assert report.followable({"hl": {"on_hl": True, "total_value": 5.0, "month_volume": 0.0}}) is False
    assert report.followable({"hl": {"on_hl": True, "total_value": 5e4, "month_volume": 10.0}}) is True
    assert report.followable({"hl": {}}) is False


def test_next_checks_point_at_the_cheapest_useful_read():
    trading = case(B, hl={"on_hl": True, "total_value": 5e4, "month_volume": 10.0, "last_ok_at": "x"})
    assert any("study_wallets" in c for c in report.next_checks(trading))
    absent = case(B, hl={"on_hl": False, "last_ok_at": "x"})
    assert any("opens one" in c for c in report.next_checks(absent))


def test_rejected_are_carried_newest_first_and_bounded():
    rejected = {f"0x{2 ** 100 + i:040x}": {"address": f"0x{2 ** 100 + i:040x}", "reason": "r",
                                           "last_seen": f"2026-10-{i % 28 + 1:02d}T00:00:00Z"}
                for i in range(report.MAX_REJECTED_SHOWN + 10)}
    scores = score.score_all({}, set())
    index = report.build_index({}, scores, rejected=rejected, run={}, target={},
                               calibration=score.calibration({}, scores), now_iso="2026-10-08T00:00:00Z")
    assert len(index["rejected"]) == report.MAX_REJECTED_SHOWN
    assert index["rejected"][0]["last_seen"] >= index["rejected"][-1]["last_seen"]
    assert index["counts"]["rejected"] == report.MAX_REJECTED_SHOWN + 10


def test_a_case_scored_through_its_cluster_says_whose_evidence_it_is():
    # Measured 2026-10-08: a sub-account with no evidence of its own ranked 6th on its
    # family's dormancy handoff, and its headline read "Opened by tier:PROBABLE".
    group = {"operator_group": {"master": A, "subaccounts": [B]}}
    cases = {A: case(A, {"dormancy_handoff": entry("dormancy_handoff", strength=0.49,
                                                   summary="First active 2 day(s) into a 10-day silence")},
                     links=group),
             B: case(B, {}, links={**group, "subaccount_of": A})}
    rows = {r["address"]: r for r in build(cases)["cases"]}
    assert rows[B]["headline"].startswith(
        f"One operator with {A[:10]}... (2 accounts): First active 2 day(s) into a 10-day silence")
    assert rows[A]["headline"].startswith("First active 2 day(s) into a 10-day silence")


def test_current_cluster_evidence_explains_a_rank_before_own_history():
    group = {"operator_group": {"master": A, "subaccounts": [B]}}
    cases = {A: case(A, {"amount_correlation": entry("amount_correlation", "historical", strength=0.7,
                                                     summary="old match")}, links=group),
             B: case(B, {"dormancy_handoff": entry("dormancy_handoff", strength=0.49,
                                                   summary="born in a silence")},
                     links={**group, "subaccount_of": A})}
    rows = {r["address"]: r for r in build(cases)["cases"]}
    assert rows[A]["headline"].startswith(f"One operator with {B[:10]}... (2 accounts): born in a silence")
    assert rows[B]["headline"].startswith("born in a silence")


def test_a_row_carries_its_reason_apart_from_the_hyperliquid_line():
    # The phone card and the dashboard show the Hyperliquid line in their own place;
    # the headline repeated it. `why` is the reason alone (2026-10-08 render check).
    c = case(B, {"private_deposit_address": entry("private_deposit_address", summary="Paid his deposit")},
             hl={"on_hl": True, "total_value": 6.0, "month_volume": 0.0, "last_ok_at": "x"})
    row = build({B: c})["cases"][0]
    assert row["why"] == "Paid his deposit"
    assert row["headline"] == "Paid his deposit - $6 on Hyperliquid, no trades in 30 days"


def test_an_invalidated_item_is_counted_as_such():
    c = case(B, {"quiet_first_funder": {**entry("quiet_first_funder", "historical", summary="funder"),
                                        "invalid_reason": "global activity: 2,282,986 txs"}})
    row = build({B: c})["cases"][0]
    assert row["statuses"] == {"invalidated": 1} and row["lapsed_only"] is True
    # nothing it rests on still counts, and it says what stopped counting and why
    assert row["headline"].startswith("funder, which no longer counts: global activity")


def test_a_case_whose_only_evidence_was_invalidated_says_so_not_the_rosters_claim():
    # 0x498216a21f on the live replay (2026-10-08): its one item, a "quiet" first funder,
    # is a 2.28M-transaction exchange hot wallet; the roster's words still claim the link.
    item = entry("quiet_first_funder", summary="Shares a first funder with the target",
                 funder="0x" + "f9" * 20)
    item["invalid_reason"] = "global activity: 2,282,992 txs, 1,031 token transfers"
    c = case(B, {"quiet_first_funder": item},
             roster={"tier": "POSSIBLE", "reasons": ["Shares the target's original funding source"]})
    row = build({B: c})["cases"][0]
    assert row["why"] == ("Shares a first funder with the target, which no longer counts: "
                          "global activity: 2,282,992 txs, 1,031 token transfers")


def test_a_family_carried_only_by_a_cluster_member_says_cluster():
    # B's own funder item no longer counts; the family's value is its sub-account's.
    mine = entry("quiet_first_funder", summary="funder", funder="0x" + "f9" * 20)
    mine["invalid_reason"] = "global activity: 2,282,986 txs"
    group = {"operator_group": {"master": B, "subaccounts": [C]}}
    cases = {B: case(B, {"quiet_first_funder": mine}, links=group),
             C: case(C, {"quiet_first_funder": entry("quiet_first_funder", funder="0x" + "e8" * 20)},
                     links=group)}
    row = next(r for r in build(cases)["cases"] if r["address"] == B)
    assert row["families"]["infrastructure"][3] == "cluster"


def test_next_checks_name_evidence_that_no_longer_counts():
    item = {**entry("quiet_first_funder", "historical", summary="funder"), "invalid_reason": "busy"}
    assert any("no longer counts" in check for check in report.next_checks(case(B, {"quiet_first_funder": item})))


def test_every_way_evidence_ends_leaves_the_case_resting_on_its_record():
    for status, words in (("lapsed", "has lapsed"), ("refuted", "was refuted"), ("historical", "is history")):
        c = case(B, {"amount_correlation": entry("amount_correlation", status, strength=0.9)})
        assert any(words in check and "rests on its record" in check for check in report.next_checks(c)), status
    both = case(B, {"amount_correlation": entry("amount_correlation", "lapsed", strength=0.9),
                    "two_way_flow": entry("two_way_flow")})
    assert not any("rests on its record" in check for check in report.next_checks(both))


def test_a_case_resting_only_on_that_days_money_never_repeats_its_void_figure():
    item = {**entry("direct_transfer", "historical",
                    summary="Moved money with the target (received $0, sent $1,030,689,919)"),
            "invalid_reason": cases.PRE_FIX_REASON}
    why = build({B: case(B, {"direct_transfer": item})})["cases"][0]["why"]
    assert "1,030,689,919" not in why and "no longer counts" in why
