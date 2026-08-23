# Real Gone Music Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: this repo's `CLAUDE.md` mandates `superpowers:subagent-driven-development` for every written implementation plan by default — do not offer `superpowers:executing-plans` as an equal alternative; only fall back to it if the user explicitly asks for inline/in-session execution instead. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/realgonemusic.py`, a `crawler_type="catalog"` Shopify plugin covering Real Gone Music's (`realgonemusic.com`) `vinyl` collection.

**Architecture:** Iterate the store's `vinyl` collection via the existing `shopify_catalog.iter_products()` helper. Set `artist` to `vendor` unconditionally — this store exposes no artist field anywhere and its titles have no artist/album delimiter, so the label name is used as an explicit accepted gap on `numerogroup.py`'s precedent, with the full product title preserved. Filter *variants* on two gates only: availability, then a `\bbundle\b` drop for multi-item packs. Apply **no** format filter — the collection tag already gates format and every live variant title is a colour/edition name. Fan out one stock item per surviving variant, titled with the variant descriptor unless that descriptor is a Shopify placeholder or repeats the product title.

**Tech Stack:** Python ≥3.9, `httpx` (via `shopify_catalog.iter_products`), `pytest`/`pytest-asyncio`, `respx` for HTTP mocking.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`; use `Optional[str]` or leave untyped. (`list[dict]` is fine — PEP 585 generics work from 3.9, and `carparkrecords.py` already uses them.)
- No new shared module — reuse `shopify_catalog.iter_products()` and `resolve_cover_image()` unchanged (per design spec's "Scope"). Note this crawler does **not** use `has_tag()` or `strip_vendor_prefix()`, unlike most siblings: there is no pre-order carve-out and no vendor prefix to strip.
- `format` is hardcoded `"Vinyl"` on every yielded item; `currency` is hardcoded `"USD"`.
- `_COLLECTION_SLUG = "vinyl"`. Do not switch to `all` or add a second collection — the design spec's "Collection choice" section records that `rarities`/`halloween`/`upcoming`/`new-releases`/`real-gone-collectibles` are cross-cuts already present as tags on `vinyl` products.
- **`artist` is `product["vendor"]` verbatim, always.** Never attempt to split the product title. This is the spec's central decision, not an oversight — see its "Artist attribution" section for the three rejected alternatives. A reviewer who "fixes" this reintroduces confidently-wrong attributions.
- **No per-variant format filter, positive or negative.** This is a deliberate departure from every sibling Shopify crawler. All 279 live variant titles are colour/edition names; a positive vinyl regex would drop the large majority of real stock.
- **No pre-order handling.** The `Upcoming` tag is a real pre-order signal and is deliberately unused — see the spec's "Pre-orders (not implemented)" section. Do not add a ` (Pre-Order)` suffix and do not bypass the availability gate.
- No comments except where the WHY is non-obvious (a hidden constraint, a confirmed-live edge case) — no comments describing WHAT the code does.
- Registration is automatic via `main.py`'s `seed_bundled_crawlers()`, which walks `backend/crawlers/` at startup and calls `register_crawler` — no wiring changes anywhere else.
- Every commit carries the AI-attribution trailer block required by this repo's `CLAUDE.md` (`ai-generated: true`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <message-file>`, not `-m`.

Full grounding for every rule above: [`docs/specifications/shaping/2026-08-23-realgonemusic-crawler-design.md`](../shaping/2026-08-23-realgonemusic-crawler-design.md).

**Running the tests.** These tests need no database — they mock HTTP with `respx` and never touch Postgres. Run from `backend/`:

```bash
cd backend && pytest tests/test_realgonemusic_crawler.py -v
```

---

### Task 1: Real Gone Music crawler + tests

**Files:**
- Create: `backend/crawlers/realgonemusic.py`
- Test: `backend/tests/test_realgonemusic_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url, collection_slug) -> AsyncIterator[dict]` and `shopify_catalog.resolve_cover_image(product, variant) -> Optional[str]` — both exist unchanged in `backend/shopify_catalog.py`.
- Produces: a `Crawler` class with `site_name: str`, `base_url: str`, `genre_summary: str`, `genre: str`, `crawler_type: str = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}`. This is the standard `catalog` plugin interface `main.py`'s startup loop discovers. Two private helpers are also part of this task's surface because the tests call one directly: `Crawler._items(product) -> list[dict]` and `Crawler._compose_title(product_title, variant_title) -> str`.

- [ ] **Step 1: Write the failing test file**

Every product literal below is real, confirmed-live data captured on 2026-08-23, except where a comment says otherwise. Create `backend/tests/test_realgonemusic_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.realgonemusic import Crawler

_PRODUCTS_URL = "https://realgonemusic.com/collections/vinyl/products.json"

# Real confirmed-live case, and the anchor for this store's central quirk:
# the artist ("3 Inches of Blood") is plainly present in the product title
# but there is no delimiter separating it from the album, and `vendor` is
# the label. Its only variant is unavailable, so this product yields
# nothing -- it exists here to pin the parse via _items() directly.
_THREE_INCHES_OF_BLOOD = {
    "title": "3 Inches of Blood Advance and Vanquish LP",
    "vendor": "Real Gone Music",
    "handle": "3-inches-of-blood-advance-and-vanquish-lp",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064017554_packshot.jpg?v=1721758507"},
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064017554_vinyl.jpg?v=1721758507"},
    ],
    "variants": [
        {
            "title": "Orange & Black",
            "price": "31.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064017554_packshot.jpg?v=1721758507"},
        },
    ],
}

# Real confirmed-live case: two in-stock colour variants alongside an
# in-stock multi-item bundle ($97.99 against a $23.99 single LP) and a
# sold-out Wax Mage. Exercises the bundle drop, the availability gate, and
# per-variant cover images all in one product.
_BARBARA_LEWIS = {
    "title": "Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl",
    "vendor": "Real Gone Music",
    "handle": "barbara-lewis-the-many-grooves-of-barbara-lewis-all-analog-vinyl",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020554_packshot.jpg?v=1771100316"},
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020394_packshot.jpg?v=1771100316"},
    ],
    "variants": [
        {
            "title": "Black Vinyl",
            "price": "23.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020394_packshot.jpg?v=1771100316"},
        },
        {
            "title": "Purple PET Vinyl",
            "price": "23.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020554_packshot.jpg?v=1771100316"},
        },
        {
            "title": "Barbara Lewis Bundle",
            "price": "97.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/BarbaraLewisBundle.jpg?v=1771100316"},
        },
        {
            "title": "Wax Mage",
            "price": "74.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/Barbara_Lewis_Wax_Mage_3.jpg?v=1771961153"},
        },
    ],
}

# Real confirmed-live case: the product's only in-stock variant is a
# bundle, so the bundle drop removes the product from the catalog
# entirely. One of exactly 3 such products live -- the accepted cost the
# design spec records for gate 2.
_CANDIDO = {
    "title": "Candido Dancin' and Prancin' LP",
    "vendor": "Real Gone Music",
    "handle": "candido-dancin-and-prancin-lp",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018117_packshot.jpg?v=1724193558"},
    ],
    "variants": [
        {
            "title": "Black",
            "price": "21.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018117_packshot.jpg?v=1724193558"},
        },
        {
            "title": "Candido Bundle",
            "price": "69.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/candidobundle.jpg?v=1720480957"},
        },
    ],
}

# Real confirmed-live case: Shopify's "Default Title" placeholder, and a
# variant with no featured_image so cover art must fall back to the
# product's first image.
_BOB_FRANK_TEST_PRESSING = {
    "title": "Bob Frank Broke Again Test Pressing",
    "vendor": "Real Gone Music",
    "handle": "bob-frank-broke-again-test-pressing",
    "tags": ["Real Gone Collectibles", "Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/image.png?v=1733861180"},
    ],
    "variants": [
        {"title": "Default Title", "price": "50.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: the OTHER placeholder spelling. 6 live products
# use "Default Title" and 4 use bare "Default", so the sibling crawlers'
# `== "Default Title"` check would miss these 4.
_BILL_LOOSE_TEST_PRESSING = {
    "title": "Bill Loose Cherry, Harry & Raquel Test Pressing",
    "vendor": "Real Gone Music",
    "handle": "bill-loose-cherry-harry-raquel-test-pressing",
    "tags": ["Real Gone Collectibles", "Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/products/1007e3d53e50772fa3a49d3c2efd7099_5cec5a5c-be5a-46c9-b161-abea77ef9b89.jpg?v=1645724959"},
    ],
    "variants": [
        {"title": "Default", "price": "35.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live product, with ONE field changed: live, this variant
# is "available": False, and it is flipped to True here. It is the only
# live product whose variant title repeats its product title verbatim, so
# it is the only realistic literal for the equality collapse -- but with
# the live availability it would be dropped by gate 1 and never reach
# _compose_title. The design spec records this collapse as defensive
# rather than live-exercised for exactly this reason.
_BUCKCHERRY = {
    "title": "Buckcherry 15 (2-LP Set)",
    "vendor": "Real Gone Music",
    "handle": "buckcherry-15-2-lp-set",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018858_mockup.jpg?v=1731023527"},
    ],
    "variants": [
        {
            "title": "Buckcherry 15 (2-LP Set)",
            "price": "44.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018858_mockup.jpg?v=1731023527"},
        },
    ],
}

# Real confirmed-live case: "Upcoming"-tagged (a genuine pre-order -- its
# product page carries a STREET DATE banner), with one in-stock variant
# whose title carries no vinyl/LP keyword at all and one sold-out variant.
# Pins BOTH deliberate departures at once: the keyword-less variant
# survives (no format filter) and the sold-out one does not (no pre-order
# bypass), with no " (Pre-Order)" suffix anywhere.
_THE_DONNAS = {
    "title": "The Donnas The Donnas (All-Analog) Vinyl",
    "vendor": "Real Gone Music",
    "handle": "the-donnas-the-donnas-lp",
    "tags": ["Upcoming", "Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064022015_mockup.jpg?v=1783551977"},
    ],
    "variants": [
        {
            "title": "Clear with Black & Purple “Inksplosion” PET Plastic",
            "price": "24.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064022015_mockup.jpg?v=1783551977"},
        },
        {
            "title": "Wax Mage Vinyl",
            "price": "89.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/donnas_st_wm3.jpg?v=1787077612"},
        },
    ],
}

# Synthetic: no live product has a malformed price. Shopify serves prices
# as decimal strings, so this pins the float() guard against a schema
# change rather than a case seen in the wild.
_SYNTHETIC_BAD_PRICE = {
    "title": "Synthetic Artist Synthetic Album LP",
    "vendor": "Real Gone Music",
    "handle": "synthetic-artist-synthetic-album-lp",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "Black", "price": None, "available": True, "featured_image": None},
        {"title": "Green", "available": True, "featured_image": None},
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


def test_artist_is_always_the_vendor_never_parsed_from_the_title():
    # The central accepted gap: "3 Inches of Blood" is right there in the
    # title, and the crawler must still report the label. See the design
    # spec's "Artist attribution" section before changing this.
    items = Crawler._items(_BARBARA_LEWIS)
    assert [item["artist"] for item in items] == ["Real Gone Music", "Real Gone Music"]
    # Asserted via _items() because this product's only variant is
    # unavailable, so crawl_catalog() yields nothing for it.
    assert Crawler._items({**_THREE_INCHES_OF_BLOOD, "variants": [
        {"title": "Orange & Black", "price": "31.99", "available": True, "featured_image": None},
    ]})[0]["artist"] == "Real Gone Music"


@respx.mock
async def test_crawl_catalog_drops_bundle_keeps_colour_variants(crawler):
    _mock_single_page([_BARBARA_LEWIS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert {item["title"] for item in items} == {
        "Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl — Black Vinyl",
        "Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl — Purple PET Vinyl",
    }
    assert all(item["format"] == "Vinyl" for item in items)
    assert all(item["currency"] == "USD" for item in items)
    assert all(item["price"] == 23.99 for item in items)
    assert all(
        item["url"]
        == "https://realgonemusic.com/products/barbara-lewis-the-many-grooves-of-barbara-lewis-all-analog-vinyl"
        for item in items
    )


@respx.mock
async def test_crawl_catalog_prefers_variant_image_over_product_image(crawler):
    _mock_single_page([_BARBARA_LEWIS])
    items = [item async for item in crawler.crawl_catalog()]
    by_title = {item["title"]: item for item in items}
    black = by_title["Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl — Black Vinyl"]
    assert black["cover_image_url"] == (
        "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020394_packshot.jpg?v=1771100316"
    )


@respx.mock
async def test_crawl_catalog_falls_back_to_product_image(crawler):
    _mock_single_page([_BOB_FRANK_TEST_PRESSING])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["cover_image_url"] == (
        "https://cdn.shopify.com/s/files/1/0810/5567/files/image.png?v=1733861180"
    )


@respx.mock
async def test_crawl_catalog_bundle_only_product_yields_nothing(crawler):
    _mock_single_page([_CANDIDO])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_default_title_variant_not_suffixed(crawler):
    _mock_single_page([_BOB_FRANK_TEST_PRESSING])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Bob Frank Broke Again Test Pressing"


@respx.mock
async def test_crawl_catalog_bare_default_variant_not_suffixed(crawler):
    _mock_single_page([_BILL_LOOSE_TEST_PRESSING])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Bill Loose Cherry, Harry & Raquel Test Pressing"


@respx.mock
async def test_crawl_catalog_variant_title_matching_product_title_not_doubled(crawler):
    _mock_single_page([_BUCKCHERRY])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Buckcherry 15 (2-LP Set)"


@respx.mock
async def test_crawl_catalog_no_format_filter_and_no_preorder_handling(crawler):
    _mock_single_page([_THE_DONNAS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == (
        "The Donnas The Donnas (All-Analog) Vinyl — Clear with Black & Purple “Inksplosion” PET Plastic"
    )
    assert "(Pre-Order)" not in items[0]["title"]


@respx.mock
async def test_crawl_catalog_malformed_price_becomes_none(crawler):
    _mock_single_page([_SYNTHETIC_BAD_PRICE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert all(item["price"] is None for item in items)


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    _mock_single_page([{**_BARBARA_LEWIS, "variants": None}])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Real Gone Music"
    assert Crawler.base_url == "https://realgonemusic.com"
    assert Crawler.genre == "marketplace"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && pytest tests/test_realgonemusic_crawler.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'crawlers.realgonemusic'`. Every test in the file errors out; none pass.

- [ ] **Step 3: Write the crawler**

Create `backend/crawlers/realgonemusic.py`:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"
# 49 of this collection's variants are multi-item packs priced at 2-6x a
# single LP (the dearest available one is "Kinky Boots Bundle" at $174.99
# against a ~$25-30 single LP). Surfacing one as a listing for a single
# release would inflate the price column and duplicate the product.
# Confirmed live: 3 products have a bundle as their only in-stock variant
# and so drop out of the catalog entirely -- accepted, since keeping them
# would mean carrying a bundle price against a single release.
_BUNDLE_RE = re.compile(r'\bbundle\b', re.IGNORECASE)
# Shopify's placeholder for a product with no real options. Two spellings
# are live here, not one -- 6 products use "Default Title" and 4 use bare
# "Default" -- so the sibling crawlers' `== "Default Title"` check would
# miss 4 of them.
_DEFAULT_VARIANT_TITLES = {"default", "default title"}


class Crawler:
    site_name: str = "Real Gone Music"
    base_url: str = "https://realgonemusic.com"
    genre_summary: str = "Los Angeles reissue label — Black Jazz jazz reissues, '90s alt-rock, death metal, and film soundtracks."
    genre: str = "marketplace"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        # There is no artist source on this site. `vendor` is the literal
        # "Real Gone Music" on all 278 vinyl products, and titles
        # concatenate artist and album with no delimiter at all ("Deicide
        # Serpents of the Light (Remastered) Vinyl"). Confirmed live that
        # products.json, /products/{handle}.js, the page's JSON-LD, and
        # its meta tags all carry either the label or the same undelimited
        # title. Used directly as a known, accepted gap -- the same call
        # numerogroup.py makes, for the same reason. Consequence:
        # db.py's _library_match_fragment requires exact artist equality,
        # so no row from this store will ever match a user's collection or
        # wantlist. Splitting the title instead was considered and
        # rejected three ways; see the design spec before changing this.
        artist = (product.get("vendor") or "").strip()
        product_title = (product.get("title") or "").strip()
        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"

        # No format filter, deliberately: the `vinyl` collection tag
        # already gates format at the product level, and all 279 live
        # variant titles are colour/edition names ("Wax Mage", "Blue-Green
        # 'Ocean Spray'"), so a positive vinyl regex -- the sibling
        # convention -- would drop the large majority of real stock.
        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            variant_title = (variant.get("title") or "").strip()
            if _BUNDLE_RE.search(variant_title):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            items.append({
                "artist": artist,
                "title": cls._compose_title(product_title, variant_title),
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _compose_title(product_title: str, variant_title: str) -> str:
        # The equality arm is defensive, not live: "Buckcherry 15 (2-LP
        # Set)" is the one product whose variant title repeats its product
        # title, and that variant is currently sold out, so this branch
        # emits nothing today. Kept because it costs one comparison and
        # the product can restock at any time.
        if (
            not variant_title
            or variant_title.lower() in _DEFAULT_VARIANT_TITLES
            or variant_title == product_title
        ):
            return product_title
        return f"{product_title} — {variant_title}"
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && pytest tests/test_realgonemusic_crawler.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Run the full crawler test suite for regressions**

The plugin loader imports every module in `backend/crawlers/`, so a syntax or import error in the new file breaks unrelated tests. This command needs no database:

```bash
cd backend && pytest tests/ -k crawler -v
```

Expected: all pass, with the 12 new ones among them. If anything unrelated fails, stop and investigate before committing — do not commit over a pre-existing red suite without saying so.

- [ ] **Step 6: Commit**

Write the message to a file first — trailers are easy to drop through shell quoting with `-m`, and this repo requires them on every commit:

```bash
cat > /tmp/realgone-commit.txt <<'MSG'
Add Real Gone Music store crawler

LA reissue label on Shopify; vinyl collection over plain httpx, no bot
gate. products.json is named in the site's own /agents.md read-only
section and is not covered by any robots.txt Disallow.

artist is set to vendor ("Real Gone Music") unconditionally. This store
exposes no artist field in products.json, the .js payload, its JSON-LD, or
its meta tags, and its titles concatenate artist and album with no
delimiter, so there is nothing to split on. Same accepted gap
numerogroup.py documents, with the same consequence: _library_match_frag-
ment needs exact artist equality, so these rows never match a user's
library. Colon-splitting, prefix clustering, and Discogs UPC lookup were
each considered and rejected -- see the design spec.

Two deliberate departures from the sibling Shopify crawlers, both pinned
by tests: no per-variant format filter (all 279 live variant titles are
colour/edition names; the collection tag already gates format), and no
pre-order handling (the Upcoming tag is a real signal, but the sibling
availability-bypass half would only admit sold-out variants here).

Bundle variants are dropped -- 49 live, multi-item packs up to $174.99
against a ~$25-30 single LP. Costs 3 products whose only in-stock variant
is a bundle.

278 products -> 202 with surviving variants -> 268 item rows.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
MSG
git add backend/crawlers/realgonemusic.py backend/tests/test_realgonemusic_crawler.py
git commit -F /tmp/realgone-commit.txt
```

---

## Post-implementation: pre-PR spec-drift check

Required by this repo's `CLAUDE.md` on every branch, including branches whose own change has no drift of its own. This is not optional and not covered by Task 1.

- [ ] **Step 1: Grep both spec trees for anything this diff touches**

Specs live in two trees and grepping only one under-finds drift:

```bash
grep -rl "numerogroup\|shopify_catalog\|iter_products\|resolve_cover_image\|marketplace\|_library_match_fragment" docs/superpowers/specs/ docs/specifications/shaping/
```

- [ ] **Step 2: Check each match against what actually shipped**

One is known to need attention before the PR opens:

1. `docs/specifications/shaping/2026-08-07-shared-title-split-helper-design.md` — this doc tracks every crawler that diverges from the proposed shared `split_artist_title()` contract, and carries three dated amendments naming `cleorecs.py`, `jackpotrecords.py`, and `asianmanrecords.py` as exceptions. `realgonemusic.py` is a further exception, and a stronger one than any prior: it does not split titles *at all*. Add the next amendment in sequence recording that, dated 2026-08-23, branch `claude/realgonemusic-crawler-84feaf`. Count the existing amendments before labelling it — as of 2026-08-23 there are four (the fourth, for `carparkrecords.py`, landed 2026-08-19), so the new one is the **fifth**.

(This crawler's own design spec had a second drift — its "Testing" section described direct `_items()` calls — but that was corrected in commit `a060093` before implementation began, so it needs no action here.)

Confirm the rest of the matches still describe reality; amend any that do not, with a short note or inline correction rather than a rewrite.

- [ ] **Step 3: Commit the spec amendments separately**

Spec drift gets its own commit on this branch, pushed before the PR opens — a PR must not merge with known drift.

```bash
cat > /tmp/realgone-spec-commit.txt <<'MSG'
Amend specs for Real Gone Music crawler

Fourth amendment to the shared-title-split-helper design: realgonemusic.py
is a fourth documented exception to the converging split_artist_title()
contract, and the strongest one -- it performs no title split at all,
because the store has no artist/album delimiter to split on.

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-opus-5
ai-tool: claude-code
ai-surface: claude-code-desktop
ai-executor: local-agent
MSG
git add docs/specifications/shaping/
git commit -F /tmp/realgone-spec-commit.txt
```

- [ ] **Step 4: Open the PR**

Ready for review, never a draft. Note in the description what drift was found and fixed. Then follow `CLAUDE.md`'s Copilot-review polling protocol: capture the head SHA after the final push, poll `gh api repos/{owner}/{repo}/pulls/{number}/reviews --paginate` every 20-30s until a `copilot-pull-request-reviewer[bot]` review whose `commit_id` matches that SHA appears, capped at ~5-6 minutes, then fetch inline comments with `gh api repos/{owner}/{repo}/pulls/{number}/comments --paginate`. If it times out, say so explicitly rather than treating "no review yet" as "no feedback". Never enable auto-merge.
