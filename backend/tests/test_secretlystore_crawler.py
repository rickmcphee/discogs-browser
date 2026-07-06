import httpx
import respx
import pytest
from crawlers.secretlystore import Crawler

_PRODUCTS_URL = "https://secretlystore.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "There Near",
    "vendor": "Dinosaur Jr.",
    "handle": "there-near",
    "tags": ["Dinosaur Jr.", "Jagjaguwar", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/dino-fallback.jpg"}],
    "variants": [
        {"title": "CD", "price": "14.99", "available": True},
        {"title": "LP", "price": "24.99", "available": True},
        {"title": "LP Purple + Gold Splash Opaque Vinyl", "price": "25.99", "available": False},
    ],
}

_PREORDER_PRODUCT = {
    "title": "There Near",
    "vendor": "Dinosaur Jr.",
    "handle": "there-near",
    "tags": ["Dinosaur Jr.", "Pre-Order", "Vinyl"],
    "images": [],
    "variants": [
        {"title": "LP Purple + Gold Splash Opaque Vinyl", "price": "25.99", "available": False},
    ],
}

_GLUED_FORMAT_PRODUCT = {
    "title": "Lost Weekend",
    "vendor": "Some Artist",
    "handle": "lost-weekend",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "2xLP (White Label)", "price": "34.99", "available": True},
        {"title": "CD", "price": "14.99", "available": True},
    ],
}

_FANPACK_PRODUCT = {
    "title": "There Near Deluxe Edition LP, 7\" and T-shirt Fanpack",
    "vendor": "Dinosaur Jr.",
    "handle": "there-near-fanpack",
    "tags": ["Dinosaur Jr."],
    "images": [],
    "variants": [
        {"title": "Small", "price": "53.99", "available": True},
        {"title": "Medium", "price": "53.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_but_includes_lp_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Dinosaur Jr."
    assert item["title"] == "There Near — LP"
    assert item["price"] == 24.99
    assert item["url"] == "https://secretlystore.com/products/there-near"


@respx.mock
async def test_crawl_catalog_includes_unavailable_vinyl_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "There Near — LP Purple + Gold Splash Opaque Vinyl (Pre-Order)"


@respx.mock
async def test_crawl_catalog_includes_glued_format_variant_excludes_cd(crawler):
    # "2xLP (White Label)" has no word boundary before "LP" (digit-glued) — plain
    # \blp\b misses it, the same gap Fat Wreck Chords needed a wider regex for.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_GLUED_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Lost Weekend — 2xLP (White Label)"


@respx.mock
async def test_crawl_catalog_excludes_apparel_only_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_FANPACK_PRODUCT]))
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
    assert Crawler.site_name == "Secretly Store"
    assert Crawler.base_url == "https://secretlystore.com"
    assert Crawler.crawler_type == "catalog"
