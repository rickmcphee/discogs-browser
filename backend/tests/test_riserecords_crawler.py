import httpx
import respx
import pytest
from crawlers.riserecords import Crawler

_PRODUCTS_URL = "https://riserecords.com/collections/all/products.json"

# Real confirmed-live case: `product_type` is "Album" (normally a CD-ish
# value on this store) even though this is a genuine vinyl LP — product_type
# can't be trusted here. Tags are the only reliable signal.
_PRODUCT = {
    "title": "Crucial Moments - Royal Blue In Highlighter Yellow Color in Color - Vinyl LP",
    "vendor": "Bouncing Souls",
    "handle": "bnslcrmorb-lp",
    "tags": ["Bouncing Souls", "FINALFEW", "Music", "NONPRESALEVINYL", "Rise Records", "RISEPROMO", "Vinyl LP"],
    "product_type": "Album",
    "images": [{"src": "https://cdn.shopify.com/bouncingsouls-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "22.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "\"Arriba la L\" Black & White Smush Vinyl LP",
    "vendor": "Ladrones",
    "handle": "ladrall0bw-lp",
    "tags": ["Ladrones", "Music", "preorder", "Vinyl LP"],
    "product_type": "Vinyl LP",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "32.00", "available": False},
    ],
}

# Real confirmed-live apparel product mixed into the "all" collection — no
# "Music" tag at all, so the tag-based filter excludes it entirely.
_APPAREL_PRODUCT = {
    "title": "\"R\" Logo Black T-Shirt",
    "vendor": "Rise Records",
    "handle": "r-logo-black-t-shirt",
    "tags": ["Rise Records", "T-Shirt"],
    "product_type": "T-Shirt",
    "images": [],
    "variants": [
        {"title": "Small", "price": "20.00", "available": True},
        {"title": "Medium", "price": "20.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_vinyl_tagged_product_despite_misleading_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Bouncing Souls"
    assert item["title"] == "Crucial Moments - Royal Blue In Highlighter Yellow Color in Color - Vinyl LP"
    assert item["price"] == 22.00
    assert item["url"] == "https://riserecords.com/products/bnslcrmorb-lp"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "\"Arriba la L\" Black & White Smush Vinyl LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_apparel_with_no_music_tag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_APPAREL_PRODUCT]))
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
    assert Crawler.site_name == "Rise Records"
    assert Crawler.base_url == "https://riserecords.com"
    assert Crawler.crawler_type == "catalog"
