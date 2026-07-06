import httpx
import respx
import pytest
from crawlers.deathwishinc import Crawler

_PRODUCTS_URL = "https://deathwishinc.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": '1 Mile North "Awakened By Decay"',
    "vendor": "Robotic Empire",
    "handle": "1-mile-north-awakened-by-decay",
    "tags": ["12\"", "2XLP", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/1mn-fallback.jpg"}],
    "variants": [
        {"title": "LP - Black", "price": "19.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": '2 Mile South "Rising Decay"',
    "vendor": "Robotic Empire",
    "handle": "2-mile-south-rising-decay",
    "tags": ["12\"", "Vinyl", "Pre-Order"],
    "images": [],
    "variants": [
        {"title": "LP - Red", "price": "21.99", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_quoted_title_not_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "1 Mile North"
    assert item["title"] == "Awakened By Decay — LP - Black"
    assert item["price"] == 19.99
    assert item["url"] == "https://deathwishinc.com/products/1-mile-north-awakened-by-decay"
    assert item["cover_image_url"] == "https://cdn.shopify.com/1mn-fallback.jpg"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Rising Decay — LP - Red (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_parses_artist_from_curly_quoted_title(crawler):
    product = {**_PRODUCT, "title": "Attempt Survivors “Educated Hips”"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Attempt Survivors"
    assert items[0]["title"] == "Educated Hips — LP - Black"


@respx.mock
async def test_crawl_catalog_parses_artist_from_quoted_title_with_trailing_format_text(crawler):
    # "...Double LP" trails the closing quote — the album match must not require the
    # closing quote to end the string.
    product = {**_PRODUCT, "title": 'All Leather "Amateur Surgery On Half-Hog Abortion Island" Double LP'}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "All Leather"
    assert items[0]["title"] == "Amateur Surgery On Half-Hog Abortion Island — LP - Black"


@respx.mock
async def test_crawl_catalog_excludes_cd_and_cassette_only_variants(crawler):
    # Deathwish's "vinyl" collection also carries pure CD/Cassette variants on the
    # same product — unlike the single-format label stores, this needs a filter.
    product = {**_PRODUCT, "variants": [
        {"title": "LP - Black", "price": "19.99", "available": True},
        {"title": "CD", "price": "9.99", "available": True},
        {"title": "Cassette - Black", "price": "7.99", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Awakened By Decay — LP - Black"


@respx.mock
async def test_crawl_catalog_includes_glued_format_variant(crawler):
    product = {**_PRODUCT, "variants": [{"title": "2xLP - Black", "price": "29.99", "available": True}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Awakened By Decay — 2xLP - Black"


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_quotes(crawler):
    product = {**_PRODUCT, "title": "Various Artists Sampler 2026", "vendor": "Robotic Empire"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Robotic Empire"
    assert items[0]["title"] == "Various Artists Sampler 2026 — LP - Black"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Deathwish Inc"
    assert Crawler.base_url == "https://deathwishinc.com"
    assert Crawler.crawler_type == "catalog"
