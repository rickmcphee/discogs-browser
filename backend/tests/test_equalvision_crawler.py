import httpx
import respx
import pytest
from crawlers.equalvision import Crawler

_PRODUCTS_URL = "https://equalvision.com/collections/equal-vision-records/products.json"

_VINYL_PRODUCT = {
    "title": "Lusitania - Blue W/ Green & White Splatter 2xLP",
    "vendor": "Fairweather",
    "product_type": "Vinyl LP",
    "handle": "fwr0lusisw-lp",
    "tags": ["Equal Vision Records", "Fairweather", "new"],
    "images": [{"src": "https://cdn.shopify.com/lusitania-fallback.jpg"}],
    "variants": [
        {"title": "Default", "price": "45.00", "available": True},
    ],
}

_CD_PRODUCT = {
    "title": "Culture Scars - CD",
    "vendor": "Hail The Sun",
    "product_type": "CD",
    "handle": "culture-scars-cd",
    "tags": ["CD", "Hail The Sun"],
    "images": [],
    "variants": [
        {"title": "Default", "price": "10.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "New Album - Splatter LP",
    "vendor": "Some Band",
    "product_type": "Vinyl LP",
    "handle": "new-album-splatter-lp",
    "tags": ["preorder"],
    "images": [],
    "variants": [
        {"title": "Default", "price": "30.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_vinyl_product_using_vendor_as_artist(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_VINYL_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Fairweather"
    assert item["title"] == "Lusitania - Blue W/ Green & White Splatter 2xLP"
    assert item["price"] == 45.00
    assert item["url"] == "https://equalvision.com/products/fwr0lusisw-lp"


@respx.mock
async def test_crawl_catalog_excludes_non_vinyl_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_CD_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "New Album - Splatter LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_VINYL_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Equal Vision"
    assert Crawler.base_url == "https://equalvision.com"
    assert Crawler.crawler_type == "catalog"
