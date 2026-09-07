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
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

import crawlers.discogs_marketplace as dm
from crawler import BotDetectedError

FIXTURES = Path(__file__).parent / "fixtures" / "crawlers" / "discogs_marketplace"
LISTED_TITLE = "Rick Astley - Never Gonna Give You Up | Releases for Sale | Discogs"
RELEASE = {"discogs_id": "r249504", "artist": "Rick Astley", "title": "Never Gonna Give You Up"}


@pytest.fixture(autouse=True)
def short_listings_deadline(monkeypatch):
    """Readiness polls to a deadline now, so the no-listings cases would each
    wait out the real 15s. Long enough to outlast the late-render fixtures'
    injections, short enough to keep the file quick."""
    monkeypatch.setattr(dm, "_LISTINGS_TIMEOUT_MS", 1000)


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
        # A sequence is consumed one entry per goto(), the last sticking --
        # the same shape as `titles`, and what lets a test give the filtered
        # and unfiltered reads of one release different markup.
        fixtures = [fixture] if isinstance(fixture, str) else list(fixture)
        self._htmls = [(FIXTURES / f).read_text(encoding="utf-8") for f in fixtures]
        self._titles = list(titles)
        self.waits = 0
        self.wait_until = None
        self.urls = []
        # What goto() answers with; None stands in for Playwright's
        # same-document case, which the crawler has to tolerate anyway. A
        # list is consumed one entry per goto() (last sticking) like the
        # fixtures, so a test can make one of several reads unclean.
        self.response = None

    async def goto(self, url, wait_until=None, timeout=None):
        self.wait_until = wait_until
        self.urls.append(url)
        html = self._htmls[0] if len(self._htmls) == 1 else self._htmls.pop(0)
        await self._real.set_content(html, wait_until="domcontentloaded")
        if isinstance(self.response, list):
            return self.response[0] if len(self.response) == 1 else self.response.pop(0)
        return self.response

    async def title(self):
        return self._titles[0] if len(self._titles) == 1 else self._titles.pop(0)

    async def wait_for_timeout(self, ms):
        self.waits += 1
        await asyncio.sleep(ms / 1000)

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


async def test_no_usa_sellers_is_not_reported_as_broken_markup(browser_page, monkeypatch):
    """The failure this crawler reported for r37054242.

    `num_for_sale` is a worldwide count and the page is filtered to United
    States sellers, so a release pressed for Europe with all its copies still
    in Europe rendered an empty page the crawler could not name, then had the
    non-zero worldwide count hold that up as proof of stale selectors. Reading
    the release unfiltered shows the same selectors parsing fine, which leaves
    only the filter to explain the first page.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html", "unrecognised_empty_state.html"],
    )

    assert await Crawler().search(RELEASE, page) == []


async def test_the_second_read_drops_the_ships_from_filter_and_nothing_else(browser_page, monkeypatch):
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html", "unrecognised_empty_state.html"],
    )
    await Crawler().search(RELEASE, page)

    assert "ships_from" in page.urls[0]
    assert "ships_from" not in page.urls[1]
    assert page.urls[1] == "https://www.discogs.com/sell/release/249504?sort=price%2Casc"
    assert page.urls[2] == page.urls[0], "the empty filtered page must be confirmed on its own URL"


async def test_a_real_restyle_still_raises_because_neither_read_parses(browser_page, monkeypatch):
    """The check must not become a way for stale selectors to pass unnoticed.

    A restyle breaks the unfiltered page exactly as it breaks the filtered
    one, so the complaint stands and the breaker still hears about it.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, ["redesigned.html", "redesigned.html"])

    with pytest.raises(RuntimeError, match="markup not recognised"):
        await Crawler().search(RELEASE, page)


async def test_an_interstitial_on_the_second_read_raises_bot_detected(browser_page, monkeypatch):
    """The pool keys its recovery on the exception type.

    `_paced_search()` resets the browser context and retries on
    `BotDetectedError`; laundering a challenge on this read into the markup
    complaint would spend that recovery and blame the selectors for an edge
    that was merely challenging us. It cannot produce the destructive empty
    result either way.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html"],
        titles=(LISTED_TITLE, "Just a moment..."),
    )

    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


async def test_a_stalled_second_navigation_raises_the_playwright_timeout(browser_page, monkeypatch):
    """`_process_claimed_rows` discards this crawler's context only for a
    Playwright timeout, so a second navigation that stalls has to escape as
    one -- otherwise the dead socket pool is inherited by every job after
    it, which is the failure `_discard_context` was added to stop."""
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, ["unrecognised_empty_state.html", "usa_listings.html"])

    real_goto = page.goto

    async def _goto(url, wait_until=None, timeout=None):
        if "ships_from" not in url:
            raise PlaywrightTimeoutError("Timeout 30000ms exceeded")
        return await real_goto(url, wait_until=wait_until, timeout=timeout)

    page.goto = _goto

    with pytest.raises(PlaywrightTimeoutError):
        await Crawler().search(RELEASE, page)


async def test_a_block_page_is_not_explained_away_by_a_readable_unfiltered_page(
    browser_page, monkeypatch
):
    """A later request's success does not vouch for an earlier block.

    A 4xx or a `cf-mitigated` response whose title missed the challenge list
    rendered no listings for a reason unrelated to the filter, so reading
    "no USA sellers" off the *second* request would turn a block into a
    confirmed miss and clear the stored price -- with the unfiltered page
    parsing perfectly, which is what makes this the destructive direction.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, ["unrecognised_empty_state.html", "usa_listings.html"])
    page.response = SimpleNamespace(status=403, headers={"cf-mitigated": "challenge"})

    with pytest.raises(RuntimeError, match="HTTP 403, cf-mitigated=challenge"):
        await Crawler().search(RELEASE, page)


async def test_a_clean_response_is_required_before_the_filter_can_explain_an_empty_page(
    browser_page, monkeypatch
):
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, ["unrecognised_empty_state.html", "usa_listings.html"])
    page.response = SimpleNamespace(status=503, headers={})

    with pytest.raises(RuntimeError, match="was not consulted"):
        await Crawler().search(RELEASE, page)

    assert len(page.urls) == 1, "an unclean response should not spend a second page load"


async def test_a_slow_filtered_page_is_not_mistaken_for_an_absence_of_sellers(
    browser_page, monkeypatch
):
    """`_read_when_ready` reports an exhausted deadline and unknown markup
    identically, so a clean 200 whose listings were merely late looks exactly
    like "no USA sellers" -- and the unfiltered read, made moments later
    against a site that has since sped up, would have vouched for it. The
    second filtered read finds the listings instead, which beats both the
    empty result that would have cleared a real USA price and the raise.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html", "usa_listings.html"],
    )

    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [6.50, 9.25, 12.99]


async def test_the_confirming_read_wants_no_listings_not_a_recognised_empty_state(
    browser_page, monkeypatch
):
    """Demanding a *recognised* empty state here would retire the fix.

    The premise of this whole path is that Discogs's empty state is markup
    this crawler cannot name -- if the confirming read had to recognise one,
    the very case this exists for could never reach the empty result, and
    every import-only release would go on raising. What must reproduce is the
    absence of listings from a clean response, which `redesigned.html` (clean,
    unrecognised, no listings) is.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html", "redesigned.html"],
    )

    assert await Crawler().search(RELEASE, page) == []


async def test_an_unclean_unfiltered_response_cannot_vouch_for_the_filter(
    browser_page, monkeypatch
):
    """The verification read is held to the standard the first read is.

    Its body is an error page that happens to carry a container we parse; a
    read whose own response was a block cannot be evidence for the empty
    result any more than the first response could.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html", "unrecognised_empty_state.html"],
    )
    page.response = [
        None,
        SimpleNamespace(status=403, headers={"cf-mitigated": "challenge"}),
        None,
    ]

    with pytest.raises(RuntimeError, match="was unreadable too"):
        await Crawler().search(RELEASE, page)


async def test_an_unclean_confirming_read_cannot_produce_the_empty_result(
    browser_page, monkeypatch
):
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html", "unrecognised_empty_state.html"],
    )
    page.response = [None, None, SimpleNamespace(status=503, headers={})]

    with pytest.raises(RuntimeError, match="never confirmed"):
        await Crawler().search(RELEASE, page)


async def test_a_cleared_challenge_still_answers_despite_its_stale_error_status(
    browser_page,
):
    """`page.goto()` reports the navigation it started, and a challenge clears
    by reloading -- so the 403 Cloudflare served with the interstitial stays
    on that response while the DOM underneath becomes the real page. Judging
    the page by that stale status would throw away the correct answer on
    exactly the case `_await_settled_title` exists to serve, and on this site
    that case is routine rather than exotic.
    """
    page = _FakePage(
        browser_page, "no_usa_listings.html",
        titles=["Just a moment...", "Just a moment...", LISTED_TITLE],
    )
    page.response = SimpleNamespace(status=403, headers={"cf-mitigated": "challenge"})

    assert await Crawler().search(RELEASE, page) == []
    assert len(page.urls) == 1, "a believable page needs no corroboration"


async def test_parsed_listings_survive_a_stale_challenge_status(browser_page):
    page = _FakePage(
        browser_page, "usa_listings.html",
        titles=["Just a moment...", LISTED_TITLE],
    )
    page.response = SimpleNamespace(status=403, headers={"cf-mitigated": "challenge"})

    results = await Crawler().search(RELEASE, page)

    assert [r["price"] for r in results] == [6.50, 9.25, 12.99]


async def test_a_block_page_that_never_showed_a_challenge_cannot_answer_empty(
    browser_page, monkeypatch
):
    """The counterpart: an unclean response we never watched a challenge clear
    on is an error body, and one that happens to carry markup we recognise
    would clear the release's stored price. It falls through to the
    corroborating reads instead of answering here.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "no_usa_listings.html")
    page.response = SimpleNamespace(status=403, headers={"cf-mitigated": "challenge"})

    with pytest.raises(RuntimeError, match="was not consulted"):
        await Crawler().search(RELEASE, page)


async def test_rows_whose_prices_will_not_parse_are_breakage_not_an_empty_shelf(
    browser_page, monkeypatch
):
    """`_read_listings()` yields nothing both when a page has no listing rows
    and when it has rows whose prices it cannot read, and only the second is
    breakage. A price shape this crawler no longer reads, on a filtered page
    whose unfiltered counterpart still parses, would otherwise be reported as
    "nothing ships from the USA" -- clearing the release's stored price and
    hiding the change that caused it.
    """
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["rows_with_unreadable_prices.html", "usa_listings.html",
         "rows_with_unreadable_prices.html"],
    )

    with pytest.raises(RuntimeError, match="price shape this crawler no longer reads"):
        await Crawler().search(RELEASE, page)


async def test_a_page_with_no_listing_rows_at_all_is_still_an_empty_shelf(
    browser_page, monkeypatch
):
    """The other side of that distinction, so the new signal cannot quietly
    turn every confirmed miss into a raise."""
    async def _stats(release_id):
        return 20

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(
        browser_page,
        ["unrecognised_empty_state.html", "usa_listings.html", "unrecognised_empty_state.html"],
    )

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
        async def goto(self, url, wait_until=None, timeout=None):
            await self._real.set_content(
                "<html><head><title>x</title></head><body><div id='shell'></div></body></html>",
                wait_until="domcontentloaded",
            )
            asyncio.create_task(self._render_later())

        async def _render_later(self):
            await asyncio.sleep(0.05)
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

    Readiness is decided by reading the DOM, so a closed page or browser
    surfaces from the locator calls themselves. Swallowing those would leave
    the poll returning "nothing rendered", and a stats lookup returning zero
    would then turn it into an empty result -- clearing the release's stored
    price on what was really a browser failure. The stats stub returns 0 here
    so that any such catch would produce exactly that silent [].
    """
    async def _stats(release_id):
        return 0

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)

    class _DeadBrowserPage(_FakePage):
        def locator(self, selector):
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
        async def goto(self, url, wait_until=None, timeout=None):
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
            await asyncio.sleep(0.05)
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
        async def goto(self, url, wait_until=None, timeout=None):
            await self._real.set_content(
                "<html><head><title>x</title></head><body>"
                "<div id='pjax_container'><table><tbody><tr><td>Loading&hellip;</td></tr></tbody></table></div>"
                "<div id='shell'></div></body></html>",
                wait_until="domcontentloaded",
            )
            asyncio.create_task(self._render_later())

        async def _render_later(self):
            await asyncio.sleep(0.05)
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


async def test_a_placeholder_row_does_not_end_readiness_before_real_listings(browser_page, monkeypatch):
    """Price-shaped is not the same as parseable.

    The shell holds a row whose price is a dash: it matches the row selector,
    so waiting for a selector to match would return immediately, find nothing
    parseable, and raise "markup not recognised" on a page whose real
    listings were still a moment away. Readiness polls until a row actually
    parses instead.
    """
    async def _stats(release_id):
        return 124

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    body = re.search(r"<body>(.*)</body>",
                     (FIXTURES / "usa_listings.html").read_text(encoding="utf-8"),
                     re.S).group(1)

    class _PlaceholderPriceThenListings(_FakePage):
        async def goto(self, url, wait_until=None, timeout=None):
            await self._real.set_content(
                "<html><head><title>x</title></head><body>"
                "<div id='pjax_container'><table class='mpitems'><tbody><tr>"
                "<td class='item_price'><span class='price'>&mdash;</span></td>"
                "</tr></tbody></table></div>"
                "<div id='shell'></div></body></html>",
                wait_until="domcontentloaded",
            )
            asyncio.create_task(self._render_later())

        async def _render_later(self):
            await asyncio.sleep(0.05)
            await self._real.evaluate(
                "html => { document.getElementById('shell').innerHTML = html; }", body
            )

    results = await Crawler().search(RELEASE, _PlaceholderPriceThenListings(browser_page, "usa_listings.html"))

    assert [r["price"] for r in results] == [6.50, 9.25, 12.99]


@respx.mock
@pytest.mark.parametrize("body", ["null", "[]", '"124"'])
async def test_a_non_object_stats_payload_is_unknown_not_an_exception(body):
    """`.get()` on a null or list body raises AttributeError.

    That escapes as a bare exception, losing both this helper's unknown path
    and the detailed RuntimeError search() raises from it.
    """
    respx.get("https://api.discogs.com/marketplace/stats/249504").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )

    assert await dm._release_num_for_sale("249504") is None


async def test_navigation_resolves_at_commit_and_leaves_readiness_to_the_polls(browser_page):
    """Pins the wait_until choice. domcontentloaded only fires once the whole
    document has arrived and its synchronous scripts have run, so a response
    that started but never finished burned the full navigation window and
    surfaced as a bare "Timeout 30000ms exceeded" -- indistinguishable from a
    connection that was never answered. Readiness is decided by the
    settle-and-parse polls regardless, so navigation itself only has to
    establish that a response arrived."""
    page = _FakePage(browser_page, "usa_listings.html")

    results = await Crawler().search(RELEASE, page)

    assert page.wait_until == "commit"
    assert results[0]["price"] == 6.50


async def test_an_unparsed_title_is_waited_on_rather_than_read_as_a_real_page(browser_page, monkeypatch):
    """Resolving at commit means <title> may not be parsed on the first read.
    An empty title contains no challenge phrase, so it used to pass the bot
    check -- and a challenge page read at that instant went on to the listings
    poll, to be reported as unrecognised markup or, as here, to read whatever
    rendered behind it as a real page. Empty is unsettled; the challenge that
    follows it is still bot detection."""
    monkeypatch.setattr(dm, "_SETTLE_TIMEOUT_MS", 1600)
    page = _FakePage(browser_page, "usa_listings.html",
                     titles=["", "", "Just a moment...", "Just a moment..."])

    with pytest.raises(BotDetectedError):
        await Crawler().search(RELEASE, page)


async def test_a_navigation_that_never_gets_a_response_raises_the_playwright_timeout(browser_page, caplog):
    """No headers inside the window is the one thing a commit timeout can
    mean. It has to surface as the Playwright timeout itself -- the crawl
    manager keys its context discard on that type -- never as [] (which
    clears the stored price) nor as BotDetectedError (an immediate retry a
    stall gives no reason to expect to fare better)."""
    class _StalledPage(_FakePage):
        async def goto(self, url, wait_until=None, timeout=None):
            raise PlaywrightTimeoutError(f"Page.goto: Timeout {timeout}ms exceeded.")

    with caplog.at_level(logging.WARNING, logger="crawlers.discogs_marketplace"), \
         pytest.raises(PlaywrightTimeoutError):
        await Crawler().search(RELEASE, _StalledPage(browser_page, "usa_listings.html"))

    assert any("no response" in r.getMessage() for r in caplog.records)


async def test_the_http_status_is_named_when_markup_is_not_recognised(browser_page, monkeypatch):
    """The status and Cloudflare's mitigation header are the difference
    between "Discogs restyled the page" and "the edge served a block page
    with a title that isn't on the challenge list", and the raise is the
    only place an operator sees either."""
    async def _stats(release_id):
        return 53

    monkeypatch.setattr(dm, "_release_num_for_sale", _stats)
    page = _FakePage(browser_page, "redesigned.html")
    page.response = SimpleNamespace(status=403, headers={"cf-mitigated": "challenge"})

    with pytest.raises(RuntimeError, match="HTTP 403, cf-mitigated=challenge"):
        await Crawler().search(RELEASE, page)
