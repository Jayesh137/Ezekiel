"""His world as one lookup table, built only from measured facts (spec §4)."""

from src.boundary import perimeter

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
F = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
HD = "0x4aecac3b90dd0ad50d274c19221874e5ba8a4d45"
HUB = "0x1f6093d33db935b2ebd81d23312da5f11759973e"
FUND = "0x68797748dd0151819841df908adb93891997b711"
BUSYF = "0x160f6ef9fcdde6ff3febc7a57edbfd476a8aab5b"
SINK, SMALL, MIXED = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40
SOL = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"
ASSOC, SVC = "0x" + "d" * 40, "0x" + "e" * 40
CONFIG = {"target_wallet": T, "known_self_wallets": [TR, F]}


def _build(**kw):
    base = dict(config=CONFIG, sentinels={}, trace_report={}, trace_registry={}, solana={},
                associates_found={}, families={}, services=set(), previous=None,
                now_iso="2026-10-06T00:00:00+00:00")
    base.update(kw)
    return perimeter.build(**base)


def rec(src, dst, usd):
    return {"src": src, "dst": dst, "amount_usd": usd}


def test_core_is_config_and_only_config():
    doc = _build()
    assert {a for a, m in doc["members"].items() if m["role"] == "core"} == {T, TR, F}
    assert doc["counts"] == {"core": 3}


def test_roles_come_from_the_detector_that_measured_them():
    doc = _build(
        sentinels={S: {"reason": "conduit"}},
        trace_report={"deposit_addresses": [{"address": HD, "kind": "hl_deposit", "hub": HUB}],
                      "funders": [{"address": FUND, "class": "quiet_eoa", "paid_him_usd": 6e6},
                                  {"address": BUSYF, "class": "unknown", "paid_him_usd": 9e7}]},
        trace_registry={SINK: {"class": "quiet_eoa", "his_money": {"in_usd": 2e5, "share": 0.9}},
                        SMALL: {"class": "quiet_eoa", "his_money": {"in_usd": 5e4, "share": 1.0}},
                        MIXED: {"class": "quiet_eoa", "his_money": {"in_usd": 5e6, "share": 0.1}}},
        solana={SOL: {"role": "cluster", "mint_recipient_hex": "0xABCD"}},
        associates_found={ASSOC: {"paid_him_usd": 2e6, "he_paid_usd": 3e6}})
    m = doc["members"]
    assert m[S]["role"] == "deposit" and m[HD]["role"] == "deposit" and m[S]["weight"] == 1.0
    assert m[FUND]["role"] == "funder" and m[FUND]["weight"] == 0.6
    assert BUSYF not in m                      # unmeasured is never quiet (rule 9)
    assert m[SINK]["role"] == "sink" and SMALL not in m and MIXED not in m
    assert m[SOL]["role"] == "identity" and m[SOL]["raw"] == "0xabcd"
    assert m[ASSOC]["role"] == "associate" and m[ASSOC]["weight"] == 0.3


def test_a_member_keeps_its_strongest_role_and_core_is_never_demoted():
    doc = _build(sentinels={T: {"reason": "x"}, S: {"reason": "y"}},
                 associates_found={S: {"paid_him_usd": 2e6, "he_paid_usd": 2e6}})
    assert doc["members"][T]["role"] == "core"
    assert doc["members"][S]["role"] == "deposit"
    assert doc["members"][S]["sources"] == ["deposit_sentinels", "substrate"]


def test_services_zero_and_system_addresses_never_join():
    zero, system = "0x" + "0" * 40, "0x2000000000000000000000000000000000000000"
    doc = _build(sentinels={zero: {}, system: {}, SVC: {}}, services={SVC})
    assert not ({zero, system, SVC} & set(doc["members"]))


def test_readings_and_first_seen_survive_a_rebuild():
    prev = {"members": {S: {"first_seen": "2026-01-01", "hl": {"checked_at": "x"}}}}
    doc = _build(sentinels={S: {"reason": "y"}}, previous=prev)
    assert doc["members"][S]["first_seen"] == "2026-01-01"
    assert doc["members"][S]["hl"] == {"checked_at": "x"}


def test_associates_need_large_flows_both_ways_with_a_person():
    P, ONEWAY, BIG, CON = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40, "0x" + "4" * 40
    recs = [rec(T, P, 2e6), rec(P, T, 1.5e6), rec(T, ONEWAY, 9e6), rec(T, BIG, 2e6),
            rec(BIG, T, 2e6), rec(T, CON, 2e6), rec(CON, T, 2e6),
            {"src": P, "dst": T, "amount_usd": None}, {**rec(P, T, 5e6), "spam": True}]
    out = perimeter.associates(recs, {T}, is_contract=lambda a: a == CON,
                               is_busy=lambda a: a == BIG, services=set())
    assert out == {P: {"paid_him_usd": 1.5e6, "he_paid_usd": 2e6}}


def test_exchange_families_follow_his_deposit_address_and_who_paid_him():
    H1, H2, H3, H4, PERSON = ("0x" + c * 40 for c in "56789")
    recs = [rec(S, H1, 5e6), rec(S, H2, 1e6), rec(S, PERSON, 50), rec(H3, T, 2e5), rec(H4, T, 10)]
    fam = perimeter.exchange_families(recs, {S}, {T}, is_hot=lambda a: a in {H1, H2, H3, H4})
    assert fam == {f"deposit:{S}": sorted([H1, H2]), "paid_him": [H3]}
    assert perimeter.family_index(fam)[H1] == [f"deposit:{S}"]


def test_index_finds_members_by_address_or_raw_form_and_knows_core():
    doc = _build(solana={SOL: {"role": "cluster", "mint_recipient_hex": "0xABCD"}},
                 families={"paid_him": ["0x" + "5" * 40]})
    idx = perimeter.Index(doc)
    assert idx.get(T.upper().replace("0X", "0x"))["role"] == "core"
    assert idx.get(None, raw="0xABCD")["address"] == SOL
    assert idx.get("0x" + "9" * 40) is None
    assert idx.core == {T, TR, F} and idx.families["0x" + "5" * 40] == ["paid_him"]


def test_core_only_is_a_working_perimeter_from_config_alone():
    idx = perimeter.Index(perimeter.core_only(CONFIG, "2026-10-06T00:00:00+00:00"))
    assert idx.core == {T, TR, F} and len(idx.members) == 3


def test_a_solana_member_is_found_by_its_own_key_as_well_as_its_token_account():
    # A CCTP burn FROM his Solana wallet names the signer's key as messageSender;
    # the stored raw form is the token account Circle minted TO. Both are his
    # (check_circle_flows.his_identities matches both).
    from src.circle_flows import base58_to_hex
    idx = perimeter.Index(_build(solana={SOL: {"role": "cluster",
                                               "mint_recipient_hex": "0xABCD"}}))
    assert idx.get(None, raw=base58_to_hex(SOL))["address"] == SOL
    assert idx.get(None, raw="0xABCD")["address"] == SOL
