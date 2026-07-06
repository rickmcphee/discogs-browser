# Seven Shopify Catalog Crawlers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add seven more catalog crawlers — Deathwish Inc, Equal Vision, Run For Cover, Secretly Store, Craft Recordings, Relapse, and Napalm Records — bringing the Store tab's total catalog sources to thirteen.

**Architecture:** All seven are Shopify storefronts, so all seven crawlers reuse the existing `backend/shopify_catalog.py` helpers and implement the same `crawl_catalog()` catalog-crawler contract as the six sites already documented in `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`. Three genuinely new decision shapes show up across this batch that the existing six sites hadn't needed: a *product-level* type filter instead of a per-variant regex (Equal Vision), a *negative* per-variant filter that excludes specific non-vinyl titles instead of requiring a positive vinyl match (Run For Cover excludes `"digital"`; Craft Recordings excludes exact `"CD"`/`"Cassette"`), and the discovery that a collection literally named `/collections/vinyl` is not proof it's vinyl-only (Deathwish Inc mixes in thousands of Cassette/CD variants and needs the same wide positive-regex filter Fat Wreck Chords/Secretly Store use). Full technical grounding for all seven (live JSON samples, exact match-rate percentages from testing regexes against full live catalogs) is written up in the spec doc's per-site subsections.

**Tech Stack:** Python 3.9, `httpx` (via the shared `iter_products` helper), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`, no decorator needed) + `respx` (HTTP mocking) for tests. All seven crawler files are auto-discovered and registered by `backend/main.py:seed_bundled_crawlers()` on next app startup — no manual registration step, no schema/API/frontend changes. `get_all_crawlers` in `backend/db.py` already does `ORDER BY site_name`, so no Settings-tab ordering changes are needed either.

---

### Task 1: Deathwish Inc crawler

**Files:**
- Create: `backend/crawlers/deathwishinc.py`
- Test: `backend/tests/test_deathwishinc_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_deathwishinc_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.deathwishinc import Crawler

_PRODUCTS_URL = "https://deathwishinc.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": '1 Mile North "Awakened By Decay"',
    "vendor": "Robotic Empire",
    "handle": "1-mile-north-awakened-by-decay",
    "tags": ["12\"", "2XLP", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/1mn-fallback.jpg"}],
    "variants": [
        {"title": "LP - Black", "price": "19.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": '2 Mile South "Rising Decay"',
    "vendor": "Robotic Empire",
    "handle": "2-mile-south-rising-decay",
    "tags": ["12\"", "Vinyl", "Pre-Order"],
    "images": [],
    "variants": [
        {"title": "LP - Red", "price": "21.99", "available": False},
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
    assert item["artist"] == "1 Mile North"
    assert item["title"] == "Awakened By Decay — LP - Black"
    assert item["price"] == 19.99
    assert item["url"] == "https://deathwishinc.com/products/1-mile-north-awakened-by-decay"
    assert item["cover_image_url"] == "https://cdn.shopify.com/1mn-fallback.jpg"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Rising Decay — LP - Red (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_parses_artist_from_curly_quoted_title(crawler):
    product = {**_PRODUCT, "title": "Attempt Survivors “Educated Hips”"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Attempt Survivors"
    assert items[0]["title"] == "Educated Hips — LP - Black"


@respx.mock
async def test_crawl_catalog_parses_artist_from_quoted_title_with_trailing_format_text(crawler):
    # "...Double LP" trails the closing quote — the album match must not require the
    # closing quote to end the string.
    product = {**_PRODUCT, "title": 'All Leather "Amateur Surgery On Half-Hog Abortion Island" Double LP'}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "All Leather"
    assert items[0]["title"] == "Amateur Surgery On Half-Hog Abortion Island — LP - Black"


@respx.mock
async def test_crawl_catalog_excludes_cd_and_cassette_only_variants(crawler):
    # Deathwish's "vinyl" collection also carries pure CD/Cassette variants on the
    # same product — unlike the single-format label stores, this needs a filter.
    product = {**_PRODUCT, "variants": [
        {"title": "LP - Black", "price": "19.99", "available": True},
        {"title": "CD", "price": "9.99", "available": True},
        {"title": "Cassette - Black", "price": "7.99", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Awakened By Decay — LP - Black"


@respx.mock
async def test_crawl_catalog_includes_glued_format_variant(crawler):
    product = {**_PRODUCT, "variants": [{"title": "2xLP - Black", "price": "29.99", "available": True}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Awakened By Decay — 2xLP - Black"


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_quotes(crawler):
    product = {**_PRODUCT, "title": "Various Artists Sampler 2026", "vendor": "Robotic Empire"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Robotic Empire"
    assert items[0]["title"] == "Various Artists Sampler 2026 — LP - Black"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Deathwish Inc"
    assert Crawler.base_url == "https://deathwishinc.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run (from `backend/`): `pytest tests/test_deathwishinc_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.deathwishinc'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/deathwishinc.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_PREORDER_TAG = "Pre-Order"
_COLLECTION_SLUG = "vinyl"
# Matches straight or curly quotes on either side independently (titles mix both, and
# some mismatch open/close style), and doesn't require the closing quote to end the
# string (titles like 'All Leather "..." Double LP' have trailing format text after it).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*["“](?P<album>.+?)["”]')
# Deathwish's "vinyl" collection actually mixes in thousands of Cassette/CD-only
# variants (confirmed live: 1035/6096 variants), unlike the smaller single-format
# label stores — needs the same per-variant filter Fat Wreck Chords/Secretly Store
# use. One confirmed false positive out of 6096 variants: a CD novelty item titled
# 'CD - 3" \'Mini Vinyl\'' matches the inch-mark pattern; accepted as noise.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Deathwish Inc"
    base_url: str = "https://deathwishinc.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album_title = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
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
            display_title = f"{album_title} — {variant_title}"
            if is_preorder:
                display_title += " (Pre-Order)"
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

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        # Deathwish's `vendor` is the distro label, not the artist — the real artist
        # only exists embedded in the title as Artist "Album Title". Falls back to the
        # label if a title doesn't match that pattern. Verified against 500 live titles:
        # this regex matches 497 (99.4%); the 3 residual misses are quote-less titles
        # (a subscription product and two feat./collab credits) that fall back to the
        # label — the same accepted-risk tradeoff as Rev HQ's title parsing.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 9 tests pass**

Run: `pytest tests/test_deathwishinc_crawler.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/rick/Documents/GitHub/discogs-browser
git add backend/crawlers/deathwishinc.py backend/tests/test_deathwishinc_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Deathwish Inc catalog crawler"
```

---

### Task 2: Equal Vision crawler

**Files:**
- Create: `backend/crawlers/equalvision.py`
- Test: `backend/tests/test_equalvision_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_equalvision_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.equalvision import Crawler

_PRODUCTS_URL = "https://equalvision.com/collections/equal-vision-records/products.json"

_VINYL_PRODUCT = {
    "title": "Lusitania - Blue W/ Green & White Splatter 2xLP",
    "vendor": "Fairweather",
    "product_type": "Vinyl LP",
    "handle": "fwr0lusisw-lp",
    "tags": ["Equal Vision Records", "Fairweather", "new"],
    "images": [{"src": "https://cdn.shopify.com/lusitania-fallback.jpg"}],
    "variants": [
        {"title": "Default", "price": "45.00", "available": True},
    ],
}

_CD_PRODUCT = {
    "title": "Culture Scars - CD",
    "vendor": "Hail The Sun",
    "product_type": "CD",
    "handle": "culture-scars-cd",
    "tags": ["CD", "Hail The Sun"],
    "images": [],
    "variants": [
        {"title": "Default", "price": "10.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "New Album - Splatter LP",
    "vendor": "Some Band",
    "product_type": "Vinyl LP",
    "handle": "new-album-splatter-lp",
    "tags": ["preorder"],
    "images": [],
    "variants": [
        {"title": "Default", "price": "30.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_vinyl_product_using_vendor_as_artist(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_VINYL_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Fairweather"
    assert item["title"] == "Lusitania - Blue W/ Green & White Splatter 2xLP"
    assert item["price"] == 45.00
    assert item["url"] == "https://equalvision.com/products/fwr0lusisw-lp"


@respx.mock
async def test_crawl_catalog_excludes_non_vinyl_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_CD_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "New Album - Splatter LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_VINYL_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Equal Vision"
    assert Crawler.base_url == "https://equalvision.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_equalvision_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.equalvision'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/equalvision.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "equal-vision-records"
_VINYL_TYPE_PREFIX = "Vinyl"


class Crawler:
    site_name: str = "Equal Vision"
    base_url: str = "https://equalvision.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # This collection also carries CD/merch product_types alongside vinyl —
        # unlike the label stores whose "vinyl" collections are already scoped.
        if not (product.get("product_type") or "").startswith(_VINYL_TYPE_PREFIX):
            return []

        artist = (product.get("vendor") or "").strip()
        title = strip_vendor_prefix(product.get("title", ""), artist)
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = has_tag(product, _PREORDER_TAG)

        items = []
        for variant in product.get("variants") or []:
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

- [ ] **Step 4: Run and confirm all 5 tests pass**

Run: `pytest tests/test_equalvision_crawler.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/equalvision.py backend/tests/test_equalvision_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Equal Vision catalog crawler"
```

---

### Task 3: Run For Cover crawler

**Files:**
- Create: `backend/crawlers/runforcoverrecords.py`
- Test: `backend/tests/test_runforcoverrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_runforcoverrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.runforcoverrecords import Crawler

_PRODUCTS_URL = "https://runforcoverrecords.com/collections/vinyl-shop/products.json"

_PRODUCT = {
    "title": "Marbled Eye - Read The Air LP",
    "vendor": "Marbled Eye",
    "product_type": "Vinyl",
    "handle": "marbled-eye-read-the-air-lp",
    "tags": ["LP", "Vinyl", "Vinyl Shop"],
    "images": [{"src": "https://cdn.shopify.com/marbled-eye-fallback.jpg"}],
    "variants": [
        {"title": "LP - Purple Marble", "price": "24.00", "available": True},
        {"title": "LP - Black", "price": "22.00", "available": True},
        {"title": "Digital Download", "price": "8.00", "available": True},
    ],
}

_DISTRO_PRODUCT = {
    "title": "Dazy - OUTOFBODY LP",
    "vendor": "Run For Cover - Distro",
    "product_type": "Vinyl",
    "handle": "dazy-outofbody-lp",
    "tags": ["Distributed Title", "LP", "Vinyl", "Vinyl Shop"],
    "images": [],
    "variants": [
        {"title": "Distributed Title Vinyl LP", "price": "25.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_dash_split(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Marbled Eye"
    assert items[0]["title"] == "Read The Air LP — LP - Purple Marble"
    assert items[0]["price"] == 24.00
    assert items[0]["url"] == "https://runforcoverrecords.com/products/marbled-eye-read-the-air-lp"


@respx.mock
async def test_crawl_catalog_excludes_digital_download_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert all("Digital" not in item["title"] for item in items)


@respx.mock
async def test_crawl_catalog_uses_vendor_fallback_when_vendor_is_distro_placeholder(crawler):
    # Even the distro placeholder vendor doesn't break title-based parsing since the
    # dash split always wins when present.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_DISTRO_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Dazy"
    assert items[0]["title"] == "OUTOFBODY LP — Distributed Title Vinyl LP"


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_PRODUCT, "title": "Untitled Release", "variants": [_PRODUCT["variants"][0]]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Marbled Eye"
    assert items[0]["title"] == "Untitled Release — LP - Purple Marble"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Run For Cover"
    assert Crawler.base_url == "https://runforcoverrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_runforcoverrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.runforcoverrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/runforcoverrecords.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl-shop"
_DIGITAL_RE = re.compile(r"digital", re.IGNORECASE)
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')


class Crawler:
    site_name: str = "Run For Cover"
    base_url: str = "https://runforcoverrecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album_title = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            variant_title = variant.get("title", "")
            if _DIGITAL_RE.search(variant_title):
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
        # Titles are "Artist - Album"; `vendor` is sometimes a distro placeholder
        # ("Run For Cover - Distro") rather than the real artist, so it's only used
        # as a fallback when the title has no " - " separator.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 6 tests pass**

Run: `pytest tests/test_runforcoverrecords_crawler.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/runforcoverrecords.py backend/tests/test_runforcoverrecords_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Run For Cover catalog crawler"
```

---

### Task 4: Secretly Store crawler

**Files:**
- Create: `backend/crawlers/secretlystore.py`
- Test: `backend/tests/test_secretlystore_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_secretlystore_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.secretlystore import Crawler

_PRODUCTS_URL = "https://secretlystore.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "There Near",
    "vendor": "Dinosaur Jr.",
    "handle": "there-near",
    "tags": ["Dinosaur Jr.", "Jagjaguwar", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/dino-fallback.jpg"}],
    "variants": [
        {"title": "CD", "price": "14.99", "available": True},
        {"title": "LP", "price": "24.99", "available": True},
        {"title": "LP Purple + Gold Splash Opaque Vinyl", "price": "25.99", "available": False},
    ],
}

_PREORDER_PRODUCT = {
    "title": "There Near",
    "vendor": "Dinosaur Jr.",
    "handle": "there-near",
    "tags": ["Dinosaur Jr.", "Pre-Order", "Vinyl"],
    "images": [],
    "variants": [
        {"title": "LP Purple + Gold Splash Opaque Vinyl", "price": "25.99", "available": False},
    ],
}

_GLUED_FORMAT_PRODUCT = {
    "title": "Lost Weekend",
    "vendor": "Some Artist",
    "handle": "lost-weekend",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "2xLP (White Label)", "price": "34.99", "available": True},
        {"title": "CD", "price": "14.99", "available": True},
    ],
}

_FANPACK_PRODUCT = {
    "title": "There Near Deluxe Edition LP, 7\" and T-shirt Fanpack",
    "vendor": "Dinosaur Jr.",
    "handle": "there-near-fanpack",
    "tags": ["Dinosaur Jr."],
    "images": [],
    "variants": [
        {"title": "Small", "price": "53.99", "available": True},
        {"title": "Medium", "price": "53.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_but_includes_lp_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Dinosaur Jr."
    assert item["title"] == "There Near — LP"
    assert item["price"] == 24.99
    assert item["url"] == "https://secretlystore.com/products/there-near"


@respx.mock
async def test_crawl_catalog_includes_unavailable_vinyl_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "There Near — LP Purple + Gold Splash Opaque Vinyl (Pre-Order)"


@respx.mock
async def test_crawl_catalog_includes_glued_format_variant_excludes_cd(crawler):
    # "2xLP (White Label)" has no word boundary before "LP" (digit-glued) — plain
    # \blp\b misses it, the same gap Fat Wreck Chords needed a wider regex for.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_GLUED_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Lost Weekend — 2xLP (White Label)"


@respx.mock
async def test_crawl_catalog_excludes_apparel_only_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_FANPACK_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Secretly Store"
    assert Crawler.base_url == "https://secretlystore.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_secretlystore_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.secretlystore'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/secretlystore.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_PREORDER_TAG = "Pre-Order"
_COLLECTION_SLUG = "vinyl"
# Plain \blp\b misses glued formats like "2xLP" (no word boundary before a digit/letter-glued
# "LP") — the same gap Fat Wreck Chords needed this wider pattern for; see the design spec.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Secretly Store"
    base_url: str = "https://secretlystore.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist = (product.get("vendor") or "").strip()
        title = product.get("title", "")
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
            display_title = f"{title} — {variant_title}"
            if is_preorder:
                display_title += " (Pre-Order)"
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

- [ ] **Step 4: Run and confirm all 6 tests pass**

Run: `pytest tests/test_secretlystore_crawler.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/secretlystore.py backend/tests/test_secretlystore_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Secretly Store catalog crawler"
```

---

### Task 5: Craft Recordings crawler

**Files:**
- Create: `backend/crawlers/craftrecordings.py`
- Test: `backend/tests/test_craftrecordings_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_craftrecordings_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.craftrecordings import Crawler

_PRODUCTS_URL = "https://craftrecordings.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Slide It in (Exclusive - Onyx LP)",
    "vendor": "Whitesnake",
    "handle": "whitesnake-slide-it-in-exclusive-onyx-lp",
    "tags": ["PR78866", "Rock", "Vinyl", "Whitesnake"],
    "images": [{"src": "https://cdn.shopify.com/whitesnake-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "28.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Whitesnake (Exclusive - Gold Black Ice LP)",
    "vendor": "Whitesnake",
    "handle": "whitesnake-gold-black-ice-lp",
    "tags": ["_preorder", "PRE-ORDER 9/18/2026", "Rock", "Vinyl"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "28.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_using_vendor_as_artist(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Whitesnake"
    assert item["title"] == "Slide It in (Exclusive - Onyx LP)"
    assert item["price"] == 28.00
    assert item["url"] == "https://craftrecordings.com/products/whitesnake-slide-it-in-exclusive-onyx-lp"


@respx.mock
async def test_crawl_catalog_marks_preorder_tagged_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Whitesnake (Exclusive - Gold Black Ice LP) (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_standalone_cd_variant_but_includes_vinyl_sibling(crawler):
    # "Pleasure (LP / CD)" bundles a standalone "CD" variant alongside "Vinyl" —
    # the one product in this catalog that isn't single-variant-vinyl-only.
    product = {**_PRODUCT, "variants": [
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "Vinyl", "price": "24.00", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 24.00


@respx.mock
async def test_crawl_catalog_includes_shirt_size_variant_of_vinyl_bundle(crawler):
    # Vinyl+shirt bundle products use the shirt size as the variant title (the vinyl
    # format lives in the product title instead) — must not be mistaken for a
    # standalone-format variant and excluded.
    product = {**_PRODUCT, "title": "Tetragon (180g LP) + Varsity Logo Tee", "variants": [
        {"title": "Small", "price": "45.00", "available": True},
        {"title": "Medium", "price": "45.00", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Craft Recordings"
    assert Crawler.base_url == "https://craftrecordings.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_craftrecordings_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.craftrecordings'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/craftrecordings.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "_preorder"
_COLLECTION_SLUG = "vinyl"
# Almost every product here has exactly one variant, and multi-variant products are
# vinyl+shirt bundles where the variant is a shirt size ("Small"/"Medium"), not a
# format — a positive vinyl-regex filter would wrongly exclude those. Only one product
# out of 572 needed excluding: "Pleasure (LP / CD)" has a standalone "CD" variant
# alongside its "Vinyl" variant, so this is a narrow negative filter, not the usual
# positive one.
_NON_VINYL_VARIANT_RE = re.compile(r"^(cd|cassette)$", re.IGNORECASE)


class Crawler:
    site_name: str = "Craft Recordings"
    base_url: str = "https://craftrecordings.com"
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
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            if _NON_VINYL_VARIANT_RE.match(variant.get("title", "")):
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

- [ ] **Step 4: Run and confirm all 7 tests pass**

Run: `pytest tests/test_craftrecordings_crawler.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/craftrecordings.py backend/tests/test_craftrecordings_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Craft Recordings catalog crawler"
```

---

### Task 6: Relapse crawler

**Files:**
- Create: `backend/crawlers/relapse.py`
- Test: `backend/tests/test_relapse_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_relapse_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.relapse import Crawler

_PRODUCTS_URL = "https://www.relapse.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": 'Ceremony "In The Spirit World Now" 12"',
    "vendor": "Ceremony",
    "handle": "ceremony-in-the-spirit-world-now-12",
    "tags": ["bf23", "wholesale"],
    "images": [{"src": "https://cdn.shopify.com/ceremony-fallback.jpg"}],
    "variants": [
        {"title": "Orange Krush Cloudy Effect", "price": "21.99", "available": True},
        {"title": "Mustard *LTD to 496*", "price": "21.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": 'Devourment "Pious Impiety" 7"',
    "vendor": "Devourment",
    "handle": "devourment-pious-impiety-7",
    "tags": ["preorder"],
    "images": [],
    "variants": [
        {"title": "Electric Blue", "price": "8.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_one_item_per_variant_using_vendor_as_artist(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Ceremony"
    assert items[0]["title"] == 'Ceremony "In The Spirit World Now" 12"'
    assert items[0]["price"] == 21.99
    assert items[0]["url"] == "https://www.relapse.com/products/ceremony-in-the-spirit-world-now-12"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == 'Devourment "Pious Impiety" 7" (Pre-Order)'


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Relapse"
    assert Crawler.base_url == "https://www.relapse.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_relapse_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.relapse'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/relapse.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Relapse"
    base_url: str = "https://www.relapse.com"
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
        for variant in product.get("variants") or []:
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

- [ ] **Step 4: Run and confirm all 5 tests pass**

Run: `pytest tests/test_relapse_crawler.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/relapse.py backend/tests/test_relapse_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Relapse catalog crawler"
```

---

### Task 7: Napalm Records crawler

**Files:**
- Create: `backend/crawlers/napalmrecords.py`
- Test: `backend/tests/test_napalmrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_napalmrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.napalmrecords import Crawler

_PRODUCTS_URL = "https://napalmrecords.us/collections/vinyl/products.json"

_PRODUCT = {
    "title": 'DevilDriver "Strike and Kill (Translucent Turquoise White Black Splatter vinyl)" 12"',
    "vendor": "DevilDriver",
    "handle": "devildriver-strike-and-kill-12",
    "tags": ["preorder"],
    "images": [{"src": "https://cdn.shopify.com/devildriver-fallback.jpg"}],
    "variants": [
        {"title": "Translucent Turquoise White Black Splatter", "price": "42.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "DevilDriver"
    assert "(Pre-Order)" in item["title"]
    assert item["price"] == 42.99
    assert item["url"] == "https://napalmrecords.us/products/devildriver-strike-and-kill-12"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "tags": []}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Napalm Records"
    assert Crawler.base_url == "https://napalmrecords.us"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_napalmrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.napalmrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/napalmrecords.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Napalm Records"
    base_url: str = "https://napalmrecords.us"
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
        for variant in product.get("variants") or []:
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

- [ ] **Step 4: Run and confirm all 4 tests pass**

Run: `pytest tests/test_napalmrecords_crawler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/napalmrecords.py backend/tests/test_napalmrecords_crawler.py
git commit -m "store-crawlers-fatwreck-jadetree: add Napalm Records catalog crawler"
```

---

### Task 8: Full backend test suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `pytest -q`

Expected: all tests pass, including all seven new files and the existing `test_main.py` (bundled-crawler seeding is generic — it globs `backend/crawlers/*.py`, so it picks up all seven new files automatically without any test changes). Total catalog crawlers registered: thirteen (six documented in the original spec plus these seven).

- [ ] **Step 2: Update the design spec**

Add a "Technical grounding" subsection for each of the seven sites to `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, following the existing per-site format (one paragraph describing the site's shape/quirks, a JSON snippet from a live fetch, a bullet list of findings). Update the Problem/Decisions/Data model/crawler-plugin-interface/Out-of-scope/Success-criteria sections to reflect thirteen total sites. This step matters because two of these seven sites reproduced format-filtering bugs already documented for other sites (Secretly Store's narrow regex, and the discovery that a "vinyl"-named collection isn't proof of anything for Deathwish Inc) — writing the grounding down is what would have caught this pattern before shipping duplicate bugs.
