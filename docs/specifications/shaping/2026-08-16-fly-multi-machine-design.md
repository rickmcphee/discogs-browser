# Fly.io multi-machine scaling

## Problem

[`2026-08-08-fly-neon-deployment-design.md`](2026-08-08-fly-neon-deployment-design.md)
deployed this app as a single always-on Fly Machine and explicitly deferred
multi-machine as a non-goal, noting only that the worker pool's `SELECT ...
FOR UPDATE SKIP LOCKED` claiming already tolerates more workers without a
design change. That's still true. What that spec didn't account for: Fly
volumes attach to exactly one Machine. A second Machine needs its own volume,
so anything written to `DISCOGS_BROWSER_DATA` (`backend/fly.toml`'s `data`
mount) forks into two independent copies the moment a second Machine exists.

Of what currently lives there (see this repo's `CLAUDE.md` "Data directory"
section), two things are load-bearing enough that forking them is a real
correctness bug, not a cosmetic one:

- `config.json` — crawl pacing (`crawl_delay_seconds`), worker count,
  `consecutive_failure_limit`, the crawl/stock cron schedules, and marketplace
  API credentials (eBay app id/cert). A settings write via the admin UI lands
  on whichever Machine served that request; the other Machine's worker pool
  and scheduler keep stale values indefinitely. Two Machines would then run
  crawls at different paces, with different failure thresholds, one of them
  possibly missing eBay credentials entirely.
- `avatar.png` — the user's profile photo. Already flagged in `CLAUDE.md` as
  "not yet re-scoped per-user"; it's a single shared file today even though
  the app is multi-tenant. Splitting it across two per-machine volumes doesn't
  just risk inconsistency, it's not even a coherent per-user concept until
  it's actually keyed by user.

`app.log` and `screenshots/` are the remaining occupants of that volume.
Both are inherently machine-local debug output — a log forking across
Machines is just what server logs do — so they're left alone.

## Goals

- Support running two Fly Machines for this app without either of them
  observing stale crawl settings or a missing/wrong avatar.
- Move `config.json` (app-wide settings) and `avatar.png` (per-user) into
  Postgres, where every Machine already reads consistent state for
  everything else.
- Converge both Machines' APScheduler cron schedules on whatever the latest
  `app_config` write says, without requiring an operator to redeploy or
  restart the Machine that didn't handle the settings write.
- Prevent two Machines from ever running a stock sync for the same store
  concurrently — `_sync_stock`'s `DELETE FROM stock_items WHERE crawler_id =
  %s` + reinsert is not safe to interleave across processes.
- Document the one-time, imperative Fly setup (second volume, machine count)
  that isn't part of `flyctl deploy` and so isn't expressible in a committed
  file — including migrating the current Machine's existing `config.json`
  content, which nothing else in this change does automatically.

## Non-goals

- Auto-scaling beyond two Machines, multi-region, or automated failover.
  Two fixed Machines in one region is the target shape, matching the
  original spec's assumption that this deployment doesn't need more.
- Moving `app.log` or `screenshots/` off local disk. Both are per-machine by
  nature; forking them across Machines isn't a bug.
- Cross-Machine SSE fan-out. `/api/crawl/stream` and `CrawlManager`'s
  `_subscribers`/`_recent` are in-process (`crawl_manager.py`), and nothing
  here changes that. A user whose stream connection lands on Machine A will
  not see live progress events for work Machine B's pool is doing — only
  the crawl's *eventual* effect (the resulting `listings`/`stock_items`
  rows) is consistent, not the live narration of it. Fixing this needs a
  cross-Machine broadcast bridge (e.g. Postgres `LISTEN`/`NOTIFY`), which is
  a separate, larger piece of work, not folded into this change. Accepted
  as a real, known gap rather than silently shipped.

## Design

### `app_config` (global, replaces `config.json`)

A singleton row in the global (non-tenant, non-RLS) schema, alongside
`catalog`/`crawlers`/`listings`:

```sql
CREATE TABLE IF NOT EXISTS app_config (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

`config.load_config()`/`config.save_config()` keep their existing signature
(callers across `crawl_manager.py`, `main.py`, `discover.py`,
`shopify_catalog.py`, and several crawler plugins all treat the return value
as a flat dict via `.get(...)` — none of that changes) but read/write this
row through `db.get_admin_pool()` instead of `CONFIG_FILE`. `config.py`
currently has no dependency on `db.py` — `db.py` imports `config` for the
connection strings, so a module-level `import db` in `config.py` would be
circular. `crawl_manager.py` already breaks an analogous cycle with a
function-local `from config import load_config`; `config.py`'s new
`load_config`/`save_config` do the same with a function-local `import db`.

This keeps every existing call site — including the several that call
`load_config()` synchronously from inside `async def` methods
(`crawl_manager.py`, crawler plugins' `async def search`) — unchanged. That's
still a blocking call inside an event loop, but it's a single-row,
no-network-hop Postgres query, the same class of call already made inline in
async methods throughout this codebase (e.g. `routers/settings.py`'s
`db.get_app_pool()` calls). It is not the pattern that caused the hang fixed
in `06ed220` — that was an unbounded `httpx` call, not a local Postgres round
trip.

### `users.avatar_image` (per-user, replaces `avatar.png`)

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_image BYTEA;
```

Follows the existing per-user-settings pattern already on `users`
(`plex_base_url`, `plex_token`, `anthropic_api_key`, ...) rather than a new
table — same shape, no new abstraction. Being a column on `users`, it
inherits that table's existing RLS policy for free.

`avatar.py`'s `save_avatar`/`get_avatar`/`delete_avatar` take a `user_id` and
read/write this column via `db.get_identity_pool()`, matching
`routers/settings.py`'s `get_user_settings`/`update_user_settings` (same
table, same pool, same direct-SQL style). Image validation/crop/resize logic
is unchanged; only the storage target moves from a file write to a bytea
column write. `routers/session.py`'s three `/auth/avatar` routes pass
`request.state.user_id` through and `GET` returns the bytes via
`Response(content=..., media_type="image/png")` instead of `FileResponse`.

### Schedule convergence (periodic re-sync)

Moving the cron *value* into `app_config` doesn't make an already-running
Machine's `AsyncIOScheduler` (`scheduler.py`) notice a change another Machine
wrote — `scheduler.configure()`/`configure_stock()` are only ever called at
boot (`main.py`'s `startup()`) and from the Machine that handled
`POST /api/settings` (`routers/settings.py`). The other Machine keeps firing
the old cron expression indefinitely.

Fix: a periodic background task (started in `main.py`'s `startup()`,
cancelled in `shutdown()`, same pattern as `crawl_manager`'s worker-pool
task) re-reads `load_config()` and re-applies it via the same
`_configure_schedules()` used at boot, every 5 minutes. `scheduler.configure()`
already removes any existing job before adding, so calling it repeatedly with
an unchanged cron expression is a no-op in effect (same job re-registered) —
safe to call on a timer rather than only on change. `_configure_schedules()`
itself has one bug fixed as part of this: it previously only called
`scheduler.configure(...)`/`configure_stock(...)` when the stored schedule
string was non-empty, so a schedule *cleared* to `""` would never actually
clear an existing job on a Machine that wasn't the one handling the clear —
`scheduler.configure("")` already removes the job and returns early, so the
guard at the call site was actively wrong. It now always calls through and
lets `scheduler.configure`/`configure_stock` handle the empty case.

This bounds staleness to at most 5 minutes on the Machine that didn't handle
the settings write, rather than "until next redeploy."

### Stock sync mutual exclusion (Postgres advisory lock)

`CrawlManager.stock_sync_running` (`crawl_manager.py`) only guards against a
second stock sync starting on the *same* process; it does nothing across
Machines. Two Machines' schedulers converging on the same cron (previous
section) makes simultaneous firing the common case, not an edge case, so
this needs a real cross-process lock, not just a documented risk.

`start_stock_sync()` takes a session-scoped Postgres advisory lock
(`pg_try_advisory_lock`) on a dedicated connection opened outside the
connection pool (`psycopg.connect(config.APP_DATABASE_URL)` — not a pooled
connection, since a pool routinely reuses a physical connection for
unrelated work, which would silently smuggle the lock's session-scoped state
into whatever later request reused that connection). Follows this
codebase's existing advisory-lock convention (`db.py`'s
`pg_advisory_xact_lock(2026080901)`, guarding the `discogs_price` column
migration against two Machines booting concurrently) — same numbering
scheme, a fixed bigint key rather than a name. If the lock is already held
(another Machine's sync is running), `start_stock_sync()` returns `False`
without starting a task, exactly like today's in-process guard. The
dedicated connection is closed when `_sync_stock()` finishes (in a `finally`
block), which releases the session-scoped lock automatically — no explicit
`pg_advisory_unlock` needed.

### Existing `config.json` on the current deployment (migration)

Nothing in this change reads the current Machine's live `/data/config.json`
and writes it into `app_config`. Deploying this branch as-is turns
`load_config()` into `{}` the moment the new code boots, with concrete
consequences: `crawl_schedule`/`stock_schedule` going empty means
`_configure_schedules` stops both cron jobs with no error; `ebay_app_id`/
`ebay_cert_id` going empty means eBay searches return `[]`, which this
codebase's crawl invariant treats as "confirmed no listing" —
**`_drain_one_batch` would then clear every existing eBay listing price**,
not just stop finding new ones. See "Manual one-time Fly setup" below for
the required migration step — it must happen before or immediately after
this deploy, not as a follow-up.

### `fly.toml`

`min_machines_running` moves from `1` to `2` — with a single value, Fly's
floor doesn't guarantee a second Machine stays up, and the point of adding
one here is redundancy/throughput, not an optional extra Fly could scale
back to zero. `auto_stop_machines = "off"` already applies app-wide (unchanged
from the original spec's reasoning: the worker pool runs continuously with
no inbound HTTP request in flight, and Fly's autostop only looks at
fly-proxy traffic).

The `[[mounts]]` block is unchanged — `app.log`, `screenshots/`, and the
bundled `crawlers/__init__.py` marker still need it, one volume per Machine.

### Manual one-time Fly setup (not committed, not run by CI)

Machine count, volume provisioning, and the `config.json` migration are all
imperative, one-time actions — not declared in `fly.toml`, and
`flyctl deploy` won't create a Machine that doesn't already exist or migrate
data on its own. Whoever operates this deployment runs, once, **in this
order**:

1. Before or immediately after deploying this branch, capture the current
   Machine's live settings and write them into `app_config` — either
   re-enter each value through the admin Settings UI once the new code is
   live (simplest, and exercises the real write path), or read
   `/data/config.json` off the running Machine (`fly ssh console -a
   tracktempest-api`, then `cat /data/config.json`) and insert it directly:
   `psql "$DATABASE_URL" -c "INSERT INTO app_config (id, data) VALUES (TRUE,
   '<paste the json>'::jsonb) ON CONFLICT (id) DO UPDATE SET data =
   EXCLUDED.data"`. Skipping this step silently stops both cron schedules
   and, if eBay credentials are among the lost values, clears every eBay
   listing price on the next crawl sweep (see "Existing `config.json`" above).
2. Provision the second Machine and its volume:
   ```bash
   fly volumes create data --region ord --size 1 -a tracktempest-api
   fly scale count 2 -a tracktempest-api
   ```

After that, every subsequent `flyctl deploy` (including the existing
`fly-deploy.yml` CI job, unchanged) rolls out to both Machines automatically.

## Open questions

- None. The remaining forked state (`app.log`, `screenshots/`) and the
  cross-Machine SSE gap are intentionally left as-is per Non-goals above.
