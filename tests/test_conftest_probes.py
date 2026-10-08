"""The backstop that fails a test writing into the real data/ (tests/conftest.py).

The candidate study tree is probed by directory mtimes, not by walking every file,
on the premise that each of its writers writes atomically (a temp file in the
target's directory, then os.replace) or deletes. These pin that premise on the OS
the suite runs on: if a directory's mtime ever stopped moving for those writes, the
probe would go blind, and this file fails first.
"""

import time

from src import utils
from src.study import archive
from tests.conftest import _DIR_PROBES, _PROBES, REAL_DATA_DIR, _dir_signature


def tree(tmp_path):
    root = tmp_path / "study"
    (root / "archive" / "0xabc").mkdir(parents=True)
    utils.atomic_write_json(root / "archive" / "0xabc" / "2026-10-01.json", {})
    return root


def changes(root, write) -> bool:
    before = _dir_signature(root)
    time.sleep(0.05)  # coarse filesystem clocks
    write()
    return _dir_signature(root) != before


def test_an_atomic_rewrite_of_an_existing_record_is_seen(tmp_path):
    root = tree(tmp_path)
    assert changes(root, lambda: utils.atomic_write_json(
        root / "archive" / "0xabc" / "2026-10-01.json", {"fills": 1}))


def test_a_new_record_the_studys_own_writer_makes_is_seen(tmp_path):
    root = tree(tmp_path)
    assert changes(root, lambda: archive.write_compact(
        root / "archive" / "0xabc" / "2026-10-02.json", {"fills": 1}))


def test_a_new_wallet_directory_and_the_summary_are_seen(tmp_path):
    root = tree(tmp_path)
    assert changes(root, lambda: utils.save_latest(str(root), {"studied": 1}))
    assert changes(root, lambda: utils.atomic_write_json(
        root / "archive" / "0xdef" / "2026-10-01.json", {}))


def test_a_deleted_record_is_seen(tmp_path):
    root = tree(tmp_path)
    assert changes(root, lambda: (root / "archive" / "0xabc" / "2026-10-01.json").unlink())


def test_an_absent_tree_has_no_signature(tmp_path):
    assert _dir_signature(tmp_path / "nothing") is None


def test_the_casebooks_own_writes_are_seen(tmp_path):
    # src/casebook/store.py rewrites case files and appends to a day's event file
    # by replacing it whole, so the casebook can be probed by directory too.
    from src.casebook import store
    case = {"schema": "casebook-case/1", "address": "0x" + "a" * 40, "evidence": {}}
    store.write_cases({case["address"]: case}, tmp_path)
    event = {"at": "2026-10-08T01:00:00Z", "address": case["address"], "kind": "k",
             "origin": "live", "detail": {}}
    store.append_events([event], tmp_path)
    root = store.root(tmp_path)
    assert changes(root, lambda: store.write_cases({case["address"]: {**case, "x": 1}}, tmp_path))
    assert changes(root, lambda: store.append_events([event], tmp_path))


def test_the_study_tree_is_probed_by_directory_and_nothing_else_moved():
    assert _DIR_PROBES == {"the candidate study tree": REAL_DATA_DIR / "study",
                           "the casebook": REAL_DATA_DIR / "casebook"}
    assert REAL_DATA_DIR / "study" not in _PROBES.values()
    assert REAL_DATA_DIR / "casebook" not in _PROBES.values()
    # The transfer records are rewritten in place, which no directory mtime shows.
    assert _PROBES["collected transfer records"] == REAL_DATA_DIR / "transfers"
