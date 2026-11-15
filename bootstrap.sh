#!/usr/bin/env bash
# This is the routine update/redeploy path -- it must never destroy the
# Postgres data volume (docker-compose down -v, rm -rf postgres-data, etc.).
# Any such reset stays a separate, deliberate action a sysadmin runs by hand,
# never something this script does as a side effect of a normal re-run.
set -e

# A scheduled task (DSM Task Scheduler, cron) often runs as a different user
# than the one that owns the checkout, and git refuses to touch a repo owned
# by someone else ("detected dubious ownership"). Trust this checkout for the
# git calls below via the environment rather than `git config --global`, so
# the run leaves no persistent change to the caller's git config.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="$REPO_DIR"

echo "==> Pulling latest changes..."
git pull

echo "==> Creating workspace directory..."
mkdir -p workspace

# docker-compose.yml has no other source for APP_VERSION; without this the
# self-hosted image always resolves to "dev" (no .git in the build context,
# no git binary in the image). Computed after git pull so it names the
# commit that's actually being built.
#
# The .dirty suffix is not cosmetic. git pull succeeds with non-conflicting
# local changes, there is no .dockerignore, and the Dockerfile's COPY . .
# takes the whole build context -- so a modified or untracked file gets baked
# into the image while the version string names a clean commit that does not
# describe it. --porcelain covers untracked files too and ignores anything in
# .gitignore, so workspace/, postgres-data/ and .env do not trip it. Marked
# rather than rejected: this is the routine redeploy path and an operator with
# a local edit should still be able to deploy, just not silently mislabel it.
APP_VERSION="$(git log -1 --format=%cd --date=format:%Y.%m.%d)+$(git rev-parse --short=7 HEAD)"
if [ -n "$(git status --porcelain)" ]; then
  APP_VERSION="${APP_VERSION}.dirty"
  echo "==> WARNING: working tree is dirty; building as ${APP_VERSION}"
fi
export APP_VERSION

echo "==> Building Docker images..."
docker-compose build

echo "==> Starting containers..."
docker-compose up -d

echo ""
echo "Done. Open http://<host-ip>:8080"
