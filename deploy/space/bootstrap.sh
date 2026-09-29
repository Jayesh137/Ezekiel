#!/usr/bin/env bash
# Clone Ezekiel with the injected token, install deps, and run the loop.
# Idempotent: re-clones fresh each container start so the Space runs latest code.
set -euo pipefail

: "${GITHUB_TOKEN:?set GITHUB_TOKEN as a Space secret}"
: "${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY (owner/repo) as a Space secret}"
BRANCH="${EZEKIEL_BRANCH:-main}"
DIR=/app/ezekiel

AUTH_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
rm -rf "$DIR"
git clone --branch "$BRANCH" "$AUTH_URL" "$DIR"
cd "$DIR"
git config user.name "ezekiel-space"
git config user.email "ezekiel-space@users.noreply.github.com"
# Keep the token in the remote so run.py's push works, but never print it.
git remote set-url origin "$AUTH_URL"

pip install --no-cache-dir -r requirements.txt -r requirements-analysis.txt

exec python deploy/space/loop.py
