"""
Tests for the SideOneDummy Records catalog crawler using a saved page
fixture.

Mirrors test_angryyoungandpoor_crawler.py's pattern: a real local headless
browser loads a saved static fixture via page.set_content() (no navigation,
no live site, no bot-detection risk). Here, a _FakePage wraps the real page
so goto() loads the fixture instead of navigating, while evaluate()
delegates straight to the real page -- this exercises the actual
_EXTRACT_JS extraction plus the downstream Python parsing.
"""

import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

from sideonedummyrecords import Crawler

FIXTURE = Path(__file__).parent.parent / "fixtures" / "crawlers" / "sideonedummyrecords" / "vinyl.html"


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


class _FakePage:
    def __init__(self, real_page, html=None, page_title="Vinyl | Shop the SideOneDummy Records Official Store",
                 evaluate_result=None):
        self._real_page = real_page
        self._html = html if html is not None else FIXTURE.read_text(encoding="utf-8")
        self._page_title = page_title
        # When set, evaluate() returns this directly instead of running the
        # real _EXTRACT_JS -- lets a test isolate the rawCount == 0 branch,
        # which real DOM can't produce here: wait_for_selector() and
        # _EXTRACT_JS query the exact same selector back to back with no
        # navigation in between, so if the former ever finds something, the
        # latter necessarily will too.
        self._evaluate_result = evaluate_result

    async def goto(self, url, timeout=None):
        await self._real_page.set_content(self._html, wait_until="domcontentloaded")

    async def title(self):
        return self._page_title

    async def evaluate(self, script):
        if self._evaluate_result is not None:
            return self._evaluate_result
        return await self._real_page.evaluate(script)

    async def wait_for_selector(self, selector, timeout=None):
        # set_content() already finished rendering everything the fixture
        # has -- nothing will appear later -- so check presence once and
        # fail immediately rather than genuinely honoring `timeout` (which
        # would make a "selector never appears" test really wait that long).
        if await self._real_page.locator(selector).count() == 0:
            raise TimeoutError(f"selector not found in fixture: {selector}")


@pytest.fixture
def fake_page(browser_page):
    return _FakePage(browser_page)


async def test_crawl_catalog_parses_dash_title(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Kerosene Heights")
    assert item["title"] == "Blame It On The Weather Limited Edition Watermelon Splash LP"
    assert item["format"] == "Vinyl"
    assert item["price"] == 25.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://sideonedummyrecords.shop.musictoday.com/product/XTLPSO123/kerosene-heights-blame-it-on-the-weather-lp"
    assert item["cover_image_url"] == "https://static.musictoday.com/store/bands/6255/product_small/XTLPSO123.PNG"


async def test_crawl_catalog_strips_quote_delimiters_so_title_matches_catalog(fake_page):
    # Regression for the db.py title-match bug: a title left as
    # "'All. Right. Now' 2xLP/CD - ..." (leading quote intact) can never
    # equal-or-prefix-match a catalog title of "All. Right. Now", silently
    # orphaning the stock row from the release. The quote marks must be
    # peeled off so the title starts with the album name itself.
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Satsang")
    assert item["title"] == "All. Right. Now 2xLP/CD - Orange Vinyl w Black Smoke"
    assert not item["title"].startswith("'")


def test_parse_artist_title_keeps_contraction_inside_quoted_album_name():
    # Regression: a bare closing-quote match (no lookahead) treats the
    # apostrophe in "Can't" as the delimiter, truncating to quoted="Can"
    # and leaving "t Stop' LP" as garbage. The real closing quote must
    # only match when followed by whitespace or end-of-string.
    artist, title = Crawler._parse_artist_title("Band 'Can't Stop' LP")
    assert artist == "Band"
    assert title == "Can't Stop LP"


def test_parse_artist_title_splits_on_dash_glued_directly_to_artist_name():
    # Real live title: the band name is "Walter Etc." (with the period),
    # and this store's markup glues the dash straight onto it with no
    # space ("Etc.- When..."), while a sibling product for the same band
    # has the more common "Etc. - When..." with a space. Both must parse
    # to the same artist -- _SEPARATOR_RE's dash branch intentionally
    # allows zero whitespace *before* the dash for exactly this case, so
    # requiring whitespace there (a plausible-looking tightening) would
    # wrongly skip this title instead of parsing it.
    artist, title = Crawler._parse_artist_title(
        'Walter Etc.- When The Band Breaks Up Again "Pink Acid Wash" Vinyl'
    )
    assert artist == "Walter Etc."
    assert title == 'When The Band Breaks Up Again "Pink Acid Wash" Vinyl'


async def test_crawl_catalog_splits_on_first_dash_not_a_later_one(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Violent Soho")
    assert item["title"] == "Hungry Ghost 10 Year Anniversary LP - Standard Version 1"


async def test_crawl_catalog_prefers_sale_price_over_list_price(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Violent Soho")
    assert item["price"] == 29.99


def test_parse_product_falls_back_to_list_price_when_sale_price_is_unparsable():
    # Not just an empty salePrice string -- a malformed one must not drop an
    # otherwise-valid product that the extraction filter already confirmed
    # has a real listPrice.
    item = Crawler._parse_product({
        "id": "X1", "name": "Band - Title LP", "href": "/product/X1/band-title",
        "image": None, "listPrice": "$25.99", "salePrice": "not-a-price",
    })
    assert item["price"] == 25.99


def test_parse_product_raises_when_list_price_itself_is_unparsable():
    # _EXTRACT_JS's malformedCount only checks that listPrice is a
    # non-empty string, not that it still parses as "$X.XX" -- so a price
    # format change (e.g. "25.99 USD" instead of "$25.99") would pass that
    # check and land here. Returning None (a silent skip) would look
    # identical to the artist-parse skip for messy titles, but a genuinely
    # present, unparsable price is a much stronger drift signal than a
    # garbled title -- must raise, not skip.
    with pytest.raises(RuntimeError):
        Crawler._parse_product({
            "id": "X1", "name": "Band - Title LP", "href": "/product/X1/band-title",
            "image": None, "listPrice": "25.99 USD", "salePrice": "",
        })


async def test_crawl_catalog_excludes_out_of_stock_product(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    assert not [i for i in items if i["artist"] == "Walter Etc."]


async def test_crawl_catalog_skips_title_with_no_separator(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    assert not [i for i in items if "Flogging Molly" in i.get("title", "")]
    titles = {i["title"] for i in items}
    assert "LP Bundle" not in titles


async def test_crawl_catalog_yields_exactly_the_in_stock_fixture_rows(fake_page):
    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog(fake_page)]
    # 5 products in the fixture: 4 in-stock + parseable, 1 out-of-stock
    # (no .PricingContainer -- excluded by the listPrice filter), 1 of the
    # 4 in-stock has no separator (Flogging Molly LP Bundle -- excluded by
    # the artist-parse skip). Net: 3.
    assert len(items) == 3


async def test_crawl_catalog_raises_when_listing_selector_never_appears(browser_page):
    # No li.ProductElementsDisplay at all, and a normal (non-interstitial)
    # title, must raise rather than yield [] -- an empty product list is
    # otherwise indistinguishable from "genuinely sold out" to
    # replace_stock_items() (db.py), which would wipe every previously
    # known in-stock row for this crawler on a false "nothing to see"
    # (e.g. markup drift). Since the title isn't the interstitial's, this
    # is the wait_for_selector-timeout -> RuntimeError branch, not
    # BotDetectedError.
    fake_page = _FakePage(browser_page, html="<html><body><ul class=\"ProductGrid\"></ul></body></html>")
    crawler = Crawler()
    with pytest.raises(RuntimeError):
        async for _ in crawler.crawl_catalog(fake_page):
            pass


async def test_crawl_catalog_raises_when_extraction_finds_zero_raw_products(browser_page):
    # Isolates the rawCount == 0 guard specifically -- real DOM can't
    # exercise it directly, since wait_for_selector() and _EXTRACT_JS query
    # the identical selector back to back with no navigation in between, so
    # if the former succeeds (as it does here, against the normal fixture),
    # the latter can't come back with a zero raw count. This is
    # defense-in-depth for exactly that coupling ever breaking (e.g. the
    # two selectors drifting apart) -- still must raise, not yield [], for
    # the same replace_stock_items() reason as the test above.
    fake_page = _FakePage(browser_page, evaluate_result={"rawCount": 0, "malformedCount": 0, "products": []})
    crawler = Crawler()
    with pytest.raises(RuntimeError):
        async for _ in crawler.crawl_catalog(fake_page):
            pass


async def test_crawl_catalog_raises_when_in_stock_cards_are_missing_expected_fields(browser_page):
    # li.ProductElementsDisplay staying intact (rawCount > 0) doesn't mean
    # each card extracted cleanly -- .ProductName, its <a>, or the pricing
    # attributes could drift independently, silently dropping every card
    # from `products` while rawCount looks fine. That would present as a
    # false "sold out" to replace_stock_items() (db.py), wiping the
    # store's existing rows, exactly like the rawCount == 0 case but via a
    # different path -- must raise instead, and must not be confused with a
    # genuinely out-of-stock card (which _EXTRACT_JS excludes up front via
    # .OutOfStockMsg, before this count is ever incremented).
    fake_page = _FakePage(browser_page, evaluate_result={"rawCount": 3, "malformedCount": 2, "products": []})
    crawler = Crawler()
    with pytest.raises(RuntimeError):
        async for _ in crawler.crawl_catalog(fake_page):
            pass


async def test_crawl_catalog_raises_bot_detected_when_interstitial_never_clears(browser_page):
    # Same empty-listing symptom as above, but with the Cloudflare
    # interstitial's own title still showing -- must be classified as
    # BotDetectedError specifically, since crawl_manager retries that one
    # with a fresh browser context (backend/crawl_manager.py
    # _run_catalog_crawler), which a generic RuntimeError doesn't get.
    from crawler import BotDetectedError

    fake_page = _FakePage(
        browser_page,
        html="<html><head><title>Just a moment...</title></head><body></body></html>",
        page_title="Just a moment...",
    )
    crawler = Crawler()
    with pytest.raises(BotDetectedError):
        async for _ in crawler.crawl_catalog(fake_page):
            pass


def test_site_metadata():
    assert Crawler.site_name == "SideOneDummy Records"
    assert Crawler.base_url == "https://sideonedummyrecords.shop.musictoday.com"
    assert Crawler.crawler_type == "catalog_browser"
    assert Crawler.genre == "punk"
