# In Stock Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "catalog crawler" kind that scans an entire site's in-stock vinyl catalog (via Shopify's public `/products.json` API) and surfaces the results in a new **In Stock** tab, fully decoupled from the existing per-release price-crawler machinery. Four sources ship together: Nuclear Blast, Century Media, Epitaph, and Rev HQ — each a Shopify storefront, but no two shaped quite the same way (see Tasks 5-8).

**Architecture:** A second crawler plugin interface (`crawl_catalog()`, no `Page`) sits alongside the existing `search(release, page)` interface, both registered in the same `crawlers` table via a new `crawler_type` column. Shared Shopify pagination/tag/image/title-cleanup helpers live in a new top-level `backend/shopify_catalog.py` (not inside `backend/crawlers/` — see Task 4 for why); each site's own crawler owns its variant-selection and title-assembly logic, since those differ meaningfully between sites. `CrawlManager` gains a `_sync_stock()` method modeled on the existing `_sync_collection()` (paginate → upsert → broadcast), not on the per-release `crawl_releases()` loop, and already loops over *all* enabled catalog crawlers, so both sites (and any future one) share one sync run. Results land in a new `stock_items` table, exposed via `GET /api/stock`, and rendered in a new `StockBrowser` frontend component.

**Tech Stack:** FastAPI + SQLite (backend), httpx (pure HTTP crawler, no Playwright), React + TypeScript + Vite (frontend), pytest + respx (backend tests), vitest + @testing-library/react (frontend tests).

**Spec:** [`docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`](../specs/2026-07-05-in-stock-crawler-design.md)

---

## Task 1: Data model — `crawler_type` column + `stock_items` table

**Files:**
- Modify: `backend/db.py:6-59` (SCHEMA), `backend/db.py:76-98` (init_db migration)
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py` (near the other schema tests, after `test_init_db_creates_tables`):

```python
def test_init_db_creates_stock_items_table(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "stock_items" in tables


def test_stock_items_table_has_expected_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_items)").fetchall()}
    assert {"crawler_id", "artist", "title", "format", "price", "currency", "url", "cover_image_url", "last_seen"} <= cols


def test_new_crawlers_default_to_release_type(conn):
    register_crawler(conn, "Amazon", "/path/amazon.py")
    row = conn.execute("SELECT crawler_type FROM crawlers WHERE site_name='Amazon'").fetchone()
    assert row[0] == "release"


def test_register_crawler_accepts_catalog_type(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    row = conn.execute("SELECT crawler_type FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()
    assert row[0] == "catalog"


def test_migration_backfills_crawler_type_for_legacy_rows():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("""
        CREATE TABLE crawlers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL UNIQUE,
            module_path TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            last_run TIMESTAMP
        )
    """)
    c.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Amazon', '/path/amazon.py')")
    c.commit()
    init_db(c)
    row = c.execute("SELECT crawler_type FROM crawlers WHERE site_name='Amazon'").fetchone()
    assert row[0] == "release"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "stock_items_table or default_to_release or accepts_catalog or backfills_crawler_type" -v`
Expected: FAIL — `stock_items` table doesn't exist; `register_crawler()` doesn't accept `crawler_type`; no `crawler_type` column.

- [ ] **Step 3: Add the table, column, and migration**

In `backend/db.py`, modify the `crawlers` table definition inside `SCHEMA` (around line 23-29):

```python
CREATE TABLE IF NOT EXISTS crawlers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
    crawler_type TEXT NOT NULL DEFAULT 'release',
    enabled BOOLEAN NOT NULL DEFAULT 1,
    last_run TIMESTAMP
);
```

Add a new table to `SCHEMA`, right after the `listings` table definition:

```python
CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    price REAL,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

In `init_db()`, add a migration check alongside the existing `releases` column checks (after the `in_wishlist` check, before the CC Music rename migration):

```python
    crawler_cols = {row[1] for row in conn.execute("PRAGMA table_info(crawlers)").fetchall()}
    if "crawler_type" not in crawler_cols:
        conn.execute("ALTER TABLE crawlers ADD COLUMN crawler_type TEXT NOT NULL DEFAULT 'release'")
```

Update `register_crawler()` (currently lines 282-288) to accept and persist the type:

```python
def register_crawler(conn: sqlite3.Connection, site_name: str, module_path: str, crawler_type: str = "release"):
    conn.execute("""
        INSERT INTO crawlers (site_name, module_path, crawler_type, enabled)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(site_name) DO UPDATE SET module_path=excluded.module_path, crawler_type=excluded.crawler_type
    """, [site_name, module_path, crawler_type])
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -v`
Expected: PASS (all, including pre-existing tests — `register_crawler`'s new parameter is optional so existing call sites are unaffected)

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "dev-instock-crawler: add crawler_type column and stock_items table"
```

---

## Task 2: `get_enabled_crawlers` type filter

**Files:**
- Modify: `backend/db.py:258-260`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py`:

```python
def test_get_enabled_crawlers_defaults_to_release_type(conn):
    register_crawler(conn, "Amazon", "/path/amazon.py", crawler_type="release")
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    result = get_enabled_crawlers(conn)
    assert [c["site_name"] for c in result] == ["Amazon"]


def test_get_enabled_crawlers_catalog_type(conn):
    register_crawler(conn, "Amazon", "/path/amazon.py", crawler_type="release")
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    result = get_enabled_crawlers(conn, crawler_type="catalog")
    assert [c["site_name"] for c in result] == ["Nuclear Blast"]


def test_get_enabled_crawlers_excludes_disabled(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    set_crawler_enabled(conn, crawler_id, False)
    result = get_enabled_crawlers(conn, crawler_type="catalog")
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "get_enabled_crawlers" -v`
Expected: FAIL — `get_enabled_crawlers()` doesn't filter by type yet, so the release-type test returns both crawlers.

- [ ] **Step 3: Add the filter**

Replace `get_enabled_crawlers` in `backend/db.py`:

```python
def get_enabled_crawlers(conn: sqlite3.Connection, crawler_type: str = "release") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM crawlers WHERE enabled = 1 AND crawler_type = ?", [crawler_type]
    ).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py backend/tests/test_crawl_manager.py -v`
Expected: PASS (the default `crawler_type="release"` keeps the existing price-crawl call site in `crawl_manager.py`'s `_run()` — which calls `get_enabled_crawlers(conn)` with no argument — working exactly as before)

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "dev-instock-crawler: filter get_enabled_crawlers by crawler_type"
```

---

## Task 3: `replace_stock_items` / `get_stock_items`

**Files:**
- Modify: `backend/db.py` (add functions near `get_listings_for_release`)
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_db.py`:

```python
@pytest.fixture
def conn_with_catalog_crawler(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nuclearblast.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    return conn, crawler_id


def test_replace_stock_items_inserts_rows(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    items = [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl",
         "price": 31.99, "currency": "USD", "url": "https://shop.nuclearblast.com/products/rob-zombie",
         "cover_image_url": "https://cdn.shopify.com/rz.png"},
    ]
    replace_stock_items(conn, crawler_id, items)
    rows = conn.execute("SELECT artist, title, format, price, cover_image_url FROM stock_items WHERE crawler_id = ?", [crawler_id]).fetchall()
    assert len(rows) == 1
    assert rows[0]["artist"] == "Rob Zombie"
    assert rows[0]["format"] == "Vinyl"
    assert rows[0]["price"] == 31.99
    assert rows[0]["cover_image_url"] == "https://cdn.shopify.com/rz.png"


def test_replace_stock_items_handles_missing_cover_image(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 10.0, "currency": "USD", "url": "https://x/1"},
    ])
    row = conn.execute("SELECT cover_image_url FROM stock_items WHERE crawler_id = ?", [crawler_id]).fetchone()
    assert row["cover_image_url"] is None


def test_replace_stock_items_clears_previous_rows(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 10.0, "currency": "USD", "url": "https://x/1"},
    ])
    replace_stock_items(conn, crawler_id, [
        {"artist": "B", "title": "T2", "format": "Vinyl", "price": 20.0, "currency": "USD", "url": "https://x/2"},
    ])
    rows = conn.execute("SELECT artist FROM stock_items WHERE crawler_id = ?", [crawler_id]).fetchall()
    assert [r["artist"] for r in rows] == ["B"]


def test_replace_stock_items_only_clears_own_crawler(conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    register_crawler(conn, "Other Shop", "/path/other.py", crawler_type="catalog")
    nb_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    other_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Other Shop'").fetchone()[0]
    replace_stock_items(conn, nb_id, [{"artist": "A", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"}])
    replace_stock_items(conn, other_id, [{"artist": "B", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"}])
    replace_stock_items(conn, nb_id, [{"artist": "A2", "title": "T3", "format": "Vinyl", "price": 3.0, "currency": "USD", "url": "https://x/3"}])
    remaining = {r["artist"] for r in conn.execute("SELECT artist FROM stock_items").fetchall()}
    assert remaining == {"A2", "B"}


def test_get_stock_items_joins_source_name(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan — Ghostly Black Vinyl", "format": "Vinyl",
         "price": 31.99, "currency": "USD", "url": "https://x/1", "cover_image_url": "https://x/rz.png"},
    ])
    result = get_stock_items(conn)
    assert result["total"] == 1
    assert result["items"][0]["source"] == "Nuclear Blast"
    assert result["items"][0]["price"] == 31.99
    assert result["items"][0]["format"] == "Vinyl"
    assert result["items"][0]["cover_image_url"] == "https://x/rz.png"


def test_get_stock_items_search_filters_artist_and_title(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan", "format": "Vinyl", "price": 31.99, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "Every Bridge Burning", "format": "Vinyl", "price": 25.99, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, search="zombie")
    assert result["total"] == 1
    assert result["items"][0]["artist"] == "Rob Zombie"


def test_get_stock_items_sorts_by_price(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 30.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "B", "title": "T2", "format": "Vinyl", "price": 10.0, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, sort="price", order="asc")
    assert [i["artist"] for i in result["items"]] == ["B", "A"]


def test_get_stock_items_sorts_by_format(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "A", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "B", "title": "T2", "format": "Cassette", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, sort="format", order="asc")
    assert [i["artist"] for i in result["items"]] == ["B", "A"]


def test_get_stock_items_paginates(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": f"Artist {i}", "title": f"T{i}", "format": "Vinyl", "price": float(i), "currency": "USD", "url": f"https://x/{i}"}
        for i in range(5)
    ])
    result = get_stock_items(conn, page=1, per_page=2)
    assert result["total"] == 5
    assert len(result["items"]) == 2


def test_get_stock_items_filters_by_artist(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    result = get_stock_items(conn, artist="Rob Zombie")
    assert result["total"] == 1
    assert result["items"][0]["artist"] == "Rob Zombie"


def test_get_distinct_stock_artists(conn_with_catalog_crawler):
    conn, crawler_id = conn_with_catalog_crawler
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    assert get_distinct_stock_artists(conn) == ["NAILS", "Rob Zombie"]


def test_get_distinct_stock_artists_empty(conn):
    assert get_distinct_stock_artists(conn) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_db.py -k "stock_items and not stock_items_table" -v`
Expected: FAIL with `ImportError`/`NameError` — `replace_stock_items` and `get_stock_items` don't exist yet.

- [ ] **Step 3: Implement**

Add to `backend/db.py`, after `get_listings_for_release`:

```python
def replace_stock_items(conn: sqlite3.Connection, crawler_id: int, items: list[dict]):
    conn.execute("DELETE FROM stock_items WHERE crawler_id = ?", [crawler_id])
    conn.executemany("""
        INSERT INTO stock_items (crawler_id, artist, title, format, price, currency, url, cover_image_url, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, [
        (crawler_id, item["artist"], item["title"], item.get("format"), item.get("price"),
         item.get("currency"), item["url"], item.get("cover_image_url"))
        for item in items
    ])
    conn.commit()


def get_stock_items(
    conn: sqlite3.Connection,
    search: Optional[str] = None,
    artist: Optional[str] = None,
    sort: str = "artist",
    order: str = "asc",
    page: int = 1,
    per_page: int = 50,
) -> dict:
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    allowed_sort = {"artist", "title", "format", "price"}
    if sort not in allowed_sort:
        sort = "artist"

    conditions = []
    params: list = []
    if search:
        conditions.append("(s.artist LIKE ? OR s.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if artist:
        conditions.append("s.artist = ?")
        params.append(artist)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"SELECT COUNT(*) FROM stock_items s {where}", params).fetchone()[0]

    offset = (page - 1) * per_page
    null_order = "ASC" if order_sql == "ASC" else "DESC"
    order_clause = f"CASE WHEN s.{sort} IS NULL THEN 1 ELSE 0 END {null_order}, s.{sort} {order_sql}"
    rows = conn.execute(f"""
        SELECT s.id, s.artist, s.title, s.format, s.price, s.currency, s.url, s.cover_image_url, s.last_seen, c.site_name AS source
        FROM stock_items s
        JOIN crawlers c ON c.id = s.crawler_id
        {where}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()

    return {"total": total, "page": page, "per_page": per_page, "items": [dict(row) for row in rows]}


def get_distinct_stock_artists(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT artist FROM stock_items ORDER BY artist").fetchall()
    return [row[0] for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/tests/test_db.py
git commit -m "dev-instock-crawler: add replace_stock_items and get_stock_items"
```

---

## Task 4: Shared Shopify catalog helpers

Nuclear Blast and Century Media are both Shopify storefronts, but their catalogs are shaped differently: Nuclear Blast merges every format (vinyl colors, CD, cassette) as sibling variants on one product, so a per-variant vinyl-title regex is needed; Century Media's `/collections/vinyl` already returns vinyl-only products with a single variant each, and the variant title is just a color name (e.g. `"Blue EcoMix"`) with no format wording at all — a regex filter would find nothing. Their pre-order tags differ too (`"pre-order"` vs `"preorder"`), and Century Media's product titles don't always start with an exact `vendor` prefix (e.g. a two-artist collab credited to only one vendor). So: pagination, pre-order tag detection, image resolution, and vendor-prefix stripping are genuinely identical shape across both sites and belong in one shared module. Which variants to include and how to assemble the display title are genuinely different per site and stay in each site's own crawler file.

**Important placement note:** crawler plugin files aren't loaded as members of a `crawlers` Python package — `backend/main.py`'s `seed_bundled_crawlers` copies each `.py` file under `backend/crawlers/` into the user's data directory and `crawler.py`'s `load_crawler_from_path` loads it via `importlib.util.spec_from_file_location` from that arbitrary path. Two consequences: (1) a shared helper module placed *inside* `backend/crawlers/` would itself get matched by `seed_bundled_crawlers`'s `glob("*.py")` and mis-registered as a bogus crawler (it has no `Crawler` class). (2) The existing crawlers already establish the right pattern — `amazon.py`/`ebay.py` do `from crawler import BotDetectedError, clean_search_text, ...`, reaching a top-level module in `backend/`, not something inside `backend/crawlers/`. So the shared helpers go in a new top-level module, `backend/shopify_catalog.py`, sitting alongside `crawler.py` — never inside `backend/crawlers/`.

**Files:**
- Create: `backend/shopify_catalog.py`
- Test: `backend/tests/test_shopify_catalog.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_shopify_catalog.py`:

```python
import httpx
import respx
import pytest
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PRODUCTS_URL = "https://example.myshopify.test/collections/vinyl/products.json"


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@respx.mock
async def test_iter_products_yields_each_product_across_pages():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([{"id": 1}, {"id": 2}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([{"id": 3}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1, 2, 3]


@respx.mock
async def test_iter_products_stops_on_first_empty_page():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert products == []


@respx.mock
async def test_iter_products_raises_on_http_error():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [p async for p in iter_products("https://example.myshopify.test", "vinyl")]


def test_has_tag_matches_case_insensitively():
    assert has_tag({"tags": ["Pre-Order", "vinyl"]}, "pre-order") is True


def test_has_tag_false_when_absent():
    assert has_tag({"tags": ["vinyl"]}, "pre-order") is False


def test_has_tag_false_when_tags_missing():
    assert has_tag({}, "pre-order") is False


def test_strip_vendor_prefix_removes_matching_prefix():
    assert strip_vendor_prefix("NAILS - Every Bridge Burning", "NAILS") == "Every Bridge Burning"


def test_strip_vendor_prefix_keeps_title_when_no_match():
    assert strip_vendor_prefix(
        "Hackett & Rothery - The Roaring Waves - LP", "Steve Hackett"
    ) == "Hackett & Rothery - The Roaring Waves - LP"


def test_resolve_cover_image_prefers_variant_featured_image():
    product = {"images": [{"src": "https://x/fallback.png"}]}
    variant = {"featured_image": {"src": "https://x/variant.png"}}
    assert resolve_cover_image(product, variant) == "https://x/variant.png"


def test_resolve_cover_image_falls_back_to_product_image():
    product = {"images": [{"src": "https://x/fallback.png"}]}
    variant = {}
    assert resolve_cover_image(product, variant) == "https://x/fallback.png"


def test_resolve_cover_image_none_when_neither_present():
    assert resolve_cover_image({"images": []}, {}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_shopify_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shopify_catalog'`

- [ ] **Step 3: Implement the shared helpers**

Create `backend/shopify_catalog.py`:

```python
import asyncio
from typing import AsyncIterator, Optional
import httpx

_PAGE_LIMIT = 250
_PAGE_DELAY_SECONDS = 1.0


async def iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]:
    """Paginate a Shopify collection's public products.json endpoint until exhausted."""
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            url = f"{base_url}/collections/{collection_slug}/products.json"
            r = await client.get(url, params={"limit": _PAGE_LIMIT, "page": page})
            r.raise_for_status()
            products = r.json().get("products", [])
            if not products:
                break
            for product in products:
                yield product
            page += 1
            await asyncio.sleep(_PAGE_DELAY_SECONDS)


def has_tag(product: dict, tag: str) -> bool:
    """Case-insensitive membership check against a Shopify product's tags array."""
    needle = tag.strip().lower()
    return any((t or "").strip().lower() == needle for t in product.get("tags", []))


def strip_vendor_prefix(title: str, vendor: str) -> str:
    """Strip a leading "{vendor} - " from a product title, if present; otherwise return it unchanged."""
    vendor = (vendor or "").strip()
    prefix = f"{vendor} - "
    if vendor and title.startswith(prefix):
        return title[len(prefix):]
    return title


def resolve_cover_image(product: dict, variant: dict) -> Optional[str]:
    """Prefer the variant's own image (e.g. a specific vinyl color), falling back to the product's first image."""
    featured = variant.get("featured_image") or {}
    if featured.get("src"):
        return featured["src"]
    images = product.get("images") or []
    return images[0].get("src") if images else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_shopify_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/shopify_catalog.py backend/tests/test_shopify_catalog.py
git commit -m "dev-instock-crawler: add shared Shopify catalog helpers"
```

---

## Task 5: Nuclear Blast catalog crawler

**Files:**
- Create: `backend/crawlers/nuclearblast.py`
- Test: `backend/tests/test_nuclearblast_crawler.py`

Nuclear Blast tags pre-order products with `"pre-order"` in the product's `tags` array (confirmed by direct inspection of the live `products.json` response), but individual variant `available` flags on a pre-order product are inconsistent — some color variants show `available: true`, others `false`, even though the whole release is purchasable now. So: for any product whose `tags` include `"pre-order"` (case-insensitive), all of its vinyl variants are included regardless of `available`, and each gets a `" (Pre-Order)"` suffix on the title. Non-pre-order products keep the strict `available == true` filter. Uses the shared helpers from Task 4 for pagination, tag detection, title cleanup, and image resolution — `respx` mocks the underlying HTTP call, so these tests are unaffected by that refactor.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nuclearblast_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.nuclearblast import Crawler

_PRODUCTS_URL = "https://shop.nuclearblast.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Rob Zombie - The Great Satan",
    "vendor": "Rob Zombie",
    "handle": "rob-zombie-the-great-satan",
    "product_type": "Vinyl",
    "tags": ["Aged-15+"],
    "images": [{"src": "https://cdn.shopify.com/rz-fallback.png"}],
    "variants": [
        {"title": "Ghostly Black Vinyl", "price": "31.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/rz-black.png"}},
        {"title": "Black / White Swirl Vinyl", "price": "31.99", "available": False},
        {"title": "Jewel Case CD", "price": "14.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Marilyn Manson - One Assassination Under God - Chapter 2",
    "vendor": "Marilyn Manson",
    "handle": "marilyn-manson-one-assassination-under-god-chapter-2",
    "product_type": "Vinyl/CD",
    "tags": ["Aged-15+", "Marilyn Manson", "media", "music", "pre-order"],
    "images": [{"src": "https://cdn.shopify.com/manson-fallback.png"}],
    "variants": [
        {"title": "Green and Blue Marble Vinyl", "price": "28.99", "available": True},
        {"title": "Tan w/ Black / Pink and Neon Green Splatter Vinyl", "price": "28.99", "available": False},
        {"title": "Jewel Case CD", "price": "14.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_available_vinyl_variants_only(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Rob Zombie"
    assert item["title"] == "The Great Satan — Ghostly Black Vinyl"
    assert item["format"] == "Vinyl"
    assert item["price"] == 31.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://shop.nuclearblast.com/products/rob-zombie-the-great-satan"
    assert item["cover_image_url"] == "https://cdn.shopify.com/rz-black.png"


@respx.mock
async def test_crawl_catalog_falls_back_to_product_image_when_variant_has_none(crawler):
    # The unavailable "Black / White Swirl Vinyl" variant has no featured_image of its own
    product = {**_PRODUCT, "tags": ["Aged-15+", "pre-order"]}  # force-include the unavailable variant
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    swirl_item = next(i for i in items if "Swirl" in i["title"])
    assert swirl_item["cover_image_url"] == "https://cdn.shopify.com/rz-fallback.png"


@respx.mock
async def test_crawl_catalog_cover_image_is_none_when_product_has_no_images(crawler):
    product = {**_PRODUCT, "images": [], "variants": [
        {"title": "Ghostly Black Vinyl", "price": "31.99", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_from_title(crawler):
    product = {**_PRODUCT, "vendor": "NAILS", "title": "NAILS - Every Bridge Burning", "handle": "nails"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Every Bridge Burning — Ghostly Black Vinyl"


@respx.mock
async def test_crawl_catalog_keeps_full_title_when_no_vendor_prefix(crawler):
    product = {**_PRODUCT, "title": "Compilation Album", "handle": "compilation"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Compilation Album — Ghostly Black Vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_vinyl_for_preorder_products(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    titles = {item["title"] for item in items}
    # both vinyl variants included regardless of `available`; CD variant still excluded
    assert len(items) == 2
    assert "One Assassination Under God - Chapter 2 — Green and Blue Marble Vinyl (Pre-Order)" in titles
    assert "One Assassination Under God - Chapter 2 — Tan w/ Black / Pink and Neon Green Splatter Vinyl (Pre-Order)" in titles


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variants_when_not_tagged_preorder(crawler):
    product = {**_PRODUCT, "tags": ["Aged-15+", "May 4th"]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert "(Pre-Order)" not in items[0]["title"]


@respx.mock
async def test_preorder_tag_matching_is_case_insensitive(crawler):
    product = {**_PRODUCT, "tags": ["Pre-Order"]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert all("(Pre-Order)" in item["title"] for item in items)


def test_site_metadata():
    assert Crawler.site_name == "Nuclear Blast"
    assert Crawler.base_url == "https://shop.nuclearblast.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_nuclearblast_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.nuclearblast'`

- [ ] **Step 3: Implement the crawler**

Create `backend/crawlers/nuclearblast.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_VINYL_RE = re.compile(r"\bvinyl\b|\blp\b", re.IGNORECASE)
_PREORDER_TAG = "pre-order"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Nuclear Blast"
    base_url: str = "https://shop.nuclearblast.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._vinyl_items(product):
                yield item

    @classmethod
    def _vinyl_items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        album_title = strip_vendor_prefix(product.get("title", ""), artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants", []):
            if not variant.get("available") and not is_preorder:
                continue
            variant_title = variant.get("title", "")
            if not _VINYL_RE.search(variant_title):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            title = f"{album_title} — {variant_title}"
            if is_preorder:
                title += " (Pre-Order)"
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_nuclearblast_crawler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/nuclearblast.py backend/tests/test_nuclearblast_crawler.py
git commit -m "dev-instock-crawler: add Nuclear Blast catalog crawler"
```

---

## Task 6: Century Media catalog crawler

Century Media's `/collections/vinyl` differs from Nuclear Blast in three ways confirmed by direct inspection of the live `products.json`: (1) its pre-order tag is spelled `"preorder"`, not `"pre-order"`; (2) every product in the collection is already vinyl-only — variants are just color names (e.g. `"Blue EcoMix"`) with no format wording, and no product mixes in a CD/cassette variant the way Nuclear Blast does, so no per-variant format regex is applied, every variant is included; (3) the product `title` already bears the color/format (e.g. `"Distant - Into Despair - Blue EcoMix LP"`), so after stripping the vendor prefix the remainder is used as-is — no variant name is appended (doing so would duplicate the color that's already in the title).

**Files:**
- Create: `backend/crawlers/centurymedia.py`
- Test: `backend/tests/test_centurymedia_crawler.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_centurymedia_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.centurymedia import Crawler

_PRODUCTS_URL = "https://centurymedia.store/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Distant - Into Despair - Blue EcoMix LP",
    "vendor": "Distant",
    "handle": "distant-into-despair-blue-ecomix-lp",
    "product_type": "12\"",
    "tags": ["cm", "distant", "preorder", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/distant-fallback.png"}],
    "variants": [
        {"title": "Blue EcoMix", "price": "24.98", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/distant-blue.png"}},
    ],
}

_RELEASED_PRODUCT = {
    "title": "Blood Incantation - All Gates Open",
    "vendor": "Blood Incantation",
    "handle": "blood-incantation-all-gates-open",
    "product_type": "2x12\"/DVD",
    "tags": ["blood incantation", "cm", "exclusive", "new release", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/bi-fallback.png"}],
    "variants": [
        {"title": "Transparent Sea Blue Ghost", "price": "49.98", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_for_each_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Distant"
    assert item["title"] == "Into Despair - Blue EcoMix LP (Pre-Order)"
    assert item["format"] == "Vinyl"
    assert item["price"] == 24.98
    assert item["currency"] == "USD"
    assert item["url"] == "https://centurymedia.store/products/distant-into-despair-blue-ecomix-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/distant-blue.png"


@respx.mock
async def test_crawl_catalog_no_preorder_suffix_for_released_items(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_RELEASED_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "All Gates Open"
    assert "(Pre-Order)" not in items[0]["title"]


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_for_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_RELEASED_PRODUCT, "variants": [{**_RELEASED_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_full_title_when_vendor_does_not_prefix_match(crawler):
    # "Hackett & Rothery - The Roaring Waves - LP" is credited to two artists but the
    # `vendor` field is only one of them, so the exact-prefix strip doesn't apply — the
    # full title is kept rather than guessing which words belong to the artist.
    product = {
        **_RELEASED_PRODUCT,
        "title": "Hackett & Rothery - The Roaring Waves - LP",
        "vendor": "Steve Hackett",
        "handle": "hackett-rothery-the-roaring-waves-lp",
    }
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Hackett & Rothery - The Roaring Waves - LP"
    assert items[0]["artist"] == "Steve Hackett"


def test_site_metadata():
    assert Crawler.site_name == "Century Media"
    assert Crawler.base_url == "https://centurymedia.store"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_centurymedia_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.centurymedia'`

- [ ] **Step 3: Implement the crawler**

Create `backend/crawlers/centurymedia.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Century Media"
    base_url: str = "https://centurymedia.store"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        title = strip_vendor_prefix(product.get("title", ""), artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants", []):
            if not variant.get("available") and not is_preorder:
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = f"{title} (Pre-Order)" if is_preorder else title
            items.append({
                "artist": artist,
                "title": display_title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_centurymedia_crawler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/centurymedia.py backend/tests/test_centurymedia_crawler.py
git commit -m "dev-instock-crawler: add Century Media catalog crawler"
```

---

## Task 7: Epitaph catalog crawler

Epitaph (`epitaph.com/collections/vinyl`) turns out to share Century Media's shape, not Nuclear Blast's: every product has exactly one variant literally titled `"Default Title"` (confirmed by direct inspection — the variant title carries no useful information at all), no format-mixing happens within a product, and the format/color is baked into the product `title` itself (e.g. `"No Devolución 2xLP (Black)"`). The one real difference from Century Media: Epitaph's titles never start with a `"{vendor} - "` prefix at all (vendor `"Thursday"`, title `"No Devolución 2xLP (Black)"` — no prefix to strip), and its pre-order tag is spelled `"pre-order"` (matching Nuclear Blast, not Century Media's `"preorder"`). `strip_vendor_prefix` already no-ops safely when the prefix doesn't match, so no new helper logic is needed — this crawler is a copy of Century Media's shape with different constants.

**Files:**
- Create: `backend/crawlers/epitaph.py`
- Test: `backend/tests/test_epitaph_crawler.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_epitaph_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.epitaph import Crawler

_PRODUCTS_URL = "https://www.epitaph.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "No Devolución 2xLP (Black)",
    "vendor": "Thursday",
    "handle": "no-devolucion-2xlp-black",
    "tags": ["12in Vinyl", "E00028", "Media Mail"],
    "images": [{"src": "https://cdn.shopify.com/thursday-fallback.png"}],
    "variants": [
        {"title": "Default Title", "price": "34.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "No Devolución 2xLP (Snowpiercer Torrent)",
    "vendor": "Thursday",
    "handle": "no-devolucion-2xlp-snowpiercer-torrent",
    "tags": ["12in Vinyl", "Exclusive", "limited", "Out of stock", "pre-order"],
    "images": [{"src": "https://cdn.shopify.com/thursday-torrent.png"}],
    "variants": [
        {"title": "Default Title", "price": "39.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_using_title_as_is(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Thursday"
    assert item["title"] == "No Devolución 2xLP (Black)"
    assert item["format"] == "Vinyl"
    assert item["price"] == 34.99
    assert item["url"] == "https://www.epitaph.com/products/no-devolucion-2xlp-black"
    assert item["cover_image_url"] == "https://cdn.shopify.com/thursday-fallback.png"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "No Devolución 2xLP (Snowpiercer Torrent) (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Epitaph"
    assert Crawler.base_url == "https://www.epitaph.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_epitaph_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.epitaph'`

- [ ] **Step 3: Implement the crawler**

Create `backend/crawlers/epitaph.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "pre-order"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Epitaph"
    base_url: str = "https://www.epitaph.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        title = strip_vendor_prefix(product.get("title", ""), artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants", []):
            if not variant.get("available") and not is_preorder:
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = f"{title} (Pre-Order)" if is_preorder else title
            items.append({
                "artist": artist,
                "title": display_title,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_epitaph_crawler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/epitaph.py backend/tests/test_epitaph_crawler.py
git commit -m "dev-instock-crawler: add Epitaph catalog crawler"
```

---

## Task 8: Rev HQ catalog crawler

Rev HQ (`revhq.com/collections/vinyl`) looks structurally like Nuclear Blast — products mix LP/CD variants, and variant titles carry real info (`"LP - Color Vinyl"`, `"7\""`) worth keeping — but with one landmine confirmed across 20 sampled products: **`vendor` is always the record label** (e.g. `"Metal Blade Records"`, `"Relapse Records"`), never the artist. The actual artist only exists embedded in the title as `Artist "Album Title"` (e.g. `100 Demons "Embrace The Black Light"`) — every sampled title matched this pattern with zero exceptions. Using `vendor` as artist here, the way every other site's crawler does, would mislabel every row with a distributor name instead of a band name.

Two other differences from Nuclear Blast:
- The vinyl-detection regex needs widening. Nuclear Blast's `\bvinyl\b|\blp\b` misses bare inch-size variants like `"7\""` (a 7" single) — no "vinyl"/"lp" wording appears in that variant title at all. Rev HQ's crawler uses its own wider pattern, `\bvinyl\b|\blp\b|\d+\s*"`, kept local to this file rather than changing Nuclear Blast's regex, since there's no evidence Nuclear Blast has the same gap.
- No pre-order override (per your call above) — no reliable structured pre-order signal was found (not in tags; a "(PRE-ORDER)" string turned out to live in one product's `sku` field, not confirmed as a stable convention). Rev HQ just uses the plain `available == true` filter.

**Files:**
- Create: `backend/crawlers/revhq.py`
- Test: `backend/tests/test_revhq_crawler.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_revhq_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.revhq import Crawler

_PRODUCTS_URL = "https://revhq.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": '100 Demons "Embrace The Black Light"',
    "vendor": "Closed Casket Activities",
    "handle": "100-demons-embrace-the-black-light",
    "tags": ["100 Demons", "hardcore", "Music", "punk", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/demons-fallback.png"}],
    "variants": [
        {"title": "LP - Color Vinyl", "price": "25.60", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/demons-lp.png"}},
        {"title": "CD", "price": "12.30", "available": True},
    ],
}

_SEVEN_INCH_PRODUCT = {
    "title": '50 Lions "Former Glory b/w Normality"',
    "vendor": "Six Feet Under Records",
    "handle": "50lions-formergloryb-wnormality-7",
    "tags": ["50 Lions", "7\"", "hardcore", "Music", "punk", "Vinyl"],
    "images": [],
    "variants": [
        {"title": "7\"", "price": "6.35", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_quoted_title_not_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "100 Demons"
    assert item["title"] == "Embrace The Black Light — LP - Color Vinyl"
    assert item["price"] == 25.60
    assert item["url"] == "https://revhq.com/products/100-demons-embrace-the-black-light"
    assert item["cover_image_url"] == "https://cdn.shopify.com/demons-lp.png"


@respx.mock
async def test_crawl_catalog_excludes_cd_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"].endswith("LP - Color Vinyl")


@respx.mock
async def test_crawl_catalog_includes_bare_inch_size_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SEVEN_INCH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "50 Lions"
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variants_no_preorder_override(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_quotes(crawler):
    product = {**_PRODUCT, "title": "Various Artists Sampler 2026", "vendor": "Trust Records"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Trust Records"
    assert items[0]["title"] == "Various Artists Sampler 2026 — LP - Color Vinyl"


def test_site_metadata():
    assert Crawler.site_name == "Rev HQ"
    assert Crawler.base_url == "https://revhq.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_revhq_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.revhq'`

- [ ] **Step 3: Implement the crawler**

Create `backend/crawlers/revhq.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_VINYL_RE = re.compile(r'\bvinyl\b|\blp\b|\d+\s*"', re.IGNORECASE)
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*"(?P<album>.+)"\s*$')
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Rev HQ"
    base_url: str = "https://revhq.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._vinyl_items(product):
                yield item

    @classmethod
    def _vinyl_items(cls, product: dict) -> list[dict]:
        artist, album_title = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        items = []
        for variant in product.get("variants", []):
            if not variant.get("available"):
                continue
            variant_title = variant.get("title", "")
            if not _VINYL_RE.search(variant_title):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            items.append({
                "artist": artist,
                "title": f"{album_title} — {variant_title}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        # Rev HQ's `vendor` is the record label, not the artist — the real artist only
        # exists embedded in the title as Artist "Album Title". Falls back to the label
        # if a title doesn't match that pattern, rather than leaving the artist blank.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_revhq_crawler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/revhq.py backend/tests/test_revhq_crawler.py
git commit -m "dev-instock-crawler: add Rev HQ catalog crawler"
```

---

## Task 9: `CrawlManager` stock sync orchestration

**Files:**
- Modify: `backend/crawl_manager.py`
- Test: `backend/tests/test_crawl_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_crawl_manager.py`:

```python
# ---------------------------------------------------------------------------
# stock sync task
# ---------------------------------------------------------------------------

async def test_stock_sync_not_running_initially(manager):
    assert manager.stock_sync_running is False


async def test_start_stock_sync_returns_true_when_idle(manager):
    async def _fake_sync():
        await asyncio.sleep(0)

    manager._sync_stock = _fake_sync  # type: ignore
    started = await manager.start_stock_sync()
    assert started is True
    await asyncio.sleep(0.01)


async def test_start_stock_sync_returns_false_when_already_running(manager):
    event = asyncio.Event()

    async def _fake_sync():
        await event.wait()

    manager._sync_stock = _fake_sync  # type: ignore
    await manager.start_stock_sync()
    assert manager.stock_sync_running is True
    second = await manager.start_stock_sync()
    assert second is False
    event.set()
    await asyncio.sleep(0.01)


async def test_stock_sync_running_false_after_completion(manager):
    async def _instant():
        pass

    manager._sync_stock = _instant  # type: ignore
    await manager.start_stock_sync()
    await asyncio.sleep(0.05)
    assert manager.stock_sync_running is False


async def test_price_crawl_and_stock_sync_can_run_concurrently(manager):
    crawl_event = asyncio.Event()
    stock_event = asyncio.Event()

    async def _fake_run(mode, release_id):
        await crawl_event.wait()

    async def _fake_stock_sync():
        await stock_event.wait()

    manager._run = _fake_run  # type: ignore
    manager._sync_stock = _fake_stock_sync  # type: ignore

    await manager.start("all")
    await manager.start_stock_sync()

    assert manager.running is True
    assert manager.stock_sync_running is True

    crawl_event.set()
    stock_event.set()
    await asyncio.sleep(0.05)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k stock_sync -v`
Expected: FAIL — `AttributeError: 'CrawlManager' object has no attribute 'stock_sync_running'`

- [ ] **Step 3: Implement the orchestration**

In `backend/crawl_manager.py`, add `self._stock_task: Optional[asyncio.Task] = None` to `__init__` (alongside `self._sync_task`):

```python
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._stock_task: Optional[asyncio.Task] = None
        self._subscribers: list[asyncio.Queue] = []
        self._recent: list[dict] = []
```

Add the following methods after `_sync_collection` and before the module-level `crawl_manager = CrawlManager()` instantiation:

```python
    @property
    def stock_sync_running(self) -> bool:
        return self._stock_task is not None and not self._stock_task.done()

    async def start_stock_sync(self) -> bool:
        if self.stock_sync_running:
            log.warning("Stock sync already running, ignoring start request")
            return False
        self._stock_task = asyncio.create_task(self._sync_stock())
        return True

    async def _sync_stock(self):
        import sqlite3
        import config as cfg_module
        from db import get_enabled_crawlers, replace_stock_items
        from crawler import load_enabled_crawlers

        await self._broadcast({"status": "stock_sync_started"})
        log.info("Stock sync started")

        conn = sqlite3.connect(cfg_module.DB_FILE, check_same_thread=False, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            enabled = get_enabled_crawlers(conn, crawler_type="catalog")
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({"status": "stock_sync_error", "error": "No enabled catalog crawlers"})
                return

            total_synced = 0
            for crawler in crawlers:
                items = []
                try:
                    async for item in crawler.crawl_catalog():
                        items.append(item)
                except Exception as e:
                    log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                    await self._broadcast({
                        "status": "stock_sync_error",
                        "error": str(e),
                        "source": crawler._db_site_name,
                    })
                    continue

                replace_stock_items(conn, crawler._db_id, items)
                total_synced += len(items)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({
                    "status": "stock_sync_progress",
                    "synced": total_synced,
                    "source": crawler._db_site_name,
                })

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced})
            log.info("Stock sync complete: %d items", total_synced)

        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
            await self._broadcast({"status": "stock_sync_error", "error": str(e)})
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/crawl_manager.py backend/tests/test_crawl_manager.py
git commit -m "dev-instock-crawler: add CrawlManager stock sync orchestration"
```

---

## Task 10: Scheduler + Settings API wiring

**Files:**
- Modify: `backend/scheduler.py`, `backend/routers/settings.py`

- [ ] **Step 1: Add `configure_stock` to the scheduler**

Add to `backend/scheduler.py`, after `configure_sync`:

```python
def configure_stock(cron_expression: str):
    if _scheduler.get_job("stock_sync"):
        _scheduler.remove_job("stock_sync")

    if not cron_expression:
        log.info("Stock sync schedule cleared")
        return

    async def _run():
        from crawl_manager import crawl_manager
        log.info("Scheduled stock sync starting")
        await crawl_manager.start_stock_sync()

    try:
        _scheduler.add_job(_run, CronTrigger.from_crontab(cron_expression), id="stock_sync")
        log.info("Stock sync scheduled: %s", cron_expression)
    except Exception as e:
        log.warning("Invalid stock sync schedule expression %r: %s", cron_expression, e)
        raise ValueError(f"Invalid cron expression: {cron_expression}") from e
```

(No dedicated test — `configure`/`configure_sync` have no existing test coverage either, since they drive a real `AsyncIOScheduler`; this follows the same precedent.)

- [ ] **Step 2: Wire `stock_schedule` through Settings**

In `backend/routers/settings.py`, add the field to `SettingsUpdate`:

```python
class SettingsUpdate(BaseModel):
    discogs_token: str
    debug_screenshot_interval: int = 20
    shuffle_crawl_order: bool = True
    crawl_delay_seconds: int = 30
    consecutive_failure_limit: int = 10
    crawl_schedule: str = ""
    crawl_schedule_mode: str = "missing"
    collection_schedule: str = ""
    collection_schedule_mode: str = "all"
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    stock_schedule: str = ""
```

Add it to `get_settings()`'s return dict:

```python
        "stock_schedule": config.get("stock_schedule", ""),
```

Add it to `update_settings()`, both the config assignment and the scheduler call:

```python
    config["stock_schedule"] = body.stock_schedule
    save_config(config)
    try:
        scheduler.configure(body.crawl_schedule, body.crawl_schedule_mode)
        scheduler.configure_sync(body.collection_schedule, body.collection_schedule_mode)
        scheduler.configure_stock(body.stock_schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 3: Manually verify the settings round-trip**

Run: `cd backend && python -c "
from config import save_config, load_config
save_config({'stock_schedule': '0 3 * * *'})
print(load_config()['stock_schedule'])
"`
Expected output: `0 3 * * *`

(This mirrors the existing lack of router-level tests for `settings.py` — there is no `test_settings_router.py` in the project — so no new test file is added here, consistent with current coverage.)

- [ ] **Step 4: Commit**

```bash
git add backend/scheduler.py backend/routers/settings.py
git commit -m "dev-instock-crawler: wire stock_schedule through settings and scheduler"
```

---

## Task 11: `GET /api/stock` and `POST /api/stock/sync/start`

**Files:**
- Create: `backend/routers/stock.py`
- Modify: `backend/main.py` (router registration + bundled-crawler type detection)
- Test: `backend/tests/test_stock_router.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_stock_router.py`:

```python
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

import db as db_module
from db import register_crawler, replace_stock_items
from routers import stock as stock_router


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(conn, monkeypatch):
    monkeypatch.setattr(stock_router, "get_connection", lambda: conn)
    app = FastAPI()
    app.include_router(stock_router.router, prefix="/api")
    yield TestClient(app)


def test_list_stock_returns_items(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "The Great Satan", "price": 31.99, "currency": "USD", "url": "https://x/1"},
    ])
    r = client.get("/api/stock")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "Nuclear Blast"


def test_list_stock_search_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock?search=zombie")
    assert r.json()["total"] == 1


def test_list_stock_artist_param(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock?artist=NAILS")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["artist"] == "NAILS"


def test_list_stock_artists_endpoint(client, conn):
    register_crawler(conn, "Nuclear Blast", "/path/nb.py", crawler_type="catalog")
    crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name='Nuclear Blast'").fetchone()[0]
    replace_stock_items(conn, crawler_id, [
        {"artist": "Rob Zombie", "title": "T1", "format": "Vinyl", "price": 1.0, "currency": "USD", "url": "https://x/1"},
        {"artist": "NAILS", "title": "T2", "format": "Vinyl", "price": 2.0, "currency": "USD", "url": "https://x/2"},
    ])
    r = client.get("/api/stock/artists")
    assert r.status_code == 200
    assert r.json()["artists"] == ["NAILS", "Rob Zombie"]


def test_start_stock_sync_calls_manager(client, monkeypatch):
    fake_manager = AsyncMock()
    fake_manager.start_stock_sync = AsyncMock(return_value=True)
    fake_manager.stock_sync_running = True
    monkeypatch.setattr(stock_router, "crawl_manager", fake_manager)
    r = client.post("/api/stock/sync/start")
    assert r.status_code == 200
    assert r.json() == {"started": True, "running": True}
    fake_manager.start_stock_sync.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_stock_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'routers.stock'`

- [ ] **Step 3: Implement the router**

Create `backend/routers/stock.py`:

```python
from fastapi import APIRouter, Query
from typing import Optional
from db import get_connection, get_stock_items, get_distinct_stock_artists
from crawl_manager import crawl_manager

router = APIRouter()


@router.get("/stock")
def list_stock(
    search: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    sort: str = Query("artist"),
    order: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
):
    conn = get_connection()
    return get_stock_items(conn, search=search, artist=artist, sort=sort, order=order, page=page, per_page=per_page)


@router.get("/stock/artists")
def list_stock_artists():
    conn = get_connection()
    return {"artists": get_distinct_stock_artists(conn)}


@router.post("/stock/sync/start")
async def start_stock_sync():
    started = await crawl_manager.start_stock_sync()
    return {"started": started, "running": crawl_manager.stock_sync_running}
```

Register it in `backend/main.py`, alongside the other `include_router` calls (around line 91-99):

```python
from routers import collection, releases, settings, crawl, logs, screenshots, crawler_auth, health, session, stock
```

```python
app.include_router(stock.router, prefix="/api")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_stock_router.py -v`
Expected: PASS

- [ ] **Step 5: Wire `crawler_type` detection into the bundled-crawler bootstrap**

In `backend/main.py`, add a helper next to `_read_site_name` and use it in `seed_bundled_crawlers`:

```python
def _read_crawler_type(path: Path, fallback: str = "release") -> str:
    import re
    try:
        text = path.read_text()
        m = re.search(r'crawler_type(?:\s*:\s*\w+)?\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return fallback
```

In `seed_bundled_crawlers`, change:

```python
        site_name = _read_site_name(dest, src.stem.replace("_", " ").title())
        register_crawler(conn, site_name, str(dest))
```

to:

```python
        site_name = _read_site_name(dest, src.stem.replace("_", " ").title())
        crawler_type = _read_crawler_type(dest)
        register_crawler(conn, site_name, str(dest), crawler_type)
```

(No dedicated test — `main.py` has no existing test file, consistent with current coverage.)

- [ ] **Step 6: Commit**

```bash
git add backend/routers/stock.py backend/main.py backend/tests/test_stock_router.py
git commit -m "dev-instock-crawler: add stock router and register Nuclear Blast on startup"
```

---

## Task 12: Frontend types and API client

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`

- [ ] **Step 1: Add types**

In `frontend/src/api/types.ts`, add `crawler_type` to the `Crawler` interface (lines 31-39):

```typescript
export interface Crawler {
  id: number
  site_name: string
  module_path: string
  crawler_type: 'release' | 'catalog'
  enabled: boolean
  last_run: string | null
  base_url: string | null
  login_url: string | null
}
```

Add `stock_schedule` to the `Settings` interface (lines 41-53):

```typescript
export interface Settings {
  discogs_token: string
  debug_screenshot_interval: number
  shuffle_crawl_order: boolean
  crawl_delay_seconds: number
  consecutive_failure_limit: number
  crawl_schedule?: string
  crawl_schedule_mode?: 'missing' | 'all'
  collection_schedule?: string
  collection_schedule_mode?: 'all' | 'new'
  ebay_app_id?: string
  ebay_cert_id?: string
  stock_schedule?: string
}
```

Extend the `CrawlEvent` status union and fields (lines 59-75):

```typescript
export interface CrawlEvent {
  status?: 'found' | 'not_found' | 'error' | 'complete' | 'started' | 'stopped' | 'ping'
    | 'sync_started' | 'sync_progress' | 'sync_complete' | 'sync_error'
    | 'stock_sync_started' | 'stock_sync_progress' | 'stock_sync_complete' | 'stock_sync_error'
  discogs_id?: string
  release?: string
  artist?: string
  site?: string
  price?: number
  error?: string
  total?: number
  total_pages?: number
  page?: number
  synced?: number
  wishlist_synced?: number
  username?: string
  screenshots?: string[]
  source?: string
}
```

Add new types at the end of the file:

```typescript
export interface StockItem {
  id: number
  artist: string
  title: string
  format: string | null
  price: number | null
  currency: string | null
  url: string
  cover_image_url: string | null
  source: string
  last_seen: string
}

export interface StockResponse {
  total: number
  page: number
  per_page: number
  items: StockItem[]
}

export type StockSortField = 'artist' | 'title' | 'format' | 'price'
```

- [ ] **Step 2: Add client functions**

In `frontend/src/api/client.ts`, add `StockResponse` and `StockSortField` to the type import (line 1-4):

```typescript
import type {
  ReleasesResponse, Crawler, Settings, SortField, SortOrder, CrawlStatus, CollectionStatus, ScreenshotSession,
  AuthState, SetupResponse, RecordScope, StockResponse, StockSortField,
} from './types'
```

Add functions after `postCrawlStop` (around line 127):

```typescript
export async function getStock(params: {
  search?: string
  artist?: string
  sort?: StockSortField
  order?: SortOrder
  page?: number
  per_page?: number
}): Promise<StockResponse> {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.artist) q.set('artist', params.artist)
  if (params.sort) q.set('sort', params.sort)
  if (params.order) q.set('order', params.order)
  if (params.page) q.set('page', String(params.page))
  if (params.per_page) q.set('per_page', String(params.per_page))
  const r = await apiFetch(`/stock?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getStockArtists(): Promise<string[]> {
  const r = await apiFetch('/stock/artists')
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.artists
}

export async function postStockSyncStart(): Promise<{ started: boolean; running: boolean }> {
  const r = await apiFetch('/stock/sync/start', { method: 'POST' })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors (this only touches types/client, no consumers reference the new fields yet, so nothing should break)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "dev-instock-crawler: add stock types and API client functions"
```

---

## Task 13: `StockBrowser` component

`StockBrowser` mirrors `RecordBrowser`'s full shell — artist sidebar, search bar, list/tile view toggle, sortable table, pagination — per the "keep the same headers/layout across Collection, Wishlist, and In Stock" requirement. It's a separate component from `RecordBrowser` (see the spec's "Implementation" decision) because the underlying data (`StockItem`) has no `discogs_id`, no per-crawler `listings` map, and no collection/wishlist actions — reusing `RecordBrowser` directly would mean branching most of its body on a data shape it wasn't designed for. The visual shell is duplicated; the data plumbing is not.

**Files:**
- Create: `frontend/src/views/StockBrowser.tsx`
- Test: `frontend/src/test/stockBrowser.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/test/stockBrowser.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StockBrowser from '../views/StockBrowser'

const items = [
  { id: 1, artist: 'Rob Zombie', title: 'The Great Satan — Ghostly Black Vinyl', format: 'Vinyl', price: 31.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/rob-zombie', cover_image_url: 'https://cdn.shopify.com/rz-black.png', source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z' },
  { id: 2, artist: 'NAILS', title: 'Every Bridge Burning — Forest Green LP', format: 'Vinyl', price: 25.99, currency: 'USD', url: 'https://shop.nuclearblast.com/products/nails', cover_image_url: null, source: 'Nuclear Blast', last_seen: '2026-07-05T00:00:00Z' },
]

const getStock = vi.fn()
const getStockArtists = vi.fn()

vi.mock('../api/client', () => ({
  getStock: (...args: unknown[]) => getStock(...args),
  getStockArtists: (...args: unknown[]) => getStockArtists(...args),
}))

beforeEach(() => {
  getStock.mockReset()
  getStockArtists.mockReset()
  getStock.mockResolvedValue({ total: 2, page: 1, per_page: 250, items })
  getStockArtists.mockResolvedValue(['NAILS', 'Rob Zombie'])
  localStorage.clear()
})

// Both the sidebar and the table render an artist's name, so tests that only
// need to confirm data has loaded wait on a title instead — titles are unique
// and never appear in the sidebar.

describe('StockBrowser', () => {
  it('renders artist, title, format, price link, source, and thumbnail for each item', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    expect(screen.getAllByText('Rob Zombie').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Vinyl').length).toBe(2)
    const link = screen.getByText('$31.99') as HTMLAnchorElement
    expect(link.closest('a')?.getAttribute('href')).toBe('https://shop.nuclearblast.com/products/rob-zombie')
    expect(screen.getAllByText('Nuclear Blast').length).toBe(2)
    const thumbnail = screen.getByAltText('The Great Satan — Ghostly Black Vinyl') as HTMLImageElement
    expect(thumbnail.getAttribute('src')).toBe('https://cdn.shopify.com/rz-black.png')
  })

  it('renders a placeholder box when cover_image_url is null', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('Every Bridge Burning — Forest Green LP')).toBeTruthy())
    expect(screen.queryByAltText('Every Bridge Burning — Forest Green LP')).toBeNull()
  })

  it('shows an empty state when there are no items', async () => {
    getStock.mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] })
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText(/No in-stock items yet/)).toBeTruthy())
  })

  it('searches by artist or title', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('Search artist or title…'), { target: { value: 'nails' } })
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ search: 'nails' })))
  })

  it('toggles sort order when a column header is clicked twice', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'asc' })))
    fireEvent.click(screen.getByText(/Price/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'price', order: 'desc' })))
  })

  it('sorts by format when the Format column header is clicked', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByText(/Format/))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ sort: 'format', order: 'asc' })))
  })

  it('renders an artist sidebar with All plus each distinct artist, and filters on click', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('All')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'NAILS' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rob Zombie' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'NAILS' }))
    await waitFor(() => expect(getStock).toHaveBeenCalledWith(expect.objectContaining({ artist: 'NAILS' })))
  })

  it('switches to tile view and links tiles to the product page', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => {
      const tileLink = screen.getByText('The Great Satan — Ghostly Black Vinyl').closest('a')
      expect(tileLink?.getAttribute('href')).toBe('https://shop.nuclearblast.com/products/rob-zombie')
    })
  })

  it('persists the view mode to localStorage under collectionViewMode_instock', async () => {
    render(<StockBrowser />)
    await waitFor(() => expect(screen.getByText('The Great Satan — Ghostly Black Vinyl')).toBeTruthy())
    fireEvent.click(screen.getByTitle('Tile view'))
    await waitFor(() => expect(localStorage.getItem('collectionViewMode_instock')).toBe('tiles'))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: FAIL — `Failed to resolve import "../views/StockBrowser"`

- [ ] **Step 3: Implement the component**

Create `frontend/src/views/StockBrowser.tsx`:

```tsx
import { useState, useEffect, useCallback, useRef } from 'react'
import { getStock, getStockArtists } from '../api/client'
import type { StockItem, StockSortField, SortOrder } from '../api/types'

export default function StockBrowser() {
  const [items, setItems] = useState<StockItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selectedArtist, setSelectedArtist] = useState('')
  const [artists, setArtists] = useState<string[]>([])
  const [sort, setSort] = useState<StockSortField>('artist')
  const [order, setOrder] = useState<SortOrder>('asc')
  const [loading, setLoading] = useState(false)
  const [viewMode, setViewMode] = useState<'list' | 'tiles'>(
    () => (localStorage.getItem('collectionViewMode_instock') === 'tiles' ? 'tiles' : 'list')
  )
  const PER_PAGE = 250
  const tableScrollRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getStock({
        search: search || undefined,
        artist: selectedArtist || undefined,
        sort, order, page, per_page: PER_PAGE,
      })
      setItems(result.items)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [search, selectedArtist, sort, order, page])

  useEffect(() => { load() }, [load])
  useEffect(() => { getStockArtists().then(setArtists) }, [])
  useEffect(() => { localStorage.setItem('collectionViewMode_instock', viewMode) }, [viewMode])
  useEffect(() => { tableScrollRef.current?.scrollTo({ top: 0 }) }, [selectedArtist])

  function toggleSort(field: StockSortField) {
    if (sort === field) {
      setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))
    } else {
      setSort(field)
      setOrder('asc')
    }
    setPage(1)
  }

  const totalPages = Math.ceil(total / PER_PAGE)

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      <aside className="w-48 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0 min-h-0">
        <div className="px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-800 shrink-0">Artist</div>
        <div className="flex flex-col gap-2 overflow-y-auto p-3">
          <button
            onClick={() => { setSelectedArtist(''); setPage(1) }}
            className={`shrink-0 text-left text-sm px-2 py-1 rounded ${!selectedArtist ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
          >
            All
          </button>
          {artists.map((a) => (
            <button
              key={a}
              onClick={() => { setSelectedArtist(a); setPage(1) }}
              className={`shrink-0 text-left text-sm px-2 py-1 rounded truncate ${selectedArtist === a ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              {a}
            </button>
          ))}
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Search bar */}
        <div className="px-4 py-3 border-b border-gray-800 bg-gray-950 flex items-center">
          <div className="relative w-full max-w-md">
            <input
              type="text"
              placeholder="Search artist or title…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 pr-7 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500"
            />
            <button
              onClick={() => { setSearch(''); setPage(1) }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            >
              ✕
            </button>
          </div>
          <span className="ml-3 text-xs text-gray-500">{total} items</span>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => setViewMode('list')}
              title="List view"
              className={`p-1.5 rounded ${viewMode === 'list' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <line x1="2" y1="4" x2="14" y2="4" />
                <line x1="2" y1="8" x2="14" y2="8" />
                <line x1="2" y1="12" x2="14" y2="12" />
              </svg>
            </button>
            <button
              onClick={() => setViewMode('tiles')}
              title="Tile view"
              className={`p-1.5 rounded ${viewMode === 'tiles' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="2" width="5" height="5" />
                <rect x="9" y="2" width="5" height="5" />
                <rect x="2" y="9" width="5" height="5" />
                <rect x="9" y="9" width="5" height="5" />
              </svg>
            </button>
          </div>
        </div>

        {/* Tiles */}
        {viewMode === 'tiles' && (
          <div className="flex-1 overflow-auto" ref={tableScrollRef}>
            {loading && <div className="text-center py-8 text-gray-500">Loading…</div>}
            {!loading && items.length === 0 && (
              <div className="text-center py-8 text-gray-500">
                No in-stock items yet. Click "Refresh Stock Now" in Settings.
              </div>
            )}
            {!loading && items.length > 0 && (
              <div className="grid gap-4 p-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                {items.map((item) => (
                  <a
                    key={item.id}
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group"
                  >
                    {item.cover_image_url ? (
                      <img
                        src={item.cover_image_url}
                        alt={item.title}
                        className="w-full aspect-square object-cover rounded"
                      />
                    ) : (
                      <div className="w-full aspect-square bg-gray-800 rounded" />
                    )}
                    <div className="mt-1.5 text-sm text-gray-200 truncate group-hover:text-indigo-400">{item.artist}</div>
                    <div className="text-xs text-gray-400 truncate">{item.title}</div>
                  </a>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Table */}
        {viewMode === 'list' && (
        <div className="flex-1 overflow-auto" ref={tableScrollRef}>
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 bg-gray-900 text-xs text-gray-400 uppercase">
              <tr>
                <th className="w-12 px-3 py-2"></th>
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('artist')}>
                  Artist {sort === 'artist' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('title')}>
                  Title {sort === 'title' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('format')}>
                  Format {sort === 'format' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center cursor-pointer hover:text-white select-none" onClick={() => toggleSort('price')}>
                  Price {sort === 'price' ? (order === 'asc' ? '↑' : '↓') : ''}
                </th>
                <th className="px-3 py-2 text-center">Source</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={6} className="text-center py-8 text-gray-500">Loading…</td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={6} className="text-center py-8 text-gray-500">No in-stock items yet. Click "Refresh Stock Now" in Settings.</td></tr>
              )}
              {items.map((item) => (
                <tr key={item.id} className="border-t border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2">
                    {item.cover_image_url ? (
                      <img
                        src={item.cover_image_url}
                        alt={item.title}
                        className="w-10 h-10 object-cover rounded"
                      />
                    ) : (
                      <div className="w-10 h-10 bg-gray-800 rounded" />
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-200">{item.artist}</td>
                  <td className="px-3 py-2 text-gray-300">{item.title}</td>
                  <td className="px-3 py-2 text-gray-400">{item.format ?? '—'}</td>
                  <td className="px-3 py-2">
                    <a href={item.url} target="_blank" rel="noreferrer" className="text-green-400 hover:text-green-300 font-medium">
                      {item.price != null ? `$${item.price.toFixed(2)}` : 'View'}
                    </a>
                  </td>
                  <td className="px-3 py-2 text-gray-400">{item.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="border-t border-gray-800 px-4 py-2 flex items-center gap-2 text-sm text-gray-400">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="px-2 py-1 rounded hover:bg-gray-800 disabled:opacity-40">← Prev</button>
            <span>Page {page} of {totalPages}</span>
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="px-2 py-1 rounded hover:bg-gray-800 disabled:opacity-40">Next →</button>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/stockBrowser.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/StockBrowser.tsx frontend/src/test/stockBrowser.test.tsx
git commit -m "dev-instock-crawler: add StockBrowser component with sidebar, search, and tile/list views"
```

---

## Task 14: Wire the In Stock tab into `App.tsx`

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/test/inStockTab.test.tsx`

**Note:** `frontend/src/test/crawlStatusBar.test.tsx` is pre-existing and already fails all 5 of its tests before this plan touches anything (verified by running `npx vitest run` on a clean checkout — it asserts a global "Refresh Prices" button that no longer exists; `RecordBrowser` now has per-row refresh buttons instead, and the file's `vi.mock` is missing several exports `App.tsx` now requires, like `checkHealth`/`getAuthState`). That's pre-existing breakage unrelated to this feature — don't extend it and don't try to make it pass as part of this plan. A new, self-contained test file is added instead. (The stale file is worth fixing separately — flag it, don't fold that fix into this branch.)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/test/inStockTab.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'

class MockEventSource {
  static instances: MockEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { MockEventSource.instances.push(this) }
  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

const postStockSyncStart = vi.fn().mockResolvedValue({ started: true, running: true })

vi.mock('../api/client', () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getAuthState: vi.fn().mockResolvedValue('authenticated'),
  setUnauthorizedHandler: vi.fn(),
  refreshCollection: vi.fn().mockResolvedValue({ synced: 0, username: 'test' }),
  getCollectionStatus: vi.fn().mockResolvedValue({ total: 0, last_synced: null }),
  getCrawlStatus: vi.fn().mockResolvedValue({ total: 0, missing: 0, oldest_checked: null }),
  postCrawlStart: vi.fn().mockResolvedValue({ started: true, running: true }),
  getCrawlers: vi.fn().mockResolvedValue([]),
  openCrawlStream: vi.fn(() => new MockEventSource()),
  getReleases: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 50, releases: [] }),
  getArtists: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({
    discogs_token: '', debug_screenshot_interval: 20, shuffle_crawl_order: true,
    crawl_delay_seconds: 30, consecutive_failure_limit: 10, crawl_schedule: '',
    crawl_schedule_mode: 'missing', collection_schedule: '', collection_schedule_mode: 'all',
    ebay_app_id: '', ebay_cert_id: '', stock_schedule: '',
  }),
  saveSettings: vi.fn(),
  setCrawlerEnabled: vi.fn(),
  getAuthStatus: vi.fn().mockResolvedValue({ active: false, active_site: null, has_state: false, state_mtime: null }),
  startLogin: vi.fn(),
  finishLogin: vi.fn(),
  clearAuthState: vi.fn(),
  changePassword: vi.fn(),
  logout: vi.fn(),
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: vi.fn((path: string) => `/api/screenshots/${path}`),
  clearLogs: vi.fn(),
  getStock: vi.fn().mockResolvedValue({ total: 0, page: 1, per_page: 250, items: [] }),
  getStockArtists: vi.fn().mockResolvedValue([]),
  postStockSyncStart: (...args: unknown[]) => postStockSyncStart(...args),
}))

function getLastCrawlSource() {
  return MockEventSource.instances[MockEventSource.instances.length - 1]
}

beforeEach(() => {
  MockEventSource.instances = []
  vi.clearAllMocks()
  postStockSyncStart.mockResolvedValue({ started: true, running: true })
})

describe('In Stock tab', () => {
  it('shows an In Stock nav button that switches views', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('In Stock')).toBeInTheDocument())
    fireEvent.click(screen.getByText('In Stock'))
    await waitFor(() => expect(screen.getByPlaceholderText('Search artist or title…')).toBeInTheDocument())
  })

  it('calls postStockSyncStart when Refresh Stock Now is clicked in Settings', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Settings')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Settings'))
    await waitFor(() => expect(screen.getByText('Refresh Stock Now')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Refresh Stock Now'))
    await waitFor(() => expect(postStockSyncStart).toHaveBeenCalled())
  })

  it('surfaces stock_sync_progress events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_progress', synced: 3, source: 'Nuclear Blast' })
    await waitFor(() => expect(screen.getByText(/Syncing in-stock catalog… 3 items \(Nuclear Blast\)/)).toBeInTheDocument())
  })

  it('surfaces stock_sync_complete events in the bottom status bar', async () => {
    render(<App />)
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0))
    const source = getLastCrawlSource()
    source.emit({ status: 'stock_sync_complete', synced: 12 })
    await waitFor(() => expect(screen.getByText(/In-stock sync complete: 12 items/)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: FAIL — no "In Stock" nav button exists yet, `postStockSyncStart` isn't exported from `client.ts`'s real module signature yet in `App.tsx`, and `stock_sync_progress`/`stock_sync_complete` aren't handled.

- [ ] **Step 3: Implement the App.tsx changes**

Import `StockBrowser` and `postStockSyncStart` (`frontend/src/App.tsx:1-8`):

```tsx
import { useState, useEffect } from 'react'
import RecordBrowser from './views/RecordBrowser'
import StockBrowser from './views/StockBrowser'
import Settings from './views/Settings'
import LogViewer from './views/LogViewer'
import LoginScreen from './views/LoginScreen'
import SetupWizard from './views/SetupWizard'
import { refreshCollection, getCollectionStatus, openCrawlStream, getCrawlStatus, postCrawlStart, postStockSyncStart, getCrawlers, checkHealth, getAuthState, setUnauthorizedHandler } from './api/client'
import type { CrawlEvent, CrawlStatus, CollectionStatus, Crawler, AuthState } from './api/types'

type View = 'collection' | 'wishlist' | 'instock' | 'settings' | 'logs'
```

Extend `handleEvent` (right after the existing `sync_error` block, around line 77-81):

```tsx
      if (event.status === 'sync_error') {
        setSyncing(false)
        setSyncMessage(`Sync failed: ${event.error}`)
        return
      }
      if (event.status === 'stock_sync_started') {
        setSyncing(true)
        setSyncMessage('Syncing in-stock catalog…')
        return
      }
      if (event.status === 'stock_sync_progress') {
        setSyncMessage(`Syncing in-stock catalog… ${event.synced} items (${event.source})`)
        return
      }
      if (event.status === 'stock_sync_complete') {
        setSyncing(false)
        setSyncMessage(`In-stock sync complete: ${event.synced} items`)
        return
      }
      if (event.status === 'stock_sync_error') {
        setSyncing(false)
        setSyncMessage(`In-stock sync failed: ${event.error}`)
        return
      }
```

Add a handler function near `handleFindPrices` (after it, before the `if (authState === null)` block):

```tsx
  async function handleRefreshStock() {
    try {
      await postStockSyncStart()
    } catch (e: any) {
      setSyncMessage(`In-stock sync failed to start: ${e.message}`)
    }
  }
```

Add the nav button, right after the "Wishlist" button (around line 205-214):

```tsx
          <button
            onClick={() => setView('instock')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              view === 'instock'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            In Stock
          </button>
```

Add the view container, right after the wishlist `<RecordBrowser>` block and before the Settings container (around line 261-262):

```tsx
        <div className={view === 'instock' ? 'h-full' : 'hidden'}>
          <StockBrowser />
        </div>
```

Pass the new handler to `Settings` (line 262):

```tsx
        <div className={view === 'settings' ? 'h-full overflow-y-auto' : 'hidden'}><Settings crawlers={crawlers} onCrawlersChange={setCrawlers} onRefreshCollection={(mode) => handleRefresh(mode)} onRefreshPrices={(mode) => handleFindPrices(undefined, mode)} onRefreshStock={handleRefreshStock} /></div>
```

- [ ] **Step 4: Run the test to check progress**

Run: `cd frontend && npx vitest run src/test/inStockTab.test.tsx`
Expected: the nav/SSE tests pass; the "Refresh Stock Now" test still FAILs with a TypeScript prop-type error (`Settings` doesn't accept `onRefreshStock` yet) until Task 15 adds it — this is expected; proceed directly to Task 15 before doing a final full-suite run.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/test/inStockTab.test.tsx
git commit -m "dev-instock-crawler: wire In Stock tab and stock sync SSE events into App"
```

---

## Task 15: "Catalog Crawlers" section in Settings

**Files:**
- Modify: `frontend/src/views/Settings.tsx`

- [ ] **Step 1: Update the component**

Add `onRefreshStock` to the `Props` interface (lines 61-66):

```tsx
interface Props {
  crawlers: Crawler[]
  onCrawlersChange: (crawlers: Crawler[]) => void
  onRefreshCollection: (mode: 'all' | 'new') => void
  onRefreshPrices: (mode: 'missing' | 'all') => void
  onRefreshStock: () => void
}
```

Update the component signature and add default `stock_schedule` to settings state (lines 68-81):

```tsx
export default function Settings({ crawlers, onCrawlersChange, onRefreshCollection, onRefreshPrices, onRefreshStock }: Props) {
  const [settings, setSettings] = useState<SettingsType>({
    discogs_token: '',
    debug_screenshot_interval: 20,
    shuffle_crawl_order: true,
    crawl_delay_seconds: 30,
    consecutive_failure_limit: 10,
    crawl_schedule: '',
    crawl_schedule_mode: 'missing',
    collection_schedule: '',
    collection_schedule_mode: 'all',
    ebay_app_id: '',
    ebay_cert_id: '',
    stock_schedule: '',
  })
```

Add crawler-type filtering right after the settings/auth state declarations (before `useEffect`):

```tsx
  const releaseCrawlers = crawlers.filter((c) => c.crawler_type !== 'catalog')
  const catalogCrawlers = crawlers.filter((c) => c.crawler_type === 'catalog')
```

Update the existing "Crawlers" section (lines 403-446) to use `releaseCrawlers` instead of `crawlers`:

```tsx
      {/* Crawlers */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-3 text-left">Crawlers</h2>
        {releaseCrawlers.length === 0 ? (
          <p className="text-gray-500 text-sm text-left">No crawlers configured.</p>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="text-left py-2 pr-4 w-40">Site</th>
                <th className="text-left py-2 pr-4 w-48">Last run</th>
                <th className="text-left py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {releaseCrawlers.map((c) => (
                <tr key={c.id} className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                    {c.base_url
                      ? <a href={c.base_url} target="_blank" rel="noreferrer"
                           className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                      : c.site_name}
                  </td>
                  <td className="py-3 pr-4 text-left text-gray-500 text-xs">
                    {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                  </td>
                  <td className="py-3 text-left">
                    <button
                      onClick={() => handleToggleCrawler(c)}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                        c.enabled
                          ? 'bg-green-700 hover:bg-green-600 text-white'
                          : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
                      }`}
                    >
                      {c.enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
```

Insert a new "Catalog Crawlers" section immediately after it, before the "Account & Security" section:

```tsx
      {/* Catalog Crawlers */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Catalog Crawlers</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Scan an entire site's in-stock catalog, independent of your collection. Results appear in the In Stock tab.
          Leave schedule blank to disable.
        </p>
        <table className="w-full text-sm border-collapse">
          <tbody>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left text-gray-300 font-medium align-top whitespace-nowrap w-40">Schedule</td>
              <td className="py-3 pr-4 text-left align-top w-64">
                <input
                  type="text"
                  value={settings.stock_schedule ?? ''}
                  placeholder="0 3 * * *"
                  onChange={(e) => setSettings({ ...settings, stock_schedule: e.target.value })}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 font-mono text-xs"
                />
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Cron expression (5 fields: min hour day month weekday). Empty = disabled.
              </td>
            </tr>
            <tr className="border-b border-gray-800/50">
              <td className="py-3 pr-4 text-left align-top whitespace-nowrap w-40"></td>
              <td className="py-3 pr-4 text-left align-top">
                <button
                  onClick={onRefreshStock}
                  className="px-3 py-1 bg-indigo-700 hover:bg-indigo-600 rounded text-xs font-medium transition-colors"
                >
                  Refresh Stock Now
                </button>
              </td>
              <td className="py-3 text-left text-gray-500 text-xs align-top leading-relaxed">
                Scan all enabled catalog crawlers immediately.
              </td>
            </tr>
          </tbody>
        </table>
        {catalogCrawlers.length === 0 ? (
          <p className="text-gray-500 text-sm text-left mt-4">No catalog crawlers configured.</p>
        ) : (
          <table className="w-full text-sm border-collapse mt-4">
            <thead>
              <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="text-left py-2 pr-4 w-40">Site</th>
                <th className="text-left py-2 pr-4 w-48">Last run</th>
                <th className="text-left py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {catalogCrawlers.map((c) => (
                <tr key={c.id} className="border-b border-gray-800/50">
                  <td className="py-3 pr-4 text-left text-gray-200 font-medium">
                    {c.base_url
                      ? <a href={c.base_url} target="_blank" rel="noreferrer"
                           className="text-indigo-400 hover:text-indigo-300 underline">{c.site_name}</a>
                      : c.site_name}
                  </td>
                  <td className="py-3 pr-4 text-left text-gray-500 text-xs">
                    {c.last_run ? new Date(c.last_run).toLocaleString() : '—'}
                  </td>
                  <td className="py-3 text-left">
                    <button
                      onClick={() => handleToggleCrawler(c)}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                        c.enabled
                          ? 'bg-green-700 hover:bg-green-600 text-white'
                          : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
                      }`}
                    >
                      {c.enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
```

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: `src/test/inStockTab.test.tsx`, `src/test/stockBrowser.test.tsx`, and all other files PASS. `src/test/crawlStatusBar.test.tsx` still shows its 5 pre-existing failures (confirmed present before this plan started, unrelated to this feature — see the note in Task 10) — that count should not increase or decrease as a result of this plan's changes.

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Settings.tsx
git commit -m "dev-instock-crawler: add Catalog Crawlers section to Settings"
```

---

## Task 16: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest -v`
Expected: PASS, no regressions in existing crawler/db/router tests.

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: same result as Task 15 Step 2 — everything passes except the 5 pre-existing `crawlStatusBar.test.tsx` failures, which predate this plan.

- [ ] **Step 3: Manual smoke test**

Run: `cd backend && uvicorn main:app --reload --port 8000` and `cd frontend && npm run dev`, then in the browser:
1. Confirm four rows — "Nuclear Blast", "Century Media", "Epitaph", "Rev HQ" — appear in Settings → Catalog Crawlers (all registered on backend startup, `enabled` by default).
2. Click "Refresh Stock Now" and confirm the bottom status bar shows "Syncing in-stock catalog…" then progress/completion counts covering all four sources.
3. Click the "In Stock" tab and confirm: an artist sidebar (with "All" plus each distinct artist across all sources) on the left, a search box, list/tile view toggle icons, and a table with a cover thumbnail, Artist, Title, Format ("Vinyl"), Price (a working hyperlink to the item's product page), and Source showing the correct site per row — matching the Collection tab's layout. Specifically verify: Rev HQ rows show the actual band as Artist, not a record label; a Rev HQ 7" single appears; pre-order releases from Nuclear Blast/Century Media/Epitaph appear with a "(Pre-Order)" suffix even when some show no confirmed stock, while Rev HQ has no such override. Confirm clicking an artist in the sidebar filters the list and switching to tile view shows cover art tiles linking to the product page.
4. Disable one catalog crawler in Settings, re-run "Refresh Stock Now", and confirm only the still-enabled crawlers' rows are (re-)populated (the disabled one's existing rows stay as last synced, unaffected). Confirm disabling any catalog crawler has no effect on the per-release price-crawl loop (Amazon/eBay) and vice versa.

- [ ] **Step 4: Update spec status**

In `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, change the header `**Status:** Ready for planning` to `**Status:** Implemented`.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md
git commit -m "dev-instock-crawler: mark spec as implemented"
```

---

## Out of scope (per spec)

- Cross-referencing In Stock items against the existing collection/wishlist.
- AI-based relevance filtering via a Claude API key (explicitly noted as future work).
- Non-vinyl formats anywhere in the pipeline.
- A fifth catalog source beyond Nuclear Blast, Century Media, Epitaph, and Rev HQ (the orchestration loop and the `shopify_catalog` helpers support it structurally, but no fifth source is being written now).
- Handling a Century Media or Epitaph product with more than one variant (none exist in either live catalog today — see Tasks 6 and 7); if one appeared, both variants would render with an identical title since the color is baked into the product title rather than the variant name.
- A pre-order override for Rev HQ (no reliable structured signal was found — see Task 8); a legitimately-purchasable Rev HQ pre-order could be excluded if its variant shows `available: false`.
- A config-driven generic Shopify crawler (add a new store from Settings with no code change) — noted as a future design session in the spec's "Future direction" section, not built now. The four crawlers written in Tasks 5-8 are a working reference for that later design; `shopify_catalog.py`'s helpers already need no rework to support it.
