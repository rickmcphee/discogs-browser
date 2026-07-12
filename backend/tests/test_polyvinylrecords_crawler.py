import httpx
import respx
import pytest
from crawlers.polyvinylrecords import Crawler

_PRODUCTS_URL = "https://polyvinylrecords.com/collections/vinyl/products.json"

# Real confirmed-live case: even a genuine Polyvinyl house release has
# vendor = the label ("Polyvinyl Records"), never the artist — title parsing
# is the only correct source, universally on this store.
_PRODUCT = {
    "title": "Deerhoof - Breakup Song",
    "vendor": "Polyvinyl Records",
    "handle": "deerhoof-breakup-song",
    "tags": ["$5CD-TAPE", "Deerhoof"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/deerhoof-fallback.jpg"}],
    "variants": [
        {"title": "Vinyl (Blue)", "price": "20.00", "available": True},
        {"title": "CD", "price": "12.00", "available": False},
        {"title": "Digital", "price": "10.00", "available": True},
    ],
}

# Real confirmed-live third-party-distributed title — included the same as
# a house release, since it's genuinely purchasable vinyl on this
# collection; vendor is the distributor ("Atlantic Records"), not the artist.
_NON_POLYVINYL_PRODUCT = {
    "title": "100 gecs - 10,000 gecs",
    "vendor": "Atlantic Records",
    "handle": "100-gecs-10-000-gecs",
    "tags": ["100 gecs", "Electronic", "Non-Polyvinyl", "Pop"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Vinyl (Black)", "price": "40.00", "available": True},
    ],
}

# Real confirmed-live release-name-embedded preorder tag — no single
# canonical tag string exists on this store; detection needs a substring
# search for "pre-order" anywhere in the tags array.
_PREORDER_PRODUCT = {
    "title": "American Football - American Football (Live in Los Angeles)",
    "vendor": "Polyvinyl Records",
    "handle": "american-football-live-in-los-angeles",
    "tags": ["AF - Live in LA Pre-Order", "American Football", "exclude_rebuy", "Live in Los Angeles"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Vinyl (Clear)", "price": "22.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_not_label_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Deerhoof"
    assert item["title"] == "Breakup Song — Vinyl (Blue)"
    assert item["price"] == 20.00
    assert item["url"] == "https://polyvinylrecords.com/products/deerhoof-breakup-song"


@respx.mock
async def test_crawl_catalog_includes_third_party_distributed_title(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_NON_POLYVINYL_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "100 gecs"
    assert items[0]["title"] == "10,000 gecs — Vinyl (Black)"


@respx.mock
async def test_crawl_catalog_detects_preorder_via_tag_substring_search(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "American Football (Live in Los Angeles) — Vinyl (Clear) (Pre-Order)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Polyvinyl Record Co."
    assert Crawler.base_url == "https://polyvinylrecords.com"
    assert Crawler.crawler_type == "catalog"
