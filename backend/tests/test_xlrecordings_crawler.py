import httpx
import respx
import pytest
from crawlers.xlrecordings import Crawler

_PRODUCTS_URL = "https://shopusa.xlrecordings.com/collections/all/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-21, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Captured: the store's dominant shape -- the product title is the album
# ALONE, `vendor` names the artist, `product_type` is the release kind rather
# than the medium, and every variant title repeats the album before its
# format. Carries the record, the cassette and the CD under one product.
_DOPAMINE_CHAMBER = {
    "title": "Dopamine Chamber",
    "vendor": "Fontaines D.C.",
    "handle": "xl1673-dopamine-chamber",
    "product_type": "Album",
    "tags": ["Fontaines D.C.", "preorder"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/fdc-open-3.jpg"}],
    "variants": [
        {"id": 49191647772907, "title": "Dopamine Chamber - Deluxe  2X LP",
         "price": "47.58", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/fdc-deluxe.jpg"}},
        {"id": 49086581309675, "title": "Dopamine Chamber - Blue Vinyl LP",
         "price": "28.03", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/fdc-blue.jpg"}},
        {"id": 49131809308907, "title": "Dopamine Chamber - Cassette",
         "price": "13.58", "available": True, "featured_image": None},
        {"id": 49086581342443, "title": "Dopamine Chamber - CD",
         "price": "15.28", "available": True, "featured_image": None},
    ],
}

# Captured: `vendor` is the store's own name, so the artist comes from the
# tags -- which also carry a release year this crawler must subtract. Its
# `Picture Disc` is the live descriptor that names no format word at all and
# is a record anyway, priced with the `White Label LP` rather than the CD.
_I_HEAR_YOU = {
    "title": "I Hear You",
    "vendor": "XLRecordingsProd",
    "handle": "xl1450-i-hear-you",
    "product_type": "Album",
    "tags": ["2024", "Peggy Gou"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/ihy-cover.jpg"}],
    "variants": [
        {"id": 1, "title": "I Hear You - Blue Vinyl LP", "price": "23.78",
         "available": True, "featured_image": None},
        {"id": 2, "title": "I Hear You - White Label LP", "price": "28.03",
         "available": True, "featured_image": None},
        {"id": 3, "title": "I Hear You - Picture Disc", "price": "28.03",
         "available": True, "featured_image": None},
        {"id": 4, "title": "I Hear You - CD", "price": "13.58",
         "available": True, "featured_image": None},
        {"id": 5, "title": "I Hear You - Cassette", "price": "12.73",
         "available": False, "featured_image": None},
    ],
}

# Captured: a double A-side, whose album half carries the ` / ` that a bundle
# also uses. The descriptor is the tail, so the slash never reaches it.
_DOUBLE_A_SIDE = {
    "title": "Gi Mi Keys Back / Auto Fake",
    "vendor": "XL Recordings USA",
    "handle": "gi-mi-keys-back-auto-fake",
    "product_type": "Single",
    "tags": ["Blawan", "preorder"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/gmkb.jpg"}],
    "variants": [
        {"id": 11, "title": 'Gi Mi Keys Back / Auto Fake - 12" Single',
         "price": "16.98", "available": True, "featured_image": None},
    ],
}

# Captured: the store's tags are a flat list with no escaping, so an artist
# whose name contains a comma arrives as two tags -- alphabetised, so the
# comma cannot be put back. `vendor` has it right, which is why vendor leads.
_COMMA_ARTIST = {
    "title": "Goblin",
    "vendor": "Tyler, The Creator",
    "handle": "goblin",
    "product_type": "Album",
    "tags": ["The Creator", "Tyler"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/goblin.jpg"}],
    "variants": [
        {"id": 21, "title": "Goblin - 2X LP", "price": "38.23",
         "available": True, "featured_image": None},
    ],
}

# Captured: the other live product where vendor and tag disagree. The tag
# names one of the two collaborators; vendor names the billing in full.
_COLLABORATION = {
    "title": "We're New Here",
    "vendor": "Gil Scott-Heron & Jamie xx",
    "handle": "were-new-here",
    "product_type": "Album",
    "tags": ["2011", "Gil Scott-Heron"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/wnh.jpg"}],
    "variants": [
        {"id": 31, "title": "We're New Here - LP", "price": "25.48",
         "available": True, "featured_image": None},
    ],
}

# Captured: the dangerous bundle. Nothing but the product title says so --
# `product_type` is blank, it carries ONE option like any record, and both
# variant tails read as ordinary pressings. Its price is two records'.
_RECORD_BUNDLE = {
    "title": "Nourished By Time Bundle",
    "vendor": "XL Recordings USA",
    "handle": "nourished-by-time-bundle",
    "product_type": "",
    "tags": [],
    "options": [{"name": "The Passionate Ones (Format)"}],
    "images": [{"src": "https://cdn.shopify.com/nbt-bundle.jpg"}],
    "variants": [
        {"id": 41, "title": "The Passionate Ones - Crystal Clear LP",
         "price": "51.25", "available": True, "featured_image": None},
        {"id": 42, "title": "The Passionate Ones - LP",
         "price": "49.80", "available": True, "featured_image": None},
    ],
}

# Captured: the record-plus-garment bundle. Two options rather than one, and
# every variant tail names the shirt half, hiding the `2X LP` written before it.
_MERCH_BUNDLE = {
    "title": "Basement Jaxx Rooty Bundle",
    "vendor": "XL Recordings USA",
    "handle": "basement-jaxx-rooty-bundle",
    "product_type": "",
    "tags": ["Basement Jaxx"],
    "options": [{"name": "Rooty (Format)"},
                {"name": "Rooty Anniversary T-shirt (Format)"}],
    "images": [{"src": "https://cdn.shopify.com/rooty-bundle.jpg"}],
    "variants": [
        {"id": 51,
         "title": "Rooty - Blue & Pink 2X LP / Rooty Anniversary T-shirt - Black Small",
         "price": "71.36", "available": True, "featured_image": None},
        {"id": 52,
         "title": "Rooty - CD / Rooty Anniversary T-shirt - Black XL",
         "price": "51.81", "available": True, "featured_image": None},
    ],
}

# Captured: merch the store left untyped. Only the bare-size descriptors say
# it is not a record.
_UNTYPED_MERCH = {
    "title": "Celeste T-Shirt",
    "vendor": "XL Recordings USA",
    "handle": "celeste-t-shirt",
    "product_type": "",
    "tags": ["Everything Is Recorded"],
    "options": [{"name": "Size"}],
    "images": [{"src": "https://cdn.shopify.com/celeste.jpg"}],
    "variants": [
        {"id": 61, "title": "S", "price": "33.98", "available": True,
         "featured_image": None},
        {"id": 62, "title": "XL", "price": "33.98", "available": True,
         "featured_image": None},
    ],
}

# Captured: merch the store DID type, whose descriptor names a hard good
# rather than a size.
_TYPED_MERCH = {
    "title": "Zoetrope Slipmat",
    "vendor": "XL Recordings USA",
    "handle": "zoetrope-slipmat",
    "product_type": "Merch",
    "tags": ["2024", "Peggy Gou"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/slipmat.jpg"}],
    "variants": [
        {"id": 71, "title": "Zoetrope Slipmat - SLIPMAT", "price": "25.48",
         "available": True, "featured_image": None},
    ],
}

# Captured: a release the store sells on CD only. It is a record by every
# other test and must still yield nothing.
_CD_ONLY = {
    "title": "Gimme my gun",
    "vendor": "XL Recordings USA",
    "handle": "gimme-my-gun",
    "product_type": "Single",
    "tags": ["Standing On The Corner"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/gmg.jpg"}],
    "variants": [
        {"id": 81, "title": "Gimme my gun - CD Maxi", "price": "11.88",
         "available": True, "featured_image": None},
    ],
}

# Captured: a compilation. `Various Artists` is a tag like any other and is
# published as the artist, which is what the library match expects.
_COMPILATION = {
    "title": "XL Banger",
    "vendor": "XL Recordings USA",
    "handle": "xl-banger",
    "product_type": "EP",
    "tags": ['12" singles', "Various Artists"],
    "options": [{"name": "Format"}],
    "images": [{"src": "https://cdn.shopify.com/xlb.jpg"}],
    "variants": [
        {"id": 91, "title": 'XL Banger - 12" EP', "price": "12.73",
         "available": True, "featured_image": None},
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


def test_plugin_identity():
    assert Crawler.site_name == "XL Recordings"
    assert Crawler.base_url == "https://shopusa.xlrecordings.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary


# ---------------------------------------------------------------------------
# Row shape
# ---------------------------------------------------------------------------

@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Fontaines D.C.",
        "title": "Dopamine Chamber — Blue Vinyl LP",
        "format": "Vinyl",
        "price": 28.03,
        "currency": "USD",
        "url": "https://shopusa.xlrecordings.com/products/xl1673-dopamine-chamber",
        "cover_image_url": "https://cdn.shopify.com/fdc-blue.jpg",
    }]


@respx.mock
async def test_album_leads_the_title_so_the_library_prefix_match_holds(crawler):
    # db._library_release_match_sql matches a stock row against a library
    # release on an exact-or-prefix-with-space test, which only holds while
    # the album comes before the pressing descriptor.
    _mock_pages(_DOPAMINE_CHAMBER)
    titles = [i["title"] async for i in crawler.crawl_catalog()]
    assert all(t.startswith("Dopamine Chamber ") for t in titles)


@respx.mock
async def test_cover_image_falls_back_to_the_product_image(crawler):
    _mock_pages(_one_pressing(_I_HEAR_YOU, 0))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/ihy-cover.jpg"


@respx.mock
async def test_every_pressing_of_a_product_becomes_its_own_row(crawler):
    _mock_pages(_DOPAMINE_CHAMBER)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Dopamine Chamber — Deluxe 2X LP",
        "Dopamine Chamber — Blue Vinyl LP",
    ]


# ---------------------------------------------------------------------------
# Artist resolution
# ---------------------------------------------------------------------------

@respx.mock
async def test_artist_comes_from_vendor_when_vendor_names_an_artist(crawler):
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Fontaines D.C."


@respx.mock
async def test_artist_comes_from_the_tag_when_vendor_is_the_label(crawler):
    _mock_pages(_one_pressing(_I_HEAR_YOU, 0))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Peggy Gou"


@respx.mock
async def test_vendor_beats_a_tag_that_lost_the_artists_comma(crawler):
    # The tags spell `Tyler, The Creator` as two alphabetised fragments.
    _mock_pages(_COMMA_ARTIST)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Tyler, The Creator"


@respx.mock
async def test_a_collaboration_is_credited_to_its_leading_artist(crawler):
    # The library match decides this, not completeness. parse_release() keeps
    # artists[0] alone and _library_release_match_sql() compares the artist
    # for EQUALITY -- only the title is prefix-matched -- so the full billing
    # is the better name and the worse key, and this field is a key.
    _mock_pages(_COLLABORATION)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Gil Scott-Heron"


def test_a_comma_in_an_artists_name_is_not_a_collaboration_separator():
    # The failure the rule must not cause: truncating one artist whose name
    # merely begins with the tag. `Tyler, The Creator` is protected twice --
    # Shopify split its comma into two tags, and a comma is not a separator.
    assert Crawler._artist(_COMMA_ARTIST) == "Tyler, The Creator"
    assert Crawler._artist({**_COMMA_ARTIST, "tags": ["Tyler"]}) == "Tyler, The Creator"


@pytest.mark.parametrize("vendor,tag,expected", [
    # Separators between two credits -- reduce to the leading one.
    ("Gil Scott-Heron & Jamie xx", "Gil Scott-Heron", "Gil Scott-Heron"),
    ("Someone + Another", "Someone", "Someone"),
    ("Someone feat. Another", "Someone", "Someone"),
    ("Someone featuring Another", "Someone", "Someone"),
    ("Someone and Another", "Someone", "Someone"),
    ("Someone with Another", "Someone", "Someone"),
    ("Someone vs. Another", "Someone", "Someone"),
    ("Someone x Another", "Someone", "Someone"),
    # Part of a name, not a separator -- the vendor stands.
    ("Tyler, The Creator", "Tyler", "Tyler, The Creator"),
    # `x` needs trailing whitespace, so `xx` cannot pose as one.
    ("Jamie xx", "Jamie", "Jamie xx"),
    # Not a leading prefix at all.
    ("Fontaines D.C.", "Some Other Act", "Fontaines D.C."),
    # Already the whole billing.
    ("Peggy Gou", "Peggy Gou", "Peggy Gou"),
])
def test_only_a_credit_separator_reduces_the_vendor(vendor, tag, expected):
    product = {**_COLLABORATION, "vendor": vendor, "tags": [tag]}
    assert Crawler._artist(product) == expected


def test_two_tags_never_reduce_the_vendor():
    # Two survivors may equally be one comma-split name or two collaborators,
    # so neither can be trusted as the leading credit.
    product = {**_COLLABORATION, "vendor": "Gil Scott-Heron & Jamie xx",
               "tags": ["Gil Scott-Heron", "Jamie xx"]}
    assert Crawler._artist(product) == "Gil Scott-Heron & Jamie xx"


@respx.mock
async def test_a_compilations_various_artists_tag_is_an_artist_like_any_other(crawler):
    _mock_pages(_COMPILATION)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Various Artists"


@pytest.mark.parametrize("vendor", [
    "XLRecordingsProd",
    "XL Recordings USA",
    # Not a live spelling. The prefix test exists so that a storefront the
    # label opens later is read as the label rather than published as an
    # artist of that name for every record it fronts.
    "XL Recordings UK",
    "xl recordings",
])
def test_label_vendors_fall_through_to_the_tags(vendor):
    assert Crawler._is_label_vendor(vendor) is True


@pytest.mark.parametrize("vendor", [
    "Fontaines D.C.", "Radiohead", "Peggy Gou", "Tyler, The Creator",
    "Gil Scott-Heron & Jamie xx", "The XX",
])
def test_artist_vendors_are_used_as_written(vendor):
    assert Crawler._is_label_vendor(vendor) is False


@pytest.mark.parametrize("tag", ["2024", "1998", '12" singles', "preorder", "XL Merch"])
def test_the_stores_non_artist_shelves_are_subtracted(tag):
    product = {**_I_HEAR_YOU, "tags": [tag, "Peggy Gou"]}
    assert Crawler._artist(product) == "Peggy Gou"


def test_a_year_tag_is_matched_by_shape_not_enumerated():
    # Enumerating them starts publishing an artist called "2027" the January
    # after this ships.
    product = {**_I_HEAR_YOU, "tags": ["2031", "Peggy Gou"]}
    assert Crawler._artist(product) == "Peggy Gou"


def test_two_surviving_tags_name_no_artist_rather_than_guessing():
    # Naming the wrong one is worse than naming none: the artist is hashed
    # into item_key and is what the library match reads.
    product = {**_I_HEAR_YOU, "vendor": "XLRecordingsProd",
               "tags": ["Peggy Gou", "Some Other Act"]}
    assert Crawler._artist(product) == ""


def test_a_label_vendor_with_no_tag_names_no_artist():
    assert Crawler._artist({**_RECORD_BUNDLE, "tags": []}) == ""


def test_a_blank_vendor_names_no_artist_rather_than_falling_back_to_tags():
    # A missing vendor is unreadable, not a claim that the label owns the
    # record, so it is NOT the case the tag fallback exists for. Falling
    # through would silently re-credit the very products vendor leads for.
    assert Crawler._artist({**_COLLABORATION, "vendor": ""}) == ""
    assert Crawler._artist({**_COLLABORATION, "vendor": "   "}) == ""
    assert Crawler._artist({**_COLLABORATION, "vendor": None}) == ""


@respx.mock
async def test_a_blanked_vendor_raises_rather_than_re_crediting_the_catalogue(crawler):
    # The failure this trades for: skipping the product makes a store-wide
    # loss of `vendor` trip the artist-source guard, where re-crediting from
    # the tags would have completed the walk and re-keyed every row whose
    # vendor said more than its tag.
    _mock_pages({**_COLLABORATION, "vendor": ""}, {**_COMMA_ARTIST, "vendor": ""})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


def test_a_non_string_tag_is_ignored_rather_than_crashing():
    product = {**_I_HEAR_YOU, "vendor": "XLRecordingsProd",
               "tags": [None, 2024, "Peggy Gou"]}
    assert Crawler._artist(product) == "Peggy Gou"


@respx.mock
async def test_a_record_with_no_readable_artist_is_skipped(crawler):
    # It would otherwise be emitted under an identity that cannot match the
    # user's library and can never be corrected without re-keying.
    _mock_pages({**_I_HEAR_YOU, "vendor": "XLRecordingsProd", "tags": ["2024"]},
                _one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fontaines D.C."]


# ---------------------------------------------------------------------------
# Pressing descriptors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant_title,expected", [
    ("Dopamine Chamber - Blue Vinyl LP", "Blue Vinyl LP"),
    # The store does not always prefix with the product's own title.
    ("SOTC II - LP", "LP"),
    # The album half carries the slash of a double A-side; the tail does not.
    ('Gi Mi Keys Back / Auto Fake - 12" Single', '12" Single'),
    # Split on the LAST separator: a bundle writes two products into one title.
    ("Rooty - Blue & Pink 2X LP / Rooty Anniversary T-shirt - Black Small",
     "Black Small"),
    # A hyphen with no surrounding whitespace is not a separator.
    ("We're New Here - CD-2", "CD-2"),
    # No separator at all: the descriptor is the whole title.
    ("XL", "XL"),
    ("Small", "Small"),
])
def test_descriptor_is_the_tail_of_the_variant_title(variant_title, expected):
    assert Crawler._descriptor(variant_title) == expected


# ---------------------------------------------------------------------------
# The format gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("descriptor", [
    "LP", "2X LP", "3X LP", "Black Vinyl LP", "White Label LP", "Pink LP",
    "Crystal Clear LP", "Deluxe LP", "LP (Exclusive)", "Blue Marble Vinyl LP",
    '12" EP', '12" Single', '7" Single', '2X 10" Album',
    "2xLP Lenticular Cover", "Deep Ocean Pearl LP", "Light Green 2X LP",
    # A record with an extra, priced as a record.
    "Blue Yolk LP + Signed Print", "Deluxe 2X LP w/ signed print",
    # A vinyl box set that happens to include a cassette. The record word is
    # tested first, so the `CS` never rejects it.
    "Deluxe 3X LP + CS + Books LP",
    # Names no format word at all and is a record anyway -- the live
    # descriptor that reaches the default-admit line.
    "Picture Disc",
])
def test_live_vinyl_vocabulary_is_admitted(descriptor):
    assert Crawler._is_vinyl(descriptor) is True


@pytest.mark.parametrize("descriptor", [
    "CD", "2X CD", "3X CD", "Deluxe CD", "Standard CD", "CD Maxi",
    "CD (Exclusive)", "CD-2", "Cassette", "DVD",
    # The store's own abbreviation for a cassette. It names no medium a
    # reader would recognise, so without it the default-admit line would
    # publish a cassette at a cassette's price as a record.
    "CS Album", "CS EP",
])
def test_other_media_are_rejected(descriptor):
    assert Crawler._is_vinyl(descriptor) is False


@pytest.mark.parametrize("descriptor", [
    "S", "M", "L", "XL", "XXL", "Small", "Medium", "Large",
    "Black Small", "Black XL", "Black XXL", "Charcoal Cotton Medium",
    "White Cotton XXL", "Long Sleeve Shirt Small", "One Size",
])
def test_garment_sizes_are_rejected(descriptor):
    assert Crawler._is_vinyl(descriptor) is False


@pytest.mark.parametrize("descriptor", ["SLIPMAT", "UMBRELLA", "Tote Bag", "Black T-Shirt"])
def test_merch_descriptors_are_rejected(descriptor):
    assert Crawler._is_vinyl(descriptor) is False


def test_an_empty_descriptor_is_not_a_record():
    # Shopify's `Default Title` placeholder folds to this. On this store
    # nothing else claims a format, so admitting it would publish a CD, a
    # cassette or a tote bag as a record on no evidence at all.
    assert Crawler._is_vinyl("") is False


@respx.mock
async def test_a_sole_placeholder_variant_yields_nothing(crawler):
    # Beside a real record, so the walk is not empty and the format-source
    # guard is not what this is measuring.
    _mock_pages(_one_pressing(_I_HEAR_YOU, 0, title="Default Title"),
                _one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dopamine Chamber — Blue Vinyl LP"]


@pytest.mark.parametrize("descriptor", ["éLP CD", "éLP CD"])
def test_an_accent_before_a_record_word_does_not_open_the_boundary(descriptor):
    # `[a-z]` is ASCII-only even under IGNORECASE, so an accented letter is
    # not a letter to it and `lp` matches inside the word -- admitting on the
    # record word before the trailing `CD` is ever reached. Both the
    # precomposed and the decomposed spelling must decide the same way.
    assert Crawler._is_vinyl(descriptor) is False


def test_an_inch_marker_needs_its_own_right_hand_boundary():
    # Without one, `12"CD` reads as a complete inch marker and the `CD` is
    # never reached.
    assert Crawler._is_vinyl('12"CD') is False


# ---------------------------------------------------------------------------
# What is not a single record
# ---------------------------------------------------------------------------

@respx.mock
async def test_a_record_plus_record_bundle_is_skipped(crawler):
    # Nothing but the product title says so: blank `product_type`, one option
    # like any record, and both tails read as ordinary pressings -- at a price
    # that is two records'.
    _mock_pages(_RECORD_BUNDLE, _one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dopamine Chamber — Blue Vinyl LP"]


@respx.mock
async def test_a_record_plus_garment_bundle_is_skipped(crawler):
    _mock_pages(_MERCH_BUNDLE, _one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dopamine Chamber — Blue Vinyl LP"]


def test_a_second_option_marks_a_bundle_that_does_not_say_bundle():
    # Shopify gives a product one option per thing being chosen, and every
    # record in this store has exactly one.
    renamed = {**_MERCH_BUNDLE, "title": "Rooty Anniversary Set"}
    assert Crawler._is_bundle(renamed) is True


def test_the_bundle_word_marks_a_bundle_carrying_only_one_option():
    assert Crawler._is_bundle(_RECORD_BUNDLE) is True


def test_a_record_carries_exactly_one_option_and_is_not_a_bundle():
    assert Crawler._is_bundle(_DOPAMINE_CHAMBER) is False


def test_a_non_mapping_option_entry_is_ignored_rather_than_crashing():
    assert Crawler._is_bundle({**_DOPAMINE_CHAMBER, "options": [{"name": "Format"}, None]}) is False


@respx.mock
async def test_typed_merch_is_skipped(crawler):
    _mock_pages(_TYPED_MERCH, _one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dopamine Chamber — Blue Vinyl LP"]


@respx.mock
async def test_untyped_merch_is_skipped_on_its_descriptors(crawler):
    _mock_pages(_UNTYPED_MERCH, _one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dopamine Chamber — Blue Vinyl LP"]


@respx.mock
async def test_a_cd_only_release_yields_nothing(crawler):
    _mock_pages(_CD_ONLY, _one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dopamine Chamber — Blue Vinyl LP"]


@respx.mock
async def test_the_cd_and_cassette_beside_a_record_are_not_listed(crawler):
    _mock_pages(_I_HEAR_YOU)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "I Hear You — Blue Vinyl LP",
        "I Hear You — White Label LP",
        "I Hear You — Picture Disc",
    ]


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@respx.mock
async def test_a_sold_out_pressing_is_not_listed(crawler):
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, available=False))
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
@pytest.mark.parametrize("available", ["false", "true", 1, None])
async def test_only_the_literal_true_admits_a_pressing(crawler, available):
    # The string "false" is truthy, so a falsiness test would publish a
    # sold-out record as in stock. Paired with a readable record so that the
    # walk is non-empty and the drift guards stay out of the way.
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, available=available),
                _one_pressing(_I_HEAR_YOU, 0))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Peggy Gou"]


@respx.mock
async def test_a_pre_order_is_listed_as_an_ordinary_row(crawler):
    # No ` (Pre-Order)` marker, though the store tags one: item_key hashes the
    # title, so a marker that disappeared when the record shipped would
    # re-key every pressing and orphan the saves and judgments held against it.
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Dopamine Chamber — Blue Vinyl LP"


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.parametrize("price", [None, "", "free", "0", "-1", True])
async def test_an_unusable_price_is_none_rather_than_a_number(crawler, price):
    # bool before float(): bool is an int subclass, so True would price a
    # record at 1.
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, price=price),
                _one_pressing(_I_HEAR_YOU, 0))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["price"] is None


@pytest.mark.parametrize("price", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_price_is_rejected(price):
    # Unreachable through a response body here, because httpx encodes with
    # allow_nan=False and refuses them -- Python's own json.dumps would emit
    # the non-standard `NaN`/`Infinity` tokens instead. Either way a server is
    # free to send those tokens and the default decoder accepts them, so
    # _price still has to answer for the values that come back.
    assert Crawler._price({"price": price}) is None


@respx.mock
async def test_a_price_given_as_a_number_is_read(crawler):
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, price=28.03))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["price"] == 28.03


# ---------------------------------------------------------------------------
# Drift guards
#
# db.replace_stock_items() DELETEs this crawler's snapshot before inserting,
# and _sync_stock only skips that call when the crawl raised -- so a
# completed-but-empty walk is destructive where a raise is inert.
# ---------------------------------------------------------------------------

@respx.mock
async def test_an_empty_collection_raises(crawler):
    _mock_pages(empty_page=1)
    with pytest.raises(RuntimeError, match="returned no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_losing_both_artist_sources_raises(crawler):
    _mock_pages({**_DOPAMINE_CHAMBER, "vendor": "XL Recordings USA", "tags": []},
                {**_I_HEAR_YOU, "vendor": "XLRecordingsProd", "tags": ["2024"]})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_losing_the_format_source_raises(crawler):
    # Unlike the sibling Shopify crawlers, the product-level format gate here
    # is POSITIVE -- only a variant descriptor says a product is a record --
    # so its disappearance CAN silently empty the walk, and the guard they
    # deliberately omit is required.
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 3), _one_pressing(_I_HEAR_YOU, 3))
    with pytest.raises(RuntimeError, match="format-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_catalog_of_nothing_but_merch_raises_rather_than_emptying_the_snapshot(crawler):
    # An all-merch page is not itself evidence the store restyled anything,
    # but it is empty, and an empty walk deletes the snapshot -- so it has to
    # raise. The format guard is the one that catches it.
    _mock_pages(_TYPED_MERCH, _UNTYPED_MERCH)
    with pytest.raises(RuntimeError, match="format-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_losing_every_price_raises(crawler):
    _mock_pages({**_DOPAMINE_CHAMBER,
                 "variants": [{**v, "price": None} for v in _DOPAMINE_CHAMBER["variants"]]})
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_isolated_null_prices_are_tolerated(crawler):
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, price=None),
                _one_pressing(_I_HEAR_YOU, 0))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 23.78]


@respx.mock
async def test_an_empty_walk_beside_a_missing_handle_raises(crawler):
    _mock_pages(_one_pressing({**_DOPAMINE_CHAMBER, "handle": ""}, 1, available=False))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_empty_walk_beside_a_missing_title_raises(crawler):
    _mock_pages(_one_pressing({**_DOPAMINE_CHAMBER, "title": ""}, 1, available=False))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_empty_walk_beside_a_dropped_in_stock_variant_raises(crawler):
    # A blank-titled in-stock variant beside a sold-out sibling makes an
    # in-stock product look sold out. Only the literal False proves sold out.
    _mock_pages({**_DOPAMINE_CHAMBER, "variants": [
        {**_DOPAMINE_CHAMBER["variants"][1], "title": "", "available": True},
        {**_DOPAMINE_CHAMBER["variants"][0], "available": False},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_junk_variant_entry_counts_as_a_dropped_variant(crawler):
    _mock_pages({**_DOPAMINE_CHAMBER, "variants": [
        "not a mapping",
        {**_DOPAMINE_CHAMBER["variants"][0], "available": False},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@pytest.mark.parametrize("spelling", ["Default Title", "default title", "Default", "default", "DEFAULT"])
def test_both_shopify_placeholder_spellings_are_recognised(spelling):
    # Bare `Default` is live alongside the long spelling (realgonemusic.py
    # found both in one catalogue). Missing it here would not skip a row, it
    # would fabricate one: the bare word survives as a descriptor, the
    # negative gate default-admits it, and the product publishes
    # `<album> — Default` as a pressing.
    product = _one_pressing(_DOPAMINE_CHAMBER, 1, title=spelling)
    assert [descriptor for _, descriptor in Crawler._pressings(product)] == [""]
    assert Crawler._claims_vinyl(product) is False
    assert Crawler._items(product) == []


@respx.mock
@pytest.mark.parametrize("spelling", ["Default Title", "Default"])
async def test_no_placeholder_spelling_fabricates_a_pressing(crawler, spelling):
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, title=spelling),
                _one_pressing(_I_HEAR_YOU, 0))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["I Hear You — Blue Vinyl LP"]


@respx.mock
async def test_an_available_placeholder_variant_is_unusable(crawler):
    # It is KEPT as a pressing, which is what made it dangerous: nothing
    # called it dropped, while it named no format and yielded no row, so it
    # reached no tally at all.
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, title="Default Title"))
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_sold_out_record_cannot_vouch_for_an_available_placeholder(crawler):
    # The mixed case that defeats every other guard: the sold-out record keeps
    # `claims_vinyl` non-zero so the format guard stays quiet, and the
    # placeholder is neither dropped nor unreadable. Without this the walk
    # completes empty and replace_stock_items() deletes the snapshot.
    _mock_pages(
        _one_pressing(_DOPAMINE_CHAMBER, 1, title="Default Title", available=True),
        {**_I_HEAR_YOU,
         "variants": [{**v, "available": False} for v in _I_HEAR_YOU["variants"]]},
    )
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_placeholder_proven_sold_out_does_not_raise(crawler):
    # The exact complement of the case above: only the literal False proves a
    # placeholder sold out, and once proven it must NOT be what makes the walk
    # raise. Paired with a readably sold-out record, because a catalogue in
    # which nothing claims a format is format-source drift on its own -- the
    # point here is that the placeholder adds nothing to that.
    _mock_pages(
        _one_pressing(_DOPAMINE_CHAMBER, 1, title="Default Title", available=False),
        {**_I_HEAR_YOU,
         "variants": [{**v, "available": False} for v in _I_HEAR_YOU["variants"]]},
    )
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_product_carrying_no_variants_at_all_is_unusable(crawler):
    # No variants is availability, format and price absent at once: the
    # product cannot be proven sold out, and cannot be shown not to have been
    # a record. It must not be what an empty walk rests on.
    _mock_pages({**_DOPAMINE_CHAMBER, "variants": []})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readably_sold_out_record_cannot_vouch_for_a_variant_less_one(crawler):
    # The partial case, and the one that actually deletes a snapshot: a
    # genuinely sold-out record keeps `claims_vinyl` non-zero, so the
    # format-source guard stays quiet, and without this the walk would
    # complete empty with nothing raised.
    _mock_pages(
        {**_I_HEAR_YOU,
         "variants": [{**v, "available": False} for v in _I_HEAR_YOU["variants"]]},
        {**_DOPAMINE_CHAMBER, "variants": []},
    )
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_empty_walk_beside_an_unreadable_stock_flag_raises(crawler):
    _mock_pages(_one_pressing(_DOPAMINE_CHAMBER, 1, available="false"))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_genuinely_sold_out_catalog_is_empty_without_raising(crawler):
    # The one empty outcome that must NOT raise: every product readable, every
    # pressing a readable False.
    _mock_pages(
        {**_DOPAMINE_CHAMBER,
         "variants": [{**v, "available": False} for v in _DOPAMINE_CHAMBER["variants"]]},
        {**_I_HEAR_YOU,
         "variants": [{**v, "available": False} for v in _I_HEAR_YOU["variants"]]},
    )
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_an_unreadable_cd_does_not_condemn_a_readably_sold_out_record(crawler):
    # The stock flags are read through the same format gate the rows are
    # published through: this store sells the record, the CD and the cassette
    # as variants of one product, so a CD's unreadable flag must not raise
    # over a record that was read perfectly.
    _mock_pages({**_DOPAMINE_CHAMBER, "variants": [
        {**_DOPAMINE_CHAMBER["variants"][1], "available": False},
        {**_DOPAMINE_CHAMBER["variants"][3], "available": "false"},
    ]})
    assert [item async for item in crawler.crawl_catalog()] == []


# ---------------------------------------------------------------------------
# Pagination and citizenship
# ---------------------------------------------------------------------------

@respx.mock
async def test_pagination_walks_until_a_page_comes_back_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_one_pressing(_DOPAMINE_CHAMBER, 1)]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_one_pressing(_I_HEAR_YOU, 0)]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Fontaines D.C.", "Peggy Gou"]


async def test_the_sites_crawl_delay_is_passed_as_a_pacing_floor(monkeypatch):
    # `crawl_delay_seconds` is admin-editable with no lower bound, so
    # honouring the store's robots.txt has to be enforced by the design.
    seen = {}

    async def fake_iter_products(base_url, collection_slug, *, min_delay=0.0):
        seen["min_delay"] = min_delay
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("crawlers.xlrecordings.iter_products", fake_iter_products)
    with pytest.raises(RuntimeError, match="returned no products"):
        [item async for item in Crawler().crawl_catalog()]
    assert seen["min_delay"] == 10.0
