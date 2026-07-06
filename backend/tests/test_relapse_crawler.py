import httpx
import respx
import pytest
from crawlers.relapse import Crawler

_PRODUCTS_URL = "https://www.relapse.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": 'Ceremony "In The Spirit World Now" 12"',
    "vendor": "Ceremony",
    "handle": "ceremony-in-the-spirit-world-now-12",
    "tags": ["bf23", "wholesale"],
    "images": [{"src": "https://cdn.shopify.com/ceremony-fallback.jpg"}],
    "variants": [
        {"title": "Orange Krush Cloudy Effect", "price": "21.99", "available": True},
        {"title": "Mustard *LTD to 496*", "price": "21.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": 'Devourment "Pious Impiety" 7"',
    "vendor": "Devourment",
    "handle": "devourment-pious-impiety-7",
    "tags": ["preorder"],
    "images": [],
    "variants": [
        {"title": "Electric Blue", "price": "8.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_one_item_per_variant_using_vendor_as_artist(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Ceremony"
    assert items[0]["title"] == 'Ceremony "In The Spirit World Now" 12"'
    assert items[0]["price"] == 21.99
    assert items[0]["url"] == "https://www.relapse.com/products/ceremony-in-the-spirit-world-now-12"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == 'Devourment "Pious Impiety" 7" (Pre-Order)'


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
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
    assert Crawler.site_name == "Relapse"
    assert Crawler.base_url == "https://www.relapse.com"
    assert Crawler.crawler_type == "catalog"
