# Carpark Records Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/carparkrecords.py`, a `crawler_type="catalog"` Shopify plugin covering Carpark Records's (`store.carparkrecords.com`) `music` collection.

**Architecture:** Iterate the store's `music` collection via the existing `shopify_catalog.iter_products()` helper. Gate each *product* on `product_type == "music"` (excludes the store's one `Merch`-typed row). Parse `artist`/`album` by stripping an optional leading catalog-code prefix (`CAK188`, `CAKD067`, ...) then splitting the remainder on the first whitespace-dash-whitespace separator — falling back to `vendor` for the one product with no dash at all. Filter *variants* negatively: drop only variants matching a small non-vinyl keyword set (`cd`, `cassette`/`cs`, `tape`, `digital`, `christmas ornament`, `playing cards`, `dvd`, plus a `\bcds?\b`/`\btape\b`/etc. suffix match for compound titles like `Gemini I CD`) — everything else, including the many no-keyword color/edition variant names this store uses, is kept as vinyl. Fan out one stock item per surviving variant, titled with the variant descriptor only when it's not `Default Title`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, and `resolve_cover_image()` unchanged (per design spec's "Scope").
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"` (deliberate vinyl-only scope decision, matching every sibling Shopify crawler).
- `_COLLECTION_SLUG = "music"`, not `"lp"` — design spec's "Collection choice" section confirms `lp` misses vinyl stock that only appears in `double-lp`/`7`.
- `product_type` gate applies before any title parsing; only `{"music"}` passes.
- Per-variant filter is negative (drop on non-vinyl signal), never positive — a positive filter would silently drop the many no-keyword vinyl color/edition variants this store uses (design spec's "Per-variant filter" section).
- Pre-order carve-out included (`has_tag(product, "preorder")`, lowercase to match this store's actual tag casing — `has_tag()` is already case-insensitive so behavior is identical either way).
- Title is `album_title` alone when the variant title is `Default Title`, otherwise `f"{album_title} — {variant_title}"` — matches `anxiousandangry.py`/`bigscarymonstersusa.py`'s convention.
- No comments except where the WHY is non-obvious (a hidden constraint, a confirmed-live edge case) — no comments describing WHAT the code does.
- Registration is automatic via `main.py`'s startup loop (reads `site_name`/`crawler_type`/`requires_discogs_release` off the module) — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md` (`ai-generated: true`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule below: [`docs/specifications/shaping/2026-08-19-carpark-records-crawler-design.md`](../shaping/2026-08-19-carpark-records-crawler-design.md).

---

### Task 1: Carpark Records crawler + tests

**Files:**
- Create: `backend/crawlers/carparkrecords.py`
- Test: `backend/tests/test_carparkrecords_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]`, `shopify_catalog.has_tag(product, tag) -> bool`, `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: `Crawler` class with `site_name: str`, `base_url: str`, `genre_summary: str`, `genre: str`, `crawler_type: str = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. This is the standard `catalog` plugin interface `main.py`'s startup loop discovers — no other task depends on internals beyond this shape.

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_carparkrecords_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.carparkrecords import Crawler

_PRODUCTS_URL = "https://store.carparkrecords.com/collections/music/products.json"

# Real confirmed-live case: space-separated catalog code, preorder-tagged,
# mixed availability, non-vinyl variants (CD/Digital) present alongside LP
# variants -- exercises code-strip, preorder unavailable-keep, and the
# non-vinyl variant filter all in one product.
_DENT_MAY_THE_BIG_ONE = {
    "title": "CAK188 Dent May - The Big One",
    "vendor": "Carpark",
    "handle": "cak188-dent-may-the-big-one",
    "product_type": "Music",
    "tags": ["Carpark Records", "Dent May", "preorder", "the big one"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0805/4266/2938/files/dentmay.jpg"}],
    "variants": [
        {"title": "Limited Edition Carpark Exclusive Red LP", "price": "27.99", "available": False, "featured_image": None},
        {"title": "Limited Edition Olive LP", "price": "26.99", "available": True, "featured_image": None},
        {"title": "CD", "price": "15.99", "available": True, "featured_image": None},
        {"title": "Digital", "price": "9.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: dash-prefixed catalog code ("CAK189 - casi -
# CASI") -- code strip must not consume the artist/title separator too.
_CASI_CASI = {
    "title": "CAK189 - casi - CASI",
    "vendor": "Carpark",
    "handle": "cak189-casi-casi",
    "product_type": "Music",
    "tags": ["Carpark Records", "casi"],
    "images": [],
    "variants": [
        {"title": "Limited Edition Red vinyl", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a literal tab character stands in for a space
# before the artist/title dash -- the split regex must treat it as
# whitespace, same as an ordinary space.
_TANUKICHAN_SPACE_GHOST = {
    "title": "CAK187 - Tanukichan x Space Ghost\t- Circles - Space Ghost Remix",
    "vendor": "Carpark",
    "handle": "cak187-tanukichan-x-space-ghost-circles-space-ghost-remix",
    "product_type": "Music",
    "tags": ["preorder", "space ghost", "Tanukichan"],
    "images": [],
    "variants": [
        {"title": "Limited Edition 12\"", "price": "14.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no catalog code at all -- code-strip regex must
# no-op cleanly, and the product's only variant is an unavailable Digital
# (non-preorder), so no items should be yielded at all.
_TANUKICHAN_MAKE_BELIEVE = {
    "title": "Tanukichan - Make Believe",
    "vendor": "Carpark",
    "handle": "tanukichan-make-believe",
    "product_type": "Music",
    "tags": ["new release", "Tanukichan"],
    "images": [],
    "variants": [
        {"title": "Digital", "price": "1.29", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: no dash separator anywhere in the title --
# falls back to `vendor` ("Carpark", the label's own name) as artist.
_SWEET_SIXTEEN = {
    "title": "CAK104 Carpark Sweet Sixteen Basketball Picture Disc LP",
    "vendor": "Carpark",
    "handle": "cak104-carpark-sweet-sixteen-basketball-picture-disc-lp",
    "product_type": "Music",
    "tags": ["Carpark Records"],
    "images": [],
    "variants": [
        {"title": "Basketball Picture Disc LP", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a genuine vinyl variant with no vinyl/LP
# keyword at all ("Eco Mix Red") alongside an unavailable, non-preorder
# Christmas Ornament bonus-item variant and a Digital variant -- both of
# the latter must be dropped while the keyword-less vinyl variant and the
# real LP variant are both kept.
_PEACE_OF_US = {
    "title": "CAK177 - Dean & Britta & Sonic Boom - A Peace of Us",
    "vendor": "Carpark",
    "handle": "cak177-dean-britta-sonic-boom-a-peace-of-us",
    "product_type": "Music",
    "tags": ["Dean & Britta & Sonic Boom"],
    "images": [],
    "variants": [
        {"title": "LP", "price": "26.99", "available": True, "featured_image": None},
        {"title": "Christmas Ornament", "price": "20.00", "available": False, "featured_image": None},
        {"title": "Digital", "price": "9.99", "available": True, "featured_image": None},
        {"title": "Eco Mix Red", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "Playing Cards" bonus-item variant bundled
# alongside real LP/Cassette/CD/Digital variants -- only the two vinyl
# variants should survive.
_CLOUD_NOTHINGS = {
    "title": "CAK130 Cloud Nothings - Last Building Burning",
    "vendor": "Carpark",
    "handle": "cak130-cloud-nothings-last-building-burning",
    "product_type": "Music",
    "tags": ["Cloud Nothings"],
    "images": [],
    "variants": [
        {"title": "Limited LP (clear vinyl)", "price": "26.99", "available": True, "featured_image": None},
        {"title": "LP", "price": "26.99", "available": True, "featured_image": None},
        {"title": "CD", "price": "15.99", "available": True, "featured_image": None},
        {"title": "Cassette", "price": "10.99", "available": True, "featured_image": None},
        {"title": "Playing Cards", "price": "9.99", "available": True, "featured_image": None},
        {"title": "Digital", "price": "9.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "LP + DVD" bundle variant -- must be kept as
# vinyl (contains "LP"), not dropped as a DVD.
_EXCEPTER_BLACK_BEACH = {
    "title": "PAW28 Excepter - Black Beach",
    "vendor": "Paw Tracks",
    "handle": "paw28-excepter-black-beach",
    "product_type": "Music",
    "tags": ["Excepter"],
    "images": [],
    "variants": [
        {"title": "Digital", "price": "13.99", "available": True, "featured_image": None},
        {"title": "LP + DVD", "price": "27.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: standalone "DVD" variant (no LP paired with
# it) -- must be dropped.
_TAKAGI_MASAKATSU = {
    "title": "CAK036 Takagi Masakatsu - World Is So Beautiful",
    "vendor": "Carpark",
    "handle": "cak036-takagi-masakatsu-world-is-so-beautiful",
    "product_type": "Music",
    "tags": ["Takagi Masakatsu"],
    "images": [],
    "variants": [
        {"title": "DVD", "price": "15.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: compound variant titles with the format word
# as a suffix rather than the whole title ("Gemini I CD", "Gemini II CD")
# -- an exact-match-only regex would miss these; both must be dropped.
_GEMINI = {
    "title": "WIX04/05 Johanna Warren - Gemini I & II",
    "vendor": "Wax Nine",
    "handle": "wix04-05-johanna-warren-gemini-i-ii",
    "product_type": "Music",
    "tags": ["Johanna Warren"],
    "images": [],
    "variants": [
        {"title": "Gemini I CD", "price": "15.99", "available": False, "featured_image": None},
        {"title": "Gemini II CD", "price": "15.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: product_type "Merch" (a print/bundle item, not
# a music release) -- must be excluded entirely regardless of title shape
# or variant contents.
_MERCH_BUNDLE = {
    "title": "CAKD074 Madeline Kenney - Summer Quarter",
    "vendor": "Carpark",
    "handle": "cakd074-madeline-kenney-summer-quarter",
    "product_type": "Merch",
    "tags": ["Madeline Kenney"],
    "images": [],
    "variants": [
        {"title": "Summer Evening' Riso Print + EP Bundle", "price": "19.99", "available": True, "featured_image": None},
        {"title": "Digital", "price": "3.99", "available": True, "featured_image": None},
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
async def test_crawl_catalog_preorder_keeps_unavailable_drops_non_vinyl(crawler):
    _mock_single_page([_DENT_MAY_THE_BIG_ONE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {
        "The Big One — Limited Edition Carpark Exclusive Red LP (Pre-Order)",
        "The Big One — Limited Edition Olive LP (Pre-Order)",
    }
    assert all(item["artist"] == "Dent May" for item in items)


@respx.mock
async def test_crawl_catalog_dash_prefixed_code(crawler):
    _mock_single_page([_CASI_CASI])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "casi"
    assert items[0]["title"] == "CASI — Limited Edition Red vinyl"


@respx.mock
async def test_crawl_catalog_tab_before_dash(crawler):
    _mock_single_page([_TANUKICHAN_SPACE_GHOST])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Tanukichan x Space Ghost"
    # Also preorder-tagged live -- (Pre-Order) suffix is expected here too.
    assert items[0]["title"] == "Circles - Space Ghost Remix — Limited Edition 12\" (Pre-Order)"


@respx.mock
async def test_crawl_catalog_no_catalog_code_and_no_available_variant(crawler):
    _mock_single_page([_TANUKICHAN_MAKE_BELIEVE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_no_dash_falls_back_to_vendor(crawler):
    _mock_single_page([_SWEET_SIXTEEN])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Carpark"
    # No dash to split on, so the fallback title (the full code-stripped
    # string) and the variant descriptor happen to overlap here -- expected,
    # not special-cased.
    assert items[0]["title"] == "Carpark Sweet Sixteen Basketball Picture Disc LP — Basketball Picture Disc LP"


@respx.mock
async def test_crawl_catalog_keyword_less_vinyl_variant_kept_bonus_item_dropped(crawler):
    _mock_single_page([_PEACE_OF_US])
    items = [item async for item in crawler.crawl_catalog()]
    titles = {item["title"] for item in items}
    assert titles == {"A Peace of Us — LP", "A Peace of Us — Eco Mix Red"}


@respx.mock
async def test_crawl_catalog_playing_cards_dropped(crawler):
    _mock_single_page([_CLOUD_NOTHINGS])
    items = [item async for item in crawler.crawl_catalog()]
    titles = {item["title"] for item in items}
    assert titles == {
        "Last Building Burning — Limited LP (clear vinyl)",
        "Last Building Burning — LP",
    }


@respx.mock
async def test_crawl_catalog_lp_plus_dvd_kept_as_vinyl(crawler):
    _mock_single_page([_EXCEPTER_BLACK_BEACH])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Black Beach — LP + DVD"


@respx.mock
async def test_crawl_catalog_standalone_dvd_dropped(crawler):
    _mock_single_page([_TAKAGI_MASAKATSU])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_compound_cd_suffix_dropped(crawler):
    _mock_single_page([_GEMINI])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_merch_product_type(crawler):
    _mock_single_page([_MERCH_BUNDLE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_CASI_CASI, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Carpark Records"
    assert Crawler.base_url == "https://store.carparkrecords.com"
    assert Crawler.genre == "indie"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the tests to verify they fail on import**

Run: `cd backend && pytest tests/test_carparkrecords_crawler.py -v`
Expected: every test errors with `ModuleNotFoundError: No module named 'crawlers.carparkrecords'` (the crawler module doesn't exist yet).

- [ ] **Step 3: Write the crawler implementation**

Create `backend/crawlers/carparkrecords.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "music"
_PREORDER_TAG = "preorder"
_ALLOWED_PRODUCT_TYPES = {"music"}
# Matches an optional catalog-number prefix ("CAK188", "CAKD067", "CAK087X",
# and the one dual-catalog-number "WIX04/05") immediately followed by the
# artist/title text -- a no-op when a title has no such prefix, since the
# class only matches an all-caps run immediately followed by digits, which
# no artist name in this catalog's live title set does.
_CODE_RE = re.compile(r'^[A-Z]{2,5}\d{1,4}[A-Z]?(?:/\d{1,4})?\s*-?\s*')
# Splits on the first whitespace-dash-whitespace run, not a bare "-", so an
# unspaced hyphen inside an album title (e.g. "2001-2005") isn't mistaken
# for the artist/title separator. \s also matches the one confirmed-live
# title with a literal tab character in place of a space before the dash.
_SPLIT_RE = re.compile(r'\s+-\s+')
# Non-vinyl variant titles are almost always a bare format word, but two
# confirmed-live products ("Gemini I CD", "Deluxe Ibanez DE-7 Pink Edition
# Tape") carry it as a suffix instead -- the \b-bounded suffix match catches
# those without matching any live vinyl variant (no vinyl variant title in
# this catalog contains "cd", "tape", "cassette", or "digital" as a word).
_NON_VINYL_RE = re.compile(r'^(cd|cs|cassette|tape|digital|christmas ornament|playing cards|dvd)$', re.IGNORECASE)
_NON_VINYL_SUFFIX_RE = re.compile(r'\btape\b|\bcassette\b|\bdigital\b|\bcds?\b', re.IGNORECASE)


class Crawler:
    site_name: str = "Carpark Records"
    base_url: str = "https://store.carparkrecords.com"
    genre_summary: str = "Annandale/Baltimore indie label -- Toro y Moi, Beach House, Dan Deacon, Speedy Ortiz, The Beths."
    genre: str = "indie"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        product_type = (product.get("product_type") or "").strip().lower()
        if product_type not in _ALLOWED_PRODUCT_TYPES:
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
            stripped = variant_title.strip()
            if _NON_VINYL_RE.match(stripped) or _NON_VINYL_SUFFIX_RE.search(stripped):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = (
                album_title if stripped.lower() == "default title"
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
        rest = _CODE_RE.sub("", title, count=1).strip()
        parts = _SPLIT_RE.split(rest, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()
        return (vendor or "").strip(), rest
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_carparkrecords_crawler.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && pytest`
Expected: no new failures (this change touches no shared code — `shopify_catalog.py` is read-only here — so nothing outside the new test file should be affected).

- [ ] **Step 6: Confirm automatic plugin registration**

Run:
```bash
cd backend && python3 -c "
from crawlers.carparkrecords import Crawler
c = Crawler()
print(c.site_name, c.base_url, c.genre, c.crawler_type, c.genre_summary)
"
```
Expected: prints `Carpark Records https://store.carparkrecords.com indie catalog Annandale/Baltimore indie label -- Toro y Moi, Beach House, Dan Deacon, Speedy Ortiz, The Beths.` — confirms the module is importable and exposes the attributes `main.py`'s startup loop reads (`site_name`, `crawler_type`, `requires_discogs_release` — absent here, which `register_crawler()` treats as `False`, matching every other `catalog` plugin). No change to `main.py` or any router is needed.

- [ ] **Step 7: Commit**

```bash
git add backend/crawlers/carparkrecords.py backend/tests/test_carparkrecords_crawler.py
git commit -F <message-file>
```

Message file content (per this repo's required AI-attribution trailers):

```
Add Carpark Records store crawler

Shopify catalog crawler covering store.carparkrecords.com's music
collection. Gates products on product_type ("music" only, excluding the
store's one Merch-typed row), parses artist/title by stripping an
optional leading catalog-code prefix then splitting on the first
whitespace-dash-whitespace separator, and filters variants negatively
against a small non-vinyl keyword set -- this store's real vinyl variants
are frequently bare color/edition names with no format keyword at all, so
a positive filter would drop them. See design spec for full grounding.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
```

---

## Post-implementation: pre-PR spec-drift check

Before opening the PR, per this repo's `CLAUDE.md`: `grep -rl` across both `docs/superpowers/specs/` and `docs/specifications/shaping/` for files, symbols, and section names touched by this change (`carparkrecords`, `carpark records`, `music` collection, `shopify_catalog`) to confirm no other spec describes behavior this branch altered. This crawler only adds a new file and reads existing shared helpers unchanged, so drift is not expected, but the check must still run and its result (found/not found) noted in the PR description.
