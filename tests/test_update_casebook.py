"""The workflow step end to end on a fixture tree: merge, probe, score, write, alert (spec §4)."""

import json

from scripts import update_casebook as update
from src.casebook import store

T = "0x45d26f28196d226497130c4bac709d808fed4029"
SELF = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
A, B = "0x" + "a" * 40, "0x" + "b" * 40
CONFIG = {"target_wallet": T, "known_self_wallets": [SELF]}
DAY = 86_400_000
NOW = 1_791_419_654_475


def write_roster(data_dir, computed_at, rows):
    (data_dir / "roster").mkdir(parents=True, exist_ok=True)
    (data_dir / "roster" / "latest.json").write_text(json.dumps({"computed_at": computed_at, "wallets": rows}))


def portfolio(value, week=0.0, month=0.0, born=NOW - 300 * DAY):
    history = [[born, str(value)], [NOW, str(value)]]
    return [[name, {"accountValueHistory": history, "pnlHistory": [], "vlm": str(v)}]
            for name, v in (("day", 0.0), ("week", week), ("month", month), ("allTime", month))]


class Fetch:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, body):
        self.calls.append(body["user"])
        return self.answers.get(body["user"], {"ok": True, "data": portfolio(0.0)})


def run(data_dir, fetch=None, now=NOW, **kw):
    return update.run(CONFIG, data_dir=data_dir, out_dir=data_dir, fetch=fetch or Fetch({}),
                      now_ms=now, alerts_on=False, **kw)


LEAD = {"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]}


def test_a_first_run_opens_cases_probes_them_and_writes_an_index(tmp_path):
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [LEAD])
    fetch = Fetch({A: {"ok": True, "data": portfolio(5e6, week=1e5, month=1e6)}})
    out = run(tmp_path, fetch)
    assert out["roster_status"] == "consumed" and out["exit_code"] == 0
    assert sorted(fetch.calls) == sorted([A, SELF])
    case = json.loads(store.case_path(A, tmp_path).read_text())
    assert case["hl"]["total_value"] == 5e6 and case["evidence"]["direct_transfer"]["live"] is True
    index = store.read_index(tmp_path)
    assert [r["address"] for r in index["cases"] if r["rank"]] == [A]
    assert store.read_state(tmp_path)["roster_computed_at_ms"]
    assert any(e["kind"] == "case_opened" for e in store.read_events(tmp_path))


def test_a_stale_roster_lapses_nothing(tmp_path):
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [LEAD])
    run(tmp_path)
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [])       # same computed_at, rows gone
    out = run(tmp_path, now=NOW + 3 * DAY)
    assert out["roster_status"].startswith("stale")
    entry = json.loads(store.case_path(A, tmp_path).read_text())["evidence"]["direct_transfer"]
    assert entry["status"] == "current" and entry["absent_since"] is None


def test_an_unreadable_roster_is_loud_and_lapses_nothing(tmp_path):
    (tmp_path / "roster").mkdir()
    (tmp_path / "roster" / "latest.json").write_text("{")
    out = run(tmp_path)
    assert out["roster_status"].startswith("unreadable") and out["exit_code"] == 1


def test_an_unreadable_case_file_blocks_only_itself(tmp_path):
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [LEAD, {**LEAD, "wallet": B}])
    run(tmp_path)
    store.case_path(A, tmp_path).write_text("{broken")
    write_roster(tmp_path, "2026-10-09T00:00:00+00:00",
                 [{**LEAD, "tier": "PROBABLE"}, {**LEAD, "wallet": B, "tier": "PROBABLE"}])
    fetch = Fetch({})
    out = run(tmp_path, fetch, now=NOW + DAY)
    assert store.case_path(A, tmp_path).read_text() == "{broken"
    assert json.loads(store.case_path(B, tmp_path).read_text())["roster"]["tier"] == "PROBABLE"
    assert A not in fetch.calls and out["exit_code"] == 1
    assert out["unreadable_cases"][0]["address"] == A


def test_a_case_not_due_is_not_read(tmp_path):
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [LEAD])
    run(tmp_path)                                        # everything probed once
    fetch = Fetch({})
    run(tmp_path, fetch, now=NOW + DAY // 4)              # six hours later nothing is due
    assert fetch.calls == []


def test_a_budget_refusal_is_not_a_failed_probe(tmp_path):
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [LEAD])

    def refusing(body):
        from src.hl_budget import current_budget
        current_budget().stopped_reason = "time_budget"
        return {"ok": False, "data": None, "error": "time_budget"}

    out = run(tmp_path, refusing)
    assert out["probes"]["failed"] == 0 and out["probes"]["stopped"] == "time_budget"
    assert "probed_at" not in json.loads(store.case_path(A, tmp_path).read_text())["hl"]


def test_a_failed_probe_is_counted_and_retried_later(tmp_path):
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [LEAD])
    out = run(tmp_path, Fetch({A: {"ok": False, "data": None, "error": "HTTP 500"}}))
    assert out["probes"]["failed"] == 1
    hl = json.loads(store.case_path(A, tmp_path).read_text())["hl"]
    assert hl["probe_ok"] is False and hl["probe_error"] == "HTTP 500"
    assert A in update.probe_order(*_cases_scores(tmp_path), NOW + 2 * 3_600_000)


def _cases_scores(data_dir):
    from src.casebook import score
    cases, _ = store.load_cases(data_dir)
    return cases, score.score_all(cases, {T, SELF})


def test_wake_alerts_go_to_eligible_suspects_with_the_right_severity(tmp_path):
    lead = {"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer", "linkage"],
            "evidence": {"shared_private_deposit_address": {"sentinel": B, "usd": 5e5}}}
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00", [lead])
    run(tmp_path, Fetch({A: {"ok": True, "data": portfolio(0.0)}}))
    (tmp_path / "dormancy").mkdir()
    (tmp_path / "dormancy" / "latest.json").write_text(json.dumps(
        {"computed_at": "2026-10-09T00:00:00+00:00", "dormancy": {"unusual": True}}))
    write_roster(tmp_path, "2026-10-09T00:00:00+00:00", [lead])
    sent = []
    fetch = Fetch({A: {"ok": True, "data": portfolio(3e6, week=1e5, month=1e5, born=NOW - DAY)}})
    out = update.run(CONFIG, data_dir=tmp_path, out_dir=tmp_path, fetch=fetch, now_ms=NOW + DAY,
                     alerts_on=True, send=lambda *a: sent.append(a) or True)
    assert [(a[0], a[1], a[4]) for a in sent] == [(A, "hl_opened", True)]
    assert out["alerts"] == 1


def test_main_dry_run_writes_only_under_the_given_directory(tmp_path, monkeypatch):
    from src import utils
    data = tmp_path / "data"
    write_roster(data, "2026-10-08T00:00:00+00:00", [LEAD])
    monkeypatch.setattr(utils, "DATA_DIR", data)
    monkeypatch.setattr(utils, "load_config", lambda: CONFIG)
    assert update.main(["--dry-run", str(tmp_path / "scratch"), "--no-probe"]) == 0
    assert (tmp_path / "scratch" / "casebook" / "latest.json").exists()
    assert not (data / "casebook").exists()


def test_the_casebook_step_follows_the_roster_in_trace():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "trace.yml").read_text(
        encoding="utf-8")
    assert text.index("python src/roster.py") < text.index("python scripts/update_casebook.py")


def test_items_resting_on_a_measured_service_are_invalidated_by_the_run(tmp_path):
    funder = "0x" + "f9" * 20
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00",
                 [{"wallet": A, "tier": "POSSIBLE", "vectors": ["linkage"],
                   "evidence": {"shared_first_funder": funder}}])
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "address_activity.json").write_text(json.dumps(
        {f"arbitrum:{funder}": {"txs": 2_282_986, "token_transfers": 1, "is_contract": False}}))
    run(tmp_path, probe=False)
    entry = json.loads(store.case_path(A, tmp_path).read_text())["evidence"]["quiet_first_funder"]
    assert "2,282,986" in entry["invalid_reason"]


def test_a_new_measurement_invalidates_even_when_the_roster_is_stale(tmp_path):
    funder = "0x" + "f9" * 20
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00",
                 [{"wallet": A, "tier": "POSSIBLE", "vectors": ["linkage"],
                   "evidence": {"shared_first_funder": funder}}])
    run(tmp_path, probe=False)
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "address_activity.json").write_text(json.dumps(
        {f"arbitrum:{funder}": {"txs": 2_282_986, "token_transfers": 1, "is_contract": False}}))
    out = run(tmp_path, probe=False, now=NOW + DAY)
    assert out["roster_status"].startswith("stale")
    entry = json.loads(store.case_path(A, tmp_path).read_text())["evidence"]["quiet_first_funder"]
    assert "2,282,986" in entry["invalid_reason"]


def test_an_unreadable_activity_table_keeps_what_was_measured(tmp_path):
    funder = "0x" + "f9" * 20
    write_roster(tmp_path, "2026-10-08T00:00:00+00:00",
                 [{"wallet": A, "tier": "POSSIBLE", "vectors": ["linkage"],
                   "evidence": {"shared_first_funder": funder}}])
    (tmp_path / "labels").mkdir()
    table = tmp_path / "labels" / "address_activity.json"
    table.write_text(json.dumps(
        {f"arbitrum:{funder}": {"txs": 2_282_986, "token_transfers": 1, "is_contract": False}}))
    run(tmp_path, probe=False)
    table.write_text("{not json")
    out = run(tmp_path, probe=False, now=NOW + DAY)
    assert out["activity"].startswith("unreadable")
    entry = json.loads(store.case_path(A, tmp_path).read_text())["evidence"]["quiet_first_funder"]
    assert "2,282,986" in entry["invalid_reason"]
