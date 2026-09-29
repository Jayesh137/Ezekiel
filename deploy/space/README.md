# Card-free always-on host — Hugging Face Space

A free Hugging Face **Space** runs the whole pipeline as a loop with **no credit
card** (email signup only). It reuses the same job definitions and run logic as the
VM scaffolding (`deploy/vm/jobs.py`, `deploy/vm/run.py`) — one code path — and
commits data back to this GitHub repo each run, because a free Space's disk is not
persistent. This gives tighter, more reliable cadence than GitHub Actions' throttled
cron, and (once jobs run here) lets the repo go private.

**Honest caveats, up front.** Spaces are meant for ML demos, so a background
pipeline is off-label; HF *could* suspend it (if so, the GitHub Actions crons are
still there as a fallback — nothing is lost). A free Space also **sleeps after ~48h
with no traffic**, so a keep-alive ping is required (step 5). It needs a GitHub
token with write access to this repo, stored as a Space secret.

## One-time setup (all card-free, ~10 minutes, your part)

1. **Create a Hugging Face account** at huggingface.co — email only, no card.
2. **Create a fine-grained GitHub token** (github.com → Settings → Developer
   settings → Fine-grained tokens): repository access = this repo only, permission
   = Contents: Read and write. Copy it.
3. **Create a Space:** New → Space → SDK = **Docker** → blank/empty → **CPU basic
   (free)**. Into the Space repo put a single file named `Dockerfile` with exactly
   the contents of `deploy/space/Dockerfile` from this repo, and `bootstrap.sh`
   with the contents of `deploy/space/bootstrap.sh` (both are in this folder). Or
   just point me at the Space repo and I will push them.
4. **Add Space secrets** (Space → Settings → Variables and secrets):
   - `GITHUB_TOKEN` = the token from step 2
   - `GITHUB_REPOSITORY` = `Jayesh137/Ezekiel`
   - `ETHERSCAN_API_KEY`, `NTFY_TOPIC` (and optional `TELEGRAM_BOT_TOKEN`,
     `TELEGRAM_CHAT_ID`) — the same values the GitHub repo uses.
   The Space builds and starts the loop automatically.
5. **Keep it awake:** copy the Space's URL and set it as a repo **Actions variable**
   `EZEKIEL_SPACE_URL` (github.com → repo → Settings → Secrets and variables →
   Actions → Variables). The `space-keepalive.yml` workflow then pings it hourly;
   it is a no-op until that variable exists.

Send me the Space URL and I will verify the loop is running and committing (its
health endpoint reports per-job last-run times), then remove the scheduled crons
from GitHub Actions one job at a time (the runbook in `deploy/vm/README.md` cutover
section applies identically) and, once clean, help you make the repo private.

## How it runs

`bootstrap.sh` clones this repo with the token, installs both requirement sets, and
runs `deploy/space/loop.py`, which serves a health endpoint on `$PORT` and runs each
job (`watch` 10m, `collect` 15m, `trace` 30m, `scan` 60m, `analyze` daily) when due,
one at a time, committing data back via `deploy/vm/run.py`'s push logic
(`check_repo_size` + `-X theirs` rebase-retry). Update behaviour by pushing to this
repo and restarting the Space (it re-clones on start; the loop also pulls each run).

## If HF suspends or you outgrow it

Nothing is lost — data lives in this repo and the GitHub Actions crons still exist.
Re-enable them, or move to the VM path (`deploy/vm/`), which is the same jobs on a
real host. This Space path is the card-free option; the VM is the robust one.
