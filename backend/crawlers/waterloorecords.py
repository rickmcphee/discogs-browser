import asyncio
import random
import re
import urllib.parse
from asyncio import sleep
from typing import Optional

import httpx

from config import load_config
from crawler import clean_search_text
from logging_config import get_logger

log = get_logger("crawlers.waterloorecords")

# Why this store searches per release instead of walking its catalog:
# Shopify's storefront products.json refuses `page` past 100, so the
# `vinyl-lps` collection (36,138 products, 145 pages) could never be walked
# past the first 25,000 -- and which third fell outside was decided by the
# collection's own ordering rather than by anything meaningful. /search/
# suggest.json has no such ceiling and reaches the whole catalog. See the
# 2026-08-28 amendment to
# docs/specifications/shaping/2026-08-24-waterloo-records-crawler-design.md.

# `type` in the suggest payload is the store's own `product_type` field --
# the same one the catalog crawler gated on, and still the only trustworthy
# format signal here. The title's trailing bracket is NOT one: live values
# include "[Import]", "[Reissue]", "[Limited Edition]" and "[Deluxe]", none
# of which name a format, while CDs carry "[Digipak]". Compared lowercased
# because the store's own casing is inconsistent ("Vinyl" vs "7-IN VINYL").
_VINYL_PRODUCT_TYPES = frozenset({
    "vinyl", "7-in vinyl", "10-in vinyl", "12-in single",
})

# "Artist - Album [Format]", split on the FIRST spaced hyphen: album halves
# legitimately contain further " - " runs ("10CC - Deceptive Bends - 180gm
# Vinyl [LP]" is Deceptive Bends, not Deceptive Bends by 10CC - Deceptive
# Bends). Requires whitespace on both sides so a hyphenated artist or album
# ("Chik-Chik") is never split mid-word. `vendor` is deliberately not used
# as a fallback: it is a numeric supplier code here ("503", "598", "206"),
# not a label or an artist.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)\s+-\s+(?P<album>.+)$')

# Discogs writes an article either way round ("The Beatles", "Beatles, The")
# and Waterloo writes it only the first way, so both forms fold to a bare key
# before comparison. Left in place mid-string -- only the leading and trailing
# positions are the article.
_LEADING_ARTICLE_RE = re.compile(r'^(?:the|a|an)\s+', re.IGNORECASE)
_TRAILING_ARTICLE_RE = re.compile(r',\s*(?:the|a|an)$', re.IGNORECASE)
_PUNCT_RE = re.compile(r'[^\w\s]')

# The store's edition and format qualifiers, which sit in brackets on top of
# the album name: "OK Computer [2LP]", "Getting Killed [Clear Vinyl] [LP]".
# Removed before ranking so a base pressing is recognisable as an exact title
# match, which is what separates it from a genuinely different release that
# merely starts the same way ("Kid A" vs "Kid A Mnesia", "OK Computer" vs
# "OK Computer Oknotok 1997 2017"). Not every qualifier is bracketed --
# "Abbey Road: Anniversary Edition [LP]" is live -- so this ranks rather than
# filters, and an unbracketed one still matches, just below a base pressing.
_BRACKETED_RE = re.compile(r'\[[^\]]*\]')

# Where this store stops naming the album and starts qualifying it. Every
# qualifier it writes is introduced by one of these -- "Abbey Road:
# Anniversary Edition [LP]", "Getting Killed [Clear Vinyl] [LP]" -- so what
# precedes the first one is the album itself. A title that merely continues in
# plain words is a *different* record: "Kid A Mnesia", "OK Computer Oknotok
# 1997 2017". That distinction is the whole point, because the fleet reads
# matches[0] and publishes its price.
_QUALIFIER_CUT_RE = re.compile(r'\s*(?::|\[|\(|\s[-\u2013\u2014]\s)')

# The store caps its own suggest widget at 10 and the fleet only ever reads
# matches[0], so this is about how far down a fuzzy result set a real match
# might sit, not about collecting everything.
_SUGGEST_LIMIT = 10

# Returned by _resolve_price when the product endpoint shows nothing buyable,
# which has to stay distinguishable from a None price -- None means "buyable,
# but the price did not parse", and only this one drops the candidate.
_UNAVAILABLE = object()


class Crawler:
    site_name: str = "Waterloo Records"
    base_url: str = "https://waterloorecords.com"
    genre_summary: str = "Austin, Texas independent record store since 1982, with a deep new-vinyl catalog spanning every genre and a strong Texas-music selection."
    genre: str = "marketplace"
    # One independent record store, not a near-universal marketplace. It stocks
    # a fraction of any given library, so a run of releases it does not carry is
    # its ordinary healthy behaviour rather than evidence it is broken -- and
    # without this, consecutive_failure_limit such releases in a row would trip
    # the circuit breaker and cool the site off. See item 8 of
    # docs/superpowers/specs/2026-08-01-worker-pool-pacing-design.md and its
    # 2026-08-28 amendment.
    empty_result_is_expected: bool = True

    @classmethod
    def search_url(cls, release: dict) -> str:
        query = urllib.parse.quote_plus(cls._query(release))
        return f"{cls.base_url}/search?q={query}"

    async def search(self, release: dict, page) -> list[dict]:
        """The in-stock vinyl this store lists for one release, cheapest first.

        `page` is the Playwright page the release path hands every crawler, and
        is deliberately unused: this is a public JSON endpoint, so a browser
        buys nothing and costs a context. crawlers/ebay.py is the precedent.

        Returns [] only when the store answered and had no matching in-stock
        vinyl. Every failure raises, per the crawler contract in CLAUDE.md --
        the consecutive-failure breaker cannot tell a dead site from an empty
        shelf otherwise.
        """
        query = self._query(release)
        if not query:
            return []

        # load_config() is a blocking Postgres call; offloaded so it cannot
        # stall the process's single event loop, the same way crawlers/ebay.py
        # and crawl_manager's _paced_search do.
        cfg = await asyncio.to_thread(load_config)
        delay = float(cfg.get("crawl_delay_seconds", 30))

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/search/suggest.json",
                params={
                    "q": query,
                    "resources[type]": "product",
                    "resources[limit]": _SUGGEST_LIMIT,
                },
                timeout=30,
            )
            r.raise_for_status()

            try:
                products = r.json()["resources"]["results"]["products"]
            except (KeyError, TypeError, ValueError) as e:
                raise RuntimeError(
                    f"unexpected suggest.json payload shape for {query!r}: {e}"
                ) from None

            ranked = []
            for product in products:
                scored = self._candidate(product, release)
                if scored is not None:
                    ranked.append(scored)

            if not ranked:
                log.info("[Waterloo Records] no in-stock vinyl match for %r", query)
                return []

            # Only the closest matches are priced. Rank dominates the ordering
            # below and the fleet reads matches[0], so a worse-ranked candidate
            # could never be used -- and pricing it would spend a request on
            # the store for an answer nobody reads.
            best = min(rank for rank, _, _ in ranked)
            priced = []
            for rank, product, url in ranked:
                if rank != best:
                    continue
                price = await self._resolve_price(product, url, client, delay)
                if price is _UNAVAILABLE:
                    log.info(
                        "[Waterloo Records] %s reported available but has no "
                        "buyable variant; skipping", url,
                    )
                    continue
                priced.append((url, price))

            listings = [
                {
                    "url": url,
                    "price": price,
                    # The store ships from one shop and quotes postage at
                    # checkout only; nothing in this payload carries it, and
                    # inventing a zero would read as free shipping.
                    "shipping": None,
                    "currency": "USD",
                    # No condition field on this store: its stock is new, and
                    # the catalog crawler recorded the same absence.
                    "condition": None,
                }
                for url, price in priced
            ]
            if not listings:
                log.info("[Waterloo Records] no in-stock vinyl match for %r", query)
                return []

        # Cheapest first. This store has no condition field, so -- as the
        # catalog crawler did before it -- a row reports the least it costs to
        # get the record. Unpriced sorts last rather than being dropped: the
        # product is real and still linkable. `l["price"] or 0.0` rather than
        # the bare price so two unpriced listings, which tie on the flag before
        # it, never compare None against None.
        listings.sort(key=lambda l: (l["price"] is None, l["price"] or 0.0))
        return listings

    @classmethod
    def _candidate(cls, product: dict, release: dict):
        """(rank, product, url) for a matching product, or None. Lower rank is closer.

        Deliberately does no fetching, so which products are worth a request is
        decided before any is sent.
        """
        if not product.get("available"):
            return None
        if (product.get("type") or "").strip().lower() not in _VINYL_PRODUCT_TYPES:
            return None

        parsed = cls._split_title(product.get("title") or "")
        if parsed is None:
            return None
        rank = cls._match_rank(parsed, release)
        if rank is None:
            return None

        url = cls._clean_url(product.get("url") or "")
        if not url:
            return None

        return rank, product, url

    @staticmethod
    def _clean_url(raw: str) -> str:
        """Absolute product URL with the search tracking parameters removed.

        suggest.json returns `/products/<handle>?_pos=1&_psq=<query>&_psid=<id>`,
        and every one of those parameters varies with the search that produced
        it. db.compute_item_key() hashes the url, so leaving them on would give
        one product a fresh item_key on every crawl and orphan the saves and
        judgments hanging off the old one.
        """
        path = urllib.parse.urlsplit(raw).path
        if not path:
            return ""
        return urllib.parse.urljoin(Crawler.base_url, path)

    @classmethod
    async def _resolve_price(cls, product: dict, url: str, client, delay: float) -> Optional[float]:
        """What the record actually costs, not what its cheapest variant costs.

        `available` on a suggest hit means *some* variant is purchasable, while
        `price`/`price_min` are minima across every variant including the sold
        out ones -- so the two can describe different variants. Live example:
        "070 Shake - Petrichor [LP]" reports available with price 24.99 and
        price_max 29.99, and the 24.99 variant is the sold out one. Publishing
        24.99 there would quote a price nobody can pay. The catalog crawler
        this replaced avoided it by picking the cheapest *in-stock* variant,
        and that very product was its fixture.

        suggest.json carries an empty `variants` list, so the variants have to
        come from the product endpoint -- but only when the product's own price
        range leaves room for disagreement. price_min == price_max means every
        variant costs the same, so whichever is available costs that, and no
        second request is worth making.
        """
        low = cls._decimal(product, "price_min", "price")
        if low is None:
            return None
        # Only *equal* known bounds prove the available variant costs `low`. A
        # missing or malformed price_max is an unknown range, not a uniform
        # one, so it takes the lookup rather than the shortcut -- shortcutting
        # there would republish exactly the sold-out minimum this exists to
        # avoid.
        high = cls._decimal(product, "price_max")
        if high is not None and low == high:
            return low

        # _paced_search spaces separate search() calls, not the requests inside
        # one, so this gap is the crawler's own to keep -- exactly as every
        # detail-fetching crawler in the fleet does it. Without it the suggest
        # request and each product lookup would go out back to back, which is
        # the burst the per-site pacing contract exists to prevent.
        await sleep(random.uniform(delay * 0.5, delay))
        r = await client.get(f"{url}.js", timeout=30)
        r.raise_for_status()
        variants = (r.json() or {}).get("variants") or []
        available = [v for v in variants if v.get("available")]
        if variants and not available:
            # The product endpoint says nothing here is buyable, contradicting
            # the `available` flag on the suggest hit that got us this far --
            # the two are separate responses and the stock moved between them.
            # Believe the later, more specific one and drop the candidate:
            # returning it would put a Waterloo row in the Store tab for a
            # record nobody can buy. _UNAVAILABLE is distinct from a None
            # price, which means "buyable, but the price did not parse".
            return _UNAVAILABLE
        # Cents here, unlike suggest.json's decimal strings.
        prices = [
            v["price"] / 100
            for v in available
            if isinstance(v.get("price"), (int, float))
        ]
        # An available variant whose price is unreadable. Falling back to `low`
        # would reintroduce exactly the sold-out price this exists to avoid, so
        # the row goes out unpriced and still linkable.
        return min(prices) if prices else None

    @staticmethod
    def _decimal(product: dict, *keys: str) -> Optional[float]:
        # Decimal strings in this payload ("24.99"), not cents.
        for key in keys:
            try:
                return float(product[key])
            except (KeyError, TypeError, ValueError):
                continue
        return None

    @classmethod
    def _split_title(cls, raw_title: str):
        m = _TITLE_RE.match(raw_title.strip())
        if not m:
            return None
        artist = m.group("artist").strip()
        album = m.group("album").strip()
        if not artist or not album:
            return None
        return artist, album

    @classmethod
    def _match_rank(cls, parsed: tuple, release: dict) -> Optional[int]:
        """How closely a fuzzy suggest hit matches the release, or None for no match.

        The endpoint is a search box, not a lookup: "geese getting killed"
        returns Getting Killed alongside 3D Country and a Third Man live
        record. Both halves of the store's own `Artist - Album` convention are
        checked rather than the fleet's usual substring sniff, because that
        convention makes a real comparison available here.

        The store appends its own qualifiers, so catalog "Getting Killed" has
        to match "Getting Killed [Clear Vinyl] [LP]". Both ranks are equality
        tests against a stripped form of the store's title, never a prefix
        test: a prefix would admit a different release that merely starts the
        same way -- "Kid A" against "Kid A Mnesia [3LP]" -- and the fleet reads
        matches[0] and publishes its price.

        Rank 0 is an exact title once the bracketed qualifiers come off, which
        only a base pressing achieves. Rank 1 is an exact title at any
        qualifier-delimiter boundary, which admits a qualified edition
        ("Abbey Road: Anniversary Edition [LP]") without admitting a longer
        album name.
        """
        store_artist, store_album = parsed
        want_artist = cls._fold(release.get("artist", ""))
        want_title = cls._fold(release.get("title", ""))
        if not want_title:
            return None

        # Discogs' catch-all entity for a compilation. The store files those
        # under a real artist or the label ("Soundtrack", "VA"), so there is
        # nothing to compare and the title check below carries the match alone.
        if want_artist and want_artist != "various":
            if cls._fold(store_artist) != want_artist:
                return None

        base = cls._fold(_BRACKETED_RE.sub(" ", store_album))
        if base == want_title:
            return 0
        # The album name as this store writes it, which is whatever precedes
        # one of its qualifier delimiters. Every boundary is tried, not just
        # the first: an album whose own title contains a delimiter would
        # otherwise be truncated to its opening fragment, so
        # "Live: In Concert" could never match this store's
        # "Live: In Concert: Anniversary Edition [LP]". Trying each still
        # rejects "Kid A Mnesia", because no boundary of it yields "Kid A".
        #
        # Deliberately an equality test at each boundary, not the
        # exact-or-prefix-with-space rule db._library_match_fragment uses: that
        # rule answers "does this stock row correspond to a release the user
        # owns", where a wrong answer mislabels ownership, while this one
        # decides which record's price gets published. With no base pressing in
        # the results, a prefix match would report "Kid A Mnesia [3LP]" as the
        # price of Kid A rather than reporting Kid A as absent, and a wrong
        # price is worse than a missing one.
        for boundary in _QUALIFIER_CUT_RE.finditer(store_album):
            if cls._fold(store_album[:boundary.start()]) == want_title:
                return 1
        return None

    @staticmethod
    def _fold(text: str) -> str:
        text = clean_search_text(text or "")
        text = _TRAILING_ARTICLE_RE.sub("", text)
        text = _LEADING_ARTICLE_RE.sub("", text)
        text = _PUNCT_RE.sub(" ", text)
        return re.sub(r'\s+', ' ', text).strip().lower()

    @classmethod
    def _query(cls, release: dict) -> str:
        artist = clean_search_text(release.get("artist", ""))
        if artist.lower() == "various":
            artist = ""
        title = clean_search_text(release.get("title", ""))
        return f"{artist} {title}".strip()
