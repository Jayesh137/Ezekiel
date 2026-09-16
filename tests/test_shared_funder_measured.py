# tests/test_shared_funder_measured.py
"""A shared first funder is ownership evidence only once the whole chain says it
is quiet — the rule the shared DEPOSIT address has followed since 2026-09-10.

`compute_linkage` pays two kinds of address reuse. Its shared-destination half
has been held to rule 9 for a week: every one of the target's destinations is
measured against Blockscout's whole-chain counters, and a busy or unmeasured one
is excluded. Its shared-FUNDER half sat one branch away and was never measured
at all, so the only thing standing between an exchange hot wallet and a linkage
vote was a hand-kept config list.

Measured 2026-09-16: the target's first funder, `0xf92402bb…`, has **2,282,986
transactions on Arbitrum**. Nothing had ever asked. Five wallets in the cache
share it, and the roster paid each of them a linkage vote for having withdrawn
from the same exchange as the target once — which is how `0x12e16e3d…` and its
four sub-accounts became five PROBABLE rows, how it became `risk.py`'s top
candidate, and one of the two vectors behind `0x5b5d5120…`'s PROBABLE grading.

`linkage_from_first_funders`' own docstring names the failure exactly: "a
funder that is exchange infrastructure links nobody … broadcasting a linkage
vector to everyone an exchange ever paid out to is rule 9 arriving at the tier
that feeds the close watch." The guard was right and was keyed on a label
nobody had set. **When one call in a pair is guarded, ask what guards the
other** — the third time this file's lesson has applied.

Being funded directly BY the target needs no measurement: the target is not
infrastructure, and that is the stronger half of the same signal.
"""

import src.linkage as lk
from src import roster

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
HOT = "0xf92402bb795fd7cd08fb83839689db79099c8c9c"
QUIET = "0x1111111111111111111111111111111111111111"
CAND = "0x2222222222222222222222222222222222222222"

BUSY_READING = {"is_contract": False, "txs": 2_282_986, "token_transfers": 1_031}
QUIET_READING = {"is_contract": False, "txs": 14, "token_transfers": 3}


class FakeCache:
    """ActivityCache's `get`, answering from a table, recording what was asked."""

    def __init__(self, readings):
        self.readings = readings
        self.asked = []

    def get(self, address, chain):
        self.asked.append((address, chain))
        return self.readings.get(address)


def _linkage(monkeypatch, *, target_funder, cand_funder, readings):
    """Run the graph's substrate linkage for one swept candidate."""
    cache = FakeCache(readings)
    monkeypatch.setattr(lk, "swept_wallets", lambda cfg: {CAND})
    monkeypatch.setattr(lk, "target_l1_profile",
                        lambda t: {"first_funder": None, "out_addrs": set()})
    monkeypatch.setattr(lk, "load_first_funders",
                        lambda: {TARGET: target_funder, CAND: cand_funder})
    monkeypatch.setattr(lk, "high_fanin_addresses", lambda threshold=25: set())
    monkeypatch.setattr(lk, "outbound_chains", lambda t: {})
    monkeypatch.setattr(lk, "activity_cache", lambda max_lookups=0, seconds=None: cache)
    monkeypatch.setattr(lk, "get_outbound_addresses",
                        lambda w, config=None, records=None: set())
    import src.chain.collect as collect
    monkeypatch.setattr(collect, "records_by_wallet", lambda ws: {w: [] for w in ws})
    config = {"excluded_addresses": [], "known_self_wallets": []}
    return lk.substrate_linkage(TARGET, [CAND], config=config), cache


def test_a_busy_first_funder_links_nobody_in_the_graph(monkeypatch):
    got, cache = _linkage(monkeypatch, target_funder=HOT, cand_funder=HOT,
                          readings={HOT: BUSY_READING})

    assert CAND not in got, "an exchange hot wallet was paid as a shared funder"
    assert (HOT, "arbitrum") in cache.asked, "the funder was never measured"


def test_an_unmeasured_first_funder_links_nobody_until_measured(monkeypatch):
    """Rule 9: unmeasured means excluded. Delayed, never asserted."""
    got, _ = _linkage(monkeypatch, target_funder=HOT, cand_funder=HOT, readings={})

    assert CAND not in got


def test_a_measured_quiet_first_funder_still_links(monkeypatch):
    got, _ = _linkage(monkeypatch, target_funder=QUIET, cand_funder=QUIET,
                      readings={QUIET: QUIET_READING})

    assert got[CAND]["shared_funder"] is True


def test_funded_directly_by_the_target_needs_no_measurement(monkeypatch):
    got, _ = _linkage(monkeypatch, target_funder=HOT, cand_funder=TARGET,
                      readings={HOT: BUSY_READING})

    assert got[CAND]["shared_funder"] is True
    assert "directly by the target" in " ".join(got[CAND]["reasons"])


def test_the_funder_is_measured_before_the_destinations(monkeypatch):
    """One funder reading decides a vote for every wallet that shares it, where
    a destination decides one at a time — so it must not be the reading a
    bounded budget runs out before reaching."""
    cache = FakeCache({HOT: BUSY_READING})
    monkeypatch.setattr(lk, "swept_wallets", lambda cfg: set())
    monkeypatch.setattr(lk, "target_l1_profile",
                        lambda t: {"first_funder": HOT,
                                   "out_addrs": {"0x" + "d" * 40, "0x" + "e" * 40}})
    monkeypatch.setattr(lk, "load_first_funders", dict)
    monkeypatch.setattr(lk, "high_fanin_addresses", lambda threshold=25: set())
    monkeypatch.setattr(lk, "outbound_chains", lambda t: {})
    monkeypatch.setattr(lk, "activity_cache", lambda max_lookups=0, seconds=None: cache)
    import src.chain.collect as collect
    monkeypatch.setattr(collect, "records_by_wallet", lambda ws: {})

    lk.substrate_linkage(TARGET, [], config={"excluded_addresses": [],
                                             "known_self_wallets": []})

    assert cache.asked[0] == (HOT, "arbitrum")


# --- the roster's own reading of the funder cache ---------------------------------


def test_roster_excludes_a_busy_funder(monkeypatch):
    funders = {TARGET: HOT, CAND: HOT}
    excluded = roster.funder_exclusions(funders, TARGET, FakeCache({HOT: BUSY_READING}))

    assert HOT in excluded
    assert roster.linkage_from_first_funders(funders, TARGET, excluded) == {}


def test_roster_excludes_an_unmeasured_funder(monkeypatch):
    funders = {TARGET: HOT, CAND: HOT}
    excluded = roster.funder_exclusions(funders, TARGET, FakeCache({}))

    assert roster.linkage_from_first_funders(funders, TARGET, excluded) == {}


def test_roster_keeps_a_quiet_funder_and_direct_funding():
    funders = {TARGET: QUIET, CAND: QUIET, "0x" + "3" * 40: TARGET}
    excluded = roster.funder_exclusions(funders, TARGET, FakeCache({QUIET: QUIET_READING}))

    found = roster.linkage_from_first_funders(funders, TARGET, excluded)
    assert found == {CAND: QUIET, "0x" + "3" * 40: TARGET}


def test_roster_funder_exclusion_survives_an_unreadable_cache():
    """A cache that raises is "we could not tell" — the funder stays excluded."""

    class Broken:
        def get(self, address, chain):
            raise OSError("disk")

    excluded = roster.funder_exclusions({TARGET: HOT}, TARGET, Broken())
    assert HOT in excluded


def test_roster_funder_exclusion_with_no_funder_is_empty():
    assert roster.funder_exclusions({}, TARGET, FakeCache({})) == set()
