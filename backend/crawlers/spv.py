import re
from typing import AsyncIterator

from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"

# Product titles are `Artist "Album" FORMAT`, with the album in straight or
# typographic double quotes and a trailing format/edition blurb:
#   Sodom "1982" LP (exclusive)
#   Magnum "The Monster Roars" LP (white & black marbled vinyl)
# Same two-stage quoted-primary/dash-fallback shape as asianmanrecords.py, the
# only other store in the fleet that quotes its album titles, including its
# optional separator before the opening quote (`Artist - "Album"`). Widened
# here to accept typographic quotes, and extended with an `extra` capture the
# sibling has no use for -- this store carries its format in the title blurb,
# not in the variant, so the gate below needs the text after the album.
# Both captures are quote-free classes: the artist can never swallow the
# album's opening quote, and the album stops at the FIRST closing quote rather
# than running to the last quote in the string.
_TITLE_RE = re.compile(
    r'^(?P<artist>[^"“”]+?)\s*[-–—]?\s*["“](?P<album>[^"“”]+)["”]\s*(?P<extra>.*)$'
)
# Fallback for the occasional unquoted title. Byte-identical to
# asianmanrecords.py's own fallback: the hyphen/en-dash/em-dash class
# cleorecs.py established, with seasonofmist.py's whitespace anchoring on at
# least one side of the separator so hyphenated artist names ("Cro-Mags")
# aren't clipped at their internal hyphen.
_DASH_RE = re.compile(r'^(?P<artist>.+?)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?P<album>.+)$')
# The dash fallback has no `extra` group of its own, so a format marker
# trailing the album half ("Sodom - 1982 CD") would otherwise reach neither the
# format gate nor the stored title. Split it off and feed it to the gate,
# mirroring asianmanrecords.py's _FORMAT_SUFFIX_RE, which does the same for its
# own no-quote titles. The leading \s+ means a single-word album that IS one of
# these words ("Tape") has nothing before it to match and is left alone.
# Deliberately narrower than _NON_VINYL_RE: only unambiguous format markers,
# no bare "MC"/"EP", since this rewrites the stored title rather than just
# gating on it.
_TRAILING_FORMAT_RE = re.compile(
    r'\s+((?:(?:\d*[x×]?LP|\d*[x×]?CDS?|VINYL|CASSETTE|TAPE|\d*[x×]?DVD|BLU-?RAY|'
    r'DIGITAL|DIGIPA[KC]K?|PICTURE\s+DISC|T-?SHIRT|SHIRT|HOODIE|LONGSLEEVE|POSTER|'
    r'PATCH|FLAG|MUG)\b|\d{1,2}\s*(?:"|INCH\b)).*)$',
    re.IGNORECASE,
)
_PLACEHOLDER_VARIANT = "default title"
_PREORDER_RE = re.compile(r'pre[\s_-]?order', re.IGNORECASE)
# \d*[x×]? on the LP/CD/DVD forms: Shopify stores write multi-disc counts both
# ways ("2LP" and "2xLP"), and this repo's own fixtures carry the x form (see
# test_temporaryresidence_crawler.py). Siblings already spell it \b\d*x?lp\b;
# the × is added because a store that types the real multiplication sign should
# not silently fall through the gate.
# The inch alternative is deliberately spelled the same way as
# _TRAILING_FORMAT_RE's. They disagreed before: this gate took only an
# unspaced mark on three specific sizes (7/10/12"), while the stripper also
# accepted a space and the word INCH -- so `10 INCH + CD` lost the vinyl
# override and was dropped as a CD, while `12" + CD` was kept. Same shape of
# bug as the 2xLP+CD one: a bundle is only safe if the override recognises
# every vinyl spelling the rest of the module does.
_VINYL_RE = re.compile(
    r'\b\d*[x×]?lp\b|\bvinyl\b|\b\d{1,2}\s*(?:"|inch\b)|\bpicture disc\b', re.IGNORECASE
)
# The \d* on cd/dvd is load-bearing: a disc count binds to the format word with
# no word boundary between them, so a bare \bcds?\b cannot match the "CD" in
# "2CD" and a double-CD edition was passing the gate as Vinyl. _VINYL_RE's
# \b\d*lp\b already had the same allowance for "2LP"; this brings the negative
# side into line. A bundle naming both ("LP+2CD") is still vinyl -- _VINYL_RE
# short-circuits ahead of this.
_NON_VINYL_RE = re.compile(
    r'\b(\d*[x×]?cds?|digital|digipa[kc]k?|cassette|tape|mc|\d*[x×]?dvd|blu-?ray|shirt|'
    r't-shirt|hoodie|longsleeve|poster|patch|flag|mug|book)\b',
    re.IGNORECASE,
)


def _split_trailing_format(album: str) -> tuple:
    """Split a trailing format marker off an album title -> (album, format)."""
    m = _TRAILING_FORMAT_RE.search(album)
    if not m:
        return album, ""
    remainder = album[:m.start()].strip()
    if not remainder:
        return album, ""
    return remainder, m.group(1).strip()


class Crawler:
    site_name: str = "SPV Entertainment"
    base_url: str = "https://store.spv.de"
    genre_summary: str = (
        "German independent label and distributor — Steamhammer and Long Branch "
        "metal, hard rock, and prog."
    )
    genre: str = "metal"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            for item in self._items(product):
                yield item

    @classmethod
    def _items(cls, product: dict) -> list[dict]:
        artist, album_title, extra = cls._parse_title(product.get("title", ""))
        # No artist parsed out of the title -> skip rather than publish a row
        # that can never match a Discogs release (darkdescentrecords.py's
        # convention).
        if not artist or not album_title:
            return []
        if not cls._is_vinyl(extra):
            return []

        handle = product.get("handle", "")
        url = f"{cls.base_url}/products/{handle}"
        is_preorder = cls._is_preorder(product)

        variants = product.get("variants") or []
        # The title blurb gates the *product*; a mixed-format product still
        # needs its non-vinyl variants dropped, or a "CD" variant of an
        # LP-titled release publishes as format "Vinyl". Same gate, applied to
        # the variant name -- deliberately not nuclearblast.py's positive
        # `_VINYL_RE.search(variant_title)` filter, which would drop every bare
        # colour name ("Black", "Splatter"), the failure mode
        # carparkrecords.py's doc records for its own store.
        #
        # Applied before the count below, not inside the loop: the qualifier
        # exists to disambiguate rows, so it should count variants that can
        # actually become rows. Filtering on format here is safe for item_key
        # stability -- a variant's format does not change as stock moves --
        # which is exactly why availability is *not* filtered here.
        eligible = [
            v for v in variants if cls._is_vinyl((v.get("title") or "").strip())
        ]
        # db.compute_item_key hashes (artist, title, url) and
        # replace_stock_items INSERTs without ON CONFLICT, so two variants of
        # one product sharing a title become two physically duplicated rows
        # under one identity -- indistinguishable in the Store tab, and sharing
        # a single judgment and saved state between them. A multi-variant
        # product therefore qualifies each row with its variant name, the shape
        # nuclearblast.py uses.
        #
        # Counted over the full format-eligible list, never the
        # availability-filtered one: if the qualifier appeared only while a
        # sibling variant happened to be in stock, the title -- and with it
        # item_key -- would change between syncs, orphaning that row's judgment
        # every time stock moved. Format is safe to filter on first because a
        # variant's format does not change as stock moves; availability is not.
        multi_variant = len(eligible) > 1

        items = []
        for variant in eligible:
            if not variant.get("available") and not is_preorder:
                continue
            try:
                price = float(variant["price"])
            except (KeyError, TypeError, ValueError):
                price = None
            display_title = album_title
            variant_title = (variant.get("title") or "").strip()
            # "Default Title" is Shopify's placeholder on a single-variant
            # product; it identifies nothing and must never reach a title.
            if multi_variant and variant_title and variant_title.lower() != _PLACEHOLDER_VARIANT:
                display_title = f"{album_title} — {variant_title}"
            if is_preorder:
                display_title += " (Pre-Order)"
            items.append({
                "artist": artist,
                "title": display_title,
                "format": "Vinyl",
                "price": price,
                "currency": "EUR",
                "url": url,
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @staticmethod
    def _parse_title(title: str) -> tuple:
        """Split `Artist "Album" FORMAT` into its three parts.

        No `vendor` fallback, deliberately, matching cleorecs.py /
        jackpotrecords.py / asianmanrecords.py: `vendor` is expected to hold the
        label here (SPV / Steamhammer / Long Branch), so falling back to it
        would publish the label as the artist -- a row that can never match a
        Discogs release. A title neither regex can parse returns no artist and
        is dropped by the caller instead.
        """
        title = (title or "").strip()
        m = _TITLE_RE.match(title)
        if m:
            return m.group("artist").strip(), m.group("album").strip(), m.group("extra").strip()
        m = _DASH_RE.match(title)
        if m:
            return (m.group("artist").strip(),) + _split_trailing_format(m.group("album").strip())
        return "", title, ""

    @staticmethod
    def _is_vinyl(extra: str) -> bool:
        """Gate on the title's trailing format blurb.

        Negative, not positive: the source collection is the store's own vinyl
        collection, so an unrecognised blurb is kept (a positive filter would
        silently drop stock whose descriptor this list doesn't anticipate) and
        only an explicit non-vinyl format is dropped. A blurb naming both
        (`LP+CD`) is vinyl.
        """
        if _VINYL_RE.search(extra):
            return True
        return not _NON_VINYL_RE.search(extra)

    @staticmethod
    def _is_preorder(product: dict) -> bool:
        tags = product.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")
        return any(_PREORDER_RE.search(t or "") for t in tags)
