#!/usr/bin/env bash
# Idempotent setup for the Ezekiel always-on VM. Safe to re-run after a pull.
#
#   sudo EZEKIEL_USER=$USER REPO_URL=git@github.com:Jayesh137/Ezekiel.git \
#        bash deploy/vm/install.sh
#
# It installs system deps, the venv, and one systemd timer per job (intervals are
# read from deploy/vm/jobs.py, the single source of truth). Timers are generated,
# not hand-written, so adding a job needs no edit here. The .env is created empty
# on first run for you to fill; nothing schedules until you enable the timers,
# which this script does last and reports.
set -euo pipefail

EZEKIEL_DIR="${EZEKIEL_DIR:-/opt/ezekiel}"
EZEKIEL_USER="${EZEKIEL_USER:-$(id -un)}"
REPO_URL="${REPO_URL:-git@github.com:Jayesh137/Ezekiel.git}"
BRANCH="${BRANCH:-main}"

echo "== Ezekiel VM install =="
echo "dir=$EZEKIEL_DIR user=$EZEKIEL_USER repo=$REPO_URL branch=$BRANCH"

if command -v apt-get >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq python3-venv python3-pip git
fi

if [ ! -d "$EZEKIEL_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$EZEKIEL_DIR"
else
  git -C "$EZEKIEL_DIR" fetch origin "$BRANCH"
  git -C "$EZEKIEL_DIR" checkout "$BRANCH"
  git -C "$EZEKIEL_DIR" pull --ff-only origin "$BRANCH" || true
fi
chown -R "$EZEKIEL_USER":"$EZEKIEL_USER" "$EZEKIEL_DIR"

# Virtualenv + dependencies (both requirement sets; analyze uses the analysis one).
sudo -u "$EZEKIEL_USER" python3 -m venv "$EZEKIEL_DIR/.venv"
sudo -u "$EZEKIEL_USER" "$EZEKIEL_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$EZEKIEL_USER" "$EZEKIEL_DIR/.venv/bin/pip" install --quiet \
  -r "$EZEKIEL_DIR/requirements.txt" -r "$EZEKIEL_DIR/requirements-analysis.txt"

mkdir -p "$EZEKIEL_DIR/data/.local"
chown -R "$EZEKIEL_USER":"$EZEKIEL_USER" "$EZEKIEL_DIR/data/.local"

if [ ! -f "$EZEKIEL_DIR/.env" ]; then
  cat > "$EZEKIEL_DIR/.env" <<'ENV'
# Fill these in, then: sudo systemctl restart ezekiel-*.timer
# The same secrets the GitHub repo used. An unset one disables that channel.
ETHERSCAN_API_KEY=
NTFY_TOPIC=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
PYTHONUNBUFFERED=1
ENV
  chown "$EZEKIEL_USER":"$EZEKIEL_USER" "$EZEKIEL_DIR/.env"
  chmod 600 "$EZEKIEL_DIR/.env"
  echo "WROTE $EZEKIEL_DIR/.env — fill in the secrets before the timers do useful work."
fi

# Install the service template with paths substituted.
sed -e "s#EZEKIEL_DIR#$EZEKIEL_DIR#g" -e "s#EZEKIEL_USER#$EZEKIEL_USER#g" \
  "$EZEKIEL_DIR/deploy/vm/systemd/ezekiel@.service" > /etc/systemd/system/ezekiel@.service

# Generate one timer per job from jobs.py (intervals live only there).
"$EZEKIEL_DIR/.venv/bin/python" - "$EZEKIEL_DIR" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from deploy.vm.jobs import JOBS
out = Path("/etc/systemd/system")
for name, job in JOBS.items():
    secs = int(job["interval_seconds"])
    (out / f"ezekiel-{name}.timer").write_text(f"""[Unit]
Description=Ezekiel {name} timer

[Timer]
OnBootSec=120
OnUnitActiveSec={secs}
AccuracySec=30
Unit=ezekiel@{name}.service

[Install]
WantedBy=timers.target
""")
    print(f"timer ezekiel-{name}.timer every {secs}s")
PY

systemctl daemon-reload
for timer in /etc/systemd/system/ezekiel-*.timer; do
  systemctl enable --now "$(basename "$timer")"
done

echo "== installed =="
systemctl list-timers 'ezekiel-*' --no-pager || true
echo "Logs:  journalctl -u 'ezekiel@*' -f"
echo "Manual run:  sudo -u $EZEKIEL_USER $EZEKIEL_DIR/.venv/bin/python $EZEKIEL_DIR/deploy/vm/run.py watch"
