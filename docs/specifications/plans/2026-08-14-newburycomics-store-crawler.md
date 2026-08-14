# Newbury Comics Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backend/crawlers/newburycomics.py`, a `crawler_type="catalog"` plugin that crawls Newbury Comics's `/collections/vinyl` Shopify collection and yields one item per available, single-variant vinyl product.

**Architecture:** A thin plugin on top of the existing `shopify_catalog.iter_products()` helper (handles pagination, pacing, retry, and fail-fast-on-429 already). The plugin's own logic is a pure `_item()` classmethod: map `vendor` → `artist` and `title` → `title` directly (no regex/parsing), skip unavailable variants, hardcode `format="Vinyl"`.

**Tech Stack:** Python 3.9 (backend), `httpx` (via `shopify_catalog.py`, already a dependency), `pytest`/`pytest-asyncio` + `respx` for tests (already used by every sibling Shopify crawler test).

## Global Constraints

- Python ≥3.9 — no `str | None` syntax; use `Optional[str]` (from `backend/CLAUDE.md` style notes, also enforced repo-wide).
- No comments unless the WHY is non-obvious.
- No backwards-compat shims.
- Crawler plugin interface (from `backend/CLAUDE.md`): a `catalog` crawler exposes `site_name: str`, `base_url: str`, `crawler_type: str = "catalog"`, and `async def crawl_catalog(self) -> AsyncIterator[dict]` yielding dicts with keys `artist`, `title`, `format`, `price`, `currency`, `url`, `cover_image_url`.
- Design spec: `docs/specifications/shaping/2026-08-14-newburycomics-store-crawler-design.md` — collection slug `"vinyl"`, `vendor` used directly as artist, `title` used as-is, single-variant products only, availability-only gate (no pre-order tag exists on this store).
- Every commit must carry the AI-attribution trailer block described in `CLAUDE.md`'s "Commits — AI attribution trailers" section, created via `git commit -F <message-file>`, not `git commit -m`.

---

### Task 1: Crawler plugin with TDD test suite

**Files:**
- Create: `backend/crawlers/newburycomics.py`
- Create: `backend/tests/test_newburycomics_crawler.py`

**Interfaces:**
- Consumes: `shopify_catalog.iter_products(base_url: str, collection_slug: str) -> AsyncIterator[dict]`, `shopify_catalog.resolve_cover_image(product: dict, variant: dict) -> Optional[str]` — both already defined in `backend/shopify_catalog.py`, used unchanged.
- Produces: `newburycomics.Crawler` with `site_name`, `base_url`, `genre_summary`, `crawler_type = "catalog"` class attributes and `async def crawl_catalog(self) -> AsyncIterator[dict]`. Consumed by `main.py`'s startup loop via `register_crawler()` — no other task depends on `Crawler`'s internals.

This task is TDD end-to-end: the test file is written first, run to confirm it fails (module doesn't exist yet), then the plugin is implemented to make it pass. Each step below is one red/green cycle building up the full test file and full plugin together — write the step's test code into the test file, then the corresponding implementation code into the plugin file, per the step instructions.

- [ ] **Step 1: Write the test file with the first case (artist/title mapped directly, unavailable variant skipped)**

Create `backend/tests/test_newburycomics_crawler.py`:

```python
import httpx
import respx
import pytest
from crawlers.newburycomics import Crawler

_PRODUCTS_URL = "https://www.newburycomics.com/collections/vinyl/products.json"

# Real confirmed-live product shape: vendor is the artist directly, title is
# the bare album title with no artist prefix, exactly one "Default Title"
# variant.
_PRODUCT = {
    "title": "#1 Record LP (180g)",
    "vendor": "Big Star",
    "handle": "big_star-number_1_record_lp_180g",
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/big-star-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "37.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_maps_vendor_to_artist_and_title_unchanged(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Big Star"
    assert items[0]["title"] == "#1 Record LP (180g)"
    assert items[0]["price"] == 37.99
    assert items[0]["format"] == "Vinyl"
    assert items[0]["currency"] == "USD"
    assert items[0]["url"] == "https://www.newburycomics.com/products/big_star-number_1_record_lp_180g"
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/big-star-fallback.jpg"


@respx.mock
async def test_crawl_catalog_skips_unavailable_variant(crawler):
    product = {**_PRODUCT, "variants": [{"title": "Default Title", "price": "37.99", "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_newburycomics_crawler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'crawlers.newburycomics'`

- [ ] **Step 3: Write the minimal plugin to make both tests pass**

Create `backend/crawlers/newburycomics.py`:

```python
from typing import AsyncIterator
from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"


class Crawler:
    site_name: str = "Newbury Comics"
    base_url: str = "https://www.newburycomics.com"
    genre_summary: str = "New England record store chain and pop-culture retailer with a broad new/exclusive vinyl selection."
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

        items = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
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

Run: `cd backend && pytest tests/test_newburycomics_crawler.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Add the `"Various Artists"` vendor case**

Append to `backend/tests/test_newburycomics_crawler.py`:

```python
@respx.mock
async def test_crawl_catalog_passes_through_various_artists_vendor_unchanged(crawler):
    product = {**_PRODUCT, "vendor": "Various Artists", "title": "Guardians Of The Galaxy: Awesome Mix Vol. 1 LP"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Various Artists"
    assert items[0]["title"] == "Guardians Of The Galaxy: Awesome Mix Vol. 1 LP"
```

Run: `cd backend && pytest tests/test_newburycomics_crawler.py -v`
Expected: PASS (3 passed) — no implementation change needed, since `_items()` already passes `vendor` through unchanged; this step documents and locks in the behavior.

- [ ] **Step 6: Add the null-variants and empty-catalog-page cases**

Append to `backend/tests/test_newburycomics_crawler.py`:

```python
@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_empty_collection_yields_nothing(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Newbury Comics"
    assert Crawler.base_url == "https://www.newburycomics.com"
    assert Crawler.crawler_type == "catalog"
```

Run: `cd backend && pytest tests/test_newburycomics_crawler.py -v`
Expected: PASS (6 passed) — `product.get("variants") or []` in `_items()` already handles `None`, and `iter_products()`'s existing empty-page break already handles the empty-collection case, so no implementation change is needed for either.

- [ ] **Step 7: Run the full backend test suite to confirm no regressions**

Run: `cd backend && pytest -v`
Expected: PASS — all tests pass, including the new `test_newburycomics_crawler.py` cases and every pre-existing test file. Per `CLAUDE.md`'s "Tests" section, run this in the foreground, not backgrounded/parallel with another pytest invocation.

- [ ] **Step 8: Commit**

```bash
cd backend && git add crawlers/newburycomics.py tests/test_newburycomics_crawler.py
```

Write the commit message to a file first (trailers are easy to drop with `-m`):

```bash
cat > /tmp/newburycomics-crawler-commit.txt << 'EOF'
Add Newbury Comics store crawler

Crawls the /collections/vinyl Shopify collection -- confirmed live to
be the complete, currently-purchasable vinyl catalog (1,128 products,
100% available), unlike /collections/all's 12,335 Vinyl-type products
of which 91% are discontinued. vendor maps directly to artist with no
title parsing needed, unlike Turntable Lab or Numero Group.

See docs/specifications/shaping/2026-08-14-newburycomics-store-crawler-design.md

Note: This commit message was created by AI
ai-generated: true
ai-model: claude-sonnet-5
ai-tool: claude-code
ai-surface: claude-code-cli
ai-executor: local-agent
EOF
git commit -F /tmp/newburycomics-crawler-commit.txt
```

---

### Task 2: Pre-PR spec-drift check and PR

**Files:** None created; this task is a review-and-open-PR step.

**Interfaces:**
- Consumes: the committed `backend/crawlers/newburycomics.py` and `backend/tests/test_newburycomics_crawler.py` from Task 1.
- Produces: an open, ready-for-review pull request.

- [ ] **Step 1: Run the pre-PR spec-drift check**

Per `CLAUDE.md`'s "Pre-PR spec-drift check" section: `grep -rl` across both `docs/superpowers/specs/` and `docs/specifications/shaping/` for `newburycomics`, `Newbury Comics`, and `collections/vinyl` to catch any other spec that references this work and might now be stale.

```bash
grep -rli "newburycomics\|newbury comics" docs/superpowers/specs/ docs/specifications/shaping/ 2>/dev/null
```

Expected: only `docs/specifications/shaping/2026-08-14-newburycomics-store-crawler-design.md` itself matches (this branch introduces no changes to any other spec — no shared helper was modified, `shopify_catalog.py` is used unchanged). If any other spec matches and has drifted, amend it as its own commit before continuing.

- [ ] **Step 2: Push the branch and open the PR**

```bash
git push -u origin claude/newbury-comics-crawler-17392e
gh pr create --title "Add Newbury Comics store crawler" --body "$(cat <<'EOF'
## Summary
- Adds `backend/crawlers/newburycomics.py`, a `catalog` crawler over Newbury Comics's `/collections/vinyl` Shopify collection (confirmed live: 1,128 products, 100% available, the complete purchasable vinyl catalog).
- `vendor` maps directly to artist and `title` is used as-is -- no regex/parsing needed, unlike Turntable Lab or Numero Group.

Design spec: `docs/specifications/shaping/2026-08-14-newburycomics-store-crawler-design.md`

## Spec-drift check
Grepped `docs/superpowers/specs/` and `docs/specifications/shaping/` for references to this crawler -- only its own design spec matched, no other spec drifted.

## Test plan
- [x] `pytest backend/tests/test_newburycomics_crawler.py -v` passes (6 tests)
- [x] `pytest backend/` full suite passes with no regressions
EOF
)" --draft=false
```

Expected: PR opens as ready for review (not draft), per `CLAUDE.md`'s "Pull requests" section. Report the PR URL back.

---

## Self-Review Notes

**Spec coverage:** Collection slug (`"vinyl"`) ✓ Task 1 Step 3. Vendor-as-artist, title-as-is ✓ Task 1 Steps 1/3/5. Availability-only gate, no pre-order ✓ Task 1 Steps 1/3. `format="Vinyl"` hardcoded ✓ Task 1 Step 3. `resolve_cover_image` reuse ✓ Task 1 Steps 1/3. Automatic registration (no wiring task) ✓ confirmed via `backend/main.py`'s `register_crawler()` startup loop, which reads every module in `backend/crawlers/` — no task needed. Pre-PR spec-drift check ✓ Task 2 Step 1.

**Placeholder scan:** No TBD/TODO; every step has literal code or an exact command.

**Type consistency:** `Crawler.crawl_catalog()` yields `dict` throughout; `_items()` returns `list[dict]` and is called the same way in both the design spec and this plan; test fixture name `crawler` and URL constant `_PRODUCTS_URL` used consistently across all six test functions.
