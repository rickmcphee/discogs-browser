import httpx
import respx
import pytest
from crawlers.seasonofmist import Crawler

_PRODUCTS_URL = "https://shopusa.season-of-mist.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Drudkh - A Few Lines in Archaic Ukrainian - 3LP Gatefold",
    "vendor": "Season of Mist - North America",
    "handle": "drudkh-a-few-lines-in-archaic-ukrainian-3lp-gatefold",
    "tags": ["_visible"],
    "product_type": "",
    "body_html": "<p>The new album from Drudkh.</p>",
    "images": [{"src": "https://cdn.shopify.com/drudkh-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "45.00", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Windir - 1184 - DOUBLE LP GATEFOLD COLORED",
    "vendor": "Season of Mist - North America",
    "handle": "windir-1184-double-lp-gatefold-colored",
    "tags": ["_visible"],
    "product_type": "",
    "body_html": "<p>This title is available for pre-order. It will be available on 07/31/2026.</p>",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "38.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_dash_separated_title_not_vendor(crawler):
    # `vendor` is always "Season of Mist - North America" for every product
    # (confirmed live across music and merch alike) — never the artist.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Drudkh"
    assert item["title"] == "A Few Lines in Archaic Ukrainian - 3LP Gatefold"
    assert item["price"] == 45.00
    assert item["url"] == "https://shopusa.season-of-mist.com/products/drudkh-a-few-lines-in-archaic-ukrainian-3lp-gatefold"


@respx.mock
async def test_crawl_catalog_detects_preorder_from_body_html_not_tags(crawler):
    # No tag or product_type carries pre-order status on this store (both are
    # always "_visible"/"" respectively, confirmed live) — only body_html free
    # text does, e.g. "...available for pre-order. It will be available on
    # 07/31/2026." Tags/product_type checks would silently miss every pre-order.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Windir"
    assert items[0]["title"] == "1184 - DOUBLE LP GATEFOLD COLORED (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_PRODUCT, "title": "Untitled Compilation", "body_html": ""}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Season of Mist - North America"
    assert items[0]["title"] == "Untitled Compilation"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Season of Mist"
    assert Crawler.base_url == "https://shopusa.season-of-mist.com"
    assert Crawler.crawler_type == "catalog"
