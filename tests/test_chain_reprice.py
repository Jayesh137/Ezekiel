# tests/test_chain_reprice.py
"""Re-pricing records that were stored before a price was available.

Pricing happens once, at collection time, against a deliberately tiny request
budget — so a sweep that collects thousands of records prices a handful. Every
other record sits on disk `price_unavailable` and is dropped by
`transfer_graph.normalise_transfer_record`, so it never becomes a graph edge.
This module is what makes "coverage improves across runs" true rather than
aspirational.

No test here makes a real request: `price_lookup` is always a fake.
"""
import json

import pytest

from src.chain import reprice


def record(**kw):
    base = {
        "id": "arbitrum:0xh:erc20:0", "chain": "arbitrum", "chain_id": 42161,
        "block": 100, "ts": 1781000000, "timestamp": "2026-06-16T00:00:00+00:00",
        "tx_hash": "0xh", "src": "0xa", "dst": "0xb", "kind": "erc20",
        "asset": "ETH", "token_address": None, "amount": 3.0,
        "amount_usd": None, "value_basis": "price_unavailable",
        "spam": False, "spam_reason": None,
    }
    base.update(kw)
    return base


def write(root, chain, day, records):
    d = root / chain
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.json").write_text(json.dumps(records, indent=2))
    return d / f"{day}.json"


def read(path):
    return json.loads(path.read_text())


def counting_lookup(price=2000.0):
    calls = []

    def lookup(symbol, date_str):
        calls.append((symbol, date_str))
        return price

    lookup.calls = calls
    return lookup


# --- the core behaviour ------------------------------------------------------

def test_a_price_unavailable_record_is_repriced_in_place(tmp_path):
    path = write(tmp_path, "arbitrum", "2026-06-16", [record()])

    health = reprice.reprice_stored_records(counting_lookup(2000.0), root=tmp_path)

    got = read(path)[0]
    assert got["amount_usd"] == 6000.0          # 3.0 ETH at $2000
    assert got["value_basis"] == "daily_close"
    assert health["repriced"] == 1
    assert health["files_rewritten"] == 1


def test_the_record_is_otherwise_untouched(tmp_path):
    """Only the two price fields may change — the id, the amount, the addresses
    and everything the graph keys on must survive byte-identical."""
    original = record()
    path = write(tmp_path, "arbitrum", "2026-06-16", [original])

    reprice.reprice_stored_records(counting_lookup(2000.0), root=tmp_path)

    got = read(path)[0]
    for field in ("id", "chain", "chain_id", "block", "ts", "timestamp",
                  "tx_hash", "src", "dst", "kind", "asset", "amount", "spam"):
        assert got[field] == original[field], field


def test_a_record_that_cannot_be_priced_is_left_exactly_as_it_was(tmp_path):
    path = write(tmp_path, "arbitrum", "2026-06-16", [record()])
    before = read(path)

    health = reprice.reprice_stored_records(lambda s, d: None, root=tmp_path)

    assert read(path) == before, "an unpriced record was mutated"
    assert health["repriced"] == 0
    assert health["still_unpriced"] == 1
    assert health["files_rewritten"] == 0, "a file was rewritten with no change"


def test_a_failed_lookup_never_writes_zero(tmp_path):
    """The rule the whole valuation path is built on: zero is invisible to
    every threshold in the system, None is loud."""
    path = write(tmp_path, "arbitrum", "2026-06-16", [record(amount=700.0)])

    reprice.reprice_stored_records(lambda s, d: None, root=tmp_path)

    got = read(path)[0]
    assert got["amount_usd"] is None
    assert got["amount_usd"] != 0.0


def test_running_twice_changes_nothing_the_second_time(tmp_path):
    path = write(tmp_path, "arbitrum", "2026-06-16", [record()])
    reprice.reprice_stored_records(counting_lookup(2000.0), root=tmp_path)
    after_first = read(path)

    second = counting_lookup(9999.0)
    health = reprice.reprice_stored_records(second, root=tmp_path)

    assert read(path) == after_first, "a priced record was repriced again"
    assert second.calls == [], "already-priced records still cost a request"
    assert health["examined"] == 0
    assert health["files_rewritten"] == 0


# --- what must NOT be touched ------------------------------------------------

def test_quarantined_records_are_never_repriced(tmp_path):
    path = write(tmp_path, "arbitrum", "2026-06-16", [
        record(id="spam", spam=True, spam_reason="lookalike")])

    lookup = counting_lookup()
    health = reprice.reprice_stored_records(lookup, root=tmp_path)

    assert read(path)[0]["amount_usd"] is None
    assert lookup.calls == []
    assert health["examined"] == 0


def test_an_unknown_token_is_not_retried(tmp_path):
    """`unpriced` is a verdict about the token, not a gap in coverage. Retrying
    it every run would spend the budget on scam airdrops forever."""
    path = write(tmp_path, "arbitrum", "2026-06-16", [
        record(asset="SCAMCOIN", value_basis="unpriced")])

    lookup = counting_lookup()
    health = reprice.reprice_stored_records(lookup, root=tmp_path)

    assert read(path)[0]["value_basis"] == "unpriced"
    assert lookup.calls == []
    assert health["examined"] == 0


def test_an_already_priced_record_is_not_touched(tmp_path):
    path = write(tmp_path, "arbitrum", "2026-06-16", [
        record(amount_usd=5000.0, value_basis="stable_par", asset="USDC")])

    lookup = counting_lookup()
    reprice.reprice_stored_records(lookup, root=tmp_path)

    assert read(path)[0]["amount_usd"] == 5000.0
    assert lookup.calls == []


# --- the budget-stretching property ------------------------------------------

def test_one_lookup_serves_every_record_in_the_same_asset_and_day(tmp_path):
    """The reason a dozen requests per run is workable at all: transfers
    cluster by (asset, date), so one price covers many records."""
    path = write(tmp_path, "arbitrum", "2026-06-16", [
        record(id=f"arbitrum:0xh{i}:erc20:0", tx_hash=f"0xh{i}") for i in range(25)])

    lookup = counting_lookup(2000.0)
    health = reprice.reprice_stored_records(lookup, root=tmp_path)

    assert len(lookup.calls) == 1, "one price per (asset, date) group"
    assert health["repriced"] == 25
    assert health["groups_tried"] == 1
    assert all(r["amount_usd"] == 6000.0 for r in read(path))


def test_the_same_group_is_shared_across_chains_and_files(tmp_path):
    """ETH on 2026-06-16 is one price whether it moved on Arbitrum or Base."""
    write(tmp_path, "arbitrum", "2026-06-16", [record(chain="arbitrum")])
    write(tmp_path, "base", "2026-06-16", [record(id="base:0xh:erc20:0", chain="base")])

    lookup = counting_lookup(2000.0)
    health = reprice.reprice_stored_records(lookup, root=tmp_path)

    assert len(lookup.calls) == 1
    assert health["repriced"] == 2


def test_different_days_are_separate_groups(tmp_path):
    write(tmp_path, "arbitrum", "2026-06-16", [record(ts=1781000000)])
    write(tmp_path, "arbitrum", "2026-06-17", [
        record(id="arbitrum:0xh2:erc20:0", ts=1781000000 + 86400)])

    lookup = counting_lookup(2000.0)
    reprice.reprice_stored_records(lookup, root=tmp_path)

    assert len({d for _, d in lookup.calls}) == 2


def test_a_group_that_comes_back_unpriced_is_not_re_asked(tmp_path):
    """A bounded budget must not be spent re-asking the same question."""
    write(tmp_path, "arbitrum", "2026-06-16", [
        record(id=f"arbitrum:0xh{i}:erc20:0") for i in range(10)])

    lookup = counting_lookup(None)
    health = reprice.reprice_stored_records(lookup, root=tmp_path)

    assert len(lookup.calls) == 1
    assert health["still_unpriced"] == 10


def test_an_exhausted_budget_makes_the_pass_a_no_op_rather_than_an_overrun(tmp_path):
    """`price_lookup` returns None for everything once its budget is spent, so
    this pass ends quietly instead of running past a job timeout."""
    path = write(tmp_path, "arbitrum", "2026-06-16", [
        record(id=f"arbitrum:0xh{i}:erc20:0", asset=a)
        for i, a in enumerate(["ETH", "WBTC", "ARB"])])
    before = read(path)

    health = reprice.reprice_stored_records(lambda s, d: None, root=tmp_path)

    assert read(path) == before
    assert health["repriced"] == 0
    assert health["still_unpriced"] == 3


# --- resilience --------------------------------------------------------------

def test_a_missing_root_is_not_an_error(tmp_path):
    health = reprice.reprice_stored_records(counting_lookup(), root=tmp_path / "nope")
    assert health["examined"] == 0
    assert health["files_rewritten"] == 0


@pytest.mark.parametrize("body", ["not json at all", '{"not": "a list"}', "null"])
def test_an_unreadable_file_is_skipped_not_rewritten(tmp_path, body):
    """A file we cannot read is not a file we may rewrite."""
    d = tmp_path / "arbitrum"
    d.mkdir(parents=True)
    bad = d / "2026-06-16.json"
    bad.write_text(body)

    health = reprice.reprice_stored_records(counting_lookup(), root=tmp_path)

    assert bad.read_text() == body, "an unreadable file was overwritten"
    assert health["files_rewritten"] == 0


def test_one_unreadable_file_does_not_stop_the_others(tmp_path):
    d = tmp_path / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-06-15.json").write_text("not json")
    good = write(tmp_path, "arbitrum", "2026-06-16", [record()])

    health = reprice.reprice_stored_records(counting_lookup(2000.0), root=tmp_path)

    assert read(good)[0]["amount_usd"] == 6000.0
    assert health["repriced"] == 1


def test_a_record_with_an_unusable_timestamp_is_counted_not_crashed(tmp_path):
    path = write(tmp_path, "arbitrum", "2026-06-16", [record(ts="not-a-number")])

    health = reprice.reprice_stored_records(counting_lookup(2000.0), root=tmp_path)

    assert read(path)[0]["amount_usd"] is None
    assert health["still_unpriced"] == 1
