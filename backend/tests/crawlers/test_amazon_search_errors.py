"""How crawlers/amazon.py's search() reports product-page failures.

A crawler's [] means "the site answered and has nothing"; anything else must
raise, or CrawlManager's consecutive-failure circuit breaker can't see it (see
docs/superpowers/specs/2026-08-01-worker-pool-pacing-design.md, item 9).
search() returns a listing dict, so a swallowed product-page failure is worse
than an eBay-style [] -- a truthy result *resets* the failure counter.

Runs against a real Chromium page with every request routed to canned HTML, so
nothing here contacts amazon.com.
"""

import pytest
from playwright.async_api import async_playwright, Error as PlaywrightError

from crawler import BotDetectedError
from crawlers.amazon import Crawler

_RELEASE = {"artist": "Test Artist", "title": "Test Album", "format": "Vinyl"}

_SEARCH_HTML = """
<html><body>
  <div data-component-type="s-search-result">
    <h2>Test Artist - Test Album</h2>
    <div data-cy="price-recipe"><a class="a-text-bold" href="/dp/TEST">Vinyl</a></div>
  </div>
</body></html>
"""

_PRODUCT_HTML_NO_PRICE = "<html><body><div id='corePrice_feature_div'></div></body></html>"
_PRODUCT_HTML_BOT_WALL = "<html><body><input value='Continue shopping'></body></html>"


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


async def _route(page, product_response):
    """Serve the search page from _SEARCH_HTML and hand the product page to
    `product_response`, which either fulfills or aborts the route."""
    async def handler(route):
        if "/dp/TEST" in route.request.url:
            await product_response(route)
        else:
            await route.fulfill(status=200, content_type="text/html", body=_SEARCH_HTML)

    await page.route("**/*", handler)


async def test_product_page_load_failure_raises(browser_page):
    """Asserts on the message, not just the type: with the failure swallowed,
    a PlaywrightError still escapes here, but it's the trailing
    goto("about:blank") tripping over the aborted navigation's chrome-error
    page -- a cleanup artifact that says nothing about the real failure. What
    must reach the caller is the connection error itself."""
    async def abort(route):
        await route.abort("connectionrefused")

    await _route(browser_page, abort)
    with pytest.raises(PlaywrightError) as excinfo:
        await Crawler().search(_RELEASE, browser_page)
    assert "ERR_CONNECTION_REFUSED" in str(excinfo.value)


async def test_product_page_bot_wall_raises_bot_detected(browser_page):
    """Swallowed, this never reached _paced_search's context-reset retry --
    the wall was invisible, and the price-less listing it returned instead
    read as a success."""
    async def wall(route):
        await route.fulfill(status=200, content_type="text/html", body=_PRODUCT_HTML_BOT_WALL)

    await _route(browser_page, wall)
    with pytest.raises(BotDetectedError):
        await Crawler().search(_RELEASE, browser_page)


async def test_product_page_without_a_price_still_returns_the_listing(browser_page):
    """The other half of the contract: a product page that loads fine but
    shows no price (out of stock, marketplace-only) is a real answer, not a
    failure -- it must keep returning the URL with price None."""
    async def no_price(route):
        await route.fulfill(status=200, content_type="text/html", body=_PRODUCT_HTML_NO_PRICE)

    await _route(browser_page, no_price)
    results = await Crawler().search(_RELEASE, browser_page)
    assert len(results) == 1
    assert results[0]["price"] is None
    assert results[0]["url"].endswith("/dp/TEST")
