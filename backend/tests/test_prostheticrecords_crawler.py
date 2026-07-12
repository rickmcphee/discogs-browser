import httpx
import respx
import pytest
from crawlers.prostheticrecords import Crawler

_PRODUCTS_URL = "https://shop.prostheticrecords.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "homewrecker. - Never Knowing When, But Knowing This Will End on Black Vinyl",
    "vendor": "homewrecker.",
    "handle": "homewrecker-never-knowing-when-but-knowing-this-will-end-on-black-vinyl",
    "tags": ["Aged-15+", "FCATNEW", "featured", "homewrecker.", "media", "music", "New Arrivals", "Vinyl"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/homewrecker-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "28.98", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Fires in the Distance - Air Not Meant For Us",
    "vendor": "Fires in the Distance",
    "handle": "fires-in-the-distance-air-not-meant-for-us",
    "tags": ["Pre-Order 08-28-26", "Pre-Orders", "Vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "26.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_from_title(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "homewrecker."
    assert item["title"] == "Never Knowing When, But Knowing This Will End on Black Vinyl"
    assert item["price"] == 28.98
    assert item["url"] == "https://shop.prostheticrecords.com/products/homewrecker-never-knowing-when-but-knowing-this-will-end-on-black-vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Air Not Meant For Us (Pre-Order)"


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
    assert Crawler.site_name == "Prosthetic Records"
    assert Crawler.base_url == "https://shop.prostheticrecords.com"
    assert Crawler.crawler_type == "catalog"
