# In Stock Crawler — Design

**Date:** 2026-07-05
**Status:** Implemented
**Branch:** `dev-instock-crawler`

**Amendment (2026-07-05, branch `store-tab-overlapping-filter`):** the tab is now labeled **Store** (was "In Stock"), and the Settings section is now labeled **Store Crawlers** (was "Catalog Crawlers") — cosmetic renames only, no data-model or endpoint changes. The "Owned-item cross-reference" item under Out of scope was reversed: an **Overlapping** filter now exists (see Decisions and API below). Text below is updated in place to match; see git history for the original wording.

---

## Problem

The existing crawler system answers "what does site X charge for release Y in my collection?" — a per-release search driven by Playwright. There's a different question worth answering: "what's currently for sale at site X, regardless of whether I already own it?" Four sources ship together: Nuclear Blast (`shop.nuclearblast.com/collections/vinyl`), Century Media (`centurymedia.store/collections/vinyl`), Epitaph (`epitaph.com/collections/vinyl`), and Rev HQ (`revhq.com/collections/vinyl`) — all full catalogs of in-stock vinyl, browsable independently of the user's Discogs collection/wishlist.

## Goal

Add a "catalog crawler" — a second, parallel crawler kind that scans an entire site's in-stock catalog (rather than searching per-release) and stores the results in a new `stock_items` table, surfaced in a new **Store** tab (named for the concept, not the site, since more catalog sources are expected later).

---

## Technical grounding

`shop.nuclearblast.com` is a Shopify storefront. Shopify exposes a public, unauthenticated JSON endpoint per collection:

```
GET https://shop.nuclearblast.com/collections/vinyl/products.json?limit=250&page=N
```

Response shape (confirmed by direct fetch):

```json
{
  "products": [
    {
      "title": "Rob Zombie - The Great Satan",
      "vendor": "Rob Zombie",
      "handle": "rob-zombie-the-great-satan",
      "product_type": "Vinyl",
      "variants": [
        {"title": "Ghostly Black Vinyl", "price": "31.99", "available": true},
        {"title": "Jewel Case CD", "price": "14.99", "available": true},
        ...
      ]
    }
  ]
}
```

- `vendor` = artist. `title` = `"Artist - Album Title"` (vendor prefix stripped for display).
- Each `variant` is one format/color combination; `available` is a per-variant in-stock boolean.
- Product page URL: `{base_url}/products/{handle}`.
- ~511 vinyl-collection products fit in 3 pages at `limit=250`.

This means the crawler is a pure `httpx` client — no Playwright, no bot-detection handling — architecturally closer to [`backend/crawlers/ebay.py`](../../../backend/crawlers/ebay.py) than [`backend/crawlers/amazon.py`](../../../backend/crawlers/amazon.py).

### Century Media — same endpoint shape, different catalog shape

`centurymedia.store` is also a Shopify storefront, and its `/collections/vinyl/products.json` endpoint has the identical top-level shape. But direct inspection of the live data turned up three real differences that shaped the design below, not just a second copy of Nuclear Blast's crawler:

1. **Pre-order tag spelling differs.** Century Media tags pre-orders `"preorder"` (no hyphen); Nuclear Blast uses `"pre-order"`.
2. **No format-mixing, so no per-variant filter is needed.** Every Nuclear Blast product bundles vinyl colors *and* CD/cassette as sibling variants on one product — that's why a per-variant vinyl-title regex is required there. Century Media's `/collections/vinyl` products are already vinyl-only: each product has exactly one variant (confirmed by scanning 50 products, no exceptions found), and that variant's `title` is just a color name (e.g. `"Blue EcoMix"`) with no format wording at all — a `\bvinyl\b|\blp\b` regex would match nothing. The collection URL alone determines vinyl-ness here.
3. **The color is baked into the product title, not the variant.** e.g. `"Distant - Into Despair - Blue EcoMix LP"` (vendor `"Distant"`). After stripping the vendor prefix, the remainder (`"Into Despair - Blue EcoMix LP"`) is already the complete display title — appending the variant name too (as Nuclear Blast's crawler does) would duplicate the color.
4. **The vendor doesn't always prefix-match the title exactly.** `"Hackett & Rothery - The Roaring Waves - LP"` has `vendor: "Steve Hackett"` — a two-artist collab credited to one vendor. The prefix-strip helper has to tolerate this by leaving the title untouched rather than guessing.

Example response (confirmed by direct fetch):

```json
{
  "products": [
    {
      "title": "Distant - Into Despair - Blue EcoMix LP",
      "vendor": "Distant",
      "handle": "distant-into-despair-blue-ecomix-lp",
      "tags": ["cm", "distant", "preorder", "vinyl"],
      "variants": [
        {"title": "Blue EcoMix", "price": "24.98", "available": true}
      ]
    }
  ]
}
```

### Epitaph — same shape as Century Media, different constants

`epitaph.com` is also Shopify, and turns out to match Century Media's shape rather than Nuclear Blast's: every product has exactly one variant, always literally titled `"Default Title"` (confirmed by direct inspection — the variant title carries zero information), no format-mixing within a product, and the format/color baked into the product `title` (e.g. `"No Devolución 2xLP (Black)"`, vendor `"Thursday"`). Two differences from Century Media: Epitaph's titles never start with an exact `"{vendor} - "` prefix at all (no case where stripping applies — `strip_vendor_prefix` already no-ops safely here), and its pre-order tag is spelled `"pre-order"` (matching Nuclear Blast, not Century Media's `"preorder"`). No new shared-module logic is needed; Epitaph's crawler is Century Media's shape with different constants.

### Rev HQ — same endpoint shape, but `vendor` is the record label, not the artist

`revhq.com` is also Shopify, and structurally resembles Nuclear Blast — products mix LP/CD variants, and variant titles carry real information (`"LP - Color Vinyl"`, `"7\""`) worth keeping in the display title. But direct inspection of 20 sampled products turned up a real landmine: **`vendor` is always the record label** (e.g. `"Metal Blade Records"`, `"Relapse Records"`), never the artist. The actual artist only exists embedded in the title as `Artist "Album Title"` — every sampled title matched this pattern with zero exceptions:

```json
{
  "products": [
    {
      "title": "100 Demons \"Embrace The Black Light\"",
      "vendor": "Closed Casket Activities",
      "handle": "100-demons-embrace-the-black-light",
      "tags": ["100 Demons", "hardcore", "Music", "punk", "Vinyl"],
      "variants": [
        {"title": "LP - Color Vinyl", "price": "25.60", "available": true},
        {"title": "CD", "price": "12.30", "available": true}
      ]
    }
  ]
}
```

Using `vendor` as artist here, the way every other site's crawler does, would mislabel every row with a distributor name instead of a band name. Two other findings:

- The vinyl-detection regex needs widening for this site. Nuclear Blast's `\bvinyl\b|\blp\b` misses bare inch-size variants like `"7\""` (a 7" single) — no "vinyl"/"lp" wording appears there at all. Rev HQ's crawler uses its own wider pattern, `\bvinyl\b|\blp\b|\d+\s*"`, kept local to this crawler rather than widening Nuclear Blast's regex, since there's no evidence Nuclear Blast has the same gap.
- **No reliable pre-order signal was found.** Tags don't carry one; a `"(PRE-ORDER)"` string turned out to live in a single product's `sku` field, not confirmed as a stable convention across the catalog. Decision: Rev HQ gets no pre-order override — it just uses the plain `available == true` filter, accepting that a legitimately-purchasable pre-order could be excluded if its variant shows `available: false`.

---

## Decisions

- **Row granularity:** one row per in-stock **vinyl variant**, not one row per product. On Nuclear Blast and Rev HQ, a product with several in-stock vinyl variants produces that many rows. On Century Media and Epitaph this is moot in practice — every product has exactly one variant — but the same one-row-per-variant model applies uniformly.
- **Format filter is site-specific, not shared.** Nuclear Blast and Rev HQ: only variants whose title matches a vinyl-detecting regex are considered (Rev HQ's is wider, to also catch bare inch sizes like `"7\""` — see the Rev HQ technical grounding above); CD variants are ignored even though they appear in the same product JSON. Century Media and Epitaph: no per-variant filter at all — their `/collections/vinyl` endpoints never mix formats within a product, and their variant titles carry no format wording to match against anyway.
- **Shared vs. per-site logic:** pagination, pre-order-tag detection, cover-image resolution, and vendor-prefix stripping are identical in shape across all four sites (where applicable — Rev HQ doesn't use the pre-order or vendor-prefix helpers at all, since neither concept applies there) and live in one shared module, `backend/shopify_catalog.py`. Which variants to include, how the artist is determined, and how the display title is assembled differ enough between sites that each crawler keeps its own logic for those things — forcing them into the shared module would mean the module encodes assumptions that are only true for one site (Rev HQ's vendor-is-the-label quirk is the clearest example of why).
- **Placement of the shared module matters.** Crawler plugin files are copied into the user's data directory and loaded via `importlib.util.spec_from_file_location` from an arbitrary path — they are never members of a real `crawlers` Python package. A shared helper module placed *inside* `backend/crawlers/` would itself get matched by the startup bootstrap's `glob("*.py")` and mis-registered as a bogus crawler (it has no `Crawler` class). The existing crawlers already establish the right pattern: `amazon.py`/`ebay.py` import from a top-level `backend/crawler.py`, not from anything inside `backend/crawlers/`. `shopify_catalog.py` follows that same pattern, living at the top level of `backend/`, alongside `crawler.py`.
- **Title display:** the variant name is appended to the album title in a single `title` field — `"The Great Satan — Ghostly Black Vinyl"` — rather than a separate column.
- **Column parity with the Collection tab:** the Store tab mirrors as much of `RecordBrowser`'s layout as the data supports — a cover thumbnail and a Format column, in addition to Artist/Title/Price/Source. Year and Label aren't available from either source's data and are skipped. Format is a constant `"Vinyl"` for every row today (since non-vinyl variants are filtered out at crawl time), but storing it explicitly means a future non-vinyl catalog source doesn't require a schema change. Columns, left to right: thumbnail, Artist, Title, Format, Price (hyperlink), Source. Sortable: Artist, Title, Format, Price.
- **Full UI parity, not just columns:** Store also gets the artist sidebar (filter by artist, scoped to whatever's currently in `stock_items`), the search bar, and the list/tile view toggle — the same three UI elements `RecordBrowser` gives Collection and Wishlist. This needs a new `GET /api/stock/artists` endpoint (mirrors `GET /api/artists?scope=...`) and an `artist` filter param on `GET /api/stock` (mirrors `GET /api/releases?artist=...`). Tile view shows the cover image with artist/title below, linking out to the item's product page (`item.url`, whichever source it came from) instead of a Discogs release page. View-mode preference persists to `localStorage` under `collectionViewMode_instock`, following the same key pattern `RecordBrowser` uses per scope.
- **Implementation: a separate component, not a third `RecordBrowser` scope.** `StockBrowser` duplicates `RecordBrowser`'s sidebar/search/tile/list/pagination *shell* (same markup and classes, for visual consistency) rather than being folded into `RecordBrowser` via a third scope value. `RecordBrowser` is deeply Discogs-shaped (`discogs_id`, `discogs_url`, `year`/`label`/`discogs_price`, a `listings` map keyed by crawler, a per-row "refresh this release" button) and none of that generalizes to a flat `StockItem` row. Forcing both through one component would mean branching most of its body on scope; two focused components sharing a visual pattern is simpler than one component with two data shapes wired through it.
- **Cover image:** Shopify's `products.json` exposes a per-variant `featured_image.src` (the color-specific shot, e.g. the black-vinyl photo vs. the marble-vinyl photo) and a product-level `images[0].src` fallback. The crawler uses the variant's `featured_image` when present, else the product's first image, else `null`.
- **Stale items:** each sync run fully replaces that crawler's rows (delete all `stock_items` for the `crawler_id`, insert the fresh set) — same pattern as `delete_listings_for_release` before a per-release re-crawl. No "last seen" flagging; sold-out/removed items simply disappear.
- **Trigger:** manual "Refresh Stock Now" button *and* a cron schedule field (`stock_schedule`), matching the existing `crawl_schedule`/`collection_schedule` pattern. No schedule "mode" toggle is needed — there's only one mode (full rescan).
- **Settings UI:** a separate "Store Crawlers" section, visually parallel to the existing "Crawlers" section (site name, last run, enable/disable), rather than merging into the same table. Enabling/disabling a catalog crawler has no effect on the per-release price crawl and vice versa.
- **Owned-item cross-reference (added later, branch `store-tab-overlapping-filter`):** a filter dropdown sits left of the list/tile toggle, listing its three options in lexicographic order: a selectable "All" (the default, and how the user turns the filter back off), a selectable "Overlapping", and a disabled "Recommended" placeholder. Selecting Overlapping filters `stock_items` to rows whose artist matches (case-insensitive) an artist in the collection (`releases` where `in_collection = 1`), via a `LOWER(...) IN (SELECT LOWER(artist) ...)` subquery so the filter is enforced server-side and pagination/totals stay correct. No new table or join column — a query-time filter only. The artist sidebar (`GET /api/stock/artists`) takes the same `overlapping` flag and refetches whenever the dropdown changes, so the sidebar only lists artists that actually have matching rows under the active filter. The search box ANDs with whichever filter is active (both conditions are appended to the same `WHERE` clause), so search never bypasses Overlapping back to the full catalog. The chosen filter persists to `localStorage` under `stockFilter`, following the same pattern as `collectionViewMode_instock`.
- **Pre-order handling is per-site, and one site gets none.** Nuclear Blast/Century Media/Epitaph all tag pre-order products in their `tags` array (spelled `"pre-order"`, `"preorder"`, `"pre-order"` respectively — confirmed via direct fetch), but individual variant `available` flags on a pre-order product are inconsistent — some variants show `available: true`, others `false`, even though the whole release is purchasable. For any product carrying that site's pre-order tag, all of its vinyl variants are included regardless of `available`, and the title gets a `" (Pre-Order)"` suffix. Rev HQ has no confirmed structured pre-order signal at all (see its technical grounding above), so it gets no override — just the plain `available == true` filter, with the accepted gap that a genuine Rev HQ pre-order could be excluded.
- **Future direction (not built now):** a filtered view showing only items "related to" the existing collection, inferred via a Claude API call (new API key field in Settings). The schema below doesn't need rework to support this later — it would be an additive column or join, not a redesign.
- **Future direction (not built now): a config-driven generic Shopify crawler**, so a user could add a new Shopify-backed store from Settings — paste a URL, the app validates it's Shopify-backed (does `{url}/collections/{slug}/products.json` return a `products` array?) — without writing a new `.py` file. This is deliberately deferred rather than built now, but worth designing together later: across the four sites here we already found **three incompatible shapes** (Nuclear Blast/Rev HQ need per-variant format filtering that would break Century Media/Epitaph, whose variant titles carry no format wording to filter on at all; Rev HQ's `vendor` field is the record label, not the artist — nothing in the JSON response itself flags that it's wrong). That means "paste a URL and the app figures out the rest" can't be fully automatic — a URL-validation step can confidently prove "this is Shopify," but not "this is shaped like Nuclear Blast." The realistic version is a small structured config per store (`collection_slug`, `preorder_tag`, `artist_source`: `vendor` vs. title-regex, `variant_filter`: none vs. regex) plus a Settings preview step showing a few parsed items so a human can pick/confirm the shape before saving — turning each of today's four crawler `.py` files into a config row against one generic engine. The shared helpers in `shopify_catalog.py` are already pure functions parameterized by exactly these kinds of values (`base_url`, `collection_slug`, a `tag` string), so no rework is needed there when this gets built — the four crawlers written now are a working reference for what the config schema needs to express.

---

## Data model

```sql
ALTER TABLE crawlers ADD COLUMN crawler_type TEXT NOT NULL DEFAULT 'release';

CREATE TABLE stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,       -- "Album Title — Variant Name"
    format TEXT,               -- "Vinyl" for every row today; explicit column so a future
                                -- non-vinyl catalog source doesn't need a schema change
    price REAL,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- `crawler_id` reuses the existing `crawlers` table — "Source" in the UI is `crawlers.site_name` joined in, same pattern as `listings.crawler_id`.
- Existing crawlers (Amazon, eBay) get `crawler_type = 'release'` via the `ALTER TABLE` default; no changes to those files.
- Nuclear Blast, Century Media, Epitaph, and Rev HQ all register with `crawler_type = 'catalog'`.

---

## Crawler plugin interface (catalog kind)

A second, parallel interface alongside the existing `search(release, page)` contract:

```python
class Crawler:
    site_name: str
    base_url: str
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        # yields {"artist": str, "title": str, "format": str|None, "price": float|None,
        #         "currency": str|None, "url": str, "cover_image_url": str|None}
```

No `Page` argument — catalog crawlers don't use the shared Playwright browser at all.

### `backend/shopify_catalog.py` (shared)

```python
async def iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]:
    ...  # paginates GET {base_url}/collections/{collection_slug}/products.json?limit=250&page=N
         # until an empty "products" array; raises on non-2xx; ~1s delay between pages

def has_tag(product: dict, tag: str) -> bool: ...       # case-insensitive tags membership
def strip_vendor_prefix(title: str, vendor: str) -> str: ...  # strips "{vendor} - " if present, else unchanged
def resolve_cover_image(product: dict, variant: dict) -> Optional[str]: ...  # variant.featured_image.src, else product.images[0].src, else None
```

Fixed ~1s delay between page requests (polite default, not configurable). No `BotDetectedError` — a non-2xx response is just a raised `httpx.HTTPError`, caught by the sync loop and reported as `stock_sync_error`, matching how `_sync_collection` handles an invalid Discogs token.

### `backend/crawlers/nuclearblast.py`

- Uses `iter_products(base_url, "vinyl")` for pagination.
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` to get the clean album title.
- For each variant: skip unless the variant `title` matches `\bvinyl\b|\blp\b`, and (either `available` is true, or `has_tag(product, "pre-order")`). Pre-order items get `" (Pre-Order)"` appended to the title. `format` is always `"Vinyl"` (every yielded variant already passed the vinyl regex). `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{album_title} — {variant_title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/centurymedia.py`

- Uses `iter_products(base_url, "vinyl")` for pagination — same helper, different base URL.
- For each product: `vendor` → artist; `strip_vendor_prefix(title, vendor)` to get the display title (used as-is, no variant name appended — see "Century Media" technical grounding above for why).
- For each variant: skip unless (`available` is true, or `has_tag(product, "preorder")` — note the different tag spelling). No format regex — the collection is already vinyl-only. `format` is always `"Vinyl"`. `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/epitaph.py`

- Same shape as `centurymedia.py`, different constants: `has_tag(product, "pre-order")` (Nuclear Blast's spelling, not Century Media's), and `strip_vendor_prefix` no-ops here since Epitaph titles never carry a vendor prefix. Yields `{"artist", "title": f"{title}[ (Pre-Order)]", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

### `backend/crawlers/revhq.py`

- Uses `iter_products(base_url, "vinyl")` and `resolve_cover_image` from the shared module; does **not** use `has_tag` or `strip_vendor_prefix` — neither pre-order tagging nor vendor-prefix stripping applies to this site.
- For each product: parses `artist`/`album_title` from the title via `^(?P<artist>.+?)\s*"(?P<album>.+)"\s*$`, falling back to the raw `vendor` (the label) and full title if a title doesn't match — this never happened in the sampled catalog, but the fallback avoids crashing or leaving the artist blank rather than assuming perfect coverage.
- For each variant: skip unless `available` is true (no pre-order override — see the Rev HQ technical grounding above) and the variant title matches `\bvinyl\b|\blp\b|\d+\s*"` (wider than Nuclear Blast's regex, to catch bare inch sizes). `format` is always `"Vinyl"`. `cover_image_url` via `resolve_cover_image(product, variant)`. Yields `{"artist", "title": f"{album_title} — {variant_title}", "format": "Vinyl", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}", "cover_image_url"}`.

---

## Backend orchestration

- `CrawlManager` gains `stock_sync_running` / `start_stock_sync()` / `_sync_stock()`, modeled directly on the existing `sync_running` / `start_sync()` / `_sync_collection()` in [`backend/crawl_manager.py`](../../../backend/crawl_manager.py).
- `_sync_stock()` loads **all enabled catalog crawlers** (all four sites, plus any future one — the loop is data-driven off the `crawlers` table, not hard-coded), and for each: calls `crawl_catalog()`, replaces that crawler's `stock_items` rows, and broadcasts progress.
- New SSE events on the existing `/api/crawl/stream` channel (no new stream): `stock_sync_started`, `stock_sync_progress` (`{synced, source}`), `stock_sync_complete` (`{synced}`), `stock_sync_error` (`{error}`).
- `db.py` additions: `replace_stock_items(conn, crawler_id, items)` (delete-then-insert in one transaction), `get_stock_items(conn, search=None, sort="artist", order="asc", page=1, per_page=50)` — sortable by `artist`, `title`, `format`, or `price`.
- `main.py`'s `seed_bundled_crawlers` reads `crawler_type` from the module file the same way it already reads `site_name` (regex on the class body), defaulting to `"release"` when absent — so `amazon.py`/`ebay.py` need no changes.

## API

- `GET /api/stock` — search/sort/paginate `stock_items` joined to `crawlers.site_name` as `source`, plus an `artist` filter and an `overlapping` boolean filter (restricts to artists also present in the collection, case-insensitive). Mirrors `get_releases`'s shape (search across artist/title, filter by artist, sort by artist/title/format/price, paginated).
- `GET /api/stock/artists` — distinct artists currently in `stock_items`, for the sidebar, plus the same `overlapping` boolean filter as `GET /api/stock`. Mirrors `GET /api/artists`.
- `POST /api/stock/sync/start` — triggers `crawl_manager.start_stock_sync()`.
- `routers/settings.py`: `SettingsUpdate`/`get_settings`/`update_settings` gain `stock_schedule: str = ""`, wired through a new `scheduler.configure_stock(...)` (mirrors `scheduler.configure_sync`).

## Frontend

- `App.tsx`: `View` union gains `'instock'`; new nav button "Store" (originally "In Stock") next to Wishlist. SSE handler gains cases for `stock_sync_started/progress/complete/error`, reusing the existing bottom status bar (`syncMessage`/`syncing`) rather than a new UI element.
- New `frontend/src/views/StockBrowser.tsx` — a separate component (see "Implementation" decision above) that mirrors `RecordBrowser`'s full shell: artist sidebar, search bar, a filter dropdown (Recommended/Overlapping, see Decisions above) left of the list/tile view toggle, sortable table (**thumbnail | Artist | Title | Format | Price (hyperlink to `url`) | Source**), and the same pagination pattern. No per-item price refresh and no collection/wishlist actions, since those don't apply to a catalog browse view.
- `Settings.tsx`: new "Store Crawlers" section (originally "Catalog Crawlers") — a table (site name, last run, enable/disable toggle) parallel to the existing "Crawlers" section, plus a `stock_schedule` cron input and a "Refresh Stock Now" button, following the exact layout of the existing "Crawler Management" section.

---

## Out of scope

- AI-based relevance filtering ("Claude, suggest what I might like from what's in stock") — noted as a likely future addition; the schema doesn't preclude it.
- Non-vinyl formats (CD, cassette, boxset) anywhere in the pipeline.
- A fifth catalog source beyond Nuclear Blast, Century Media, Epitaph, and Rev HQ (the orchestration loop and `shopify_catalog.py` support it structurally, but no fifth crawler is being written now).
- A Century Media or Epitaph product with more than one variant (none exist in either live catalog today); if one appeared, both variants would render with an identical title since the color is baked into the product title rather than the variant name.
- A pre-order override for Rev HQ (no reliable structured signal was found); a legitimately-purchasable Rev HQ pre-order could be excluded if its variant shows `available: false`.

## Success criteria

- "Refresh Stock Now" populates the Store tab with in-stock vinyl variants from all four sources, each priced and linked to its product page, with the correct source shown per row.
- Rev HQ rows show the actual band as Artist, not the record label from `vendor`.
- Re-running the sync after a variant sells out removes it from the tab (per source — each crawler's rows are replaced independently).
- Disabling any catalog crawler in Settings has no effect on the existing per-release price crawl, and vice versa; disabling one catalog crawler has no effect on another's rows.
- A cron expression in the new `stock_schedule` field triggers an unattended stock sync covering all enabled catalog crawlers.
- Selecting "Overlapping" in the Store tab's filter dropdown shows only rows whose artist matches (case-insensitively) an artist already in the collection; totals and pagination reflect the filtered count, not the unfiltered one.
- The artist sidebar under "Overlapping" lists only artists with at least one row in the filtered results — no dead entries that would filter down to zero items.
- Selecting "All" after "Overlapping" turns the filter back off, returning to the unfiltered catalog.
- Typing in the search box while "Overlapping" is active narrows within the overlapping set rather than replacing it.
- Reloading the Store tab (or navigating away and back) keeps whichever filter was last selected.
