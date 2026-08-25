import httpx
import respx
import pytest
from crawlers.turntablelab import Crawler

_PRODUCTS_URL = "https://www.turntablelab.com/collections/vinyl-lps-alpha/products.json"

_PRODUCT = {
    "title": "Deerhoof: Breakup Song",
    "vendor": "Deerhoof",
    "handle": "deerhoof-breakup-song",
    "tags": ["Indie Rock Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/deerhoof-fallback.jpg"}],
    "variants": [
        {"title": "Vinyl LP", "price": "20.00", "available": True},
    ],
}

# Real confirmed-live case: vendor is abbreviated ("Blue Note") relative to
# the artist name embedded in the title ("Blue Note Records") -- parsing off
# the title, not the vendor field, is the only source that's correct here.
_VENDOR_MISMATCH_PRODUCT = {
    "title": "Blue Note Records: Droppin' Science - Greatest Samples From The Blue Note Lab Vinyl 2LP",
    "vendor": "Blue Note",
    "handle": "blue-note-droppin-science",
    "tags": [],
    "images": [],
    "variants": [
        {"title": "Vinyl 2LP", "price": "34.95", "available": True},
    ],
}

# Real confirmed-live case: a space before the colon separator.
_SPACE_BEFORE_COLON_PRODUCT = {
    "title": "Warren G : Regulate... G Funk Era - 20th Anniversary Edition Vinyl 2LP",
    "vendor": "Warren G",
    "handle": "warren-g-regulate",
    "tags": [],
    "images": [],
    "variants": [
        {"title": "Vinyl 2LP", "price": "29.95", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Hemlocke Springs: The Apple Tree Under The Sea (Colored Vinyl) Vinyl LP - PRE-ORDER",
    "vendor": "hemlocke springs",
    "handle": "hemlocke-springs-apple-tree",
    "tags": ["pre-order", "limited edition"],
    "images": [],
    "variants": [
        {"title": "Vinyl LP", "price": "26.95", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_and_album_from_colon_title(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Deerhoof"
    assert item["title"] == "Breakup Song"
    assert item["price"] == 20.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://www.turntablelab.com/products/deerhoof-breakup-song"


@respx.mock
async def test_crawl_catalog_prefers_title_artist_over_abbreviated_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_VENDOR_MISMATCH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Blue Note Records"
    assert items[0]["title"] == "Droppin' Science - Greatest Samples From The Blue Note Lab Vinyl 2LP"


@respx.mock
async def test_crawl_catalog_handles_space_before_colon(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_SPACE_BEFORE_COLON_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Warren G"
    assert items[0]["title"] == "Regulate... G Funk Era - 20th Anniversary Edition Vinyl 2LP"


@respx.mock
async def test_crawl_catalog_includes_unavailable_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Hemlocke Springs"
    assert items[0]["price"] == 26.95


@respx.mock
async def test_crawl_catalog_skips_unavailable_non_preorder(crawler):
    product = {**_PRODUCT, "variants": [{"title": "Vinyl LP", "price": "20.00", "available": False}]}
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


@respx.mock
async def test_crawl_catalog_disambiguates_condition_graded_variants(crawler):
    # Real confirmed-live shape: a standard copy alongside a cheaper
    # cosmetically-graded copy of the same pressing, as two variants.
    product = {
        **_PRODUCT,
        "variants": [
            {"title": "Vinyl LP", "price": "20.00", "available": True},
            {"title": "Seam Split Vinyl LP", "price": "15.00", "available": True},
        ],
    }
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["title"] == "Breakup Song — Vinyl LP"
    assert items[0]["price"] == 20.00
    assert items[1]["title"] == "Breakup Song — Seam Split Vinyl LP"
    assert items[1]["price"] == 15.00


def test_site_metadata():
    assert Crawler.site_name == "Turntable Lab"
    assert Crawler.base_url == "https://www.turntablelab.com"
    assert Crawler.crawler_type == "catalog"
