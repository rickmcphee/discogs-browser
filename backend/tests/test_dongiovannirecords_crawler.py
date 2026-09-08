import httpx
import respx
import pytest
from crawlers.dongiovannirecords import Crawler

_PRODUCTS_URL = "https://dongiovannirecords.com/collections/vinyl/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-07, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape -- `vendor` is the artist, the title
# is `Artist "Album" <format>`, and the sole variant names a colour.
_KISS_BIG_PRODUCT = {
    "title": 'Ailbhe Reddy "Kiss Big" 12"',
    "vendor": "Ailbhe Reddy",
    "handle": "ailbhe-reddy-kiss-big-12",
    "product_type": '12"',
    "tags": ["preorder"],
    "images": [{"src": "https://cdn.shopify.com/KissBig.png"}],
    "variants": [
        {"id": 47965814751475, "title": "Red", "price": "24.99",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/KissBig_Red.png"}},
    ],
}

# Captured: a plain in-stock record with no pre-order tag.
_FIRE_PRODUCT = {
    "title": 'Amy Klein "Fire" 12"',
    "vendor": "Amy Klein",
    "handle": "amy-klein-fire-12",
    "product_type": '12"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/Fire.jpg"}],
    "variants": [
        {"id": 40001, "title": "Black", "price": "22.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a three-colour product, two of them sold out, none of the
# variants carrying an image of its own.
_TEENAGE_HALLOWEEN_PRODUCT = {
    "title": 'Teenage Halloween "Teenage Halloween" 12"',
    "vendor": "Teenage Halloween",
    "handle": "teenage-halloween-teenage-halloween-12",
    "product_type": '12"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/TeenageHalloween.jpg"}],
    "variants": [
        {"id": 40010, "title": "Black", "price": "23.99", "available": True, "featured_image": None},
        {"id": 40011, "title": "Electric Smoke", "price": "23.99", "available": False, "featured_image": None},
        {"id": 40012, "title": "Light Blue (Transparent)", "price": "23.99", "available": False, "featured_image": None},
    ],
}

# Captured: a double LP. The `2x12"` descriptor is kept in the row title
# exactly as the store writes it.
_OPEN_THE_GATES_PRODUCT = {
    "title": 'Irreversible Entanglements "Open The Gates" 2x12"',
    "vendor": "Irreversible Entanglements",
    "handle": "irreversible-entanglements-open-the-gates-2x12",
    "product_type": '2x12"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/OpenTheGates.jpg"}],
    "variants": [
        {"id": 40020, "title": "Sands Of Time", "price": "29.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/Gates_Sands.jpg"}},
        {"id": 40021, "title": "Neptune Blue", "price": "29.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/Gates_Neptune.jpg"}},
    ],
}

# Captured: a 7".
_SISSYBEARS_PRODUCT = {
    "title": 'Alice Bag "Alice Bag & The Sissybears" 7"',
    "vendor": "Alice Bag",
    "handle": "alice-bag-alice-bag-the-sissybears-7",
    "product_type": '7"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/Sissybears.jpg"}],
    "variants": [
        {"id": 40030, "title": "Yellow", "price": "9.99", "available": False, "featured_image": None},
    ],
}

# Captured: the one product where `vendor` and the title's own artist prefix
# disagree. The store truncates the credit in the title at 100 characters,
# mid-word (`Marissa Nadler a`); `vendor` carries its own ellipsis.
_LONDON_ENSEMBLE_PRODUCT = {
    "title": ('The London Experimental Ensemble with Richard Thompson, Wesley Stace, '
              'Sivert Høyem, Marissa Nadler a "Child Ballads: The Final Six" 2x12"'),
    "vendor": ("The London Experimental Ensemble with Richard Thompson, Wesley Stace, "
               "Sivert Høyem, Marissa Nadler..."),
    "handle": "the-london-experimental-ensemble-child-ballads-the-final-six-2x12",
    "product_type": '2x12"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/ChildBallads.jpg"}],
    "variants": [
        {"id": 40040, "title": "Black", "price": "29.99", "available": True, "featured_image": None},
    ],
}

# Captured pair: one record the store splits across two products, one colour
# each, at consecutive handles. Same artist, same album, same format --
# only the handle and the colour tell them apart.
_DEAD_BEST_BLACK_PRODUCT = {
    "title": 'Dead Best "Dead Best" 12"',
    "vendor": "Dead Best",
    "handle": "dead-best-dead-best-12",
    "product_type": '12"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/DeadBest_Black.jpg"}],
    "variants": [
        {"id": 40050, "title": "Black", "price": "21.99", "available": True, "featured_image": None},
    ],
}
_DEAD_BEST_YELLOW_PRODUCT = {
    "title": 'Dead Best "Dead Best" 12"',
    "vendor": "Dead Best",
    "handle": "dead-best-dead-best-13",
    "product_type": '12"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/DeadBest_Yellow.jpg"}],
    "variants": [
        {"id": 40051, "title": "Yellow", "price": "20.99", "available": True, "featured_image": None},
    ],
}

# Captured: a pre-order whose only variant is sold out. The store renders it
# unavailable; the row is skipped like any other sold-out variant, and no
# marker is written for a row that never exists.
_SOLD_OUT_PREORDER_PRODUCT = {
    "title": 'Lee Bains "Free South 2025" 12"',
    "vendor": "Lee Bains + The Glory Fires",
    "handle": "lee-bains-free-south-2025-12",
    "product_type": '12"',
    "tags": ["preorder"],
    "images": [{"src": "https://cdn.shopify.com/FreeSouth.jpg"}],
    "variants": [
        {"id": 40060, "title": "Black", "price": "25.99", "available": False, "featured_image": None},
    ],
}

# Captured from the store's `all` collection, not from the vinyl shelf: a CD
# titled exactly like the records. Shelved here it would parse perfectly, and
# only the descriptor keeps it out.
_CD_PRODUCT = {
    "title": 'Ailbhe Reddy "Kiss Big" CD',
    "vendor": "Ailbhe Reddy",
    "handle": "ailbhe-reddy-kiss-big-cd",
    "product_type": "CD",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/KissBigCD.png"}],
    "variants": [
        {"id": 40070, "title": "CD", "price": "12.99", "available": True, "featured_image": None},
    ],
}

# Captured from the store's `all` collection: a T-shirt, likewise titled to
# the store's one convention.
_SHIRT_PRODUCT = {
    "title": 'Bad Moves "Logo" T-Shirt',
    "vendor": "Bad Moves",
    "handle": "bad-moves-logo-t-shirt",
    "product_type": "T-Shirt",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/BadMovesLogoTee.jpg"}],
    "variants": [
        {"id": 40080, "title": "Small", "price": "24.99", "available": True, "featured_image": None},
    ],
}

# Captured from the store's `all` collection: a book. It follows the quoted
# convention but names no format at all, which is what every live record does
# name -- so the parse, not the gate, is what keeps it out.
_BOOK_PRODUCT = {
    "title": 'Larry Livermore "Spy Rock Memories"',
    "vendor": "Larry Livermore",
    "handle": "larry-livermore-spy-rock-memories",
    "product_type": "Hardcover Book",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/SpyRock.jpg"}],
    "variants": [
        {"id": 40110, "title": "Hardcover", "price": "19.99", "available": True, "featured_image": None},
    ],
}

# Captured from the store's `all` collection: a pin, the same shape.
_PIN_PRODUCT = {
    "title": 'Teenage Halloween "Cloud"',
    "vendor": "Teenage Halloween",
    "handle": "teenage-halloween-cloud-pin",
    "product_type": "Pins",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/CloudPin.jpg"}],
    "variants": [
        {"id": 40120, "title": "Enamel", "price": "8.00", "available": True, "featured_image": None},
    ],
}

# Captured from the store's `all` collection: the one non-record in the store
# that does name a format, and names one no reject word covered until `Zine`
# was added.
_ZINE_PRODUCT = {
    "title": 'Liz Pelly "P.S. Eliot: 2007-2011" Zine',
    "vendor": "Liz Pelly",
    "handle": "liz-pelly-p-s-eliot-2007-2011-zine",
    "product_type": "Paperback Book",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/PSEliotZine.jpg"}],
    "variants": [
        {"id": 40130, "title": "Zine", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# Captured from the store's `all` collection: a bundle. It carries no quoted
# album, so the title parse excludes it even without the bundle rule.
_BUNDLE_PRODUCT = {
    "title": "Bad Moves Vinyl Bundle",
    "vendor": "Bad Moves",
    "handle": "bad-moves-vinyl-bundle",
    "product_type": "",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/BadMovesBundle.jpg"}],
    "variants": [
        {"id": 40090, "title": "Black", "price": "60.00", "available": True, "featured_image": None},
    ],
}

# Invented: the bundle the rule actually exists for -- shelved in `vinyl` and
# written to the store's usual convention, so the title parses and the
# descriptor's own "Vinyl" would admit it through the format gate.
_CONVENTIONAL_BUNDLE_PRODUCT = {
    "title": 'Bad Moves "Untenable" Vinyl Bundle',
    "vendor": "Bad Moves",
    "handle": "bad-moves-untenable-vinyl-bundle",
    "product_type": '12"',
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/UntenableBundle.jpg"}],
    "variants": [
        {"id": 40100, "title": "Black", "price": "55.00", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_pages(*products, empty_page=2):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(list(products)))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(empty_page)}).mock(
        return_value=_page_response([]))


def _one_pressing(product, index=0, **overrides):
    # A captured product reduced to one of its variants, so a test can alter
    # that variant without carrying the siblings along.
    return {**product, "variants": [{**product["variants"][index], **overrides}]}


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_FIRE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Amy Klein",
        "title": 'Fire 12" — Black',
        "format": "Vinyl",
        "price": 22.99,
        "currency": "USD",
        "url": "https://dongiovannirecords.com/products/amy-klein-fire-12",
        "cover_image_url": "https://cdn.shopify.com/Fire.jpg",
    }]


def test_plugin_identity():
    assert Crawler.site_name == "Don Giovanni Records"
    assert Crawler.base_url == "https://dongiovannirecords.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "punk"
    assert Crawler.genre_summary


# --- the title parse ---------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ('Ailbhe Reddy "Kiss Big" 12"', ("Kiss Big", '12"')),
    ('Irreversible Entanglements "Open The Gates" 2x12"', ("Open The Gates", '2x12"')),
    ('Alice Bag "Alice Bag & The Sissybears" 7"', ("Alice Bag & The Sissybears", '7"')),
    # Apostrophes live inside album names all over this catalog and never
    # stand in for the closing quote.
    ('Modern Hut "I Don\'t Want To Get Adjusted To This World" 12"',
     ("I Don't Want To Get Adjusted To This World", '12"')),
    ('Jeffrey Lewis "It\'s The Ones Who\'ve Cracked That The Light Shines Through" 12"',
     ("It's The Ones Who've Cracked That The Light Shines Through", '12"')),
    ('Jenny Mae "What\'s Wrong With Me?" 12"', ("What's Wrong With Me?", '12"')),
    # A parenthesised pressing the store put in the album name itself.
    ('Bad Moves "Untenable (Ice Blue)" 12"', ("Untenable (Ice Blue)", '12"')),
    ('Worriers "Imaginary Life (10th Anniversary Deluxe Edition)" 12"',
     ("Imaginary Life (10th Anniversary Deluxe Edition)", '12"')),
    # A colon inside the album.
    ('Dead Best "GOD: Out Of Order" 12"', ("GOD: Out Of Order", '12"')),
    # Whitespace is collapsed before the parse ever runs.
    ('  Amy   Klein   "Fire"   12"  ', ("Fire", '12"')),
    # Curly quotes are admitted though the store writes none.
    ('Amy Klein \u201cFire\u201d 12"', ("Fire", '12"')),
    # A descriptor glued straight onto the closing quote -- the `\d` arm of
    # the closing lookahead. This store does not write it; a sibling Shopify
    # store does.
    ('Amy Klein "Fire"12"', ("Fire", '12"')),
    # The trailing inch marker can never be read as the album's opening
    # quote, because the leading group excludes quotes outright.
    ('Bert Susanka "Well Qualified To Represent The L.B. Sea!" 2x12"',
     ("Well Qualified To Represent The L.B. Sea!", '2x12"')),
    # The rule must not reject a legitimate inch marker, in any spelling the
    # format gate accepts -- including the spaced form, which the digit
    # lookbehind wrongly refused. Found in review on PR #323.
    ('Amy Klein "Fire" 12 "', ("Fire", '12 "')),
    ('Amy Klein "Fire" 12 inch', ("Fire", "12 inch")),
    # ... and in any of the three quote spellings.
    ('Amy Klein "Fire" 12\u2033', ("Fire", "12\u2033")),
    ('Amy Klein "Fire" 12\u201d', ("Fire", "12\u201d")),
    # A title that omits the artist entirely still carries a readable album
    # and format. The leading group is allowed to be empty on purpose: the
    # credit comes from `vendor`, so this row is built correctly from a
    # field this title never touches, and rejecting it would lose it.
    ('"Album" 12"', ("Album", '12"')),
])
def test_title_parse(title, expected):
    assert Crawler._parse_title(title) == expected


@pytest.mark.parametrize("title", [
    None,
    "",
    "   ",
    # A quote anywhere ahead of the album is not an opening quote: the artist
    # group excludes quotes, so the album's opening quote is always the
    # title's first and a stray inch marker cannot start one.
    'Amy Klein 12" "Fire" LP',
    # An inch marker needs a right-hand boundary: glued to a following word,
    # `12"CD` is not one, and admitting it would publish a CD as vinyl.
    # Found in review on PR #323.
    'Amy Klein "Fire" 12"CD',
    'Amy Klein "Fire" 7"Cassette',
    'Amy Klein "Fire" 12"x',
    # A quote embedded in a word is not an inch marker, even though it does
    # follow a digit -- the digit lookbehind that stood in for the format
    # gate's own token accepted this. Found in review on PR #323.
    'Amy Klein "The " Studio54" LP',
    'Amy Klein "The " 12x" LP',
    # A descriptor carries at most one inch marker. Two quotes are the tail of
    # a nested quotation, and the digit exemption alone cannot see it when the
    # inner content ends in digits. Found in review on PR #323.
    'Amy Klein "The " 54" 12"',
    'Amy Klein "A " 7" 12"',
    # A double prime inside the album is drift too. The album group excludes
    # every character the crawler calls a quote, not just three of the four --
    # both classes are now built from one constant so they cannot disagree.
    # Found in review on PR #323.
    'Amy Klein "The \u2033 Big" 12"',
    'Amy Klein "\u2033" 12"',
    # A double prime is an inch marker after a digit and drift anywhere else,
    # exactly like the straight and right-curly forms.
    'Amy Klein "Fire" Deluxe\u2033',
    # A left curly quote is never an inch marker, so the digit exemption in
    # the stray-quote check must not cover it. Found in review on PR #323.
    'Amy Klein "Fire" 12\u201c',
    'Amy Klein "Fire" \u201c12',
    # A quote inside the album is not a closing quote either, for the mirror
    # reason -- so a nested quotation is skipped rather than parsed into a
    # severed album. The second spelling puts whitespace ahead of the inner
    # quote, which satisfies the closing lookahead: only the stray-quote
    # check on the descriptor rejects it. Found in review on PR #323.
    'Amy Klein "The "Big" One" 12"',
    'Amy Klein "The " Big" 12"',
    'Amy Klein "A " B " C" 12"',
    # A closing quote glued to a letter -- what the lookahead does reject.
    'Amy Klein "Fire"X 12"',
    'Amy Klein "" 12" Vinyl',
    # No quoted album at all -- the shape the store's bundles and its books
    # take.
    "Bad Moves LP + Shirt",
    "Larry Livermore How To Ruin A Record Label",
    # An opening quote with nothing after it.
    'Amy Klein "',
    # An empty album.
    'Amy Klein "" 12"',
    # A quoted album and nothing after it. The convention is both halves, and
    # the products that stop here are the store's books, pins and stickers.
    'Larry Livermore "Spy Rock Memories"',
    'Teenage Halloween "Cloud"',
    'Amy Klein "Fire"',
    'Amy Klein "Fire"   ',
])
def test_unparseable_titles_yield_nothing(title):
    assert Crawler._parse_title(title) == ("", "")


@respx.mock
async def test_a_product_whose_title_does_not_parse_is_skipped(crawler):
    _mock_pages(_FIRE_PRODUCT, {**_FIRE_PRODUCT, "title": "Amy Klein Fire LP",
                                "handle": "amy-klein-fire-lp"})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["url"] for i in items] == [
        "https://dongiovannirecords.com/products/amy-klein-fire-12"]


# --- the artist comes from vendor -------------------------------------

@respx.mock
async def test_the_artist_comes_from_vendor_not_the_title(crawler):
    _mock_pages({**_FIRE_PRODUCT, "vendor": "Amy Klein & Friends"})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein & Friends"]


@respx.mock
async def test_the_truncated_title_credit_never_reaches_the_row(crawler):
    _mock_pages(_LONDON_ENSEMBLE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    # The title severs the credit at "Marissa Nadler a"; vendor does not.
    assert items[0]["artist"] == _LONDON_ENSEMBLE_PRODUCT["vendor"]
    assert not items[0]["artist"].endswith("Nadler a")
    assert items[0]["title"] == 'Child Ballads: The Final Six 2x12" — Black'


@respx.mock
async def test_a_blank_vendor_does_not_fall_back_to_the_title(crawler):
    # The fallback is deliberately absent: it would keep the artist-source
    # guard from ever firing and would re-key every row whose credit the two
    # sources spell differently.
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "vendor": "   "})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_vendor_whitespace_is_collapsed(crawler):
    _mock_pages({**_FIRE_PRODUCT, "vendor": "  Amy   Klein  "})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_a_catalog_with_no_vendor_raises(crawler):
    _mock_pages({**_FIRE_PRODUCT, "vendor": ""})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_vendorless_product_among_real_rows_does_not_raise(crawler):
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "vendor": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_a_title_less_product_is_counted_toward_identity_drift(crawler):
    # _record() reads the title, so a title-less product can never reach the
    # identity check inside the format gate. Counted before it, or a partial
    # loss of `title` leaves an empty walk looking like a sold-out shelf and
    # replace_stock_items() deletes the snapshot. Found in review on PR #323.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, "title": "  "})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_title_less_product_among_real_rows_does_not_raise(crawler):
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "title": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_sources_satisfied_by_different_products_raises(crawler):
    # One product has a vendor and an unreadable title, the other a readable
    # title and no vendor: artist_ok and parsed_ok are both non-zero, yet no
    # product carries what a row needs. Without the combined guard this walk
    # completes empty and deletes the snapshot. Found in review on PR #323.
    _mock_pages({**_FIRE_PRODUCT, "title": "Amy Klein Fire LP"},
                {**_KISS_BIG_PRODUCT, "vendor": ""})
    with pytest.raises(RuntimeError, match="combined-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("broken", [
    {"vendor": ""},                       # source failure: no artist on this product
    {"title": "Other Album LP"},          # source failure: title does not parse
])
async def test_one_unreadable_product_beside_a_sold_out_one_raises(crawler, broken):
    # The catalog-wide tallies only notice a source vanishing from EVERY
    # product. One well-formed sold-out record keeps them all non-zero while
    # an unreadable product beside it goes uncounted, the walk completes
    # empty, and replace_stock_items() deletes the snapshot. Found in review
    # on PR #323.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, **broken})
    with pytest.raises(RuntimeError, match="classification drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_unreadable_product_among_real_rows_does_not_raise(crawler):
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "vendor": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@pytest.mark.parametrize("title,shaped", [
    ("Bad Moves Vinyl Bundle", True),
    ("Bad Moves LP + Shirt", True),
    ("Bad Moves Shirt + All Vinyl", True),
    ("Bad Moves Wearing Out The Refrain Shirt + CD", True),
    # Both halves are required, so a record title that lost its quotes but
    # kept a `+` in the artist credit is still drift, not a known shape.
    ('Lee Bains + The Glory Fires Youth Detention 12"', False),
    ("Amy Klein Fire LP", False),
    # The word must END the title. A real record that lost its album quotes
    # would otherwise be waved through as a known shape, and beside a sold-out
    # product the crawl would complete empty and delete the snapshot. The
    # gate's own rejection stays broad; only this exemption narrows.
    # Found in review on PR #323.
    ('Amy Klein Bundle of Joy 12"', False),
    ("Bundle of Joy", False),
    ("Bad Moves Vinyl Bundles", True),
    # The docstring's precondition is enforced, not assumed: these shapes are
    # about titles written WITHOUT a quoted album, so any quote disqualifies.
    # A record that lost only its format would otherwise satisfy the second
    # heuristic. Found in review on PR #323.
    ('Artist "Pins + Needles"', False),
    ('Artist "Album Bundle"', False),
    ('Artist "Untenable" LP + Shirt', False),
])
def test_the_unquoted_bundle_shapes_are_recognised(title, shaped):
    assert Crawler._bundle_shaped(title) is shaped


@respx.mock
async def test_an_unquoted_combo_bundle_is_a_classified_skip(crawler):
    # It carries no quoted album, so it never reaches the format gate's
    # `+`/merch rule -- and it has no literal "bundle" either. Counted as
    # unclassifiable it would raise on a shelf that merely sold out.
    # Found in review on PR #323.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, "title": "Bad Moves LP + Shirt"})
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_record_that_lost_its_quotes_and_says_bundle_is_still_drift(crawler):
    # `Amy Klein "Bundle of Joy" 12"` is a real record; stripped of its album
    # quotes it must not pass for the store's bundle shape.
    # Found in review on PR #323.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, "title": 'Amy Klein Bundle of Joy 12"'})
    with pytest.raises(RuntimeError, match="classification drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_album_containing_bundle_still_keeps_its_row_when_quoted(crawler):
    # The narrowed exemption must not disturb the gate's own broad rejection,
    # nor the album that prompted it.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "Bundle of Joy" 12"'})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Bundle of Joy 12" — Black']


@respx.mock
async def test_a_record_title_that_lost_its_quotes_is_still_drift(crawler):
    # The `+` in the artist credit must not buy an exemption on its own.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, "title": 'Lee Bains + The Glory Fires Youth Detention 12"'})
    with pytest.raises(RuntimeError, match="classification drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_sole_blank_titled_variant_that_is_sold_out_is_readable(crawler):
    # Readability is judged against the raw variant set, not the pressings
    # kept from it: this product keeps none, but its only variant is a
    # readable False, so the shelf is legitimately sold out and must not
    # raise. Found in review on PR #323.
    _mock_pages({**_FIRE_PRODUCT, "variants": [
        {"title": "  ", "price": "22.99", "available": False, "featured_image": None},
    ]})
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_product_with_no_variants_at_all_is_still_unreadable(crawler):
    _mock_pages({**_FIRE_PRODUCT, "variants": []})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("product", [_CD_PRODUCT, _SHIRT_PRODUCT])
async def test_a_blank_vendored_non_record_is_not_classification_drift(crawler, product):
    # Its descriptor already says it is not a record, so the missing vendor
    # is evidence of nothing -- counted, it would raise on a shelf that had
    # merely sold out and keep stale rows alive. Found in review on PR #323.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**product, "vendor": ""})
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_blank_vendored_record_is_still_classification_drift(crawler):
    # The exemption is for products the gate rejects, not for every missing
    # vendor: this one's descriptor reads as a record.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, "vendor": ""})
    with pytest.raises(RuntimeError, match="classification drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_quoted_title_that_lost_only_its_format_is_drift(crawler):
    # `Artist "Pins + Needles"` carries a `+` and a merch word, so it matched
    # the unquoted-bundle heuristic before the precondition was enforced.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, "title": 'Amy Klein "Pins + Needles"'})
    with pytest.raises(RuntimeError, match="classification drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_classified_skip_is_not_classification_drift(crawler):
    # A CD and a bundle were both read successfully and then deliberately
    # skipped, which is nothing like a product the crawler could not read.
    # Neither may turn a legitimately sold-out shelf into a raise.
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                _CD_PRODUCT, _SHIRT_PRODUCT, _BUNDLE_PRODUCT,
                _CONVENTIONAL_BUNDLE_PRODUCT)
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_one_product_carrying_both_sources_satisfies_the_combined_guard(crawler):
    # A readable record that is simply sold out: the combined guard is
    # satisfied and the empty result stands. (This case previously paired it
    # with a vendor-less product, which the classification guard added in
    # review now raises on -- correctly, since that is the hole it closes.)
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False))
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_shelf_of_non_records_that_ALSO_lost_every_vendor_raises(crawler):
    # Deliberate, and declined from review on PR #323. The documented
    # legitimate case is a shelf that filled up with CDs -- and the store's
    # real CDs and shirts carry vendors, so it does not raise (the two tests
    # above). Blanking the vendor on every product as well is not that case:
    # it is a store-wide loss of the only artist source, which is precisely
    # what this guard exists to catch.
    #
    # Exempting classified non-records from the source-health guards would
    # invert the asymmetry the whole guard set rests on -- it would DELETE the
    # snapshot on a shelf where not one product could be read for an artist.
    # A false raise costs an inert no-op; a false empty costs the catalog.
    _mock_pages({**_CD_PRODUCT, "vendor": ""}, {**_SHIRT_PRODUCT, "vendor": ""})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_all_cd_shelf_satisfies_the_combined_guard(crawler):
    # Taken before the format gate on purpose: a shelf that legitimately
    # filled up with CDs still has products carrying both sources, so it is
    # a legitimate empty result rather than drift.
    _mock_pages(_CD_PRODUCT, _SHIRT_PRODUCT)
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_catalog_with_no_parseable_title_raises(crawler):
    _mock_pages({**_FIRE_PRODUCT, "title": "Amy Klein Fire LP"})
    with pytest.raises(RuntimeError, match="album-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_catalog_that_dropped_its_artist_prefixes_still_yields(crawler):
    # The album-source guard tallies the album, which is what _record gates a
    # row on -- not the discarded prefix ahead of it. Tallying the prefix
    # would raise here, throwing away a catalog every row of which the
    # crawler reads correctly, because the credit comes from `vendor`.
    _mock_pages({**_FIRE_PRODUCT, "title": '"Fire" 12"'},
                {**_KISS_BIG_PRODUCT, "title": '"Kiss Big" 12"'})
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Amy Klein", 'Fire 12" — Black'),
        ("Ailbhe Reddy", 'Kiss Big 12" — Red'),
    ]


@respx.mock
async def test_the_two_source_guards_are_independent(crawler):
    # Every product has a vendor and none has a parseable title: the album
    # guard must still fire. Tallied together, the working source would hide
    # the dark one.
    _mock_pages({**_FIRE_PRODUCT, "title": "Amy Klein Fire LP"},
                {**_KISS_BIG_PRODUCT, "title": "Ailbhe Reddy Kiss Big LP"})
    with pytest.raises(RuntimeError, match="album-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_shelf_of_only_other_media_does_not_raise_source_drift(crawler):
    # Both sources are readable; the products are simply not records. That is
    # a legitimately empty result, not drift -- which is why neither tally is
    # nested inside the format gate.
    _mock_pages(_CD_PRODUCT, _SHIRT_PRODUCT)
    assert [item async for item in crawler.crawl_catalog()] == []


# --- the row's title ---------------------------------------------------

@respx.mock
async def test_the_row_title_leads_with_the_album_so_it_prefix_matches_a_library_title(crawler):
    _mock_pages(_FIRE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    # db._library_release_match_sql matches a catalog title that equals the
    # library title or begins with it followed by a space.
    assert items[0]["title"].startswith("Fire ")


@respx.mock
async def test_the_format_descriptor_is_kept_in_the_row_title(crawler):
    _mock_pages(_OPEN_THE_GATES_PRODUCT, _SISSYBEARS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        'Open The Gates 2x12" — Sands Of Time',
        'Open The Gates 2x12" — Neptune Blue',
    ]


@respx.mock
async def test_the_whole_title_is_collapsed_before_it_is_parsed(crawler):
    # The collapse happens once, on the raw title, so the album and the
    # descriptor are already clean by the time they are joined -- the row's
    # identity must never carry the store's stray double space.
    _mock_pages({**_FIRE_PRODUCT, "title": ' Amy  Klein "Fire  Escape"   12" '})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == 'Fire Escape 12" — Black'


# --- the format gate ---------------------------------------------------

@pytest.mark.parametrize("descriptor", [
    '12"', '2x12"', '7"', '10"', "LP", "2xLP", "Vinyl", "Picture Disc",
    "Test Pressing", '12" Vinyl',
    # A vinyl word beside another medium is still a record.
    "LP + Bonus CD",
    # Undeclared but present: the shelf has already said it is a record, so
    # an unrecognised descriptor is admitted. Silence is handled at the parse
    # instead, and never reaches here.
    "Box Set", "Deluxe Edition", "Gatefold", "Coloured",
])
def test_format_gate_admits_records_and_undeclared_descriptors(descriptor):
    assert Crawler._is_vinyl(descriptor) is True


@pytest.mark.parametrize("descriptor", [
    # A `+` joining a record to merch is a bundle, and the record word must
    # not admit it first. These are the store's own live combo products.
    "LP + Shirt", "Shirt + All Vinyl", 'Vinyl + T-Shirt', "12\" + Tote",
    # The store's own product_type vocabulary for everything that is not a
    # record.
    "CD", "2xCD", "Cassette", "DVD", "Blu-Ray",
    "T-Shirt", "T Shirt", "Tshirt", "Girls T-shirt", "Tank Top",
    "Longsleeve", "Crewneck Sweatshirt", "Hoodie",
    "Books", "Paperback Book", "Hardcover Book",
    "Pins", "Stickers & Decals", "Bag", "Tote", "Zine",
])
def test_format_gate_rejects_other_media_and_merch(descriptor):
    assert Crawler._is_vinyl(descriptor) is False


@respx.mock
@pytest.mark.parametrize("product", [_CD_PRODUCT, _SHIRT_PRODUCT])
async def test_non_vinyl_products_shelved_here_yield_nothing(crawler, product):
    _mock_pages(_FIRE_PRODUCT, product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_an_album_name_resembling_another_medium_does_not_decide_the_format(crawler):
    # The gate reads the descriptor only. `ABCD` contains "CD" and `Bag` is a
    # merch word, and neither may reject the record it names.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "ABCD" 12"'},
                {**_FIRE_PRODUCT, "title": 'Alice Bag "Bag" 12"',
                 "vendor": "Alice Bag", "handle": "alice-bag-bag-12"})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['ABCD 12" — Black', 'Bag 12" — Black']


@respx.mock
@pytest.mark.parametrize("product", [_BOOK_PRODUCT, _PIN_PRODUCT, _ZINE_PRODUCT])
async def test_the_stores_non_records_stay_out_even_when_mis_shelved(crawler, product):
    # None of these is in the vinyl collection today, and they are excluded by
    # two different rules: the book and the pin name no format at all, so the
    # parse rejects them and they never reach the gate; only the zine carries
    # a descriptor, and it is the gate's reject vocabulary that keeps it out.
    # Both paths matter, because the store titles all three exactly like its
    # records.
    _mock_pages(_FIRE_PRODUCT, product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_a_record_naming_an_unrecognised_format_is_still_admitted(crawler):
    # Rejecting silence must not turn into rejecting novelty: a format the
    # store adds later stays in on the shelf's own claim.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "Fire" Deluxe Box Set'})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Fire Deluxe Box Set — Black"]


@respx.mock
async def test_a_catalog_that_stopped_naming_formats_raises(crawler):
    # The fail-safe for the rule above: a store-wide loss of the trailing
    # format empties the album tally rather than silently emptying the walk
    # and letting replace_stock_items() delete the previous snapshot.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "Fire"'},
                {**_KISS_BIG_PRODUCT, "title": 'Ailbhe Reddy "Kiss Big"'})
    with pytest.raises(RuntimeError, match="album-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_record_plus_a_bonus_disc_is_still_a_record(crawler):
    # The combo rule must not swallow a record that ships with an extra
    # disc: one item, one price, and no merch word in it.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "Fire" LP + Bonus CD'})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Fire LP + Bonus CD — Black"]


@respx.mock
async def test_a_quoted_combo_bundle_yields_nothing(crawler):
    # `_BUNDLE_RE` only catches the literal word, so this is the shape that
    # would otherwise reach `_is_vinyl` and be admitted on its own `LP`.
    _mock_pages(_FIRE_PRODUCT,
                {**_KISS_BIG_PRODUCT, "title": 'Bad Moves "Untenable" LP + Shirt',
                 "vendor": "Bad Moves", "handle": "bad-moves-untenable-lp-shirt"})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_an_album_named_like_a_combo_does_not_trip_the_bundle_rule(crawler):
    # The combo rule reads the descriptor, so this album keeps its row.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "Pins + Needles" 12"'})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Pins + Needles 12" — Black']


@respx.mock
async def test_a_merch_word_in_the_album_does_not_reject_the_record(crawler):
    # The sharper version of the test above: with no record word in the
    # descriptor to admit it early, the gate's reject arm is what runs -- and
    # it must still read the descriptor alone, or this album's own "Bag"
    # would throw the record out.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Alice Bag "Bag" Box Set',
                 "vendor": "Alice Bag", "handle": "alice-bag-bag-box-set"})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Bag Box Set — Black"]


@pytest.mark.parametrize("descriptor,expected", [
    # An embedded `lp` must not admit. Paired with a rejecting medium and no
    # disc size, so a false match is observable: if `\blps?\b` matched inside
    # "Helps" the descriptor would be admitted instead of rejected.
    ("Helps CD", False),
    ("Scalpel Cassette", False),
    # An embedded `cd` must not reject. "Mcdonalds" is the contiguous case;
    # with nothing else to decide, a false match would flip this to False.
    ("Mcdonalds Box Set", True),
    ("Mcdonalds Gatefold", True),
    # A glued inch marker is not one, so the gate falls through to the media
    # word it was masking. Found in review on PR #323.
    ('12"CD', False),
    ('12"Cassette', False),
])
def test_a_medium_word_embedded_in_another_word_does_not_decide_the_format(descriptor, expected):
    assert Crawler._is_vinyl(descriptor) is expected


@respx.mock
async def test_a_descriptor_naming_no_medium_keeps_its_row(crawler):
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "Fire" Gatefold 12"'})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == 'Fire Gatefold 12" — Black'


# --- bundles -----------------------------------------------------------

@pytest.mark.parametrize("descriptor", ["Vinyl Bundle", "Bundles", "LP Bundle"])
def test_a_bundle_descriptor_is_not_a_record(descriptor):
    assert Crawler._is_vinyl(descriptor) is False


@respx.mock
async def test_an_album_containing_the_word_bundle_keeps_its_row(crawler):
    # The rule reads the descriptor, not the whole title: scanning the title
    # would discard this album and silently drop an existing stock row.
    # Found in review on PR #323.
    _mock_pages({**_FIRE_PRODUCT, "title": 'Amy Klein "Bundle of Joy" 12"'})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Bundle of Joy 12" — Black']


@respx.mock
@pytest.mark.parametrize("product", [_BUNDLE_PRODUCT, _CONVENTIONAL_BUNDLE_PRODUCT])
async def test_a_bundle_yields_nothing(crawler, product):
    _mock_pages(_FIRE_PRODUCT, product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


# --- variants ----------------------------------------------------------

@respx.mock
async def test_the_colour_is_appended_to_every_row(crawler):
    _mock_pages(_OPEN_THE_GATES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        'Open The Gates 2x12" — Sands Of Time',
        'Open The Gates 2x12" — Neptune Blue',
    ]


@respx.mock
async def test_a_product_reduced_to_one_colour_still_carries_it(crawler):
    # Keyed on the variant count instead, a sibling being delisted would
    # re-title the survivor and orphan everything keyed on its old identity.
    _mock_pages(_one_pressing(_OPEN_THE_GATES_PRODUCT))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Open The Gates 2x12" — Sands Of Time']


@respx.mock
async def test_placeholder_variant_carries_the_title_alone(crawler):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, title="Default Title"))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Fire 12"']


@respx.mock
async def test_placeholder_matching_is_case_insensitive(crawler):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, title="  default TITLE  "))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Fire 12"']


@respx.mock
async def test_placeholder_on_a_multi_variant_product_is_skipped(crawler):
    # A placeholder beside real colours is malformed data: admitted, its row
    # would share the title, the URL and so the item_key with nothing, but
    # would claim an identity the product's real pressings do not own.
    _mock_pages({**_OPEN_THE_GATES_PRODUCT, "variants": [
        {**_OPEN_THE_GATES_PRODUCT["variants"][0], "title": "Default Title"},
        _OPEN_THE_GATES_PRODUCT["variants"][1],
    ]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Open The Gates 2x12" — Neptune Blue']


@respx.mock
async def test_blank_variant_title_is_skipped(crawler):
    _mock_pages({**_OPEN_THE_GATES_PRODUCT, "variants": [
        {**_OPEN_THE_GATES_PRODUCT["variants"][0], "title": "   "},
        _OPEN_THE_GATES_PRODUCT["variants"][1],
    ]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Open The Gates 2x12" — Neptune Blue']


@respx.mock
async def test_variant_title_whitespace_is_collapsed(crawler):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, title="  Coke  Bottle   Clear "))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Fire 12" — Coke Bottle Clear']


@respx.mock
async def test_a_variant_title_containing_quotes_is_carried_verbatim(crawler):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, title='"Needle Drop" B/W splatter'))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Fire 12" — "Needle Drop" B/W splatter']


@respx.mock
async def test_the_same_record_split_across_two_products_keeps_two_identities(crawler):
    _mock_pages(_DEAD_BEST_BLACK_PRODUCT, _DEAD_BEST_YELLOW_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["title"], i["url"], i["price"]) for i in items] == [
        ('Dead Best 12" — Black',
         "https://dongiovannirecords.com/products/dead-best-dead-best-12", 21.99),
        ('Dead Best 12" — Yellow',
         "https://dongiovannirecords.com/products/dead-best-dead-best-13", 20.99),
    ]


@respx.mock
async def test_junk_variant_entries_are_ignored(crawler):
    _mock_pages({**_FIRE_PRODUCT, "variants": [
        "not a dict", None, 42, _FIRE_PRODUCT["variants"][0]]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Fire 12" — Black']


# --- availability and pre-orders ---------------------------------------

@respx.mock
async def test_sold_out_variant_is_skipped_beside_its_in_stock_siblings(crawler):
    _mock_pages(_TEENAGE_HALLOWEEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Teenage Halloween 12" — Black']


@respx.mock
@pytest.mark.parametrize("available", [False, "false", "true", 1, 0, None, "yes"])
async def test_only_the_literal_true_admits_a_variant(crawler, available):
    # Beside a well-formed product, so the walk is non-empty and the answer
    # is this variant being skipped rather than the stock-source guard
    # firing -- which is what test_a_catalog_with_no_readable_availability
    # covers instead.
    _mock_pages(_FIRE_PRODUCT, _one_pressing(_KISS_BIG_PRODUCT, available=available))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_a_missing_available_key_is_not_in_stock(crawler):
    variant = {k: v for k, v in _KISS_BIG_PRODUCT["variants"][0].items() if k != "available"}
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "variants": [variant]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_a_preorder_row_carries_no_marker(crawler):
    # compute_item_key hashes artist, title and URL, so a marker that
    # disappears when the record ships would re-key every pressing at exactly
    # the moment a waiting user cares most. Same churn the colour rule
    # refuses. Decided in review on PR #323.
    _mock_pages(_KISS_BIG_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Kiss Big 12" — Red']


@respx.mock
async def test_the_preorder_tag_never_reaches_the_row_identity(crawler):
    _mock_pages({**_OPEN_THE_GATES_PRODUCT, "tags": ["preorder"]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        'Open The Gates 2x12" — Sands Of Time',
        'Open The Gates 2x12" — Neptune Blue',
    ]


@respx.mock
async def test_a_products_tags_never_change_its_title(crawler):
    # The row a tagged product yields is byte-identical to the row it would
    # yield untagged, so shipping a pre-order cannot orphan anything.
    _mock_pages({**_FIRE_PRODUCT, "tags": ["preorder", "sync"]})
    tagged = [item async for item in crawler.crawl_catalog()]
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([{**_FIRE_PRODUCT, "tags": []}]))
    untagged = [item async for item in crawler.crawl_catalog()]
    assert tagged == untagged == [{
        "artist": "Amy Klein",
        "title": 'Fire 12" — Black',
        "format": "Vinyl",
        "price": 22.99,
        "currency": "USD",
        "url": "https://dongiovannirecords.com/products/amy-klein-fire-12",
        "cover_image_url": "https://cdn.shopify.com/Fire.jpg",
    }]


@respx.mock
async def test_an_untagged_product_yields_the_plain_title(crawler):
    _mock_pages({**_FIRE_PRODUCT, "tags": ["sync", "preorders"]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Fire 12" — Black']


@respx.mock
async def test_a_sold_out_preorder_is_skipped_with_no_bypass(crawler):
    _mock_pages(_FIRE_PRODUCT, _SOLD_OUT_PREORDER_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


# --- prices ------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    None, "", "   ", "free", "$21.99", True, False, "nan", "inf", "-inf",
    float("nan"), float("inf"), "0", 0, "-1", -1.0, [], {},
])
def test_unusable_price_yields_none(raw):
    assert Crawler._price({"price": raw}) is None


@pytest.mark.parametrize("raw,expected", [
    ("24.99", 24.99), ("21.99", 21.99), (29.99, 29.99), ("30", 30.0), (4.99, 4.99),
])
def test_usable_price_is_parsed(raw, expected):
    assert Crawler._price({"price": raw}) == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    # Beside a priced product, so an isolated null stays an ordinary row
    # rather than tripping the price-source guard.
    variant = {k: v for k, v in _KISS_BIG_PRODUCT["variants"][0].items() if k != "price"}
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "variants": [variant]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [22.99, None]


@respx.mock
@pytest.mark.parametrize("mutate", [{"price": None}, {"price": "free"}])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, **mutate))
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    _mock_pages(_FIRE_PRODUCT, _one_pressing(_KISS_BIG_PRODUCT, price=None))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [22.99, None]


@respx.mock
async def test_an_empty_catalog_does_not_trip_the_price_guard(crawler):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False, price=None))
    assert [item async for item in crawler.crawl_catalog()] == []


# --- identity ----------------------------------------------------------

@respx.mock
@pytest.mark.parametrize("missing", ["title", "handle"])
async def test_product_missing_its_identity_is_skipped(crawler, missing):
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, missing: "  "})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_catalog_without_handles_raises(crawler):
    _mock_pages({**_FIRE_PRODUCT, "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_less_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "handle": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_a_sold_out_product_missing_its_handle_still_raises(crawler):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=False),
                {**_KISS_BIG_PRODUCT, "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_on_a_non_record_does_not_satisfy_the_identity_guard(crawler):
    # The identity tally is nested inside the format gate: a shirt's missing
    # handle says nothing about a walk of records.
    _mock_pages({**_CD_PRODUCT, "handle": ""})
    assert [item async for item in crawler.crawl_catalog()] == []


# --- stock readability -------------------------------------------------

@respx.mock
@pytest.mark.parametrize("available", ["false", "true", 1, None])
async def test_a_catalog_with_no_readable_availability_raises(crawler, available):
    _mock_pages(_one_pressing(_FIRE_PRODUCT, available=available))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_unreadable_product_among_real_rows_does_not_raise(crawler):
    _mock_pages(_FIRE_PRODUCT, _one_pressing(_KISS_BIG_PRODUCT, available="false"))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_an_in_stock_variant_with_no_usable_title_is_variant_identity_drift(crawler):
    # The blank-titled variant is in stock but cannot be published, and its
    # named sibling is sold out -- so the product yields nothing while
    # _has_readable_stock_flag still reports the sibling readable. Uncounted,
    # this looks like a shelf that sold out and the snapshot is deleted.
    # Found in review on PR #323.
    _mock_pages({**_FIRE_PRODUCT, "variants": [
        {"title": "   ", "price": "22.99", "available": True, "featured_image": None},
        {"title": "Black", "price": "22.99", "available": False, "featured_image": None},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_placeholder_beside_a_sold_out_sibling_is_identity_drift(crawler):
    # Same hole via the other route a variant is dropped: the placeholder on a
    # multi-variant product names no pressing, so an in-stock one is just as
    # unpublishable as a blank title.
    _mock_pages({**_OPEN_THE_GATES_PRODUCT, "variants": [
        {**_OPEN_THE_GATES_PRODUCT["variants"][0], "title": "Default Title", "available": True},
        {**_OPEN_THE_GATES_PRODUCT["variants"][1], "available": False},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("available", ["true", "false", True, 1, 0, None])
async def test_a_dropped_variant_not_provably_sold_out_is_drift(crawler, available):
    # Only the literal False proves a dropped variant was safely sold out. A
    # blank-titled variant carrying the string "true" is exactly as invisible
    # as one carrying True, and the readable sold-out sibling beside it must
    # not vouch for the emptiness. Found in review on PR #323.
    _mock_pages({**_FIRE_PRODUCT, "variants": [
        {"title": "  ", "price": "22.99", "available": available, "featured_image": None},
        {"title": "Black", "price": "22.99", "available": False, "featured_image": None},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_non_mapping_variant_is_never_provably_sold_out(crawler):
    # _pressings drops a junk entry before anything reads it, which is right
    # for building rows and wrong for trusting an empty one: it carries no
    # availability, so it cannot be proven sold out and the readable sibling
    # beside it must not vouch for the emptiness. Found in review on PR #323.
    _mock_pages({**_FIRE_PRODUCT, "variants": [
        None,
        {"title": "Black", "price": "22.99", "available": False, "featured_image": None},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_variant_identity_drift_is_named_apart_from_product_identity(crawler):
    # The product's own title and handle are present; only the variant's
    # identity failed, and the message must say so. Found in review on PR #323.
    _mock_pages({**_FIRE_PRODUCT, "variants": [
        {"title": "  ", "price": "22.99", "available": True, "featured_image": None},
        {"title": "Black", "price": "22.99", "available": False, "featured_image": None},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_sold_out_variant_with_no_usable_title_is_not_drift(crawler):
    # Only an IN-STOCK dropped variant says a row went missing; a sold-out one
    # would not have yielded anyway.
    _mock_pages({**_FIRE_PRODUCT, "variants": [
        {"title": "", "price": "22.99", "available": False, "featured_image": None},
        {"title": "Black", "price": "22.99", "available": False, "featured_image": None},
    ]})
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_dropped_in_stock_variant_beside_real_rows_does_not_raise(crawler):
    _mock_pages(_FIRE_PRODUCT, {**_KISS_BIG_PRODUCT, "variants": [
        {"title": "  ", "price": "24.99", "available": True, "featured_image": None},
        {"title": "Red", "price": "24.99", "available": False, "featured_image": None},
    ]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein"]


@respx.mock
async def test_a_genuinely_sold_out_shelf_is_a_legitimate_empty_result(crawler):
    _mock_pages(_SISSYBEARS_PRODUCT, _SOLD_OUT_PREORDER_PRODUCT)
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_one_unreadable_variant_does_not_vouch_for_its_readable_sibling(crawler):
    # all(), not any(): the black pressing is a readable False and the
    # coloured one is the string "false", so the product yields nothing and
    # must not vouch for an emptiness half its own doing.
    _mock_pages({**_OPEN_THE_GATES_PRODUCT, "variants": [
        {**_OPEN_THE_GATES_PRODUCT["variants"][0], "available": False},
        {**_OPEN_THE_GATES_PRODUCT["variants"][1], "available": "false"},
    ]})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_product_with_no_usable_variants_is_not_readable(crawler):
    _mock_pages({**_FIRE_PRODUCT, "variants": []})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


# --- covers, URLs, pagination ------------------------------------------

@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    _mock_pages(_KISS_BIG_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/KissBig_Red.png"


@respx.mock
async def test_cover_falls_back_to_product_image(crawler):
    _mock_pages(_TEENAGE_HALLOWEEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/TeenageHalloween.jpg"


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    _mock_pages({**_FIRE_PRODUCT, "images": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_url_is_built_from_the_handle_as_written(crawler):
    _mock_pages({**_FIRE_PRODUCT, "handle": "  amy-klein-fire-12  "})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["url"] == "https://dongiovannirecords.com/products/amy-klein-fire-12"


@respx.mock
async def test_empty_collection_raises(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([]))
    with pytest.raises(RuntimeError, match="returned no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_pagination_walks_every_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_FIRE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_KISS_BIG_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Amy Klein", "Ailbhe Reddy"]
