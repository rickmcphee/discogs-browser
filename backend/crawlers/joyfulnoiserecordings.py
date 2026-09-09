import math
import re
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
# But the head cannot supply the vinyl signal, only veto it. The store titles
# its hand-made lathe-cut singles with the SONG -- `Can't Let Go Juno
# (Hand-made lathe-cut 7" limited to 100 copies)` -- so for a whole series of
# real records the head names no format at all and the evidence is entirely
# inside the parenthesis. Same for `Limited Edition Box Set + Digital (3xLP on
# deluxe colored vinyl ...)`.
#
# Hence: the head decides whether some OTHER medium owns the product, and the
# full string then has to show vinyl. A head that names vinyl outright
# (`Hardbound Book + 7"`) is not vetoed by the medium sitting beside it.
_VINYL_RE = re.compile(
    r'\bvinyl\b'
    r'|(?<![a-z])\d*\s*[x×]?\s*lps?\b'
    # `flexi` alone, not just `flexi disc`: the store also sells a `Flexi Book`
    # -- one release spiral-bound out of several square flexi discs.
    r'|\bflexi[-\s]?(?:discs?)?\b'
    r'|\blathe[-\s]?cuts?\b'
    r'|\btest\s+pressings?\b'
    r'|\bpicture\s+discs?\b'
    # Record sizes only. An unrestricted inch marker is what lets a poster
    # (`18"x24" Poster`) and a tote bag (`15"W x 16"H`) in, and those are the
    # store's own live listings, not hypotheticals.
    r'|(?<![\d.])(?:5|7|10|12)\s*(?:"|”|″|\s*inch\b)',
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
_CREDIT_RE = re.compile(
    r"^(?P<artist>[^'’\"“]+?)\s*['’]\s*(?P<album>.+?)\s*['’](?=\s|$)\s*(?P<rest>.*)$"
)


class Crawler:
    site_name: str = "Joyful Noise Recordings"
    base_url: str = "https://www.joyfulnoiserecordings.com"
    genre_summary: str = "Indianapolis label and store for exploratory indie rock and outsider pop — Deerhoof, Kishi Bashi, Joan of Arc, Tropical Fuck Storm, Swamp Dogg, Sebadoh and Lou Barlow — plus its White Label Series of one-off artist records, hand-made lathe-cut singles, flexi-discs and test pressings."
    genre: str = "rock"
    crawler_type: str = "catalog"

    async def crawl_catalog(self) -> AsyncIterator[dict]:
        products_seen = 0
        format_named = 0
        artist_missing = 0
        identity_missing = 0
        unreadable_stock = 0
        yielded = 0
        unpriced = 0
        async for product in iter_products(self.base_url, _COLLECTION_SLUG):
            products_seen += 1
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
            unpriced += self._unpriced(product)
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
        if format_named == 0:
            # Unlike a negative format gate, this one is positive: it needs a
            # vinyl word in the variant to admit anything. So the store moving
            # format out of the `Format` option -- into product_type, tags, or
            # a metafield -- would empty the walk in silence rather than
            # merely admitting too much. Nothing else notices that.
            raise RuntimeError(
                f"no product in the {_COLLECTION_SLUG} collection has a variant naming "
                "a vinyl format -- format-source drift")
        if not yielded and unpriced:
            # A record with no usable price is dropped rather than listed
            # (see _items), so a `price` field removed or retyped store-wide
            # would empty the walk instead of merely blanking it -- and an
            # empty walk REPLACES the snapshot. Gated on having yielded
            # nothing, so the store's own unpriced placeholders stay ordinary
            # skipped rows.
            raise RuntimeError(
                f"{_COLLECTION_SLUG} collection yielded no rows while "
                f"{unpriced} in-stock record(s) carry no usable price -- "
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
        url = f"{cls.base_url}/products/{(product.get('handle') or '').strip()}"
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
                "cover_image_url": resolve_cover_image(product, variant),
            })
        return items

    @classmethod
    def _unpriced(cls, product: dict) -> int:
        """In-stock records this product drops for want of a usable price."""
        if not cls._has_identity(product):
            return 0
        return sum(
            1 for variant, _ in cls._pressings(product)
            if variant.get("available") is True and cls._price(variant) is None
        )

    @classmethod
    def _credit(cls, product: dict) -> Tuple[str, str]:
        """(artist, album) for a product, reading `vendor` unless it names a series."""
        vendor = (product.get("vendor") or "").strip()
        title = " ".join((product.get("title") or "").split())
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
        if _BUNDLE_RE.search(" ".join((product.get("title") or "").split())):
            return []
        # Non-mapping entries are dropped here, before anything reads them, so
        # a junk entry is an ordinary skipped row rather than an AttributeError
        # from inside the yield loop.
        variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
        pairs = []
        for variant in variants:
            title = " ".join((variant.get("title") or "").split())
            if not title or _BUNDLE_RE.search(title):
                continue
            if not cls._is_vinyl(title):
                continue
            pairs.append((variant, cls._descriptor(title)))
        # The descriptor is the format's name, with the store's blurb dropped,
        # because the blurb runs to whole paragraphs and the row title has to
        # stay readable. Trimming can in principle collide two of a product's
        # variants -- and a collision is not cosmetic, since item_key hashes
        # the title, so the second row would overwrite the first. Confirmed no
        # product collides today; a product that starts to falls back to the
        # untrimmed titles, which are what distinguished them in the first
        # place.
        heads = [d for _, d in pairs]
        if len(set(heads)) != len(heads):
            return [(v, " ".join((v.get("title") or "").split())) for v, _ in pairs]
        return pairs

    @staticmethod
    def _descriptor(variant_title: str) -> str:
        return variant_title.split("(")[0].strip()

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
    def _has_identity(product: dict) -> bool:
        return bool((product.get("title") or "").strip()) and bool((product.get("handle") or "").strip())

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
