"""
Tests for the Rough Trade release crawler.

Mirrors test_amoeba_crawler.py / test_discogs_marketplace_crawler.py: a real
local headless browser loads fixture HTML via set_content() (no navigation,
no live site, no bot-detection risk), wrapped in a _FakePage that maps
candidate product URLs to (html, status) pairs so 404 probing, challenge
titles, and wrong-product landings can all be scripted.

The fixtures are constructed to the schema.org/OpenGraph contract the crawler
consumes, not captures of the live page -- roughtrade.com's Cloudflare bot
management blocks every non-browser client reachable from the authoring
sandbox (see 2026-09-01-rough-trade-crawler-design.md).
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

import roughtrade
from roughtrade import (
    Crawler,
    _finite_price,
    _offer_listing,
    _product_nodes,
    _slugify,
)
from crawler import BotDetectedError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "crawlers" / "roughtrade"

RELEASE = {"artist": "Sample Artist", "title": "Sample Album"}
PRODUCT_URL = "https://www.roughtrade.com/en-us/product/sample-artist/sample-album"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- Pure helpers -----------------------------------------------------------


def test_site_metadata():
    assert Crawler.site_name == "Rough Trade"
    assert Crawler.base_url == "https://www.roughtrade.com"
    # `is True` on purpose: crawl_manager only honours a literal True.
    assert Crawler.empty_result_is_expected is True
    # A release crawler: no crawler_type attribute, so registration defaults it.
    assert not hasattr(Crawler, "crawler_type")
    # Keeps stock-item fan-out from aiming slug guesses at other stores'
    # storefront title strings.
    assert Crawler.requires_discogs_release is True


def test_slugify_lowercases_and_hyphenates():
    assert _slugify("Sample Album") == "sample-album"
    assert _slugify("The Devil Wears Prada") == "the-devil-wears-prada"


def test_slugify_folds_diacritics():
    assert _slugify("Björk") == "bjork"
    assert _slugify("Café Tacvba") == "cafe-tacvba"


def test_slugify_drops_punctuation():
    assert _slugify("What's Going On") == "whats-going-on"
    assert _slugify("Sam & Dave") == "sam-dave"
    assert _slugify("...And Justice For All") == "and-justice-for-all"


def test_candidate_urls_single_when_no_ampersand():
    assert Crawler._candidate_urls(RELEASE) == [PRODUCT_URL]


def test_candidate_urls_adds_and_variant_for_ampersand():
    urls = Crawler._candidate_urls({"artist": "Sam & Dave", "title": "Soul Men"})
    assert urls == [
        "https://www.roughtrade.com/en-us/product/sam-dave/soul-men",
        "https://www.roughtrade.com/en-us/product/sam-and-dave/soul-men",
    ]


def test_candidate_urls_strips_discogs_disambiguator():
    urls = Crawler._candidate_urls({"artist": "Nirvana (2)", "title": "Nevermind"})
    assert urls == ["https://www.roughtrade.com/en-us/product/nirvana/nevermind"]


def test_candidate_urls_empty_without_artist_or_title():
    assert Crawler._candidate_urls({"artist": "", "title": "Something"}) == []
    assert Crawler._candidate_urls({"artist": "Someone", "title": ""}) == []


def test_search_url_is_first_candidate():
    assert Crawler.search_url(RELEASE) == PRODUCT_URL


def test_title_matches_full_page_title():
    assert Crawler._title_matches(
        "Sample Artist - Sample Album on Vinyl LP | Rough Trade - (LP) | Rough Trade",
        "Sample Artist", "Sample Album",
    )


def test_title_matches_self_titled_release():
    assert Crawler._title_matches(
        "Ramones - Ramones on Vinyl LP | Rough Trade - (LP) | Rough Trade",
        "Ramones", "Ramones",
    )


def test_title_matches_survives_page_title_truncation():
    # Live page titles truncate long product names mid-word; the check only
    # needs the leading words.
    assert Crawler._title_matches(
        "Ramones - Greatest Hits (Start Your Ear Off R on Vinyl LP | Rough Trade",
        "Ramones", "Greatest Hits (Start Your Ear Off Right)",
    )


def test_title_matches_accepts_edition_suffix_on_the_name():
    assert Crawler._title_matches(
        "Sample Artist - Sample Album - Black Friday 2024 - (Vinyl LP)",
        "Sample Artist", "Sample Album",
    )


def test_title_matches_rejects_other_artist():
    assert not Crawler._title_matches(
        "Unrelated Band - Sample Album on Vinyl LP", "Sample Artist", "Sample Album"
    )


def test_title_matches_rejects_other_title():
    assert not Crawler._title_matches(
        "Sample Artist - Something Else Entirely on Vinyl LP",
        "Sample Artist", "Sample Album",
    )


def test_title_matches_rejects_blank_page_title():
    assert not Crawler._title_matches("", "Sample Artist", "Sample Album")


def test_finite_price_parses_strings_and_numbers():
    assert _finite_price("31.99") == 31.99
    assert _finite_price(24.5) == 24.5


def test_finite_price_rejects_unusable_values():
    assert _finite_price(None) is None
    assert _finite_price("") is None
    assert _finite_price("nan") is None
    assert _finite_price("inf") is None
    assert _finite_price("-3.00") is None
    assert _finite_price("0") is None
    assert _finite_price("$31.99") is None


def test_product_nodes_reads_top_level_lists_and_graph():
    texts = [
        '{"@type": "Product", "name": "A"}',
        '[{"@type": "Product", "name": "B"}, {"@type": "BreadcrumbList"}]',
        '{"@graph": [{"@type": ["Product", "IndividualProduct"], "name": "C"}]}',
        '{"@type": "WebSite"}',
        "not json at all",
        None,
    ]
    assert [n["name"] for n in _product_nodes(texts)] == ["A", "B", "C"]


def test_offer_listing_skips_unavailable():
    for availability in (
        "https://schema.org/OutOfStock", "http://schema.org/SoldOut",
        "OutOfStock", "Discontinued",
    ):
        offer = {"price": "31.99", "priceCurrency": "USD", "availability": availability}
        assert _offer_listing(offer, PRODUCT_URL) is None


def test_offer_listing_keeps_preorder_and_unstated_availability():
    for availability in ("https://schema.org/PreOrder", "https://schema.org/InStock", None):
        offer = {"price": "31.99", "priceCurrency": "USD"}
        if availability:
            offer["availability"] = availability
        assert _offer_listing(offer, PRODUCT_URL)["price"] == 31.99


def test_offer_listing_uses_low_price_for_aggregate_offers():
    offer = {"@type": "AggregateOffer", "lowPrice": "24.50", "priceCurrency": "GBP"}
    listing = _offer_listing(offer, PRODUCT_URL)
    assert listing["price"] == 24.50
    assert listing["currency"] == "GBP"


def test_offer_listing_defaults_currency_to_usd():
    assert _offer_listing({"price": "31.99"}, PRODUCT_URL)["currency"] == "USD"


def test_offer_listing_rejects_priceless_offer():
    assert _offer_listing({"priceCurrency": "USD"}, PRODUCT_URL) is None


# --- Page-reading tests -----------------------------------------------------


@pytest.fixture(autouse=True)
def fast_search(monkeypatch):
    async def instant_sleep(seconds):
        pass

    monkeypatch.setattr(roughtrade, "sleep", instant_sleep)
    # The settle loop polls to a deadline; the challenge cases would each wait
    # out the real 15s otherwise.
    monkeypatch.setattr(roughtrade, "_SETTLE_TIMEOUT_MS", 300)


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


class _FakePage:
    """Maps candidate URLs to (html, status); scripts titles when asked.

    `titles`, when given, is consumed one entry per title() call with the last
    entry sticking (the discogs_marketplace pattern) -- that is what lets a
    test drive a Cloudflare interstitial being replaced by the real page.
    Without it, title() reads the loaded fixture's real <title>.
    """

    def __init__(self, real_page, routes, titles=None):
        self._real = real_page
        self._routes = routes
        self._titles = list(titles) if titles is not None else None
        self.visited = []

    async def goto(self, url, wait_until=None):
        self.visited.append(url)
        if url == "about:blank":
            return None
        html, status = self._routes[url]
        await self._real.set_content(html, wait_until="domcontentloaded")
        return SimpleNamespace(status=status)

    async def title(self):
        if self._titles is not None:
            return self._titles[0] if len(self._titles) == 1 else self._titles.pop(0)
        return await self._real.title()

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(ms / 1000)

    async def evaluate(self, script, arg=None):
        return await self._real.evaluate(script, arg)


_NOT_FOUND = ("<html><head><title>Rough Trade</title></head><body>404</body></html>", 404)


async def test_search_returns_offers_cheapest_first(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_in_stock.html"), 200)})
    results = await Crawler().search(RELEASE, page)

    # The $49.99 signed edition is out of stock and dropped; the carousel's
    # $9.99 must never appear -- visible text is not a price source.
    assert [r["price"] for r in results] == [27.99, 31.99]
    assert results[0] == {
        "url": PRODUCT_URL,
        "price": 27.99,
        "shipping": None,
        "currency": "USD",
        "condition": None,
    }


async def test_search_ends_on_a_blank_page(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_in_stock.html"), 200)})
    await Crawler().search(RELEASE, page)
    assert page.visited[-1] == "about:blank"


async def test_search_returns_empty_when_every_offer_is_out_of_stock(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_out_of_stock.html"), 200)})
    assert await Crawler().search(RELEASE, page) == []


async def test_search_reads_aggregate_offers_from_a_graph(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_aggregate_offer.html"), 200)})
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["currency"]) for r in results] == [(24.50, "GBP")]


async def test_search_falls_back_to_og_price_metas(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_meta_only.html"), 200)})
    results = await Crawler().search(RELEASE, page)
    assert [(r["price"], r["currency"]) for r in results] == [(29.99, "USD")]


async def test_search_raises_rather_than_scraping_visible_prices(browser_page):
    # The page shows $31.99 in plain text twice; without a machine-readable
    # signal the crawl must raise, not guess -- a free-text amount is as
    # likely to be a recommendation-carousel price as the product's.
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("product_no_signals.html"), 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_tries_the_and_variant_after_a_404(browser_page):
    release = {"artist": "Sam & Dave", "title": "Sample Album"}
    hit = "https://www.roughtrade.com/en-us/product/sam-and-dave/sample-album"
    html = _fixture("product_in_stock.html").replace("Sample Artist", "Sam & Dave")
    page = _FakePage(browser_page, {
        "https://www.roughtrade.com/en-us/product/sam-dave/sample-album": _NOT_FOUND,
        hit: (html, 200),
    })
    results = await Crawler().search(release, page)

    assert results[0]["url"] == hit
    assert page.visited[:2] == [
        "https://www.roughtrade.com/en-us/product/sam-dave/sample-album", hit,
    ]


async def test_search_returns_empty_when_every_candidate_404s(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: _NOT_FOUND})
    assert await Crawler().search(RELEASE, page) == []


async def test_search_treats_a_wrong_product_landing_as_a_miss(browser_page):
    page = _FakePage(browser_page, {PRODUCT_URL: (_fixture("different_product.html"), 200)})
    assert await Crawler().search(RELEASE, page) == []


async def test_search_raises_when_every_available_offer_is_priceless(browser_page):
    # An available offer whose price cannot be read is not "understood" -- a
    # page of only those must raise, not report a confirmed miss.
    html = (
        "<html><head>"
        "<title>Sample Artist - Sample Album on Vinyl LP | Rough Trade</title>"
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "Sample Album",'
        ' "offers": [{"@type": "Offer", "price": "TBC", "priceCurrency": "USD",'
        ' "availability": "https://schema.org/InStock"}]}'
        "</script></head><body></body></html>"
    )
    page = _FakePage(browser_page, {PRODUCT_URL: (html, 200)})
    with pytest.raises(RuntimeError, match="price signals"):
        await Crawler().search(RELEASE, page)


async def test_search_raises_on_an_unresolved_challenge(browser_page):
    challenge = ("<html><head><title>Just a moment...</title></head><body></body></html>", 403)
    page = _FakePage(browser_page, {PRODUCT_URL: challenge})
    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


async def test_search_raises_bot_detected_on_a_hard_error_page(browser_page):
    blocked = (
        "<html><head><title>Attention Required! | Cloudflare</title></head><body></body></html>",
        403,
    )
    page = _FakePage(browser_page, {PRODUCT_URL: blocked})
    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


async def test_search_parses_after_a_challenge_clears_despite_the_403(browser_page):
    # A cleared challenge reloads the real page while goto's response object
    # still holds the interstitial's 403 -- the settled title decides, not
    # the status.
    page = _FakePage(
        browser_page,
        {PRODUCT_URL: (_fixture("product_in_stock.html"), 403)},
        titles=[
            "Just a moment...",
            "Sample Artist - Sample Album on Vinyl LP | Rough Trade - (LP) | Rough Trade",
        ],
    )
    results = await Crawler().search(RELEASE, page)
    assert results[0]["price"] == 27.99
