# Angry Young and Poor Store Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `angryyoungandpoor.com` as a new Store-tab catalog source, introducing a new `catalog_browser` crawler kind (Playwright-backed) alongside the existing pure-`httpx` `catalog` kind, since this site's Cloudflare protection blocks any non-browser request.

**Architecture:** `CrawlManager._sync_stock` gains a small `_run_catalog_crawler(crawler)` helper that dispatches on `crawler.crawler_type`: plain `catalog` crawlers call `crawl_catalog()` exactly as today (zero-arg, `httpx`-only, untouched); `catalog_browser` crawlers get a Playwright page opened from the already-running shared stealth Chromium instance (`self._browser`/`self._stealth`, started in `start_worker_pool()`), with the same one-retry-on-`BotDetectedError` convention the release-crawl path already uses (`_new_context`/`_reset_context`). `backend/crawlers/angryyoungandpoor.py` is the first `catalog_browser` crawler: it loads four PinnacleCart category pages via `?viewAll=yes`, extracts product data with a single `page.evaluate()` call per category (no new HTML-parsing dependency — mirrors how `amazon.py` already queries the live DOM), and does the artist/title/format/condition parsing and cross-category pid dedup in plain Python.

**Tech Stack:** Python 3.9 (FastAPI backend), Playwright + playwright-stealth (already a dependency), pytest/pytest-asyncio, React + TypeScript frontend, Vitest.

**Full design reference:** [`docs/superpowers/specs/2026-08-05-angryyoungandpoor-store-crawler-design.md`](../specs/2026-08-05-angryyoungandpoor-store-crawler-design.md)

---

### Task 1: `CrawlManager` — `catalog_browser` dispatch

**Files:**
- Modify: `backend/crawl_manager.py:506-581` (insert a new method after `start_stock_sync`, rewrite `_sync_stock`'s crawler loop)
- Test: `backend/tests/test_crawl_manager.py` (append near the existing `test_worker_retries_once_on_bot_detection_then_succeeds` test, ~line 817)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_crawl_manager.py`:

```python
async def test_run_catalog_crawler_calls_zero_arg_crawl_catalog_for_plain_catalog_type(manager):
    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog"

    async def fake_crawl_catalog():
        yield {"artist": "A", "title": "T", "url": "https://x"}
    fake_plugin.crawl_catalog = fake_crawl_catalog

    items = await manager._run_catalog_crawler(fake_plugin)
    assert items == [{"artist": "A", "title": "T", "url": "https://x"}]


async def test_run_catalog_crawler_opens_a_page_and_closes_it_for_catalog_browser_type(manager):
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_context = AsyncMock()
    fake_page = MagicMock()
    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog_browser"
    received_pages = []

    async def fake_crawl_catalog(page):
        received_pages.append(page)
        yield {"artist": "A", "title": "T", "url": "https://x"}
    fake_plugin.crawl_catalog = fake_crawl_catalog

    with patch("crawler._new_context", new=AsyncMock(return_value=(fake_context, fake_page))):
        items = await manager._run_catalog_crawler(fake_plugin)

    assert items == [{"artist": "A", "title": "T", "url": "https://x"}]
    assert received_pages == [fake_page]
    fake_context.close.assert_awaited_once()


async def test_run_catalog_crawler_retries_once_on_bot_detection_then_succeeds(manager):
    from crawler import BotDetectedError
    manager._browser = MagicMock()
    manager._stealth = MagicMock()
    fake_context = AsyncMock()
    fake_page = MagicMock()
    fake_plugin = MagicMock()
    fake_plugin.crawler_type = "catalog_browser"
    call_count = {"n": 0}

    async def fake_crawl_catalog(page):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise BotDetectedError("interstitial")
        yield {"artist": "A", "title": "T", "url": "https://x"}
    fake_plugin.crawl_catalog = fake_crawl_catalog

    with patch("crawler._new_context", new=AsyncMock(return_value=(fake_context, fake_page))), \
         patch("crawler._reset_context", new=AsyncMock(return_value=(fake_context, fake_page))):
        items = await manager._run_catalog_crawler(fake_plugin)

    assert items == [{"artist": "A", "title": "T", "url": "https://x"}]
    assert call_count["n"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_crawl_manager.py -k test_run_catalog_crawler -v`
Expected: FAIL with `AttributeError: 'CrawlManager' object has no attribute '_run_catalog_crawler'`

- [ ] **Step 3: Add `_run_catalog_crawler` and wire it into `_sync_stock`**

In `backend/crawl_manager.py`, insert this new method between `start_stock_sync` (ends `return True` at line 515) and `_sync_stock` (line 517):

```python
    async def _run_catalog_crawler(self, crawler) -> list[dict]:
        """Runs crawler.crawl_catalog(), handling the catalog_browser kind's
        Playwright page + one-retry-on-BotDetectedError convention (same as
        the release-crawl path's _paced_search). Plain catalog crawlers keep
        calling crawl_catalog() zero-arg, unchanged."""
        from crawler import _new_context, _reset_context, BotDetectedError

        if crawler.crawler_type != "catalog_browser":
            return [item async for item in crawler.crawl_catalog()]

        context, page = await _new_context(self._browser, self._stealth)
        try:
            try:
                return [item async for item in crawler.crawl_catalog(page)]
            except BotDetectedError:
                context, page = await _reset_context(context, self._browser, self._stealth, None)
                return [item async for item in crawler.crawl_catalog(page)]
        finally:
            await context.close()

```

Then replace `_sync_stock` (currently `backend/crawl_manager.py:517-581`) with:

```python
    async def _sync_stock(self):
        import httpx
        from db import get_app_pool, get_enabled_crawlers, replace_stock_items, update_crawler_last_run
        from crawler import load_enabled_crawlers

        await self._broadcast({"status": "stock_sync_started"})
        log.info("Stock sync started")
        try:
            with get_app_pool().connection() as conn:
                enabled = (
                    get_enabled_crawlers(conn, crawler_type="catalog")
                    + get_enabled_crawlers(conn, crawler_type="catalog_browser")
                )
            crawlers = load_enabled_crawlers(enabled)
            if not crawlers:
                await self._broadcast({"status": "stock_sync_error", "error": "No enabled catalog crawlers"})
                return

            total_synced = 0
            consecutive_429_sites: list[str] = []
            for crawler in crawlers:
                try:
                    items = await self._run_catalog_crawler(crawler)
                except Exception as e:
                    is_rate_limited = isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
                    if is_rate_limited:
                        log.warning("[%s] Stock crawl rate-limited (HTTP 429): %s", crawler._db_site_name, e)
                        consecutive_429_sites.append(crawler._db_site_name)
                    else:
                        log.error("[%s] Stock crawl failed: %s", crawler._db_site_name, e, exc_info=True)
                        await self._broadcast({
                            "status": "stock_sync_error",
                            "error": str(e),
                            "source": crawler._db_site_name,
                        })
                        consecutive_429_sites = []
                    if len(consecutive_429_sites) >= 2:
                        log.warning(
                            "Stock sync aborted: %d catalog sites in a row hit HTTP 429 (%s) -- "
                            "likely a platform-wide rate limit, not grinding the rest of the run into it",
                            len(consecutive_429_sites), ", ".join(consecutive_429_sites),
                        )
                        await self._broadcast({
                            "status": "stock_sync_aborted",
                            "error": "Too many consecutive rate-limited catalog sites",
                            "sources": list(consecutive_429_sites),
                        })
                        return
                    continue

                consecutive_429_sites = []
                with get_app_pool().connection() as conn:
                    replace_stock_items(conn, crawler._db_id, items)
                    update_crawler_last_run(conn, crawler._db_id)
                    conn.commit()
                total_synced += len(items)
                log.info("[%s] Stock sync found %d items", crawler._db_site_name, len(items))
                await self._broadcast({"status": "stock_sync_progress", "synced": total_synced, "source": crawler._db_site_name})

            await self._broadcast({"status": "stock_sync_complete", "synced": total_synced})
            log.info("Stock sync complete: %d items", total_synced)
        except asyncio.CancelledError:
            log.info("Stock sync cancelled")
            raise
        except Exception as e:
            log.error("Stock sync failed: %s", e, exc_info=True)
```

(This is a straight extraction — the loop body's `try`/`except`/429-handling logic is byte-for-byte the same as before, just calling `self._run_catalog_crawler(crawler)` in place of the old inline `async for item in crawler.crawl_catalog(): items.append(item)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_crawl_manager.py -k test_run_catalog_crawler -v`
Expected: 3 passed

- [ ] **Step 5: Run the full crawl_manager test suite to check for regressions**

Run: `cd backend && pytest tests/test_crawl_manager.py -v`
Expected: all pass (the pre-existing stock-sync-adjacent tests don't exist yet for `_sync_stock` itself, so this mainly guards the worker-pool/collection-sync tests weren't disturbed)

- [ ] **Step 6: Commit**

```bash
cd backend && git add crawl_manager.py tests/test_crawl_manager.py
```

Use the `sdlc:commit` skill (per this session's standing instruction to always use it for commits) rather than a raw `git commit`.

---

### Task 2: `backend/crawlers/angryyoungandpoor.py`

**Files:**
- Create: `backend/crawlers/angryyoungandpoor.py`
- Create: `backend/tests/fixtures/crawlers/angryyoungandpoor/records.html`
- Create: `backend/tests/fixtures/crawlers/angryyoungandpoor/va_compilation.html`
- Test: `backend/tests/crawlers/test_angryyoungandpoor_crawler.py`

- [ ] **Step 1: Add the HTML fixtures**

Create `backend/tests/fixtures/crawlers/angryyoungandpoor/records.html` — a minimal hand-built snapshot of PinnacleCart's real `.pcShowProducts`/`[data-pid]` markup (captured live from `Records-c301.htm` during design; see the design spec's Technical grounding section), covering: one real release, two non-release accessories (to exercise the dash-split filter), one sale-priced release (to exercise the cross-category dedup test in Task 2 Step 6), and one used release (to exercise the `(USED)` → condition-suffix rule):

```html
<div class="pcShowProducts">
  <div class="pcColumn pccol-fluid pccol-fluid-4" data-pid="372193">
    <div class="pcShowProductsH pcShowProductBgHover">
      <div class="pcShowProductImageH">
        <a itemprop="url" href="100-Demons-Embrace-The-Black-Light-LP-Onyx-Marble-Vinyl-301p372193.htm"><img itemprop="image" src="catalog/products/lp/CCAS157X.jpg" alt="100 Demons- Embrace The Black Light LP (Onyx Marble Vinyl)"></a>
      </div>
      <div class="pcShowProductInfoH">
        <div class="pcShowProductName">
          <a href="100-Demons-Embrace-The-Black-Light-LP-Onyx-Marble-Vinyl-301p372193.htm"><span itemprop="name">100 Demons- Embrace The Black Light LP (Onyx Marble Vinyl)</span></a>
        </div>
        <div class="pcShowProductPrice" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD">
          <meta itemprop="price" content="27.99">
          Price: <span class="eprice">$27.99</span>
        </div>
      </div>
    </div>
  </div>
  <div class="pcColumn pccol-fluid pccol-fluid-4" data-pid="91756">
    <div class="pcShowProductsH pcShowProductBgHover">
      <div class="pcShowProductImageH">
        <a itemprop="url" href="12-Record-Sleeve-301p91756.htm"><img itemprop="image" src="catalog/thumbs/cd/lpsleeve.jpg" alt="12&quot; Record Sleeve"></a>
      </div>
      <div class="pcShowProductInfoH">
        <div class="pcShowProductName">
          <a href="12-Record-Sleeve-301p91756.htm"><span itemprop="name">12" Record Sleeve</span></a>
        </div>
        <div class="pcShowProductPrice" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD">
          <meta itemprop="price" content="0.37">
          Price: <span class="eprice">$0.37</span>
        </div>
      </div>
    </div>
  </div>
  <div class="pcColumn pccol-fluid pccol-fluid-4" data-pid="322708">
    <div class="pcShowProductsH pcShowProductBgHover">
      <div class="pcShowProductImageH">
        <a itemprop="url" href="Vinyl-Styl-Record-Cleaning-Fluid-1-25oz-301p322708.htm"><img itemprop="image" src="catalog/products/lp/vnst72331X.jpg" alt="Vinyl Styl Record Cleaning Fluid (1.25oz)"></a>
      </div>
      <div class="pcShowProductInfoH">
        <div class="pcShowProductName">
          <a href="Vinyl-Styl-Record-Cleaning-Fluid-1-25oz-301p322708.htm"><span itemprop="name">Vinyl Styl Record Cleaning Fluid (1.25oz)</span></a>
        </div>
        <div class="pcShowProductPrice" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD">
          <meta itemprop="price" content="5.99">
          Price: <span class="eprice">$5.99</span>
        </div>
      </div>
    </div>
  </div>
  <div class="pcColumn pccol-fluid pccol-fluid-4" data-pid="360293">
    <div class="pcShowProductsH pcShowProductBgHover">
      <div class="pcShowProductImageH">
        <a itemprop="url" href="13th-Floor-Elevators-Easter-Everywhere-LP-Sale-price-301p360293.htm"><img itemprop="image" src="catalog/products/lp/CHAY7X.jpg" alt="13th Floor Elevators- Easter Everywhere LP (Sale price!)"></a>
      </div>
      <div class="pcShowProductInfoH">
        <div class="pcShowProductName">
          <a href="13th-Floor-Elevators-Easter-Everywhere-LP-Sale-price-301p360293.htm"><span itemprop="name">13th Floor Elevators- Easter Everywhere LP (Sale price!)</span></a>
        </div>
        <div class="pcShowProductPrice" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD">
          <meta itemprop="price" content="28.99">
          Price: <span class="eprice">$28.99</span>
        </div>
      </div>
    </div>
  </div>
  <div class="pcColumn pccol-fluid pccol-fluid-4" data-pid="450102">
    <div class="pcShowProductsH pcShowProductBgHover">
      <div class="pcShowProductImageH">
        <a itemprop="url" href="Agnosy-When-Daylight-Reveals-The-Torture-LP-301p450102.htm"><img itemprop="image" src="catalog/products/lp/agnosyX.jpg" alt="Agnosy- When Daylight Reveals The Torture LP (USED)"></a>
      </div>
      <div class="pcShowProductInfoH">
        <div class="pcShowProductName">
          <a href="Agnosy-When-Daylight-Reveals-The-Torture-LP-301p450102.htm"><span itemprop="name">Agnosy- When Daylight Reveals The Torture LP (USED)</span></a>
        </div>
        <div class="pcShowProductPrice" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD">
          <meta itemprop="price" content="13.99">
          Price: <span class="eprice">$13.99</span>
        </div>
      </div>
    </div>
  </div>
</div>
```

Create `backend/tests/fixtures/crawlers/angryyoungandpoor/va_compilation.html` — two real V/A Compilation LPs titles (captured live from `V-A-Compilation-LPs-c397.htm`), demonstrating the no-artist-dash title shape:

```html
<div class="pcShowProducts">
  <div class="pcColumn pccol-fluid pccol-fluid-4" data-pid="364352">
    <div class="pcShowProductsH pcShowProductBgHover">
      <div class="pcShowProductImageH">
        <a itemprop="url" href="Barbarian-Soundtrack-LP-Mothers-Milk-Blood-Splatter-Vinyl-397p364352.htm"><img itemprop="image" src="catalog/products/lp/barbarianX.jpg" alt="Barbarian (Soundtrack) LP (Mothers Milk &amp; Blood Splatter Vinyl)"></a>
      </div>
      <div class="pcShowProductInfoH">
        <div class="pcShowProductName">
          <a href="Barbarian-Soundtrack-LP-Mothers-Milk-Blood-Splatter-Vinyl-397p364352.htm"><span itemprop="name">Barbarian (Soundtrack) LP (Mothers Milk &amp; Blood Splatter Vinyl)</span></a>
        </div>
        <div class="pcShowProductPrice" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD">
          <meta itemprop="price" content="29.99">
          Price: <span class="eprice">$29.99</span>
        </div>
      </div>
    </div>
  </div>
  <div class="pcColumn pccol-fluid pccol-fluid-4" data-pid="364353">
    <div class="pcShowProductsH pcShowProductBgHover">
      <div class="pcShowProductImageH">
        <a itemprop="url" href="Carrie-Soundtrack-2xLP-Red-Orange-Smoke-Vinyl-397p364353.htm"><img itemprop="image" src="catalog/products/lp/carrieX.jpg" alt="Carrie (Soundtrack) 2xLP (Red &amp; Orange Smoke Vinyl)"></a>
      </div>
      <div class="pcShowProductInfoH">
        <div class="pcShowProductName">
          <a href="Carrie-Soundtrack-2xLP-Red-Orange-Smoke-Vinyl-397p364353.htm"><span itemprop="name">Carrie (Soundtrack) 2xLP (Red &amp; Orange Smoke Vinyl)</span></a>
        </div>
        <div class="pcShowProductPrice" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD">
          <meta itemprop="price" content="34.99">
          Price: <span class="eprice">$34.99</span>
        </div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/crawlers/test_angryyoungandpoor_crawler.py`:

```python
"""
Tests for the Angry Young and Poor catalog crawler using saved page fixtures.

Mirrors test_amazon_price_extraction.py's pattern: a real local headless
browser loads a saved static fixture via page.set_content() (no navigation,
no live site, no bot-detection risk). Here, a _FakePage wraps the real page
so goto() loads a fixture by category instead of navigating, while
evaluate() delegates straight to the real page -- this exercises the actual
_EXTRACT_JS extraction plus the downstream Python parsing.
"""

import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

from angryyoungandpoor import Crawler, _DASH_CATEGORIES

FIXTURES = Path(__file__).parent.parent / "fixtures" / "crawlers" / "angryyoungandpoor"


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


class _FakePage:
    def __init__(self, real_page):
        self._real_page = real_page

    async def goto(self, url, timeout=None):
        category = url.split("/")[-1].split("?")[0]
        fixture = "records.html" if category in _DASH_CATEGORIES else "va_compilation.html"
        html = (FIXTURES / fixture).read_text(encoding="utf-8")
        await self._real_page.set_content(html, wait_until="domcontentloaded")

    async def title(self):
        return "Records - Angry, Young and Poor"

    async def evaluate(self, script):
        return await self._real_page.evaluate(script)


@pytest.fixture
def fake_page(browser_page):
    return _FakePage(browser_page)


async def test_crawl_catalog_excludes_accessories_and_dedupes_across_categories(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]

    # records.html loads 3 times (Records, Sale Records, Used Records
    # categories all map to it) and has 5 raw entries: 2 accessories
    # (excluded) + 3 real releases, deduped by pid across the 3 loads to 3
    # unique items. va_compilation.html loads once with 2 real entries.
    # 3 + 2 = 5 unique items total.
    assert len(items) == 5
    titles = {item["title"] for item in items}
    assert "12\" Record Sleeve" not in titles
    assert "Vinyl Styl Record Cleaning Fluid (1.25oz)" not in titles


async def test_crawl_catalog_parses_real_release(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "100 Demons")
    assert item["title"] == "Embrace The Black Light LP (Onyx Marble Vinyl)"
    assert item["format"] == "Vinyl"
    assert item["price"] == 27.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://www.angryyoungandpoor.com/store/pc/100-Demons-Embrace-The-Black-Light-LP-Onyx-Marble-Vinyl-301p372193.htm"
    assert item["cover_image_url"] == "https://www.angryyoungandpoor.com/store/pc/catalog/products/lp/CCAS157X.jpg"


async def test_crawl_catalog_marks_used_condition_suffix(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Agnosy")
    assert item["title"] == "When Daylight Reveals The Torture LP (Used)"


async def test_crawl_catalog_uses_various_artists_for_va_compilation(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    va_items = [i for i in items if i["artist"] == "Various Artists"]
    assert len(va_items) == 2
    assert {i["title"] for i in va_items} == {
        "Barbarian (Soundtrack) LP (Mothers Milk & Blood Splatter Vinyl)",
        "Carrie (Soundtrack) 2xLP (Red & Orange Smoke Vinyl)",
    }


def test_site_metadata():
    assert Crawler.site_name == "Angry Young and Poor"
    assert Crawler.base_url == "https://www.angryyoungandpoor.com/store/pc"
    assert Crawler.crawler_type == "catalog_browser"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && pytest tests/crawlers/test_angryyoungandpoor_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'angryyoungandpoor'`

- [ ] **Step 4: Write the crawler**

Create `backend/crawlers/angryyoungandpoor.py`:

```python
import re
from typing import AsyncIterator, Optional

from crawler import BotDetectedError

# Same wider vinyl/format regex secretlystore.py uses -- plain \blp\b misses
# glued formats like "2xLP" -- plus revhq.py's \d+\s*" for bare inch-size
# singles (7"/10"/12") that carry no "lp"/"vinyl" wording at all.
_FORMAT_RE = re.compile(r'\bvinyl\b|\b\d*x?lp\b|\bep\b|\d+\s*"', re.IGNORECASE)
_USED_RE = re.compile(r'\s*\(USED\)\s*$')

# Confirmed live: these three share the "Artist- Title FORMAT (variant)"
# title shape (and Records/Sale-Records even share literal product IDs --
# Sale Records is a cross-listing, not separate stock). V/A Compilation LPs
# titles carry no artist prefix at all ("Barbarian (Soundtrack) LP ...") and
# use a different rule below.
_DASH_CATEGORIES = {"Records-c301.htm", "Sale-Records-c472.htm", "Used-Records-c1215.htm"}

# Single page.evaluate() round trip per category instead of looping
# page.locator() calls across ~4400 items. Scoped to the confirmed single
# .pcShowProducts container so an unrelated widget elsewhere on the page
# can't leak in a stray [data-pid].
_EXTRACT_JS = """
() => Array.from(document.querySelectorAll('.pcShowProducts [data-pid]')).map(el => {
  const nameEl = el.querySelector('[itemprop="name"]');
  const urlEl = el.querySelector('[itemprop="url"]');
  const imgEl = el.querySelector('[itemprop="image"]');
  const priceEl = el.querySelector('meta[itemprop="price"]');
  return {
    pid: el.getAttribute('data-pid'),
    name: nameEl ? nameEl.textContent.trim() : null,
    url: urlEl ? urlEl.getAttribute('href') : null,
    image: imgEl ? imgEl.getAttribute('src') : null,
    price: priceEl ? priceEl.getAttribute('content') : null,
  };
}).filter(p => p.pid && p.name && p.url && p.price)
"""


class Crawler:
    site_name: str = "Angry Young and Poor"
    base_url: str = "https://www.angryyoungandpoor.com/store/pc"
    crawler_type: str = "catalog_browser"

    _CATEGORIES = [
        "Records-c301.htm",
        "Sale-Records-c472.htm",
        "Used-Records-c1215.htm",
        "V-A-Compilation-LPs-c397.htm",
    ]

    async def crawl_catalog(self, page) -> AsyncIterator[dict]:
        seen_pids: set[str] = set()
        for category_path in self._CATEGORIES:
            await page.goto(f"{self.base_url}/{category_path}?viewAll=yes", timeout=120_000)
            if "Cloudflare" in await page.title():
                raise BotDetectedError("Cloudflare interstitial")
            raw_products = await page.evaluate(_EXTRACT_JS)
            for product in raw_products:
                pid = product["pid"]
                if pid in seen_pids:
                    continue
                item = self._parse_product(category_path, product)
                if item is None:
                    continue
                seen_pids.add(pid)
                yield item

    @classmethod
    def _parse_product(cls, category_path: str, product: dict) -> Optional[dict]:
        name = product["name"]
        if category_path in _DASH_CATEGORIES:
            if "- " not in name:
                return None
            artist, remainder = name.split("- ", 1)
            artist = artist.strip()
            remainder = remainder.strip()
            if not artist or not remainder or not _FORMAT_RE.search(remainder):
                return None
        else:
            if not _FORMAT_RE.search(name):
                return None
            artist = "Various Artists"
            remainder = name.strip()

        if _USED_RE.search(remainder):
            remainder = _USED_RE.sub('', remainder).strip()
            title = f"{remainder} (Used)"
        else:
            title = remainder

        try:
            price = float(product["price"])
        except (TypeError, ValueError):
            price = None

        return {
            "artist": artist,
            "title": title,
            "format": "Vinyl",
            "price": price,
            "currency": "USD",
            "url": f"{cls.base_url}/{product['url']}",
            "cover_image_url": f"{cls.base_url}/{product['image']}" if product.get("image") else None,
        }
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/crawlers/test_angryyoungandpoor_crawler.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
cd backend && git add crawlers/angryyoungandpoor.py tests/crawlers/test_angryyoungandpoor_crawler.py tests/fixtures/crawlers/angryyoungandpoor/
```

Use the `sdlc:commit` skill.

---

### Task 3: Frontend — `catalog_browser` bucketing fix

**Files:**
- Modify: `frontend/src/api/types.ts:37`
- Modify: `frontend/src/views/Settings.tsx:83-84`
- Test: `frontend/src/test/settings.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/test/settings.test.tsx`, change the import on line 3 to add `within`:

```ts
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
```

Then append a new test inside the `describe('Settings', ...)` block (after the `it('shows both View and Crawl columns...)` test at line 116):

```ts
  it('buckets a catalog_browser crawler into the Store Catalog Sources table, not the release table', async () => {
    const crawlers: Crawler[] = [
      ...CRAWLERS,
      { id: 4, site_name: 'Angry Young and Poor', module_path: '', crawler_type: 'catalog_browser', enabled: true, last_run: null, base_url: null },
    ]
    renderSettings({ crawlers })
    await waitFor(() => expect(getSettings).toHaveBeenCalled())
    const tables = screen.getAllByRole('table')
    expect(within(tables[0]).queryByText('Angry Young and Poor')).not.toBeInTheDocument()
    expect(within(tables[1]).getByText('Angry Young and Poor')).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx -t "buckets a catalog_browser crawler"`
Expected: FAIL — TypeScript error (`crawler_type` doesn't accept `'catalog_browser'`) and/or the assertion fails because the crawler renders in `tables[0]` instead of `tables[1]`

- [ ] **Step 3: Widen the `crawler_type` type**

In `frontend/src/api/types.ts:37`, change:

```ts
  crawler_type: 'release' | 'catalog'
```

to:

```ts
  crawler_type: 'release' | 'catalog' | 'catalog_browser'
```

- [ ] **Step 4: Fix the Settings.tsx bucketing filters**

In `frontend/src/views/Settings.tsx:83-84`, change:

```ts
  const releaseCrawlers = crawlers.filter((c) => c.crawler_type !== 'catalog')
  const catalogCrawlers = crawlers.filter((c) => c.crawler_type === 'catalog')
```

to:

```ts
  const releaseCrawlers = crawlers.filter((c) => c.crawler_type === 'release')
  const catalogCrawlers = crawlers.filter((c) => c.crawler_type === 'catalog' || c.crawler_type === 'catalog_browser')
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/test/settings.test.tsx`
Expected: all pass, including the new test

- [ ] **Step 6: Run the full frontend test suite to check for regressions**

Run: `cd frontend && npx vitest run`
Expected: all pass (in particular `recordBrowser.test.tsx` and `viewRenderChurn.test.tsx`, which reference `crawler_type` fixtures directly)

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/api/types.ts src/views/Settings.tsx src/test/settings.test.tsx
```

Use the `sdlc:commit` skill.

---

### Task 4: Version bump and manual verification

**Files:**
- Modify: `backend/version.py`

- [ ] **Step 1: Bump the version**

Per `CLAUDE.md`'s Versioning section ("minor bump is the default, automatic action... on every PR merge"), change `backend/version.py`:

```python
VERSION = "2.1"
```

- [ ] **Step 2: Commit**

```bash
cd backend && git add version.py
```

Use the `sdlc:commit` skill.

- [ ] **Step 3: Manual integration verification (not automated — Playwright live-navigation code isn't unit-tested per `CLAUDE.md`)**

With the backend running locally (`cd backend && uvicorn main:app --reload --port 8000`) and an admin account:
1. Confirm "Angry Young and Poor" appears in Settings under **Store Catalog Sources** (not under Collection & Wishlist Price Sources), enabled by default (auto-registered via `seed_bundled_crawlers()` on startup).
2. Trigger a stock sync (Store tab → refresh, or `POST /api/stock/sync`) and watch the backend log for `[Angry Young and Poor] Stock sync found N items`.
3. Check the Store tab for Angry Young and Poor results: verify artist/title/price/format look correct, no record-sleeve/cleaning-supply accessories present, at least one `(Used)`-suffixed item from Used Records, and at least one `"Various Artists"` item from V/A Compilation LPs.
4. Check the backend log for any `BotDetectedError`/Cloudflare-interstitial warnings — if Cloudflare escalates beyond what `playwright_stealth` handles, this is where it would surface (see the design spec's Open items).

---

## Execution note

Task 1 and Task 2 are independent of each other (Task 1's tests use a fully fake plugin object) and could be done in either order or in parallel; Task 3 depends on neither but is logically last since it's the bucketing fix for whichever `catalog_browser` crawler exists. Task 4 is last regardless.
