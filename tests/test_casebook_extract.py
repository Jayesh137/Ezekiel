"""A roster row becomes casebook items the same way today and for any old roster (spec §6)."""

from src.casebook import extract

T = "0x45d26f28196d226497130c4bac709d808fed4029"
SELF = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
A = "0x" + "a" * 40
B = "0x" + "b" * 40
CONFIG = {"target_wallet": T, "known_self_wallets": [SELF]}


def kinds(row, config=CONFIG):
    items, _ = extract.extract_items(row, config)
    return {i["kind"]: i for i in items}


def test_a_direct_transfer_and_two_way_flow_from_totals():
    got = kinds({"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"],
                 "evidence": {"totals": {"received_from_target_usd": 1_000_000.0,
                                         "sent_to_target_usd": 5_000.0}, "depth": 1}})
    assert got["direct_transfer"]["strength"] == 1_000_000.0
    assert "$1,000,000" in got["direct_transfer"]["summary"]
    assert got["two_way_flow"]["strength"] == 5_000.0


def test_dust_both_ways_is_not_a_relationship():
    got = kinds({"wallet": A, "vectors": [], "evidence": {
        "totals": {"received_from_target_usd": 3.0, "sent_to_target_usd": 2.0}}})
    assert "two_way_flow" not in got and "direct_transfer" not in got


def test_a_vector_held_only_through_the_group_is_not_extracted_again():
    got = kinds({"wallet": A, "vectors": ["transfer"], "evidence": {
        "vectors_via_group": ["transfer"], "operator_group": {"master": B, "subaccounts": [A]}}})
    assert "direct_transfer" not in got and "operator_group" in got


def test_explicit_links_count_only_with_his_wallets():
    got = kinds({"wallet": A, "vectors": ["explicit_link"], "evidence": {
        "explicit_links": [{"kind": "subaccount", "with": SELF}, {"kind": "agent", "with": B}]}})
    assert got["subaccount_of_cluster"]["facts"]["links"] == [{"kind": "subaccount", "with": SELF}]
    assert "agent_of_cluster" not in got
    assert "subaccount_of_cluster" in kinds({"wallet": A, "evidence": {"subaccount_of": SELF}})


def test_first_funded_by_the_target_is_money_and_a_quiet_funder_is_infrastructure():
    assert "funded_by_target" in kinds({"wallet": A, "vectors": ["linkage"],
                                        "evidence": {"shared_first_funder": T}})
    got = kinds({"wallet": A, "vectors": ["linkage"], "evidence": {"shared_first_funder": B}})
    assert got["quiet_first_funder"]["facts"]["funder"] == B
    assert "linkage_graph" not in got and "funded_by_target" not in got


def test_a_shared_funder_summary_claims_no_measurement():
    # Before 2026-09-16 the roster reported a shared funder nobody had measured (five
    # wallets shared a 2.28M-transaction hot wallet), so the item says what was observed;
    # whether the funder is quiet is re-judged apart, as invalid_reason.
    got = kinds({"wallet": A, "vectors": ["linkage"], "evidence": {"shared_first_funder": B}})
    assert got["quiet_first_funder"]["summary"] == f"Shares his first funder {B[:10]}..."


def test_an_unexplained_linkage_vote_still_counts_once():
    got = kinds({"wallet": A, "vectors": ["linkage"],
                 "reasons": ["Shares a deposit address with the target"]})
    assert set(got) == {"linkage_graph"}
    assert got["linkage_graph"]["summary"] == "Shares a deposit address with the target"


def test_private_deposit_address_and_boundary_votes():
    got = kinds({"wallet": A, "vectors": ["linkage", "transfer"], "evidence": {
        "shared_private_deposit_address": {"sentinel": B, "usd": 249993.84, "count": 1,
                                           "first_ts": 1722384000, "chains": ["ethereum"]},
        "boundary": [{"kind": "outside_account_paid_his_world", "vote": "transfer", "amount_usd": 50_000.0},
                     {"kind": "perimeter_member_active_on_hl", "vote": None, "usd": 10.0},
                     {"kind": "outside_account_paid_his_world", "vote": "linkage", "amount_usd": 9_000.0}]}})
    assert got["private_deposit_address"]["strength"] == 249993.84
    assert "2024-07-31" in got["private_deposit_address"]["summary"]
    assert got["boundary_transfer"]["strength"] == 50_000.0
    assert "boundary_member" in got and "boundary_deposit_address" in got
    # The transfer vote is explained by the protocol record and there is no flow
    # with the target itself, so no second money item is invented for it.
    assert "direct_transfer" not in got


def test_strength_scaled_kinds_and_the_study():
    got = kinds({"wallet": A, "vectors": ["correlation", "execution_program"], "evidence": {
        "correlation_confidence": 0.9974, "correlation_gap_hours": 30.5, "competing_deposits": 0,
        "dormancy_handoff": {"score": 0.4286, "gap_length": 6, "delay_days": 2,
                             "candidate_first_day": 20682},
        "study": {"as_of": "2026-10-07T00:00:00+00:00", "coverage_days": 12,
                  "families": {"tooling": {"verdict": "against", "lr": 0.04,
                                           "by": ["T1:against"], "basis": "family"}}},
        "execution_program": {"clip_match_ratio": 0.8, "clips_matched": 4, "clips_compared": 5}}})
    assert got["amount_correlation"]["strength"] == 0.9974
    assert got["dormancy_handoff"]["strength"] == 0.4286
    assert "2 day(s) into a 6-day silence" in got["dormancy_handoff"]["summary"]
    assert got["study_tooling"]["facts"]["verdict"] == "against"
    assert got["study_tooling"]["strength"] == 0.04
    assert got["execution_program"]["strength"] == 0.8


def test_a_neutral_study_refutes_an_earlier_tooling_verdict():
    items, refutes = extract.extract_items({"wallet": A, "evidence": {"study": {
        "families": {"tooling": {"verdict": "neutral", "lr": 1.3}}}}}, CONFIG)
    assert [i["kind"] for i in items] == ["study_context"] and refutes == {"study_tooling"}
    _, refutes = extract.extract_items({"wallet": A, "evidence": {"study": {
        "families": {"tooling": {"verdict": "uncalibrated", "lr": None}}}}}, CONFIG)
    assert refutes == set()


def test_behaviour_vote_score_and_veto():
    got = kinds({"wallet": A, "vectors": [], "evidence": {
        "behavioural_score": 0.61, "style_vetoes": ["Decision frequency 258x apart"]}})
    assert "behavioural_score" in got and "behavioural_vote" not in got and "style_veto" in got
    assert "behavioural_vote" in kinds({"wallet": A, "vectors": ["behavioural"],
                                        "evidence": {"behavioural_score": 0.7}})


def test_referral_with_his_wallet_and_pairs_with_others():
    got = kinds({"wallet": A, "vectors": ["referral"], "evidence": {
        "referral_links": [{"kind": "referral", "with": SELF}],
        "referral_pairs": [{"with": B, "role": "referred", "code_accounts": 3}]}})
    assert got["referral_with_cluster"]["facts"]["links"] == [{"kind": "referral", "with": SELF}]
    assert got["referral_pair"]["facts"]["pairs"][0]["with"] == B


def test_an_operator_ruling_is_an_item():
    config = {**CONFIG, "casebook_rulings": {A.upper().replace("0X", "0x"): {
        "verdict": "not_him", "note": "market maker", "date": "2026-10-08"}}}
    got = kinds({"wallet": A}, config)
    assert got["operator_not_him"]["facts"]["note"] == "market maker"


def test_missing_readings_make_no_items():
    assert kinds({"wallet": A, "evidence": {
        "correlation_confidence": None, "dormancy_handoff": {"score": None},
        "behavioural_score": "n/a", "portfolio_overlap": float("nan"),
        "shared_agents": [], "circle_flows": None, "boundary": "broken"}}) == {}


def test_old_roster_shapes_extract_cleanly():
    # 2026-09-10: no peak_tier, no vectors_via_group, no study; vectors may be None.
    assert kinds({"wallet": A, "tier": "POSSIBLE", "vectors": None, "evidence": None}) == {}
    got = kinds({"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer", "correlation"],
                 "evidence": {"correlation_confidence": 0.7, "totals": None, "depth": 2}})
    assert set(got) == {"direct_transfer", "amount_correlation"}
    assert got["direct_transfer"]["strength"] is None


def test_facts_are_bounded_for_keeping_forever():
    row = {"wallet": A, "evidence": {"circle_flows": [
        {"amount_usd": float(i), "tx_hash": "0x" + "1" * 300} for i in range(50)]}}
    item = kinds(row)["circle_flow"]
    assert item["facts"]["count"] == 50 and len(item["facts"]["flows"]) == 5
    assert all(len(f["tx_hash"]) <= 200 for f in item["facts"]["flows"])
    assert item["strength"] == 49.0


def test_generated_text_is_ascii():
    got = kinds({"wallet": A, "vectors": ["linkage", "transfer"], "evidence": {
        "shared_private_deposit_address": {"sentinel": B, "usd": 5.0},
        "totals": {"received_from_target_usd": 2_000.0}}})
    for item in got.values():
        item["summary"].encode("ascii")


def test_admission():
    assert extract.classify_row({"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]},
                                CONFIG)["status"] == "admitted"
    assert extract.classify_row({"wallet": A, "tier": "WATCH", "peak_tier": "PROBABLE"},
                                CONFIG)["status"] == "admitted"
    noise = {"wallet": A, "tier": "WATCH", "evidence": {
        "graph_reach_only": True, "behavioural_score": 0.3, "style_vetoes": ["x"]}}
    assert extract.classify_row(noise, CONFIG)["status"] == "ignored"
    handoff = {"wallet": A, "tier": "WATCH", "evidence": {"dormancy_handoff": {"score": 0.5}}}
    assert extract.classify_row(handoff, CONFIG)["why"] == ["dormancy_handoff"]
    watch = {**CONFIG, "watch_wallets": [{"address": A}]}
    assert extract.classify_row({"wallet": A, "tier": "WATCH"}, watch)["why"] == ["config:watch"]
    assert extract.classify_row({"wallet": T, "tier": "CONFIRMED"}, CONFIG)["status"] == "target"
    assert extract.classify_row({"wallet": "0xnope"}, CONFIG)["status"] == "invalid"


def test_rejection_records_only_rows_that_had_something():
    token = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    got = extract.classify_row({"wallet": token, "tier": "POSSIBLE", "vectors": ["transfer"]}, CONFIG)
    assert got["status"] == "rejected" and got["reason"].startswith("not a wallet: token contract")
    busy = {"wallet": B, "tier": "INFRASTRUCTURE", "is_service": True, "vectors": ["linkage"],
            "evidence": {"service_reason": "global activity: 2,282,986 txs"}}
    got = extract.classify_row(busy, CONFIG)
    assert got["status"] == "rejected" and "2,282,986" in got["reason"]
    got = extract.classify_row({"wallet": token, "tier": "WATCH"}, CONFIG)
    assert got["status"] == "ignored" and got["reason"]


def test_known_wallets_are_immune_to_the_service_grade():
    got = extract.classify_row({"wallet": SELF, "tier": "CONFIRMED", "is_service": True,
                                "vectors": ["transfer"]}, CONFIG)
    assert got["status"] == "admitted" and "config:known_self" in got["why"]


def test_a_transfer_vote_with_no_valued_flow_with_the_target_says_so():
    # Measured 2026-10-08: rows voting `transfer` through his config wallets carry
    # zero totals with the target; "received $0, sent $0" misread them, and $0 is
    # not the amount of an unpriced movement (rule 6).
    got = kinds({"wallet": A, "vectors": ["transfer"], "reasons": ["Sent funds to the target wallet"],
                 "evidence": {"totals": {"received_from_target_usd": 0, "sent_to_target_usd": 0,
                                         "edge_count": 3, "unvalued_edge_count": 1}}})
    item = got["direct_transfer"]
    assert item["strength"] is None
    assert item["summary"] == "Observed transfer with the target (3 movements, 1 unpriced)"
    got = kinds({"wallet": A, "vectors": ["transfer"], "evidence": {"totals": {
        "received_from_target_usd": 0, "sent_to_target_usd": 0, "edge_count": 2,
        "unvalued_edge_count": 0}}})
    assert got["direct_transfer"]["summary"] == "Observed transfer with one of his wallets (2 movements)"


def test_a_forgery_of_his_wallet_is_rejected_whatever_the_roster_says():
    forgery = "0xf078170f3993bcbd76c3234724314ae8ba59f19e"      # 0xf078...f19e, like SELF
    out = extract.classify_row({"wallet": forgery, "tier": "POSSIBLE", "vectors": ["linkage", "transfer"]},
                               CONFIG)
    assert out["status"] == "rejected"
    assert out["reason"].startswith("forgery of his wallet 0xf078969e")


def test_a_lookalike_the_roster_saw_move_real_money_with_him_is_admitted():
    forgery = "0xf078170f3993bcbd76c3234724314ae8ba59f19e"
    row = {"wallet": forgery, "tier": "POSSIBLE", "vectors": ["transfer"],
           "evidence": {"totals": {"received_from_target_usd": 25_000.0, "sent_to_target_usd": 0}}}
    assert extract.classify_row(row, CONFIG)["status"] == "admitted"
