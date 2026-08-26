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
- **The Marketplace Management Refresh.** `POST /crawl/start` only *enqueues*,
  and the worker pool broadcasts no lifecycle event when it later picks the
  work up — `started`/`complete`/`stopped` went with the crawl-queue refactor
  (see the CrawlManager amendment in
  [`2026-06-27-discogs-browser-design.md`](../../superpowers/specs/2026-06-27-discogs-browser-design.md)),
  leaving only per-write `listing_changed`. So nothing at all reached the
  screen between the click and the first row changing price, and on a run that
  enqueued nothing, nothing ever would.

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
- **Every path releases the button, including the one with no path.** An
  optimistic claim that outlives its request is worse than no feedback at all:
  a button stuck mid-spin. It is released on the accepted path (by
  `stock_sync_started`), on the rejected path (`started: false`), on the thrown
  path, and on every terminal `stock_sync_*` event as a backstop for a missed
  `stock_sync_started`. None of those covers a stream that misses the *whole*
  sync, which is reachable: `_events_to_replay` (`routers/crawl.py`) returns
  nothing once no job is active, so a sync that both starts and finishes while
  the SSE stream is reconnecting replays neither event, and the claim would
  hold every Store Refresh disabled until a page reload. A timeout
  (`START_CLAIM_TIMEOUT_MS`) bounds it. The price claim needs the same bound
  from the other end: `apiFetch` wraps a plain `fetch` with no timeout or abort
  signal, so a stalled POST never settles and would leave the Marketplace
  button disabled with no way to retry. Both expiries bump their sequence
  first, so a response that lands afterwards is dropped rather than acted on. Releasing early is self-correcting
  in both directions: a late `stock_sync_started` takes the button straight
  back, and a click during a sync the UI has lost track of is rejected by the
  server with the "already running" message rather than starting anything
  twice. Expiring replaces the "Starting…" message as well as releasing the
  button — leaving it would put a Dismiss button beside "Starting…", the exact
  reading-as-finished this design rules out one bullet down — and the
  replacement says the app has lost track and points at the Logs tab. Not at a
  reload: `stock_sync_running` is this process's `_stock_task`, so on a
  multi-Machine deployment a reconnect landing on the Machine that does not
  hold the advisory lock reports idle for a sync that is genuinely running,
  and `_events_to_replay` hands back nothing. The log store is merged across
  Machines and durable
  ([`2026-08-17-unified-log-store-design.md`](2026-08-17-unified-log-store-design.md)),
  so it is the one signal that always answers. Guarded on the message still
  being the one the claim set, so a real progress message that arrived
  meanwhile is never clobbered.
- **The thing that is running is the thing that stands out.** The running row's
  button inverts to the lit nav pill (`navButtonClass(true)`) instead of being
  dimmed by the disabled styling that the rows merely waiting on it carry.
- **The bulk button and a row never both claim a run.** A run with no
  `crawler_id` is the bulk one, so the bulk button spins and the rows are only
  disabled; a single-store run inverts that. Labelling the bulk button
  "Refreshing…" during a single store's refresh would point at the wrong work.
- **Prices report their own request's count, since no event is coming.**
  `POST /crawl/start`'s reply is the only confirmation that exists at click
  time.
- **That count is records *requested*, not queued.** `routers/crawl.py` counts
  targets, while `db.enqueue_crawl_queue` no-ops on a row that is already
  `pending` or `in_progress` — so a re-click can be told "412" having inserted
  nothing. "Requested" is true in both cases, and is the more useful of the two
  anyway: an affected-row count would report "0" for a re-click mid-crawl whose
  records are all queued and about to be crawled, which reads as a dead button.
  Reported by wording rather than by changing the endpoint for that reason, and
  because `{"enqueued": N}` is already documented as a target count.
- **Nothing guards the notice against the crawl status bar.** An earlier draft
  had the notice stand down while `crawling`, so it could not sit on top of
  live progress. It cannot: `crawling` is set only by the `started` event,
  which no longer exists, so the crawl status bar in `App.tsx` has been
  unreachable in production since the crawl-queue refactor and
  `crawlStatusBar.test.tsx` keeps it green by synthesizing events the backend
  does not send. The guard was removed rather than kept as insurance — dead
  code with tests asserting unsupported behaviour. Deleting the status bar
  itself is a separate cleanup, out of scope here.

- **Busy-ness belongs to the message, not to the app.** The banner's spinner
  (and the absence of its Dismiss button) is derived from whether the message
  currently on screen is one the user is still waiting on — compared by text —
  rather than from "is anything pending anywhere". The two drift: a stock sync
  completing while a price request was still in flight left its *completion*
  message spinning with no Dismiss. Comparing text needs nothing kept in sync
  by hand, since a message that has been replaced simply stops matching.
  `syncing` is the same shape one level up and has the same drift — a
  locally-generated message shown mid-sync still spins — but it predates this
  and fixing it means giving the shared status bar a full ownership model,
  which is a bigger change than this one.
- **Neither start request may answer for a click that is no longer current.**
  Both use the sequence-guard idiom already in this file (`latestPriceStatusSeq`
  and friends): a response whose sequence has been superseded returns without
  touching state. Not theoretical here — the claim timeout can re-enable the
  button while the first request is still in flight, and `POST
  /stock/sync/start` has no bounded response time, so a first request rejecting
  after a second click would otherwise clear the newer claim and overwrite its
  status with the older result.
- **Two guards, because a claim and the status bar are owned separately.** The
  per-operation counters decide whether a response may still touch *its own*
  claim; a third, shared `latestStatusOwnerSeq` decides whether it may touch
  the status bar, which both handlers write and which Settings lets them
  contend for — a stock start and a price refresh can be in flight at once, and
  without it an older stock rejection lands on top of a newer price refresh's
  "Starting…" and clears its busy state mid-request. Cleanup of a claim's own
  state stays on the per-operation guard: a request that lost the banner must
  still release the button it took.
- **The banner is a live region, and it stays mounted.** It carries
  `role="status"`, so the click confirmations this design exists to add reach
  screen-reader users too; none of them moves focus, so without it they were
  silent. The region is an always-present wrapper with the banner rendered
  inside it, not the banner itself: assistive technology does not reliably
  announce a `role="status"` element inserted together with its text, which
  would have cost exactly the first confirmation after a click. Spinners are
  `aria-hidden` and the buttons carry their state in `title`, which is their
  accessible name. `BackendDownScreen` is a second `role="status"`, so tests
  reaching for the overlay query it by its message rather than by role.

## Frontend design

`frontend/src/App.tsx`:

- New state `stockSyncStarting: number | 'all' | null`, set on click and
  cleared on every release path above, with an effect arming a
  `START_CLAIM_TIMEOUT_MS` timer for as long as it is non-null.
  `stockSyncTarget` cannot do this job itself — it is the *server's* answer,
  and its absence is what the gap is made of.
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
  to "Starting…" would read as finished) is driven by `syncing` *or* the
  displayed message still being `busyStatusMessage` — see the message-ownership
  decision above. Not by a flag per in-flight request, which is what let an
  unrelated request leave a finished message spinning.
- The banner element carries `role="status"`.
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
| Either stock claim expired unconfirmed | `Lost track of the {what} — check the Logs tab to see whether it is still running.` |
| Price claim expired unconfirmed | `Lost track of the price refresh — check the Logs tab to see whether it started.` |
| Marketplace Refresh, in flight | `Starting price refresh for records with no price yet…` / `…for every record…` |
| Marketplace Refresh, done | `Price refresh requested for {n} record(s).` |
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
- The Marketplace Refresh shows the spinner and reads "Starting…" while its
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
  and is released by `stock_sync_complete`; a claim that never receives any
  event at all is released by the timeout and swaps its "Starting…" message for
  the lost-track one, is not released before the timeout, and does not overwrite
  a real progress message that arrived while it was held; a start response that
  arrives after a newer click is ignored rather than clobbering the newer
  claim; the banner is reachable by its `status` role.
- `frontend/src/test/crawlStatusBar.test.tsx` — the requested count is
  reported, singular and plural; a zero-target run says so and stays
  dismissible; the in-flight state spins and says what it is doing; a failed
  start lands in the status bar and raises no `alert`; a message that has
  finished is not left spinning by an unrelated request still in flight; a
  request that never settles releases the button by the timeout; an older stock
  rejection does not overwrite a newer price refresh's notice, while still
  releasing its own button; and the live region is the same mounted node before
  and after its first message.

Verified visually as well as in tests: the Settings view was rendered in each
state in a browser and screenshotted, since "more obvious" is not something a
class-name assertion can confirm on its own.

## Runtime/agent document impact

None. No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exists in this repo. No new external trigger, input, or output shape — this
renders values already being sent. No stack, golden-command, or CI/CD change.
