"""The candidate measurement pass: who gets measured, and who is skipped.

Network-free: only the pure selection is exercised here.
"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "measure_candidates", Path(__file__).resolve().parent.parent / "scripts" / "measure_candidates.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

CFG = {"target_wallet": "0xTARGET", "known_self_wallets": ["0xSELF"]}


def roster(*rows):
    return {"wallets": [dict(r) for r in rows]}


def test_candidates_are_the_tiers_the_operator_sees():
    doc = roster({"wallet": "0xa", "tier": "POSSIBLE"},
                 {"wallet": "0xb", "tier": "WATCH"},
                 {"wallet": "0xc", "tier": "CONFIRMED"},
                 {"wallet": "0xd", "tier": "INFRASTRUCTURE"})
    assert mc.candidates(doc, CFG) == ["0xa", "0xc"]


def test_config_ground_truth_is_never_measured():
    """A reading cannot disqualify a wallet the operator named, so a lookup
    spent on one buys nothing."""
    doc = roster({"wallet": "0xtarget", "tier": "CONFIRMED"},
                 {"wallet": "0xself", "tier": "CONFIRMED"},
                 {"wallet": "0xa", "tier": "POSSIBLE"})
    assert mc.candidates(doc, CFG) == ["0xa"]


def test_a_wallet_already_graded_a_service_is_skipped():
    doc = roster({"wallet": "0xa", "tier": "POSSIBLE", "is_service": True},
                 {"wallet": "0xb", "tier": "POSSIBLE"})
    assert mc.candidates(doc, CFG) == ["0xb"]


def test_roster_order_is_preserved_so_the_strongest_are_measured_first():
    doc = roster({"wallet": "0xz", "tier": "CONFIRMED"},
                 {"wallet": "0xa", "tier": "POSSIBLE"})
    assert mc.candidates(doc, CFG) == ["0xz", "0xa"]


def test_unmeasured_matches_on_the_address_whatever_the_chain():
    table = {"arbitrum:0xa": {"txs": 1}, "ethereum:0xc": {"txs": 2}}
    assert mc.unmeasured(["0xa", "0xb", "0xc"], table) == ["0xb"]


def test_unmeasured_handles_an_empty_or_missing_cache():
    assert mc.unmeasured(["0xa"], {}) == ["0xa"]
    assert mc.unmeasured(["0xa"], None) == ["0xa"]


def test_no_roster_is_not_an_error():
    """The pass runs before the first roster exists, and must not fail the run."""
    assert mc.candidates({}, CFG) == []
