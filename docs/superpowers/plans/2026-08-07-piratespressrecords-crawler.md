# Pirates Press Records Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a catalog crawler for Pirates Press Records (`shop.piratespressrecords.com`), a new Store-tab catalog source.

**Architecture:** Reuses `backend/shopify_catalog.py`'s `iter_products`/`has_tag`/`resolve_cover_image` helpers via the same `crawl_catalog()` contract every other catalog crawler implements. This store has no single collection holding all of its vinyl, but the storewide `/collections/all` feed does, at 560/560 vinyl listings (a since-abandoned four-collection-plus-dedup draft only reached 496/560, and needed more HTTP requests to do it) — so the crawler is the standard single-slug shape, `_COLLECTION_SLUG = "all"`, the same pattern `riserecords.py`/`saddlecreek.py`/`killrockstars.py`/`triplebrecords.py` already use. Format filtering is a product-level `product_type` check (`"Vinyl LP"` or `"Picture Disc"`); every product has exactly one variant, so no per-variant filter is layered on top. `vendor` is the artist; the display title is derived by splitting the raw title on its own first `" - "` rather than calling the shared `strip_vendor_prefix` (which needs an exact `"{vendor} - "` prefix match that 58/566 titles fail due to case/whitespace drift). That split requires whitespace on at least one side of the hyphen — a plain `\s*-\s*` split incorrectly breaks on two artists whose own name contains an unspaced internal hyphen (`"A-100s"`, `"The Re-Volts"`).

**Tech Stack:** Python 3.9, `httpx` (via `iter_products`), `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`, no decorator needed) + `respx` (HTTP mocking) for tests.

## Global Constraints

- Python ≥3.9 syntax only — no `str | None`, use `Optional[str]` or untyped (repo `CLAUDE.md` Style notes).
- No comments unless the WHY is non-obvious (repo `CLAUDE.md` Style notes).
- Prefer editing existing files; don't create new abstractions without a clear reason (repo `CLAUDE.md` Style notes) — this task creates exactly two new files, no others.
- Dropping the new file in `backend/crawlers/` is sufficient for registration — `main.py:seed_bundled_crawlers()` reads `site_name`/`crawler_type` directly off the loaded `Crawler` class on next app startup. No data-model, API, or frontend change.
- Every commit needs the AI-attribution trailer block, created via `git commit -F <file>` (never `-m`) — see repo `CLAUDE.md` "Commits" section. Prefer the `upside-sdlc:commit` skill's packaged helper when available.
- **Documentation impact:** the design is already recorded as the 2026-08-07 amendment to `docs/superpowers/specs/2026-07-05-in-stock-crawler-design.md` (including a correction, made before any implementation shipped, from an initial four-collection-plus-dedup draft to the single-slug `/collections/all` design below), and the new worktree-isolation rule is already in this repo's `CLAUDE.md` — both done prior to this plan. This repo has no `.agents/INPUTS.md`, `.agents/OUTPUTS.md`, or `.agents/INSTRUCTIONS.md`; it uses `CLAUDE.md` plus `docs/superpowers/specs`/`plans` instead, and both are already current. Root `README.md` describes catalog crawlers generically, with no per-site enumeration, so it needs no change for this task.
- All work happens in the existing worktree at `.claude/worktrees/piratespressrecords-crawler` (branch `worktree-piratespressrecords-crawler`), per the new `CLAUDE.md` rule. A venv already exists at `backend/.venv` with `pip install -e ".[dev]"` already run — use `backend/.venv/bin/python` for every command below, not the bare `python3`/`pip` (which hit an externally-managed-environment error in this sandbox).
- **Supersedes a prior implementation.** A first version of this crawler (four collection slugs + cross-collection dedup, and a title-split regex that didn't require whitespace around the hyphen) was already implemented, tested, task-reviewed (approved), and committed (`51007e5`) — then a whole-branch review found live-data problems with both the slug choice (misses 64 real releases) and the regex (mangles hyphenated artist names). This plan's Task 1 replaces that implementation outright with the corrected design below; it is not an incremental patch on top of it.

---

### Task 1: Pirates Press Records catalog crawler

**Files:**
- Modify (full rewrite): `backend/crawlers/piratespressrecords.py`
- Modify (full rewrite): `backend/tests/test_piratespressrecords_crawler.py`

**Interfaces:**
- Consumes (pre-existing, unchanged): `shopify_catalog.iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]`; `shopify_catalog.has_tag(product: dict, tag: str) -> bool`; `shopify_catalog.resolve_cover_image(product: dict, variant: dict) -> Optional[str]`.
- Produces: `Crawler.crawl_catalog(self) -> AsyncIterator[dict]`, yielding the standard catalog-crawler item shape every other crawler yields — `{"artist": str, "title": str, "format": "Vinyl", "price": Optional[float], "currency": "USD", "url": str, "cover_image_url": Optional[str]}` — consumed unmodified by `CrawlManager._sync_stock` (`backend/crawl_manager.py`).

- [ ] **Step 1: Replace the test file (still red — the current implementation predates the fix)**

Replace the full contents of `backend/tests/test_piratespressrecords_crawler.py` with:

```python
import httpx
import respx
import pytest
from crawlers.piratespressrecords import Crawler

_BASE = "https://shop.piratespressrecords.com"
_URL = f"{_BASE}/collections/all/products.json"

_PRODUCT = {
    "id": 9299152470294,
    "title": "45 Adapters - Unstoppable - Black - Vinyl LP",
    "vendor": "45 Adapters",
    "handle": "45adp391bl-lp",
    "product_type": "Vinyl LP",
    "tags": ["45 Adapters", "Music", "Vinyl LP"],
    "images": [{"src": "https://cdn.shopify.com/45adp-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "19.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_products(products):
    """Mock the /collections/all endpoint. An empty page 1 means no page-2
    request happens, matching how iter_products stops on the first empty
    page."""
    respx.get(_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
    if products:
        respx.get(_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_vinyl_lp_product(crawler):
    _mock_products([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "45 Adapters"
    assert item["title"] == "Unstoppable - Black - Vinyl LP"
    assert item["format"] == "Vinyl"
    assert item["price"] == 19.99
    assert item["currency"] == "USD"
    assert item["url"] == f"{_BASE}/products/45adp391bl-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/45adp-fallback.jpg"


@respx.mock
async def test_crawl_catalog_includes_picture_disc_product_type(crawler):
    product = {**_PRODUCT, "product_type": "Picture Disc"}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_crawl_catalog_excludes_non_vinyl_product_type(crawler):
    # /collections/all mixes in merch/CD/Cassette (35 distinct non-vinyl
    # product_type values confirmed live) alongside the 566 vinyl products —
    # this asserts the allowlist filter rejects them.
    product = {**_PRODUCT, "product_type": "T-Shirt"}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    product = {
        **_PRODUCT,
        "tags": ["Music", "preorder"],
        "variants": [{"title": "Default Title", "price": "21.99", "available": False}],
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Unstoppable - Black - Vinyl LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{"title": "Default Title", "price": "21.99", "available": False}]}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_splits_title_on_first_dash_even_when_vendor_mismatches_case(crawler):
    # vendor "Crim" doesn't exact-prefix-match title "CRIM - ..." (case drift,
    # confirmed live on 58/566 titles) — strip_vendor_prefix would no-op here,
    # leaving "CRIM - ..." in the display title. The local dash-split doesn't
    # care what vendor says.
    product = {
        **_PRODUCT,
        "title": "CRIM - Blau Sang, Vermell Cel Black Vinyl LP",
        "vendor": "Crim",
        "handle": "crimp170bl-lp",
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Crim"
    assert items[0]["title"] == "Blau Sang, Vermell Cel Black Vinyl LP"


@respx.mock
async def test_crawl_catalog_keeps_hyphenated_artist_name_intact(crawler):
    # "The Re-Volts" has an unspaced internal hyphen — confirmed live that a
    # naive \s*-\s* split (the original implementation) breaks on it, clipping
    # to "Volts". The real separator " - " later in the title has whitespace
    # on both sides; the hyphen inside "Re-Volts" has none on either side.
    product = {
        **_PRODUCT,
        "title": 'The Re-Volts - Wages Orange Vinyl 7"',
        "vendor": "The Re-Volts",
        "handle": "revop104or-45",
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "The Re-Volts"
    assert items[0]["title"] == 'Wages Orange Vinyl 7"'


@respx.mock
async def test_crawl_catalog_falls_back_to_full_title_when_no_dash_separator(crawler):
    # Confirmed live: 2/566 titles have no " - " at all. Accepted miss, same
    # tradeoff as Deathwish Inc's quote-matching residual misses.
    product = {
        **_PRODUCT,
        "title": "The Barstool Preachers Blatant Propaganda Black Vinyl LP",
        "vendor": "The Bar Stool Preachers",
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "The Bar Stool Preachers"
    assert items[0]["title"] == "The Barstool Preachers Blatant Propaganda Black Vinyl LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Pirates Press Records"
    assert Crawler.base_url == "https://shop.piratespressrecords.com"
    assert Crawler.crawler_type == "catalog"
```

- [ ] **Step 2: Run and confirm it fails against the old implementation**

Run (from `backend/`): `.venv/bin/python -m pytest tests/test_piratespressrecords_crawler.py -v`
Expected: several failures — at minimum `test_crawl_catalog_keeps_hyphenated_artist_name_intact` (the old regex clips "The Re-Volts" to "Volts"), and any test whose URL/mock no longer matches the old four-slug crawler's requests (the old crawler will make requests this test file no longer mocks, which `respx` treats as an error rather than a silent pass).

- [ ] **Step 3: Replace the implementation**

Replace the full contents of `backend/crawlers/piratespressrecords.py` with:

```python
import re
from typing import AsyncIterator
from shopify_catalog import iter_products, has_tag, resolve_cover_image

_COLLECTION_SLUG = "all"
_PREORDER_TAG = "preorder"
_VINYL_PRODUCT_TYPES = {"Vinyl LP", "Picture Disc"}
# vendor is reliably the artist here, but doesn't always exact-prefix-match the
# title (case/whitespace drift confirmed on 58/566 titles live, e.g. vendor
# "Crim" vs. title "CRIM - ..."), so strip_vendor_prefix would miss those.
# Splitting the title on its own first " - " works regardless of vendor's
# casing -- but the hyphen must have whitespace on at least one side, not just
# any hyphen: two artists on this store ("A-100s", "The Re-Volts") have an
# internal hyphen with no surrounding space, and a plain \s*-\s* split breaks
# on both (confirmed live against all 566 vinyl titles).
_TITLE_RE = re.compile(r'^.+?(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')


class Crawler:
    site_name: str = "Pirates Press Records"
    base_url: str = "https://shop.piratespressrecords.com"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list:
        if product.get("product_type") not in _VINYL_PRODUCT_TYPES:
            return []

        artist = (product.get("vendor") or "").strip()
        title = cls._display_title(product.get("title", ""))
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
    def _display_title(title: str) -> str:
        m = _TITLE_RE.match(title)
        return m.group("album") if m else title
```

- [ ] **Step 4: Run and confirm all tests pass**

Run: `.venv/bin/python -m pytest tests/test_piratespressrecords_crawler.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the full backend test suite to confirm no regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: the same baseline already established for this worktree — 401 passed, 4 failed, 225 errored, all of the failures/errors tracing to no Postgres/Playwright-browser available in this sandbox (unrelated to this change) — plus the 10 tests in this file now passing (411 passed total; this replaces, not adds to, the 10 tests the previous implementation already had passing). If any *other* previously-passing test now fails, stop and investigate before continuing.

- [ ] **Step 6: Commit**

```bash
git add backend/crawlers/piratespressrecords.py backend/tests/test_piratespressrecords_crawler.py
git commit -F <message-file-with-AI-attribution-trailers>
```

Write a commit message describing this as replacing the four-collection/dedup design with the single-slug `/collections/all` design and fixing the hyphenated-artist-name regex bug — not as an incremental tweak. Per repo `CLAUDE.md`: use `git commit -F <file>`, not `-m`, and include the full AI-attribution trailer block. Prefer the `upside-sdlc:commit` skill's packaged helper (`commit-with-cleanup.sh`) when available.

---

## Testing Strategy

- Unit-only, `respx`-mocked HTTP — no live network calls, no Postgres, no Playwright (this crawler is pure `httpx`, no bot-detection involved).
- Each of the following is its own test, mirroring the coverage shape of `test_riserecords_crawler.py`/`test_equalvision_crawler.py`: product_type inclusion (`Picture Disc`) and exclusion, preorder inclusion/exclusion, vendor-mismatched title-splitting, hyphenated-artist-name title-splitting (the bug this task fixes), no-hyphen title fallback, and a defensive null-variants case.

## Out of scope for this plan

- Live integration testing against the real store — manual, per repo `CLAUDE.md`'s existing convention for non-Playwright-dependent crawl paths.
- Any change to `shopify_catalog.py`, `main.py`, the data model, the API, or the frontend — none needed; confirmed in the spec's 2026-08-07 amendment.
- Fixing the same whitespace-unaware title-split regex (`^(?P<artist>.+?)\s*-\s*(?P<album>.+)$`) in `backend/crawlers/seasonofmist.py`, `fatherdaughterrecords.py`, `closedcasketactivities.py`, and `triplebrecords.py` — confirmed byte-identical in all four. Worse there than the bug fixed here: those four assign the split's `artist` group directly to the output, so a hyphenated band name gets clipped in the artist field too, not just the title. Flagged as a follow-up, not fixed here — this task only touches the two Pirates Press Records files.
