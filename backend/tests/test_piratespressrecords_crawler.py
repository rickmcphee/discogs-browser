import httpx
import respx
import pytest
from crawlers.piratespressrecords import Crawler

_BASE = "https://shop.piratespressrecords.com"
_SLUGS = ("ppr-12-vinyl", "ppr-7", "ppr-10-vinyl", "all-distro-titles")

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


def _mock_slugs(per_slug):
    """Mock all four collection endpoints. A slug absent from `per_slug` returns
    an immediately-empty page 1 (no page-2 request happens, matching how
    iter_products stops on the first empty page)."""
    for slug in _SLUGS:
        products = per_slug.get(slug, [])
        url = f"{_BASE}/collections/{slug}/products.json"
        respx.get(url, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
        if products:
            respx.get(url, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_vinyl_lp_product(crawler):
    _mock_slugs({"ppr-12-vinyl": [_PRODUCT]})
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
async def test_crawl_catalog_dedupes_product_returned_by_multiple_collections(crawler):
    # 11/500 products are confirmed live to appear in 2-3 of the four collections
    # (e.g. a 7" also filed under the 12" collection) — the same product `id`
    # must only ever be yielded once.
    _mock_slugs({
        "ppr-12-vinyl": [_PRODUCT],
        "ppr-7": [_PRODUCT],
        "all-distro-titles": [_PRODUCT],
    })
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_crawl_catalog_includes_picture_disc_product_type(crawler):
    product = {**_PRODUCT, "product_type": "Picture Disc"}
    _mock_slugs({"ppr-7": [product]})
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_crawl_catalog_excludes_non_vinyl_product_type(crawler):
    # No non-vinyl product_type was found live in any of the four collections,
    # but the filter still needs to correctly reject one if it ever appears —
    # same defensive-test shape as Equal Vision's crawler tests.
    product = {**_PRODUCT, "product_type": "T-Shirt"}
    _mock_slugs({"ppr-12-vinyl": [product]})
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_includes_unavailable_variant_when_tagged_preorder(crawler):
    product = {
        **_PRODUCT,
        "tags": ["Music", "preorder"],
        "variants": [{"title": "Default Title", "price": "21.99", "available": False}],
    }
    _mock_slugs({"ppr-10-vinyl": [product]})
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Unstoppable - Black - Vinyl LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variant_when_not_preorder(crawler):
    product = {**_PRODUCT, "variants": [{"title": "Default Title", "price": "21.99", "available": False}]}
    _mock_slugs({"ppr-10-vinyl": [product]})
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_splits_title_on_first_dash_even_when_vendor_mismatches_case(crawler):
    # vendor "Crim" doesn't exact-prefix-match title "CRIM - ..." (case drift,
    # confirmed live on 58/500 titles) — strip_vendor_prefix would no-op here,
    # leaving "CRIM - ..." in the display title. The local dash-split doesn't
    # care what vendor says.
    product = {
        **_PRODUCT,
        "title": "CRIM - Blau Sang, Vermell Cel Black Vinyl LP",
        "vendor": "Crim",
        "handle": "crimp170bl-lp",
    }
    _mock_slugs({"all-distro-titles": [product]})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Crim"
    assert items[0]["title"] == "Blau Sang, Vermell Cel Black Vinyl LP"


@respx.mock
async def test_crawl_catalog_falls_back_to_full_title_when_no_dash_separator(crawler):
    # Confirmed live: 2/500 titles have no " - " at all. Accepted miss, same
    # tradeoff as Deathwish Inc's quote-matching residual misses.
    product = {
        **_PRODUCT,
        "title": "The Barstool Preachers Blatant Propaganda Black Vinyl LP",
        "vendor": "The Bar Stool Preachers",
    }
    _mock_slugs({"ppr-12-vinyl": [product]})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "The Bar Stool Preachers"
    assert items[0]["title"] == "The Barstool Preachers Blatant Propaganda Black Vinyl LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    _mock_slugs({"ppr-12-vinyl": [product]})
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Pirates Press Records"
    assert Crawler.base_url == "https://shop.piratespressrecords.com"
    assert Crawler.crawler_type == "catalog"
