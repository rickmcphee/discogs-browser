import httpx
import respx
import pytest
from crawlers.fatpossum import Crawler

_PRODUCTS_URL = "https://fatpossum.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "A Ass Pocket of Whiskey",
    "vendor": "R.L. Burnside",
    "handle": "a-ass-pocket-of-whiskey",
    "tags": ["1990s", "Fat Possum", "g::Blues", "View Collection", "Vinyl"],
    "product_type": "Releases",
    "images": [{"src": "https://cdn.shopify.com/rlburnside-fallback.jpg"}],
    "variants": [
        {"title": "Compact Disc", "price": "13.00", "available": True},
        {"title": "Vinyl", "price": "23.00", "available": True},
    ],
}

_MULTI_VARIANT_PRODUCT = {
    "title": "Active Listening: Night on Earth",
    "vendor": "Empath",
    "handle": "active-listening-night-on-earth",
    "tags": ["2010s", "Double Vinyl", "Fat Possum", "g::Rock", "View Collection", "Vinyl"],
    "product_type": "Releases",
    "images": [],
    "variants": [
        {"title": "Standard Vinyl", "price": "21.00", "available": True},
        {"title": "Deluxe Vinyl", "price": "22.00", "available": False},
        {"title": "Cassette", "price": "9.00", "available": True},
        {"title": "Compact Disc", "price": "12.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_vinyl_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "R.L. Burnside"
    assert item["title"] == "A Ass Pocket of Whiskey — Vinyl"
    assert item["price"] == 23.00
    assert item["url"] == "https://fatpossum.com/products/a-ass-pocket-of-whiskey"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_deluxe_vinyl_includes_standard(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_VARIANT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Active Listening: Night on Earth — Standard Vinyl"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Fat Possum"
    assert Crawler.base_url == "https://fatpossum.com"
    assert Crawler.crawler_type == "catalog"
