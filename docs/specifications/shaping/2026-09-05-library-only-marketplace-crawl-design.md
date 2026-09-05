# Library-only marketplace crawling

Date: 2026-09-05
Branch: `claude/marketplace-crawler-filtering-gc9408`

## Problem

Every stock sync enqueues a `crawl_queue` row for every item every enabled
store lists, and the worker pool then runs every enabled marketplace crawler
against every one of them. That is the Store tab's "compare against Amazon,
eBay, Discogs" feature working as designed, but the cost scales with the size
of the stores' catalogs, not with anything a user has asked for. Most of those
items are records nobody in the deployment owns, wants, or has saved, and the
prices found for them are never looked at. An admin has no way to trade that
coverage for a shorter queue short of disabling marketplace crawlers outright.

## Scope

An admin setting, `crawl_library_only`, off by default, in the Marketplace
Management section of Settings as a "Library only" checkbox. On, marketplace
crawlers price only stock items *someone* has an interest in: saved (Store
tab's heart), or matching a record in their collection or wantlist. Off is
exactly today's behaviour.

Release rows are untouched in either state. A `crawl_queue` row keyed by
`discogs_id` only ever comes from a user's own `library_items` (collection
sync, wantlist sync, the Refresh button, the scheduled sweep), so it is
already "in someone's library" by construction. The setting is a stock-item
predicate.

Touches:

- `backend/db.py` — `library_stock_item_keys` view; `_library_release_match_sql`
  extracted from `_library_match_fragment`; `_library_interest_exists` and
  `_stock_item_crawlable`; a `library_only` parameter on
  `enqueue_crawl_queue_for_stock_item`, `claim_crawl_queue_batch`,
  `delete_dead_stock_crawl_queue_rows`, `_queue_row_state_sql`, `_queue_totals`,
  `_queue_fanout`, `queue_summary`, `queue_next_for_crawler`.
- `backend/config.py` — `crawl_library_only(config=None)`.
- `backend/crawl_manager.py` — `_drain_one_batch` reads the setting per claim;
  `_sync_stock` reads it per source for the enqueue and once more for its
  end-of-run sweep.
- `backend/routers/settings.py` — the field on `GET`/`POST /api/settings`;
  the off-to-on sweep; the crawler toggle's sweeps pass the setting.
- `backend/routers/stock.py` — the save endpoint queues the saved item if it
  has no row; `_sync_collection_blocking` does the same for a user's matching
  items as a sync completes.
- `backend/routers/queue.py` — passes the setting to the Queue tab's queries.
- `frontend/src/views/Settings.tsx`, `frontend/src/api/types.ts` — a `checkbox`
  row type and the "Library only" row.
- Tests in `backend/tests/test_crawl_queue.py`, `test_settings_router.py`,
  `test_queue_router.py`, `test_crawl_manager.py`, and
  `frontend/src/test/settings.test.tsx`.

## Design

### Where the gate lives

The same three places the enabled-store gate lives, for the same reasons
[`2026-08-10-dead-stock-crawl-jobs-design.md`](2026-08-10-dead-stock-crawl-jobs-design.md)
gives for that one:

- **Claim.** `claim_crawl_queue_batch` skips a stock row nobody wants. This is
  what makes the setting take effect on the next batch with no restart, the
  property [`2026-08-14-per-item-crawler-fanout-design.md`](2026-08-14-per-item-crawler-fanout-design.md)
  establishes for `crawlers.enabled`. It is also what makes *un*-saving an item,
  or a wantlist sync dropping a record, stop its crawl without any sweep.
- **Enqueue.** `enqueue_crawl_queue_for_stock_item` inserts nothing for such an
  item, so a stock sync under the setting leaves the queue as short as the
  crawl it will actually do.
- **Sweep.** `delete_dead_stock_crawl_queue_rows` deletes pending stock rows
  that fail it. The claim would skip them anyway; the sweep is so they do not
  sit pending — unclaimable, but counted — in the claim index and the Queue tab.

All three go through one predicate, `_stock_item_crawlable(item_key_expr,
library_only)`: the enabled-store `EXISTS`, and, when `library_only`, `AND`
the interest `EXISTS`. Composed in Python from the bool rather than bound as a
parameter, so the off case is the identical statement that ran before the
setting existed — no view join for the planner to weigh, and nothing for a
generic plan to fail to prune. `library_only` is only ever the setting's value
as read by the caller, never request-derived.

The setting is read fresh at each decision point rather than cached in the
worker: `_drain_one_batch` already loads config per claim for the stranded
threshold, and the flag rides the same read; `_sync_stock` reads it per source,
as it already re-reads the enabled list per source, so a flip mid-run governs
the sites still to come. `load_config()` goes through the admin pool, so every
read happens before the app-pool connection is borrowed, the ordering
[`2026-08-25-admin-queue-tab-design.md`](2026-08-25-admin-queue-tab-design.md)
already requires.

### What "someone wants it" means

`library_stock_item_keys` is a view of `item_key`s — one column, distinct — for
which some user has a `stock_item_saves` row, or a `library_items` row flagged
`in_collection` or `in_wishlist` whose catalog release matches the stock row by
`_library_release_match_sql`: case-folded artist, and a title that is equal or
that the listing extends with a space-separated qualifier. That is the rule the
Store tab's library filter applies (`_library_match_fragment`), extracted so the
two share one definition; what the gate crawls is what that filter shows.

Only the flags count. A `library_items` row with neither set (a release that
was in a wantlist and is now only remembered) is nobody's interest.

### Why a view, and what it costs

The worker reads the queue on an unscoped `app_user` connection, and
`library_items` and `stock_item_saves` are `FORCE ROW LEVEL SECURITY` tables
whose policy keys on `app.user_id` — an unscoped connection sees no rows of
either, which is the isolation the multi-tenant design depends on. The claim
cannot ask "does any user want this" through those tables.

A view is read with its owner's identity (`security_invoker` off, the
default), and the owner is the admin role that runs `init_tenant_schema`. That
role already has to bypass RLS — `_ensure_role`'s `ALTER ROLE app_identity ...
BYPASSRLS` fails at boot otherwise — so `app_user` reading the view sees every
user's rows, while reading the base tables it still sees none. Verified
directly against Postgres 16 before building on it: a `NOBYPASSRLS` role with
`SELECT` on a forced-RLS table and on a superuser-owned view over it counts
zero rows through the table and every row through the view, scoped or not.
No policy changes, no new grant on either base table; `GRANT SELECT` on the
view is the whole of it.

That is deliberately the crawl queue's only cross-tenant read, and it is
shaped to carry as little as it can: no `user_id`, no other column, `UNION`
rather than `UNION ALL`. What it discloses is "some user in this deployment
wants this record", which the setting discloses anyway through which items
get crawled. It does not say who.

The view is created in `init_tenant_schema`, after `TENANT_SCHEMA` (it reads
tables that string creates) and on that same admin connection (ownership is
the mechanism). `CREATE OR REPLACE VIEW` is idempotent while the column list
holds, so it re-runs on every boot like the rest of the DDL.

### The Queue tab

`unactionable_rows` gains a third cause under the setting: a stock row nobody
wants. `_queue_row_state_sql` folds it into `live` through the same hoisted
`LEFT JOIN` shape the enabled-store gate uses, joining the view once for the
report rather than probing it per row; the view is already distinct per key,
so the join cannot fan a row out. Off, the join is not written at all — the
statement is the one the tab's plan regression test pins. `_queue_fanout` and
`queue_next_for_crawler` take the composed predicate, so the per-crawler units
and the "next up" list agree with what the claim will do.

### Switching it on, and off

`POST /api/settings` runs the sweep on the off-to-on edge only, after the save,
and returns the count as `discarded` alongside `ok`. After the save so a worker
claiming concurrently already reads the setting as on and cannot take a row
the sweep is about to delete. Edge-only because the setting rides the same
debounced auto-save as every other field — the endpoint runs on each edit to
any of them, and sweeping every time would re-scan the pending stock backlog
for each keystroke in a schedule box. The crawler toggle's two sweeps pass the
setting too, so toggling any crawler while it is on clears unwanted rows
whatever store they came from.

Switching off does nothing beyond saving. The rows it would restore are
exactly what the next stock sync enqueues — every item every enabled store
lists, done rows revived — so the next scheduled or manual store refresh is
the restore, and the Settings description says so. An immediate re-enqueue
would be that same full re-crawl, started from a settings save instead of the
Store Management Refresh button that already exists for it.

### Interest added means a row exists

The gate alone leaves a hole, found in review. A save only writes
`stock_item_saves`; a collection or wantlist sync only enqueues release-keyed
rows; the one producer of stock-item rows is `_sync_stock`, once per store
refresh. So after the switch-on sweep deleted an item's row (or `_sync_stock`
never inserted one) while nobody wanted it, saving it or syncing a matching
record changed only the view — "picked up at the next claim" held for a row
still pending, and for nothing else.

Two insert-if-absent helpers close it. `enqueue_crawl_queue_for_saved_stock_item`
runs from the save endpoint, in the save's own transaction, for that one key.
`enqueue_crawl_queue_for_library_stock_items` runs at the end of a collection
sync, under the user's `user_scope`, for the stock items matching that user's
library by `_library_match_fragment` — the view is deliberately not used here,
because it cannot say whose interest a key is, and a sync should only restore
rows for the records it brought in. Both keep the enabled-store gate.

Insert-if-absent, not the revive `enqueue_crawl_queue_for_stock_item` does: a
`done` row is the record that the item was already priced, reviving it would
make every save a marketplace re-crawl, and the next stock sync revives it
with everything else. Neither asks the setting: with it off every live item
already has a row, so both are no-ops rather than something to switch off.

### Boot-time callers keep the default

`register_crawler`'s kind conversions call the sweep at boot with no setting
in hand and get `library_only=False`; they are about a store changing kind, and
the rows the setting would additionally sweep are caught by the next stock
sync's own sweep. The parameter defaults to `False` on every function that
takes it for that reason, and so the many test call sites that predate the
setting keep their meaning; the paths that matter all pass it explicitly and
are tested doing so.

## Consequences

- With the setting on, a record nobody wants is never priced against
  marketplaces, so the Store tab's comparison rows for it stay empty until
  someone saves it or adds it to their library. A save queues it in the same
  transaction; a library sync queues its matches as it completes. Either way
  the next claim picks it up, with no store refresh needed.
- Un-saving an item, or a wantlist sync dropping a record, stops its crawl at
  the next claim. Its pending row stays until a sweep runs (any stock sync, any
  crawler toggle); the Queue tab reports it as unactionable meanwhile.
- The claim under the setting evaluates the interest `EXISTS` per scanned row.
  After the switch-on sweep and with the enqueue gate in place, the pending
  stock rows are nearly all wanted ones, so the scan does not walk far past its
  `LIMIT`. Both arms of the view are index-served: `stock_item_saves`'s primary
  key, and `stock_items_item_key_idx` then `catalog_artist_lower_idx` for the
  library arm.
- Prices already found for unwanted items are left as they are. This design is
  about what gets crawled next, not about what was crawled.
- `in_collection` never auto-clears (see CLAUDE.md), so a record once synced
  from a collection stays "wanted" even after leaving the real Discogs
  collection. That is the existing library semantics, inherited rather than
  re-decided here.

## Testing

`backend/tests/test_crawl_queue.py`, through the real `app_user` role (the
superuser `admin_conn` bypasses RLS and would prove nothing about the view):

- The view is readable across users by an unscoped `app_user` connection that
  sees neither base table, and carries only keys.
- The view applies the Store tab's rule: prefix-with-space matches, prefix
  without a space does not; a wantlist flag counts; a flagless row does not.
- Claim: skips an unwanted stock row under the setting and takes it with the
  setting off; still takes release rows; takes a library match by title rule;
  still honours the enabled-store gate.
- Enqueue inserts nothing for an unwanted item under the setting.
- Sweep deletes pending unwanted stock rows under the setting, leaves release
  and wanted rows, deletes nothing extra with it off, and leaves `in_progress`
  rows alone.

- Interest restores a missing row: a save inserts a pending row for an item
  with none, leaves a `done` row alone, and inserts nothing for an item no
  enabled store lists; a library sync inserts rows for that user's matching
  items only.

`backend/tests/test_stock_router.py`: saving an item queues it when it has no
row and leaves a `done` row alone, across repeated saves.

`backend/tests/test_settings_router.py`: default off; round trip; the
off-to-on sweep with its count and log line; no sweep when already on; no
sweep or enqueue on switching off; a saved item survives the sweep; the crawler
toggle's sweep honours the setting.

`backend/tests/test_queue_router.py`: unwanted rows count as unactionable under
the setting and claimable without it, in totals and per-crawler units; `next`
excludes them; the plan regression check holds with the view joined.

`backend/tests/test_crawl_manager.py`: the worker leaves an unwanted stock row
unclaimed under the setting and claims it on the very next batch once someone
saves it; a stock sync under the setting enqueues only wanted items; a
collection sync queues the store items matching the records it synced, and
only the ones with no row.

`frontend/src/test/settings.test.tsx`: the checkbox renders for an admin and
reflects the saved value, auto-saves like every other field, and is absent for
a non-admin.

## Spec drift check

- [`2026-08-10-dead-stock-crawl-jobs-design.md`](2026-08-10-dead-stock-crawl-jobs-design.md):
  its shared predicate is now one half of `_stock_item_crawlable`, and the
  sweep takes a parameter — amended at its head.
- [`2026-08-14-per-item-crawler-fanout-design.md`](2026-08-14-per-item-crawler-fanout-design.md):
  "the stock source gate stays" and the invariant list — amended in place.
- [`2026-08-25-admin-queue-tab-design.md`](2026-08-25-admin-queue-tab-design.md):
  `unactionable_rows`'s two causes become three under the setting — amended.
- [`../../superpowers/specs/2026-06-27-discogs-browser-design.md`](../../superpowers/specs/2026-06-27-discogs-browser-design.md):
  the settings field table gains the row.
- `CLAUDE.md`'s dispatch invariant gains the setting and the view.
