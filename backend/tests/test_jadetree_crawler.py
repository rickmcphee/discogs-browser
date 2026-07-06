import httpx
import respx
import pytest
from crawlers.jadetree import Crawler

_PRODUCTS_URL = "https://jadetree.store/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Nothing Feels Good LP (Blue/White Galaxy)",
    "vendor": "The Promise Ring",
    "handle": "nothing-feels-good-lp-blue-white-galaxy",
    "tags": ["12in Vinyl", "Featured", "J00000", "limited", "Media Mail"],
    "images": [{"src": "https://cdn.shopify.com/promisering-fallback.png"}],
    "variants": [
        {"title": "Default Title", "price": "26.99", "available": True},
    ],
}

_PREFIXED_PRODUCT = {
    "title": "Joan Of Arc - A Portable Model Of LP (Black 180)",
    "vendor": "Joan Of Arc",
    "handle": "joan-of-arc-a-portable-model-of-lp-black-180",
    "tags": ["12in Vinyl", "Media Mail"],
    "images": [{"src": "https://cdn.shopify.com/joanofarc-fallback.png"}],
    "variants": [
        {"title": "Default Title", "price": "22.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_using_title_as_is(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "The Promise Ring"
    assert item["title"] == "Nothing Feels Good LP (Blue/White Galaxy)"
    assert item["format"] == "Vinyl"
    assert item["price"] == 26.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://jadetree.store/products/nothing-feels-good-lp-blue-white-galaxy"
    assert item["cover_image_url"] == "https://cdn.shopify.com/promisering-fallback.png"


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_when_present(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREFIXED_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []  # unavailable and no pre-order override — see next test for the positive case


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_when_available(crawler):
    product = {**_PREFIXED_PRODUCT, "variants": [{**_PREFIXED_PRODUCT["variants"][0], "available": True}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "A Portable Model Of LP (Black 180)"
    assert items[0]["artist"] == "Joan Of Arc"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_no_preorder_override(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Jade Tree Records"
    assert Crawler.base_url == "https://jadetree.store"
    assert Crawler.crawler_type == "catalog"
