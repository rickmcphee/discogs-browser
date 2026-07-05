import httpx
import respx
import pytest
from crawlers.nuclearblast import Crawler

_PRODUCTS_URL = "https://shop.nuclearblast.com/collections/vinyl/products.json"

_PRODUCT = {
    "title": "Rob Zombie - The Great Satan",
    "vendor": "Rob Zombie",
    "handle": "rob-zombie-the-great-satan",
    "product_type": "Vinyl",
    "tags": ["Aged-15+"],
    "images": [{"src": "https://cdn.shopify.com/rz-fallback.png"}],
    "variants": [
        {"title": "Ghostly Black Vinyl", "price": "31.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/rz-black.png"}},
        {"title": "Black / White Swirl Vinyl", "price": "31.99", "available": False},
        {"title": "Jewel Case CD", "price": "14.99", "available": True},
    ],
}

_PREORDER_PRODUCT = {
    "title": "Marilyn Manson - One Assassination Under God - Chapter 2",
    "vendor": "Marilyn Manson",
    "handle": "marilyn-manson-one-assassination-under-god-chapter-2",
    "product_type": "Vinyl/CD",
    "tags": ["Aged-15+", "Marilyn Manson", "media", "music", "pre-order"],
    "images": [{"src": "https://cdn.shopify.com/manson-fallback.png"}],
    "variants": [
        {"title": "Green and Blue Marble Vinyl", "price": "28.99", "available": True},
        {"title": "Tan w/ Black / Pink and Neon Green Splatter Vinyl", "price": "28.99", "available": False},
        {"title": "Jewel Case CD", "price": "14.99", "available": True},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_available_vinyl_variants_only(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Rob Zombie"
    assert item["title"] == "The Great Satan — Ghostly Black Vinyl"
    assert item["format"] == "Vinyl"
    assert item["price"] == 31.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://shop.nuclearblast.com/products/rob-zombie-the-great-satan"
    assert item["cover_image_url"] == "https://cdn.shopify.com/rz-black.png"


@respx.mock
async def test_crawl_catalog_falls_back_to_product_image_when_variant_has_none(crawler):
    # The unavailable "Black / White Swirl Vinyl" variant has no featured_image of its own
    product = {**_PRODUCT, "tags": ["Aged-15+", "pre-order"]}  # force-include the unavailable variant
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    swirl_item = next(i for i in items if "Swirl" in i["title"])
    assert swirl_item["cover_image_url"] == "https://cdn.shopify.com/rz-fallback.png"


@respx.mock
async def test_crawl_catalog_cover_image_is_none_when_product_has_no_images(crawler):
    product = {**_PRODUCT, "images": [], "variants": [
        {"title": "Ghostly Black Vinyl", "price": "31.99", "available": True},
    ]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_strips_vendor_prefix_from_title(crawler):
    product = {**_PRODUCT, "vendor": "NAILS", "title": "NAILS - Every Bridge Burning", "handle": "nails"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Every Bridge Burning — Ghostly Black Vinyl"


@respx.mock
async def test_crawl_catalog_keeps_full_title_when_no_vendor_prefix(crawler):
    product = {**_PRODUCT, "title": "Compilation Album", "handle": "compilation"}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Compilation Album — Ghostly Black Vinyl"


@respx.mock
async def test_crawl_catalog_includes_unavailable_vinyl_for_preorder_products(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([_PREORDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    titles = {item["title"] for item in items}
    # both vinyl variants included regardless of `available`; CD variant still excluded
    assert len(items) == 2
    assert "One Assassination Under God - Chapter 2 — Green and Blue Marble Vinyl (Pre-Order)" in titles
    assert "One Assassination Under God - Chapter 2 — Tan w/ Black / Pink and Neon Green Splatter Vinyl (Pre-Order)" in titles


@respx.mock
async def test_crawl_catalog_excludes_unavailable_variants_when_not_tagged_preorder(crawler):
    product = {**_PRODUCT, "tags": ["Aged-15+", "May 4th"]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert "(Pre-Order)" not in items[0]["title"]


@respx.mock
async def test_preorder_tag_matching_is_case_insensitive(crawler):
    product = {**_PRODUCT, "tags": ["Pre-Order"]}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert all("(Pre-Order)" in item["title"] for item in items)


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_PRODUCT, "variants": None}
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([product]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Nuclear Blast"
    assert Crawler.base_url == "https://shop.nuclearblast.com"
    assert Crawler.crawler_type == "catalog"
