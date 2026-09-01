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

# Amount and currency read as namespace pairs, never mixed across them: a
# valid og: amount next to a stale product: currency must not combine into a
# price in the wrong currency.
_META_PAIRS = (
    ("product:price:amount", "product:price:currency"),
    ("og:price:amount", "og:price:currency"),
)

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


# Words that must never stand as a mid-word truncation fragment -- an
# unstripped format-marker remnant coinciding with a title word's prefix
# ("one".startswith("on")) would otherwise pass as truncation.
_SUFFIX_FRAGMENT_STOP = frozenset({"on", "vinyl", "lp", "cd"})

# Live page titles only truncate *long* product names -- the observed case
# cut around 35 characters. A name ending on a prefix word well short of
# that is not a truncation, it is a different, shorter title
# ("International Super" vs "International Superhits ..."), so the
# relaxation below requires the matched span to have plausibly hit the
# truncation length. Misses a name truncated shorter than this; that errs
# toward a miss, never a wrong product.
_TRUNCATION_MIN_CHARS = 30

# The trailing format marker a product-name core carries in confirmed page
# titles ("{Title} on Vinyl LP", "{Title} on CD").
_FORMAT_MARKER_RE = re.compile(r"\s+on\s+(vinyl(\s+lp)?|lp|cd)\s*$", re.IGNORECASE)


# Both confirmed places a page title states its format: the name core's
# trailing marker ("on Vinyl LP", "on CD") and the parenthesised later
# segments ("- (Vinyl LP)", "- (CD)", "- (LP - Rainbow Road)").
_TITLE_FORMAT_SIGNALS_RE = re.compile(
    r"\bon\s+(vinyl(?:\s+lp)?|lp|cd)\b|\(\s*(vinyl(?:\s+lp)?|lp|cd)\b",
    re.IGNORECASE,
)


def _page_format(title_rest: str) -> Optional[str]:
    """"vinyl", "cd", or None when the title's format signals are absent or
    contradict each other (unknown stays accepted downstream)."""
    signals = set()
    for m in _TITLE_FORMAT_SIGNALS_RE.finditer(title_rest):
        token = (m.group(1) or m.group(2)).lower()
        signals.add("cd" if token == "cd" else "vinyl")
    return signals.pop() if len(signals) == 1 else None


def _format_conflicts(page_format: Optional[str], release_format: str) -> bool:
    """Does the page title's format contradict the release's format?

    Only a *known* contradiction rejects -- vinyl release on a CD page or
    vice versa. An absent page format or an unrecognised release format
    string stays accepted.
    """
    if not page_format:
        return False
    fmt = (release_format or "").lower()
    release_is_cd = "cd" in fmt
    release_is_vinyl = any(t in fmt for t in ("vinyl", "lp", '12"', '10"', '7"'))
    if page_format == "cd" and release_is_vinyl and not release_is_cd:
        return True
    if page_format == "vinyl" and release_is_cd and not release_is_vinyl:
        return True
    return False


def _title_core_matches(expected: list, got: list) -> bool:
    """Does a page's product-name core name exactly this release?

    `got` is the name core -- delimiter/branding/format-marker segments
    already stripped -- so trailing words are a *sibling title* ("Greatest
    Hits Volume Two" for "Greatest Hits"), not edition noise: equality is
    required, word for word. The one relaxation is the documented mid-word
    truncation of live page titles ("...Start Your Ear Off R"): the final
    word may be a leading fragment of the final expected word, once the
    matched span has reached the length live titles truncate at.
    """
    if not expected or not got:
        return False
    if got == expected:
        return True
    if len(got) != len(expected) or got[:-1] != expected[:-1]:
        return False
    fragment = got[-1]
    if not fragment or fragment in _SUFFIX_FRAGMENT_STOP:
        return False
    if not expected[-1].startswith(fragment):
        return False
    return len(" ".join(got)) >= _TRUNCATION_MIN_CHARS


def _name_matches(name: str, artist: str, title: str) -> bool:
    """Does a Product node's name unambiguously describe this release?

    Stricter than the page-title check: every accepted node contributes
    offers, so a name merely *starting* with the title would let a sibling
    product ("Greatest Hits Volume Two" for release "Greatest Hits") supply
    the stored price. A segment of the " - "-delimited name must therefore
    equal the title exactly -- the bare title (with any " - " edition suffix
    after it), or the "{Artist} - {Title}[ - suffix]" shape. JSON-LD names
    are not length-truncated the way page titles are, so no truncation
    relaxation applies here.
    """
    title_words = _norm_words(title)
    if not title_words:
        return False
    segments = [_norm_words(s) for s in name.split(" - ")]
    if segments and segments[0] == title_words:
        return True
    return (
        len(segments) > 1
        and segments[0] == _norm_words(artist)
        and segments[1] == title_words
    )


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


def _node_scope(node: dict, product_path: str) -> str:
    """Where a Product node's own url/@id says it belongs.

    "match": it names this product's path (locale-full or locale-less);
    "other": it names some other path -- a recommendation node, whatever its
    name claims; "unknown": it carries neither field.
    """
    for key in ("url", "@id"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            clean = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
            return "match" if clean.endswith(product_path) else "other"
    return "unknown"


def _iter_offers(offers) -> list:
    if isinstance(offers, list):
        return [o for o in offers if isinstance(o, dict)]
    if isinstance(offers, dict):
        return [offers]
    return []


def _offer_unavailable(offer: dict) -> bool:
    """Is the offer deliberately marked unpurchasable?

    JSON-LD encodes availability as a string IRI, a node reference
    ({"@id": "https://schema.org/OutOfStock"}), or an array of either; every
    form must be read, or an out-of-stock offer with a price passes as
    purchasable.
    """
    value = offer.get("availability")
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, dict):
            item = item.get("@id")
        if isinstance(item, str) and _UNAVAILABLE_RE.search(item):
            return True
    return False


def _offer_listing(offer: dict, url: str) -> Optional[dict]:
    if _offer_unavailable(offer):
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


# A product page's title carries a format marker ("on Vinyl LP", "on CD",
# "(Vinyl LP)", "(LP)"). Site pages that merely carry the branding --
# "Access Denied - Rough Trade", "Privacy Choices - Rough Trade" -- do not.
_PRODUCT_TITLE_SHAPE_RE = re.compile(
    r"\bon\s+(vinyl|cd|lp)\b|\((vinyl|lp|cd|\d+\s*\")", re.IGNORECASE
)


def _recognized_non_match(page_title: str) -> bool:
    """Positive evidence that a mismatching 200 page is a *confirmed* miss.

    Either a not-found page, or a structurally valid Rough Trade *product*
    page for some other product -- the "{Artist} - {Name}" shape plus a
    format marker, so a generic site page ("Access Denied - Rough Trade")
    never qualifies. Anything else -- a maintenance page, a consent wall, an
    unrecognised layout -- is unclassifiable, and the caller raises instead
    of recording a miss that would clear a stored price.
    """
    lower = page_title.lower()
    if "not found" in lower or "404" in lower:
        return True
    return (
        " - " in page_title
        and "rough trade" in lower
        and bool(_PRODUCT_TITLE_SHAPE_RE.search(page_title))
    )


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
    def _title_matches(page_title: str, artist: str, title: str,
                       release_format: str = "") -> bool:
        """Did the slug land on this release's product page?

        Confirmed page titles are "{Artist} - {Title}[ format marker][ -
        edition/format segments]..." with literal " - " delimiters, so the
        artist is compared against the segment *before* the first delimiter,
        exactly -- normalizing the whole title first would let artist "Love"
        + title "Is" claim a "Love Is All - ..." page. The product-name core
        is then isolated -- everything up to the next " - " or " | ", with
        the trailing format marker stripped -- and must equal the release
        title word for word (see _title_core_matches): trailing words there
        are a sibling title ("... Volume Two"), not edition noise, and even
        with its JSON-LD filtered a sibling page's nameless node or OG metas
        could persist the wrong price.
        """
        artist_seg, sep, rest = page_title.partition(" - ")
        if not sep:
            return False
        artist_words = _norm_words(artist)
        if not artist_words or _norm_words(artist_seg) != artist_words:
            return False
        title_words = _norm_words(title)
        if not title_words:
            return False
        # A known cross-format landing -- a vinyl release's slug resolving to
        # the CD product, or vice versa -- is a different product, whatever
        # the name says: its price must not be persisted for this release.
        # Read from the whole post-artist title, since the format can sit in
        # the name core ("on CD") or a later segment ("- (CD)").
        if _format_conflicts(_page_format(rest), release_format):
            return False
        name_core = rest.split(" - ")[0].split(" | ")[0]
        name_core = _FORMAT_MARKER_RE.sub("", name_core)
        return _title_core_matches(title_words, _norm_words(name_core))

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

                # An absent format passes through as unknown -- forcing a
                # default would turn a formatless release's landing on its
                # own (say) CD page into a price-clearing miss.
                if not self._title_matches(page_title, artist, title,
                                           release.get("format") or ""):
                    if status is not None and status >= 400:
                        # An error status whose settled page is neither a
                        # challenge nor this product is the Cloudflare wall
                        # (or an outage), never a product answer.
                        raise BotDetectedError(f"HTTP {status} on {url}")
                    if not _recognized_non_match(page_title):
                        # A 200 whose title is neither this product, a
                        # not-found page, nor another Rough Trade product
                        # page (maintenance, consent wall, ...) cannot be
                        # classified -- and a miss here would clear a stored
                        # price with no site-health signal recorded, since
                        # this crawler's empty results bypass the breaker.
                        raise RuntimeError(
                            f"unrecognised page at {url} (page title "
                            f"{page_title!r}) -- neither this release's "
                            f"product page, a not-found page, nor another "
                            f"product; refusing to record a miss"
                        )
                    # The slug resolved, but to something else -- a soft-404
                    # page or a different product. A miss, never a parse
                    # attempt against the wrong page.
                    log.debug("[Rough Trade] title %r does not match %s - %s",
                              page_title, artist, title)
                    continue
                # A matching product title trumps the status: a cleared
                # challenge reloads the real page while goto's response object
                # still holds the interstitial's 403.

                listings = await self._read_listings(page, url, artist, title)
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

    async def _read_listings(self, page, url: str, artist: str, title: str) -> Optional[list]:
        """Listings from the page's machine-readable signals, cheapest first.

        None means no confirmed answer was read (the caller raises);
        [] means every observed offer is confirmed unpurchasable. Visible
        price text is never scraped -- a free-text amount on a product page
        is as likely to belong to a recommendation carousel as to the product
        (amazon.py's buybox scoping exists for exactly that reason).
        """
        signals = await page.evaluate(_EXTRACT_SIGNALS_JS)

        # A recommendation carousel can emit Product JSON-LD of its own, and
        # an unscoped read would let its cheapest offer become this release's
        # price -- the exact trap this crawler exists to avoid. Scoping, in
        # order of evidence: a node whose url/@id names this product path is
        # this page's product whatever it is called; one naming another path
        # is not, whatever it is called (the guard against a carousel node
        # for a same-titled album by someone else); a url-less node is read
        # only when its name matches the release. A nameless, url-less node
        # is unattributable -- used only as the page's sole Product node,
        # and *poisoning* the read otherwise, exactly like an unparsable
        # offer, rather than being merged in or silently dropped.
        product_path = "/" + "/".join(url.rstrip("/").split("/")[-3:])
        listings = []
        tallies = {"unavailable": 0, "unparsed_available": 0}

        def read_node(node):
            for offer in _iter_offers(node.get("offers")):
                if _offer_unavailable(offer):
                    tallies["unavailable"] += 1
                    continue
                listing = _offer_listing(offer, url)
                if listing:
                    listings.append(listing)
                else:
                    tallies["unparsed_available"] += 1

        anonymous_nodes = []
        accepted_any = False
        all_nodes = _product_nodes(signals.get("ldjson") or [])
        for node in all_nodes:
            scope = _node_scope(node, product_path)
            if scope == "other":
                continue
            name = node.get("name")
            if scope == "unknown":
                if not (isinstance(name, str) and name.strip()):
                    anonymous_nodes.append(node)
                    continue
                if not _name_matches(name, artist, title):
                    continue
            accepted_any = True
            read_node(node)

        if anonymous_nodes:
            if not accepted_any and len(all_nodes) == 1:
                read_node(anonymous_nodes[0])
            else:
                tallies["unparsed_available"] += len(anonymous_nodes)
        unavailable = tallies["unavailable"]
        unparsed_available = tallies["unparsed_available"]

        # An unparseable available offer poisons the whole JSON-LD read, not
        # just the empty case: returning the offers that *did* parse would
        # report their cheapest as the store's price while the unparsed
        # variant could undercut it. Half-parsed means the OG metas rescue
        # the page or the caller raises -- never a partial answer.
        if not unparsed_available:
            if listings:
                listings.sort(key=lambda x: x["price"])
                return listings
            # [] is only a confirmed miss when every observed offer was
            # deliberately unpurchasable.
            if unavailable:
                return []

        meta = signals.get("meta") or {}
        for amount_key, currency_key in _META_PAIRS:
            price = _finite_price(meta.get(amount_key))
            if price is None:
                continue
            currency = meta.get(currency_key)
            return [{
                "url": url,
                "price": price,
                "shipping": None,
                "currency": currency if isinstance(currency, str) and currency else "USD",
                "condition": None,
            }]
        return None
