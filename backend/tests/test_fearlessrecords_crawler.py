import httpx
import respx
import pytest
from crawlers.fearlessrecords import Crawler

_PRODUCTS_URL = "https://store.fearlessrecords.com/collections/vinyl/products.json"

# Real confirmed-live product: preorder tag "preorder" plus a dated companion
# tag "PRE-ORDER M/D/YYYY" (no hyphen in the generic tag — Flatspot's is
# hyphenated "pre-order", a different spelling on a different store).
_PREORDER_PRODUCT = {
    "title": "\"Pure Ecstasy\" Black Vinyl",
    "vendor": "Beartooth",
    "handle": "beartooth-pure-ecstasy-black-vinyl",
    "tags": ["Beartooth", "PR77954", "PRE-ORDER 8/28/2026", "preorder", "Vinyl"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/beartooth-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

_PRODUCT = {
    "title": "\"god forbid a girl spits out her feelings!\" Iridescent Gold Vinyl",
    "vendor": "LOLO",
    "handle": "lolo-god-forbid-a-girl-spits-out-her-feelings-iridescent-gold-vinyl",
    "tags": ["LOLO", "Vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live unavailable, non-preorder product.
_SOLD_OUT_PRODUCT = {
    "title": "\"Happier Now\" SIGNED Ruby Vinyl",
    "vendor": "Movements",
    "handle": "movements-happier-now-signed-ruby-vinyl",
    "tags": ["Movements", "Vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "40.00", "available": False},
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
    assert item["artist"] == "LOLO"
    assert item["title"] == "\"god forbid a girl spits out her feelings!\" Iridescent Gold Vinyl"
    assert item["price"] == 25.00
    assert item["url"] == "https://store.fearlessrecords.com/products/lolo-god-forbid-a-girl-spits-out-her-feelings-iridescent-gold-vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "\"Pure Ecstasy\" Black Vinyl (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SOLD_OUT_PRODUCT]))
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
    assert Crawler.site_name == "Fearless Records"
    assert Crawler.base_url == "https://store.fearlessrecords.com"
    assert Crawler.crawler_type == "catalog"
