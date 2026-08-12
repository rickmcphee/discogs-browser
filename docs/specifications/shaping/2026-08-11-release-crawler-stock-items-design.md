# Release-crawler matches feed Store/Track

Date: 2026-08-11
Branch: `claude/collection-wishlist-crawler-queue-f39ab5`

## Problem

A prior change on this branch made wishlist items auto-enqueue to release
crawlers (Amazon, eBay/CCmusic, Discogs Marketplace) the same as collection
items already did. The expectation was that a match would then show up in
the Track tab. It doesn't, for either wishlist or collection items, and
never has.

Release crawlers write to `listings` (`crawl_manager.py:308`, `is_release`
branch of `_drain_one_batch`), one row per `(release_id, crawler_id)`. The
Store tab (`StockBrowser` scope `store`) and Track tab (`scope="track"`) are
both driven entirely by `db.get_stock_items`, which reads only from
`stock_items`. Nothing ever selects `listings` rows keyed by `release_id` —
the one place that reads `listings` at all (`get_crawl_status_for_user`)
uses it purely for missing/complete bookkeeping, not display.

`stock_items` is populated exclusively by `replace_stock_items`, called only
from the catalog-crawler path (`_sync_stock`, `crawler_type="catalog"`).
Release crawlers and catalog crawlers are structurally separate pipelines
that happen to share a `crawlers` table; only one of the two feeds anything
the UI displays.

## Scope

Touches:

- `backend/db.py` — `GLOBAL_SCHEMA` gains `stock_items.release_id` (nullable
  FK to `catalog`) plus a unique index on `(crawler_id, release_id)`; new
  `upsert_stock_item_from_release()` and `delete_stock_item_for_release()`
  helpers alongside the existing `upsert_listing`.
- `backend/crawl_manager.py` — `_drain_one_batch`'s `is_release` branch calls
  the new helpers after (not instead of) `upsert_listing`.
- `frontend/src/api/types.ts` — `CrawlEvent` gains `type?: 'listing_changed'`
  and `item_key?: string`, matching the wire shape `_broadcast_listing_changed`/
  `_broadcast_stock_listing_changed` already send but the type doesn't declare.
- `frontend/src/App.tsx` — SSE handler bumps `stockSyncGeneration` on any
  `type === 'listing_changed'` event, not just `stock_sync_progress`/
  `stock_sync_complete`. Covers both the release-path event (`discogs_id`,
  what this spec adds a `stock_items` write to) and the pre-existing
  item_key-path event (a new price comparison attached to an existing
  anchor row also changes what Store/Track render) — matching the existing
  live-repaint behavior for catalog crawls (#118). `event.status` values
  `'found'`/`'not_found'` are already used by an unrelated, and apparently
  otherwise-dead, older per-release progress banner keyed on `event.release`
  (`crawlStatusBar.test.tsx`) — the new handler discriminates on `type`, not
  `status`, so it can't collide with that path.
- Tests: `backend/tests/test_crawl_manager.py` (`_drain_one_batch` release
  branch), `backend/tests/test_stock_crud.py` (new helpers),
  `frontend/src/test/inStockTab.test.tsx` for the `listing_changed` →
  `stockSyncGeneration` wiring (same file already covers `stock_sync_*` →
  `stockSyncGeneration`).

Out of scope:

- **Backfill.** Existing `listings` rows with `release_id` set and a price
  predate this change and are left alone. The scheduled sweep and manual
  crawls will re-crawl and populate `stock_items` naturally going forward —
  `get_missing_releases` (fixed earlier on this branch) already includes
  wishlist items as candidates.
- **`get_stock_items` / Store / Track query logic.** Once `stock_items` rows
  exist, pagination, sorting, tile view, `_library_match_fragment`
  intersection, and recommended filtering all already work — none of that
  code changes.
- **Stock-item price-comparison path** (`enqueue_crawl_queue_for_stock_item`,
  `upsert_stock_item_listing`, the `item_key`-keyed half of `listings`).
  Unrelated: that path compares an already-in-`stock_items` item's price
  across sites, not the release-crawler-finds-a-release case this spec
  covers.
- **Deduplication against catalog-crawler rows for the same release.** A
  catalog crawler's storefront scrape and a release crawler's per-release
  search are different sources; both surfacing as separate rows for the same
  record is correct, same as any two catalog crawlers finding the same title
  today.

## Decisions carried from brainstorming

- **Feed `stock_items` directly, not a Track-tab-only synthetic row.** An
  earlier direction considered unioning `listings` rows into
  `get_stock_items`'s result set at query time, scoped to the requesting
  user's library. Rejected: it would need a second, parallel code path for
  sorting/pagination/tile-view/recommended, and would leave the Store tab
  (unfiltered, cross-user) with no equivalent — release crawlers only ever
  run against one user's library items, so nothing would populate Store for
  a release nobody's library-scoped crawl had touched, even after another
  crawler proved it exists. Writing into `stock_items` is the same data one
  layer earlier: every existing consumer (Store, Track, recommended,
  artist facets) inherits it for free.
- **Store tab shows these globally, same as catalog-crawler rows.** A
  release's price on Amazon is public information about the release, not
  about the user whose wishlist triggered the crawl — no different in kind
  from a catalog crawler discovering the same release in a store's
  inventory. No per-user scoping is added on the write side.
- **Delete on a clean "not found," not on a crawl exception.** The crawler
  plugin interface's contract (`CLAUDE.md`) already distinguishes these:
  `[]` means the site answered and has nothing, while any failure must
  raise. `_drain_one_batch`'s `except` block already short-circuits before
  reaching match processing, so "matches == [] and no exception was raised"
  is already the exact signal available at the point the new delete call is
  added — no new signal needs to be threaded through.
- **`upsert_listing` is unchanged, not replaced.** It still backs
  `get_missing_releases`/`get_crawl_status_for_user`'s completeness
  accounting, which has nothing to do with what the Store/Track UI shows.
  The two writes serve different readers of the same crawl result.
- **No backfill script.** The 2026-08-08 wishlist-enqueue fix earlier on this
  branch means the scheduled sweep already treats wishlist items as
  candidates, so existing gaps close on their own within one sweep cycle
  without one-off migration code.

## Data model

```sql
ALTER TABLE stock_items ADD COLUMN IF NOT EXISTS release_id TEXT REFERENCES catalog(discogs_id);
CREATE UNIQUE INDEX IF NOT EXISTS stock_items_crawler_release_idx ON stock_items (crawler_id, release_id);
```

Postgres unique indexes treat `NULL` as distinct from every other value, so
catalog-crawler rows (`release_id IS NULL`, arbitrarily many per crawler)
are unaffected by the new index; only release-crawler rows (`release_id`
set) are constrained to one row per `(crawler_id, release_id)`.

## Write path

`_drain_one_batch`'s `is_release` branch (`crawl_manager.py:291-318`),
after the existing `upsert_listing(...)` call:

- **Match found** (`matches` non-empty): `upsert_stock_item_from_release`
  inserts or updates a `stock_items` row keyed by `(crawler_id, release_id)`
  — `artist`/`title`/`format`/`cover_image_url` from `target` (the catalog
  row already fetched earlier in the loop), `price`/`currency`/`url` from
  `best` (`matches[0]`), `item_key = compute_item_key(artist, title, url)`.
  It also upserts the matching `stock_item_identities` row (same
  `ON CONFLICT (item_key) DO UPDATE` shape `replace_stock_items` already
  uses), keeping the "every `stock_items` row has a durable identity row"
  invariant that `get_recommended_stock_items`/judgments' `LEFT JOIN`
  depends on — release-crawler rows would otherwise silently fall back to
  `NULL` artist/title/format there instead of erroring, a real but easy-to-
  miss gap rather than a hard failure.
- **Clean "not found"** (`matches == []`): `delete_stock_item_for_release`
  removes any existing `stock_items` row for that `(crawler_id, release_id)`
  — the release is no longer in stock there. The `stock_item_identities` row
  is left in place, same as the existing dead-stock-item convention
  (identity rows are durable; only the live `stock_items` row goes).
- **Crawl exception**: neither call runs (the existing `except` block
  already `continue`s first) — an untrustworthy failure leaves any prior
  `stock_items` row as-is.

## Live repaint

`listing_changed` SSE events already fire on every release-crawl result
(`_broadcast_listing_changed`, `crawl_manager.py:328`) and every stock-item
price-comparison result (`_broadcast_stock_listing_changed`,
`crawl_manager.py:334`), but the frontend currently drops both — the
per-release UI that used to consume the first was removed by the 2026-08-08
tab rename, and nothing has ever consumed the second. `App.tsx`'s SSE
handler gains a case matching `event.type === 'listing_changed'` (either
shape) that bumps `stockSyncGeneration`, the same counter
`stock_sync_progress`/`stock_sync_complete` already bump, so `StockBrowser`
(both `store` and `track` scopes) refetches live — as a release crawler
finds a match (this spec's new `stock_items` write) or as a price
comparison arrives for an item already shown (an existing, previously-inert
signal) — consistent with catalog-crawl live repaint (#118).

## Testing

- `_drain_one_batch`: a release-type crawl that finds a match creates a
  `stock_items` row with the expected fields; a second run with no match
  deletes it; a run that raises leaves an existing row untouched; the
  existing `listings`-row assertions keep passing unchanged.
- `upsert_stock_item_from_release`/`delete_stock_item_for_release`: direct
  unit coverage in `test_stock_crud.py`, including the upsert-on-conflict
  case (same release/crawler crawled twice with a changed price/url).
- Frontend: a `listing_changed` SSE event triggers a `getStock` refetch,
  proven the same way `inStockTab.test.tsx` already proves it for
  `stock_sync_progress`.
