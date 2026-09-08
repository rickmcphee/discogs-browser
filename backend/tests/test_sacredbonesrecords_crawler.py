import httpx
import respx
import pytest
from crawlers.sacredbonesrecords import Crawler

_PRODUCTS_URL = "https://www.sacredbonesrecords.com/collections/vinyl/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-08, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape -- `vendor` is the artist, the title
# is the album alone, and each variant names a pressing and carries its own
# image. Tagged as a pre-order, with both pre-order variants available.
_LOST_THEMES_PRODUCT = {
    "title": "Lost Themes II Expanded 10th Anniversary Expanded Edition",
    "vendor": "John Carpenter",
    "handle": "sbr385-john-carpenter-lost-themes-ii-expanded",
    "product_type": "Release",
    "tags": ["2026", "All Music", "Flag_Preorder", "John Carpenter", "Release",
             "Sacred Bones Records", "Street Date"],
    "images": [{"src": "https://cdn.shopify.com/sbr385-johncarpenter-3840.jpg"}],
    "variants": [
        {"id": 42648837193774, "title": "Sacred Bones Exclusive Blue & Magenta Plasma Vinyl LP",
         "price": "33.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sbr385-johncarpenter-BE.jpg"}},
        {"id": 42648837226542, "title": "Limited Edition Violet Sunrise Vinyl LP",
         "price": "31.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sbr385-johncarpenter-C2.jpg"}},
    ],
}

# Captured: one release published on vinyl, CD, cassette and two digital
# formats, which is why the medium is decided per variant rather than per
# product. `Sacred Bones Koi Pond Edition` and `Deluxe Zoetrope Edition` name
# no format at all and are admitted on the collection's claim.
_BELAYA_POLOSA_PRODUCT = {
    "title": "Belaya Polosa",
    "vendor": "Molchat Doma",
    "handle": "sbr345-molchat-doma-belaya-polosa",
    "product_type": "Release",
    "tags": ["2024", "All Music", "Molchat Doma", "Release", "Sacred Bones Records"],
    "images": [{"src": "https://cdn.shopify.com/sbr345-molchatdoma-1800.jpg"}],
    "variants": [
        {"id": 40884525596718, "title": "Sacred Bones Koi Pond Edition", "price": "25.00",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/SBR345_BE.jpg"}},
        {"id": 40906193895470, "title": "Limited Edition Cloudy Clear LP", "price": "23.00",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/SBR345_C3.jpg"}},
        {"id": 40884525563950, "title": "Black LP", "price": "21.00",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/SBR345_LP.jpg"}},
        {"id": 40884525629486, "title": "CD", "price": "14.00",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/sbr345-CD.jpg"}},
        {"id": 40884525662254, "title": "Cassette", "price": "12.00",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/sbr345-CA.jpg"}},
        {"id": 40884525826094, "title": "Deluxe Zoetrope Edition", "price": "30.00",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/SBR345_C6.jpg"}},
        {"id": 41087115100206, "title": "Digital Download: WAV", "price": "7.00",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/sbr345-1800.jpg"}},
        {"id": 41087115264046, "title": "Digital Download: MP3", "price": "7.00",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/sbr345-1800.jpg"}},
    ],
}

# Captured: the shelf's non-record strays sit beside the records as extra
# variants of the same product -- a guitar pedal here, and a cassette the
# store spells `Tape`.
_BORIS_W_PRODUCT = {
    "title": "W",
    "vendor": "Boris",
    "handle": "sbr287-boris-w",
    "product_type": "Release",
    "tags": ["2022", "All Music", "boris", "Release", "Sacred Bones Records"],
    "images": [{"src": "https://cdn.shopify.com/sbr287-boris-1800.jpg"}],
    "variants": [
        {"id": 39623738425390, "title": "Sacred Bones Exclusive Blue and White Starburst Vinyl LP",
         "price": "30.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/SBR-287-BE.jpg"}},
        {"id": 39623738490926, "title": "Black Vinyl LP", "price": "25.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/SBR-287-Black.jpg"}},
        {"id": 39623738556462, "title": "CD", "price": "13.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sbr287-boris-CD.jpg"}},
        {"id": 39623738589230, "title": "Tape", "price": "10.00", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/sbr287-boris-CA.jpg"}},
        {"id": 39623772930094, "title": "Sacred Bones exclusive Boris Pedal", "price": "150.00",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/Hizumitas.jpg"}},
    ],
}

# Captured: the digit-glued counts. `\b` matches nothing between `3` and `CD`
# or between `2` and `xCS`, so a plain word-boundary pattern reads neither as
# another medium.
_HALLOWEEN_BOX_PRODUCT = {
    "title": "Halloween: The Complete Expanded Collection",
    "vendor": "John Carpenter, Cody Carpenter, and Daniel Davies",
    "handle": "halloween-the-complete-expanded-collection",
    "product_type": "Release",
    "tags": ["2025", "box set", "halloween", "Release", "Sacred Bones Records", "soundtrack"],
    "images": [{"src": "https://cdn.shopify.com/sbr348-halloweenbox-3600.jpg"}],
    "variants": [
        {"id": 41459041632302, "title": "Limited Edition Dried Blood Vinyl Box Set",
         "price": "155.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sbr348-C3.jpg"}},
        {"id": 41459041665070, "title": "Limited Edition 3xCD Box Set", "price": "65.00",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/sbr348-CD.jpg"}},
    ],
}

_UNIFORM_BODY_PRODUCT = {
    "title": "Everything That Dies Someday Comes Back",
    "vendor": "Uniform & The Body",
    "handle": "sba003-uniform-the-body-everything-that-dies-someday-comes-back",
    "product_type": "Release",
    "tags": ["2019", "All Music", "Release", "Sacred Bones Alliance"],
    "images": [{"src": "https://cdn.shopify.com/sba003-unibody-300.jpg"}],
    "variants": [
        {"id": 40063500025902, "title": "15th Label Anniversary Silver LP", "price": "23.00",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/SBA003-C2.jpg"}},
        {"id": 16824510087214, "title": "2-in-1 CD", "price": "13.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sba004-CD.jpg"}},
        {"id": 16824510119982, "title": "2xCS Box Set", "price": "20.00", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/sba004-cassette.jpg"}},
        {"id": 39282274140206, "title": "Digital Album MP3", "price": "9.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sba003-unibody-300.jpg"}},
    ],
}

# Captured: a tagged pre-order whose wax-seal edition had already sold out
# while its siblings were still selling -- which is why an unavailable
# variant is skipped even on a pre-order.
_PREORDER_PRODUCT = {
    "title": "evidence",
    "vendor": "Spike Fuck",
    "handle": "sbr-384-spike-fcuk-evidence",
    "product_type": "Release",
    "tags": ["2026", "All Music", "Flag_Preorder", "Release", "Spike Fuck", "Street Date"],
    "images": [{"src": "https://cdn.shopify.com/sbr384-spikefuck-3840.jpg"}],
    "variants": [
        {"id": 42487619387438, "title": "Sacred Bones Wax Seal Limited Edition Silver Vinyl LP",
         "price": "30.00", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/sbr384-LE.jpg"}},
        {"id": 42487619420206, "title": "Sacred Bones Exclusive Silver Vinyl LP", "price": "25.00",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/sbr384-BE.jpg"}},
        {"id": 42487619485742, "title": "CD", "price": "14.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sbr384-CD.jpg"}},
    ],
}

# Captured: the store's charity raffle. Every "variant" is a $10 entry, and
# two of them name a prize rather than a pressing.
_RAFFLE_PRODUCT = {
    "title": "Immigration Solidarity Record Raffle",
    "vendor": "Sacred Bones Records",
    "handle": "immigration-solidarity-record-raffle",
    "product_type": "Release",
    "tags": ["All Music", "bundle", "Charity", "David Lynch", "Donation", "Flag_Sold Out",
             "Raffle", "Release", "Sacred Bones Records"],
    "images": [{"src": "https://cdn.shopify.com/immigration-raffle.jpg"}],
    "variants": [
        {"id": 42150000000001, "title": "Society 25", "price": "10.00", "available": True,
         "featured_image": None},
        {"id": 42150000000002, "title": "Signed Mandy Indiana Poster  Sacred Bones Exclusive "
                                       "Vinyl LP of URGH", "price": "10.00", "available": True,
         "featured_image": None},
    ],
}

# Captured: a distro product, single variant, sold out. The option axis is
# named `LP` rather than `Format`, which the crawler never reads -- the
# variant title carries the medium either way.
_DISTRO_PRODUCT = {
    "title": "London 69",
    "vendor": "Hector Sepulveda",
    "handle": "london-69",
    "product_type": "Distro",
    "tags": ["BYM Records", "distro", "Hector Sepulveda"],
    "images": [{"src": "https://cdn.shopify.com/Front_BYM057.jpg"}],
    "variants": [
        {"id": 14887140458542, "title": "LP", "price": "20.00", "available": False,
         "featured_image": None},
    ],
}

# Altered: _DISTRO_PRODUCT's sole variant, in stock, with no image of its own
# so the cover falls back to the product image.
_DISTRO_IN_STOCK = {
    **_DISTRO_PRODUCT,
    "variants": [{**_DISTRO_PRODUCT["variants"][0], "available": True}],
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


def _pressing(product, name, **overrides):
    # A captured product whose sole variant is renamed, for the gate tests:
    # everything else about the row stays live data.
    return _one_pressing(product, title=name, available=True, **overrides)


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "John Carpenter",
        "title": "Lost Themes II Expanded 10th Anniversary Expanded Edition — "
                 "Sacred Bones Exclusive Blue & Magenta Plasma Vinyl LP",
        "format": "Vinyl",
        "price": 33.0,
        "currency": "USD",
        "url": "https://www.sacredbonesrecords.com/products/"
               "sbr385-john-carpenter-lost-themes-ii-expanded",
        "cover_image_url": "https://cdn.shopify.com/sbr385-johncarpenter-BE.jpg",
    }]


def test_plugin_identity():
    assert Crawler.site_name == "Sacred Bones Records"
    assert Crawler.base_url == "https://www.sacredbonesrecords.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary


@respx.mock
async def test_artist_is_the_vendor_and_title_is_the_album_alone(crawler):
    _mock_pages(_one_pressing(_BELAYA_POLOSA_PRODUCT, index=2))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Molchat Doma"
    assert items[0]["title"] == "Belaya Polosa — Black LP"


@respx.mock
async def test_the_row_title_leads_with_the_album_so_it_prefix_matches_a_library_title(crawler):
    # db._library_release_match_sql matches a stock title against a catalog
    # title with exact-or-prefix-with-space, so the album has to lead and the
    # pressing has to follow a space.
    _mock_pages(_one_pressing(_BELAYA_POLOSA_PRODUCT, index=2))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"].lower().startswith("belaya polosa ")


@respx.mock
async def test_a_self_titled_record_keeps_its_title(crawler):
    # Invented only in that the store's self-titled records (Khanate /
    # Khanate) are spread over other products: nothing is stripped just
    # because the title starts with the vendor.
    _mock_pages(_one_pressing({**_BORIS_W_PRODUCT, "title": "Boris"}))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Boris — Sacred Bones Exclusive Blue and White Starburst Vinyl LP"


@pytest.mark.parametrize("name", [
    "Black LP",
    "Limited Edition Cloudy Clear LP",
    "Sea Blue Vinyl LP",
    "Red Vinyl 2xLP",
    'Black Vinyl 7"',
    'Limited Edition Blue Galaxy Vinyl 12"',
    "Gatefold LP+Zine",
    "Limited Edition Smoke Vinyl LP w/ Print Set",
    "Wicked Animal Limited Edition Heart Shaped Flexi",
    "Chrystabell & David Lynch - Cellophane Memories Test Press",
    "Limited Edition Dried Blood Vinyl Box Set",
])
def test_format_gate_admits_a_named_record(name):
    assert Crawler._is_vinyl(name) is True


@pytest.mark.parametrize("name", [
    "Sacred Bones Koi Pond Edition",
    "Deluxe Zoetrope Edition",
    "Lavender Swirl",
    "Clear Pink",
    "Sacred Bones Exclusive Black and White Galaxy",
    "Blue & White Galaxy",
    "15th Label Anniversary Limited Edition Royal Blue",
    "Art Edition Red Fire",
])
def test_format_gate_admits_a_pressing_named_only_by_its_colour(name):
    # The store names coloured pressings by colour alone often enough that a
    # positive-only regex would drop most of its exclusives.
    assert Crawler._is_vinyl(name) is True


@pytest.mark.parametrize("name", [
    "CD",
    "Black CD",
    "Double CD",
    "Japanese Import CD with OBI-Strip",
    "Limited Edition 3xCD Box Set",
    "2xCS Box Set",
    "Cassette",
    "Blue Shell Cassette",
    "Tape",
    "Rave Case Tape",
    "8 Track",
    "8 Track - White Shell",
    "Blu-Ray",
    "Digital Album MP3",
    "Digital Download: WAV",
    "Digital Single AIFF",
    "SBR-333-WAV",
    "MP3 Digital Download",
    "Limited Edition hand numbered posters designed by Grace O’Conner, limited to 150",
    "Sacred Bones exclusive Boris Pedal",
    "Deluxe Photobook + CD",
])
def test_format_gate_rejects_another_medium(name):
    assert Crawler._is_vinyl(name) is False


def test_a_vinyl_word_wins_over_a_merch_word_in_the_same_name():
    # The merch words reject a pressing that merely mentions one, so a name
    # that says "record" as well has to be admitted.
    assert Crawler._is_vinyl("Limited Edition Red Glitter Vinyl LP + Poster") is True


@respx.mock
async def test_other_media_variants_of_a_vinyl_release_are_skipped(crawler):
    _mock_pages(_BELAYA_POLOSA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Belaya Polosa — Limited Edition Cloudy Clear LP",
        "Belaya Polosa — Black LP",
    ]


@respx.mock
async def test_the_shelfs_non_record_strays_are_skipped(crawler):
    _mock_pages(_BORIS_W_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "W — Sacred Bones Exclusive Blue and White Starburst Vinyl LP",
        "W — Black Vinyl LP",
    ]


@respx.mock
@pytest.mark.parametrize("product,expected", [
    (_HALLOWEEN_BOX_PRODUCT, "Halloween: The Complete Expanded Collection — "
                             "Limited Edition Dried Blood Vinyl Box Set"),
    (_UNIFORM_BODY_PRODUCT, "Everything That Dies Someday Comes Back — "
                            "15th Label Anniversary Silver LP"),
])
async def test_a_digit_glued_count_does_not_hide_another_medium(crawler, product, expected):
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [expected]


@respx.mock
async def test_an_album_name_resembling_another_medium_does_not_decide_the_format(crawler):
    # The gate reads the pressing name only, never the product title, so an
    # album called "Tape" cannot reject its own vinyl.
    _mock_pages(_one_pressing({**_BORIS_W_PRODUCT, "title": "Cassette Tape"}))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Cassette Tape — Sacred Bones Exclusive Blue and White Starburst Vinyl LP"


@respx.mock
async def test_a_raffle_yields_nothing(crawler):
    # Both entries are in stock and one of them names a record, so only the
    # tag keeps them out.
    _mock_pages(_RAFFLE_PRODUCT, _one_pressing(_LOST_THEMES_PRODUCT))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["John Carpenter"]


@respx.mock
@pytest.mark.parametrize("tag", ["Raffle", "raffle", "Donation", " donation "])
async def test_the_skip_tags_are_matched_case_and_whitespace_insensitively(crawler, tag):
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT), "tags": ["Release", tag]},
                _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Hector Sepulveda"]


@respx.mock
async def test_a_skipped_product_is_a_legitimate_empty_result(crawler):
    # A skipped product contributes no pressings, so it cannot tally toward
    # the identity or stock guards -- a shelf of raffles is empty, not drift.
    _mock_pages(_RAFFLE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_named_pressings_are_appended_to_every_row(crawler):
    # Appended even when a product is down to one listed pressing: a sibling
    # selling out must not re-title the survivor and orphan what hangs off
    # its item_key.
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"].endswith(" — Sacred Bones Exclusive Blue & Magenta Plasma Vinyl LP")


@respx.mock
@pytest.mark.parametrize("name", ["Default Title", "default title", "  ", ""])
async def test_a_nameless_sole_variant_carries_the_title_alone(crawler, name):
    _mock_pages(_pressing(_DISTRO_PRODUCT, name))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "London 69"


@respx.mock
@pytest.mark.parametrize("name", ["Default Title", ""])
async def test_a_nameless_variant_beside_siblings_is_skipped(crawler, name):
    # It names no pressing, so a row built on it would share its title and
    # product URL -- and so its item_key -- with the bare-title row.
    product = {**_BELAYA_POLOSA_PRODUCT, "variants": [
        {**_BELAYA_POLOSA_PRODUCT["variants"][2]},
        {**_BELAYA_POLOSA_PRODUCT["variants"][1], "title": name},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Belaya Polosa — Black LP"]


@respx.mock
async def test_pressing_name_whitespace_is_collapsed(crawler):
    _mock_pages(_pressing(_DISTRO_PRODUCT, "  Black   Vinyl\nLP "))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "London 69 — Black Vinyl LP"


@respx.mock
async def test_every_admitted_pressing_yields_its_own_row(crawler):
    _mock_pages(_LOST_THEMES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len({i["title"] for i in items}) == 2
    assert len({i["cover_image_url"] for i in items}) == 2


@respx.mock
async def test_sold_out_pressing_is_skipped_beside_its_in_stock_siblings(crawler):
    _mock_pages(_BELAYA_POLOSA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert "Belaya Polosa — Sacred Bones Koi Pond Edition" not in {i["title"] for i in items}


@respx.mock
@pytest.mark.parametrize("available", [False, "false", "true", 1, None, "yes"])
async def test_only_the_literal_true_admits_a_pressing(crawler, available):
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT, available=available),
                _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Hector Sepulveda"]


@respx.mock
async def test_a_sold_out_preorder_is_skipped_and_no_marker_is_written(crawler):
    # The store's live pre-orders report available True, so an unavailable
    # variant on one is a closed allocation, not a pre-order to admit.
    _mock_pages(_PREORDER_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["evidence — Sacred Bones Exclusive Silver Vinyl LP"]
    assert "Pre-Order" not in items[0]["title"]


@respx.mock
async def test_junk_variant_entries_are_ignored(crawler):
    product = {**_DISTRO_IN_STOCK,
               "variants": ["nonsense", None, 7] + _DISTRO_IN_STOCK["variants"]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["London 69 — LP"]


@respx.mock
@pytest.mark.parametrize("mutate", [{"vendor": ""}, {"vendor": "   "}, {"vendor": None}])
async def test_a_product_with_no_vendor_is_skipped(crawler, mutate):
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT), **mutate},
                _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Hector Sepulveda"]


@respx.mock
@pytest.mark.parametrize("mutate", [{"vendor": ""}, {"vendor": None}])
async def test_a_catalog_with_no_vendor_at_all_raises(crawler, mutate):
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT), **mutate})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_credited_product_is_enough_to_satisfy_the_artist_guard(crawler):
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT), "vendor": ""},
                _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
@pytest.mark.parametrize("mutate", [{"handle": ""}, {"handle": "   "}, {"title": ""}])
async def test_a_product_missing_its_identity_is_skipped(crawler, mutate):
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT), **mutate},
                _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Hector Sepulveda"]


@respx.mock
@pytest.mark.parametrize("mutate", [{"handle": ""}, {"title": ""}])
async def test_a_catalog_with_no_identity_raises(crawler, mutate):
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT), **mutate})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_identity_less_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT), "handle": ""},
                _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_a_sold_out_product_missing_its_handle_still_raises(crawler):
    # The tally is taken before the availability filter, so a product that
    # would have yielded a row had it been in stock still counts.
    _mock_pages({**_one_pressing(_LOST_THEMES_PRODUCT, available=False), "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_identity_less_non_record_does_not_trip_the_guard(crawler):
    # Nothing about a CD-only product's missing handle says the payload
    # drifted, so a shelf of them is a legitimate empty result.
    _mock_pages({**_one_pressing(_BELAYA_POLOSA_PRODUCT, index=3), "handle": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
@pytest.mark.parametrize("available", ["false", 1, None])
async def test_a_catalog_with_no_readable_availability_raises(crawler, available):
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT, available=available))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_unreadable_product_among_real_rows_does_not_raise(crawler):
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT, available="false"),
                _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_a_genuinely_sold_out_shelf_is_a_legitimate_empty_result(crawler):
    _mock_pages(_DISTRO_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_one_unreadable_pressing_does_not_vouch_for_its_readable_sibling(crawler):
    # every(), not any(): the readable False would otherwise make a product
    # half of whose emptiness is unexplained look legitimately sold out.
    product = {**_LOST_THEMES_PRODUCT, "variants": [
        {**_LOST_THEMES_PRODUCT["variants"][0], "available": False},
        {**_LOST_THEMES_PRODUCT["variants"][1], "available": "false"},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@pytest.mark.parametrize("raw", [None, "", "free", True, False, "nan", "inf", "-inf",
                                 float("nan"), float("inf"), "0", 0, -1, [], {}])
def test_unusable_price_yields_none(raw):
    assert Crawler._price({"price": raw}) is None


@pytest.mark.parametrize("raw,expected", [("21.00", 21.0), ("7", 7.0), (23.5, 23.5), (19, 19.0)])
def test_usable_price_is_parsed(raw, expected):
    assert Crawler._price({"price": raw}) == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    variant = {k: v for k, v in _LOST_THEMES_PRODUCT["variants"][0].items() if k != "price"}
    _mock_pages({**_LOST_THEMES_PRODUCT, "variants": [variant,
                 _LOST_THEMES_PRODUCT["variants"][1]]})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["price"] is None
    assert items[1]["price"] == 31.0


@respx.mock
@pytest.mark.parametrize("mutate", [{"price": None}, {"price": "free"}, {"price": "0"}])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT, **mutate))
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT, price=None), _one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 20.0]


@respx.mock
async def test_an_empty_result_does_not_trip_the_price_guard(crawler):
    _mock_pages(_DISTRO_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    _mock_pages(_one_pressing(_LOST_THEMES_PRODUCT))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/sbr385-johncarpenter-BE.jpg"


@respx.mock
async def test_cover_falls_back_to_product_image(crawler):
    _mock_pages(_DISTRO_IN_STOCK)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/Front_BYM057.jpg"


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    _mock_pages({**_DISTRO_IN_STOCK, "images": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_url_is_built_from_the_handle_as_written(crawler):
    _mock_pages(_one_pressing(_DISTRO_IN_STOCK))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["url"] == "https://www.sacredbonesrecords.com/products/london-69"


@respx.mock
async def test_empty_collection_raises(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([]))
    with pytest.raises(RuntimeError, match="returned no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_catalog_of_only_other_media_does_not_raise(crawler):
    # The format gate is negative, so a shelf that legitimately filled up
    # with CDs is an empty result rather than drift.
    _mock_pages(_one_pressing(_BELAYA_POLOSA_PRODUCT, index=3),
                _one_pressing(_BELAYA_POLOSA_PRODUCT, index=6))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_pagination_walks_every_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_one_pressing(_LOST_THEMES_PRODUCT)]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_one_pressing(_DISTRO_IN_STOCK)]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["John Carpenter", "Hector Sepulveda"]
