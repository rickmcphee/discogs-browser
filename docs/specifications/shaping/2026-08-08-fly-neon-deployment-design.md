# Fly.io + Neon deployment design

Date: 2026-08-08
Branch: `fly-neon-deployment`

## Problem

The multi-tenant architecture spec (`docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md`)
explicitly deferred hosting-provider and managed-Postgres choices as a non-goal.
Today the app runs self-hosted on a home Synology NAS. Continuing to host the
multi-tenant, publicly-reachable version there means the operator has to
maintain a hardened home network — any compromise of the publicly-reachable
app has a blast radius into other devices on that LAN. This spec resolves that
non-goal: move the origin off the home network entirely, onto managed hosting,
without changing the application architecture already designed (FastAPI +
Playwright, in-process worker pool, Postgres with RLS).

Cloudflare Workers was considered and rejected before this design: it's a V8
isolate runtime with no persistent processes and no Playwright/Chromium
support, which conflicts with the app's core crawling mechanism regardless of
the hosting-security question. `upside-services` was checked for precedent
(per standing instruction) — no Cloudflare Workers, Fly.io, Render, or Railway
usage anywhere in the org; it's AWS-only throughout. The org's closest analog,
`gas-price-lambdas`'s Lambda-based crawl dedup, was already flagged in the
multi-tenant spec as a poor fit for the same reason Workers is: Playwright
needs a persistent browser process, which fights any serverless/isolate
execution model. Nothing in the org transfers directly to this choice; it's
decided on the merits below.

## Goals / non-goals

**Goals**
- Retire the home NAS as the origin for the publicly-reachable app.
- Keep the existing application architecture (FastAPI, Playwright worker pool,
  Postgres + RLS) unchanged — this is a hosting-target change, not a rearchitecture.
- Minimize ongoing infra ops — favor a managed PaaS over self-managed VMs or
  AWS ECS/EKS.
- Put `tracktempest.com` (the domain already registered) in front of the
  hosted app.

**Non-goals**
- Renaming the repo/app from `discogs-browser`/"Discogs Browser" to
  `track-tempest`/"TrackTempest". Tracked separately; this spec only wires up
  the domain for the existing app.
- Billing, CI/CD beyond a basic deploy-on-push workflow, and any application
  *architecture* changes. Out of scope per the multi-tenant spec's own
  non-goals, still out of scope here — this spec's own small hardcoded-
  assumption fixes (`DATABASE_URL` handling, CORS origins, cross-origin API
  base) are config/deployment-target adaptations, not architecture changes.
- ~~Multi-region or multi-machine scaling. Single always-on machine is
  sufficient at this scale; the worker pool's existing `SELECT ... FOR UPDATE
  SKIP LOCKED` claiming logic already tolerates adding more later without a
  design change.~~ Superseded by
  [`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md):
  a second Machine turned out to need `config.json`/`avatar.png` moved off
  the (per-machine) volume and into Postgres first. Multi-region is still a
  non-goal.

## Architecture

```
Cloudflare (DNS for tracktempest.com)
    ├── Pages: tracktempest.com → static frontend build (Vite SPA)
    └── DNS CNAME: api.tracktempest.com → Fly app hostname
                │   (Cloudflare proxy on, SSL/TLS mode "Full (strict)")
                ▼
Fly.io app (always-on Machine(s), region near operator — see
    2026-08-16-fly-multi-machine-design.md for running two)
    ├── FastAPI + in-process crawl worker pool + shared Playwright/Chromium
    └── outbound TLS connection (sslmode=require) ──────────┐
                                                             ▼
                                              Neon Postgres (managed)
                                              catalog / listings / crawlers /
                                              crawl_queue / users / sessions /
                                              library_items / invites — RLS
                                              as designed in the multi-tenant spec
```

The home NAS is removed from the picture entirely. No inbound ports on the
operator's home network. `docker-compose.yml`'s two-service shape is retired
for the hosted deployment — the frontend becomes a static Pages build instead
of a served container, the backend becomes a Fly Machine instead of a
docker-compose service.

## Components

### Fly.io (backend)

- Reuses `backend/Dockerfile` largely as-is — Playwright's own base image
  already provides Chromium and its OS-level dependencies, so no new system
  packages need adding for Fly specifically.
- `fly.toml` sets `auto_stop_machines = false` and `min_machines_running = 2`
  (`1` until `2026-08-16-fly-multi-machine-design.md`).
  This is required, not optional: Fly's autostop/autostart is driven by
  fly-proxy's view of inbound traffic, not app-level activity. The worker pool
  (`CrawlManager.start_worker_pool()`) runs continuously draining
  `crawl_queue` with no inbound HTTP request in flight for long stretches —
  without disabling autostop, Fly would suspend the machine mid-crawl.
- ~~Single machine is sufficient. If a second is ever added, the existing
  `claim_crawl_queue_batch` `SKIP LOCKED` claiming and per-crawler pacing
  locks (`docs/superpowers/plans/2026-08-01-worker-pool-pacing.md`) already
  assume safe concurrent workers, so this isn't a new design surface.~~ A
  second Machine was added; see `2026-08-16-fly-multi-machine-design.md` for
  `min_machines_running` and the local-disk state that had to move first.
- Region: pick the Fly region geographically closest to the operator, since
  there's no multi-region requirement and it minimizes latency for the
  primary (and likely only, at least initially) user base.

### Neon (Postgres)

- Standard Postgres wire protocol — no change to the multi-tenant spec's RLS
  design, schema, or migrations.
- App connects via Neon's pooled (pgbouncer-compatible) connection string.
  The unpooled/direct connection string is only needed for one-off migration
  runs that require session-level features (e.g., advisory locks across a
  transaction boundary), not for the app's steady-state pool.
- Free tier covers this scale (single-digit users, invite-gated rollout).
  Upgrading to a paid tier is a config change, not a design change, if
  storage/compute ever outgrows it.

### Cloudflare (DNS + frontend)

- `tracktempest.com` (apex) → Cloudflare Pages, serving the Vite SPA build.
  Pages builds directly from the repo's frontend directory on push — no
  separate CI workflow needed for this piece.
- `api.tracktempest.com` → CNAME to the Fly app's assigned hostname, proxied
  through Cloudflare. TLS mode must be "Full (strict)" (Fly issues its own
  Let's Encrypt cert for the custom hostname) — "Flexible" would create a
  redirect loop between Cloudflare and Fly's own HTTPS-upgrade behavior.
- This is the one place Cloudflare's edge is actually a good fit: static
  asset hosting and DNS, not the Playwright-dependent backend.

### Secrets

Set via `fly secrets set`, not committed to the repo:
- `DATABASE_URL` (Neon pooled connection string)
- Discogs OAuth consumer key/secret
- Session cookie signing key
- Symmetric key encrypting stored Discogs OAuth token pairs at rest, per the
  multi-tenant spec's "encrypt before persisting" principle (borrowed in
  principle from `radstone-ai-zensational`'s KMS-encrypted token storage, not
  the storage engine — Fly has no KMS equivalent, so this is a plain app
  secret, which is an acceptable trade at this scale).

### CI/CD

- No org precedent to borrow (AWS-only elsewhere, per Problem section).
- GitHub Actions workflow: `flyctl deploy` on push to `main`, functionally
  replacing what `bootstrap.sh` + manual `git pull`/`docker-compose up -d`
  does for the NAS path today, minus the SSH step.
- The deploy job runs in the `production` GitHub environment, whose deployment
  branch policy restricts it to `main`. `FLY_API_TOKEN` belongs on that
  environment rather than at repository scope, so a workflow on some other
  branch cannot read it. The environment carries no required reviewer — the
  deploy is deliberately unattended so an auto-merged PR ships without a human
  in the loop.
- Cloudflare Pages deploys on push independently via its own git integration.

## Migration / cutover

1. Provision Neon project + database; run existing migrations against it.
2. Provision Fly app, set secrets, deploy backend image, confirm
   `auto_stop_machines = false` / `min_machines_running = 1` took effect.
3. Point `api.tracktempest.com` at the Fly app; verify TLS mode and that the
   worker pool survives an idle period (no inbound traffic) without
   suspending.
4. Deploy frontend to Cloudflare Pages, point `tracktempest.com` at it.
5. Decommission the NAS-hosted instance. The last self-hosted single-owner
   shape remains recoverable via the existing `last-self-hosted-single-owner`
   tag; this cutover doesn't touch that history.

## Docs impact

- `README.md`: add a hosted-deployment section (Fly.io + Neon + Cloudflare)
  alongside the existing NAS section, which already marks itself as
  describing the retired single-owner mode and is left untouched.
- No `.agents/INSTRUCTIONS.md`, `INPUTS.md`, or `OUTPUTS.md` exist in this
  repo yet. This change alters *where* the app runs, not its triggers or
  outputs, so there's nothing to reconcile even if those documents existed.

## Open questions (deferred, not blocking)

- Exact Fly region and machine size — pick at implementation time based on
  observed Playwright memory usage under real crawl load.
- Whether to put Cloudflare Access (or similar) in front of `/api` for an
  extra auth layer beyond the app's own session cookies. Not required by this
  design; the app's existing auth model is unchanged and sufficient on its
  own.
