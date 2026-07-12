import httpx
import respx
import pytest
from crawlers.peaceville import Crawler

_PRODUCTS_URL = "https://usa-peaceville.myshopify.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "The Wolf Race - Beacon",
    "vendor": "Winterfylleth",
    "handle": "wnt0wolfrb-lp",
    "tags": ["Music", "new", "Peaceville", "Vinyl LP", "Winterfylleth"],
    "product_type": "Vinyl LP",
    "images": [{"src": "https://cdn.shopify.com/winterfylleth-fallback.jpg"}],
    "variants": [
        {"title": "Default", "price": "27.99", "available": True},
    ],
}

# Real product confirmed live: available=false + lowercase "preorder" tag,
# no date embedded (unlike Prosthetic's dated "Pre-Order MM-DD-YY" tag).
_PREORDER_PRODUCT = {
    "title": "Goh-Ka - Pink Combo (Baby Pink & Magenta) Vinyl 2xLP",
    "vendor": "Sigh",
    "handle": "sih0gohkpm-lp",
    "tags": ["Forthcoming", "Music", "new", "Peaceville", "preorder", "Sigh", "Vinyl LP"],
    "product_type": "Vinyl LP",
    "images": [],
    "variants": [
        {"title": "Default", "price": "39.99", "available": False},
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
    assert item["artist"] == "Winterfylleth"
    assert item["title"] == "The Wolf Race - Beacon"
    assert item["price"] == 27.99
    assert item["url"] == "https://usa-peaceville.myshopify.com/products/wnt0wolfrb-lp"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Goh-Ka - Pink Combo (Baby Pink & Magenta) Vinyl 2xLP (Pre-Order)"


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
    assert Crawler.site_name == "Peaceville"
    assert Crawler.base_url == "https://usa-peaceville.myshopify.com"
    assert Crawler.crawler_type == "catalog"
