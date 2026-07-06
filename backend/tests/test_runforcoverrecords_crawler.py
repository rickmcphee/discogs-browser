import httpx
import respx
import pytest
from crawlers.runforcoverrecords import Crawler

_PRODUCTS_URL = "https://runforcoverrecords.com/collections/vinyl-shop/products.json"

_PRODUCT = {
    "title": "Marbled Eye - Read The Air LP",
    "vendor": "Marbled Eye",
    "product_type": "Vinyl",
    "handle": "marbled-eye-read-the-air-lp",
    "tags": ["LP", "Vinyl", "Vinyl Shop"],
    "images": [{"src": "https://cdn.shopify.com/marbled-eye-fallback.jpg"}],
    "variants": [
        {"title": "LP - Purple Marble", "price": "24.00", "available": True},
        {"title": "LP - Black", "price": "22.00", "available": True},
        {"title": "Digital Download", "price": "8.00", "available": True},
    ],
}

_DISTRO_PRODUCT = {
    "title": "Dazy - OUTOFBODY LP",
    "vendor": "Run For Cover - Distro",
    "product_type": "Vinyl",
    "handle": "dazy-outofbody-lp",
    "tags": ["Distributed Title", "LP", "Vinyl", "Vinyl Shop"],
    "images": [],
    "variants": [
        {"title": "Distributed Title Vinyl LP", "price": "25.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_dash_split(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Marbled Eye"
    assert items[0]["title"] == "Read The Air LP — LP - Purple Marble"
    assert items[0]["price"] == 24.00
    assert items[0]["url"] == "https://runforcoverrecords.com/products/marbled-eye-read-the-air-lp"


@respx.mock
async def test_crawl_catalog_excludes_digital_download_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert all("Digital" not in item["title"] for item in items)


@respx.mock
async def test_crawl_catalog_uses_vendor_fallback_when_vendor_is_distro_placeholder(crawler):
    # Even the distro placeholder vendor doesn't break title-based parsing since the
    # dash split always wins when present.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_DISTRO_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Dazy"
    assert items[0]["title"] == "OUTOFBODY LP — Distributed Title Vinyl LP"


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_PRODUCT, "title": "Untitled Release", "variants": [_PRODUCT["variants"][0]]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Marbled Eye"
    assert items[0]["title"] == "Untitled Release — LP - Purple Marble"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant(crawler):
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
    assert Crawler.site_name == "Run For Cover"
    assert Crawler.base_url == "https://runforcoverrecords.com"
    assert Crawler.crawler_type == "catalog"
