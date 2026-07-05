# In Stock Crawler — Design

**Date:** 2026-07-05
**Status:** Ready for planning
**Branch:** `dev-instock-crawler`

---

## Problem

The existing crawler system answers "what does site X charge for release Y in my collection?" — a per-release search driven by Playwright. There's a different question worth answering: "what's currently for sale at site X, regardless of whether I already own it?" Nuclear Blast (`shop.nuclearblast.com/collections/vinyl`) is the first source: a full catalog of in-stock vinyl, browsable independently of the user's Discogs collection/wishlist.

## Goal

Add a "catalog crawler" — a second, parallel crawler kind that scans an entire site's in-stock catalog (rather than searching per-release) and stores the results in a new `stock_items` table, surfaced in a new **In Stock** tab (named for the concept, not the site, since more catalog sources are expected later).

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

---

## Decisions

- **Row granularity:** one row per in-stock **vinyl variant**, not one row per product. A product with 3 in-stock vinyl colors produces 3 rows.
- **Format filter:** only variants whose title matches `\bvinyl\b|\blp\b` (case-insensitive) are considered. CD/cassette/boxset variants are ignored entirely, even though they appear in the same product JSON.
- **Title display:** the variant name is appended to the album title in a single `title` field — `"The Great Satan — Ghostly Black Vinyl"` — rather than a separate column. Keeps the tab at 4 columns: Artist, Title, Price, Source.
- **Stale items:** each sync run fully replaces that crawler's rows (delete all `stock_items` for the `crawler_id`, insert the fresh set) — same pattern as `delete_listings_for_release` before a per-release re-crawl. No "last seen" flagging; sold-out/removed items simply disappear.
- **Trigger:** manual "Refresh Stock Now" button *and* a cron schedule field (`stock_schedule`), matching the existing `crawl_schedule`/`collection_schedule` pattern. No schedule "mode" toggle is needed — there's only one mode (full rescan).
- **Settings UI:** a separate "Catalog Crawlers" section, visually parallel to the existing "Crawlers" section (site name, last run, enable/disable), rather than merging into the same table. Enabling/disabling a catalog crawler has no effect on the per-release price crawl and vice versa.
- **Owned-item cross-reference:** explicitly out of scope. In Stock is a plain browse/discovery list.
- **Future direction (not built now):** a filtered view showing only items "related to" the existing collection, inferred via a Claude API call (new API key field in Settings). The schema below doesn't need rework to support this later — it would be an additive column or join, not a redesign.

---

## Data model

```sql
ALTER TABLE crawlers ADD COLUMN crawler_type TEXT NOT NULL DEFAULT 'release';

CREATE TABLE stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,       -- "Album Title — Variant Name"
    price REAL,
    currency TEXT,
    url TEXT NOT NULL,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- `crawler_id` reuses the existing `crawlers` table — "Source" in the UI is `crawlers.site_name` joined in, same pattern as `listings.crawler_id`.
- Existing crawlers (Amazon, eBay) get `crawler_type = 'release'` via the `ALTER TABLE` default; no changes to those files.
- Nuclear Blast registers with `crawler_type = 'catalog'`.

---

## Crawler plugin interface (catalog kind)

A second, parallel interface alongside the existing `search(release, page)` contract:

```python
class Crawler:
    site_name: str
    base_url: str
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        # yields {"artist": str, "title": str, "price": float|None,
        #         "currency": str|None, "url": str}
```

No `Page` argument — catalog crawlers don't use the shared Playwright browser at all.

### `backend/crawlers/nuclearblast.py`

- Paginates `GET /collections/vinyl/products.json?limit=250&page=N` until an empty `products` array is returned.
- For each product: `vendor` → artist; strip the `"{vendor} - "` prefix from `title` if present to get the clean album title.
- For each variant: skip unless `available` is true and the variant `title` matches the vinyl regex; yield `{"artist", "title": f"{album_title} — {variant_title}", "price": float(variant["price"]), "currency": "USD", "url": f"{base_url}/products/{handle}"}`.
- Fixed ~1s delay between page requests (polite default, not configurable — only 3 requests total).
- No `BotDetectedError` — a non-2xx response is just a raised `httpx.HTTPError`, caught by the sync loop and reported as `stock_sync_error`, matching how `_sync_collection` handles an invalid Discogs token.

---

## Backend orchestration

- `CrawlManager` gains `stock_sync_running` / `start_stock_sync()` / `_sync_stock()`, modeled directly on the existing `sync_running` / `start_sync()` / `_sync_collection()` in [`backend/crawl_manager.py`](../../../backend/crawl_manager.py).
- `_sync_stock()` loads **all enabled catalog crawlers** (not just Nuclear Blast — future sites join this loop automatically), and for each: calls `crawl_catalog()`, replaces that crawler's `stock_items` rows, and broadcasts progress.
- New SSE events on the existing `/api/crawl/stream` channel (no new stream): `stock_sync_started`, `stock_sync_progress` (`{synced, source}`), `stock_sync_complete` (`{synced}`), `stock_sync_error` (`{error}`).
- `db.py` additions: `get_catalog_crawlers(conn)`, `replace_stock_items(conn, crawler_id, items)` (delete-then-insert in one transaction), `get_stock_items(conn, search=None, sort="artist", order="asc", page=1, per_page=50)`.
- `main.py`'s `seed_bundled_crawlers` reads `crawler_type` from the module file the same way it already reads `site_name` (regex on the class body), defaulting to `"release"` when absent — so `amazon.py`/`ebay.py` need no changes.

## API

- `GET /api/stock` — search/sort/paginate `stock_items` joined to `crawlers.site_name` as `source`. Mirrors `get_releases`'s shape (search across artist/title, sort by artist/title/price, paginated).
- `POST /api/stock/sync/start` — triggers `crawl_manager.start_stock_sync()`.
- `routers/settings.py`: `SettingsUpdate`/`get_settings`/`update_settings` gain `stock_schedule: str = ""`, wired through a new `scheduler.configure_stock(...)` (mirrors `scheduler.configure_sync`).

## Frontend

- `App.tsx`: `View` union gains `'instock'`; new nav button "In Stock" next to Wishlist. SSE handler gains cases for `stock_sync_started/progress/complete/error`, reusing the existing bottom status bar (`syncMessage`/`syncing`) rather than a new UI element.
- New `frontend/src/views/StockBrowser.tsx` — not a reuse of `RecordBrowser` (no cover art, no per-item price refresh, no collection/wishlist actions): a table with **Artist | Title | Price (hyperlink to `url`) | Source**, search box, sortable columns, same pagination pattern as `RecordBrowser`.
- `Settings.tsx`: new "Catalog Crawlers" section — a table (site name, last run, enable/disable toggle) parallel to the existing "Crawlers" section, plus a `stock_schedule` cron input and a "Refresh Stock Now" button, following the exact layout of the existing "Crawler Management" section.

---

## Out of scope

- Cross-referencing In Stock items against the existing collection/wishlist (flagging "already owned").
- AI-based relevance filtering ("Claude, suggest what I might like from what's in stock") — noted as a likely future addition; the schema doesn't preclude it.
- Non-vinyl formats (CD, cassette, boxset) anywhere in the pipeline.
- Any second catalog source beyond Nuclear Blast (the orchestration loop supports it, but no second crawler is being written now).

## Success criteria

- "Refresh Stock Now" populates the In Stock tab with in-stock Nuclear Blast vinyl variants, each priced and linked to its product page.
- Re-running the sync after a variant sells out removes it from the tab.
- Disabling the Nuclear Blast catalog crawler in Settings has no effect on the existing per-release price crawl, and vice versa.
- A cron expression in the new `stock_schedule` field triggers an unattended stock sync.
