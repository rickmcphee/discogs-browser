# Jackpot Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/jackpotrecords.py`, a `crawler_type="catalog"` Shopify plugin covering Jackpot Records' (`jackpotrecords.com`) new-vinyl catalog.

**Architecture:** Iterate the store's full `online-store` collection via the existing `shopify_catalog.iter_products()` helper, gate each product in-process on `has_tag(product, "Vinyl") OR title contains "vinyl"` (recovers 7 confirmed-live products the store's own `all-vinyl` collection mistags), split `artist`/`album` off the title with a hyphen/en-dash/em-dash regex (no vendor fallback — `vendor` is unreliable on this store), and yield one stock item per product (every product on this store has exactly one variant).

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` from `typing`.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, `resolve_cover_image()` unchanged (per design spec's "Scope").
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"`.
- No comments except where the WHY is non-obvious (a hidden constraint, a workaround, a confirmed-live edge case) — no comments describing WHAT the code does.
- No disambiguation/multi-variant handling — every confirmed-live product on this store has exactly one variant (design spec "Variants: always exactly one per product").
- Registration is automatic via `main.py`'s startup loop (reads `site_name`/`crawler_type`/`requires_discogs_release` off the module) — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md` (`ai-generated: true`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule below: [`docs/specifications/shaping/2026-08-14-jackpot-records-store-crawler-design.md`](../shaping/2026-08-14-jackpot-records-store-crawler-design.md).

---

### Task 1: Jackpot Records crawler + tests

**Files:**
- Create: `backend/crawlers/jackpotrecords.py`
- Test: `backend/tests/test_jackpotrecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]`, `shopify_catalog.has_tag(product, tag) -> bool`, `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: `Crawler` class with `site_name: str`, `base_url: str`, `genre_summary: str`, `crawler_type: str = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. This is the standard `catalog` plugin interface `main.py`'s startup loop discovers — no other task depends on internals beyond this shape.

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_jackpotrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.jackpotrecords import Crawler

_PRODUCTS_URL = "https://jackpotrecords.com/collections/online-store/products.json"

_PRODUCT = {
    "title": "Deerhoof - Breakup Song",
    "vendor": "Joyful Noise Recordings",
    "handle": "deerhoof-breakup-song",
    "product_type": "Vinyl",
    "tags": ["Vinyl", "Rock"],
    "images": [{"src": "https://cdn.shopify.com/deerhoof-fallback.jpg"}],
    "variants": [
        {"title": "New", "price": "20.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: en-dash separator, spaces on both sides.
_ENDASH_PRODUCT = {
    "title": "Carn, Doug – The Best Of Doug Carn (2LP)",
    "vendor": "Soul Jazz",
    "handle": "doug-carn-best-of",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "New", "price": "34.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: hyphen glued to the artist name, space only
# after -- the whitespace-anchored regex must still split on it.
_ASYMMETRIC_SPACING_PRODUCT = {
    "title": "Electric Wizard- Black Magic Rituals & Perversions Vol. 1 (2LP, Crystal Meth Marbled Vinyl)",
    "vendor": "Spinefarm",
    "handle": "electric-wizard-black-magic-rituals",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "New", "price": "39.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no separator at all, and vendor is a reissue
# label ("Rhino"), not the artist ("Doobie Brothers") -- must not be used
# as a fallback.
_NO_SEPARATOR_PRODUCT = {
    "title": "Best of the Doobie Brothers",
    "vendor": "Rhino",
    "handle": "best-of-doobie-brothers",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "New", "price": "24.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no Vinyl tag and product_type is wrong ("CD"),
# but the title spells out "Vinyl" -- must still be included.
_UNTAGGED_VINYL_WORD_PRODUCT = {
    "title": "Deftones - Private Music (Indie Ex) (Vinyl)",
    "vendor": "Jackpot Records",
    "handle": "private-music-indie-ex",
    "product_type": "CD",
    "tags": ["CD", "Rock", "WEA"],
    "images": [],
    "variants": [
        {"title": "New", "price": "15.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: Vinyl tag present, but the title itself never
# spells out the word "vinyl" -- the tag alone must be enough.
_TAGGED_NO_VINYL_WORD_PRODUCT = {
    "title": "Wipers - Land of the Lost",
    "vendor": "Jackpot Records",
    "handle": "wipers-land-of-the-lost",
    "product_type": "Records & LPs",
    "tags": ["Vinyl", "Jackpot Records Label"],
    "images": [],
    "variants": [
        {"title": "New", "price": "19.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: neither signal -- a standalone CD with no Vinyl
# tag and no "vinyl" word anywhere in the title.
_NON_VINYL_PRODUCT = {
    "title": "Anderson.Paak - Oxnard (CD)",
    "vendor": "Jackpot Records",
    "handle": "anderson-paak-oxnard-cd",
    "product_type": "CD",
    "tags": ["CD"],
    "images": [],
    "variants": [
        {"title": "New", "price": "12.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "LP" is part of the album's own canonical title,
# not a format marker -- this must NOT be caught by a naive LP-substring
# gate, and it carries no Vinyl tag and no "vinyl" word.
_LP_IN_ALBUM_NAME_PRODUCT = {
    "title": "Eminem - The Marshall Mathers LP (CD)",
    "vendor": "Jackpot Records",
    "handle": "eminem-marshall-mathers-lp-cd",
    "product_type": "CD",
    "tags": ["CD", "Hip Hop", "UNI"],
    "images": [],
    "variants": [
        {"title": "New", "price": "13.99", "available": True, "featured_image": None},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Suzanne Vega - An Evening of New York Songs and Stories (2LP, Clear Vinyl) PRE-ORDER",
    "vendor": "Jackpot Records",
    "handle": "suzanne-vega-an-evening",
    "product_type": "Pre-Order",
    "tags": ["Pre-Order"],
    "images": [],
    "variants": [
        {"title": "New", "price": "32.99", "available": False, "featured_image": None},
    ],
}

_DEFAULT_TITLE_PRODUCT = {
    "title": "Big Lebowski - Original Soundtrack (Vinyl)",
    "vendor": "Mobile Fidelity",
    "handle": "big-lebowski-soundtrack",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/big-lebowski-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "27.99", "available": True, "featured_image": None},
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
async def test_crawl_catalog_parses_artist_and_album_from_hyphen_title(crawler):
    _mock_single_page([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Deerhoof"
    assert item["title"] == "Breakup Song"
    assert item["price"] == 20.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://jackpotrecords.com/products/deerhoof-breakup-song"
    assert item["cover_image_url"] == "https://cdn.shopify.com/deerhoof-fallback.jpg"


@respx.mock
async def test_crawl_catalog_splits_en_dash_title(crawler):
    _mock_single_page([_ENDASH_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Carn, Doug"
    assert items[0]["title"] == "The Best Of Doug Carn (2LP)"


@respx.mock
async def test_crawl_catalog_splits_asymmetric_spacing_hyphen(crawler):
    _mock_single_page([_ASYMMETRIC_SPACING_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Electric Wizard"
    assert items[0]["title"] == "Black Magic Rituals & Perversions Vol. 1 (2LP, Crystal Meth Marbled Vinyl)"


@respx.mock
async def test_crawl_catalog_skips_title_with_no_separator(crawler):
    _mock_single_page([_NO_SEPARATOR_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_untagged_product_with_vinyl_word_in_title(crawler):
    _mock_single_page([_UNTAGGED_VINYL_WORD_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Deftones"
    assert items[0]["title"] == "Private Music (Indie Ex) (Vinyl)"


@respx.mock
async def test_crawl_catalog_includes_tagged_product_without_vinyl_word(crawler):
    _mock_single_page([_TAGGED_NO_VINYL_WORD_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Wipers"
    assert items[0]["title"] == "Land of the Lost"


@respx.mock
async def test_crawl_catalog_excludes_product_with_neither_signal(crawler):
    _mock_single_page([_NON_VINYL_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_lp_in_canonical_album_name(crawler):
    _mock_single_page([_LP_IN_ALBUM_NAME_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_unavailable_preorder(crawler):
    _mock_single_page([_PREORDER_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Suzanne Vega"
    assert items[0]["price"] == 32.99


@respx.mock
async def test_crawl_catalog_skips_unavailable_non_preorder(crawler):
    product = {**_PRODUCT, "variants": [{"title": "New", "price": "20.00", "available": False, "featured_image": None}]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_default_title_variant_yields_bare_album_title(crawler):
    _mock_single_page([_DEFAULT_TITLE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Original Soundtrack (Vinyl)"


def test_site_metadata():
    assert Crawler.site_name == "Jackpot Records"
    assert Crawler.base_url == "https://jackpotrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the tests to verify they fail on import**

Run: `cd backend && pytest tests/test_jackpotrecords_crawler.py -v`
Expected: every test errors with `ModuleNotFoundError: No module named 'crawlers.jackpotrecords'` (the crawler module doesn't exist yet).

- [ ] **Step 3: Write the crawler implementation**

Create `backend/crawlers/jackpotrecords.py`:

```python
import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "online-store"
_PREORDER_TAG = "pre-order"
_VINYL_TAG = "vinyl"
# The store's own "all-vinyl" collection omits confirmed-live vinyl products
# it has mistagged (e.g. product_type "CD" on a real vinyl pressing) -- see
# 2026-08-14-jackpot-records-store-crawler-design.md "Why the full catalog,
# not the all-vinyl collection". Iterating the full catalog and gating
# in-process on tag-or-title-word recovers them without also matching "LP"
# as a substring of an album's own canonical name (e.g. "The Marshall
# Mathers LP"), which a broader lp|ep|"-style regex would.
_VINYL_WORD_RE = re.compile(r'\bvinyl\b', re.IGNORECASE)
# Hyphen/en-dash/em-dash split with asymmetric spacing, from cleorecs.py --
# confirmed live: this store also uses "Artist – Album" and "Artist- Album"
# forms, not just "Artist - Album". No vendor fallback: unlike the label
# stores this fleet otherwise covers, vendor here is the store's own name
# or a reissue label, never the artist.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')


class Crawler:
    site_name: str = "Jackpot Records"
    base_url: str = "https://jackpotrecords.com"
    genre_summary: str = "Portland, Oregon record store and label with a broad new-vinyl selection across genres."
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            if not self._is_vinyl(product):
                continue
            item = self._item(product)
            if item is not None:
                yield item

    @classmethod
    def _is_vinyl(cls, product: dict) -> bool:
        return has_tag(product, _VINYL_TAG) or bool(_VINYL_WORD_RE.search(product.get("title", "")))

    @classmethod
    def _item(cls, product: dict) -> Optional[dict]:
        m = _TITLE_RE.match(product.get("title", ""))
        if not m:
            return None
        artist = m.group("artist").strip()
        album = m.group("album").strip()

        variants = product.get("variants") or []
        if not variants:
            return None
        variant = variants[0]
        is_preorder = has_tag(product, _PREORDER_TAG)
        if not variant.get("available") and not is_preorder:
            return None

        try:
            price = float(variant["price"])
        except (KeyError, TypeError, ValueError):
            price = None

        handle = product.get("handle", "")
        return {
            "artist": artist,
            "title": album,
            "format": "Vinyl",
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}/products/{handle}",
            "cover_image_url": resolve_cover_image(product, variant),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_jackpotrecords_crawler.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && pytest`
Expected: no new failures (this change touches no shared code — `shopify_catalog.py` is read-only here — so nothing outside the new test file should be affected).

- [ ] **Step 6: Confirm automatic plugin registration**

Run:
```bash
cd backend && python3 -c "
from crawlers.jackpotrecords import Crawler
c = Crawler()
print(c.site_name, c.base_url, c.crawler_type, c.genre_summary)
"
```
Expected: prints `Jackpot Records https://jackpotrecords.com catalog Portland, Oregon record store and label with a broad new-vinyl selection across genres.` — confirms the module is importable and exposes the attributes `main.py`'s startup loop reads (`site_name`, `crawler_type`, `requires_discogs_release` — absent here, which `register_crawler()` treats as `False`, matching every other `catalog` plugin). No change to `main.py` or any router is needed.

- [ ] **Step 7: Commit**

```bash
git add backend/crawlers/jackpotrecords.py backend/tests/test_jackpotrecords_crawler.py
git commit -F <message-file>
```

Message file content (per this repo's required AI-attribution trailers):

```
Add Jackpot Records store crawler

Shopify catalog crawler covering jackpotrecords.com's new-vinyl stock.
Gates on a Vinyl tag OR the word "vinyl" in the title rather than the
store's own all-vinyl collection, which mistags several confirmed-live
vinyl products as CD/other. See design spec for full grounding.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
```

---

## Post-implementation: pre-PR spec-drift check

Before opening the PR, per this repo's `CLAUDE.md`: `grep -rl` across both
`docs/superpowers/specs/` and `docs/specifications/shaping/` for files,
symbols, and section names touched by this change (`jackpotrecords`,
`online-store`, `all-vinyl`, `shopify_catalog`) to confirm no other spec
describes behavior this branch altered. This crawler only adds a new file
and reads existing shared helpers unchanged, so drift is not expected, but
the check must still run and its result (found/not found) noted in the PR
description.
