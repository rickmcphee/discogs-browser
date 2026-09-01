import json
import math
import random
import re
import time
import unicodedata
from asyncio import sleep
from typing import Optional
from urllib.parse import urlparse

from crawler import BotDetectedError, clean_search_text
from logging_config import get_logger

log = get_logger("crawlers.roughtrade")

_CHALLENGE_TITLES = ("just a moment", "attention required", "checking your browser")

_SETTLE_TIMEOUT_MS = 15_000

# The Discogs artist-name disambiguator ("Nirvana (2)"). Stripped for
# identity comparison without clean_search_text()'s other removals.
_ARTIST_DISAMBIGUATOR_RE = re.compile(r"\s*\(\d+\)\s*$")

# How long to keep re-reading a page whose machine-readable signals have not
# appeared yet before declaring drift: a cleared Cloudflare challenge (and any
# late-hydrated markup) can update <title> before the replacement document's
# head has finished loading.
_SIGNALS_TIMEOUT_MS = 5_000

# schema.org availability can arrive as a full URL ("https://schema.org/OutOfStock")
# or a bare token; only the tail is meaningful. PreOrder/BackOrder stay purchasable
# -- Rough Trade trades heavily in pre-orders, and a pre-order price is exactly
# what a wantlist watcher wants to see.
_UNAVAILABLE_RE = re.compile(r"(outofstock|soldout|discontinued)\s*$", re.IGNORECASE)

# The purchasable schema.org availability states, as normalized IRI tails.
# Anything present that is neither here nor unavailable is ambiguous.
_AVAILABLE_TAILS = frozenset({
    "instock", "preorder", "presale", "backorder", "limitedavailability",
    "onlineonly",
})

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
    'product:availability', 'og:availability',
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
    # (same rationale as discogs_marketplace._finite_price). Booleans are
    # rejected before coercion -- float(True) is 1.0, and a malformed
    # JSON-LD price of `true` must not persist as a real $1.
    if isinstance(value, bool):
        return None
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
    """Lowercased words for identity comparison: diacritics folded, but
    Unicode letters preserved -- reusing the ASCII URL slugification here
    would discard every non-Latin character, equating same-artist titles
    that differ only in them ("Album 日本" vs "Album 中国")."""
    if not text:
        return []
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    # Apostrophes split rather than vanish -- deleting them fuses "We're"
    # into the same identity as "Were", and with the guessed slugs also
    # colliding, a wrong-product landing would pass both identity checks.
    # Both comparison sides render the apostrophe, so they split alike.
    # '&' survives as its own token for the same reason: "Sam & Dave" and a
    # different artist "Sam Dave" collide on the guessed slug, and identity
    # is what has to tell them apart.
    return [w for w in re.split(r"[^\w&]+", text) if w]


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


# The parenthesised format shape of the later title segments ("- (Vinyl
# LP)", "- (CD)", "- (LP - Rainbow Road)", '- (12")'). Numeric-inch markers
# are vinyl -- _PRODUCT_TITLE_SHAPE_RE already counts them as product-page
# evidence, so the format guard must read them too.
_PAREN_FORMAT_RE = re.compile(
    r"\(\s*(vinyl(?:\s+lp)?|lp|cd|\d+\s*\")", re.IGNORECASE
)


def _page_format_after_core(candidate_raw: str, title_rest: str) -> Optional[str]:
    """"vinyl", "cd", or None when the title's format signals are absent or
    contradict each other (unknown stays accepted downstream).

    Read only from the positions *outside the matched product-name core* --
    its trailing marker and the parenthesised segments after it. Text inside
    the core is the release's own name, where "Live on Vinyl" or a title's
    literal "(Vinyl)" would put a spurious signal on its own CD page and
    neutralise the guard.
    """
    signals = set()
    # Trailing markers can end the matched core itself ("{Title} on CD") or
    # the pre-branding suffix after it ("{Title} - Deluxe on CD") -- reading
    # only the core would let a vinyl target accept the latter's CD price.
    after_core_primary = title_rest.split(" | ")[0][len(candidate_raw):]
    for marker in (_FORMAT_MARKER_RE.search(candidate_raw),
                   _FORMAT_MARKER_RE.search(after_core_primary)):
        if marker:
            signals.add("cd" if marker.group(1).lower() == "cd" else "vinyl")
    for m in _PAREN_FORMAT_RE.finditer(title_rest[len(candidate_raw):]):
        signals.add("cd" if m.group(1).lower() == "cd" else "vinyl")
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


def _title_core_matches(expected: list, got: list,
                        allow_truncation: bool = True) -> bool:
    """Does a page's product-name core name exactly this release?

    `got` is the name core -- delimiter/branding/format-marker segments
    already stripped -- so trailing words are a *sibling title* ("Greatest
    Hits Volume Two" for "Greatest Hits"), not edition noise: equality is
    required, word for word. The one relaxation is the documented mid-word
    truncation of live page titles ("...Start Your Ear Off R"): the final
    word may be a leading fragment of the final expected word, once the
    matched span has reached the length live titles truncate at. Length
    alone cannot prove a cut, though -- a real sibling product whose full
    title *is* that leading fragment reads identically -- so the caller
    only allows it with independent evidence that the full title names the
    landed page (search() requires the landed slug to be the full title's).
    """
    if not expected or not got:
        return False
    if got == expected:
        return True
    if not allow_truncation:
        return False
    if len(got) != len(expected) or got[:-1] != expected[:-1]:
        return False
    fragment = got[-1]
    if not fragment or fragment in _SUFFIX_FRAGMENT_STOP:
        return False
    if not expected[-1].startswith(fragment):
        return False
    return len(" ".join(got)) >= _TRUNCATION_MIN_CHARS


_CANONICAL_SLUG_SUFFIX_RE = re.compile(r"-\d+$")


def _landed_slug_is_full_title(landed_url: str, title: str) -> bool:
    """Does the landed URL's product slug spell out the full release title?

    The independent evidence the truncation relaxation needs: candidate
    URLs are built from the full title's slug, so a page still sitting at
    that slug (or at it plus the canonical numeric suffix a redirect
    appends -- "/sample-album" -> "/sample-album-155") is the full-title
    product, and a cut <title> is genuinely display truncation. A redirect
    that lands anywhere else -- say a platform fuzzy-matching the unknown
    slug "/international-superhits" to a real sibling product at
    "/international-super" -- offers no such proof, and there the cut
    reading would persist the sibling's price.
    """
    slug = _slugify(title)
    if not slug:
        return False
    last = urlparse(landed_url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    if last == slug:
        return True
    return (bool(_CANONICAL_SLUG_SUFFIX_RE.search(last))
            and _CANONICAL_SLUG_SUFFIX_RE.sub("", last) == slug)


def _name_matches(name: str, artist: str, title: str) -> bool:
    """Does a Product node's name unambiguously describe this release?

    Stricter than the page-title check: every accepted node contributes
    offers, so a name merely *starting* with the title would let a sibling
    product ("Greatest Hits Volume Two" for release "Greatest Hits") supply
    the stored price. Two shapes match: the bare title as the *whole* name
    (no suffix tolerance -- "{title} - {anything}" also reads as someone
    else's "Artist - Title"), or "{Artist} - {Title}[ - edition suffix]"
    with the first segment anchored to this release's artist, where
    progressive joins let the title carry its own delimiter. JSON-LD names
    are not length-truncated the way page titles are, so no truncation
    relaxation applies here.
    """
    title_words = _norm_words(title)
    if not title_words:
        return False
    # The bare-title shape requires the *whole* name to equal the title: a
    # release titled with someone else's artist name ("Other Artist") must
    # not claim a carousel node named "Other Artist - Different Album", so a
    # trailing segment after a bare-title match is never edition tolerance.
    if _norm_words(name) == title_words:
        return True
    # Edition suffixes are tolerated only in the artist-qualified shape,
    # where the first segment anchoring to this release's artist rules the
    # someone-else's-record reading out. Progressive joins let the title
    # carry its own delimiter ("Sample Album - Deluxe" + "- Red Vinyl").
    segments = name.split(" - ")
    artist_words = _norm_words(artist)
    if not artist_words or _norm_words(segments[0]) != artist_words:
        return False
    rest = segments[1:]
    for end in range(1, len(rest) + 1):
        if _norm_words(" - ".join(rest[:end])) == title_words:
            return True
    return False


def _type_tail(t: str) -> str:
    """The bare type name of a JSON-LD @type value -- "Product" from
    "Product", "schema:Product", or "https://schema.org/Product" alike:
    @type is an IRI, and the compact and expanded encodings are all valid
    ways for the live markup to spell the same type."""
    return t.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _product_nodes(ldjson_texts: list) -> tuple:
    """(Product nodes, malformed script count) across the page's JSON-LD --
    top-level objects, list roots, and @graph members alike. A script that
    does not parse is counted, not silently discarded: it could have been
    this product's node, so a page carrying one is half-parsed and must not
    produce a confirmed miss or a partial cheapest result."""
    nodes = []
    malformed = 0
    for raw in ldjson_texts:
        try:
            data = json.loads(raw or "")
        except (TypeError, ValueError):
            malformed += 1
            continue
        roots = data if isinstance(data, list) else [data]
        for root in roots:
            if not isinstance(root, dict):
                # Parsing to null/a string/a number is as unreadable as not
                # parsing: it could have been this product's node, and
                # silently dropping it would let an out-of-stock sibling
                # turn a half-parsed page into a confirmed miss.
                malformed += 1
                continue
            graph = root.get("@graph")
            if isinstance(graph, dict):
                # JSON-LD permits a single node object here, not only a
                # list -- ignoring it would drop this product's own node.
                graph = [graph]
            candidates = [root] + (graph if isinstance(graph, list) else [])
            for node in candidates:
                if not isinstance(node, dict):
                    continue
                node_type = node.get("@type")
                types = node_type if isinstance(node_type, list) else [node_type]
                if any(_type_tail(t) == "Product"
                       for t in types if isinstance(t, str)):
                    nodes.append(node)
    return nodes, malformed


def _node_scope(node: dict, product_path: str) -> str:
    """Where a Product node's own url/@id says it belongs.

    "match": its identifiers name this product's path (locale-full or
    locale-less); "other": they name some other path -- a recommendation
    node, whatever its name claims; "ambiguous": an identifier is present
    but unreadable, or url and @id contradict each other -- a node whose url
    claims this page while its @id names another product has no identity
    this parser can trust; "unknown": it carries neither field. Both fields
    are read when both exist: returning on the first would let a carousel
    node ride a matching url past an @id naming its real product.
    """
    field_verdicts = []
    for key in ("url", "@id"):
        value = node.get(key)
        if value is None:
            continue
        # JSON-LD permits an array value; each member is read, and a present
        # but unreadable value must not fall through to name scoping -- that
        # is exactly the path a mis-shaped carousel node would take.
        members = value if isinstance(value, list) else [value]
        verdicts = set()
        for member in members:
            if not isinstance(member, str) or not member.strip():
                verdicts.add("unreadable")
                continue
            clean = member.split("?", 1)[0].split("#", 1)[0].rstrip("/")
            if not clean:
                # A fragment- or query-only identifier ("#product") is
                # document-relative -- it carries no cross-product evidence.
                continue
            parsed = urlparse(clean)
            if parsed.scheme or parsed.netloc:
                # A suffix match alone would accept another origin, or
                # another storefront locale whose same-slug product prices
                # in a different currency; the host and the exact locale
                # path both have to agree.
                if parsed.netloc.lower() not in ("www.roughtrade.com", "roughtrade.com"):
                    verdicts.add("other")
                    continue
                path = parsed.path
            else:
                path = clean
            path = "/" + path.strip("/")
            verdicts.add(
                "match" if path in (product_path, "/en-us" + product_path) else "other"
            )
        # Within one field a member explicitly naming this product's path
        # outweighs aliases; a carousel node for another product never lists
        # this product's path among its own aliases.
        if "match" in verdicts:
            field_verdicts.append("match")
        elif "other" in verdicts:
            field_verdicts.append("other")
        elif "unreadable" in verdicts:
            field_verdicts.append("ambiguous")
        # else: every member was fragment/query-only -- no evidence.
    if not field_verdicts:
        return "unknown"
    if "ambiguous" in field_verdicts:
        return "ambiguous"
    if "match" in field_verdicts and "other" in field_verdicts:
        # url and @id disagree about which product this node is. The alias
        # argument does not span fields: these are two declarations of the
        # node's identity, and a contradiction between them is exactly the
        # shape a mislabelled carousel node would take.
        return "ambiguous"
    return field_verdicts[0]


_OFFER_TYPE_TAILS = frozenset({"Offer", "AggregateOffer", "OfferForPurchase",
                               "OfferForLease"})


def _is_offer_shaped(member) -> bool:
    """A dict with no @type, or one whose @type names a supported Offer
    kind by IRI tail. An explicit non-Offer node ("Demand") must not have
    its price fields trusted -- and a substring test would let
    "OfferCatalog" (or any drifted "...Offer..." coinage) pass as one."""
    if not isinstance(member, dict):
        return False
    node_type = member.get("@type")
    if node_type is None:
        return True
    types = node_type if isinstance(node_type, list) else [node_type]
    return any(
        isinstance(t, str) and _type_tail(t) in _OFFER_TYPE_TAILS for t in types
    )


def _iter_offers(offers) -> tuple:
    """(offer dicts, dropped count) -- a payload or member this parser
    cannot read as an offer is counted, not silently discarded: it is a
    half-parsed page, and swallowing it would let an out-of-stock sibling
    offer turn the page into a confirmed miss."""
    if offers is None:
        return [], 0
    members = offers if isinstance(offers, list) else [offers]
    usable = [o for o in members if _is_offer_shaped(o)]
    return usable, len(members) - len(usable)


def _is_aggregate_offer(offer: dict) -> bool:
    node_type = offer.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    return any(
        isinstance(t, str) and _type_tail(t) == "AggregateOffer" for t in types
    )


_CURRENCY_CODE_RE = re.compile(r"^[A-Za-z]{3}$")


def _clean_currency(value) -> Optional[str]:
    """A trimmed, uppercased ISO-4217-shaped code, or None.

    Downstream only uppercases (db._normalized_currency, the frontend's
    formatPrice), so a verbatim " USD " or "usd" would open a separate
    price bucket and render without its symbol. Anything that is not three
    letters after trimming is schema drift, not a code to persist.
    """
    if not isinstance(value, str):
        return None
    code = value.strip()
    if not _CURRENCY_CODE_RE.match(code):
        return None
    return code.upper()


def _offer_availability(offer: dict) -> str:
    """"available", "unavailable", or "ambiguous".

    JSON-LD encodes availability as a string IRI, a node reference
    ({"@id": "https://schema.org/OutOfStock"}), or an array of either; every
    form must be read, or an out-of-stock offer with a price passes as
    purchasable. Only a *consistent* signal counts: a contradictory array
    ([InStock, OutOfStock]) must not become a confirmed miss that clears a
    stored price, and a malformed member (an object without @id, a blank)
    must not pass as available -- both are ambiguous, which the caller
    routes to the unparsed/loud path. An absent field stays available.

    An AggregateOffer's offerCount is availability evidence too: zero means
    nothing is for sale, and a malformed count is ambiguous -- but a zero
    count next to an explicit *available* state is a contradiction, not a
    confirmed miss, so it goes ambiguous rather than clearing a price.
    """
    zero_count = False
    if _is_aggregate_offer(offer):
        count = offer.get("offerCount")
        if count is not None:
            if isinstance(count, bool):
                return "ambiguous"
            # schema.org commonly serializes counts as digit strings ("3"),
            # the same way prices arrive as strings; junking those would
            # make every such AggregateOffer raise instead of listing.
            if isinstance(count, str) and count.strip().isdigit():
                count = int(count.strip())
            if not isinstance(count, int) or count < 0:
                return "ambiguous"
            zero_count = count == 0
    value = offer.get("availability")
    if value is None:
        return "unavailable" if zero_count else "available"
    values = value if isinstance(value, list) else [value]
    flags = set()
    for item in values:
        if isinstance(item, dict):
            item = item.get("@id")
        if not isinstance(item, str) or not item.strip():
            flags.add("junk")
            continue
        # _type_tail also strips a compact prefix ("schema:InStock") --
        # rsplit on "/" alone leaves the colon in and junks a valid state.
        tail = re.sub(r"[^a-z]", "", _type_tail(item).lower())
        if _UNAVAILABLE_RE.search(item):
            flags.add("unavailable")
        elif tail in _AVAILABLE_TAILS:
            flags.add("available")
        else:
            # A present but unrecognised state ("unknown", "InStoreOnly")
            # must not pass as purchasable -- only whitelisted states do.
            flags.add("junk")
    if flags == {"unavailable"}:
        return "unavailable"
    if flags == {"available"}:
        return "ambiguous" if zero_count else "available"
    return "ambiguous"


def _offer_listing(offer: dict, url: str) -> Optional[dict]:
    if _offer_availability(offer) != "available":
        return None
    # lowPrice is only meaningful on a confirmed AggregateOffer -- a stale
    # lowPrice on a plain Offer must not stand in for its missing price.
    # On an aggregate the preference reverses: lowPrice *is* the cheapest
    # constituent, and a price beside it has no defined "cheapest" meaning,
    # so reading price first would report 39.99 over a 24.50 lowPrice
    # against the cheapest-price contract. A present but unreadable
    # lowPrice means the cheapest is unknown -- unparsed, never the
    # possibly-higher price instead.
    if _is_aggregate_offer(offer):
        if offer.get("lowPrice") is not None:
            price = _finite_price(offer.get("lowPrice"))
        else:
            price = _finite_price(offer.get("price"))
    else:
        price = _finite_price(offer.get("price"))
    if price is None:
        return None
    # Defaulting is only for an *absent* currency; a present value that
    # does not clean up to a three-letter code is schema drift, and this
    # crawler accepts non-USD offers, so silently stamping USD could
    # persist the right amount in the wrong currency. Unparseable instead
    # -- the loud path.
    raw_currency = offer.get("priceCurrency")
    currency = "USD" if raw_currency is None else _clean_currency(raw_currency)
    if currency is None:
        return None
    return {
        "url": url,
        "price": price,
        "shipping": None,
        "currency": currency,
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
    never qualifies. Branding is deliberately *not* required: the confirmed
    title shapes include unbranded ones ("Ramones - Greatest Hits -
    (Vinyl LP)"), and demanding "Rough Trade" would misread such a
    wrong-product landing as breakage. Anything else -- a maintenance
    page, a consent wall, an unrecognised layout -- is unclassifiable, and
    the caller raises instead of recording a miss that would clear a
    stored price.
    """
    lower = page_title.lower()
    if "not found" in lower or "404" in lower:
        return True
    # The format marker must sit after the first " - ", where a product
    # title's name/format segment lives -- scanning the whole title would
    # let a site page whose *leading* segment happens to carry a marker
    # word ("News on Vinyl - Rough Trade") classify as a confirmed
    # different-product miss and clear a stored price.
    _, sep, rest = page_title.partition(" - ")
    return bool(sep) and bool(_PRODUCT_TITLE_SHAPE_RE.search(rest))


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
            # clean_search_text() is for the artist only: its trailing-"(2)"
            # strip is the Discogs *artist* disambiguator convention, and
            # applying it to a title legitimately named "Album (2)" would
            # probe the sibling "/album" instead of "/album-2".
            artist_slug = _slugify(clean_search_text(a))
            title_slug = _slugify(t)
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
                       release_format: str = "",
                       allow_truncation: bool = True) -> bool:
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
        # The product name may itself contain the delimiter ("Sample Album -
        # Deluxe"), so every progressive join of the primary chunk's " - "
        # segments is a candidate core -- cutting at the first delimiter
        # unconditionally would classify such a release's own page as a miss
        # and clear its stored price. Once a candidate matches, the format
        # signals are read from *outside* that core -- a known cross-format
        # landing (a vinyl release's slug resolving to the CD product, or
        # vice versa) is a different product whatever the name says, and its
        # price must not be persisted for this release.
        segments = rest.split(" | ")[0].split(" - ")
        for end in range(1, len(segments) + 1):
            candidate_raw = " - ".join(segments[:end])
            candidate = _FORMAT_MARKER_RE.sub("", candidate_raw)
            if _title_core_matches(title_words, _norm_words(candidate),
                                   allow_truncation):
                return not _format_conflicts(
                    _page_format_after_core(candidate_raw, rest), release_format
                )
        return False

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
        # For identity only the Discogs "(2)" disambiguator is stripped from
        # the artist -- clean_search_text() would also drop '&', collapsing
        # "Sam & Dave" into the same identity as a different artist named
        # "Sam Dave" on the very slug the two collide on. The raw title, for
        # the same reason as _candidate_urls: stripping a trailing "(2)"
        # would let a sibling page pass identity.
        artist = _ARTIST_DISAMBIGUATOR_RE.sub("", release.get("artist") or "").strip()
        title = (release.get("title") or "").strip()
        candidates = self._candidate_urls(release)
        if not candidates:
            return []
        log.info("[Rough Trade] probing %d candidate URL(s) for: %s - %s",
                 len(candidates), artist, title)

        try:
            for url in candidates:
                response = await page.goto(url, wait_until="domcontentloaded")

                status = response.status if response else None
                if status == 429:
                    # Read before challenge or title handling, because both
                    # of those paths retry: the challenge check raises
                    # BotDetectedError (fresh-context retry within seconds)
                    # and a matching title would parse the page as success.
                    # A rate-limited site gets neither -- this repo's 429
                    # policy is to never retry while a site is rate-limiting
                    # (shopify_catalog.iter_products records why), so a
                    # plain failure the breaker counts, whatever the body
                    # settled into.
                    raise RuntimeError(
                        f"HTTP 429 (rate limited) on {url} -- not retried"
                    )

                await sleep(random.uniform(1, 2))

                page_title = await self._settled_title(page)
                if any(c in page_title.lower() for c in _CHALLENGE_TITLES):
                    raise BotDetectedError(f"challenge did not clear on {url}")

                if status == 404:
                    log.debug("[Rough Trade] 404 for %s", url)
                    continue

                # The slug guess can redirect to the canonical, suffixed
                # product URL ("/sample-album" -> "/sample-album-155"); node
                # scoping and the persisted listing must use where the page
                # actually landed, or the real product's url/@id classifies
                # as "other" and the valid page raises.
                landed_url = getattr(page, "url", "") or url

                # An absent format passes through as unknown -- forcing a
                # default would turn a formatless release's landing on its
                # own (say) CD page into a price-clearing miss. The mid-word
                # truncation relaxation is allowed only when the landed slug
                # spells out the full release title: a redirect anywhere
                # else could be a sibling product whose full title *is* the
                # cut prefix, which must read as a mismatch, not a cut.
                if not self._title_matches(
                    page_title, artist, title, release.get("format") or "",
                    allow_truncation=_landed_slug_is_full_title(landed_url, title),
                ):
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
                # A matching product title trumps only the one error status a
                # cleared challenge is known to leave behind: the real page
                # reloads while goto's response object still holds the
                # interstitial's 403. Any other error status under a matching
                # title is a combination this crawler has no account of, and
                # parsing it would trust a body served with a server error.
                if status is not None and status >= 400 and status != 403:
                    raise BotDetectedError(f"HTTP {status} on {url}")

                listings = await self._read_listings_when_ready(
                    page, landed_url, artist, title
                )
                if listings is None:
                    raise RuntimeError(
                        f"no complete, attributable machine-readable price "
                        f"signal on {url} (page title {page_title!r}) -- "
                        f"Product JSON-LD / price metas absent, malformed, "
                        f"half-parsed, mixed-currency, or unattributable; the "
                        f"price signals this crawler depends on have drifted "
                        f"-- re-check {__name__} against the live page"
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

    async def _read_listings_when_ready(self, page, url: str, artist: str,
                                        title: str) -> Optional[list]:
        # A first read finding no signal is retried briefly before being
        # declared drift: a cleared challenge's replacement document can
        # carry its <title> before the head's JSON-LD/metas have loaded
        # (discogs_marketplace._read_when_ready's rationale). None is the
        # only retried outcome -- [] and listings are real answers.
        deadline = time.monotonic() + _SIGNALS_TIMEOUT_MS / 1000
        while True:
            listings = await self._read_listings(page, url, artist, title)
            if listings is not None or time.monotonic() >= deadline:
                return listings
            await page.wait_for_timeout(500)

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
        # Built from the parsed path: a canonical redirect can append a query
        # string, which _node_scope() strips from node identifiers -- left in
        # product_path it would classify the valid canonical node as "other".
        landed_path = urlparse(url).path.rstrip("/")
        product_path = "/" + "/".join(landed_path.split("/")[-3:]).lstrip("/")
        listings = []
        tallies = {"unavailable": 0, "unparsed_available": 0, "ambiguous": 0}

        def read_node(node):
            offers, dropped = _iter_offers(node.get("offers"))
            tallies["unparsed_available"] += dropped
            for offer in offers:
                availability = _offer_availability(offer)
                if availability == "unavailable":
                    tallies["unavailable"] += 1
                    continue
                if availability == "ambiguous":
                    # Fatal, not merely unparsed: the OG metas carry a price
                    # but no availability, so they cannot rescue an offer
                    # whose purchasability is itself in question.
                    tallies["ambiguous"] += 1
                    continue
                listing = _offer_listing(offer, url)
                if listing:
                    listings.append(listing)
                else:
                    tallies["unparsed_available"] += 1

        anonymous_nodes = []
        accepted_any = False
        all_nodes, malformed_scripts = _product_nodes(signals.get("ldjson") or [])
        tallies["unparsed_available"] += malformed_scripts
        for node in all_nodes:
            scope = _node_scope(node, product_path)
            if scope == "other":
                continue
            if scope == "ambiguous":
                # A present but unreadable url/@id must not fall through to
                # name scoping -- unattributable, so it poisons the read.
                tallies["ambiguous"] += 1
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
        if tallies["ambiguous"]:
            return None
        unavailable = tallies["unavailable"]
        unparsed_available = tallies["unparsed_available"]

        # The en-us storefront prices in one currency; offers in mixed
        # currencies would let the numerically smallest amount masquerade as
        # cheapest with no exchange-rate comparison. Poisoned like an
        # unparsable offer rather than sorted.
        if len({listing["currency"] for listing in listings}) > 1:
            unparsed_available += len(listings)
            listings = []

        meta = signals.get("meta") or {}
        # The availability metas are read up front: they gate the OG
        # fallback below, and they are contrary evidence the confirmed-miss
        # branch has to see -- an explicitly available meta beside
        # all-unavailable JSON-LD makes the page self-contradictory.
        states = set()
        for key in ("product:availability", "og:availability"):
            value = meta.get(key)
            if value is None:
                continue
            token = re.sub(r"[^a-z]", "", value.lower()) if isinstance(value, str) else ""
            if token in ("instock", "available", "preorder", "presale", "backorder"):
                states.add("available")
            elif token in ("oos", "outofstock", "soldout", "discontinued", "unavailable"):
                states.add("unavailable")
            else:
                states.add("ambiguous")

        # An unparseable available offer poisons the whole JSON-LD read, not
        # just the empty case: returning the offers that *did* parse would
        # report their cheapest as the store's price while the unparsed
        # variant could undercut it. Half-parsed means the OG metas rescue
        # the page or the caller raises -- never a partial answer.
        if not unparsed_available:
            if listings:
                if "unavailable" in states:
                    # The mirror of the miss-side contradiction: a meta
                    # explicitly saying unavailable beside complete,
                    # purchasable JSON-LD makes the page self-contradictory
                    # -- persisting the price could sell a sold-out page,
                    # so it stays loud rather than listed.
                    return None
                listings.sort(key=lambda x: x["price"])
                return listings
            # [] is only a confirmed miss when every observed offer was
            # deliberately unpurchasable -- and no other machine signal
            # contradicts it. *Any* meta explicitly saying available
            # beside all-unavailable offers must stay loud, not clear a
            # stored price -- including one namespace saying available
            # while the other agrees with the offers; absent, unavailable,
            # or unrecognised metas leave the complete JSON-LD answer
            # standing.
            if unavailable:
                return None if "available" in states else []

        # An OG amount alone does not establish purchasability -- sold-out
        # pages commonly retain their price metadata -- so the fallback
        # requires a machine-readable availability meta too: unavailable is a
        # confirmed miss, available unlocks the price pairs, and absent or
        # unrecognised means the metas cannot answer (the caller raises).
        # Both namespaces are read: a conflicting or unrecognised pair must
        # neither clear a price nor persist one -- it stays loud.
        if states == {"unavailable"}:
            # A confirmed miss only when the JSON-LD read was complete: a
            # half-parsed *available* offer is contrary evidence, and a
            # stale unavailable meta must not clear a price the JSON-LD
            # says the page still sells for.
            return None if unparsed_available else []
        if states != {"available"}:
            return None
        for amount_key, currency_key in _META_PAIRS:
            price = _finite_price(meta.get(amount_key))
            if price is None:
                continue
            raw_currency = meta.get(currency_key)
            if raw_currency is None:
                currency = "USD"
            else:
                currency = _clean_currency(raw_currency)
                if currency is None:
                    # Same rule as the JSON-LD path: default only for an
                    # absent currency, and skip a malformed pair rather than
                    # persisting drifted data or stamping USD over it.
                    continue
            return [{
                "url": url,
                "price": price,
                "shipping": None,
                "currency": currency,
                "condition": None,
            }]
        return None
