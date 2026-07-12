import httpx
import respx
import pytest
from crawlers.triplebrecords import Crawler

_PRODUCTS_URL = "https://triplebrecords.net/collections/all/products.json"

# Real confirmed-live product with 8 variants: vinyl color variants named
# with NO format keyword at all ("Baby Blue / Black Swirl (out of 200)"),
# plus one standalone "CD" variant. No positive vinyl-regex would match any
# of these color names — the filter here is a narrow negative one instead.
_PRODUCT = {
    "title": "Missing Link - Watch Me Bleed CD / LP",
    "vendor": "TRIPLE B RECORDS",
    "handle": "missing-link-watch-me-bleed-pre-order",
    "tags": ["Aged-15+", "Bandcamp"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/missinglink-fallback.jpg"}],
    "variants": [
        {"title": "Baby Blue / Black Swirl (out of 200)", "price": "25.00", "available": True},
        {"title": "Baby Blue w/ Black Splatter (out of 800)", "price": "25.00", "available": True},
        {"title": "CD", "price": "10.00", "available": True},
        {"title": "Gold Nugget (out of 150) *BBB Exclusive*", "price": "25.00", "available": False},
        {"title": "Black Ice w/Gold Splatter (out of 350)", "price": "25.00", "available": False},
    ],
}

# Real confirmed-live exception: vendor is a distributed band's own name, not
# "TRIPLE B RECORDS" — the title's dash-split is what actually gives the
# right artist regardless.
_COMBUST_PRODUCT = {
    "title": "COMBUST - Another Life CD / LP",
    "vendor": "Combust",
    "handle": "combust-another-life-cd-lp",
    "tags": ["CD", "media", "music", "Triple B Records", "Vinyl"],
    "product_type": "CD/Vinyl",
    "images": [],
    "variants": [
        {"title": "Black", "price": "25.00", "available": True},
        {"title": "CD", "price": "10.00", "available": True},
    ],
}

# Real confirmed-live non-release product — a shipping-insurance add-on, not
# a record at all. Excluded entirely via product_type.
_SHIPPING_PROTECTION_PRODUCT = {
    "title": "Guide Package Protection",
    "vendor": "Guide",
    "handle": "guide-package-protection",
    "tags": ["guide_0-99.99-0"],
    "product_type": "Shipping Protection",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "2.00", "available": True},
    ],
}

# Real confirmed-live apparel product mixed into the "all" collection.
_TSHIRT_PRODUCT = {
    "title": "Triple B Records Logo T-Shirt",
    "vendor": "TRIPLE B RECORDS",
    "handle": "triple-b-records-logo-t-shirt",
    "tags": ["Aged-15+"],
    "product_type": "T-Shirt",
    "images": [],
    "variants": [
        {"title": "Small", "price": "20.00", "available": True},
    ],
}

# Real confirmed-live legacy listing with empty tags/product_type — title
# parsing is the only signal that works for these.
_LEGACY_PRODUCT = {
    "title": "AMERICA'S HARDCORE COMPILATION Volume 1",
    "vendor": "TRIPLE B RECORDS",
    "handle": "americas-hardcore-compilation-volume-1",
    "tags": [],
    "product_type": "",
    "images": [],
    "variants": [
        {"title": "Black", "price": "15.00", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_color_variants_with_no_format_keyword(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert items[0]["artist"] == "Missing Link"
    assert items[0]["title"] == "Watch Me Bleed CD / LP — Baby Blue / Black Swirl (out of 200)"
    assert items[0]["price"] == 25.00
    assert all("CD" != i["title"].split("— ")[-1] for i in items)


@respx.mock
async def test_crawl_catalog_parses_artist_from_title_not_unreliable_vendor(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_COMBUST_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "COMBUST"
    assert items[0]["title"] == "Another Life CD / LP — Black"


@respx.mock
async def test_crawl_catalog_excludes_shipping_protection_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SHIPPING_PROTECTION_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_tshirt_product(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_TSHIRT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_legacy_listing_with_empty_tags_and_product_type(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_LEGACY_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "TRIPLE B RECORDS"
    assert items[0]["title"] == "AMERICA'S HARDCORE COMPILATION Volume 1 — Black"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Triple B Records"
    assert Crawler.base_url == "https://triplebrecords.net"
    assert Crawler.crawler_type == "catalog"
