# Punk/Hardcore/Indie Catalog Crawlers (Batch 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five punk/hardcore/indie catalog crawlers — Closed Casket Activities, Big Scary Monsters USA, Kill Rock Stars, Saddle Creek, and Polyvinyl Record Co. — the third of four planned batches covering the 18 new US-only, USD-billed Shopify record stores researched for the Store tab. Developed independently in parallel with the other batches (see the parallel-batch note in Task 6) — this plan's crawler-count math is based on the current unmerged `main` (13 pre-existing catalog crawlers), not on any sibling batch landing first.

**Architecture:** All five are Shopify storefronts. A live-data grounding pass (mandatory per this repo's convention — no invented fixtures) turned up three new wrinkles not seen in any of the 17 crawlers built in the two prior batches:

- **Closed Casket Activities and Big Scary Monsters USA both have genuine single-variant `"Default Title"` vinyl products mixed into the same collection as multi-variant LP+CD products.** A positive vinyl-regex filter (the shape used by most prior crawlers) would wrongly exclude the `"Default Title"` ones, since that literal string carries no format keyword at all. Both crawlers use a **negative** filter instead — exclude only variants titled exactly `"CD"`/`"Cassette"`/`"Digital"` — which correctly keeps `"Default Title"` while still dropping the CD/Cassette/Digital siblings on multi-variant products. Both also need a conditional display-title rule: don't append the variant name when it's literally `"Default Title"` (uninformative), but do append it otherwise (real color/pressing info).
- **Kill Rock Stars mixes vinyl, CD, digital, apparel, and "bundle" variants inside one large `/collections/all` catalog**, including one confirmed product with 38 variants spanning vinyl-only, CD-only, vinyl+CD-bundle, T-shirt-only, and sticker-pack-only options crossed with shirt sizes. A pure positive vinyl-regex (matching `LP`/inch-mark patterns) turns out to be sufficient to exclude apparel/digital/CD without any product-level tag or `product_type` gate at all — except that a bundle variant like `"LP + CD Bundle / X-Small"` also contains the substring `"LP"` and would false-positive, so an additional exclusion for variant titles containing `"bundle"` or `"+"` is layered on top.
- **Polyvinyl Record Co.'s `vendor` is never the artist — not even for the label's own house releases.** Even a genuine Polyvinyl-catalog title like `"Deerhoof - Breakup Song"` has `vendor: "Polyvinyl Records"` (the label), not `"Deerhoof"`. 73% of a 250-product sample is tagged `"Non-Polyvinyl"` (third-party labels distributed through this storefront, e.g. `"Atlantic Records"`, `"4AD"`, `"Nitro"`) — those are included the same as house releases in this crawler, since they're genuinely purchasable vinyl on the collection; only the artist-attribution source differs (title, never vendor).

Saddle Creek needed a live-verified correction to its own prior research notes ("small/dated catalog" was wrong — it's 337 products) and needs a `product_type == "Music"` gate, since `/collections/all` mixes in apparel/goods/gift-cards the same way Rise Records' and Triple B Records' collections did in the prior batch.

All five reuse `backend/shopify_catalog.py`'s `iter_products`/`has_tag`/`resolve_cover_image` helpers (none needed `strip_vendor_prefix` — every site here either uses `vendor` directly with no prefix, or ignores `vendor` for artist entirely). No changes to `shopify_catalog.py`, the data model, the orchestration loop, the API, or the frontend.

**Tech Stack:** Python 3.9, `httpx` (via `iter_products`), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) + `respx`. Every commit is created via `git commit -F` (never `-m`) with the full `ai-generated`/`ai-model`/`ai-tool`/`ai-surface`/`ai-executor` trailer block, per `CLAUDE.md`'s AI-attribution rule, using the packaged `commit-with-cleanup.sh` helper.

---

### Task 1: Closed Casket Activities crawler

**Files:**
- Create: `backend/crawlers/closedcasketactivities.py`
- Test: `backend/tests/test_closedcasketactivities_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_closedcasketactivities_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.closedcasketactivities import Crawler

_PRODUCTS_URL = "https://closedcasketactivities.com/collections/vinyl/products.json"

# Real confirmed-live product: single "Default Title" variant carries NO
# format keyword at all — a positive vinyl-regex filter would wrongly exclude
# this, since it's genuinely the only (vinyl) variant. Also a real split
# release ("Artist1 / Artist2 - Title"), and the title itself literally
# contains "**PREORDER**" in addition to the tag-driven preorder detection —
# a confirmed, accepted cosmetic redundancy (not stripped).
_PREORDER_PRODUCT = {
    "title": "Whirr / Luster - Whirr & Luster Split - LP -  Black Ice w/ Blue Splatter **PREORDER**",
    "vendor": "Free Whirl Records",
    "handle": "whirr-luster-whirr-luster-split-lp-black-ice-w-blue-splatter",
    "tags": ["__label:Pre-Order", "media", "mediamail"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live multi-variant product mixing LP color variants with a
# bare "CD" sibling — needs the negative filter, not a positive one.
_MULTI_VARIANT_PRODUCT = {
    "title": "100 Demons - Embrace The Black Light",
    "vendor": "Closed Casket Activities",
    "handle": "100-demons-embrace-the-black-light",
    "tags": ["__label:New", "media", "mediamail", "new"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/100demons-fallback.jpg"}],
    "variants": [
        {"title": "LP - Cloudy Bone in Tan", "price": "25.00", "available": True},
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "LP - Gold Black Marble", "price": "25.00", "available": False},
    ],
}

# Real confirmed-live secondary preorder tag convention — lowercase
# "preorder", no "__label:" prefix, no title suffix.
_SECONDARY_PREORDER_PRODUCT = {
    "title": "Eye Flys - Exigent Circumstance",
    "vendor": "Closed Casket Activities",
    "handle": "eye-flys-exigent-circumstance",
    "tags": ["preorder", "media", "mediamail"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "LP - Black", "price": "25.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_single_default_title_variant_without_appending_it(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Whirr / Luster"
    assert item["title"] == "Whirr & Luster Split - LP -  Black Ice w/ Blue Splatter **PREORDER** (Pre-Order)"
    assert item["price"] == 25.00
    assert item["url"] == "https://closedcasketactivities.com/products/whirr-luster-whirr-luster-split-lp-black-ice-w-blue-splatter"


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_lp_color_variants(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_VARIANT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "100 Demons"
    assert items[0]["title"] == "Embrace The Black Light — LP - Cloudy Bone in Tan"


@respx.mock
async def test_crawl_catalog_detects_secondary_lowercase_preorder_tag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SECONDARY_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Exigent Circumstance — LP - Black (Pre-Order)"


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_MULTI_VARIANT_PRODUCT, "title": "Split Compilation", "variants": [_MULTI_VARIANT_PRODUCT["variants"][0]]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Closed Casket Activities"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_MULTI_VARIANT_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Closed Casket Activities"
    assert Crawler.base_url == "https://closedcasketactivities.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run (from `backend/`): `pytest tests/test_closedcasketactivities_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.closedcasketactivities'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/closedcasketactivities.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
# Two pre-order tag conventions confirmed live on this store: "__label:Pre-Order"
# and a bare lowercase "preorder" — a substring search covers both.
_PREORDER_RE = re.compile(r'pre-?order', re.IGNORECASE)
# A positive vinyl-regex would wrongly exclude the confirmed-live single
# "Default Title" vinyl variant (no format keyword at all) — a narrow
# negative filter for the actual non-vinyl siblings is used instead.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cd|cassette|digital( download)?)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Closed Casket Activities"
    base_url: str = "https://closedcasketactivities.com"
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
        is_preorder = cls._is_preorder(product)

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available") and not is_preorder:
                continue
            variant_title = variant.get("title", "")
            if _NON_VINYL_VARIANT_RE.match(variant_title.strip()):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = (
                album_title if variant_title.strip().lower() == "default title"
                else f"{album_title} — {variant_title}"
            )
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
    def _is_preorder(product: dict) -> bool:
        return any(_PREORDER_RE.search(t or "") for t in product.get("tags") or [])

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        # `vendor` is always the label here, never the artist — real artist
        # (sometimes two, for splits: "Artist1 / Artist2") is embedded in the
        # title as "Artist - Album".
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 6 tests pass**

Run: `pytest tests/test_closedcasketactivities_crawler.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

Write the commit message (with full AI-attribution trailer block) to a temp file and run:

```bash
git add backend/crawlers/closedcasketactivities.py backend/tests/test_closedcasketactivities_crawler.py
bash <path-to-commit-skill>/commit-with-cleanup.sh <message-file>
```

---

### Task 2: Big Scary Monsters USA crawler

**Files:**
- Create: `backend/crawlers/bigscarymonstersusa.py`
- Test: `backend/tests/test_bigscarymonstersusa_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_bigscarymonstersusa_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.bigscarymonstersusa import Crawler

_PRODUCTS_URL = "https://usa.bsmrocks.com/collections/vinyl/products.json"

# Real confirmed-live product: single "Default Title" variant, no format
# keyword at all — same landmine as Closed Casket Activities.
_PRODUCT = {
    "title": "Pancho - Pancho",
    "vendor": "Pancho",
    "handle": "pancho-pancho",
    "tags": ["Vinyl"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/pancho-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.99", "available": True},
    ],
}

# Real confirmed-live vendor-is-unreliable case: this exact artist ("Lakes")
# has vendor = "Lakes" correctly on one release but vendor = "Big Scary
# Monsters USA" (the store, not the artist) on this one — title parsing is
# the only consistent source. Note the en dash "–", not a hyphen, in the
# confirmed-live title of a different product; this one uses a hyphen.
_UNRELIABLE_VENDOR_PRODUCT = {
    "title": "Lakes - Slow Fade",
    "vendor": "Big Scary Monsters USA",
    "handle": "lakes-slow-fade",
    "tags": ["Vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Blue LP", "price": "25.99", "available": True},
        {"title": "CD", "price": "10.99", "available": True},
    ],
}

# Real confirmed-live title using an en dash "–" instead of a hyphen "-".
_EN_DASH_PRODUCT = {
    "title": "Alpha Male Tea Party – Reptilian Brain",
    "vendor": "Alpha Male Tea Party",
    "handle": "alpha-male-tea-party-reptilian-brain",
    "tags": ["Alpha Male Tea Party", "CD", "UK Math", "Vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Orange Galaxy LP", "price": "25.99", "available": True},
        {"title": "CD", "price": "10.99", "available": True},
    ],
}

# Real confirmed-live preorder-tagged product.
_PREORDER_PRODUCT = {
    "title": "Thank - I Have A Physical Body That Can Be Harmed",
    "vendor": "Thank",
    "handle": "thank-i-have-a-physical-body-that-can-be-harmed",
    "tags": ["Pre-order", "Thank", "Vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Purple Rain LP", "price": "24.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_single_default_title_variant_without_appending_it(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Pancho"
    assert item["title"] == "Pancho"
    assert item["price"] == 25.99
    assert item["url"] == "https://usa.bsmrocks.com/products/pancho-pancho"


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_not_unreliable_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_UNRELIABLE_VENDOR_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Lakes"
    assert items[0]["title"] == "Slow Fade — Blue LP"


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_using_en_dash(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_EN_DASH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Alpha Male Tea Party"
    assert items[0]["title"] == "Reptilian Brain — Orange Galaxy LP"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "I Have A Physical Body That Can Be Harmed — Purple Rain LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Big Scary Monsters USA"
    assert Crawler.base_url == "https://usa.bsmrocks.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_bigscarymonstersusa_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.bigscarymonstersusa'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/bigscarymonstersusa.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
# Confirmed live: titles use either a hyphen or an en dash ("–") as the
# artist/album separator.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*[-–]\s*(?P<album>.+)$')
_PREORDER_TAG = "Pre-order"
# Same landmine as Closed Casket Activities: a confirmed-live single
# "Default Title" vinyl variant carries no format keyword, so the filter here
# is negative (exclude the real non-vinyl siblings) rather than positive.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cd|cassette|digital( download)?)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Big Scary Monsters USA"
    base_url: str = "https://usa.bsmrocks.com"
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
            if _NON_VINYL_VARIANT_RE.match(variant_title.strip()):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = (
                album_title if variant_title.strip().lower() == "default title"
                else f"{album_title} — {variant_title}"
            )
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
        # `vendor` is confirmed unreliable — the same artist ("Lakes") shows
        # up correctly as vendor on one release and as the store's own name
        # on another. Title parsing is the only consistent source.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 6 tests pass**

Run: `pytest tests/test_bigscarymonstersusa_crawler.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 3: Kill Rock Stars crawler

**Files:**
- Create: `backend/crawlers/killrockstars.py`
- Test: `backend/tests/test_killrockstars_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_killrockstars_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.killrockstars import Crawler

_PRODUCTS_URL = "https://killrockstars.com/collections/all/products.json"

_PRODUCT = {
    "title": "All Bets Are Off",
    "vendor": "Tamar Aphek",
    "handle": "all-bets-are-off",
    "tags": ["CD", "Digital Album", "Preorder", "Tamar Aphek", "Vinyl"],
    "product_type": "Album",
    "images": [{"src": "https://cdn.shopify.com/tamaraphek-fallback.jpg"}],
    "variants": [
        {"title": "LP - Violet", "price": "25.00", "available": True},
        {"title": "LP - Black", "price": "25.00", "available": True},
        {"title": "CD", "price": "15.00", "available": True},
    ],
}

_GLUED_FORMAT_PRODUCT = {
    "title": "100 Songs (A Master Class In Songwriting)",
    "vendor": "Jad Fair",
    "handle": "100-songs-a-master-class-in-songwriting",
    "tags": ["Jad Fair", "Vinyl"],
    "product_type": "Album",
    "images": [],
    "variants": [
        {"title": "2LP", "price": "30.00", "available": True},
        {"title": "Jad Fair Bundle", "price": "40.00", "available": True},
    ],
}

# Real confirmed-live 38-variant bundle product (trimmed here to the
# variants that matter for the filter logic): pure-vinyl variants, a
# vinyl+CD bundle (must be excluded despite containing "LP"), a pure-CD
# variant, and a T-shirt-only variant.
_BUNDLE_PRODUCT = {
    "title": "9 Sad Symphonies",
    "vendor": "Kate Nash",
    "handle": "9-sad-symphonies",
    "tags": ["CD", "Kate Nash", "Vinyl"],
    "product_type": "Album",
    "images": [],
    "variants": [
        {"title": "LP - Baby Blue Vinyl / No Shirt", "price": "26.00", "available": True},
        {"title": "LP + CD Bundle / X-Small", "price": "75.00", "available": True},
        {"title": "CD / No Shirt", "price": "16.00", "available": True},
        {"title": "T-Shirt / X-Small", "price": "30.00", "available": True},
    ],
}

# Real confirmed-live cross-title bundle — neither variant contains an LP or
# inch-mark token, so this whole product yields zero items. Accepted gap.
_CROSS_TITLE_BUNDLE_PRODUCT = {
    "title": "\"All Of My Love\" - Habibi Bundle",
    "vendor": "Habibi",
    "handle": "all-of-my-love-habibi-bundle",
    "tags": ["CD", "Habibi", "Vinyl"],
    "product_type": "Album",
    "images": [],
    "variants": [
        {"title": "Habibi + Anywhere But Here - Pink Vinyl Bundle", "price": "50.00", "available": True},
        {"title": "Habibi + Anywhere But Here - CD Bundle", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live 7" release with no "Vinyl" tag at all — confirms the
# crawler must not gate on a product-level tag, only the per-variant regex.
_NO_VINYL_TAG_PRODUCT = {
    "title": "Alternate Versions from Either/Or",
    "vendor": "Elliott Smith",
    "handle": "either-or-alternate-takes",
    "tags": ["Elliott Smith", "Released"],
    "product_type": "7\"",
    "images": [],
    "variants": [
        {"title": "7\" - White Vinyl", "price": "12.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_lp_color_variants(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Tamar Aphek"
    assert items[0]["title"] == "All Bets Are Off — LP - Violet"


@respx.mock
async def test_crawl_catalog_includes_glued_2lp_excludes_bundle_containing_lp_substring(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_GLUED_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "100 Songs (A Master Class In Songwriting) — 2LP"


@respx.mock
async def test_crawl_catalog_excludes_lp_cd_bundle_and_tshirt_includes_pure_lp_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "9 Sad Symphonies — LP - Baby Blue Vinyl / No Shirt"


@respx.mock
async def test_crawl_catalog_yields_nothing_for_cross_title_bundle_with_no_lp_token(crawler):
    # Accepted gap: neither variant title contains "LP" or an inch mark, so
    # this genuinely-vinyl cross-release bundle yields zero items.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_CROSS_TITLE_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_variant_from_product_with_no_vinyl_tag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_NO_VINYL_TAG_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Elliott Smith"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Kill Rock Stars"
    assert Crawler.base_url == "https://killrockstars.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_killrockstars_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.killrockstars'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/killrockstars.py`. Note: `/collections/vinyl` returns `{"products": []}` on this store (confirmed live) — `/collections/all` is used, and no product-level tag/type gate is applied at all (the confirmed-live Elliott Smith 7" has no `"Vinyl"` tag, so gating on it would wrongly exclude a real release) — the per-variant regex alone does all the work.

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "all"
_PREORDER_TAG = "Preorder"
_VINYL_VARIANT_RE = re.compile(r'\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)
# Confirmed live: a 38-variant bundle product mixes pure-vinyl, pure-CD,
# vinyl+CD-bundle, and T-shirt-only variants. A vinyl+CD-bundle variant title
# ("LP + CD Bundle / X-Small") contains "LP" and would false-positive on the
# regex above — this excludes any variant that's a bundle, even one whose
# LP-only sibling passes.
_BUNDLE_RE = re.compile(r'bundle|\+', re.IGNORECASE)


class Crawler:
    site_name: str = "Kill Rock Stars"
    base_url: str = "https://killrockstars.com"
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
            if not _VINYL_VARIANT_RE.search(variant_title) or _BUNDLE_RE.search(variant_title):
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

- [ ] **Step 4: Run and confirm all 7 tests pass**

Run: `pytest tests/test_killrockstars_crawler.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 4: Saddle Creek crawler

**Files:**
- Create: `backend/crawlers/saddlecreek.py`
- Test: `backend/tests/test_saddlecreek_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_saddlecreek_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.saddlecreek import Crawler

_PRODUCTS_URL = "https://saddle-creek.com/collections/all/products.json"

_PRODUCT = {
    "title": "Adam Cayton-Holland Performs His Signature Bits",
    "vendor": "Adam Cayton-Holland",
    "handle": "adam-cayton-holland-performs-his-signature-bits",
    "tags": ["colored vinyl", "LP", "meta-related-collection-adam-cayton-holland", "vinyl"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/adamcaytonholland-fallback.jpg"}],
    "variants": [
        {"title": "Colored LP", "price": "18.99", "available": True},
    ],
}

# Real confirmed-live product mixing a glued "2xLP" vinyl variant with
# CD/MP3-only variants, one of them unavailable (no preorder signal exists
# on this store at all — confirmed after a full 337-product scan).
_MULTI_VARIANT_PRODUCT = {
    "title": "Album Of The Year",
    "vendor": "The Good Life",
    "handle": "album-of-the-year",
    "tags": ["artist:The Good Life", "CD", "colored vinyl", "LP", "meta-related-collection-the-good-life", "MP3", "vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "GOLD NUGGET 2xLP + MP3", "price": "29.99", "available": False},
        {"title": "CD + MP3", "price": "9.99", "available": True},
        {"title": "MP3", "price": "8.99", "available": True},
    ],
}

# Real confirmed-live apparel product mixed into the "all" collection.
_APPAREL_PRODUCT = {
    "title": "Algernon Cadwallader Tee",
    "vendor": "Algernon Cadwallader",
    "handle": "algernon-cadwallader-tee",
    "tags": ["Algernon Cadwallader", "t-shirt"],
    "product_type": "Apparel",
    "images": [],
    "variants": [
        {"title": "Small", "price": "20.00", "available": True},
    ],
}

# Real confirmed-live release with no vinyl edition at all — CD/MP3 only.
_NO_VINYL_EDITION_PRODUCT = {
    "title": "11:11",
    "vendor": "Maria Taylor",
    "handle": "11-11-2016",
    "tags": ["artist:Maria Taylor", "CD", "meta-related-collection-maria-taylor"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "CD + MP3", "price": "9.99", "available": True},
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
    assert item["artist"] == "Adam Cayton-Holland"
    assert item["title"] == "Adam Cayton-Holland Performs His Signature Bits — Colored LP"
    assert item["price"] == 18.99
    assert item["url"] == "https://saddle-creek.com/products/adam-cayton-holland-performs-his-signature-bits"


@respx.mock
async def test_crawl_catalog_includes_glued_2xlp_excludes_cd_and_mp3(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_VARIANT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []  # the only vinyl variant is unavailable, and there's no preorder override on this store


@respx.mock
async def test_crawl_catalog_excludes_apparel_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_APPAREL_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_yields_nothing_for_release_with_no_vinyl_edition(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_NO_VINYL_EDITION_PRODUCT]))
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
    assert Crawler.site_name == "Saddle Creek"
    assert Crawler.base_url == "https://saddle-creek.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_saddlecreek_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.saddlecreek'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/saddlecreek.py`. Note: a prior research pass's "small/dated catalog" note for this store was wrong — confirmed live at 337 products, and `/collections/all` mixes in apparel/goods/gift-cards (the same shape Rise Records and Triple B Records needed in the prior batch), so a `product_type == "Music"` gate is required.

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "all"
_MUSIC_PRODUCT_TYPE = "Music"
_VINYL_VARIANT_RE = re.compile(r'\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)


class Crawler:
    site_name: str = "Saddle Creek"
    base_url: str = "https://saddle-creek.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        if (product.get("product_type") or "").strip() != _MUSIC_PRODUCT_TYPE:
            return []

        artist = (product.get("vendor") or "").strip()
        title = product.get("title", "")
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            variant_title = variant.get("title", "")
            if not _VINYL_VARIANT_RE.search(variant_title):
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

- [ ] **Step 4: Run and confirm all 5 tests pass**

Run: `pytest tests/test_saddlecreek_crawler.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 5: Polyvinyl Record Co. crawler

**Files:**
- Create: `backend/crawlers/polyvinylrecords.py`
- Test: `backend/tests/test_polyvinylrecords_crawler.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_polyvinylrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.polyvinylrecords import Crawler

_PRODUCTS_URL = "https://polyvinylrecords.com/collections/vinyl/products.json"

# Real confirmed-live case: even a genuine Polyvinyl house release has
# vendor = the label ("Polyvinyl Records"), never the artist — title parsing
# is the only correct source, universally on this store.
_PRODUCT = {
    "title": "Deerhoof - Breakup Song",
    "vendor": "Polyvinyl Records",
    "handle": "deerhoof-breakup-song",
    "tags": ["$5CD-TAPE", "Deerhoof"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/deerhoof-fallback.jpg"}],
    "variants": [
        {"title": "Vinyl (Blue)", "price": "20.00", "available": True},
        {"title": "CD", "price": "12.00", "available": False},
        {"title": "Digital", "price": "10.00", "available": True},
    ],
}

# Real confirmed-live third-party-distributed title — included the same as
# a house release, since it's genuinely purchasable vinyl on this
# collection; vendor is the distributor ("Atlantic Records"), not the artist.
_NON_POLYVINYL_PRODUCT = {
    "title": "100 gecs - 10,000 gecs",
    "vendor": "Atlantic Records",
    "handle": "100-gecs-10-000-gecs",
    "tags": ["100 gecs", "Electronic", "Non-Polyvinyl", "Pop"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Vinyl (Black)", "price": "40.00", "available": True},
    ],
}

# Real confirmed-live release-name-embedded preorder tag — no single
# canonical tag string exists on this store; detection needs a substring
# search for "pre-order" anywhere in the tags array.
_PREORDER_PRODUCT = {
    "title": "American Football - American Football (Live in Los Angeles)",
    "vendor": "Polyvinyl Records",
    "handle": "american-football-live-in-los-angeles",
    "tags": ["AF - Live in LA Pre-Order", "American Football", "exclude_rebuy", "Live in Los Angeles"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Vinyl (Clear)", "price": "22.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_not_label_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Deerhoof"
    assert item["title"] == "Breakup Song — Vinyl (Blue)"
    assert item["price"] == 20.00
    assert item["url"] == "https://polyvinylrecords.com/products/deerhoof-breakup-song"


@respx.mock
async def test_crawl_catalog_includes_third_party_distributed_title(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_NON_POLYVINYL_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "100 gecs"
    assert items[0]["title"] == "10,000 gecs — Vinyl (Black)"


@respx.mock
async def test_crawl_catalog_detects_preorder_via_tag_substring_search(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "American Football (Live in Los Angeles) — Vinyl (Clear) (Pre-Order)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Polyvinyl Record Co."
    assert Crawler.base_url == "https://polyvinylrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails on import**

Run: `pytest tests/test_polyvinylrecords_crawler.py -v`
Expected: `ModuleNotFoundError: No module named 'crawlers.polyvinylrecords'`

- [ ] **Step 3: Write the implementation**

Create `backend/crawlers/polyvinylrecords.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*-\s*(?P<album>.+)$')
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*"', re.IGNORECASE)
_PREORDER_RE = re.compile(r'pre-?order', re.IGNORECASE)


class Crawler:
    site_name: str = "Polyvinyl Record Co."
    base_url: str = "https://polyvinylrecords.com"
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
        is_preorder = cls._is_preorder(product)

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
    def _is_preorder(product: dict) -> bool:
        return any(_PREORDER_RE.search(t or "") for t in product.get("tags") or [])

    @staticmethod
    def _parse_artist_title(title: str, vendor: str):
        # `vendor` is always a label/distributor here, never the artist — even
        # Polyvinyl's own releases show "Polyvinyl Records" as vendor while
        # the title is "Artist - Album". Confirmed live: 73% of a 250-product
        # sample carries a "Non-Polyvinyl" tag (third-party labels distributed
        # through this storefront) — those are included the same as house
        # releases, since they're genuinely purchasable vinyl on this
        # collection; only the artist-attribution source differs.
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run and confirm all 5 tests pass**

Run: `pytest tests/test_polyvinylrecords_crawler.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit** (same trailer/helper process as Task 1)

---

### Task 6: Full backend test suite verification, pre-PR spec-drift check, spec update

**Files:** `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (modify), plus any other spec found to have drifted

- [ ] **Step 1: Run the full backend test suite**

Run (from `backend/`): `pytest -q`
Expected: all tests pass, including all five new files. Total catalog crawlers registered on this branch: eighteen (13 pre-existing + 5 from this batch).

- [ ] **Step 2: Check for `main` movement, rebase if needed**

```bash
git fetch origin main
git log --oneline HEAD..origin/main
```

If `origin/main` has moved (e.g. because a sibling batch — metal or punk/hardcore pt1 — merged while this branch was in progress), `git rebase origin/main` and re-run the full suite before the drift check below. **Parallel-batch note:** this plan's crawler-count math (13 pre-existing + 5 = eighteen) assumes neither sibling batch has merged yet. If one has, the actual count after rebase will be higher (13 + whichever sibling batches already landed + 5) — recompute from what's actually in the rebased `main`, don't trust the number in this plan document.

- [ ] **Step 3: Run the mandatory pre-PR spec-drift check**

Per `CLAUDE.md`'s "Pre-PR spec-drift check" rule: `grep -rl` across `docs/superpowers/specs/` for files/symbols/section names/UI strings touched by this diff. Specifically check `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, `2026-07-06-store-recommended-filter-design.md`, and `2026-07-08-collection-price-crawlers-design.md` for the same "N catalog crawlers"/"Store Crawlers" drift pattern already found and fixed twice in the two prior batches — it's likely present again here since this branch started from a pre-batch main, same as the other two times.

- [ ] **Step 4: Update the design spec**

Add a "Technical grounding" subsection for each of the five sites to `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md`, following the existing per-site format. Update the "Format filter comes in N shapes" Decisions bullet — Closed Casket Activities/Big Scary Monsters USA's negative-filter-with-conditional-display-title shape and Kill Rock Stars' positive-regex-plus-bundle-exclusion shape are each worth a callout, and Saddle Creek's confirmed-live catalog-size correction (337, not "small/dated") is worth noting so a future crawler author doesn't propagate the same wrong assumption.

- [ ] **Step 5: Commit the spec update and any drift fixes** (same trailer/helper process as Task 1)
