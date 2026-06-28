#!/usr/bin/env bash
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
