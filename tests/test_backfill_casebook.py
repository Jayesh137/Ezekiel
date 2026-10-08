"""History in, the same casebook the live update would have built (spec §10)."""

import json

import pytest

from scripts import backfill_casebook as backfill
from src.casebook import store
from src.casebook.cases import iso

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B = "0x" + "a" * 40, "0x" + "b" * 40
CONFIG = {"target_wallet": T, "known_self_wallets": []}
H = 3_600_000
T0 = 1_757_500_000_000       # 2025-09-10T10:13:20Z


def version(hours, rows, computed=True):
    doc = {"wallets": rows}
    if computed:
        doc["computed_at"] = iso(T0 + hours * H)
    return (T0 + hours * H, f"v{hours}", doc)


def test_backfill_builds_history_and_marks_what_ended(tmp_path):
    history = [version(0, [{"wallet": A, "tier": "POSSIBLE", "vectors": ["correlation"],
                            "evidence": {"correlation_confidence": 0.9}}]),
               version(30, [{"wallet": A, "tier": "WATCH", "peak_tier": "POSSIBLE"}]),
               version(60, [{"wallet": A, "tier": "WATCH", "peak_tier": "POSSIBLE"},
                            {"wallet": B, "tier": "POSSIBLE", "vectors": ["transfer"]}])]
    out = backfill.run_backfill(history, CONFIG, out_dir=tmp_path)
    assert out["versions"] == 3 and out["cases"] == 2
    case = json.loads(store.case_path(A, tmp_path).read_text())
    assert case["evidence"]["amount_correlation"]["status"] == "historical"
    assert case["origin"] == "backfill" and case["roster"]["peak_tier"] == "POSSIBLE"
    assert [t for _, t in case["roster"]["tiers"]] == ["POSSIBLE", "WATCH"]
    days = {p.stem for p in (store.root(tmp_path) / "events").glob("*.jsonl")}
    assert days == {"2025-09-10", "2025-09-11", "2025-09-12"}
    assert store.read_state(tmp_path)["roster_computed_at_ms"] == T0 + 60 * H
    assert store.read_index(tmp_path)["run"]["roster_status"] == "backfill"


def test_backfill_refuses_a_casebook_that_already_has_cases(tmp_path):
    history = [version(0, [{"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]}])]
    backfill.run_backfill(history, CONFIG, out_dir=tmp_path)
    with pytest.raises(backfill.NotEmpty):
        backfill.run_backfill(history, CONFIG, out_dir=tmp_path)
    assert backfill.run_backfill(history, CONFIG, out_dir=tmp_path, rebuild=True)["cases"] == 1


def test_backfill_survives_an_old_and_a_broken_version(tmp_path):
    history = [version(0, [{"wallet": A, "tier": "POSSIBLE", "vectors": None, "evidence": None}],
                       computed=False),
               (T0 + 5 * H, "broken", None),
               (T0 + 6 * H, "list", ["not", "a", "roster"]),
               version(7, [{"wallet": A, "tier": "POSSIBLE"}])]
    out = backfill.run_backfill(history, CONFIG, out_dir=tmp_path)
    assert out["versions"] == 2 and out["skipped"] == 2


def test_backfill_skips_a_version_older_than_the_last(tmp_path):
    history = [version(10, [{"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]}]), version(5, [])]
    assert backfill.run_backfill(history, CONFIG, out_dir=tmp_path)["versions"] == 1


def test_an_empty_history_writes_nothing(tmp_path):
    assert backfill.run_backfill([], CONFIG, out_dir=tmp_path)["cases"] == 0
    assert not (store.root(tmp_path) / "latest.json").exists()


def test_parse_log_reads_sha_and_seconds():
    sha = "a" * 40
    assert backfill.parse_log(f"{sha} 1757500000\n\nnot a line\n{sha} x\n") == [(sha, 1_757_500_000_000)]


def test_backfill_voids_what_only_the_2026_09_10_rosters_reported(tmp_path):
    day = 1_789_027_200_000           # 2026-09-10T08:00:00Z, before that day's fixes
    history = [(day, "v0", {"computed_at": iso(day), "wallets": [
                   {"wallet": B, "tier": "POSSIBLE", "vectors": ["transfer"]}]}),
               (day + 50 * H, "v1", {"computed_at": iso(day + 50 * H), "wallets": [
                   {"wallet": B, "tier": "WATCH"}]}),
               (day + 80 * H, "v2", {"computed_at": iso(day + 80 * H), "wallets": [   # absent 24 h+
                   {"wallet": B, "tier": "WATCH"}]})]
    backfill.run_backfill(history, CONFIG, out_dir=tmp_path)
    entry = json.loads(store.case_path(B, tmp_path).read_text())["evidence"]["direct_transfer"]
    assert entry["status"] == "historical"
    assert entry["invalid_reason"].startswith("reported only by the 2026-09-10 rosters")
