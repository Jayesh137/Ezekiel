# tests/test_spam_ground_truth.py
"""An address the operator has declared cannot be judged a forgery of anything.

Found on the live quarantine ledger 2026-09-16:

    address : 0x45d26f28...   <- THE TARGET
    reason  : lookalike
    mimics  : 0x45d2e417c052b8fd155a0c89985b20a41a264029
    asset   : USDC  (0xaf88d065... - the canonical Arbitrum USDC in config.json)
    count   : 796            2024-06-13 -> 2026-08

**796 records of genuine canonical USDC involving the target were destroyed**,
because he was judged to be a forgery of his own vanity twin. Quarantined
records never reach `data/transfers/` and the cursor advances past them, so the
loss is permanent — those are 796 edges the transfer graph will never have.

On-chain the verdict is backwards: on Arbitrum the target has 688 transactions
and 9,625 token transfers, the twin 59 and 698. He is 14x the more active
address; the twin is the poisoner.

The mechanism is rule 9 in a different file. `is_lookalike` ranks by volume
**within one sweep's `counterparty_volume`** — a local count answering a global
question about identity, exactly as fan-in inside the substrate once made
SocketGateway look like a five-sender private deposit address. `forged_side`
already refuses to convict the SWEPT wallet, and its docstring explains why in
detail; but when some third wallet is swept, the target is merely a
counterparty and is eligible again, losing to whichever twin moved more money
in that one sweep.

`config.target_wallet` and `config.known_self_wallets` are operator ground
truth everywhere else in this project — `roster.assign_tier` says so in as many
words, "known_self is operator ground truth from config and outranks
measurement". Here they could be branded forgeries by an arithmetic accident.

Protection is deliberately ONE-SIDED: it stops a protected address being called
the forgery, and does nothing to stop the address forging IT from being caught.
Disabling the check around these addresses would be the opposite of the point,
since they are precisely the addresses worth poisoning.
"""

from src.chain import spam as spam_mod

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
TWIN = "0x45d2e417c052b8fd155a0c89985b20a41a264029"
OTHER = "0x9999999999999999999999999999999999999999"

# The twin outspends the target in THIS sweep, which is the whole trap: it is
# true locally and false on the chain.
VOLUME = {TARGET: 100.0, TWIN: 5_000.0, OTHER: 20.0}


def _record(src, dst, asset="USDC"):
    return {"src": src, "dst": dst, "asset": asset, "amount": 1000.0,
            "amount_usd": 1000.0, "value_basis": "stable_par"}


def test_a_protected_address_is_never_the_forgery():
    found = spam_mod.forged_side(_record(TWIN, TARGET), VOLUME,
                                 wallet=OTHER, protected={TARGET})

    assert found is None or found[0] != TARGET


def test_without_protection_the_target_is_convicted():
    """The live bug, pinned — so the guard is shown to be load-bearing."""
    found = spam_mod.forged_side(_record(TWIN, TARGET), VOLUME, wallet=OTHER)

    assert found == (TARGET, TWIN)


def test_a_protected_address_record_is_not_quarantined_as_a_lookalike():
    reason = spam_mod.classify_spam(_record(TWIN, TARGET), VOLUME,
                                    wallet=OTHER, protected={TARGET})

    assert reason != "lookalike"


def test_protection_still_catches_the_address_forging_the_protected_one():
    """One-sided. A protected address is worth poisoning, so the net stays up.

    Here the target outspends the twin — the normal case — and the twin must
    still be convicted with the target named as what it mimics.
    """
    volume = {TARGET: 5_000.0, TWIN: 100.0}

    found = spam_mod.forged_side(_record(TWIN, TARGET), volume,
                                 wallet=OTHER, protected={TARGET})

    assert found == (TWIN, TARGET)


def test_protection_is_case_insensitive():
    found = spam_mod.forged_side(_record(TWIN, TARGET.upper()), VOLUME,
                                 wallet=OTHER, protected={TARGET.upper()})

    assert found is None or found[0] != TARGET


def test_no_protected_set_leaves_behaviour_unchanged():
    """`None` means protect nothing, the pre-existing behaviour."""
    assert (spam_mod.forged_side(_record(TWIN, TARGET), VOLUME, wallet=OTHER)
            == spam_mod.forged_side(_record(TWIN, TARGET), VOLUME,
                                    wallet=OTHER, protected=None))


# --- deriving the set, and threading it through the sweep -------------------

def test_ground_truth_is_the_target_plus_the_declared_self_wallets():
    got = spam_mod.ground_truth_addresses({
        "target_wallet": TARGET.upper(),
        "known_self_wallets": ["0xAAA", "0xbbb"],
    })

    assert got == {TARGET, "0xaaa", "0xbbb"}


def test_ground_truth_of_an_empty_config_is_empty():
    """Never a default that quietly protects nothing-in-particular."""
    assert spam_mod.ground_truth_addresses({}) == set()


def test_roster_confirmed_wallets_are_not_ground_truth():
    """A tier is an inference; only config is the operator speaking.

    `roster.assign_tier` puts it plainly — known_self "is operator ground truth
    from config and outranks measurement". A CONFIRMED tier is measurement, and
    letting it grant forgery immunity would let one inference silence a
    detector, which is the coupling the linkage fix was about.
    """
    got = spam_mod.ground_truth_addresses({"target_wallet": TARGET})

    assert got == {TARGET}


def test_the_sweep_passes_ground_truth_to_the_classifier(monkeypatch):
    """The guard is worth nothing if the sweep never hands it over."""
    seen = {}

    def spy(record, volume, **kwargs):
        seen.update(kwargs)
        return None

    from src.chain import collect
    # fetch_kind is faked below, so the key only has to be present
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    monkeypatch.setattr(collect.spam_mod, "classify_spam", spy)
    monkeypatch.setattr(collect.spam_mod, "counterparty_volume", lambda *a, **k: {})
    from src.chain.client import WalkResult
    walk = WalkResult(rows=[{"from": "0x1", "to": "0x2"}], last_block=1,
                      pages=1, truncated=False, possible_gaps=[])
    monkeypatch.setattr(collect, "fetch_kind", lambda *a, **k: (walk, None))
    monkeypatch.setattr(collect, "normalise_row",
                        lambda *a, **k: _record(TWIN, TARGET))
    monkeypatch.setattr(collect, "append_records", lambda *a, **k: 0)
    monkeypatch.setattr(collect, "newest_block", lambda *a, **k: (None, None))
    monkeypatch.setattr(collect, "write_cursors", lambda *a, **k: None)
    monkeypatch.setattr(collect, "read_cursors", lambda *a, **k: {})

    from src.chain.budget import CallBudget
    collect.sweep_wallet(
        OTHER, [{"name": "arbitrum", "chain_id": 42161, "native": "ETH"}],
        CallBudget(max_calls=10, seconds=10), cluster=True, protected={TARGET})

    assert seen.get("protected") == {TARGET}
