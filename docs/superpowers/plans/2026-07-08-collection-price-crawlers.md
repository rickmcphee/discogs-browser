# Collection/Wishlist Price Crawler Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop showing empty catalog-crawler columns in Collections/Wishlist, and add two new per-release price crawlers: an unrestricted eBay search and a Discogs marketplace search scoped to USA-shipping listings.

**Architecture:** One frontend one-line filter fix (shared by both tabs already). On the backend, extract the existing eBay Browse API logic out of `crawlers/ebay.py` into a new top-level `ebay_api.py` shared module (mirroring the existing `shopify_catalog.py` precedent for catalog crawlers), then add two new crawler plugin files that use it/parallel it: `crawlers/ebay_general.py` (API-based, shares the extracted module) and `crawlers/discogs_marketplace.py` (Playwright-based, scrapes `discogs.com/sell/release/{id}`).

**Tech Stack:** FastAPI/Python backend (httpx, Playwright, pytest + pytest-asyncio + respx), React/TypeScript frontend (Vitest + Testing Library).

**Spec:** [`docs/superpowers/specs/2026-07-08-collection-price-crawlers-design.md`](../specs/2026-07-08-collection-price-crawlers-design.md)

---

### Task 1: Filter Collections/Wishlist columns to release-type crawlers

**Files:**
- Modify: `frontend/src/views/RecordBrowser.tsx:106`
- Test: `frontend/src/test/recordBrowser.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/test/recordBrowser.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import RecordBrowser from '../views/RecordBrowser'
import type { Crawler } from '../api/types'

const getReleases = vi.fn()
const getArtists = vi.fn()

vi.mock('../api/client', () => ({
  getReleases: (...args: unknown[]) => getReleases(...args),
  getArtists: (...args: unknown[]) => getArtists(...args),
}))

const CRAWLERS: Crawler[] = [
  { id: 1, site_name: 'Amazon', module_path: '', crawler_type: 'release', enabled: true, last_run: null, base_url: null, login_url: null },
  { id: 2, site_name: 'Epitaph', module_path: '', crawler_type: 'catalog', enabled: true, last_run: null, base_url: null, login_url: null },
]

beforeEach(() => {
  getReleases.mockReset()
  getArtists.mockReset()
  getReleases.mockResolvedValue({ total: 0, page: 1, per_page: 250, releases: [] })
  getArtists.mockResolvedValue([])
  localStorage.clear()
})

describe('RecordBrowser', () => {
  it('renders a column for an enabled release-type crawler but not an enabled catalog-type crawler', async () => {
    render(<RecordBrowser scope="collection" onRefreshPrices={() => {}} crawlers={CRAWLERS} />)
    await waitFor(() => expect(screen.getByText('Amazon')).toBeTruthy())
    expect(screen.queryByText('Epitaph')).toBeNull()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: FAIL — `screen.queryByText('Epitaph')` is not null (the "Epitaph" column header renders today).

- [ ] **Step 3: Fix the filter**

In `frontend/src/views/RecordBrowser.tsx`, change line 106:

```tsx
const enabledCrawlers = crawlers.filter((c) => c.enabled)
```

to:

```tsx
const enabledCrawlers = crawlers.filter((c) => c.enabled && c.crawler_type === 'release')
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/test/recordBrowser.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/RecordBrowser.tsx frontend/src/test/recordBrowser.test.tsx
git commit -m "collection-price-crawlers: only show release-type crawler columns in Collections/Wishlist"
```

---

### Task 2: Extract shared eBay Browse API logic into `backend/ebay_api.py`

This is a pure refactor — behavior must not change. `crawlers/ebay.py` currently contains the OAuth token fetch, item-matching, and format-mapping logic inline; a second eBay-based crawler (Task 3) needs the same logic without a copy-paste duplicate. Crawler plugin files are loaded via `importlib.util.spec_from_file_location` from an arbitrary path and are never real members of the `backend.crawlers` package, so a shared module must live at the top level of `backend/` (alongside `crawler.py`, `shopify_catalog.py`), not inside `backend/crawlers/` — a file placed there would get mis-registered as a bogus crawler by `main.py`'s bundling scan.

**Files:**
- Create: `backend/ebay_api.py`
- Modify: `backend/crawlers/ebay.py` (replace inline logic with calls into `ebay_api`)
- Modify: `backend/tests/test_ebay_crawler.py` (update module references after the extraction)
- Create: `backend/tests/test_ebay_api.py` (pure-function tests moved out of `test_ebay_crawler.py`)

- [ ] **Step 1: Create `backend/ebay_api.py`**

```python
import re
import time
from typing import Optional
import httpx
from logging_config import get_logger
from crawler import clean_search_text, strip_stop_words, title_variants

log = get_logger("ebay_api")

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_SCOPE = "https://api.ebay.com/oauth/api_scope"

FORMAT_KEYWORDS = {
    "Vinyl":    [r"\bvinyl\b", r"\blp\b", r"\brecord\b"],
    "CD":       [r"\bcd\b"],
    "Cassette": [r"\bcassette\b", r"\btape\b"],
    "DVD":      [r"\bdvd\b"],
    "Blu-ray":  [r"\bblu.?ray\b"],
}

# eBay US Music leaf-category IDs, used to constrain the Browse API search to
# the release's format so the price sort operates within-format rather than
# across all formats. Verified against ebay.com/b category URLs.
FORMAT_CATEGORY_IDS = {
    "Vinyl": "176985",
    "CD":    "176984",
}

# Module-level token cache, shared by every crawler that calls search_ebay()
_token = None  # type: ignore[assignment]
_token_expires_at: float = 0.0


async def get_token(app_id: str, cert_id: str) -> str:
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _TOKEN_URL,
            auth=(app_id, cert_id),
            data={"grant_type": "client_credentials", "scope": _SCOPE},
        )
        r.raise_for_status()
        data = r.json()
    _token = data["access_token"]
    _token_expires_at = time.time() + int(data.get("expires_in", 7200))
    return _token


def _words(text: str) -> set:
    return set(text.lower().split())


def pick_matching_item(items: list, release: dict) -> Optional[dict]:
    artist_words = _words(clean_search_text(release.get("artist", "")))
    title_words = _words(clean_search_text(release.get("title", "")))
    fmt_patterns = FORMAT_KEYWORDS.get(release.get("format", ""))

    for item in items:
        listing_title = item.get("title", "").lower()
        listing_words = set(listing_title.split())

        if artist_words:
            if len(artist_words & listing_words) / len(artist_words) < 0.5:
                continue
        if title_words:
            if len(title_words & listing_words) / len(title_words) < 0.5:
                continue

        if fmt_patterns:
            if not any(re.search(p, listing_title) for p in fmt_patterns):
                continue

        return item
    return None


async def search_ebay(
    release: dict,
    app_id: str,
    cert_id: str,
    seller: Optional[str],
    limit: int,
    log_prefix: str,
    fallback_url: str,
) -> list[dict]:
    if not app_id or not cert_id:
        log.warning("[%s] ebay_app_id or ebay_cert_id not configured", log_prefix)
        return []

    barcode = release.get("barcode") or ""
    if barcode:
        query = barcode
        log.info("[%s] Searching by barcode: %s", log_prefix, barcode)
    else:
        raw_artist = clean_search_text(release.get("artist", ""))
        artist = strip_stop_words(raw_artist) if raw_artist.lower() != "various" else ""
        raw_title = clean_search_text(release.get("title", ""))
        title = title_variants(raw_title)[-1]
        query = f"{artist} {title}".strip()
        log.info("[%s] searching by artist/title: %s", log_prefix, query)

    try:
        token = await get_token(app_id, cert_id)
    except httpx.HTTPError as e:
        log.error("[%s] token fetch failed: %s", log_prefix, e)
        raise

    filter_clauses = ["buyingOptions:{FIXED_PRICE}"]
    if seller:
        filter_clauses.insert(0, f"sellers:{{{seller}}}")
    params = {
        "q": query,
        "filter": ",".join(filter_clauses),
        "sort": "price+shippingCost",
        "limit": str(limit),
    }
    category_id = FORMAT_CATEGORY_IDS.get(release.get("format", ""))
    if category_id:
        params["category_ids"] = category_id

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                _SEARCH_URL,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        log.error("[%s] search HTTP error %s: %s", log_prefix, e.response.status_code, e)
        return []
    except httpx.RequestError as e:
        log.error("[%s] search request error: %s", log_prefix, e)
        return []

    items = data.get("itemSummaries")
    if not items:
        log.info("[%s] No results for: %s", log_prefix, query)
        return []

    item = pick_matching_item(items, release)
    if item is None:
        log.info("[%s] no validated match for: %s", log_prefix, query)
        return []

    price_val = item.get("price", {})
    shipping_options = item.get("shippingOptions", [])
    shipping = None
    if shipping_options:
        raw = shipping_options[0].get("shippingCost", {}).get("value")
        if raw is not None:
            try:
                shipping = float(raw)
            except (ValueError, TypeError):
                pass

    try:
        price = float(price_val.get("value", 0))
    except (ValueError, TypeError):
        price = None

    item_url = item.get("itemWebUrl", "")
    if not item_url or not item_url.startswith("https://www.ebay.com"):
        legacy_id = item.get("legacyItemId")
        item_url = f"https://www.ebay.com/itm/{legacy_id}" if legacy_id else fallback_url

    return [{
        "url": item_url,
        "price": price,
        "shipping": shipping,
        "currency": price_val.get("currency"),
        "condition": item.get("condition"),
    }]
```

- [ ] **Step 2: Rewrite `backend/crawlers/ebay.py` to use it**

Replace the full file contents with:

```python
import urllib.parse
from config import load_config
from crawler import clean_search_text
from ebay_api import search_ebay

CCMUSIC_SELLER = "collectorschoicemusic"


class Crawler:
    site_name: str = "CC Music/eBay"
    base_url: str = f"https://www.ebay.com/str/{CCMUSIC_SELLER}"
    login_url: str = ""

    @classmethod
    def search_url(cls, release: dict) -> str:
        artist = clean_search_text(release.get("artist", ""))
        title = clean_search_text(release.get("title", ""))
        query = urllib.parse.quote_plus(f"{artist} {title}")
        return f"https://www.ebay.com/sch/{CCMUSIC_SELLER}/i.html?_nkw={query}"

    async def search(self, release: dict, page) -> list[dict]:
        cfg = load_config()
        return await search_ebay(
            release,
            cfg.get("ebay_app_id", ""),
            cfg.get("ebay_cert_id", ""),
            seller=CCMUSIC_SELLER,
            limit=3,
            log_prefix="CC Music/eBay",
            fallback_url=self.search_url(release),
        )
```

- [ ] **Step 3: Update `backend/tests/test_ebay_crawler.py` to reference the moved token cache**

Change the top of the file — replace:

```python
import time
import respx
import httpx
import pytest
import crawlers.ebay as ebay_module
from crawlers.ebay import Crawler, _pick_matching_item
```

with:

```python
import time
import respx
import httpx
import pytest
import ebay_api as ebay_api_module
from crawlers.ebay import Crawler
```

Then replace every remaining `ebay_module` reference with `ebay_api_module` — this appears in the `reset_token_cache` fixture (both `_token`/`_token_expires_at` resets, before and after `yield`) and in `test_token_refreshed_when_expired` (the pre-fill assignment and the final assertion). There are 5 occurrences total.

Then delete the `_pick_matching_item` test section — everything from the `# ---...--- \n# _pick_matching_item format validation\n# ---...---` comment block through the end of `test_pick_matching_item_returns_first_passing` (this is being moved to `test_ebay_api.py` in the next step). Keep `test_search_url_format`, `test_search_url_encodes_spaces`, and `test_config_round_trip`, which come after that block in the original file — they still test `crawlers/ebay.py` directly.

- [ ] **Step 4: Create `backend/tests/test_ebay_api.py`**

```python
from ebay_api import pick_matching_item


def test_pick_matching_item_vinyl_match():
    items = [{"title": "Miles Davis Kind of Blue Vinyl LP"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    assert pick_matching_item(items, release) is not None


def test_pick_matching_item_rejects_cd_for_vinyl():
    items = [{"title": "Miles Davis Kind of Blue CD"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    assert pick_matching_item(items, release) is None


def test_pick_matching_item_cd_match():
    items = [{"title": "Miles Davis Kind of Blue CD"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "CD"}
    assert pick_matching_item(items, release) is not None


def test_pick_matching_item_rejects_vinyl_for_cd():
    items = [{"title": "Miles Davis Kind of Blue Vinyl LP"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "CD"}
    assert pick_matching_item(items, release) is None


def test_pick_matching_item_unknown_format_passes_through():
    items = [{"title": "Miles Davis Kind of Blue"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Box Set"}
    assert pick_matching_item(items, release) is not None


def test_pick_matching_item_rejects_artist_mismatch():
    items = [{"title": "John Coltrane Kind of Blue Vinyl LP"}]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    assert pick_matching_item(items, release) is None


def test_pick_matching_item_returns_first_passing():
    items = [
        {"title": "Miles Davis Kind of Blue CD"},
        {"title": "Miles Davis Kind of Blue Vinyl LP"},
    ]
    release = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}
    result = pick_matching_item(items, release)
    assert result is not None
    assert "Vinyl" in result["title"]
```

- [ ] **Step 5: Run both test files to verify the refactor preserved behavior**

Run: `cd backend && pytest tests/test_ebay_crawler.py tests/test_ebay_api.py -v`
Expected: All tests PASS (same assertions as before the refactor, just against the new module split).

- [ ] **Step 6: Commit**

```bash
git add backend/ebay_api.py backend/crawlers/ebay.py backend/tests/test_ebay_crawler.py backend/tests/test_ebay_api.py
git commit -m "collection-price-crawlers: extract shared eBay Browse API logic into ebay_api.py"
```

---

### Task 3: Add the general eBay crawler

**Files:**
- Create: `backend/crawlers/ebay_general.py`
- Test: `backend/tests/test_ebay_general_crawler.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ebay_general_crawler.py`:

```python
import respx
import httpx
import pytest
import ebay_api as ebay_api_module
from crawlers.ebay_general import Crawler

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_TOKEN_RESP = {"access_token": "test-token", "expires_in": 7200}
_ITEM = {
    "title": "Miles Davis Kind of Blue Vinyl LP",
    "itemWebUrl": "https://www.ebay.com/itm/456",
    "price": {"value": "9.99", "currency": "USD"},
    "shippingOptions": [{"shippingCost": {"value": "4.00"}}],
    "condition": "Very Good (VG)",
}
_RELEASE = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl", "barcode": None}


@pytest.fixture(autouse=True)
def reset_token_cache():
    ebay_api_module._token = None
    ebay_api_module._token_expires_at = 0.0
    yield
    ebay_api_module._token = None
    ebay_api_module._token_expires_at = 0.0


@pytest.fixture
def crawler(tmp_config_dir):
    import config as config_module
    cfg = config_module.load_config()
    cfg["ebay_app_id"] = "app-id"
    cfg["ebay_cert_id"] = "cert-id"
    config_module.save_config(cfg)
    return Crawler()


@respx.mock
async def test_search_returns_lowest_price_listing(crawler):
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json={"itemSummaries": [_ITEM]}))
    results = await crawler.search(_RELEASE, page=None)
    assert results == [{
        "url": "https://www.ebay.com/itm/456",
        "price": 9.99,
        "shipping": 4.00,
        "currency": "USD",
        "condition": "Very Good (VG)",
    }]


@respx.mock
async def test_search_omits_seller_filter_and_raises_limit(crawler):
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json={"itemSummaries": [_ITEM]}))
    await crawler.search(_RELEASE, page=None)
    search_call = next(c for c in respx.calls if str(c.request.url).startswith(_SEARCH_URL))
    assert "sellers:" not in search_call.request.url.params["filter"]
    assert search_call.request.url.params["limit"] == "5"


@respx.mock
async def test_search_returns_empty_when_missing_config(tmp_config_dir):
    crawler = Crawler()
    results = await crawler.search(_RELEASE, page=None)
    assert results == []
    assert not respx.calls


def test_site_name_is_ebay():
    assert Crawler.site_name == "eBay"


def test_search_url_has_no_seller_path():
    url = Crawler.search_url({"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"})
    assert "collectorschoicemusic" not in url
    assert url.startswith("https://www.ebay.com/sch/i.html?_nkw=")
    assert "Miles" in url or "miles" in url.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_ebay_general_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.ebay_general'`

- [ ] **Step 3: Create `backend/crawlers/ebay_general.py`**

```python
import urllib.parse
from config import load_config
from crawler import clean_search_text
from ebay_api import search_ebay


class Crawler:
    site_name: str = "eBay"
    base_url: str = "https://www.ebay.com"
    login_url: str = ""

    @classmethod
    def search_url(cls, release: dict) -> str:
        artist = clean_search_text(release.get("artist", ""))
        title = clean_search_text(release.get("title", ""))
        query = urllib.parse.quote_plus(f"{artist} {title}")
        return f"https://www.ebay.com/sch/i.html?_nkw={query}"

    async def search(self, release: dict, page) -> list[dict]:
        cfg = load_config()
        return await search_ebay(
            release,
            cfg.get("ebay_app_id", ""),
            cfg.get("ebay_cert_id", ""),
            seller=None,
            limit=5,
            log_prefix="eBay",
            fallback_url=self.search_url(release),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_ebay_general_crawler.py -v`
Expected: All PASS

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && pytest`
Expected: All PASS (in particular `test_main.py`'s bundled-crawler seeding tests, if any assert on the exact set of bundled crawler files — check for a failure there and adjust the assertion to include `ebay_general.py` if one exists, rather than skipping it)

- [ ] **Step 6: Commit**

```bash
git add backend/crawlers/ebay_general.py backend/tests/test_ebay_general_crawler.py
git commit -m "collection-price-crawlers: add general eBay crawler (all sellers, not just CC Music)"
```

---

### Task 4: Add the Discogs marketplace crawler

Discogs marketplace listings for a release live at `discogs.com/sell/release/{release_id}`, filterable to `ships_from=United States` and sortable by `sort=price,asc` — confirmed as the current, correct path via web search (the release-scoped marketplace API endpoint was shut down years ago, which is why this has to be a page scrape, not an API call). Direct `curl` against that URL returns a Cloudflare "Just a moment..." challenge (HTTP 403), confirming this must run through the real Playwright browser context the app already uses for Amazon, with bot-interstitial detection from the start.

The exact listing-row selectors below (`#pjax_container table tbody tr`, `td.item_price .price[data-pricevalue]`, `.item_shipping`, `td.item_description .item_condition`) come from a third-party Discogs-scraping writeup, not a first-party capture against the live DOM — they could not be verified from this environment (no working browser tool, and the app's own capture script requires a real Chrome session). Step 6 of this task is a mandatory manual verification pass before treating this crawler as done — the same posture this repo already takes for Playwright-scraped selectors (see `CLAUDE.md`: "Playwright-dependent code ... is not unit-tested; integration testing is manual").

**Files:**
- Create: `backend/crawlers/discogs_marketplace.py`
- Test: `backend/tests/test_discogs_marketplace_crawler.py` (create)

- [ ] **Step 1: Write the failing tests for the pure helpers**

Create `backend/tests/test_discogs_marketplace_crawler.py`:

```python
from crawlers.discogs_marketplace import Crawler, _parse_amount


def test_parse_amount_extracts_price():
    assert _parse_amount("$12.50") == 12.50


def test_parse_amount_extracts_from_shipping_text():
    assert _parse_amount("+$4.00 Shipping") == 4.00


def test_parse_amount_strips_thousands_separator():
    assert _parse_amount("$1,024.99") == 1024.99


def test_parse_amount_returns_none_for_free_shipping():
    assert _parse_amount("Free Shipping") is None


def test_parse_amount_returns_none_for_empty_string():
    assert _parse_amount("") is None


def test_search_url_strips_leading_r_from_discogs_id():
    url = Crawler.search_url({"discogs_id": "r249504"})
    assert url == "https://www.discogs.com/sell/release/249504?ships_from=United+States&sort=price%2Casc"


def test_site_name_is_discogs():
    assert Crawler.site_name == "Discogs"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_discogs_marketplace_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crawlers.discogs_marketplace'`

- [ ] **Step 3: Create `backend/crawlers/discogs_marketplace.py`**

```python
import re
import urllib.parse
from typing import Optional
from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("crawlers.discogs_marketplace")

_AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")


def _parse_amount(text: str) -> Optional[float]:
    if not text:
        return None
    match = _AMOUNT_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


class Crawler:
    site_name: str = "Discogs"
    base_url: str = "https://www.discogs.com"
    login_url: str = ""

    @classmethod
    def search_url(cls, release: dict) -> str:
        release_id = release["discogs_id"][1:]
        query = urllib.parse.urlencode({"ships_from": "United States", "sort": "price,asc"})
        return f"https://www.discogs.com/sell/release/{release_id}?{query}"

    async def search(self, release: dict, page) -> list[dict]:
        url = self.search_url(release)
        await page.goto(url, wait_until="domcontentloaded")

        title = await page.title()
        if "just a moment" in title.lower():
            log.warning("[Discogs] bot interstitial detected for release %s", release.get("discogs_id"))
            raise BotDetectedError()

        rows = page.locator("#pjax_container table tbody tr")
        if await rows.count() == 0:
            log.info("[Discogs] no USA-shipping listings for release %s", release.get("discogs_id"))
            return []

        row = rows.first
        price_el = row.locator("td.item_price .price")
        shipping_el = row.locator("td.item_price .item_shipping")
        condition_el = row.locator("td.item_description .item_condition")

        price = None
        currency = None
        if await price_el.count():
            currency = await price_el.get_attribute("data-currency")
            price_attr = await price_el.get_attribute("data-pricevalue")
            price = float(price_attr) if price_attr else _parse_amount(await price_el.inner_text())

        shipping = _parse_amount(await shipping_el.inner_text()) if await shipping_el.count() else None
        condition = (await condition_el.inner_text()).strip() if await condition_el.count() else None

        return [{
            "url": url,
            "price": price,
            "shipping": shipping,
            "currency": currency,
            "condition": condition,
        }]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_discogs_marketplace_crawler.py -v`
Expected: All PASS

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && pytest`
Expected: All PASS (again, check `test_main.py` for any assertion on the exact bundled-crawler file list and add `discogs_marketplace.py` if needed)

- [ ] **Step 6: Manually verify the scraping selectors against the live site**

This step requires a machine with the app's real Playwright/Chrome setup (persistent profile + stealth), which isn't available in this planning environment — do this once, locally, before relying on this crawler:

1. From `backend/`, run: `python scripts/capture_fixture.py discogs_marketplace "https://www.discogs.com/sell/release/<a real release_id from your own collection>?ships_from=United+States&sort=price,asc" "<artist> - <title>"`
2. Open the saved file at `backend/tests/fixtures/crawlers/discogs_marketplace/<slug>.html` in a text editor.
3. Confirm the top listing row is inside `<table>...<tbody><tr>...` under an element with `id="pjax_container"`, and that its price cell has a class containing `item_price` with a nested element with class `price` carrying a `data-pricevalue` attribute. If the real markup differs, update the selectors in `backend/crawlers/discogs_marketplace.py` (`price_el`/`shipping_el`/`condition_el`) to match what you actually see, and re-run Step 4/5.
4. Confirm the release you captured actually has at least one USA-shipping listing, and that the price extracted matches what you see when you open the URL in an ordinary browser tab.
5. If the Cloudflare check falsely triggers (page title contains "just a moment" even after the challenge resolves) or never triggers when it should, adjust the check in `search()` accordingly.

- [ ] **Step 7: Commit**

```bash
git add backend/crawlers/discogs_marketplace.py backend/tests/test_discogs_marketplace_crawler.py
git commit -m "collection-price-crawlers: add Discogs marketplace crawler (cheapest USA-shipping listing)"
```

---

## Self-Review

**Spec coverage:**
- Column fix (Problem #1) → Task 1.
- General eBay crawler (Problem #2) → Tasks 2 (shared extraction) + 3.
- Discogs marketplace crawler (Problem #3) → Task 4.
- "No data model changes" → confirmed, no task touches `db.py` or adds a migration.
- "Both new crawlers auto-enable on first registration" (per spec amendment) → no code change needed, this is `db.register_crawler`'s existing behavior; nothing to build.
- `BotDetectedError` handling in the Discogs crawler (per spec amendment) → included in Task 4, Step 3.

**Type/signature consistency check:** `search_ebay(release, app_id, cert_id, seller, limit, log_prefix, fallback_url)` is defined once in Task 2 and called identically (same argument names, same order) in both `crawlers/ebay.py` (Task 2) and `crawlers/ebay_general.py` (Task 3). `pick_matching_item` is defined in `ebay_api.py` (Task 2) and imported by that exact name in `test_ebay_api.py` — no leftover reference to the old `_pick_matching_item` name outside the deleted test block.

**Known open risk carried forward (not a gap, a disclosed limitation):** the Discogs crawler's selectors are sourced from third-party documentation, not a first-party live capture — Task 4 Step 6 is a required, concrete manual verification pass, not an optional nice-to-have.
