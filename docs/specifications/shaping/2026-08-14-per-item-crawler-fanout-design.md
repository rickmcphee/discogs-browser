# Per-Item Marketplace Crawler Fan-Out — Design

**Status:** implemented
**Date:** 2026-08-14
**Verified against:** `origin/main` @ `ebf38f2`

## Problem

`crawl_queue` stores one row per `(target, crawler)` pair, and the crawler is chosen by the
*producer* at enqueue time from a snapshot of `get_enabled_crawlers(conn, "release")`. Three
consequences follow.

**Enabling a marketplace crawler schedules no work.** `set_crawler_enabled` flips a flag and
nothing else. Rows for the newly enabled crawler exist only after the next collection sync or
scheduled sweep enqueues them, and those rows carry a fresh `requested_at`, so they sort behind
the entire existing backlog. With a large pending queue this reads as "crawler A drains
completely, then crawler D starts" — the per-crawler-consecutive behaviour this design exists to
remove.

**Per-item interleaving is emergent, not guaranteed.** Every producer loops target-outer /
crawler-inner, and `requested_at` is `CURRENT_TIMESTAMP` (transaction start), so all rows from one
commit tie and the claim's `ORDER BY (item_key IS NOT NULL), requested_at, id` falls through to
`id` — which happens to be target-major. That ordering survives only as long as the enabled set
does not change between enqueue bursts. It is a coincidence of insertion order, not a property
anyone declared.

**The enabled set is fixed into thousands of rows.** Keeping it honest has taken a growing set of
compensating gates: a statement-level `WHERE EXISTS (… crawlers … enabled)` in both enqueue
helpers, a live `crawler_id IN (SELECT id FROM crawlers WHERE enabled)` gate in the claim, and
`delete_pending_crawl_queue_for_crawler` to purge pending rows on disable. Each is correct; each
exists only because a runtime decision was frozen into row data.

## Goals

- Each enabled marketplace crawler is called for a given target before work moves on to the next
  target, by construction rather than by insertion-order coincidence.
- The set of crawlers to run is resolved at dequeue time, so enabling or disabling one takes
  effect immediately without a restart, a purge, or a re-sync.
- Enabling a crawler backfills the prices it is missing, without re-crawling what other crawlers
  already priced.

## Non-goals

- **Strict per-item completion.** No barrier between targets. A worker that would otherwise idle
  waiting on one site's pacing window moves on to the next work unit. Pacing, not ordering, is the
  throughput ceiling here.
- **Per-user fairness.** Collapsing N pair-rows into one shortens the queue by the crawler
  multiple but not the wall-clock wait, since each row now carries N site requests. Claim order
  stays global FIFO, so one user's large sync still sits ahead of everyone else's work. Fixing it
  on the *claim* side would need an owner concept the global, item-keyed `crawl_queue` does not
  have (a release can be in many users' libraries); fixing it on the *enqueue* side would not, but
  is a separate change to producers and ordering. Either way, out of scope here — see Follow-ups.
- **Retry semantics.** Work stays one-shot per pass. A failed unit is not retried within the pass;
  it returns via the next sync or sweep. The only new re-schedule path is cooldown deferral.
- **Freshness/TTL.** Still no `last_checked` consultation on any enqueue path.

## Design

### Schema

`crawl_queue` becomes a table of *targets needing prices*. `crawler_id` is dropped; the target
alone is unique.

```sql
-- new shape
crawl_queue(
    id, discogs_id, item_key, requested_at, status, claimed_by, claimed_at,
    pending_crawler_ids INTEGER[] NULL,
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
CREATE UNIQUE INDEX crawl_queue_discogs_id_idx ON crawl_queue (discogs_id);
CREATE UNIQUE INDEX crawl_queue_item_key_idx   ON crawl_queue (item_key);
CREATE INDEX crawl_queue_pending_idx ON crawl_queue ((item_key IS NOT NULL), requested_at, id)
    WHERE status = 'pending';
```

Both unique indexes are on nullable columns, so multiple NULLs coexist (exactly one of
`discogs_id`/`item_key` is set per row) and `ON CONFLICT (discogs_id)` / `ON CONFLICT (item_key)`
can infer them — the same mechanism the current `crawl_queue_item_key_crawler_idx` relies on.

`pending_crawler_ids` is the per-pass progress record. `NULL` means "every currently eligible
crawler"; a non-NULL array narrows the pass to unfinished work from a previous one. It exists
because `listings` cannot answer "did this crawler already run for this target" — an empty result
writes no row (and, since #122, may leave a NULL-price row via `clear_listing_price`), and adding
a placeholder row would break the "no row means not-yet-crawled" invariant the UI depends on.

`available_at` is a not-before marker. Without it, a row deferred behind a site cooldown would be
re-claimed immediately and re-deferred in a hot loop.

`available_at` is a filter predicate on the scanned pending rows rather than an index key.
Deferred rows are a small minority of pending rows, so keeping the index ordered for the claim's
`ORDER BY` is the better trade.

### Migration

There is no migration framework; `init_global_schema` is idempotent DDL run at boot before
`start_worker_pool`, on a single-instance deployment. The collapse goes there, guarded on
`crawl_queue.crawler_id` still existing in `information_schema.columns` so re-runs are no-ops:

1. Add `pending_crawler_ids` and `available_at`.
2. For each target, keep the lowest `id` row. Set its `status` to `pending` if any of that
   target's pairs was `pending` or `in_progress`, else `done`. Set `pending_crawler_ids` to
   `array_agg` of the `crawler_id`s of those unfinished pairs, so in-flight intent survives the
   migration exactly; `NULL` where the target collapsed to `done`.
3. Delete the other rows for that target.
4. Drop `crawler_id`, drop the old unique constraint and `crawl_queue_item_key_crawler_idx`,
   create the new indexes.

`listings`, `stock_items`, and `stock_item_identities` are untouched — they already key on
`(target, crawler_id)` and remain the result store.

### Producers

All four call sites lose their inner crawler loop *and* their `get_enabled_crawlers` call:

| producer | file |
|---|---|
| collection sync (collection + wishlist branches) | `backend/crawl_manager.py` |
| `POST /api/crawl/start` | `backend/routers/crawl.py` |
| scheduled sweep (`sweep_enqueue`) | `backend/crawl_manager.py` |
| stock sync fan-out (`_sync_stock`) | `backend/crawl_manager.py` |

`_sync_stock` also drops its `requires_discogs_release` filter; that predicate moves to dispatch,
where it belongs — it is a property of the target kind, not of the producer.

```sql
-- enqueue_crawl_queue(conn, discogs_id)
INSERT INTO crawl_queue (discogs_id) VALUES (%(discogs_id)s)
ON CONFLICT (discogs_id) DO UPDATE SET
    status = 'pending', requested_at = CURRENT_TIMESTAMP, available_at = CURRENT_TIMESTAMP,
    claimed_by = NULL, claimed_at = NULL, pending_crawler_ids = NULL
WHERE crawl_queue.status = 'done'
```

The `WHERE crawl_queue.status = 'done'` revival guard is unchanged: re-enqueuing a pending or
in-progress target is a no-op, re-enqueuing a finished one revives it as a full pass.

The `WHERE EXISTS (… crawlers … enabled)` gate in both enqueue helpers is **deleted** — with no
crawler on the row there is nothing to gate, and eligibility is now resolved at dispatch.

`enqueue_crawl_queue_for_stock_item(conn, item_key)` keeps the `_enabled_stock_source_exists`
gate: that predicate is about whether any enabled *store* still stocks the item, which is
independent of which marketplace crawler will price it.

### Claim

`excluded_crawler_ids` is removed — there is no crawler on the row to exclude, so cooldown moves
to an in-loop skip. The live `crawler_id IN (SELECT id FROM crawlers WHERE enabled)` gate is
removed for the same reason, replaced by dispatch-time resolution. The stock source gate stays.

```sql
UPDATE crawl_queue SET status = 'in_progress', claimed_by = %(worker_id)s,
                       claimed_at = CURRENT_TIMESTAMP
WHERE id IN (
    SELECT id FROM crawl_queue
    WHERE status = 'pending'
      AND available_at <= CURRENT_TIMESTAMP
      AND (item_key IS NULL OR <stock_source_gate>)
    ORDER BY (item_key IS NOT NULL), requested_at, id
    LIMIT %(limit)s
    FOR UPDATE SKIP LOCKED
)
RETURNING id, discogs_id, item_key, pending_crawler_ids
```

`ORDER BY` is unchanged, preserving both existing guarantees: release rows ahead of stock-item
rows (anti-starvation, priority within a batch rather than exclusion) and FIFO by
`requested_at, id` within a kind.

`batch_size` drops from 5 to 2. A batch is now `batch_size × N` site requests — at three enabled
crawlers and a 30s pace, a batch of 5 is roughly five minutes of held claims, which widens the
hung-worker stranding window documented on `claim_crawl_queue_batch` for no benefit.

### Dispatch

Per claimed row, one connection loads the target and resolves eligibility together:

- `get_catalog_release` / `get_stock_item_identity` as today.
- `get_enabled_crawlers(conn, "release")`, minus `requires_discogs_release` crawlers when the
  target is a stock item, intersected with `pending_crawler_ids` when it is non-NULL.

No TTL cache: `crawlers` is a handful of rows and the connection is already open, so eligibility
is genuinely live at the moment of dispatch.

The batch's rows then flatten into a single target-major list of `(row, crawler)` work units,
drained sequentially. This is where "non-strict" lands — there is no barrier between targets, and
a unit whose crawler is currently in cooldown is *deferred* rather than waited on, so the worker
proceeds to the next unit instead of idling.

Per work unit, unchanged from today: `_paced_search` under that crawler's lock, one bot-detection
retry inside the lock, `_record_site_result` (release rows record `bool(matches) and not
bot_detected`; stock-item rows record only a genuine signal), then `upsert_listing` +
`upsert_stock_item_from_release` on a match, or `delete_stock_item_for_release` +
`clear_listing_price` on an empty non-bot release result. SSE granularity is unchanged: still one
`listing_changed` / `stock_listing_changed` event per `(target, crawler)`.

Plugin lookup keeps the boot-built `plugins_by_crawler_id` from `get_crawlers` (all crawlers,
enabled or not), so a crawler enabled at runtime already has its plugin loaded. A crawler whose
module failed to load at boot is absent from the dict; that unit is skipped and recorded as a site
failure on the breaker, and is deliberately **not** deferred — a permanently broken module would
otherwise defer its row forever.

### Row resolution

Each row is resolved exactly once, as soon as its own last work unit finishes — not in a single
pass after the whole batch drains. The distinction is load-bearing: `units` is built target-major
and contiguous per row, and resolving at batch end meant a `CancelledError` escaping the unit loop
(which `except Exception` deliberately does not catch, and which `stop_worker_pool`'s
`task.cancel()` raises) skipped resolution entirely, leaving an already-completed row at
`in_progress`. With no reclaim path and `enqueue_crawl_queue`'s revival gated on `status = 'done'`,
such a row is unclaimable and unrevivable forever — its target never priced again.

*Amended 2026-08-25: `db.reclaim_stranded_crawl_queue_rows` now ages such a row back to `pending`,
so "forever" is now "until the stranded threshold elapses". Per-row resolution is still the fix —
the reclaim is a crash backstop measured in tens of minutes, not a substitute for resolving a row
when its last unit finishes. See
[`2026-08-25-stranded-crawl-queue-row-reclaim-design.md`](2026-08-25-stranded-crawl-queue-row-reclaim-design.md).*

The resolution itself:

- Any deferred crawlers → `status = 'pending'`, `claimed_by = NULL`, `claimed_at = NULL`,
  `pending_crawler_ids = <deferred ids>`, `available_at = CURRENT_TIMESTAMP + <remaining
  cooldown>`, computed from the monotonic cooldown deadline. `requested_at` is left alone, so the
  row returns near its original queue position rather than at the back.
- Otherwise `mark_crawl_queue_done`.

*Amended 2026-08-25: both writes now also match `claimed_by = <this worker>`, and return their
rowcount. The reclaim added a case this passage predates — two workers holding one row, because an
age-based reclaim cannot tell a dead worker from a slow one — and without the match a stale `done`
overwrites a fresh deferral, dropping that crawler for the target. Worker ids are namespaced by
`config.MACHINE_ID` so the match identifies one worker across Machines rather than one per Machine.*

A row whose eligible set resolves to empty — no enabled marketplace crawlers at all, or a narrowed
`pending_crawler_ids` whose every member has since been disabled — is marked `done`. There is no
work to do and nothing to wait for.

### Backfill on enable

`set_crawler_enabled` gains a backfill for `crawler_type = 'release'` crawlers being enabled (not
disabled). Two statements:

```sql
-- 1. revive finished targets this crawler has no price for
UPDATE crawl_queue SET status = 'pending', requested_at = CURRENT_TIMESTAMP,
       available_at = CURRENT_TIMESTAMP, claimed_by = NULL, claimed_at = NULL,
       pending_crawler_ids = ARRAY[%(crawler_id)s]
WHERE status = 'done'
  AND NOT EXISTS (
      SELECT 1 FROM listings l
      WHERE l.crawler_id = %(crawler_id)s AND l.price IS NOT NULL
        AND (l.release_id = crawl_queue.discogs_id OR l.item_key = crawl_queue.item_key)
  )
  -- when the crawler requires_discogs_release:
  -- AND discogs_id IS NOT NULL

-- 2. widen rows already narrowed by an earlier deferral
UPDATE crawl_queue SET pending_crawler_ids = pending_crawler_ids || %(crawler_id)s,
                       available_at = CURRENT_TIMESTAMP
WHERE status = 'pending' AND pending_crawler_ids IS NOT NULL
  AND NOT (%(crawler_id)s = ANY(pending_crawler_ids))
```

Both statements select their rows through a `FOR UPDATE SKIP LOCKED` subquery and update by id. A
transaction that never waits on a row lock cannot join a wait cycle, so the backfill cannot
deadlock against a collection sync holding queue rows for the length of a page. `lock_timeout`
alone could not provide that: Postgres's `deadlock_timeout` is 1s, so a longer bound never fires
first, and the detector picks its own victim — measured, in the case that motivated this, as the
user's sync rather than the admin's request. Skipping a locked row loses nothing on statement 1: a
row the sync holds is a row the sync is re-enqueuing, which resets `pending_crawler_ids` to NULL,
so the newly enabled crawler is picked up at dispatch anyway.

Statement 2 matters: a row deferred behind a cooldown carries a narrowed set that would otherwise
exclude the newly enabled crawler. Rows with `pending_crawler_ids IS NULL` need nothing, since
NULL already means "all currently eligible".

It resets `available_at` along with the widen, and that reset is the point rather than a detail. A
narrowed row is narrowed *because* another crawler is cooling down, so it carries that crawler's
deadline — up to 30 minutes out. Appending the newly enabled crawler without clearing the deadline
would leave the row unclaimable for that whole cooldown, so enabling a crawler would not take
effect on the next batch the way it does everywhere else in this design. Dispatch re-defers the
still-cooling crawler on its own terms.

`pending_crawler_ids = ARRAY[id]` is what makes this a backfill rather than a re-crawl: only the
newly enabled crawler runs for those targets, and the prices other crawlers already found are left
untouched.

The `price IS NOT NULL` condition matches `get_missing_releases` semantics and is required rather
than cosmetic: #122's `clear_listing_price` leaves NULL-price `listings` rows behind, and a bare
`NOT EXISTS` would treat those as already priced. The consequence is that targets where this
crawler legitimately found nothing have no priced row and are re-enqueued on every enable — a
bounded, idempotent cost, but not a free one.

`delete_pending_crawl_queue_for_crawler` and its `PATCH /api/crawlers/{id}` call site are
**deleted**. With no per-crawler rows there is nothing to purge, and disable now takes effect
because dispatch stops selecting that crawler.

The `discarded` count and its log line stay. Only the per-marketplace-crawler purge is gone; the
endpoint still reports what `delete_dead_stock_crawl_queue_rows` removed, which is the *other*
population — stock-item rows left behind when a store is disabled or an item leaves stock. So
disabling a marketplace crawler now reports `0`, while disabling a store still reports a real
count, and the frontend's "N queued jobs discarded" line stays accurate. The enable path runs the
same sweep (backfill statement 1 has no stock-source predicate, so it can revive dead stock rows)
and logs its count separately, deliberately not folded into `discarded`.

### Counts and API surface

`count_pending_crawl_queue_for_user` currently joins `crawlers` to avoid counting rows for
disabled crawlers, because `routers/crawl._events_to_replay` reads a non-zero count as "this user
is mid-job" and would replay stale event history forever if the count could not reach zero. The
item-level equivalent must preserve that property: a row counts only if something can actually
claim it.

```sql
SELECT COUNT(*) FROM crawl_queue cq
JOIN library_items li ON li.discogs_id = cq.discogs_id
WHERE li.user_id = %(user_id)s AND cq.status IN ('pending', 'in_progress')
  AND EXISTS (
      SELECT 1 FROM crawlers c
      WHERE c.enabled AND c.crawler_type = 'release'
        AND (cq.pending_crawler_ids IS NULL OR c.id = ANY(cq.pending_crawler_ids))
  )
```

Two counts change meaning from pairs to targets: this one, and `POST /api/crawl/start`'s
`enqueued`. Neither is rendered — `CrawlStatus.pending` exists in `frontend/src/api/types.ts` and
is never displayed — so this is a backend semantics change plus comments, with no UI work.

`delete_dead_stock_crawl_queue_rows` keeps working unchanged: it is keyed on `item_key` and
`status = 'pending'`, neither of which this design alters.

## Invariants preserved

- Release rows are claimed ahead of stock-item rows.
- No NULL-price placeholder is created for a target that has not been crawled; a missing
  `listings` row still means "not yet crawled".
- Only `matches[0]` is stored.
- Short-lived connections, each committed on its own, never spanning Playwright calls. Under
  fan-out that is three kinds of transaction per target rather than the one-per-row of the pair-row
  design: one to load the target and resolve its eligible crawlers, one per work unit to write that
  crawler's listing, and one to resolve the queue row. The invariant being protected is unchanged —
  no connection is held across a page load, and no single transaction spans several units, so one
  unit's crash cannot roll back an earlier unit's committed work.
- One request per site in flight process-wide, via the per-`crawler_id` lock, spanning the
  bot-detection retry.
- Failure domains: crawlers declaring the same `failure_domain` share breaker state, applied per
  `crawler_id` to domain peers.
- Empty stock-item results carry no site-health signal.
- The stock source gate: a stock item is only crawled while some enabled store still lists it.

## Testing

Rewrites in `backend/tests/test_crawl_queue.py`:

- Enqueue idempotency and revival at target level; revival resets `pending_crawler_ids` to NULL.
- Claim ordering: release rows ahead of stock-item rows; FIFO within a kind.
- Claim skips rows with a future `available_at`; claims them once it passes.
- Claim still honours the stock source gate.
- Deferral writes the narrowed `pending_crawler_ids` and a future `available_at`, and leaves
  `requested_at` untouched.
- Migration collapse: pending + done pairs for one target become one pending row whose
  `pending_crawler_ids` holds exactly the unfinished crawler ids; all-done pairs collapse to one
  done row with NULL; the guard makes a second run a no-op.
- Pending count excludes rows no enabled crawler can claim, including a narrowed row whose members
  are all disabled.

New coverage in `backend/tests/test_crawl_manager.py`:

- One claimed target fans out to every enabled marketplace crawler in one pass.
- A crawler enabled mid-run is picked up on the next batch without a restart; one disabled mid-run
  stops being dispatched.
- A cooling-down crawler is skipped and deferred rather than waited on, and the rest of the
  target's crawlers still run in that pass.
- `requires_discogs_release` crawlers are excluded for stock-item targets and included for
  release targets.
- A crawler with no loaded plugin is skipped, recorded as a failure, and does not defer the row.
- A target whose eligible set is empty is marked done.
- Work units drain target-major across a multi-row batch, with no barrier between targets.

Backfill coverage (`test_crawler_crud.py` or a new `test_crawler_enable_backfill.py`):

- Enabling revives done targets with `pending_crawler_ids = ARRAY[id]`.
- Targets already priced by that crawler are not revived; targets with a NULL-price row are.
- `requires_discogs_release` crawlers do not revive stock-item rows.
- Narrowed pending rows are widened; NULL ones are left alone.
- Disabling revives nothing.

Existing tests that must keep passing unchanged: the pacing tests, the breaker/failure-domain
tests, the per-row commit isolation test, `_sync_stock`'s 429 abort and streak tests, and the
`sweep_enqueue` tests (with pair counts updated to target counts).

## Risks

- **The migration is one-way.** Collapsing pairs discards which crawlers had already finished for
  a `done` target. That information is only used to avoid redundant crawls, and `listings` already
  records the useful part (what was found), so the loss is bounded to some re-crawling if the
  change is reverted.
- **Longer claim residency.** A row is `in_progress` for `N × pace` rather than one request,
  widening the existing hung-worker stranding window. Mitigated by `batch_size = 2`, not
  eliminated — the reclaim gap remains a known, accepted gap. *(Amended 2026-08-25: closed —
  `db.reclaim_stranded_crawl_queue_rows`. The longer residency this change introduced is also why
  the reclaim threshold is derived from the enabled-crawler count and the pacing setting rather
  than fixed.)*
- **Repeated toggling re-enqueues not-found targets**, as described under Backfill on enable.
- **A crawler enabled mid-pass misses the targets already in flight.** Both backfill statements
  filter on `status` — `done` for the revive, `pending` for the widen — so neither touches a row a
  worker currently holds as `in_progress`. A row claimed before the enable commits resolves its
  eligibility from the pre-enable crawler set, and the backfill then skips it, so that target does
  not get the newly enabled crawler until a later sync or scheduled sweep re-enqueues it. The
  window is one row's pass (seconds to a couple of minutes at `crawl_delay_seconds = 30`), bounded
  to at most `worker_count × batch_size` targets — four by default. This is the same fallback the
  design already accepts for rows the backfill skips via `FOR UPDATE SKIP LOCKED`, and it is
  deliberately not closed here: doing so means re-resolving eligibility at resolution time against
  a claim-time baseline (to avoid re-running crawlers an earlier pass already completed for a
  narrowed row), which adds two queries and a second eligibility-diffing path per row to the hot
  loop. That cost is not worth removing an hours-at-most delay on four targets, and any check of
  this shape leaves a residual window between the re-check and the status write anyway.
- **Deferral churn** if a site's cooldown expires while its peers keep failing: a row can be
  claimed, deferred, and re-claimed several times. Each cycle costs one claim and one update, not
  a crawl, and `available_at` bounds the rate.

## Follow-ups (not in scope)

- **Per-user fairness in claim ordering.** Two mechanisms were considered and rejected for this
  spec. Randomized row selection does not help: with `k` rows claimed from `N` pending, a row's
  expected wait is `N/k` batches either way, because uniform-over-rows weights each user by row
  count exactly as FIFO does over time — it trades determinism for variance, costs the ordered
  index scan, and breaks the release-before-stock tier. The mechanism that would work is weighted
  fair queueing applied at enqueue time: instead of stamping every row in a page-commit with the
  same transaction-start `requested_at`, spread a producer's rows across a virtual timeline
  (`requested_at = now() + row_index × spacing`) so a later, smaller sync interleaves near the
  front. That preserves the claim index, the tier ordering, and FIFO reasoning, and needs no owner
  column. It touches producers and queue ordering rather than dispatch, so it is shaped separately.
- A reclaim/timeout path for rows stranded `in_progress` by a hung worker. *(Shipped separately,
  2026-08-25 — see
  [`2026-08-25-stranded-crawl-queue-row-reclaim-design.md`](2026-08-25-stranded-crawl-queue-row-reclaim-design.md).)*
- Freshness-driven re-enqueue based on `listings.last_checked`.
