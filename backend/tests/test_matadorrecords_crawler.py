import httpx
import respx
import pytest
from crawlers.matadorrecords import Crawler

_PRODUCTS_URL = "https://matadorrecords.com/collections/all/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-06, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape -- vendor is the artist, the CD sits
# beside the LP as a sibling variant, each variant title repeats the album
# name and then names the format after " - ", and the LP variant carries its
# own featured image.
_ADORE_LIFE_PRODUCT = {
    "title": "Adore Life",
    "vendor": "Savages",
    "handle": "adore-life",
    "product_type": "Album",
    "tags": ["migrated", "Savages"],
    "images": [{"src": "https://cdn.shopify.com/Savages_AdoreLife_Packshot_1x1_1.jpg"}],
    "variants": [
        {"id": 49486273675585, "title": "Adore Life - LP", "price": "19.53",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/savages_-adore_life.jpg"}},
        {"id": 49486273708353, "title": "Adore Life - CD", "price": "13.58",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/savages_-adore_life.jpg"}},
    ],
}

# Captured: variant titles with and without the " - " separator on one
# product, two sold-out colour pressings, and a variant with no featured
# image of its own.
_ANTICS_PRODUCT = {
    "title": "Antics",
    "vendor": "Interpol",
    "handle": "antics",
    "product_type": "Album",
    "tags": ["Interpol", "migrated"],
    "images": [{"src": "https://cdn.shopify.com/interpol_antics_1x1_LP_front.jpg"}],
    "variants": [
        {"id": 49486258307393, "title": "Antics CD", "price": "13.58",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/ole-616_antics.jpg"}},
        {"id": 49486258340161, "title": "Antics Black Vinyl LP", "price": "21.23",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/ole-616_antics.jpg"}},
        {"id": 49486258372929, "title": "Antics - White Vinyl LP", "price": "21.23",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/ole-616_antics.jpg"}},
        {"id": 49536165806401, "title": "Antics - Red LP", "price": "22.08",
         "available": False, "featured_image": None},
    ],
}

# Captured: a pre-order, tagged and available, with the store's own double
# space inside "Deluxe  LP" and no variant images.
_PERFECTH_PRODUCT = {
    "title": "Perfecth",
    "vendor": "Queens Of The Stone Age",
    "handle": "ole2253-perfecth",
    "product_type": "Album",
    "tags": ["preorder", "Queens of the Stone Age"],
    "images": [{"src": "https://cdn.shopify.com/perfecth_pack.jpg"}],
    "variants": [
        {"id": 52014940717377, "title": "Perfecth - Deluxe  LP", "price": "59.48",
         "available": True, "featured_image": None},
        {"id": 52014940750145, "title": "Perfecth - Standard LP", "price": "25.48",
         "available": True, "featured_image": None},
        {"id": 52014940782913, "title": "Perfecth - CD", "price": "14.43",
         "available": True, "featured_image": None},
    ],
}

# Captured: vendor is the label's myshopify name and the artist is in tags;
# a vinyl box set beside a CD box set, both named "Boxset".
_77_81_PRODUCT = {
    "title": "77-81",
    "vendor": "MatadorRecordsProd",
    "handle": "77-81",
    "product_type": "Album",
    "tags": ["Gang of Four", "migrated"],
    "images": [{"src": "https://cdn.shopify.com/unopenedbox_191_1.jpg"}],
    "variants": [
        {"id": 49486290649409, "title": "77-81 - Vinyl Boxset", "price": "148.73",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/unopenedbox_191_1.jpg"}},
        {"id": 49486290682177, "title": "77-81 - 4CD Boxset", "price": "67.98",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/unopenedbox_191_1.jpg"}},
    ],
}

# Captured: a split 7" credited to two artists through tags, sold out, and
# with the variant title reversing the product title's A/B order.
_SPLIT_SINGLE_PRODUCT = {
    "title": "Hang Them All / No Garage",
    "vendor": "MatadorRecordsProd",
    "handle": "hang-them-all-no-garage",
    "product_type": "Single",
    "tags": ["Jay Reatard", "migrated", "sale", "Sonic Youth"],
    "images": [{"src": "https://cdn.shopify.com/jay-sy-7.jpg"}],
    "variants": [
        {"id": 49486266499393, "title": "No Garage / Hang Them All - 7\"", "price": "6.78",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/jay-sy-7.jpg"}},
    ],
}

# Captured: the one product whose title carries a "{vendor} - " prefix.
_COMING_APART_PRODUCT = {
    "title": "Body/Head - Coming Apart",
    "vendor": "Body/Head",
    "handle": "coming-apart",
    "product_type": "Album",
    "tags": ["Body/Head", "migrated"],
    "images": [{"src": "https://cdn.shopify.com/bodyheadcoverimagehighres.jpg"}],
    "variants": [
        {"id": 49486269546817, "title": "Coming Apart - Dbl LP", "price": "19.53",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/bodyheadcoverimagehighres.jpg"}},
        {"id": 49486269579585, "title": "Coming Apart - CD", "price": "12.73",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/bodyheadcoverimagehighres.jpg"}},
    ],
}

# Captured: single-variant, sold out, and the product title already names the
# format -- the variant title is the product title verbatim.
_LAZY_SON_PRODUCT = {
    "title": "I'm A Lazy Son...But I'm The Only Son - 12\" EP",
    "vendor": "MatadorRecordsProd",
    "handle": "im-a-lazy-son-but-im-the-only-son-12-ep",
    "product_type": "EP",
    "tags": ["Lower", "migrated"],
    "images": [{"src": "https://cdn.shopify.com/L_IL_1_lower_lazyson_coverart.jpg"}],
    "variants": [
        {"id": 49486273380673, "title": "I'm A Lazy Son...But I'm The Only Son - 12\" EP", "price": "12.00",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/L_IL_1_lower_lazyson_coverart.jpg"}},
    ],
}

# Captured: a single-variant EP whose parenthesised edition the variant
# writes unwrapped.
_VALENTINE_DEMOS_PRODUCT = {
    "title": "Valentine (Demos)",
    "vendor": "Snail Mail",
    "handle": "valentine-demos",
    "product_type": "EP",
    "tags": ["migrated", "Snail Mail"],
    "images": [{"src": "https://cdn.shopify.com/valentine_demos.jpeg"}],
    "variants": [
        {"id": 49486304018753, "title": "Valentine Demos - 12\" EP", "price": "16.98",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/valentine_demos.jpeg"}},
    ],
}

# Captured: a music-typed product with no record in it at all.
_DVD_PRODUCT = {
    "title": "Psychic Live",
    "vendor": "Darkside",
    "handle": "psychic-live",
    "product_type": "Album",
    "tags": ["DARKSIDE", "migrated"],
    "images": [{"src": "https://cdn.shopify.com/ole-1087_darkside_-_psychic_live.jpg"}],
    "variants": [
        {"id": 49486273282369, "title": "Psychic Live - DVD", "price": "13.57",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/ole-1087_darkside_-_psychic_live.jpg"}},
    ],
}

# Captured: merch, with a vendor that is a real artist.
_MERCH_PRODUCT = {
    "title": "Some Like It Hot T-shirt",
    "vendor": "Bar Italia",
    "handle": "ole2200-some-like-it-hot-t-shirt",
    "product_type": "Merch",
    "tags": ["bar italia"],
    "images": [{"src": "https://cdn.shopify.com/BarItalia_SomeLikeItHot_Tshirt_1x1.jpg"}],
    "variants": [
        {"id": 50730028663105, "title": "Some Like It Hot T-shirt - Black XL", "price": "23.78",
         "available": True, "featured_image": None},
    ],
}

# Captured: an untyped bundle whose variant titles are full of LPs. Trimmed
# to two of its combinations.
_BUNDLE_PRODUCT = {
    "title": "Darkside Bundle",
    "vendor": "Darkside",
    "handle": "darkside-bundle",
    "product_type": "",
    "tags": ["DARKSIDE"],
    "images": [{"src": "https://cdn.shopify.com/1x1darksidebundle.jpg"}],
    "variants": [
        {"id": 49871481176385, "title": "Nothing - Red LP / Spiral - White Vinyl Dbl LP / Psychic - Black Vinyl Dbl LP",
         "price": "71.86", "available": True, "featured_image": None},
        {"id": 49871481209153, "title": "Nothing - Red LP / Spiral - White Vinyl Dbl LP / Psychic - CD",
         "price": "61.91", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_pages(*products, empty_page=2):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(list(products)))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(empty_page)}).mock(
        return_value=_page_response([]))


def _lp_only(product, **overrides):
    # A captured product reduced to its first variant, the LP, so a test can
    # alter that one variant without carrying the CD sibling along.
    return {**product, "variants": [{**product["variants"][0], **overrides}]}


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_ADORE_LIFE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Savages",
        "title": "Adore Life — LP",
        "format": "Vinyl",
        "price": 19.53,
        "currency": "USD",
        "url": "https://matadorrecords.com/products/adore-life",
        "cover_image_url": "https://cdn.shopify.com/savages_-adore_life.jpg",
    }]


@respx.mock
async def test_only_the_vinyl_variants_of_a_product_yield(crawler):
    # The CD is skipped by the format gate, the two colour pressings by the
    # availability filter; the format gate reads the variant title with or
    # without a " - " separator.
    _mock_pages(_ANTICS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Antics — Black Vinyl LP"]


def test_music_type_gate():
    # Enumerated, so that a type the store might add for non-music stays out
    # by default; the blank type is the store's bundles.
    for ptype in ["Album", "EP", "Single", "album", " EP ", "SINGLE"]:
        assert Crawler._items({**_ADORE_LIFE_PRODUCT, "product_type": ptype}), ptype
    for ptype in ["Merch", "", "  ", "Bundle", "Poster", "Albums", "Album - Vinyl", None]:
        assert Crawler._items({**_ADORE_LIFE_PRODUCT, "product_type": ptype}) == [], ptype


@pytest.mark.parametrize("descriptor", [
    "LP", "Dbl LP", "dbl LP", "Dbl Lp", "2X LP", "2XLP", "2xLP", "3LP", "4xLP", "1.5 LP",
    "120 gram 1.5XLP", "120g Black LP", "Black Vinyl LP", "Black vinyl LP",
    "Standard Black Vinyl 12\"", "12\" EP", "12\" Ep", "7\"", "7\" Single", "Dbl 7\"",
    "7\" Boxset", "3x12\" Boxset", "Subscription 3x12\" Boxset", "Vinyl Boxset",
    "Picture Disc LP", "Blue Vinyl", "Anniversary Red Vinyl", "Opaque White Vinyl",
    "EP - 12\"", "LP + 7\"", "LP + Signed Print", "Black Vinyl LP + bumper sticker",
    "Color Vinyl + Keychain/Bottle Opener", "LP + POSTER MIXED BUNDLE",
    "Deluxe Black Vinyl 2X LP + Bonus CD", "White Vinyl Dbl LP (VINYL RECORD)",
    "2 LPs + CD", "10 inch", "10″", "Deluxe Pop-Up LP",
])
def test_format_gate_admits_records(descriptor):
    assert Crawler._is_vinyl(descriptor), descriptor


@pytest.mark.parametrize("descriptor", [
    "CD", "CDEP", "CD EP", "2X CD", "2XCD", "Dbl CD", "Dbl CD (COMPACT DISC)", "4CD Boxset",
    "Cassette", "CS Album", "DMD Album", "DVD", "Default Title", "", "Black Small",
    "Size XL", "T-shirt - Black Small", "12\" x 12\" Poster", "12\" print", "Help",
    "Alps", "Tulips", "Slipmat 12\"", "Deluxe Edition",
])
def test_format_gate_rejects_everything_else(descriptor):
    # Positive rather than negative, because the walk covers the whole store:
    # an unrecognised descriptor is a CD until it says otherwise. The merch
    # cases pin the check order -- a dimension is not a format claim.
    assert not Crawler._is_vinyl(descriptor), descriptor


@pytest.mark.parametrize("product_title,variant_title,expected", [
    ("Adore Life", "Adore Life - LP", "LP"),
    ("Antics", "Antics Black Vinyl LP", "Black Vinyl LP"),
    ("Antics", "Antics CD", "CD"),
    ("Alien Lanes", "Alien Lanes - Color Vinyl LP - NO BOTTLE OPENER", "Color Vinyl LP - NO BOTTLE OPENER"),
    ("Perfecth", "Perfecth - Deluxe  LP", "Deluxe LP"),
    ("Electric Version (20th Anniversary Edition)", "Electric Version LP", "LP"),
    ("Valentine (Demos)", "Valentine Demos - 12\" EP", "12\" EP"),
    ("Set and Setting (25th Anniversary Edition)", "Set and Setting 25th Anniversary Edition - LP", "LP"),
    ("Signals, Calls & Marches (The Standard Edition)", "Signals, Calls &amp; Marches THE STANDARD EDITION LP", "LP"),
    ("The Greatest: Slipcase Edition", "The Greatest 120 gram LP", "120 gram LP"),
    ("The Greatest: Slipcase Edition", "The Greatest: Slipcase Edition CD", "CD"),
    ("If You're Feeling Sinister", "If You’re Feeling Sinister - Black Vinyl LP", "Black Vinyl LP"),
    ("Matador Singles '08", "Matador Singles ‘08 CD", "CD"),
    ("Wowee Zowee", "Wowee Zowee - 120 gram 1.5XLP", "120 gram 1.5XLP"),
    ("Mountains", "Mountains 20th Anniversary Expanded Edition - Dbl LP", "20th Anniversary Expanded Edition - Dbl LP"),
    ("6 Feet Beneath The Moon", "Six Feet Beneath The Moon - Dbl LP", "Dbl LP"),
    ("Body/Head - Coming Apart", "Coming Apart - Dbl LP", "Dbl LP"),
    ("Valentine", "Snail Mail Valentine - Pink Glass LP", "Pink Glass LP"),
    ("I'm A Lazy Son...But I'm The Only Son - 12\" EP", "I'm A Lazy Son...But I'm The Only Son - 12\" EP", "12\" EP"),
    ("Go", "Gone Glimmering LP", "Gone Glimmering LP"),
    ("Go", "Go - LP", "LP"),
    ("Antics", "Default Title", "Default Title"),
    ("", "Adore Life - LP", "LP"),
])
def test_descriptor_derivation(product_title, variant_title, expected):
    # Every shape is captured from the live catalog except "Go", which pins
    # the word boundary: an album name must not claim a variant that merely
    # starts with the same letters.
    assert Crawler._descriptor(product_title, variant_title) == expected


@respx.mock
async def test_descriptor_is_appended_on_a_single_variant_product(crawler):
    # Always appended, not only when the product has siblings: the descriptor
    # is the pressing, so it is part of the row's identity, and that identity
    # must not depend on whether a CD sibling happens to be listed.
    _mock_pages(_VALENTINE_DEMOS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Valentine (Demos) — 12\" EP"]


@respx.mock
async def test_a_title_that_already_ends_in_the_descriptor_names_the_format_once(crawler):
    # Altered: the captured product whose title already carries " - 12\" EP"
    # flipped available. The copy inside the title goes and the appended one
    # stands, so the row is not "... - 12\" EP — 12\" EP".
    _mock_pages(_lp_only(_LAZY_SON_PRODUCT, available=True))
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Lower", "I'm A Lazy Son...But I'm The Only Son — 12\" EP")]


@pytest.mark.parametrize("album,descriptor,expected", [
    ("I'm A Lazy Son...But I'm The Only Son - 12\" EP", "12\" EP", "I'm A Lazy Son...But I'm The Only Son"),
    ("Album - Black Vinyl  LP", "Black Vinyl LP", "Album"),
    ("Album – lp", "LP", "Album"),
    ("Album - LP", "Dbl LP", "Album - LP"),
    ("Album LP", "LP", "Album LP"),
    ("Black Vinyl", "Black Vinyl", "Black Vinyl"),
    ("LP", "LP", "LP"),
    ("Album - LP - Remastered", "LP", "Album - LP - Remastered"),
    ("Album", "", "Album"),
])
def test_trailing_descriptor_strip(album, descriptor, expected):
    # Only the dash-separated terminal form is stripped, so an album that
    # merely ends in the same words, or is nothing but them, is left alone.
    assert Crawler._without_trailing_descriptor(album, descriptor) == expected


@respx.mock
async def test_delisting_a_cd_sibling_does_not_change_the_vinyl_row_identity(crawler):
    # The failure the always-append rule prevents: keyed on the variant
    # count, this row would be "Adore Life" with the CD listed and
    # "Adore Life — LP" without it, orphaning everything keyed on the first.
    _mock_pages(_ADORE_LIFE_PRODUCT, _lp_only({**_ADORE_LIFE_PRODUCT, "handle": "adore-life-2"}))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Adore Life — LP", "Adore Life — LP"]


@respx.mock
async def test_artist_comes_from_tags_when_vendor_is_the_label(crawler):
    _mock_pages(_77_81_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [("Gang of Four", "77-81 — Vinyl Boxset")]


@pytest.mark.parametrize("vendor", ["Matador Records", "MatadorRecordsProd", "matador records", " MATADORRECORDSPROD "])
def test_every_spelling_of_the_label_vendor_defers_to_tags(vendor):
    items = Crawler._items({**_77_81_PRODUCT, "vendor": vendor})
    assert [i["artist"] for i in items] == ["Gang of Four"], vendor


@respx.mock
async def test_a_split_is_credited_to_its_first_artist_tag(crawler):
    # Altered: the captured split flipped available. The catalog keeps a
    # release's primary artist alone and the Track tab's library match is an
    # exact artist equality, so a joined "Jay Reatard / Sonic Youth" could
    # never match; the first credit, in the store's alphabetical tag order,
    # is the row's artist.
    _mock_pages(_lp_only(_SPLIT_SINGLE_PRODUCT, available=True))
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Jay Reatard", "Hang Them All / No Garage — 7\"")]


def test_housekeeping_tags_are_never_read_as_an_artist():
    product = {**_77_81_PRODUCT, "tags": ["Migrated", "PREORDER", "sale", "checkbox", "Matador Merch", "Gang of Four", "", None]}
    assert [i["artist"] for i in Crawler._items(product)] == ["Gang of Four"]


def test_artist_tag_does_not_override_a_real_vendor():
    # Tags on a real-vendor product carry other credits ("Stephen Malkmus &
    # The Jicks" on a Pavement product); the vendor wins.
    product = {**_ADORE_LIFE_PRODUCT, "tags": ["migrated", "Someone Else"]}
    assert [i["artist"] for i in Crawler._items(product)] == ["Savages"]


@respx.mock
async def test_label_vendor_without_an_artist_tag_is_skipped(crawler):
    # Altered: artist tag removed. Nothing left in the payload names the act,
    # so the product is skipped rather than credited to the label -- a row
    # credited to "Matador Records" can never match a Discogs release.
    _mock_pages({**_77_81_PRODUCT, "tags": ["migrated"]}, _ADORE_LIFE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Savages"]


@respx.mock
async def test_blank_vendor_product_is_skipped(crawler):
    # Altered: vendor blanked, no tags; no live product lacks a vendor.
    _mock_pages({**_ADORE_LIFE_PRODUCT, "vendor": "  ", "tags": []}, _ANTICS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Interpol"]


@respx.mock
async def test_vendor_dash_prefix_is_stripped(crawler):
    _mock_pages(_COMING_APART_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Coming Apart — Dbl LP"]


@respx.mock
async def test_self_titled_album_is_not_stripped(crawler):
    # Captured shape: "Algiers" by Algiers, "Interpol" by Interpol. The name
    # is the whole title, with no separator, so nothing is removed.
    product = {**_ADORE_LIFE_PRODUCT, "vendor": "Algiers", "title": "Algiers",
               "variants": [{"id": 1, "title": "Algiers - LP", "price": "18.68", "available": True}]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Algiers — LP"]


@respx.mock
async def test_preorder_tag_appends_suffix_before_the_descriptor(crawler):
    _mock_pages(_PERFECTH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["title"] for i in items} == {"Perfecth (Pre-Order) — Deluxe LP",
                                           "Perfecth (Pre-Order) — Standard LP"}


@respx.mock
async def test_preorder_tag_matching_is_case_insensitive(crawler):
    # Altered: tag re-cased. has_tag normalises, so the store re-casing its
    # own tag must not silently stop marking pre-orders.
    _mock_pages({**_PERFECTH_PRODUCT, "tags": ["PreOrder"]})
    items = [item async for item in crawler.crawl_catalog()]
    assert all(" (Pre-Order) — " in i["title"] for i in items)


@respx.mock
async def test_no_preorder_availability_bypass(crawler):
    # Altered: the captured pre-order flipped unavailable. Every live
    # pre-order reports available=True, so an unavailable one is gone
    # allocation -- pinned so reintroducing napalmrecords.py's bypass would
    # have to be deliberate.
    _mock_pages(_lp_only(_PERFECTH_PRODUCT, available=False))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_merch_product_is_skipped(crawler):
    _mock_pages(_MERCH_PRODUCT, _ADORE_LIFE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Savages"]


@respx.mock
async def test_untyped_bundle_is_skipped(crawler):
    # The bundle's variant titles are full of LPs, so the type gate is what
    # keeps it out, not the format gate.
    assert all(Crawler._is_vinyl(v["title"]) for v in _BUNDLE_PRODUCT["variants"])
    _mock_pages(_BUNDLE_PRODUCT, _ADORE_LIFE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Savages"]


@respx.mock
async def test_music_product_with_no_record_yields_nothing(crawler):
    _mock_pages(_DVD_PRODUCT, _ADORE_LIFE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Savages"]


@respx.mock
async def test_sold_out_product_is_skipped(crawler):
    _mock_pages(_LAZY_SON_PRODUCT, _ADORE_LIFE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Savages"]


@respx.mock
async def test_junk_variant_entry_is_ignored(crawler):
    # Non-mapping entries are dropped before anything reads them, so one is
    # an ordinary skipped row rather than an AttributeError mid-walk -- and
    # since the descriptor is always appended, it cannot change the healthy
    # row's identity either.
    product = {**_ADORE_LIFE_PRODUCT, "variants": _ADORE_LIFE_PRODUCT["variants"] + ["not-a-variant", 7]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Adore Life — LP"]


@pytest.mark.parametrize("raw", ["n/a", "", None, [], {}, float("nan"),
                                 float("inf"), "0", "-1", 0, -5, True, False])
def test_unusable_price_yields_none(raw):
    # Altered: price corrupted. bool and nan are the two a plain
    # float()-with-fallback lets through -- True would price a record at 1,
    # and nan is truthy, so it would reach the stock row and break JSON
    # serialisation downstream.
    items = Crawler._items(_lp_only(_ADORE_LIFE_PRODUCT, price=raw))
    assert items[0]["price"] is None, raw


@pytest.mark.parametrize("raw,expected", [("19.53", 19.53), (19.53, 19.53),
                                          ("148.73", 148.73), ("6.78", 6.78)])
def test_usable_price_is_parsed(raw, expected):
    assert Crawler._items(_lp_only(_ADORE_LIFE_PRODUCT, price=raw))[0]["price"] == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    # Altered: price key removed, alongside a healthy priced product. An
    # isolated null is tolerated; a catalog with no price anywhere raises.
    variant = {k: v for k, v in _ADORE_LIFE_PRODUCT["variants"][0].items() if k != "price"}
    _mock_pages({**_ADORE_LIFE_PRODUCT, "variants": [variant]}, _ANTICS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 21.23]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda v: {k: x for k, x in v.items() if k != "price"}, id="price-key-gone"),
    pytest.param(lambda v: {**v, "price": None}, id="price-null"),
    pytest.param(lambda v: {**v, "price": "n/a"}, id="price-unparseable"),
    pytest.param(lambda v: {**v, "price": "0"}, id="price-zero"),
    pytest.param(lambda v: {**v, "amount": v["price"], "price": None}, id="price-renamed"),
])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    # Rows without the emptiness: `_price` answers None for a value it
    # cannot use, so a price field removed or retyped store-wide re-lists the
    # whole catalog with no prices, which is worse than the snapshot it
    # would replace.
    products = [
        {**_ADORE_LIFE_PRODUCT, "handle": f"unpriced-{i}", "variants": [mutate(_ADORE_LIFE_PRODUCT["variants"][0])]}
        for i in range(2)
    ]
    _mock_pages(*products)
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    unpriced = [{**_ADORE_LIFE_PRODUCT, "handle": f"unpriced-{i}",
                 "variants": [{**_ADORE_LIFE_PRODUCT["variants"][0], "price": "n/a"}]}
                for i in range(5)]
    _mock_pages(_ANTICS_PRODUCT, *unpriced)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 6
    assert [i["price"] for i in items].count(None) == 5


@respx.mock
async def test_an_empty_catalog_does_not_trip_the_price_guard(crawler):
    _mock_pages(_LAZY_SON_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    # Captured: the LP variant's image differs from the product's own.
    _mock_pages(_ADORE_LIFE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/savages_-adore_life.jpg"


@respx.mock
async def test_cover_falls_back_to_product_image(crawler):
    # Captured: no variant image on the pre-order.
    _mock_pages(_PERFECTH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["cover_image_url"] for i in items} == {"https://cdn.shopify.com/perfecth_pack.jpg"}


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    # Altered: images emptied; every live music product has at least one.
    _mock_pages(_lp_only({**_PERFECTH_PRODUCT, "images": []}))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_without_a_music_type_raises(crawler):
    # Altered: a catalog of merch and bundles only. Completing empty would
    # have replace_stock_items() delete the previous snapshot.
    _mock_pages(_MERCH_PRODUCT, _BUNDLE_PRODUCT)
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_whose_music_products_resolve_no_artist_raises(crawler):
    # Altered: every music product vendored to the label with its artist tag
    # gone -- artist-source drift.
    _mock_pages({**_77_81_PRODUCT, "tags": ["migrated"]}, {**_ADORE_LIFE_PRODUCT, "vendor": "", "tags": []})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_vendor_on_a_non_music_product_does_not_satisfy_the_artist_guard(crawler):
    # The merch has a real artist as vendor; the one music product has none.
    # Counting vendors across every product would let the merch vouch for
    # music that has lost its artist source.
    _mock_pages(_MERCH_PRODUCT, {**_77_81_PRODUCT, "tags": ["migrated"]})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_whose_variant_titles_no_longer_name_a_record_raises(crawler):
    # Altered: the format gone from every variant title. The format is read
    # out of free text, so this is the guard that notices the store moving
    # it somewhere else -- every product would yield nothing while the type
    # and artist tallies stayed non-zero.
    product = {**_ADORE_LIFE_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "19.53", "available": True},
        {"id": 2, "title": "Clear", "price": "21.23", "available": True},
    ]}
    _mock_pages(product, _DVD_PRODUCT)
    with pytest.raises(RuntimeError, match="format-descriptor drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_record_in_a_bundle_does_not_satisfy_the_descriptor_guard(crawler):
    # The bundle's variants name LPs but the bundle can never yield, so it
    # must not vouch for a catalog whose music products have lost theirs.
    _mock_pages(_BUNDLE_PRODUCT, _DVD_PRODUCT)
    with pytest.raises(RuntimeError, match="format-descriptor drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: {k: v for k, v in p.items() if k != "variants"}, id="variants-key-gone"),
    pytest.param(lambda p: {**p, "variants": None}, id="variants-null"),
    pytest.param(lambda p: {**p, "variants": []}, id="variants-empty"),
    pytest.param(lambda p: {**p, "variants": ["not-a-dict"]}, id="variant-not-a-mapping"),
])
async def test_catalog_without_variants_raises(crawler, mutate):
    # The format lives on the variant, so a catalog with no readable
    # variants is one with no readable format -- named as descriptor drift
    # rather than stock drift, and a raise either way.
    _mock_pages(mutate(_ADORE_LIFE_PRODUCT))
    with pytest.raises(RuntimeError, match="format-descriptor drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda v: {k: x for k, x in v.items() if k != "available"}, id="available-key-gone"),
    pytest.param(lambda v: {**{k: x for k, x in v.items() if k != "available"}, "stock_status": "in_stock"},
                 id="available-renamed"),
    pytest.param(lambda v: {**v, "available": "false"}, id="available-string-false"),
    pytest.param(lambda v: {**v, "available": "true"}, id="available-string-true"),
    pytest.param(lambda v: {**v, "available": ""}, id="available-empty-string"),
    pytest.param(lambda v: {**v, "available": 0}, id="available-int-0"),
    pytest.param(lambda v: {**v, "available": 1}, id="available-int-1"),
    pytest.param(lambda v: {**v, "available": None}, id="available-null"),
])
async def test_catalog_without_a_readable_availability_flag_raises(crawler, mutate):
    # Altered: the field the availability filter reads, removed, renamed or
    # retyped on every record. Without this guard every product yields
    # nothing while every tally above it stays non-zero, and the walk
    # completes "successfully" empty. The string "false" is why this demands
    # the type rather than the key's presence: it is truthy, so a filter
    # reading truthiness would publish a sold-out record as in stock.
    _mock_pages({**_ADORE_LIFE_PRODUCT, "variants": [mutate(_ADORE_LIFE_PRODUCT["variants"][0])]})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("raw", ["false", "true", "", "0", 1, 0, None])
async def test_a_non_boolean_flag_is_never_emitted_as_in_stock(crawler, raw):
    # No guard is involved: the healthy row makes `yielded` non-zero, so the
    # outcome guard is skipped, and this is the per-variant filter rejecting
    # the malformed record on its own. Only the literal True admits one.
    _mock_pages(_ANTICS_PRODUCT, _lp_only(_ADORE_LIFE_PRODUCT, available=raw))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Interpol"], raw


@respx.mock
async def test_a_malformed_variant_does_not_hide_its_healthy_sibling(crawler):
    product = {**_ADORE_LIFE_PRODUCT, "variants": [
        {"id": 1, "title": "Adore Life - Black LP", "price": "19.53", "available": True},
        {"id": 2, "title": "Adore Life - Clear LP", "price": "21.23", "available": "false"},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Adore Life — Black LP"]


@respx.mock
async def test_one_malformed_product_does_not_trip_the_stock_guard(crawler):
    _mock_pages(_lp_only(_ADORE_LIFE_PRODUCT, available="false"), _ANTICS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Interpol"]


@respx.mock
async def test_one_readable_sold_out_product_cannot_vouch_for_an_unreadable_catalog(crawler):
    # The case that defeats an "at least one readable" test, and the reason
    # the guard counts UNREADABLE products instead: one genuinely sold-out
    # record must not vouch for a catalog that has gone unreadable behind it.
    unreadable = [_lp_only({**_ADORE_LIFE_PRODUCT, "handle": f"unreadable-{i}"}, available="false")
                  for i in range(3)]
    _mock_pages(_LAZY_SON_PRODUCT, *unreadable)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_sold_out_pressing_does_not_vouch_for_a_malformed_sibling(crawler):
    # every(), not any(): the black pressing is a readable False and the
    # clear one carries the string "false", so the product yields nothing
    # while an any() test would count it readable as it does so.
    product = {**_ADORE_LIFE_PRODUCT, "variants": [
        {"id": 1, "title": "Adore Life - Black LP", "price": "19.53", "available": False},
        {"id": 2, "title": "Adore Life - Clear LP", "price": "21.23", "available": "false"},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_cd_sibling_with_a_malformed_flag_does_not_make_the_record_unreadable(crawler):
    # Readability is judged over the vinyl variants only, because only they
    # could have yielded: a sold-out LP beside a CD whose flag is junk is a
    # sold-out LP, and the walk completes empty as the truth.
    product = {**_ADORE_LIFE_PRODUCT, "variants": [
        {**_ADORE_LIFE_PRODUCT["variants"][0], "available": False},
        {**_ADORE_LIFE_PRODUCT["variants"][1], "available": "false"},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_a_readable_cd_sibling_does_not_vouch_for_an_unreadable_record(crawler):
    # The other direction of the same scoping: the CD's clean flag says
    # nothing about whether the LP's emptiness can be trusted.
    product = {**_ADORE_LIFE_PRODUCT, "variants": [
        {**_ADORE_LIFE_PRODUCT["variants"][0], "available": "false"},
        {**_ADORE_LIFE_PRODUCT["variants"][1], "available": False},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_fully_readable_sold_out_product_completes_empty(crawler):
    product = {**_ADORE_LIFE_PRODUCT, "variants": [
        {"id": 1, "title": "Adore Life - Black LP", "price": "19.53", "available": False},
        {"id": 2, "title": "Adore Life - Clear LP", "price": "21.23", "available": False},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_an_unreadable_product_among_yielded_rows_does_not_raise(crawler):
    # An unreadable product is an ordinary skipped row while the walk is
    # still producing rows; only an empty result makes it mean the emptiness
    # cannot be trusted.
    _mock_pages(_ANTICS_PRODUCT, _lp_only({**_ADORE_LIFE_PRODUCT, "handle": "unreadable"}, available="false"))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Interpol"]


@respx.mock
async def test_a_cleanly_sold_out_catalog_completes_empty(crawler):
    # The one case where emptiness is the truth: every record readable and
    # every one of them out of stock. Both captured products are sold out.
    _mock_pages(_LAZY_SON_PRODUCT, _SPLIT_SINGLE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_two_products_cannot_each_satisfy_half_of_the_yield_guards(crawler):
    # A row needs the music type, an artist, a vinyl variant and a readable
    # flag on ONE product. The first has an artist but no readable flag; the
    # second a readable flag but no artist. Tallied independently both guards
    # pass and the walk completes empty -- so the tallies are nested.
    artist_no_flag = _lp_only({**_ADORE_LIFE_PRODUCT, "handle": "artist-no-flag"}, available="false")
    flag_no_artist = {**_77_81_PRODUCT, "handle": "flag-no-artist", "tags": ["migrated"]}
    _mock_pages(artist_no_flag, flag_no_artist)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_flag_on_a_non_music_product_does_not_satisfy_the_stock_guard(crawler):
    # The merch is readable and the record is not; the merch could never
    # have yielded, so it must not vouch for the record's emptiness.
    _mock_pages(_MERCH_PRODUCT, _lp_only(_ADORE_LIFE_PRODUCT, available="false"))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_ADORE_LIFE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_ANTICS_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Savages", "Interpol"]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Matador Records"
    assert Crawler.base_url == "https://matadorrecords.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary
