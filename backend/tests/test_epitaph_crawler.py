import httpx
import respx
import pytest
from crawlers.epitaph import Crawler

_PRODUCTS_URL = "https://www.epitaph.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "No Devolución 2xLP (Black)",
    "vendor": "Thursday",
    "handle": "no-devolucion-2xlp-black",
    "tags": ["12in Vinyl", "E00028", "Media Mail"],
    "images": [{"src": "https://cdn.shopify.com/thursday-fallback.png"}],
    "variants": [
        {"title": "Default Title", "price": "34.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "No Devolución 2xLP (Snowpiercer Torrent)",
    "vendor": "Thursday",
    "handle": "no-devolucion-2xlp-snowpiercer-torrent",
    "tags": ["12in Vinyl", "Exclusive", "limited", "Out of stock", "pre-order"],
    "images": [{"src": "https://cdn.shopify.com/thursday-torrent.png"}],
    "variants": [
        {"title": "Default Title", "price": "39.99", "available": False},
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
    assert item["artist"] == "Thursday"
    assert item["title"] == "No Devolución 2xLP (Black)"
    assert item["format"] == "Vinyl"
    assert item["price"] == 34.99
    assert item["url"] == "https://www.epitaph.com/products/no-devolucion-2xlp-black"
    assert item["cover_image_url"] == "https://cdn.shopify.com/thursday-fallback.png"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "No Devolución 2xLP (Snowpiercer Torrent) (Pre-Order)"


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
    assert Crawler.site_name == "Epitaph"
    assert Crawler.base_url == "https://www.epitaph.com"
    assert Crawler.crawler_type == "catalog"
