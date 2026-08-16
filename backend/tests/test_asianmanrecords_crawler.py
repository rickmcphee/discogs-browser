import httpx
import respx
import pytest
from crawlers.asianmanrecords import Crawler

_PRODUCTS_URL = "https://asianmanrecords.com/collections/all-products/products.json"

# Real confirmed-live case: quoted album, no hyphen before the quote.
_KOREA_GIRL = {
    "title": 'KOREA GIRL "Korea Girl" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "korea-girl-korea-girl-12",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0868/9755/7813/files/KoreaGirl_Cover_Lp_24.jpg"}],
    "variants": [
        {"title": "COLOR VINYL", "price": "25.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: quoted album with a spaced hyphen before the quote,
# product_type says vinyl but the tags array doesn't (only "NEW RELEASE") -- the
# gate must still include it. Also a real apparel-bundle case: 6 size variants,
# all "... BUNDLE DEAL", priced 29.99/29.99/29.99/29.99/31.99/34.99 -- must
# collapse to one stock item at the cheapest price, not six near-duplicates.
_GRUMPSTER = {
    "title": 'GRUMPSTER - "Honeydew" 12" VINYL + T-SHIRT',
    "vendor": "Asian Man Records",
    "handle": "grumpster-honeydew-12-vinyl",
    "product_type": "12-INCH VINYL",
    "tags": ["NEW RELEASE"],
    "images": [],
    "variants": [
        {"title": "SMALL BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "MEDIUM BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "LARGE BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "XL BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "XXL BUNDLE DEAL", "price": "31.99", "available": True, "featured_image": None},
        {"title": "XXXL BUNDLE DEAL", "price": "34.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "PRE ORDER:" prefix plus a hyphen glued to the artist
# word (no space before it) -- both the prefix strip and the quote regex's
# optional/asymmetric hyphen handling are exercised together. Another real
# apparel-bundle case (6 size variants, cheapest is 36.99).
_SMOKING_POPES_PREORDER = {
    "title": 'PRE ORDER: SMOKING POPES- "Stay Down" 12" VINYL + T-SHIRT',
    "vendor": "Asian Man Records",
    "handle": "pre-order-the-albert-square-i-wish-i-could-talk-to-people-12-vinyl-t-shirt-copy",
    "product_type": "12-INCH VINYL",
    "tags": ["NEW RELEASE"],
    "images": [],
    "variants": [
        {"title": "SMALL BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "MEDIUM BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "LARGE BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "XL BUNDLE DEAL", "price": "36.99", "available": True, "featured_image": None},
        {"title": "XXL BUNDLE DEAL", "price": "38.99", "available": True, "featured_image": None},
        {"title": "XXXL BUNDLE DEAL", "price": "39.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "AMR DISTRO:" prefix -- another label's release
# (Skankin' Pickle, not an Asian Man Records release) resold through this
# store's own distro. In scope per the design spec: the prefix is stripped,
# the product is not excluded.
_SKANKIN_PICKLE_DISTRO = {
    "title": 'AMR DISTRO: SKANKIN\' PICKLE "Green Album" 12" BLACK VINYL',
    "vendor": "Asian Man Records",
    "handle": "skankin-pickle-green-album-lp-black-vinyl",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "BLACK", "price": "18.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: both prefixes together, with the site's own doubled-
# colon typo ("AMR DISTRO::") -- confirms _PREFIX_RE's `:+` handles it.
_AJJ_DISTRO_PREORDER = {
    "title": 'PRE ORDER: AMR DISTRO:: AJJ - "Dirty Old Power" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "ajj-dirty-old-power-lp",
    "product_type": "",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "LP - Splatter", "price": "30.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quoted album at all -- hyphen-fallback path, with
# a trailing "12\" VINYL" format suffix that must be stripped off the album half.
_MU330_CHUMPS = {
    "title": 'MU330 - CHUMPS ON PARADE 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "mu330-chumps-on-parade-12",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "18.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: hyphen-fallback path, self-titled shorthand -- the
# album half is the literal "S/T" once the format suffix is stripped.
_MU330_ST = {
    "title": 'MU330 - S/T 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "mu330-s-t-12",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "18.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quote, no hyphen -- just artist name plus format,
# no album at all. Neither parser matches; must be skipped.
_MAGUMA_TAISHI_NO_ALBUM = {
    "title": 'MAGUMA TAISHI 7"',
    "vendor": "Asian Man Records",
    "handle": "maguma-taishi-7",
    "product_type": "CDs",
    "tags": ["7-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "7.00", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: a compilation title with no artist/album separator
# structure at all -- must be skipped, not misparsed.
_GILMAN_STREET_COMPILATION = {
    "title": "V/A GILMAN STREET RIPOFFS(A Tribute To DOOKIE)",
    "vendor": "Asian Man Records",
    "handle": "v-a-gilman-street-ripoffsa-tribute-to-dookie",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "DOOKIE BROWN VINYL", "price": "24.99", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: TEST PRESSES product_type/tag -- a one-off etching,
# not standard stock. Neither vinyl signal is present; must be gated out.
_JONAH_RAY_TEST_PRESS = {
    "title": 'JONAH RAY "You Can\'t Call Me Al" 12" etching',
    "vendor": "Asian Man Records",
    "handle": "jonah-ray-you-cant-call-me-al-12-etching",
    "product_type": "TEST PRESSES",
    "tags": ["TEST PRESS"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "14.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live shape: a standalone CD product, no vinyl signal in
# product_type or tags -- must be gated out entirely.
_KITTY_KAT_CD = {
    "title": 'KITTY KAT FAN CLUB "Dreamy Little You" CD',
    "vendor": "Asian Man Records",
    "handle": "kitty-kat-fan-club-dreamy-little-you-cd",
    "product_type": "",
    "tags": ["CDs"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: multi-variant product bundling a CD as a sibling
# variant of the vinyl -- the CD variant must be dropped, leaving one survivor
# (so no variant-title suffix on the yielded stock item's title).
_HEY_SMITH = {
    "title": 'HEY-SMITH "Life In The Sun" 12" VINYL/CD',
    "vendor": "Asian Man Records",
    "handle": "hey-smith-life-in-the-sun-lp-cd",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL", "CDs"],
    "images": [],
    "variants": [
        {"title": "COLOR VINYL", "price": "18.99", "available": True, "featured_image": None},
        {"title": "CD", "price": "8.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: multi-variant product bundling a cassette sibling
# variant -- must be dropped, even though it's the only unavailable variant
# (i.e. it's the vinyl variant that's available, not the cassette).
_CLASSICS_OF_LOVE = {
    "title": 'CLASSICS OF LOVE "S/T" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "classics-of-love-s-t-lp",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL", "cassette"],
    "images": [],
    "variants": [
        {"title": "COLOR VINYL", "price": "19.99", "available": True, "featured_image": None},
        {"title": "Cassette", "price": "5.99", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: multi-variant product bundling a promo slipmat as a
# purchasable alternative to the vinyl itself -- must be dropped.
_ALKALINE_TRIO_ST = {
    "title": 'ALKALINE TRIO "S/T" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "alkaline-trio-s-t-lp",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "A3 - RANDOM COLOR VINYL", "price": "19.99", "available": True, "featured_image": None},
        {"title": "A3 - SLIPMAT", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: single-variant product whose sole variant is titled
# "Default Title" -- confirms single-variant products are never run through the
# CD/cassette/slipmat exclusion regex (47/102 of them would wrongly fail a
# vinyl-word requirement, since "Default Title" contains no such word).
_LEMURIA = {
    "title": 'LEMURIA "Get Better" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "lemuria-get-better-lp",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "17.99", "available": True, "featured_image": None},
    ],
}


# Real confirmed-live case: 3 genuine color-variant pressings, no CD/cassette/
# slipmat sibling and no bundle-deal sizing -- the primary multi-variant case
# (alternate pressings), which every other multi-variant fixture in this file
# collapses away to a single survivor before reaching the suffix branch.
_SMOKING_POPES_STAY_DOWN = {
    "title": 'SMOKING POPES- "Stay Down" 12" VINYL',
    "vendor": "Asian Man Records",
    "handle": "smoking-popes-stay-down-12-vinyl",
    "product_type": "12-INCH VINYL",
    "tags": ["12-INCH VINYL"],
    "images": [],
    "variants": [
        {"title": "CLEAR GREEN VINYL", "price": "20.99", "available": True, "featured_image": None},
        {"title": "RANCOM COLOR", "price": "20.99", "available": True, "featured_image": None},
        {"title": "COKE BOTTLE CLEAR", "price": "20.99", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_quoted_album_no_hyphen(crawler):
    _mock_single_page([_KOREA_GIRL])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "KOREA GIRL"
    assert item["title"] == "Korea Girl"
    assert item["price"] == 25.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://asianmanrecords.com/products/korea-girl-korea-girl-12"
    assert item["cover_image_url"] == "https://cdn.shopify.com/s/files/1/0868/9755/7813/files/KoreaGirl_Cover_Lp_24.jpg"


@respx.mock
async def test_crawl_catalog_includes_product_type_vinyl_without_vinyl_tag(crawler):
    _mock_single_page([_GRUMPSTER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "GRUMPSTER"
    assert items[0]["title"] == "Honeydew"


@respx.mock
async def test_crawl_catalog_collapses_apparel_bundle_sizes_to_cheapest(crawler):
    _mock_single_page([_GRUMPSTER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 29.99


@respx.mock
async def test_crawl_catalog_bundle_collapse_skips_unavailable_cheapest_size(crawler):
    product = {**_GRUMPSTER, "variants": [
        {"title": "SMALL BUNDLE DEAL", "price": "29.99", "available": False, "featured_image": None},
        {"title": "LARGE BUNDLE DEAL", "price": "29.99", "available": True, "featured_image": None},
        {"title": "XXL BUNDLE DEAL", "price": "31.99", "available": True, "featured_image": None},
    ]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 29.99


@respx.mock
async def test_crawl_catalog_suffixes_title_for_multiple_surviving_variants(crawler):
    _mock_single_page([_SMOKING_POPES_STAY_DOWN])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 3
    titles = {item["title"] for item in items}
    assert titles == {
        "Stay Down — CLEAR GREEN VINYL",
        "Stay Down — RANCOM COLOR",
        "Stay Down — COKE BOTTLE CLEAR",
    }
    assert all(item["artist"] == "SMOKING POPES" for item in items)
    assert all(item["price"] == 20.99 for item in items)


@respx.mock
async def test_crawl_catalog_suffix_stays_stable_when_a_sibling_variant_sells_out(crawler):
    # One of the three real color variants above goes unavailable -- the two
    # still in stock must keep their disambiguating suffix (derived from the
    # product's full edition set, not from what happens to be available this
    # sync), so item_key stays stable across syncs instead of collapsing to a
    # bare "Stay Down" the moment a sibling color sells out.
    product = {**_SMOKING_POPES_STAY_DOWN, "variants": [
        {**_SMOKING_POPES_STAY_DOWN["variants"][0], "available": False},
        _SMOKING_POPES_STAY_DOWN["variants"][1],
        _SMOKING_POPES_STAY_DOWN["variants"][2],
    ]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {"Stay Down — RANCOM COLOR", "Stay Down — COKE BOTTLE CLEAR"}


@respx.mock
async def test_crawl_catalog_strips_preorder_prefix_and_asymmetric_hyphen(crawler):
    _mock_single_page([_SMOKING_POPES_PREORDER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "SMOKING POPES"
    assert items[0]["title"] == "Stay Down"
    assert items[0]["price"] == 36.99


@respx.mock
async def test_crawl_catalog_includes_amr_distro_item(crawler):
    _mock_single_page([_SKANKIN_PICKLE_DISTRO])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "SKANKIN' PICKLE"
    assert items[0]["title"] == "Green Album"


@respx.mock
async def test_crawl_catalog_strips_doubled_colon_distro_preorder_prefix(crawler):
    _mock_single_page([_AJJ_DISTRO_PREORDER])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "AJJ"
    assert items[0]["title"] == "Dirty Old Power"


@respx.mock
async def test_crawl_catalog_hyphen_fallback_strips_format_suffix(crawler):
    _mock_single_page([_MU330_CHUMPS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "MU330"
    assert items[0]["title"] == "CHUMPS ON PARADE"


@respx.mock
async def test_crawl_catalog_hyphen_fallback_self_titled(crawler):
    _mock_single_page([_MU330_ST])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "MU330"
    assert items[0]["title"] == "S/T"


@respx.mock
async def test_crawl_catalog_skips_title_with_no_album(crawler):
    _mock_single_page([_MAGUMA_TAISHI_NO_ALBUM])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_compilation_with_no_split(crawler):
    _mock_single_page([_GILMAN_STREET_COMPILATION])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_test_press(crawler):
    _mock_single_page([_JONAH_RAY_TEST_PRESS])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_non_vinyl_product(crawler):
    _mock_single_page([_KITTY_KAT_CD])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_drops_cd_sibling_variant(crawler):
    _mock_single_page([_HEY_SMITH])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Life In The Sun"
    assert items[0]["price"] == 18.99


@respx.mock
async def test_crawl_catalog_drops_cassette_sibling_variant(crawler):
    _mock_single_page([_CLASSICS_OF_LOVE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "S/T"
    assert items[0]["price"] == 19.99


@respx.mock
async def test_crawl_catalog_drops_slipmat_sibling_variant(crawler):
    _mock_single_page([_ALKALINE_TRIO_ST])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "S/T"
    assert items[0]["price"] == 19.99


@respx.mock
async def test_crawl_catalog_single_variant_default_title_no_vinyl_word_required(crawler):
    _mock_single_page([_LEMURIA])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Get Better"
    assert items[0]["price"] == 17.99


@respx.mock
async def test_crawl_catalog_skips_unavailable_non_preorder(crawler):
    product = {**_MU330_CHUMPS, "variants": [{**_MU330_CHUMPS["variants"][0], "available": False}]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_unavailable_preorder(crawler):
    # Hypothetical: no live AMR pre-order is currently unavailable (see design
    # spec's "Pre-orders and availability") -- constructed to test the
    # future-safety rule the same way jackpotrecords.py's spec documents.
    product = {**_AJJ_DISTRO_PREORDER, "variants": [{**_AJJ_DISTRO_PREORDER["variants"][0], "available": False}]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "AJJ"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_KOREA_GIRL, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Asian Man Records"
    assert Crawler.base_url == "https://asianmanrecords.com"
    assert Crawler.crawler_type == "catalog"
