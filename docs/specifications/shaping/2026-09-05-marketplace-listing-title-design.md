# Show the name a marketplace gave the item it found

Date: 2026-09-05
Branch: `claude/marketplace-search-result-names-iea0xg`

## Problem

A release crawler (Amazon, eBay, eBay/CCmusic, Discogs Marketplace) searches
a marketplace by the target's artist and title and takes the first result
that passes a loose word-overlap check (`ebay_api.pick_matching_item`, the
`h2` check in `amazon.py`). The match is deliberately imprecise, and it is
often a *different pressing* of the same record: a coloured-vinyl variant in
the library is searched by name, the marketplace answers with the standard
black pressing, and the row that lands in Store/Track shows the variant's
Discogs title next to the black pressing's price and link.

Nothing in the result carried the marketplace's own name for the item.
`search()` returned `{url, price, shipping, currency, condition}`, and both
writers of a match — `upsert_stock_item_from_release` for a release target
and `upsert_stock_item_listing` for a stock-item target — stored only those,
so the UI had nothing but the target's title to show. Comparison rows under a
store's own row copy that row's title outright (`get_stock_items`,
`"title": r["title"]`), so an eBay listing for a different edition read as
the store's edition.

## Scope

- `backend/crawlers/*.py` (release crawlers) and `backend/ebay_api.py` — the
  `search()` result dict gains an optional `title`: the name the site itself
  gives the matched item. eBay reports the Browse API item's `title`; Amazon
  reports the search-result heading its artist/title check matched (the
  heading, not the product page's, because the heading is what was actually
  accepted). `discogs_marketplace` omits it: the sell page is built around
  the exact release id, so the target's name is already the right one.
- `backend/db.py` — `stock_items.listing_title` and `listings.listing_title`
  (both nullable TEXT, added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
  like `stock_items.release_id`). `upsert_listing` and
  `upsert_stock_item_listing` take a trailing optional `listing_title`;
  `upsert_stock_item_from_release` reads `listing["title"]`. Both
  `get_stock_items` paths (grouped and the flat Cost-sort CTE) return
  `listing_title` on every row: the own row's from `stock_items`, a
  comparison row's from its `listings` row rather than the own row's.
- `backend/crawl_manager.py` — `_write_result` passes `best.get("title")`
  through on both the release and the stock-item branch.
- `frontend/src/api/types.ts` / `frontend/src/views/StockBrowser.tsx` —
  `StockItem.listing_title: string | null`; the title cell in the table,
  list and tile views renders `listing_title ?? title`, and when a
  `listing_title` differs from `title` the target's own title becomes the
  cell's hover text (a recommendation reason keeps that slot when there is
  one, as before).
- `CLAUDE.md` crawler plugin interface, and the 2026-06-27 and 2026-08-11
  specs, amended to match.

Out of scope:

- **Backfill.** Rows written before this change have `listing_title` NULL
  and keep showing the target's title until the next pass re-crawls them —
  the scheduled sweep and manual crawls populate the column naturally, the
  same posture the 2026-08-11 design took for `stock_items.release_id`.
- **Price-drop notifications.** `get_price_drop_notifications` names an item
  from `stock_item_identities`, which is the identity, not any one source's
  listing; left as-is.
- **Store (catalog) crawlers.** Their `stock_items.title` *is* already what
  the store calls the item, scraped from the storefront. Nothing changes for
  them; their rows simply carry `listing_title` NULL.

## Decisions

- **A second column, not an overwrite of `stock_items.title`.** The obvious
  version — write the marketplace's name into `title` — breaks three things
  that hang off that column. `item_key` hashes `artist|title|url`, and the
  2026-08-11 design pins it to the catalog title so a release found by a
  release crawler and a catalog crawler resolves to one identity. Title sort
  and the search box run on `s.title`. And `_library_match_fragment`, which
  is what puts a row on the Track tab at all, is an exact-or-prefix match of
  `stock_items.title` against the library's catalog title — an eBay name
  like "NIRVANA Nevermind LP Blue Vinyl Sealed" neither equals nor starts
  with "Nevermind", so a wishlist match would vanish from the very tab it
  was crawled for. `listing_title` is display-only, and every one of those
  readers keeps reading `title`.
- **Same column name on both tables.** `listings` has no `title` of its own,
  so it could have taken the plain name; `stock_items` could not. One name
  across both keeps the API field, the frontend type and the SQL that unions
  the two (`_STOCK_OFFERS_CTE`) reading the same way.
- **Written on every pass, absent included.** A crawler that reported a name
  last time and none this time (a plugin change, a page that rendered
  without its heading) must not leave the old name attached to a listing it
  no longer describes, so the upserts write `EXCLUDED.listing_title`
  unconditionally, and the release path folds an empty string to NULL.
- **Target title moves to hover text.** The substitution should stay
  visible: a user who sees an unexpected name can hover to confirm which
  library item the row was searched for. When a judgment reason already
  occupies the tooltip it wins, since it did before.
- **No `title` from `discogs_marketplace`.** The listing rows on a sell page
  carry seller, condition and price, not a per-listing item name, and the
  page is for one exact release — reporting the catalog title back would
  only make every Discogs row look "renamed" to the same string.

## Testing

- `backend/tests/test_stock_crud.py` — the release upsert stores the
  reported name alongside the untouched catalog `title`/`item_key`/identity
  row, and drops it when a rerun reports none; `upsert_stock_item_listing`
  and `upsert_listing` store it on `listings` (and clear it when omitted);
  both `get_stock_items` paths return each row's own source's name, with a
  comparison row carrying its listing's rather than the own row's.
- `backend/tests/test_crawl_manager.py` — `_drain_one_batch` carries a
  `title` from `search()` into `stock_items.listing_title` and
  `listings.listing_title` on the release branch, and into
  `listings.listing_title` on the stock-item branch.
- `backend/tests/test_ebay_api.py` — `search_ebay` reports the matched
  Browse API item's title.
- `frontend/src/test/stockBrowser.test.tsx` — a comparison row with a
  `listing_title` renders that name with the target title as hover text,
  while the own row without one still shows the target title.
- Amazon's `search()` is Playwright-driven and, per `CLAUDE.md`, not
  unit-tested; the heading capture is a manual-verification item.
