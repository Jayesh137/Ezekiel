"""The card-free Space loop reuses the shared job definitions and run logic."""

from deploy.space import loop
from deploy.vm.jobs import JOBS


def test_loop_schedules_every_defined_job_via_the_shared_runner():
    ran = []
    # Fake run_job records calls; stop after one full pass by raising from sleep.
    orig_sleep = loop.time.sleep
    calls = {"n": 0}

    def fake_run(name):
        ran.append(name)

    def stop_after_first_tick(_seconds):
        calls["n"] += 1
        raise KeyboardInterrupt

    loop.time.sleep = stop_after_first_tick
    try:
        try:
            loop.scheduler(tick_seconds=0, run_job=fake_run)
        except KeyboardInterrupt:
            pass
    finally:
        loop.time.sleep = orig_sleep
    # Every job is due on the first tick (last-run defaults to 0), so all run once.
    assert set(ran) == set(JOBS)


def test_a_failing_job_does_not_stop_the_loop():
    ran = []

    def flaky_run(name):
        ran.append(name)
        if name == sorted(JOBS, key=lambda n: JOBS[n]["interval_seconds"])[0]:
            raise RuntimeError("boom")

    orig_sleep = loop.time.sleep

    def stop(_seconds):
        raise KeyboardInterrupt

    loop.time.sleep = stop
    try:
        try:
            loop.scheduler(tick_seconds=0, run_job=flaky_run)
        except KeyboardInterrupt:
            pass
    finally:
        loop.time.sleep = orig_sleep
    # The failing first job did not prevent the rest from running.
    assert set(ran) == set(JOBS)
