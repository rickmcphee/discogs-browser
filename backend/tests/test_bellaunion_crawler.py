import httpx
import respx
import pytest
from crawlers.bellaunion import Crawler

_PRODUCTS_URL = "https://bellaunion.com/collections/all/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-08, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape -- `Artist - Album`, `product_type`
# of `Vinyl`, a coloured pressing beside the CD, and `vendor` naming the
# label rather than anybody who made the record.
_ARCO_PRODUCT = {
    "title": "A.A. Williams - Arco",
    "vendor": "Bella Union",
    "handle": "a-a-williams-arco",
    "product_type": "Vinyl",
    "tags": ["A.A. Williams", "Arco", "Bella Union", "Green Vinyl", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/242802-1.jpg"}],
    "variants": [
        {"id": 49735188250953, "title": "Galaxy Teal Vinyl", "price": "22.99",
         "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/242802-teal.jpg"}},
        {"id": 49735188283721, "title": "CD", "price": "9.99",
         "available": False, "featured_image": None},
    ],
}

# Captured: the album name carries `(Signed Print)`, which the format gate
# must never see -- it reads the variant descriptor and nothing else.
_SIGNED_PRINT_PRODUCT = {
    "title": "A.A. Williams - As The Moon Rests (Signed Print)",
    "vendor": "Bella Union",
    "handle": "a-a-williams-as-the-moon-rests",
    "product_type": "Vinyl",
    "tags": ["A.A. Williams"],
    "images": [{"src": "https://cdn.shopify.com/312624-7-1.jpg"}],
    "variants": [
        {"id": 1, "title": "Double Gold Vinyl", "price": "31.99", "available": True,
         "featured_image": None},
        {"id": 2, "title": "Double Black & White Vinyl", "price": "31.99",
         "available": True, "featured_image": None},
        {"id": 3, "title": "CD", "price": "9.99", "available": True, "featured_image": None},
    ],
}

# Captured: a record whose sole variant is Shopify's placeholder. Nothing in
# the payload names a format; `product_type` is the only claim it makes.
_ALIEN_PRODUCT = {
    "title": 'Beach House - Alien / Lose Your Smile 7"',
    "vendor": "Bella Union",
    "handle": "beach-house-alien-lose-your-smile-7",
    "product_type": "Vinyl",
    "tags": ["Beach House", "Bella Union", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/beachhouse-alien.jpg"}],
    "variants": [
        {"id": 4, "title": "Default Title", "price": "9.99", "available": True,
         "featured_image": None},
    ],
}

# Captured: two vinyl box editions whose descriptors name no format at all,
# beside the CD. The default-admit branch is what keeps them.
_ONCE_TWICE_MELODY_PRODUCT = {
    "title": "Beach House - Once Twice Melody",
    "vendor": "Bella Union",
    "handle": "beach-house-once-twice-melody",
    "product_type": "Vinyl",
    "tags": ["Beach House"],
    "images": [{"src": "https://cdn.shopify.com/255020-1.jpg"}],
    "variants": [
        {"id": 5, "title": "Gold Edition", "price": "89.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/otm-gold.jpg"}},
        {"id": 6, "title": "Silver Edition", "price": "29.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/otm-silver.jpg"}},
        {"id": 7, "title": "CD", "price": "11.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/otm-cd.jpg"}},
    ],
}

# Captured: a record the store left untyped. Only a variant naming a record
# says it is one.
_HARP_PRODUCT = {
    "title": "Harp - Albion",
    "vendor": "Bella Union",
    "handle": "harp-albion",
    "product_type": "",
    "tags": ["Harp"],
    "images": [{"src": "https://cdn.shopify.com/413031-2.jpg"}],
    "variants": [
        {"id": 8, "title": "Vinyl", "price": "22.99", "available": False,
         "featured_image": None},
        {"id": 9, "title": "CD", "price": "9.99", "available": False,
         "featured_image": None},
    ],
}

# Captured: the store's cassette typo, on a product whose other variants are
# records.
_MAHASHMASHANA_PRODUCT = {
    "title": "Father John Misty - Mahashmashana",
    "vendor": "Bella Union",
    "handle": "father-john-misty-mahashmashana",
    "product_type": "Vinyl",
    "tags": ["cd", "Father John Misty", "Repress", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/fjm-mahashmashana.jpg"}],
    "variants": [
        {"id": 10, "title": "Double Yellow Vinyl", "price": "28.99", "available": True,
         "featured_image": None},
        {"id": 11, "title": "Double Black Vinyl", "price": "28.99", "available": True,
         "featured_image": None},
        {"id": 12, "title": "CD", "price": "7.99", "available": True, "featured_image": None},
        {"id": 13, "title": "Casseette", "price": "6.99", "available": True,
         "featured_image": None},
    ],
}

# Captured: a record sold both plain and as a shirt bundle at nearly twice
# the price, under one product title.
_SINGLES_LIVE_PRODUCT = {
    "title": "The Fall - Singles Live Vol.2",
    "vendor": "Bella Union",
    "handle": "the-fall-singles-live-vol-2",
    "product_type": "Vinyl",
    "tags": ["Bella Union", "cd", "The Fall", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/the-fall-vol2.jpg"}],
    "variants": [
        {"id": 14, "title": "Yellow/White Swirl Vinyl + T Shirt Bundle", "price": "39.99",
         "available": False, "featured_image": None},
        {"id": 15, "title": "Yellow/White Swirl Vinyl", "price": "23.99",
         "available": False, "featured_image": None},
        {"id": 16, "title": "Black Vinyl + T Shirt Bundle", "price": "39.99",
         "available": False, "featured_image": None},
        {"id": 17, "title": "Black Vinyl", "price": "23.99", "available": True,
         "featured_image": None},
        {"id": 18, "title": "CD", "price": "9.99", "available": True,
         "featured_image": None},
    ],
}

# Captured: the same bundle shape spelled with a slash, alongside a pressing
# carrying a signed insert that IS a record at a record's price.
_WOW_SCENARIO_PRODUCT = {
    "title": "The Wow! Scenario - Stand in the Star. A Verse and a Chorus",
    "vendor": "Bella Union",
    "handle": "the-wow-scenario",
    "product_type": "Vinyl",
    "tags": ["Bella Union", "cd", "James Acaster", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/wow-scenario.jpg"}],
    "variants": [
        {"id": 19, "title": "Yellow/Silver Marble Vinyl + Signed Insert", "price": "22.99",
         "available": True, "featured_image": None},
        {"id": 20, "title": 'Vinyl / "Tie" T Shirt Bundle + Signed Insert',
         "price": "41.99", "available": True, "featured_image": None},
        {"id": 21, "title": 'Vinyl / "Window" T Shirt Bundle + Signed Insert',
         "price": "41.99", "available": True, "featured_image": None},
        {"id": 22, "title": "CD", "price": "9.99", "available": True, "featured_image": None},
        {"id": 23, "title": 'CD / "Tie" T Shirt Bundle', "price": "28.99",
         "available": True, "featured_image": None},
    ],
}

# Captured: a pressing packaged with a patch, at the plain pressing's own
# price -- a record with an extra, not a bundle.
_WHITE_DENIM_PRODUCT = {
    "title": "White Denim - 13",
    "vendor": "Bella Union",
    "handle": "white-denim-13",
    "product_type": "Vinyl",
    "tags": ["Bella Union", "cd", "Vinyl", "White Denim"],
    "images": [{"src": "https://cdn.shopify.com/white-denim-13.jpg"}],
    "variants": [
        {"id": 24, "title": "Yellow Vinyl + Embroidered Patch", "price": "23.99",
         "available": False, "featured_image": None},
        {"id": 25, "title": "Yellow Vinyl", "price": "23.99", "available": True,
         "featured_image": None},
        {"id": 26, "title": "CD", "price": "9.99", "available": True,
         "featured_image": None},
    ],
}

# Captured: a product the store types `Vinyl` while stocking only the CD.
_PERADAM_PRODUCT = {
    "title": "Soundwalk Collective with Patti Smith - Peradam",
    "vendor": "Bella Union",
    "handle": "soundwalk-collective-with-patti-smith-peradam",
    "product_type": "Vinyl",
    "tags": ["Bella Union", "Patti Smith", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/192350-3.jpg"}],
    "variants": [
        {"id": 27, "title": "CD", "price": "9.99", "available": False,
         "featured_image": None},
    ],
}

# Captured: the store's merch, sized rather than pressed.
_TSHIRT_PRODUCT = {
    "title": 'The Fall "Fiery Jack" T Shirt',
    "vendor": "Bella Union",
    "handle": "the-fall-t-shirt",
    "product_type": "",
    "tags": ["Merch", "The Fall", "ZZZ"],
    "images": [{"src": "https://cdn.shopify.com/the-fall-shirt.png"}],
    "variants": [
        {"id": 28, "title": "S", "price": "22.99", "available": False, "featured_image": None},
        {"id": 29, "title": "M", "price": "22.99", "available": True, "featured_image": None},
    ],
}

# Captured: merch carrying the placeholder variant rather than sizes.
_CAP_PRODUCT = {
    "title": "Bella Union Katakana Logo Cap",
    "vendor": "Bella Union",
    "handle": "bella-union-katakana-logo-cap",
    "product_type": "",
    "tags": ["Merch", "ZZZ"],
    "images": [{"src": "https://cdn.shopify.com/katakana-cap.webp"}],
    "variants": [
        {"id": 30, "title": "Default Title", "price": "17.00", "available": True,
         "featured_image": None},
    ],
}

# Captured: one of the two live records the store did not title
# `Artist - Album` -- two spaces stand in for the separator. It is in stock
# and is skipped; see the design doc's "Two records the parse does not read".
_FOUR_CALENDAR_PRODUCT = {
    "title": "Cocteau Twins  Four-Calendar Café  (signed by Simon Raymonde)",
    "vendor": "4AD",
    "handle": "cocteau-twins-four-calendar-cafe-signed-by-simon-raymonde",
    "product_type": "Vinyl",
    "tags": ["Vinyl", "ZZZ"],
    "images": [{"src": "https://cdn.shopify.com/four-calendar-cafe.jpg"}],
    "variants": [
        {"id": 31, "title": "Default Title", "price": "26.99", "available": True,
         "featured_image": None},
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
    _mock_pages(_ARCO_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "A.A. Williams",
        "title": "Arco — Galaxy Teal Vinyl",
        "format": "Vinyl",
        "price": 22.99,
        "currency": "GBP",
        "url": "https://bellaunion.com/products/a-a-williams-arco",
        "cover_image_url": "https://cdn.shopify.com/242802-teal.jpg",
    }]


def test_plugin_identity():
    assert Crawler.site_name == "Bella Union"
    assert Crawler.base_url == "https://bellaunion.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary


@pytest.mark.parametrize("title,expected", [
    ("A.A. Williams - Arco", ("A.A. Williams", "Arco")),
    # A hyphen inside either half is not a separator -- it has no surrounding
    # whitespace.
    ("Colouring - Love To You, Mate", ("Colouring", "Love To You, Mate")),
    ("Lanterns On The Lake - Gracious Tide, Take Me Home",
     ("Lanterns On The Lake", "Gracious Tide, Take Me Home")),
    # A colon inside the album is left alone: the separator is the dash.
    ("Ivor Raymonde - Odyssey: The Sound Of Ivor Raymonde",
     ("Ivor Raymonde", "Odyssey: The Sound Of Ivor Raymonde")),
    ('Beach House - Alien / Lose Your Smile 7"',
     ("Beach House", 'Alien / Lose Your Smile 7"')),
    ("Emmy The Great - April 月音", ("Emmy The Great", "April 月音")),
    # Surrounding and interior whitespace is collapsed before the split.
    ("  Midlake  -  Antiphon  ", ("Midlake", "Antiphon")),
    # Only the FIRST separator splits, so an album carrying one keeps it.
    ("Artist - Album - Deluxe", ("Artist", "Album - Deluxe")),
])
def test_title_parse(title, expected):
    assert Crawler._parse_title(title) == expected


@pytest.mark.parametrize("title", [
    # The two live records the store did not write to its own convention.
    "Cocteau Twins  Four-Calendar Café  (signed by Simon Raymonde)",
    "Harold Budd / Elizabeth Fraser: The Moon and the Melodies",
    # Its merch, none of which names an artist at all.
    'The Fall "Fiery Jack" T Shirt',
    "Bella Union Katakana Logo Cap",
    "Bella Union X Preston Dynamos Nike Football Jersey",
    # Half a credit is not a credit.
    "- Arco",
    "A.A. Williams - ",
    "",
    None,
    # A bare hyphen is not the separator; the store always spaces it.
    "A.A.Williams-Arco",
])
def test_titles_the_parse_does_not_read(title):
    assert Crawler._parse_title(title) == ("", "")


@respx.mock
async def test_a_title_the_parse_does_not_read_yields_nothing(crawler):
    _mock_pages(_ARCO_PRODUCT, _FOUR_CALENDAR_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_the_artist_never_comes_from_vendor(crawler):
    # `vendor` names the label on every product in the store, and on the
    # licensed reissues it names a different label again -- never the artist.
    _mock_pages({**_ARCO_PRODUCT, "vendor": "4AD"})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_a_vendorless_product_still_yields_its_row(crawler):
    _mock_pages({**_ARCO_PRODUCT, "vendor": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_the_row_title_leads_with_the_album_so_it_prefix_matches_a_library_title(crawler):
    # db._library_release_match_sql matches a stock row to a library release
    # on an exact-or-prefix-with-space title test.
    _mock_pages(_ARCO_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"].lower().startswith("arco ")


@pytest.mark.parametrize("descriptor", [
    "Vinyl",
    "Black Vinyl",
    "Double Black & White Vinyl",
    "140g EcoMix Vinyl",
    ' 10" Signed Vinyl',
    "Vinyl (BELLA1090V)",
    "Bella Union Vinyl Edition",
    # Descriptors naming no format at all: every live one is a vinyl box or
    # edition, and the negative gate is what keeps them.
    "Gold Edition",
    "Deluxe Boxset",
    "Limited Edition Boxset",
    "Special Edition Fabric Sleeve Vinyl",
    "",
    # A record packaged with an extra, at a record's price.
    "Yellow Vinyl + Embroidered Patch",
    "Black Vinyl + Signed Print",
    "Red Vinyl + Zine",
    "140g Smoky Black Vinyl + Human Assholes Trading Cards",
    "Clear Vinyl + Comic (BELLA1292VX)",
    # A record word admits over another medium named beside it.
    "Double Vinyl + Bonus CD",
])
def test_format_gate_admits_records_and_undeclared_descriptors(descriptor):
    assert Crawler._is_vinyl(descriptor) is True


@pytest.mark.parametrize("descriptor", [
    "CD",
    "CD (BELLA950CD)",
    "CD + Signed Print",
    "2xCD",
    "Cassette",
    # The store's own misspelling, on a product whose siblings are records.
    "Casseette",
    "Digital",
    "DVD",
    "Blu-ray",
    # Garment sizes, the store's only other option values.
    "S", "M", "L", "XL", "2XL", "One Size",
    # A record joined to a garment: a bundle price, not a record's.
    "Black Vinyl + T Shirt Bundle",
    'Vinyl / "Tie" T Shirt Bundle + Signed Insert',
    'CD / "Window" T Shirt Bundle',
    # A bundle of records is not one record either.
    "Vinyl Bundle",
    # A garment named without the word bundle, and vice versa.
    "Black Vinyl + Tote Bag",
    "Vinyl + Hoodie",
])
def test_format_gate_rejects_other_media_garments_and_bundles(descriptor):
    assert Crawler._is_vinyl(descriptor) is False


@respx.mock
async def test_the_cd_beside_a_record_is_not_published(crawler):
    _mock_pages(_SIGNED_PRINT_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "As The Moon Rests (Signed Print) — Double Gold Vinyl",
        "As The Moon Rests (Signed Print) — Double Black & White Vinyl",
    ]


@respx.mock
async def test_an_album_name_resembling_merch_does_not_decide_the_format(crawler):
    # `(Signed Print)` is part of the album, and the gate never reads it.
    _mock_pages(_SIGNED_PRINT_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_the_stores_cassette_typo_is_not_published_as_vinyl(crawler):
    _mock_pages(_MAHASHMASHANA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Mahashmashana — Double Yellow Vinyl",
        "Mahashmashana — Double Black Vinyl",
    ]


@respx.mock
async def test_a_shirt_bundle_is_skipped_beside_the_plain_pressing(crawler):
    _mock_pages(_SINGLES_LIVE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Singles Live Vol.2 — Black Vinyl"]


@respx.mock
async def test_a_slash_spelled_shirt_bundle_is_skipped(crawler):
    _mock_pages(_WOW_SCENARIO_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Stand in the Star. A Verse and a Chorus — Yellow/Silver Marble Vinyl + Signed Insert",
    ]


@respx.mock
async def test_a_pressing_packaged_with_an_extra_is_still_a_record(crawler):
    _mock_pages(_WHITE_DENIM_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["13 — Yellow Vinyl"]


@respx.mock
async def test_a_descriptor_naming_neither_format_is_admitted(crawler):
    _mock_pages(_ONCE_TWICE_MELODY_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["title"], i["price"]) for i in items] == [
        ("Once Twice Melody — Gold Edition", 89.99),
        ("Once Twice Melody — Silver Edition", 29.99),
    ]


@pytest.mark.parametrize("product,claims", [
    (_ARCO_PRODUCT, True),
    # Untyped, and only a variant says it is a record.
    (_HARP_PRODUCT, True),
    # Typed, and no variant names a format at all.
    (_ALIEN_PRODUCT, True),
    (_ONCE_TWICE_MELODY_PRODUCT, True),
    # Untyped merch, whose variants name sizes.
    (_TSHIRT_PRODUCT, False),
    (_CAP_PRODUCT, False),
])
def test_product_claims_a_record(product, claims):
    assert Crawler._claims_vinyl(product) is claims


@respx.mock
async def test_an_untyped_record_is_published_on_its_variants_claim(crawler):
    _mock_pages({**_HARP_PRODUCT,
                 "variants": [{**_HARP_PRODUCT["variants"][0], "available": True},
                              _HARP_PRODUCT["variants"][1]]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Albion — Vinyl"]


@respx.mock
async def test_an_untyped_product_with_no_record_among_its_formats_yields_nothing(crawler):
    _mock_pages(_ARCO_PRODUCT,
                {**_HARP_PRODUCT, "variants": [
                    {"id": 90, "title": "CD", "price": "9.99", "available": True,
                     "featured_image": None}]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
@pytest.mark.parametrize("product", [_TSHIRT_PRODUCT, _CAP_PRODUCT])
async def test_merch_yields_nothing(crawler, product):
    _mock_pages(_ARCO_PRODUCT, product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_merch_titled_like_a_record_is_kept_out_by_its_tag_alone(crawler):
    # Invented: the tag is the store's own claim and decides ahead of
    # everything else. Every other rule is defeated on purpose here -- the
    # title parses, the store typed it `Vinyl`, and the option is a colour
    # rather than a size -- so the tag is the only thing left rejecting it.
    _mock_pages(_ARCO_PRODUCT, {
        "title": "The Fall - Fiery Jack Shirt",
        "vendor": "Bella Union",
        "handle": "the-fall-fiery-jack-shirt",
        "product_type": "Vinyl",
        "tags": ["Merch", "The Fall"],
        "images": [{"src": "https://cdn.shopify.com/the-fall-shirt.png"}],
        "variants": [
            {"id": 92, "title": "Black", "price": "22.99", "available": True,
             "featured_image": None},
        ],
    })
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_an_untyped_product_naming_no_format_is_kept_out_by_the_claim_alone(crawler):
    # Invented: an untagged, untyped non-record whose sole variant is the
    # placeholder. The title parses and the descriptor names nothing the
    # variant gate rejects, so the product-level claim is the only thing
    # keeping it out.
    _mock_pages(_ARCO_PRODUCT, {
        "title": "Beach House - Tour Poster",
        "vendor": "Bella Union",
        "handle": "beach-house-tour-poster",
        "product_type": "",
        "tags": ["Beach House"],
        "images": [{"src": "https://cdn.shopify.com/bh-poster.jpg"}],
        "variants": [
            {"id": 93, "title": "Default Title", "price": "15.00", "available": True,
             "featured_image": None},
        ],
    })
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_a_cd_only_product_yields_nothing_without_raising(crawler):
    _mock_pages(_ARCO_PRODUCT, _PERADAM_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_named_variants_are_appended_to_every_row(crawler):
    _mock_pages(_MAHASHMASHANA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert all(" — " in i["title"] for i in items)


@respx.mock
async def test_a_product_reduced_to_one_named_variant_still_carries_it(crawler):
    # A sibling being delisted must not re-title the survivor and orphan the
    # saves and judgments keyed on its item_key.
    _mock_pages(_one_pressing(_ARCO_PRODUCT))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Arco — Galaxy Teal Vinyl"]


@respx.mock
async def test_placeholder_variant_carries_the_album_alone(crawler):
    _mock_pages(_ALIEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Alien / Lose Your Smile 7"']


@respx.mock
async def test_placeholder_matching_is_case_insensitive(crawler):
    # Altered: Shopify's own casing varies across storefronts.
    _mock_pages(_one_pressing(_ALIEN_PRODUCT, title="  default title  "))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ['Alien / Lose Your Smile 7"']


@respx.mock
async def test_placeholder_on_a_multi_variant_product_is_skipped(crawler):
    # Invented: malformed data. Published, it would share the album and the
    # URL -- and so the item_key -- with the sole-variant row it would be.
    _mock_pages({**_ARCO_PRODUCT, "variants": [
        {"id": 40, "title": "Default Title", "price": "22.99", "available": True,
         "featured_image": None},
        {"id": 41, "title": "Black Vinyl", "price": "22.99", "available": True,
         "featured_image": None},
    ]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Arco — Black Vinyl"]


@respx.mock
async def test_blank_variant_title_is_skipped(crawler):
    # Invented: a blank descriptor is never a pressing.
    _mock_pages({**_ARCO_PRODUCT, "variants": [
        {"id": 42, "title": "   ", "price": "22.99", "available": True,
         "featured_image": None},
        {"id": 43, "title": "Black Vinyl", "price": "22.99", "available": True,
         "featured_image": None},
    ]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Arco — Black Vinyl"]


@respx.mock
async def test_variant_title_whitespace_is_collapsed(crawler):
    _mock_pages(_one_pressing(_ARCO_PRODUCT, title="  Galaxy   Teal  Vinyl "))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Arco — Galaxy Teal Vinyl"]


@respx.mock
async def test_every_admitted_variant_yields_its_own_identity(crawler):
    _mock_pages(_SIGNED_PRINT_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len({(i["artist"], i["title"], i["url"]) for i in items}) == len(items)


@respx.mock
async def test_sold_out_variant_is_skipped_beside_its_in_stock_siblings(crawler):
    _mock_pages(_ARCO_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Arco — Galaxy Teal Vinyl"]


@respx.mock
@pytest.mark.parametrize("available", [False, "false", "true", 1, None, "yes"])
async def test_only_the_literal_true_admits_a_variant(crawler, available):
    _mock_pages(_ARCO_PRODUCT,
                _one_pressing(_ALIEN_PRODUCT, available=available))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
async def test_a_preorder_carries_no_marker(crawler):
    # The store's only pre-order signal is membership of a separate
    # collection, and no marker is written even so: compute_item_key hashes
    # the title, so one that vanished on release would re-key every pressing.
    _mock_pages(_ARCO_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert "pre-order" not in items[0]["title"].lower()


@respx.mock
async def test_junk_variant_entries_are_ignored(crawler):
    # Invented: a non-mapping entry must not raise from inside the yield loop.
    _mock_pages({**_ARCO_PRODUCT, "variants": [
        "not-a-variant",
        {"id": 44, "title": "Black Vinyl", "price": "22.99", "available": True,
         "featured_image": None},
    ]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Arco — Black Vinyl"]


@respx.mock
async def test_product_missing_its_handle_is_skipped(crawler):
    _mock_pages(_ARCO_PRODUCT, {**_ALIEN_PRODUCT, "handle": "  "})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams"]


@respx.mock
@pytest.mark.parametrize("price,expected", [
    ("22.99", 22.99),
    (22.99, 22.99),
    (None, None),
    ("", None),
    ("free", None),
    (True, None),
    ("0", None),
    ("-1.00", None),
    ("nan", None),
    ("inf", None),
])
async def test_price_parsing(crawler, price, expected):
    _mock_pages(_ARCO_PRODUCT, _one_pressing(_ALIEN_PRODUCT, price=price))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[1]["price"] == expected or (items[1]["price"] is None and expected is None)


@respx.mock
async def test_cover_falls_back_to_the_products_first_image(crawler):
    _mock_pages(_one_pressing(_ARCO_PRODUCT, featured_image=None))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/242802-1.jpg"


@respx.mock
async def test_cover_is_none_when_the_product_has_no_images(crawler):
    _mock_pages({**_one_pressing(_ARCO_PRODUCT, featured_image=None), "images": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_pagination_walks_every_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_ARCO_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_ALIEN_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["A.A. Williams", "Beach House"]


@respx.mock
async def test_an_empty_collection_raises(crawler):
    _mock_pages(empty_page=1)
    with pytest.raises(RuntimeError, match="returned no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_catalog_with_no_readable_title_raises(crawler):
    _mock_pages({**_ARCO_PRODUCT, "title": "Arco"})
    with pytest.raises(RuntimeError, match="title-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_unreadable_title_among_real_rows_does_not_raise(crawler):
    _mock_pages(_ARCO_PRODUCT, _FOUR_CALENDAR_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_a_catalog_with_no_prices_raises(crawler):
    _mock_pages(_one_pressing(_ARCO_PRODUCT, price=None))
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priceless_row_among_priced_ones_does_not_raise(crawler):
    _mock_pages(_ARCO_PRODUCT, _one_pressing(_ALIEN_PRODUCT, price=None))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [22.99, None]


@respx.mock
async def test_an_unreadable_product_beside_a_sold_out_one_raises(crawler):
    # The catalog-wide title guard cannot see a source failing on ONE
    # product: the sold-out record keeps parsed_ok non-zero.
    _mock_pages(_one_pressing(_ARCO_PRODUCT, available=False), _FOUR_CALENDAR_PRODUCT)
    with pytest.raises(RuntimeError, match="classification drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("product", [_TSHIRT_PRODUCT, _CAP_PRODUCT])
async def test_a_classified_skip_beside_a_sold_out_record_does_not_raise(crawler, product):
    # Merch was read and deliberately skipped; it is evidence of nothing.
    _mock_pages(_one_pressing(_ARCO_PRODUCT, available=False), product)
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_non_record_product_beside_a_sold_out_record_does_not_raise(crawler):
    # A product with no record among its formats was read and skipped too.
    _mock_pages(_one_pressing(_ARCO_PRODUCT, available=False),
                {**_HARP_PRODUCT, "variants": [
                    {"id": 91, "title": "CD", "price": "9.99", "available": True,
                     "featured_image": None}]})
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_title_less_product_beside_a_sold_out_record_raises(crawler):
    # Counted before the format gate: the parse reads the title, so nothing
    # downstream can classify a product without one.
    _mock_pages(_one_pressing(_ARCO_PRODUCT, available=False),
                {**_ALIEN_PRODUCT, "title": "  "})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_sold_out_record_missing_its_handle_raises(crawler):
    _mock_pages({**_one_pressing(_ARCO_PRODUCT, available=False), "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_less_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages(_ARCO_PRODUCT, {**_ALIEN_PRODUCT, "handle": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_a_handle_less_non_record_does_not_satisfy_the_identity_guard(crawler):
    _mock_pages(_one_pressing(_ARCO_PRODUCT, available=False),
                {**_CAP_PRODUCT, "handle": ""})
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
@pytest.mark.parametrize("available", ["true", 1, None])
async def test_a_catalog_with_no_readable_availability_raises(crawler, available):
    _mock_pages(_one_pressing(_ARCO_PRODUCT, available=available))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_readable_pressing_does_not_vouch_for_an_unreadable_sibling(crawler):
    # all(), not any(): the black pressing is a readable False and the
    # coloured one carries the string "false", so the product yields nothing
    # and half that emptiness is its own doing.
    _mock_pages({**_ARCO_PRODUCT, "variants": [
        {"id": 50, "title": "Black Vinyl", "price": "22.99", "available": False,
         "featured_image": None},
        {"id": 51, "title": "Clear Vinyl", "price": "22.99", "available": "false",
         "featured_image": None},
    ]})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_product_with_no_variants_at_all_raises(crawler):
    _mock_pages({**_ARCO_PRODUCT, "variants": []})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_unreadable_product_among_real_rows_does_not_raise(crawler):
    _mock_pages(_ARCO_PRODUCT, _one_pressing(_ALIEN_PRODUCT, available="true"))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_a_genuinely_sold_out_catalog_is_a_legitimate_empty_result(crawler):
    _mock_pages(_one_pressing(_ARCO_PRODUCT, available=False),
                _one_pressing(_ALIEN_PRODUCT, available=False))
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
@pytest.mark.parametrize("available", [True, "true", 1, None])
async def test_a_dropped_variant_that_is_not_provably_sold_out_raises(crawler, available):
    # An in-stock pressing whose descriptor is unusable is invisible, and the
    # readable sold-out sibling beside it must not vouch for the emptiness.
    _mock_pages({**_ARCO_PRODUCT, "variants": [
        {"id": 45, "title": "  ", "price": "22.99", "available": available,
         "featured_image": None},
        {"id": 46, "title": "Black Vinyl", "price": "22.99", "available": False,
         "featured_image": None},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_dropped_variant_that_is_provably_sold_out_does_not_raise(crawler):
    _mock_pages({**_ARCO_PRODUCT, "variants": [
        {"id": 47, "title": "  ", "price": "22.99", "available": False,
         "featured_image": None},
        {"id": 48, "title": "Black Vinyl", "price": "22.99", "available": False,
         "featured_image": None},
    ]})
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_junk_variant_beside_a_sold_out_pressing_raises(crawler):
    # A junk entry carries no availability at all, so it can never be proven
    # sold out.
    _mock_pages({**_ARCO_PRODUCT, "variants": [
        "not-a-variant",
        {"id": 49, "title": "Black Vinyl", "price": "22.99", "available": False,
         "featured_image": None},
    ]})
    with pytest.raises(RuntimeError, match="variant-identity drift"):
        [item async for item in crawler.crawl_catalog()]
