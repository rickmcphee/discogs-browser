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
- `backend/db.py` — no new queue logic (see Design); the expedite helper's
  docstring stops describing itself as save-only.
- `frontend/src/api/client.ts` — `recrawlStockItem`.
- `frontend/src/views/StockBrowser.tsx` — the button on each layout the row
  renders in, and per-`item_key` pending state.
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

Response: `{"queued": bool}`, true when the call left the item queued because of
it (the helper's rowcount, which the save path already treats the same way).
`false` means the enabled-store gate declined — no enabled store still lists the
item — and the UI says so rather than spinning forever.

An unknown `item_key` is the same `false`; the gate asks `stock_items`, so no
separate existence check exists to drift from it.

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
cooldowns for far longer than any spinner should run. The result arrives the
way every crawl result already does: `listing_changed` bumps the Store's
refetch and the row repaints. The button's confirmation is a status line
("Re-checking prices for *Title*…"), using the status bar the other refresh
buttons already write to; a `queued: false` writes "Nothing to re-check — no
enabled store lists this item."

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
- **Rows that came from a release crawler** (not a catalog store) have a
  `discogs_id` target, not an `item_key`. Their `item_key` rows exist in
  `stock_items`, and whether the item_key queue row for such an item prices
  anything is not established here; the plan's first task checks it before the
  button is shown on those rows.

## Spec drift

The main design spec's 2026-08-27 amendment states the per-row refresh button
is gone. It remains true for the Collection and Wantlist tables; a short note
there points to this design for the Store tab.
