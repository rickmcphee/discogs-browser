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


# --- Page-reading tests -----------------------------------------------------
#
# A real headless browser loads a saved fixture via set_content() (no
# navigation, no live site), mirroring test_sideonedummyrecords_crawler.py.
# These cover the behaviour the crawler previously got wrong: it read the DOM
# at domcontentloaded without waiting for anything, and reported every page it
# could not parse as "no listings" -- which the crawl manager acts on by
# clearing the release's stored price.

import asyncio
import re
from pathlib import Path

import httpx
import pytest
import respx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

import crawlers.discogs_marketplace as dm
from crawler import BotDetectedError

FIXTURES = Path(__file__).parent / "fixtures" / "crawlers" / "discogs_marketplace"
LISTED_TITLE = "Rick Astley - Never Gonna Give You Up | Releases for Sale | Discogs"
RELEASE = {"discogs_id": "r249504", "artist": "Rick Astley", "title": "Never Gonna Give You Up"}


@pytest.fixture
async def browser_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


class _FakePage:
    """Wraps a real page so goto() loads a fixture, and scripts the title.

    `titles` is consumed one entry per title() call, the last entry sticking --
    that is what lets a test drive Cloudflare's interstitial being replaced by
    the real page a moment later, which is the case the old crawler could never
    see because it read the title once and immediately gave up.
    """

    def __init__(self, real_page, fixture, titles=(LISTED_TITLE,)):
        self._real = real_page
        self._html = (FIXTURES / fixture).read_text(encoding="utf-8")
        self._titles = list(titles)
        self.waits = 0

    async def goto(self, url, wait_until=None):
        await self._real.set_content(self._html, wait_until="domcontentloaded")

    async def title(self):
        return self._titles[0] if len(self._titles) == 1 else self._titles.pop(0)

    async def wait_for_timeout(self, ms):
        self.waits += 1

    async def wait_for_selector(self, selector, timeout=None, state=None):
        # set_content() has already painted everything the fixture will ever
        # have, so presence is decided once rather than really waiting out
        # `timeout` -- otherwise the "markup not recognised" tests would each
        # burn the full 15s.
        if await self._real.locator(selector).count() == 0:
            raise PlaywrightTimeoutError(f"selector not found in fixture: {selector}")

    def locator(self, selector):
        return self._real.locator(selector)


async def test_returns_every_usa_listing_cheapest_first(browser_page):
    page = _FakePage(browser_page, "usa_listings.html")
    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [6.50, 9.25, 12.99]
    assert results[0]["currency"] == "USD"
    assert results[0]["shipping"] == 3.00
    assert "Near Mint" in results[0]["condition"]


async def test_cheapest_is_chosen_here_not_taken_from_page_order(browser_page):
    """The fixture's rows are deliberately not price-ascending.

    `sort=price,asc` in the search URL has never been confirmed against the
    live page, so a crawler that trusted it would report $12.99 -- the first
    row -- as the cheapest listing.
    """
    page = _FakePage(browser_page, "usa_listings.html")
    results = await Crawler().search(RELEASE, page)

    assert results[0]["price"] == 6.50


async def test_free_shipping_row_keeps_a_null_shipping(browser_page):
    page = _FakePage(browser_page, "usa_listings.html")
    results = await Crawler().search(RELEASE, page)

    assert next(r for r in results if r["price"] == 9.25)["shipping"] is None


async def test_reported_url_is_the_search_url_not_the_landed_url(browser_page):
    page = _FakePage(browser_page, "usa_listings.html")
    results = await Crawler().search(RELEASE, page)

    assert results[0]["url"] == Crawler.search_url(RELEASE)


async def test_unverified_card_markup_raises_rather_than_being_guessed_at(browser_page, monkeypatch):
    """A restyle to cards is unsupported on purpose.

    An earlier revision tried to support it speculatively, scoping card rows
    to `[class*='marketplace']`. That container is the whole app in any
    plausible layout, so a recommendation card nested under it parsed as a
    listing and -- the cheapest winning -- became the release's price. The
    real markup cannot be checked from here, and a guessed selector that
    half-works is worse than none: this raises instead, naming what to fix.
    """
    async def _stats(release_id):
        return 124

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "card_listings.html")

    with pytest.raises(RuntimeError, match="markup not recognised"):
        await Crawler().search(RELEASE, page)


async def test_rendered_empty_state_is_an_honest_no_match(browser_page):
    page = _FakePage(browser_page, "no_usa_listings.html")

    assert await Crawler().search(RELEASE, page) == []


async def test_unreadable_page_raises_when_copies_are_for_sale(browser_page, monkeypatch):
    """The bug this crawler shipped with.

    Unrecognised markup used to return [], which the crawl manager reads as
    "the site answered and has nothing" -- clearing the release's stored price
    and counting the miss toward the circuit breaker. It has to raise instead.
    """
    async def _stats(release_id):
        return 124

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "redesigned.html")

    with pytest.raises(RuntimeError, match="markup not recognised"):
        await Crawler().search(RELEASE, page)


async def test_unreadable_page_is_a_no_match_when_nothing_is_for_sale(browser_page, monkeypatch):
    """A release with no copies anywhere renders no listings region either.

    Raising here would cool the whole site off for 30 minutes over a run of
    obscure records, so Discogs's own count is what settles it.
    """
    async def _stats(release_id):
        return 0

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "redesigned.html")

    assert await Crawler().search(RELEASE, page) == []


async def test_unreadable_page_raises_when_the_stats_api_will_not_answer(browser_page, monkeypatch):
    async def _stats(release_id):
        return None

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "redesigned.html")

    with pytest.raises(RuntimeError, match="unknown"):
        await Crawler().search(RELEASE, page)


async def test_interstitial_that_clears_is_not_treated_as_bot_detection(browser_page):
    page = _FakePage(browser_page, "usa_listings.html",
                     titles=["Just a moment...", "Just a moment...", LISTED_TITLE])
    results = await Crawler().search(RELEASE, page)

    assert results[0]["price"] == 6.50
    assert page.waits == 2


async def test_interstitial_that_never_clears_raises_bot_detected(browser_page, monkeypatch):
    monkeypatch.setattr(dm, "_SETTLE_TIMEOUT_MS", 50)
    page = _FakePage(browser_page, "usa_listings.html", titles=["Just a moment..."])

    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


@respx.mock
async def test_release_num_for_sale_reads_the_marketplace_stats_api():
    respx.get("https://api.discogs.com/marketplace/stats/249504").mock(
        return_value=httpx.Response(200, json={"num_for_sale": 124, "blocked_from_sale": False})
    )

    assert await dm._release_num_for_sale("249504") == 124


@respx.mock
async def test_release_num_for_sale_returns_none_when_the_api_errors():
    respx.get("https://api.discogs.com/marketplace/stats/249504").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )

    assert await dm._release_num_for_sale("249504") is None


def test_empty_result_is_expected_so_a_confirmed_miss_does_not_trip_the_breaker():
    """Only safe because unreadable pages now raise rather than returning [].

    With both outcomes collapsed into [], the crawl manager could only guess,
    and it guessed "broken" -- cooling Discogs off over releases that genuinely
    have no USA seller.
    """
    assert Crawler.empty_result_is_expected is True


async def test_listings_that_render_after_navigation_are_waited_for(browser_page):
    """The actual regression: content that is not in the DOM at goto() time.

    Every other fixture here is fully painted by the time set_content()
    returns, so none of them can tell a crawler that waits from one that reads
    straight through -- which is precisely the bug. Here goto() lands an empty
    shell and the listings are injected a moment later, so search() only sees
    them if it genuinely waits.
    """
    body = re.search(r"<body>(.*)</body>",
                     (FIXTURES / "usa_listings.html").read_text(encoding="utf-8"),
                     re.S).group(1)

    class _LateRenderPage(_FakePage):
        async def goto(self, url, wait_until=None):
            await self._real.set_content(
                "<html><head><title>x</title></head><body><div id='shell'></div></body></html>",
                wait_until="domcontentloaded",
            )
            asyncio.create_task(self._render_later())

        async def _render_later(self):
            await asyncio.sleep(0.25)
            await self._real.evaluate(
                "html => { document.getElementById('shell').innerHTML = html; }", body
            )

        async def wait_for_selector(self, selector, timeout=None, state=None):
            # The real wait, not the presence shortcut -- that shortcut is what
            # makes the other tests unable to catch this.
            await self._real.wait_for_selector(selector, timeout=timeout, state=state)

    results = await Crawler().search(RELEASE, _LateRenderPage(browser_page, "usa_listings.html"))

    assert [r["price"] for r in results] == [6.50, 9.25, 12.99]


async def test_recommendation_carousel_price_is_not_mistaken_for_a_listing(browser_page):
    """A carousel price sits outside the listings container and must stay there.

    Unscoped row selectors would match it, and since the cheapest wins it would
    become matches[0] -- overwriting the release with an unrelated price.
    """
    page = _FakePage(browser_page, "listings_with_carousel.html")
    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [18.00]


async def test_a_page_with_only_carousel_prices_raises_rather_than_guessing(browser_page, monkeypatch):
    async def _stats(release_id):
        return 124

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "carousel_only.html")

    with pytest.raises(RuntimeError, match="markup not recognised"):
        await Crawler().search(RELEASE, page)


async def test_a_browser_failure_is_not_laundered_into_a_confirmed_miss(browser_page, monkeypatch):
    """A dead page must not read as "the listings never rendered".

    Catching every exception around the readiness wait would fold a closed
    page or browser into that answer, and a stats lookup returning zero would
    then turn it into an empty result -- clearing the release's stored price
    on what was really a browser failure. The stats stub returns 0 here so
    that a broad catch would produce exactly that silent [].
    """
    async def _stats(release_id):
        return 0

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)

    class _DeadBrowserPage(_FakePage):
        async def wait_for_selector(self, selector, timeout=None, state=None):
            raise RuntimeError("Target page, context or browser has been closed")

    with pytest.raises(RuntimeError, match="browser has been closed"):
        await Crawler().search(RELEASE, _DeadBrowserPage(browser_page, "usa_listings.html"))


async def test_an_unparseable_row_does_not_hide_the_parseable_one_beside_it(browser_page):
    """A matched row that yields no price is skipped, not counted.

    Both rows match the container's selector -- the first prices a dash with
    no data-pricevalue and parses to nothing, the second carries the
    attribute. Treating "this selector matched rows" as "this selector found
    listings" would report the release as unreadable.
    """
    page = _FakePage(browser_page, "unparseable_then_parseable_rows.html")
    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [11.00]


async def test_a_carousel_nested_inside_the_container_is_not_a_listing(browser_page):
    """Scoping is to the listings table, not merely to the page region.

    The recommendations here sit inside `#pjax_container` alongside the real
    table, so container-level scoping alone would not exclude them -- at
    $0.99 the carousel entry would win on price and become the release's.
    """
    page = _FakePage(browser_page, "carousel_inside_container.html")
    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [24.00]


async def test_a_hidden_empty_state_does_not_end_the_wait_before_listings_render(browser_page, monkeypatch):
    """The destructive direction, and the reason empty states require :visible.

    A stale hidden no-items node is in the shell from the start and the
    listings arrive afterwards. Matching it while they were still rendering
    ends the readiness wait, reads as a confirmed miss, and returns the []
    that clears the release's stored price.
    """
    async def _stats(release_id):
        return 124

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    body = re.search(r"<body>(.*)</body>",
                     (FIXTURES / "usa_listings.html").read_text(encoding="utf-8"),
                     re.S).group(1)

    class _HiddenEmptyThenListings(_FakePage):
        async def goto(self, url, wait_until=None):
            await self._real.set_content(
                "<html><head><title>x</title></head><body>"
                "<div id='pjax_container'>"
                "<div class='marketplace_empty' style='display:none'>"
                "<h2>No items are available for this release</h2></div>"
                "</div><div id='shell'></div></body></html>",
                wait_until="domcontentloaded",
            )
            asyncio.create_task(self._render_later())

        async def _render_later(self):
            await asyncio.sleep(0.25)
            await self._real.evaluate(
                "html => { document.getElementById('shell').innerHTML = html; }", body
            )

        async def wait_for_selector(self, selector, timeout=None, state=None):
            await self._real.wait_for_selector(selector, timeout=timeout, state=state)

    results = await Crawler().search(RELEASE, _HiddenEmptyThenListings(browser_page, "usa_listings.html"))

    assert [r["price"] for r in results] == [6.50, 9.25, 12.99]


async def test_empty_state_text_outside_the_container_is_not_a_confirmed_miss(browser_page, monkeypatch):
    """An unrelated "No items for sale?" in a footer is not an answer."""
    async def _stats(release_id):
        return 124

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "empty_text_outside_container.html")

    with pytest.raises(RuntimeError, match="markup not recognised"):
        await Crawler().search(RELEASE, page)


async def test_a_price_less_row_cannot_end_the_readiness_wait_early(browser_page, monkeypatch):
    """The readiness invariant: every row selector carries a price node.

    A bare "tbody tr" would be satisfied by the placeholder row present from
    the start, so the wait would return before the real listings arrived and
    the crawl would raise on a page that was merely still rendering.
    """
    async def _stats(release_id):
        return 124

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    body = re.search(r"<body>(.*)</body>",
                     (FIXTURES / "usa_listings.html").read_text(encoding="utf-8"),
                     re.S).group(1)

    class _PlaceholderThenListings(_FakePage):
        async def goto(self, url, wait_until=None):
            await self._real.set_content(
                "<html><head><title>x</title></head><body>"
                "<div id='pjax_container'><table><tbody><tr><td>Loading&hellip;</td></tr></tbody></table></div>"
                "<div id='shell'></div></body></html>",
                wait_until="domcontentloaded",
            )
            asyncio.create_task(self._render_later())

        async def _render_later(self):
            await asyncio.sleep(0.25)
            await self._real.evaluate(
                "html => { document.getElementById('shell').innerHTML = html; }", body
            )

        async def wait_for_selector(self, selector, timeout=None, state=None):
            await self._real.wait_for_selector(selector, timeout=timeout, state=state)

    results = await Crawler().search(RELEASE, _PlaceholderThenListings(browser_page, "usa_listings.html"))

    assert [r["price"] for r in results] == [6.50, 9.25, 12.99]


async def test_both_price_shapes_in_one_table_are_read_together(browser_page):
    """A cheaper row must not be skipped for using the other price shape.

    Emitting one selector per price shape read this table with whichever
    selector matched first, so the $7.00 row -- priced by attribute rather
    than by the legacy cell -- was never considered, and the crawl persisted
    the $40.00 one as the cheapest listing.
    """
    page = _FakePage(browser_page, "mixed_price_shapes.html")
    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [7.00, 40.00]


@respx.mock
async def test_a_non_integer_num_for_sale_is_unknown_not_zero():
    """int(False) and int(0.5) are both 0.

    Coercing them would turn an API schema change into a confirmed
    no-listings result -- the one answer that lets search() return the empty
    result which clears a stored price.
    """
    for malformed in (False, 0.5, "124", None):
        respx.get("https://api.discogs.com/marketplace/stats/249504").mock(
            return_value=httpx.Response(200, json={"num_for_sale": malformed})
        )
        assert await dm._release_num_for_sale("249504") is None


@respx.mock
async def test_a_real_zero_num_for_sale_is_still_a_confirmed_miss():
    respx.get("https://api.discogs.com/marketplace/stats/249504").mock(
        return_value=httpx.Response(200, json={"num_for_sale": 0})
    )

    assert await dm._release_num_for_sale("249504") == 0


async def test_a_container_whose_rows_all_fail_to_parse_falls_through(browser_page):
    """The other half of keying on parsed listings: the container loop.

    `table.mpitems` is tried first and its only row prices a dash, so it
    yields nothing; the listing is reached by the `#pjax_container table`
    selector after it. Stopping at the first container that matched any row
    would raise instead.
    """
    page = _FakePage(browser_page, "unparseable_container_then_next.html")
    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [15.00]


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "-3.00", "not-a-price", "", None])
def test_attribute_price_rejects_anything_that_is_not_a_price(raw):
    assert dm._attribute_price(raw) is None


@pytest.mark.parametrize("raw,expected", [("14.00", 14.00), ("0", 0.0), ("1024.99", 1024.99)])
def test_attribute_price_accepts_a_real_price(raw, expected):
    assert dm._attribute_price(raw) == expected


async def test_malformed_price_attributes_do_not_abandon_the_other_rows(browser_page):
    """`data-pricevalue` is site-controlled text.

    Letting float() raise on it would abandon every remaining row over one
    bad cell -- including the cheapest listing. NaN is worse than a raise: it
    sorts into matches[0] and reaches the DOUBLE PRECISION price column,
    where it also breaks JSON serialisation downstream.

    The NaN row here carries a readable $99.99 in its text and falls back to
    it; the negative and non-numeric rows have no readable text either and
    are skipped; the well-formed $14.00 row is unaffected.
    """
    page = _FakePage(browser_page, "malformed_price_attributes.html")
    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [14.00, 99.99]
