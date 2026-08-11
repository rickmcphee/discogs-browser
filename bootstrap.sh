#!/usr/bin/env bash
# This is the routine update/redeploy path -- it must never destroy the
# Postgres data volume (docker-compose down -v, rm -rf postgres-data, etc.).
# Any such reset stays a separate, deliberate action a sysadmin runs by hand,
# never something this script does as a side effect of a normal re-run.
set -e

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
