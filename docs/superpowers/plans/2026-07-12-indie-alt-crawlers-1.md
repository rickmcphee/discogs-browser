# Indie/Alt Catalog Crawlers (Batch 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five indie/alt catalog crawlers — Numero Group, Sub Pop Mega Mart, Fat Possum, Father/Daughter Records, and Temporary Residence Ltd — the fourth and final planned batch covering the 18 new US-only, USD-billed Shopify record stores researched for the Store tab. Developed independently in parallel with the other three batches (see the parallel-batch note in Task 6) — this plan's crawler-count math is based on the current unmerged `main` (13 pre-existing catalog crawlers), not on any sibling batch landing first.

**Architecture:** All five are Shopify storefronts. A live-data grounding pass (mandatory per this repo's convention — no invented fixtures) turned up two new wrinkles not seen in any of the 17 crawlers built across the three prior batches:

- **Sub Pop Mega Mart's clean, apparel-free `/collections/vinyl` silently excludes pre-order releases.** The store's root `/products.json` has pre-orders but is contaminated with T-shirts/bags/hats; `/collections/vinyl` is cleanly `product_type: "Music"` but two confirmed-live pre-order titles are absent from all pages of it. Rather than hit both endpoints and merge, this crawler uses `/collections/vinyl` and accepts the gap — no pre-order override, and pre-order titles simply won't appear until they leave pre-order status. This is a deliberate scope decision, not a bug.
- **Father/Daughter Records' bundle/grab-bag products are reliably identifiable by an empty `product_type` string, and collapse to one uninformative `"Default Title"` variant.** Confirmed live: every bundle product (`"Father/Daughter - Essentials LP Bundle"`, `"The Softies - The Bed I Made LP/CD + Tee Bundle"`, etc.) has `product_type: ""`, and no variant-title signal can tell vinyl from non-vinyl for a bundle's single "Default Title" variant. These are excluded entirely via a `product_type` presence check, rather than guessed at.

Numero Group also has a real, accepted data gap: for the bulk of its back-catalog reissues, `vendor` is a label placeholder (`"Numero"`/`"Numero Group"`) and the album `title` never contains the artist name either — there is no reliable artist field in `products.json` for most of this catalog. `vendor` is used directly anyway (same as every "vendor might be a label for various-artist-style releases" precedent already accepted elsewhere in this spec, e.g. Fat Wreck Chords' compilations), and this is documented as a known limitation, not silently hidden.

All five reuse `backend/shopify_catalog.py`'s `iter_products`/`has_tag`/`resolve_cover_image` helpers (none needed `strip_vendor_prefix`). No changes to `shopify_catalog.py`, the data model, the orchestration loop, the API, or the frontend.

**Tech Stack:** Python 3.9, `httpx` (via `iter_products`), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) + `respx`. Every commit is created via `git commit -F` (never `-m`) with the full `ai-generated`/`ai-model`/`ai-tool`/`ai-surface`/`ai-executor` trailer block, per `CLAUDE.md`'s AI-attribution rule, using the packaged commit helper (search `~/.claude/remote/plugins/*/skills/commit/commit-with-cleanup.sh` for its actual path in this environment — it is not at the path the skill's own docs claim).

---

### Task 1: Numero Group crawler

**Files:**
- Create: `backend/crawlers/numerogroup.py`
- Test: `backend/tests/test_numerogroup_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_numerogroup_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.numerogroup import Crawler

_PRODUCTS_URL = "https://numerogroup.com/collections/vinyl/products.json"

# Real confirmed-live product: `vendor` is the label, not the artist — this
# catalog's `title` never contains the real artist either (accepted gap, no
# reliable artist source exists for most of this back-catalog). Variants mix
# vinyl colors, cassette, CD, and digital on one product.
_PRODUCT = {
    "title": "Stratosphere",
    "vendor": "Numero Group",
    "handle": "duster-stratosphere",
    "tags": ["format:Cassette", "format:CD", "format:Digital", "format:LP", "Numero Group", "Punk", "Rock", "Slowcore"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/duster-fallback.jpg"}],
    "variants": [
        {"title": "Gold Dust Vinyl", "price": "27.00", "available": True},
        {"title": "Cassette", "price": "12.00", "available": False},
        {"title": "CD", "price": "12.00", "available": False},
        {"title": "Black LP Vinyl", "price": "25.00", "available": True},
        {"title": "Digital", "price": "10.00", "available": True},
    ],
}

# Real confirmed-live upcoming release: `vendor` is the real artist here (the
# exception to the label-placeholder rule), and the "Street Date" tag marks
# a pre-order — vinyl/CD variants are unavailable, only Digital is available.
_PREORDER_PRODUCT = {
    "title": "1985: The Miracle Year",
    "vendor": "Hüsker Dü",
    "handle": "1985-the-miracle-year",
    "tags": ["101325", "Deep Dive", "Domestic 3 day", "format:Boxset", "format:LP", "International 5 day", "Punk", "Street Date"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "4xLP Boxset (Divide And Conquer Vinyl) [Numero Exclusive]", "price": "110.00", "available": False},
        {"title": "2xCD Boxset", "price": "25.00", "available": False},
        {"title": "Digital", "price": "20.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cassette_cd_digital_includes_vinyl_variants(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Numero Group"
    assert items[0]["title"] == "Stratosphere — Gold Dust Vinyl"
    assert items[0]["price"] == 27.00


@respx.mock
async def test_crawl_catalog_includes_unavailable_boxset_variant_when_street_date_tagged(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Hüsker Dü"
    assert items[0]["title"] == "1985: The Miracle Year — 4xLP Boxset (Divide And Conquer Vinyl) [Numero Exclusive] (Pre-Order)"


@respx.mock
async def test_crawl_catalog_includes_glued_multiplier_lp_variant(crawler):
    product = {**_PRODUCT, "title": "1992-1998", "variants": [
        {"title": "5xLP Box", "price": "50.00", "available": True},
        {"title": "4xCD", "price": "40.00", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "1992-1998 — 5xLP Box"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Numero Group"
    assert Crawler.base_url == "https://numerogroup.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run (from `backend/`): `pytest tests/test_numerogroup_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.numerogroup'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/numerogroup.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_PREORDER_TAG = "Street Date"
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Numero Group"
    base_url: str = "https://numerogroup.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # `vendor` is a label placeholder ("Numero"/"Numero Group") for most
        # of this back-catalog — confirmed live, and the album title never
        # contains the real artist either. Used directly as a known,
        # accepted gap: there is no reliable artist source for most of this
        # catalog. Upcoming releases (Street Date tagged) are the exception,
        # where vendor genuinely is the artist.
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

- [ ] **Step 4: Run and confirm all 5 tests pass**

Run: `pytest tests/test_numerogroup_crawler.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

Write the commit message (with full AI-attribution trailer block) to a temp file and run the commit helper.

---

### Task 2: Sub Pop Mega Mart crawler

**Files:**
- Create: `backend/crawlers/subpopmegamart.py`
- Test: `backend/tests/test_subpopmegamart_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_subpopmegamart_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.subpopmegamart import Crawler

_PRODUCTS_URL = "https://megamart.subpop.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Hell + It's Dead",
    "vendor": "Girl and Girl",
    "handle": "girl-and-girl_hell-its-dead",
    "tags": ["format-digital", "label-sub-pop", "music"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Digital", "price": "2.00", "available": True},
    ],
}

_MULTI_FORMAT_PRODUCT = {
    "title": "Free Electricity",
    "vendor": "The Go",
    "handle": "the-go_free-electricity",
    "tags": ["format-cd", "format-digital", "format-loser-color-lp", "label-sub-pop", "music", "pre-order", "the-go"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/thego-fallback.jpg"}],
    "variants": [
        {"title": "Loser (color) LP", "price": "26.00", "available": True},
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "Digital", "price": "10.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_nothing_for_digital_only_release(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_cd_and_digital_includes_lp_variant(crawler):
    # This product's "pre-order" tag is irrelevant here — confirmed live,
    # /collections/vinyl silently excludes pre-order titles entirely, so a
    # real crawl would never see this product's tag at all. No pre-order
    # override exists in this crawler as a result (accepted scope decision).
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "The Go"
    assert items[0]["title"] == "Free Electricity — Loser (color) LP"
    assert items[0]["price"] == 26.00


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant(crawler):
    product = {**_MULTI_FORMAT_PRODUCT, "variants": [{**_MULTI_FORMAT_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_MULTI_FORMAT_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Sub Pop Mega Mart"
    assert Crawler.base_url == "https://megamart.subpop.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_subpopmegamart_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.subpopmegamart'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/subpopmegamart.py`. Note: root `/products.json` has pre-orders but mixes in apparel (T-Shirts/Bags/Hats); `/collections/vinyl` is confirmed live to be cleanly `product_type: "Music"` but silently excludes pre-order titles entirely — this crawler uses the clean endpoint and accepts that gap rather than merging both.

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Sub Pop Mega Mart"
    base_url: str = "https://megamart.subpop.com"
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

        items = []
        for variant in product.get("variants") or []:
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
                "title": f"{title} — {variant_title}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
```

- [ ] **Step 4: Run and confirm all 4 tests pass**

Run: `pytest tests/test_subpopmegamart_crawler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 3: Fat Possum crawler

**Files:**
- Create: `backend/crawlers/fatpossum.py`
- Test: `backend/tests/test_fatpossum_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_fatpossum_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.fatpossum import Crawler

_PRODUCTS_URL = "https://fatpossum.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "A Ass Pocket of Whiskey",
    "vendor": "R.L. Burnside",
    "handle": "a-ass-pocket-of-whiskey",
    "tags": ["1990s", "Fat Possum", "g::Blues", "View Collection", "Vinyl"],
    "product_type": "Releases",
    "images": [{"src": "https://cdn.shopify.com/rlburnside-fallback.jpg"}],
    "variants": [
        {"title": "Compact Disc", "price": "13.00", "available": True},
        {"title": "Vinyl", "price": "23.00", "available": True},
    ],
}

_MULTI_VARIANT_PRODUCT = {
    "title": "Active Listening: Night on Earth",
    "vendor": "Empath",
    "handle": "active-listening-night-on-earth",
    "tags": ["2010s", "Double Vinyl", "Fat Possum", "g::Rock", "View Collection", "Vinyl"],
    "product_type": "Releases",
    "images": [],
    "variants": [
        {"title": "Standard Vinyl", "price": "21.00", "available": True},
        {"title": "Deluxe Vinyl", "price": "22.00", "available": False},
        {"title": "Cassette", "price": "9.00", "available": True},
        {"title": "Compact Disc", "price": "12.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_vinyl_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "R.L. Burnside"
    assert item["title"] == "A Ass Pocket of Whiskey — Vinyl"
    assert item["price"] == 23.00
    assert item["url"] == "https://fatpossum.com/products/a-ass-pocket-of-whiskey"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_deluxe_vinyl_includes_standard(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_VARIANT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Active Listening: Night on Earth — Standard Vinyl"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Fat Possum"
    assert Crawler.base_url == "https://fatpossum.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_fatpossum_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.fatpossum'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/fatpossum.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_VINYL_RE = re.compile(r'\bvinyl\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Fat Possum"
    base_url: str = "https://fatpossum.com"
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

        items = []
        for variant in product.get("variants") or []:
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
                "title": f"{title} — {variant_title}",
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items
```

- [ ] **Step 4: Run and confirm all 3 tests pass**

Run: `pytest tests/test_fatpossum_crawler.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 4: Father/Daughter Records crawler

**Files:**
- Create: `backend/crawlers/fatherdaughterrecords.py`
- Test: `backend/tests/test_fatherdaughterrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_fatherdaughterrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.fatherdaughterrecords import Crawler

_PRODUCTS_URL = "https://fatherdaughterrecords.com/collections/vinyl/products.json"

# Real confirmed-live product: `vendor` is a label placeholder, never the
# artist. Also confirmed live: a preorder tag "Pre-order".
_PREORDER_PRODUCT = {
    "title": "Attic Abasement - Moonlight Passes On",
    "vendor": "Father/Daughter Records",
    "handle": "attic-abasement-moonlight-passes-on",
    "tags": ["Attic Abasement", "CD", "Digital download", "LP", "Merch", "Pre-order"],
    "product_type": "Music & Sound Recordings",
    "images": [{"src": "https://cdn.shopify.com/atticabasement-fallback.jpg"}],
    "variants": [
        {"title": "Vinyl", "price": "22.00", "available": True},
        {"title": "CD", "price": "10.00", "available": True},
        {"title": "Digital", "price": "1.00", "available": True},
    ],
}

# Real confirmed-live grab-bag/bundle product: empty product_type, collapses
# to a single non-descriptive "Default Title" variant — excluded entirely.
_BUNDLE_PRODUCT = {
    "title": "Anna McClellan - 3xLP Bundle",
    "vendor": "Father/Daughter Records",
    "handle": "anna-mcclellan-3xlp-bundle",
    "tags": ["Anna McClellan", "LP"],
    "product_type": "",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "58.00", "available": True},
    ],
}

# Real confirmed-live title with no dash — falls back to vendor.
_MYSTERY_PRODUCT = {
    "title": "Mystery LP",
    "vendor": "Father/Daughter Records",
    "handle": "mystery-lp",
    "tags": ["LP"],
    "product_type": "Music & Sound Recordings",
    "images": [],
    "variants": [
        {"title": "1 Mystery LP", "price": "7.00", "available": True},
        {"title": "2 Mystery LPs", "price": "12.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_excludes_cd_and_digital(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Attic Abasement"
    assert item["title"] == "Moonlight Passes On — Vinyl (Pre-Order)"
    assert item["price"] == 22.00


@respx.mock
async def test_crawl_catalog_excludes_bundle_product_with_empty_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_for_mystery_grab_bag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MYSTERY_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Father/Daughter Records"
    assert items[0]["title"] == "Mystery LP — 1 Mystery LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PREORDER_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Father/Daughter Records"
    assert Crawler.base_url == "https://fatherdaughterrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_fatherdaughterrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.fatherdaughterrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/fatherdaughterrecords.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
_PREORDER_TAG = "Pre-order"
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Father/Daughter Records"
    base_url: str = "https://fatherdaughterrecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # Bundle/grab-bag products collapse to a single non-descriptive
        # "Default Title" variant and are confirmed live to always have an
        # empty product_type — no variant-title signal can tell vinyl from
        # non-vinyl for these, so they're excluded entirely.
        if not (product.get("product_type") or "").strip():
            return []

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
        # `vendor` is a label placeholder, spelled two different ways live
        # ("Father/Daughter Records" and "Father/Daughter") — never the
        # artist. Real artist is embedded in the title as "Artist - Album"
        # for ordinary releases; grab-bag titles like "Mystery LP" have no
        # dash and fall back to vendor.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 4 tests pass**

Run: `pytest tests/test_fatherdaughterrecords_crawler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 5: Temporary Residence Ltd crawler

**Files:**
- Create: `backend/crawlers/temporaryresidence.py`
- Test: `backend/tests/test_temporaryresidence_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_temporaryresidence_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.temporaryresidence import Crawler

_PRODUCTS_URL = "https://temporaryresidence.com/collections/shop/products.json"

# Real confirmed-live product. Variant titles use a bullet (U+2022)
# immediately followed by a non-breaking space (U+00A0), not a regular
# space — "2xLP • Black Vinyl". The plain vinyl/LP substring match
# still works regardless of the exact separator character.
_PREORDER_PRODUCT = {
    "title": "Pyramid of the Sun – Anniversary Edition",
    "vendor": "Maserati",
    "handle": "trr384",
    "tags": ["Flag_Pre-Order", "Maserati"],
    "product_type": "Albums",
    "images": [{"src": "https://cdn.shopify.com/maserati-fallback.jpg"}],
    "variants": [
        {"title": "2xCD", "price": "14.00", "available": True},
        {"title": "2xLP • Black Vinyl", "price": "25.00", "available": True},
        {"title": "2xLP • Purple & Magenta Colored Vinyl", "price": "30.00", "available": True},
    ],
}

# Real confirmed-live apparel product mixed into the "shop" collection.
_TSHIRT_PRODUCT = {
    "title": "Temporary Residence Logo Tee",
    "vendor": "Temporary Residence",
    "handle": "logo-tee",
    "tags": [],
    "product_type": "T-Shirts",
    "images": [],
    "variants": [
        {"title": "S • BLACK  (Unisex Organic)", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live mistyped non-music product — product_type "Albums" but
# the single variant is literally "Book", not a vinyl format.
_BOOK_PRODUCT = {
    "title": "The Early Days Revisited",
    "vendor": "Nina Nastasia",
    "handle": "trr395-trr396-trr397-book",
    "tags": ["Nina Nastasia"],
    "product_type": "Albums",
    "images": [],
    "variants": [
        {"title": "Book", "price": "20.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_includes_bulleted_lp_variants_when_preorder_tagged(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Maserati"
    assert items[0]["title"] == "Pyramid of the Sun – Anniversary Edition — 2xLP • Black Vinyl (Pre-Order)"
    assert items[0]["price"] == 25.00


@respx.mock
async def test_crawl_catalog_excludes_tshirt_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_TSHIRT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_yields_nothing_for_mistyped_book_product(crawler):
    # Accepted gap: this product has product_type "Albums" but its only
    # variant is "Book", which doesn't match the vinyl/LP regex — correctly
    # excluded without needing a special case for it.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_BOOK_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PREORDER_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Temporary Residence Ltd"
    assert Crawler.base_url == "https://temporaryresidence.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_temporaryresidence_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.temporaryresidence'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/temporaryresidence.py`. Note: `/collections/vinyl/products.json` returns `{"products": []}` on this store (confirmed live) — `/collections/shop` is the working, non-standard collection slug.

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "shop"
_PREORDER_TAG = "Flag_Pre-Order"
_ALBUM_PRODUCT_TYPE = "Albums"
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Temporary Residence Ltd"
    base_url: str = "https://temporaryresidence.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # "shop" mixes in T-Shirts and Gift Cards alongside real releases —
        # confirmed live. A mistyped non-music "Book" product also carries
        # product_type "Albums", but its variant title doesn't match the
        # vinyl/LP regex below, so no special case is needed for it.
        if (product.get("product_type") or "").strip() != _ALBUM_PRODUCT_TYPE:
            return []

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

- [ ] **Step 4: Run and confirm all 4 tests pass**

Run: `pytest tests/test_temporaryresidence_crawler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 6: Full backend test suite verification, pre-PR spec-drift check, spec update

**Files:** `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (modify), plus any other spec found to have drifted

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `pytest -q`
Expected: all tests pass, including all five new files. Total catalog crawlers registered on this branch: eighteen (13 pre-existing + 5 from this batch) — this is the fourth and final planned batch, so once all four PRs eventually merge the true total is 13 + 4 + 4 + 5 + 5 = 31, not any single PR's own branch-time count of 18.

- [ ] **Step 2: Check for `main` movement, rebase if needed**

```bash
git fetch origin main
git log --oneline HEAD..origin/main
```

If `origin/main` has moved (e.g. because a sibling batch — metal, punk/hardcore pt1, or punk/hardcore/indie pt2 — merged while this branch was in progress), `git rebase origin/main` and re-run the full suite before the drift check below.

- [ ] **Step 3: Run the mandatory pre-PR spec-drift check**

Per `CLAUDE.md`'s "Pre-PR spec-drift check" rule: check `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, `2026-07-06-store-recommended-filter-design.md`, and `2026-07-08-collection-price-crawlers-design.md` for the same "N catalog crawlers"/"Store Crawlers" drift pattern already found and fixed three times across the three prior batches — it's likely present again here since this branch started from a pre-batch `main`, same as every other time.

- [ ] **Step 4: Update the design spec**

Add a "Technical grounding" subsection for each of the five sites to `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, following the existing per-site format. Two things need explicit callouts: Sub Pop Mega Mart's accepted pre-order-exclusion tradeoff (the first site in this spec where a *deliberate endpoint choice*, not a data limitation, causes a known gap), and Father/Daughter Records' empty-`product_type`-as-bundle-signal (a new, reliable variant of the "some products can't be classified by variant title alone" problem this spec has seen before, but solved differently each time — Craft Recordings used shirt-size detection, Kill Rock Stars used bundle-keyword detection, this one uses product-level `product_type` absence).

- [ ] **Step 5: Commit the spec update and any drift fixes** (same trailer/helper process as Task 1)

- [ ] **Step 6: This is the last of the four planned batches — after this merges (whenever it and its siblings land), consider whether the four-PR "Store tab expansion" project as a whole needs a closing note in `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`'s Out of scope / Success criteria sections reflecting the final, reconciled total (31 catalog crawlers) rather than each PR's individual branch-time count.**
