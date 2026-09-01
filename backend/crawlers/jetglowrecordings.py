import html
import re
from typing import AsyncIterator

import httpx

from catalog_http import get_with_retry
from config import load_config
from crawl_progress import report_page

# Whitespace required on at least one side of the hyphen, matching the repo's
# standard fix for this bug class: a plain \s*-\s* form would clip a
# hyphenated word with no surrounding space.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
_VARIOUS_RE = re.compile(r'^various(?:\s+artists)?$', re.IGNORECASE)

# The store's one media category. Unlike asbestosrecords.py's `Vinyl`, this
# lumps all three physical formats together, so it answers "is this a record
# release rather than a t-shirt", not "is this vinyl" -- it's a merch gate
# only, and the vinyl decision happens per option below. Load-bearing: the
# poster product carries an option literally named "Poster + Vinyl", which
# _VINYL_RE would otherwise accept as a record.
_MEDIA_CATEGORY = "Vinyl - Cassette - CD"

# Positive per-option match, not a negative filter. Confirmed live: this
# store's vinyl options always name the format ("Black Vinyl", "Smoked Red
# Vinyl", "Vinyl + CD", "Bundle Black Vinyls + Digipack CD"), so a positive
# match loses nothing -- and unlike a negative filter it correctly declines
# the opaque "Special Bluedeep Bundle 1" / "Special Box" rows whose contents
# are unknowable from the feed. (carparkrecords.py reaches the opposite
# conclusion for its own store, whose vinyl variants are named by colour
# alone with no format word at all.)
#
# `\bep\b` is deliberately absent -- zero live matches, and untested here.
# The inch mark is kept despite also having zero live matches: a 7" is
# plausible future stock and a digit followed by a quote mark cannot misfire.
_VINYL_RE = re.compile(r'\bvinyls?\b|\b\d*x?lps?\b|\d+\s*"', re.IGNORECASE)

_SEGMENT_RE = re.compile(r'\s+-\s+')
_WORD_RE = re.compile(r'[a-z0-9]+')
# Deliberately narrow: format and edition words only, never ordinary title
# words. "the", "with", "deluxe", "collectors", and "only" were trialled and
# removed -- they change nothing on the live feed and each risks eating a
# real album title.
_FORMAT_WORDS = frozenset({
    "lp", "lps", "2xlp", "vinyl", "vinyls", "cd", "cds", "cassette",
    "cassettes", "digipack", "tape", "tapes", "box", "bundle", "bundles",
    "edition", "editions", "ed", "version", "included", "preorder", "and",
})


class Crawler:
    site_name: str = "Jetglow Recordings"
    base_url: str = "https://jetglowrecordings.bigcartel.com"
    genre_summary: str = "Italian hard rock, glam, and punk label — Warrior Soul, Kory Clarke, Space Age Playboys."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        # Confirmed live: page= and limit= are silently ignored -- ?page=2
        # returns the identical 50 rows starting with the same first product,
        # the same behaviour asbestosrecords.py documents for its own store.
        # 50 is the whole catalog, not a page cap: /products.xml also reports
        # 50, and the four category feeds sum to 47+1+1+1 = 50. One request
        # per sync, still paced like every sibling crawler.
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        failure_limit = int(cfg.get("consecutive_failure_limit", 10))
        async with httpx.AsyncClient() as client:
            r = await get_with_retry(
                client, f"{self.base_url}/products.json",
                delay=delay, failure_limit=failure_limit,
            )
        products = r.json()

        items = [item for product in products for item in self._items(product)]
        await report_page(1, len(items))
        for item in items:
            yield item

    @classmethod
    def _items(cls, product: dict) -> list:
        # Product-level `status`, not option-level `sold_out`: confirmed live,
        # all 114 options on this store report sold_out=False, including every
        # option of the six products the storefront itself renders "Sold Out".
        # The option flag is still honoured below -- it's insufficient here,
        # not wrong, and a partially sold-out product could set it later.
        if product.get("status") != "active":
            return []
        categories = product.get("categories") or []
        if not any(c.get("name") == _MEDIA_CATEGORY for c in categories):
            return []

        name = html.unescape(product.get("name") or "").strip()
        artist, album = cls._parse_artist_title(name, product.get("artists") or [])
        if artist is None:
            return []

        url = f"{cls.base_url}{product.get('url', '')}"
        images = product.get("images") or []
        cover_image_url = images[0].get("url") if images else None
        display_album = cls._strip_format_suffix(album)

        items = []
        for option in product.get("options") or []:
            if option.get("sold_out"):
                continue
            option_name = html.unescape(option.get("name") or "").strip()
            # Big Cartel has no Shopify-style "Default Title" placeholder: a
            # single-option product repeats its own name as the option name,
            # so that option carries no independent format signal and the gate
            # falls back to the product name. Load-bearing -- it's the only
            # thing keeping the three "(LP VERSION)"-style single-option
            # releases.
            echoes_product = option_name == name
            if not _VINYL_RE.search(name if echoes_product else option_name):
                continue
            title = display_album if echoes_product else f"{display_album} — {option_name}"
            price = option.get("price")
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                # EUR, not USD -- confirmed from the storefront's own
                # data-currency-code="EUR" markup. Every sibling Big Cartel
                # store prices in USD; darkdescentrecords.py is the precedent
                # for a non-USD catalog crawler.
                "currency": "EUR",
                "price": float(price) if isinstance(price, (int, float)) else None,
                "url": url,
                "cover_image_url": cover_image_url,
            })
        return items

    @classmethod
    def _strip_format_suffix(cls, album: str) -> str:
        """Drop a trailing ' - ' segment made entirely of format/edition words.

        Product names are `ARTIST - ALBUM - FORMAT BLURB`, so splitting on the
        first separator leaves the blurb inside the album and appending the
        option name doubles it ("... - VINYL — Vinyl"). Display-only cleanup:
        db._library_match_fragment matches exact-or-prefix-with-space, so both
        forms already matched Discogs. Runs after the format gate, so it can
        never remove the token the gate depended on.
        """
        segments = _SEGMENT_RE.split(album)
        while len(segments) > 1:
            words = _WORD_RE.findall(segments[-1].lower())
            if not words or not all(w in _FORMAT_WORDS for w in words):
                break
            segments = segments[:-1]
        return " - ".join(segments).strip()

    @classmethod
    def _parse_artist_title(cls, name: str, artists: list):
        # The curated `artists` field is only a fallback: confirmed live, some
        # tagged artists don't match the title's billing at all ("WARRIOR SOUL
        # - THE SPACE AGE PLAYBOYS ... CD ED." is tagged "Space Age Playboys",
        # and every KORY CLARKE release is tagged "Kory Clarke / Warrior
        # Soul", which matches no Discogs artist). A literal title split
        # always wins when one exists.
        clean = html.unescape(name).strip()
        m = _TITLE_RE.match(clean)
        if m:
            artist = m.group("artist").strip()
            album = m.group("album").strip()
            if _VARIOUS_RE.match(artist):
                # Discogs' own entity name is "Various", not "Various
                # Artists" -- db.py's _library_match_fragment does exact
                # LOWER() equality on artist, so the long form never matches.
                artist = "Various"
            return artist, album
        if artists:
            # A blank/missing name on the first curated entry must fall
            # through to the skip below, not return an empty-string artist --
            # _items()'s `if artist is None` guard only rejects None.
            fallback = html.unescape(artists[0].get("name") or "").strip()
            if fallback:
                return fallback, clean
        return None, clean
