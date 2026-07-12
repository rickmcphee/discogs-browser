import httpx
import respx
import pytest
from crawlers.closedcasketactivities import Crawler

_PRODUCTS_URL = "https://closedcasketactivities.com/collections/vinyl/products.json"

# Real confirmed-live product: single "Default Title" variant carries NO
# format keyword at all — a positive vinyl-regex filter would wrongly exclude
# this, since it's genuinely the only (vinyl) variant. Also a real split
# release ("Artist1 / Artist2 - Title"), and the title itself literally
# contains "**PREORDER**" in addition to the tag-driven preorder detection —
# a confirmed, accepted cosmetic redundancy (not stripped).
_PREORDER_PRODUCT = {
    "title": "Whirr / Luster - Whirr & Luster Split - LP -  Black Ice w/ Blue Splatter **PREORDER**",
    "vendor": "Free Whirl Records",
    "handle": "whirr-luster-whirr-luster-split-lp-black-ice-w-blue-splatter",
    "tags": ["__label:Pre-Order", "media", "mediamail"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live multi-variant product mixing LP color variants with a
# bare "CD" sibling — needs the negative filter, not a positive one.
_MULTI_VARIANT_PRODUCT = {
    "title": "100 Demons - Embrace The Black Light",
    "vendor": "Closed Casket Activities",
    "handle": "100-demons-embrace-the-black-light",
    "tags": ["__label:New", "media", "mediamail", "new"],
    "product_type": "Music",
    "images": [{"src": "https://cdn.shopify.com/100demons-fallback.jpg"}],
    "variants": [
        {"title": "LP - Cloudy Bone in Tan", "price": "25.00", "available": True},
        {"title": "CD", "price": "12.00", "available": True},
        {"title": "LP - Gold Black Marble", "price": "25.00", "available": False},
    ],
}

# Real confirmed-live secondary preorder tag convention — lowercase
# "preorder", no "__label:" prefix, no title suffix.
_SECONDARY_PREORDER_PRODUCT = {
    "title": "Eye Flys - Exigent Circumstance",
    "vendor": "Closed Casket Activities",
    "handle": "eye-flys-exigent-circumstance",
    "tags": ["preorder", "media", "mediamail"],
    "product_type": "Music",
    "images": [],
    "variants": [
        {"title": "LP - Black", "price": "25.00", "available": False},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_includes_single_default_title_variant_without_appending_it(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Whirr / Luster"
    assert item["title"] == "Whirr & Luster Split - LP -  Black Ice w/ Blue Splatter **PREORDER** (Pre-Order)"
    assert item["price"] == 25.00
    assert item["url"] == "https://closedcasketactivities.com/products/whirr-luster-whirr-luster-split-lp-black-ice-w-blue-splatter"


@respx.mock
async def test_crawl_catalog_excludes_cd_variant_includes_lp_color_variants(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_MULTI_VARIANT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "100 Demons"
    assert items[0]["title"] == "Embrace The Black Light — LP - Cloudy Bone in Tan"


@respx.mock
async def test_crawl_catalog_detects_secondary_lowercase_preorder_tag(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SECONDARY_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Exigent Circumstance — LP - Black (Pre-Order)"


@respx.mock
async def test_crawl_catalog_falls_back_to_vendor_when_title_has_no_dash(crawler):
    product = {**_MULTI_VARIANT_PRODUCT, "title": "Split Compilation", "variants": [_MULTI_VARIANT_PRODUCT["variants"][0]]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Closed Casket Activities"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_MULTI_VARIANT_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Closed Casket Activities"
    assert Crawler.base_url == "https://closedcasketactivities.com"
    assert Crawler.crawler_type == "catalog"
