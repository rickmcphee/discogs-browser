#!/usr/bin/env bash
set -e

echo "==> Creating workspace directory..."
mkdir -p workspace

echo "==> Building Docker images..."
docker-compose build

echo ""
echo "Done. Start the app with:"
echo "  docker-compose up -d"
echo "Then open http://<host-ip>:8080 and set your Discogs token in Settings."
