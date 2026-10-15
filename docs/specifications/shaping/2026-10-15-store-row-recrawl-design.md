# Re-crawl one Store row

Date: 2026-10-15
Branch: `worktree-store-row-recrawl`

## Problem

A Store row's marketplace comparison listings (Amazon, eBay, and the other
release crawlers) are only refreshed by the queue draining past the item, and
nothing a user can do on the row hurries that along except saving it. Saving is
the wrong lever for "I think this price or title is stale": it changes what
Saved holds, it can only be undone by unsaving, and it is invisible as a
refresh.

There used to be a per-row refresh. The Collection and Wantlist tables had one
beside each release's per-crawler listing columns, and it went away with them
([main design spec](../../superpowers/specs/2026-06-27-discogs-browser-design.md),
2026-08-27 amendment) because marketplace prices stopped being that table's
subject. That was the right call for those tables. It left the Store tab, which
*is* the marketplace-price surface, with no row-level equivalent, and left the
backend's one-release re-crawl (`POST /crawl/start` with `release_id`)
unreachable from any UI.

Two things make the gap bite now:

- Comparison rows can carry stale or absent `listing_title` — rows last crawled
  before the marketplace listing-title feature have none, so they borrow the
  parent row's title and look like a confirmed match. A re-crawl of one row is
  the cheapest way to see whether the real title appears.
- Cheapest and the comparison rows are only as good as the last crawl of that
  item. A user looking at one record who wants a fresh answer has no way to ask
  for it.

## Scope

A `↻` button on each Store row that puts that row's marketplace crawl at the
front of the queue.

It re-prices the item against every eligible marketplace crawler. It does
**not** re-scan the store the row came from — that is the catalog sync, a
shared global job, and one row's click has no business triggering it. The
button's `title` says "Re-check marketplace prices", not "Refresh".

Touches:

- `backend/routers/stock.py` — `POST /stock/{item_key}/recrawl`.
- `backend/db.py` — a thin wrapper that applies the library-only gate under
  the reconciliation lock before calling the existing expedite helper (see
  Design); that helper's docstring stops describing itself as save-only.
- `frontend/src/api/client.ts` — `recrawlStockItem`.
- `frontend/src/views/StockBrowser.tsx` — the button on each layout the row
  renders in, per-`item_key` pending state, and the bounded fallback refetch.
- `frontend/src/App.tsx` — passes `StockBrowser` a status callback.
- Tests: `backend/tests/test_stock_router.py`,
  `frontend/src/test/` for the Store view.

Deliberately untouched: the catalog sync and its per-store Settings refresh;
the release-target path (`/crawl/start`); the Queue tab, which already shows an
expedited row; the SSE contract, which already carries `listing_changed` for a
stock `item_key`.

## Design

### Reuse the save's expedite, don't write another one

`enqueue_crawl_queue_for_saved_stock_item` is already "insert-or-expedite": no
row → insert at `QUEUE_PRIORITY_INTERACTIVE`, `done` → revive, `pending` →
raise priority and widen to every eligible crawler, `in_progress` → raise
priority only. A repeated call on an already-expedited row is a true no-op.
Those are exactly the semantics a re-crawl click needs, including the idempotence
that makes a double-click harmless, and the enabled-store gate and the
reconciliation lock come with it. See
[`2026-09-19-save-jumps-the-marketplace-queue-design.md`](2026-09-19-save-jumps-the-marketplace-queue-design.md),
which this inherits wholesale — in particular its stated cross-tenant property
(one user's expedited row sorts ahead of everyone's routine work, with no
quota) and its known limitation (a claim already in flight on a narrowed set
consumes the request). Neither is made worse by a second caller, because the
lane is still bounded by clicks.

The helper keeps its name and gains a second caller. Renaming it buys nothing
the docstring cannot say, and the name is referenced from the specs above.

### The endpoint

`POST /stock/{item_key}/recrawl`, any authenticated user — the same audience as
Save, which is the same lane. It does not touch `stock_item_saves`. It takes
the same single retry on `DeadlockDetected`/`LockNotAvailable` as the save, for
the same rolling-deploy reason, so the retry is factored out rather than
copied.

Response: `{"queued": bool, "reason": str|null}`. `queued` is true when the call
left the item queued because of it. When false, `reason` says why, so the UI
can be accurate rather than generic:

- `"not_listed"` — no enabled catalog store lists the item. This also covers an
  unknown `item_key`, and rows that came from a release crawler:
  `_enabled_stock_source_exists` counts catalog-type crawlers only, so those
  never queue. (This settles the plan's first open question about them.)
- `"library_only"` — `crawl_library_only` is on and no user has saved the item
  or holds a matching collection/wantlist record.

#### The library-only gate is checked, not inherited

The save helper reaches `claim_crawl_queue_batch` safely only because Save
writes `stock_item_saves` first: that is the interest `_stock_item_crawlable`
looks for when `crawl_library_only` is on. A re-check writes no interest, so
reusing the helper alone would return `queued: true` for an item the claim then
skips regardless of priority, and which the dead-row sweep can delete. The
click would report success and do nothing.

Re-check is not given an exception honoured by both the claim and the sweep.
That would make a single click override a setting the admin chose, in two more
places, to price an item nobody in the library wants. It is rejected with
`reason: "library_only"` and the message says how to get it priced: save it.

The check and the enqueue share one transaction and the stock queue
reconciliation lock the sweep also takes, so the sweep cannot delete between
them. Mechanically: a small wrapper takes `_lock_stock_queue_reconciliation`,
evaluates `_stock_item_crawlable(item_key, library_only)`, and only then calls
the expedite helper. `library_only` is read from `config.crawl_library_only()`
before an app connection is borrowed and passed in, as every other call site
does; the wrapper takes it as a required argument, because the helper's own
default of `False` is the unrestricted gate and a caller that forgets it gets
that silently.

### UI

One `↻` per row in each layout that renders a Save bookmark (the same three
places), beside it. Styling and states follow
[`2026-08-26-refresh-click-feedback-design.md`](2026-08-26-refresh-click-feedback-design.md):
the glyph spins while busy and the button inverts, rather than dimming.

State is per `item_key`, mirroring `pendingSaves`: a click while one is in
flight for that key is a no-op, so there is never more than one outstanding
request per row.

"Busy" is the request only — until the server says queued. Showing busy until
the crawl *completes* would need the page to correlate `listing_changed` events
to a click, across multiple crawlers per item, and a crawl can sit behind
cooldowns for far longer than any spinner should run.

The result normally arrives the way every crawl result already does:
`listing_changed` bumps the Store's refetch and the row repaints. That event is
broadcast in the worker's own process only, so on a deployment with more than
one backend instance a crawl claimed by a different instance than the one
holding this browser's stream never reaches it. That is true of every crawl
result today; it matters more here because the click promises a repaint. So a
queued re-check also schedules a **bounded fallback refetch** of the Store list
at a few fixed delays after the click (the plan sets them), through the same
guarded refetch path other triggers use. It is cheap, idempotent and
independent of which instance crawled; it stops when the delays run out and
does not track the crawl's real completion, which a row sitting behind a
circuit-breaker cooldown may not reach in that time. The status line says
"queued", not "done", for that reason. The single-instance NAS deployment
never needs it; the multi-instance one the repo keeps the option of does.

The button's confirmation is a status line written through the shared status
bar, which `App.tsx` owns: `StockBrowser` gets an `onStatus` prop the way other
views get their callbacks, wired to `setSyncStatus`. Messages: queued —
"Re-checking prices for *Title*…"; `not_listed` — "Not re-checked — no enabled
store lists this item."; `library_only` — "Not re-checked — library-only
crawling is on and nobody has saved or owns this item. Save it to have it
priced."

### Why this does not reverse the Collection/Wantlist decision

The 2026-08-27 amendment removed the button because the table it sat in no
longer displayed marketplace data, so the button refreshed something invisible.
This one sits on rows that display exactly the listings it refreshes.

## Open questions

- **Admin-only?** Defaulted to all users, matching Save. The per-store Settings
  refresh is admin-only because it triggers a shared job; this triggers one
  queue row. If the queue's lack of fairness becomes a concern, a per-user
  rate limit is the first fairness mechanism this queue would get, and it
  belongs in its own design.
- **Rows that came from a release crawler** never queue (see `not_listed`
  above), so the button shows its message on them rather than being hidden.
  Settled: the Store row payload carries no field saying where a row came
  from. `get_stock_items` returns `source` (the crawler's `site_name`) and
  `saved`, and `StockItem` in `frontend/src/api/types.ts` mirrors them, with
  no crawler id or type, so hiding the button client-side would need a new
  payload field. Not added: the click is rare, the server's `not_listed`
  answer is accurate, and the message is the cheaper cost.

## Spec drift

The main design spec's 2026-08-27 amendment states the per-row refresh button
is gone. It remains true for the Collection and Wantlist tables; a short note
there points to this design for the Store tab.
