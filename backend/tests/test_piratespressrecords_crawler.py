import httpx
import respx
import pytest
from crawlers.piratespressrecords import Crawler

_BASE = "https://shop.piratespressrecords.com"
_URL = f"{_BASE}/collections/all/products.json"

_PRODUCT = {
    "id": 9299152470294,
    "title": "45 Adapters - Unstoppable - Black - Vinyl LP",
    "vendor": "45 Adapters",
    "handle": "45adp391bl-lp",
    "product_type": "Vinyl LP",
    "tags": ["45 Adapters", "Music", "Vinyl LP"],
    "images": [{"src": "https://cdn.shopify.com/45adp-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "19.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_products(products):
    """Mock the /collections/all endpoint. An empty page 1 means no page-2
    request happens, matching how iter_products stops on the first empty
    page."""
    respx.get(_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
    if products:
        respx.get(_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_vinyl_lp_product(crawler):
    _mock_products([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "45 Adapters"
    assert item["title"] == "Unstoppable - Black - Vinyl LP"
    assert item["format"] == "Vinyl"
    assert item["price"] == 19.99
    assert item["currency"] == "USD"
    assert item["url"] == f"{_BASE}/products/45adp391bl-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/45adp-fallback.jpg"


@respx.mock
async def test_crawl_catalog_includes_picture_disc_product_type(crawler):
    product = {**_PRODUCT, "product_type": "Picture Disc"}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_crawl_catalog_excludes_non_vinyl_product_type(crawler):
    # /collections/all mixes in merch/CD/Cassette (35 distinct non-vinyl
    # product_type values confirmed live) alongside the 566 vinyl products —
    # this asserts the allowlist filter rejects them.
    product = {**_PRODUCT, "product_type": "T-Shirt"}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    product = {
        **_PRODUCT,
        "tags": ["Music", "preorder"],
        "variants": [{"title": "Default Title", "price": "21.99", "available": False}],
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Unstoppable - Black - Vinyl LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{"title": "Default Title", "price": "21.99", "available": False}]}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_splits_title_on_first_dash_even_when_vendor_mismatches_case(crawler):
    # vendor "Crim" doesn't exact-prefix-match title "CRIM - ..." (case drift,
    # confirmed live on 58/566 titles) — strip_vendor_prefix would no-op here,
    # leaving "CRIM - ..." in the display title. The local dash-split doesn't
    # care what vendor says.
    product = {
        **_PRODUCT,
        "title": "CRIM - Blau Sang, Vermell Cel Black Vinyl LP",
        "vendor": "Crim",
        "handle": "crimp170bl-lp",
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Crim"
    assert items[0]["title"] == "Blau Sang, Vermell Cel Black Vinyl LP"


@respx.mock
async def test_crawl_catalog_keeps_hyphenated_artist_name_intact(crawler):
    # "The Re-Volts" has an unspaced internal hyphen — confirmed live that a
    # naive \s*-\s* split (the original implementation) breaks on it, clipping
    # to "Volts". The real separator " - " later in the title has whitespace
    # on both sides; the hyphen inside "Re-Volts" has none on either side.
    product = {
        **_PRODUCT,
        "title": 'The Re-Volts - Wages Orange Vinyl 7"',
        "vendor": "The Re-Volts",
        "handle": "revop104or-45",
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "The Re-Volts"
    assert items[0]["title"] == 'Wages Orange Vinyl 7"'


@respx.mock
async def test_crawl_catalog_falls_back_to_full_title_when_no_dash_separator(crawler):
    # Confirmed live: 2/566 titles have no " - " at all. Accepted miss, same
    # tradeoff as Deathwish Inc's quote-matching residual misses.
    product = {
        **_PRODUCT,
        "title": "The Barstool Preachers Blatant Propaganda Black Vinyl LP",
        "vendor": "The Bar Stool Preachers",
    }
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "The Bar Stool Preachers"
    assert items[0]["title"] == "The Barstool Preachers Blatant Propaganda Black Vinyl LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    _mock_products([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Pirates Press Records"
    assert Crawler.base_url == "https://shop.piratespressrecords.com"
    assert Crawler.crawler_type == "catalog"
