import httpx
import respx
import pytest
from crawlers.twentybuckspin import Crawler

_PRODUCTS_URL = "https://20buckspin.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "ACHERONTAS - MALOCCHIO: THE SEVEN TONGUES OF AAHMON LP",
    "vendor": "Osmose",
    "handle": "acherontas-malocchio-the-seven-tongues-of-aahmon-lp",
    "product_type": "VINYL",
    "tags": ["A"],
    "images": [{"src": "https://cdn.shopify.com/acherontas-fallback.jpg"}],
    "variants": [
        {"title": "BLACK SMOKE GALAXY", "price": "24.99", "available": True, "sku": None},
    ],
}

# Real confirmed-live promo item: a "buy 3-4/5-6 regular LPs, get mystery
# LP(s) free" bundle, priced at $0.00 per variant — not a real release.
_MYSTERY_LP_PROMO = {
    "title": "*FREE MYSTERY LPs W/ APPLICABLE VINYL PURCHASE*",
    "vendor": "20 Buck Spin",
    "handle": "free-mystery-lps-w-applicable-vinyl-purchase",
    "product_type": "VINYL",
    "tags": ["A", "B", "C"],
    "images": [],
    "variants": [
        {"title": "1 MYSTERY LP (3-4 REGULAR PRICED LPS IN CART)", "price": "0.00", "available": True, "sku": None},
        {"title": "2 MYSTERY LPs (5-6 REGULAR PRICED LPS IN CART)", "price": "0.00", "available": True, "sku": None},
    ],
}

# Real confirmed-live merch item mislabeled product_type "VINYL" — the only
# non-$0 non-release listing found in this collection, so it needs its own
# title-keyword filter rather than the price<=0 filter above.
_TOTE_BAG = {
    "title": "20 BUCK SPIN - REIGN IN HELL TOTE BAG",
    "vendor": "20 Buck Spin",
    "handle": "20-buck-spin-reign-in-hell-tote-bag",
    "product_type": "VINYL",
    "tags": ["R"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "15.00", "available": True, "sku": ""},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_dash_split_not_vendor(crawler):
    # `vendor` alternates between the store's own imprint ("20 Buck Spin") and
    # labels it distributes ("Osmose", "Dark Descent") — never the artist,
    # confirmed live across the fetched sample.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "ACHERONTAS"
    assert item["title"] == "MALOCCHIO: THE SEVEN TONGUES OF AAHMON LP — BLACK SMOKE GALAXY"
    assert item["price"] == 24.99
    assert item["url"] == "https://20buckspin.com/products/acherontas-malocchio-the-seven-tongues-of-aahmon-lp"


@respx.mock
async def test_crawl_catalog_excludes_zero_priced_mystery_lp_promo(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MYSTERY_LP_PROMO]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_tote_bag_mislabeled_as_vinyl(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_TOTE_BAG]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant(crawler):
    product = {**_PRODUCT, "variants": [{**_PRODUCT["variants"][0], "available": False}]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_PRODUCT, "title": "Split Compilation Vol. 4"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Osmose"
    assert items[0]["title"] == "Split Compilation Vol. 4 — BLACK SMOKE GALAXY"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "20 Buck Spin"
    assert Crawler.base_url == "https://20buckspin.com"
    assert Crawler.crawler_type == "catalog"
