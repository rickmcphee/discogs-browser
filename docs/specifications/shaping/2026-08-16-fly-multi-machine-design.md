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
  **Superseded for `app.log`** by
  [`2026-08-17-unified-log-store-design.md`](2026-08-17-unified-log-store-design.md):
  once the app had invited users beyond the operator, being able to see one
  merged log across Machines (and retain history past a single process's
  lifetime) outweighed the original reasoning. `screenshots/` is unaffected
  and remains per-machine.
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

**Amendment (2026-08-17, production incident):** "no-network-hop" was wrong
for this deployment. `get_admin_pool()` (`config.DATABASE_URL`) and
`get_app_pool()` (`config.APP_DATABASE_URL`) both resolve to Neon, not a
co-located Postgres — every call through either is a real network round
trip through Neon's pooler, not a local one, and unlike the `06ed220`
`httpx` call none of them has a timeout of its own.

Two distinct call paths were involved, not one. `load_config()` (via
`get_admin_pool()`) is read on every single crawl unit: once in
`_paced_search`'s pacing delay and once in `_record_site_result`'s
failure-limit check — two Neon round trips per unit, the highest-frequency
offender since a batch can be several units. Separately, `_drain_one_batch`
opens its own `get_app_pool()` connections for the crawl-queue writes —
once to claim a batch, then per row for target resolution and again for
its terminal status write, and per unit for the listing result write — a
different pool, a coarser-but-still-repeated cadence, not tied to
`load_config()` at all. Observed in production: a crawler tripping its
consecutive-failure limit logged at 01:34:25; Fly reported `/api/health`
failing at 01:34:36 on the same Machine — consistent with this process's
single event loop stalling on a burst of blocking calls from either or
both paths for long enough to miss the healthcheck's 5s timeout, since
`/api/health` has no DB dependency of its own but still has to be
dispatched through that same loop.

Fixed by wrapping every `get_app_pool()`/`get_admin_pool()` call reachable
from the worker loop (`start_worker_pool`, `_drain_one_batch`,
`_paced_search`, `_record_site_result`) in `asyncio.to_thread`, matching the
`_sync_collection_blocking`/`start_stock_sync` pattern already used
elsewhere in this file (see the "Stock sync mutual exclusion" amendment
above). `_sync_stock`'s own remaining inline `get_app_pool()` calls and
`_sync_collection`'s blocking `time.sleep(1.1)` barcode pacing are not part
of this fix — separate, longer-running background tasks, not the tight loop
implicated in this incident.

**Amendment (2026-08-17, GitHub Copilot PR review):** the first pass of this
fix was incomplete and, separately, introduced a new bug — both caught in
review before merge.

Incomplete: `_paced_search` calls `plugin.search()`, and the two eBay
plugins (`crawlers/ebay.py`, `crawlers/ebay_general.py`, pooled under
`failure_domain = "ebay-browse-api"`) each call `load_config()` synchronously
as the first line of their own `async def search`, before `_paced_search`'s
own offloaded config read ever runs. Every eBay crawl unit could therefore
still block the event loop on the same kind of Neon round trip this fix was
meant to eliminate. Fixed by offloading those two calls the same way. (Four
other crawlers' `load_config()` calls — `amoeba.py`, `angryyoungandpoor.py`,
`sgrecordshop.py`, `asbestosrecords.py` — are `async def crawl_catalog`, only
reachable from `_sync_stock`, not the worker loop; left blocking, same as
`_sync_stock`'s own calls two paragraphs up.)

New bug: making `_record_site_result` async gave its `load_config()` call a
real yield point it didn't have before (the previous synchronous version
ran to completion with no `await`, so two calls could never interleave).
Two crawler_ids sharing a failure domain — the eBay pair is the only
current example — can now have their `_record_site_result` calls
interleave: a failure's read-modify-write can land after a chronologically
later success's reset, resurrecting a stale failure count instead of the
reset sticking. `_site_locks` (used by `_paced_search` to pace requests)
does not already prevent this — it's keyed by `crawler_id`, so the two eBay
crawler_ids get two separate locks and their *requests* can run
concurrently; only their shared-counter *bookkeeping* needed serializing.
Fixed by adding a second lock, `CrawlManager._site_result_locks`, keyed the
same way `_domain_peers` groups crawler_ids (by domain, not crawler_id), so
`_record_site_result` now runs its whole body — config read and counter
mutation — under that per-domain lock.

**Amendment (2026-08-17, GitHub Copilot PR review):** four more instances of
the pattern this whole change is about, all in `_drain_one_batch`: the
`asyncio.to_thread` calls that claim a batch (`_claim_batch`), mark a
targetless row done (`_mark_done`), and write a row's terminal status or
listing result (`resolve_row`'s `_write`, `_write_result`) all write to
`crawl_queue`, and `asyncio.to_thread`'s cancellation only stops a callable
that hasn't started running yet — once the worker thread has picked it up,
cancelling the awaiting task abandons the result without stopping the
write. `stop_worker_pool()`'s `task.cancel()` can therefore let one of
these commits land after the worker has already exited, with nothing left
to act on the result: the row reads `'in_progress'` forever with no reclaim
path, the same accepted gap `claim_crawl_queue_batch`'s docstring documents
for a Machine crash or a genuinely hung worker (that docstring previously
claimed a crash "rolls back the open transaction and self-heals" — wrong
for this codebase, since the claim's own `UPDATE` commits immediately as a
short, separate transaction from whatever processes the row afterward;
fixed alongside this amendment, caught in a later review round).

First pass fixed this with `_to_thread_uncancelable`, a wrapper that
shields the underlying thread from cancellation and then awaits it anyway
before re-raising, guaranteeing the *write itself* finishes. Review caught
that this was still incomplete for two of the four: guaranteeing a write
finishes doesn't guarantee anything *depending on it* also runs before the
cancellation propagates.

- `_claim_batch`: `_to_thread_uncancelable` lets the claim's `UPDATE ...
  SET status = 'in_progress'` commit, but the resulting `rows` are still
  discarded the moment it re-raises — `_drain_one_batch` never receives
  them, so nothing ever resolves the rows it just claimed. Fixed
  differently: on cancellation, the (already-committed) claimed rows are
  read back from the awaited task and explicitly reverted via a new
  `db.revert_crawl_queue_claim(conn, queue_ids)` (`status = 'pending',
  claimed_by = NULL, claimed_at = NULL`, leaving `pending_crawler_ids`/
  `available_at` untouched) before the cancellation is allowed to
  propagate — undoing the claim rather than trying to make it durable.
- `_write_result`: `_to_thread_uncancelable` guarantees the listing commit
  finishes, but the `resolve_row` call that must follow it (for the row's
  last unit) is separate code after the `await` — a cancellation landing
  in between skips it, leaving the row `'in_progress'` with its listing
  already correct. Fixed by generalizing the shielding: a new `_shielded`
  helper wraps an arbitrary coroutine (not just a single `to_thread` call)
  to completion despite cancellation, and `_write_result` plus the
  following `resolve_row` are now one coroutine passed to it, so a
  cancellation landing anywhere in that pair still lets both finish before
  propagating. (`_to_thread_uncancelable` itself became a thin wrapper —
  `_shielded(asyncio.to_thread(func))` — for the two call sites,
  `_mark_done` and `resolve_row`'s own `_write`, where the write already is
  the entire terminal unit with nothing depending on it afterward.)

Each fix has a regression test that reproduces the exact race (cancels
`_drain_one_batch` mid-flight via a deliberately slowed DB call, using a
`threading.Event` to cancel precisely once the slow call has started
rather than guessing a sleep duration) and was verified to fail without
its fix before the fix was added back.

**Amendment (2026-08-17, GitHub Copilot PR review, architectural):** a
fourth review round found the same class of gap again — this time in
`_paced_search`'s own `load_config()` read, `_record_site_result`'s
`load_config()` read, and `_resolve_target` — none of which write to
`crawl_queue` themselves, but a cancellation landing during any of them
still lets `CancelledError` propagate out of `_process_claimed_rows` (as
it then was, inlined in `_drain_one_batch`) before that row's terminal
write ever runs. Four rounds of finding the next unshielded `await` in the
same claimed row's path is the "3+ narrow fixes each revealing a new
instance elsewhere" signal for a wrong-grained fix, not a wrong-grained
review: individually wrapping each write, as the two amendments above did,
can never close this class of gap, because the vulnerability isn't in any
one call — it's every `await` a claimed row's processing passes through
before its terminal write.

Restructured accordingly: `_drain_one_batch` now only claims the batch
(reverting on cancellation, as before — cheap and immediate, since nothing
is at stake yet). Everything after a successful claim — resolving targets,
running each crawler, recording results, writing listings, resolving each
row's terminal status — moved into a new method, `_process_claimed_rows`,
and `_drain_one_batch` runs it as a single `_shielded()` call. A
cancellation landing anywhere inside `_process_claimed_rows` now lets the
*entire* claimed batch finish processing before propagating, not just
whichever write happened to be in flight. This also let the per-call
`_shielded`/`_to_thread_uncancelable` wrapping added in the two amendments
above be removed — with the whole method shielded by its only caller,
wrapping the individual DB calls inside it again was redundant, so they're
back to plain `asyncio.to_thread` calls, and `_to_thread_uncancelable`
itself was deleted as dead code.

Trade-off, chosen deliberately over continuing to patch instances: once a
batch is claimed, `stop_worker_pool()`'s `task.cancel()` no longer stops a
worker until that batch (batch_size rows × their eligible crawlers,
including real crawler network requests and pacing delays) finishes
processing, rather than at whichever `await` cancellation happened to
land on. Graceful shutdown gets correspondingly slower in the worst case,
bounded by how long one claimed batch takes — accepted as the right price
for actually closing this class of gap instead of leaving it open at
whichever call site hasn't been reviewed yet.

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

**Amendment (2026-08-17, GitHub Copilot PR review):** the periodic resync
turned a latent bug into a recurring one. `routers/settings.py`'s
`update_settings()` calls `save_config(config)` — persisting the new cron
string to `app_config` — *before* calling `scheduler.configure(...)` to
validate it; an invalid cron still gets a 400 back to the caller, but the
bad string is already saved. `scheduler.configure()` itself compounded
this: it removed the *current* job unconditionally, before attempting to
parse the replacement, so a parse failure left no job at all. Before this
change that was a one-machine, one-request blast radius; with a 5-minute
resync reading the same bad value from Postgres, it now repeats forever on
every Machine, permanently discarding a previously-working schedule.

Fixed both ends: `scheduler.configure()`/`configure_stock()` now parse the
new cron expression *first* and only remove/replace the existing job once
the replacement is known-valid — a parse failure leaves the current job
untouched. `update_settings()` validates both cron strings (via the same
`CronTrigger.from_crontab` parse) before calling `save_config()`, so an
invalid value is rejected before it ever reaches Postgres, not just before
it takes effect on the request's own Machine.

### Stock sync mutual exclusion (Postgres advisory lock)

`CrawlManager.stock_sync_running` (`crawl_manager.py`) only guards against a
second stock sync starting on the *same* process; it does nothing across
Machines. Two Machines' schedulers converging on the same cron (previous
section) makes simultaneous firing the common case, not an edge case, so
this needs a real cross-process lock, not just a documented risk.

`start_stock_sync()` takes a session-scoped Postgres advisory lock
(`pg_try_advisory_lock`) on a dedicated connection opened outside the
connection pool (`psycopg.connect(config.APP_DATABASE_URL, autocommit=True)`
— not a pooled connection, since a pool routinely reuses a physical
connection for unrelated work, which would silently smuggle the lock's
session-scoped state into whatever later request reused that connection).
Follows this codebase's existing advisory-lock convention (`db.py`'s
`pg_advisory_xact_lock(2026080901)`, guarding the `discogs_price` column
migration against two Machines booting concurrently) — same numbering
scheme, a fixed bigint key rather than a name. If the lock is already held
(another Machine's sync is running), `start_stock_sync()` returns `False`
without starting a task, exactly like today's in-process guard. The
dedicated connection is closed when `_sync_stock()` finishes (in a `finally`
block), which releases the session-scoped lock automatically — no explicit
`pg_advisory_unlock` needed.

**Amendment (2026-08-17, commit `9cd886c`):** two corrections to the above,
found in re-review. (1) The connection must be opened with
`autocommit=True`, not left at psycopg's default — a default connection
sits idle-in-transaction for the sync's full duration, and a managed
Postgres's `idle_in_transaction_session_timeout` can kill that backend
mid-sync, silently releasing the advisory lock and readmitting the exact
concurrent `replace_stock_items()` this lock exists to prevent. (2) `connect()`
and the `pg_try_advisory_lock` query are both blocking calls; `start_stock_sync()`
runs them via `run_in_threadpool`, matching the `_sync_collection_blocking`
pattern already used elsewhere in `crawl_manager.py`, rather than calling them
inline on the event loop as this section originally described.

**Amendment (2026-08-17, GitHub Copilot PR review):** `config.APP_DATABASE_URL`
is derived from `DATABASE_URL`, and this deployment's `DATABASE_URL` is Neon's
*pooled* (PgBouncer transaction-mode) connection string (see this repo's
original deployment spec, "Neon (Postgres)" section). A session-scoped
advisory lock is meaningless through a transaction pooler: PgBouncer can
multiplex `lock_conn`'s logical session onto a different backend per
statement, so `pg_try_advisory_lock` and the later `.close()` are not
guaranteed to touch the same real Postgres session — the lock can outlive
the connection that took it (wedging stock sync until a database-side
timeout, if any, expires) or be silently dropped early. The lock connection
must bypass the pooler entirely.

Fix: a new `config.DIRECT_APP_DATABASE_URL`, derived the same way
`APP_DATABASE_URL` already is (`_with_userinfo` swapping in the `app_user`
role) but from a new `DIRECT_DATABASE_URL` env var — Neon's *unpooled*
connection string — instead of `DATABASE_URL`. Defaults to `DATABASE_URL`
itself when unset, which is correct for local/CI Postgres (nothing pools
in front of it there) but is a real gap if the operator forgets to set it
against Neon: see "Secrets," below, and the Fly setup checklist — this is
now a required secret, not optional. `start_stock_sync()`'s dedicated
connection uses `config.DIRECT_APP_DATABASE_URL`.

### Existing `config.json` on the current deployment (automatic migration)

The current Machine's live `/data/config.json` has no automatic path into
`app_config` unless something migrates it. Left undone, `load_config()`
turns into `{}` the moment this branch boots, with concrete consequences:
`crawl_schedule`/`stock_schedule` going empty means `_configure_schedules`
stops both cron jobs with no error; `ebay_app_id`/`ebay_cert_id` going
empty means eBay searches return `[]`, which this codebase's crawl
invariant treats as "confirmed no listing" — **`_drain_one_batch` would
then clear every existing eBay listing price**, not just stop finding new
ones.

**Amendment (2026-08-17, GitHub Copilot PR review):** the original version
of this section put the migration on the operator as a manual step "before
or immediately after" deploy. That doesn't actually work: `app_config`
itself doesn't exist until this branch's `init_global_schema()` runs, so
"before deploy" has no table to insert into, and "immediately after" races
the worker pool — `startup()` creates the table and starts draining
`crawl_queue` in the same function, before a human can plausibly run a
manual `psql`/UI step, so eBay rows could already be cleared by the time
the operator finishes typing.

Fixed by making the migration automatic and race-safe instead of manual: a
one-shot step in `startup()`, right after `init_global_schema()` and before
anything reads `load_config()` for real work, that — if `/data/config.json`
still exists on this Machine's local disk and `app_config`'s row is still
empty — reads the file and writes it into `app_config`, guarded by
`pg_advisory_xact_lock`, the same pattern (and the same lock-numbering
convention) as `db.py`'s existing `discogs_price` one-shot migration:
double-checked (empty-row check outside the lock, re-checked under it), so
two Machines booting concurrently — the exact scenario this whole change
is for — can't both migrate, and a Machine with no local `config.json` (the
second Machine, which has its own empty volume) is a no-op. Once
`app_config` holds real data, every subsequent boot on every Machine skips
this entirely. No manual operator step is needed for this specific risk
any more; removed from "Manual one-time Fly setup" below.

### `fly.toml`

`min_machines_running` moves from `1` to `2` — with a single value, Fly's
floor doesn't guarantee a second Machine stays up, and the point of adding
one here is redundancy/throughput, not an optional extra Fly could scale
back to zero. `auto_stop_machines = "off"` already applies app-wide (unchanged
from the original spec's reasoning: the worker pool runs continuously with
no inbound HTTP request in flight, and Fly's autostop only looks at
fly-proxy traffic).

The `[[mounts]]` block is unchanged — `screenshots/` and the bundled
`crawlers/__init__.py` marker still need it, one volume per Machine.
(`app.log` no longer does: superseded by
[`2026-08-17-unified-log-store-design.md`](2026-08-17-unified-log-store-design.md),
which moved logs into Postgres.)

### Manual one-time Fly setup (not committed, not run by CI)

Machine count and volume provisioning are imperative, one-time actions —
not declared in `fly.toml`, and `flyctl deploy` won't create a Machine that
doesn't already exist. The `config.json` migration is no longer a manual
step here (see "Automatic migration" above); what remains manual is the new
secret the stock-sync lock needs and the Machine/volume provisioning
itself. Whoever operates this deployment runs, once, **in this order**:

1. **Before scaling to a second Machine**, set the unpooled connection
   string the advisory lock requires — get it from Neon's dashboard (the
   connection string *without* `-pooler` in the hostname):
   ```bash
   fly secrets set DIRECT_DATABASE_URL="<Neon's unpooled connection string>" -a tracktempest-api
   ```
   Skipping this leaves `DIRECT_DATABASE_URL` defaulted to `DATABASE_URL`
   (Neon's pooled endpoint), which silently defeats the stock-sync lock's
   mutual exclusion — see "Stock sync mutual exclusion" above.
2. Provision the second Machine and its volume:
   ```bash
   fly volumes create data --region ord --size 1 -a tracktempest-api
   fly scale count 2 -a tracktempest-api
   ```

After that, every subsequent `flyctl deploy` (including the existing
`fly-deploy.yml` CI job, unchanged) rolls out to both Machines automatically.

## Open questions

- None. The remaining forked state (`screenshots/`; `app.log` is no longer
  forked, see the Non-goals amendment above) and the cross-Machine SSE gap
  are intentionally left as-is per Non-goals above.
