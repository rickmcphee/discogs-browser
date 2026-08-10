# Stop crawling a store the moment it is disabled

Date: 2026-08-09
Branch: `claude/stop-crawls-disabled-stores-ff6645`

## Problem

Disabling a crawler in Settings' "Crawler Management" / "Store Management"
tables (`PATCH /crawlers/{id}` → `db.set_crawler_enabled`) only stops *future*
enqueues. Work already in motion for that store carries on, on two independent
paths:

- **Price/release crawl.** `db.claim_crawl_queue_batch` selects pending rows
  with no reference to `crawlers.enabled`, and the worker pool's
  `plugins_by_crawler_id` is a boot-time snapshot built from
  `get_enabled_crawlers(conn)` (`crawl_manager.start_worker_pool`). Every row
  already queued for the disabled store keeps getting claimed and crawled,
  indefinitely — a collection-wide "Refresh prices" can leave tens of thousands
  of rows that a disable does nothing about.
- **In-stock catalog sync.** `_sync_stock` reads the enabled catalog crawlers
  once at the top of the run and then iterates that snapshot. A store disabled
  mid-run is still visited when the loop reaches it.

There is also a latent bug on the inverse operation that this change makes easy
to hit. Because the pool loads plugins for *enabled* crawlers only, a crawler
enabled after boot has no entry in `plugins_by_crawler_id`; its claimed rows hit
the `plugin is None` branch in `_drain_one_batch` and are marked `done` with no
listing written, no error, and no log line. Disable-then-re-enable within one
session walks straight into it.

## Scope

Touches:

- `backend/db.py` — `claim_crawl_queue_batch` gains a live enabled gate; new
  `delete_pending_crawl_queue_for_crawler`; new `get_crawlers`;
  `enqueue_crawl_queue` and `enqueue_crawl_queue_for_stock_item` gain an
  enabled guard.
- `backend/routers/settings.py` — `update_crawler` purges pending rows on
  disable and returns the count.
- `backend/crawl_manager.py` — `start_worker_pool` loads all release crawlers;
  `_sync_stock` re-checks enabled state per source.
- `frontend/src/api/client.ts` — `setCrawlerEnabled` returns the response body.
- `frontend/src/views/Settings.tsx` — show the discarded count next to the
  toggled row.
- Tests: `backend/tests/test_crawl_queue.py`,
  `backend/tests/test_settings_router.py`,
  `backend/tests/test_crawl_manager.py`,
  `frontend/src/test/settings.test.tsx`.

Out of scope:

- **Existing data.** `listings` and `stock_items` rows already recorded for a
  disabled store are left alone. Disabling stops crawling; it does not purge
  data. Hiding a store's results from the UI is the separate, already-shipped
  `hiddenCrawlerIds` filter.
- **Aborting an in-flight request.** See "Stop granularity" below.

## Decisions carried from brainstorming

- **Stop granularity: finish the current unit, stop before the next.** The
  in-flight `plugin.search()` or catalog-page fetch runs to completion; the
  crawler is then skipped for every subsequent item and every subsequent
  catalog source. Cancelling the running task instead would mean tearing a
  Playwright context out from under a plugin mid-navigation, with partial
  state and a failure path the circuit breaker would misread as a site fault.
  Worst-case overrun on the price path is one batch per worker — with the
  current defaults, 2 workers × `batch_size=5` = 10 items.
- **Pending rows are deleted, not parked.** Leaving them `pending` behind a
  claim-time filter would resume them automatically on re-enable, but the
  per-user pending count on the crawl-status UI would never reach zero while
  the store stayed off. Honest count wins; the cost is that re-enabling does
  not resume queued work (see "Consequences").
- **Load all plugins, gate on `enabled` at runtime.** Fixes the silent-drop
  path above, and makes re-enabling work without an app restart. The
  alternative — having `PATCH /crawlers/{id}` reload and re-import the
  registry — mutates a dict that two running worker tasks read and forces
  `_set_failure_domains` to re-run mid-flight, for the same outcome.
- **Feedback is a log line plus a count in the PATCH response.** No new SSE
  event type.

## Backend design

### 1. Live gate at claim time

`db.claim_crawl_queue_batch` is the authoritative stop: it re-evaluates on
every batch, so the first claim after the toggle already skips the store. Add
an enabled subquery alongside the existing cooldown exclusion:

```sql
SELECT id FROM crawl_queue
WHERE status = 'pending'
  AND crawler_id IN (SELECT id FROM crawlers WHERE enabled)
  {exclusion_clause}
ORDER BY (item_key IS NOT NULL), requested_at, id
LIMIT %(limit)s
FOR UPDATE SKIP LOCKED
```

Unconditional, unlike `exclusion_clause` — there is no "no crawlers disabled"
case worth branching on, and the subquery is a small indexed scan of a table
with one row per crawler. `FOR UPDATE` locks only `crawl_queue`; an `IN`
subquery in `WHERE` is not a locked relation, so no `crawlers` row is locked by
a claim. `crawlers` and `crawl_queue` are both in `GLOBAL_SCHEMA` with no RLS,
so this reads the same from an app-pool connection and from a `user_scope` one.

This is deliberately the same shape as the circuit breaker's per-site cooldown
exclusion, which already exists on this query for the same purpose: stop
sending work to a site without disturbing anything else about the queue.

### 2. Purge pending rows on disable

New helper in `db.py`:

```python
def delete_pending_crawl_queue_for_crawler(conn, crawler_id: int) -> int:
    return conn.execute(
        "DELETE FROM crawl_queue WHERE crawler_id = %s AND status = 'pending'",
        [crawler_id],
    ).rowcount
```

`status = 'pending'` only. An `in_progress` row is held by a worker's open
transaction — it is the "current item" that finishes by design, and deleting it
would block on that worker's row lock until it committed.

`routers/settings.update_crawler`:

```python
@router.patch("/crawlers/{crawler_id}", dependencies=[Depends(require_admin)])
def update_crawler(crawler_id: int, body: CrawlerUpdate):
    with db.get_app_pool().connection() as conn:
        db.set_crawler_enabled(conn, crawler_id, body.enabled)
        discarded = 0
        if not body.enabled:
            discarded = db.delete_pending_crawl_queue_for_crawler(conn, crawler_id)
        conn.commit()
    if discarded:
        log.info("Crawler %d disabled: %d pending crawl jobs discarded", crawler_id, discarded)
    return {"ok": True, "discarded": discarded}
```

Both statements in one transaction, so the flag flip and the purge commit
together — a worker cannot observe a window where the crawler is still enabled
but its queue is already empty, or vice versa.

INFO, not WARNING: `routers/logs.py`'s `_line_visible` filters by exact level
membership rather than level-and-above, so a WARNING here would be invisible to
anyone watching the INFO stream that carries the rest of the crawl narrative —
the same reasoning already recorded on the cooldown log line in
`_record_site_result`.

`routers/settings.py` currently has no module logger; add the standard
`log = get_logger("routers.settings")` used by the other routers.

### 3. Enqueue guard

Without this, the purge does not stick. `_sync_collection` reads
`enabled_crawlers` once before its page loop and then enqueues across every
collection page for minutes afterwards; a store disabled mid-sync keeps getting
rows re-created after the delete, and the pending count climbs back off zero.
`sweep_enqueue` has the same shape across users.

Rather than re-reading the enabled list at three loop boundaries, guard the
insert itself, so all four call sites (`routers/crawl.crawl_start`,
`_sync_collection`, `sweep_enqueue`, and `_sync_stock`'s price-crawler fan-out)
are covered without being touched:

```python
def enqueue_crawl_queue(conn, discogs_id: str, crawler_id: int):
    conn.execute(
        """
        INSERT INTO crawl_queue (discogs_id, crawler_id)
        SELECT %(discogs_id)s, %(crawler_id)s
        WHERE EXISTS (SELECT 1 FROM crawlers WHERE id = %(crawler_id)s AND enabled)
        ON CONFLICT (discogs_id, crawler_id) DO UPDATE SET
            status = 'pending', requested_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL
        WHERE crawl_queue.status = 'done'
        """,
        {"discogs_id": discogs_id, "crawler_id": crawler_id},
    )
```

`enqueue_crawl_queue_for_stock_item` takes the identical treatment on
`(item_key, crawler_id)`. One primary-key lookup per insert. Note that
`INSERT ... SELECT` still supports `ON CONFLICT`, so the existing
resurrect-a-`done`-row semantics are unchanged; when the `WHERE EXISTS` fails
the statement inserts zero rows and the `ON CONFLICT` clause is simply never
reached.

### 4. Stock sync: per-source live check

`_sync_stock` already re-checks `self._cooling_down_crawler_ids()` per crawler
inside its loop, precisely so a site that trips its limit mid-run takes effect
immediately. Read the live enabled set in the same place:

```python
with get_app_pool().connection() as conn:
    live_enabled = {c["id"] for c in (
        get_enabled_crawlers(conn, crawler_type="catalog")
        + get_enabled_crawlers(conn, crawler_type="catalog_browser")
    )}
if crawler._db_id not in live_enabled:
    disabled_sources.append(crawler._db_site_name)
    log.info("[%s] Stock crawl skipped: crawler was disabled during this run", crawler._db_site_name)
    continue
```

`disabled_sources` joins `failed_sources` and `skipped_sources` in the
completion log's notes tail, so "complete: 0 items" is never left unexplained:

```
Stock sync complete: 412 items -- 1 failed (Amoeba); 2 disabled (Revhq, Relapse)
```

Placed before the `stock_sync_source_started` broadcast, so a skipped source
never announces itself.

A store disabled while its own catalog crawl is already in flight finishes that
crawl and its items are written. The fetch has already happened, the snapshot is
complete and valid, and `replace_stock_items` with a partially-paged catalog
would wipe good rows and replace them with a truncated set — strictly worse than
storing a correct one.

The check is a small query issued once per catalog source (single digits per
run), not per item, so its cost is irrelevant next to a catalog crawl.

### 5. Load all plugins, gate on enabled

New plain helper, mirroring `get_enabled_crawlers` minus the filter:

```python
def get_crawlers(conn, crawler_type: str = "release") -> list[dict]:
    return conn.execute("SELECT * FROM crawlers WHERE crawler_type = %s", [crawler_type]).fetchall()
```

Not `get_all_crawlers` — that one import-executes every plugin module a second
time to read a cosmetic `base_url` for the admin listing, work
`load_enabled_crawlers` is about to redo.

`start_worker_pool` switches to it:

```python
with get_app_pool().connection() as conn:
    all_crawlers = get_crawlers(conn)
plugins = load_enabled_crawlers(all_crawlers)
```

`enabled` then means exactly one thing — a runtime gate at claim time — and the
`plugin is None` branch in `_drain_one_batch` reverts to what it was written
for: a `crawlers` row whose module genuinely failed to import.
`_set_failure_domains` covers disabled crawlers too, which is harmless since
their rows are never claimed, and correct the moment one is re-enabled.

`load_enabled_crawlers` keeps its name and signature; it filters nothing itself
and never did — it takes whatever rows it is given.

## Frontend design

`frontend/src/api/client.ts` — `setCrawlerEnabled` currently discards the
response body:

```typescript
export async function setCrawlerEnabled(id: number, enabled: boolean): Promise<{ ok: boolean; discarded: number }> {
  const r = await apiFetch(`/crawlers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

`frontend/src/views/Settings.tsx` — `handleToggleCrawler` keeps the count for
the row it just toggled:

```typescript
const [discardedNotice, setDiscardedNotice] = useState<{ crawlerId: number; count: number } | null>(null)

async function handleToggleCrawler(crawler: Crawler) {
  const { discarded } = await setCrawlerEnabled(crawler.id, !crawler.enabled)
  onCrawlersChange(crawlers.map((c) => c.id === crawler.id ? { ...c, enabled: !c.enabled } : c))
  setDiscardedNotice(discarded ? { crawlerId: crawler.id, count: discarded } : null)
}
```

Rendered in the existing "Crawl" cell, after the toggle button:

```tsx
{discardedNotice?.crawlerId === c.id && (
  <span className="ml-2 text-xs text-gray-500">
    {discardedNotice.count} queued {discardedNotice.count === 1 ? 'job' : 'jobs'} discarded
  </span>
)}
```

Single-slot state, so toggling any other crawler replaces the notice rather
than accumulating a column of stale counts, and re-enabling the same crawler
clears it (`discarded` is 0 on enable). No timer — the note is a record of what
the click did, and it disappears on the next crawler action or navigation away
from Settings.

## Consequences

- **Re-enabling does not resume queued work.** The rows were deleted, so the
  admin must run "Refresh prices" (or wait for the next scheduled crawl) to
  re-enqueue. This is the accepted cost of the honest pending count.
- **An open in-stock sync progress panel lags by one source.** With no SSE
  event on disable, a client watching the sync keeps showing the disabled store
  until the loop reaches the next source and broadcasts
  `stock_sync_source_started` for it. Cosmetic; the follow-on if it grates is a
  `crawler_disabled` broadcast, deliberately not built now.
- **A long-running collection sync no longer enqueues for a store disabled
  mid-sync.** A behaviour change beyond the literal ask, but the alternative is
  a purge that immediately un-purges itself.

## Testing

`backend/tests/test_crawl_queue.py`:

- `claim_crawl_queue_batch` does not return pending rows for a disabled
  crawler, and returns them again once it is re-enabled.
- A disabled crawler's rows do not consume batch slots — a batch with one
  disabled crawler's rows and one enabled crawler's rows returns only the
  enabled crawler's, up to `limit`.
- `enqueue_crawl_queue` inserts nothing for a disabled crawler and inserts for
  an enabled one; the same for `enqueue_crawl_queue_for_stock_item`.
- Resurrect semantics survive the rewrite: a `done` row for an enabled crawler
  is still flipped back to `pending` by a re-enqueue, and a `pending` or
  `in_progress` row is still left alone.
- `delete_pending_crawl_queue_for_crawler` deletes `pending` rows for that
  crawler only, leaves `in_progress` and `done` rows, leaves other crawlers'
  rows, and returns the deleted count.

`backend/tests/test_settings_router.py`:

- `PATCH /crawlers/{id}` with `enabled=false` returns the discarded count and
  the rows are gone; with `enabled=true` it returns `discarded: 0` and deletes
  nothing.
- Still 403 for a non-admin (existing coverage, unchanged behaviour).

`backend/tests/test_crawl_manager.py`:

- `_sync_stock` skips a source disabled between loop iterations — the crawler's
  `crawl_catalog()` is never called, `replace_stock_items` is never called for
  it, and the run still completes and processes the remaining sources.
- The completion log names disabled sources in its notes tail.
- `start_worker_pool` builds a `plugins_by_crawler_id` that includes a disabled
  crawler (asserted against the registry, with Playwright launch faked as this
  file's existing pool tests already do).

`frontend/src/test/settings.test.tsx`:

- Toggling a crawler off renders the discarded count in that row and no other;
  toggling a second crawler moves the note; a response with `discarded: 0`
  renders no note.

Per CLAUDE.md, Playwright-dependent behaviour — a real `plugin.search()`
finishing after a disable, a real catalog page fetch — stays manual. No new
crawler fixture is needed; this change touches no scraping logic.

## Spec drift check

Grep for the touched symbols across both `docs/superpowers/specs/` and
`docs/specifications/shaping/` before opening the PR — in particular
`claim_crawl_queue_batch`, `enqueue_crawl_queue`, `start_worker_pool`, and
`_sync_stock`, which appear in
`2026-07-27-crawl-queue-refactor-design.md`, `2026-08-01-worker-pool-pacing-design.md`,
`2026-08-02-stock-sync-429-backoff-design.md`,
`2026-07-05-in-stock-crawler-design.md`, and
`2026-08-07-store-crawler-refresh-button-design.md`. The refresh-button spec's
"Enabled-only" decision and its note that a disabled crawler hits the "No
enabled catalog crawlers" path are the most likely to need an amendment, since
this change adds a second, mid-run point at which enabled state is consulted.
Also re-check CLAUDE.md's "Key invariants" — the crawl-is-a-shared-queue bullet
does not currently mention that `enabled` gates claiming.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change alters the behaviour of an existing admin HTTP
action rather than adding a trigger, input, or output shape; the only wire
change is an additive `discarded` field on the `PATCH /crawlers/{id}` response.
No stack, golden-command, or CI/CD change. `README.md` documents no
crawler-toggle semantics to update.
