# tests/test_alert_shards.py
"""Delivery health survives two workflows recording at once.

`_record_delivery` used to be a read-modify-write on one shared file: read
data/alerts/latest.json, append a row, write it back. Every committing
workflow shared the `data-commit` concurrency group, so two of them never
overlapped and that was safe. Giving watch.yml its own group on 2026-09-12
removed that protection, and the push step resolves a conflict with
`git rebase -X theirs` — which takes the pushing run's file WHOLE. The other
run's row, and its counters, are discarded.

That is not a cosmetic loss. Every counter is computed from the copy of the
file read at job start:

    run A (watch)   base healthy=true  -> send FAILS -> writes healthy=false
    run B (trace)   base healthy=true  -> suppressed -> writes healthy=true
    B pushes second -> B's file wins   -> A's failure is erased

The dashboard then reads healthy, which is exactly the failure this file
exists to catch: `_record_delivery`'s own docstring says a dead output channel
otherwise looks like a quiet week.

The fix is that each run writes its OWN shard under data/alerts/runs/, which
two runs can never collide on because the filenames differ. latest.json stays
byte-compatible for the dashboard and the ten tests that read it, but it is
now a DERIVED cache: recomputed from the shards on every write. A rollup lost
to a rebase therefore costs nothing — the shards it was computed from are all
still there, and the next write rebuilds it.
"""

import json

import pytest

from src import alerts


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Alerting pointed at a temp data dir. No channel configured, so nothing
    in here can reach the network; conftest sandboxes DATA_DIR as well."""
    monkeypatch.setattr(alerts, "DATA_DIR", tmp_path)
    for var in ("NTFY_TOPIC", "NTFY_INCLUDE_INFO", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", "GITHUB_RUN_ID"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path

CRIT = "[EZEKIEL] CRITICAL: Watched Wallet Touched The Target's World"
INFO = "[EZEKIEL] INFO: Operational Counterparty (33% confidence)"


def _events(*specs):
    """(at, subject, delivered) triples -> event dicts."""
    return [{"at": at, "subject": subj, "delivered": deliv,
             "status": "delivered" if deliv else (
                 "suppressed" if not alerts._health_bearing(subj) else "failed"),
             "reason": None}
            for at, subj, deliv in specs]


# ---------------------------------------------------------------- derive_health

def test_health_is_derived_from_the_events_not_carried_forward():
    got = alerts.derive_health(_events(
        ("2026-09-12T00:00:00+00:00", CRIT, True),
        ("2026-09-12T00:01:00+00:00", CRIT, False),
        ("2026-09-12T00:02:00+00:00", CRIT, False)))
    assert got["healthy"] is False
    assert got["consecutive_failures"] == 2
    assert got["undelivered"] == 2
    assert got["last_failure_at"] == "2026-09-12T00:02:00+00:00"
    assert got["last_success_at"] == "2026-09-12T00:00:00+00:00"


def test_a_delivery_clears_a_standing_failure():
    got = alerts.derive_health(_events(
        ("2026-09-12T00:00:00+00:00", CRIT, False),
        ("2026-09-12T00:01:00+00:00", CRIT, True)))
    assert got["healthy"] is True
    assert got["consecutive_failures"] == 0
    assert got["undelivered"] == 0


def test_a_suppressed_info_is_counted_but_never_touches_health():
    got = alerts.derive_health(_events(
        ("2026-09-12T00:00:00+00:00", CRIT, False),
        ("2026-09-12T00:01:00+00:00", INFO, False),
        ("2026-09-12T00:02:00+00:00", INFO, False)))
    assert got["suppressed"] == 2
    # The standing CRITICAL failure must still be the health verdict.
    assert got["healthy"] is False
    assert got["consecutive_failures"] == 1


def test_events_are_ordered_by_time_whatever_order_the_shards_arrive_in():
    got = alerts.derive_health(_events(
        ("2026-09-12T00:02:00+00:00", CRIT, True),
        ("2026-09-12T00:01:00+00:00", CRIT, False)))
    assert got["healthy"] is True, "the later event decides"
    assert [r["at"] for r in got["recent"]] == [
        "2026-09-12T00:01:00+00:00", "2026-09-12T00:02:00+00:00"]


def test_no_events_at_all_is_not_an_outage():
    got = alerts.derive_health([])
    assert got["healthy"] is True
    assert got["consecutive_failures"] == 0
    assert got["recent"] == []


# ------------------------------------------------------------ concurrent writes

def test_two_runs_recording_at_once_keep_both_rows(wired, monkeypatch):
    """The whole point. Two run ids, two shards, nothing overwritten."""
    monkeypatch.setenv("GITHUB_RUN_ID", "run-A")
    alerts._record_delivery(CRIT, True, "via ntfy")
    monkeypatch.setenv("GITHUB_RUN_ID", "run-B")
    alerts._record_delivery(INFO, False, "withheld")

    shards = sorted((wired / "alerts" / "runs").glob("*.json"))
    assert len(shards) == 2, f"expected one shard per run, got {shards}"

    roll = json.loads((wired / "alerts" / "latest.json").read_text())
    assert {r["subject"] for r in roll["recent"]} == {CRIT, INFO}


def test_a_rollup_discarded_by_a_rebase_is_rebuilt_from_the_shards(wired,
                                                                   monkeypatch):
    """`-X theirs` can throw the cache away; it cannot throw the truth away."""
    monkeypatch.setenv("GITHUB_RUN_ID", "run-A")
    alerts._record_delivery(CRIT, False, "ntfy down")
    before = json.loads((wired / "alerts" / "latest.json").read_text())
    assert before["healthy"] is False

    # What a rebase does to the losing run: the rollup reverts, shards remain.
    (wired / "alerts" / "latest.json").unlink()

    monkeypatch.setenv("GITHUB_RUN_ID", "run-B")
    alerts._record_delivery(INFO, False, "withheld")

    after = json.loads((wired / "alerts" / "latest.json").read_text())
    assert after["healthy"] is False, "run A's failure must survive run B"
    assert after["consecutive_failures"] == 1
    assert {r["subject"] for r in after["recent"]} == {CRIT, INFO}


def test_one_run_sending_twice_keeps_both_in_its_own_shard(wired, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "run-A")
    alerts._record_delivery(CRIT, True, None)
    alerts._record_delivery(INFO, False, None)

    shards = list((wired / "alerts" / "runs").glob("*.json"))
    assert len(shards) == 1, "a run owns one shard and appends to it"
    roll = json.loads((wired / "alerts" / "latest.json").read_text())
    assert len(roll["recent"]) == 2


def test_an_existing_latest_is_migrated_so_history_is_not_lost(wired,
                                                               monkeypatch):
    (wired / "alerts").mkdir(parents=True, exist_ok=True)
    (wired / "alerts" / "latest.json").write_text(json.dumps({
        "healthy": True, "consecutive_failures": 0, "undelivered": 0,
        "suppressed": 8,
        "recent": _events(("2026-09-11T23:31:34+00:00", INFO, False))}))

    monkeypatch.setenv("GITHUB_RUN_ID", "run-new")
    alerts._record_delivery(CRIT, True, None)

    roll = json.loads((wired / "alerts" / "latest.json").read_text())
    subjects = [r["subject"] for r in roll["recent"]]
    assert INFO in subjects, "the pre-shard record must be carried over"
    assert CRIT in subjects


def test_recording_still_never_breaks_alerting(wired, monkeypatch):
    """The never-raise contract, now that there is more to go wrong."""
    monkeypatch.setattr(alerts, "DATA_DIR", wired / "nope" / "\0bad")
    alerts._record_delivery(CRIT, True, None)  # must not raise


# ------------------------------------------------------------------- retention

def test_pruning_drops_old_shards_and_keeps_recent_ones():
    keep = [("2026-09-12T00-00-00-run-new.json", 1.0)]
    drop = [("2026-07-01T00-00-00-run-old.json", 80.0)]
    kept, dropped = alerts.partition_shards_by_age(keep + drop,
                                                   max_age_days=30.0)
    assert [n for n, _ in kept] == ["2026-09-12T00-00-00-run-new.json"]
    assert [n for n, _ in dropped] == ["2026-07-01T00-00-00-run-old.json"]


def test_pruning_also_caps_the_file_count():
    shards = [(f"s{i}.json", float(i)) for i in range(10)]
    kept, dropped = alerts.partition_shards_by_age(shards, max_age_days=365.0,
                                                   max_files=4)
    assert len(kept) == 4 and len(dropped) == 6
    # The four youngest survive: age ascending means s0..s3.
    assert sorted(n for n, _ in kept) == ["s0.json", "s1.json", "s2.json",
                                          "s3.json"]
