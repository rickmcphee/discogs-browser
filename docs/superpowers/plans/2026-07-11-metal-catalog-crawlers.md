# Metal Catalog Crawlers (Batch 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four metal-genre catalog crawlers — Prosthetic Records, Peaceville (US store), Season of Mist (US store), and 20 Buck Spin — the first of four batches covering 18 new US-only, USD-billed Shopify record stores researched for the Store tab. Brings the Store tab's total catalog sources to eighteen.

**Architecture:** All four are Shopify storefronts and reuse the existing `backend/shopify_catalog.py` helpers and `crawl_catalog()` contract documented in `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`. Two of the four (Prosthetic, Peaceville) are straightforward — `vendor` is the artist directly, matching the `relapse.py`/`napalmrecords.py` shape. The other two needed real live-data grounding before writing fixtures, because both reproduce a pattern already known from `deathwishinc.py` (`vendor` is a label, not the artist) but with new wrinkles: Season of Mist's `vendor` is *always* `"Season of Mist - North America"` with the real artist embedded as `"Artist - Album - Format"` in the title (reusing Run For Cover's dash-split regex), and its pre-order status lives only in free-text `body_html`, not in any tag — a new detection technique for this codebase. 20 Buck Spin has the same title shape, but its `/collections/vinyl/` also carries non-release listings (a `$0.00` "mystery LP" promo bundle, a `$0.00`-ish discount SKU, and a tote bag with `product_type: "VINYL"`) that need explicit filtering, confirmed via live fetch rather than assumed.

All four were selected and verified in a prior research pass: confirmed live Shopify `products.json` endpoint, confirmed USD billing (via `cart.json`), confirmed page-2 pagination works, confirmed `robots.txt` doesn't disallow `/products.json` or `/collections/*/products.json`. Non-US labels (Earache, Big Scary Monsters' UK store, Pure Noise's UK/EU mirror) were excluded from the full candidate list or redirected to a confirmed US-specific storefront where one exists; none of those substitutions are in this batch.

**Tech Stack:** Python 3.9, `httpx` (via the shared `iter_products` helper), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`, no decorator needed) + `respx` (HTTP mocking) for tests. All four crawler files are auto-discovered and registered by `backend/main.py`'s bundled-crawler sync on next app startup — no manual registration step, no schema/API/frontend changes.

---

### Task 1: Prosthetic Records crawler

**Files:**
- Create: `backend/crawlers/prostheticrecords.py`
- Test: `backend/tests/test_prostheticrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_prostheticrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.prostheticrecords import Crawler

_PRODUCTS_URL = "https://shop.prostheticrecords.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "homewrecker. - Never Knowing When, But Knowing This Will End on Black Vinyl",
    "vendor": "homewrecker.",
    "handle": "homewrecker-never-knowing-when-but-knowing-this-will-end-on-black-vinyl",
    "tags": ["Aged-15+", "FCATNEW", "featured", "homewrecker.", "media", "music", "New Arrivals", "Vinyl"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/homewrecker-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "28.98", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Fires in the Distance - Air Not Meant For Us",
    "vendor": "Fires in the Distance",
    "handle": "fires-in-the-distance-air-not-meant-for-us",
    "tags": ["Pre-Order 08-28-26", "Pre-Orders", "Vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "26.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_from_title(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "homewrecker."
    assert item["title"] == "Never Knowing When, But Knowing This Will End on Black Vinyl"
    assert item["price"] == 28.98
    assert item["url"] == "https://shop.prostheticrecords.com/products/homewrecker-never-knowing-when-but-knowing-this-will-end-on-black-vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    # "Pre-Orders" (plural, no date) is the stable companion tag alongside the
    # dated "Pre-Order MM-DD-YY" tag — confirmed live on two products; matching
    # the plural form avoids needing a date regex.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Air Not Meant For Us (Pre-Order)"


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
    assert Crawler.site_name == "Prosthetic Records"
    assert Crawler.base_url == "https://shop.prostheticrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run (from `backend/`): `pytest tests/test_prostheticrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.prostheticrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/prostheticrecords.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "Pre-Orders"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Prosthetic Records"
    base_url: str = "https://shop.prostheticrecords.com"
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

Run: `pytest tests/test_prostheticrecords_crawler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/prostheticrecords.py backend/tests/test_prostheticrecords_crawler.py
git commit -m "metal-catalog-crawlers: add Prosthetic Records catalog crawler"
```

---

### Task 2: Peaceville crawler

**Files:**
- Create: `backend/crawlers/peaceville.py`
- Test: `backend/tests/test_peaceville_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_peaceville_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.peaceville import Crawler

_PRODUCTS_URL = "https://usa-peaceville.myshopify.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "The Wolf Race - Beacon",
    "vendor": "Winterfylleth",
    "handle": "wnt0wolfrb-lp",
    "tags": ["Music", "new", "Peaceville", "Vinyl LP", "Winterfylleth"],
    "product_type": "Vinyl LP",
    "images": [{"src": "https://cdn.shopify.com/winterfylleth-fallback.jpg"}],
    "variants": [
        {"title": "Default", "price": "27.99", "available": True},
    ],
}

# Real product confirmed live: available=false + lowercase "preorder" tag,
# no date embedded (unlike Prosthetic's dated "Pre-Order MM-DD-YY" tag).
_PREORDER_PRODUCT = {
    "title": "Goh-Ka - Pink Combo (Baby Pink & Magenta) Vinyl 2xLP",
    "vendor": "Sigh",
    "handle": "sih0gohkpm-lp",
    "tags": ["Forthcoming", "Music", "new", "Peaceville", "preorder", "Sigh", "Vinyl LP"],
    "product_type": "Vinyl LP",
    "images": [],
    "variants": [
        {"title": "Default", "price": "39.99", "available": False},
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
    assert item["artist"] == "Winterfylleth"
    assert item["title"] == "The Wolf Race - Beacon"
    assert item["price"] == 27.99
    assert item["url"] == "https://usa-peaceville.myshopify.com/products/wnt0wolfrb-lp"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Goh-Ka - Pink Combo (Baby Pink & Magenta) Vinyl 2xLP (Pre-Order)"


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
    assert Crawler.site_name == "Peaceville"
    assert Crawler.base_url == "https://usa-peaceville.myshopify.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_peaceville_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.peaceville'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/peaceville.py`. Note: `usa-peaceville.myshopify.com` is a US-specific storefront distinct from Peaceville's UK/EU store — confirmed billing in USD via `cart.json`. The root `/products.json` on this domain mixes in CDs (`product_type: "CD"`); the `/collections/vinyl/` endpoint is confirmed vinyl-only, so use that, not root.

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Peaceville"
    base_url: str = "https://usa-peaceville.myshopify.com"
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

Run: `pytest tests/test_peaceville_crawler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/peaceville.py backend/tests/test_peaceville_crawler.py
git commit -m "metal-catalog-crawlers: add Peaceville catalog crawler"
```

---

### Task 3: Season of Mist crawler

**Files:**
- Create: `backend/crawlers/seasonofmist.py`
- Test: `backend/tests/test_seasonofmist_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_seasonofmist_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.seasonofmist import Crawler

_PRODUCTS_URL = "https://shopusa.season-of-mist.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Drudkh - A Few Lines in Archaic Ukrainian - 3LP Gatefold",
    "vendor": "Season of Mist - North America",
    "handle": "drudkh-a-few-lines-in-archaic-ukrainian-3lp-gatefold",
    "tags": ["_visible"],
    "product_type": "",
    "body_html": "<p>The new album from Drudkh.</p>",
    "images": [{"src": "https://cdn.shopify.com/drudkh-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "45.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Windir - 1184 - DOUBLE LP GATEFOLD COLORED",
    "vendor": "Season of Mist - North America",
    "handle": "windir-1184-double-lp-gatefold-colored",
    "tags": ["_visible"],
    "product_type": "",
    "body_html": "<p>This title is available for pre-order. It will be available on 07/31/2026.</p>",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "38.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_dash_separated_title_not_vendor(crawler):
    # `vendor` is always "Season of Mist - North America" for every product
    # (confirmed live across music and merch alike) — never the artist.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Drudkh"
    assert item["title"] == "A Few Lines in Archaic Ukrainian - 3LP Gatefold"
    assert item["price"] == 45.00
    assert item["url"] == "https://shopusa.season-of-mist.com/products/drudkh-a-few-lines-in-archaic-ukrainian-3lp-gatefold"


@respx.mock
async def test_crawl_catalog_detects_preorder_from_body_html_not_tags(crawler):
    # No tag or product_type carries pre-order status on this store (both are
    # always "_visible"/"" respectively, confirmed live) — only body_html free
    # text does, e.g. "...available for pre-order. It will be available on
    # 07/31/2026." Tags/product_type checks would silently miss every pre-order.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Windir"
    assert items[0]["title"] == "1184 - DOUBLE LP GATEFOLD COLORED (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_PRODUCT, "title": "Untitled Compilation", "body_html": ""}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Season of Mist - North America"
    assert items[0]["title"] == "Untitled Compilation"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Season of Mist"
    assert Crawler.base_url == "https://shopusa.season-of-mist.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_seasonofmist_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.seasonofmist'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/seasonofmist.py`. Note: `shopusa.season-of-mist.com` is the confirmed US-billed (USD via `cart.json`) storefront, distinct from the label's EUR-billed global store at `shop.season-of-mist.com`.

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
_PREORDER_RE = re.compile(r'pre-?order', re.IGNORECASE)


class Crawler:
    site_name: str = "Season of Mist"
    base_url: str = "https://shopusa.season-of-mist.com"
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
        is_preorder = bool(_PREORDER_RE.search(product.get("body_html") or ""))

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = f"{album_title} (Pre-Order)" if is_preorder else album_title
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
        # `vendor` is always the label ("Season of Mist - North America"), never
        # the artist — the real artist is embedded in the title as
        # "Artist - Album - Format" (e.g. "Windir - 1184 - DOUBLE LP GATEFOLD
        # COLORED"). Reuses Run For Cover's non-greedy dash-split: it stops at
        # the FIRST " - ", so the album capture correctly keeps any further
        # dashes (the format descriptor) intact. Falls back to vendor only for
        # the rare title with no " - " at all.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 5 tests pass**

Run: `pytest tests/test_seasonofmist_crawler.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/seasonofmist.py backend/tests/test_seasonofmist_crawler.py
git commit -m "metal-catalog-crawlers: add Season of Mist catalog crawler"
```

---

### Task 4: 20 Buck Spin crawler

**Files:**
- Create: `backend/crawlers/twentybuckspin.py` (module filename must be a valid Python identifier — `20buckspin.py` would break `from crawlers.20buckspin import ...`; `site_name` still reads `"20 Buck Spin"`)
- Test: `backend/tests/test_twentybuckspin_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_twentybuckspin_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.twentybuckspin import Crawler

_PRODUCTS_URL = "https://20buckspin.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "ACHERONTAS - MALOCCHIO: THE SEVEN TONGUES OF AAHMON LP",
    "vendor": "Osmose",
    "handle": "acherontas-malocchio-the-seven-tongues-of-aahmon-lp",
    "product_type": "VINYL",
    "tags": ["A"],
    "images": [{"src": "https://cdn.shopify.com/acherontas-fallback.jpg"}],
    "variants": [
        {"title": "BLACK SMOKE GALAXY", "price": "24.99", "available": True, "sku": None},
    ],
}

# Real confirmed-live promo item: a "buy 3-4/5-6 regular LPs, get mystery
# LP(s) free" bundle, priced at $0.00 per variant — not a real release.
_MYSTERY_LP_PROMO = {
    "title": "*FREE MYSTERY LPs W/ APPLICABLE VINYL PURCHASE*",
    "vendor": "20 Buck Spin",
    "handle": "free-mystery-lps-w-applicable-vinyl-purchase",
    "product_type": "VINYL",
    "tags": ["A", "B", "C"],
    "images": [],
    "variants": [
        {"title": "1 MYSTERY LP (3-4 REGULAR PRICED LPS IN CART)", "price": "0.00", "available": True, "sku": None},
        {"title": "2 MYSTERY LPs (5-6 REGULAR PRICED LPS IN CART)", "price": "0.00", "available": True, "sku": None},
    ],
}

# Real confirmed-live merch item mislabeled product_type "VINYL" — the only
# non-$0 non-release listing found in this collection, so it needs its own
# title-keyword filter rather than the price<=0 filter above.
_TOTE_BAG = {
    "title": "20 BUCK SPIN - REIGN IN HELL TOTE BAG",
    "vendor": "20 Buck Spin",
    "handle": "20-buck-spin-reign-in-hell-tote-bag",
    "product_type": "VINYL",
    "tags": ["R"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "15.00", "available": True, "sku": ""},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_dash_split_not_vendor(crawler):
    # `vendor` alternates between the store's own imprint ("20 Buck Spin") and
    # labels it distributes ("Osmose", "Dark Descent") — never the artist,
    # confirmed live across the fetched sample.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "ACHERONTAS"
    assert item["title"] == "MALOCCHIO: THE SEVEN TONGUES OF AAHMON LP — BLACK SMOKE GALAXY"
    assert item["price"] == 24.99
    assert item["url"] == "https://20buckspin.com/products/acherontas-malocchio-the-seven-tongues-of-aahmon-lp"


@respx.mock
async def test_crawl_catalog_excludes_zero_priced_mystery_lp_promo(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MYSTERY_LP_PROMO]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_tote_bag_mislabeled_as_vinyl(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_TOTE_BAG]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_PRODUCT, "title": "Split Compilation Vol. 4"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Osmose"
    assert items[0]["title"] == "Split Compilation Vol. 4 — BLACK SMOKE GALAXY"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "20 Buck Spin"
    assert Crawler.base_url == "https://20buckspin.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_twentybuckspin_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.twentybuckspin'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/twentybuckspin.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
# Confirmed live in this collection: a tote bag with product_type "VINYL" —
# the format field can't be trusted to exclude it, only the title keyword can.
_MERCH_TITLE_RE = re.compile(r'tote bag|t-shirt|hoodie', re.IGNORECASE)


class Crawler:
    site_name: str = "20 Buck Spin"
    base_url: str = "https://20buckspin.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        raw_title = product.get("title", "")
        if _MERCH_TITLE_RE.search(raw_title):
            return []

        artist, album_title = cls._parse_artist_title(raw_title, product.get("vendor", ""))
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
            # Confirmed live: a "free mystery LP" promo bundle prices every
            # variant at $0.00 — not a real release, and no other signal
            # (tags are meaningless single letters, product_type is always
            # "VINYL") distinguishes it, so a zero/missing price is the filter.
            if not price:
                continue
            items.append({
                "artist": artist,
                "title": f"{album_title} — {variant.get('title', '')}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 6 tests pass**

Run: `pytest tests/test_twentybuckspin_crawler.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/crawlers/twentybuckspin.py backend/tests/test_twentybuckspin_crawler.py
git commit -m "metal-catalog-crawlers: add 20 Buck Spin catalog crawler"
```

---

### Task 5: Full backend test suite verification + spec update

**Files:** `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (modify)

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `pytest -q`
Expected: all tests pass, including all four new files and the existing `test_main.py` (bundled-crawler seeding globs `backend/crawlers/*.py`, so it picks up all four new files automatically without any test changes). Total catalog crawlers registered: eighteen.

- [ ] **Step 2: Update the design spec**

Add a "Technical grounding" subsection for each of the four sites to `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, following the existing per-site format (one paragraph describing the site's shape/quirks, a live JSON snippet, a bullet list of findings). Update the totals and any "N sites" references. Two things are worth calling out explicitly in the writeup:

1. Season of Mist's `body_html`-text pre-order detection is a new technique — every other site so far used a tag or `product_type`. Flag it as a fragile point: a future copy-editing change to the store's pre-order blurb wording would silently break detection with no test failure until a real pre-order item stops showing "(Pre-Order)".
2. 20 Buck Spin's non-release filtering (price `<= 0`, merch title keywords) was built from exactly two confirmed live exceptions in a ~10-product sample — there is likely a third category not yet seen (the research pass flagged an unconfirmed "10% OFF ALL YOUR ORDERS" item whose price was never fetched). Note this as a known gap rather than treating the current filter as exhaustive.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md
git commit -m "metal-catalog-crawlers: document technical grounding for four new sites"
```
