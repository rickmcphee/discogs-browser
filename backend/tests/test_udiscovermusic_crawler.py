import httpx
import respx
import pytest
from crawlers.udiscovermusic import Crawler

_PRODUCTS_URL = "https://shop.udiscovermusic.com/collections/hard-rock-heavy-metal/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-01, trimmed to the fields the crawler reads. Ones marked "altered"
# are captured products with one field changed to reach a branch the live
# data never takes; "invented" products exercise guards the live catalog
# cannot -- each says so at its definition.

# Captured: the store's dominant shape -- clean vendor, album title with a
# trailing pressing descriptor, single Default Title variant, pre-order tag.
_KISS_PRODUCT = {
    "title": "You Wanted The Best, You Got The Best!! (30th Anniversary Purple Fire) 2LP",
    "vendor": "KISS",
    "handle": "kiss-you-wanted-the-best-you-got-the-best-30th-anniversary-purple-fire-2lp",
    "product_type": "2LP",
    "tags": ["2LP", "Color Vinyl", "LP", "pre-order"],
    "images": [{"src": "https://cdn.shopify.com/kiss-purple-fire.png"}],
    "variants": [
        {"id": 43507961233485, "title": "Default Title", "price": "59.98",
         "available": True, "featured_image": None},
    ],
}

# Captured: sold out (available=False), not pre-order tagged.
_SOLD_OUT_PRODUCT = {
    "title": "Pyromania 2LP",
    "vendor": "Def Leppard",
    "handle": "def-leppard-pyromania-2lp",
    "product_type": "2LP",
    "tags": ["LP"],
    "images": [{"src": "https://cdn.shopify.com/pyromania.png"}],
    "variants": [
        {"id": 40514365063245, "title": "Default Title", "price": "39.98",
         "available": False, "featured_image": None},
    ],
}

# Captured: a CD in the same collection -- the format gate's everyday work.
_CD_PRODUCT = {
    "title": "The Art Of Losing (CD)",
    "vendor": "American Hi-Fi",
    "handle": "american-hi-fi-the-art-of-losing-cd",
    "product_type": "CD",
    "tags": ["pre-order"],
    "images": [{"src": "https://cdn.shopify.com/art-of-losing-cd.png"}],
    "variants": [
        {"id": 43365831639117, "title": "Default Title", "price": "13.98",
         "available": True, "featured_image": None},
    ],
}

# Captured: a self-titled album whose title starts with the vendor's name and
# no " - " separator -- the shape the vendor-prefix guard must leave alone.
_SELF_TITLED_PRODUCT = {
    "title": "She Wants Revenge 2LP",
    "vendor": "She Wants Revenge",
    "handle": "she-wants-revenge-she-wants-revenge-2lp",
    "product_type": "2LP",
    "tags": ["2LP", "LP", "pre-order"],
    "images": [{"src": "https://cdn.shopify.com/swr.png"}],
    "variants": [
        {"id": 43303349420109, "title": "Default Title", "price": "32.99",
         "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_pages(*products, empty_page=2):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(list(products)))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(empty_page)}).mock(
        return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    # Altered: pre-order tag removed from the captured KISS product so the
    # plain field mapping is asserted without the suffix.
    product = {**_KISS_PRODUCT, "tags": ["2LP", "Color Vinyl", "LP"]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "KISS"
    assert item["title"] == "You Wanted The Best, You Got The Best!! (30th Anniversary Purple Fire) 2LP"
    assert item["format"] == "Vinyl"
    assert item["price"] == 59.98
    assert item["currency"] == "USD"
    assert item["url"] == "https://shop.udiscovermusic.com/products/kiss-you-wanted-the-best-you-got-the-best-30th-anniversary-purple-fire-2lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/kiss-purple-fire.png"


def test_format_gate_admits_live_vinyl_types_only():
    # Altered: the captured KISS product re-typed across the gate's boundary
    # cases. 1LP/3LP/4LP/7in are live vinyl types; LP/10in/12in are the
    # gate's deliberate allowances; the rest are live non-vinyl types.
    admitted = ["1LP", "2LP", "3LP", "4LP", "7in", "LP", "10in", "12in", "2lp"]
    rejected = ["CD", "2CD", "3CD", "DVD / Blu-Ray", "Box Set (Music Only)",
                "Box Set (Music + Merch)", "T-Shirt", "Cassette", "Bundle",
                "Other", "", "LP + CD", "Vinyl"]
    for ptype in admitted:
        assert Crawler._items({**_KISS_PRODUCT, "product_type": ptype}), ptype
    for ptype in rejected:
        assert Crawler._items({**_KISS_PRODUCT, "product_type": ptype}) == [], ptype


@respx.mock
async def test_crawl_catalog_skips_cd_products(crawler):
    _mock_pages(_CD_PRODUCT, _SELF_TITLED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["She Wants Revenge"]


@respx.mock
async def test_self_titled_title_is_not_stripped(crawler):
    _mock_pages(_SELF_TITLED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "She Wants Revenge 2LP (Pre-Order)"


@respx.mock
async def test_vendor_dash_prefix_is_stripped(crawler):
    # Invented: no live title carries a "{vendor} - " prefix; the shared
    # strip_vendor_prefix is a drift guard, asserted here so its presence
    # is deliberate.
    product = {**_KISS_PRODUCT, "tags": [], "title": "KISS - Destroyer 1LP",
               "product_type": "1LP"}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Destroyer 1LP"


@respx.mock
async def test_preorder_tag_appends_suffix(crawler):
    _mock_pages(_KISS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"].endswith(" (Pre-Order)")


@respx.mock
async def test_preorder_tag_matching_is_case_insensitive(crawler):
    # Altered: tag re-cased.
    product = {**_KISS_PRODUCT, "tags": ["Pre-Order"]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"].endswith(" (Pre-Order)")


@respx.mock
async def test_sold_out_product_is_skipped(crawler):
    _mock_pages(_SOLD_OUT_PRODUCT, _KISS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["KISS"]


@respx.mock
async def test_no_preorder_availability_bypass(crawler):
    # Altered: the captured pre-order flipped unavailable. All 13 live
    # pre-order-tagged vinyl products report available=True, so an
    # unavailable one is gone allocation, not not-yet-released -- pinned
    # here so reintroducing the napalmrecords.py bypass is deliberate.
    product = {**_KISS_PRODUCT, "variants": [
        {**_KISS_PRODUCT["variants"][0], "available": False},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_blank_vendor_product_is_skipped(crawler):
    # Altered: vendor blanked; no live vinyl product lacks one.
    product = {**_KISS_PRODUCT, "vendor": "  "}
    _mock_pages(product, _SELF_TITLED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["She Wants Revenge"]


@respx.mock
async def test_null_variants_product_is_skipped(crawler):
    # Altered: variants nulled.
    product = {**_KISS_PRODUCT, "variants": None}
    _mock_pages(product, _SELF_TITLED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["She Wants Revenge"]


@respx.mock
async def test_unparseable_price_yields_none(crawler):
    # Altered: price corrupted.
    product = {**_KISS_PRODUCT, "variants": [
        {**_KISS_PRODUCT["variants"][0], "price": "n/a"},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["price"] is None


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    # Altered: featured_image populated; no live variant carries one.
    product = {**_KISS_PRODUCT, "variants": [
        {**_KISS_PRODUCT["variants"][0],
         "featured_image": {"src": "https://cdn.shopify.com/kiss-variant.png"}},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/kiss-variant.png"


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    # Altered: images emptied; every live vinyl product has at least one.
    product = {**_KISS_PRODUCT, "images": []}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_multi_variant_product_appends_variant_descriptor(crawler):
    # Invented: every live vinyl product is single-variant; without a
    # per-variant descriptor these rows would share (artist, title, url)
    # and collapse onto one item_key downstream.
    product = {**_KISS_PRODUCT, "tags": [], "variants": [
        {"id": 1, "title": "Purple Fire", "price": "59.98", "available": True},
        {"id": 2, "title": "Black", "price": "49.98", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    titles = {i["title"] for i in items}
    assert len(items) == 2
    assert all("—" in t for t in titles)
    assert any(t.endswith("— Purple Fire") for t in titles)
    assert any(t.endswith("— Black") for t in titles)


@respx.mock
async def test_multi_variant_placeholder_title_falls_back_to_id(crawler):
    # Invented: placeholder variant titles on a multi-variant product.
    product = {**_KISS_PRODUCT, "tags": [], "variants": [
        {"id": 1, "title": "Default Title", "price": "59.98", "available": True},
        {"id": 2, "title": "  ", "price": "49.98", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    titles = {i["title"] for i in items}
    assert any(t.endswith("— 1") for t in titles)
    assert any(t.endswith("— 2") for t in titles)


@respx.mock
async def test_multi_variant_without_title_or_id_raises(crawler):
    # Invented: a variant with neither identity source.
    product = {**_KISS_PRODUCT, "tags": [], "variants": [
        {"id": 1, "title": "Purple Fire", "price": "59.98", "available": True},
        {"title": "Default Title", "price": "49.98", "available": True},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="neither a usable title nor an id"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_all_blank_vendors_raises(crawler):
    # Altered: every product's vendor blanked -- artist-source drift.
    product = {**_KISS_PRODUCT, "vendor": ""}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_format_gated_products_do_not_trip_drift_guards(crawler):
    # An all-CD page run must complete empty without raising: the collection
    # is genuinely mixed-format, so format-gated products still count toward
    # both tallies.
    _mock_pages(_CD_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_KISS_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_SELF_TITLED_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["KISS", "She Wants Revenge"]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "uDiscover Music"
    assert Crawler.base_url == "https://shop.udiscovermusic.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary
