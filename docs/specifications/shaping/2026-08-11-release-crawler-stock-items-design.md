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

**Added during PR review, beyond the original scope above:**

- `backend/crawl_manager.py`/`backend/db.py` — the not-found branch also
  gates on `not bot_detected` and calls a new `clear_listing_price`, and
  `upsert_stock_item_from_release` stopped normalizing already-curated
  catalog artist/title (see "Decisions carried" and "Write path" below).
- `backend/db.py` — `get_missing_releases`/`get_crawl_status_for_user`
  scope their enabled-crawler count to `crawler_type = 'release'`; an
  enabled catalog crawler was inflating the denominator and could make a
  fully-priced release look permanently incomplete.
- `backend/routers/crawl.py` — `crawl_stream`/`_events_to_replay` stopped
  filtering `listing_changed` events by library ownership (deleted
  `_event_touches_user`); see "Live repaint" below.
- Tests: `backend/tests/test_library_maintenance.py`, `backend/tests/test_crawl_router.py`.

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
- **Delete on a clean "not found," not on a crawl exception or a bot-detected
  empty retry.** The crawler plugin interface's contract (`CLAUDE.md`)
  already distinguishes exception from `[]`: `[]` means the site answered
  and has nothing, while any failure must raise. `_drain_one_batch`'s
  `except` block already short-circuits before reaching match processing.
  A third case surfaced during review: `_paced_search` can also return
  `matches == []` with `bot_detected = True` — a post-interstitial retry
  that came back empty is not a trustworthy "not in stock" answer either
  (the same signal the circuit breaker already treats as untrustworthy), so
  the delete (and the `clear_listing_price` call below) is additionally
  gated on `not bot_detected`.
- **Clearing `listings.price`, not just deleting the `stock_items` row, on a
  clean not-found.** `upsert_listing` writes the release_id-keyed `listings`
  row `get_missing_releases`/`get_crawl_status_for_user` use for
  completeness accounting; deleting only `stock_items` left that row's
  stale non-NULL price behind, so a release that matched once and later
  went not-found looked permanently "complete" and never got re-enqueued by
  the scheduled sweep. `clear_listing_price` nulls the price in place
  (`listings.url` is `NOT NULL`, so there's no url to write a fresh row
  with on a not-found) without touching `url`, making the release missing
  again.
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
CREATE UNIQUE INDEX IF NOT EXISTS stock_items_crawler_release_idx ON stock_items (crawler_id, release_id) WHERE release_id IS NOT NULL;
```

The index is partial (`WHERE release_id IS NOT NULL`) so it only ever
constrains release-crawler rows — catalog-crawler rows (`release_id IS
NULL`, arbitrarily many per crawler) never enter it at all, rather than
relying on Postgres's NULL-is-distinct behavior to make an unconstrained
full index a no-op for them. `upsert_stock_item_from_release`'s `ON
CONFLICT (crawler_id, release_id) WHERE release_id IS NOT NULL DO UPDATE`
repeats the same predicate, which Postgres requires to infer a partial
index as the conflict target.

## Write path

`_drain_one_batch`'s `is_release` branch, after the existing
`upsert_listing(...)` call:

- **Match found** (`matches` non-empty): `upsert_stock_item_from_release`
  inserts or updates a `stock_items` row keyed by `(crawler_id, release_id)`
  — `artist`/`title`/`format`/`cover_image_url` stored as-is from `target`
  (the catalog row already fetched earlier in the loop; unlike
  `replace_stock_items`, no `normalize_artist_casing`/`normalize_title_casing`
  pass, since catalog data is already curated Discogs metadata, not scraped
  text), `price`/`currency`/`url` from `best` (`matches[0]`). `item_key`
  hashes `catalog_release["artist"].title()` and the raw (non-normalized)
  title — matching `replace_stock_items`'s legacy convention exactly,
  regardless of what gets stored for display, so an item found by both a
  release crawler and a catalog crawler resolves to the same `item_key`
  rather than two different hashes for one identity. It also upserts the
  matching `stock_item_identities` row (same `ON CONFLICT (item_key) DO
  UPDATE` shape `replace_stock_items` already uses), keeping the "every
  `stock_items` row has a durable identity row" invariant that
  `get_recommended_stock_items`/judgments' `LEFT JOIN` depends on.
- **Clean "not found"** (`matches == []` and `bot_detected` is `False`):
  `delete_stock_item_for_release` removes any existing `stock_items` row
  for that `(crawler_id, release_id)`, and `clear_listing_price` nulls the
  `listings` row's price in place (leaving `url`, which is `NOT NULL`,
  untouched) so `get_missing_releases` treats the release as missing again
  instead of permanently complete. The `stock_item_identities` row is left
  in place, same as the existing dead-stock-item convention.
- **Untrustworthy result** — a crawl exception, or `bot_detected = True`
  (a post-interstitial retry that came back empty): neither call runs, so
  any prior `stock_items`/`listings` state is left exactly as-is. The
  exception case is the existing `except` block, which already `continue`s
  before reaching match processing; the `bot_detected` case is a `matches
  == []` result that reaches this branch but is excluded by an explicit
  `and not bot_detected` guard, since an empty post-retry result is the
  same untrustworthy signal the circuit breaker already treats specially
  one line above.

## Live repaint

`listing_changed` SSE events already fire on every release-crawl result
(`_broadcast_listing_changed`) and every stock-item price-comparison result
(`_broadcast_stock_listing_changed`), but the frontend currently drops
both — the per-release UI that used to consume the first was removed by
the 2026-08-08 tab rename, and nothing has ever consumed the second.
`App.tsx`'s SSE handler gains a case matching `event.type ===
'listing_changed'` (either shape) that bumps `stockSyncGeneration`, the
same counter `stock_sync_progress`/`stock_sync_complete` already bump, so
`StockBrowser` (both `store` and `track` scopes) refetches live —
consistent with catalog-crawl live repaint (#118).

Since Store/Track are global rather than per-user, `backend/routers/crawl.py`
no longer filters `listing_changed` events by whether the release is in the
requesting user's own library before delivering them — a discogs_id-scoped
filter existed for an older, now-removed per-release progress UI, and left
in place it would have silently starved every user's Store/Track tab of
repaints for any release outside their own collection/wishlist. No
ownership filter now stands between a `listing_changed` event and any user
subscribed on the broadcasting process. **(2026-08-23:** the original
sentence — "both the live SSE stream and the reconnect replay buffer
deliver every `listing_changed` event to every connected user" —
overstated twice. (1) The replay half was never real: both broadcasters
`put_nowait` straight onto subscriber queues and never touch `_recent`
(`crawl_manager.py:533-557`), so such an event is delivered live or not at
all; a reconnecting client cannot replay one. (2) "Every connected user" is
not literally true in the deployed multi-Machine setup either —
`_subscribers` is an in-process list, so a stream landing on one Machine
never sees events another Machine's worker pool produced. That is an
accepted, documented gap, not a regression: see
[`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)'s
"Cross-Machine SSE fan-out". What this paragraph exists to establish — the
absence of ownership filtering — holds regardless.)**

## Testing

- `_drain_one_batch`: a release-type crawl that finds a match creates a
  `stock_items` row with the expected fields; a second run with no match
  deletes it and nulls the `listings` price; a run that raises, and a run
  whose retry comes back empty after bot detection, both leave any existing
  `stock_items`/`listings` state untouched; the existing `listings`-row
  assertions keep passing unchanged.
- `upsert_stock_item_from_release`/`delete_stock_item_for_release`/
  `clear_listing_price`: direct unit coverage in `test_stock_crud.py`,
  including the upsert-on-conflict case (same release/crawler crawled twice
  with a changed price/url) and the legacy `.title()` item_key convention.
- `get_missing_releases`/`get_crawl_status_for_user`: a release fully
  priced by every enabled *release* crawler is not "missing", even with an
  unrelated enabled *catalog* crawler also present (`test_library_maintenance.py`).
- `_events_to_replay`: a `listing_changed` event for a release outside the
  requesting user's own library is not filtered out on its way to that user
  (`test_crawl_router.py`). **(2026-08-23:** this bullet said such an event
  "is still included in their replay buffer", which overstates it — the test
  hand-seeds `_recent` and calls `_events_to_replay`, but production never
  puts a `listing_changed` event in `_recent` at all: both broadcasters
  `put_nowait` straight onto subscriber queues (`crawl_manager.py:533-557`),
  so these events are only ever delivered live. What the test really pins is
  the absence of ownership filtering, which is the property that mattered
  then and still holds — `_visible_to()` acts only on `user_id`-tagged
  events. See `2026-08-23-per-user-sse-event-filtering-design.md`.)**
- Frontend: a `listing_changed` SSE event triggers a `getStock` refetch,
  proven the same way `inStockTab.test.tsx` already proves it for
  `stock_sync_progress`.
