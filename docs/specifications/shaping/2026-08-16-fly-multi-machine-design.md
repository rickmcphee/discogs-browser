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
- Document the one-time, imperative Fly setup (second volume, machine count)
  that isn't part of `flyctl deploy` and so isn't expressible in a committed
  file.

## Non-goals

- Auto-scaling beyond two Machines, multi-region, or automated failover.
  Two fixed Machines in one region is the target shape, matching the
  original spec's assumption that this deployment doesn't need more.
- Moving `app.log` or `screenshots/` off local disk. Both are per-machine by
  nature; forking them across Machines isn't a bug.
- Coordinating APScheduler cron firing across Machines. Both Machines will
  independently fire the same cron tick and both will call
  `sweep_enqueue`/`start_stock_sync`, but every enqueue underneath is
  `ON CONFLICT ... DO UPDATE` (see `db.py`'s `crawl_queue`/`stock_items`
  upserts), so a duplicate fire just re-upserts the same rows. No new
  coordination needed.

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

Machine count and volume provisioning are imperative `flyctl` actions, not
declared in `fly.toml`, and `flyctl deploy` won't create a Machine that
doesn't already exist. Whoever operates this deployment runs, once:

```bash
fly volumes create data --region ord --size 1 -a tracktempest-api
fly scale count 2 -a tracktempest-api
```

After that, every subsequent `flyctl deploy` (including the existing
`fly-deploy.yml` CI job, unchanged) rolls out to both Machines automatically.

## Open questions

- None. The remaining forked state (`app.log`, `screenshots/`) is
  intentionally left as-is per Non-goals above.
