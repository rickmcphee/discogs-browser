# Undeploy the hosted deployment (Fly.io + Neon + Cloudflare) back to the NAS

Date: 2026-10-07
Branch: `claude/undeploy-cloud-infra-g4ufcm`

## Problem

The app has run hosted since
[`2026-08-08-fly-neon-deployment-design.md`](2026-08-08-fly-neon-deployment-design.md):
backend on Fly.io (`tracktempest-api`, scaled out per
[`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)),
Postgres on Neon, frontend as a Cloudflare Worker, DNS on Cloudflare. The
operator wants it back on the self-hosted docker-compose stack on the NAS,
redeployed by hand with `bootstrap.sh`, with the hosted infrastructure torn
down — while keeping the option of hosting it again later.

## Decisions

- **Deploys stop by default; the machinery stays.** The `deploy` job in
  `.github/workflows/ci.yml` now also requires the repository
  variable `FLY_DEPLOY_ENABLED` to equal `"true"`. Unset, every push to
  `main` still runs the backend and frontend test jobs (they are the
  required checks) and the deploy job is skipped. *Amended 2026-10-08:* the
  workflow was since renamed from `fly-deploy.yml` / "CI / Fly Deploy" to
  `ci.yml` / "CI", since it is the repository's CI first. The required checks
  are matched by job name ("Backend tests", "Frontend tests"), which did not
  change, and both test jobs gained `timeout-minutes` so a hung run fails fast
  instead of holding a PR for GitHub's six-hour cap. `backend/fly.toml`,
  `config.py`'s Neon handling (`DIRECT_DATABASE_URL`, the pooler warning),
  `FLY_MACHINE_ID`, and `VITE_API_BASE_URL` all stay: each is inert outside
  a hosted deployment and is what a future one needs.
  A variable rather than deleting the job, because re-hosting should be a
  settings change plus provisioning, not a code change that has to be
  rediscovered from history.
- **Teardown is a manual workflow, not a side effect.**
  `.github/workflows/cloud-teardown.yml` (`workflow_dispatch` only) destroys
  the Fly app named in `backend/fly.toml` and, when given a project id and a
  `NEON_API_KEY` secret, the Neon project. It requires the app name typed as
  confirmation, runs in the `production` environment (so any protection
  rules on it apply), and shares the deploy job's concurrency group so it
  cannot interleave with a deploy. Cloudflare (the frontend Worker and its
  git integration, DNS records) has no token in this repo and is torn down
  in its dashboard.
- **Data moves by `pg_dump`, not by the app.**
  `scripts/migrate-from-neon.sh` runs on the NAS: plain-SQL `pg_dump` from
  Neon's unpooled endpoint (using a `postgres:<neon-major>` image, since
  `pg_dump` must be at least the server's version), restored in one
  transaction into the compose `postgres` service, then compared
  table-by-table on exact row counts. `--no-owner --no-acl` because Neon's
  owner role does not exist locally; `init_tenant_schema()` recreates
  `app_identity`/`app_user` and every grant on the backend's next start, and
  the RLS policies name no role, so they restore as written. The script
  never drops anything: a non-empty local database is renamed aside
  (`--set-aside-existing`) rather than overwritten, in keeping with
  `bootstrap.sh`'s rule that a data reset is always a separate, deliberate
  act.
- **`TOKEN_ENCRYPTION_KEY` must come with the data.** Every stored Discogs
  OAuth token is Fernet-encrypted with it; restoring the rows under a
  different key leaves every user unable to sync until they log in again.
  Fly secrets cannot be read back with `fly secrets list`, so the runbook
  reads them out of a running Machine *before* the app is stopped.

## Trade-off the operator should weigh

The hosted move was made so that a *publicly reachable*, multi-tenant app
would not sit on the home LAN (see the fly-neon design's "Problem"). That
concern returns if the NAS deployment is exposed to the internet again. A
LAN-only deployment, or one reached through a tunnel/VPN rather than an
open port, does not reintroduce it.

## Runbook

Order matters: stop writes, copy, verify, *then* destroy.

1. **Merge this change.** From then on pushes to `main` no longer deploy.
   In Cloudflare, disconnect the frontend Worker's git integration (Workers &
   Pages → the Worker → Settings → Build), or it keeps redeploying the
   frontend on every push.
2. **Capture the secrets the NAS needs** while a Machine is still up:
   `fly ssh console -a tracktempest-api -C 'printenv TOKEN_ENCRYPTION_KEY DISCOGS_CONSUMER_KEY DISCOGS_CONSUMER_SECRET'`.
   Put them in the NAS checkout's `.env` (see `.env.example`), with
   `BACKEND_BASE_URL` set to the NAS's own address. `IDENTITY_DB_PASSWORD` /
   `APP_DB_PASSWORD` need not match Fly's — roles and grants are not copied.
3. **Stop writes to Neon:** `fly scale count 0 -a tracktempest-api --yes`.
   Neon keeps the data; the app is merely down.
4. **Copy the data** on the NAS, from the checkout:
   `NEON_DATABASE_URL='<unpooled connection string>' bash scripts/migrate-from-neon.sh`
   (add `--set-aside-existing` if the NAS database still holds the
   pre-Fly data; it is renamed to `discogs_browser_pre_neon_<timestamp>`,
   not dropped). The script exits non-zero unless every table's row count
   matches.
5. **Start and check:** `bash bootstrap.sh` (or `docker-compose up -d`), log
   in, and check the library, Store tab and recommendations. If Discogs
   rejects the OAuth callback, update the callback URL on the Discogs
   developer settings page to `<BACKEND_BASE_URL>/api/auth/discogs/callback`.
6. **Tear down Fly (and optionally Neon):** Actions → *Tear down hosted
   deployment* → Run workflow, typing `tracktempest-api`; give the Neon
   project id only if a `NEON_API_KEY` secret is set, otherwise delete the
   project from the Neon console. If the `FLY_API_TOKEN` is a deploy token
   scoped too narrowly to destroy the app, run
   `fly apps destroy tracktempest-api` locally instead.
7. **Cloudflare:** delete the frontend Worker and the `api.tracktempest.com`
   / apex records (or repoint them), and the `_fly-ownership` TXT record.
8. **GitHub:** delete the `FLY_API_TOKEN` (and `NEON_API_KEY`) secrets and,
   if nothing else uses it, the `production` environment. Leave
   `FLY_DEPLOY_ENABLED` unset.
9. Once the NAS has run cleanly for a while, delete `neon-export/` — it holds
   every user's encrypted tokens and session rows.

## Re-hosting later

Provision per the fly-neon runbook
([`../plans/2026-08-08-fly-neon-deployment.md`](../plans/2026-08-08-fly-neon-deployment.md)),
set `FLY_API_TOKEN`, then set the `FLY_DEPLOY_ENABLED` repository variable
to `true`. The data goes the other way with `pg_dump` from the NAS into the
new managed database.
