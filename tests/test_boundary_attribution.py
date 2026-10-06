"""Spec §6.2, one test per row of the rules table."""

from src.boundary import attribution as at
from src.boundary import perimeter

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SINK, FUND, ASSOC = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40
SOL = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"
OUT, OTHER, HUB = "0x" + "1" * 40, "0x" + "6" * 40, "0x" + "3" * 40

INDEX = perimeter.Index(perimeter.build(
    config={"target_wallet": T, "known_self_wallets": [TR]},
    sentinels={S: {"reason": "conduit"}},
    trace_report={"funders": [{"address": FUND, "class": "quiet_eoa", "paid_him_usd": 6e6}]},
    trace_registry={SINK: {"class": "quiet_eoa", "his_money": {"in_usd": 5e5, "share": 1.0}}},
    solana={SOL: {"role": "cluster", "mint_recipient_hex": "0x" + "ab" * 32}},
    associates_found={ASSOC: {"paid_him_usd": 2e6, "he_paid_usd": 2e6}},
    families={}, services=set(), previous=None, now_iso="2026-10-06T00:00:00+00:00"))


def ev(direction, account, counterparty, usd=50_000.0, source="bridge2", **kw):
    return at.event(source=source, direction=direction, hl_account=account,
                    counterparty=counterparty, chain="arbitrum", amount_usd=usd, ts=1, ref="0xr",
                    **kw)


def test_an_outside_account_paying_his_wallet_is_critical_transfer():
    f = at.classify(ev("out", OUT, T), INDEX)
    assert (f["kind"], f["severity"], f["vote"], f["role"]) == \
        (at.KIND_PAID_HIS_WORLD, "CRITICAL", "transfer", "core")


def test_an_outside_account_paying_his_deposit_address_is_critical_linkage():
    f = at.classify(ev("out", OUT, S), INDEX)
    assert (f["severity"], f["vote"], f["role"]) == ("CRITICAL", "linkage", "deposit")


def test_paying_a_sink_or_funder_is_high_evidence_only():
    for member in (SINK, FUND):
        f = at.classify(ev("out", OUT, member), INDEX)
        assert (f["severity"], f["vote"]) == ("HIGH", None)


def test_an_associate_is_recorded_never_alerted():
    f = at.classify(ev("out", OUT, ASSOC), INDEX)
    assert f is not None and f["severity"] is None and f["vote"] is None


def test_his_world_funding_an_outside_account_is_critical_transfer():
    f = at.classify(ev("in", OUT, T, source="hl_send"), INDEX)
    assert (f["kind"], f["severity"], f["vote"]) == (at.KIND_FUNDED_FROM_HIS_WORLD, "CRITICAL", "transfer")
    f = at.classify(ev("in", OUT, FUND, source="circle"), INDEX)
    assert (f["severity"], f["vote"]) == ("HIGH", None)


def test_his_account_paying_an_address_outside_his_world_is_critical():
    f = at.classify(ev("out", TR, OTHER), INDEX)
    assert (f["kind"], f["severity"], f["vote"]) == (at.KIND_HIS_ACCOUNT_PAID_OUTSIDE, "CRITICAL", None)


def test_his_account_paying_his_own_world_or_itself_is_nothing():
    assert at.classify(ev("out", T, S), INDEX) is None
    assert at.classify(ev("out", T, T), INDEX) is None
    assert at.classify(ev("in", T, OTHER), INDEX) is None


def test_a_withdrawal_to_itself_carries_no_relationship_unless_it_is_a_member():
    assert at.classify(ev("out", OUT, OUT), INDEX) is None
    f = at.classify(ev("in", SINK, SINK), INDEX)
    assert (f["kind"], f["severity"]) == (at.KIND_MEMBER_ACTIVE, "HIGH")
    f = at.classify(ev("out", ASSOC, ASSOC), INDEX)
    assert f["kind"] == at.KIND_MEMBER_ACTIVE and f["severity"] is None


def test_dust_never_counts_and_unvalued_never_pages_critical():
    assert at.classify(ev("out", OUT, T, usd=40.0), INDEX) is None
    f = at.classify(ev("out", OUT, SOL, usd=None, source="unit"), INDEX)
    assert (f["severity"], f["vote"], f["role"]) == ("HIGH", "transfer", "identity")


def test_raw_forms_match_his_solana_identity():
    f = at.classify(ev("out", OUT, None, counterparty_raw="0x" + "ab" * 32, source="circle"), INDEX)
    assert f["role"] == "identity" and f["severity"] == "CRITICAL"


def test_history_is_announced_at_high_but_keeps_its_vote():
    f = at.classify(ev("out", OUT, T, retro=True), INDEX)
    assert (f["severity"], f["vote"]) == ("HIGH", "transfer")


def test_hl_edges_become_events_for_the_other_account_and_skip_hubs():
    edges = [{"src": T, "dst": OTHER, "amount_usd": 5e5, "ts": 9, "tx_hash": "0xa",
              "id": "e1", "kind": "send"},
             {"src": OTHER, "dst": T, "amount_usd": 2e3, "ts": 10, "tx_hash": "0xb",
              "id": "e2", "kind": "send"},
             {"src": HUB, "dst": T, "amount_usd": 6e3, "ts": 11, "tx_hash": "0xc",
              "id": "e3", "kind": "spotTransfer"},
             {"src": T, "dst": TR, "amount_usd": 9e6, "ts": 12, "tx_hash": "0xd",
              "id": "e4", "kind": "internalTransfer"},
             {"src": "0x2000000000000000000000000000000000000000", "dst": T,
              "amount_usd": 9e6, "ts": 13, "tx_hash": "0xe", "id": "e5", "kind": "send"}]
    evs = at.from_hl_edges(edges, {T, TR}, exclude={HUB})
    assert [(e["direction"], e["hl_account"], e["counterparty"]) for e in evs] == \
        [("in", OTHER, T), ("out", OTHER, T)]
    found = at.classify_all(evs, INDEX)
    assert {f["kind"] for f in found} == {at.KIND_FUNDED_FROM_HIS_WORLD, at.KIND_PAID_HIS_WORLD}


def test_bridge2_rows_become_events():
    w = {"user": OUT, "destination": S, "usd": 1e6, "ts": 5, "tx_hash": "0xt", "log_index": 3,
         "nonce": 7}
    e = at.from_bridge2_withdrawal(w)
    assert (e["direction"], e["hl_account"], e["counterparty"], e["nonce"]) == ("out", OUT, S, 7)
    d = at.from_bridge2_deposit({"depositor": SINK, "usd": 2e5, "ts": 6, "tx_hash": "0xu",
                                 "log_index": 1})
    assert (d["direction"], d["hl_account"], d["counterparty"]) == ("in", SINK, SINK)
    assert at.classify(d, INDEX)["kind"] == at.KIND_MEMBER_ACTIVE


def test_finding_keys_are_stable_and_distinct():
    a = at.classify(ev("out", OUT, T), INDEX)
    b = at.classify(ev("out", OTHER, T), INDEX)
    assert a["key"] == at.finding_key(a) and a["key"] != b["key"]
    assert set(at.TITLES) == {at.KIND_PAID_HIS_WORLD, at.KIND_FUNDED_FROM_HIS_WORLD,
                              at.KIND_HIS_ACCOUNT_PAID_OUTSIDE, at.KIND_MEMBER_ACTIVE}


FORWARDER = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"
VAULT = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"


def test_vault_moves_and_circles_forwarder_are_not_accounts_paying_him():
    # Dry run 2026-10-06: 27 of 30 findings were the forwarder delivering HIS
    # Circle deposits (a false CRITICAL on every future one), and a vault
    # withdrawal is a vault, not an account — excluded here, not only when the
    # trace registry happens to call the vault a hub.
    edges = [{"src": FORWARDER, "dst": T, "amount_usd": 3e6, "ts": 9, "tx_hash": "0xa",
              "id": "e1", "kind": "send"},
             {"src": VAULT, "dst": T, "amount_usd": 5e6, "ts": 10, "tx_hash": "0xb",
              "id": "e2", "kind": "vaultWithdraw"},
             {"src": T, "dst": VAULT, "amount_usd": 5e6, "ts": 11, "tx_hash": "0xc",
              "id": "e3", "kind": "vaultDeposit"}]
    assert at.from_hl_edges(edges, {T, TR}) == []


def test_an_unpriced_token_sent_into_his_world_is_not_a_payment():
    # MAX and LATINA airdropped to the target and the treasury seconds apart.
    # Anyone can send any token; with no price it proves no payment. A token
    # HE sends out is his own act and still counts.
    assert at.classify(ev("out", OTHER, T, usd=None, source="hl_send"), INDEX) is None
    mine = at.classify(ev("in", OTHER, T, usd=None, source="hl_send"), INDEX)
    assert mine["kind"] == at.KIND_FUNDED_FROM_HIS_WORLD and mine["severity"] == "HIGH"


def test_his_deposit_address_receiving_his_money_is_not_attribution():
    # 0xf078969e paying his own HyperCore deposit address 0x4aecac3b: that is
    # what makes it his deposit address, not an outside account.
    assert at.classify(ev("in", S, T, source="hl_send"), INDEX) is None
    assert at.classify(ev("out", S, T, source="hl_send"), INDEX) is None


def test_a_sink_or_associate_moving_money_with_him_on_hl_is_that_member_active():
    # Spec §6.2: a non-core member active on its own HL account — HIGH from
    # weight 0.6, recorded for an associate, never a vote.
    sink = at.classify(ev("out", SINK, T, source="hl_send"), INDEX)
    assert (sink["kind"], sink["severity"], sink["vote"]) == (at.KIND_MEMBER_ACTIVE, "HIGH", None)
    assoc = at.classify(ev("in", ASSOC, T, source="hl_send"), INDEX)
    assert (assoc["kind"], assoc["severity"], assoc["vote"]) == (at.KIND_MEMBER_ACTIVE, None, None)
