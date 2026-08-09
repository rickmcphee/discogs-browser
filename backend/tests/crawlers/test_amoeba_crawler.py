"""
Tests for the Amoeba Music catalog crawler using a saved AJAX-payload fixture.

Mirrors test_angryyoungandpoor_crawler.py: a real local headless browser runs
the crawler's real extraction JS, so DOMParser and the selectors are exercised
for real, but a _FakePage stubs window.fetch to serve the fixture instead of
hitting the live site (no navigation, no bot-detection risk).
"""

import json
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "crawlers"))

from amoeba import Crawler
from crawler import BotDetectedError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "crawlers" / "amoeba"

_INSTALL_FETCH_STUB_JS = """
(pages) => {
  window.__fetched = [];
  window.fetch = async (url) => {
    window.__fetched.push(url);
    const match = url.match(/[?&]page=(\\d+)/);
    const payload = match ? pages[match[1]] : null;
    if (!payload) return {status: 403, json: async () => ({})};
    return {status: 200, json: async () => payload};
  };
}
"""


def test_site_metadata():
    assert Crawler.site_name == "Amoeba Music"
    assert Crawler.base_url == "https://www.amoeba.com"
    assert Crawler.crawler_type == "catalog_browser"


def test_listing_url_requests_newest_vinyl_only():
    url = Crawler._listing_url(3)

    assert url.startswith("/ajax/cds_and_vinyl.php?")
    assert "page=3" in url
    assert "show=200" in url
    assert "order=date" in url
    assert "direction=desc" in url
    # Filter params are top-level and URL-encoded, not nested under filter=.
    for format_id in (3, 4, 17, 19, 21):
        assert f"format%5B{format_id}%5D={format_id}" in url
    # Exactly these 5 -- guards against a spurious extra format id that would
    # otherwise pass every assertion above.
    assert url.count("format%5B") == 5
    assert "filter=" not in url
    # CD and cassette are explicitly out of scope.
    assert "format%5B1%5D" not in url
    assert "format%5B24%5D" not in url


def test_extract_price_uses_new_price_when_present():
    assert Crawler._extract_price("$36.98", None) == 36.98


def test_extract_price_falls_back_to_used_for_wording():
    assert Crawler._extract_price(None, "1 Used for $3.99") == 3.99


def test_extract_price_falls_back_to_used_from_wording():
    assert Crawler._extract_price(None, "3 Used from $5.99") == 5.99


def test_extract_price_prefers_new_over_used():
    assert Crawler._extract_price("$16.98", "1 Used for $6.99") == 16.98


def test_extract_price_handles_thousands_separator():
    assert Crawler._extract_price("$1,234.56", None) == 1234.56


def test_extract_price_returns_none_when_no_price_anywhere():
    assert Crawler._extract_price(None, None) is None
    assert Crawler._extract_price("", "") is None


def test_extract_price_falls_through_when_new_price_has_no_amount():
    assert Crawler._extract_price("Out of stock", "1 Used for $6.99") == 6.99


def test_extract_price_returns_none_for_a_malformed_amount():
    assert Crawler._extract_price("$,", None) is None
    assert Crawler._extract_price("$", None) is None


def test_extract_format_reads_trailing_token():
    assert Crawler._extract_format("Sound Signal Serenades (LP)") == "LP"
    assert Crawler._extract_format('Split Single (7")') == '7"'
    assert Crawler._extract_format('Deep Cut (12")') == '12"'
    assert Crawler._extract_format('Rarity (10")') == '10"'
    assert Crawler._extract_format("Old Shellac (78)") == "78"


def test_extract_format_keeps_bracketed_variant_before_token():
    assert Crawler._extract_format('Louder Now [Coke Bottle Clear + 7"] (LP)') == "LP"


def test_extract_format_defaults_to_vinyl_without_a_known_token():
    assert Crawler._extract_format("Untitled") == "Vinyl"
    assert Crawler._extract_format("Some Record (Deluxe)") == "Vinyl"
    # A CD-suffixed title should never reach here (the request filters CD out),
    # but it must not be reported as a CD if it does.
    assert Crawler._extract_format("Loosen Up (CD)") == "Vinyl"


def test_extract_format_only_reads_a_trailing_token():
    assert Crawler._extract_format("Reissue (LP) (Remastered)") == "Vinyl"


def _row(
    href="/sound-signal-serenades-lp-son-volt/albums/4495703/",
    title="Sound Signal Serenades (LP)",
    artist="Son Volt",
    newPrice="$29.98",
    used=None,
    image="https://www.amoeba.com/sized-images/crop/50/50/uploads/a.jpg",
):
    return {
        "href": href, "title": title, "artist": artist,
        "newPrice": newPrice, "used": used, "image": image,
    }


def test_parse_row_builds_the_plugin_contract():
    item = Crawler._parse_row(_row())

    assert item == {
        "artist": "Son Volt",
        "title": "Sound Signal Serenades (LP)",
        "format": "LP",
        "price": 29.98,
        "currency": "USD",
        "url": "https://www.amoeba.com/sound-signal-serenades-lp-son-volt/albums/4495703/",
        "cover_image_url": "https://www.amoeba.com/sized-images/crop/50/50/uploads/a.jpg",
    }


def test_parse_row_skips_row_with_no_artist():
    assert Crawler._parse_row(_row(artist=None)) is None
    assert Crawler._parse_row(_row(artist="   ")) is None


def test_parse_row_skips_row_with_no_title():
    assert Crawler._parse_row(_row(title=None)) is None
    assert Crawler._parse_row(_row(title="   ")) is None


def test_parse_row_skips_row_with_no_href():
    assert Crawler._parse_row(_row(href=None)) is None
    assert Crawler._parse_row(_row(href="")) is None


def test_parse_row_skips_row_with_no_price():
    assert Crawler._parse_row(_row(newPrice=None, used=None)) is None


def test_parse_row_does_not_normalise_casing():
    # db.replace_stock_items() owns casing normalisation downstream.
    item = Crawler._parse_row(_row(artist="AC/DC", title="BACK IN BLACK (LP)"))
    assert item["artist"] == "AC/DC"
    assert item["title"] == "BACK IN BLACK (LP)"


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


class _FakePage:
    """Serves the saved AJAX payloads to the crawler's real extraction JS."""

    def __init__(self, real_page, pages, title="Vinyl & CD - Free U.S. Shipping"):
        self._real_page = real_page
        self._pages = pages
        self._title = title
        self.goto_url = None

    async def goto(self, url, timeout=None):
        self.goto_url = url
        await self._real_page.set_content(
            "<html><head></head><body></body></html>", wait_until="domcontentloaded"
        )
        await self._real_page.evaluate(_INSTALL_FETCH_STUB_JS, self._pages)

    async def title(self):
        return self._title

    async def evaluate(self, script, arg=None):
        return await self._real_page.evaluate(script, arg)

    async def fetched_urls(self):
        return await self._real_page.evaluate("() => window.__fetched")


@pytest.fixture
def window_pages():
    return json.loads((FIXTURES / "vinyl_window.json").read_text(encoding="utf-8"))


@pytest.fixture
def fake_page(browser_page, window_pages):
    return _FakePage(browser_page, window_pages)


async def test_crawl_catalog_yields_parsable_rows_and_dedupes_across_pages(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]

    # Page 1 has 9 rows; the no-artist and two no-price rows are skipped -> 6.
    # Page 2's only row repeats page 1's first album id -> 0.
    # Pages 3 and 4 are empty. Page 5 adds the Kevin Morby row plus the
    # Encore row that recurs from page 1's no-price skip, now priced -> 2.
    # Total 8.
    assert len(items) == 8
    titles = {item["title"] for item in items}
    assert "Mystery Record (LP)" not in titles
    assert "Priceless Pressing (LP)" not in titles
    # The duplicate on page 2 must not overwrite page 1's price.
    louder_now = [i for i in items if i["title"].startswith("Louder Now")]
    assert len(louder_now) == 1
    assert louder_now[0]["price"] == 36.98


async def test_crawl_catalog_yields_a_skipped_row_when_it_recurs_with_a_price(fake_page):
    # A row skipped on page 1 for having no price must still be eligible when the
    # same album recurs later with one -- a failed parse must not reserve the id.
    items = [item async for item in Crawler().crawl_catalog(fake_page)]
    encore = [i for i in items if i["artist"] == "Encore"]
    assert len(encore) == 1
    assert encore[0]["price"] == 21.98


async def test_crawl_catalog_requests_every_page_in_the_window(fake_page):
    [item async for item in Crawler().crawl_catalog(fake_page)]

    fetched = await fake_page.fetched_urls()
    assert len(fetched) == 5
    for page_num in range(1, 6):
        assert any(f"page={page_num}&" in url for url in fetched)


async def test_crawl_catalog_builds_the_full_item_contract(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Son Volt")

    assert item["title"] == "Sound Signal Serenades (LP)"
    assert item["format"] == "LP"
    assert item["price"] == 3.99
    assert item["currency"] == "USD"
    assert item["url"] == (
        "https://www.amoeba.com/sound-signal-serenades-lp-son-volt/albums/4495703/"
    )
    assert item["cover_image_url"] == "https://www.amoeba.com/sized-images/sv.jpg"


async def test_crawl_catalog_reads_both_used_label_wordings(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]

    assert next(i for i in items if i["artist"] == "Son Volt")["price"] == 3.99
    assert next(i for i in items if i["artist"] == "Arca")["price"] == 5.99


async def test_crawl_catalog_prefers_new_price_when_a_row_has_both(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]
    item = next(i for i in items if i["artist"] == "Lady Antebellum")
    assert item["price"] == 16.98


async def test_crawl_catalog_sets_format_from_the_title_suffix(fake_page):
    items = [item async for item in Crawler().crawl_catalog(fake_page)]
    by_artist = {i["artist"]: i for i in items}

    assert by_artist["The Army, The Navy"]["format"] == '7"'
    assert by_artist["Kevin Morby"]["format"] == '12"'
    assert by_artist["Neu!"]["format"] == "Vinyl"


async def test_crawl_catalog_paces_every_request(fake_page, monkeypatch):
    calls = []

    async def counting_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr("amoeba.sleep", counting_sleep)
    [item async for item in Crawler().crawl_catalog(fake_page)]

    assert len(calls) == 5
    assert all(seconds > 0 for seconds in calls)


async def test_crawl_catalog_navigates_to_the_category_page_first(fake_page):
    [item async for item in Crawler().crawl_catalog(fake_page)]
    assert fake_page.goto_url == "https://www.amoeba.com/music/cd-and-vinyl"


async def test_crawl_catalog_raises_on_cloudflare_block_page(browser_page, window_pages):
    blocked = _FakePage(
        browser_page, window_pages, title="Attention Required! | Cloudflare"
    )

    with pytest.raises(BotDetectedError):
        [item async for item in Crawler().crawl_catalog(blocked)]


async def test_crawl_catalog_raises_when_the_ajax_endpoint_is_blocked(browser_page):
    # No payload for any page number -> the stub returns 403.
    blocked_ajax = _FakePage(browser_page, {})

    with pytest.raises(BotDetectedError):
        [item async for item in Crawler().crawl_catalog(blocked_ajax)]


async def test_crawl_catalog_warns_when_a_page_comes_back_short(fake_page, caplog):
    with caplog.at_level("WARNING", logger="amoeba"):
        [item async for item in Crawler().crawl_catalog(fake_page)]

    short_page_warnings = [
        r for r in caplog.records if "expected 200" in r.getMessage()
    ]
    # Fixture pages 1-4 all hold fewer than 200 rows; page 5 is the last page
    # in the window and is not expected to be full.
    assert len(short_page_warnings) == 4
    assert "page 1" in short_page_warnings[0].getMessage()
