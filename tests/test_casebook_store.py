"""The casebook's files: atomic, strict about its own cases, never overwriting what it cannot read."""

import json

import pytest

from src.casebook import store

A, B = "0x" + "a" * 40, "0x" + "b" * 40


def case(address, **extra):
    return {"schema": "casebook-case/1", "address": address, "evidence": {}, **extra}


def test_cases_round_trip_and_are_written_only_when_changed(tmp_path):
    cases = {A: case(A, note="x"), B: case(B)}
    assert store.write_cases(cases, tmp_path) == 2
    loaded, unreadable = store.load_cases(tmp_path)
    assert loaded == cases and unreadable == []
    assert store.write_cases(loaded, tmp_path) == 0
    loaded[A]["note"] = "y"
    assert store.write_cases(loaded, tmp_path) == 1


def test_an_unreadable_case_is_reported_and_never_rewritten(tmp_path):
    store.write_cases({A: case(A)}, tmp_path)
    path = store.case_path(A, tmp_path)
    path.write_text('{"schema": "casebook-case/1", "addr')          # truncated
    loaded, unreadable = store.load_cases(tmp_path)
    assert A not in loaded and unreadable[0]["address"] == A
    assert store.write_cases({A: case(A, note="new")}, tmp_path, skip={A}) == 0
    # Defence in depth: even a caller that forgets `skip` cannot overwrite it.
    assert store.write_cases({A: case(A, note="new")}, tmp_path) == 0
    assert path.read_text().endswith('"addr')


def test_a_case_file_naming_another_address_is_unreadable(tmp_path):
    folder = store.root(tmp_path) / "cases"
    folder.mkdir(parents=True)
    (folder / f"{A}.json").write_text(json.dumps(case(B)))
    loaded, unreadable = store.load_cases(tmp_path)
    assert loaded == {} and unreadable[0]["address"] == A


def test_events_append_into_day_files(tmp_path):
    events = [{"at": "2026-10-08T01:00:00Z", "address": A, "kind": "case_opened", "origin": "live", "detail": {}},
              {"at": "2026-10-09T01:00:00Z", "address": B, "kind": "hl_woke", "origin": "live", "detail": {}}]
    assert store.append_events(events, tmp_path) == 2
    store.append_events(events[:1], tmp_path)
    day = store.root(tmp_path) / "events" / "2026-10-08.jsonl"
    assert len(day.read_text().splitlines()) == 2
    assert [e["kind"] for e in store.read_events(tmp_path)] == ["case_opened", "case_opened", "hl_woke"]
    assert [e["kind"] for e in store.read_events(tmp_path, address=B)] == ["hl_woke"]
    assert store.read_events(tmp_path, since="2026-10-09")[0]["address"] == B


def test_a_bad_event_line_is_skipped_on_read_and_kept_on_disk(tmp_path):
    folder = store.root(tmp_path) / "events"
    folder.mkdir(parents=True)
    (folder / "2026-10-08.jsonl").write_text("{broken\n")
    store.append_events([{"at": "2026-10-08T02:00:00Z", "address": A, "kind": "x", "origin": "live",
                          "detail": {}}], tmp_path)
    assert (folder / "2026-10-08.jsonl").read_text().startswith("{broken\n")
    assert [e["kind"] for e in store.read_events(tmp_path)] == ["x"]


def test_state_and_index_are_lenient_to_read(tmp_path):
    assert store.read_state(tmp_path) == {}
    assert store.read_index(tmp_path) is None
    store.write_state({"roster_computed_at_ms": 5}, tmp_path)
    store.write_index({"cases": []}, tmp_path)
    assert store.read_state(tmp_path) == {"roster_computed_at_ms": 5}
    assert store.read_index(tmp_path) == {"cases": []}
    (store.root(tmp_path) / "latest.json").write_text("{")
    assert store.read_index(tmp_path) is None


def test_clear_removes_only_the_casebooks_own_files(tmp_path):
    store.write_cases({A: case(A)}, tmp_path)
    store.write_state({}, tmp_path)
    readme = store.root(tmp_path) / "README.md"
    readme.write_text("keep me")
    store.clear(tmp_path)
    assert readme.exists() and not (store.root(tmp_path) / "cases").exists()
    assert not (store.root(tmp_path) / "state.json").exists()


def test_no_temporary_files_are_left_behind(tmp_path):
    store.write_cases({A: case(A)}, tmp_path)
    store.append_events([{"at": "2026-10-08T01:00:00Z", "address": A, "kind": "k", "origin": "live",
                          "detail": {}}], tmp_path)
    assert not list(store.root(tmp_path).rglob(".*.tmp"))


def test_case_path_refuses_a_non_address(tmp_path):
    with pytest.raises(ValueError):
        store.case_path("../escape", tmp_path)


def test_the_default_root_is_read_at_call_time(tmp_path, monkeypatch):
    from src import utils
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path / "elsewhere")
    assert store.root() == tmp_path / "elsewhere" / "casebook"
