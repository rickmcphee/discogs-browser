# Punk/Hardcore Catalog Crawlers (Batch 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four punk/hardcore-genre catalog crawlers — Fearless Records, Flatspot Records, Rise Records, and Triple B Records — the second of four planned batches covering the 18 new US-only, USD-billed Shopify record stores researched for the Store tab. Brings the Store tab's total catalog sources to twenty-two.

**Architecture:** All four are Shopify storefronts and reuse `backend/shopify_catalog.py`'s `iter_products`/`has_tag`/`strip_vendor_prefix`/`resolve_cover_image` helpers and the `crawl_catalog()` contract. Fearless Records is the simplest shape in this batch (`vendor` = artist, single-variant, already vinyl-scoped — the Century Media/Epitaph/Peaceville/Prosthetic Records shape). Flatspot Records is nearly identical, with a two-tag-spelling pre-order signal and a small set of label-vendor various-artist compilations (accepted as correct, not a bug, per the Fat Wreck Chords precedent).

Rise Records and Triple B Records both needed a real live-data pass before writing fixtures (the repo's established convention — no invented data), and both surfaced genuinely new filter shapes not seen in any of the 18 crawlers built so far:

- **Rise Records**: its `/collections/vinyl` is empty; `/collections/all` works but is dominated by apparel, and — unlike every prior site — `product_type` itself is unreliable (a confirmed-live vinyl LP is mislabeled `product_type: "Album"`). The reliable signal is tags: every real vinyl product carries `"Music"` plus `"Vinyl LP"` or `"Vinyl 7"`; apparel never carries `"Music"` at all. This is a new *tag-based product-level filter* — similar in spirit to Equal Vision's `product_type` filter, but using tags because `product_type` can't be trusted here.
- **Triple B Records**: its `/collections/vinyl` is empty too; `/collections/all` works. Real vinyl variants are named by **color only** ("Baby Blue / Black Swirl (out of 200)") with no format keyword anywhere in the variant title, so every existing positive vinyl-regex filter in this codebase would match nothing here. The working filter is the reverse: a product-level exclusion by `product_type` (apparel, cassette-only, CD-only, digital-only, and one confirmed non-release "Shipping Protection" product), plus a narrow variant-level negative filter excluding exact `"CD"`/`"Digital"` siblings on otherwise-vinyl products. `vendor` is also confirmed unreliable here (mostly `"TRIPLE B RECORDS"`, with real casing variance and at least one genuinely different label, `"Combust"`) — artist comes from the title's `"Artist - Album"` dash split instead, reusing the same regex as Season of Mist/Run For Cover/20 Buck Spin.

**Tech Stack:** Python 3.9, `httpx` (via the shared `iter_products` helper), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) + `respx` for tests. All four crawler files are auto-discovered and registered by `backend/main.py`'s bundled-crawler sync on next app startup — no manual registration step, no schema/API/frontend changes, no changes to `shopify_catalog.py` (both new filter shapes are local to their own crawler files).

Per the repo's now-mandatory pre-PR spec-drift check and AI-attribution-trailer rule (`CLAUDE.md`), the final task in this plan runs the full drift check across every spec and creates every commit with the required `ai-generated`/`ai-model`/`ai-tool`/`ai-surface`/`ai-executor` trailers via `git commit -F`, not `-m`.

---

### Task 1: Fearless Records crawler

**Files:**
- Create: `backend/crawlers/fearlessrecords.py`
- Test: `backend/tests/test_fearlessrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_fearlessrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.fearlessrecords import Crawler

_PRODUCTS_URL = "https://store.fearlessrecords.com/collections/vinyl/products.json"

# Real confirmed-live product: preorder tag "preorder" plus a dated companion
# tag "PRE-ORDER M/D/YYYY" (no hyphen in the generic tag — Flatspot's is
# hyphenated "pre-order", a different spelling on a different store).
_PREORDER_PRODUCT = {
    "title": "\"Pure Ecstasy\" Black Vinyl",
    "vendor": "Beartooth",
    "handle": "beartooth-pure-ecstasy-black-vinyl",
    "tags": ["Beartooth", "PR77954", "PRE-ORDER 8/28/2026", "preorder", "Vinyl"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/beartooth-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

_PRODUCT = {
    "title": "\"god forbid a girl spits out her feelings!\" Iridescent Gold Vinyl",
    "vendor": "LOLO",
    "handle": "lolo-god-forbid-a-girl-spits-out-her-feelings-iridescent-gold-vinyl",
    "tags": ["LOLO", "Vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live unavailable, non-preorder product.
_SOLD_OUT_PRODUCT = {
    "title": "\"Happier Now\" SIGNED Ruby Vinyl",
    "vendor": "Movements",
    "handle": "movements-happier-now-signed-ruby-vinyl",
    "tags": ["Movements", "Vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "40.00", "available": False},
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
    assert item["artist"] == "LOLO"
    assert item["title"] == "\"god forbid a girl spits out her feelings!\" Iridescent Gold Vinyl"
    assert item["price"] == 25.00
    assert item["url"] == "https://store.fearlessrecords.com/products/lolo-god-forbid-a-girl-spits-out-her-feelings-iridescent-gold-vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "\"Pure Ecstasy\" Black Vinyl (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SOLD_OUT_PRODUCT]))
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
    assert Crawler.site_name == "Fearless Records"
    assert Crawler.base_url == "https://store.fearlessrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run (from `backend/`): `pytest tests/test_fearlessrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.fearlessrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/fearlessrecords.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Fearless Records"
    base_url: str = "https://store.fearlessrecords.com"
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

Run: `pytest tests/test_fearlessrecords_crawler.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

Write the commit message to a temp file (per `CLAUDE.md`'s AI-attribution-trailer rule) and commit via the packaged helper, e.g.:

```bash
# write message (including ai-generated/ai-model/ai-tool/ai-surface/ai-executor trailers) to a temp file, then:
git add backend/crawlers/fearlessrecords.py backend/tests/test_fearlessrecords_crawler.py
bash <path-to-commit-skill>/commit-with-cleanup.sh <message-file>
```

---

### Task 2: Flatspot Records crawler

**Files:**
- Create: `backend/crawlers/flatspotrecords.py`
- Test: `backend/tests/test_flatspotrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_flatspotrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.flatspotrecords import Crawler

_PRODUCTS_URL = "https://flatspotrecords.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "The S.E.T. - Self Evident Truth Moonphase Vinyl",
    "vendor": "The S.E.T.",
    "handle": "the-s-e-t-self-evident-truth-moonphase-vinyl",
    "tags": ["Aged-15+", "media", "music", "The S.E.T.", "vinyl"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/theset-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live preorder product — tag is dated "Pre-Order MM-DD-YY"
# (capitalized, hyphenated), a different spelling from Fearless's lowercase
# unhyphenated "preorder". Matched via a regex on tags starting with
# "pre-order" case-insensitively, not an exact has_tag match, since this
# store's generic and dated tag forms both start that way.
_PREORDER_PRODUCT = {
    "title": "Mizery - Mizery Baby Blue Opaque Vinyl",
    "vendor": "Mizery",
    "handle": "mizery-mizery-baby-blue-opaque-vinyl",
    "tags": ["Aged-15+", "media", "Mizery", "Pre-Order 03-20-26", "vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

_SOLD_OUT_PRODUCT = {
    "title": "Terror - Still Suffer Sky Blue / White Cornetto Vinyl (Flatspot Exclusive)",
    "vendor": "Terror",
    "handle": "terror-still-suffer-sky-blue-white-cornetto-vinyl",
    "tags": ["Aged-15+", "media", "music", "Terror", "vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": False},
    ],
}

# Real confirmed-live various-artist compilation where `vendor` is the label
# itself, not a band — this is the label correctly showing up as "artist" for
# a genuine various-artist release (same accepted shape as Fat Wreck Chords'
# compilations), not a bug needing special-casing. The title starts with
# "Flatspot Records - ", so strip_vendor_prefix genuinely fires here too.
_LABEL_COMPILATION_PRODUCT = {
    "title": "Flatspot Records - The Extermination Vol. 2 LP (Black)",
    "vendor": "Flatspot Records",
    "handle": "flatspot-records-the-extermination-vol-2-lp-black",
    "tags": ["Aged-15+", "Flatspot Records"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "20.00", "available": True},
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
    assert item["artist"] == "The S.E.T."
    assert item["title"] == "Self Evident Truth Moonphase Vinyl"
    assert item["price"] == 25.00
    assert item["url"] == "https://flatspotrecords.com/products/the-s-e-t-self-evident-truth-moonphase-vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Mizery Baby Blue Opaque Vinyl (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SOLD_OUT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_uses_label_as_artist_for_various_artist_compilation(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_LABEL_COMPILATION_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Flatspot Records"
    assert items[0]["title"] == "The Extermination Vol. 2 LP (Black)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Flatspot Records"
    assert Crawler.base_url == "https://flatspotrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_flatspotrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.flatspotrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/flatspotrecords.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, strip_vendor_prefix, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
# Matches both this store's generic "pre-order" tag and its dated
# "Pre-Order MM-DD-YY" tag in one check — has_tag's exact-match wouldn't
# catch the dated form, and this store uses both forms live.
_PREORDER_RE = re.compile(r'^pre-order', re.IGNORECASE)


class Crawler:
    site_name: str = "Flatspot Records"
    base_url: str = "https://flatspotrecords.com"
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
        is_preorder = cls._is_preorder(product)

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

    @staticmethod
    def _is_preorder(product: dict) -> bool:
        return any(_PREORDER_RE.match((t or "").strip()) for t in product.get("tags") or [])
```

- [ ] **Step 4: Run and confirm all 6 tests pass**

Run: `pytest tests/test_flatspotrecords_crawler.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 3: Rise Records crawler

**Files:**
- Create: `backend/crawlers/riserecords.py`
- Test: `backend/tests/test_riserecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_riserecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.riserecords import Crawler

_PRODUCTS_URL = "https://riserecords.com/collections/all/products.json"

# Real confirmed-live case: `product_type` is "Album" (normally a CD-ish
# value on this store) even though this is a genuine vinyl LP — product_type
# can't be trusted here. Tags are the only reliable signal.
_PRODUCT = {
    "title": "Crucial Moments - Royal Blue In Highlighter Yellow Color in Color - Vinyl LP",
    "vendor": "Bouncing Souls",
    "handle": "bnslcrmorb-lp",
    "tags": ["Bouncing Souls", "FINALFEW", "Music", "NONPRESALEVINYL", "Rise Records", "RISEPROMO", "Vinyl LP"],
    "product_type": "Album",
    "images": [{"src": "https://cdn.shopify.com/bouncingsouls-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "22.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "\"Arriba la L\" Black & White Smush Vinyl LP",
    "vendor": "Ladrones",
    "handle": "ladrall0bw-lp",
    "tags": ["Ladrones", "Music", "preorder", "Vinyl LP"],
    "product_type": "Vinyl LP",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "32.00", "available": False},
    ],
}

# Real confirmed-live apparel product mixed into the "all" collection — no
# "Music" tag at all, so the tag-based filter excludes it entirely.
_APPAREL_PRODUCT = {
    "title": "\"R\" Logo Black T-Shirt",
    "vendor": "Rise Records",
    "handle": "r-logo-black-t-shirt",
    "tags": ["Rise Records", "T-Shirt"],
    "product_type": "T-Shirt",
    "images": [],
    "variants": [
        {"title": "Small", "price": "20.00", "available": True},
        {"title": "Medium", "price": "20.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_vinyl_tagged_product_despite_misleading_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Bouncing Souls"
    assert item["title"] == "Crucial Moments - Royal Blue In Highlighter Yellow Color in Color - Vinyl LP"
    assert item["price"] == 22.00
    assert item["url"] == "https://riserecords.com/products/bnslcrmorb-lp"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "\"Arriba la L\" Black & White Smush Vinyl LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_apparel_with_no_music_tag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_APPAREL_PRODUCT]))
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
    assert Crawler.site_name == "Rise Records"
    assert Crawler.base_url == "https://riserecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_riserecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.riserecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/riserecords.py`. Note: `/collections/vinyl/products.json` returns `{"products": []}` on this store — confirmed live — so `/collections/all` is used instead, filtered by tag.

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PREORDER_TAG = "preorder"
_COLLECTION_SLUG = "all"
_MUSIC_TAG = "Music"
_VINYL_TAGS = ("Vinyl LP", "Vinyl 7")


class Crawler:
    site_name: str = "Rise Records"
    base_url: str = "https://riserecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        if not cls._is_vinyl(product):
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

    @staticmethod
    def _is_vinyl(product: dict) -> bool:
        # The collection is "all" (the "vinyl" slug is empty) and product_type
        # is unreliable — a confirmed-live vinyl LP had product_type "Album".
        # Tags are the reliable signal: every real vinyl product carries "Music"
        # plus "Vinyl LP" or "Vinyl 7"; apparel never carries "Music" at all.
        return has_tag(product, _MUSIC_TAG) and any(has_tag(product, t) for t in _VINYL_TAGS)
```

- [ ] **Step 4: Run and confirm all 4 tests pass**

Run: `pytest tests/test_riserecords_crawler.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 4: Triple B Records crawler

**Files:**
- Create: `backend/crawlers/triplebrecords.py`
- Test: `backend/tests/test_triplebrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_triplebrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.triplebrecords import Crawler

_PRODUCTS_URL = "https://triplebrecords.net/collections/all/products.json"

# Real confirmed-live product with 8 variants: vinyl color variants named
# with NO format keyword at all ("Baby Blue / Black Swirl (out of 200)"),
# plus one standalone "CD" variant. No positive vinyl-regex would match any
# of these color names — the filter here is a narrow negative one instead.
_PRODUCT = {
    "title": "Missing Link - Watch Me Bleed CD / LP",
    "vendor": "TRIPLE B RECORDS",
    "handle": "missing-link-watch-me-bleed-pre-order",
    "tags": ["Aged-15+", "Bandcamp"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/missinglink-fallback.jpg"}],
    "variants": [
        {"title": "Baby Blue / Black Swirl (out of 200)", "price": "25.00", "available": True},
        {"title": "Baby Blue w/ Black Splatter (out of 800)", "price": "25.00", "available": True},
        {"title": "CD", "price": "10.00", "available": True},
        {"title": "Gold Nugget (out of 150) *BBB Exclusive*", "price": "25.00", "available": False},
        {"title": "Black Ice w/Gold Splatter (out of 350)", "price": "25.00", "available": False},
    ],
}

# Real confirmed-live exception: vendor is a distributed band's own name, not
# "TRIPLE B RECORDS" — the title's dash-split is what actually gives the
# right artist regardless.
_COMBUST_PRODUCT = {
    "title": "COMBUST - Another Life CD / LP",
    "vendor": "Combust",
    "handle": "combust-another-life-cd-lp",
    "tags": ["CD", "media", "music", "Triple B Records", "Vinyl"],
    "product_type": "CD/Vinyl",
    "images": [],
    "variants": [
        {"title": "Black", "price": "25.00", "available": True},
        {"title": "CD", "price": "10.00", "available": True},
    ],
}

# Real confirmed-live non-release product — a shipping-insurance add-on, not
# a record at all. Excluded entirely via product_type.
_SHIPPING_PROTECTION_PRODUCT = {
    "title": "Guide Package Protection",
    "vendor": "Guide",
    "handle": "guide-package-protection",
    "tags": ["guide_0-99.99-0"],
    "product_type": "Shipping Protection",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "2.00", "available": True},
    ],
}

# Real confirmed-live apparel product mixed into the "all" collection.
_TSHIRT_PRODUCT = {
    "title": "Triple B Records Logo T-Shirt",
    "vendor": "TRIPLE B RECORDS",
    "handle": "triple-b-records-logo-t-shirt",
    "tags": ["Aged-15+"],
    "product_type": "T-Shirt",
    "images": [],
    "variants": [
        {"title": "Small", "price": "20.00", "available": True},
    ],
}

# Real confirmed-live legacy listing with empty tags/product_type — title
# parsing is the only signal that works for these.
_LEGACY_PRODUCT = {
    "title": "AMERICA'S HARDCORE COMPILATION Volume 1",
    "vendor": "TRIPLE B RECORDS",
    "handle": "americas-hardcore-compilation-volume-1",
    "tags": [],
    "product_type": "",
    "images": [],
    "variants": [
        {"title": "Black", "price": "15.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_color_variants_with_no_format_keyword(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Missing Link"
    assert items[0]["title"] == "Watch Me Bleed CD / LP — Baby Blue / Black Swirl (out of 200)"
    assert items[0]["price"] == 25.00
    assert all("CD" != i["title"].split("— ")[-1] for i in items)


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_not_unreliable_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_COMBUST_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "COMBUST"
    assert items[0]["title"] == "Another Life CD / LP — Black"


@respx.mock
async def test_crawl_catalog_excludes_shipping_protection_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SHIPPING_PROTECTION_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_tshirt_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_TSHIRT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_legacy_listing_with_empty_tags_and_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_LEGACY_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "TRIPLE B RECORDS"
    assert items[0]["title"] == "AMERICA'S HARDCORE COMPILATION Volume 1 — Black"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Triple B Records"
    assert Crawler.base_url == "https://triplebrecords.net"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_triplebrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.triplebrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/triplebrecords.py`. Note: `/collections/vinyl/products.json` returns `{"products": []}` on this store too (same empty-slug situation as Rise Records) — confirmed live — so `/collections/all` is used, which returns the same catalog as the bare `/products.json` root endpoint.

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "all"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
# Confirmed live product_type distribution: real vinyl releases are "Vinyl",
# "CD/Vinyl" (mixed-format, needs the variant filter below), or "" (legacy
# listings with no metadata at all). Everything else here is apparel,
# cassette-only, CD-only, digital-only, or the one confirmed non-release
# "Shipping Protection" add-on product — none of those have a vinyl variant
# worth salvaging.
_EXCLUDED_PRODUCT_TYPES = {
    "t-shirt", "shirt", "hoodie", "bottoms", "accessory", "accessories",
    "shipping protection", "cassette", "cd", "digital",
}
# Real vinyl color variants here carry NO format keyword at all ("Baby Blue /
# Black Swirl (out of 200)") — the opposite of Fat Wreck Chords/Secretly
# Store/Deathwish Inc, where a positive vinyl-regex works. Here only the CD/
# Digital siblings on an otherwise-vinyl product need excluding.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cd|digital( download)?)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Triple B Records"
    base_url: str = "https://triplebrecords.net"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        product_type = (product.get("product_type") or "").strip().lower()
        if product_type in _EXCLUDED_PRODUCT_TYPES:
            return []

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
            if _NON_VINYL_VARIANT_RE.match(variant_title.strip()):
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
        # `vendor` is usually "TRIPLE B RECORDS" (with real live casing
        # variance) but not always — a distributed band ("Combust") shows up
        # as its own vendor — so vendor can't be trusted either way. The
        # title's "Artist - Album" dash split is reliable regardless of what
        # vendor says.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 6 tests pass**

Run: `pytest tests/test_triplebrecords_crawler.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 5: Full backend test suite verification, pre-PR spec-drift check, spec update

**Files:** `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (modify), plus any other spec found to have drifted

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `pytest -q`
Expected: all tests pass, including all four new files. Total catalog crawlers registered: twenty-two.

- [ ] **Step 2: Rebase onto latest `origin/main` before checking for drift**

`main` may have advanced since this branch started (it did during the previous batch). Fetch and rebase, then re-run the full suite, before doing the drift check below — checking against a stale `main` would miss real drift.

```bash
git fetch origin main:main
git rebase main
cd backend && pytest -q
```

- [ ] **Step 3: Run the mandatory pre-PR spec-drift check**

Per `CLAUDE.md`'s "Pre-PR spec-drift check" rule: `grep -rl` across `docs/superpowers/specs/` for files/symbols/section names/UI strings touched by this diff (crawler count references, "Store Management" section naming, any list of catalog-source names), confirm each match still describes what actually shipped, and amend any spec found to have drifted as its own commit — even drift this branch didn't cause. Update the crawler-count references in `2026-07-05-in-stock-crawler-design.md` from eighteen to twenty-two, and check the same "13/18-crawler-count" pattern this repo has already needed fixing twice for in any spec referencing the total.

- [ ] **Step 4: Update the design spec**

Add a "Technical grounding" subsection for each of the four sites to `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, following the existing per-site format. Two things need explicit callouts:

1. Rise Records' and Triple B Records' `/collections/vinyl` both return `{"products": []}` — the second and third confirmed cases of this in the spec (after Rise's "all" precedent doesn't yet exist in the doc, this is actually the *first* — note it clearly since a future crawler author might otherwise assume `/collections/vinyl` always works).
2. Two new filter shapes: Rise Records' tag-based product filter (`Music` + `Vinyl LP`/`Vinyl 7`, because `product_type` is confirmed unreliable there) and Triple B Records' negative-variant-plus-product-type-exclusion filter (because real vinyl variant titles carry no format keyword at all, the opposite problem from every positive-regex site). Update the "Format filter comes in N shapes" Decisions bullet to add these two, bringing the total to six shapes.

- [ ] **Step 5: Commit the spec update and any drift fixes** (same trailer/helper process as Task 1)
