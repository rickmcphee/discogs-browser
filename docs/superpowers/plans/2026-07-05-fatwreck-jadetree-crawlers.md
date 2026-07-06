# Fat Wreck Chords and Jade Tree Crawlers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new catalog crawlers — Fat Wreck Chords (`fatwreck.com/collections/vinyl-1`) and Jade Tree Records (`jadetree.store/collections/vinyl`) — so their in-stock vinyl shows up in the Store tab alongside the four existing catalog sources.

**Architecture:** Both are Shopify storefronts, so both crawlers reuse the existing `backend/shopify_catalog.py` helpers (`iter_products`, `has_tag`, `strip_vendor_prefix`, `resolve_cover_image`) and implement the same `crawl_catalog()` catalog-crawler contract as the four existing sites. Fat Wreck Chords mixes CD/cassette variants with vinyl on the same product (like Nuclear Blast), so it needs its own per-variant vinyl-detecting regex — one that also catches glued formats like `"2xLP"` that neither Nuclear Blast's nor Rev HQ's regex matches. Jade Tree is single-variant and vinyl-only (like Century Media/Epitaph), so it needs no per-variant filter at all. Full technical grounding for both sites (live JSON samples, regex verification against every real variant title) is already written up in `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`.

**Tech Stack:** Python 3.9, `httpx` (via the shared `iter_products` helper), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`, no decorator needed) + `respx` (HTTP mocking) for tests. Both new crawler files are auto-discovered and registered by `backend/main.py:seed_bundled_crawlers()` on next app startup — no manual registration step, no schema/API/frontend changes.

---

### Task 1: Fat Wreck Chords crawler

**Files:**
- Create: `backend/crawlers/fatwreck.py`
- Test: `backend/tests/test_fatwreck_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_fatwreck_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.fatwreck import Crawler

_PRODUCTS_URL = "https://fatwreck.com/collections/vinyl-1/products.json"

_PRODUCT = {
    "title": "12 Song Program",
    "vendor": "Tony Sly",
    "handle": "tslyf751bl-lp",
    "tags": ["Fat Wreck Chords", "Music", "new"],
    "images": [{"src": "https://cdn.shopify.com/tonysly-fallback.png"}],
    "variants": [
        {"title": "CD", "price": "10.00", "available": True},
        {"title": "LP", "price": "23.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/tonysly-lp.png"}},
    ],
}

_GLUED_FORMAT_PRODUCT = {
    "title": "Wood/Water",
    "vendor": "The Real McKenzies",
    "handle": "woodwater-2xlp",
    "tags": ["Fat Wreck Chords"],
    "images": [{"src": "https://cdn.shopify.com/woodwater-fallback.png"}],
    "variants": [
        {"title": "2xLP", "price": "24.99", "available": True},
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "Cassette", "price": "8.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "A to H",
    "vendor": "Common Rider",
    "handle": "cmnrdf000bl-lp",
    "tags": ["Fat Wreck Chords", "preorder"],
    "images": [{"src": "https://cdn.shopify.com/commonrider-fallback.png"}],
    "variants": [
        {"title": "LP", "price": "20.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_available_vinyl_variant_only(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Tony Sly"
    assert item["title"] == "12 Song Program — LP"
    assert item["format"] == "Vinyl"
    assert item["price"] == 23.00
    assert item["currency"] == "USD"
    assert item["url"] == "https://fatwreck.com/products/tslyf751bl-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/tonysly-lp.png"


@respx.mock
async def test_crawl_catalog_includes_glued_format_variant_excludes_cd_and_cassette(crawler):
    # "2xLP" has no word boundary before "LP" (digit/word-char glued on) — the exact
    # gap neither Nuclear Blast's `\bvinyl\b|\blp\b` nor Rev HQ's wider regex covers.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_GLUED_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Wood/Water — 2xLP"
    assert items[0]["artist"] == "The Real McKenzies"


@respx.mock
async def test_crawl_catalog_includes_unavailable_vinyl_for_preorder_products(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "A to H — LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][1], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_if_present(crawler):
    product = {**_PRODUCT, "vendor": "NAILS", "title": "NAILS - Every Bridge Burning", "handle": "nails"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Every Bridge Burning — LP"


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
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Fat Wreck Chords"
    assert Crawler.base_url == "https://fatwreck.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the test file and confirm it fails on import**

Run (from `backend/`): `pytest tests/test_fatwreck_crawler.py -v`

Expected: `ModuleNotFoundError: No module named 'crawlers.fatwreck'` (the module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/fatwreck.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)
_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "vinyl-1"


class Crawler:
    site_name: str = "Fat Wreck Chords"
    base_url: str = "https://fatwreck.com"
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
        for variant in product.get("variants") or []:
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

Note the collection slug is `"vinyl-1"`, not `"vinyl"` — Fat Wreck Chords' vinyl collection URL is `/collections/vinyl-1`, unlike every other site here.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `pytest tests/test_fatwreck_crawler.py -v`

Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/rick/Documents/GitHub/discogs-browser
git add backend/crawlers/fatwreck.py backend/tests/test_fatwreck_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Fat Wreck Chords catalog crawler"
```

(Use the packaged commit helper per the commit skill if the user has asked for commits along the way; otherwise stage and leave uncommitted until they do.)

---

### Task 2: Jade Tree Records crawler

**Files:**
- Create: `backend/crawlers/jadetree.py`
- Test: `backend/tests/test_jadetree_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_jadetree_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.jadetree import Crawler

_PRODUCTS_URL = "https://jadetree.store/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Nothing Feels Good LP (Blue/White Galaxy)",
    "vendor": "The Promise Ring",
    "handle": "nothing-feels-good-lp-blue-white-galaxy",
    "tags": ["12in Vinyl", "Featured", "J00000", "limited", "Media Mail"],
    "images": [{"src": "https://cdn.shopify.com/promisering-fallback.png"}],
    "variants": [
        {"title": "Default Title", "price": "26.99", "available": True},
    ],
}

_PREFIXED_PRODUCT = {
    "title": "Joan Of Arc - A Portable Model Of LP (Black 180)",
    "vendor": "Joan Of Arc",
    "handle": "joan-of-arc-a-portable-model-of-lp-black-180",
    "tags": ["12in Vinyl", "Media Mail"],
    "images": [{"src": "https://cdn.shopify.com/joanofarc-fallback.png"}],
    "variants": [
        {"title": "Default Title", "price": "22.99", "available": False},
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
    assert item["artist"] == "The Promise Ring"
    assert item["title"] == "Nothing Feels Good LP (Blue/White Galaxy)"
    assert item["format"] == "Vinyl"
    assert item["price"] == 26.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://jadetree.store/products/nothing-feels-good-lp-blue-white-galaxy"
    assert item["cover_image_url"] == "https://cdn.shopify.com/promisering-fallback.png"


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_when_present(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREFIXED_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []  # unavailable and no pre-order override — see next test for the positive case


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_when_available(crawler):
    product = {**_PREFIXED_PRODUCT, "variants": [{**_PREFIXED_PRODUCT["variants"][0], "available": True}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "A Portable Model Of LP (Black 180)"
    assert items[0]["artist"] == "Joan Of Arc"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_no_preorder_override(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


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
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Jade Tree Records"
    assert Crawler.base_url == "https://jadetree.store"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the test file and confirm it fails on import**

Run: `pytest tests/test_jadetree_crawler.py -v`

Expected: `ModuleNotFoundError: No module named 'crawlers.jadetree'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/jadetree.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, strip_vendor_prefix, resolve_cover_image

_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Jade Tree Records"
    base_url: str = "https://jadetree.store"
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

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
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

No `has_tag` import and no pre-order logic — no reliable pre-order signal was found on this site (see the design spec's Jade Tree technical-grounding section), so unlike Century Media/Epitaph/Fat Wreck Chords, unavailable variants are always excluded.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `pytest tests/test_jadetree_crawler.py -v`

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/rick/Documents/GitHub/discogs-browser
git add backend/crawlers/jadetree.py backend/tests/test_jadetree_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Jade Tree Records catalog crawler"
```

---

### Task 3: Full backend test suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `pytest -q`

Expected: all tests pass, including the two new files and the existing `test_main.py` (bundled-crawler seeding is generic — it globs `backend/crawlers/*.py`, so it picks up the two new files automatically without any test changes).

- [ ] **Step 2: Manually verify via the running app (optional, not unit-testable)**

Per `CLAUDE.md`, Playwright-dependent code is manual-only — but these two crawlers are pure `httpx`, so they *can* be exercised live if you want end-to-end confidence beyond the mocked unit tests:

```bash
cd backend && pip install -e ".[dev]" && uvicorn main:app --reload --port 8000
```

Then in another terminal, trigger a stock sync and check both new sources show up:

```bash
curl -s -X POST http://localhost:8000/api/stock/sync/start
# wait a few seconds for the sync to finish, then:
curl -s http://localhost:8000/api/stock | python3 -m json.tool | grep -A2 '"source": "Fat Wreck Chords"' | head -20
curl -s http://localhost:8000/api/stock | python3 -m json.tool | grep -A2 '"source": "Jade Tree Records"' | head -20
```

(This step requires the app's auth middleware to be satisfied — skip if that's not set up locally; the unit tests in Tasks 1–2 are the required verification.)
