# Stranded crawl_queue row reclaim

## Problem

A `crawl_queue` row moves `pending → in_progress → done`.
`claim_crawl_queue_batch` (`backend/db.py`) claims only `status = 'pending'`,
and its own comment has documented since it was written that there is no
reclaim or timeout path for a row left `in_progress` by a worker that never
resolves it: such a row "stays unclaimable by anyone else indefinitely."

That comment understates the consequence. Nothing else can rescue the row
either — every other writer of `crawl_queue` was checked, and each is gated
away from `in_progress`:

| Writer | Gate | Effect on a stranded row |
| --- | --- | --- |
| `mark_crawl_queue_done` | called only from `_process_claimed_rows`, by the worker holding the claim | that worker is gone |
| `enqueue_crawl_queue` | `ON CONFLICT … WHERE crawl_queue.status = 'done'` | a re-sync skips it |
| `enqueue_crawl_queue_for_stock_item` | same `'done'` gate | a stock sync skips it |
| `backfill_crawl_queue_for_crawler` | revive selects `WHERE status = 'done'`; the `pending_crawler_ids` widen that follows selects `WHERE status = 'pending'` | enabling a crawler skips it, twice |
| `delete_dead_stock_crawl_queue_rows` | `WHERE status = 'pending'` | a store disable skips it |
| `revert_crawl_queue_claim` | called only from `_drain_one_batch`'s own cancellation handler, in-process | unreachable once the process is gone |

So a stranded row's *target* is permanently frozen: never re-priced by any
sync, any crawler re-enable, or any schedule, for as long as the table lives.
The population is monotonic — every crash adds to it and nothing subtracts.

This is not hypothetical. The Queue tab (`docs/specifications/shaping/2026-08-25-admin-queue-tab-design.md`),
which shipped precisely to make this visible, reported on 2026-08-25 a live
deployment holding 147 rows `in_progress` of which 137 were over the Stranded
threshold. That deployment runs two Fly Machines × two workers × batch size 2,
so at most 8 rows can be genuinely claimed at any instant. Roughly 93% of the
`in_progress` population was dead weight.

The same snapshot showed 162 in-progress *work units* against those 147 rows —
close to 1:1, where a healthy claimed row fans out to one unit per enabled
release crawler and the ratio should be many-to-one. A ratio that low means
those rows carry narrow `pending_crawler_ids` naming roughly one crawler each,
which is what `defer_crawl_queue_row` writes. The shape that implies is: rows
deferred behind a circuit-breaker cooldown, re-claimed after it expired, then
stranded when a worker died mid-pass. That is a reading of one snapshot, not an
established fact, and nothing in this design depends on it being right — but it
is the reason `pending_crawler_ids` preservation below is load-bearing rather
than cosmetic.

## Goals

- A stranded `in_progress` row returns to `pending` and becomes claimable
  again, automatically, with no operator action and no manual SQL.
- The reclaim and the Queue tab's Stranded tile agree, by construction, on what
  "stranded" means.
- A resumed row picks up the crawlers it still owes rather than redoing the
  ones an earlier pass already finished.
- No new scheduler, no new background task, no new deployment surface.

## Non-goals

- **Worker heartbeats.** A liveness signal — a `crawl_queue_claims` heartbeat
  row, or a worker registry with a TTL — would distinguish "this worker is
  slow" from "this worker is dead", which age cannot. It also needs a new
  table, a new write on the crawl hot path, and a new failure mode of its own
  (a worker alive but unable to write its heartbeat). See "Age, not liveness"
  below for why age is the right trade here and what it costs.
- **A UI surface for the reclaim.** No new tile, no new field in
  `/api/queue/summary`. `crawl_queue` records no "times reclaimed" and adding
  one is a schema change for a reporting figure. The Stranded tile falling to
  zero *is* the observable outcome, and it already exists. Revisit once the
  reclaim has been watched against real numbers.
- **A write path in `routers/queue.py`.** That router stays strictly read-only,
  as the Queue tab design requires. The reclaim lives on the worker path, which
  is where the claim it undoes lives.
- **Bounding how many times one row may be reclaimed.** A row that crashes its
  worker every pass will be reclaimed, re-strand, and be reclaimed again — but
  only once per threshold window (≥30 minutes), because the reclaim is
  age-gated. That is a slow enough loop to be a monitoring problem rather than
  a runaway, and a `reclaim_count` column to cap it would need a policy for
  what to do with a row that hits the cap — dead-letter it, or drop it — which
  is a larger decision than this change.

## Design

### One helper, one threshold

```python
def reclaim_stranded_crawl_queue_rows(conn, crawl_delay_seconds: float) -> int
```

in `backend/db.py`, next to `revert_crawl_queue_claim`, whose semantics it
shares. It computes its cutoff by calling `_queue_stranded_after_seconds(conn,
crawl_delay_seconds)` — the *same function* the Queue tab's Stranded tile is
computed from, not a second definition that happens to agree today.

This is the central constraint of the design. `_queue_stranded_after_seconds`
returns `max(QUEUE_STRANDED_FLOOR_SECONDS, enabled_release_crawlers ×
crawl_delay_seconds × QUEUE_STRANDED_SLACK)` — a derived value that moves when
an admin enables a crawler or changes the pacing setting. A reclaim carrying
its own constant would drift out of agreement with the tile the moment either
changed, and the two disagreeing is worse than either being wrong alone: the
tile is the only instrument an operator has for judging whether the reclaim is
working. Sharing the function makes agreement structural rather than a thing
someone has to remember.

The statement:

```sql
UPDATE crawl_queue SET status = 'pending', claimed_by = NULL, claimed_at = NULL
WHERE id IN (
    SELECT id FROM crawl_queue
    WHERE status = 'in_progress'
      AND claimed_at < CURRENT_TIMESTAMP - %(stranded)s * INTERVAL '1 second'
    FOR UPDATE SKIP LOCKED
)
```

Three columns written, and deliberately no others:

- **`pending_crawler_ids` untouched.** A row deferred to one crawler and then
  stranded resumes owing that one crawler, not every eligible one. Clearing it
  to `NULL` would mean "re-run everything for this target", re-crawling
  crawlers a previous pass already completed and paid for. On the production
  population described above — where the unit:row ratio suggests most stranded
  rows are narrowed to about one crawler — clearing it would multiply the
  reclaim's crawl cost by the enabled-crawler count.
- **`available_at` untouched.** A row that reached `in_progress` was claimable,
  so its `available_at` is already in the past; leaving it there makes the
  reclaimed row claimable immediately. Writing `CURRENT_TIMESTAMP` would be a
  no-op with a worse failure mode if that assumption ever stopped holding.
- **`requested_at` untouched.** This is what puts reclaimed rows at the *front*
  of the queue. `claim_crawl_queue_batch` orders by `(item_key IS NOT NULL),
  requested_at, id`, so a row that has been stuck for hours sorts ahead of
  everything enqueued since. Bumping it would punish the row for having been
  stranded. Same reasoning `defer_crawl_queue_row` already carries.

`FOR UPDATE SKIP LOCKED` in the subquery, not a bare `UPDATE … WHERE`: several
workers across several Machines run this concurrently, and a plain UPDATE would
have them queue on each other's row locks. Skipping a row another worker is
already reclaiming loses nothing — that worker is reclaiming it.

Returns `rowcount`, for logging.

### Where it runs

Inside `_drain_one_batch`'s existing `_claim_batch` closure
(`backend/crawl_manager.py`), in the **same transaction as the claim**, one
statement before it:

```python
def _claim_batch():
    with get_app_pool().connection() as conn:
        reclaimed = reclaim_stranded_crawl_queue_rows(conn, crawl_delay_seconds)
        rows = claim_crawl_queue_batch(conn, worker_id, limit=batch_size)
        conn.commit()
        return rows, reclaimed
```

Consequences worth stating:

- **A row reclaimed here is claimable by the very next statement.** The claim's
  inner `SELECT … FOR UPDATE SKIP LOCKED` sees this transaction's own writes,
  and the rows it just locked are its own locks, so `SKIP LOCKED` does not skip
  them. A worker that finds nothing else to do can therefore rescue a stranded
  row and start crawling it in one round trip.
- **No new scheduler.** The worker loop already calls `_drain_one_batch`
  continuously — every batch when busy, every 5 seconds when the queue is
  empty. The reclaim inherits that cadence for free. This was the deciding
  factor over an APScheduler job: a scheduled sweep would be a second place
  that writes `crawl_queue` on a timer, on every Machine, with its own
  failure mode when the scheduler doesn't start.
- **The transaction stays short** — two statements and a commit, exactly as
  before. The claim's existing contract ("the row lock is held until the caller
  commits") is unchanged; the reclaim commits with it.

`crawl_delay_seconds` comes from `load_config()`, read **before** the app
connection is borrowed. `load_config()` reads `app_config` through the *admin*
pool, and holding an app-pool connection — the pool the workers claim through —
while doing unrelated I/O on another pool is the exact shape PR #181 just
removed from `routers/queue.py`. The same rule applies here.

Cost of doing this every drain iteration: one `load_config()` read, one
`SELECT COUNT(*)` over `crawlers`, and one UPDATE served by the existing
partial index `crawl_queue_active_idx (status) WHERE status <> 'done'`. That is
strictly less often than `_pace_and_search`, which already calls
`load_config()` once per *work unit* — so this adds no new class of load, and
no throttle is introduced. A throttle would be a scheduler wearing a disguise.

### Age, not liveness

The reclaim infers death from a claim's age. It cannot distinguish a dead
worker from a live one that is merely slow, so a sufficiently slow pass will
have its row reclaimed out from under it and crawled twice, concurrently.

That is survivable, and cheaply, because the crawl's terminal writes are
idempotent: `upsert_listing` is an upsert keyed on the (target, crawler) pair,
so a duplicate pass overwrites with an equal-or-fresher price rather than
duplicating a row. `mark_crawl_queue_done` is likewise idempotent — the losing
worker's `UPDATE … WHERE id = %s` just sets an already-`done` row `done` again.
The observable cost of an over-eager reclaim is one redundant page load per
crawler on that row, plus its pacing delay.

It is not free, though, and the honest statement of the trade is: this design
buys "no infrastructure" with "the threshold has to stay generous." That is
why the threshold is `_queue_stranded_after_seconds` and not something tighter
— `QUEUE_STRANDED_SLACK = 4` already means a claim must outlast four times the
honest worst case before anything touches it, and the ≥30-minute floor covers
a small or unconfigured deployment. Recovery inside half an hour, from a
condition whose current recovery time is *never*, is the win; shaving that to
minutes is what heartbeats would be for.

One case is explicitly **not** this gap: cancellation during graceful shutdown.
`_drain_one_batch` reverts an in-flight claim on cancellation and shields
everything after a successful one, so an orderly stop leaves no stranded rows
and never depends on this path.

### Logging

When the reclaim returns non-zero, `_drain_one_batch` logs at warning level
with the worker id and the count. Non-zero means either a worker died or a pass
overran the threshold, and both are worth a line in the log. Zero logs nothing
— that is the steady state and it happens every few seconds.

## Queue tab labelling

Folded into this change because the reclaim is what makes the Stranded tile's
number *move*, and an operator watching it move needs the tab's two units to be
distinguishable.

The tab renders both units on one screen with one label. `QueueView.tsx` shows
an "In progress" stat tile reading `totals.in_progress_rows` and, in the same
render from the same snapshot, a donut segment reading
`totals.in_progress_units`. Both are labelled exactly "In progress", the legend
prints its numbers bare, and only some of the row-denominated tiles carry a
`rows` hint — `Claimable` and `In progress` do, `Held`, `Stranded` and
`Unactionable` do not. The two numbers differing (147 against 162) reads as a
sync bug in the tab. It is not one: they are different units of the same
snapshot, exactly as "The counting model" in the Queue tab design intends.

The fix is labelling only — no number changes:

- Every row-denominated stat tile states `rows` in its hint, not just two of
  them.
- Every donut legend entry states `units` beside its number.

The explanatory paragraph under the legend already defines a work unit; what
was missing was the unit on the figures themselves.

## Testing

Backend, in `backend/tests/test_crawl_queue.py`:

- A row aged past the threshold is reclaimed **with `pending_crawler_ids`
  intact**, and `claim_crawl_queue_batch` then returns it carrying that same
  narrowed set. This is the required test. It asserts the preserved array, not
  just the status.
- A row claimed *inside* the threshold is left alone.
- The threshold tracks `_queue_stranded_after_seconds` rather than a constant:
  the same row, at the same age, is reclaimed or not depending on
  `crawl_delay_seconds` and the enabled-crawler count. This is what pins the
  reclaim to the tile's definition; a hardcoded cutoff would pass every other
  test in this list.
- `requested_at` survives, so a reclaimed row keeps its place at the front.
- Nothing `pending` or `done` is touched.

In `backend/tests/test_crawl_manager.py`:

- `_drain_one_batch` claims a row that was stranded before it ran. This is the
  end-to-end assertion and the load-bearing one: against unmodified
  `db.py`/`crawl_manager.py` it fails on the claim count, `0 == 1`. The
  `test_crawl_queue.py` tests above all fail before the fix too, but only by
  `AttributeError` on the new helper — which proves the helper is new, not that
  the path was broken. This one proves the path.
- `_drain_one_batch` leaves a row claimed moments ago to the worker holding it.
  A regression guard, not a fail-first test: it passes against the merged code,
  because the merged code never reclaims anything. It exists so that a later
  change loosening the threshold, or dropping it, is caught.

Frontend, in `frontend/src/test/queueView.test.tsx`: the "In progress" tile and
the "In progress" legend entry render their differing values each with its own
unit, from one snapshot where `in_progress_rows` and `in_progress_units`
deliberately differ; and every row-denominated tile states `rows`. Both fail
against the merged view. One existing assertion in that file pinned the
`Unactionable` hint string exactly and is updated to the new text.

Confirmation method: the source change was stashed and the tests re-run, rather
than the tests being read for whether they *should* fail. Every test named above
as failing before the fix was observed doing so.
