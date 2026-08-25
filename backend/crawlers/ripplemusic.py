import html
import random
import re
from asyncio import sleep
from typing import AsyncIterator

import httpx

from config import load_config
from crawl_progress import report_page
from logging_config import get_logger

log = get_logger("ripplemusic")

# Whitespace required on at least one side of the hyphen, matching the repo's
# standard fix for this bug class: a plain \s*-\s* form would clip a
# hyphenated word with no surrounding space.
_TITLE_RE = re.compile(r'^(?P<artist>.+?)(?:\s+-\s*|\s*-\s+)(?P<album>.+)$')
_VARIOUS_RE = re.compile(r'^various(?:\s+artists?)?$', re.IGNORECASE)

# The vinyl vocabulary itself. NOT safe to call directly on a category,
# product, or option name -- `\bvinyls?\b` matches "Vinyl Sticker", where the
# word names a material rather than a format. Go through _vinyl_word(), which
# strips those compounds first; every caller in this file does. This is the
# post-strip vocabulary, not a general-purpose "is it vinyl" test, and the
# distinction is load-bearing: an earlier version of this comment called it
# safe to trust anywhere, and that is exactly the bug that produced.
#
# This store splits its media categories by format *and size* ('12" Vinyl',
# '10" Vinyl', '7" Vinyl', 'Double LP', 'Test Presses'), unlike
# asbestosrecords.py's single `Vinyl` category and jetglowrecordings.py's one
# lumped 'Vinyl - Cassette - CD' -- so a token regex generalises where an
# exact category string would not. `test press` is included because a test
# pressing is vinyl by definition and this store sells them as a category.
_FORMAT_TOKEN = r'(?:vinyls?|\d*x?lps?|test press(?:es|ing|ings)?)'
_VINYL_WORD_RE = re.compile(r'\b' + _FORMAT_TOKEN + r'\b', re.IGNORECASE)

# A bare inch mark is a *weak* signal, deliberately kept out of the regex
# above. It reads as vinyl in "Wo Fat - Split 7\"" and as merch in
# "Ripple Music 12\" Slipmat", and this store sells both -- it has a Slipmat
# category. jetglowrecordings.py notes that a digit followed by a quote mark
# "cannot misfire"; that holds on its store, which sells no merch measured in
# inches, and does not hold here. Trusted only where nothing in the same
# string contradicts it -- see _looks_vinyl.
_INCH_RE = re.compile(r'\d+\s*"')

# One merch vocabulary, two derived regexes. Keeping it in a single list is
# the whole point: the hand-written pair this replaces had `patches` in one
# and `patch` in the other, no `tee` in either despite the store having a
# `Tees` category, and singular-only forms throughout -- so `Vinyl Patch`,
# `12" Slipmats`, and a bare `Tee` all walked straight through as vinyl.
# Two regexes written by hand drift; two built from one list cannot.
#
# Deliberately excluded as too ambiguous in a record-store context, where a
# false positive drops a real release rather than admitting a mug: `sleeve`
# (a "Gatefold Sleeve" variant is a record), `mat` (pluralizes into the
# ordinary word "mates"), `cap` (pluralizes into "capes"), `wrap`. `slipmat`
# covers the real merch case without any of that.
_MERCH_NOUNS = (
    "sticker", "decal", "banner", "slipmat", "poster", "patch", "pin",
    "koozie", "book", "tee", "t-?shirt", "shirt", "hoodie", "sweatshirt",
    "hat", "beanie", "tote", "mug", "magnet", "keychain", "toy", "figure",
    "flag", "puzzle",
)
_MERCH_NOUN = r'(?:' + "|".join(_MERCH_NOUNS) + r')(?:s|es)?'

# Competing formats and merch, used only as a per-option filter and only when
# the option names no vinyl token (see _items). Deliberately broad: the vinyl
# override makes over-inclusion here harmless -- a bundle option named
# "Black Vinyl + Sticker" matches both regexes and is kept -- while a missing
# entry silently publishes a CD as though it were a record.
_NON_VINYL_RE = re.compile(
    r'\b(?:cds?|cassettes?|tapes?|digital|dvds?|blu-?ray)\b'
    r'|\b' + _MERCH_NOUN + r'\b',
    re.IGNORECASE,
)

# A format token immediately followed by a merch noun is naming a material,
# not a format: a vinyl sticker, an LP t-shirt, a test-press poster. These
# compounds are removed before the format test (see _vinyl_word) so the token
# cannot vouch for the merch noun it modifies. A genuine bundle is unaffected
# -- "Black Vinyl + Sticker" has no compound, so its own token survives and
# keeps the row.
#
# Built from _FORMAT_TOKEN, the same alternation _VINYL_WORD_RE uses, for the
# reason _MERCH_NOUNS exists: the first version of this regex hard-coded
# `vinyls?` while _VINYL_WORD_RE accepted three tokens, so "LP T-Shirt" and
# "Test Press Poster" kept their format word and were published as records.
# Fixing the vinyl case and leaving its two siblings is the bug this shape
# makes impossible.
_VINYL_MERCH_RE = re.compile(
    r'\b' + _FORMAT_TOKEN + r'[\s-]+' + _MERCH_NOUN + r'\b', re.IGNORECASE
)

# Runaway guard on the paging loop below, not a coverage decision. At Big
# Cartel's 24-per-page storefront default this is ~1200 products, comfortably
# above any plausible size for this store; hitting it is logged, never silent.
_MAX_PAGES = 50


class Crawler:
    site_name: str = "Ripple Music"
    base_url: str = "https://ripplemusic.bigcartel.com"
    genre_summary: str = "Bay Area stoner rock, doom, and heavy psych label — Wo Fat, Mothership, Cortez, Vokonis."
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        """Page /products.json until it stops returning products we haven't seen.

        The two sibling Big Cartel crawlers each issue exactly one GET, having
        confirmed live that `page=` is silently ignored on their stores.

        This store does the same -- measured 2026-08-24: 373 products, and
        ?page=2 returns the same 373, with the sitemap's product count
        agreeing. So in practice this loop makes two requests and the
        accumulate branch below is never taken.

        It is kept anyway. When it was written the behaviour was unknown, and
        nothing observable from outside distinguished "returns everything"
        from "caps the response" -- this store is five to seven times either
        sibling (76 and 50 products, against 373) and its storefront does
        paginate, /products?page=3 and /category/cds?page=4 both existing. The
        two failure modes are not symmetric: assuming unpaginated and being
        wrong silently drops most of a label's catalog, while assuming
        paginated and being wrong costs one GET. That asymmetry is the reason
        for the shape, and it outlives the measurement -- the store can change
        its behaviour without telling us.

        - If `page=` is honoured, pages accumulate until one comes back empty.
        - If `page=` is ignored, page 1 carries the whole catalog and page 2
          repeats it verbatim; the key-based freshness check sees nothing new
          and stops. Cost of the unknown is one extra request per sync.

        Dropping the loop for a single GET would silently truncate the catalog
        in the first case, which is why the ambiguity is resolved this way and
        not by picking the simpler shape.
        """
        cfg = load_config()
        delay = float(cfg.get("crawl_delay_seconds", 30))
        seen_keys = set()
        page = 1
        async with httpx.AsyncClient() as client:
            while page <= _MAX_PAGES:
                await sleep(random.uniform(delay * 0.5, delay))
                r = await client.get(f"{self.base_url}/products.json", params={"page": page})
                r.raise_for_status()
                products = r.json()
                if not products:
                    break

                fresh = []
                for product in products:
                    key = self._key(product)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    fresh.append(product)
                if not fresh:
                    break

                items = [item for product in fresh for item in self._items(product)]
                await report_page(page, len(items))
                for item in items:
                    yield item
                page += 1
            else:
                log.warning(
                    "[%s] stopped at the %d-page guard; catalog may be truncated",
                    self.base_url, _MAX_PAGES,
                )

    @classmethod
    def _key(cls, product: dict):
        """Identity for the repeat-page check. `id` is Big Cartel's own product
        key; `url` is the fallback, so a row missing an id is still recognised
        on the next page instead of looking fresh forever. Both are per-product
        unique -- `name` deliberately is not in the chain, since a repress can
        share a name with the release it replaces and would collapse onto it."""
        return product.get("id") or product.get("url")

    @classmethod
    def _items(cls, product: dict) -> list:
        # Only drop on an explicitly non-active status. jetglowrecordings.py
        # gates on `status != "active"` because it confirmed live that its
        # store's option-level sold_out is inert and product `status` carries
        # availability alone. Neither half of that could be confirmed here, so
        # an absent `status` must fall through to the option-level flag rather
        # than silently emptying the whole catalog.
        status = product.get("status")
        if status is not None and status != "active":
            return []

        name = html.unescape(product.get("name") or "").strip()
        categories = product.get("categories") or []
        # Union gate, on asbestosrecords.py's finding that neither signal is
        # sufficient alone: 26% of that store's real vinyl releases carried no
        # categories at all, while others carried a vinyl category but no
        # format token in the name. This store's format-named categories make
        # the category arm stronger than it was there, but not sufficient.
        in_vinyl_category = any(
            cls._looks_vinyl(c.get("name") or "") for c in categories
        )
        if not (in_vinyl_category or cls._looks_vinyl(name)):
            return []

        artist, album = cls._parse_artist_title(name, product.get("artists") or [])
        if artist is None:
            return []

        url = f"{cls.base_url}{product.get('url', '')}"
        images = product.get("images") or []
        cover_image_url = images[0].get("url") if images else None

        items = []
        for option in product.get("options") or []:
            if option.get("sold_out"):
                continue
            option_name = html.unescape(option.get("name") or "").strip()
            # Big Cartel has no Shopify-style "Default Title" placeholder: a
            # single-option product repeats its own name as the option name.
            echoes_product = option_name == name
            if not echoes_product and cls._is_non_vinyl(option_name):
                continue
            title = album if echoes_product else f"{album} — {option_name}"
            price = option.get("price")
            items.append({
                "artist": artist,
                "title": title,
                "format": "Vinyl",
                "price": float(price) if isinstance(price, (int, float)) else None,
                # USD: this is a US (SF Bay Area) label and its own product
                # pages price in dollars. darkdescentrecords.py and
                # jetglowrecordings.py are the precedent for a non-USD
                # catalog crawler if that ever changes.
                "currency": "USD",
                "url": url,
                "cover_image_url": cover_image_url,
            })
        return items

    @classmethod
    def _vinyl_word(cls, text: str):
        """Vinyl vocabulary, with material-sense compounds removed first.

        Stripping rather than rejecting: only the compound itself is removed,
        so any independent format token in the same string still counts.
        "Vinyl Sticker" loses its only token and stops looking like a record;
        "Vinyl Sticker + LP" keeps its LP and stays one.
        """
        return _VINYL_WORD_RE.search(_VINYL_MERCH_RE.sub(" ", text))

    @classmethod
    def _looks_vinyl(cls, text: str) -> bool:
        """Whether a category or product name says "this is a record".

        The unambiguous vocabulary is trusted outright. A bare inch mark is
        trusted only when the same string names no competing format or merch
        item, because the mark alone cannot tell a 7" single from a 12"
        slipmat. Without that second clause a product named
        `Ripple Music 12" Slipmat` clears the gate on its inch mark, and a
        single-option product's echoing option then bypasses _is_non_vinyl
        and publishes a slipmat as vinyl.
        """
        if cls._vinyl_word(text):
            return True
        return bool(_INCH_RE.search(text) and not _NON_VINYL_RE.search(text))

    @classmethod
    def _is_non_vinyl(cls, option_name: str) -> bool:
        """Negative per-option filter, the opposite polarity to
        jetglowrecordings.py's positive one, because this store's variant
        names are frequently colour/edition names carrying no format word at
        all ("Rare Test Press", "Worldwide Edition Classic Black Vinyl LP").
        A positive gate would drop the unmarked ones; the product-level gate
        above has already established the product is a record, so the only
        job left is to drop the competing-format variants that mixed
        vinyl/CD products carry. A vinyl *word* wins, so bundle variants
        ("LP + CD") survive -- but an inch mark does not, so a `12" Slipmat`
        option is dropped rather than rescued. Nothing is lost by that: an
        option named only `7"` matches no blocklist entry, so the negative
        filter never reaches for an override on it.
        """
        return bool(
            _NON_VINYL_RE.search(option_name) and not cls._vinyl_word(option_name)
        )

    @classmethod
    def _parse_artist_title(cls, name: str, artists: list):
        # Big Cartel's `artists` field is store-curated per product and this
        # store uses it (its storefront exposes /artist/<slug> pages), but it
        # stays a fallback only: on both sibling stores some tagged artists
        # don't match the title's own billing, so a literal title split always
        # wins when one exists.
        clean = html.unescape(name).strip()
        m = _TITLE_RE.match(clean)
        if m:
            return cls._normalize_artist(m.group("artist").strip()), m.group("album").strip()
        if artists:
            # A blank/missing name on the first curated entry must fall
            # through to the skip below, not return an empty-string artist --
            # _items()'s `if artist is None` guard only rejects None.
            fallback = html.unescape(artists[0].get("name") or "").strip()
            if fallback:
                # Normalized here too, not only on the split branch: a curated
                # tag reading "Various Artists" is exactly as unmatchable as a
                # title billing that reads the same way.
                return cls._normalize_artist(fallback), clean
        return None, clean

    @staticmethod
    def _normalize_artist(artist: str) -> str:
        """Discogs' own entity name is "Various", not "Various Artists" --
        db._library_match_fragment does exact LOWER() equality on artist, so
        the long form never matches anything."""
        return "Various" if _VARIOUS_RE.match(artist) else artist
