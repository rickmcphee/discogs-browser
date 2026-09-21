import httpx
import pytest
import respx

from crawlers.lenoise import Crawler

_PRODUCTS_URL = "https://lenoise.ca/collections/vinyl/products.json"


# Every fixture below is marked `captured` (a real product, taken from the
# 2026-09-21 walk of the live shelf) or `invented` (a shape the live shelf does
# not publish, written to pin a rule that would otherwise go untested).

# captured. The plain shape: one in-stock "Default Title" variant, a pressing
# bracket, and an artist the store writes out in full while `vendor` does not.
_STEF_CHURA = {
    "title": "Stef Chura - Dancing Alone On The Concrete (Blue)",
    "handle": "stef-chura-dancing-alone-on-the-concrete-blue",
    # Surname-first, and the reason `vendor` is never read: the same field is a
    # bare catalogue number on the next product down.
    "vendor": "Chura Stef",
    "product_type": "Vinyl",
    "tags": ["021026", "Preorder", "RedEye", "Rock & Pop"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0250/3344/1364/files/a1674265645_10.jpg?v=1789936803"}],
    "variants": [{"title": "Default Title", "price": "34.99", "available": True, "featured_image": None}],
}

# captured. `vendor` is a bare catalogue number here.
_TAME_IMPALA = {
    "title": "Tame Impala - Innerspeaker (2LP)",
    "handle": "tame-impala-innerspeaker-2lp",
    "vendor": "MODVL128",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop", "Universal"],
    "images": [{"src": "https://cdn.shopify.com/innerspeaker.jpg"}],
    "variants": [{"title": "Default Title", "price": "42.99", "available": True, "featured_image": None}],
}

# captured. The album half carries its own " - " run, so a greedy or last-dash
# split would report the artist as "John Carpenter - Cathedral".
_JOHN_CARPENTER = {
    "title": "John Carpenter - Cathedral - Deluxe Edition (Coloured)",
    "handle": "john-carpenter-cathedral",
    # Genre-first, and a third shape of the same field.
    "vendor": "Soundtrack - John Carpenter",
    "product_type": "Vinyl",
    "tags": ["Electronica", "FAB"],
    "images": [{"src": "https://cdn.shopify.com/cathedral.jpg"}],
    "variants": [{"title": "Default Title", "price": "26.99", "available": True, "featured_image": None}],
}

# captured. U+2010 HYPHEN, not the ASCII hyphen-minus. An ASCII-only separator
# class files this record under no artist at all.
_POLICE = {
    "title": "Police ‐ Greatest Hits (2LP)",
    "handle": "police-greatest-hits-2lp",
    "vendor": "Police",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop", "Universal"],
    "images": [{"src": "https://cdn.shopify.com/greatest-hits.jpg"}],
    "variants": [{"title": "Default Title", "price": "39.99", "available": True, "featured_image": None}],
}

# captured. U+2013 EN DASH.
_MOLECULE = {
    "title": "Molecule – Nazare",
    "handle": "molecule-nazare",
    "vendor": "Molecule",
    "product_type": "Vinyl",
    "tags": ["Electronica"],
    "images": [{"src": "https://cdn.shopify.com/nazare.jpg"}],
    "variants": [{"title": "Default Title", "price": "32.99", "available": True, "featured_image": None}],
}

# captured. Separator closed up on the RIGHT, and an album half packed with
# hyphens — the case that proves the split is found by laziness rather than by
# preferring one alternative over the other.
_IRON_BUTTERFLY = {
    "title": "Iron Butterfly -In-A-Gadda-Da-Vida (Clear)",
    "handle": "iron-butterfly-in-a-gadda-da-vida-clear",
    "vendor": "Iron Butterfly",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop"],
    "images": [{"src": "https://cdn.shopify.com/gadda.jpg"}],
    "variants": [{"title": "Default Title", "price": "29.99", "available": True, "featured_image": None}],
}

# captured. Separator closed up on the LEFT.
_LITTLE_BIG_TOWN = {
    "title": "Little Big Town- Mr. Sun (2LP)(Blue)",
    "handle": "little-big-town-mr-sun-2lp-blue",
    "vendor": "Little Big Town",
    "product_type": "Vinyl",
    "tags": ["Country", "Universal"],
    "images": [{"src": "https://cdn.shopify.com/mr-sun.jpg"}],
    "variants": [{"title": "Default Title", "price": "44.99", "available": True, "featured_image": None}],
}

# captured. The store's own casing slip on a real record. An exact-match
# product_type gate drops it.
_ERIK_SATIE = {
    "title": "Erik Satie - Best Of",
    "handle": "erik-satie-best-of",
    "vendor": "Erik Satie",
    "product_type": "VInyl",
    "tags": ["World & Classical"],
    "images": [{"src": "https://cdn.shopify.com/satie.jpg"}],
    "variants": [{"title": "Default Title", "price": "27.99", "available": True, "featured_image": None}],
}

# captured. A CD the store filed on the vinyl shelf, under the CD product_type.
_YOKO_ONO_CD = {
    "title": "Yoko Ono - It's Alright (CD)",
    "handle": "yoko-ono-its-alright-cd",
    "vendor": "Yoko Ono",
    "product_type": "CD",
    "tags": ["180926", "FAB", "Rock & Pop"],
    "images": [{"src": "https://cdn.shopify.com/its-alright.jpg"}],
    "variants": [{"title": "Default Title", "price": "23.99", "available": True, "featured_image": None}],
}

# captured, and the reason the product_type gate is not enough on its own: a CD
# carrying the *Vinyl* product_type. Only the title's bracket says otherwise.
_JOE_JACKSON_CD = {
    "title": "Joe Jackson - Hope And Fury (CD)",
    "handle": "joe-jackson-hope-and-fury-cd",
    "vendor": "Joe Jackson",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop", "Sony"],
    "images": [{"src": "https://cdn.shopify.com/hope-and-fury.jpg"}],
    "variants": [{"title": "Default Title", "price": "20.99", "available": True, "featured_image": None}],
}

# captured. Same shape with a count glued to the medium, which has no word
# boundary before the letters.
_NIRVANA_5CD = {
    "title": "Nirvana - Nevermind 30th Anniversary (5CD)",
    "handle": "nirvana-nevermind-30th-anniversary-5cd",
    "vendor": "Nirvana",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop", "Universal"],
    "images": [{"src": "https://cdn.shopify.com/nevermind.jpg"}],
    "variants": [{"title": "Default Title", "price": "89.99", "available": True, "featured_image": None}],
}

# captured. A cassette on the vinyl shelf, again under the Vinyl product_type.
_MAYA_HAWKE_CASSETTE = {
    "title": "Maya Hawke - Chaos Angel (Cassette)",
    "handle": "maya-hawke-chaos-angel-cassette",
    "vendor": "Maya Hawke",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop"],
    "images": [{"src": "https://cdn.shopify.com/chaos-angel.jpg"}],
    "variants": [{"title": "Default Title", "price": "16.99", "available": True, "featured_image": None}],
}

# captured. Two media in one bracket, neither of them vinyl.
_MANIC_STREET_PREACHERS = {
    "title": "Manic Street Preachers - The Holy Bible Live (CD/BRD)",
    "handle": "manic-street-preachers-the-holy-bible-live-cd-brd",
    "vendor": "Manic Street Preachers",
    "product_type": "Vinyl",
    "tags": ["231026", "Preorder", "Rock & Pop", "Sony"],
    "images": [{"src": "https://cdn.shopify.com/holy-bible.jpg"}],
    "variants": [{"title": "Default Title", "price": "34.99", "available": True, "featured_image": None}],
}

# captured. A vinyl box with bonus discs — the case a bare "names a CD" gate
# would throw away with the CDs.
_LEE_PERRY_BOX = {
    "title": "Lee Perry - King Scratch (4LP+4CD)",
    "handle": "lee-perry-king-scratch-4lp-4cd",
    "vendor": "Lee Perry",
    "product_type": "Vinyl",
    "tags": ["Reggae", "Universal"],
    "images": [{"src": "https://cdn.shopify.com/king-scratch.jpg"}],
    "variants": [{"title": "Default Title", "price": "129.99", "available": True, "featured_image": None}],
}

# captured. The same shape with a slash.
_GEORGE_MICHAEL_BOX = {
    "title": "George Michael - The Faith Tour (3LP/2CD)",
    "handle": "george-michael-the-faith-tour-3lp-2cd",
    "vendor": "George Michael",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop", "Sony"],
    "images": [{"src": "https://cdn.shopify.com/faith-tour.jpg"}],
    "variants": [{"title": "Default Title", "price": "99.99", "available": True, "featured_image": None}],
}

# captured. Sold out, and carrying a price — the availability gate has to run
# before anything reads that price.
_PEARL_JAM_SOLD_OUT = {
    "title": "Pearl Jam - Ten (2LP)",
    "handle": "pearl-jam-ten-2lp",
    "vendor": "Pearl Jam",
    "product_type": "Vinyl",
    "tags": ["Rock & Pop", "Sony"],
    "images": [{"src": "https://cdn.shopify.com/ten.jpg"}],
    "variants": [{"title": "Default Title", "price": "39.99", "available": False, "featured_image": None}],
}

# captured. No images at all, so the cover falls through to None.
_GRAVE_NO_IMAGE = {
    "title": "Grave - Necropsy (3LP)",
    "handle": "grave-necropsy",
    "vendor": "Grave",
    "product_type": "Vinyl",
    "tags": ["Metal"],
    "images": [],
    "variants": [{"title": "Default Title", "price": "54.99", "available": True, "featured_image": None}],
}

# captured. No dash anywhere, so there is no artist to report and no fallback
# to invent one from.
_NO_SEPARATOR = {
    "title": "Matt Jencik & Midwife (Clear)",
    "handle": "matt-jencik-midwife-clear",
    "vendor": "Matt Jencik",
    "product_type": "Vinyl",
    "tags": ["Electronica"],
    "images": [{"src": "https://cdn.shopify.com/jencik.jpg"}],
    "variants": [{"title": "Default Title", "price": "31.99", "available": True, "featured_image": None}],
}

# captured, and the reason the medium gate reads the bracket rather than the
# whole title: the album is *named* "Big Foot Cassette" and is pressed on
# yellow vinyl. Its bracket names a colour, not a medium, so a whole-title
# scan finds the cassette, finds no vinyl to override it, and throws a real
# in-stock record away.
_LIP_CREAM = {
    "title": "Lip Cream - Big Foot Cassette (Yellow)",
    "handle": "lip-cream-big-foot-cassette-yellow",
    "vendor": "Lip Cream",
    "product_type": "Vinyl",
    "tags": ["Punk"],
    "images": [{"src": "https://cdn.shopify.com/big-foot.jpg"}],
    "variants": [{"title": "Default Title", "price": "38.99", "available": True, "featured_image": None}],
}

# invented. The live shelf publishes exactly one variant per product, so only a
# made-up product can pin the one-row-per-product rule that item_key collisions
# depend on, and the cheapest-in-stock pick.
_INVENTED_TWO_IN_STOCK = {
    "title": "Invented Artist - Two In Stock (2LP)",
    "handle": "invented-artist-two-in-stock-2lp",
    "vendor": "Invented Artist",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/product.jpg"}],
    "variants": [
        {"title": "New", "price": "44.99", "available": True, "featured_image": None},
        {"title": "Used", "price": "22.99", "available": True, "featured_image": None},
    ],
}

# invented. A cheaper variant that is sold out, beside a dearer one that is not.
_INVENTED_CHEAPER_SOLD_OUT = {
    "title": "Invented Artist - Cheaper Sold Out (2LP)",
    "handle": "invented-artist-cheaper-sold-out-2lp",
    "vendor": "Invented Artist",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/product.jpg"}],
    "variants": [
        {"title": "New", "price": "44.99", "available": True, "featured_image": None},
        {"title": "Damaged", "price": "19.99", "available": False, "featured_image": None},
    ],
}

# invented. A malformed price must not drop in-stock vinyl.
_INVENTED_BAD_PRICE = {
    "title": "Invented Artist - Bad Price",
    "handle": "invented-artist-bad-price",
    "vendor": "Invented Artist",
    "product_type": "Vinyl",
    "tags": [],
    "images": [],
    "variants": [{"title": "Default Title", "price": None, "available": True, "featured_image": None}],
}

# invented. featured_image is set on one live product in the whole walk, and
# that one is out of stock, so only a made-up product can prove
# resolve_cover_image's variant-first preference is wired up.
_INVENTED_VARIANT_IMAGE = {
    "title": "Invented Artist - Variant Image (Red)",
    "handle": "invented-artist-variant-image-red",
    "vendor": "Invented Artist",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/product.jpg"}],
    "variants": [{
        "title": "Default Title",
        "price": "34.99",
        "available": True,
        "featured_image": {"src": "https://cdn.shopify.com/variant.jpg"},
    }],
}

# invented. `available` as the STRING "false", which is truthy in Python. A
# falsiness test would publish this sold-out record as in stock.
_INVENTED_STRING_AVAILABLE = {
    "title": "Invented Artist - String Available",
    "handle": "invented-artist-string-available",
    "vendor": "Invented Artist",
    "product_type": "Vinyl",
    "tags": [],
    "images": [],
    "variants": [{"title": "Default Title", "price": "29.99", "available": "false", "featured_image": None}],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_pages(*pages):
    """Mock one products.json page per argument, then the empty page that ends the walk."""
    for index, products in enumerate(pages, start=1):
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(index)}).mock(
            return_value=_page_response(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(len(pages) + 1)}).mock(
        return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


def test_site_metadata():
    assert Crawler.site_name == "Le Noise"
    assert Crawler.base_url == "https://lenoise.ca"
    assert Crawler.genre == "marketplace"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre_summary


@respx.mock
async def test_parses_artist_album_and_keeps_the_pressing_bracket(crawler):
    _mock_pages([_STEF_CHURA])
    assert [item async for item in crawler.crawl_catalog()] == [{
        "artist": "Stef Chura",
        # The bracket stays: it is the only thing separating two pressings of
        # one album, which are distinct products at distinct URLs.
        "title": "Dancing Alone On The Concrete (Blue)",
        "format": "Vinyl",
        "price": 34.99,
        "currency": "CAD",
        "url": "https://lenoise.ca/products/stef-chura-dancing-alone-on-the-concrete-blue",
        "cover_image_url": "https://cdn.shopify.com/s/files/1/0250/3344/1364/files/a1674265645_10.jpg?v=1789936803",
    }]


@respx.mock
async def test_splits_on_the_first_dash_not_the_last(crawler):
    _mock_pages([_JOHN_CARPENTER])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "John Carpenter"
    assert items[0]["title"] == "Cathedral - Deluxe Edition (Coloured)"


@respx.mock
async def test_reads_the_typographic_dashes_the_store_uses(crawler):
    _mock_pages([_POLICE, _MOLECULE])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Police", "Greatest Hits (2LP)"),
        ("Molecule", "Nazare"),
    ]


@respx.mock
async def test_reads_a_separator_closed_up_on_either_side(crawler):
    _mock_pages([_IRON_BUTTERFLY, _LITTLE_BIG_TOWN])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        # Not "Iron Butterfly -In", and not "Iron": every dash inside the album
        # is flanked by letters, so the lazy match finds the only one that
        # qualifies.
        ("Iron Butterfly", "In-A-Gadda-Da-Vida (Clear)"),
        ("Little Big Town", "Mr. Sun (2LP)(Blue)"),
    ]


def test_never_splits_a_hyphenated_name_mid_word():
    # A dash with a letter hard against it on both sides is inside a word, and
    # is the one shape neither alternative of the separator admits.
    assert Crawler._split_title("Anti-Flag - The Terror State") == (
        "Anti-Flag", "The Terror State")
    assert Crawler._split_title("Bryan Ferry - Bitter-Sweet (Red)") == (
        "Bryan Ferry", "Bitter-Sweet (Red)")
    assert Crawler._split_title("Bitter-Sweet") is None


@respx.mock
async def test_vendor_is_never_used_as_the_artist(crawler):
    # The field holds the act surname-first ("Chura Stef"), a bare catalogue
    # number ("MODVL128") and a genre-first label ("Soundtrack - John
    # Carpenter"), with nothing marking which.
    _mock_pages([_STEF_CHURA, _TAME_IMPALA, _JOHN_CARPENTER])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura", "Tame Impala", "John Carpenter"]


@respx.mock
async def test_admits_the_stores_product_type_casing_slip(crawler):
    _mock_pages([_ERIK_SATIE])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Erik Satie"]


@respx.mock
async def test_drops_a_cd_filed_under_the_cd_product_type(crawler):
    _mock_pages([_YOKO_ONO_CD, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura"]


@respx.mock
async def test_drops_a_cd_or_cassette_filed_under_the_vinyl_product_type(crawler):
    # The regression with teeth: product_type says `Vinyl` on every one of
    # these, so only the title's bracket keeps them off the shelf.
    _mock_pages([_JOE_JACKSON_CD, _NIRVANA_5CD, _MAYA_HAWKE_CASSETTE,
                 _MANIC_STREET_PREACHERS, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura"]


@respx.mock
async def test_keeps_a_vinyl_box_that_bundles_discs(crawler):
    # A bare "names a CD" gate throws these away with the CDs above.
    _mock_pages([_LEE_PERRY_BOX, _GEORGE_MICHAEL_BOX])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Lee Perry", "King Scratch (4LP+4CD)"),
        ("George Michael", "The Faith Tour (3LP/2CD)"),
    ]


@respx.mock
async def test_medium_gate_reads_the_bracket_not_the_album_name(crawler):
    # "Big Foot Cassette" is the album's own name, and its bracket names a
    # colour rather than a medium -- so nothing overrides a whole-title scan,
    # and a real in-stock record is the price of getting this wrong.
    _mock_pages([_LIP_CREAM])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Lip Cream", "Big Foot Cassette (Yellow)")]
    # "Live EP" is likewise the album's own name; the bracket is what names the
    # medium, in both directions.
    assert Crawler._is_other_medium("Led Zeppelin - Live EP (CD)") is True
    assert Crawler._is_other_medium("Led Zeppelin - Live EP (2LP)") is False


def test_a_counted_prefix_is_read_on_both_sides_of_the_vocabulary():
    # `\b\d*\s*` cannot cross an ASCII `x`, so "5xCD" was not read as a CD and
    # was published as a record — while "5×CD" *was* read, because the
    # multiplication sign is not a word character and `\b` found the boundary
    # the `x` hid. One shared counted prefix removes that asymmetry.
    for counted in ('A - B (5xCD)', 'A - B (5\u00d7CD)', 'A - B (5CD)'):
        assert Crawler._is_other_medium(counted) is True, counted
    # And the vinyl side needs the same prefix, or a vinyl box with bonus discs
    # loses its override and is dropped with the CDs it names.
    for mixed in ('A - B (3xLP/2xCD)', 'A - B (3LP/2CD)', 'A - B (4LP+4CD)'):
        assert Crawler._is_other_medium(mixed) is False, mixed
    for vinyl in ('A - B (2xLP)', 'A - B (2\u00d7LP)', 'A - B (2x12")'):
        assert Crawler._is_other_medium(vinyl) is False, vinyl
    # The boundary sits before the WHOLE prefix, so a match cannot restart
    # partway through a glued digit run, and the count is `\d+`, so a bare
    # letter cannot read as a multiplier. Both would otherwise vouch for a CD.
    for not_vinyl in ('A - B (Studio12LP CD)', 'A - B (XLP CD)'):
        assert Crawler._is_other_medium(not_vinyl) is True, not_vinyl


def test_a_glued_inch_marker_does_not_rescue_a_non_vinyl_bracket():
    # A quote glyph is already a non-word character, so the inch marker cannot
    # get a closing boundary from `\b` the way the word alternatives do.
    # Without one, "12\"" reads as a complete vinyl marker inside "12\"CD", the
    # bracket names vinyl *and* a CD, and the two-sided rule keeps the CD.
    for glued in ('A - B (12"CD)', 'A - B (7"Cassette)', 'A - B (12"2CD)'):
        assert Crawler._is_other_medium(glued) is True, glued
    # A genuine record bundled with a disc names a separator, so its marker
    # still reads and the two-sided rule still keeps it.
    for separated in ('A - B (12"/CD)', 'A - B (12" + CD)', 'A - B (12", CD)'):
        assert Crawler._is_other_medium(separated) is False, separated
    # And a bracket that names only a size is untouched.
    assert Crawler._is_other_medium('A - B (7" Box Set)') is False


@respx.mock
async def test_a_glued_inch_marker_product_is_dropped_end_to_end(crawler):
    _mock_pages([
        {**_JOE_JACKSON_CD, "title": 'Joe Jackson - Hope And Fury (12"CD)',
         "handle": "joe-jackson-hope-and-fury-12-cd"},
        _STEF_CHURA,
    ])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura"]


@respx.mock
async def test_sold_out_product_yields_nothing(crawler):
    _mock_pages([_PEARL_JAM_SOLD_OUT, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura"]


@respx.mock
async def test_a_string_available_flag_is_not_in_stock(crawler):
    _mock_pages([_INVENTED_STRING_AVAILABLE, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura"]


@respx.mock
async def test_out_of_stock_variant_never_sets_the_price(crawler):
    _mock_pages([_INVENTED_CHEAPER_SOLD_OUT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 44.99


@respx.mock
async def test_one_row_per_product_at_the_cheapest_in_stock_price(crawler):
    # Two in-stock variants share (artist, title, url), so two rows would
    # collide on item_key, which replace_stock_items INSERTs unguarded.
    _mock_pages([_INVENTED_TWO_IN_STOCK])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 22.99


@respx.mock
async def test_a_malformed_price_does_not_drop_in_stock_vinyl(crawler):
    _mock_pages([_INVENTED_BAD_PRICE, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["price"]) for i in items] == [
        ("Invented Artist", None), ("Stef Chura", 34.99)]


@respx.mock
async def test_prefers_the_variant_image_over_the_product_image(crawler):
    _mock_pages([_INVENTED_VARIANT_IMAGE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/variant.jpg"


@respx.mock
async def test_a_product_with_no_images_still_yields_a_row(crawler):
    _mock_pages([_GRAVE_NO_IMAGE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_a_title_with_no_separator_yields_nothing(crawler):
    _mock_pages([_NO_SEPARATOR, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura"]


@respx.mock
async def test_walks_every_page_until_the_collection_is_exhausted(crawler):
    _mock_pages([_STEF_CHURA], [_TAME_IMPALA], [_POLICE])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura", "Tame Impala", "Police"]


# --- drift guards -----------------------------------------------------------
#
# replace_stock_items() DELETEs the previous snapshot before inserting, and
# _sync_stock only skips that call when the crawl raised -- so each of these
# asserts that a payload this crawler can no longer read raises rather than
# quietly replacing the store with nothing.

@respx.mock
async def test_an_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="returned no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_collection_with_no_vinyl_product_type_raises(crawler):
    _mock_pages([_YOKO_ONO_CD])
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_every_product_reading_as_another_medium_raises(crawler):
    _mock_pages([_JOE_JACKSON_CD, _MAYA_HAWKE_CASSETTE])
    with pytest.raises(RuntimeError, match="medium-bracket drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_unreadable_variants_raise(crawler):
    _mock_pages([{**_STEF_CHURA, "variants": None}])
    with pytest.raises(RuntimeError, match="variant-identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_lost_handle_raises(crawler):
    _mock_pages([{**_STEF_CHURA, "handle": None}])
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_titles_that_stop_carrying_an_artist_raise(crawler):
    _mock_pages([_NO_SEPARATOR])
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_unreadable_availability_flag_raises(crawler):
    _mock_pages([{**_STEF_CHURA, "variants": [
        {"title": "Default Title", "price": "34.99", "available": "maybe", "featured_image": None},
    ]}])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_catalog_with_no_price_anywhere_raises(crawler):
    _mock_pages([_INVENTED_BAD_PRICE])
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_sold_out_record_never_vouches_for_an_unreadable_catalog(crawler):
    # The partial case the unreadable-stock tally exists for: a genuinely
    # sold-out record beside one whose flag cannot be read at all.
    _mock_pages([_PEARL_JAM_SOLD_OUT, {**_TAME_IMPALA, "variants": [
        {"title": "Default Title", "price": "42.99", "available": None, "featured_image": None},
    ]}])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_isolated_null_price_is_tolerated(crawler):
    # The price guard fires only when NO row carries a price.
    _mock_pages([_INVENTED_BAD_PRICE, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 34.99]


@respx.mock
async def test_an_isolated_unreadable_product_is_tolerated(crawler):
    # Each of the four "no rows AND" guards is gated on an empty outcome: one
    # broken product among real rows is an ordinary skipped row.
    _mock_pages([{**_STEF_CHURA, "handle": None}, _NO_SEPARATOR,
                 {**_TAME_IMPALA, "variants": None}, _POLICE])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Police"]


@respx.mock
async def test_a_dropped_variant_entry_is_not_silently_readable(crawler):
    # [{sold out}, None] keeps one readable variant, so the collection is
    # neither empty nor unreadable -- and before the drop was counted, this
    # yielded no row while incrementing nothing at all.
    _mock_pages([{**_STEF_CHURA, "variants": [
        {"title": "Default Title", "price": "34.99", "available": False, "featured_image": None},
        None,
    ]}])
    with pytest.raises(RuntimeError, match="variant-identity-source drift") as raised:
        [item async for item in crawler.crawl_catalog()]
    # The diagnostic has to describe what was actually counted. This product
    # has a perfectly readable variant beside the broken one, so "no readable
    # variants" would send a reader looking for an empty collection that was
    # never the cause. Found by Copilot in review on PR #394.
    assert "unreadable variant data" in str(raised.value)
    assert "no readable variants" not in str(raised.value)


@respx.mock
async def test_one_readable_variant_never_vouches_for_a_corrupt_sibling(crawler):
    # Readability is judged with all(), not any(): the sold-out variant reads
    # perfectly, and under any() it certified the "maybe" beside it.
    _mock_pages([{**_STEF_CHURA, "variants": [
        {"title": "Default Title", "price": "34.99", "available": False, "featured_image": None},
        {"title": "Default Title", "price": "34.99", "available": "maybe", "featured_image": None},
    ]}])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_isolated_corrupt_variant_is_tolerated(crawler):
    # Both guards above stay gated on an empty outcome.
    _mock_pages([{**_TAME_IMPALA, "variants": [
        {"title": "Default Title", "price": "42.99", "available": False, "featured_image": None},
        None,
    ]}, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Stef Chura"]


def test_a_boolean_price_does_not_price_a_record_at_one():
    # bool is an int subclass, so float(True) is 1.0.
    assert Crawler._price({"price": True}) is None
    assert Crawler._price({"price": False}) is None


def test_non_finite_and_non_positive_prices_are_not_prices():
    for raw in ("NaN", "Infinity", "-Infinity", float("nan"), float("inf"), "0.00", 0, "-5"):
        assert Crawler._price({"price": raw}) is None, raw
    assert Crawler._price({"price": "34.99"}) == 34.99


def test_an_oversized_json_integer_price_is_not_a_price():
    # An oversized JSON *integer* makes float() raise OverflowError, which is
    # not a ValueError -- so leaving it unhandled aborts the whole source over
    # one malformed price. An oversized *string* does not take that path: it
    # becomes inf, which the finiteness test rejects.
    for raw in (10 ** 400, -(10 ** 400)):
        assert Crawler._price({"price": raw}) is None, raw
    assert Crawler._price({"price": "1e400"}) is None


@respx.mock
async def test_an_oversized_price_does_not_abort_the_source(crawler):
    _mock_pages([{**_TAME_IMPALA, "variants": [
        {"title": "Default Title", "price": 10 ** 400, "available": True, "featured_image": None},
    ]}, _STEF_CHURA])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["price"]) for i in items] == [
        ("Tame Impala", None), ("Stef Chura", 34.99)]


@respx.mock
async def test_a_catalog_priced_entirely_in_nan_raises(crawler):
    # nan counts toward `priced` as readily as a real price would, so without
    # the finiteness check this satisfied price-source drift while publishing
    # a catalog of prices no reader can use.
    _mock_pages([{**_STEF_CHURA, "variants": [
        {"title": "Default Title", "price": "NaN", "available": True, "featured_image": None},
    ]}])
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_non_string_image_src_never_reaches_cover_image_url(crawler):
    # resolve_cover_image() returns whatever sits at `src`, so a retyped one
    # would hand an int to a Postgres TEXT column and kill the whole refresh
    # over display-only artwork.
    _mock_pages([
        {**_STEF_CHURA, "images": [{"src": 123}]},
        {**_TAME_IMPALA, "images": [{"src": ""}, {"src": "https://cdn.shopify.com/second.jpg"}]},
        {**_POLICE, "images": [{"src": "https://cdn.shopify.com/police.jpg"}], "variants": [
            {"title": "Default Title", "price": "39.99", "available": True,
             "featured_image": {"src": 456}},
        ]},
    ])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["cover_image_url"]) for i in items] == [
        ("Stef Chura", None),
        # The unusable first image is passed over rather than allowed to answer.
        ("Tame Impala", "https://cdn.shopify.com/second.jpg"),
        # A retyped variant image falls back to the product's own.
        ("Police", "https://cdn.shopify.com/police.jpg"),
    ]


# --- payload type safety ----------------------------------------------------

@respx.mock
async def test_a_retyped_field_skips_the_product_instead_of_aborting(crawler):
    # A raise here would abort the whole source over one product, freezing the
    # store at its previous snapshot for as long as that product is published.
    _mock_pages([
        {**_TAME_IMPALA, "title": 12345},
        {**_POLICE, "product_type": ["Vinyl"]},
        {**_JOHN_CARPENTER, "images": "not-a-list"},
        # Not merely retyped but non-iterable: `images or []` leaves this
        # intact and the comprehension over it raises, taking the whole source
        # down over display-only artwork.
        {**_IRON_BUTTERFLY, "images": 123},
        {**_MOLECULE, "variants": [{"title": "Default Title", "price": "32.99",
                                    "available": True, "featured_image": "not-a-dict"}]},
        _STEF_CHURA,
    ])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["cover_image_url"]) for i in items] == [
        ("John Carpenter", None),
        ("Iron Butterfly", None),
        ("Molecule", "https://cdn.shopify.com/nazare.jpg"),
        ("Stef Chura", "https://cdn.shopify.com/s/files/1/0250/3344/1364/files/a1674265645_10.jpg?v=1789936803"),
    ]


@respx.mock
async def test_a_payload_with_no_products_list_raises(crawler):
    # shopify_catalog treats only an empty LIST as exhaustion; a missing or
    # retyped field is drift, and stopping the walk on it would replace the
    # snapshot with a partial prefix.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(200, json={"products": None}))
    with pytest.raises(RuntimeError, match="payload drift"):
        [item async for item in crawler.crawl_catalog()]
