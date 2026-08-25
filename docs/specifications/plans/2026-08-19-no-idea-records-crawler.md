# No Idea Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/no_idea_records.py`, a `crawler_type="catalog"` Shopify plugin covering No Idea Records' (`noidearecords.com`) vinyl catalog.

**Architecture:** Iterate the store's `list` collection ("Music", the union of every format-specific collection) via the existing `shopify_catalog.iter_products()` helper, parse `artist`/`album` off the title's `ARTIST "Album"` quoted convention (falling back to `vendor` for the minority with no quotes), filter to vinyl variants only via a per-variant regex, and fan out one stock item per surviving variant, title always suffixed with the variant's own descriptor.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged (per design spec's "Scope").
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"` (deliberate vinyl-only scope decision — confirmed with the user during brainstorming, not an oversight).
- No pre-order carve-out — this store has no pre-order tag anywhere in its catalog (confirmed live against all 360 `list` products); an unavailable variant is simply skipped.
- Title is always `f"{album_title} — {variant_title}"`, unconditionally (matches `deathwishinc.py`'s own rule exactly — it does not special-case single-variant products).
- No comments except where the WHY is non-obvious (a hidden constraint, a confirmed-live edge case) — no comments describing WHAT the code does.
- Registration is automatic via `main.py`'s startup loop (reads `site_name`/`crawler_type`/`requires_discogs_release` off the module) — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md` (`ai-generated: true`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule below: [`docs/specifications/shaping/2026-08-19-no-idea-records-crawler-design.md`](../shaping/2026-08-19-no-idea-records-crawler-design.md).

---

### Task 1: No Idea Records crawler + tests

**Files:**
- Create: `backend/crawlers/no_idea_records.py`
- Test: `backend/tests/test_no_idea_records_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]`, `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: `Crawler` class with `site_name: str`, `base_url: str`, `genre_summary: str`, `genre: str`, `crawler_type: str = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. This is the standard `catalog` plugin interface `main.py`'s startup loop discovers — no other task depends on internals beyond this shape.

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_no_idea_records_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.no_idea_records import Crawler

_PRODUCTS_URL = "https://noidearecords.com/collections/list/products.json"

# Real confirmed-live case: quoted album, single vinyl variant, trailing
# descriptive text ("+ POSTER") outside the closing quote must not leak into
# the parsed album title.
_A_WILHELM_SCREAM = {
    "title": 'A WILHELM SCREAM "Partycrasher" + POSTER',
    "vendor": "No Idea Records",
    "handle": "a-wilhelm-scream-partycrasher-poster",
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0254/9599/products/awilhelmscream.jpg"}],
    "variants": [
        {"title": "RED VINYL + POSTER LP", "price": "20.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quotes at all (parenthetical text only) --
# falls back to vendor like _CLEVELAND_BOUND, confirming the parentheses
# don't accidentally trigger a false quote-match.
_ACRID_BUZZSAW = {
    "title": 'ACRID / LEFT FOR DEAD BUZZSAW (BLUE-GREEN VARIANT)',
    "vendor": "No Idea Records",
    "handle": "acrid-left-for-dead-buzzsaw-blue-green",
    "images": [],
    "variants": [
        {"title": "BLUE-GREEN BUZZSAW-SHAPED LP", "price": "99.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quoted album at all -- falls back to vendor
# as the artist.
_CLEVELAND_BOUND = {
    "title": "CLEVELAND BOUND DEATH SENTENCE",
    "vendor": "No Idea Records",
    "handle": "cleveland-bound-death-sentence",
    "images": [],
    "variants": [
        {"title": "LP", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: mixed vinyl + CD variants on the same product --
# the CD variant must be dropped, only the vinyl variant survives.
_ARMALITE = {
    "title": 'ARMALITE "Armalite"',
    "vendor": "No Idea Records",
    "handle": "armalite-armalite",
    "images": [],
    "variants": [
        {"title": "CD", "price": "10.00", "available": True, "featured_image": None},
        {"title": "LP", "price": "14.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a cassette-and-download-only product with no
# vinyl variant at all -- every variant dropped, item list is empty.
_ACHERS = {
    "title": 'ACHERS "Bottom of the Hill" TAPE',
    "vendor": "No Idea Records",
    "handle": "achers-bottom-of-the-hill-tape",
    "images": [],
    "variants": [
        {"title": "CASSETTE TAPE", "price": "10.00", "available": True, "featured_image": None},
        {"title": "Download (lossless)", "price": "4.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: vinyl LP alongside CD and Download siblings on
# the same product -- only the LP variant survives, the other two are
# dropped, and the vinyl variant is not required to be the only one present.
_AMPERE_LIKE_SHADOWS = {
    "title": 'AMPERE "Like Shadows"',
    "vendor": "No Idea Records",
    "handle": "ampere-like-shadows",
    "images": [],
    "variants": [
        {"title": "LP", "price": "14.00", "available": True, "featured_image": None},
        {"title": "CD", "price": "10.00", "available": True, "featured_image": None},
        {"title": "Download", "price": "8.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: multiple genuine vinyl color variants -- both
# survive, each title suffixed with its own variant descriptor.
_ASSHOLEPARADE = {
    "title": 'ASSHOLEPARADE "Student Ghetto Violence"',
    "vendor": "No Idea Records",
    "handle": "assholeparade-student-ghetto-violence",
    "images": [],
    "variants": [
        {"title": "GREEN VINYL LP+CD", "price": "18.00", "available": True, "featured_image": None},
        {"title": "CD", "price": "10.00", "available": True, "featured_image": None},
        {"title": "PURPLE VINYL LP", "price": "18.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: this store's curly right double quotation mark
# (U+201D, "”") used for the inch mark on a 7" variant -- must still be
# recognized as vinyl, not dropped as an unrecognized format.
_AGAINST_ME_CURLY_QUOTE = {
    "title": 'AGAINST ME! "Sink, Florida, Sink / Unsubstantiated Rumors"',
    "vendor": "No Idea Records",
    "handle": "against-me-sink-florida-sink",
    "images": [],
    "variants": [
        {"title": "DARK GREEN 7”", "price": "29.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: straight quote inch mark also recognized.
_AMPERE_RAEIN_SPLIT = {
    "title": 'AMPERE / RAEIN "Split"',
    "vendor": "No Idea Records",
    "handle": "ampere-raein-split",
    "images": [],
    "variants": [
        {"title": 'BLUE 8"', "price": "12.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a genuine vinyl pressing whose only variant-title
# signal is a bare color name -- no "LP"/"vinyl"/inch-mark token anywhere.
# No structural signal distinguishes this from real non-vinyl noise, so it's
# dropped as documented accepted noise in the design spec.
_DEFIANCE_OHIO_BARE_COLOR = {
    "title": "DEFIANCE, OHIO \"The Great Depression\" (BLUE) (LTD to 203)",
    "vendor": "No Idea Records",
    "handle": "defiance-ohio-the-great-depression-blue",
    "images": [],
    "variants": [
        {"title": "TRANSLUCENT BLUE", "price": "12.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: unavailable vinyl variant is skipped, no
# pre-order carve-out exists on this store.
_UNAVAILABLE_VARIANT = {
    "title": 'WORN IN RED "Banshees" TEST PRESSING',
    "vendor": "No Idea Records",
    "handle": "worn-in-red-banshees-test-pressing",
    "images": [],
    "variants": [
        {"title": "TEST PRESSING LP", "price": "20.99", "available": False, "featured_image": None},
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
async def test_crawl_catalog_parses_quoted_album_with_trailing_text(crawler):
    _mock_single_page([_A_WILHELM_SCREAM])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "A WILHELM SCREAM"
    assert item["title"] == "Partycrasher — RED VINYL + POSTER LP"
    assert item["price"] == 20.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://noidearecords.com/products/a-wilhelm-scream-partycrasher-poster"
    assert item["cover_image_url"] == "https://cdn.shopify.com/s/files/1/0254/9599/products/awilhelmscream.jpg"


@respx.mock
async def test_crawl_catalog_no_quotes_falls_back_to_vendor(crawler):
    _mock_single_page([_CLEVELAND_BOUND])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "No Idea Records"
    assert items[0]["title"] == "CLEVELAND BOUND DEATH SENTENCE — LP"


@respx.mock
async def test_crawl_catalog_drops_cd_sibling_variant(crawler):
    _mock_single_page([_ARMALITE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Armalite — LP"
    assert items[0]["price"] == 14.00


@respx.mock
async def test_crawl_catalog_drops_cassette_and_download_variants(crawler):
    _mock_single_page([_ACHERS])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_vinyl_drops_cd_and_download_siblings(crawler):
    _mock_single_page([_AMPERE_LIKE_SHADOWS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Like Shadows — LP"
    assert items[0]["price"] == 14.00


@respx.mock
async def test_crawl_catalog_keeps_multiple_vinyl_color_variants(crawler):
    _mock_single_page([_ASSHOLEPARADE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {
        "Student Ghetto Violence — GREEN VINYL LP+CD",
        "Student Ghetto Violence — PURPLE VINYL LP",
    }
    assert all(item["artist"] == "ASSHOLEPARADE" for item in items)


@respx.mock
async def test_crawl_catalog_recognizes_curly_quote_inch_mark(crawler):
    _mock_single_page([_AGAINST_ME_CURLY_QUOTE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Sink, Florida, Sink / Unsubstantiated Rumors — DARK GREEN 7”"


@respx.mock
async def test_crawl_catalog_recognizes_straight_quote_inch_mark(crawler):
    _mock_single_page([_AMPERE_RAEIN_SPLIT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == 'Split — BLUE 8"'


@respx.mock
async def test_crawl_catalog_drops_bare_color_variant_with_no_format_token(crawler):
    _mock_single_page([_DEFIANCE_OHIO_BARE_COLOR])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_unavailable_variant(crawler):
    _mock_single_page([_UNAVAILABLE_VARIANT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_parenthetical_no_quotes_falls_back_to_vendor(crawler):
    _mock_single_page([_ACRID_BUZZSAW])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "No Idea Records"
    assert items[0]["title"] == "ACRID / LEFT FOR DEAD BUZZSAW (BLUE-GREEN VARIANT) — BLUE-GREEN BUZZSAW-SHAPED LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_A_WILHELM_SCREAM, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "No Idea Records"
    assert Crawler.base_url == "https://noidearecords.com"
    assert Crawler.genre == "punk"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the tests to verify they fail on import**

Run: `cd backend && pytest tests/test_no_idea_records_crawler.py -v`
Expected: every test errors with `ModuleNotFoundError: No module named 'crawlers.no_idea_records'` (the crawler module doesn't exist yet).

- [ ] **Step 3: Write the crawler implementation**

Create `backend/crawlers/no_idea_records.py`:

```python
import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "list"
# Matches straight or curly quotes on either side independently, and doesn't
# require the closing quote to end the string -- titles like 'A WILHELM
# SCREAM "Partycrasher" + POSTER' have trailing format text after it.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*["“](?P<album>.+?)["”]')
# This store uses the curly right double quotation mark (U+201D) for the inch
# mark on some 7"/8" variants alongside straight quotes on others -- both
# forms confirmed live. 4/345 kept variants are accepted noise: a genuine
# vinyl pressing whose only variant-title signal is a bare color name (no
# "LP"/"vinyl"/inch-mark token at all) is indistinguishable from real
# non-vinyl noise on this store and is dropped along with it.
_VINYL_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\d+\s*[”″"]', re.IGNORECASE)


class Crawler:
    site_name: str = "No Idea Records"
    base_url: str = "https://noidearecords.com"
    genre_summary: str = "Gainesville, FL punk and emo label and mailorder store."
    genre: str = "punk"
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
        # No Idea's `vendor` is the store's own name, not the artist -- the
        # real artist only exists embedded in the title as Artist "Album
        # Title". Falls back to the store name if a title doesn't match that
        # pattern (confirmed live: 352/360 titles match, 97.8%).
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip()
        return (vendor or "").strip(), title.strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_no_idea_records_crawler.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && pytest`
Expected: no new failures (this change touches no shared code — `shopify_catalog.py` is read-only here — so nothing outside the new test file should be affected).

- [ ] **Step 6: Confirm automatic plugin registration**

Run:
```bash
cd backend && python3 -c "
from crawlers.no_idea_records import Crawler
c = Crawler()
print(c.site_name, c.base_url, c.genre, c.crawler_type, c.genre_summary)
"
```
Expected: prints `No Idea Records https://noidearecords.com punk catalog Gainesville, FL punk and emo label and mailorder store.` — confirms the module is importable and exposes the attributes `main.py`'s startup loop reads (`site_name`, `crawler_type`, `requires_discogs_release` — absent here, which `register_crawler()` treats as `False`, matching every other `catalog` plugin). No change to `main.py` or any router is needed.

- [ ] **Step 7: Commit**

```bash
git add backend/crawlers/no_idea_records.py backend/tests/test_no_idea_records_crawler.py
git commit -F <message-file>
```

Message file content (per this repo's required AI-attribution trailers):

```
Add No Idea Records store crawler

Shopify catalog crawler covering noidearecords.com's vinyl stock.
Iterates the store's "list" (Music) collection, parses the quoted-album
title convention (ARTIST "Album"), and filters to vinyl-only variants
via a per-variant regex extended for this store's curly-quote inch
marks. See design spec for full grounding.

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
symbols, and section names touched by this change (`no_idea_records`,
`noidearecords`, `list` collection, `shopify_catalog`) to confirm no other
spec describes behavior this branch altered. This crawler only adds a new
file and reads existing shared helpers unchanged, so drift is not expected,
but the check must still run and its result (found/not found) noted in the
PR description.
