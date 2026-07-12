import httpx
import respx
import pytest
from crawlers.temporaryresidence import Crawler

_PRODUCTS_URL = "https://temporaryresidence.com/collections/shop/products.json"

# Real confirmed-live product. Variant titles use a bullet (U+2022)
# immediately followed by a non-breaking space (U+00A0), not a regular
# space — "2xLP • Black Vinyl". The plain vinyl/LP substring match
# still works regardless of the exact separator character.
_PREORDER_PRODUCT = {
    "title": "Pyramid of the Sun – Anniversary Edition",
    "vendor": "Maserati",
    "handle": "trr384",
    "tags": ["Flag_Pre-Order", "Maserati"],
    "product_type": "Albums",
    "images": [{"src": "https://cdn.shopify.com/maserati-fallback.jpg"}],
    "variants": [
        {"title": "2xCD", "price": "14.00", "available": True},
        {"title": "2xLP • Black Vinyl", "price": "25.00", "available": True},
        {"title": "2xLP • Purple & Magenta Colored Vinyl", "price": "30.00", "available": True},
    ],
}

# Real confirmed-live apparel product mixed into the "shop" collection.
_TSHIRT_PRODUCT = {
    "title": "Temporary Residence Logo Tee",
    "vendor": "Temporary Residence",
    "handle": "logo-tee",
    "tags": [],
    "product_type": "T-Shirts",
    "images": [],
    "variants": [
        {"title": "S • BLACK  (Unisex Organic)", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live mistyped non-music product — product_type "Albums" but
# the single variant is literally "Book", not a vinyl format.
_BOOK_PRODUCT = {
    "title": "The Early Days Revisited",
    "vendor": "Nina Nastasia",
    "handle": "trr395-trr396-trr397-book",
    "tags": ["Nina Nastasia"],
    "product_type": "Albums",
    "images": [],
    "variants": [
        {"title": "Book", "price": "20.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_includes_bulleted_lp_variants_when_preorder_tagged(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Maserati"
    assert items[0]["title"] == "Pyramid of the Sun – Anniversary Edition — 2xLP • Black Vinyl (Pre-Order)"
    assert items[0]["price"] == 25.00


@respx.mock
async def test_crawl_catalog_excludes_tshirt_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_TSHIRT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_yields_nothing_for_mistyped_book_product(crawler):
    # Accepted gap: this product has product_type "Albums" but its only
    # variant is "Book", which doesn't match the vinyl/LP regex — correctly
    # excluded without needing a special case for it.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_BOOK_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PREORDER_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Temporary Residence Ltd"
    assert Crawler.base_url == "https://temporaryresidence.com"
    assert Crawler.crawler_type == "catalog"
