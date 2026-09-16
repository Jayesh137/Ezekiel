# tests/test_target_not_a_candidate.py
"""The target may never be scored as a candidate for being himself.

`scan_priority_targets` had no target exclusion while the leaderboard sweep one
screen below it did (`if wallet.lower() == target: continue`). Six priority
sources feed that function and any of them can name him: he deposits to the
bridge, he is a CCTP depositor, and — as actually happened — the tracer books a
fund-flow finding whose source and destination are both him.

Measured on the live data 2026-09-16: three such findings, worth $0.00004 each,
put him in the priority set. From there:

  * `data/scans/latest.json` carried him at score 1.0, and `backtest.py` scores
    every row of that file as a STRANGER — so the self-match was ranked behind
    itself. Reported `self_rank` went 2, 3, 3, 3, 2 over four days and
    `passed: false` throughout. Without the contaminant the same run reads
    self 0.6593 against best stranger 0.5927: rank 1, margin +0.0666, which
    PASSES. This is not a scoring change — rule 4 forbids those — it is the
    removal of a row that was never a stranger.
  * `roster.behavioural_is_trustworthy()` reads that verdict, so a whole vector
    stopped casting a vote while the backtest said FAILING.
  * `risk.py` takes the top candidate's score, so he paid the full 22 of 22
    points for trading like himself, and `CRITICAL: Migration Risk CRITICAL
    75/100` fired on 2026-09-15 on the strength of it.

So three guards, because the contamination is durable at each stage and each
stage answers for a different one:

  1. the priority set, which is where he gets in;
  2. `persist_candidate`, which writes a per-wallet file that `latest.json` is
     re-globbed from — so one contaminated scan leaves a file that resurfaces
     him at the top of the candidate list on every run FOREVER, long after the
     scan that wrote it;
  3. the backtest's stranger loop, because the instrument that validates the
     scorer must not be fooled by a stored file it did not write.
"""

import json

import pytest

from src import backtest, scanner

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
STRANGER = "0x1111111111111111111111111111111111111111"


@pytest.fixture
def scan_dirs(tmp_path, monkeypatch):
    """A data dir holding only a fund-flow finding that names the given wallets."""
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scanner, "get_recent_bridge_depositors", lambda *a, **k: [])
    return tmp_path


def _write_fund_flows(root, destinations):
    d = root / "fund_flows"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps({
        "findings": [{"source": TARGET, "destination": dest, "amount_usdc": "0.00"}
                     for dest in destinations]
    }))


def _spy_on_scanned(monkeypatch):
    """Record every wallet the priority phase decides to scan.

    Returning None is what a wallet with too few fills returns, so the loop
    simply continues — the decision of WHOM to scan is the behaviour under test.
    """
    scanned = []

    def recorder(wallet, *args, **kwargs):
        scanned.append(wallet.lower())
        return None

    monkeypatch.setattr(scanner, "scan_specific_wallet", recorder)
    return scanned


def _config():
    return {"target_wallet": TARGET,
            "scanner": {"candidate_threshold": 0.65}}


def test_priority_scan_never_scans_the_target(scan_dirs, monkeypatch):
    """A fund-flow finding pointing at the target must not put him in the sweep."""
    _write_fund_flows(scan_dirs, [TARGET])
    scanned = _spy_on_scanned(monkeypatch)

    scanner.scan_priority_targets({}, _config(), {"low": 0.65})

    assert TARGET not in scanned


def test_priority_scan_still_scans_everyone_else(scan_dirs, monkeypatch):
    """The exclusion must remove one wallet, not disable the priority phase."""
    _write_fund_flows(scan_dirs, [TARGET, STRANGER])
    scanned = _spy_on_scanned(monkeypatch)

    scanner.scan_priority_targets({}, _config(), {"low": 0.65})

    assert scanned == [STRANGER]


def test_persist_candidate_writes_no_file_for_the_target(tmp_path, monkeypatch):
    """The per-wallet candidate file outlives the scan that wrote it.

    `latest.json` is rebuilt by globbing `0x*.json` and sorting on `best_score`,
    which only ratchets up — so a single contaminated write pins the target at
    the top of the operator's candidate list, and at 22 of 22 risk points,
    indefinitely.
    """
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)

    scanner.persist_candidate({
        "wallet": TARGET,
        "score": 1.0,
        "scanned_at": "2026-09-16T00:00:00+00:00",
        "evidence": {"tier": "CONFIRMED_CANDIDATE"},
        "dimensions": {},
    })

    assert not (tmp_path / "candidates" / f"{TARGET}.json").exists()


def test_persist_candidate_still_writes_for_a_stranger(tmp_path, monkeypatch):
    """Guard against the exclusion swallowing every candidate."""
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)

    scanner.persist_candidate({
        "wallet": STRANGER,
        "score": 0.71,
        "scanned_at": "2026-09-16T00:00:00+00:00",
        "evidence": {"tier": "STRONG_MATCH"},
        "dimensions": {},
    })

    assert (tmp_path / "candidates" / f"{STRANGER}.json").exists()


def test_backtest_strangers_exclude_the_target():
    """A stored scan file naming the target must not seed the lineup.

    The self-match is windowed and therefore handicapped against a fingerprint
    built from the wallet's whole history: live, the target scored 0.6985 as a
    "stranger" against his own 0.6593 self-match and took rank 1 off himself.
    """
    scan = {"results": [
        {"wallet": TARGET, "fingerprint": {"x": 1}},
        {"wallet": STRANGER, "fingerprint": {"x": 2}},
    ]}

    rows = backtest.stranger_results(scan, TARGET)

    assert [r["wallet"] for r in rows] == [STRANGER]


def test_backtest_strangers_match_the_target_case_insensitively():
    """Addresses arrive checksummed from the API and lowercased from storage."""
    scan = {"results": [{"wallet": TARGET.upper(), "fingerprint": {"x": 1}}]}

    assert backtest.stranger_results(scan, TARGET) == []
