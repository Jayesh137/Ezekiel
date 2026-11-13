"""Every committing workflow's rebase must settle two runs pruning one alert shard.

2026-11-13, trace run 43365752896: the run and a concurrent watch each deleted
the same expired `data/alerts/runs/<id>.json` and wrote a shard of their own.
Git read "deleted here, a similar file added there" as a RENAME, and a
rename/delete conflict is a tree conflict `-X theirs` cannot settle — so all
five retries failed and an hour of roster, casebook and dormancy readings was
dropped, on the first day trace could commit again. Replayed in a scratch
remote: with rename detection off (`-X no-renames`) both shards survive and the
pushing run's reading is kept.

This pins the flag, because nothing else would notice it being dropped: the
conflict needs two runs pruning the same file in the same minutes.
"""

from pathlib import Path

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


def _rebase_lines():
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if "git rebase" in stripped and "--abort" not in stripped and not stripped.startswith("#"):
                out.append((path.name, stripped))
    return out


def test_the_committing_workflows_are_found():
    assert len(_rebase_lines()) >= 9


def test_every_rebase_keeps_this_runs_files_and_detects_no_renames():
    for name, line in _rebase_lines():
        assert "-X theirs" in line, (name, line)
        assert "-X no-renames" in line, (name, line)
        assert "--autostash" in line, (name, line)
