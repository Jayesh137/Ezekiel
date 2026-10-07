"""Phase 2 readiness: the self-recall check (spec §14 acceptance 4) and the gate the
Phase 2 routine runs before it builds anything."""

import json
from datetime import UTC, datetime

import pytest

from scripts import check_phase2_ready as gate
from src.study import archive, assemble, readiness, records
from tests.test_study_assemble import JUNE, T, history, stranger_rows

DAY, HOUR = records.DAY_MS, records.HOUR_MS
NOW = readiness.PHASE1_LIVE_MS + 6 * DAY  # 2026-10-13, six days after Phase 1 went live
A, B = "0x" + "a" * 40, "0x" + "b" * 40


def ms(*args) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)


def iso(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, UTC).isoformat()


def his():
    # Seven months (June to December): leaving any one out still leaves his six self
    # windows, the self basis's bar.
    return history(T, JUNE, 210)


def test_recent_months_are_the_whole_months_before_now():
    assert readiness.recent_months(ms(2026, 10, 12)) == ["2026-08", "2026-09"]
    assert readiness.recent_months(ms(2027, 1, 5)) == ["2026-11", "2026-12"]
    assert readiness.recent_months(ms(2027, 1, 5), n=1) == ["2026-12"]


def test_his_own_months_read_for_once_the_panels_are_calibrated():
    got = readiness.self_recall(his(), stranger_rows(200), [], ["2026-10", "2026-11"])
    assert [r["verdict"] for r in got] == ["for", "for"]
    assert all(r["tests"]["T1"] == "for" and r["basis"] == "self_only" for r in got)
    assert readiness.recall_ok(got)


def test_each_month_is_left_out_of_what_it_is_judged_against(monkeypatch):
    # Rule 4: the month judged never calibrates itself — not through his reference and
    # not through his own same-operator windows.
    seen = []
    ref, splits = assemble.his_reference, assemble.self_splits
    monkeypatch.setattr(assemble, "his_reference", lambda d: seen.append(set(d)) or ref(d))
    monkeypatch.setattr(assemble, "self_splits",
                        lambda d, r: seen.append(set(d)) or splits(d, r))
    readiness.self_recall(his(), stranger_rows(200), [], ["2026-10"])
    assert len(seen) == 2
    assert all(days and not any(d.startswith("2026-10") for d in days) for days in seen)


def test_a_month_that_trades_another_way_is_not_recalled():
    # The check must be able to say no: a month of a maker bot's posting, at another
    # rhythm and in another coin, is not him.
    days = {d: r for d, r in his().items() if not d.startswith("2026-11")}
    days.update(history(T, ms(2026, 11, 1), 30, tif="Alo", cloid="0x1", crossed=False,
                        coins=(("DOGE", "1000"),), gap_ms=60_000))
    got = readiness.self_recall(days, stranger_rows(200), [], ["2026-11"])
    assert got[0]["verdict"] != "for" and got[0]["tests"]["T1"] == "neutral"
    assert not readiness.recall_ok(got)


def test_an_uncalibrated_panel_is_not_a_recall():
    got = readiness.self_recall(his(), stranger_rows(199), [], ["2026-11"])
    assert got[0]["verdict"] == "uncalibrated" and not readiness.recall_ok(got)


def test_a_month_he_did_not_trade_is_counted_neither_way():
    got = readiness.self_recall(his(), stranger_rows(200), [], ["2026-04", "2026-11"])
    assert got[0] == {"month": "2026-04", "verdict": "insufficient", "lr": None, "by": [],
                      "basis": None, "days": 0, "tests": {}}
    assert readiness.recall_ok(got)
    assert not readiness.recall_ok(got[:1])  # nothing judged is no recall
    assert not readiness.recall_ok([])
    assert not readiness.recall_ok([{"verdict": "for"}, {"verdict": "neutral"}])
    assert not readiness.recall_ok([{"verdict": "for"}, {"verdict": "mixed"}])


def latest_doc(now=NOW, **over):
    doc = {"computed_at": iso(now - 2 * HOUR), "studied": 2, "unreadable": [],
           "panels": {"strangers": 200, "family_pairs": 3},
           "wallets": [{"wallet": w, "last_read_ms": now - 3 * HOUR,
                        "families": {"tooling": {"verdict": "uncalibrated"}}} for w in (A, B)]}
    doc.update(over)
    return doc


RECALLED = [{"month": "2026-08", "verdict": "for"}, {"month": "2026-09", "verdict": "for"}]
CENSUS = {"measured": 1, "attempted": 81, "habit_measured": 80}


def report(**over):
    kwargs = {"now_ms": NOW, "latest": latest_doc(), "read_without_coverage": [],
              "unreadable_archives": [], "recall": RECALLED, "false_for": [],
              "census": CENSUS}
    return readiness.readiness(**{**kwargs, **over})


def failing(got):
    return sorted(name for name, c in got["checks"].items() if not c["ok"])


def test_phase_2_is_ready_when_every_check_passes():
    got = report()
    assert got["ready"] and failing(got) == []
    assert set(got["checks"]) == {"study_fresh", "in_production", "records", "strangers",
                                  "self_recall", "no_false_for"}


@pytest.mark.parametrize("over, check", [
    ({"now_ms": readiness.PHASE1_LIVE_MS + 4 * DAY,
      "latest": latest_doc(readiness.PHASE1_LIVE_MS + 4 * DAY)}, "in_production"),
    ({"latest": latest_doc(computed_at=iso(NOW - 14 * HOUR))}, "study_fresh"),
    ({"latest": latest_doc(computed_at="not a time")}, "study_fresh"),
    ({"read_without_coverage": [A]}, "records"),
    ({"unreadable_archives": [A]}, "records"),
    ({"latest": latest_doc(unreadable=[{"wallet": A, "error": "archive"}])}, "records"),
    ({"latest": latest_doc(studied=3)}, "records"),
    ({"latest": latest_doc(panels={"strangers": 199, "family_pairs": 3})}, "strangers"),
    ({"latest": latest_doc(panels={})}, "strangers"),
    ({"recall": [{"month": "2026-09", "verdict": "neutral"}]}, "self_recall"),
    ({"recall": []}, "self_recall"),
    ({"false_for": [{"wallet": A, "verdict": "for", "traits": ["client_ids"]}]},
     "no_false_for"),
])
def test_any_one_failing_check_holds_phase_2_back(over, check):
    got = report(**over)
    assert not got["ready"] and failing(got) == [check]


def test_acceptance_1_and_3_are_reported_and_never_required():
    # Neither measures whether Phase 1 can recognise him: the census vote is Phase 0's,
    # and the family panel only gates T1's against.
    got = report(latest=latest_doc(panels={"strangers": 200, "family_pairs": 0}))
    assert got["ready"]
    assert got["info"]["census"] == CENSUS
    assert got["info"]["family_pairs"] == {"value": 0, "bar": 40}
    assert got["info"]["tooling_verdicts"] == {"uncalibrated": 2}


# The script: what it reads, and how it answers.

def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def covered_day(wallet, day):
    record = records.empty_day(wallet, day, "studied")
    lo = records.day_start_ms(day)
    record["coverage"]["ledger"] = [[lo, lo + DAY]]
    return record


def data_dir(tmp_path, strangers=200):
    data = tmp_path / "data"
    write(data / "study" / "latest.json", latest_doc())
    day = records.day_of(NOW - DAY)
    archive.save_days({day: covered_day(A, day)}, data)
    archive.save_days({day: covered_day(B, day)}, data)
    habits = {row.pop("wallet"): row for row in stranger_rows(strangers)}
    write(data / "execution_program" / "census_state.json",
          {"processed": {}, "hits": {}, "habits": habits})
    write(data / "execution_program" / "census.json", CENSUS)
    return data


@pytest.fixture
def his_data(monkeypatch):
    monkeypatch.setattr(gate.run_study, "his_days", lambda data_dir, target: his())


def run_gate(data, capsys):
    code = gate.main(["--data-dir", str(data), "--now-ms", str(NOW)])
    return code, json.loads(capsys.readouterr().out)


def test_the_gate_answers_ready_from_the_data_it_reads(tmp_path, his_data, capsys):
    code, got = run_gate(data_dir(tmp_path), capsys)
    assert code == 0 and got["ready"]
    assert [r["month"] for r in got["checks"]["self_recall"]["value"]] == ["2026-08", "2026-09"]


def test_the_gate_answers_not_yet_below_the_stranger_bar(tmp_path, his_data, capsys):
    code, got = run_gate(data_dir(tmp_path, strangers=150), capsys)
    assert code == 1 and not got["ready"]
    assert failing(got) == ["self_recall"]  # the study's own summary still says 200


def test_a_wallet_read_with_no_records_holds_it_back(tmp_path, his_data, capsys):
    data = data_dir(tmp_path)
    for path in archive.wallet_dir(B, data).iterdir():
        path.unlink()
    code, got = run_gate(data, capsys)
    assert code == 1 and failing(got) == ["records"]
    assert got["checks"]["records"]["value"]["read_without_coverage"] == [B]


def reading(data, wallet, said):
    doc = json.loads((data / "study" / "latest.json").read_text(encoding="utf-8"))
    for row in doc["wallets"]:
        if row["wallet"] == wallet:
            row["families"]["tooling"]["verdict"] = said
    write(data / "study" / "latest.json", doc)


def test_a_wallet_showing_a_trait_he_never_shows_must_not_read_for(tmp_path, his_data, capsys):
    # Acceptance 3's negative half: a client-id bot reading FOR (or MIXED) holds Phase 2
    # back, whatever test said so.
    data = data_dir(tmp_path)
    reading(data, A, "mixed")
    write(data / "study" / "wallets" / f"{A}.json",
          {"tests": {"T1": {"detail": {"against_traits": ["maker", "client_ids"]}}}})
    code, got = run_gate(data, capsys)
    assert code == 1 and failing(got) == ["no_false_for"]
    assert got["checks"]["no_false_for"]["value"] == [
        {"wallet": A, "verdict": "mixed", "traits": ["client_ids", "maker"]}]


def test_a_for_reading_with_no_trait_against_him_passes(tmp_path, his_data, capsys):
    data = data_dir(tmp_path)
    reading(data, A, "for")
    write(data / "study" / "wallets" / f"{A}.json",
          {"tests": {"T1": {"detail": {"against_traits": []}}}})
    code, got = run_gate(data, capsys)
    assert code == 0 and got["checks"]["no_false_for"]["ok"]


def test_a_for_reading_whose_dossier_cannot_be_read_is_not_cleared(tmp_path, his_data, capsys):
    data = data_dir(tmp_path)
    reading(data, B, "for")
    code, got = run_gate(data, capsys)
    assert code == 1
    assert got["checks"]["no_false_for"]["value"] == [
        {"wallet": B, "verdict": "for", "traits": ["dossier unreadable"]}]


def test_without_the_studys_summary_the_gate_cannot_tell(tmp_path, his_data, capsys):
    assert gate.main(["--data-dir", str(tmp_path / "data"), "--now-ms", str(NOW)]) == 2


@pytest.mark.parametrize("relative", ["study/latest.json",
                                      "execution_program/census_state.json"])
def test_an_unreadable_input_is_a_fault_never_a_no(tmp_path, his_data, capsys, relative):
    # Rule 5: a file that cannot be read is not evidence that Phase 2 is not ready.
    data = data_dir(tmp_path)
    (data / relative).write_text("{not json", encoding="utf-8")
    assert gate.main(["--data-dir", str(data), "--now-ms", str(NOW)]) == 2


def test_without_his_history_the_gate_cannot_tell(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate.run_study, "his_days", lambda data_dir, target: {})
    assert gate.main(["--data-dir", str(data_dir(tmp_path)), "--now-ms", str(NOW)]) == 2


def test_the_gate_writes_nothing(tmp_path, his_data, capsys):
    data = data_dir(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()}
    run_gate(data, capsys)
    assert {p: p.stat().st_mtime_ns for p in data.rglob("*") if p.is_file()} == before
