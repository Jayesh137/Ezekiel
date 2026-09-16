"""What Hyperliquid declares about an account, read into the roster.

Three rules, each from a measured case on 2026-09-16:

  * A sub-account exists only because its master created it, so the two are one
    operator. Between a cluster wallet and anyone else that CONFIRMs alone, and
    among candidates it means a vector found on one account is a vector found
    on the operator — `0x7fdafde5…` had five sub-accounts, one holding $1.26M,
    that no detector had ever read.
  * A referral is chosen by the account typing the code: one vote, and only on
    a quiet code with the cluster.
  * A referral between two strangers on a code nobody else uses is a private
    link to a Hyperliquid address nothing reads, so the other side gets a row —
    evidence only, so every per-wallet detector starts asking about it.
"""

import json

import src.roster as roster

TARGET = "0x" + "11" * 20
SELF = "0x" + "12" * 20
MASTER = "0x" + "22" * 20
SUB = "0x" + "33" * 20
EMPTY_SUB = "0x" + "34" * 20
REFERRER = "0x" + "44" * 20
EXCHANGE = "0x" + "55" * 20
CONFIG = {"target_wallet": TARGET, "known_self_wallets": [SELF]}


def _setup(tmp_path, monkeypatch, surface=None, **files):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "load_roster", lambda: {})
    profile = tmp_path.parent / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "backtest.json").write_text(json.dumps({"passed": False}))
    for name, doc in files.items():
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "latest.json").write_text(json.dumps(doc))
    if surface is not None:
        (tmp_path / "hl_surface").mkdir(parents=True, exist_ok=True)
        (tmp_path / "hl_surface" / "latest.json").write_text(json.dumps(
            {"subaccounts": {}, "referrals": {}, "vault_deposits": {}, "links": [],
             **surface}))


def _rows(config=CONFIG):
    return {r["wallet"]: r for r in roster.build_roster(config)["wallets"]}


def _sub(master, value):
    return {"master": master, "name": "x", "account_value": value}


def test_a_subaccount_of_the_target_is_confirmed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, surface={
        "subaccounts": {SUB: _sub(TARGET, 0.0)},
        "links": [{"kind": "subaccount", "address": SUB, "linked_to": TARGET}]})
    row = _rows()[SUB]
    assert row["tier"] == roster.TIER_CONFIRMED
    assert roster.VECTOR_EXPLICIT in row["vectors"]
    assert TARGET not in _rows()


def test_vectors_found_on_two_accounts_of_one_operator_agree(tmp_path, monkeypatch):
    """Correlation on the master and dormancy on its sub-account are two
    independent detectors describing one person — two vectors, not one each."""
    _setup(tmp_path, monkeypatch,
           surface={"subaccounts": {SUB: _sub(MASTER, 50_000.0)}},
           correlations={"matches": [{"wallet": MASTER, "confidence": 0.7}]},
           dormancy={"handoffs": {SUB: {"score": 0.43}}})
    rows = _rows()
    for wallet in (MASTER, SUB):
        assert rows[wallet]["tier"] == roster.TIER_PROBABLE
        assert set(rows[wallet]["vectors"]) == {roster.VECTOR_CORRELATION,
                                                roster.VECTOR_DORMANCY}
        assert rows[wallet]["evidence"]["operator_group"]["master"] == MASTER
    assert rows[MASTER]["evidence"]["vectors_via_group"] == [roster.VECTOR_DORMANCY]
    assert rows[SUB]["evidence"]["vectors_via_group"] == [roster.VECTOR_CORRELATION]


def test_a_service_neither_lends_nor_borrows_group_vectors(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           surface={"subaccounts": {SUB: _sub(EXCHANGE, 50_000.0)}},
           transfer_graph={"nodes": [{"wallet": EXCHANGE, "confidence": 0.9,
                                      "evidence": {"is_service": True,
                                                   "transfer_count": 9}}]},
           dormancy={"handoffs": {SUB: {"score": 0.43}}})
    rows = _rows()
    assert rows[EXCHANGE]["tier"] == roster.TIER_INFRASTRUCTURE
    assert rows[SUB]["vectors"] == [roster.VECTOR_DORMANCY]
    assert "operator_group" not in rows[SUB]["evidence"]


def test_an_empty_subaccount_of_a_weak_master_gets_no_row(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           surface={"subaccounts": {SUB: _sub(MASTER, 1500.0),
                                    EMPTY_SUB: _sub(MASTER, 0.0)}},
           correlations={"matches": [{"wallet": MASTER, "confidence": 0.7}]})
    rows = _rows()
    assert SUB in rows and EMPTY_SUB not in rows
    assert rows[SUB]["evidence"]["subaccount_of"] == MASTER


def test_every_subaccount_of_a_probable_operator_gets_a_row(tmp_path, monkeypatch):
    """An empty sub-account of a wallet we believe is his is where he would move."""
    _setup(tmp_path, monkeypatch,
           surface={"subaccounts": {EMPTY_SUB: _sub(MASTER, 0.0)}},
           correlations={"matches": [{"wallet": MASTER, "confidence": 0.7}]},
           dormancy={"handoffs": {MASTER: {"score": 0.43}}})
    rows = _rows()
    assert rows[MASTER]["tier"] == roster.TIER_PROBABLE
    assert rows[EMPTY_SUB]["tier"] == roster.TIER_PROBABLE
    assert rows[EMPTY_SUB]["evidence"]["subaccount_of"] == MASTER


def test_a_quiet_referral_with_the_cluster_is_one_vote(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, surface={
        "links": [{"kind": "referral", "address": MASTER, "linked_to": SELF,
                   "code_accounts": 1}]})
    rows = _rows()
    assert rows[MASTER]["vectors"] == [roster.VECTOR_REFERRAL]
    assert rows[MASTER]["tier"] == roster.TIER_POSSIBLE


def test_a_quiet_stranger_referral_surfaces_the_referrer_as_evidence(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, surface={
        "links": [{"kind": "referral_pair", "address": MASTER, "linked_to": REFERRER,
                   "code_accounts": 1}]})
    rows = _rows()
    assert rows[REFERRER]["vectors"] == [] and rows[REFERRER]["tier"] == roster.TIER_WATCH
    assert rows[REFERRER]["evidence"]["referral_pairs"] == [
        {"with": MASTER, "role": "referrer", "code_accounts": 1}]
    assert rows[MASTER]["evidence"]["referral_pairs"] == [
        {"with": REFERRER, "role": "referred", "code_accounts": 1}]


def test_a_public_code_adds_no_rows(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, surface={
        "links": [{"kind": "referral_pair", "address": MASTER, "linked_to": REFERRER,
                   "code_accounts": 5000}]})
    assert _rows() == {}


def test_the_roster_builds_without_the_file(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           correlations={"matches": [{"wallet": MASTER, "confidence": 0.7}]})
    assert _rows()[MASTER]["vectors"] == [roster.VECTOR_CORRELATION]
