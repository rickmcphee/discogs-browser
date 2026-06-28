"""
Regression tests for Amazon price extraction using saved page fixtures.

Each fixture is a rendered HTML snapshot captured via scripts/capture_fixture.py.
Tests load the fixture with page.set_content() and call extract_price() directly,
bypassing navigation so they run offline without bot-detection risk.
"""

import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

from amazon import extract_price

FIXTURES = Path(__file__).parent.parent / "fixtures" / "crawlers" / "amazon"


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


async def _load(page, fixture_name: str):
    html = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    await page.set_content(html, wait_until="domcontentloaded")


# ---------------------------------------------------------------------------
# 311 — Mosaic (2-LP vinyl, direct Amazon listing, price present)
# Expected: $35.16
# ---------------------------------------------------------------------------
async def test_mosaic_price(browser_page):
    await _load(browser_page, "311_mosaic.html")
    price = await extract_price(browser_page, ["vinyl"])
    assert price == 35.16


# ---------------------------------------------------------------------------
# 311 — Evolver (vinyl only via marketplace / "See All Buying Options")
# Expected: no price — the scoped selectors must not return carousel prices
# ---------------------------------------------------------------------------
async def test_evolver_no_price(browser_page):
    await _load(browser_page, "311_evolver.html")
    price = await extract_price(browser_page, ["vinyl"])
    assert price is None


# ---------------------------------------------------------------------------
# Adam And The Ants — Prince Charming (marketplace only, "See All Buying Options")
# Expected: no price
# ---------------------------------------------------------------------------
async def test_adam_ants_prince_charming_no_price(browser_page):
    await _load(browser_page, "adam_and_the_ants_prince_charming.html")
    price = await extract_price(browser_page, ["vinyl"])
    assert price is None
