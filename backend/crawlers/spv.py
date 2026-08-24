import re
from typing import AsyncIterator

from shopify_catalog import iter_products, resolve_cover_image

_COLLECTION_SLUG = "vinyl"

# Product titles are `Artist "Album" FORMAT`, with the album in straight or
# typographic double quotes and a trailing format/edition blurb:
#   Sodom "1982" LP (exclusive)
#   Magnum "The Monster Roars" LP (white & black marbled vinyl)
# Same two-stage quoted-primary/dash-fallback shape as asianmanrecords.py,
# which quotes its album titles the same way, including its optional
# separator before the opening quote (`Artist - "Album"`). Widened
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
# It carries the SAME vocabulary as the gates, deliberately -- see the note
# below. An earlier version was narrower on the reasoning that this expression
# rewrites the stored title rather than just gating on it; that reasoning
# produced four separate escapes and is not worth restoring. "EP" stays out of
# both tuples, though, and for that original reason: an EP is not a format
# decision (EP pressings exist on vinyl and CD alike), so stripping it would
# edit titles for no classification gain.
# One vocabulary, three uses. These lists were maintained as separate literals
# and drifted apart four times in review -- disc counts, then Digital, then
# merch, then Book and MC -- each time letting a non-vinyl product through on
# whichever path carried the shorter list. Deriving every expression from the
# same two tuples makes that specific mistake impossible: a word added here
# reaches the product gate, the variant gate and the dash-path stripper at once.
# Every noun carries s?: `\blp\b` cannot match the "LPs" in "2 LPs + CD",
# because there is no word boundary between the p and the s. On the vinyl side
# that silently *dropped* real stock -- the override failed, then the CD half
# matched the negative side -- and `fatherdaughterrecords.py` already carries a
# comment about this exact trap that this crawler did not inherit. On the merch
# side it let the dimension bug below back in as "12\" x 12\" Posters". Both
# found in review on PR #165. `patche?s?` rather than `patch(?:es)?` so the
# structural guard test can still derive a sample by stripping `?`.
_VINYL_WORDS = (r'\d*[x×]?lps?', r'vinyls?', r'picture\s+discs?')
# Split media from merch because the two behave differently against an inch
# marker, not because the stripper cares -- it still sees the concatenation.
# An inch marker next to a *media* format is a real bundle ("10 INCH + CD");
# next to *merch* it is usually a measurement ("12\" x 12\" Poster"), so it must
# not be read as a vinyl claim. See _is_vinyl.
_NON_VINYL_MEDIA_WORDS = (
    r'\d*[x×]?cds?', r'digital', r'digipa[kc]k?s?', r'cassettes?', r'tapes?',
    r'mcs?', r'\d*[x×]?dvds?', r'blu-?rays?',
)
_MERCH_WORDS = (
    r't-?shirts?', r'shirts?', r'hoodies?', r'longsleeves?',
    r'posters?', r'patche?s?', r'flags?', r'mugs?', r'books?',
)
_NON_VINYL_WORDS = _NON_VINYL_MEDIA_WORDS + _MERCH_WORDS
# Inch markers stay separate from the word tuples: the mark is a non-word
# character, so a trailing \b cannot follow it.
# The hyphen is optional and load-bearing: "12-INCH VINYL"/"7-INCH VINYL" is
# established notation in this repo (asianmanrecords.py's _VINYL_TYPES), and
# without it `12-INCH + CD` missed the vinyl override and dropped as a CD.
# "7-INCH" alone was right only by accident -- the override never fired, the
# row survived because nothing negative matched. Found in review on PR #165.
_INCH = r'\b\d{1,2}\s*-?\s*(?:"|inch\b)'


def _alternation(*tuples):
    return "|".join(word for group in tuples for word in group)


# Finding the trailing format run took three attempts, so the reasoning is
# recorded rather than just the answer.
#
#   leftmost match      -> 'The Book of Souls LP' split at 'Book', album 'The'
#   greedy prefix       -> '1982 LP + CD' split at ' CD', album '1982 LP +',
#                          and the bundle dropped because _is_vinyl never saw LP
#   run-must-reach-end  -> '1982 Digital Download' stopped matching at all,
#                          because 'Download' is not a format token
#
# What all three got wrong is that the split point is the start of the last
# *run* of format tokens, not the first token, not the last token, and not a
# region that has to be pure format to the end. So: find every format token,
# anchor on the last one, then walk left across any tokens joined to it by only
# a connector. Everything from there to the end is the format blurb, trailing
# qualifier words ('Download') and parentheticals ('(exclusive)') included.
_FORMAT_TOKEN_RE = re.compile(
    r'\b(?:%s)\b|%s' % (_alternation(_VINYL_WORDS, _NON_VINYL_WORDS), _INCH),
    re.IGNORECASE,
)
_CONNECTOR_ONLY_RE = re.compile(r'^[\s+/&,]*$')


_PLACEHOLDER_VARIANT = "default title"
_PREORDER_RE = re.compile(r'pre[\s_-]?order', re.IGNORECASE)
# \d*[x×]? on the LP/CD/DVD forms: Shopify stores write multi-disc counts both
# ways ("2LP" and "2xLP"), and this repo's own fixtures carry the x form (see
# test_temporaryresidence_crawler.py). Siblings already spell it \b\d*x?lp\b;
# the × is added because a store that types the real multiplication sign should
# not silently fall through the gate.
# The inch alternative is deliberately spelled the same way as
# _FORMAT_TOKEN_RE's. They disagreed before: this gate took only an
# unspaced mark on three specific sizes (7/10/12"), while the stripper also
# accepted a space and the word INCH -- so `10 INCH + CD` lost the vinyl
# override and was dropped as a CD, while `12" + CD` was kept. Same shape of
# bug as the 2xLP+CD one: a bundle is only safe if the override recognises
# every vinyl spelling the rest of the module does.
_VINYL_WORD_RE = re.compile(r'\b(?:%s)\b' % _alternation(_VINYL_WORDS), re.IGNORECASE)
_INCH_RE = re.compile(_INCH, re.IGNORECASE)
_MERCH_RE = re.compile(r'\b(?:%s)\b' % _alternation(_MERCH_WORDS), re.IGNORECASE)
_NON_VINYL_RE = re.compile(
    r'\b(?:%s)\b' % _alternation(_NON_VINYL_WORDS), re.IGNORECASE
)
# The \d* on cd/dvd is load-bearing: a disc count binds to the format word with
# no word boundary between them, so a bare \bcds?\b cannot match the "CD" in
# "2CD" and a double-CD edition was passing the gate as Vinyl. _VINYL_WORD_RE's
# \b\d*lp\b already had the same allowance for "2LP"; this brings the negative
# side into line. A bundle naming both ("LP+2CD") is still vinyl -- _VINYL_WORD_RE
# short-circuits ahead of this.


def _split_trailing_format(album: str) -> tuple:
    """Split the trailing format run off an album title -> (album, format)."""
    matches = list(_FORMAT_TOKEN_RE.finditer(album))
    if not matches:
        return album, ""
    run_start = matches[-1].start()
    for earlier in reversed(matches[:-1]):
        if not _CONNECTOR_ONLY_RE.match(album[earlier.end():run_start]):
            break
        run_start = earlier.start()
    remainder = album[:run_start].strip()
    # No remainder means the album *is* the format word ("Tape"); leave it be.
    if not remainder or remainder == album:
        return album, ""
    return remainder, album[run_start:].strip()


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
        # `_VINYL_WORD_RE.search(variant_title)` filter, which would drop every bare
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

        Merch is checked before the inch marker because a dimension is not a
        format claim. `_INCH` matches any 1-2 digit measurement, so a bare
        `12"` used to override an explicit merch word and publish
        `12" x 12" Poster` as Vinyl -- a title shape this repo already meets
        (`test_cleorecs_crawler.py`'s poster fixture). Ordering the checks this
        way keeps real media bundles (`10 INCH + CD`) and vinyl-plus-merch
        bundles (`LP + T-Shirt`), because both name a format outright.
        """
        if _VINYL_WORD_RE.search(extra):
            return True
        if _MERCH_RE.search(extra):
            return False
        if _INCH_RE.search(extra):
            return True
        return not _NON_VINYL_RE.search(extra)

    @staticmethod
    def _is_preorder(product: dict) -> bool:
        tags = product.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")
        return any(_PREORDER_RE.search(t or "") for t in tags)
