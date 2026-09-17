# tests/test_owner_wallets.py
"""The operator's own copy-trading accounts are never candidates.

The owner copies the target by hand, so an owner account trades his markets in
his direction just after him — to the style scorer, co-movement and portfolio
overlap, the most target-like wallet on the venue. It would top the lineup the
day it entered a scan: the contamination the target himself caused on
2026-09-13, from the other side.
"""

import json

import src.roster as roster
from src import backtest, scanner
from src.utils import never_candidates

T = "0x45d26f28196d226497130c4bac709d808fed4029"
OWNER = "0x" + "0a" * 20
STRANGER = "0x" + "5e" * 20
CONFIG = {"target_wallet": T, "owner_wallets": [OWNER.upper()]}


def test_never_candidates_is_the_target_and_the_owner():
    assert never_candidates(CONFIG) == {T, OWNER}
    assert never_candidates({"target_wallet": T}) == {T}
    assert never_candidates(None) == set()


def test_the_backtest_lineup_refuses_the_owner():
    scan = {"results": [{"wallet": OWNER}, {"wallet": T}, {"wallet": STRANGER}]}
    got = backtest.stranger_results(scan, T, exclude=[OWNER])
    assert [r["wallet"] for r in got] == [STRANGER]


def test_the_priority_scan_refuses_the_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    (tmp_path / "hl_transfers").mkdir(parents=True)
    (tmp_path / "hl_transfers" / "latest.json").write_text(json.dumps({"counterparties": [
        {"wallet": OWNER}, {"wallet": STRANGER}]}))
    scanned = []
    monkeypatch.setattr(scanner, "get_recent_bridge_depositors", lambda: [])
    monkeypatch.setattr(scanner, "scan_specific_wallet",
                        lambda wallet, *a, **k: scanned.append(wallet) or None)
    scanner.scan_priority_targets({}, {**CONFIG, "scanner": {}}, {"low": 0.65})
    assert STRANGER in scanned and OWNER not in scanned


def test_no_candidate_file_is_written_for_the_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scanner, "load_config", lambda: CONFIG)
    scanner.persist_candidate({"wallet": OWNER, "score": 0.99,
                               "scanned_at": "2026-09-17T00:00:00+00:00",
                               "evidence": {}, "dimensions": {}})
    assert not (tmp_path / "candidates" / f"{OWNER}.json").exists()


def test_the_roster_never_ranks_the_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "dormancy").mkdir(parents=True)
    (tmp_path / "dormancy" / "latest.json").write_text(json.dumps({"handoffs": {
        OWNER: {"score": 0.9}, STRANGER: {"score": 0.5}}}))
    rows = [r["wallet"] for r in roster.build_roster(CONFIG)["wallets"]]
    assert STRANGER in rows and OWNER not in rows
