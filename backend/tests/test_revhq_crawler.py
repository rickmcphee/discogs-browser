import httpx
import respx
import pytest
from crawlers.revhq import Crawler

_PRODUCTS_URL = "https://revhq.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": '100 Demons "Embrace The Black Light"',
    "vendor": "Closed Casket Activities",
    "handle": "100-demons-embrace-the-black-light",
    "tags": ["100 Demons", "hardcore", "Music", "punk", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/demons-fallback.png"}],
    "variants": [
        {"title": "LP - Color Vinyl", "price": "25.60", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/demons-lp.png"}},
        {"title": "CD", "price": "12.30", "available": True},
    ],
}

_SEVEN_INCH_PRODUCT = {
    "title": '50 Lions "Former Glory b/w Normality"',
    "vendor": "Six Feet Under Records",
    "handle": "50lions-formergloryb-wnormality-7",
    "tags": ["50 Lions", "7\"", "hardcore", "Music", "punk", "Vinyl"],
    "images": [],
    "variants": [
        {"title": "7\"", "price": "6.35", "available": True},
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
    assert item["artist"] == "100 Demons"
    assert item["title"] == "Embrace The Black Light — LP - Color Vinyl"
    assert item["price"] == 25.60
    assert item["url"] == "https://revhq.com/products/100-demons-embrace-the-black-light"
    assert item["cover_image_url"] == "https://cdn.shopify.com/demons-lp.png"


@respx.mock
async def test_crawl_catalog_excludes_cd_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"].endswith("LP - Color Vinyl")


@respx.mock
async def test_crawl_catalog_includes_bare_inch_size_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SEVEN_INCH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "50 Lions"
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variants_no_preorder_override(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_quotes(crawler):
    product = {**_PRODUCT, "title": "Various Artists Sampler 2026", "vendor": "Trust Records"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Trust Records"
    assert items[0]["title"] == "Various Artists Sampler 2026 — LP - Color Vinyl"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Rev HQ"
    assert Crawler.base_url == "https://revhq.com"
    assert Crawler.crawler_type == "catalog"
