"""The VM job map must mirror the workflows and name only real scripts.

The GitHub workflows stay the source of truth for which detectors run in each job
during the migration. This guards the VM runner against silently drifting from
them: a detector added to a workflow but not to deploy/vm/jobs.py would run in
Actions and not on the VM (or vice-versa), which is exactly the kind of coverage
gap this project keeps paying for.
"""

import re
from pathlib import Path

from deploy.vm import jobs
from scripts.keep_schedule import SCHEDULE

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _primary(argv):
    """The token that identifies a command: its script/module path, or '-c'."""
    if len(argv) >= 2 and argv[0] == "python":
        return argv[1]
    return argv[0]


def _workflow_commands(name):
    text = (WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        m = re.search(r"run:\s*(python|pip)\b(.*)", line.strip())
        if not m:
            continue
        rest = m.group(2)
        if m.group(1) == "pip":
            out.append("pip")
            continue
        # First non-flag token after python is the script/module, or -c.
        tokens = rest.split()
        if tokens and tokens[0] == "-c":
            out.append("-c")
        elif tokens:
            out.append(tokens[0])
    return out


def _dropped(primary):
    return any(primary.startswith(d) for d in jobs.DROPPED_ON_VM)


def test_every_vm_step_names_a_real_file():
    for job in jobs.JOBS.values():
        for argv, _timeout in job["steps"]:
            primary = _primary(argv)
            if primary == "-c":
                continue
            assert (ROOT / primary).exists(), f"VM step references missing file: {primary}"


def test_every_vm_step_has_a_positive_timeout():
    for job in jobs.JOBS.values():
        for argv, timeout in job["steps"]:
            assert isinstance(timeout, int) and timeout > 0, argv


def test_vm_jobs_mirror_the_workflow_detector_sequences():
    for name, job in jobs.JOBS.items():
        workflow = [c for c in _workflow_commands(name) if not _dropped(c)]
        vm = [_primary(argv) for argv, _ in job["steps"]]
        assert vm == workflow, (f"{name}: VM job drifted from the workflow.\n"
                                f"  workflow: {workflow}\n  vm:       {vm}")


def test_the_study_runs_on_the_vm_as_the_workflow_and_the_keeper_have_it():
    """A cutover replaces the keeper, the PC dispatcher and the relay, so a
    scheduled workflow with no VM job simply stops. The mirror test above walks
    JOBS and never the workflows, so it could not notice the study missing."""
    job = jobs.JOBS["study"]
    keeper = next(j for j in SCHEDULE if j["file"] == "study.yml")
    assert job["interval_seconds"] == keeper["minutes"] * 60
    workflow = (WORKFLOWS / "study.yml").read_text(encoding="utf-8")
    step = workflow.split("name: Study the candidates", 1)[1].split("- name:", 1)[0]
    budget = int(re.search(r"timeout-minutes:\s*(\d+)", step).group(1)) * 60
    assert [(_primary(argv), timeout) for argv, timeout in job["steps"]] == [
        ("scripts/run_study.py", budget)]


def test_intervals_are_present_and_ordered_fastest_first():
    intervals = {name: job["interval_seconds"] for name, job in jobs.JOBS.items()}
    assert intervals["watch"] < intervals["collect"] <= intervals["trace"] < intervals["scan"]
    assert all(v > 0 for v in intervals.values())


def test_run_module_imports_and_covers_every_job_with_a_commit_message():
    from deploy.vm import run
    for name in jobs.JOBS:
        assert name in run.COMMIT_MESSAGES, f"no commit message for job {name}"
