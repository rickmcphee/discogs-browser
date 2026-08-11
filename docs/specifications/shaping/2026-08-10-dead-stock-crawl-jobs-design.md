# Stop price-crawling stock items with no enabled source

Date: 2026-08-10
Branch: `claude/dead-stock-crawl-jobs`

## Problem

[The 2026-08-09 disabled-stores change](2026-08-09-stop-crawling-disabled-stores-design.md)
made `crawlers.enabled` a live gate on the crawler *doing* the crawling. It
never gated the crawler the work came *from*, and on the stock-item path those
are different crawlers.

`_sync_stock` enqueues one `crawl_queue` row per `(item_key, price_crawler)`
for every item a catalog crawler just found — `crawl_manager.py:752`, fanning
each store's whole catalog out across Amazon and eBay. That row's `crawler_id`
is Amazon's or eBay's. It carries no reference at all to the store that
produced the item; the only link back is `stock_items.crawler_id`, joined on
`item_key`.

So all three of the existing gates miss it:

- `enqueue_crawl_queue_for_stock_item` checks `crawlers WHERE id = crawler_id
  AND enabled` — Amazon.
- `claim_crawl_queue_batch` checks `crawler_id IN (SELECT id FROM crawlers
  WHERE enabled)` — Amazon.
- `update_crawler` purges `WHERE crawler_id = %s AND status = 'pending'` —
  which, for a catalog crawler, matches essentially nothing, since catalog
  crawlers don't queue work under their own id.

Disabling a store therefore stops it being *visited* (`crawl_manager.py:698`)
while Amazon and eBay keep grinding through its already-queued items to
completion. That backlog is large and slow: a stock sync enqueues thousands of
rows, and `crawl_delay_seconds` (default 30s) paces each site, so a disable can
be hours or days ahead of the last job it should have stopped.

A second population has the same shape and the same fix. `replace_stock_items`
deletes a crawler's whole `stock_items` batch and re-inserts only what is
currently in stock (`db.py:944`), while `stock_item_identities` rows and
`crawl_queue` rows both survive. An item that sold out or was delisted keeps
being price-crawled forever, sourced from nothing.

## Scope

Touches:

- `backend/db.py` — new `_enabled_stock_source_exists()` SQL fragment helper;
  `claim_crawl_queue_batch` and `enqueue_crawl_queue_for_stock_item` gain the
  predicate; new `delete_dead_stock_crawl_queue_rows()`.
- `backend/routers/settings.py` — `update_crawler` folds the sweep's rowcount
  into the `discarded` it already returns.
- `backend/crawl_manager.py` — `_sync_stock` sweeps once at the end of a run.
- `backend/version.py` — minor bump.
- Tests: `backend/tests/test_crawl_queue.py`,
  `backend/tests/test_stock_crud.py`, `backend/tests/test_settings_router.py`,
  `backend/tests/test_crawl_manager.py`.

Out of scope:

- **Existing data.** `listings`, `stock_items`, and `stock_item_identities`
  rows already recorded for a dead item are left alone, carrying forward the
  prior spec's rule: disabling stops crawling, it does not purge data. Hiding a
  store's results from the UI remains the separate `hiddenCrawlerIds` filter.
- **Release rows.** Anything with a `discogs_id` is untouched by every change
  here.
- **Frontend.** No API shape change, so no frontend or frontend-test work.
- **Aborting an in-flight request.** Unchanged from the prior spec: the current
  `plugin.search()` runs to completion.

## Decisions carried from brainstorming

- **One predicate covers both populations.** "At least one enabled crawler
  currently lists this `item_key` in `stock_items`" is a single condition that
  stops disabled-store items and sold-out items alike. Gating strictly on
  "every source is disabled" would need a second, differently-shaped mechanism
  for the sold-out case and would leave those items priced forever.
- **Gate *and* sweep, not one or the other.** See "Why both" below.
- **The sweep is global, not scoped to a crawler.** It deletes every pending
  row failing the predicate, whatever store it came from. That makes it
  idempotent, self-correcting, and able to clear residue that predates this
  change. The honest cost: a `discarded` count returned by disabling one store
  can include rows attributable to another store's delisted items. The number
  means "jobs that are now dead", not "jobs this store created".
- **One combined `discarded` count.** No new response field, no frontend
  change. The admin reads it as "disabling this discarded N queued jobs",
  which is true regardless of which crawler was going to run them.
- **Pending rows are deleted, not parked** — carried from the prior spec. Here
  the reason is different: the per-user pending count never included stock rows
  (`count_pending_crawl_queue_for_user` inner-joins `library_items` on
  `discogs_id`), so this is not about an honest count. It is about the claim
  query's cost — see "Why both".
- **`in_progress` rows are left alone** — carried unchanged. Such a row has
  already been claimed and committed by a worker that is mid-crawl and will
  `mark_crawl_queue_done()` when it finishes; deleting it would leave that
  update matching nothing while the crawl still writes its listing.

## Backend design

### 1. The shared predicate

One definition, used by three statements, so the three cannot drift apart. The
item-key expression differs between them — two correlate to a `crawl_queue`
row, one has only a bound parameter — so it is a function returning SQL, the
same pattern `db.py` already uses for `_library_match_fragment()` and
`_in_library_clause()`:

```python
def _enabled_stock_source_exists(item_key_expr: str) -> str:
    """A stock item is worth crawling only while some enabled crawler still
    lists it. Covers both a disabled store and an item that has left every
    store's stock -- replace_stock_items() deletes a crawler's whole batch and
    reinserts only what is currently in stock, so a sold-out item loses its
    stock_items row while its stock_item_identities row and its queue rows
    survive."""
    return f"""
        EXISTS (
            SELECT 1 FROM stock_items si
            JOIN crawlers sc ON sc.id = si.crawler_id
            WHERE si.item_key = {item_key_expr} AND sc.enabled
        )
    """
```

`item_key_expr` is always a literal string chosen at the call site — a column
reference or a `%(item_key)s` placeholder — never request-derived, matching how
`_library_match_fragment` already takes its `user_id_param`.

Served by `stock_items_item_key_idx`, which already exists. `FOR UPDATE` in the
claim query locks only `crawl_queue`; relations read inside a `WHERE`
subquery are not locked, so no `stock_items` or `crawlers` row is locked by a
claim. Both tables are in `GLOBAL_SCHEMA` with no RLS, so this reads identically
from an app-pool connection and from a `user_scope` one.

No grant work, unlike the prior spec: `init_tenant_schema` already grants
`app_user` `SELECT` on `stock_items` and `crawlers`, and `DELETE` on
`crawl_queue` (`db.py:402-416`).

### 2. Claim-time gate

```sql
SELECT id FROM crawl_queue
WHERE status = 'pending'
  AND crawler_id IN (SELECT id FROM crawlers WHERE enabled)
  AND (item_key IS NULL OR {_enabled_stock_source_exists("crawl_queue.item_key")})
  {exclusion_clause}
ORDER BY (item_key IS NOT NULL), requested_at, id
LIMIT %(limit)s
FOR UPDATE SKIP LOCKED
```

`item_key IS NULL OR` is what keeps release rows out of it: their `item_key` is
NULL and they must claim exactly as before.

### 3. Enqueue guard

```sql
INSERT INTO crawl_queue (item_key, crawler_id)
SELECT %(item_key)s, %(crawler_id)s
WHERE EXISTS (SELECT 1 FROM crawlers WHERE id = %(crawler_id)s AND enabled)
  AND {_enabled_stock_source_exists("%(item_key)s")}
ON CONFLICT (item_key, crawler_id) DO UPDATE SET ...
```

This closes a window the sweep cannot. `_sync_stock` reads its enabled catalog
set, crawls one store for minutes, and only then enqueues; under READ COMMITTED
an enqueue that began before a disable committed still evaluates against the
older snapshot. The existing note on `count_pending_crawl_queue_for_user`
records the same race for the release path.

The guard is satisfiable at the moment it runs: `replace_stock_items` and the
enqueue fan-out share one transaction and one connection
(`crawl_manager.py:746-755`), so the `stock_items` rows this predicate looks
for were inserted by the statement immediately above it.

`INSERT ... SELECT` still supports `ON CONFLICT`, so the existing
resurrect-a-`done`-row semantics are unchanged; when the `WHERE` fails, zero
rows are inserted and the `ON CONFLICT` clause is never reached.

### 4. The sweep

```python
def delete_dead_stock_crawl_queue_rows(conn) -> int:
    return conn.execute(
        f"""
        DELETE FROM crawl_queue
        WHERE status = 'pending'
          AND item_key IS NOT NULL
          AND NOT {_enabled_stock_source_exists("crawl_queue.item_key")}
        """
    ).rowcount
```

`status = 'pending'` only, for the `in_progress` reason above. `done` rows are
the historical record of past crawls and are never claimed — only
`enqueue_crawl_queue_for_stock_item` resets them, and it now refuses to.

### 5. Call sites

`routers/settings.update_crawler`, inside the existing single transaction so
the flag flip and both purges commit together:

```python
if not body.enabled:
    discarded = (
        db.delete_pending_crawl_queue_for_crawler(conn, crawler_id)
        + db.delete_dead_stock_crawl_queue_rows(conn)
    )
```

The existing log line and `{"ok": True, "discarded": discarded}` response are
unchanged.

`crawl_manager._sync_stock`, once after the source loop and before the
`stock_sync_complete` broadcast — not per source, since the statement is global
and running it once per store would repeat identical work. Its own
`get_app_pool().connection()` block with its own `commit()`, entered and exited
around nothing else, matching how every other write in that loop already holds
a connection only for the statement it runs. Log at INFO when non-zero,
matching `routers/logs.py`'s exact-level filtering, which the cooldown and
discard lines already account for.

The 429-abort path returns before reaching it. Dead rows then wait for the next
stock sync or the next disable; they are unclaimable in the meantime, so the
only cost of the delay is the claim-query cost below.

## Why both a gate and a sweep

The gate is the correctness mechanism. It re-evaluates every batch, so a
disable takes effect on the very next claim, and it covers the enqueue race in
§3 that no amount of after-the-fact deletion can prevent.

The sweep is what keeps the gate cheap. `claim_crawl_queue_batch`'s
`ORDER BY (item_key IS NOT NULL), requested_at, id` forces a sort over the
whole filtered set rather than an index walk terminated by `LIMIT`, so the
predicate is evaluated for every pending row on every batch. Without the sweep,
dead rows would accumulate permanently — never claimed, never removed, re-tested
by every claim for the life of the deployment. The sweep keeps dead rows from
accumulating on top of that working set — the added predicate is a
constant-factor bump on a scan and sort that already existed, and the sweep is
what stops that bump growing without limit as disabled stores and delisted
items pile up.

## Consequences

- **Re-enabling a store does not resume its queued price lookups.** They were
  deleted. Recovery is the per-store Refresh button
  ([2026-08-07 spec](2026-08-07-store-crawler-refresh-button-design.md)) or the
  next scheduled stock sync: `replace_stock_items` returns the item keys and
  the fan-out re-enqueues them.
- **Sold-out items stop being priced.** This is a behaviour change beyond the
  disabled-store motivation and is intended.
- **Worst-case overrun is one batch per worker**, unchanged from the prior
  spec: rows already claimed run to completion.
- **`count_pending_crawl_queue_for_user` needs no change.** Its inner join on
  `library_items.discogs_id` already excludes every stock row.
- **First run after deploy may discard a large number of rows** — every dead
  row accumulated before this existed. Expected, and the log line reports it.

## Testing

All state is constructed in-test; nothing may be inherited from a prior run or
a hand-provisioned database (see
[2026-08-09-test-database-freshness-design.md](2026-08-09-test-database-freshness-design.md)).

`test_crawl_queue.py`'s `_make_stock_identity_and_crawler` helper currently
inserts a `stock_item_identities` row and registers one crawler, and no
`stock_items` row at all — so every existing stock-item test in that file fails
the new predicate. The helper must gain a second registered crawler acting as
the source and a `stock_items` row linking it to the `item_key`, mirroring
production, where the source is a catalog crawler and the queue row's
`crawler_id` is a price crawler. Keeping them distinct is what lets a test
disable the source without disabling the crawler under test.

- `test_crawl_queue.py` — a pending stock row is not claimed when its only
  source crawler is disabled; it is claimed again once re-enabled; a row whose
  `item_key` has no `stock_items` row is not claimed; release rows still claim
  with the source disabled; `enqueue_crawl_queue_for_stock_item` inserts
  nothing when no enabled source exists; an item with two sources, one
  disabled, still claims.
- `test_stock_crud.py` — the sweep deletes pending rows for a disabled-source
  key and for a zero-source key, returns the count, and leaves `in_progress`
  rows, `done` rows, release rows, and live rows untouched.
- `test_settings_router.py` — disabling a store returns a `discarded` covering
  both purges.
- `test_crawl_manager.py` — `_sync_stock` calls the sweep once at the end of a
  run.

## Spec drift check

To perform before opening the PR, per `CLAUDE.md`:

- [2026-08-09-stop-crawling-disabled-stores-design.md](2026-08-09-stop-crawling-disabled-stores-design.md)
  describes the claim gate and the disable purge as being about the crawling
  crawler alone, and scopes "existing data" out without distinguishing recorded
  rows from queued work. Amend with a pointer here rather than rewriting.
- `CLAUDE.md`'s `crawlers.enabled` invariant enumerates where enabled state is
  consulted. It needs the source-side gate added.

## Runtime/agent document impact

`CLAUDE.md` as above. No change to the crawler plugin interface: plugins see the
same `(release_or_identity_dict, page)` call and never learn why a job was or
was not queued.
