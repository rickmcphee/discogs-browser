# Anxious and Angry Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/anxiousandangry.py`, a `crawler_type="catalog"` Shopify plugin covering Anxious and Angry's (`anxiousandangry.com`) `record-store` collection.

**Architecture:** Iterate the store's `record-store` collection via the existing `shopify_catalog.iter_products()` helper. Parse `artist`/`album` off the title's `Artist "Album"` quoted convention (falling back to `vendor` for the minority with no quotes). Gate each *product* using the text after the closing quote (or the full title, for quote-less fallbacks): exclude only when it signals CD/cassette/gift-card with no vinyl signal at all — this collection mixes real non-vinyl products in, unlike most sibling Shopify crawlers' pre-filtered collections. Then filter *variants* negatively (only `cd`/`cassette` exact matches are dropped), since this store's variant titles are almost always a bare color name or `Default Title`, not a format word — the one exception being 4 products that genuinely offer separate `LP`/`CD` variants. Fan out one stock item per surviving variant, titled with the variant descriptor only when it's not `Default Title`.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped.
- No new shared module — reuse `shopify_catalog.iter_products()`, `has_tag()`, and `resolve_cover_image()` unchanged (per design spec's "Scope").
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"` (deliberate vinyl-only scope decision, matching every sibling Shopify crawler).
- Product-level gate and per-variant filter apply the text-after-quote regexes and the negative variant regex exactly as measured in the design spec (107/128 products pass, 120 stock items total).
- Pre-order carve-out included (`has_tag(product, "PREORDER")`) even though no vinyl product in the sampled catalog currently carries it — the tag exists on this store (on an excluded CD product), unlike `no_idea_records.py` where it's absent store-wide.
- Title is `album_title` alone when the variant title is `Default Title`, otherwise `f"{album_title} — {variant_title}"` — matches `bigscarymonstersusa.py`/`closedcasketactivities.py`'s convention, not `deathwishinc.py`'s unconditional-suffix one (see design spec's "Fields" section for why).
- No comments except where the WHY is non-obvious (a hidden constraint, a confirmed-live edge case) — no comments describing WHAT the code does.
- Registration is automatic via `main.py`'s startup loop (reads `site_name`/`crawler_type`/`requires_discogs_release` off the module) — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md` (`ai-generated: true`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule below: [`docs/specifications/shaping/2026-08-19-anxious-and-angry-crawler-design.md`](../shaping/2026-08-19-anxious-and-angry-crawler-design.md).

---

### Task 1: Anxious and Angry crawler + tests

**Files:**
- Create: `backend/crawlers/anxiousandangry.py`
- Test: `backend/tests/test_anxiousandangry_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]`, `shopify_catalog.has_tag(product, tag) -> bool`, `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]` — all exist unchanged in `backend/shopify_catalog.py`.
- Produces: `Crawler` class with `site_name: str`, `base_url: str`, `genre_summary: str`, `genre: str`, `crawler_type: str = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. This is the standard `catalog` plugin interface `main.py`'s startup loop discovers — no other task depends on internals beyond this shape.

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_anxiousandangry_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.anxiousandangry import Crawler

_PRODUCTS_URL = "https://anxiousandangry.com/collections/record-store/products.json"

# Real confirmed-live case: quoted album, single "Default Title" vinyl
# variant -- title must NOT be suffixed with the meaningless variant label.
_ABSENT_IN_BODY = {
    "title": 'Absent In Body "Plague God" LP',
    "vendor": "Anxious and Angry",
    "handle": "absent-in-body-plague-god-lp",
    "tags": ["Band Vinyl", "LPs", "Record Store", "VINYL"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0046/3142/9190/products/4062656-2795494.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "20.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: two genuine vinyl color variants, no CD sibling
# -- both survive, each title suffixed with its own color descriptor.
_ARRIVALS_PAYLOAD = {
    "title": 'Arrivals, The "Payload" LP',
    "vendor": "Arrivals Payload Pre",
    "handle": "arrivals-the-payload-lp",
    "tags": ["Band Vinyl", "LP", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Orange Vinyl", "price": "30.00", "available": True, "featured_image": None},
        {"title": "Blue Vinyl", "price": "30.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quotes at all -- falls back to `vendor` as
# artist. `vendor` here is the store's own name (unlike Arrivals above,
# where `vendor` happens to carry release-specific text).
_HALLOWEEN_KILLS = {
    "title": "Halloween Kills Original Motion Picture Soundtrack LP",
    "vendor": "Anxious and Angry",
    "handle": "halloween-kills-original-motion-picture-soundtrack-lp",
    "tags": ["Record Store", "Soundtrack", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: CD-only product, "Default Title" variant --
# product-level gate must exclude it entirely (the per-variant filter alone
# can't: "Default Title" doesn't match the negative cd/cassette pattern).
_ARRIVALS_MARVELS_CD = {
    "title": 'Arrivals, The "Marvels of Industry" CD',
    "vendor": "Anxious and Angry",
    "handle": "arrivals-the-marvels-of-industry-cd",
    "tags": ["Band Vinyl", "CD", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quoted album, no vinyl signal anywhere --
# gift card must be excluded entirely by the product-level gate.
_GIFT_CARD = {
    "title": "Anxious and Angry Gift Card",
    "vendor": "Anxious and Angry",
    "handle": "anxious-and-angry-gift-card",
    "tags": ["Gift Card", "Record Store"],
    "images": [],
    "variants": [
        {"title": "$10.00", "price": "10.00", "available": True, "featured_image": None},
        {"title": "$25.00", "price": "25.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: dual-format product with separate LP/CD
# variants -- only the LP variant is a genuine format-token exception to
# this store's usual bare-color/Default-Title variant titles.
_COPYRIGHTS_CD_LP = {
    "title": 'Copyrights, The "Alone In A Dome" CD/LP',
    "vendor": "Copyrights",
    "handle": "copyrights-the-alone-in-a-dome-cd-lp",
    "tags": ["Band Vinyl", "CD", "LP", "Record Store", "VINYL"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0046/3142/9190/products/a2570152274_10.jpg"}],
    "variants": [
        {"title": "LP", "price": "25.00", "available": True, "featured_image": None},
        {"title": "CD", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: album title ends in a digit immediately before
# the closing quote ('Vol. 2"'), which would false-positive-match an
# inch-mark regex applied to the *whole* title. Must still be correctly
# excluded as CD-only once the regex is scoped to the post-quote suffix.
_FYP_INCOMPLETE_CRAP = {
    "title": 'F.Y.P "Incomplete Crap Vol. 2" CD',
    "vendor": "Anxious and Angry",
    "handle": "f-y-p-incomplete-crap-vol-2-cd",
    "tags": ["Band Vinyl", "CD", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "12.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "7 Inch" spelled out (not a bare inch mark) must
# still be recognized as vinyl.
_WESTERN_ADDICTION_7INCH = {
    "title": 'Western Addiction "Pines" 7 Inch',
    "vendor": "Western Addiction",
    "handle": "western-addiction-pines-7-inch-1",
    "tags": ["Band Vinyl", "LP", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "Shaped Picture Disc" format suffix, no "LP" or
# inch mark at all -- must still be recognized as vinyl.
_OWTH_PICTURE_DISC = {
    "title": 'Off With Their Heads "I Will Follow You" Shaped Picture Disc',
    "vendor": "Anxious and Angry",
    "handle": "off-with-their-heads-i-will-follow-you-shaped-picture-disc",
    "tags": ["Band Vinyl", "LPs", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: unavailable "Default Title" variant, no
# pre-order tag -- must be skipped, item list empty.
_SAMIAM_BILLY_UNAVAILABLE = {
    "title": 'Samiam "Billy" LP (Color Vinyl)',
    "vendor": "Anxious and Angry",
    "handle": "samiam-billy-lp",
    "tags": ["Band Vinyl", "Record Store", "Samiam", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "28.99", "available": False, "featured_image": None},
    ],
}

# Synthetic (this store's PREORDER tag exists but no vinyl product in the
# sampled catalog currently carries it) -- confirms the carve-out works:
# an unavailable variant is still kept when the product is tagged PREORDER.
_PREORDER_VINYL = {
    "title": 'Some Band "Upcoming Album" LP',
    "vendor": "Anxious and Angry",
    "handle": "some-band-upcoming-album-lp",
    "tags": ["Band Vinyl", "PREORDER", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "22.00", "available": False, "featured_image": None},
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
async def test_crawl_catalog_default_title_variant_not_suffixed(crawler):
    _mock_single_page([_ABSENT_IN_BODY])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Absent In Body"
    assert item["title"] == "Plague God"
    assert item["price"] == 20.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://anxiousandangry.com/products/absent-in-body-plague-god-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/s/files/1/0046/3142/9190/products/4062656-2795494.jpg"


@respx.mock
async def test_crawl_catalog_keeps_multiple_vinyl_color_variants(crawler):
    _mock_single_page([_ARRIVALS_PAYLOAD])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {"Payload — Orange Vinyl", "Payload — Blue Vinyl"}
    assert all(item["artist"] == "Arrivals, The" for item in items)


@respx.mock
async def test_crawl_catalog_no_quotes_falls_back_to_vendor(crawler):
    _mock_single_page([_HALLOWEEN_KILLS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Anxious and Angry"
    assert items[0]["title"] == "Halloween Kills Original Motion Picture Soundtrack LP"


@respx.mock
async def test_crawl_catalog_excludes_cd_only_product(crawler):
    _mock_single_page([_ARRIVALS_MARVELS_CD])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_gift_card(crawler):
    _mock_single_page([_GIFT_CARD])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_dual_format_keeps_lp_drops_cd(crawler):
    _mock_single_page([_COPYRIGHTS_CD_LP])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Alone In A Dome — LP"
    assert items[0]["price"] == 25.00


@respx.mock
async def test_crawl_catalog_digit_before_closing_quote_not_read_as_inch_mark(crawler):
    _mock_single_page([_FYP_INCOMPLETE_CRAP])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_recognizes_spelled_out_inch(crawler):
    _mock_single_page([_WESTERN_ADDICTION_7INCH])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Pines"


@respx.mock
async def test_crawl_catalog_recognizes_picture_disc(crawler):
    _mock_single_page([_OWTH_PICTURE_DISC])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "I Will Follow You"


@respx.mock
async def test_crawl_catalog_skips_unavailable_variant(crawler):
    _mock_single_page([_SAMIAM_BILLY_UNAVAILABLE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_preorder_keeps_unavailable_variant(crawler):
    _mock_single_page([_PREORDER_VINYL])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Upcoming Album (Pre-Order)"
    assert items[0]["price"] == 22.00


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_ABSENT_IN_BODY, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Anxious and Angry"
    assert Crawler.base_url == "https://anxiousandangry.com"
    assert Crawler.genre == "punk"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the tests to verify they fail on import**

Run: `cd backend && pytest tests/test_anxiousandangry_crawler.py -v`
Expected: every test errors with `ModuleNotFoundError: No module named 'crawlers.anxiousandangry'` (the crawler module doesn't exist yet).

- [ ] **Step 3: Write the crawler implementation**

Create `backend/crawlers/anxiousandangry.py`:

```python
import re
from typing import AsyncIterator, Optional
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "record-store"
_PREORDER_TAG = "PREORDER"
# Matches straight or curly quotes on either side independently, and doesn't
# require the closing quote to end the string -- titles like 'Absent In
# Body "Plague God" LP' have trailing format text after it.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s*["“](?P<album>.+?)["”]')
# Applied to the text *after* the closing quote (or the whole title, for the
# minority with no quotes at all) -- never to the whole title including the
# album name. Several album titles end in a digit right before the closing
# quote (F.Y.P "Incomplete Crap Vol. 2" CD), which a whole-title inch-mark
# regex misreads as a real inch mark.
_VINYL_RE = re.compile(r'\bvinyl\b|\blps?\b|\d{1,2}\s*(?:"|\binch\b)|\bpicture disc\b', re.IGNORECASE)
_NON_VINYL_RE = re.compile(r'\bcds?\b|\bcassette\b|\btape\b|\bgift card\b', re.IGNORECASE)
# This store's variant titles are almost always a bare color name or
# "Default Title" -- no format word to match positively. The one live
# exception is 4 products offering separate LP/CD variants, where the
# variant title literally is "LP" or "CD"; this negative filter only ever
# fires on those products' CD variant.
_NON_VINYL_VARIANT_RE = re.compile(r'^(cds?|cassette)$', re.IGNORECASE)


class Crawler:
    site_name: str = "Anxious and Angry"
    base_url: str = "https://anxiousandangry.com"
    genre_summary: str = "Ryan Young (Off With Their Heads)'s punk mailorder record store."
    genre: str = "punk"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album_title, suffix = cls._parse_artist_title(
            product.get("title", ""), product.get("vendor", "")
        )
        is_vinyl = bool(_VINYL_RE.search(suffix))
        is_non_vinyl = bool(_NON_VINYL_RE.search(suffix))
        if is_non_vinyl and not is_vinyl:
            return []

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
        # `vendor` carries the real artist on some releases (American Steel
        # "Rogues March" LP -> vendor "American Steel") but is just the
        # store's own name on others -- unlike deathwishinc.py/
        # no_idea_records.py, it's not uniformly one or the other, so it's
        # only used as the fallback, never overriding a quote match
        # (confirmed live: 124/128 titles match, 96.9%).
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip(), title[m.end():]
        return (vendor or "").strip(), title.strip(), title
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_anxiousandangry_crawler.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && pytest`
Expected: no new failures (this change touches no shared code — `shopify_catalog.py` is read-only here — so nothing outside the new test file should be affected).

- [ ] **Step 6: Confirm automatic plugin registration**

Run:
```bash
cd backend && python3 -c "
from crawlers.anxiousandangry import Crawler
c = Crawler()
print(c.site_name, c.base_url, c.genre, c.crawler_type, c.genre_summary)
"
```
Expected: prints `Anxious and Angry https://anxiousandangry.com punk catalog Ryan Young (Off With Their Heads)'s punk mailorder record store.` — confirms the module is importable and exposes the attributes `main.py`'s startup loop reads (`site_name`, `crawler_type`, `requires_discogs_release` — absent here, which `register_crawler()` treats as `False`, matching every other `catalog` plugin). No change to `main.py` or any router is needed.

- [ ] **Step 7: Commit**

```bash
git add backend/crawlers/anxiousandangry.py backend/tests/test_anxiousandangry_crawler.py
git commit -F <message-file>
```

Message file content (per this repo's required AI-attribution trailers):

```
Add Anxious and Angry store crawler

Shopify catalog crawler covering anxiousandangry.com's record-store
collection. Parses the quoted-album title convention (Artist "Album"),
gates each product on the post-quote format suffix (excluding CD-only,
cassette-only, and gift-card products, since this collection -- unlike
most sibling Shopify crawlers' -- mixes non-vinyl products in), then
filters variants negatively since format usually lives in the product
title, not the variant title. See design spec for full grounding.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
```

---

## Post-implementation: pre-PR spec-drift check

Before opening the PR, per this repo's `CLAUDE.md`: `grep -rl` across both `docs/superpowers/specs/` and `docs/specifications/shaping/` for files, symbols, and section names touched by this change (`anxiousandangry`, `anxious and angry`, `record-store` collection, `shopify_catalog`) to confirm no other spec describes behavior this branch altered. This crawler only adds a new file and reads existing shared helpers unchanged, so drift is not expected, but the check must still run and its result (found/not found) noted in the PR description.
