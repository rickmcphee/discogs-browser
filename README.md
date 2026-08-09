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

Access is via Discogs OAuth — click "Continue with Discogs" on the login screen and approve on discogs.com. There is no password or second factor to manage.

New accounts require a valid, unredeemed invite code, entered once right after your first successful Discogs login; returning users are logged straight in. Invite codes are minted by hand (no self-serve generation yet). This app is no longer single-owner — see [`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`](docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md) for the full multi-tenant design.

**Deployment / TLS:** the session cookie is HttpOnly and SameSite=Strict, and is marked `Secure` based on the `X-Forwarded-Proto` header (or the request scheme if absent). If you run behind a TLS-terminating reverse proxy (nginx, Caddy, an ALB, etc.), it must forward `X-Forwarded-Proto`, and uvicorn should be started with `--proxy-headers --forwarded-allow-ips="*"` so that header is trusted. On a plain-HTTP LAN deployment the cookie is sent without `Secure`, which fits the LAN threat model, but TLS everywhere is the recommended posture for any production/commercial deployment.

**Deployment / rate-limit keying:** the invite-redemption and Discogs-OAuth rate limiters key on the caller's IP (`_client_key` in `backend/routers/session.py`). Behind a reverse proxy every request shares the proxy's own peer IP, collapsing all callers into one shared limiter bucket unless the real client IP is recovered from a header. `CF-Connecting-IP` (set, and any client-supplied value overwritten, by Cloudflare for traffic it proxies) is only read when `TRUST_CF_CONNECTING_IP=1` is set — that header is fully client-controlled otherwise, so trusting it unconditionally would let a caller defeat its own rate limit by sending a different value per request. Set `TRUST_CF_CONNECTING_IP=1` only for the hosted deployment, once Cloudflare is confirmed to be the sole path in (still spoofable by anyone reaching the app directly via its Fly hostname, which isn't yet blocked — tracked separately). Leave it unset for local dev, Docker Compose, or any self-hosted reverse proxy that doesn't set this header; `_client_key` falls back to the raw peer IP.

## Deployment (Synology NAS) — describes the retired single-owner mode

The instructions below describe the single-owner, SQLite-backed deployment mode that predates the multi-tenant pivot. That mode is retired, not maintained alongside the new one — the last commit in that shape is tagged `last-self-hosted-single-owner`. These NAS instructions will be replaced once the hosted multi-tenant deployment story (Postgres, hosting provider, etc. — deliberately not yet decided, per the architecture spec's own non-goals) is worked out in a later plan. Kept here for now rather than deleted, since it's still the accurate way to run the last single-owner release.

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

## Deployment (Fly.io + Neon)

The hosted multi-tenant deployment runs on Fly.io (backend, app `tracktempest-api`)
+ Neon (Postgres) + Cloudflare (frontend), live at `tracktempest.com`. See
[`docs/specifications/shaping/2026-08-08-fly-neon-deployment-design.md`](docs/specifications/shaping/2026-08-08-fly-neon-deployment-design.md)
for the architecture and
[`docs/specifications/plans/2026-08-08-fly-neon-deployment.md`](docs/specifications/plans/2026-08-08-fly-neon-deployment.md)
for the full provisioning runbook.

The frontend is a static Vite build. It's served via a Cloudflare Worker with
static assets — Cloudflare folded Pages into Workers in 2026, so a new
"Create application" project deploys this way by default now rather than as
a classic Pages project; functionally equivalent for a static SPA. Build
settings: root directory `frontend`, build command `npm run build`, output
directory `dist`, with `VITE_API_BASE_URL=https://api.tracktempest.com/api`
set as a build-time environment variable (unset/empty means same-origin
`/api`, i.e. local dev and Docker).

**DNS:** `api.tracktempest.com` is a proxied CNAME to the Fly app's hostname;
the zone's SSL/TLS mode must be **Full (strict)**, and Fly's certificate
needs an `_fly-ownership` TXT record to verify ownership through Cloudflare's
proxy (`fly certs setup <hostname>` prints the exact value to add). For the
apex `tracktempest.com`, prefer Cloudflare's **Custom Domain** binding
(Worker settings → Domains & Routes → Custom Domains) over a manual CNAME to
the Worker's own `*.workers.dev` hostname — a manual CNAME works too (Cloudflare
flattens it), but routes through a second Cloudflare proxy hop and can
surface a transient `522`. Either way, allow real time for a freshly-added
apex record to propagate before concluding something's wrong.

**Redeploys:** every push to `main` triggers `.github/workflows/fly-deploy.yml`,
which redeploys the backend to Fly.io regardless of which files changed
(no path filter — a docs-only or frontend-only push still re-deploys the
unchanged backend), gated behind that same workflow's backend/frontend test
jobs. The frontend redeploys automatically on every push via Cloudflare's
own git integration.

**Bootstrapping a fresh instance:** the first invite can't be created
through the app — the create-invite endpoint requires an existing admin
user, and a brand-new database has none. Insert it directly (`created_by`
is nullable for exactly this case), using a real random code — not a
literal, guessable string — the same way the app's own invite endpoint
does (`secrets.token_urlsafe(12)`); the `invites` table has no expiry
column, so an unredeemed code stays valid indefinitely until redeemed or
deleted:

```bash
CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
psql "<connection-string>" -c "INSERT INTO invites (code, created_by, created_at) VALUES ('$CODE', NULL, CURRENT_TIMESTAMP);"
echo "$CODE"
```

After redeeming it and logging in once, promote yourself to admin
(`is_admin` defaults to `false`) so future invites can be minted through the
app instead of raw SQL:

```sql
UPDATE users SET is_admin = true WHERE discogs_username = '<your-discogs-username>';
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DISCOGS_BROWSER_DATA` | `~/.discogs-browser` | Data directory |
| `PLAYWRIGHT_CHANNEL` | `"chrome"` | `""` = bundled Chromium (Docker), `"chrome"` = real Chrome |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/discogs_browser` | Postgres admin connection string |
| `IDENTITY_DB_PASSWORD` / `APP_DB_PASSWORD` | _(none, required)_ | Passwords for the `app_identity`/`app_user` Postgres roles |
| `TOKEN_ENCRYPTION_KEY` | _(none, required)_ | Fernet key encrypting stored Discogs OAuth tokens at rest |
| `DISCOGS_CONSUMER_KEY` / `DISCOGS_CONSUMER_SECRET` | _(none, required)_ | This app's own registered Discogs OAuth consumer credentials |
| `BACKEND_BASE_URL` | `http://localhost:8000` | This backend's own publicly-reachable base URL, used to build the Discogs OAuth callback |
| `FRONTEND_BASE_URL` | `""` (relative, same-origin) | Where the backend redirects the browser after login; set for local dev where frontend/backend are different origins |
| `FRONTEND_ORIGINS` | `http://localhost:5173` | Comma-separated list of origins allowed to make cross-origin credentialed requests (CORS) |
| `TRUST_CF_CONNECTING_IP` | `""` (unset) | Set to `1` only once Cloudflare is confirmed to be the sole path into the deployment; see "Deployment / rate-limit keying" above |
| `SESSION_IDLE_SECONDS` | 7 days | Session idle timeout |
| `SESSION_MAX_SECONDS` | 30 days | Session absolute max lifetime |
| `LOGIN_MAX_FAILURES` | `5` | Failure threshold shared by the invite-redemption and Discogs-OAuth rate limiters before a temporary lockout |
| `LOGIN_LOCKOUT_SECONDS` | `300` | Lockout duration after `LOGIN_MAX_FAILURES` is hit |

## Running tests

```bash
cd backend
TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/discogs_browser_test" \
IDENTITY_DB_PASSWORD=test \
APP_DB_PASSWORD=test \
pytest
```

The passwords are arbitrary local test values, not real secrets. The database
named in `TEST_DATABASE_URL` is never itself read from or written to —
each pytest session provisions its own `<base>_run_<hex>` database from
`TEMPLATE template0`, runs entirely inside that database, and drops it on
teardown. `TEST_DATABASE_URL` only supplies connection details (host, port,
credentials), so its role needs `CREATEDB` and access to the `postgres`
maintenance database to create and drop the per-run database; the local
Docker `postgres` superuser and CI's `postgres` user both already qualify.

If a run crashes before teardown, its per-run database (and possibly a
poisoned `app_user` role) can be left behind — run `make test-db-clean` to
drop leaked `_run_*` databases and repair the role.

HTML fixtures for the Amazon price extraction regression tests live in `backend/tests/fixtures/crawlers/amazon/`. New fixtures can be captured using `backend/scripts/capture_fixture.py`.
