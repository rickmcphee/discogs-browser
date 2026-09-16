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
  https with a hostname — the value lands in an `<img src>` the browser
  fetches and it is only the API's word that it is an image at all, the same
  reasoning `_is_ebay_item_url` already applies to the link. Both now read
  the host through one `_https_host` helper that answers None instead of
  raising; see the decision below. Amazon reports the search
  tile's `img.s-image`, captured beside the heading its artist/title check
  matched, because that tile is what was actually accepted; it takes the
  `src` only when it is https with a hostname, since a tile that has not
  finished loading carries a base64 `data:` placeholder — the wrong picture,
  and kilobytes of it on every row that stored it. Both crawlers read the
  host through one `crawler.https_host` helper; see the decision below.
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
- **One `https_host` guard, in `crawler.py`, for every URL a crawler hands
  the browser.** Two hazards, and checking them at each call site got both
  wrong in a different way. `urlparse` *raises* on a malformed authority —
  `"https://["` is an unterminated IPv6 literal — so checking the scheme
  inline meant one bad string in an otherwise fine response aborted the whole
  search, which on the stock-item path reads to the consecutive-failure
  breaker as the site being down. It also skipped past the fallback each
  caller carefully provides: a good `thumbnailImages` entry under a malformed
  gallery image, and the `legacyItemId` URL under a malformed `itemWebUrl`.
  Separately, a guard that reads only the scheme and hostname passes URLs
  the browser cannot load: `"https:///x.jpg"` has no hostname at all (a
  `startswith("https://")` prefix test, which is what the Amazon tile used,
  accepts it), and `"https://h:not-a-port/x.jpg"` splits with a clean scheme
  and hostname because `urlparse` does not check the port until `.port` is
  read. Neither is merely useless: the row *prefers* `listing_image_url` over
  `cover_image_url`, so such a value beats the target's good art and renders
  a broken thumbnail, which is precisely the fallback this design promises.
  `https_host` parses inside a `try`, reads `.port` there too so the parse is
  finished rather than merely started, answers None rather than raising, and
  requires a hostname — so every caller simply falls back.
- **A URL that parses differently here than in the browser is rejected
  outright.** The guard answers a question about what the *browser* will do
  with a string, so where `urlparse` and WHATWG disagree, `urlparse` is the
  wrong authority. Backslash is the sharp case: WHATWG treats `\` as `/` in a
  special scheme, so `"https://evil.example\@www.ebay.com/itm/1"` is host
  `evil.example` to a browser while `urlparse` reads `evil.example\` as
  userinfo and answers `www.ebay.com` — which turns `_is_ebay_item_url`, a
  hostname allowlist guarding a link the user clicks, into a redirect to
  anywhere. C0 controls, DEL and space are rejected with it: a browser strips
  or rejects them, and which of those Python does has changed across
  releases, so the host would otherwise depend on the interpreter (`urlparse`
  answers `www.ebay.com` for the NUL variant of the same spoof). Rejecting
  the class before parsing is the safer fix than matching browser
  normalisation, and costs only a fallback to the target's own art or to the
  `legacyItemId` link. It
  lives in `crawler.py` because `amazon.py` and `ebay_api.py` both already
  import from there, and because the third caller — `_is_ebay_item_url`,
  which had the identical inline parse on `itemWebUrl` and predates this
  change — is the evidence that a per-call-site check does not stay right.
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
  fields come back the wrong shape. Three more cover the parse itself: an
  unparseable gallery image falls back to the thumbnail rather than aborting,
  a host-less `https:///…` is rejected, and an unparseable `itemWebUrl` falls
  back to the `legacyItemId` link — the last being the pre-existing half of
  the same hazard.
- `frontend/src/test/stockBrowser.test.tsx` — a comparison row with a
  `listing_image_url` renders that picture with the listing's name as alt
  text while the own row keeps the store's cover; a row with a
  `listing_title` but no `listing_image_url` falls back to the target's cover
  *and* to the target's title as that image's alt text.
- `backend/tests/test_crawler_utils.py` — `https_host` directly: it returns
  the host of a good URL, keeps a valid explicit port, rejects a host-less
  `https:///…` and a malformed or out-of-range port, answers None rather
  than raising on `"https://["`, rejects the backslash and NUL hostname
  spoofs along with tab/newline/space, and rejects non-https, empty and
  non-string values.
- `backend/tests/test_ebay_api.py` also covers the spoof end to end: a
  `itemWebUrl` of `"https://evil.example\@www.ebay.com/itm/123"` does not
  satisfy the allowlist and the listing falls back to the `legacyItemId`
  link. This is where the Amazon guard is actually covered:
  `search()` is Playwright-driven and, per `CLAUDE.md`, not unit-tested, so
  routing its check through a shared pure function is what makes it testable
  at all.
- Amazon's `search()` itself remains a manual-verification item for the tile
  capture, as the heading capture was.
