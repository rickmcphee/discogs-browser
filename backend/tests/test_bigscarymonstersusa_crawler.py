import httpx
import respx
import pytest
from crawlers.bigscarymonstersusa import Crawler

_PRODUCTS_URL = "https://usa.bsmrocks.com/collections/vinyl/products.json"

# Real confirmed-live product: single "Default Title" variant, no format
# keyword at all — same landmine as Closed Casket Activities.
_PRODUCT = {
    "title": "Pancho - Pancho",
    "vendor": "Pancho",
    "handle": "pancho-pancho",
    "tags": ["Vinyl"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/pancho-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.99", "available": True},
    ],
}

# Real confirmed-live vendor-is-unreliable case: this exact artist ("Lakes")
# has vendor = "Lakes" correctly on one release but vendor = "Big Scary
# Monsters USA" (the store, not the artist) on this one — title parsing is
# the only consistent source. Note the en dash "–", not a hyphen, in the
# confirmed-live title of a different product; this one uses a hyphen.
_UNRELIABLE_VENDOR_PRODUCT = {
    "title": "Lakes - Slow Fade",
    "vendor": "Big Scary Monsters USA",
    "handle": "lakes-slow-fade",
    "tags": ["Vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Blue LP", "price": "25.99", "available": True},
        {"title": "CD", "price": "10.99", "available": True},
    ],
}

# Real confirmed-live title using an en dash "–" instead of a hyphen "-".
_EN_DASH_PRODUCT = {
    "title": "Alpha Male Tea Party – Reptilian Brain",
    "vendor": "Alpha Male Tea Party",
    "handle": "alpha-male-tea-party-reptilian-brain",
    "tags": ["Alpha Male Tea Party", "CD", "UK Math", "Vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Orange Galaxy LP", "price": "25.99", "available": True},
        {"title": "CD", "price": "10.99", "available": True},
    ],
}

# Real confirmed-live preorder-tagged product.
_PREORDER_PRODUCT = {
    "title": "Thank - I Have A Physical Body That Can Be Harmed",
    "vendor": "Thank",
    "handle": "thank-i-have-a-physical-body-that-can-be-harmed",
    "tags": ["Pre-order", "Thank", "Vinyl"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Purple Rain LP", "price": "24.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_single_default_title_variant_without_appending_it(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Pancho"
    assert item["title"] == "Pancho"
    assert item["price"] == 25.99
    assert item["url"] == "https://usa.bsmrocks.com/products/pancho-pancho"


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_not_unreliable_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_UNRELIABLE_VENDOR_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Lakes"
    assert items[0]["title"] == "Slow Fade — Blue LP"


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_using_en_dash(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_EN_DASH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Alpha Male Tea Party"
    assert items[0]["title"] == "Reptilian Brain — Orange Galaxy LP"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "I Have A Physical Body That Can Be Harmed — Purple Rain LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Big Scary Monsters USA"
    assert Crawler.base_url == "https://usa.bsmrocks.com"
    assert Crawler.crawler_type == "catalog"
