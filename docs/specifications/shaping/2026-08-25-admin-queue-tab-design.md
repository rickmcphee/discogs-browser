# Admin Queue tab

## Problem

The crawl queue is the app's central piece of shared machinery and it is
completely opaque. `GET /api/crawl/status` returns one number —
`count_pending_crawl_queue_for_user`, scoped to the calling user's own
`library_items` — and nothing else. Everything an operator would actually want
to know about the queue is either unexposed or unrecorded:

- How much work is outstanding, and how it splits between the two target kinds
  (release rows and stock-item rows, which claim in that fixed priority order).
- Which marketplace crawlers that work fans out to. A queue row names a
  *target*, never a crawler, so this is not a column anyone can read — it is a
  join against live `crawlers` state, resolved per row at dispatch.
- How much of the queue is *held* behind `available_at` because a pass deferred
  it into a circuit-breaker cooldown, versus genuinely claimable now.
- Whether any row is stranded `in_progress`. `claim_crawl_queue_batch`'s own
  comment documents that there is no reclaim or timeout path: a row left
  `in_progress` by a crashed browser or a hung worker "stays unclaimable by
  anyone else indefinitely", and `count_pending_crawl_queue_for_user` counts it
  without being able to tell it from real work. Today nothing surfaces it.
- Whether any pending row is unactionable — gate-failing stock rows that
  `delete_dead_stock_crawl_queue_rows` will sweep, and rows whose narrowed
  `pending_crawler_ids` are all disabled, which `_drain_one_batch` marks done
  only when it happens to reach them.

The operator-facing question this is all in service of is "why isn't the queue
draining", and there is currently no way to answer it short of opening a psql
session.

## Goals

- An admin-only **Queue** tab giving a live, read-only view of the shared crawl
  queue: its overall shape, its per-crawler fan-out, and a drill-down for one
  selected crawler.
- Surface the two conditions nothing surfaces today — stranded `in_progress`
  rows and unactionable pending rows — as first-class numbers.
- Cost the view so it can be polled continuously without competing with the
  worker pool for the database.

## Non-goals

- **Any write path.** The tab is strictly read-only. It does not reclaim
  stranded rows, purge dead rows, clear a cooldown, or enable/disable a
  crawler. Adding the reclaim path `claim_crawl_queue_batch` documents as
  missing is a separate change with its own correctness argument to make;
  observing the problem does not require fixing it, and shipping the observer
  first means that change can be validated against real numbers.
- Surfacing in-process circuit-breaker state (`_site_consecutive_failures`,
  `_site_cooldown_until`). Those dicts live in one `CrawlManager` instance, so
  on a two-Machine deployment an admin would see whichever Machine served the
  request and have no way to know it. `available_at` is the same signal
  recorded in the database, is globally true, and is what the tab reports
  instead.
- Queue history or trend lines. Nothing records queue depth over time, and
  inventing a sampler is out of scope.
- Changing how the queue is claimed, ordered, or drained.

## The counting model

A `crawl_queue` row names a target — `discogs_id` xor `item_key` — and no
crawler. The `crawler_id` column was deliberately dropped (see the migration
guarded on `information_schema` in `backend/db.py`); `_drain_one_batch` calls
`db.get_eligible_crawlers` per claimed row against live `crawlers` state, so a
row's crawler set is a runtime decision, not row data.

Two units follow from that, and the tab must never conflate them:

- A **row** is one target. This is what the queue's length actually is, what
  drains, and what the ETA is denominated in.
- A **work unit** is one (row, crawler) pair — one search a worker will
  perform. This is the fan-out, and it is what a per-crawler number counts.

Work units sum to far more than rows, because almost every enabled crawler is
eligible for almost every row. That is also why the per-crawler breakdown is
rendered as a sorted bar list rather than as ring segments: segmented by
crawler, a ring comes out near-uniform, and the signal an admin wants — a
crawler whose share is *short* — is the one thing a donut of near-equal arcs
cannot show.

Eligibility mirrors `get_eligible_crawlers` exactly: `enabled`,
`crawler_type = 'release'`, `discogs_id IS NOT NULL OR NOT
requires_discogs_release`, and `pending_crawler_ids IS NULL OR id = ANY(...)`.
The claim-side gates from `claim_crawl_queue_batch` apply too: a stock row
counts only while `_enabled_stock_source_exists` still holds for its
`item_key`, and `available_at > CURRENT_TIMESTAMP` marks a row held rather than
claimable. Any divergence between this resolution and dispatch's is a bug in
this feature, not a difference of opinion.

## Cost: why the fan-out is not a join

The obvious query — pending rows joined to eligible crawlers — materializes a
cross product. A stock sync can enqueue on the order of 20,000 rows, and with
the bundled release crawlers between them that is on the order of a million
rows to aggregate, on every poll, against the same database the worker pool is
claiming from.

It is not necessary. `pending_crawler_ids IS NULL` is the common case (the
schema comment calls narrowed rows "a small minority"), and every NULL row
resolves to the *same* crawler set for a given target kind. So the summary is
computed as two aggregates and combined per crawler in Python:

- **Broad**: rows with `pending_crawler_ids IS NULL`, grouped by target kind
  and held-ness. A handful of output rows regardless of queue size.
- **Narrowed**: rows with an array, `unnest`ed and grouped by crawler id as
  well. Proportional to the narrowed minority, not to the whole queue.

This constrains which per-crawler statistics are available: only those that
compose across the two aggregates. `MIN(requested_at)` and bucketed age counts
compose; a median does not (the median of a union is not derivable from two
medians). The tab therefore reports **oldest wait plus age buckets**, which
shows a starving tail more directly than a median would anyway.

`crawl_queue` gains `crawl_queue_completed_idx ON (completed_at) WHERE status =
'done'`, so the drain-rate window is a bounded range scan rather than a walk of
a row per target this app has ever queued.

`listings` gains `listings_crawler_last_checked_idx ON (crawler_id,
last_checked)`, and the activity query is driven *from* `crawlers` rather than
grouped over `listings`, so neither half visits rows outside the window. A bare
`GROUP BY crawler_id` has no `WHERE` to restrict it — the all-time `MAX` forces
every row of the table, or of the index, to be visited on each poll. Two
correlated subqueries ride the index instead: the recent count as a range scan
of just that slice, the recency as a single backward probe of one entry.

## API

Both endpoints are `dependencies=[Depends(require_admin)]`, in a new
`backend/routers/queue.py`. The summary runs under a single `REPEATABLE READ`
transaction: it issues several queries while the worker pool is claiming and
finishing rows throughout, and at `READ COMMITTED` each would see a different
queue — a routine poll could count one row as claimable in the stat tiles and
its units as in progress in the donut. Every statement is a read, so the
stricter isolation has nothing to serialization-fail on. Both read global tables (`crawl_queue`, `crawlers`,
`listings`, `catalog`, `stock_item_identities`), none of which carry a per-user
owner column, so they use `db.get_app_pool()` — the same pool
`_drain_one_batch` reads them through — not `user_scope`.

### `GET /api/queue/summary`

```
{
  "pool_running": bool,
  "generated_at": iso8601,
  "stranded_after_seconds": float,   // derived; see below
  "activity_window_seconds": 3600,
  "totals": {
    "claimable_rows": int,      // pending, available now, gate-passing, actionable
    "held_rows": int,           // pending, available_at > now
    "in_progress_rows": int,
    "stranded_rows": int,       // in_progress, claimed_at older than the threshold
    "unactionable_rows": int,   // pending, no eligible enabled crawler, plus dead stock rows
    "claimable_release_rows": int, "claimable_stock_rows": int,
    "rows_done_last_hour": int, // done rows whose completed_at falls in the window
    "eta_seconds": float|null,  // claimable_rows / drain rate; null when the rate is 0
    "claimable_units": int, "held_units": int, "in_progress_units": int
  },
  "crawlers": [{
    "crawler_id": int, "site_name": str, "requires_discogs_release": bool,
    "claimable_units": int, "held_units": int, "in_progress_units": int,
    "release_units": int, "stock_units": int,   // whole pending backlog, held included
    "oldest_wait_seconds": float|null,
    "age_buckets": {"under_1h": int, "under_24h": int, "over_24h": int},
    "results_last_hour": int, "last_result_seconds_ago": float|null,
    "eta_seconds": float|null
  }]
}
```

`crawlers` lists every enabled release crawler, including those with no
outstanding work — an enabled crawler sitting at zero while the queue is deep
is itself worth seeing, and an inner join would hide it.

`rows_done_last_hour` is measured off a dedicated `crawl_queue.completed_at`,
stamped by `mark_crawl_queue_done` and cleared by every revival path
(`enqueue_crawl_queue`, `enqueue_crawl_queue_for_stock_item`,
`backfill_crawl_queue_for_crawler`), so it is set only on a row that is
currently `done`. `claimed_at` cannot stand in for it: a row fans out to one
sequential search per eligible crawler, paced by `crawl_delay_seconds`, so a
row claimed well outside the window routinely completes inside it. Counting
claims would report a zero drain rate — and a null ETA everywhere — precisely
while long-running rows were finishing.

**Stranded is derived, not a constant.** A claimed row runs one sequential
search per eligible crawler, each preceded by a wait of 50–100% of
`crawl_delay_seconds` and capped by a page-load timeout, so how long a claim can
legitimately last scales with both the pacing setting and how many crawlers are
enabled. A fixed threshold contradicted the `completed_at` argument above — the
same fan-out that makes `claimed_at` useless as a completion proxy also means
healthy rows stay claimed well past half an hour on any realistic crawler set,
and they would have lit a tile coloured *critical*. The threshold is
`max(floor, enabled_release_crawlers × crawl_delay_seconds × slack)`, reported
in the response so the UI can label the tile with the figure actually used. The
slack is generous on purpose: a tile that cries wolf is worth less than one that
notices late. `crawl_delay_seconds` is read by the router through the admin
pool, since `app_user` has no grant on `app_config`.

**`in_progress_units` counts units in claimed rows, not units remaining.** A
claimed row's unit list is built once by `_drain_one_batch` and worked through in
memory; nothing narrows `pending_crawler_ids` as individual units finish (only a
deferral rewrites it). So a unit already crawled earlier in the row keeps
counting until that row resolves, and toggling a crawler mid-row moves the number
even though the worker's own list is fixed. Narrowing the row per completed unit
would put a write on the crawl hot path to sharpen a reporting figure — the wrong
trade — so the tab names the segment for what it measures instead.

`release_units`, `stock_units`, `oldest_wait_seconds` and `age_buckets` cover a
crawler's whole pending backlog, claimable and held alike; only
`claimable_units`/`held_units`/`in_progress_units` split it by state. Excluding
held work from composition would empty the detail panel for exactly the crawler
an admin reached by asking what is held — a fully held crawler would report a
real held backlog beside "0 release, 0 stock item" and no oldest wait. The ETA
below does need the claimable stock figure specifically; that is tracked
separately rather than by narrowing what `stock_units` means.

Per-crawler `eta_seconds` divides a position estimate by the same drain rate.
The estimate leans on the fixed claim order, in which every claimable release
row sorts ahead of every claimable stock row: a crawler with no *claimable*
stock units is positioned behind the release rows only, everything else behind
the whole claimable queue. It is an estimate and is labelled as one; it ignores
narrowing, which can only make a crawler's true position earlier.

`results_last_hour` counts **distinct `listings` rows whose `last_checked`
moved** inside the window. It is not a count of searches, and the difference
runs in both directions, so the UI names it "listing rows touched" and states
the blind spots rather than presenting it as a throughput rate:

- A release crawl that finds *nothing* still counts, because `_drain_one_batch`
  calls `clear_listing_price`, which bumps `last_checked` on the existing row.
- A first-ever miss counts for nothing: with no row yet that `UPDATE` matches
  nothing, and the "no listings pre-population" invariant means none is
  created. A stock-item miss never touches anything either — the clear path is
  guarded on `is_release`.
- Repeat passes over the same (target, crawler) inside the window collapse to
  one row, since `upsert_listing` is an `ON CONFLICT DO UPDATE` in place.

### `GET /api/queue/crawlers/{crawler_id}/next?limit=25`

The next targets this crawler will actually be run against, in
`claim_crawl_queue_batch`'s own sort order — `(item_key IS NOT NULL),
requested_at, id` — filtered by the same eligibility and gate predicates.
Returns `artist`, `title`, `kind` (`release`/`stock`), `waiting_seconds`, and
`narrowed` (whether the row carries a `pending_crawler_ids` array). Split out
from the summary because it needs `catalog`/`stock_item_identities` joins and
is only wanted on click.

## UI

`frontend/src/views/QueueView.tsx`, reached from a **Queue** nav button gated
on `showAdminNav` exactly as Logs and Settings are. Like `LogViewer`, the view
is not mounted at all for a non-admin. It polls `/api/queue/summary` every 10
seconds, only while the tab is the active view and the document is visible.
Each poll carries a generation counter and a response from a superseded one is
dropped: `setInterval` will start a second request while the first is in flight,
and an older snapshot or error landing last would be exactly the wrong failure
in a view whose job is to report live state.

**Top half.**

- A KPI row of stat tiles: pool state, claimable rows, in progress, held,
  stranded, unactionable, drain rate, queue ETA. The pool tile is labelled
  "this machine only": `crawl_manager.pool_running` is process-local, so on a
  multi-Machine deployment consecutive polls can land on different Machines, and
  neither value says anything about the other's pool. Every other tile is global
  database state. Small print carrying diagnostic meaning — every tile hint, the
  composition labels, the activity caveat — is rendered at a contrast that
  clears 4.5:1 against the tile surface rather than the app's usual recessive
  grey, which measured about 2.3:1. Stranded and unactionable
  carry status colors (`critical` `#d03b3b`, `warning` `#fab219`) with their
  labels beside them, never colour alone; the rest wear text tokens.
- One donut, showing the part-to-whole a ring is actually good at: total
  outstanding **work units** split into In progress / Claimable / Held. Its
  centre counts *rows*, and counts unactionable ones too even though they
  contribute no arc — leaving them out let the centre read "0 rows" beside a
  non-zero Unactionable tile. Three
  segments, coloured on a single-hue ordinal ramp — `#86b6ef`, `#3987e5`,
  `#184f95`, light for work in flight through dark for work that is stuck —
  validated as an ordinal ramp against the app's `#030712` surface. The centre
  reads the row count, labelled as rows, so the two units cannot be confused.
- A sorted horizontal bar list, one row per enabled release crawler: name, bar,
  count, and a held badge. With a ring segment selected, the bar, the number and
  the ordering all describe *that* state; otherwise they describe claimable work
  with held alongside. Filtering by a state and then rendering a matching
  crawler as a bare "0" — which sizing everything on claimable units alone did
  for In progress — is worse than not filtering. This is the clickable surface. Bars use emphasis — the selected crawler in the accent
  hue, the rest recessive — rather than a per-crawler hue, which past a handful
  of series would carry no information.
- Clicking a ring segment filters the bar list to crawlers with units in that
  state.

**Bottom half.** The selected crawler's detail, in three panels:

1. **Age & composition** — oldest wait, the age buckets, and a two-segment
   stacked bar splitting release units from stock units.
2. **Throughput & ETA** — results written in the window (with its floor caveat
   stated inline), time since this crawler last wrote any result at all, and
   the estimated drain time with the rate it was derived from.
3. **Next up** — the `next` endpoint's table.

With nothing selected the bottom half prompts for a selection rather than
rendering an empty frame.

## Testing

Backend tests build queue state through the code under test —
`enqueue_crawl_queue`, `enqueue_crawl_queue_for_stock_item`,
`register_crawler`, `claim_crawl_queue_batch`, `defer_crawl_queue_row` — never
by hand-writing `crawl_queue` rows, so the fixture cannot drift from what
dispatch actually produces. Coverage: admin gating on both endpoints; fan-out
arithmetic for broad and narrowed rows and for both target kinds; the
`requires_discogs_release` exclusion; held rows counted as held and not
claimable; the stock source gate; stranded and unactionable counts; and `next`
returning claim order.

Frontend tests cover the nav button's admin gating (alongside the existing Logs
and Settings assertions), that the view is not mounted and does not poll for a
non-admin, segment/bar selection driving the detail fetch, and the empty state.
