import math
import re
import unicodedata
from typing import AsyncIterator, List, Optional, Tuple

from shopify_catalog import iter_products, resolve_cover_image

# Shopify's built-in all-products collection, not the store's own `vinyl`
# shelf. The shelf is exactly the products tagged `Vinyl`, and that tag is not
# how this store decides what a record is: confirmed live 2026-09-09 that 272
# in-stock products outside the shelf still carry a vinyl variant, among them
# ordinary catalog LPs -- Surfer Blood "1000 Palms" (Sky Blue and Black Vinyl),
# Joan of Arc "1984" (Yellow Vinyl), Tall Tall Trees "A Wave of Golden Things"
# (Gold Vinyl) -- alongside every 7" single, flexi-disc and test pressing.
# Walking the shelf would drop the larger part of the store's vinyl.
#
# `all` was confirmed the whole published catalog rather than a curated
# shelf, which matters here because the store ALSO publishes a hand-made
# collection titled "All": collections.json reports it at a products_count
# below the store's published_products_count, so the two are not the same set
# and reading that count alone would have argued against this walk. The
# built-in wins the handle -- /collections/all/products.json returns exactly
# published_products_count products and exactly the handle set of the
# store-wide /products.json, neither more nor less.
_COLLECTION_SLUG = "all"

# The store keeps format in the VARIANT, under a `Format` option, and nowhere
# else that can be trusted: product_type names the release kind ("Albums",
# "Singles", "Test Pressing", "Box Sets") and says nothing about medium, and
# many products carry no tags at all. So the gate reads variant titles.
#
# Two layers, because neither the head nor the full string works alone.
#
# The head -- the part before the first parenthesis -- is the format's NAME,
# and it is what settles the medium. Read the full string instead and every
# record's own blurb convicts it, since nearly every vinyl variant here says
# "+ Digital" or "Includes MP3 download"; worse, the digital edition of a box
# set is titled `Digital (Includes MP3 and WAV downloads of all 5 LPs ...)`
# and its only vinyl word sits in that blurb.
#
# But the head cannot be RELIED ON for the vinyl signal. Where it does name
# vinyl it is taken at its word and admits the variant on the spot; the trouble
# is how often it says nothing. The store titles its hand-made lathe-cut
# singles with the SONG -- `Can't Let Go Juno (Hand-made lathe-cut 7" limited
# to 100 copies)` -- so for a whole series of real records the head names no
# format at all and the evidence is entirely inside the parenthesis. Same for
# `Limited Edition Box Set + Digital (3xLP on deluxe colored vinyl ...)`.
#
# Hence: the head decides whether some OTHER medium owns the product, and the
# full string then has to show vinyl. A head that names vinyl outright
# (`Hardbound Book + 7"`) is not vetoed by the medium sitting beside it.
# `[a-z]` is ASCII-only even under IGNORECASE, so a lookbehind spelled that way
# treats an accented letter as a separator: `ÉLP CD` matched the embedded `LP`
# and was admitted as vinyl before the `CD` could reject it. These say what they
# mean instead -- "not preceded by a letter", "not followed by a letter or
# digit" -- in any script.
#
# The right-hand one is defined beside its opposite deliberately. Without it the
# inch marker had no closing boundary at all, so `12"CD` read `12"` as a
# complete marker and was admitted before the `CD` veto ran; `dongiovanni`
# records that fixing the left boundaries and leaving the right one ASCII, in
# one commit, is exactly how that bug survived. Both found by Copilot in review
# on PR #337, and both already solved on `dongiovannirecords.py` in PR #323 --
# which pins `12"CD` and `12"Cassette` in its own suite.
_NOT_AFTER_LETTER = r'(?<![^\W\d_])'
_NOT_BEFORE_LETTER_OR_DIGIT = r'(?![^\W_])'
_VINYL_RE = re.compile(
    r'\bvinyl\b'
    r'|' + _NOT_AFTER_LETTER + r'\d*\s*[x×]?\s*lps?\b'
    # `flexi` alone, not just `flexi disc`: the store also sells a `Flexi Book`
    # -- one release spiral-bound out of several square flexi discs.
    r'|\bflexi[-\s]?(?:discs?)?\b'
    r'|\blathe[-\s]?cuts?\b'
    r'|\btest\s+pressings?\b'
    r'|\bpicture\s+discs?\b'
    # Record sizes only. An unrestricted inch marker is what lets a poster
    # (`18"x24" Poster`) and a tote bag (`15"W x 16"H`) in, and those are the
    # store's own live listings, not hypotheticals.
    r'|(?<![\d.])(?:5|7|10|12)\s*(?:["”″]' + _NOT_BEFORE_LETTER_OR_DIGIT + r'|\s*inch\b)',
    re.IGNORECASE,
)
# A pair of inch marks joined by an x is a physical measurement, never a
# record. Restricting the marker above to record sizes is not enough on its
# own, because merchandise is routinely sold at record size -- `12"x12"
# Poster` names a size this crawler otherwise reads as proof of vinyl. Found
# by Copilot in review on PR #337; the live listing that prompted the size
# restriction (`18"x24" Poster`) happened to fall outside it and hid the hole.
#
# A record's own multiplier is not a pair and survives: `4x10" Vinyl Box Set`
# carries no inch mark on the 4, because it counts discs rather than measuring
# one. The optional letter covers the store's `15"W x 16"H` spelling.
_MEASUREMENT_RE = re.compile(
    r'\d+(?:\.\d+)?\s*["”″]\s*[whd]?\s*[x×]\s*\d+(?:\.\d+)?\s*["”″]',
    re.IGNORECASE,
)
# A trailing `+ Digital` (or `+ MP3`, `& WAV`, ...) is the download that comes
# WITH the physical item, not the item itself, so it is cut from the head
# before the head is asked what medium this is. Without that cut the download
# convicts its own record: `Limited Edition Box Set + Digital (3xLP on deluxe
# colored vinyl ...)` is a live pressing whose head names no vinyl, so the
# medium veto rejected it and the blurb never got to answer. Found by Copilot
# in review on PR #337, against the very example the design doc had cited as a
# shape the head alone cannot judge.
#
# Anchored on the joining `+`/`&`, so a variant that IS the download is
# untouched: `Digital (...)` and `MP3 Download (...)` keep their heads whole
# and stay vetoed.
_COMPANION_RE = re.compile(
    r'\s*[+&]\s*(?:instant\s+)?(?:digital|mp3s?|wavs?|aiffs?|downloads?)\b.*$',
    re.IGNORECASE,
)
_NON_VINYL_MEDIA_RE = re.compile(
    # The disc counts take the same multiplier prefix as the vinyl pattern
    # above, and for the same reason: there is no word boundary inside `5xCD`,
    # so a plain \bcds?\b reads straight past it. That is not hypothetical --
    # `5xCD Box Set (... an elaborate 12"x12", 27 page bound-book)` is a live
    # listing whose only inch marker measures the book, and without the prefix
    # nothing here vetoes it before that 12" admits it as a record.
    r'(?<![a-z])\d*\s*[x×]?\s*cds?\b|\bcompact\s+discs?\b'
    r'|(?<![a-z])\d*\s*[x×]?\s*cassettes?\b|(?<![a-z])\d*\s*[x×]?\s*tapes?\b'
    r'|\bdigital\b|\bmp3s?\b|\bwavs?\b|\bdownloads?\b'
    r'|\bbooks?\b|\bzines?\b|\bposters?\b|\btotes?\b|\bshirts?\b'
    r'|(?<![a-z])\d*\s*[x×]?\s*dvds?\b|\bblu-?\s?rays?\b',
    re.IGNORECASE,
)
# The media that are physical goods rather than the download every record here
# ships with. Only these veto a bare container's blurb: the download words
# cannot, because every legitimate record's blurb names one.
_PHYSICAL_MEDIA_RE = re.compile(
    r'(?<![a-z])\d*\s*[x×]?\s*cds?\b|\bcompact\s+discs?\b'
    r'|(?<![a-z])\d*\s*[x×]?\s*cassettes?\b|(?<![a-z])\d*\s*[x×]?\s*tapes?\b'
    r'|\bbooks?\b|\bzines?\b|\bposters?\b|\btotes?\b|\bshirts?\b'
    r'|(?<![a-z])\d*\s*[x×]?\s*dvds?\b|\bblu-?\s?rays?\b',
    re.IGNORECASE,
)
# A lot of more than one release: its price is not any single record's price,
# and the row would attach that price to whichever album it was shelved under.
# `Box Set` is deliberately NOT a bundle word on its own -- the store sells
# single releases that way (`4x10" Vinyl Box Set`, and WHY?'s "Moh Lhean -
# Expanded" as eight 7"s in a box) -- but `Complete Box Set` always names a
# whole series or discography here (Joan of Arc's first five albums, the Gray
# Area cassette series, Danielson's lathe-cut club). `Club` and `Full Set`
# catch the subscription lots that ship a series in one purchase.
#
# Tested against the product title as well as the variant, because the store
# sometimes puts the lot in the product name and a plain format in the variant
# (`Danielson Artist Enabler Club One-Time Payment` / `15 lathe-cuts + Wooden
# Box + Digital`).
_BUNDLE_RE = re.compile(
    r'\bbundles?\b|\bgrab\s+bag\b|\blucky\s+dip\b'
    r'|\bcomplete\s+box\s+set\b|\bfull\s+set\b|\bclub\b',
    re.IGNORECASE,
)
# `Artist 'Album'`, the form the store falls back on when `vendor` names a
# series rather than a person -- every White Label Series record is vendored
# to the series itself. The closing quote must be followed by whitespace or
# the end of the string, which is what stops an apostrophe INSIDE the album
# closing it early (`Ambulances 'Frankie Bacon’s Blue, Blue Heart'` would
# otherwise credit an album of `Frankie Bacon`). The artist group excludes
# quotes outright, so the album's opening quote is always the title's first.
#
# A heuristic, not a guarantee: an inner quote that IS followed by whitespace --
# a possessive plural -- does close early, so `Ambulances 'The Beatles'
# Greatest'` would yield an album of `The Beatles Greatest'`. No title in the
# catalog has that shape. Raised by Copilot in review on PR #337 against the
# `Bacon’s` title, where it does not apply -- that apostrophe is followed by a
# letter -- but the shape is real, and preferring the LAST eligible quote is
# what would close it if a title ever needs it.
_CREDIT_RE = re.compile(
    r"^(?P<artist>[^'’\"“]+?)\s*['’]\s*(?P<album>.+?)\s*['’](?=\s|$)\s*(?P<rest>.*)$"
)


def _text(value) -> str:
    """A whitespace-collapsed string, or "" for anything that is not one.

    Every string field this crawler reads goes through here, and the reason is
    the `or ""` idiom it replaces: that covers a null or absent field, but
    hands a truthy NON-string straight to `.strip()` or `.split()`, which
    raises AttributeError from inside the walk and aborts the whole source.
    One malformed product would stop the catalog refreshing for as long as the
    store served it, leaving every price stale.

    That is not fail-safe, just unexplained -- the raise does keep
    `replace_stock_items()` from running, but it does so by crashing rather
    than by any guard deciding the payload was untrustworthy. Answering ""
    instead routes the product into the identity, artist and title tallies, so
    it is skipped AND counted, and the drift guards get to make that decision
    where they can name it.

    Found by Copilot in review on PR #337, at product level, after the same
    hole was fixed one commit earlier for variant titles alone.
    `theflenser.py` and `monorailmusic.py` both already carry this helper, with
    docstrings making this same argument.

    Normalised to NFC on the way through, which the format gate depends on.
    Its boundaries ask "is a letter next to this token", and in DECOMPOSED text
    the character beside the token is a combining mark rather than the letter
    it belongs to -- so `éLP CD` read as vinyl spelled one way and as a CD
    spelled the other. The same descriptor must not classify two ways
    depending on how it was encoded. NFC is canonical, so any two spellings of
    one string share a form; it does not compose every mark in existence, but
    it makes the reading consistent, which is the property that was missing.
    Every live title is already NFC, so this re-keys nothing.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", value).split())


class Crawler:
    site_name: str = "Joyful Noise Recordings"
    base_url: str = "https://www.joyfulnoiserecordings.com"
    genre_summary: str = "Indianapolis label and store for exploratory indie rock and outsider pop — Deerhoof, Kishi Bashi, Joan of Arc, Tropical Fuck Storm, Swamp Dogg, Sebadoh and Lou Barlow — plus its White Label Series of one-off artist records, hand-made lathe-cut singles, flexi-discs and test pressings."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        variants_unreadable = 0
        format_named = 0
        artist_missing = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        unreadable_prices = 0
        untitled_live = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
            # Counted outside the `pressings` branch below, which is the whole
            # point: a product whose variants cannot be read yields no
            # pressings, so every tally nested in that branch skips it and it
            # reaches no guard at all.
            if not self._has_readable_variants(product):
                variants_unreadable += 1
            # Counted outside the branch too, and for the same reason: an
            # untitled variant is dropped before `available` is read, so it
            # produces no pressing and every tally below skips it.
            untitled_live += self._untitled_live_variants(product)
            # Tallied before the availability filter, and only for products
            # this crawler reads as records, so a sold-out record still
            # vouches for the payload it was read out of.
            pressings = self._pressings(product)
            if pressings:
                format_named += 1
                # Same order _items() skips in, so each tally counts the
                # products that reached it rather than the ones an earlier
                # skip already accounted for.
                if not self._has_identity(product):
                    identity_missing += 1
                elif not all(self._credit(product)):
                    artist_missing += 1
                elif not self._has_readable_stock_flag(pressings):
                    unreadable_stock += 1
            unreadable_prices += self._unreadable_prices(product)
            for item in self._items(product):
                yielded += 1
                yield item
        # db.replace_stock_items() DELETEs this crawler's previous snapshot
        # before inserting, and _sync_stock only skips that call when the crawl
        # raised -- so a completed-but-empty walk is destructive where a raise
        # is inert. Each guard names a distinct way the payload can stop
        # carrying what this crawler reads.
        if products_seen == 0:
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection returned no products -- "
                "renamed, removed, or payload drift")
        if not yielded and variants_unreadable:
            # An unreadable `variants` collection -- absent, retyped, empty,
            # or holding no mapping -- is invisible to every other guard here.
            # `_pressings` reads it through `_raw_variants` and drops
            # non-mappings, so such a product looks exactly like one that
            # simply stocks no records, and a single readable sold-out record
            # elsewhere is enough to keep `format_named` non-zero and wave the
            # empty walk through. Found by Copilot in review on PR #337, the
            # same shape of hole as the artist tally below.
            #
            # Checked before the format guard so it names the upstream cause:
            # when the collection breaks store-wide both conditions hold, and
            # "no variant names a vinyl format" would be true but misleading.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{variants_unreadable} product(s) carry no readable variants "
                "-- variant-source drift")
        if not yielded and untitled_live:
            # A variant with no readable title is dropped before `available` is
            # ever read, and `_has_readable_variants` calls its product
            # readable because the variant IS a mapping -- so the drop reached
            # no guard at all. Titles going blank or retyped across the store's
            # in-stock variants would empty the walk while one sold-out sibling
            # with an intact title kept `format_named` non-zero, and the
            # completed-but-empty walk would delete the snapshot.
            #
            # Checked before the format guard for the same reason the variant
            # guard is: when titles break store-wide both conditions hold, and
            # "no variant names a vinyl format" names the gate that was starved
            # rather than what starved it. Found by Copilot in review on
            # PR #337.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{untitled_live} variant(s) not known to be sold out carry no "
                "readable title -- title-source drift")
        if format_named == 0:
            # Unlike a negative format gate, this one is positive: it needs a
            # vinyl word in the variant to admit anything. So the store moving
            # format out of the `Format` option -- into product_type, tags, or
            # a metafield -- would empty the walk in silence rather than
            # merely admitting too much. Nothing else notices that.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection has a variant naming "
                "a vinyl format -- format-source drift")
        if not yielded and unreadable_prices:
            # A record with no usable price is dropped rather than listed
            # (see _items), so a `price` field removed or retyped store-wide
            # would empty the walk instead of merely blanking it -- and an
            # empty walk REPLACES the snapshot.
            #
            # Counts prices that cannot be READ, not every price the walk
            # declined to use. Gating on `not yielded` does not by itself
            # excuse the store's zero-priced placeholders, which was the
            # original mistake here: they are in-stock records the walk drops,
            # so a tally of dropped records never falls below their number,
            # and this guard would then fire on every empty walk -- pinning a
            # stale snapshot in place on the one payload it is supposed to let
            # through, a catalog that has honestly sold out. Found by Copilot
            # in review on PR #337.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_prices} in-stock record(s) carry no readable price -- "
                "price-source drift")
        if not yielded and artist_missing:
            # `vendor` is the artist for all but the series-vendored records,
            # and _CREDIT_RE only rescues those, so a vendor lost store-wide
            # skips every record in _items() and empties the walk.
            #
            # Counted only for products that ARE records, and only when the
            # walk yielded nothing. A tally taken over every product instead
            # would be satisfied by merch and CD-only products that can never
            # yield a row: they would keep their vendor while the records lost
            # theirs, and the guard would wave through a completed-but-empty
            # walk that deletes the snapshot. Found by Copilot in review on
            # PR #337.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{artist_missing} record(s) carry no artist -- artist-source drift")
        if not yielded and identity_missing:
            # `title` and `handle` are identity, not display: item_key hashes
            # the row's artist, title and URL, so a product missing either is
            # skipped rather than emitted under a fresh identity that would
            # orphan the judgments and saves keyed on its old one. Skipped
            # rows leave the walk looking sold out.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{identity_missing} record(s) carry no title or handle -- "
                "identity-source drift")
        if not yielded and unreadable_stock:
            # An empty result is only trustworthy when every product that
            # could have yielded a row was readable and simply out of stock.
            # Counting unreadable products rather than readable ones is what
            # catches the partial case: one genuinely sold-out record must not
            # vouch for a catalog that has gone unreadable behind it.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unreadable_stock} record(s) carry no readable availability flag -- "
                "stock-source drift")

    @classmethod
    def _items(cls, product: dict) -> List[dict]:
        pressings = cls._pressings(product)
        if not pressings:
            return []
        if not cls._has_identity(product):
            return []
        artist, album = cls._credit(product)
        if not artist or not album:
            return []
        url = f"{cls.base_url}/products/{_text(product.get('handle'))}"
        items = []
        for variant, descriptor in pressings:
            # Only the literal True admits a variant: the string "false" is
            # truthy, so a falsiness test would publish a sold-out record as in
            # stock. Anything else -- False, "false", 1, None, absent -- is
            # skipped, which is also what keeps this filter and
            # _has_readable_stock_flag agreeing on what "readable" means.
            #
            # No sold-out-wording bypass is needed in either direction: every
            # variant whose title announces "[SOLD OUT]" or "We are SOLD OUT"
            # was confirmed live to carry available=False, so the flag already
            # says everything the wording does.
            if variant.get("available") is not True:
                continue
            price = cls._price(variant)
            # A record the store prices at zero is not a listing, and every
            # one of them here is an internal placeholder rather than a
            # record: two "VIP LATHE TEST" products, and a literal duplicate
            # (handle `copy-of-...`) whose vendor and product_type are both
            # the string "hidden" -- that last would otherwise reach the Store
            # tab crediting an artist named "hidden". Emitting it price-blank
            # would put an unbuyable row in front of the user under a name
            # nobody can match; the drift guard above is what keeps this from
            # quietly emptying the walk if prices break store-wide.
            if price is None:
                continue
            items.append({
                "artist": artist,
                "title": f"{album} — {descriptor}" if descriptor else album,
                "format": "Vinyl",
                "price": price,
                "currency": "USD",
                "url": url,
                "cover_image_url": cls._cover(product, variant),
            })
        return items

    @classmethod
    def _unreadable_prices(cls, product: dict) -> int:
        """In-stock records this product drops for want of a READABLE price.

        Deliberately not every record dropped for want of a *usable* one: the
        store's zero-priced placeholders are usable-price failures but not
        drift, and counting them broke the guard outright. See
        `_price_unreadable`.
        """
        if not cls._has_identity(product):
            return 0
        return sum(
            1 for variant, _ in cls._pressings(product)
            if variant.get("available") is True and cls._price_unreadable(variant)
        )

    @classmethod
    def _credit(cls, product: dict) -> Tuple[str, str]:
        """(artist, album) for a product, reading `vendor` unless it names a series."""
        vendor = _text(product.get("vendor"))
        title = _text(product.get("title"))
        m = _CREDIT_RE.match(title)
        # The parse only wins where `vendor` demonstrably is not the artist.
        # Requiring the vendor to be absent from the title is what keeps it off
        # the records the store titles with a quoted album while vendoring them
        # correctly -- there the two agree, so deferring to `vendor` costs
        # nothing and risks nothing.
        if m and vendor and vendor.lower() not in title.lower():
            artist = m.group("artist").strip()
            album = " ".join(f'{m.group("album").strip()} {m.group("rest").strip()}'.split())
            if artist and album:
                return artist, album
        return vendor, title

    @classmethod
    def _pressings(cls, product: dict) -> List[Tuple[dict, str]]:
        """(variant, descriptor) for each of a product's variants that is a record."""
        if _BUNDLE_RE.search(_text(product.get("title"))):
            return []
        # Non-mapping entries are dropped here, before anything reads them, so
        # a junk entry is an ordinary skipped row rather than an AttributeError
        # from inside the yield loop. `_raw_variants` settles what is even a
        # collection first -- iterating that directly is what crashed on a
        # truthy scalar.
        variants = [v for v in cls._raw_variants(product) if isinstance(v, dict)]
        pairs = []
        for variant in variants:
            title = cls._variant_title(variant)
            if not title or _BUNDLE_RE.search(title):
                continue
            if not cls._is_vinyl(title):
                continue
            # The descriptor is the variant's WHOLE title, as every sibling
            # crawler uses, and the reason is identity rather than taste.
            # item_key hashes the row title, so the descriptor has to be a
            # function of this variant and nothing else.
            #
            # This trimmed the store's blurb off first, for readability -- its
            # variant titles run to whole paragraphs -- and fell back to the
            # untrimmed titles when two of a product's heads collided, since
            # the blurb was the only thing telling them apart. That made a
            # pressing's identity depend on its SIBLINGS: five products collide
            # today, two of them with an in-stock variant sitting beside the
            # sold-out one it collides with, so the store deleting that dead
            # variant -- routine housekeeping -- would flip the survivor from
            # its full title back to the trimmed one, change its item_key, and
            # orphan the saves, judgments and listings hanging off it. Found by
            # Copilot in review on PR #337.
            #
            # Untrimmed is what the collision fallback reached for anyway, so
            # using it always costs only the readability, and buys a title no
            # sibling variant can move.
            pairs.append((variant, title))
        return pairs

    @staticmethod
    def _is_vinyl(variant_title: str) -> bool:
        """Whether a variant is a record, from its title alone."""
        # Measurements are dropped before anything looks for vinyl, so a
        # record-sized one cannot stand in as the evidence. Dropping rather
        # than rejecting outright keeps a record that merely states its
        # dimensions: the strong words are still there to be found.
        title = _MEASUREMENT_RE.sub(" ", variant_title)
        head = _COMPANION_RE.sub("", title.split("(")[0]).strip()
        if _VINYL_RE.search(head):
            return True
        if _NON_VINYL_MEDIA_RE.search(head):
            return False
        # The head names no medium at all: a bare container ("Box Set"), or
        # the song, which is how the store titles its lathe-cut singles. The
        # blurb answers instead -- but a competing PHYSICAL medium there still
        # vetoes, which is what keeps out a box of cassettes whose lid happens
        # to be a playable lathe-cut single.
        if _PHYSICAL_MEDIA_RE.search(title):
            return False
        return bool(_VINYL_RE.search(title))

    @staticmethod
    def _raw_variants(product: dict) -> list:
        """The product's `variants` collection, or [] when it is not one.

        isinstance before anything iterates: a truthy JSON scalar (`1`, `true`,
        `2.5`) is not iterable, so a comprehension over it raises TypeError and
        aborts the whole source before `variant-source drift` can name the
        cause. A string or dict is worse than useless in the other direction --
        both ARE iterable, and would invent entries out of characters or keys.
        A retyped `variants` is a broken payload, so it reads as no variants
        and lands in that guard, which is the one that explains it.

        Shared with `_has_readable_variants` rather than re-derived there: the
        two disagreeing about what counts as a collection is exactly how the
        crash got in, since that one already tested isinstance and `_pressings`
        did not. Found by Copilot in review on PR #337; `theflenser.py` carries
        the same helper, for the same reason, from PR #331.
        """
        raw = product.get("variants")
        return raw if isinstance(raw, list) else []

    @staticmethod
    def _variant_title(variant: dict) -> str:
        """A variant's title as whitespace-normalised text, or "" if it is none.

        isinstance before `.split()`: a retyped title (a number, a mapping, a
        list) is truthy, so `or ""` hands it straight through and `.split()`
        raises AttributeError -- the same shape as the truthy scalar
        `variants` one level up, and it aborts the source the same way.
        """
        return _text(variant.get("title"))

    @staticmethod
    def _cover(product: dict, variant: dict) -> Optional[str]:
        """resolve_cover_image() with its two collections type-checked first.

        The shared helper reads `variant["featured_image"].get(...)` and
        `product["images"][0].get(...)` behind `or` guards, which catch a
        missing or null field but pass a RETYPED one straight through to
        `.get()`. A raise there aborts the whole source over one product's
        artwork -- which is display-only, so the cost is wildly out of
        proportion to what is broken: every price in the snapshot goes stale
        because one record's image field is a string.

        Guarded here rather than in `shopify_catalog`, because every Shopify
        crawler in the fleet reads that helper and this is one store's payload,
        not a fleet-wide change to make from inside this crawler.
        `monorailmusic.py` draws the same boundary for the same reason. Found
        by Copilot in review on PR #337.

        Type-checking the two CONTAINERS is not enough, which is the second
        thing found here: the helper returns whatever sits at `src` without
        looking at it, so a nested `{"src": 123}` came back as the row's
        `cover_image_url` in breach of the `Optional[str]` contract, and
        `replace_stock_items()` then hands an int to a Postgres TEXT column --
        killing the whole refresh over display-only artwork, which is the
        failure this boundary exists to prevent, arriving one level deeper.
        So `src` is required to be a non-empty string too, and an image that
        has no usable one is passed over rather than allowed to answer.
        """
        raw = product.get("images")
        raw = raw if isinstance(raw, (list, tuple)) else []
        images = [i for i in raw if _text(i.get("src") if isinstance(i, dict) else None)]
        featured = variant.get("featured_image")
        if not (isinstance(featured, dict) and _text(featured.get("src"))):
            variant = {**variant, "featured_image": None}
        return resolve_cover_image({**product, "images": images}, variant)

    @classmethod
    def _untitled_live_variants(cls, product: dict) -> int:
        """Variants dropped for want of a title that were not proven sold out.

        `_pressings` drops an untitled variant before it ever reads
        `available`, and `_has_readable_variants` calls the product readable
        because the variant IS a mapping -- so this drop reached no guard at
        all. Titles going blank or retyped across the store's in-stock
        variants would empty the walk while a sold-out sibling with an intact
        title kept `format_named` non-zero, and the completed-but-empty walk
        would delete the snapshot. Found by Copilot in review on PR #337.

        Only `available is False` excuses a variant, never a truthy or
        unreadable flag: a sold-out variant with no title is a dead row the
        store stopped maintaining, but anything else is a record this crawler
        cannot see. That asymmetry is the point -- the sold-out sibling must
        not vouch for the ones that are still live.
        """
        return sum(
            1 for v in cls._raw_variants(product)
            if isinstance(v, dict)
            and not cls._variant_title(v)
            and v.get("available") is not False
        )

    @classmethod
    def _has_readable_variants(cls, product: dict) -> bool:
        """Whether a product's `variants` is a non-empty list of mappings.

        Every published product on this store carries one, so anything else is
        drift rather than a product without pressings. Non-mappings still get
        dropped silently in `_pressings`, leaving an isolated malformed product
        an ordinary skipped row -- this only decides whether it is COUNTED.
        """
        variants = cls._raw_variants(product)
        if not variants:
            return False
        return all(isinstance(v, dict) for v in variants)

    @staticmethod
    def _has_identity(product: dict) -> bool:
        return bool(_text(product.get("title"))) and bool(_text(product.get("handle")))

    @staticmethod
    def _has_readable_stock_flag(pressings: List[Tuple[dict, str]]) -> bool:
        # every(), not any(): one readable variant does not make the product
        # readable. A product whose black pressing is a readable False and
        # whose coloured pressing carries the string "false" yields nothing,
        # and under any() would vouch for an emptiness half its own doing.
        return bool(pressings) and all(
            isinstance(v.get("available"), bool) for v, _ in pressings)

    @staticmethod
    def _price(variant: dict) -> Optional[float]:
        raw = variant.get("price")
        # bool before float(): bool is an int subclass, so True would price a
        # record at 1. nan is the other one a truthiness check cannot catch.
        if isinstance(raw, bool):
            return None
        try:
            price = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        return price

    @staticmethod
    def _price_unreadable(variant: dict) -> bool:
        """Whether a variant's price cannot be read as a number at all.

        Strictly narrower than `_price() is None`, and the gap between the two
        is the whole point. A price of zero reads perfectly well -- the store
        means it, and every one on a record here is a placeholder (see
        _items) -- so counting those as evidence of drift leaves the tally
        permanently non-zero, and a guard gated on `not yielded` then fires on
        EVERY empty walk, including the honest one where the catalog has
        simply sold out. That inverts the guard: it would pin a stale snapshot
        in place precisely when the store is telling the truth. Found by
        Copilot in review on PR #337.

        Absent, retyped and non-finite is the shape a price field actually
        breaks in, and only those are counted -- matching how the stock and
        variant guards already read their own sources.
        """
        raw = variant.get("price")
        # A boolean price is a retyped field, not a cheap record: True would
        # otherwise parse to a perfectly finite 1.0.
        if isinstance(raw, bool):
            return True
        try:
            return not math.isfinite(float(raw))
        except (TypeError, ValueError):
            return True
