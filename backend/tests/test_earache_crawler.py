import httpx
import respx
import pytest
from crawlers.earache import Crawler

_PRODUCTS_URL = "https://earache.com/collections/vinyl/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-07, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape -- `vendor` is the label's own name,
# the artist is in the title before the quoted album, and the sole variant is
# Shopify's placeholder.
_ABBATH_PRODUCT = {
    "title": 'Abbath "Dread Reaver" Gatefold Silver Vinyl',
    "vendor": "Earache Records Ltd",
    "handle": "abbath-dread-reaver-gatefold-silver-vinyl",
    "product_type": "",
    "tags": ["All Vinyl. All Vinyl: Other Vinyl", "Artists A-Z. Artists A-Z: Abbath"],
    "images": [{"src": "https://cdn.shopify.com/AbbathDreadReaverSilverLP.jpg"}],
    "variants": [
        {"id": 48162384838969, "title": "Default Title", "price": "24.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: the other `vendor` spelling, and an album closed with an
# apostrophe rather than a matching double quote.
_COC_PRODUCT = {
    "title": 'Corrosion Of Conformity "America\'s Volume Dealer\' Black / White Swirl Vinyl',
    "vendor": "vendor-unknown",
    "handle": "corrosion-of-conformity-americas-volume-dealer-black-white-splattered-vinyl-pre-order",
    "product_type": "2026-05-15P",
    "tags": ["Artists A-Z. Artists A-Z: Corrosion Of Conformity"],
    "images": [{"src": "https://cdn.shopify.com/CorrosionSwirl.webp"}],
    "variants": [
        {"id": 56205625360763, "title": "Default Title", "price": "27.49",
         "available": True, "featured_image": None},
    ],
}

# Captured: a "Choice of Colour" product -- the colour lives only in the
# variants, one of which is sold out, and every variant shares the product's
# own image.
_TRIGGERED_PRODUCT = {
    "title": 'Massive Wagons "TRIGGERED!" Choice of Colour Vinyl w/ 12 Page Booklet',
    "vendor": "vendor-unknown",
    "handle": "massive-wagons-triggered-choice-of-colour-vinyl-w-12-page-booklet-download",
    "product_type": "Artists A-Z",
    # The artist tags are alphabetical and lead with an artist who is not the
    # credit; the crawler never reads them.
    "tags": ["Artists A-Z. Artists A-Z: Dub War", "Artists A-Z. Artists A-Z: Massive Wagons"],
    "images": [{"src": "https://cdn.shopify.com/MassiveWagonsTriggered.jpg"}],
    "variants": [
        {"id": 45238100132153, "title": "Orange", "price": "24.99", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/Triggered_Orange.jpg"}},
        {"id": 45238100164921, "title": "Green", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/Triggered_Green.jpg"}},
        {"id": 45238100197689, "title": "Pink", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/Triggered_Pink.jpg"}},
    ],
}

# Captured: a two-colour product with no variant images of its own.
_CULT_OF_LUNA_PRODUCT = {
    "title": 'Cult Of Luna "Cult Of Luna" Gatefold 2x12" Colour Vinyl',
    "vendor": "vendor-unknown",
    "handle": "cult-of-luna-cult-of-luna-gatefold-2x12-colour-vinyl",
    "product_type": "Artists A-Z",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/CultOfLunaCultOfLunaLP.jpg"}],
    "variants": [
        {"id": 45236022903097, "title": "White", "price": "24.99", "available": True, "featured_image": None},
        {"id": 45236022935865, "title": "Black", "price": "24.99", "available": True, "featured_image": None},
    ],
}

# Captured: a record whose descriptor names no format word at all -- only
# "Test Pressing" keeps it in.
_TEST_PRESSING_PRODUCT = {
    "title": 'Green Druid "At The Maw Of Ruin" REJECTED Test Pressing (Side A/B only)',
    "vendor": "vendor-unknown",
    "handle": "green-druid-at-the-maw-of-ruin-rejected-test-pressing-side-a-b-only",
    "product_type": "Artists A-Z",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/TestPressing.jpg"}],
    "variants": [
        {"id": 47578038763833, "title": "Default Title", "price": "40.00",
         "available": True, "featured_image": None},
    ],
}

# Captured: the one live product whose descriptor names neither a record nor
# another medium. It is a vinyl box, and the default admits it.
_BLACK_ALBUM_PRODUCT = {
    "title": 'Metallica "Metallica (The Black Album)\' Remastered Deluxe Box Set',
    "vendor": "vendor-unknown",
    "handle": "metallica-metallica-the-black-album-remastered-deluxe-box-set",
    "product_type": "Artists A-Z",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/MetallicaBox.jpg"}],
    "variants": [
        {"id": 55965161521531, "title": "Default Title", "price": "220.00",
         "available": True, "featured_image": None},
    ],
}

# Captured: a CD shelved in the vinyl collection.
_CD_PRODUCT = {
    "title": 'Thorns "Thorns" CD',
    "vendor": "vendor-unknown",
    "handle": "thorns-thorns-cd",
    "product_type": "All Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/ThornsThorns.jpg"}],
    "variants": [
        {"id": 45235472924985, "title": "Default Title", "price": "10.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a cassette box whose ALBUM name would match the CD word if the
# gate read the whole title instead of the descriptor.
_CASSETTE_BOX_PRODUCT = {
    "title": 'Morbid Angel "ABCD" Cassette Tape Collector\'s Box',
    "vendor": "vendor-unknown",
    "handle": "morbid-angel-abcd-cassette-tape-collectors-box-downloads",
    "product_type": "Artists A-Z",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/MorbidANgelTapeBox.jpg"}],
    "variants": [
        {"id": 45238205579577, "title": "Default Title", "price": "55.00",
         "available": True, "featured_image": None},
    ],
}

# Captured: a sold-out pre-order -- a closed early-bird allocation, which the
# store's own product page renders as "Sold Out".
_SOLD_OUT_PREORDER_PRODUCT = {
    "title": 'The Bites "The Bites" SIGNED Silver / Red Merge Vinyl w/ Alt Mirror Sleeve - PRE-ORDER',
    "vendor": "vendor-unknown",
    "handle": "the-bites-the-bites-signed-silver-red-merge-vinyl-w-alt-mirror-sleeve-pre-order-early-bird-price",
    "product_type": "2026-09-18P",
    "tags": ["Pre-Orders"],
    "images": [{"src": "https://cdn.shopify.com/TheBitesTB_MergeSignedMirror.jpg"}],
    "variants": [
        {"id": 56276958118267, "title": "Default Title", "price": "22.00",
         "available": False, "featured_image": None},
    ],
}

# Captured: an in-stock pre-order, whose ` - PRE-ORDER` suffix is part of the
# descriptor the store wrote and is kept verbatim.
_PREORDER_PRODUCT = {
    "title": 'Anthrax "Cursum Perficio" Gatefold 2x12" Magenta Vinyl - PRE-ORDER',
    "vendor": "vendor-unknown",
    "handle": "anthrax-cursum-perficio-gatefold-2x12-magenta-vinyl-pre-order",
    "product_type": "2026-04-24P",
    "tags": ["Pre-Orders"],
    "images": [{"src": "https://cdn.shopify.com/AnthraxCursumMagenta.jpg"}],
    "variants": [
        {"id": 56102019432763, "title": "Default Title", "price": "31.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a multi-record bundle.
_BUNDLE_PRODUCT = {
    "title": 'Threshold "Psychedelicatessen" & "Wounded Land" Colour Vinyl Bundle',
    "vendor": "vendor-unknown",
    "handle": "threshold-psychedelicatessen-wounded-land-colour-vinyl-bundle",
    "product_type": "Artists A-Z",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/ThresholdBundle.jpg"}],
    "variants": [
        {"id": 45238100000001, "title": "Default Title", "price": "44.99",
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


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_ABBATH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Abbath",
        "title": "Dread Reaver Gatefold Silver Vinyl",
        "format": "Vinyl",
        "price": 24.99,
        "currency": "GBP",
        "url": "https://earache.com/products/abbath-dread-reaver-gatefold-silver-vinyl",
        "cover_image_url": "https://cdn.shopify.com/AbbathDreadReaverSilverLP.jpg",
    }]


def test_plugin_identity():
    assert Crawler.site_name == "Earache Records"
    assert Crawler.base_url == "https://earache.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
    assert Crawler.genre_summary


@pytest.mark.parametrize("title,expected", [
    ('Abbath "Dread Reaver" Gatefold Silver Vinyl',
     ("Abbath", "Dread Reaver", "Gatefold Silver Vinyl")),
    # An inch marker in the descriptor is never mistaken for the album's
    # quotes: the artist group cannot contain one, so the album's opening
    # quote is always the title's first.
    ('40 Watt Sun "A Perfect Light" 3x12" Yellow Vinyl',
     ("40 Watt Sun", "A Perfect Light", '3x12" Yellow Vinyl')),
    ('A Perfect Circle "Thirteenth Step" 2x12" Vinyl',
     ("A Perfect Circle", "Thirteenth Step", '2x12" Vinyl')),
    # The store closes some albums with an apostrophe.
    ('Corrosion Of Conformity "America\'s Volume Dealer\' Black / White Swirl Vinyl',
     ("Corrosion Of Conformity", "America's Volume Dealer", "Black / White Swirl Vinyl")),
    ('Motorhead "Overnight Sensation\' Vinyl',
     ("Motorhead", "Overnight Sensation", "Vinyl")),
    # An apostrophe INSIDE an album does not close it: one only closes when
    # whitespace or the end of the title follows.
    ('AC/DC "\'74 Jailbreak" Vinyl', ("AC/DC", "'74 Jailbreak", "Vinyl")),
    ('David Bowie "Live In Santa Monica \'72" Vinyl',
     ("David Bowie", "Live In Santa Monica '72", "Vinyl")),
    ('Anathema "We\'re Here Because We\'re Here" Vinyl',
     ("Anathema", "We're Here Because We're Here", "Vinyl")),
    ('AC/DC "The Razor\'s Edge" Gold Vinyl', ("AC/DC", "The Razor's Edge", "Gold Vinyl")),
    # A closing quote glued straight onto the descriptor still closes.
    ('Sikth "Death Of A Dead Day"2x12"  Vinyl',
     ("Sikth", "Death Of A Dead Day", '2x12" Vinyl')),
    # A quoted colour name in the descriptor is left in it.
    ('Converge "Hum Of Hurt" \'Fist In The Gold\' Vinyl',
     ("Converge", "Hum Of Hurt", "'Fist In The Gold' Vinyl")),
    # Typographic quotes on either side.
    ('Zeal & Ardor “Greif” Black Vinyl', ("Zeal & Ardor", "Greif", "Black Vinyl")),
    # An album carrying a colon, and one that is the artist's own name.
    ('Abhorrence "Maggots: The Original Artyfacts" 3 x 7" Black Vinyl Box',
     ("Abhorrence", "Maggots: The Original Artyfacts", '3 x 7" Black Vinyl Box')),
    ('Category 7 "Category 7" \'Sable Smoke\' Vinyl',
     ("Category 7", "Category 7", "'Sable Smoke' Vinyl")),
    # Whitespace is collapsed, inside the title and around it.
    ('  Decapitated  "Nihility"  Merge Vinyl  (Ltd to 500 Copies)  ',
     ("Decapitated", "Nihility", "Merge Vinyl (Ltd to 500 Copies)")),
    # An album with no descriptor after it.
    ('Aborym "Shifting.Negative"', ("Aborym", "Shifting.Negative", "")),
    # Not of the form: no quoted album, an artist that is only a quote, an
    # empty album, nothing at all.
    ('Exhumed / Iron Reagan Split 12" EP', ("", "", "")),
    ('Dream Theater 10LP Clear Vinyl', ("", "", "")),
    ('"Dread Reaver" Gatefold Silver Vinyl', ("", "", "")),
    # A title that opens on the album leaves no artist, even when a later
    # quoted word could be read as one: the artist group cannot contain a
    # quote, so the album's opening quote is always the title's first.
    ('"Dread Reaver" Gatefold "Silver" Vinyl', ("", "", "")),
    ('Abbath "" Gatefold Silver Vinyl', ("", "", "")),
    ("", ("", "", "")),
    (None, ("", "", "")),
])
def test_title_parse(title, expected):
    assert Crawler._parse_title(title) == expected


@pytest.mark.parametrize("title", [
    'Dream Theater 10LP Clear Vinyl Bundle',
    'Threshold "Psychedelicatessen" & "Wounded Land" Colour Vinyl Bundle',
    'Morbid Angel "Entangled In Chaos" Triple Vinyl Bundle',
    'Prong Vinyl Bundle - "Prove You Wrong", "Cleansing", "Beg To Differ" & "Rude Awakening"',
    'In Flames Anniversary Bundle - "Siren Charms", "Colony" & "Lunar Strain" 180g Colour Vinyl',
    'Lucky Dip Sale - 3 LPs for £25',
    'Some Band "Some Album" vinyl bundles',
])
def test_bundles_and_mixed_lots_are_not_records(title):
    assert Crawler._parse_title(title) == ("", "", "")


@respx.mock
async def test_a_bundle_yields_nothing(crawler):
    _mock_pages(_BUNDLE_PRODUCT, _ABBATH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Abbath"]


@respx.mock
async def test_the_artist_never_comes_from_vendor_or_tags(crawler):
    # Altered: the title lost its quoted album while `vendor` and the
    # `Artists A-Z` tags still name an artist. Neither is a source: the tags
    # serialise alphabetically and lead with an artist who is not the credit
    # ("Dub War" before "Massive Wagons" on the captured product below), and
    # `vendor` is the label. A row credited to either would be wrong, so the
    # product is skipped.
    _mock_pages({**_TRIGGERED_PRODUCT, "title": "TRIGGERED! Choice of Colour Vinyl"}, _ABBATH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Abbath"]


@respx.mock
async def test_the_credit_comes_from_the_title_not_the_leading_artist_tag(crawler):
    _mock_pages(_TRIGGERED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["artist"] for i in items} == {"Massive Wagons"}


@respx.mock
async def test_the_row_title_leads_with_the_album_so_it_prefix_matches_a_library_title(crawler):
    # db._library_release_match_sql matches a stock row to a catalog release
    # on exact-or-prefix-with-space, so the descriptor has to follow the
    # album rather than wrap it in the quotes the store typed.
    _mock_pages(_ABBATH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"].lower().startswith("dread reaver ")


@pytest.mark.parametrize("descriptor", [
    "Gatefold Silver Vinyl", "Black Vinyl", "180g Vinyl", "Vinyl",
    '3x12" Yellow Vinyl', '2x12" Vinyl', '10" Vinyl', '3 x 7" Black Vinyl Box',
    "Shaped Picture Disc Vinyl", "TEST PRESSING Vinyl", "REJECTED Test Pressing (Side A/B only)",
    "2 LP / 2 CD / Blu Ray Box Set", "Deluxe Splatter Vinyl / CD / Cassette Tape Box Set",
    '2x12" Vinyl (inc CD)', "8 Vinyl LP Box Set", "Remastered Deluxe Box Set", "",
])
def test_format_gate_admits_records_and_undeclared_descriptors(descriptor):
    assert Crawler._is_vinyl(descriptor)


@pytest.mark.parametrize("descriptor", [
    "CD", "2 CD", "30th Anniversary 2 CD", "Cassette Tape Collector's Box",
    "Digipak CD", "Blu-Ray", "Blu Ray", "DVD", "Cassette",
])
def test_format_gate_rejects_other_media(descriptor):
    assert not Crawler._is_vinyl(descriptor)


@respx.mock
@pytest.mark.parametrize("product", [_CD_PRODUCT, _CASSETTE_BOX_PRODUCT])
async def test_non_vinyl_products_in_the_collection_yield_nothing(crawler, product):
    _mock_pages(product, _ABBATH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Abbath"]


@respx.mock
async def test_an_album_name_resembling_another_medium_does_not_decide_the_format(crawler):
    # Altered: the cassette box's descriptor now names a record. Its album is
    # "ABCD"; the gate reads the descriptor only, so the row is admitted.
    _mock_pages({**_CASSETTE_BOX_PRODUCT, "title": 'Morbid Angel "ABCD" 4x12" Black Vinyl Box'})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['ABCD 4x12" Black Vinyl Box']


@respx.mock
async def test_a_test_pressing_naming_no_format_word_is_admitted(crawler):
    _mock_pages(_TEST_PRESSING_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["At The Maw Of Ruin REJECTED Test Pressing (Side A/B only)"]


@respx.mock
async def test_a_descriptor_naming_neither_is_admitted(crawler):
    _mock_pages(_BLACK_ALBUM_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Metallica (The Black Album) Remastered Deluxe Box Set"]


@respx.mock
async def test_named_variants_are_appended_to_every_row(crawler):
    _mock_pages(_CULT_OF_LUNA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        'Cult Of Luna Gatefold 2x12" Colour Vinyl — White',
        'Cult Of Luna Gatefold 2x12" Colour Vinyl — Black',
    ]


@respx.mock
async def test_a_product_reduced_to_one_named_variant_still_carries_it(crawler):
    # Keyed on the variant count, the row would re-title -- and so re-key,
    # orphaning its listings, judgments and saves -- the day a sibling was
    # delisted.
    _mock_pages(_one_pressing(_CULT_OF_LUNA_PRODUCT, index=1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Cult Of Luna Gatefold 2x12" Colour Vinyl — Black']


@respx.mock
async def test_placeholder_variant_carries_the_title_alone(crawler):
    _mock_pages(_ABBATH_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dread Reaver Gatefold Silver Vinyl"]


@respx.mock
async def test_placeholder_matching_is_case_insensitive(crawler):
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, title="default title"))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dread Reaver Gatefold Silver Vinyl"]


@respx.mock
async def test_placeholder_on_a_multi_variant_product_is_skipped(crawler):
    # Altered: Shopify never issues the placeholder beside a real variant, so
    # here it is malformed data. Admitted with no descriptor it would share
    # the bare title, and so the item_key, with every sibling built the same
    # way; admitted with one it would put "Default Title" on a row.
    product = {**_CULT_OF_LUNA_PRODUCT, "variants": _CULT_OF_LUNA_PRODUCT["variants"] + [
        {"id": 3, "title": "Default Title", "price": "24.99", "available": True, "featured_image": None}]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        'Cult Of Luna Gatefold 2x12" Colour Vinyl — White',
        'Cult Of Luna Gatefold 2x12" Colour Vinyl — Black',
    ]


@respx.mock
async def test_blank_variant_title_is_skipped(crawler):
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, title="  "),
                {**_CULT_OF_LUNA_PRODUCT, "variants": [
                    {**_CULT_OF_LUNA_PRODUCT["variants"][0], "title": ""},
                    _CULT_OF_LUNA_PRODUCT["variants"][1]]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Cult Of Luna Gatefold 2x12" Colour Vinyl — Black']


@respx.mock
async def test_variant_title_whitespace_is_collapsed(crawler):
    _mock_pages(_one_pressing(_CULT_OF_LUNA_PRODUCT, index=1, title="  Black \n Splatter  "))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Cult Of Luna Gatefold 2x12" Colour Vinyl — Black Splatter']


@respx.mock
async def test_every_admitted_variant_yields_its_own_identity(crawler):
    _mock_pages(_TRIGGERED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len({(i["artist"], i["title"], i["url"]) for i in items}) == len(items) == 2


@respx.mock
async def test_sold_out_variant_is_skipped_beside_its_in_stock_siblings(crawler):
    _mock_pages(_TRIGGERED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "TRIGGERED! Choice of Colour Vinyl w/ 12 Page Booklet — Green",
        "TRIGGERED! Choice of Colour Vinyl w/ 12 Page Booklet — Pink",
    ]


@respx.mock
@pytest.mark.parametrize("available", [False, "false", "true", 1, None, "yes"])
async def test_only_the_literal_true_admits_a_variant(crawler, available):
    # Altered: "false" is a truthy string, so a falsiness test would publish
    # a sold-out record as in stock.
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, available=available), _COC_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Corrosion Of Conformity"]


@respx.mock
async def test_a_sold_out_preorder_is_skipped_and_no_marker_is_written(crawler):
    # A pre-order reporting False here is a closed early-bird allocation, so
    # there is no bypass; and the store's own ` - PRE-ORDER` suffix is part
    # of the descriptor, neither added nor removed -- compute_item_key hashes
    # the title, so a marker the crawler wrote would re-key the row when the
    # record shipped.
    _mock_pages(_SOLD_OUT_PREORDER_PRODUCT, _PREORDER_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Cursum Perficio Gatefold 2x12" Magenta Vinyl - PRE-ORDER']
    assert "(Pre-Order)" not in items[0]["title"]


@respx.mock
async def test_junk_variant_entries_are_ignored(crawler):
    # Dropped before anything reads them, so the placeholder rule sees the
    # mapping entries only: the placeholder is still the sole variant and
    # still carries the title alone.
    _mock_pages({**_ABBATH_PRODUCT, "variants": [None, "junk", 3] + _ABBATH_PRODUCT["variants"]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Dread Reaver Gatefold Silver Vinyl"]


@respx.mock
async def test_product_missing_its_identity_is_skipped(crawler):
    _mock_pages({**_ABBATH_PRODUCT, "handle": ""}, {**_COC_PRODUCT, "handle": None}, _PREORDER_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Anthrax"]


@respx.mock
@pytest.mark.parametrize("mutate", [
    lambda p: {**p, "handle": ""},
    lambda p: {**p, "handle": None},
    lambda p: {k: v for k, v in p.items() if k != "handle"},
])
async def test_catalog_without_handles_raises(crawler, mutate):
    _mock_pages(mutate(_ABBATH_PRODUCT), mutate(_COC_PRODUCT))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_less_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages({**_ABBATH_PRODUCT, "handle": ""}, _CULT_OF_LUNA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_a_sold_out_product_missing_its_handle_still_raises(crawler):
    # The identity tally is taken before the availability filter: a
    # handle-less product is drift whether or not it is in stock.
    _mock_pages(_one_pressing({**_ABBATH_PRODUCT, "handle": ""}, available=False))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_on_a_non_record_does_not_satisfy_the_identity_guard(crawler):
    _mock_pages(_CD_PRODUCT, {**_ABBATH_PRODUCT, "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("available", ["true", 1, None])
async def test_a_catalog_with_no_readable_availability_raises(crawler, available):
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, available=available),
                _one_pressing(_COC_PRODUCT, available=available))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_unreadable_product_among_real_rows_does_not_raise(crawler):
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, available="true"), _COC_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Corrosion Of Conformity"]


@respx.mock
async def test_a_genuinely_sold_out_shelf_is_a_legitimate_empty_result(crawler):
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, available=False),
                _one_pressing(_COC_PRODUCT, available=False))
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_one_unreadable_variant_does_not_vouch_for_its_readable_sibling(crawler):
    # every(), not any(): a product whose one pressing is a readable False
    # and whose other carries the string "true" yields nothing, and under
    # any() would vouch for an emptiness half its own doing.
    product = {**_CULT_OF_LUNA_PRODUCT, "variants": [
        {**_CULT_OF_LUNA_PRODUCT["variants"][0], "available": False},
        {**_CULT_OF_LUNA_PRODUCT["variants"][1], "available": "true"}]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "-5", "nan", "inf", True, False, 0, -1.5, float("nan")])
def test_unusable_price_yields_none(raw):
    assert Crawler._price({"price": raw}) is None


@pytest.mark.parametrize("raw,expected", [("24.99", 24.99), ("220.00", 220.0), (5.99, 5.99), (30, 30.0)])
def test_usable_price_is_parsed(raw, expected):
    assert Crawler._price({"price": raw}) == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    variant = {k: v for k, v in _ABBATH_PRODUCT["variants"][0].items() if k != "price"}
    _mock_pages({**_ABBATH_PRODUCT, "variants": [variant]}, _CULT_OF_LUNA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 24.99, 24.99]


@respx.mock
@pytest.mark.parametrize("mutate", [
    lambda v: {**v, "price": None},
    lambda v: {**v, "price": "free"},
    lambda v: {k: x for k, x in v.items() if k != "price"},
])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    _mock_pages({**_ABBATH_PRODUCT, "variants": [mutate(v) for v in _ABBATH_PRODUCT["variants"]]},
                {**_CULT_OF_LUNA_PRODUCT, "variants": [mutate(v) for v in _CULT_OF_LUNA_PRODUCT["variants"]]})
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, price=None), _COC_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 27.49]


@respx.mock
async def test_an_empty_catalog_does_not_trip_the_price_guard(crawler):
    _mock_pages(_one_pressing(_ABBATH_PRODUCT, available=False, price=None))
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    _mock_pages(_TRIGGERED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["cover_image_url"] for i in items] == [
        "https://cdn.shopify.com/Triggered_Green.jpg",
        "https://cdn.shopify.com/Triggered_Pink.jpg",
    ]


@respx.mock
async def test_cover_falls_back_to_product_image(crawler):
    _mock_pages(_CULT_OF_LUNA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["cover_image_url"] for i in items} == {"https://cdn.shopify.com/CultOfLunaCultOfLunaLP.jpg"}


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    _mock_pages({**_ABBATH_PRODUCT, "images": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["cover_image_url"] for i in items] == [None]


@respx.mock
async def test_url_is_built_from_the_handle_as_written(crawler):
    _mock_pages(_TEST_PRESSING_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["url"] for i in items] == [
        "https://earache.com/products/green-druid-at-the-maw-of-ruin-rejected-test-pressing-side-a-b-only"]


@respx.mock
async def test_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_with_no_parseable_title_raises(crawler):
    # Altered: the store dropped the quotes around every album. Completing
    # empty would have replace_stock_items() delete the previous snapshot.
    _mock_pages({**_ABBATH_PRODUCT, "title": "Abbath Dread Reaver Gatefold Silver Vinyl"},
                {**_COC_PRODUCT, "title": "Corrosion Of Conformity Black / White Swirl Vinyl"})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_catalog_of_only_other_media_does_not_raise(crawler):
    # Invented: the negative format gate has no positive signal to lose, so
    # a shelf that answered with CDs only is not drift -- it yields nothing
    # and says so, rather than raising on a guard that cannot exist here.
    _mock_pages(_CD_PRODUCT, _CASSETTE_BOX_PRODUCT)
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_pagination_walks_every_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_ABBATH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_COC_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Abbath", "Corrosion Of Conformity"]
