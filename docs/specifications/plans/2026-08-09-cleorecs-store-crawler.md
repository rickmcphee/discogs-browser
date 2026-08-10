# Cleopatra Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `crawler_type="catalog"` plugin that ingests Cleopatra Records' vinyl stock from `https://cleorecs.com/collections/vinyl-1` into `stock_items`.

**Architecture:** A single new file, `backend/crawlers/cleorecs.py`, following the shape of the 33 existing Shopify plugins (`twentybuckspin.py` is the closest sibling). It delegates all HTTP, pacing, retry and progress reporting to the existing `shopify_catalog.iter_products()` helper and adds only this store's parsing and filtering rules. Registration is automatic via `main.py`'s startup scan of `backend/crawlers/`; no other file changes except the version bump.

**Tech Stack:** Python 3.9+, `httpx` (indirectly, via `shopify_catalog`), `pytest` with `asyncio_mode = "auto"`, `respx` for HTTP mocking.

**Spec:** [`docs/specifications/shaping/2026-08-09-cleorecs-store-crawler-design.md`](../shaping/2026-08-09-cleorecs-store-crawler-design.md)

## Global Constraints

- Python ≥3.9. No `str | None` union syntax — use `Optional[str]` or leave untyped.
- No comments unless the WHY is non-obvious. Where a rule exists because of a confirmed-live observation, say so and give the number.
- No backwards-compat shims.
- Every commit needs the AI-attribution trailer block as its last paragraph, created via `git commit -F <message-file>`, never `git commit -m`:
  ```
  Note: This commit message was created by AI
  ai-generated: true
  ai-model: claude-opus-5
  ai-tool: claude-code
  ai-surface: claude-code-desktop
  ai-executor: local-agent
  ```
- Run tests from `backend/`. These tests touch no database, so no Postgres env vars are needed: `cd backend && pytest tests/test_cleorecs_crawler.py -v`
- **The test file must be named `test_cleorecs_crawler.py`** — ending in `_crawler`, exactly. `conftest.py`'s autouse `_fast_catalog_crawl_sleep` fixture ([backend/tests/conftest.py:251](../../../backend/tests/conftest.py)) only patches out `shopify_catalog.sleep` when `request.module.__name__.endswith("_crawler")`. Under any other name the Task 3 tests sleep `crawl_delay_seconds` (default 30s) per page.
- `format` must be the literal string `"Vinyl"` on every emitted item — never `7"`, never `LP`. `ebay_api.FORMAT_KEYWORDS` and `FORMAT_CATEGORY_IDS` are keyed on `Vinyl`/`CD`/`Cassette`/`DVD`/`Blu-ray`; any other value resolves both lookups to `None` and silently drops eBay's keyword filter and category constraint.
- `product.get("vendor")` must never be used as an artist source. It is the imprint (`Cleopatra Records`, `Purple Pyramid Records`, `Deadline Music`, …) on 100% of live products.

---

### Task 1: Title parsing

Splits `"Artist - Album (Colour/Format)"` into artist and album. This is the whole of the store's artist attribution, so it gets its own task and its own tests.

**Files:**
- Create: `backend/crawlers/cleorecs.py`
- Test: `backend/tests/test_cleorecs_crawler.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Crawler._strip_trailing_parens(title: str) -> str` — `@staticmethod`. Returns `title.strip()` with every trailing `(...)` group removed, right to left, stopping at the first trailing text that is not bracketed. The result is always a prefix of `title.strip()`, which is what lets the album text be reconstructed from the original title by index.
  - `Crawler._parse_artist_title(title: str)` — `@classmethod`, returns a `(artist, album)` tuple of `str`. Task 2 calls this.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_cleorecs_crawler.py`. Every literal below is a real product confirmed live on 2026-08-09.

```python
import pytest

from crawlers.cleorecs import Crawler


@pytest.mark.parametrize("title,expected_artist,expected_album", [
    # Ordinary shape: split on " - ", parenthetical stays on the album.
    (
        "UFO - A Conspiracy Of Stars (Colored Double Vinyl LP)",
        "UFO",
        "A Conspiracy Of Stars (Colored Double Vinyl LP)",
    ),
    # En-dash separator. 34 live titles use it; only bigscarymonstersusa.py
    # allows this character today.
    (
        "U.K. Subs – Endangered Species (2 LP)",
        "U.K. Subs",
        "Endangered Species (2 LP)",
    ),
    # Hyphenated artist name. 18 live artists have an internal hyphen with no
    # surrounding space; a plain \s*-\s* split clips this to "Anti".
    (
        "Anti-Flag - Die For The Government (Picture Disc Vinyl)",
        "Anti-Flag",
        "Die For The Government (Picture Disc Vinyl)",
    ),
    # Non-greedy: only the FIRST separator splits, so a dash inside the album
    # title survives.
    (
        "Ministry - Hate To Go - Take Out Or Delivery (Colored Vinyl LP or Deluxe Box Set)",
        "Ministry",
        "Hate To Go - Take Out Or Delivery (Colored Vinyl LP or Deluxe Box Set)",
    ),
])
def test_parse_artist_title_splits_on_first_separator(title, expected_artist, expected_album):
    assert Crawler._parse_artist_title(title) == (expected_artist, expected_album)


def test_parse_artist_title_ignores_separator_inside_trailing_parenthetical():
    # 11 live titles have no artist prefix but do contain " - " inside their
    # trailing bracket. Splitting on it yields the artist
    # "Danzig Sings Elvis (Gatefold Green Vinyl LP".
    title = "Danzig Sings Elvis (Gatefold Green Vinyl LP - Signed by Glenn Danzig)"
    assert Crawler._parse_artist_title(title) == ("Various Artists", title)


def test_parse_artist_title_falls_back_to_various_artists_not_vendor():
    # 161 live products carry no artist in the title, overwhelmingly the
    # label's own compilations. "Various Artists" is the literal string
    # Discogs uses, so library matching still works.
    title = "Punk Rock Christmas (Black Vinyl LP Test Pressing)"
    assert Crawler._parse_artist_title(title) == ("Various Artists", title)


def test_strip_trailing_parens_removes_groups_right_to_left():
    assert Crawler._strip_trailing_parens(
        "Alleluia! The Devil's Carnival (Original Motion Picture 2015 Soundtrack) "
        "(Limited Edition Red & Black Marble LP)"
    ) == "Alleluia! The Devil's Carnival"


def test_strip_trailing_parens_stops_at_unbracketed_trailing_text():
    # Live title with text appended after a closing bracket. The strip must
    # stop there, leaving the string a prefix of the original.
    title = (
        "Anti-Flag - Die For The Government (Limited Edition Pink Vinyl)Out Of Print "
        "(Jacket cover has ding Right corner crease )"
    )
    assert Crawler._strip_trailing_parens(title) == (
        "Anti-Flag - Die For The Government (Limited Edition Pink Vinyl)Out Of Print"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_cleorecs_crawler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'crawlers.cleorecs'`

- [ ] **Step 3: Write the minimal implementation**

Create `backend/crawlers/cleorecs.py`:

```python
import re

_COLLECTION_SLUG = "vinyl-1"

# En-dash and em-dash are in the class because 34 live titles separate with
# "–" rather than "-" ("U.K. Subs – Endangered Species"). Whitespace is
# required on at least one side so hyphenated artist names survive: 18 live
# artists carry an internal hyphen with no surrounding space (Anti-Flag,
# Blink-182, Buck-O-Nine, Ann-Margret, Eek-A-Mouse).
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
_TRAILING_PARENS_RE = re.compile(r'\s*\([^()]*\)\s*$')


class Crawler:
    site_name: str = "Cleopatra Records"
    base_url: str = "https://cleorecs.com"
    crawler_type: str = "catalog"

    @classmethod
    def _parse_artist_title(cls, title: str):
        # `vendor` is the imprint on every live product (Cleopatra Records,
        # Purple Pyramid Records, Deadline Music, ...) and never the artist, so
        # the sibling crawlers' vendor fallback is deliberately not used here.
        #
        # The split point is found on the title with trailing parentheticals
        # removed, because 11 live titles carry no artist prefix but do contain
        # " - " inside their trailing bracket. The album text keeps the
        # parentheticals: db.py's _library_match_fragment matches
        # exact-or-prefix-with-space, so a colour/format suffix doesn't block a
        # catalog match.
        base = title.strip()
        stripped = cls._strip_trailing_parens(base)
        m = _TITLE_RE.match(stripped)
        if not m:
            return "Various Artists", base
        return m.group("artist").strip(), base[m.start("album"):].strip()

    @staticmethod
    def _strip_trailing_parens(title: str) -> str:
        stripped = title.strip()
        while True:
            shorter = _TRAILING_PARENS_RE.sub('', stripped)
            if shorter == stripped:
                return stripped
            stripped = shorter
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_cleorecs_crawler.py -v`
Expected: PASS, 8 tests (4 parametrized cases + 4 named tests)

- [ ] **Step 5: Commit**

```bash
cat > /tmp/cleorecs-task1-msg.txt <<'EOF'
feat: parse artist and album from Cleopatra Records product titles

vendor is the imprint on every product, so the artist comes from the
title. Separator class allows en-dash (34 live titles) and requires
whitespace on one side (18 live artists have an internal hyphen). The
split point is found with trailing parentheticals removed, because 11
live titles hide a " - " inside their trailing bracket.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/crawlers/cleorecs.py backend/tests/test_cleorecs_crawler.py && git commit -F /tmp/cleorecs-task1-msg.txt
```

---

### Task 2: Product filtering and variant rows

Turns one Shopify product dict into zero or more `stock_items` rows.

**Files:**
- Modify: `backend/crawlers/cleorecs.py`
- Test: `backend/tests/test_cleorecs_crawler.py`

**Interfaces:**
- Consumes: `Crawler._parse_artist_title(title)` from Task 1.
- Produces: `Crawler._items(product: dict) -> list[dict]` — `@classmethod`. Each returned dict has exactly the keys `artist`, `title`, `format`, `price`, `currency`, `url`, `cover_image_url`. Task 3 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_cleorecs_crawler.py`. Add `resolve_cover_image`'s behaviour to the imports at the top of the file only if needed — it is not; these tests assert on the returned URLs directly.

```python
_MULTI_COLOUR_PRODUCT = {
    "title": "UFO - A Conspiracy Of Stars (Colored Double Vinyl LP)",
    "vendor": "Cleopatra Records",
    "handle": "ufo-a-conspiracy-of-stars-colored-double-vinyl-lp",
    "product_type": "LP",
    "tags": ["Cleopatra Records", "Double LP", "Pre-Orders", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/product-fallback.png"}],
    "variants": [
        {
            "title": "Red Marble",
            "price": "38.98",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/CLO6869LP-RD-MAR-1.png"},
        },
        {
            "title": "Blue Marble",
            "price": "38.98",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/CLO6878LP-BL-MAR-1.png"},
        },
    ],
}

_DEFAULT_TITLE_PRODUCT = {
    "title": "Anti-Flag - Die For The Government (Picture Disc Vinyl)",
    "vendor": "New Red Archives",
    "handle": "anti-flag-die-for-the-government-picture-disc-vinyl",
    "product_type": "LP",
    "tags": ["Anti-Flag", "Punk", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/CLO2659PD-1-1.png"}],
    "variants": [
        {"title": "Default Title", "price": "24.98", "available": True, "featured_image": None},
    ],
}

_SEVEN_INCH_PRODUCT = {
    "title": "Iggy & The Stooges - Cock In My Pocket (Red 7\" Vinyl)",
    "vendor": "Cleopatra Records",
    "handle": "iggy-the-stooges-cock-in-my-pocket-red-7-vinyl",
    "product_type": "SP",
    "tags": ["7 Inch Vinyl", "Punk Rock"],
    "images": [{"src": "https://cdn.shopify.com/R-1889634-1250363226_1.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "29.99", "available": True, "featured_image": None},
    ],
}

_POSTER_PRODUCT = {
    "title": "Revolting Cocks (12\" x 12\" Poster)",
    "vendor": "Cleopatra Records",
    "handle": "revolting-cocks-12-x-12-poster",
    "product_type": "PS",
    "tags": ["Merch", "Poster"],
    "images": [{"src": "https://cdn.shopify.com/MER0250PS-2.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "29.98", "available": True, "featured_image": None},
    ],
}

_SHIRT_BUNDLE_PRODUCT = {
    "title": "Tank - Filth Hounds Of Hades (Double Vinyl LP + Shirt + Tote Bag Bundle)",
    "vendor": "Cleopatra Records",
    "handle": "tank-filth-hounds-of-hades-double-vinyl-lp-shirt-tote-bag-bundle",
    "product_type": "BND",
    "tags": ["Bundle", "Merch", "T-Shirt"],
    "images": [{"src": "https://cdn.shopify.com/CLO7380LP-BND.png"}],
    "variants": [
        {"title": "Short Sleeve Shirt - Small", "price": "77.97", "available": True, "featured_image": None},
        {"title": "Long Sleeve Shirt - XX-Large", "price": "85.97", "available": True, "featured_image": None},
    ],
}

_BOOK_PRODUCT = {
    "title": "The Dickies And Me by Leonard Graves Phillips (Hardback Book + 7\" Vinyl)",
    "vendor": "Cleopatra Records",
    "handle": "the-dickies-and-me-by-leonard-graves-phillips-hardback-book-7-vinyl",
    "product_type": "BK",
    "tags": ["book", "Hardback Book", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/CLO7264BK-1.png"}],
    "variants": [
        {"title": "Default Title", "price": "69.98", "available": True, "featured_image": None},
    ],
}


def test_items_emits_one_row_per_colour_variant_with_its_own_image():
    items = Crawler._items(_MULTI_COLOUR_PRODUCT)
    assert [i["title"] for i in items] == [
        "A Conspiracy Of Stars (Colored Double Vinyl LP) — Red Marble",
        "A Conspiracy Of Stars (Colored Double Vinyl LP) — Blue Marble",
    ]
    assert [i["cover_image_url"] for i in items] == [
        "https://cdn.shopify.com/CLO6869LP-RD-MAR-1.png",
        "https://cdn.shopify.com/CLO6878LP-BL-MAR-1.png",
    ]
    assert all(i["artist"] == "UFO" for i in items)
    assert all(i["price"] == 38.98 for i in items)


def test_items_omits_shopify_default_title_placeholder():
    # 2,650 of 3,151 live available variants are named "Default Title";
    # appending it the way subpopmegamart.py and twentybuckspin.py do would
    # stamp "— Default Title" onto almost every row.
    items = Crawler._items(_DEFAULT_TITLE_PRODUCT)
    assert len(items) == 1
    assert items[0]["title"] == "Die For The Government (Picture Disc Vinyl)"
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/CLO2659PD-1-1.png"


def test_items_emits_full_row_shape():
    items = Crawler._items(_DEFAULT_TITLE_PRODUCT)
    assert items[0] == {
        "artist": "Anti-Flag",
        "title": "Die For The Government (Picture Disc Vinyl)",
        "format": "Vinyl",
        "price": 24.98,
        "currency": "USD",
        "url": "https://cleorecs.com/products/anti-flag-die-for-the-government-picture-disc-vinyl",
        "cover_image_url": "https://cdn.shopify.com/CLO2659PD-1-1.png",
    }


def test_items_reports_seven_inch_singles_as_vinyl():
    # ebay_api.FORMAT_KEYWORDS/FORMAT_CATEGORY_IDS are keyed on "Vinyl"; a
    # 7" value would resolve both to None and drop eBay's filters.
    items = Crawler._items(_SEVEN_INCH_PRODUCT)
    assert len(items) == 1
    assert items[0]["format"] == "Vinyl"


@pytest.mark.parametrize("product", [_POSTER_PRODUCT, _SHIRT_BUNDLE_PRODUCT, _BOOK_PRODUCT])
def test_items_drops_non_vinyl_products(product):
    assert Crawler._items(product) == []


def test_items_drops_merch_typed_as_vinyl():
    # product_type is correct on today's data, but 20 Buck Spin hit a tote bag
    # typed "VINYL" live, so the title keyword check backs it up.
    product = {**_DEFAULT_TITLE_PRODUCT, "product_type": "LP",
               "title": "Cleopatra Records - Logo Tote Bag"}
    assert Crawler._items(product) == []


def test_items_skips_unavailable_variants():
    product = {**_MULTI_COLOUR_PRODUCT, "variants": [
        {**_MULTI_COLOUR_PRODUCT["variants"][0], "available": False},
        _MULTI_COLOUR_PRODUCT["variants"][1],
    ]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "A Conspiracy Of Stars (Colored Double Vinyl LP) — Blue Marble"


def test_items_keeps_test_pressings_marked_by_their_own_title():
    # All 533 live tag-bearing test pressings also say it in the title, so no
    # decoration is needed — keeping the title verbatim is the marking.
    product = {**_DEFAULT_TITLE_PRODUCT,
               "title": "Punk Rock Christmas (Black Vinyl LP Test Pressing)",
               "tags": ["Test Pressing", "Vinyl Test Pressing"]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["artist"] == "Various Artists"
    assert items[0]["title"] == "Punk Rock Christmas (Black Vinyl LP Test Pressing)"


def test_items_handles_null_variants():
    assert Crawler._items({**_DEFAULT_TITLE_PRODUCT, "variants": None}) == []


def test_items_emits_none_price_on_unparseable_price():
    product = {**_DEFAULT_TITLE_PRODUCT, "variants": [
        {"title": "Default Title", "price": None, "available": True, "featured_image": None},
    ]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["price"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_cleorecs_crawler.py -v`
Expected: FAIL — `AttributeError: type object 'Crawler' has no attribute '_items'`. Task 1's tests still pass.

- [ ] **Step 3: Write the minimal implementation**

Add the import and the two new module constants at the top of `backend/crawlers/cleorecs.py`:

```python
from shopify_catalog import resolve_cover_image
```

```python
# Confirmed live in this collection: BND shirt bundles, BK books, PS/PO
# posters, CD/DVD/BR discs, TB tote bags.
_NON_VINYL_TYPES = {"BND", "BK", "PS", "PO", "DVD", "BR", "TB", "CD"}
# product_type is correct on today's data, but 20 Buck Spin hit a tote bag
# typed "VINYL" live, so the title keyword check backs it up.
_MERCH_TITLE_RE = re.compile(
    r'poster|hardback book|tote bag|\bshirt\b|hoodie|sweater|bundle', re.IGNORECASE)
_DEFAULT_VARIANT_TITLE = "Default Title"
```

Add the method to `Crawler`, above `_parse_artist_title`:

```python
    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        raw_title = product.get("title", "")
        if product.get("product_type") in _NON_VINYL_TYPES:
            return []
        if _MERCH_TITLE_RE.search(raw_title):
            return []

        artist, album = cls._parse_artist_title(raw_title)
        url = f"{cls.base_url}/products/{product.get('handle', '')}"

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            variant_title = (variant.get("title") or "").strip()
            title = album if variant_title in ("", _DEFAULT_VARIANT_TITLE) \
                else f"{album} — {variant_title}"
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_cleorecs_crawler.py -v`
Expected: PASS, all tests including Task 1's.

- [ ] **Step 5: Commit**

```bash
cat > /tmp/cleorecs-task2-msg.txt <<'EOF'
feat: filter non-vinyl products and emit one row per Cleopatra vinyl variant

Drops posters, books, discs and shirt bundles by product_type, backed by
a title keyword check. One row per available variant, naming the pressing
colour only when the variant isn't Shopify's "Default Title" placeholder
-- 2,650 of 3,151 live variants carry it. format stays "Vinyl" even for
7" singles: ebay_api keys its keyword and category lookups on that value.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/crawlers/cleorecs.py backend/tests/test_cleorecs_crawler.py && git commit -F /tmp/cleorecs-task2-msg.txt
```

---

### Task 3: Collection crawl and registration

Wires `_items` to the paginated collection feed and declares the plugin metadata `main.py` registers on.

**Files:**
- Modify: `backend/crawlers/cleorecs.py`
- Test: `backend/tests/test_cleorecs_crawler.py`

**Interfaces:**
- Consumes: `Crawler._items(product)` from Task 2.
- Produces: `Crawler.crawl_catalog() -> AsyncIterator[dict]` — the entry point `crawl_manager._sync_stock` calls. Plus the class attributes `site_name`, `base_url`, `crawler_type` that `main.py`'s `_crawler_metadata()` reads.

- [ ] **Step 1: Write the failing tests**

Add these imports to the top of `backend/tests/test_cleorecs_crawler.py`:

```python
import httpx
import respx
```

Append:

```python
_PRODUCTS_URL = "https://cleorecs.com/collections/vinyl-1/products.json"


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@respx.mock
async def test_crawl_catalog_yields_items_across_pages():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_MULTI_COLOUR_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_SEVEN_INCH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert [i["artist"] for i in items] == ["UFO", "UFO", "Iggy & The Stooges"]


@respx.mock
async def test_crawl_catalog_drops_non_vinyl_products_from_the_feed():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_POSTER_PRODUCT, _BOOK_PRODUCT, _SHIRT_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Cleopatra Records"
    assert Crawler.base_url == "https://cleorecs.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_cleorecs_crawler.py -v`
Expected: FAIL — `AttributeError: 'Crawler' object has no attribute 'crawl_catalog'`. `test_site_metadata` already passes.

- [ ] **Step 3: Write the minimal implementation**

Extend the imports at the top of `backend/crawlers/cleorecs.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image
```

Add the method to `Crawler`, above `_items`:

```python
    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_cleorecs_crawler.py -v`
Expected: PASS, every test in the file.

- [ ] **Step 5: Run the wider crawler suite for regressions**

Run: `cd backend && pytest tests/ -k crawler -v`
Expected: PASS. No existing crawler test should change; this task adds a file and touches nothing shared.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/cleorecs-task3-msg.txt <<'EOF'
feat: crawl the Cleopatra Records vinyl collection

Paginates /collections/vinyl-1/products.json through
shopify_catalog.iter_products, which already supplies pacing, the 429
fail-fast rule and progress reporting. 15 requests per sync. Registration
is automatic via main.py's startup scan of backend/crawlers/.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/crawlers/cleorecs.py backend/tests/test_cleorecs_crawler.py && git commit -F /tmp/cleorecs-task3-msg.txt
```

---

### Task 4: Version bump, drift check, PR

**Files:**
- Modify: `backend/version.py:1`

- [ ] **Step 1: Bump the version**

`backend/version.py` currently reads `VERSION = "3.13"`. Change it to:

```python
VERSION = "3.14"
```

Minor bump is the default automatic action on every PR that merges to `main`. Do not take a major bump.

- [ ] **Step 2: Run the pre-PR spec-drift check**

This is required on every branch, including ones whose change has its own spec. Grep **both** spec trees for the symbols and strings this diff touches:

```bash
cd /Users/rickmcphee/Documents/GitHub/discogs-browser/.claude/worktrees/auto-merge-when-ready-config-7f5679 && grep -rln "crawl_catalog\|shopify_catalog\|iter_products\|FORMAT_KEYWORDS\|resolve_cover_image\|catalog_browser" docs/superpowers/specs/ docs/specifications/shaping/
```

For each file that matches, confirm its text still describes what this branch ships. Expected outcome, to be verified rather than assumed: no drift, because this change adds a plugin and modifies no shared code path. If any spec has drifted, amend it as its own commit on this branch and push it before opening the PR.

- [ ] **Step 3: Run the full backend test suite**

Postgres-backed tests need all three vars:

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest
```

Expected: PASS. Report the actual output; do not claim success without it.

- [ ] **Step 4: Commit the version bump**

```bash
cat > /tmp/cleorecs-task4-msg.txt <<'EOF'
chore: bump version to 3.14

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
EOF
git add backend/version.py && git commit -F /tmp/cleorecs-task4-msg.txt
```

- [ ] **Step 5: Open the PR**

Open it as ready for review, never a draft — pass `--draft=false`. The description must state what the drift check found (or that none was found), and must name the accepted consequence from the spec: ~9,450 `crawl_queue` jobs per sync, ~39 hours to drain, no window applied, release rows still claim ahead of stock-item rows.

---

## Manual verification (not automated)

Per the repo's convention, the live crawl path stays manually integration-tested. After merge, confirm on a running backend that a stock sync for `Cleopatra Records` writes roughly 3,150 rows, that no row's artist is `Cleopatra Records` or another imprint, and that the Store tab shows colour variants as separate rows.
