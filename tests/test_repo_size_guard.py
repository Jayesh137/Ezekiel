# tests/test_repo_size_guard.py
"""Catch a file outgrowing GitHub BEFORE the push, not in a pre-receive hook.

On 2026-09-12 `data/transfer_graph/latest.json` crossed 100 MiB and every "Trace
Fund Flows" run began failing — the graph was computed, the push was declined,
and the run's whole reading was thrown away. The diagnosis was buried: the push
step retries, so the log holds five identical GH001 rejections and the actual
sentence naming the file scrolls past in the middle of them.

Nothing was watching the size. `test.yml` only runs on pushes touching src/,
tests/ or scripts/, so it never sees a data commit, and a repo that commits its
own output every run has a size budget nobody was tracking. The substrate is the
next one to cross: `data/transfers/ethereum/2026-09-10.json` is 73.1 MiB for one
finished day.

So the check runs in the committing workflows, before the push: a HARD failure
with a readable message at the limit, and a warning band below it so the
operator hears about it while there is still time to act.
"""

from scripts import check_repo_size as rs


def _write(tmp_path, name, mib):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * int(mib * 1024 * 1024))
    return p


def test_a_small_tree_is_clean(tmp_path):
    _write(tmp_path, "data/small.json", 1)
    report = rs.scan(tmp_path)
    assert report["over_limit"] == [] and report["warning"] == []
    assert report["ok"] is True


def test_a_file_over_the_hard_limit_is_reported(tmp_path):
    _write(tmp_path, "data/huge.json", rs.HARD_LIMIT_MIB + 1)
    report = rs.scan(tmp_path)
    assert [f["path"] for f in report["over_limit"]] == ["data/huge.json"]
    assert report["ok"] is False


def test_the_limit_is_MEBIbytes_not_megabytes(tmp_path):
    """The failure looked sudden because 104.23 MB is 99.4 MiB — under the limit.

    A check using decimal MB would have called the file oversized for days while
    pushes still worked, and then been ignored."""
    assert rs.HARD_LIMIT_MIB == 100
    _write(tmp_path, "data/just_under.json", 99.5)
    assert rs.scan(tmp_path)["over_limit"] == []


def test_a_file_in_the_warning_band_is_reported_but_does_not_fail(tmp_path):
    _write(tmp_path, "data/growing.json", rs.WARN_LIMIT_MIB + 1)
    report = rs.scan(tmp_path)
    assert [f["path"] for f in report["warning"]] == ["data/growing.json"]
    assert report["over_limit"] == []
    assert report["ok"] is True


def test_the_warning_band_leaves_real_room_to_act():
    """A warning at 99 MiB is not a warning, it is the failure with extra steps."""
    assert rs.WARN_LIMIT_MIB <= 80


def test_findings_are_ordered_biggest_first(tmp_path):
    _write(tmp_path, "data/a.json", rs.WARN_LIMIT_MIB + 1)
    _write(tmp_path, "data/b.json", rs.WARN_LIMIT_MIB + 5)
    assert [f["path"] for f in rs.scan(tmp_path)["warning"]] == ["data/b.json", "data/a.json"]


def test_the_git_directory_is_never_scanned(tmp_path):
    """Pack files are routinely huge and are not what gets pushed as a blob."""
    _write(tmp_path, ".git/objects/pack/huge.pack", rs.HARD_LIMIT_MIB + 5)
    assert rs.scan(tmp_path)["ok"] is True


def test_node_modules_and_build_output_are_not_scanned(tmp_path):
    for junk in ("node_modules/big.bin", "dashboard/build/big.bin", ".venv/big.bin"):
        _write(tmp_path, junk, rs.HARD_LIMIT_MIB + 2)
    assert rs.scan(tmp_path)["ok"] is True


def test_the_report_names_the_size_in_both_units(tmp_path):
    """The log has to be readable by whoever finds the failed run."""
    _write(tmp_path, "data/huge.json", rs.HARD_LIMIT_MIB + 1)
    f = rs.scan(tmp_path)["over_limit"][0]
    assert f["mib"] > rs.HARD_LIMIT_MIB and f["mb"] > f["mib"]


def test_main_exits_nonzero_only_on_a_hard_violation(tmp_path, capsys):
    _write(tmp_path, "data/growing.json", rs.WARN_LIMIT_MIB + 1)
    assert rs.main([str(tmp_path)]) == 0
    assert "WARNING" in capsys.readouterr().out

    _write(tmp_path, "data/huge.json", rs.HARD_LIMIT_MIB + 1)
    assert rs.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "data/huge.json" in out and "100 MiB" in out
