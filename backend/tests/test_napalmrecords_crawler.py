import httpx
import respx
import pytest
from crawlers.napalmrecords import Crawler

_PRODUCTS_URL = "https://napalmrecords.us/collections/vinyl/products.json"

_PRODUCT = {
    "title": 'DevilDriver "Strike and Kill (Translucent Turquoise White Black Splatter vinyl)" 12"',
    "vendor": "DevilDriver",
    "handle": "devildriver-strike-and-kill-12",
    "tags": ["preorder"],
    "images": [{"src": "https://cdn.shopify.com/devildriver-fallback.jpg"}],
    "variants": [
        {"title": "Translucent Turquoise White Black Splatter", "price": "42.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "DevilDriver"
    assert "(Pre-Order)" in item["title"]
    assert item["price"] == 42.99
    assert item["url"] == "https://napalmrecords.us/products/devildriver-strike-and-kill-12"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "tags": []}
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
    assert Crawler.site_name == "Napalm Records"
    assert Crawler.base_url == "https://napalmrecords.us"
    assert Crawler.crawler_type == "catalog"
