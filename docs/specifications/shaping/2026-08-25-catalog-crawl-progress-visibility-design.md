# Catalog crawl progress visibility

Date: 2026-08-25
Branch: `claude/dischord-crawler-logging-0larf8`

## Problem

Clicking Refresh on the Dischord Records row in Settings produced one log
line — `Loaded crawler: Dischord Records` — and then nothing. No further
log output, no status-bar movement, no way to tell a working crawl from a
wedged one. Meanwhile every other store's Refresh was rejected with
`Stock sync already running, ignoring start request`, which named neither
what was running nor for how long, and the frontend rendered nothing at
all for a rejected click.

Three separate gaps produced that silence:

**1. A two-phase crawler reports nothing until a whole listing page is
done.** `crawl_progress.report_page()` is the only progress signal a
catalog crawler has, and it fires once per *listing* page. For a one-phase
crawler (`shopify_catalog.iter_products()` and friends) that's one paced
HTTP request apart. `dischordrecords.py` is two-phase: it must fetch a
detail page per release before it knows any prices, so the gap between one
`report_page()` and the next is a listing page's worth of paced detail
fetches. At the default `crawl_delay_seconds` of 30 that is tens of
minutes, and
[`2026-08-23-dischordrecords-crawler-design.md`](2026-08-23-dischordrecords-crawler-design.md)
puts the full run at roughly 108 minutes.

**2. The lifecycle events that did exist were SSE-only.** `_sync_stock`
broadcast `stock_sync_source_started` but never logged it, and
`_run_catalog_crawler`'s page reporter broadcast `stock_sync_page_fetched`
without logging either. The bottom status bar shows only the latest event
and is gone on reload; the Log Viewer is the durable record someone goes
back to. Nothing in it ever named the source actually being crawled.

**3. A rejected Refresh was invisible in the UI.** `POST
/stock/sync/start` answered `{"started": false, "running": true}` and
`App.tsx`'s `handleRefreshStoreCrawler` discarded the result, so the click
looked like it had done nothing.

The stock sync is deliberately one shared job under one advisory lock
(see
[`2026-08-07-store-crawler-refresh-button-design.md`](2026-08-07-store-crawler-refresh-button-design.md)
and
[`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)),
so serialisation is correct and unchanged here. What was missing is any
way to see *what* holds it.

## Goals

- A long catalog crawl produces a steady, durable log trail from start to
  finish, at intervals bounded by one paced request rather than one
  listing page.
- The source currently being crawled is identifiable from the Log Viewer
  alone.
- A rejected Refresh tells the user what is running and for how long, in
  both the log and the UI.

## Non-goals

- **Changing the serialisation.** One stock sync at a time, one advisory
  lock, remains the design.
- **Speeding up the Dischord crawl.** Its pacing is a crawl-citizenship
  decision recorded in its own spec; the lever there, if ever needed, is a
  lower per-request delay for that site, not a change here.
- **A watchdog that cancels a stalled crawl.** This change makes a stall
  visible; deciding what to do about one is separate.
- **Retrofitting every crawler.** `report_detail()` is available to any
  plugin with a per-item phase. `dischordrecords.py` and
  `darkdescentrecords.py` call it -- the crawlers here whose pacing puts a
  page's worth of paced requests between two `report_page()` calls. A
  one-phase crawler gains nothing from it.

## Design

### `crawl_progress.report_detail(done, total, label)`

A second reporter alongside `report_page()`, installed the same way — a
`contextvars` variable set by `_run_catalog_crawler` for the duration of
one `crawl_catalog()`, so an async generator's paging loop reaches it
without threading a callback through every plugin. `done`/`total` count
one listing page's detail fetches; `label` names the batch they belong to
(`"listing page 2/8"`). A no-op when no reporter is installed, so a
crawler stays directly runnable outside a stock sync.

Kept as a separate function rather than an overload of `report_page()`
because the two report different units — pages fetched vs. items fetched
*within* a page — and the frontend renders them differently.

### `stock_sync_detail_progress` SSE event

`{source, done, total, label}`, broadcast by `_run_catalog_crawler`'s
reporter on the existing `/api/crawl/stream` channel. Untagged, like every
other `stock_sync_*` event: one shared catalog refresh has no per-user
owner, and `routers/crawl.py`'s `_visible_to` gate only filters events
carrying a `user_id` (see
[`2026-08-23-per-user-sse-event-filtering-design.md`](2026-08-23-per-user-sse-event-filtering-design.md)).

`App.tsx` renders it into the existing bottom status bar as
`Syncing in-stock catalog… {source} {label} — {done}/{total} releases`,
singular `release` when `total` is 1, matching what the page-progress
handler beside it already does for products. Reachable here rather than
hypothetical: dedup is crawl-wide, so a listing page whose releases
mostly appeared on the previous one can contribute a single new href.

### Both reporters now log as well as broadcast

`_run_catalog_crawler` logs each reported page and each reported detail
fetch at INFO, and `_sync_stock` logs `[{source}] Stock crawl started`
next to the existing `stock_sync_source_started` broadcast. INFO, not
DEBUG or WARNING: `routers/logs.py` filters in SQL by exact level
membership (`WHERE level = ANY(...)`), not level-and-above, so anything
outside the INFO stream is invisible to someone watching the rest of the
crawl narrative — the same reasoning that already governs the
dead-queue-row sweep line.

Log volume is bounded by pacing, not by catalog size: one line per paced
request, so at the default delay a Dischord run emits on the order of one
line every 22 seconds.

The two completion lines gain elapsed time —
`[{source}] Stock sync found N items in 1h 48m` and
`Stock sync complete: N items in 2h 3m` — which is what makes a *later*
reading of the log able to answer "was that normal?"

### Identifying the lock holder

`CrawlManager` tracks the sync's start time, the current source, and that
source's start time; `stock_sync_state()` exposes them as
`{running, source, elapsed_seconds, source_elapsed_seconds}`, all `None`
when nothing is running or before the first crawler is reached (the task
sets them, so a caller that reads immediately after `create_task` sees the
not-yet-started shape). They are cleared in `_sync_stock`'s `finally`,
alongside the advisory-lock release.

`start_stock_sync`'s rejection log becomes
`Stock sync already running (on Dischord Records for 1h 15m, running 1h 30m
in total), ignoring start request`.

`start_stock_sync` returns `{"started", "on_another_instance", **state}`
rather than a bare bool, and `POST /stock/sync/start` returns that verbatim.
It is the only place that knows *which* of its two rejections happened, and
they describe different worlds: the in-process one can read the local state,
but on the cross-Machine advisory-lock rejection this process has no
`_stock_task`, so `stock_sync_state()` would report the idle shape --
`running: false`, no source, no timings -- for a sync that is genuinely
running elsewhere. That path therefore states `running: True` and
`on_another_instance: True` directly, leaving source and timings null
because they live in the holder's memory and are not readable from here.
Assembling the response in the router from a second `stock_sync_state()`
read would also race a sync that finished in between and report a bare
"nothing running."

`start_stock_sync`'s guard, lock acquisition, and task assignment run under
one `asyncio.Lock`, created lazily because the module-level `crawl_manager`
singleton is built at import, before any event loop exists (the same reason
`_site_locks` is populated on first use). Without it the classification is
unsound: `_stock_task` is not assigned until after the threadpool
acquisition awaits, so two callers on this process could both clear the
`stock_sync_running` guard, and the loser -- finding the advisory lock held
by the *other local request* -- would be told another Machine owns it.
Serialized, the loser waits and then takes the in-process branch carrying
the winner's real state, which is both correct and strictly more
informative.

Surfacing the holder's source and elapsed time *across* Machines would need
shared lock-holder metadata in Postgres, kept fresh as the holder walks its
sources. That is a larger change than this one and is not attempted here;
`on_another_instance` is what lets the UI say something true without it.

`App.tsx` renders a rejected start into the status bar as
`In-stock sync already running — {source} ({source elapsed} so far),
{total elapsed} in total. Try again once it finishes.`, or, when
`on_another_instance` is set, `In-stock sync already running on another
instance. Try again once it finishes.` The flag is what separates that case
from a sync running *here* that has not yet reached its first crawler --
both carry a null source and null timings.

Durations are formatted `45s` / `14m` / `1h 48m` by matching helpers on
each side (`crawl_manager._format_duration`, `App.tsx`'s `formatElapsed`).

### The two-phase crawlers report their detail phase

#### `dischordrecords.py`

One `report_detail(0, N, label)` before the loop over a page's release
hrefs — which puts the size of the wait on the record before the first
fetch has even finished — then one per href after its fetch resolves,
including an href skipped on 404 (a skip still advances the count, so the
last report of a page never sits below its total and reads as a stall). A
raise still aborts before reporting, since the crawl is over either way. The
leading `0/N` is skipped when a page's releases were all seen on an earlier
one, where `0/0` would announce a wait that isn't coming.

#### `darkdescentrecords.py`

Same silence, different shape. Its listing request returns a page of products
at once, and only a *variable* product costs a paced detail fetch; a simple
one is priced from the listing payload. So `total` counts the page's variable
products rather than all of its products, which is what keeps the reporting
rate at one line per paced request. Counting every product instead would emit
a burst of instant reports for the simple ones and then fall silent on each
variable one -- the opposite of the signal this is for.

`_needs_detail_fetch(product)` is the single predicate deciding that, and
`_items()` branches on it rather than on `type` directly, so the total counted
before a page's loop cannot drift from what the loop actually fetches. Its
purchasable/in-stock/artist gates are redundant where `_items()` calls it --
that path has already returned `[]` for those -- and are exactly what make the
count right where `crawl_catalog()` calls it, before anything is filtered.
The label is `"listing page {page}"`; there is no total, since this crawler
pages until a short response rather than reading a page count up front.

## Testing

- `backend/tests/test_crawl_manager.py`: page and detail reports each
  produce their log line as well as their SSE event; the detail reporter
  is cleared when a crawl ends; `_sync_stock` logs the source it is
  starting and the elapsed time it finished in; `stock_sync_state()` while
  running and while idle; the rejection log with and without a source
  reached; `_format_duration` across its boundaries; two concurrent
  starts on one process yielding exactly one winner and a loser
  reported as in-process, not cross-Machine.
- `backend/tests/test_dischordrecords_crawler.py`: progress reported
  across every listing page including the leading `0/N`; a 404-skipped
  release still advances the count; the crawl still runs with no reporter
  installed.
- `backend/tests/test_darkdescentrecords_crawler.py`: a page's variable
  products reported one per paced fetch while its simple products neither
  advance the counter nor count toward the total; `_needs_detail_fetch`
  agreeing with every gate `_items()` applies; a page of only simple
  products reporting nothing at all; the label naming the listing page it
  is on; the crawl still runs with no reporter installed.
- `backend/tests/test_stock_router.py`: the start endpoint's payload when
  accepted, when rejected in-process, and when rejected because another
  Machine holds the advisory lock.
- `frontend/src/test/inStockTab.test.tsx`: `stock_sync_detail_progress`
  in the status bar; a rejected Refresh names the holder; a cross-Machine
  rejection says so instead of claiming "unknown in total"; an accepted one
  stays quiet.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change adds no new trigger, no new outbound host,
and no new inbound interface — `stock_sync_detail_progress` added to an
existing SSE stream, and `on_another_instance`, `source`, `elapsed_seconds`,
and `source_elapsed_seconds` added to an existing endpoint's response.
Named rather than counted, per `CLAUDE.md`: the enumeration stays correct
when the shape changes, and this sentence's count had already gone stale
once within the same branch.

`backend/version.py`'s `VERSION` is derived from git and is not edited by
this change.
