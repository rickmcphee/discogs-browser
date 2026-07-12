import httpx
import respx
import pytest
from crawlers.subpopmegamart import Crawler

_PRODUCTS_URL = "https://megamart.subpop.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Hell + It's Dead",
    "vendor": "Girl and Girl",
    "handle": "girl-and-girl_hell-its-dead",
    "tags": ["format-digital", "label-sub-pop", "music"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Digital", "price": "2.00", "available": True},
    ],
}

_MULTI_FORMAT_PRODUCT = {
    "title": "Free Electricity",
    "vendor": "The Go",
    "handle": "the-go_free-electricity",
    "tags": ["format-cd", "format-digital", "format-loser-color-lp", "label-sub-pop", "music", "pre-order", "the-go"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/thego-fallback.jpg"}],
    "variants": [
        {"title": "Loser (color) LP", "price": "26.00", "available": True},
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "Digital", "price": "10.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_nothing_for_digital_only_release(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_cd_and_digital_includes_lp_variant(crawler):
    # This product's "pre-order" tag is irrelevant here — confirmed live,
    # /collections/vinyl silently excludes pre-order titles entirely, so a
    # real crawl would never see this product's tag at all. No pre-order
    # override exists in this crawler as a result (accepted scope decision).
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "The Go"
    assert items[0]["title"] == "Free Electricity — Loser (color) LP"
    assert items[0]["price"] == 26.00


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant(crawler):
    product = {**_MULTI_FORMAT_PRODUCT, "variants": [{**_MULTI_FORMAT_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_MULTI_FORMAT_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Sub Pop Mega Mart"
    assert Crawler.base_url == "https://megamart.subpop.com"
    assert Crawler.crawler_type == "catalog"
