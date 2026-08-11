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
export APP_VERSION="$(git log -1 --format=%cd --date=format:%Y.%m.%d)+$(git rev-parse --short HEAD)"

echo "==> Building Docker images..."
docker-compose build

echo "==> Starting containers..."
docker-compose up -d

echo ""
echo "Done. Open http://<host-ip>:8080"
