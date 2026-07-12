import httpx
import respx
import pytest
from crawlers.fatherdaughterrecords import Crawler

_PRODUCTS_URL = "https://fatherdaughterrecords.com/collections/vinyl/products.json"

# Real confirmed-live product: `vendor` is a label placeholder, never the
# artist. Also confirmed live: a preorder tag "Pre-order".
_PREORDER_PRODUCT = {
    "title": "Attic Abasement - Moonlight Passes On",
    "vendor": "Father/Daughter Records",
    "handle": "attic-abasement-moonlight-passes-on",
    "tags": ["Attic Abasement", "CD", "Digital download", "LP", "Merch", "Pre-order"],
    "product_type": "Music & Sound Recordings",
    "images": [{"src": "https://cdn.shopify.com/atticabasement-fallback.jpg"}],
    "variants": [
        {"title": "Vinyl", "price": "22.00", "available": True},
        {"title": "CD", "price": "10.00", "available": True},
        {"title": "Digital", "price": "1.00", "available": True},
    ],
}

# Real confirmed-live grab-bag/bundle product: empty product_type, collapses
# to a single non-descriptive "Default Title" variant — excluded entirely.
_BUNDLE_PRODUCT = {
    "title": "Anna McClellan - 3xLP Bundle",
    "vendor": "Father/Daughter Records",
    "handle": "anna-mcclellan-3xlp-bundle",
    "tags": ["Anna McClellan", "LP"],
    "product_type": "",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "58.00", "available": True},
    ],
}

# Real confirmed-live title with no dash — falls back to vendor.
_MYSTERY_PRODUCT = {
    "title": "Mystery LP",
    "vendor": "Father/Daughter Records",
    "handle": "mystery-lp",
    "tags": ["LP"],
    "product_type": "Music & Sound Recordings",
    "images": [],
    "variants": [
        {"title": "1 Mystery LP", "price": "7.00", "available": True},
        {"title": "2 Mystery LPs", "price": "12.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_excludes_cd_and_digital(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Attic Abasement"
    assert item["title"] == "Moonlight Passes On — Vinyl (Pre-Order)"
    assert item["price"] == 22.00


@respx.mock
async def test_crawl_catalog_excludes_bundle_product_with_empty_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_for_mystery_grab_bag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MYSTERY_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Father/Daughter Records"
    assert items[0]["title"] == "Mystery LP — 1 Mystery LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PREORDER_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Father/Daughter Records"
    assert Crawler.base_url == "https://fatherdaughterrecords.com"
    assert Crawler.crawler_type == "catalog"
