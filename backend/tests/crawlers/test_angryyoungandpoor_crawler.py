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
