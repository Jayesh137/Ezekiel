import json
from pathlib import Path

from src.chain import spam

SELF_WALLET = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
HL_BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
FIXTURE = Path(__file__).parent / "fixtures" / "poisoning_live.json"


def record(src, dst, usd=100.0, basis="stable_par", amount=100.0):
    return {"src": src, "dst": dst, "amount_usd": usd, "amount": amount,
            "value_basis": basis, "asset": "USDC"}


def test_counterparty_volume_sums_priced_usd():
    """counterparty_volume aggregates priced transfers by counterparty."""
    wallet = "0xtarget"
    records = [
        record(wallet, "0xA", usd=100.0),
        record("0xA", wallet, usd=50.0),
        record(wallet, "0xB", usd=1000.0),
        record(wallet, "0xC", usd=None),  # unpriced, not counted
    ]
    vol = spam.counterparty_volume(records, wallet)
    assert vol.get("0xa") == 150.0
    assert vol.get("0xb") == 1000.0
    assert "0xc" not in vol


def test_every_live_poisoning_address_is_caught_by_the_four_four_rule():
    """All 8 fixture addresses classify as lookalikes when genuine anchors
    have realistic volume. Tests the pattern matcher directly."""
    wallet = "0xtarget"
    genuine_records = [
        record(wallet, SELF_WALLET, usd=13_000_000.0),
        record(wallet, HL_BRIDGE, usd=5_000_000.0),
    ]
    vol = spam.counterparty_volume(genuine_records, wallet)
    entries = json.loads(FIXTURE.read_text())
    for entry in entries:
        is_fake_of = spam.is_lookalike(entry["address"], vol)
        assert is_fake_of in {SELF_WALLET.lower(), HL_BRIDGE.lower()}, (
            f"{entry['address']} should be lookalike of genuine address"
        )


def test_fixture_forgeries_classified_as_lookalike_through_classifier():
    """All 8 live observed forgeries classify as lookalike through
    classify_spam, not just the pattern matcher. This verifies the
    integration and catches regressions in classify_spam's logic."""
    wallet = "0xtarget"
    genuine_records = [
        record(wallet, SELF_WALLET, usd=13_000_000.0),
        record(wallet, HL_BRIDGE, usd=5_000_000.0),
    ]
    vol = spam.counterparty_volume(genuine_records, wallet)
    entries = json.loads(FIXTURE.read_text())
    for entry in entries:
        # Each fixture address, sent to the wallet, should classify as
        # lookalike through the full classifier pipeline.
        r = record("0xspammer", entry["address"], usd=0.1)
        reason = spam.classify_spam(r, vol)
        assert reason == "lookalike", (
            f"{entry['address']} should classify as lookalike, got {reason}"
        )


def test_a_genuine_counterparty_is_not_a_lookalike():
    vol = {SELF_WALLET.lower(): 1000.0, HL_BRIDGE.lower(): 500.0}
    assert spam.is_lookalike("0xa95d9c1f655341597c94393fddc30cf3c08e4fce", vol) is None


def test_an_address_is_never_a_lookalike_of_itself():
    vol = {SELF_WALLET.lower(): 100.0}
    assert spam.is_lookalike(SELF_WALLET, vol) is None


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
    wallet = "0xtarget"
    poison = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    # poison is a forgery of SELF_WALLET, which has moved $13M.
    vol = {SELF_WALLET.lower(): 13_000_000.0}
    reason = spam.classify_spam(record(wallet, poison, usd=0.0, amount=0.0), vol)
    assert reason == "lookalike"


def test_zero_value_transfer_is_quarantined():
    reason = spam.classify_spam(record("0xtarget", "0xsomebody", usd=0.0,
                                       amount=0.0), {})
    assert reason == "zero_value"


def test_sub_dust_transfer_is_quarantined():
    reason = spam.classify_spam(record("0xtarget", "0xsomebody", usd=0.4), {})
    assert reason == "dust"


def test_unpriced_token_is_quarantined():
    r = record("0xtarget", "0xsomebody", usd=None, basis="unpriced", amount=1e9)
    r["asset"] = "SCAMAIRDROP"
    assert spam.classify_spam(r, {}) == "unpriced_token"


def test_a_price_unavailable_major_is_not_spam():
    """A known major (ETH, WBTC, ...) the price source could not price today
    is real money, not noise. Quarantining it on the strength of a price
    outage would discard it permanently: quarantined records never reach
    TRANSFERS_DIR, only an address-keyed count survives, and the cursor
    advances past the range regardless -- there is no second chance once the
    price source recovers."""
    r = record("0xtarget", "0xbig", usd=None, basis="price_unavailable", amount=2.5)
    r["asset"] = "ETH"
    assert spam.classify_spam(r, {}) is None


def test_a_real_transfer_is_not_spam():
    assert spam.classify_spam(record("0xtarget", "0xbig", usd=13_000_000.0), {}) is None


def test_genuine_counterparty_not_flagged_when_low_volume_forgery_exists():
    """A genuine high-volume counterparty is NOT flagged as lookalike when
    a low-volume forgery of it exists in the volume dict."""
    wallet = "0xtarget"
    genuine = SELF_WALLET.lower()
    forgery = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    # Genuine moved $13M, forgery moved $1.
    vol = {genuine: 13_000_000.0, forgery: 1.0}
    # Classifying a genuine transfer to the genuine address.
    r = record(wallet, SELF_WALLET, usd=100.0)
    reason = spam.classify_spam(r, vol)
    assert reason is None


def test_forgery_still_flagged_when_genuine_has_more_volume():
    """A low-volume forgery IS still flagged as lookalike when the genuine
    address it copies has higher volume. This is the core case the
    volume ordering enables."""
    wallet = "0xtarget"
    genuine = SELF_WALLET.lower()
    forgery = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    # Genuine moved $13M, forgery moved $1.
    vol = {genuine: 13_000_000.0, forgery: 1.0}
    # Classifying a record FROM the forgery.
    r = record(forgery, wallet, usd=0.5)
    reason = spam.classify_spam(r, vol)
    assert reason == "lookalike"


def test_anchor_below_dust_usd_cannot_cause_match():
    """An anchor address with volume < dust_usd cannot match, even if the
    4+4 pattern matches."""
    genuine = SELF_WALLET.lower()
    forgery = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    # Genuine moved only $0.5 (below dust_usd=1.0).
    vol = {genuine: 0.5, forgery: 0.1}
    result = spam.is_lookalike(forgery, vol, dust_usd=1.0)
    assert result is None


def test_equal_volumes_flag_neither_side():
    """When two addresses have equal volume, neither is a lookalike of the
    other (strictly greater, not >=)."""
    address_a = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    address_b = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
    # Both have same volume.
    vol = {address_a: 100.0, address_b: 100.0}
    assert spam.is_lookalike(address_a, vol) is None
    assert spam.is_lookalike(address_b, vol) is None


def test_the_swept_wallet_is_never_a_forgery_of_its_own_counterparty():
    """counterparty_volume excludes the swept wallet, so it reads as $0.00 and
    any counterparty over dust_usd sharing its head/tail beats it. Judging the
    wallet against that map convicts it of forging its own counterparty."""
    wallet = SELF_WALLET.lower()
    forgery = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    vol = {forgery: 1.01, "0xa95d9c1f655341597c94393fddc30cf3c08e4fce": 13_500_000.0}

    r = record(wallet, "0xa95d9c1f655341597c94393fddc30cf3c08e4fce", usd=13_500_000.0)
    assert spam.classify_spam(r, vol, wallet=wallet) is None
    # Without the wallet, the old behaviour is still what it was.
    assert spam.classify_spam(r, vol) == "lookalike"


def test_forged_side_names_the_forgery_and_never_the_swept_wallet():
    wallet = SELF_WALLET.lower()
    forgery = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    other = "0xa95d9c1f655341597c94393fddc30cf3c08e4fce"
    vol = {forgery: 0.5, other: 13_500_000.0, wallet: 13_500_000.0}

    # The forgery is adjudicated against the wallet as anchor and named.
    assert spam.forged_side(record(forgery, other, usd=0.5), vol,
                            wallet=other) == (forgery, wallet)
    # The wallet itself is skipped even when it would otherwise match.
    assert spam.forged_side(record(wallet, other, usd=1.0), vol,
                            wallet=wallet) is None


def test_rollup_aggregates_by_address_and_keeps_the_mimic_target():
    wallet = "0xtarget"
    records = [
        {"src": wallet, "dst": "0xpoison", "spam": True, "spam_reason": "lookalike",
         "mimics": SELF_WALLET, "forged": "0xpoison", "ts": 100,
         "asset": "USDC", "token_address": "0xaf88"},
        {"src": "0xpoison", "dst": wallet, "spam": True, "spam_reason": "lookalike",
         "mimics": SELF_WALLET, "forged": "0xpoison", "ts": 300,
         "asset": "USDC", "token_address": "0xaf88"},
        {"src": wallet, "dst": "0xok", "spam": False, "spam_reason": None, "ts": 200},
    ]
    rolled = spam.rollup(records, wallet=wallet)
    assert len(rolled) == 1
    entry = rolled[0]
    assert entry["address"] == "0xpoison"
    assert entry["count"] == 2
    assert entry["mimics"] == SELF_WALLET
    assert entry["first_seen"] == 100
    assert entry["last_seen"] == 300


def test_rollup_keeps_the_token_of_an_unpriced_entry_so_it_can_be_registered():
    wallet = "0xtarget"
    records = [{"src": wallet, "dst": "0xnew", "spam": True,
                "spam_reason": "unpriced_token", "ts": 5,
                "asset": "REALTOKEN", "token_address": "0xdeadbeef"}]
    entry = spam.rollup(records, wallet=wallet)[0]
    assert entry["asset"] == "REALTOKEN"
    assert entry["token_address"] == "0xdeadbeef"
    assert entry["address"] == "0xnew"


def test_rollup_outgoing_spam_rolls_up_under_spammer():
    """Outgoing spam (src=wallet, dst=spammer) rolls up under the spammer."""
    wallet = "0xtarget"
    spammer = "0xspammer"
    records = [
        {"src": wallet, "dst": spammer, "spam": True,
         "spam_reason": "dust", "ts": 100, "asset": "USDC"},
    ]
    rolled = spam.rollup(records, wallet=wallet)
    assert len(rolled) == 1
    assert rolled[0]["address"] == spammer


def test_rollup_incoming_dust_rolls_up_under_spammer():
    """Incoming dust (src=spammer, dst=wallet) rolls up under the spammer."""
    wallet = "0xtarget"
    spammer1 = "0xspammer1"
    spammer2 = "0xspammer2"
    records = [
        {"src": spammer1, "dst": wallet, "spam": True,
         "spam_reason": "dust", "ts": 100, "asset": "USDC"},
        {"src": spammer2, "dst": wallet, "spam": True,
         "spam_reason": "dust", "ts": 200, "asset": "USDC"},
    ]
    rolled = spam.rollup(records, wallet=wallet)
    assert len(rolled) == 2
    addresses = {entry["address"] for entry in rolled}
    assert spammer1 in addresses
    assert spammer2 in addresses
