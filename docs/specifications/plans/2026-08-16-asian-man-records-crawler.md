# Asian Man Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/asianmanrecords.py`, a `crawler_type="catalog"` Shopify plugin covering Asian Man Records' (`asianmanrecords.com`) vinyl catalog.

**Architecture:** Iterate the store's full `all-products` collection via the existing `shopify_catalog.iter_products()` helper, gate each product in-process on `product_type` or tags naming `12-INCH VINYL`/`7-INCH VINYL`, parse `artist`/`album` off the title (primary: a quoted-album form `ARTIST "Album" FORMAT` this store uses, after stripping a `PRE ORDER:`/`AMR DISTRO:` prefix; fallback: hyphen-split with a trailing format-suffix strip), and fan out one stock item per surviving Shopify variant — excluding CD/cassette/slipmat sibling variants and collapsing apparel-bundle size variants to the cheapest.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `resolve_cover_image()` unchanged (per design spec's "Scope"). `has_tag()` is not used — this store's gate reads `product_type` directly, not just tags.
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"`.
- No comments except where the WHY is non-obvious (a hidden constraint, a confirmed-live edge case) — no comments describing WHAT the code does.
- AMR DISTRO items (other labels' releases resold through this store) are in scope, per user decision during brainstorming — only the `AMR DISTRO:`/`PRE ORDER:` title prefixes are stripped, the products themselves are not excluded.
- Registration is automatic via `main.py`'s startup loop (reads `site_name`/`crawler_type`/`requires_discogs_release` off the module) — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md` (`ai-generated: true`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule below: [`docs/specifications/shaping/2026-08-16-asian-man-records-crawler-design.md`](../shaping/2026-08-16-asian-man-records-crawler-design.md).

---

### Task 1: Asian Man Records crawler + tests

**Files:**
- Create: `backend/crawlers/asianmanrecords.py`
- Test: `backend/tests/test_asianmanrecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]`, `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: `Crawler` class with `site_name: str`, `base_url: str`, `genre_summary: str`, `crawler_type: str = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. This is the standard `catalog` plugin interface `main.py`'s startup loop discovers — no other task depends on internals beyond this shape.

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_asianmanrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.asianmanrecords import Crawler

_PRODUCTS_URL = "https://asianmanrecords.com/collections/all-products/products.json"

# Real confirmed-live case: quoted album, no hyphen before the quote.
_KOREA_GIRL = {
    "title": 'KOREA GIRL "Korea Girl" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "korea-girl-korea-girl-12",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0868/9755/7813/files/KoreaGirl_Cover_Lp_24.jpg"}],
    "variants": [
        {"title": "COLOR VINYL", "price": "25.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: quoted album with a spaced hyphen before the quote,
# product_type says vinyl but the tags array doesn't (only "NEW RELEASE") -- the
# gate must still include it. Also a real apparel-bundle case: 6 size variants,
# all "... BUNDLE DEAL", priced 29.99/29.99/29.99/29.99/31.99/34.99 -- must
# collapse to one stock item at the cheapest price, not six near-duplicates.
_GRUMPSTER = {
    "title": 'GRUMPSTER - "Honeydew" 12" VINYL + T-SHIRT',
    "vendor": "Asian Man Records",
    "handle": "grumpster-honeydew-12-vinyl",
    "product_type": "12-INCH VINYL",
    "tags": ["NEW RELEASE"],
    "images": [],
    "variants": [
        {"title": "SMALL BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "MEDIUM BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "LARGE BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "XL BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "XXL BUNDLE DEAL", "price": "31.99", "available": True, "featured_image": None},
        {"title": "XXXL BUNDLE DEAL", "price": "34.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "PRE ORDER:" prefix plus a hyphen glued to the artist
# word (no space before it) -- both the prefix strip and the quote regex's
# optional/asymmetric hyphen handling are exercised together. Another real
# apparel-bundle case (6 size variants, cheapest is 36.99).
_SMOKING_POPES_PREORDER = {
    "title": 'PRE ORDER: SMOKING POPES- "Stay Down" 12" VINYL + T-SHIRT',
    "vendor": "Asian Man Records",
    "handle": "pre-order-the-albert-square-i-wish-i-could-talk-to-people-12-vinyl-t-shirt-copy",
    "product_type": "12-INCH VINYL",
    "tags": ["NEW RELEASE"],
    "images": [],
    "variants": [
        {"title": "SMALL BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "MEDIUM BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "LARGE BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "XL BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "XXL BUNDLE DEAL", "price": "38.99", "available": True, "featured_image": None},
        {"title": "XXXL BUNDLE DEAL", "price": "39.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "AMR DISTRO:" prefix -- another label's release
# (Skankin' Pickle, not an Asian Man Records release) resold through this
# store's own distro. In scope per the design spec: the prefix is stripped,
# the product is not excluded.
_SKANKIN_PICKLE_DISTRO = {
    "title": 'AMR DISTRO: SKANKIN\' PICKLE "Green Album" 12" BLACK VINYL',
    "vendor": "Asian Man Records",
    "handle": "skankin-pickle-green-album-lp-black-vinyl",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "BLACK", "price": "18.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: both prefixes together, with the site's own doubled-
# colon typo ("AMR DISTRO::") -- confirms _PREFIX_RE's `:+` handles it.
_AJJ_DISTRO_PREORDER = {
    "title": 'PRE ORDER: AMR DISTRO:: AJJ - "Dirty Old Power" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "ajj-dirty-old-power-lp",
    "product_type": "",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "LP - Splatter", "price": "30.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quoted album at all -- hyphen-fallback path, with
# a trailing "12\" VINYL" format suffix that must be stripped off the album half.
_MU330_CHUMPS = {
    "title": 'MU330 - CHUMPS ON PARADE 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "mu330-chumps-on-parade-12",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "18.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: hyphen-fallback path, self-titled shorthand -- the
# album half is the literal "S/T" once the format suffix is stripped.
_MU330_ST = {
    "title": 'MU330 - S/T 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "mu330-s-t-12",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "18.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quote, no hyphen -- just artist name plus format,
# no album at all. Neither parser matches; must be skipped.
_MAGUMA_TAISHI_NO_ALBUM = {
    "title": 'MAGUMA TAISHI 7"',
    "vendor": "Asian Man Records",
    "handle": "maguma-taishi-7",
    "product_type": "CDs",
    "tags": ["7-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "7.00", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: a compilation title with no artist/album separator
# structure at all -- must be skipped, not misparsed.
_GILMAN_STREET_COMPILATION = {
    "title": "V/A GILMAN STREET RIPOFFS(A Tribute To DOOKIE)",
    "vendor": "Asian Man Records",
    "handle": "v-a-gilman-street-ripoffsa-tribute-to-dookie",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "DOOKIE BROWN VINYL", "price": "24.99", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: TEST PRESSES product_type/tag -- a one-off etching,
# not standard stock. Neither vinyl signal is present; must be gated out.
_JONAH_RAY_TEST_PRESS = {
    "title": 'JONAH RAY "You Can\'t Call Me Al" 12" etching',
    "vendor": "Asian Man Records",
    "handle": "jonah-ray-you-cant-call-me-al-12-etching",
    "product_type": "TEST PRESSES",
    "tags": ["TEST PRESS"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "14.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live shape: a standalone CD product, no vinyl signal in
# product_type or tags -- must be gated out entirely.
_KITTY_KAT_CD = {
    "title": 'KITTY KAT FAN CLUB "Dreamy Little You" CD',
    "vendor": "Asian Man Records",
    "handle": "kitty-kat-fan-club-dreamy-little-you-cd",
    "product_type": "",
    "tags": ["CDs"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: multi-variant product bundling a CD as a sibling
# variant of the vinyl -- the CD variant must be dropped, leaving one survivor
# (so no variant-title suffix on the yielded stock item's title).
_HEY_SMITH = {
    "title": 'HEY-SMITH "Life In The Sun" 12" VINYL/CD',
    "vendor": "Asian Man Records",
    "handle": "hey-smith-life-in-the-sun-lp-cd",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL", "CDs"],
    "images": [],
    "variants": [
        {"title": "COLOR VINYL", "price": "18.99", "available": True, "featured_image": None},
        {"title": "CD", "price": "8.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: multi-variant product bundling a cassette sibling
# variant -- must be dropped, even though it's the only unavailable variant
# (i.e. it's the vinyl variant that's available, not the cassette).
_CLASSICS_OF_LOVE = {
    "title": 'CLASSICS OF LOVE "S/T" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "classics-of-love-s-t-lp",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL", "cassette"],
    "images": [],
    "variants": [
        {"title": "COLOR VINYL", "price": "19.99", "available": True, "featured_image": None},
        {"title": "Cassette", "price": "5.99", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: multi-variant product bundling a promo slipmat as a
# purchasable alternative to the vinyl itself -- must be dropped.
_ALKALINE_TRIO_ST = {
    "title": 'ALKALINE TRIO "S/T" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "alkaline-trio-s-t-lp",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "A3 - RANDOM COLOR VINYL", "price": "19.99", "available": True, "featured_image": None},
        {"title": "A3 - SLIPMAT", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: single-variant product whose sole variant is titled
# "Default Title" -- confirms single-variant products are never run through the
# CD/cassette/slipmat exclusion regex (47/102 of them would wrongly fail a
# vinyl-word requirement, since "Default Title" contains no such word).
_LEMURIA = {
    "title": 'LEMURIA "Get Better" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "lemuria-get-better-lp",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "17.99", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_quoted_album_no_hyphen(crawler):
    _mock_single_page([_KOREA_GIRL])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "KOREA GIRL"
    assert item["title"] == "Korea Girl"
    assert item["price"] == 25.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://asianmanrecords.com/products/korea-girl-korea-girl-12"
    assert item["cover_image_url"] == "https://cdn.shopify.com/s/files/1/0868/9755/7813/files/KoreaGirl_Cover_Lp_24.jpg"


@respx.mock
async def test_crawl_catalog_includes_product_type_vinyl_without_vinyl_tag(crawler):
    _mock_single_page([_GRUMPSTER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "GRUMPSTER"
    assert items[0]["title"] == "Honeydew"


@respx.mock
async def test_crawl_catalog_collapses_apparel_bundle_sizes_to_cheapest(crawler):
    _mock_single_page([_GRUMPSTER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 29.99


@respx.mock
async def test_crawl_catalog_strips_preorder_prefix_and_asymmetric_hyphen(crawler):
    _mock_single_page([_SMOKING_POPES_PREORDER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "SMOKING POPES"
    assert items[0]["title"] == "Stay Down"
    assert items[0]["price"] == 36.99


@respx.mock
async def test_crawl_catalog_includes_amr_distro_item(crawler):
    _mock_single_page([_SKANKIN_PICKLE_DISTRO])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "SKANKIN' PICKLE"
    assert items[0]["title"] == "Green Album"


@respx.mock
async def test_crawl_catalog_strips_doubled_colon_distro_preorder_prefix(crawler):
    _mock_single_page([_AJJ_DISTRO_PREORDER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "AJJ"
    assert items[0]["title"] == "Dirty Old Power"


@respx.mock
async def test_crawl_catalog_hyphen_fallback_strips_format_suffix(crawler):
    _mock_single_page([_MU330_CHUMPS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "MU330"
    assert items[0]["title"] == "CHUMPS ON PARADE"


@respx.mock
async def test_crawl_catalog_hyphen_fallback_self_titled(crawler):
    _mock_single_page([_MU330_ST])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "MU330"
    assert items[0]["title"] == "S/T"


@respx.mock
async def test_crawl_catalog_skips_title_with_no_album(crawler):
    _mock_single_page([_MAGUMA_TAISHI_NO_ALBUM])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_compilation_with_no_split(crawler):
    _mock_single_page([_GILMAN_STREET_COMPILATION])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_test_press(crawler):
    _mock_single_page([_JONAH_RAY_TEST_PRESS])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_non_vinyl_product(crawler):
    _mock_single_page([_KITTY_KAT_CD])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_drops_cd_sibling_variant(crawler):
    _mock_single_page([_HEY_SMITH])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Life In The Sun"
    assert items[0]["price"] == 18.99


@respx.mock
async def test_crawl_catalog_drops_cassette_sibling_variant(crawler):
    _mock_single_page([_CLASSICS_OF_LOVE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "S/T"
    assert items[0]["price"] == 19.99


@respx.mock
async def test_crawl_catalog_drops_slipmat_sibling_variant(crawler):
    _mock_single_page([_ALKALINE_TRIO_ST])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "S/T"
    assert items[0]["price"] == 19.99


@respx.mock
async def test_crawl_catalog_single_variant_default_title_no_vinyl_word_required(crawler):
    _mock_single_page([_LEMURIA])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Get Better"
    assert items[0]["price"] == 17.99


@respx.mock
async def test_crawl_catalog_skips_unavailable_non_preorder(crawler):
    product = {**_MU330_CHUMPS, "variants": [{**_MU330_CHUMPS["variants"][0], "available": False}]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_unavailable_preorder(crawler):
    # Hypothetical: no live AMR pre-order is currently unavailable (see design
    # spec's "Pre-orders and availability") -- constructed to test the
    # future-safety rule the same way jackpotrecords.py's spec documents.
    product = {**_AJJ_DISTRO_PREORDER, "variants": [{**_AJJ_DISTRO_PREORDER["variants"][0], "available": False}]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "AJJ"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_KOREA_GIRL, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Asian Man Records"
    assert Crawler.base_url == "https://asianmanrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the tests to verify they fail on import**

Run: `cd backend && pytest tests/test_asianmanrecords_crawler.py -v`
Expected: every test errors with `ModuleNotFoundError: No module named 'crawlers.asianmanrecords'` (the crawler module doesn't exist yet).

- [ ] **Step 3: Write the crawler implementation**

Create `backend/crawlers/asianmanrecords.py`:

```python
import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "all-products"
_VINYL_TYPES = {"12-INCH VINYL", "7-INCH VINYL"}

# Prefix noise seen live: "PRE ORDER: AJJ ..." and "AMR DISTRO: SKANKIN' PICKLE
# ...", sometimes both together with a doubled-colon typo
# ("PRE ORDER: AMR DISTRO:: AJJ ..."). Distro items -- another label's release
# resold through Asian Man's own store -- stay in scope; only this prefix noise
# is stripped before parsing.
_PREFIX_RE = re.compile(r'^(?:PRE ORDER:\s*)?(?:AMR DISTRO:+\s*)?', re.IGNORECASE)
# Primary title convention on this store: ARTIST "Album" FORMAT, with or
# without a hyphen (glued or spaced) before the opening quote.
_QUOTE_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*[-–—]?\s*"(?P<album>[^"]+)"')
# Fallback for the minority of titles with no quoted album at all -- reuses
# cleorecs.py's hyphen/en-dash/em-dash split.
_HYPHEN_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
# Strips a trailing format marker off the hyphen-fallback album half -- only
# the digit+quote/digit+INCH forms confirmed live on this store's no-quote
# titles (e.g. "CHUMPS ON PARADE 12\" VINYL" -> "CHUMPS ON PARADE").
_FORMAT_SUFFIX_RE = re.compile(r'\s+(?:DOUBLE\s+)?\d{1,2}\s*(?:"|INCH\b).*$', re.IGNORECASE)
# Non-vinyl alternate purchases this store bundles as sibling Shopify variants
# of the vinyl product itself (CD, cassette, promo slipmat) -- excluded only
# when a product has more than one variant. A single-variant product's sole
# variant is often just "Default Title" or a bare color word with no such
# word to match, so applying this unconditionally would wrongly drop it.
_EXCLUDED_VARIANT_RE = re.compile(r'\bCD\b|\bCS\b|\bCASSETTE\b|\bSLIPMAT\b', re.IGNORECASE)
# Apparel-bundle sizing (vinyl + T-shirt, varying only by shirt size) --
# collapsed to the cheapest variant instead of one near-duplicate stock item
# per size.
_BUNDLE_DEAL_RE = re.compile(r'BUNDLE DEAL', re.IGNORECASE)


class Crawler:
    site_name: str = "Asian Man Records"
    base_url: str = "https://asianmanrecords.com"
    genre_summary: str = "Mike Park's Bay Area punk/ska label store, selling its own catalog plus a small distro of other labels' releases."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            if not self._is_vinyl(product):
                continue
            for item in self._items(product):
                yield item

    @classmethod
    def _is_vinyl(cls, product: dict) -> bool:
        if product.get("product_type") in _VINYL_TYPES:
            return True
        return any(t in _VINYL_TYPES for t in (product.get("tags") or []))

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album, is_preorder = cls._parse_title(product.get("title", ""))
        if artist is None:
            return []

        variants = product.get("variants") or []
        if len(variants) > 1:
            survivors = [v for v in variants if not _EXCLUDED_VARIANT_RE.search(v.get("title", ""))]
        else:
            survivors = list(variants)
        if survivors and all(_BUNDLE_DEAL_RE.search(v.get("title", "")) for v in survivors):
            survivors = [min(survivors, key=cls._variant_price_sort_key)]

        handle = product.get("handle", "")
        items = []
        for variant in survivors:
            if not variant.get("available") and not is_preorder:
                continue
            title = album if len(survivors) == 1 else f"{album} — {variant.get('title', '')}"
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": cls._price(variant),
                "currency": "USD",
                "url": f"{cls.base_url}/products/{handle}",
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        try:
            return float(variant["price"])
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def _variant_price_sort_key(cls, variant: dict) -> float:
        price = cls._price(variant)
        return price if price is not None else float("inf")

    @classmethod
    def _parse_title(cls, raw_title: str):
        is_preorder = raw_title.strip().upper().startswith("PRE ORDER")
        stripped = _PREFIX_RE.sub('', raw_title).strip()

        m = _QUOTE_TITLE_RE.match(stripped)
        if m:
            return m.group("artist").strip(), m.group("album").strip(), is_preorder

        m = _HYPHEN_TITLE_RE.match(stripped)
        if m:
            album = _FORMAT_SUFFIX_RE.sub('', m.group("album")).strip()
            if album:
                return m.group("artist").strip(), album, is_preorder

        return None, None, is_preorder
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_asianmanrecords_crawler.py -v`
Expected: all 20 tests PASS.

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && pytest`
Expected: no new failures (this change touches no shared code — `shopify_catalog.py` is read-only here — so nothing outside the new test file should be affected).

- [ ] **Step 6: Confirm automatic plugin registration**

Run:
```bash
cd backend && python3 -c "
from crawlers.asianmanrecords import Crawler
c = Crawler()
print(c.site_name, c.base_url, c.crawler_type, c.genre_summary)
"
```
Expected: prints `Asian Man Records https://asianmanrecords.com catalog Mike Park's Bay Area punk/ska label store, selling its own catalog plus a small distro of other labels' releases.` — confirms the module is importable and exposes the attributes `main.py`'s startup loop reads (`site_name`, `crawler_type`, `requires_discogs_release` — absent here, which `register_crawler()` treats as `False`, matching every other `catalog` plugin). No change to `main.py` or any router is needed.

- [ ] **Step 7: Commit**

```bash
git add backend/crawlers/asianmanrecords.py backend/tests/test_asianmanrecords_crawler.py
git commit -F <message-file>
```

Message file content (per this repo's required AI-attribution trailers):

```
Add Asian Man Records store crawler

Shopify catalog crawler covering asianmanrecords.com's vinyl stock. Gates
on product_type/tags naming 12-INCH VINYL or 7-INCH VINYL, parses the
store's quoted-album title convention (ARTIST "Album" FORMAT) with a
hyphen-split fallback, and filters CD/cassette/slipmat sibling variants
and apparel-bundle sizing that this store bundles onto the same Shopify
product as the vinyl itself. See design spec for full grounding.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
```

---

## Post-implementation: pre-PR spec-drift check

Before opening the PR, per this repo's `CLAUDE.md`: `grep -rl` across both
`docs/superpowers/specs/` and `docs/specifications/shaping/` for files,
symbols, and section names touched by this change (`asianmanrecords`,
`all-products`, `shopify_catalog`) to confirm no other spec describes
behavior this branch altered. This crawler only adds a new file and reads
existing shared helpers unchanged, so drift is not expected, but the check
must still run and its result (found/not found) noted in the PR description.
