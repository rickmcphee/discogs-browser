import httpx
import respx
import pytest
from crawlers.fatwreck import Crawler

_PRODUCTS_URL = "https://fatwreck.com/collections/vinyl-1/products.json"

_PRODUCT = {
    "title": "12 Song Program",
    "vendor": "Tony Sly",
    "handle": "tslyf751bl-lp",
    "tags": ["Fat Wreck Chords", "Music", "new"],
    "images": [{"src": "https://cdn.shopify.com/tonysly-fallback.png"}],
    "variants": [
        {"title": "CD", "price": "10.00", "available": True},
        {"title": "LP", "price": "23.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/tonysly-lp.png"}},
    ],
}

_GLUED_FORMAT_PRODUCT = {
    "title": "Wood/Water",
    "vendor": "The Real McKenzies",
    "handle": "woodwater-2xlp",
    "tags": ["Fat Wreck Chords"],
    "images": [{"src": "https://cdn.shopify.com/woodwater-fallback.png"}],
    "variants": [
        {"title": "2xLP", "price": "24.99", "available": True},
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "Cassette", "price": "8.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "A to H",
    "vendor": "Common Rider",
    "handle": "cmnrdf000bl-lp",
    "tags": ["Fat Wreck Chords", "preorder"],
    "images": [{"src": "https://cdn.shopify.com/commonrider-fallback.png"}],
    "variants": [
        {"title": "LP", "price": "20.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_available_vinyl_variant_only(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Tony Sly"
    assert item["title"] == "12 Song Program — LP"
    assert item["format"] == "Vinyl"
    assert item["price"] == 23.00
    assert item["currency"] == "USD"
    assert item["url"] == "https://fatwreck.com/products/tslyf751bl-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/tonysly-lp.png"


@respx.mock
async def test_crawl_catalog_includes_glued_format_variant_excludes_cd_and_cassette(crawler):
    # "2xLP" has no word boundary before "LP" (digit/word-char glued on) — the exact
    # gap neither Nuclear Blast's `\bvinyl\b|\blp\b` nor Rev HQ's wider regex covers.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_GLUED_FORMAT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Wood/Water — 2xLP"
    assert items[0]["artist"] == "The Real McKenzies"


@respx.mock
async def test_crawl_catalog_includes_unavailable_vinyl_for_preorder_products(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "A to H — LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][1], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_if_present(crawler):
    product = {**_PRODUCT, "vendor": "NAILS", "title": "NAILS - Every Bridge Burning", "handle": "nails"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Every Bridge Burning — LP"


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Fat Wreck Chords"
    assert Crawler.base_url == "https://fatwreck.com"
    assert Crawler.crawler_type == "catalog"
