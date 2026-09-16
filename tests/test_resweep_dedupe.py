# tests/test_resweep_dedupe.py
"""A full re-read must not re-store the history it already holds.

`utils.append_records` dedupes against TODAY'S file and nothing else, because a
normal incremental sweep resumes from a cursor and only ever sees new rows. A
`--reset` sweep breaks that assumption completely: it re-reads every wallet from
block 0, so every record already on disk under an earlier date comes back and is
appended again under today's.

For the 2-wallet cluster that was harmless. Frontier-wide it is fatal.
Measured 2026-09-16: 229 swept wallets, 604,155 unique records at ~683 bytes,
plus roughly 332,000 newly-admitted unpriced ones — about 640 MB landing in one
day, split across three chain files at roughly 213 MB each. GitHub refuses a
blob over 100 MiB, `scripts/check_repo_size.py` fails the step before the
commit, and the whole run's work is discarded. The re-sweep would destroy its
own output.

So the sweep takes a set of ids it already holds. Two properties matter:

  * it is built ONCE per run and shared across wallets, because building it per
    wallet is the O(wallets x substrate) shape that already failed the linkage
    phase (see `collect.records_by_wallet`);
  * a record accepted during the run is ADDED to it, so two wallets touching
    the same transfer store it once rather than twice — the within-run half of
    the same problem.

Passing None keeps the old behaviour exactly, so the incremental sweeps that
never had this problem are untouched.
"""

import json

import pytest

from src.chain import collect
from src.chain.budget import CallBudget
from src.chain.client import WalkResult

CHAIN = {"name": "ethereum", "chain_id": 1, "native": "ETH"}
WALLET = "0x1111111111111111111111111111111111111111"


@pytest.fixture
def swept(tmp_path, monkeypatch):
    """A sweep wired to fake rows, writing into an isolated substrate."""
    root = tmp_path / "transfers"
    root.mkdir(parents=True)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")
    monkeypatch.setattr(collect, "TRANSFERS_DIR", root)
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "cursors.json")
    monkeypatch.setattr(collect, "newest_block", lambda *a, **k: (None, None))
    monkeypatch.setattr(collect.spam_mod, "classify_spam", lambda *a, **k: None)
    monkeypatch.setattr(collect.spam_mod, "counterparty_volume", lambda *a, **k: {})

    rows = [{"from": WALLET, "to": "0x2222222222222222222222222222222222222222",
             "value": "1000000", "timeStamp": "1700000000", "blockNumber": "1",
             "hash": "0xdead", "tokenSymbol": "USDC", "tokenDecimal": "6",
             "contractAddress": "0xaf88d065e77c8cc2239327c5edb3a432268e5831"}]

    def fake_fetch(addr, chain, kind, start, budget, **kw):
        # one row, on the erc20 pass only, so the record id is stable
        return (WalkResult(rows=rows if kind == "erc20" else [], last_block=1,
                           pages=1, truncated=False, possible_gaps=[]), None)

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch)

    def run(known_ids=None):
        return collect.sweep_wallet(
            WALLET, [CHAIN], CallBudget(max_calls=50, seconds=30),
            cluster=True, known_ids=known_ids)

    return run, root


def _stored(root):
    files = list((root / "ethereum").glob("*.json"))
    out = []
    for f in files:
        out.extend(json.loads(f.read_text()))
    return out


def test_a_record_already_held_is_not_stored_again(swept):
    run, root = swept
    run()
    first = _stored(root)
    assert len(first) == 1

    known = {r["id"] for r in first}
    run(known_ids=known)

    assert len(_stored(root)) == 1, "the re-read stored a second copy"


def test_without_the_id_set_the_old_behaviour_is_unchanged(swept):
    """Incremental sweeps never had this problem and must not change."""
    run, root = swept
    run()
    run()          # same day, so append_records' own dedupe still applies

    assert len(_stored(root)) == 1


def test_a_record_accepted_this_run_joins_the_set(swept):
    """Two wallets touching one transfer must store it once, not twice.

    The set is shared across the wallets of a run, so it has to learn as it
    goes — otherwise the within-run duplicates survive and the saving is only
    against previous runs.
    """
    run, _root = swept
    known: set = set()
    run(known_ids=known)

    assert len(known) == 1


def test_a_genuinely_new_record_is_still_stored(swept):
    """Guard against the filter swallowing the sweep."""
    run, root = swept

    run(known_ids={"ethereum:0xsomethingelse:erc20:0"})

    assert len(_stored(root)) == 1


# --- which wallets a bounded batch takes, and in what order ----------------

def test_the_cluster_leads_the_batch(monkeypatch):
    """A bounded run must never leave the target at the back of a queue."""
    from scripts import backfill_transfers as bt

    monkeypatch.setattr(bt, "read_cursors", lambda: {
        "ethereum:0xaaa:erc20": 900, "ethereum:0xbbb:erc20": 100,
        "ethereum:0xtarget:erc20": 50})

    assert bt.swept_wallets_by_age(["0xtarget"])[0] == "0xtarget"


def test_the_rest_follow_least_recently_swept_first():
    """Repeated runs march through the list instead of re-reading the head.

    The `expanded_ledger` lesson in the one other place that walks a wallet
    list under a cap: if the order is anything but progress, a bounded run
    re-does the same work every time and the tail is never reached.
    """
    import unittest.mock as m

    from scripts import backfill_transfers as bt

    with m.patch.object(bt, "read_cursors", lambda: {
            "ethereum:0xaaa:erc20": 900, "ethereum:0xbbb:erc20": 100,
            "ethereum:0xccc:erc20": 500}):
        assert bt.swept_wallets_by_age([]) == ["0xbbb", "0xccc", "0xaaa"]


def test_a_wallet_in_the_cluster_is_not_listed_twice():
    import unittest.mock as m

    from scripts import backfill_transfers as bt

    with m.patch.object(bt, "read_cursors", lambda: {"ethereum:0xtarget:erc20": 5}):
        assert bt.swept_wallets_by_age(["0xtarget"]) == ["0xtarget"]


def test_a_wallets_furthest_cursor_decides_its_age():
    """Per (chain, kind) cursors; the wallet is as swept as its furthest one."""
    import unittest.mock as m

    from scripts import backfill_transfers as bt

    with m.patch.object(bt, "read_cursors", lambda: {
            "ethereum:0xaaa:erc20": 10, "arbitrum:0xaaa:native": 999,
            "ethereum:0xbbb:erc20": 500}):
        assert bt.swept_wallets_by_age([]) == ["0xbbb", "0xaaa"]
