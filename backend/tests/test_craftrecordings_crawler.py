import httpx
import respx
import pytest
from crawlers.craftrecordings import Crawler

_PRODUCTS_URL = "https://craftrecordings.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Slide It in (Exclusive - Onyx LP)",
    "vendor": "Whitesnake",
    "handle": "whitesnake-slide-it-in-exclusive-onyx-lp",
    "tags": ["PR78866", "Rock", "Vinyl", "Whitesnake"],
    "images": [{"src": "https://cdn.shopify.com/whitesnake-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "28.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Whitesnake (Exclusive - Gold Black Ice LP)",
    "vendor": "Whitesnake",
    "handle": "whitesnake-gold-black-ice-lp",
    "tags": ["_preorder", "PRE-ORDER 9/18/2026", "Rock", "Vinyl"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "28.00", "available": True},
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
    assert item["artist"] == "Whitesnake"
    assert item["title"] == "Slide It in (Exclusive - Onyx LP)"
    assert item["price"] == 28.00
    assert item["url"] == "https://craftrecordings.com/products/whitesnake-slide-it-in-exclusive-onyx-lp"


@respx.mock
async def test_crawl_catalog_marks_preorder_tagged_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Whitesnake (Exclusive - Gold Black Ice LP) (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_standalone_cd_variant_but_includes_vinyl_sibling(crawler):
    # "Pleasure (LP / CD)" bundles a standalone "CD" variant alongside "Vinyl" —
    # the one product in this catalog that isn't single-variant-vinyl-only.
    product = {**_PRODUCT, "variants": [
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "Vinyl", "price": "24.00", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 24.00


@respx.mock
async def test_crawl_catalog_includes_shirt_size_variant_of_vinyl_bundle(crawler):
    # Vinyl+shirt bundle products use the shirt size as the variant title (the vinyl
    # format lives in the product title instead) — must not be mistaken for a
    # standalone-format variant and excluded.
    product = {**_PRODUCT, "title": "Tetragon (180g LP) + Varsity Logo Tee", "variants": [
        {"title": "Small", "price": "45.00", "available": True},
        {"title": "Medium", "price": "45.00", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


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
    assert Crawler.site_name == "Craft Recordings"
    assert Crawler.base_url == "https://craftrecordings.com"
    assert Crawler.crawler_type == "catalog"
