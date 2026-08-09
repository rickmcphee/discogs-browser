# Discogs tab rename design

Date: 2026-08-08
Branch: `worktree-discogs-tab-rename`

## Problem

**Amendment (2026-08-10):** the rename this spec describes was reversed by
`2026-08-10-collection-wishlist-filter-design.md`. The intersection tab it
anticipated is now called **Track**, and this tab went back to
**Collection**; the wantlist tab is now **Wantlist**. The frontend scope
values changed with the labels (`'discogs'` → `'collection'`), though the
backend `scope="discogs"` value this spec introduced is unchanged.

This is the first slice of a v3.0 redesign that eventually splits the app's
tabs along two use cases: tracking the value of a Discogs collection, and
comparing prices for in-stock inventory across ecommerce sites (including a
future tab, reusing the name "Collection," that intersects the two). Later
slices touch crawl targets and the Store tab; this slice only does the part
that's fully self-contained: the current Collection tab
(`RecordBrowser.tsx`, scope `collection`) mixes pure Discogs metadata
(artist/title/year/label/format/`discogs_price`) with per-site price columns
(amazon/discogs-marketplace/eBay, driven by `crawler_type='release'`
crawlers) in one table. That mix no longer matches what this tab is for: it
becomes "Discogs" and shows only what Discogs itself reports about a
release, plus a "Date Added" column Discogs already provides but this app
has never stored. The Wishlist tab, which shares the same component and the
same price columns, loses its price columns too, for the same reason: a
wishlist item's cross-site pricing has no home until the future intersection
tab exists.

## Scope

Touches:

- `backend/db.py` — `TENANT_SCHEMA` gains two `library_items` columns;
  `upsert_library_item` gains two kwargs; `get_library_releases` drops the
  `price_*` sort branch and the per-row listings lookup, and its SELECT/sort
  allowlist gain `date_added`; `get_listings_for_release` is deleted (no
  remaining caller); `get_library_releases` and `get_distinct_artists` both
  rename their `scope == "collection"` check to `scope == "discogs"` (see
  "Internal naming" below — `get_distinct_artists` backs `/api/artists`,
  the artist sidebar, and carries the exact same check).
- `backend/discogs.py` — collection/wantlist item parsing captures
  `date_added`.
- `backend/crawl_manager.py` — `_sync_collection` passes
  `collection_date_added`/`wishlist_date_added` through to
  `upsert_library_item`; the wishlist loop stops calling
  `enqueue_crawl_queue`.
- `frontend/src/views/RecordBrowser.tsx` — drop price columns, the per-row
  refresh action, and the crawl-event listener effect; add the Date Added
  column; drop the now-unused `crawlers`/`hiddenCrawlerIds`/`crawling`/
  `crawlingReleaseId`/`crawlEvents`/`onRefreshPrices` props.
- `frontend/src/App.tsx` — rename the `collection` view/scope to `discogs`;
  drop the now-dead `crawlingReleaseId` state and the props RecordBrowser no
  longer takes.
- `frontend/src/api/types.ts` — `RecordScope`'s `'collection'` becomes
  `'discogs'`; `Release` drops `listings`; `Listing` is deleted (no
  remaining reference).
- `frontend/src/api/client.ts` — no signature changes; callers pass the new
  scope value.
- Tests: `backend/tests/test_catalog_crud.py`,
  `backend/tests/test_global_schema.py`, `backend/tests/test_crawl_manager.py`,
  `backend/tests/test_library_maintenance.py` (its one `get_distinct_artists`
  test uses the old `scope="collection"` literal),
  `frontend/src/test/recordBrowser.test.tsx`,
  `frontend/src/test/staleListingClear.test.tsx`,
  `frontend/src/test/wishlistRefresh.test.tsx`,
  `frontend/src/test/syncRefetch.test.tsx`,
  `frontend/src/test/viewRenderChurn.test.tsx`,
  `frontend/src/test/plexLink.test.tsx`,
  `frontend/src/test/crawlStatusBar.test.tsx`.

Out of scope (later specs, per the v3.0 brainstorm): the Store tab
reorganization (folding release-crawler results into it), the new
intersection Collection tab, and expanding release-crawler crawl targets to
cover store inventory. The `/crawl/start` `release_id` param, `startCrawl`,
and `postCrawlStart` are untouched — they're a generic single-release crawl
capability the frontend just stops calling from these two tabs, not
something specific to the removed UI.

## Decisions carried from brainstorming

- **Wishlist loses price columns too**, for consistency with the new
  Discogs tab, even though only the Collection→Discogs rename was
  originally requested. Wishlist price comparison has no home until the
  future intersection Collection tab exists.
- **Wishlist sync stops enqueueing release-crawler price crawls.** Nothing
  displays that data anymore, and the stated v3.0 union (discogs collection
  ∪ store items) doesn't include wishlist items. Collection sync keeps
  enqueueing them — that data still feeds the future Collection tab even
  though this tab won't display it.
- **`collection_date_added` and `wishlist_date_added` are separate
  columns**, not one shared `date_added` — an item can be in both the
  collection and the wishlist at once (the app already allows this; see
  `library_items.in_collection`/`in_wishlist` independence), and a shared
  column would have one sync's write silently clobber the other's date.
- **Internal naming moves from `collection` to `discogs` now**, not just the
  visible label: `RecordScope`, the `/api/releases?scope=` and
  `/api/artists?scope=` params (and both backend functions that check that
  value — `get_library_releases` and `get_distinct_artists`), the
  `collectionViewMode_*` localStorage key suffix, and `App.tsx`'s `View`
  union and `view` state. Once the future tab is also called "Collection,"
  leaving today's plumbing as `'collection'` would mean two different
  concepts sharing one identifier.
- **No new migration tooling.** This repo has no schema migration system
  beyond `TENANT_SCHEMA`'s idempotent `CREATE TABLE IF NOT EXISTS`, re-run on
  every startup. The two new columns are added the same way, via `ALTER
  TABLE library_items ADD COLUMN IF NOT EXISTS ...`, in the same block.

## Backend design

`backend/db.py`, `TENANT_SCHEMA` (appended after the existing
`library_items` table definition):

```sql
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS collection_date_added TIMESTAMP;
ALTER TABLE library_items ADD COLUMN IF NOT EXISTS wishlist_date_added TIMESTAMP;
```

`upsert_library_item` gains two optional kwargs, following the same
COALESCE-on-conflict shape the existing `in_collection`/`in_wishlist`
kwargs use (so a wishlist-sync write doesn't null out a collection-sync
write's date, or vice versa):

```python
def upsert_library_item(
    conn,
    user_id: int,
    discogs_id: str,
    in_collection: Optional[bool] = None,
    in_wishlist: Optional[bool] = None,
    collection_date_added: Optional[str] = None,
    wishlist_date_added: Optional[str] = None,
):
    conn.execute(
        """
        INSERT INTO library_items (
            user_id, discogs_id, in_collection, in_wishlist,
            collection_date_added, wishlist_date_added, last_synced
        )
        VALUES (
            %(user_id)s, %(discogs_id)s, COALESCE(%(in_collection)s, FALSE),
            COALESCE(%(in_wishlist)s, FALSE), %(collection_date_added)s,
            %(wishlist_date_added)s, CURRENT_TIMESTAMP
        )
        ON CONFLICT (user_id, discogs_id) DO UPDATE SET
            in_collection = COALESCE(%(in_collection)s, library_items.in_collection),
            in_wishlist = COALESCE(%(in_wishlist)s, library_items.in_wishlist),
            collection_date_added = COALESCE(%(collection_date_added)s, library_items.collection_date_added),
            wishlist_date_added = COALESCE(%(wishlist_date_added)s, library_items.wishlist_date_added),
            last_synced = CURRENT_TIMESTAMP
        """,
        {
            "user_id": user_id, "discogs_id": discogs_id,
            "in_collection": in_collection, "in_wishlist": in_wishlist,
            "collection_date_added": collection_date_added,
            "wishlist_date_added": wishlist_date_added,
        },
    )
```

`backend/discogs.py`: the Discogs collection API's release entries and the
wantlist API's want entries each carry a top-level `date_added` string
(ISO 8601). `parse_release` stays release-shape-only (it's keyed by
`discogs_id`, shared across users, and `date_added` is a per-user,
per-instance fact — it doesn't belong on `catalog`). Instead,
`crawl_manager._sync_collection`'s two loops read `item.get("date_added")`
directly off the raw API item (same place `item["basic_information"]` is
read today) and pass it through:

- Collection loop (`crawl_manager.py:397`):
  `upsert_library_item(conn, user_id, rid, in_collection=True, collection_date_added=item.get("date_added"))`.
- Wishlist loop (`crawl_manager.py:436-439`):
  `upsert_library_item(conn, user_id, rid, in_wishlist=True, in_collection=False if is_new_release else None, wishlist_date_added=item.get("date_added"))`.

Wishlist loop's `enqueue_crawl_queue` call (`crawl_manager.py:440-441`) is
deleted — the `for crawler in enabled_crawlers:` loop and its body go away
entirely from this branch. The collection loop's equivalent
(`crawl_manager.py:398-399`) is unchanged.

`get_library_releases`:

- SELECT gains `li.collection_date_added, li.wishlist_date_added` (added
  next to the existing `li.plex_url, li.plex_matched_at`).
- The response's per-row `date_added` is computed in Python after the query
  (not in SQL, since which column applies depends on `scope`, and `scope`
  is optional/absent for some other theoretical caller): `row["date_added"]
  = row["collection_date_added"] if scope == "discogs" else
  row["wishlist_date_added"] if scope == "wishlist" else None`.
- `_RELEASE_ALLOWED_SORT` gains `"date_added"`. Because the underlying
  column differs by scope, the sort branch needs the same scope-aware
  mapping: `sort_col = "collection_date_added" if scope == "discogs" else
  "wishlist_date_added"` when `sort == "date_added"`, else the existing
  `_RELEASE_ALLOWED_SORT` lookup.
- The `sort.startswith("price_")` branch and its crawler-lookup query are
  deleted — no caller will send that value anymore.
- `r["listings"] = get_listings_for_release(conn, r["discogs_id"])` is
  deleted — no consumer reads it anymore.
- Its existing top-of-function `if scope == "collection":` filter (the one
  gating `li.in_collection = TRUE`) becomes `if scope == "discogs":` — see
  "Internal naming" above.

`get_listings_for_release` is deleted outright (grep confirms
`get_library_releases` was its only caller).

`get_distinct_artists` gets the same one-line rename: `if scope ==
"collection":` becomes `if scope == "discogs":`. It's otherwise untouched —
no date_added involvement, just the same wire-value rename as
`get_library_releases`.

## Frontend design

`frontend/src/api/types.ts`:

- `RecordScope = 'discogs' | 'wishlist'`.
- `Release` drops `listings: Record<string, Listing | null>`; gains
  `date_added: string | null`.
- `Listing` interface deleted.

`frontend/src/views/RecordBrowser.tsx`:

- Props shrink to `{ scope, syncing, onRefreshCollection, syncGeneration }`
  — `onRefreshPrices`, `crawling`, `crawlingReleaseId`, `crawlEvents`,
  `crawlers`, `hiddenCrawlerIds` are removed.
- The `useEffect` that consumes `crawlEvents` to patch stale listings
  (lines 38-73 today) is deleted along with `processedCount`.
- `enabledCrawlers` (line 124) is deleted.
- Table header loses the per-crawler `<th>` loop and gains a "Date Added"
  `<th>` (sortable, same pattern as the existing Year/Label/Format headers)
  after the Price column.
- Table body loses the per-crawler `<td>` loop and the trailing refresh-icon
  `<td>`; gains a Date Added `<td>` rendering
  `r.date_added ? new Date(r.date_added).toLocaleDateString() : '—'` —
  same `new Date(...).toLocaleString()`-with-`'—'`-fallback convention
  `Settings.tsx:153` uses for `c.last_run`, using `toLocaleDateString()`
  instead of `toLocaleString()` since a collection date has no meaningful
  time-of-day component.
- The component's two remaining `scope === 'collection'` comparisons
  (`RecordBrowser.tsx:90`, gating the `unmatched` query param, and
  `RecordBrowser.tsx:174`, gating the Unmatched `<select>`'s visibility)
  become `scope === 'discogs'` — same rename as everywhere else in this
  slice, just easy to miss since neither is in the table markup this
  section otherwise describes.
- `colSpan` on the loading/empty rows drops the `+ enabledCrawlers.length`
  term (now a fixed column count).

`frontend/src/App.tsx`:

- `View` union's `'collection'` becomes `'discogs'`; initial `useState<View>`
  value updates to match.
- Nav button: `setView('collection')` / `view === 'collection'` → `'discogs'`;
  button label text stays as literal `Discogs` (was `Collection`).
- The `discogs`-view `<RecordBrowser>` call drops `onRefreshPrices`,
  `crawling`, `crawlingReleaseId`, `crawlEvents`, `crawlers`,
  `hiddenCrawlerIds`; keeps `scope="discogs"`, `syncing`,
  `onRefreshCollection={() => handleRefresh()}`, `syncGeneration`.
- The `wishlist`-view `<RecordBrowser>` call drops the same six props; keeps
  `scope="wishlist"`, `syncing`, `onRefreshCollection={() =>
  handleRefreshWishlist()}`, `syncGeneration`.
- `crawlingReleaseId` state (`useState` at line 37) and its two setters
  (`setCrawlingReleaseId(undefined)` on crawl complete/stopped,
  `setCrawlingReleaseId(releaseId)` in `startCrawl`) are deleted — with both
  `<RecordBrowser>` props gone, nothing reads this state anymore.
  `startCrawl`'s `releaseId` parameter itself is untouched (still forwarded
  to `postCrawlStart`), since `handleRefreshPricesFromSettings` and the
  generic bulk-crawl path still exist independent of this state.

## Testing

- `backend/tests/test_catalog_crud.py` — `test_get_library_releases_*`
  cases involving `price_Amazon`/`price_NoSuchSite` sorting and
  `test_get_listings_for_release_joins_crawler_site_name` are deleted (the
  code paths they cover no longer exist). New cases: `upsert_library_item`
  writes `collection_date_added` without touching `wishlist_date_added` and
  vice versa (COALESCE-on-conflict behavior, mirroring the existing
  `in_collection`/`in_wishlist` independence test); `get_library_releases`
  returns `date_added` sourced from `collection_date_added` when
  `scope="discogs"` and from `wishlist_date_added` when
  `scope="wishlist"`; sorting by `date_added` orders by the scope-correct
  column with nulls last, both orders; `get_distinct_artists` still filters
  correctly once its scope literal is `"discogs"`.
- `backend/tests/test_global_schema.py` — whatever it asserts about
  `library_items`' column set gets the two new columns added (as written
  today, it doesn't assert on `library_items`' columns at all, only
  `catalog`'s — so in practice this file needs no change).
- `backend/tests/test_library_maintenance.py` — its `get_distinct_artists`
  test's `scope="collection"` literal is renamed to `scope="discogs"`.
- `backend/tests/test_crawl_manager.py` — collection sync passes
  `collection_date_added` from the API item's `date_added` through to
  `upsert_library_item`; wishlist sync passes `wishlist_date_added` the same
  way; wishlist sync no longer calls `enqueue_crawl_queue` for any crawler
  (existing collection-sync enqueue assertions are unchanged).
- `frontend/src/test/recordBrowser.test.tsx` — the crawler-column and
  `hiddenCrawlerIds` cases are deleted (no `crawlers`/`hiddenCrawlerIds`
  props anymore); every `render(<RecordBrowser scope="collection" ...>)`
  call updates to `scope="discogs"` and drops `onRefreshPrices`; new case
  asserts the Date Added column renders `r.date_added` (or `—` when null)
  and is sortable.
- `frontend/src/test/staleListingClear.test.tsx` — deleted outright; the
  effect it tests no longer exists.
- `frontend/src/test/wishlistRefresh.test.tsx`,
  `frontend/src/test/syncRefetch.test.tsx`,
  `frontend/src/test/plexLink.test.tsx` — updated wherever they construct a
  `Release` fixture with `listings` (drop the field, add `date_added`) or
  render `<RecordBrowser scope="collection">` (rename to `"discogs"`); no
  behavioral assertions in these three files target removed functionality.
- `frontend/src/test/crawlStatusBar.test.tsx` and
  `frontend/src/test/viewRenderChurn.test.tsx` need more than a rename:
  both drive their scenario by clicking RecordBrowser's now-removed
  per-row "Refresh prices for this record" button
  (`clickRefreshAndGetSource()` in both files) to obtain the `MockEventSource`
  the test then emits on. Since the SSE connection already opens on mount
  regardless of any click (`App.tsx`'s stream-open effect, already exercised
  button-free by this same file's last two tests), the fix is to replace
  that helper with one that just waits for the mount-time connection:
  `await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0)); return getLastCrawlSource()`.
  `viewRenderChurn.test.tsx` additionally asserts
  `screen.getByText('eBay')` (a price-column cell) as its signal that a
  second crawl event landed — that assertion is replaced with a wait on the
  App-level crawl banner text instead (e.g. the `X/total` progress count
  `crawlStatusBar.test.tsx` already uses), and its `await
  screen.findAllByText('Amazon')` readiness wait (a proxy for "the
  crawlers-fetch state settled," keyed off RecordBrowser's now-removed
  crawler-column rendering) is replaced with `await waitFor(() =>
  expect(settingsSpy).toHaveBeenCalled())`, matching the reasoning already
  in that test's comment about why a settle-wait is needed there.

Playwright-dependent code is unaffected — this change touches no crawler
scraping logic.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo. This change doesn't add or alter a trigger, an external
call, or a runtime input/output shape — `date_added` is sourced from API
calls (`iter_collection_pages`/`iter_wantlist_pages`) this app already
makes; nothing new is called or consumed. `README.md` has no tab-by-tab UI
documentation to update. No agent-facing documentation changes are needed.
