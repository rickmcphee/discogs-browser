# Show the picture a marketplace showed for the item it found

Date: 2026-09-16
Branch: `claude/eager-hawking-xevk84`

## Problem

[`2026-09-05-marketplace-listing-title-design.md`](2026-09-05-marketplace-listing-title-design.md)
fixed half of a row's identity and left the other half behind. A release
crawler that searches by name takes the first result passing a loose
word-overlap check, so what it finds is often a *different pressing* of the
record. That design gave the row the marketplace's own name for what it
found; the thumbnail beside that name went on showing the target's cover art.

So a Store row now reads "Rob Zombie - The Great Satan [Standard Black LP]"
— Amazon's name for the copy it matched — next to a picture of the ghostly
green variant the library actually holds, at Amazon's price, linking to
Amazon's page. Every field on the row describes the copy that was found
except the one a user reads first. On a comparison row under a store's own
row the effect is stronger still, because `get_stock_items` copies the parent
row's `cover_image_url` outright: an eBay listing for a different edition was
pictured with the *store's* photograph of its own edition.

`StockBrowser.tsx` recorded the gap in a comment rather than closing it —
"The thumbnail's alt text is not substituted: the image is the target's own
cover, not the listing's."

## Scope

- `backend/crawlers/amazon.py` and `backend/ebay_api.py` — the `search()`
  result dict gains an optional `cover_image_url`: the picture the site
  itself showed for the matched item. It reuses the key catalog crawlers
  already put an item's picture under, so "the picture of this item" has one
  name across both crawler kinds. eBay reports the Browse API item's `image`,
  falling back to a `thumbnailImages` entry, and skips any URL that is not
  https — the value lands in an `<img src>` the browser fetches and it is
  only the API's word that it is an image at all, the same reasoning
  `_is_ebay_item_url` already applies to the link. Amazon reports the search
  tile's `img.s-image`, captured beside the heading its artist/title check
  matched, because that tile is what was actually accepted; it takes the
  `src` only when it is https, since a tile that has not finished loading
  carries a base64 `data:` placeholder — the wrong picture, and kilobytes of
  it on every row that stored it.
- `backend/db.py` — `stock_items.listing_image_url` and
  `listings.listing_image_url` (both nullable TEXT, added via `ALTER TABLE
  ... ADD COLUMN IF NOT EXISTS` alongside `listing_title`). `upsert_listing`
  and `upsert_stock_item_listing` take a trailing optional
  `listing_image_url`; `upsert_stock_item_from_release` reads
  `listing["cover_image_url"]`. Both `get_stock_items` paths return
  `listing_image_url` on every row: the own row's from `stock_items`, a
  comparison row's from its `listings` row rather than the own row's.
  `cover_image_url` keeps coming from the target on every row.
- `backend/crawl_manager.py` — `_write_result` passes
  `best.get("cover_image_url")` through on both the release and the
  stock-item branch.
- `frontend/src/api/types.ts` / `frontend/src/views/StockBrowser.tsx` —
  `StockItem.listing_image_url: string | null`; the thumbnail in the table,
  list and tile views renders `listing_image_url ?? cover_image_url`.
- `CLAUDE.md` crawler plugin interface, and the 2026-06-27, 2026-08-08 and
  2026-08-11 specs, amended to match.

Out of scope:

- **Backfill.** Rows written before this change have `listing_image_url`
  NULL and keep showing the target's cover until the next pass re-crawls
  them — the same posture the 2026-09-05 design took for `listing_title`,
  and `stock_items.release_id` before it.
- **Discogs Marketplace and Rough Trade.** Neither reports a `title` either,
  and for the same reason: `discogs_marketplace` opens the sell page for the
  exact release id, and `roughtrade` probes a candidate URL and verifies the
  artist/title identity of the page it lands on. Where the match is to the
  exact record rather than to a name, the target's own art is already the
  right picture.
- **Catalog (store) crawlers.** Their `stock_items.cover_image_url` *is*
  already the picture the store showed, scraped from the storefront.
  Nothing changes for them; their rows simply carry `listing_image_url` NULL.
- **Price-drop notifications.** `get_price_drop_notifications` picks a
  picture by `item_key` from whichever `stock_items` row has one, because it
  names an *identity* rather than any one source's listing. Left as-is, as
  the 2026-09-05 design left the name there.

## Decisions

- **A second column, not an overwrite of `cover_image_url`.** The parallel
  with `listing_title` is exact, and so is the reason. `cover_image_url` is
  the target's own art — the Discogs cover for a release target, the
  storefront's photo for a stock-item one — and it is what a row falls back
  *to* when the source showed no picture. Overwriting it would throw the
  fallback away, and on a comparison row there would be nothing to fall back
  to at all: the CTE reads the picture from the parent `stock_items` row,
  which is the only row a comparison has.
- **`listing_image_url`, not `listing_cover_image_url`.** `listing_title`
  pairs with `title`, so the strictly parallel name would pair with
  `cover_image_url`. "Cover" is a catalog-art notion, though, and what a
  marketplace shows is a photograph of the copy for sale — often a seller's
  own snapshot, sometimes of the sleeve's back. The shorter name says the
  true thing; the `listing_` prefix already marks it as the source-reported
  counterpart.
- **`cover_image_url` as the `search()` key, `listing_image_url` as the
  column.** Exactly the split `listing_title` already uses, where the dict
  key is the plain `title`. A plugin author describes the item in front of
  them; the column records whose description it was.
- **Written on every pass, absent included.** A source that showed a picture
  last time and none this time (a plugin change, a tile that rendered
  without its thumbnail) must not leave the old picture attached to a
  listing it no longer describes, so the upserts write
  `EXCLUDED.listing_image_url` unconditionally and fold an empty string to
  NULL.
- **Alt text follows the picture, not the row's name.** `displayTitle()` and
  `displayImage()` fall back independently, so a source can report a name
  and no picture — and there the thumbnail is the target's cover while the
  row is named for the listing. Naming the image with `displayTitle()` would
  describe it as something it is not, to precisely the users who cannot see
  it, so `imageAlt()` returns the listing's name only when the listing's
  picture is what rendered.
- **Amazon's tile, not its product page.** The product page carries a larger
  image, but reading it would mean a second extraction against a page whose
  price extraction is already the fragile part of this crawler, for a
  thumbnail rendered at 40-56px. The tile's `img.s-image` is the picture
  beside the heading the match was made on, and capturing both together
  keeps them describing one item.
- **Best-effort capture, never a failure.** A missing thumbnail leaves
  `matched_image` empty and the target's cover standing. It must not raise:
  on the stock-item path a raise is a site-health signal to the
  consecutive-failure breaker, and a tile that renders without a thumbnail
  is not evidence that Amazon is down.

## Testing

- `backend/tests/test_stock_crud.py` — the release upsert stores the
  reported picture alongside an untouched `cover_image_url`, and drops it
  when a rerun reports none; `upsert_stock_item_listing` and `upsert_listing`
  store it on `listings` (and clear it when omitted or empty); both
  `get_stock_items` paths return each row's own source's picture, with a
  comparison row carrying its listing's while still carrying the own row's
  cover as the fallback.
- `backend/tests/test_crawl_manager.py` — `_drain_one_batch` carries a
  `cover_image_url` from `search()` into `stock_items.listing_image_url` and
  `listings.listing_image_url` on the release branch, and into
  `listings.listing_image_url` on the stock-item branch.
- `backend/tests/test_ebay_api.py` — `search_ebay` reports the matched Browse
  API item's gallery image, falls back to a thumbnail, skips a non-https URL,
  and returns the listing with no picture rather than raising when the image
  fields come back the wrong shape.
- `frontend/src/test/stockBrowser.test.tsx` — a comparison row with a
  `listing_image_url` renders that picture with the listing's name as alt
  text while the own row keeps the store's cover; a row with a
  `listing_title` but no `listing_image_url` falls back to the target's cover
  *and* to the target's title as that image's alt text.
- Amazon's `search()` is Playwright-driven and, per `CLAUDE.md`, not
  unit-tested; the tile thumbnail capture is a manual-verification item, as
  the heading capture was.
