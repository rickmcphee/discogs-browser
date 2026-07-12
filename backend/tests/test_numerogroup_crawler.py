import httpx
import respx
import pytest
from crawlers.numerogroup import Crawler

_PRODUCTS_URL = "https://numerogroup.com/collections/vinyl/products.json"

# Real confirmed-live product: `vendor` is the label, not the artist — this
# catalog's `title` never contains the real artist either (accepted gap, no
# reliable artist source exists for most of this back-catalog). Variants mix
# vinyl colors, cassette, CD, and digital on one product.
_PRODUCT = {
    "title": "Stratosphere",
    "vendor": "Numero Group",
    "handle": "duster-stratosphere",
    "tags": ["format:Cassette", "format:CD", "format:Digital", "format:LP", "Numero Group", "Punk", "Rock", "Slowcore"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/duster-fallback.jpg"}],
    "variants": [
        {"title": "Gold Dust Vinyl", "price": "27.00", "available": True},
        {"title": "Cassette", "price": "12.00", "available": False},
        {"title": "CD", "price": "12.00", "available": False},
        {"title": "Black LP Vinyl", "price": "25.00", "available": True},
        {"title": "Digital", "price": "10.00", "available": True},
    ],
}

# Real confirmed-live upcoming release: `vendor` is the real artist here (the
# exception to the label-placeholder rule), and the "Street Date" tag marks
# a pre-order — vinyl/CD variants are unavailable, only Digital is available.
_PREORDER_PRODUCT = {
    "title": "1985: The Miracle Year",
    "vendor": "Hüsker Dü",
    "handle": "1985-the-miracle-year",
    "tags": ["101325", "Deep Dive", "Domestic 3 day", "format:Boxset", "format:LP", "International 5 day", "Punk", "Street Date"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "4xLP Boxset (Divide And Conquer Vinyl) [Numero Exclusive]", "price": "110.00", "available": False},
        {"title": "2xCD Boxset", "price": "25.00", "available": False},
        {"title": "Digital", "price": "20.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cassette_cd_digital_includes_vinyl_variants(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Numero Group"
    assert items[0]["title"] == "Stratosphere — Gold Dust Vinyl"
    assert items[0]["price"] == 27.00


@respx.mock
async def test_crawl_catalog_includes_unavailable_boxset_variant_when_street_date_tagged(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Hüsker Dü"
    assert items[0]["title"] == "1985: The Miracle Year — 4xLP Boxset (Divide And Conquer Vinyl) [Numero Exclusive] (Pre-Order)"


@respx.mock
async def test_crawl_catalog_includes_glued_multiplier_lp_variant(crawler):
    product = {**_PRODUCT, "title": "1992-1998", "variants": [
        {"title": "5xLP Box", "price": "50.00", "available": True},
        {"title": "4xCD", "price": "40.00", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "1992-1998 — 5xLP Box"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Numero Group"
    assert Crawler.base_url == "https://numerogroup.com"
    assert Crawler.crawler_type == "catalog"
