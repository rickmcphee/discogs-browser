# discogs-browser

A personal web app for browsing a Discogs record collection cross-referenced with prices from third-party record-selling websites (Amazon, CC Music).

## What this is

This repository is **specification-driven**. The design spec and implementation plan in `docs/` came first; the code was generated from them by Claude Code. It is not intended for human code contributions — changes are made by updating the spec and regenerating or evolving the implementation via Claude Code sessions.

The spec and plan are the authoritative source of truth. If you want to understand how the system works, start with:

- [`docs/superpowers/specs/2026-06-27-discogs-browser-design.md`](docs/superpowers/specs/2026-06-27-discogs-browser-design.md) — design spec
- [`docs/superpowers/plans/2026-06-27-discogs-browser.md`](docs/superpowers/plans/2026-06-27-discogs-browser.md) — implementation plan (tasks 1–12 initial build; 13–18 subsequent improvements)

See [`CLAUDE.md`](CLAUDE.md) for instructions aimed at Claude Code running in this repo.

## Architecture

Two services:

- **Backend** — FastAPI + Playwright, runs on port 8000
- **Frontend** — React/Vite SPA, proxies `/api` to the backend

Persistent state lives under `~/.discogs-browser/` (local dev) or `/data/` (Docker).

## Running locally

```bash
# Backend
cd backend
pip install -e ".[dev]"
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Set your Discogs token in Settings.

## Running with Docker

```bash
docker-compose up --build
```

Open http://localhost:8080. Set your Discogs token in Settings.

The Docker container uses bundled Chromium (no real Chrome required) and headless mode. The login flow for session-authenticated crawlers is not available in Docker.

## Deployment

Designed to run permanently on a Synology NAS or any always-on Docker host. The `discogs_data` volume persists your collection, crawler state, and logs across container restarts.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DISCOGS_BROWSER_DATA` | `~/.discogs-browser` | Data directory |
| `PLAYWRIGHT_CHANNEL` | `"chrome"` | `""` = bundled Chromium (Docker), `"chrome"` = real Chrome |
| `HEADLESS_AUTH` | `""` | `"1"` disables the macOS browser-launch login flow |

## Running tests

```bash
cd backend
pytest
```

HTML fixtures for the Amazon price extraction regression tests live in `backend/tests/fixtures/crawlers/amazon/`. New fixtures can be captured using `backend/scripts/capture_fixture.py`.
