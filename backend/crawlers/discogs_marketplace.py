import math
import re
import time
import urllib.parse
from collections import namedtuple
from typing import Optional

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from crawler import BotDetectedError
from logging_config import get_logger

log = get_logger("crawlers.discogs_marketplace")

_AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")

# `candidates` is separate from `recognised` because a page whose listing rows
# match `_ROW_SELECTORS` but whose prices none of them yield is not the same
# page as one with no listing rows at all -- the first is a price shape this
# crawler no longer reads, the second is an absence of sellers. Collapsing them
# lets a changed price format be reported as "nothing ships from the USA",
# which clears the release's stored price instead of naming the breakage.
# `answered` is whether the response itself could be believed at all.
_Read = namedtuple("_Read", "listings recognised answered candidates")

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
# rendering at all. Recognising one ends the readiness poll immediately, so a
# genuine no-listings page does not sit out the full timeout -- a common path
# for a library with obscure records.
#
# Scoped to the container and required to be :visible, because this is the
# destructive direction: an unrelated or hidden "no items for sale" string
# anywhere on the page would otherwise be read as a confirmed miss, clearing
# the release's stored price while the listings were still rendering.
_EMPTY_STATE = tuple(
    f"#pjax_container {state}:visible"
    for state in (
        ".marketplace_empty",
        ":is(h1,h2,h3,p,strong):has-text('No items are available')",
        ":is(h1,h2,h3,p,strong):has-text('There are no items for sale')",
        ":is(h1,h2,h3,p,strong):has-text('No items for sale')",
    )
)

# Navigation resolves at `commit` -- response headers received, document
# committed -- not at `domcontentloaded`. Readiness is decided by the polls
# below either way (the settled title, then a parsed row or a rendered empty
# state), so DCL bought nothing; what it cost was diagnosis. DCL only fires
# once the whole document has arrived and its synchronous scripts have run,
# so a response that had started but not finished -- a slow origin, or an
# edge stalling a client it has scored as a bot -- burned the entire window
# and surfaced as a bare "Timeout 30000ms exceeded", indistinguishable from a
# connection that was never answered at all. With `commit`, a timeout means
# exactly one thing: no response headers within the window.
_NAVIGATION_TIMEOUT_MS = 30_000
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


def _response_is_clean(response) -> bool:
    """Whether a response is sound enough to read a destructive conclusion from.

    None is Playwright's same-document case, which carries no status to
    doubt. Anything else has to have answered without an error status and
    without Cloudflare marking it as mitigated: an error body that happens to
    contain a container we recognise would otherwise be read as a listings
    page or an empty state, and the empty result clears a stored price.
    """
    if response is None:
        return True
    if response.status >= 400:
        return False
    return not response.headers.get("cf-mitigated")


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
            payload = r.json()
            # A successful response whose body is null, a list, or anything
            # else non-object would raise AttributeError on .get() and escape
            # as a bare exception, losing both this helper's unknown path and
            # the detailed RuntimeError search() raises from it.
            value = payload.get("num_for_sale") if isinstance(payload, dict) else None
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


def _is_challenge_title(title: str) -> bool:
    return any(c in title.lower() for c in _CHALLENGE_TITLES)


async def _await_settled_title(page):
    """Wait out Cloudflare's interstitial and return the page's settled title.

    The challenge is always what renders first, so reading the title straight
    after navigation sees "Just a moment..." on every challenged request --
    including the ones that would have cleared on their own a few seconds
    later.

    An empty title is unsettled too, not evidence of a real page: navigation
    resolves at `commit`, before the parser has necessarily reached <title>,
    and a challenge read at that instant would otherwise pass the bot check
    and go on to be reported as unrecognised markup.

    Returns (title, challenge_cleared). The second half is what lets a caller
    trust a page whose *response* looks bad: `page.goto()` reports the
    navigation it started, and a challenge that clears does so by reloading,
    so the 403 or 503 Cloudflare served with the interstitial stays on that
    response while the DOM underneath becomes the real page. Having watched
    the title turn from a challenge into a real one is the evidence that this
    is what happened, and it is the only signal here that separates a cleared
    challenge from a block page that simply never said so. An empty title
    that fills in is not that -- it is just the parser catching up -- so only
    a title that was actually a challenge counts."""
    deadline = time.monotonic() + _SETTLE_TIMEOUT_MS / 1000
    challenge_seen = False
    title = await page.title()
    while not title.strip() or _is_challenge_title(title):
        challenge_seen = challenge_seen or _is_challenge_title(title)
        if time.monotonic() >= deadline:
            return title, False
        await page.wait_for_timeout(500)
        title = await page.title()
    return title, challenge_seen


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

    @classmethod
    def unfiltered_url(cls, release_id: str) -> str:
        """The same release's marketplace page with no `ships_from` filter.

        Differs from search_url() in that one parameter and nothing else, so
        reading it answers exactly one question -- whether the filter is what
        emptied the page -- rather than several at once."""
        query = urllib.parse.urlencode({"sort": "price,asc"})
        return f"https://www.discogs.com/sell/release/{release_id}?{query}"

    async def search(self, release: dict, page) -> list[dict]:
        discogs_id = release["discogs_id"]
        release_id = discogs_id[1:]
        url = self.search_url(release)
        try:
            response = await page.goto(url, wait_until="commit", timeout=_NAVIGATION_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # Re-raised as it is rather than wrapped or read as bot detection.
            # Nothing arrived, so nothing here can say why; the crawl manager
            # discards this crawler's browser context on a Playwright timeout
            # (keyed on the exception type) so the next request starts on a
            # fresh connection, and the breaker counts it as the failure it
            # is. Raising BotDetectedError instead would spend another full
            # window on an immediate retry that a stall gives no reason to
            # expect to fare better.
            log.warning(
                "[Discogs] no response from %s within %ds for release %s -- the "
                "connection stalled before any headers arrived, so this is not a "
                "challenge page that failed to clear",
                url, _NAVIGATION_TIMEOUT_MS // 1000, discogs_id,
            )
            raise
        # None for a same-document navigation; never expected here, but the
        # Playwright contract allows it.
        status = response.status if response is not None else None
        mitigated = response.headers.get("cf-mitigated") if response is not None else None
        if status is not None and status >= 400:
            log.info(
                "[Discogs] HTTP %s for release %s (cf-mitigated=%s); waiting for the page to settle",
                status, discogs_id, mitigated,
            )

        title, challenge_cleared = await _await_settled_title(page)
        if _is_challenge_title(title):
            log.warning(
                "[Discogs] bot interstitial did not clear for release %s (HTTP %s, cf-mitigated=%s)",
                discogs_id, status, mitigated,
            )
            raise BotDetectedError()
        trustworthy = _response_is_clean(response) or challenge_cleared

        listings, recognised, candidates = await self._read_when_ready(page, url)
        if listings:
            best = listings[0]
            log.info(
                "[Discogs] release %s: %d USA-shipping listing(s), cheapest %s %s",
                discogs_id, len(listings), best.get("currency"), best.get("price"),
            )
            return listings

        # Rows that settled without yielding a price are direct evidence that
        # this release has USA listings the crawler can no longer read, and
        # they outrank every empty answer below -- the stats API reporting
        # zero, or a confirming read that happens to find no rows, would
        # otherwise clear the release's stored price with the contradiction
        # in plain sight. Checked after the listings above, so rows that were
        # merely mid-render when first seen still return their prices.
        if candidates:
            raise RuntimeError(
                f"Discogs listings markup not recognised for release {discogs_id} "
                f"(HTTP {status}, cf-mitigated={mitigated}, page title {title!r}). "
                f"The filtered page's listing rows would not yield a price, so this "
                f"is a price shape this crawler no longer reads rather than an "
                f"absence of USA sellers. Re-check the selectors in {__name__} "
                f"against {url}"
            )

        # An empty state is only an answer if this page can be believed. On a
        # response that was neither clean nor a challenge we watched clear,
        # it falls through to the checks below instead of returning here --
        # an error body carrying markup we happen to recognise would
        # otherwise clear the release's stored price, and those checks demand
        # far better evidence than one page's DOM. Parsed listings are not
        # gated the same way: they are positive data, they cannot erase
        # anything, and refusing them would throw away the correct answer
        # every time a challenge cleared -- the case this crawler is built
        # around.
        if recognised and trustworthy:
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

        # Copies existing is not the same as copies existing *under this
        # crawl's filter*, and conflating the two is what made this raise on
        # pages it had read perfectly well. `num_for_sale` is a worldwide
        # count -- the stats API has no `ships_from` parameter -- while the
        # page just read was narrowed to United States sellers. A release
        # pressed for Europe with every copy still in Europe therefore lands
        # here on a page that rendered an honest "nothing matches", and the
        # worldwide count, being non-zero, reported it as broken markup. It
        # can never do otherwise: the zero branch above is reachable only for
        # a release with no copies anywhere on earth.
        #
        # What settles it is re-reading the same release unfiltered with the
        # same selectors. Recognising that page proves the markup is still
        # understood, which leaves the filter as the only thing that could
        # have emptied the first one. Not recognising it means the selectors
        # really have gone stale, and the complaint below stands.
        # Two things have to hold before an empty page can be blamed on the
        # filter, and each needs its own read. The unfiltered page must parse,
        # which proves the selectors still work; and the filtered page must
        # render empty *again*, which proves its first empty rendering was
        # what the site had rather than a slow one caught mid-flight. Neither
        # is worth asking unless this response was itself clean -- a 4xx/5xx,
        # or a `cf-mitigated` body whose title missed the challenge list,
        # rendered no listings for a reason that has nothing to do with the
        # filter, and letting a later request's success speak for it would
        # turn a block page into "no USA sellers".
        verdict = (
            "The unfiltered page was not consulted: this response was too unclean for "
            "an empty page to be attributed to the ships_from filter"
        )
        if trustworthy:
            unfiltered = await self._verify_read(
                page, self.unfiltered_url(release_id),
                f"checking whether the ships_from filter explains the empty page for {discogs_id}",
            )
            if not unfiltered.recognised:
                verdict = (
                    "The same release read without the ships_from filter "
                    "was unreadable too"
                )
            else:
                # `_read_when_ready` reports an exhausted deadline and
                # unknown markup identically, so the first read cannot tell
                # "no USA sellers" from "this page was slow just now". Only a
                # second filtered read separates them -- and if the listings
                # were merely late, it finds them, which beats both the empty
                # result and the raise.
                confirm = await self._verify_read(
                    page, url, f"confirming the empty filtered page for {discogs_id}",
                )
                listings = confirm.listings
                if listings:
                    log.info(
                        "[Discogs] release %s: %d USA-shipping listing(s) on a second "
                        "read, cheapest %s %s -- the first read was early, not empty",
                        discogs_id, len(listings),
                        listings[0].get("currency"), listings[0].get("price"),
                    )
                    return listings
                elif confirm.candidates:
                    # Listing rows are there; their prices are what this
                    # crawler could not read. That is a price shape it no
                    # longer understands, not an absence of USA sellers, and
                    # the two must not share an answer -- reporting the first
                    # as the second clears the release's stored price and
                    # hides the breakage that caused it.
                    verdict = (
                        "The unfiltered page parsed, but the filtered page's listing "
                        "rows would not yield a price on either read, so this is a "
                        "price shape this crawler no longer reads rather than an "
                        "absence of USA sellers"
                    )
                # With rows ruled out, what has to reproduce is the *absence
                # of listings*, not a recognised empty state. The premise of
                # this whole path is that Discogs's empty state is one this
                # crawler cannot name -- demanding it here would mean the
                # case this exists for could never reach the empty result at
                # all. Two independent windows finding no listing rows,
                # either side of an unfiltered read that parsed, is what
                # separates "no USA sellers" from "slow just then";
                # recognising the real empty markup would settle it at the
                # first read and retire this path entirely.
                elif confirm.answered:
                    log.info(
                        "[Discogs] no USA-shipping listings for release %s (%s for sale "
                        "worldwide, the unfiltered page parsed and the filtered page "
                        "rendered no listings twice, so the ships_from filter emptied "
                        "it rather than stale selectors)",
                        discogs_id,
                        f"{num_for_sale} copies" if num_for_sale is not None
                        else "an unknown number of copies",
                    )
                    return []
                else:
                    verdict = (
                        "The unfiltered page parsed, but the confirming re-read of the "
                        "filtered page did not answer cleanly, so its empty state was "
                        "never confirmed"
                    )

        raise RuntimeError(
            f"Discogs listings markup not recognised for release {discogs_id} "
            f"(HTTP {status}, cf-mitigated={mitigated}, page title {title!r}, "
            f"{num_for_sale if num_for_sale is not None else 'unknown'} "
            f"copies for sale per the marketplace API). {verdict}. "
            f"Re-check the selectors in {__name__} against {url}"
        )

    async def _verify_read(self, page, url: str, what: str):
        """Navigate to `url` again and read it with the same selectors.

        Returns a `_Read`. `answered` is whether the response itself could
        be believed and the document actually parsed; `recognised`
        additionally requires the DOM to have rendered something known; and
        `candidates` is whether listing rows were present at all, however
        unreadable their prices. They are separate because the
        callers want different strengths of evidence: proving the selectors
        still work needs a page we positively recognise, while confirming an
        absence of listings only needs a clean response that produced none --
        Discogs's empty state is markup this crawler cannot name, which is
        the whole reason this path exists. Neither is available from an
        unclean response: a block page or an error body cannot be evidence
        for the destructive empty result any more than the first response
        could, and may well contain a container we would parse.

        Stalls and interstitials are re-raised in the shapes the first read
        gives them, never folded into the caller's markup complaint, because
        the pool keys its recovery on the exception type. A Playwright
        timeout is what makes `CrawlManager._process_claimed_rows` discard
        this crawler's browser context, so a navigation that stalls here has
        to escape as one or the dead socket pool is inherited by every job
        after it; and `BotDetectedError` is what makes `_paced_search` reset
        the context and retry, which a challenge on this read deserves as
        much as a challenge on the first.
        """
        try:
            response = await page.goto(url, wait_until="commit", timeout=_NAVIGATION_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            log.warning(
                "[Discogs] no response from %s within %ds while %s",
                url, _NAVIGATION_TIMEOUT_MS // 1000, what,
            )
            raise


        title, challenge_cleared = await _await_settled_title(page)
        if _is_challenge_title(title):
            log.warning("[Discogs] bot interstitial did not clear on %s while %s", url, what)
            raise BotDetectedError()

        if not title.strip():
            # This module treats an empty title as unsettled everywhere else,
            # and a document that never got as far as its <title> cannot be
            # read as having no listings: that is the destructive answer, and
            # nothing here has actually parsed.
            log.warning(
                "[Discogs] %s never settled a title while %s, so nothing it rendered "
                "can stand as evidence", url, what,
            )
            return _Read([], False, False, False)

        if not (_response_is_clean(response) or challenge_cleared):
            log.warning(
                "[Discogs] %s answered HTTP %s (cf-mitigated=%s) while %s, so its markup "
                "cannot stand as evidence either way",
                url, response.status if response is not None else None,
                response.headers.get("cf-mitigated") if response is not None else None, what,
            )
            return _Read([], False, False, False)

        listings, recognised, candidates = await self._read_when_ready(page, url)
        return _Read(listings, recognised, True, candidates)

    async def _read_when_ready(self, page, url: str):
        """Poll until a row actually parses or a visible empty state renders.

        Returns (listings, recognised, candidates); recognised is False only
        if the deadline passed with neither, and candidates says whether the
        page had listing rows at all -- which is what separates a price shape
        this crawler can no longer read from an absence of sellers.

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
            listings, candidates = await self._read_listings(page, url)
            if listings:
                return listings, True, candidates
            if await self._empty_state_rendered(page):
                return [], True, candidates
            if time.monotonic() >= deadline:
                # The final observation rather than any made along the way: a
                # skeleton row that appears and is replaced should not leave
                # the settled page looking like it had listings.
                return [], False, candidates
            await page.wait_for_timeout(_POLL_INTERVAL_MS)

    async def _read_listings(self, page, url: str):
        """(listings, candidates): every listing row the page rendered,
        cheapest first, and whether it had listing rows at all.

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
        candidates = False
        for selector in _ROW_SELECTORS:
            rows = page.locator(selector)
            count = await rows.count()
            candidates = candidates or count > 0
            for i in range(count):
                parsed = await self._parse_row(rows.nth(i), url)
                if parsed:
                    listings.append(parsed)
            if listings:
                break
        if not listings:
            return [], candidates

        listings.sort(key=lambda x: (x["price"] is None, x["price"] or 0.0, x["shipping"] or 0.0))
        return listings, candidates

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
