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

echo "==> Building Docker images..."
docker-compose build

echo "==> Starting containers..."
docker-compose up -d

echo ""
echo "Done. Open http://<host-ip>:8080"
