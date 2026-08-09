# Store/Collection tab split design

Date: 2026-08-08
Branch: `worktree-store-collection-split`

## Problem

This is the third slice of the v3.0 redesign (see
`2026-08-08-discogs-tab-rename-design.md` for slice 1 and
`2026-08-08-crawl-target-expansion-design.md` for slice 2, which built
`listings.item_key` but surfaced it nowhere). The Store tab
(`StockBrowser.tsx`, internal view `instock`) shows each in-stock item with
only the one price its own store reported — the cross-site comparison
prices slice 2 now collects for stock items (via `enqueue_crawl_queue_for_stock_item`
enqueueing amazon/ebay searches keyed to each item's `item_key`) have no UI
consumer yet. This slice makes the Store tab show that comparison data, and
adds a new tab — reusing the name "Collection," freed for this purpose by
slice 1 — that's the same underlying inventory filtered down to items also
in the user's Discogs collection (today's "Overlapping" filter, promoted to
its own tab).

A structural note that shapes the design below: `item_key` (computed by
`compute_item_key(artist, title, url)`) is keyed off each store listing's
own URL, so it's unique per *store listing*, not per canonical release. The
same album in stock at two different stores gets two different `item_key`s,
each independently enqueuing and accumulating its own amazon/ebay comparison
prices. "Combined inventory" in this doc always means store-item rows plus
comparison rows *for that same store listing* — never a release-level
merge across stores.

## Scope

Touches:

- `backend/db.py` — `get_stock_items` gains a second query that fetches
  `listings` rows for the current page's `item_key`s and a Python merge
  step that flattens each stock item's own row plus its comparison rows
  into the returned list; `exclude_crawler_ids` applies to both. No
  signature, filter, sort, or pagination-math changes — `total`/`page`/
  `per_page` stay item-counted, exactly as today.
- `frontend/src/views/StockBrowser.tsx` — gains a `scope: 'store' |
  'collection'` prop (default `'store'`); list-view table body renders the
  new flattened multi-row shape; tile view keeps rendering one tile per
  item, now filtered to `is_own` rows.
- `frontend/src/App.tsx` — `View` union gains `'collection'`; new nav
  button; renders `<StockBrowser scope="collection" ...>`.
- `frontend/src/api/types.ts` — `StockItem` gains `item_key: string` and
  `is_own: boolean`; `id` becomes `number | string` (a comparison row has
  no `stock_items.id` of its own).
- Tests: `backend/tests/test_stock_crud.py` (where `get_stock_items` is
  tested), `backend/tests/test_stock_router.py`, and a new frontend test
  for `StockBrowser`'s `scope` prop plus an `App.tsx` nav test.

Out of scope (per brainstorm): live SSE updates for comparison prices as
they land (Store/Collection stay fetch-on-load/filter-change, same as
StockBrowser today); any change to how/when comparison crawls are enqueued
(slice 2, already shipped); `discogs_marketplace` (already structurally
excluded via `requires_discogs_release`).

## Decisions carried from brainstorming

- **Both Store and Collection use the same multi-row-per-item table** — one
  row per price source (the store's own price, then one row per comparison
  listing), flat and fully repeated (no rowspan/grouping), sorted so an
  item's rows land together because they share `artist`/`title`. This was
  an open fork going in (the alternative was widening Store's existing
  single-row table with per-site columns, like the pre-slice-1
  `RecordBrowser`); multi-row won because it's one shape for both tabs
  instead of two.
- **Tile view survives, scoped to each item's own row only.** A tile shows
  one cover + one price; it can't represent multiple comparison rows. The
  backend tags every row `is_own: bool` specifically so the frontend never
  has to reverse-engineer "which row is the store's own" from `source`
  name-matching.
- **Store's "Overlapping" filter is removed, not duplicated.** It already
  means exactly what Collection now exists to show ("also in your Discogs
  collection," via `_not_owned_clause`). Store's filter dropdown shrinks to
  All/Recommended; Collection has no filter dropdown at all — the tab
  itself is the filter, always sending `overlapping=true`, never
  `recommended=true` (recommended items are defined to exclude owned items,
  so within Collection that filter could only ever return empty).
- **Collection reuses `StockBrowser`'s full shell** (artist sidebar, search,
  pagination, tile/list toggle) rather than being a new plain sortable
  table like `RecordBrowser`. It's the same `stock_items` data source, just
  pre-filtered; a `scope` prop is the same pattern `RecordBrowser` already
  uses for Discogs/Wishlist, so this is a mechanical extension, not a new
  component.
- **Hiding a crawler in Settings now also hides its comparison rows.**
  `hiddenCrawlerIds`/`exclude_crawler_ids` already excludes whole stock
  items sourced from a hidden *store* crawler; this slice extends the same
  parameter to the new comparison-row query, so hiding Amazon in Settings
  finally does something for Amazon comparison rows too (today it's a
  no-op there, since `RecordBrowser` stopped consuming it in slice 1 and
  nothing else ever did for release crawlers).
- **Price sort uses the item's own store price.** With multiple price rows
  per item, "sort by price" is ambiguous (own price vs. cheapest
  comparison). Sorting stays keyed on `stock_items.price` exactly as today;
  comparison rows simply follow their item wherever it lands. No new sort
  fields.
- **No placeholder rows for pending comparisons.** A crawler that hasn't
  found a match yet (no `listings` row) contributes nothing — matching the
  existing repo-wide invariant ("no row means not yet crawled, not a
  NULL-price placeholder") rather than introducing a new pending/checking
  state to track and render.
- **No backend or API signature changes for the Collection tab.**
  `/api/stock` and `/api/stock/artists` are unchanged; Collection is just a
  caller that always passes `overlapping=true`. All of the filtering logic
  (`_not_owned_clause`) it needs already exists and needs no changes.
- **This PR takes the major version bump to `3.0`, on the repo owner's
  explicit instruction** (`backend/version.py`, currently `"2.14"`) —
  overriding the usual default-to-minor-bump convention. This is the last
  of the three v3.0 slices (tab rename, crawl-target expansion, this
  split), so the major bump lands when this one merges, not on either of
  the earlier two.

## Backend design

`get_stock_items` (`backend/db.py:891`) keeps its existing conditions/sort/
pagination block completely as-is through the point where it currently
fetches and returns `rows`. What's added, after that fetch:

```python
    item_keys = [r["item_key"] for r in rows]
    comparisons = conn.execute(
        """
        SELECT l.item_key, l.price, l.currency, l.url, l.condition, l.last_checked,
               cr.site_name AS source
        FROM listings l
        JOIN crawlers cr ON cr.id = l.crawler_id
        WHERE l.item_key = ANY(%(item_keys)s) AND l.price IS NOT NULL
        """ + (" AND cr.id != ALL(%(exclude_crawler_ids)s)" if exclude_crawler_ids else ""),
        {"item_keys": item_keys, "exclude_crawler_ids": exclude_crawler_ids},
    ).fetchall()
    comparisons_by_item: dict[str, list[dict]] = {}
    for c in comparisons:
        comparisons_by_item.setdefault(c["item_key"], []).append(c)

    flattened = []
    for r in rows:
        flattened.append({**r, "is_own": True})
        for c in comparisons_by_item.get(r["item_key"], []):
            flattened.append({
                "id": f"{r['item_key']}:{c['source']}",
                "item_key": r["item_key"], "artist": r["artist"], "title": r["title"],
                "format": r["format"], "cover_image_url": r["cover_image_url"],
                "price": c["price"], "currency": c["currency"], "url": c["url"],
                "source": c["source"], "reason": r["reason"], "last_seen": c["last_checked"],
                "is_own": False,
            })

    return {"total": total, "page": page, "per_page": per_page, "items": flattened}
```

**Amendment (2026-08-09):** the synthesized comparison-row `"id"` above is
built from `r['id']` (the own row's `stock_items.id`), not `r['item_key']`
as shown — `item_key` isn't unique per `stock_items` row (two rows can
share one), so keying off it collided. The comparison query also carries
an `ORDER BY l.item_key, cr.site_name` for deterministic multi-crawler
ordering, omitted above.

`l.price IS NOT NULL` mirrors the existing defensive filter in
`get_missing_releases` — under the current invariant `upsert_listing`/
`upsert_stock_item_listing` only ever write a row on a match, so this is
belt-and-suspenders, not load-bearing.

`total`/pagination are computed from the `COUNT(*) FROM stock_items s
{where}` query exactly as today, before flattening — a 250-item page can
render more than 250 table rows, and that's fine, since pagination is about
"how many items have you paged through," not "how many rows rendered."

`get_distinct_stock_artists` is unchanged — the artist sidebar lists
distinct `stock_items.artist` values regardless of comparison data.

No API/router changes: `routers/stock.py`'s `list_stock`/
`list_stock_artists` call these functions with the same parameters they
already accept. The Collection tab is purely a new *caller* (`overlapping=
true`, `recommended` never sent) of the existing `/api/stock`/`/api/stock/
artists` endpoints — no new query param, no new endpoint.

## Frontend design

`frontend/src/api/types.ts`:

```ts
export interface StockItem {
  id: number | string     // number (stock_items.id) for an own row;
                           // `${item_key}:${source}` string for a
                           // comparison row, which has no stock_items id
  item_key: string
  artist: string
  title: string
  format: string | null
  price: number | null
  currency: string | null
  url: string
  cover_image_url: string | null
  source: string
  last_seen: string
  reason: string | null
  is_own: boolean
}
```

`frontend/src/views/StockBrowser.tsx`:

- Props gain `scope?: 'store' | 'collection'` (default `'store'`).
- `load()`'s `getStock(...)` call sets `overlapping: scope === 'collection'`
  — the sole source of `overlapping: true` going forward, now that the
  `'overlapping'` filter option (the only other former source) is removed
  from the dropdown entirely (next bullet).
- The filter `<select>` (`artist/title/format` search bar's right-hand
  dropdown) is rendered only when `scope === 'store'`; its options drop
  `'overlapping'`, leaving `All`/`Recommended`. When `scope === 'collection'`,
  no dropdown renders at all.
- Tile view's `items.map(...)` filters to `item.is_own` before mapping —
  unaffected otherwise; still one tile per item, same cover/artist/title
  rendering as today.
- List view's `<tbody>` maps over `items` directly (no filtering) — every
  row (own or comparison) gets a `<tr>`. The existing per-row cells
  (cover/artist/title/format/price/source) render unchanged per row; a
  comparison row's cover cell reuses the same `cover_image_url` its group's
  own row carries (comparison listings have no cover of their own), giving
  visual continuity across a group's rows without any grouping markup.
- No new state, no new effects, no SSE — `load()`'s shape and triggers
  (search/artist/sort/page/filter/hiddenCrawlerIds) are unchanged.

`frontend/src/App.tsx`:

- `View` union: `'discogs' | 'wishlist' | 'instock' | 'collection' |
  'settings' | 'logs' | 'account'`.
- New nav button between Store and Settings: `setView('collection')` /
  `view === 'collection'`, label `Collection`.
- New render block:
  ```tsx
  <div className={view === 'collection' ? 'h-full' : 'hidden'}>
    <StockBrowser scope="collection" hiddenCrawlerIds={hiddenCrawlerIds} />
  </div>
  ```
  `recommendedAvailable` isn't passed (defaults to `false`, and is unused by
  `StockBrowser` when `scope === 'collection'` anyway, since the dropdown
  that would consume it never renders).

## Testing

- `backend/tests/test_stock_crud.py` — new cases alongside the existing
  `test_get_stock_items_*`/`test_get_distinct_stock_artists_*` tests:
  `get_stock_items` returns an item's own row followed by its comparison
  rows (ordered, `is_own` set correctly); an item with no comparison
  listings yet returns only its own row; `exclude_crawler_ids` suppresses a
  matching comparison row without touching that item's own row; a
  comparison listing with `price IS NULL` is excluded; `overlapping=True`
  still returns just the owned-intersection, now including each returned
  item's comparison rows; pagination `total` stays item-counted when items
  have comparison rows (a page can render more table rows than
  `per_page`).
- `backend/tests/test_stock_router.py` — `GET /stock` response shape
  includes comparison rows for an item with `listings`; no new query
  params are introduced, so no new router-level param-parsing tests are
  needed beyond confirming existing ones still pass unchanged.
- Frontend: new `frontend/src/test/stockBrowserScope.test.tsx` (or added
  to an existing `StockBrowser` test file if one exists) —
  `scope="collection"` sends `overlapping: true` to `getStock`/
  `getStockArtists` and hides the filter dropdown; `scope="store"` (or
  unset) keeps today's behavior with the dropdown showing only All/
  Recommended; list view renders multiple `<tr>`s for one item_key; tile
  view renders exactly one tile per item_key regardless of comparison
  rows.
- `frontend/src/test/` — App-level test (new or added to an existing
  App test) asserting the Collection nav button switches `view` and
  renders `<StockBrowser scope="collection">`.

Playwright-dependent code is unaffected — this slice only changes how
already-collected data is queried and displayed.

## Runtime/agent document impact

No `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`
exist in this repo (same finding as slices 1 and 2). This change adds no
new external trigger, outbound call, or runtime input/output shape — it's
a new read/display path over data slice 2 already collects. No
agent-facing documentation changes are needed.
