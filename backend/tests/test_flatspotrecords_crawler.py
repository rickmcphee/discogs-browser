import httpx
import respx
import pytest
from crawlers.flatspotrecords import Crawler

_PRODUCTS_URL = "https://flatspotrecords.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "The S.E.T. - Self Evident Truth Moonphase Vinyl",
    "vendor": "The S.E.T.",
    "handle": "the-s-e-t-self-evident-truth-moonphase-vinyl",
    "tags": ["Aged-15+", "media", "music", "The S.E.T.", "vinyl"],
    "product_type": "Vinyl",
    "images": [{"src": "https://cdn.shopify.com/theset-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

# Real confirmed-live preorder product — tag is dated "Pre-Order MM-DD-YY"
# (capitalized, hyphenated), a different spelling from Fearless's lowercase
# unhyphenated "preorder". Matched via a regex on tags starting with
# "pre-order" case-insensitively, not an exact has_tag match, since this
# store's generic and dated tag forms both start that way.
_PREORDER_PRODUCT = {
    "title": "Mizery - Mizery Baby Blue Opaque Vinyl",
    "vendor": "Mizery",
    "handle": "mizery-mizery-baby-blue-opaque-vinyl",
    "tags": ["Aged-15+", "media", "Mizery", "Pre-Order 03-20-26", "vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

_SOLD_OUT_PRODUCT = {
    "title": "Terror - Still Suffer Sky Blue / White Cornetto Vinyl (Flatspot Exclusive)",
    "vendor": "Terror",
    "handle": "terror-still-suffer-sky-blue-white-cornetto-vinyl",
    "tags": ["Aged-15+", "media", "music", "Terror", "vinyl"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": False},
    ],
}

# Real confirmed-live various-artist compilation where `vendor` is the label
# itself, not a band — this is the label correctly showing up as "artist" for
# a genuine various-artist release (same accepted shape as Fat Wreck Chords'
# compilations), not a bug needing special-casing. The title starts with
# "Flatspot Records - ", so strip_vendor_prefix genuinely fires here too.
_LABEL_COMPILATION_PRODUCT = {
    "title": "Flatspot Records - The Extermination Vol. 2 LP (Black)",
    "vendor": "Flatspot Records",
    "handle": "flatspot-records-the-extermination-vol-2-lp-black",
    "tags": ["Aged-15+", "Flatspot Records"],
    "product_type": "Vinyl",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "20.00", "available": True},
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
    assert item["artist"] == "The S.E.T."
    assert item["title"] == "Self Evident Truth Moonphase Vinyl"
    assert item["price"] == 25.00
    assert item["url"] == "https://flatspotrecords.com/products/the-s-e-t-self-evident-truth-moonphase-vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Mizery Baby Blue Opaque Vinyl (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_SOLD_OUT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_uses_label_as_artist_for_various_artist_compilation(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_LABEL_COMPILATION_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Flatspot Records"
    assert items[0]["title"] == "The Extermination Vol. 2 LP (Black)"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Flatspot Records"
    assert Crawler.base_url == "https://flatspotrecords.com"
    assert Crawler.crawler_type == "catalog"
