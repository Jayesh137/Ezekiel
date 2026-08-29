import json
from pathlib import Path

from src.chain import spam

SELF_WALLET = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
HL_BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
FIXTURE = Path(__file__).parent / "fixtures" / "poisoning_live.json"


def record(src, dst, usd=100.0, basis="stable_par", amount=100.0):
    return {"src": src, "dst": dst, "amount_usd": usd, "amount": amount,
            "value_basis": basis, "asset": "USDC"}


def test_every_live_poisoning_address_is_caught_by_the_four_four_rule():
    real = {SELF_WALLET, HL_BRIDGE}
    entries = json.loads(FIXTURE.read_text())
    for entry in entries:
        assert spam.is_lookalike(entry["address"], real) in real, entry["address"]


def test_a_genuine_counterparty_is_not_a_lookalike():
    real = {SELF_WALLET, HL_BRIDGE}
    assert spam.is_lookalike("0xa95d9c1f655341597c94393fddc30cf3c08e4fce", real) is None


def test_an_address_is_never_a_lookalike_of_itself():
    assert spam.is_lookalike(SELF_WALLET, {SELF_WALLET}) is None


def test_real_counterparties_need_a_priced_non_dust_transfer():
    wallet = "0xtarget"
    records = [
        record(wallet, "0xbig", usd=5000.0),
        record(wallet, "0xdusty", usd=0.4),
        record(wallet, "0xzero", usd=0.0),
        record(wallet, "0xunpriced", usd=None, basis="unpriced"),
    ]
    assert spam.derive_real_counterparties(records, wallet) == {"0xbig"}


def test_lookalike_is_evaluated_before_dust():
    """Which address is being mimicked is intelligence: attackers mimic
    addresses that received large sums."""
    real = {SELF_WALLET}
    poison = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    reason = spam.classify_spam(record("0xtarget", poison, usd=0.0, amount=0.0), real)
    assert reason == "lookalike"


def test_zero_value_transfer_is_quarantined():
    reason = spam.classify_spam(record("0xtarget", "0xsomebody", usd=0.0, amount=0.0), set())
    assert reason == "zero_value"


def test_sub_dust_transfer_is_quarantined():
    reason = spam.classify_spam(record("0xtarget", "0xsomebody", usd=0.4), set())
    assert reason == "dust"


def test_unpriced_token_is_quarantined():
    r = record("0xtarget", "0xsomebody", usd=None, basis="unpriced", amount=1e9)
    r["asset"] = "SCAMAIRDROP"
    assert spam.classify_spam(r, set()) == "unpriced_token"


def test_a_real_transfer_is_not_spam():
    assert spam.classify_spam(record("0xtarget", "0xbig", usd=13_000_000.0), set()) is None


def test_rollup_aggregates_by_address_and_keeps_the_mimic_target():
    records = [
        {"src": "0xtarget", "dst": "0xpoison", "spam": True, "spam_reason": "lookalike",
         "mimics": SELF_WALLET, "forged": "0xpoison", "ts": 100,
         "asset": "USDC", "token_address": "0xaf88"},
        {"src": "0xpoison", "dst": "0xtarget", "spam": True, "spam_reason": "lookalike",
         "mimics": SELF_WALLET, "forged": "0xpoison", "ts": 300,
         "asset": "USDC", "token_address": "0xaf88"},
        {"src": "0xtarget", "dst": "0xok", "spam": False, "spam_reason": None, "ts": 200},
    ]
    rolled = spam.rollup(records)
    assert len(rolled) == 1
    entry = rolled[0]
    assert entry["address"] == "0xpoison"
    assert entry["count"] == 2
    assert entry["mimics"] == SELF_WALLET
    assert entry["first_seen"] == 100
    assert entry["last_seen"] == 300


def test_rollup_keeps_the_token_of_an_unpriced_entry_so_it_can_be_registered():
    records = [{"src": "0xtarget", "dst": "0xnew", "spam": True,
                "spam_reason": "unpriced_token", "ts": 5,
                "asset": "REALTOKEN", "token_address": "0xdeadbeef"}]
    entry = spam.rollup(records)[0]
    assert entry["asset"] == "REALTOKEN"
    assert entry["token_address"] == "0xdeadbeef"
