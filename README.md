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

## Authentication

The app is single-owner: access is protected by a password (hashed with Argon2id) plus a TOTP second factor, always enforced on every `/api` request.

**First run:** the backend generates a one-time bootstrap token, printed to the log and written to `~/.discogs-browser/bootstrap_token`. Open the app URL and use that token to complete first-run setup — set your password, enroll TOTP in an authenticator app, and save the recovery codes shown.

**Recovery:** a recovery code can be used in place of the TOTP code at login (each code is single-use). If you lose your password, authenticator, and recovery codes, run `python -m reset_owner` from `backend/` to clear the owner account and all sessions and return to first-run setup with a fresh bootstrap token.

**Deployment / TLS:** the session cookie is HttpOnly and SameSite=Strict, and is marked `Secure` based on the `X-Forwarded-Proto` header (or the request scheme if absent). If you run behind a TLS-terminating reverse proxy (nginx, Caddy, an ALB, etc.), it must forward `X-Forwarded-Proto`, and uvicorn should be started with `--proxy-headers --forwarded-allow-ips="*"` so that header is trusted. On a plain-HTTP LAN deployment the cookie is sent without `Secure`, which fits the LAN threat model, but TLS everywhere is the recommended posture for any production/commercial deployment.

## Deployment (Synology NAS)

Designed to run on a Synology NAS via Container Manager. Persistent data (config, database, logs) is stored in `workspace/` inside the repo directory, which is mounted into the container.

**One-time setup via SSH:**

```bash
ssh admin@<nas-ip>
mkdir -p /volume1/docker/discogs-browser
cd /volume1/docker/discogs-browser
git clone https://github.com/rickmcphee/discogs-browser.git .
bash bootstrap.sh
```

`bootstrap.sh` creates the `workspace/` directory and builds the Docker images.

**Create the project in Container Manager:**

1. Open Container Manager → Project → Create
2. Name: `discogs-browser`
3. Path: `/volume1/docker/discogs-browser`
4. Container Manager picks up `docker-compose.yml` automatically
5. Click Next → Done

Open `http://<nas-ip>:8080` and set your Discogs token in Settings.

**Updating to a new version:**

```bash
cd /volume1/docker/discogs-browser
git pull
bash bootstrap.sh
docker-compose up -d
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DISCOGS_BROWSER_DATA` | `~/.discogs-browser` | Data directory |
| `PLAYWRIGHT_CHANNEL` | `"chrome"` | `""` = bundled Chromium (Docker), `"chrome"` = real Chrome |
| `SESSION_IDLE_SECONDS` | 7 days | Session idle timeout |
| `SESSION_MAX_SECONDS` | 30 days | Session absolute max lifetime |
| `LOGIN_MAX_FAILURES` | `5` | Failed login attempts before lockout |
| `LOGIN_LOCKOUT_SECONDS` | `300` | Lockout duration after `LOGIN_MAX_FAILURES` is hit |

## Running tests

```bash
cd backend
pytest
```

HTML fixtures for the Amazon price extraction regression tests live in `backend/tests/fixtures/crawlers/amazon/`. New fixtures can be captured using `backend/scripts/capture_fixture.py`.
