# Refresh-click feedback design

Date: 2026-08-26
Branch: `claude/crawler-refresh-feedback-0cwvat`

## Problem

Clicking a Refresh button in Settings produced nothing an admin could see, for
long enough that the click read as a no-op.

Every Refresh button in Settings starts work that is *reported* over SSE rather
than returned by the click's own request, and in each case the reporting event
arrives well after the click:

- **A store's per-row Refresh** (`↻`, Store Management crawler table).
  `POST /stock/sync/start` opens a fresh `psycopg` connection and takes a
  Postgres advisory lock before it even returns, and the `stock_sync_started`
  event that sets `stockSyncTarget` comes later still. Until it landed, the
  button was unchanged and the status bar was empty.
- **The bulk Store Management Refresh.** Same request, same gap.
- **The Marketplace Management Refresh.** `POST /crawl/start` only *enqueues*;
  the `started` event that raises the crawl banner comes from whenever the
  shared worker pool next drains the queue, which can be much later than the
  click — and if nothing was enqueued (a "missing only" run with no missing
  prices), no event is coming at all and the click is silent forever.

What feedback existed once the event finally arrived was also close to
invisible: the per-row button swapped its glyph from `↻` to `⟳` — two
near-identical characters at the same size, weight and colour, on a row that
otherwise didn't change — while the same `disabled:opacity-30` dimmed the
running row and the idle rows alike, so the one store actually being crawled
was the hardest thing in the table to pick out.

This is the accepted-start half of the gap that
[`2026-08-25-catalog-crawl-progress-visibility-design.md`](2026-08-25-catalog-crawl-progress-visibility-design.md)
closed for the *rejected* half: a Refresh rejected because a sync was already
running now says so, but a Refresh that was accepted still said nothing.

## Scope

Frontend only. No API, schema, or crawler change — every value rendered here
was already on the wire.

Touches:

- `frontend/src/App.tsx` — optimistic "starting" state for the stock sync and
  the price crawl; one shared stock-sync start handler; the enqueued count
  reported from `POST /crawl/start`'s reply.
- `frontend/src/views/Settings.tsx` — spinners, an inverted (undimmed) active
  row button, and a highlighted row.
- Tests: `frontend/src/test/settings.test.tsx`,
  `frontend/src/test/inStockTab.test.tsx`,
  `frontend/src/test/crawlStatusBar.test.tsx`.

Out of scope: highlighting the row of the source a *bulk* run is currently
scanning. `stock_sync_source_started` carries the site name and could drive it,
but that is progress reporting for a run already under way, not confirmation
that a click landed.

## Decisions

- **Optimistic, then handed over.** The click claims the button immediately;
  the real `stock_sync_*` state takes it back as soon as the server confirms.
  The optimistic value never overrides a real one — a per-store Refresh
  rejected by an already-running bulk sync must keep showing the bulk sync, not
  the row that was just clicked.
- **Every path releases the button.** An optimistic claim that outlives its
  request is worse than no feedback at all: a button stuck mid-spin. It is
  released on the accepted path (by `stock_sync_started`), on the rejected path
  (`started: false`), on the thrown path, and again on every terminal
  `stock_sync_*` event as a backstop for a claim whose confirming event was
  missed across an SSE reconnect.
- **The thing that is running is the thing that stands out.** The running row's
  button inverts to the lit nav pill (`navButtonClass(true)`) instead of being
  dimmed by the disabled styling that the rows merely waiting on it carry.
- **The bulk button and a row never both claim a run.** A run with no
  `crawler_id` is the bulk one, so the bulk button spins and the rows are only
  disabled; a single-store run inverts that. Labelling the bulk button
  "Refreshing…" during a single store's refresh would point at the wrong work.
- **Prices report their enqueued count rather than waiting for an event.**
  `POST /crawl/start`'s reply is the only confirmation available at click time,
  and it says more than a "started" would: how many records were queued, or
  that there was nothing to queue.
- **The queued count yields to live progress.** Only one status bar renders at
  a time and the sync bar wins, so a "Queued 412 records" notice left standing
  would hide the crawl banner's live progress behind a Dismiss button. The
  notice is a confirmation, not a running job: while a crawl is running and the
  sync message is still that notice, the crawl banner takes the bar. Deciding
  it at render time by comparing the message *text* — rather than clearing the
  message when `started` arrives — makes the two events' order irrelevant, and
  lets anything that has since replaced the notice (a collection sync, a stock
  sync, a save failure) take the banner's normal precedence back. A
  nothing-to-queue notice is never superseded, because no crawl is coming.

## Frontend design

`frontend/src/App.tsx`:

- New state `stockSyncStarting: number | 'all' | null`, set on click and
  cleared on every release path above. `stockSyncTarget` cannot do this job
  itself — it is the *server's* answer, and its absence is what the gap is
  made of.
- New state `priceRefreshStarting: boolean`, covering the `POST /crawl/start`
  request itself.
- `handleRefreshStock` and `handleRefreshStoreCrawler` collapse into one
  `startStockSync(crawlerId?)`: the feedback is identical either way, and the
  only difference is whether the status message can name a store
  (`crawlers.find(...)?.site_name`) or has to say "in-stock catalog".
- `startCrawl` now awaits `postCrawlStart` and reports the outcome in the
  status bar. This also removes the `alert()` it used to raise on failure —
  the one error path in the app that blocked the page — so the checkpoint
  modal's Resume/Restart buttons get the same feedback as Settings' Refresh.
- The status bar's spinner (and the absence of its Dismiss button, which next
  to "Starting…" would read as finished) is driven by
  `syncing || stockSyncStarting !== null || priceRefreshStarting` rather than
  `syncing` alone.
- New state `priceQueueNotice: string | null` holds the queued-count message's
  text so `syncBannerVisible` can recognise it and stand down while `crawling`.
- `Settings` receives the merged target — `stockSyncTarget ?? stockSyncStarting`
  — through the existing `stockSyncBusy`/`stockSyncCrawlerId` props, so both
  keep their current meanings and only widen to cover the pre-confirmation
  window. `priceRefreshBusy` is a new prop.

Status messages, all locally generated (no event id, so never suppressed by the
dismissed-event-id replay guard):

| Click | Message |
| --- | --- |
| A store's Refresh | `Starting {site} catalog refresh…` |
| Bulk Store Refresh | `Starting in-stock catalog refresh…` |
| Marketplace Refresh, in flight | `Queueing records with no price yet…` / `Queueing every record for a price refresh…` |
| Marketplace Refresh, done | `Queued {n} record(s) for a price refresh.` |
| Marketplace Refresh, nothing queued | `Nothing to refresh — every record already has a price.` |

`frontend/src/views/Settings.tsx`:

- A local `Spinner` — the same ring the status bar spins, sized per call site.
  A button that swaps its glyph for a spinner has to keep the glyph's box, or
  the row twitches on every state change.
- The per-row button, when `stockSyncCrawlerId` names its crawler: spinner
  instead of `↻`, `navButtonClass(true)` instead of the dimmed idle styling,
  and `title` changing from `Refresh {site} catalog now` to
  `Refreshing {site} catalog…` (which is also the button's accessible name, as
  the spinner is `aria-hidden`). The `⟳` glyph is gone.
- The row of the crawler being refreshed gets `bg-gray-800/60`.
- The bulk Store Refresh shows the spinner and reads "Refreshing…" while the
  *bulk* run holds the lock, and is undimmed then; during a single store's run
  it stays disabled and reads "Refresh".
- The Marketplace Refresh shows the spinner and reads "Queueing…" while its
  request is in flight. It is not disabled for the crawl's duration — the
  queue is shared and re-enqueueing mid-crawl is legitimate — only for the
  request.

## Testing

- `frontend/src/test/settings.test.tsx` — the running row spins, is undimmed,
  and re-titles; its row is highlighted and others aren't; the bulk button
  spins for a bulk run and doesn't for a single-store run; the Marketplace
  button spins and disables while `priceRefreshBusy`.
- `frontend/src/test/inStockTab.test.tsx` — the clicked store's button is
  claimed *before* any `stock_sync_started` is emitted; the bulk click names
  the bulk run and no row claims it; the button is released on a rejected start
  and on a thrown one; the claim survives the hand-over to `stock_sync_started`
  and is released by `stock_sync_complete`.
- `frontend/src/test/crawlStatusBar.test.tsx` — the queued count is reported,
  singular and plural; a zero-queue run says so; the in-flight state spins and
  says what it is doing; a failed start lands in the status bar and raises no
  `alert`; the notice yields to the crawl banner with the `started` event
  arriving both after and before the POST's reply, and a nothing-to-queue
  notice stays up.

Verified visually as well as in tests: the Settings view was rendered in each
state in a browser and screenshotted, since "more obvious" is not something a
class-name assertion can confirm on its own.

## Runtime/agent document impact

None. No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exists in this repo. No new external trigger, input, or output shape — this
renders values already being sent. No stack, golden-command, or CI/CD change.
