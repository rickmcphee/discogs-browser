# Fly.io + Neon Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the app's origin off the home NAS onto Fly.io (backend) + Neon (Postgres) + Cloudflare Pages (frontend), under `tracktempest.com`, with no application-architecture change.

**Architecture:** Three small backend/frontend code changes remove hardcoded assumptions that only held for the Docker Compose/NAS environment (an admin DB role literally named `postgres`, a single hardcoded CORS origin, a same-origin `/api` path). Everything else is configuration: a `fly.toml` + GitHub Actions workflow for the backend, and a manual one-time cloud-provisioning runbook (Neon project, Fly app/secrets/domain, Cloudflare Pages/DNS) that only someone with real Fly/Neon/Cloudflare account credentials can run.

**Tech Stack:** FastAPI (Python 3.11, plain pip/venv), React 19 + Vite + TypeScript (npm), Postgres (psycopg3), Fly.io, Neon, Cloudflare Pages/DNS, GitHub Actions.

## Global Constraints

- Spec: [`docs/specifications/shaping/2026-08-08-fly-neon-deployment-design.md`](../shaping/2026-08-08-fly-neon-deployment-design.md) — full architecture, component, and migration rationale.
- No application architecture change: the FastAPI process, in-process crawl worker pool, shared Playwright browser, and Postgres/RLS schema are unchanged. Every task here is either a hardcoded-assumption fix or infrastructure configuration.
- No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md` exist in this repo. This work changes *where* the app runs, not its triggers, outputs, or stack — confirmed in the spec's "Docs impact" section. `README.md` **is** affected (new hosted-deployment section, new env var table rows) — that update is folded into Task 5 below, the task whose deliverable it documents, not a trailing catch-all.
- Backend commands run from `backend/` via a plain pip-installed venv, not Poetry — this repo has no `poetry.lock`/`[tool.poetry]` section; `README.md`'s own documented setup is `pip install -e ".[dev]"`, confirmed by the CI test-gate workflow (`.github/workflows/fly-deploy.yml`) using the same: `pytest`, `uvicorn main:app --reload`.
- Frontend commands run from `frontend/`: `npm run test` (vitest run), `npm run build` (tsc -b && vite build), `npm run lint` (oxlint).
- Commit via `commit-with-cleanup.sh`, never `git commit -m` (drops the AI-attribution trailer) — see `CLAUDE.md`.
- Versioning rule (`CLAUDE.md`): `backend/version.py`'s `VERSION` gets a minor bump as part of this PR — done as the last step of Task 4, not a separate follow-up. Shipped as `2.13` → `2.14` after a rebase onto `main`'s own subsequent version bumps (originally `2.11` → `2.12` at the time this task was implemented).
- Task 5 (cloud provisioning) requires real Fly.io, Neon, and Cloudflare account credentials. It cannot be executed inside a sandboxed agent session without those credentials — it's written as an exact, bite-sized runbook for whoever (the repo owner, or an agent handed those credentials) runs it.

---

### Task 1: Preserve external `DATABASE_URL` credentials in `config.py`

**Files:**
- Modify: `backend/config.py:26-31`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `config.DATABASE_URL` (str) — consumed by `db.get_admin_pool()` and, via `_with_userinfo`, by `config.IDENTITY_DATABASE_URL`/`config.APP_DATABASE_URL`. Signature/behavior of `_with_userinfo` itself is unchanged.

**Why:** `config.py` currently *always* rewrites the connecting role to the literal username `"postgres"` with the `POSTGRES_PASSWORD` env var, regardless of what's actually in `DATABASE_URL`. That's a Docker Compose-specific workaround (compose can't safely interpolate a password containing URL-reserved characters into its own YAML, so the password is injected here in Python instead). Neon's default role is not named `postgres` — it's `{database_name}_owner` (e.g. `neondb_owner`), confirmed via [Neon's role documentation](https://neon.com/docs/manage/roles). A Neon `DATABASE_URL` is one ready-made connection string with its own real role name/password already embedded; today's code silently discards that and substitutes a `postgres`/`postgres` login that doesn't exist on Neon, breaking every DB connection at startup.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_config.py` (add `import importlib` and `import config as config_module` to the existing imports at the top of the file):

```python
import importlib

import config as config_module


def test_database_url_preserves_external_credentials_when_no_postgres_password(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://neondb_owner:realsecret@ep-example.us-east-2.aws.neon.tech/discogs_browser?sslmode=require",
    )
    try:
        importlib.reload(config_module)
        assert config_module.DATABASE_URL == (
            "postgresql://neondb_owner:realsecret@ep-example.us-east-2.aws.neon.tech/discogs_browser?sslmode=require"
        )
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def test_database_url_still_injects_postgres_user_when_postgres_password_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@postgres:5432/discogs_browser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    try:
        importlib.reload(config_module)
        assert config_module.DATABASE_URL == "postgresql://postgres:s3cret@postgres:5432/discogs_browser"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: the two new tests FAIL (`test_database_url_preserves_external_credentials_when_no_postgres_password` fails because today's code still substitutes `postgres`/`postgres`).

- [ ] **Step 3: Fix `config.py`**

Replace the current `DATABASE_URL` assignment in `backend/config.py`:

```python
DATABASE_URL = _with_userinfo(
    os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/discogs_browser"),
    "postgres",
    os.environ.get("POSTGRES_PASSWORD", "postgres"),
)
```

with:

```python
_raw_database_url = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/discogs_browser"
)
# docker-compose's backend service passes DATABASE_URL without a password
# (see docker-compose.yml) and the real secret via POSTGRES_PASSWORD instead,
# since compose's raw YAML interpolation can't safely quote a password
# containing URL-reserved characters -- Python injects it here instead. A
# managed-Postgres deployment (e.g. Neon) sets DATABASE_URL to one ready-made
# connection string with its own role/password already embedded, and must
# not have that overwritten with a hardcoded "postgres" user.
if "POSTGRES_PASSWORD" in os.environ:
    DATABASE_URL = _with_userinfo(_raw_database_url, "postgres", os.environ["POSTGRES_PASSWORD"])
else:
    DATABASE_URL = _raw_database_url
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: PASS, all tests including the pre-existing `test_with_userinfo_*` ones.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_config.py
```
Write commit message to a temp file and run `commit-with-cleanup.sh` per `CLAUDE.md`'s commit rule (subject: `fix: preserve external DATABASE_URL credentials for managed Postgres`).

---

### Task 2: Configurable CORS allowed origins

**Files:**
- Modify: `backend/config.py` (add new constant near the other `os.environ.get(...)`-derived constants)
- Modify: `backend/main.py:1-10,50-61` (import + `CORSMiddleware` call)
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Produces: `config.FRONTEND_ORIGINS` (`list[str]`) — consumed by `main.py`'s `CORSMiddleware`.
- Consumes: none from Task 1.

**Why:** `main.py` hardcodes `allow_origins=["http://localhost:5173"]`. That's unrelated to the existing `config.FRONTEND_BASE_URL` (which only builds OAuth redirect targets, in `routers/session.py` — grepped, confirmed no overlap). Once the frontend is served from `https://tracktempest.com` and the backend from `https://api.tracktempest.com`, the browser will send real cross-origin XHR/EventSource requests (Cloudflare Pages has no server-side proxy, unlike today's Vite-dev-server/nginx proxying), so the production origin must be added.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_main.py`:

```python
import importlib


def test_cors_allows_configured_frontend_origin(pg_test_db, monkeypatch):
    import config
    import main

    monkeypatch.setenv("FRONTEND_ORIGINS", "https://tracktempest.com,http://localhost:5173")
    try:
        importlib.reload(config)
        importlib.reload(main)
        with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
             patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()):
            with TestClient(main.app) as client:
                r = client.options(
                    "/api/health",
                    headers={
                        "Origin": "https://tracktempest.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
        assert r.headers["access-control-allow-origin"] == "https://tracktempest.com"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(main)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_main.py -v`
Expected: FAIL — today's hardcoded `allow_origins` doesn't include `https://tracktempest.com`, so no `access-control-allow-origin` header is returned for that preflight.

- [ ] **Step 3: Add `FRONTEND_ORIGINS` to `config.py`**

Add near the other env-derived constants (after `FRONTEND_BASE_URL`):

```python
FRONTEND_ORIGINS = [
    o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]
```

- [ ] **Step 4: Wire it into `main.py`**

Change the import line:
```python
from config import ensure_dirs, CRAWLERS_DIR, load_config
```
to:
```python
from config import ensure_dirs, CRAWLERS_DIR, load_config, FRONTEND_ORIGINS
```

Change:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
to:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_main.py -v`
Expected: PASS, both existing tests in the file and the new one.

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/main.py backend/tests/test_main.py
```
Commit via `commit-with-cleanup.sh` (subject: `feat: make CORS allowed origins configurable via FRONTEND_ORIGINS`).

---

### Task 3: Cross-origin-capable frontend API client

**Files:**
- Modify: `frontend/src/api/client.ts:1-16,132-134`
- Create: `frontend/src/vite-env.d.ts`
- Test: `frontend/src/test/client.test.ts`

**Interfaces:**
- Produces: nothing new exported; `BASE` becomes env-configurable, `apiFetch` and `openCrawlStream` become credential-including.
- Consumes: none from Tasks 1-2 directly (talks to whatever CORS/backend config Task 2 sets up, but the frontend build doesn't depend on backend code).

**Why:** `client.ts` hardcodes `BASE = '/api'` and does `fetch(...)`/`new EventSource(...)` with no explicit credentials mode. That works today because both local dev (Vite's `/api` proxy) and Docker (nginx's `/api` proxy) keep requests same-origin. Cloudflare Pages is pure static hosting with no server-side proxy, so once the frontend is at `tracktempest.com` and the backend at `api.tracktempest.com`, these become real cross-origin requests: `BASE` needs to be a full origin in production, and both `fetch` and `EventSource` need to explicitly opt into sending the session cookie cross-origin (`credentials: 'include'` / `withCredentials: true` — the browser's default `fetch` credentials mode is `same-origin`, which drops cookies cross-origin regardless of the cookie's own `SameSite` setting unless a request explicitly opts in).

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/test/client.test.ts` (add `openCrawlStream` to the existing import from `'../api/client'`):

```ts
it('apiFetch requests include credentials for cross-origin cookie auth', async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
  await getUserSettings()
  expect(fetchMock.mock.calls[0][1].credentials).toBe('include')
})

it('openCrawlStream sets withCredentials for cross-origin cookie auth', () => {
  const es = openCrawlStream()
  expect(es.withCredentials).toBe(true)
  es.close()
})

it('BASE uses VITE_API_BASE_URL when set, for cross-origin API calls', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.tracktempest.com/api')
  vi.resetModules()
  const { getUserSettings: getUserSettingsWithBase } = await import('../api/client')
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
  await getUserSettingsWithBase()
  expect(fetchMock.mock.calls[0][0]).toBe('https://api.tracktempest.com/api/user-settings')
  vi.unstubAllEnvs()
  vi.resetModules()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm run test -- client.test.ts`
Expected: FAIL — no `credentials` key in the fetch init, `EventSource.withCredentials` is `false` by default, `BASE` ignores `VITE_API_BASE_URL`.

- [ ] **Step 3: Add the env var type declaration**

Create `frontend/src/vite-env.d.ts` (per [Vite's documented pattern](https://vite.dev/guide/env-and-mode) — no `import` statements in this file, or the interface augmentation breaks):

```ts
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
```

- [ ] **Step 4: Update `client.ts`**

Change:
```ts
const BASE = '/api'
```
to:
```ts
const BASE = import.meta.env.VITE_API_BASE_URL || '/api'
```

Change `apiFetch`:
```ts
async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Requested-With', 'fetch')
  const r = await fetch(`${BASE}${path}`, { ...init, headers })
  if (r.status === 401) {
    onUnauthorized?.()
  }
  return r
}
```
to:
```ts
async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Requested-With', 'fetch')
  const r = await fetch(`${BASE}${path}`, { ...init, headers, credentials: 'include' })
  if (r.status === 401) {
    onUnauthorized?.()
  }
  return r
}
```

Change:
```ts
export function openCrawlStream(): EventSource {
  return new EventSource('/api/crawl/stream')
}
```
to:
```ts
export function openCrawlStream(): EventSource {
  return new EventSource(`${BASE}/crawl/stream`, { withCredentials: true })
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm run test -- client.test.ts`
Expected: PASS, all tests in the file including pre-existing ones.

- [ ] **Step 6: Run the full frontend build to confirm no type errors**

Run: `cd frontend && npm run build`
Expected: succeeds (`tsc -b && vite build` exits 0) — confirms `vite-env.d.ts`'s `ImportMetaEnv` augmentation is picked up correctly by `tsconfig.app.json`'s existing `"types": ["vite/client"]`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/vite-env.d.ts frontend/src/test/client.test.ts
```
Commit via `commit-with-cleanup.sh` (subject: `feat: make frontend API client cross-origin-capable`).

---

### Task 4: Fly deploy pipeline

**Files:**
- Create: `backend/fly.toml`
- Create: `.github/workflows/fly-deploy.yml`
- Modify: `backend/version.py`

**Interfaces:**
- Consumes: nothing from earlier tasks directly — this is deploy configuration for the code Tasks 1-3 already produced.
- Produces: nothing consumed by later tasks in this plan; Task 5's manual steps reference `backend/fly.toml`'s app name and this workflow's `FLY_API_TOKEN` secret requirement.

**Why:** `fly.toml` lives in `backend/` (not the repo root) so that `fly deploy` run with `backend/` as the working directory gets `backend/` as both the config location and the Docker build context — matching `Dockerfile`'s existing relative `COPY pyproject.toml .` / `COPY . .` instructions unchanged (Fly's `dockerfile` key only overrides the file path, not the build context, per [Fly's own configuration reference](https://fly.io/docs/reference/configuration/#the-build-section) — putting `fly.toml` alongside the `Dockerfile` avoids that mismatch entirely rather than fighting it with path overrides). `auto_stop_machines = "off"` and `min_machines_running = 1` are not cosmetic: Fly's autostop is driven by the proxy's view of inbound HTTP traffic, not app-level activity, and the crawl worker pool runs continuously with long gaps between inbound requests — without this, Fly would suspend the machine mid-crawl.

This isn't independently testable via `pytest`/`vitest` (there's no code here, only deploy config) — its correctness is verified in Task 5's runbook (`fly deploy` succeeding, `fly status` staying in the `started` state through an idle period). It's still its own task/commit because Task 5 depends on this file existing.

- [ ] **Step 1: Create `backend/fly.toml`**

```toml
app = "tracktempest-api"
primary_region = "ord"

[build]

[env]
  PLAYWRIGHT_CHANNEL = ""
  DISCOGS_BROWSER_DATA = "/data"

[[mounts]]
  source = "data"
  destination = "/data"
  initial_size = "1gb"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "off"
  auto_start_machines = true
  min_machines_running = 1

[[http_service.checks]]
  grace_period = "30s"
  interval = "15s"
  timeout = "5s"
  method = "GET"
  path = "/api/health"

[[vm]]
  size = "shared-cpu-2x"
  memory = "1gb"
```

`primary_region = "ord"` (Chicago) is a starting default, not a hard requirement — change it to whichever [Fly region](https://fly.io/docs/reference/regions/) is geographically closest to you before Task 5's `fly apps create`, since there's no multi-region requirement here (per the spec's own deferred "exact Fly region" open question) and this only affects your own latency.

- [ ] **Step 2: Create `.github/workflows/fly-deploy.yml`**

```yaml
name: Fly Deploy
on:
  push:
    branches:
      - main
    paths:
      - 'backend/**'
      - '.github/workflows/fly-deploy.yml'
jobs:
  deploy:
    name: Deploy backend to Fly.io
    runs-on: ubuntu-latest
    concurrency: fly-deploy-group
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - name: Deploy
        working-directory: backend
        run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

`working-directory: backend` makes `flyctl deploy` pick up `backend/fly.toml` and use `backend/` as the build context, per Task 4 Step 1's rationale. The `paths` filter avoids redeploying the backend on frontend-only changes (Cloudflare Pages deploys the frontend independently via its own git integration — see Task 5).

- [ ] **Step 3: Bump the app version**

In `backend/version.py`, bump the minor version by one from whatever `main` currently has, per `CLAUDE.md`'s versioning rule (minor bump on every PR merging to `main`). This task originally shipped it as `2.11` → `2.12`; a later rebase onto `main` (which had gained its own version bumps from other merged PRs in the meantime) moved the actual shipped value to `2.13` → `2.14` — bump from whatever `main`'s value is at rebase/merge time, not the literal numbers below.

- [ ] **Step 4: Commit**

```bash
git add backend/fly.toml .github/workflows/fly-deploy.yml backend/version.py
```
Commit via `commit-with-cleanup.sh` (subject: `ci: add Fly.io deploy pipeline for the backend`).

---

### Task 5: Cloud provisioning & cutover runbook

**Files:**
- Modify: `README.md` (new "Deployment (Fly.io + Neon)" section after the existing NAS section at `README.md:59-95`; new env var table rows after `README.md:107`)

**Interfaces:** none — this task is operational steps plus one doc update, not code.

**Requires:** a Fly.io account + `flyctl` CLI logged in (`fly auth login`), a Neon account, and a Cloudflare account with `tracktempest.com` addable as a zone. **This cannot be run by an agent without those credentials** — it's written as an exact runbook for whoever has them (you, or an agent you hand the credentials to).

- [ ] **Step 1: Point `tracktempest.com` at Cloudflare**

In the Cloudflare dashboard, add `tracktempest.com` as a new zone, then update the domain's nameservers at your registrar to the two Cloudflare-assigned nameservers. Cloudflare Pages requires the domain already be a Cloudflare zone before it can be used as a Pages custom domain (confirmed via [Cloudflare's custom domains docs](https://developers.cloudflare.com/pages/configuration/custom-domains/)).

Verify: `dig NS tracktempest.com +short` returns the two `*.ns.cloudflare.com` nameservers (may take up to 24h to propagate).

- [ ] **Step 2: Create the Neon project and run migrations**

In the Neon console, create a project (e.g. named `tracktempest`) with a database named `discogs_browser`. Copy the connection string it gives you (already includes `?sslmode=require`).

Locally, with that connection string as `DATABASE_URL` (and a temporary strong value for `IDENTITY_DB_PASSWORD`/`APP_DB_PASSWORD`/`TOKEN_ENCRYPTION_KEY` — reuse real values for these three going forward, don't regenerate them again for Step 4):

```bash
cd backend
DATABASE_URL="<neon-connection-string>" \
IDENTITY_DB_PASSWORD="<pick-one>" \
APP_DB_PASSWORD="<pick-one>" \
TOKEN_ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
DISCOGS_CONSUMER_KEY=placeholder DISCOGS_CONSUMER_SECRET=placeholder \
uvicorn main:app --port 8000
```

Watch the startup log for `init_global_schema`/`init_tenant_schema` running without error (they run at FastAPI startup — see `main.py`), then Ctrl-C.

Verify: `psql "<neon-connection-string>" -c '\dt'` lists `catalog`, `listings`, `crawlers`, `crawl_queue`, `users`, `sessions`, `library_items`, `invites`, etc.

- [ ] **Step 3: Create the Fly app and set secrets**

```bash
fly auth login
cd backend
fly apps create tracktempest-api   # or whatever name you set in fly.toml
fly secrets set \
  DATABASE_URL="<neon-connection-string>" \
  IDENTITY_DB_PASSWORD="<same value used in Step 2>" \
  APP_DB_PASSWORD="<same value used in Step 2>" \
  TOKEN_ENCRYPTION_KEY="<same value used in Step 2>" \
  DISCOGS_CONSUMER_KEY="<real value from https://www.discogs.com/settings/developers>" \
  DISCOGS_CONSUMER_SECRET="<real value>" \
  BACKEND_BASE_URL="https://api.tracktempest.com" \
  FRONTEND_BASE_URL="https://tracktempest.com" \
  FRONTEND_ORIGINS="https://tracktempest.com"
```

Register `https://api.tracktempest.com/api/auth/discogs/callback` as this app's callback URL in the Discogs developer settings (per `README.md`'s existing `.env.example` comment on `DISCOGS_CONSUMER_KEY`).

Verify: `fly secrets list` shows all eight names (values are never shown back).

- [ ] **Step 4: First deploy and custom domain**

```bash
fly volumes create data -s 1 -r ord -a tracktempest-api   # match region/app name to your fly.toml
fly deploy   # run from backend/, picks up backend/fly.toml
fly certs add api.tracktempest.com
```

In Cloudflare DNS, add a `CNAME` record: `api` → `tracktempest-api.fly.dev`, proxy status **Proxied** (orange cloud). In Cloudflare's SSL/TLS settings for the zone, set the encryption mode to **Full (strict)** — "Flexible" causes a redirect loop against Fly's own HTTPS-upgrade behavior (per the spec's Cloudflare section).

Verify: `fly certs check api.tracktempest.com` reports the cert as issued/ready, then `curl -I https://api.tracktempest.com/api/health` returns `HTTP/2 200`.

- [ ] **Step 5: Verify the worker pool survives an idle period**

Wait at least 10 minutes without sending any request to `api.tracktempest.com`, then check machine state.

Verify: `fly status` shows the single machine still in the `started` state (not `stopped`/`suspended`) — confirms Step 4 of Task 4's `auto_stop_machines = "off"` / `min_machines_running = 1` actually took effect.

- [ ] **Step 6: Create the Cloudflare Pages project**

In the Cloudflare dashboard, create a Pages project connected to this GitHub repo, with:
- Root directory: `frontend`
- Build command: `npm run build`
- Build output directory: `dist`
- Environment variable: `VITE_API_BASE_URL` = `https://api.tracktempest.com/api`

(Fields confirmed via [Cloudflare Pages' git integration docs](https://developers.cloudflare.com/pages/get-started/git-integration/), including monorepo root-directory support.)

Verify: the initial build succeeds and the `*.pages.dev` preview URL loads the app's login screen.

Full login is same-site-cookie-dependent (`SameSite=strict`) and will not work end-to-end from a `*.pages.dev` preview URL, since it isn't the same site as `api.tracktempest.com` — do the actual end-to-end login check at Step 7, once the apex domain is wired up.

- [ ] **Step 7: Add the apex custom domain**

In the Pages project's custom domains settings, add `tracktempest.com`. Since the zone is already on Cloudflare (Step 1), Cloudflare auto-creates the required CNAME for the apex (confirmed via [Cloudflare's custom domains docs](https://developers.cloudflare.com/pages/configuration/custom-domains/)).

Verify: `curl -I https://tracktempest.com` returns `HTTP/2 200` and serves the SPA, and logging in via Discogs OAuth completes successfully end-to-end (exercises `FRONTEND_ORIGINS`/CORS from Task 2, `credentials: 'include'` from Task 3, and the real `BACKEND_BASE_URL`/`FRONTEND_BASE_URL` redirect from Step 3).

- [ ] **Step 8: Wire up the GitHub Actions secret**

```bash
fly tokens create deploy -x 999999h
```

Add the printed token as a GitHub Actions repository secret named `FLY_API_TOKEN` (repo Settings → Secrets and variables → Actions).

Verify: push a trivial change under `backend/` to `main` and confirm the `Fly Deploy` workflow (Task 4) runs and succeeds in the Actions tab.

- [ ] **Step 9: Update `README.md`**

Add a new section after the existing `## Deployment (Synology NAS) — describes the retired single-owner mode` section (`README.md:59-95`, left untouched):

```markdown
## Deployment (Fly.io + Neon)

The hosted multi-tenant deployment runs on Fly.io (backend) + Neon (Postgres)
+ Cloudflare Pages (frontend), under `tracktempest.com`. See
[`docs/specifications/shaping/2026-08-08-fly-neon-deployment-design.md`](docs/specifications/shaping/2026-08-08-fly-neon-deployment-design.md)
for the architecture and
[`docs/specifications/plans/2026-08-08-fly-neon-deployment.md`](docs/specifications/plans/2026-08-08-fly-neon-deployment.md)
for the exact provisioning runbook (Neon project + migrations, Fly app +
secrets + custom domain, Cloudflare Pages project + DNS).

Redeploys: pushing to `main` under `backend/` triggers
`.github/workflows/fly-deploy.yml` automatically. The frontend redeploys
automatically on every push via Cloudflare Pages' own git integration.
```

Add two rows to the `## Environment variables` table (`README.md:96-111`), directly after the existing `FRONTEND_BASE_URL` row:

```markdown
| `FRONTEND_ORIGINS` | `http://localhost:5173` | Comma-separated list of origins allowed to make cross-origin credentialed requests (CORS) |
```

And in the frontend build docs (wherever `npm run build` is first mentioned, or as a note under the new Fly.io+Neon section): document that `VITE_API_BASE_URL` (set at Cloudflare Pages build time, see Step 6 above) points the frontend at a non-same-origin backend; unset/empty means same-origin `/api` (local dev, Docker).

- [ ] **Step 10: Decommission the NAS instance**

On the NAS, stop the old deployment (`docker-compose down`, no `-v`) once `tracktempest.com` is confirmed fully working end-to-end (Steps 5 and 7). No data migration back is needed in the other direction — the `last-self-hosted-single-owner` git tag already preserves that shape's history per the multi-tenant spec, and Step 2 already migrated forward into Neon.

Verify: `tracktempest.com` continues to serve correctly with the NAS instance stopped.

- [ ] **Step 11: Commit the README update**

```bash
git add README.md
```
Commit via `commit-with-cleanup.sh` (subject: `docs: document Fly.io + Neon hosted deployment`).

---

## Self-Review

**Spec coverage:** Architecture (Fly/Neon/Cloudflare shape) → Task 5. Fly specifics (Dockerfile reuse, autostop) → Task 4 + Task 5 Step 5. Neon specifics (pooled connection, RLS) → Task 1 fixes the one real blocker found (hardcoded `postgres` user); RLS/BYPASSRLS compatibility was verified against Neon's docs (`neon_superuser` role membership includes `CREATEROLE`/`BYPASSRLS`) and needed no code change. Cloudflare (DNS + Pages) → Task 5 Steps 1, 6, 7. Secrets → Task 5 Step 3. CI/CD → Task 4. Migration/cutover → Task 5 Steps 2, 10. Docs impact → Task 5 Step 9.

**Beyond the spec, found during implementation planning (not in the original design doc, both required for it to actually work):** Task 2 (hardcoded CORS origin) and Task 3 (hardcoded same-origin `/api` path, no explicit credentials mode) — the spec's architecture diagram implies a cross-origin frontend/backend split but didn't call out that the current frontend code has no cross-origin path at all today (local dev and Docker both stay same-origin via a proxy that Cloudflare Pages doesn't have). Both are now covered.

**Placeholder scan:** none found — every step has concrete code, commands, or exact values (region/app name are explicitly-flagged adjustable defaults, not TBDs, matching the spec's own deferred-but-not-blocking region/sizing note).

**Type consistency:** `FRONTEND_ORIGINS` (config.py) is a `list[str]`, matches `CORSMiddleware(allow_origins=...)`'s expected type. `BASE`/`VITE_API_BASE_URL` used consistently across Task 3's three call sites (`apiFetch`, `openCrawlStream`, the new test).
