import httpx
import respx
import pytest
from crawlers.hammerheart import Crawler

_PRODUCTS_URL = "https://hammerheart.indiemerch.com/collections/vinyl/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-08-30, trimmed to the fields the crawler reads. Ones marked "altered"
# are captured products with one field changed to reach a branch the live
# data never takes; "invented" products exercise guards the live catalog
# cannot -- each says so at its definition.

# Captured: the store's dominant title shape -- the artist leads the title in
# ALL CAPS while `vendor` carries mixed case, so an exact-case prefix strip
# misses it.
_CAPS_PREFIX_PRODUCT = {
    "title": "TROUBLE - Psalm 9 / Black Vinyl LP",
    "vendor": "Trouble",
    "handle": "trouble-psalm-9-black-vinyl-lp",
    "product_type": "12\"",
    "tags": ["trouble", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/trouble-fallback.png"}],
    "variants": [
        {"id": 111, "title": "Default Title", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/trouble-black.png"}},
    ],
}

# Captured: exact-case vendor prefix, the minority shape.
_EXACT_PREFIX_PRODUCT = {
    "title": "Vintersorg - Till Fjälls / Baby Blue Vinyl LP",
    "vendor": "Vintersorg",
    "handle": "vintersorg-till-fjalls-baby-blue-vinyl-lp",
    "product_type": "12\"",
    "tags": ["vintersorg", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/vintersorg.png"}],
    "variants": [
        {"id": 222, "title": "Baby Blue", "price": "23.99", "available": True},
    ],
}

# Captured: no artist prefix at all -- album title with a parenthetical
# pressing-color suffix, pre-order tagged.
_PAREN_PRODUCT = {
    "title": "Gathered Around the Oaken Table (Black vinyl)",
    "vendor": "Mithotyn",
    "handle": "gathered-around-the-oaken-table-black-vinyl",
    "product_type": "2x12\"",
    "tags": ["preorder"],
    "images": [{"src": "https://cdn.shopify.com/mithotyn.png"}],
    "variants": [
        {"id": 333, "title": "Black", "price": "27.99", "available": True},
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
    _mock_pages(_EXACT_PREFIX_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Vintersorg"
    assert item["title"] == "Till Fjälls / Baby Blue Vinyl LP"
    assert item["format"] == "Vinyl"
    assert item["price"] == 23.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://hammerheart.indiemerch.com/products/vintersorg-till-fjalls-baby-blue-vinyl-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/vintersorg.png"


@respx.mock
async def test_strips_all_caps_artist_prefix(crawler):
    _mock_pages(_CAPS_PREFIX_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Trouble"
    assert items[0]["title"] == "Psalm 9 / Black Vinyl LP"
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/trouble-black.png"


@respx.mock
async def test_strips_prefix_with_tab_before_dash(crawler):
    # Captured: two live products put a tab between artist and dash.
    product = {**_EXACT_PREFIX_PRODUCT,
               "title": "Sarcasm\t- Lifeforce Omnibound / Black Vinyl LP",
               "vendor": "Sarcasm"}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Lifeforce Omnibound / Black Vinyl LP"


@respx.mock
async def test_strips_prefix_with_slash_separator(crawler):
    # Captured: one live product separates artist from album with " / ".
    product = {**_EXACT_PREFIX_PRODUCT,
               "title": "ORPHANAGE / Oblivion / Blue Vinyl LP",
               "vendor": "Orphanage"}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Oblivion / Blue Vinyl LP"


@respx.mock
async def test_self_titled_album_is_not_stripped(crawler):
    # Captured: a self-titled album leads with the artist's name but has no
    # separator -- stripping it would leave only "(Black vinyl)".
    product = {**_PAREN_PRODUCT,
               "title": "Abramelin (Black vinyl)",
               "vendor": "Abramelin",
               "tags": []}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Abramelin (Black vinyl)"


@respx.mock
async def test_preorder_tag_suffixes_title(crawler):
    _mock_pages(_PAREN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Gathered Around the Oaken Table (Black vinyl) (Pre-Order)"


@respx.mock
async def test_unavailable_variant_skipped_even_when_preorder(crawler):
    # Altered from the captured Gold sibling of _PAREN_PRODUCT: this store
    # flags purchasable pre-orders available, so an unavailable pre-order is
    # a sold-out allocation (confirmed live: its page renders "Sold Out"),
    # not a not-yet-released record. Pins the deliberate absence of the
    # pre-order availability bypass napalmrecords.py/centurymedia.py carry.
    product = {**_PAREN_PRODUCT,
               "variants": [{**_PAREN_PRODUCT["variants"][0], "available": False}]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_unavailable_variant_skipped_when_not_preorder(crawler):
    product = {**_EXACT_PREFIX_PRODUCT,
               "variants": [{**_EXACT_PREFIX_PRODUCT["variants"][0], "available": False}]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_mistyped_cd_is_dropped(crawler):
    # Captured: a CD filed in the vinyl collection under product_type 12".
    product = {**_EXACT_PREFIX_PRODUCT,
               "title": "ARTCH - Another Return / CD",
               "vendor": "Artch"}
    _mock_pages(product, _EXACT_PREFIX_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Vintersorg"


@respx.mock
async def test_digipak_cd_is_dropped(crawler):
    # Captured: the other live mistype in the vinyl collection.
    product = {**_EXACT_PREFIX_PRODUCT,
               "title": "MONOLITHE - Black Hole District / Digipak CD",
               "vendor": "Monolithe"}
    _mock_pages(product, _EXACT_PREFIX_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_counted_cd_form_is_dropped(crawler):
    # Invented: no live vinyl-collection title carries a counted CD form,
    # but "2xCD" defeats a bare \bcd\b -- the spv.py/onetwothreefourgo.py
    # regression this crawler's regexes guard against.
    product = {**_EXACT_PREFIX_PRODUCT,
               "title": "Vintersorg - Till Fjälls / Deluxe 2xCD"}
    _mock_pages(product, _EXACT_PREFIX_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1


@respx.mock
async def test_hybrid_title_with_vinyl_signal_is_kept(crawler):
    # Invented: no live title pairs a non-vinyl word with a vinyl word, but a
    # genuine LP + CD bundle must survive the non-vinyl filter.
    product = {**_EXACT_PREFIX_PRODUCT,
               "title": "Vintersorg - Till Fjälls / Black Vinyl LP + Bonus CD"}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Till Fjälls / Black Vinyl LP + Bonus CD"


@respx.mock
async def test_blank_vendor_product_is_skipped(crawler):
    # Altered: every live product carries a vendor; a blank one has no
    # artist source and follows the fleet's "no artist source -> skip".
    product = {**_EXACT_PREFIX_PRODUCT, "vendor": ""}
    _mock_pages(product, _CAPS_PREFIX_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Trouble"


@respx.mock
async def test_null_variants_product_yields_nothing(crawler):
    product = {**_EXACT_PREFIX_PRODUCT, "variants": None}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_empty_collection_raises(crawler):
    # A renamed or removed collection must raise, not complete empty --
    # _sync_stock would otherwise DELETE the previous snapshot and record
    # the site as succeeding.
    _mock_pages(empty_page=1)
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_all_blank_vendors_raises(crawler):
    # Invented: artist-source drift. If the store stopped writing artists
    # into `vendor`, every product would be silently skipped -- raise so the
    # previous snapshot survives.
    products = [{**_EXACT_PREFIX_PRODUCT, "vendor": ""},
                {**_CAPS_PREFIX_PRODUCT, "vendor": None}]
    _mock_pages(*products)
    with pytest.raises(RuntimeError, match="vendor"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_multi_variant_product_appends_variant_descriptor(crawler):
    # Invented: every live product is single-variant. If the store moved
    # pressing colors into variants, rows sharing (artist, title, url) would
    # collapse onto one item_key and fail the sync in replace_stock_items().
    product = {**_EXACT_PREFIX_PRODUCT, "variants": [
        {"id": 1, "title": "Black", "price": "23.99", "available": True},
        {"id": 2, "title": "Baby Blue", "price": "25.99", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Till Fjälls / Baby Blue Vinyl LP — Black",
        "Till Fjälls / Baby Blue Vinyl LP — Baby Blue",
    ]


@respx.mock
async def test_multi_variant_placeholder_title_falls_back_to_variant_id(crawler):
    # Invented: malformed multi-variant shape -- placeholder and blank
    # variant titles get the immutable variant id as their descriptor.
    product = {**_EXACT_PREFIX_PRODUCT, "variants": [
        {"id": 91, "title": "Default Title", "price": "23.99", "available": True},
        {"id": 92, "title": "", "price": "25.99", "available": True},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Till Fjälls / Baby Blue Vinyl LP — 91",
        "Till Fjälls / Baby Blue Vinyl LP — 92",
    ]


@respx.mock
async def test_multi_variant_with_no_identity_at_all_raises(crawler):
    product = {**_EXACT_PREFIX_PRODUCT, "variants": [
        {"title": "", "price": "23.99", "available": True},
        {"title": "", "price": "25.99", "available": True},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="neither a usable title nor an id"):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Hammerheart Records"
    assert Crawler.base_url == "https://hammerheart.indiemerch.com"
    assert Crawler.genre == "metal"
    assert Crawler.crawler_type == "catalog"
