import re
import urllib.parse
from typing import Optional

import httpx

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

# The store caps its own suggest widget at 10 and the fleet only ever reads
# matches[0], so this is about how far down a fuzzy result set a real match
# might sit, not about collecting everything.
_SUGGEST_LIMIT = 10


class Crawler:
    site_name: str = "Waterloo Records"
    base_url: str = "https://waterloorecords.com"
    genre_summary: str = "Austin, Texas independent record store since 1982, with a deep new-vinyl catalog spanning every genre and a strong Texas-music selection."
    genre: str = "marketplace"

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
            scored = self._listing(product, release)
            if scored is not None:
                ranked.append(scored)

        if not ranked:
            log.info("[Waterloo Records] no in-stock vinyl match for %r", query)
            return []

        # Base pressings ahead of everything else, then cheapest. The fleet
        # reads matches[0], and this store has no condition field, so -- as the
        # catalog crawler did before it -- a row reports the least it costs to
        # get the record. Unpriced sorts last rather than being dropped: the
        # product is real and still linkable. `l["price"] or 0.0` rather than
        # the bare price so two unpriced listings, which tie on the flag before
        # it, never compare None against None.
        ranked.sort(key=lambda r: (r[0], r[1]["price"] is None, r[1]["price"] or 0.0))
        return [listing for _, listing in ranked]

    @classmethod
    def _listing(cls, product: dict, release: dict):
        """(rank, listing) for a matching product, or None. Lower rank is closer."""
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

        return rank, {
            "url": url,
            "price": cls._price(product),
            # The store ships from one shop and quotes postage at checkout
            # only; nothing in this payload carries it, and inventing a zero
            # would read as free shipping.
            "shipping": None,
            "currency": "USD",
            # No condition field on this store: its stock is new, and the
            # catalog crawler recorded the same absence.
            "condition": None,
        }

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

    @staticmethod
    def _price(product: dict) -> Optional[float]:
        # A string in this payload ("24.99"), unlike products.json's variant
        # prices -- and dollars, not cents.
        for key in ("price", "price_min"):
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

        Title is exact-or-prefix-with-space, matching
        db._library_match_fragment: the store appends its own qualifiers, so
        catalog "Getting Killed" has to match "Getting Killed [Clear Vinyl]
        [LP]". That rule alone also admits a different release that merely
        starts the same way -- "Kid A" matches "Kid A Mnesia [3LP]" -- which
        matters because the fleet reads matches[0]. Rather than tighten it out
        of step with the app's own library matcher, the two cases are ranked:
        rank 0 is an exact title once the bracketed qualifiers come off, which
        only a base pressing achieves, and rank 1 is the looser prefix.
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
        album = cls._fold(store_album)
        if album == want_title or album.startswith(want_title + " "):
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
