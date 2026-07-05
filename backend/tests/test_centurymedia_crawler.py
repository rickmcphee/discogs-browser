import httpx
import respx
import pytest
from crawlers.centurymedia import Crawler

_PRODUCTS_URL = "https://centurymedia.store/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Distant - Into Despair - Blue EcoMix LP",
    "vendor": "Distant",
    "handle": "distant-into-despair-blue-ecomix-lp",
    "product_type": "12\"",
    "tags": ["cm", "distant", "preorder", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/distant-fallback.png"}],
    "variants": [
        {"title": "Blue EcoMix", "price": "24.98", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/distant-blue.png"}},
    ],
}

_RELEASED_PRODUCT = {
    "title": "Blood Incantation - All Gates Open",
    "vendor": "Blood Incantation",
    "handle": "blood-incantation-all-gates-open",
    "product_type": "2x12\"/DVD",
    "tags": ["blood incantation", "cm", "exclusive", "new release", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/bi-fallback.png"}],
    "variants": [
        {"title": "Transparent Sea Blue Ghost", "price": "49.98", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_for_each_variant(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Distant"
    assert item["title"] == "Into Despair - Blue EcoMix LP (Pre-Order)"
    assert item["format"] == "Vinyl"
    assert item["price"] == 24.98
    assert item["currency"] == "USD"
    assert item["url"] == "https://centurymedia.store/products/distant-into-despair-blue-ecomix-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/distant-blue.png"


@respx.mock
async def test_crawl_catalog_no_preorder_suffix_for_released_items(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_RELEASED_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "All Gates Open"
    assert "(Pre-Order)" not in items[0]["title"]


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_for_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_RELEASED_PRODUCT, "variants": [{**_RELEASED_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_full_title_when_vendor_does_not_prefix_match(crawler):
    # "Hackett & Rothery - The Roaring Waves - LP" is credited to two artists but the
    # `vendor` field is only one of them, so the exact-prefix strip doesn't apply — the
    # full title is kept rather than guessing which words belong to the artist.
    product = {
        **_RELEASED_PRODUCT,
        "title": "Hackett & Rothery - The Roaring Waves - LP",
        "vendor": "Steve Hackett",
        "handle": "hackett-rothery-the-roaring-waves-lp",
    }
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Hackett & Rothery - The Roaring Waves - LP"
    assert items[0]["artist"] == "Steve Hackett"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Century Media"
    assert Crawler.base_url == "https://centurymedia.store"
    assert Crawler.crawler_type == "catalog"
