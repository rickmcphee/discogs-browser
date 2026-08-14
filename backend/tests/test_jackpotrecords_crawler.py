import httpx
import respx
import pytest
from crawlers.jackpotrecords import Crawler

_PRODUCTS_URL = "https://jackpotrecords.com/collections/online-store/products.json"

_PRODUCT = {
    "title": "Deerhoof - Breakup Song",
    "vendor": "Joyful Noise Recordings",
    "handle": "deerhoof-breakup-song",
    "product_type": "Vinyl",
    "tags": ["Vinyl", "Rock"],
    "images": [{"src": "https://cdn.shopify.com/deerhoof-fallback.jpg"}],
    "variants": [
        {"title": "New", "price": "20.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: en-dash separator, spaces on both sides.
_ENDASH_PRODUCT = {
    "title": "Carn, Doug – The Best Of Doug Carn (2LP)",
    "vendor": "Soul Jazz",
    "handle": "doug-carn-best-of",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "New", "price": "34.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: hyphen glued to the artist name, space only
# after -- the whitespace-anchored regex must still split on it.
_ASYMMETRIC_SPACING_PRODUCT = {
    "title": "Electric Wizard- Black Magic Rituals & Perversions Vol. 1 (2LP, Crystal Meth Marbled Vinyl)",
    "vendor": "Spinefarm",
    "handle": "electric-wizard-black-magic-rituals",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "New", "price": "39.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no separator at all, and vendor is a reissue
# label ("Rhino"), not the artist ("Doobie Brothers") -- must not be used
# as a fallback.
_NO_SEPARATOR_PRODUCT = {
    "title": "Best of the Doobie Brothers",
    "vendor": "Rhino",
    "handle": "best-of-doobie-brothers",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "New", "price": "24.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no Vinyl tag and product_type is wrong ("CD"),
# but the title spells out "Vinyl" -- must still be included.
_UNTAGGED_VINYL_WORD_PRODUCT = {
    "title": "Deftones - Private Music (Indie Ex) (Vinyl)",
    "vendor": "Jackpot Records",
    "handle": "private-music-indie-ex",
    "product_type": "CD",
    "tags": ["CD", "Rock", "WEA"],
    "images": [],
    "variants": [
        {"title": "New", "price": "15.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: Vinyl tag present, but the title itself never
# spells out the word "vinyl" -- the tag alone must be enough.
_TAGGED_NO_VINYL_WORD_PRODUCT = {
    "title": "Wipers - Land of the Lost",
    "vendor": "Jackpot Records",
    "handle": "wipers-land-of-the-lost",
    "product_type": "Records & LPs",
    "tags": ["Vinyl", "Jackpot Records Label"],
    "images": [],
    "variants": [
        {"title": "New", "price": "19.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: neither signal -- a standalone CD with no Vinyl
# tag and no "vinyl" word anywhere in the title.
_NON_VINYL_PRODUCT = {
    "title": "Anderson.Paak - Oxnard (CD)",
    "vendor": "Jackpot Records",
    "handle": "anderson-paak-oxnard-cd",
    "product_type": "CD",
    "tags": ["CD"],
    "images": [],
    "variants": [
        {"title": "New", "price": "12.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "LP" is part of the album's own canonical title,
# not a format marker -- this must NOT be caught by a naive LP-substring
# gate, and it carries no Vinyl tag and no "vinyl" word.
_LP_IN_ALBUM_NAME_PRODUCT = {
    "title": "Eminem - The Marshall Mathers LP (CD)",
    "vendor": "Jackpot Records",
    "handle": "eminem-marshall-mathers-lp-cd",
    "product_type": "CD",
    "tags": ["CD", "Hip Hop", "UNI"],
    "images": [],
    "variants": [
        {"title": "New", "price": "13.99", "available": True, "featured_image": None},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Suzanne Vega - An Evening of New York Songs and Stories (2LP, Clear Vinyl) PRE-ORDER",
    "vendor": "Jackpot Records",
    "handle": "suzanne-vega-an-evening",
    "product_type": "Pre-Order",
    "tags": ["Pre-Order"],
    "images": [],
    "variants": [
        {"title": "New", "price": "32.99", "available": False, "featured_image": None},
    ],
}

_DEFAULT_TITLE_PRODUCT = {
    "title": "Big Lebowski - Original Soundtrack (Vinyl)",
    "vendor": "Mobile Fidelity",
    "handle": "big-lebowski-soundtrack",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/big-lebowski-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "27.99", "available": True, "featured_image": None},
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
async def test_crawl_catalog_parses_artist_and_album_from_hyphen_title(crawler):
    _mock_single_page([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Deerhoof"
    assert item["title"] == "Breakup Song"
    assert item["price"] == 20.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://jackpotrecords.com/products/deerhoof-breakup-song"
    assert item["cover_image_url"] == "https://cdn.shopify.com/deerhoof-fallback.jpg"


@respx.mock
async def test_crawl_catalog_splits_en_dash_title(crawler):
    _mock_single_page([_ENDASH_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Carn, Doug"
    assert items[0]["title"] == "The Best Of Doug Carn (2LP)"


@respx.mock
async def test_crawl_catalog_splits_asymmetric_spacing_hyphen(crawler):
    _mock_single_page([_ASYMMETRIC_SPACING_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Electric Wizard"
    assert items[0]["title"] == "Black Magic Rituals & Perversions Vol. 1 (2LP, Crystal Meth Marbled Vinyl)"


@respx.mock
async def test_crawl_catalog_skips_title_with_no_separator(crawler):
    _mock_single_page([_NO_SEPARATOR_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_untagged_product_with_vinyl_word_in_title(crawler):
    _mock_single_page([_UNTAGGED_VINYL_WORD_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Deftones"
    assert items[0]["title"] == "Private Music (Indie Ex) (Vinyl)"


@respx.mock
async def test_crawl_catalog_includes_tagged_product_without_vinyl_word(crawler):
    _mock_single_page([_TAGGED_NO_VINYL_WORD_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Wipers"
    assert items[0]["title"] == "Land of the Lost"


@respx.mock
async def test_crawl_catalog_excludes_product_with_neither_signal(crawler):
    _mock_single_page([_NON_VINYL_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_lp_in_canonical_album_name(crawler):
    _mock_single_page([_LP_IN_ALBUM_NAME_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_unavailable_preorder(crawler):
    _mock_single_page([_PREORDER_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Suzanne Vega"
    assert items[0]["price"] == 32.99


@respx.mock
async def test_crawl_catalog_skips_unavailable_non_preorder(crawler):
    product = {**_PRODUCT, "variants": [{"title": "New", "price": "20.00", "available": False, "featured_image": None}]}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_default_title_variant_yields_bare_album_title(crawler):
    _mock_single_page([_DEFAULT_TITLE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Original Soundtrack (Vinyl)"


def test_site_metadata():
    assert Crawler.site_name == "Jackpot Records"
    assert Crawler.base_url == "https://jackpotrecords.com"
    assert Crawler.crawler_type == "catalog"
