import httpx
import respx
import pytest
from crawlers.newburycomics import Crawler

_PRODUCTS_URL = "https://www.newburycomics.com/collections/vinyl/products.json"

# Real confirmed-live product shape: vendor is the artist directly, title is
# the bare album title with no artist prefix, exactly one "Default Title"
# variant.
_PRODUCT = {
    "title": "#1 Record LP (180g)",
    "vendor": "Big Star",
    "handle": "big_star-number_1_record_lp_180g",
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/big-star-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "37.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_maps_vendor_to_artist_and_title_unchanged(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Big Star"
    assert items[0]["title"] == "#1 Record LP (180g)"
    assert items[0]["price"] == 37.99
    assert items[0]["format"] == "Vinyl"
    assert items[0]["currency"] == "USD"
    assert items[0]["url"] == "https://www.newburycomics.com/products/big_star-number_1_record_lp_180g"
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/big-star-fallback.jpg"


@respx.mock
async def test_crawl_catalog_skips_unavailable_variant(crawler):
    product = {**_PRODUCT, "variants": [{"title": "Default Title", "price": "37.99", "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_passes_through_various_artists_vendor_unchanged(crawler):
    product = {**_PRODUCT, "vendor": "Various Artists", "title": "Guardians Of The Galaxy: Awesome Mix Vol. 1 LP"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Various Artists"
    assert items[0]["title"] == "Guardians Of The Galaxy: Awesome Mix Vol. 1 LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_empty_collection_yields_nothing(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Newbury Comics"
    assert Crawler.base_url == "https://www.newburycomics.com"
    assert Crawler.crawler_type == "catalog"
