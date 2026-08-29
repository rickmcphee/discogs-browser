import re
import time
import urllib.parse
from typing import Optional

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("crawlers.discogs_marketplace")

_AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")

_USER_AGENT = "DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser"

# Cloudflare serves an interstitial first and swaps in the real page once its
# JS challenge clears, so the title is only meaningful after it settles.
_CHALLENGE_TITLES = ("just a moment", "attention required", "checking your browser")

# Discogs has restyled this page before and will again. Each of these is a
# separate generation of the listings markup; `data-pricevalue` is the
# longest-lived hook among them because Discogs's own currency-switching JS
# reads it, so it is the one most likely to survive the next restyle.
#
# Every one of them is scoped to a marketplace listings container. An
# unscoped `tr:has([data-pricevalue])` would also match a recommendation
# carousel, and amazon.py already learned where that ends: a carousel price
# becoming matches[0] overwrites the release with an unrelated price. A
# restyle that moves listings out of all of these containers is meant to raise
# rather than be caught by a broader net.
_LISTING_CONTAINERS = ("#pjax_container", "table.mpitems", "[class*='marketplace']")

_ROW_SELECTORS = tuple(
    f"{container} {row}"
    for container in _LISTING_CONTAINERS
    for row in ("tbody tr", "tr:has([data-pricevalue])", "li:has([data-pricevalue])")
)

# The listings region rendering with nothing in it is a real answer ("nothing
# ships from the USA"), and has to be told apart from the region never
# rendering at all. Written as :has-text() rather than the text= engine so it
# can be comma-joined with the row selectors into one wait -- otherwise a
# genuine no-listings page burns the full listings timeout before anything
# recognises it, on what is a common path.
_EMPTY_STATE = (
    ".marketplace_empty",
    ":is(h1,h2,h3,p,strong):has-text('No items are available')",
    ":is(h1,h2,h3,p,strong):has-text('There are no items for sale')",
    ":is(h1,h2,h3,p,strong):has-text('No items for sale')",
)

# Deliberately no bare "[data-pricevalue]" here: a stray price element
# anywhere on the page would satisfy this wait before the listings themselves
# render, which is the immediate-read race this crawler exists to stop. Every
# row selector already contains the price node, so nothing is lost.
_PAGE_READ = ", ".join(_ROW_SELECTORS + _EMPTY_STATE)

_SETTLE_TIMEOUT_MS = 15_000
_LISTINGS_TIMEOUT_MS = 15_000


def _parse_amount(text: str) -> Optional[float]:
    if not text:
        return None
    match = _AMOUNT_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


async def _release_num_for_sale(release_id: str) -> Optional[int]:
    """How many copies Discogs itself says are for sale, or None if it won't say.

    Read from the public marketplace-stats API, which needs no auth and is not
    behind the Cloudflare challenge that guards the HTML page. This is only
    consulted when the page could not be read: it is what separates a release
    with genuinely nothing for sale (an honest empty result) from a page whose
    markup we no longer understand (a failure, which must raise)."""
    url = f"https://api.discogs.com/marketplace/stats/{release_id}?curr_abbr=USD"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
            r.raise_for_status()
            value = r.json().get("num_for_sale")
        return int(value) if value is not None else None
    except (httpx.HTTPError, ValueError, TypeError) as e:
        log.warning("[Discogs] marketplace stats lookup failed for release %s: %s", release_id, e)
        return None


async def _await_settled_title(page) -> str:
    """Wait out Cloudflare's interstitial and return the page's settled title.

    The challenge is always what renders first, so reading the title straight
    after domcontentloaded sees "Just a moment..." on every challenged request
    -- including the ones that would have cleared on their own a few seconds
    later."""
    deadline = time.monotonic() + _SETTLE_TIMEOUT_MS / 1000
    title = await page.title()
    while any(c in title.lower() for c in _CHALLENGE_TITLES):
        if time.monotonic() >= deadline:
            return title
        await page.wait_for_timeout(500)
        title = await page.title()
    return title


class Crawler:
    site_name: str = "Discogs"
    base_url: str = "https://www.discogs.com"
    requires_discogs_release: bool = True

    # Normally reserved for a single store rather than a near-universal
    # marketplace, because an empty result from a marketplace is good evidence
    # the crawler is broken. That inference is only needed while a crawler
    # cannot tell the two apart -- this one now can, and raises outright on a
    # page it could not read, so the breaker hears about breakage directly.
    # What is left over is a confirmed "nothing ships from the USA for this
    # release", which is an answer and not a fault: counting it cooled the
    # whole site off for 30 minutes over a run of obscure records.
    empty_result_is_expected: bool = True

    @classmethod
    def search_url(cls, release: dict) -> str:
        release_id = release["discogs_id"][1:]
        query = urllib.parse.urlencode({"ships_from": "United States", "sort": "price,asc"})
        return f"https://www.discogs.com/sell/release/{release_id}?{query}"

    async def search(self, release: dict, page) -> list[dict]:
        discogs_id = release["discogs_id"]
        release_id = discogs_id[1:]
        url = self.search_url(release)
        await page.goto(url, wait_until="domcontentloaded")

        title = await _await_settled_title(page)
        if any(c in title.lower() for c in _CHALLENGE_TITLES):
            log.warning("[Discogs] bot interstitial did not clear for release %s", discogs_id)
            raise BotDetectedError()

        recognised = await self._wait_until_page_read(page)

        listings = await self._read_listings(page, url) if recognised else []
        if listings:
            best = listings[0]
            log.info(
                "[Discogs] release %s: %d USA-shipping listing(s), cheapest %s %s",
                discogs_id, len(listings), best.get("currency"), best.get("price"),
            )
            return listings

        if recognised and await self._empty_state_rendered(page):
            log.info("[Discogs] no USA-shipping listings for release %s", discogs_id)
            return []

        # Nothing recognisable rendered. Returning [] here is what made this
        # crawler under-report: the caller reads an empty result as "the site
        # answered and has nothing" and clears whatever price it had already
        # found for the release, so a page we simply failed to parse erased
        # good data on every pass. Only claim an empty result when Discogs
        # itself confirms there is nothing to find.
        num_for_sale = await _release_num_for_sale(release_id)
        if num_for_sale == 0:
            log.info("[Discogs] release %s has no copies for sale at all", discogs_id)
            return []

        raise RuntimeError(
            f"Discogs listings markup not recognised for release {discogs_id} "
            f"(page title {title!r}, {num_for_sale if num_for_sale is not None else 'unknown'} "
            f"copies for sale per the marketplace API) -- re-check the selectors in "
            f"{__name__} against {url}"
        )

    async def _wait_until_page_read(self, page) -> bool:
        """Whether either listings or a recognised empty state ever rendered.

        Waiting is the point: the listings are not necessarily in the DOM at
        domcontentloaded, and reading straight through was what turned a page
        that had not finished rendering into "no listings".
        """
        # Only a timeout means "it never rendered". Catching every exception
        # would fold a closed page or a dead browser into that same answer, and
        # a confirmed-zero stats lookup would then turn it into an empty result
        # -- clearing the stored price on what was really a browser failure,
        # which is the whole class of bug this crawler was changed to stop.
        try:
            await page.wait_for_selector(_PAGE_READ, timeout=_LISTINGS_TIMEOUT_MS, state="attached")
            return True
        except PlaywrightTimeoutError:
            return False

    async def _read_listings(self, page, url: str) -> list[dict]:
        """Every listing row the page rendered, cheapest first.

        Sorted here rather than trusting the `sort=price,asc` URL parameter --
        that parameter has never been confirmed against the live page, and a
        silently ignored sort would otherwise make "the first row" masquerade
        as "the cheapest listing"."""
        rows = None
        for selector in _ROW_SELECTORS:
            candidate = page.locator(selector)
            if await candidate.count():
                rows = candidate
                break
        if rows is None:
            return []

        listings = []
        for i in range(await rows.count()):
            parsed = await self._parse_row(rows.nth(i), url)
            if parsed:
                listings.append(parsed)

        listings.sort(key=lambda x: (x["price"] is None, x["price"] or 0.0, x["shipping"] or 0.0))
        return listings

    async def _parse_row(self, row, url: str) -> Optional[dict]:
        price_el = row.locator("[data-pricevalue], td.item_price .price").first
        if not await price_el.count():
            return None

        currency = await price_el.get_attribute("data-currency")
        price_attr = await price_el.get_attribute("data-pricevalue")
        price = float(price_attr) if price_attr else _parse_amount(await price_el.inner_text())
        if price is None:
            return None

        shipping_el = row.locator(".item_shipping, [class*='shipping']").first
        shipping = _parse_amount(await shipping_el.inner_text()) if await shipping_el.count() else None

        condition_el = row.locator(".item_condition, [class*='condition']").first
        condition = (await condition_el.inner_text()).strip() if await condition_el.count() else None
        if condition:
            condition = re.sub(r"\s+", " ", condition)

        return {
            "url": url,
            "price": price,
            "shipping": shipping,
            "currency": currency,
            "condition": condition,
        }

    async def _empty_state_rendered(self, page) -> bool:
        # No exception handling on purpose, for the reason above: these
        # selectors are static and tested, so a locator error here is a browser
        # failure, and it has to reach the caller as one rather than becoming a
        # confirmed miss.
        for selector in _EMPTY_STATE:
            if await page.locator(selector).count():
                return True
        return False
