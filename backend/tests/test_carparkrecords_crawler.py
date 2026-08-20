import httpx
import respx
import pytest
from crawlers.carparkrecords import Crawler

_PRODUCTS_URL = "https://store.carparkrecords.com/collections/music/products.json"

# Real confirmed-live case: space-separated catalog code, preorder-tagged,
# mixed availability, non-vinyl variants (CD/Digital) present alongside LP
# variants -- exercises code-strip, preorder unavailable-keep, and the
# non-vinyl variant filter all in one product.
_DENT_MAY_THE_BIG_ONE = {
    "title": "CAK188 Dent May - The Big One",
    "vendor": "Carpark",
    "handle": "cak188-dent-may-the-big-one",
    "product_type": "Music",
    "tags": ["Carpark Records", "Dent May", "preorder", "the big one"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0805/4266/2938/files/dentmay.jpg"}],
    "variants": [
        {"title": "Limited Edition Carpark Exclusive Red LP", "price": "27.99", "available": False, "featured_image": None},
        {"title": "Limited Edition Olive LP", "price": "26.99", "available": True, "featured_image": None},
        {"title": "CD", "price": "15.99", "available": True, "featured_image": None},
        {"title": "Digital", "price": "9.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: dash-prefixed catalog code ("CAK189 - casi -
# CASI") -- code strip must not consume the artist/title separator too.
_CASI_CASI = {
    "title": "CAK189 - casi - CASI",
    "vendor": "Carpark",
    "handle": "cak189-casi-casi",
    "product_type": "Music",
    "tags": ["Carpark Records", "casi"],
    "images": [],
    "variants": [
        {"title": "Limited Edition Red vinyl", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a literal tab character stands in for a space
# before the artist/title dash -- the split regex must treat it as
# whitespace, same as an ordinary space.
_TANUKICHAN_SPACE_GHOST = {
    "title": "CAK187 - Tanukichan x Space Ghost\t- Circles - Space Ghost Remix",
    "vendor": "Carpark",
    "handle": "cak187-tanukichan-x-space-ghost-circles-space-ghost-remix",
    "product_type": "Music",
    "tags": ["preorder", "space ghost", "Tanukichan"],
    "images": [],
    "variants": [
        {"title": "Limited Edition 12\"", "price": "14.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no catalog code at all -- code-strip regex must
# no-op cleanly, and the product's only variant is an unavailable Digital
# (non-preorder), so no items should be yielded at all.
_TANUKICHAN_MAKE_BELIEVE = {
    "title": "Tanukichan - Make Believe",
    "vendor": "Carpark",
    "handle": "tanukichan-make-believe",
    "product_type": "Music",
    "tags": ["new release", "Tanukichan"],
    "images": [],
    "variants": [
        {"title": "Digital", "price": "1.29", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: no dash separator anywhere in the title --
# falls back to `vendor` ("Carpark", the label's own name) as artist.
_SWEET_SIXTEEN = {
    "title": "CAK104 Carpark Sweet Sixteen Basketball Picture Disc LP",
    "vendor": "Carpark",
    "handle": "cak104-carpark-sweet-sixteen-basketball-picture-disc-lp",
    "product_type": "Music",
    "tags": ["Carpark Records"],
    "images": [],
    "variants": [
        {"title": "Basketball Picture Disc LP", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a genuine vinyl variant with no vinyl/LP
# keyword at all ("Eco Mix Red") alongside an unavailable, non-preorder
# Christmas Ornament bonus-item variant and a Digital variant -- both of
# the latter must be dropped while the keyword-less vinyl variant and the
# real LP variant are both kept.
_PEACE_OF_US = {
    "title": "CAK177 - Dean & Britta & Sonic Boom - A Peace of Us",
    "vendor": "Carpark",
    "handle": "cak177-dean-britta-sonic-boom-a-peace-of-us",
    "product_type": "Music",
    "tags": ["Dean & Britta & Sonic Boom"],
    "images": [],
    "variants": [
        {"title": "LP", "price": "26.99", "available": True, "featured_image": None},
        {"title": "Christmas Ornament", "price": "20.00", "available": False, "featured_image": None},
        {"title": "Digital", "price": "9.99", "available": True, "featured_image": None},
        {"title": "Eco Mix Red", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "Playing Cards" bonus-item variant bundled
# alongside real LP/Cassette/CD/Digital variants -- only the two vinyl
# variants should survive.
_CLOUD_NOTHINGS = {
    "title": "CAK130 Cloud Nothings - Last Building Burning",
    "vendor": "Carpark",
    "handle": "cak130-cloud-nothings-last-building-burning",
    "product_type": "Music",
    "tags": ["Cloud Nothings"],
    "images": [],
    "variants": [
        {"title": "Limited LP (clear vinyl)", "price": "26.99", "available": True, "featured_image": None},
        {"title": "LP", "price": "26.99", "available": True, "featured_image": None},
        {"title": "CD", "price": "15.99", "available": True, "featured_image": None},
        {"title": "Cassette", "price": "10.99", "available": True, "featured_image": None},
        {"title": "Playing Cards", "price": "9.99", "available": True, "featured_image": None},
        {"title": "Digital", "price": "9.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "LP + DVD" bundle variant -- must be kept as
# vinyl (contains "LP"), not dropped as a DVD.
_EXCEPTER_BLACK_BEACH = {
    "title": "PAW28 Excepter - Black Beach",
    "vendor": "Paw Tracks",
    "handle": "paw28-excepter-black-beach",
    "product_type": "Music",
    "tags": ["Excepter"],
    "images": [],
    "variants": [
        {"title": "Digital", "price": "13.99", "available": True, "featured_image": None},
        {"title": "LP + DVD", "price": "27.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: standalone "DVD" variant (no LP paired with
# it) -- must be dropped.
_TAKAGI_MASAKATSU = {
    "title": "CAK036 Takagi Masakatsu - World Is So Beautiful",
    "vendor": "Carpark",
    "handle": "cak036-takagi-masakatsu-world-is-so-beautiful",
    "product_type": "Music",
    "tags": ["Takagi Masakatsu"],
    "images": [],
    "variants": [
        {"title": "DVD", "price": "15.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: compound variant titles with the format word
# as a suffix rather than the whole title ("Gemini I CD", "Gemini II CD")
# -- an exact-match-only regex would miss these; both must be dropped.
_GEMINI = {
    "title": "WIX04/05 Johanna Warren - Gemini I & II",
    "vendor": "Wax Nine",
    "handle": "wix04-05-johanna-warren-gemini-i-ii",
    "product_type": "Music",
    "tags": ["Johanna Warren"],
    "images": [],
    "variants": [
        {"title": "Gemini I CD", "price": "15.99", "available": False, "featured_image": None},
        {"title": "Gemini II CD", "price": "15.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: product_type "Merch" (a print/bundle item, not
# a music release) -- must be excluded entirely regardless of title shape
# or variant contents.
_MERCH_BUNDLE = {
    "title": "CAKD074 Madeline Kenney - Summer Quarter",
    "vendor": "Carpark",
    "handle": "cakd074-madeline-kenney-summer-quarter",
    "product_type": "Merch",
    "tags": ["Madeline Kenney"],
    "images": [],
    "variants": [
        {"title": "Summer Evening' Riso Print + EP Bundle", "price": "19.99", "available": True, "featured_image": None},
        {"title": "Digital", "price": "3.99", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_preorder_keeps_unavailable_drops_non_vinyl(crawler):
    _mock_single_page([_DENT_MAY_THE_BIG_ONE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {
        "The Big One — Limited Edition Carpark Exclusive Red LP (Pre-Order)",
        "The Big One — Limited Edition Olive LP (Pre-Order)",
    }
    assert all(item["artist"] == "Dent May" for item in items)


@respx.mock
async def test_crawl_catalog_dash_prefixed_code(crawler):
    _mock_single_page([_CASI_CASI])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "casi"
    assert items[0]["title"] == "CASI — Limited Edition Red vinyl"


@respx.mock
async def test_crawl_catalog_tab_before_dash(crawler):
    _mock_single_page([_TANUKICHAN_SPACE_GHOST])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Tanukichan x Space Ghost"
    # Also preorder-tagged live -- (Pre-Order) suffix is expected here too.
    assert items[0]["title"] == "Circles - Space Ghost Remix — Limited Edition 12\" (Pre-Order)"


@respx.mock
async def test_crawl_catalog_no_catalog_code_and_no_available_variant(crawler):
    _mock_single_page([_TANUKICHAN_MAKE_BELIEVE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_no_dash_falls_back_to_vendor(crawler):
    _mock_single_page([_SWEET_SIXTEEN])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Carpark"
    # No dash to split on, so the fallback title (the full code-stripped
    # string) and the variant descriptor happen to overlap here -- expected,
    # not special-cased.
    assert items[0]["title"] == "Carpark Sweet Sixteen Basketball Picture Disc LP — Basketball Picture Disc LP"


@respx.mock
async def test_crawl_catalog_keyword_less_vinyl_variant_kept_bonus_item_dropped(crawler):
    _mock_single_page([_PEACE_OF_US])
    items = [item async for item in crawler.crawl_catalog()]
    titles = {item["title"] for item in items}
    assert titles == {"A Peace of Us — LP", "A Peace of Us — Eco Mix Red"}


@respx.mock
async def test_crawl_catalog_playing_cards_dropped(crawler):
    _mock_single_page([_CLOUD_NOTHINGS])
    items = [item async for item in crawler.crawl_catalog()]
    titles = {item["title"] for item in items}
    assert titles == {
        "Last Building Burning — Limited LP (clear vinyl)",
        "Last Building Burning — LP",
    }


@respx.mock
async def test_crawl_catalog_lp_plus_dvd_kept_as_vinyl(crawler):
    _mock_single_page([_EXCEPTER_BLACK_BEACH])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Black Beach — LP + DVD"


@respx.mock
async def test_crawl_catalog_standalone_dvd_dropped(crawler):
    _mock_single_page([_TAKAGI_MASAKATSU])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_compound_cd_suffix_dropped(crawler):
    _mock_single_page([_GEMINI])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_merch_product_type(crawler):
    _mock_single_page([_MERCH_BUNDLE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_CASI_CASI, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Carpark Records"
    assert Crawler.base_url == "https://store.carparkrecords.com"
    assert Crawler.genre == "indie"
    assert Crawler.crawler_type == "catalog"
