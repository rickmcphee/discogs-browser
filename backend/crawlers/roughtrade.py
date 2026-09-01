import json
import math
import random
import re
import time
import unicodedata
from asyncio import sleep
from typing import Optional

from crawler import BotDetectedError, clean_search_text
from logging_config import get_logger

log = get_logger("crawlers.roughtrade")

_CHALLENGE_TITLES = ("just a moment", "attention required", "checking your browser")

_SETTLE_TIMEOUT_MS = 15_000

# schema.org availability can arrive as a full URL ("https://schema.org/OutOfStock")
# or a bare token; only the tail is meaningful. PreOrder/BackOrder stay purchasable
# -- Rough Trade trades heavily in pre-orders, and a pre-order price is exactly
# what a wantlist watcher wants to see.
_UNAVAILABLE_RE = re.compile(r"(outofstock|soldout|discontinued)\s*$", re.IGNORECASE)

_META_PRICE_PROPS = ("product:price:amount", "og:price:amount")
_META_CURRENCY_PROPS = ("product:price:currency", "og:price:currency")

# One round trip: every JSON-LD script body plus the OG price metas. All
# parsing happens in Python -- the page only hands over raw strings.
_EXTRACT_SIGNALS_JS = """
() => {
  const ldjson = Array.from(
    document.querySelectorAll('script[type="application/ld+json"]')
  ).map((s) => s.textContent);
  const meta = {};
  for (const prop of [
    'product:price:amount', 'product:price:currency',
    'og:price:amount', 'og:price:currency',
  ]) {
    const el = document.querySelector(`meta[property="${prop}"]`);
    meta[prop] = el ? el.getAttribute('content') : null;
  }
  return {ldjson, meta};
}
"""


def _finite_price(value) -> Optional[float]:
    # float() accepts "nan"/"inf" text without raising, and neither is a
    # price; a NaN would sort first and reach the DOUBLE PRECISION column
    # (same rationale as discogs_marketplace._finite_price).
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    # Apostrophes contract rather than hyphenate ("What's" -> "whats"),
    # matching the convention the confirmed slugs follow.
    text = re.sub(r"['’]", "", text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _norm_words(text: str) -> list:
    """Lowercased alphanumeric words, diacritics folded -- the comparison
    space in which slugs, page titles, and release fields all meet."""
    return _slugify(text).split("-") if text else []


def _product_nodes(ldjson_texts: list) -> list:
    """Every schema.org Product node across the page's JSON-LD scripts --
    top-level objects, list roots, and @graph members alike."""
    nodes = []
    for raw in ldjson_texts:
        try:
            data = json.loads(raw or "")
        except (TypeError, ValueError):
            continue
        roots = data if isinstance(data, list) else [data]
        for root in roots:
            if not isinstance(root, dict):
                continue
            graph = root.get("@graph")
            candidates = [root] + (graph if isinstance(graph, list) else [])
            for node in candidates:
                if not isinstance(node, dict):
                    continue
                node_type = node.get("@type")
                types = node_type if isinstance(node_type, list) else [node_type]
                if any(t == "Product" for t in types if isinstance(t, str)):
                    nodes.append(node)
    return nodes


def _iter_offers(offers) -> list:
    if isinstance(offers, list):
        return [o for o in offers if isinstance(o, dict)]
    if isinstance(offers, dict):
        return [offers]
    return []


def _offer_listing(offer: dict, url: str) -> Optional[dict]:
    availability = offer.get("availability")
    if isinstance(availability, str) and _UNAVAILABLE_RE.search(availability):
        return None
    # AggregateOffer carries lowPrice instead of price; when both somehow
    # exist, price is the offer's own and wins.
    price = _finite_price(offer.get("price"))
    if price is None:
        price = _finite_price(offer.get("lowPrice"))
    if price is None:
        return None
    currency = offer.get("priceCurrency")
    return {
        "url": url,
        "price": price,
        "shipping": None,
        "currency": currency if isinstance(currency, str) and currency else "USD",
        "condition": None,
    }


class Crawler:
    site_name: str = "Rough Trade"
    base_url: str = "https://www.roughtrade.com"
    genre_summary: str = (
        "Legendary independent record store, London-born with US shops -- new "
        "vinyl across every genre with heavy exclusive and limited-edition coverage."
    )

    # Not an id dependency (the discogs_id is never read) but an input-quality
    # gate: eligibility for stock-item fan-out would point slug construction
    # at other stores' storefront title strings ("Album -- LP - Rainbow Road"),
    # which all but guarantee a 404 -- paced requests to a carefully-treated
    # site for near-zero yield. Discogs release titles are the clean inputs
    # the slug guess actually works from.
    requires_discogs_release: bool = True

    # [] from this crawler is only ever a confirmed answer: a 404 on every
    # candidate URL, a slug that resolved to some other product, or a product
    # page whose offers are all out of stock. A page that answered but could
    # not be read raises instead, so the circuit breaker still hears about
    # genuine breakage directly -- the discogs_marketplace separation, earned
    # the same way.
    empty_result_is_expected: bool = True

    @classmethod
    def _candidate_urls(cls, release: dict) -> list:
        """Product-page URL guesses, most likely first, never a search.

        robots.txt disallows */search/ (and every other enumeration path) for
        general-purpose clients while leaving product pages open, so the only
        compliant lookup is constructing the product URL from the release's
        own fields. See 2026-09-01-rough-trade-crawler-design.md.
        """
        raw_artist = release.get("artist", "")
        raw_title = release.get("title", "")
        urls = []
        # The &->and substitution runs on the raw fields: clean_search_text
        # itself drops '&' (a URL-special char), so it must not get there first.
        for a, t in (
            (raw_artist, raw_title),
            (raw_artist.replace("&", " and "), raw_title.replace("&", " and ")),
        ):
            artist_slug = _slugify(clean_search_text(a))
            title_slug = _slugify(clean_search_text(t))
            if not (artist_slug and title_slug):
                continue
            url = f"{cls.base_url}/en-us/product/{artist_slug}/{title_slug}"
            if url not in urls:
                urls.append(url)
        return urls

    @classmethod
    def search_url(cls, release: dict) -> str:
        candidates = cls._candidate_urls(release)
        return candidates[0] if candidates else f"{cls.base_url}/en-us"

    @staticmethod
    def _title_matches(page_title: str, artist: str, title: str) -> bool:
        """Did the slug land on this release's product page?

        Confirmed page titles start "{Artist} - {Title}..." with the product
        name sometimes truncated mid-word, so the check is: artist words as a
        prefix, then the leading title words (up to three -- enough to reject
        a different product, short enough to survive both the page title's own
        truncation and Rough Trade suffixing edition text onto the name).
        """
        page_words = _norm_words(page_title)
        artist_words = _norm_words(artist)
        title_words = _norm_words(title)[:3]
        if not page_words or not artist_words or not title_words:
            return False
        if page_words[: len(artist_words)] != artist_words:
            return False
        rest = page_words[len(artist_words):]
        return rest[: len(title_words)] == title_words

    async def _settled_title(self, page) -> str:
        # Cloudflare's interstitial always renders first, so a title read at
        # domcontentloaded says "Just a moment..." on every challenged request
        # including the ones about to clear (discogs_marketplace pattern).
        deadline = time.monotonic() + _SETTLE_TIMEOUT_MS / 1000
        title = await page.title()
        while any(c in title.lower() for c in _CHALLENGE_TITLES):
            if time.monotonic() >= deadline:
                return title
            await page.wait_for_timeout(500)
            title = await page.title()
        return title

    async def search(self, release: dict, page) -> list[dict]:
        artist = clean_search_text(release.get("artist", ""))
        title = clean_search_text(release.get("title", ""))
        candidates = self._candidate_urls(release)
        if not candidates:
            return []
        log.info("[Rough Trade] probing %d candidate URL(s) for: %s - %s",
                 len(candidates), artist, title)

        try:
            for url in candidates:
                response = await page.goto(url, wait_until="domcontentloaded")
                await sleep(random.uniform(1, 2))

                page_title = await self._settled_title(page)
                if any(c in page_title.lower() for c in _CHALLENGE_TITLES):
                    raise BotDetectedError(f"challenge did not clear on {url}")

                status = response.status if response else None
                if status == 404:
                    log.debug("[Rough Trade] 404 for %s", url)
                    continue

                if not self._title_matches(page_title, artist, title):
                    if status is not None and status >= 400:
                        # An error status whose settled page is neither a
                        # challenge nor this product is the Cloudflare wall
                        # (or an outage), never a product answer.
                        raise BotDetectedError(f"HTTP {status} on {url}")
                    # The slug resolved, but to something else -- a soft-404
                    # page or a different product. A miss, never a parse
                    # attempt against the wrong page.
                    log.debug("[Rough Trade] title %r does not match %s - %s",
                              page_title, artist, title)
                    continue
                # A matching product title trumps the status: a cleared
                # challenge reloads the real page while goto's response object
                # still holds the interstitial's 403.

                listings = await self._read_listings(page, url)
                if listings is None:
                    raise RuntimeError(
                        f"no Product JSON-LD or price meta recognised on {url} "
                        f"(page title {page_title!r}) -- the machine-readable "
                        f"price signals this crawler depends on have drifted; "
                        f"re-check {__name__} against the live page"
                    )
                if listings:
                    log.info("[Rough Trade] %d offer(s) for %s - %s, cheapest %s %s",
                             len(listings), artist, title,
                             listings[0]["currency"], listings[0]["price"])
                else:
                    log.info("[Rough Trade] %s - %s listed but not purchasable", artist, title)
                return listings

            return []
        finally:
            # Best-effort, and deliberately the one swallow in here: a live
            # page otherwise keeps running its scripts on the shared context
            # through the inter-request delay, but this must never replace
            # the failure being reported (amazon.py's rationale verbatim).
            try:
                await page.goto("about:blank")
            except Exception:
                pass

    async def _read_listings(self, page, url: str) -> Optional[list]:
        """Listings from the page's machine-readable signals, cheapest first.

        None means no signal was recognised at all (the caller raises);
        [] means signals were read and nothing is purchasable. Visible price
        text is never scraped -- a free-text amount on a product page is as
        likely to belong to a recommendation carousel as to the product
        (amazon.py's buybox scoping exists for exactly that reason).
        """
        signals = await page.evaluate(_EXTRACT_SIGNALS_JS)
        listings = []
        saw_signal = False
        for node in _product_nodes(signals.get("ldjson") or []):
            for offer in _iter_offers(node.get("offers")):
                # A signal is an offer actually *understood* -- priced, or
                # deliberately marked unpurchasable. An available offer whose
                # price cannot be read counts as neither: if no offer on the
                # page does better, the crawl raises rather than reporting a
                # confirmed miss off a page it only half-parsed.
                availability = offer.get("availability")
                if isinstance(availability, str) and _UNAVAILABLE_RE.search(availability):
                    saw_signal = True
                    continue
                listing = _offer_listing(offer, url)
                if listing:
                    saw_signal = True
                    listings.append(listing)

        if not listings and not saw_signal:
            meta = signals.get("meta") or {}
            price = next(
                (p for p in (_finite_price(meta.get(k)) for k in _META_PRICE_PROPS) if p),
                None,
            )
            if price is not None:
                saw_signal = True
                currency = next(
                    (meta.get(k) for k in _META_CURRENCY_PROPS if meta.get(k)), None
                )
                listings.append({
                    "url": url,
                    "price": price,
                    "shipping": None,
                    "currency": currency or "USD",
                    "condition": None,
                })

        if not saw_signal:
            return None
        listings.sort(key=lambda x: x["price"])
        return listings
