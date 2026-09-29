"""Card-free always-on host: run the pipeline as a loop on a Hugging Face Space.

A free HF Space (CPU Basic, no credit card) runs this container indefinitely as
long as it is not left idle. It reuses the SAME job definitions and run logic as
the VM scaffolding (`deploy/vm/jobs.py`, `deploy/vm/run.py`), so there is one code
path, not two — the drift-guard test covers it. State is committed back to the
GitHub repo each run, because a free Space's disk is not persistent.

Why a loop and a health server together:
- The loop runs each job when its interval is due, one at a time (the single-writer
  model the VM uses via flock; here it is one thread, so strictly serial and safe).
- HF suspends a free Space that receives no traffic, so a tiny HTTP health endpoint
  is served on $PORT for an external keep-alive to ping (see the runbook). The
  endpoint also reports last-run times so the ping doubles as a liveness check.
"""

import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy.vm import run  # noqa: E402
from deploy.vm.jobs import JOBS  # noqa: E402

_STATE = {"started": time.time(), "last_run": {}, "last_error": {}}


def _health_server():
    port = int(os.environ.get("PORT", 7860))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"service": "ezekiel", "uptime_s": round(time.time() - _STATE["started"]),
                               "last_run": _STATE["last_run"], "last_error": _STATE["last_error"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    http.server.HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def scheduler(tick_seconds=30, run_job=None):
    """Run each job when due, oldest-interval first, one at a time."""
    run_job = run_job or run.run_job
    last = {}
    order = sorted(JOBS, key=lambda name: JOBS[name]["interval_seconds"])
    while True:
        now = time.time()
        for name in order:
            if now - last.get(name, 0) >= JOBS[name]["interval_seconds"]:
                last[name] = now
                try:
                    run_job(name)
                    _STATE["last_run"][name] = int(time.time())
                except Exception as exc:  # noqa: BLE001 - one bad job must not stop the loop
                    _STATE["last_error"][name] = f"{type(exc).__name__}: {exc}"[:200]
                    print(f"[loop] job {name} failed: {exc}", flush=True)
        time.sleep(tick_seconds)


def main():
    threading.Thread(target=_health_server, daemon=True).start()
    print(f"[loop] health server up; scheduling {list(JOBS)}", flush=True)
    scheduler()


if __name__ == "__main__":
    main()
