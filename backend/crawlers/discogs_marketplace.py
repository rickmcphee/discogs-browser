import math
import re
import time
import urllib.parse
from typing import Optional

import httpx

from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("crawlers.discogs_marketplace")

_AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")

_USER_AGENT = "DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser"

# Cloudflare serves an interstitial first and swaps in the real page once its
# JS challenge clears, so the title is only meaningful after it settles.
_CHALLENGE_TITLES = ("just a moment", "attention required", "checking your browser")

# Only containers Discogs is actually known to use: `table.mpitems` is the
# listings table, and `#pjax_container table` is what this crawler originally
# shipped with. Nothing speculative -- an earlier revision guessed at
# restyled markup with `[class*='marketplace']` and card rows, and the guess
# could not be scoped: that container is the whole app in any plausible
# layout, so a recommendation card nested under it parses as a listing and,
# because the cheapest wins, becomes the release's price. A guessed selector
# that half-works is worse here than none, and the live DOM cannot be
# verified from a sandbox Cloudflare challenges on every request. A restyle
# that moves listings out of these containers raises, which is the outcome
# this crawler is built around: loud, with the release id and page title in
# the message, rather than a wrong price shown as fact.
_LISTING_CONTAINERS = ("table.mpitems", "#pjax_container table")

# Readiness is decided by _read_when_ready() parsing a row, not by any of
# these merely matching, so there is no bare "[data-pricevalue]" anywhere: a
# stray price element cannot stand in for a listing at any stage.
#
# Every row shape carries a price node _parse_row() reads -- the legacy
# `td.item_price .price` node (whose price may predate data-pricevalue) or
# the attribute itself -- so a selector cannot match a row it has no way to
# parse. A bare "tbody tr" would break the readiness invariant below: an
# unrelated or half-rendered row inside the container would satisfy the wait
# while the listings were still on their way.
# One selector per container accepting either price shape, rather than one
# per shape: _read_listings() stops at the first selector that parses
# anything, so splitting them means a table holding both a legacy-priced row
# and a data-pricevalue-only row would be read by the first selector alone --
# and if the row it skipped was the cheaper one, that is the price the crawl
# persists.
_ROW_SELECTORS = tuple(
    f"{container} tbody tr:has(td.item_price .price, [data-pricevalue])"
    for container in _LISTING_CONTAINERS
)

# The listings region rendering with nothing in it is a real answer ("nothing
# ships from the USA"), and has to be told apart from the region never
# rendering at all. Written as :has-text() rather than the text= engine so it
# can be comma-joined with the row selectors into one wait -- otherwise a
# genuine no-listings page burns the full listings timeout before anything
# recognises it, on what is a common path.
#
# Scoped to the container and required to be :visible, because this is the
# destructive direction: an unrelated or hidden "no items for sale" string
# anywhere on the page would otherwise end the wait and be read as a
# confirmed miss, clearing the release's stored price while the listings were
# still rendering.
_EMPTY_STATE = tuple(
    f"#pjax_container {state}:visible"
    for state in (
        ".marketplace_empty",
        ":is(h1,h2,h3,p,strong):has-text('No items are available')",
        ":is(h1,h2,h3,p,strong):has-text('There are no items for sale')",
        ":is(h1,h2,h3,p,strong):has-text('No items for sale')",
    )
)

_SETTLE_TIMEOUT_MS = 15_000
_LISTINGS_TIMEOUT_MS = 15_000
_POLL_INTERVAL_MS = 250


def _finite_price(value: Optional[float]) -> Optional[float]:
    """None unless `value` is usable as a price.

    float() accepts "nan"/"inf"/"-inf" and negative numeric text without
    raising, and none of those is a price. A NaN in particular would sort
    into matches[0] and reach the DOUBLE PRECISION price column, where it
    also breaks JSON serialisation downstream -- sideonedummyrecords.py
    rejects them for the same reason."""
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return value


def _attribute_price(raw: Optional[str]) -> Optional[float]:
    """`data-pricevalue` is site-controlled text, so a malformed one makes the
    row unparseable rather than the crawl a failure. Letting float() raise
    would abandon every remaining row over one bad cell -- including rows
    that parse perfectly well, and including the cheapest listing."""
    if not raw:
        return None
    try:
        return _finite_price(float(raw))
    except ValueError:
        return None


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
        # Type-checked rather than coerced. int(False) and int(0.5) are both
        # 0, so a schema change would arrive here looking exactly like a
        # confirmed "nothing for sale" -- the one answer that lets search()
        # return the empty result that clears a stored price. A value that
        # isn't a plain integer is an unknown, not a zero.
        if isinstance(value, bool) or not isinstance(value, int):
            log.warning(
                "[Discogs] marketplace stats for release %s returned a non-integer num_for_sale: %r",
                release_id, value,
            )
            return None
        return value
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

        listings, recognised = await self._read_when_ready(page, url)
        if listings:
            best = listings[0]
            log.info(
                "[Discogs] release %s: %d USA-shipping listing(s), cheapest %s %s",
                discogs_id, len(listings), best.get("currency"), best.get("price"),
            )
            return listings

        if recognised:
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

    async def _read_when_ready(self, page, url: str):
        """Poll until a row actually parses or a visible empty state renders.

        Returns (listings, recognised); recognised is False only if the
        deadline passed with neither.

        Waiting for a price-shaped node to attach would be weaker than what
        the caller needs. _parse_row() rejects a matched row whose price is a
        placeholder or malformed, so a skeleton row satisfies that weaker
        wait while the real listings are still on their way -- and the crawl
        then raises "markup not recognised" on a page that was about to be
        perfectly readable. Waiting is the point, so wait for the thing that
        is actually wanted.
        """
        deadline = time.monotonic() + _LISTINGS_TIMEOUT_MS / 1000
        while True:
            listings = await self._read_listings(page, url)
            if listings:
                return listings, True
            if await self._empty_state_rendered(page):
                return [], True
            if time.monotonic() >= deadline:
                return [], False
            await page.wait_for_timeout(_POLL_INTERVAL_MS)

    async def _read_listings(self, page, url: str) -> list[dict]:
        """Every listing row the page rendered, cheapest first.

        Sorted here rather than trusting the `sort=price,asc` URL parameter --
        that parameter has never been confirmed against the live page, and a
        silently ignored sort would otherwise make "the first row" masquerade
        as "the cheapest listing"."""
        # Keyed on parsed listings, not on a selector merely matching. Two
        # things follow: a row that matches but yields no price is skipped
        # rather than counted, and a container whose rows all fail to parse
        # advances to the next one instead of shadowing it. Stopping at the
        # first selector that matched anything would raise on a page whose
        # listings a later container would have found.
        listings = []
        for selector in _ROW_SELECTORS:
            rows = page.locator(selector)
            for i in range(await rows.count()):
                parsed = await self._parse_row(rows.nth(i), url)
                if parsed:
                    listings.append(parsed)
            if listings:
                break
        if not listings:
            return []

        listings.sort(key=lambda x: (x["price"] is None, x["price"] or 0.0, x["shipping"] or 0.0))
        return listings

    async def _parse_row(self, row, url: str) -> Optional[dict]:
        price_el = row.locator("[data-pricevalue], td.item_price .price").first
        if not await price_el.count():
            return None

        currency = await price_el.get_attribute("data-currency")
        price = _attribute_price(await price_el.get_attribute("data-pricevalue"))
        if price is None:
            # Falls through on a malformed attribute rather than only on a
            # missing one: the rendered text is a second reading of the same
            # price, and is worth trying before giving the row up.
            price = _finite_price(_parse_amount(await price_el.inner_text()))
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
