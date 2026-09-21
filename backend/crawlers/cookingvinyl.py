import math
import re
import xml.etree.ElementTree as ET
from typing import AsyncIterator, Optional

import httpx

from catalog_http import get_with_retry
from config import load_config
from crawl_progress import report_page

# The store's own published feed, and the only path on this host an HTTP client
# can read. `/`, `/products`, `/product/<id>` and `/sitemap.xml` all answer 403
# behind a Cloudflare managed challenge; `/robots.txt` and this are exempt. The
# storefront would be no use even without the challenge -- its product grid is
# client-rendered, and the HTML carries the app bundle and nothing else.
# robots.txt allows this path and names no Crawl-delay.
_FEED_PATH = "/productfeed"

# httpx sends `python-httpx/<version>` by default, and Cloudflare blocklists
# that agent on this host -- as it does `python-requests` -- so a request
# without this header gets the interstitial every time. The rule is a
# library-agent blocklist rather than a browser allowlist: an agent that names
# this app passes, which is why this identifies rather than impersonating.
# Same string discogs_marketplace.py sends.
_USER_AGENT = "DiscogsCollectionBrowser/1.0 +https://github.com/local/discogs-browser"

# Shapes, not literals, and the same construction monorailmusic.py and
# earache.py use. \b matches nothing between a digit and a letter, so a plain
# \blps?\b sees none of `2LP`, `2xLP`, `DLP` or `LP2`; the lookbehind is what
# stops the leading \d* letting a letter run into it. Sizes are only 7, 10 and
# 12 before an inch mark rather than any number, so a print measured in inches
# is not read as a single.
_VINYL_RE = re.compile(
    r"vinyl"
    r"|(?<![a-z])\d*\s*[x×]?\s*d?lps?\d?\b"
    r"|\bpicture\s+discs?\b"
    r"|\btest\s+pressings?\b"
    r"|(?<![a-z0-9])(?:\d+\s*[x×]\s*)?(?:7|10|12)\s*-?\s*(?:[\"”″]|inch(?:es)?\b|in\b)",
    re.IGNORECASE,
)

# A bundle's price is a record's price plus something else's, so publishing one
# as a record misprices the album. This list catches the bundles this platform
# joins with `&` -- "True North 2LP Heavyweight Vinyl & CD", "... & Black
# T-Shirt" -- which the `+` test below cannot see.
#
# Every word here was read off a live bundle. Two that suggest themselves are
# deliberately absent, because this test only ever sees a name that has already
# named vinyl -- so its whole exposure is a record whose *title* contains one of
# these words, and a word has to earn that. `digital` never appears in a vinyl
# bundle (those say "Download", which is here), and would drop Bright Eyes'
# "Digital Ash In A Digital Urn"; `cap` appears in no bundle at all, and the
# apparel ones are already covered.
#
# The disc media take a count the same way a format does, and for the same
# reason must be matched by shape rather than by \b: nothing matches between a
# digit and a letter, so a plain \bcds?\b sees neither `2CD` nor `2xCD`. That
# is the trap _VINYL_RE above is already built around, and leaving the other
# half of the gate with the naive pattern published bundles at a bundle's
# price -- `&` being deliberately not a bundle marker, and `/` not one either,
# nothing else caught them. Not hypothetical: on the platform's flagship store
# the naive pattern admitted `Legend / Legend Extended (40th Anniversary
# Edition) Double Vinyl & 2CD`, `The Journey - Part 3 Double LP & 2CD`,
# `Tapping The Vein 3LP/2CD Deluxe Bookpack Boxset`, `Harvest (50th
# Anniversary Edition) 2LP/7"/2DVD Boxset` and five more like them.
# (Copilot, PR #393.)
_COUNTED = r"(?<![a-z])\d*\s*[x×]?\s*"
_OTHER_MEDIUM_RE = re.compile(
    rf"{_COUNTED}cds?\b"
    rf"|{_COUNTED}cassettes?\b"
    rf"|{_COUNTED}dvds?\b"
    r"|\bdownloads?\b|\bt-?shirts?\b"
    r"|\bmugs?\b|\bhoodie\b|\bsweatshirt\b|\bpolo\b|\bscarf\b"
    r"|\bmagnets?\b|\bblu-?\s?rays?\b",
    re.IGNORECASE,
)

# How this platform joins the items of a bundle. Needed alongside the medium
# list above because one live bundle names no second medium at all -- "A Matter
# of Time + Liquid Gold Red & Black Marble Vinyl Represses" is two records at
# one price, which is a listing for neither.
#
# `&` is deliberately NOT here. The store writes colours with it -- "Changed
# Giver RSD 2024 Half White & Half Black Vinyl" is live, as are "Red & Black
# Marble Vinyl" and "(Signed & Numbered) Test Pressing Vinyl" elsewhere on the
# platform -- so reading it as a join would drop real records. It does not need
# to be here: every `&`-joined bundle observed names its second medium, so the
# test above already has them.
_BUNDLE_RE = re.compile(r"\+")

# Both values the feed's availability field has ever carried, and both are
# purchasable -- sold-out products are absent from the feed entirely rather
# than flagged. A positive literal gate rather than a rejected list, so a value
# this crawler has never seen is not assumed to mean "buyable".
_PURCHASABLE = frozenset({"in stock", "preorder"})

# Discogs' own entity name is the bare "Various", and two consumers compare
# against that exact string: amazon.py's Crawler._artist() and db.py's
# _library_release_match_sql. "Various Artists" satisfies neither. Same rewrite
# as musiconvinyl.py, angryyoungandpoor.py and cleorecs.py.
_VARIOUS_ARTISTS = frozenset({"various artists", "various"})

# What a product's own `<currency>` falls back to. Not None: frontend
# formatPrice() reads a null currency as USD (deliberately -- most sources
# hardcode USD and predate the column), so a single product losing the field
# would put a dollar sign on a sterling price. Every product on every feed
# sampled says GBP and the feed ignores `?cur=`, so the store's own currency is
# the honest fallback; the per-product value is still what is read first, so a
# store that genuinely started pricing in euros would not be misreported.
# (Copilot, PR #393.)
_DEFAULT_CURRENCY = "GBP"


class Crawler:
    site_name: str = "Cooking Vinyl"
    base_url: str = "https://cookingvinyl.tmstor.es"
    genre_summary: str = (
        "Camden independent label's direct-to-fan store -- Billy Bragg, The Prodigy, "
        "Passenger, Alison Moyet, Shed Seven and the rest of the Cooking Vinyl roster, "
        "heavy on Record Store Day pressings."
    )
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        failure_limit = int(cfg.get("consecutive_failure_limit", 10))

        async with httpx.AsyncClient(base_url=self.base_url, follow_redirects=True) as client:
            r = await get_with_retry(
                client, _FEED_PATH,
                delay=delay, failure_limit=failure_limit,
                headers={"User-Agent": _USER_AGENT},
            )
            products = self._products(r.text)

        products_seen = 0
        vinyl_named = 0
        publishable = 0
        identity_missing = 0
        unrecognised_availability = 0
        unrecognised_values = set()
        items = []
        for product in products:
            products_seen += 1
            name = self._text(product, "name")
            if not _VINYL_RE.search(name):
                continue
            # Counted apart from the bundle rejection below, so the two guards
            # can say which of them emptied the walk: a catalog that stops
            # naming formats and one where everything reads as a bundle are
            # different breakages with different fixes.
            vinyl_named += 1
            if _BUNDLE_RE.search(name) or _OTHER_MEDIUM_RE.search(name):
                continue
            # Nested, not sibling tallies: only a publishable product with an
            # identity AND a readable availability could have yielded a row, so
            # only such a product's readability says anything about an empty
            # result. Tallied independently, one product could satisfy each
            # condition while none can yield.
            publishable += 1
            if not self._has_identity(product):
                identity_missing += 1
                continue
            availability = self._text(product, "availability").lower()
            if availability not in _PURCHASABLE:
                # Sold-out products are absent from the feed rather than
                # flagged, so it has never carried a value outside
                # _PURCHASABLE -- which makes any other value, an empty one
                # included, drift rather than a shelf that sold out.
                unrecognised_availability += 1
                unrecognised_values.add(availability or "(empty)")
                continue
            items.append(self._item(product))

        yielded = len(items)
        priced = sum(1 for item in items if item["price"] is not None)
        await report_page(1, yielded)
        for item in items:
            yield item

        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the crawl
        # raised -- so a completed-but-empty walk is destructive where a raise
        # is inert. Each guard below names a distinct way the feed can stop
        # carrying what this crawler reads.
        if products_seen == 0:
            # An empty feed is a real state on this platform, so for a store
            # this small a genuine total sell-out trips this. Accepted: a raise
            # keeps the previous snapshot and costs one tick on this site's
            # failure breaker, where wiping a good snapshot has no ceiling.
            raise RuntimeError(f"{_FEED_PATH} carried no products -- feed emptied, or product-element drift")
        if vinyl_named == 0:
            # A vinyl label's store whose every observed product has been a
            # record does not stop naming records, so zero reads as a change in
            # how the store words its formats rather than a sold-out shelf.
            raise RuntimeError(
                f"no product in {_FEED_PATH} names a vinyl format -- format-naming drift")
        if publishable == 0:
            # Every record read as a bundle. The store publishes no bundle at
            # all today, so this is the rejection tests over-matching -- a `+`
            # or a medium word that has become part of how the store writes an
            # ordinary title. Without this guard that reads as a sold-out
            # catalog and takes the snapshot with it.
            raise RuntimeError(
                f"all {vinyl_named} vinyl product(s) in {_FEED_PATH} read as bundles "
                "-- bundle-detection drift")
        if not yielded and identity_missing:
            # `artist`, `name` and `purl` are identity, not display: item_key
            # hashes the row's title and URL, so a product missing one is
            # skipped rather than emitted under a fresh identity that would
            # orphan the judgments and saves keyed on its old one. Skipped rows
            # leave the walk looking sold out, which is why this and the stock
            # guard below are gated on having yielded nothing at all.
            raise RuntimeError(
                f"{_FEED_PATH} yielded no rows while {identity_missing} vinyl product(s) "
                "carry no artist, name or product URL -- identity-source drift")
        if not yielded and unrecognised_availability:
            # An empty result is only trustworthy when every product that could
            # have yielded a row was readable and simply unavailable. Counting
            # the unreadable ones rather than the readable ones is what catches
            # the partial case: one genuinely sold-out product must not vouch
            # for a catalog that has gone unreadable behind it.
            #
            # The values are named, not just counted: a missing field and a
            # value the platform has newly introduced ("on backorder") both
            # land here, and they send whoever reads this to different places.
            # (Copilot, PR #393.)
            raise RuntimeError(
                f"{_FEED_PATH} yielded no rows while {unrecognised_availability} vinyl product(s) "
                f"carry an unrecognised availability ({', '.join(sorted(unrecognised_values))}) "
                "-- stock-source drift")
        if yielded and not priced:
            # Rows without the emptiness: _price answers None for a value it
            # cannot use, so a price field removed or retyped store-wide
            # re-lists the whole catalog with no prices, which is worse than
            # the snapshot it would replace. Isolated nulls stay tolerated.
            raise RuntimeError(
                f"none of the {yielded} rows from {_FEED_PATH} carries a price -- price-source drift")

    @classmethod
    def _products(cls, body: str) -> list:
        """The feed's product elements, or a raise naming what came back instead.

        Separate from the emptiness guard above, and ahead of it: a challenge
        page, an HTML error page or a renamed endpoint is a transport failure,
        and reporting one as "the catalog is empty" would send whoever reads
        the log after the store's stock rather than after its feed.
        """
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise RuntimeError(f"{_FEED_PATH} did not return XML -- endpoint drift, or a challenge page: {e}")
        if root.tag != "rss":
            raise RuntimeError(f"{_FEED_PATH} returned <{root.tag}>, not <rss> -- endpoint drift")
        merchant = root.find("merchant")
        if merchant is None:
            raise RuntimeError(f"{_FEED_PATH} returned an <rss> with no <merchant> -- feed-shape drift")
        return merchant.findall("product")

    @classmethod
    def _item(cls, product) -> dict:
        return {
            "artist": cls._artist(product),
            # The store's own name for the product, verbatim, pressing and all.
            # Not split behind an em-dash fence the way the Shopify plugins
            # compose one: those read two separate fields, this payload blends
            # both into one string, and guessing where the fence goes is the
            # false merge title_key.py errs away from. Leaving it whole costs
            # one judgment per colour variant -- the cheap direction -- and
            # title_key() drops `vinyl` and `lp` unfenced anyway, so the
            # Cheapest filter still groups this store against the others.
            "title": cls._text(product, "name"),
            "format": "Vinyl",
            "price": cls._price(product),
            # Read per product rather than hardcoded, so a store that started
            # pricing in another currency is reported as it prices; see
            # _DEFAULT_CURRENCY for why the absent case falls back to the
            # store's own currency rather than to None.
            "currency": cls._text(product, "currency") or _DEFAULT_CURRENCY,
            "url": cls._text(product, "purl"),
            "cover_image_url": cls._text(product, "imgurl") or None,
        }

    @staticmethod
    def _text(product, tag: str) -> str:
        """The stripped text of `tag`, or "" when it is absent or empty. Every
        field this crawler reads goes through here, so a missing element is an
        ordinary skipped row rather than an AttributeError that would abort the
        walk over one malformed product."""
        return (product.findtext(tag) or "").strip()

    @classmethod
    def _has_identity(cls, product) -> bool:
        return bool(cls._artist(product)) and bool(cls._text(product, "name")) and bool(cls._text(product, "purl"))

    @classmethod
    def _artist(cls, product) -> str:
        artist = cls._text(product, "artist")
        if artist.lower() in _VARIOUS_ARTISTS:
            return "Various"
        return artist

    @classmethod
    def _price(cls, product) -> Optional[float]:
        try:
            price = float(cls._text(product, "price"))
        except ValueError:
            return None
        # float() parses "nan" and "inf" from a string, so the finiteness test
        # is doing work the try above cannot.
        if not math.isfinite(price) or price <= 0:
            return None
        return price
