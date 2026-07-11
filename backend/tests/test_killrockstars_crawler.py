import httpx
import respx
import pytest
from crawlers.killrockstars import Crawler

_PRODUCTS_URL = "https://killrockstars.com/collections/all/products.json"

_PRODUCT = {
    "title": "All Bets Are Off",
    "vendor": "Tamar Aphek",
    "handle": "all-bets-are-off",
    "tags": ["CD", "Digital Album", "Preorder", "Tamar Aphek", "Vinyl"],
    "product_type": "Album",
    "images": [{"src": "https://cdn.shopify.com/tamaraphek-fallback.jpg"}],
    "variants": [
        {"title": "LP - Violet", "price": "25.00", "available": True},
        {"title": "LP - Black", "price": "25.00", "available": True},
        {"title": "CD", "price": "15.00", "available": True},
    ],
}

_GLUED_FORMAT_PRODUCT = {
    "title": "100 Songs (A Master Class In Songwriting)",
    "vendor": "Jad Fair",
    "handle": "100-songs-a-master-class-in-songwriting",
    "tags": ["Jad Fair", "Vinyl"],
    "product_type": "Album",
    "images": [],
    "variants": [
        {"title": "2LP", "price": "30.00", "available": True},
        {"title": "Jad Fair Bundle", "price": "40.00", "available": True},
    ],
}

# Real confirmed-live 38-variant bundle product (trimmed here to the
# variants that matter for the filter logic): pure-vinyl variants, a
# vinyl+CD bundle (must be excluded despite containing "LP"), a pure-CD
# variant, and a T-shirt-only variant.
_BUNDLE_PRODUCT = {
    "title": "9 Sad Symphonies",
    "vendor": "Kate Nash",
    "handle": "9-sad-symphonies",
    "tags": ["CD", "Kate Nash", "Vinyl"],
    "product_type": "Album",
    "images": [],
    "variants": [
        {"title": "LP - Baby Blue Vinyl / No Shirt", "price": "26.00", "available": True},
        {"title": "LP + CD Bundle / X-Small", "price": "75.00", "available": True},
        {"title": "CD / No Shirt", "price": "16.00", "available": True},
        {"title": "T-Shirt / X-Small", "price": "30.00", "available": True},
    ],
}

# Real confirmed-live cross-title bundle — neither variant contains an LP or
# inch-mark token, so this whole product yields zero items. Accepted gap.
_CROSS_TITLE_BUNDLE_PRODUCT = {
    "title": "\"All Of My Love\" - Habibi Bundle",
    "vendor": "Habibi",
    "handle": "all-of-my-love-habibi-bundle",
    "tags": ["CD", "Habibi", "Vinyl"],
    "product_type": "Album",
    "images": [],
    "variants": [
        {"title": "Habibi + Anywhere But Here - Pink Vinyl Bundle", "price": "50.00", "available": True},
        {"title": "Habibi + Anywhere But Here - CD Bundle", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live 7" release with no "Vinyl" tag at all — confirms the
# crawler must not gate on a product-level tag, only the per-variant regex.
# `available` flipped to True here (the real confirmed value is False, with
# no preorder override on this product) since this test is about the missing
# tag, not availability.
_NO_VINYL_TAG_PRODUCT = {
    "title": "Alternate Versions from Either/Or",
    "vendor": "Elliott Smith",
    "handle": "either-or-alternate-takes",
    "tags": ["Elliott Smith", "Released"],
    "product_type": "7\"",
    "images": [],
    "variants": [
        {"title": "7\" - White Vinyl", "price": "12.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_lp_color_variants(crawler):
    # This fixture's tags include "Preorder" (confirmed live) — every yielded
    # item gets the "(Pre-Order)" suffix as a result.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Tamar Aphek"
    assert items[0]["title"] == "All Bets Are Off — LP - Violet (Pre-Order)"


@respx.mock
async def test_crawl_catalog_includes_glued_2lp_excludes_bundle_containing_lp_substring(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_GLUED_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "100 Songs (A Master Class In Songwriting) — 2LP"


@respx.mock
async def test_crawl_catalog_excludes_lp_cd_bundle_and_tshirt_includes_pure_lp_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "9 Sad Symphonies — LP - Baby Blue Vinyl / No Shirt"


@respx.mock
async def test_crawl_catalog_yields_nothing_for_cross_title_bundle_with_no_lp_token(crawler):
    # Accepted gap: neither variant title contains "LP" or an inch mark, so
    # this genuinely-vinyl cross-release bundle yields zero items.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_CROSS_TITLE_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_variant_from_product_with_no_vinyl_tag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_NO_VINYL_TAG_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Elliott Smith"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Kill Rock Stars"
    assert Crawler.base_url == "https://killrockstars.com"
    assert Crawler.crawler_type == "catalog"
