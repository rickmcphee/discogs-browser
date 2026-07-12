import httpx
import respx
import pytest
from crawlers.saddlecreek import Crawler

_PRODUCTS_URL = "https://saddle-creek.com/collections/all/products.json"

_PRODUCT = {
    "title": "Adam Cayton-Holland Performs His Signature Bits",
    "vendor": "Adam Cayton-Holland",
    "handle": "adam-cayton-holland-performs-his-signature-bits",
    "tags": ["colored vinyl", "LP", "meta-related-collection-adam-cayton-holland", "vinyl"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/adamcaytonholland-fallback.jpg"}],
    "variants": [
        {"title": "Colored LP", "price": "18.99", "available": True},
    ],
}

# Real confirmed-live product mixing a glued "2xLP" vinyl variant with
# CD/MP3-only variants, one of them unavailable (no preorder signal exists
# on this store at all — confirmed after a full 337-product scan).
_MULTI_VARIANT_PRODUCT = {
    "title": "Album Of The Year",
    "vendor": "The Good Life",
    "handle": "album-of-the-year",
    "tags": ["artist:The Good Life", "CD", "colored vinyl", "LP", "meta-related-collection-the-good-life", "MP3", "vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "GOLD NUGGET 2xLP + MP3", "price": "29.99", "available": False},
        {"title": "CD + MP3", "price": "9.99", "available": True},
        {"title": "MP3", "price": "8.99", "available": True},
    ],
}

# Real confirmed-live apparel product mixed into the "all" collection.
_APPAREL_PRODUCT = {
    "title": "Algernon Cadwallader Tee",
    "vendor": "Algernon Cadwallader",
    "handle": "algernon-cadwallader-tee",
    "tags": ["Algernon Cadwallader", "t-shirt"],
    "product_type": "Apparel",
    "images": [],
    "variants": [
        {"title": "Small", "price": "20.00", "available": True},
    ],
}

# Real confirmed-live release with no vinyl edition at all — CD/MP3 only.
_NO_VINYL_EDITION_PRODUCT = {
    "title": "11:11",
    "vendor": "Maria Taylor",
    "handle": "11-11-2016",
    "tags": ["artist:Maria Taylor", "CD", "meta-related-collection-maria-taylor"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "CD + MP3", "price": "9.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_using_vendor_as_artist(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Adam Cayton-Holland"
    assert item["title"] == "Adam Cayton-Holland Performs His Signature Bits — Colored LP"
    assert item["price"] == 18.99
    assert item["url"] == "https://saddle-creek.com/products/adam-cayton-holland-performs-his-signature-bits"


@respx.mock
async def test_crawl_catalog_includes_glued_2xlp_excludes_cd_and_mp3(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_VARIANT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []  # the only vinyl variant is unavailable, and there's no preorder override on this store


@respx.mock
async def test_crawl_catalog_excludes_apparel_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_APPAREL_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_yields_nothing_for_release_with_no_vinyl_edition(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_NO_VINYL_EDITION_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Saddle Creek"
    assert Crawler.base_url == "https://saddle-creek.com"
    assert Crawler.crawler_type == "catalog"
