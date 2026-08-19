import httpx
import respx
import pytest
from crawlers.no_idea_records import Crawler

_PRODUCTS_URL = "https://noidearecords.com/collections/list/products.json"

# Real confirmed-live case: quoted album, single vinyl variant, trailing
# descriptive text ("+ POSTER") outside the closing quote must not leak into
# the parsed album title.
_A_WILHELM_SCREAM = {
    "title": 'A WILHELM SCREAM "Partycrasher" + POSTER',
    "vendor": "No Idea Records",
    "handle": "a-wilhelm-scream-partycrasher-poster",
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0254/9599/products/awilhelmscream.jpg"}],
    "variants": [
        {"title": "RED VINYL + POSTER LP", "price": "20.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quotes at all (parenthetical text only) --
# falls back to vendor like _CLEVELAND_BOUND, confirming the parentheses
# don't accidentally trigger a false quote-match.
_ACRID_BUZZSAW = {
    "title": 'ACRID / LEFT FOR DEAD BUZZSAW (BLUE-GREEN VARIANT)',
    "vendor": "No Idea Records",
    "handle": "acrid-left-for-dead-buzzsaw-blue-green",
    "images": [],
    "variants": [
        {"title": "BLUE-GREEN BUZZSAW-SHAPED LP", "price": "99.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quoted album at all -- falls back to vendor
# as the artist.
_CLEVELAND_BOUND = {
    "title": "CLEVELAND BOUND DEATH SENTENCE",
    "vendor": "No Idea Records",
    "handle": "cleveland-bound-death-sentence",
    "images": [],
    "variants": [
        {"title": "LP", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: mixed vinyl + CD variants on the same product --
# the CD variant must be dropped, only the vinyl variant survives.
_ARMALITE = {
    "title": 'ARMALITE "Armalite"',
    "vendor": "No Idea Records",
    "handle": "armalite-armalite",
    "images": [],
    "variants": [
        {"title": "CD", "price": "10.00", "available": True, "featured_image": None},
        {"title": "LP", "price": "14.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a cassette-and-download-only product with no
# vinyl variant at all -- every variant dropped, item list is empty.
_ACHERS = {
    "title": 'ACHERS "Bottom of the Hill" TAPE',
    "vendor": "No Idea Records",
    "handle": "achers-bottom-of-the-hill-tape",
    "images": [],
    "variants": [
        {"title": "CASSETTE TAPE", "price": "10.00", "available": True, "featured_image": None},
        {"title": "Download (lossless)", "price": "4.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: vinyl LP alongside CD and Download siblings on
# the same product -- only the LP variant survives, the other two are
# dropped, and the vinyl variant is not required to be the only one present.
_AMPERE_LIKE_SHADOWS = {
    "title": 'AMPERE "Like Shadows"',
    "vendor": "No Idea Records",
    "handle": "ampere-like-shadows",
    "images": [],
    "variants": [
        {"title": "LP", "price": "14.00", "available": True, "featured_image": None},
        {"title": "CD", "price": "10.00", "available": True, "featured_image": None},
        {"title": "Download", "price": "8.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: multiple genuine vinyl color variants -- both
# survive, each title suffixed with its own variant descriptor.
_ASSHOLEPARADE = {
    "title": 'ASSHOLEPARADE "Student Ghetto Violence"',
    "vendor": "No Idea Records",
    "handle": "assholeparade-student-ghetto-violence",
    "images": [],
    "variants": [
        {"title": "GREEN VINYL LP+CD", "price": "18.00", "available": True, "featured_image": None},
        {"title": "CD", "price": "10.00", "available": True, "featured_image": None},
        {"title": "PURPLE VINYL LP", "price": "18.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: this store's curly right double quotation mark
# (U+201D, "”") used for the inch mark on a 7" variant -- must still be
# recognized as vinyl, not dropped as an unrecognized format.
_AGAINST_ME_CURLY_QUOTE = {
    "title": 'AGAINST ME! "Sink, Florida, Sink / Unsubstantiated Rumors"',
    "vendor": "No Idea Records",
    "handle": "against-me-sink-florida-sink",
    "images": [],
    "variants": [
        {"title": "DARK GREEN 7\u201d", "price": "29.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: straight quote inch mark also recognized.
_AMPERE_RAEIN_SPLIT = {
    "title": 'AMPERE / RAEIN "Split"',
    "vendor": "No Idea Records",
    "handle": "ampere-raein-split",
    "images": [],
    "variants": [
        {"title": 'BLUE 8"', "price": "12.00", "available": True, "featured_image": None},
    ],
}

# Synthetic case (not confirmed live on this store): the third inch-mark
# alternative in _VINYL_RE's character class, the double-prime mark
# (U+2033, "″"), distinct from both the curly right double quotation
# mark (U+201D) and the straight quote already covered above. Added per
# code review so this branch of the character class can't be silently
# dropped by a future edit -- the same failure mode that already hit
# _TITLE_RE once on this branch.
_DOUBLE_PRIME_INCH_MARK = {
    "title": 'REPLICATOR "Test Pattern"',
    "vendor": "No Idea Records",
    "handle": "replicator-test-pattern",
    "images": [],
    "variants": [
        {"title": "GREEN 10″", "price": "16.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a genuine vinyl pressing whose only variant-title
# signal is a bare color name -- no "LP"/"vinyl"/inch-mark token anywhere.
# No structural signal distinguishes this from real non-vinyl noise, so it's
# dropped as documented accepted noise in the design spec.
_DEFIANCE_OHIO_BARE_COLOR = {
    "title": "DEFIANCE, OHIO \"The Great Depression\" (BLUE) (LTD to 203)",
    "vendor": "No Idea Records",
    "handle": "defiance-ohio-the-great-depression-blue",
    "images": [],
    "variants": [
        {"title": "TRANSLUCENT BLUE", "price": "12.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: unavailable vinyl variant is skipped, no
# pre-order carve-out exists on this store.
_UNAVAILABLE_VARIANT = {
    "title": 'WORN IN RED "Banshees" TEST PRESSING',
    "vendor": "No Idea Records",
    "handle": "worn-in-red-banshees-test-pressing",
    "images": [],
    "variants": [
        {"title": "TEST PRESSING LP", "price": "20.99", "available": False, "featured_image": None},
    ],
}

# Regression case for the _TITLE_RE fix: curly quotes used as the
# artist/album delimiter in the product title itself (not just in a
# variant's inch mark) -- must still parse correctly, not fall back to
# vendor.
_CURLY_QUOTE_TITLE = {
    "title": 'SOME BAND “Album Name” + POSTER',
    "vendor": "No Idea Records",
    "handle": "some-band-album-name-poster",
    "images": [],
    "variants": [
        {"title": "RED VINYL LP", "price": "18.00", "available": True, "featured_image": None},
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
async def test_crawl_catalog_parses_quoted_album_with_trailing_text(crawler):
    _mock_single_page([_A_WILHELM_SCREAM])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "A WILHELM SCREAM"
    assert item["title"] == "Partycrasher — RED VINYL + POSTER LP"
    assert item["price"] == 20.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://noidearecords.com/products/a-wilhelm-scream-partycrasher-poster"
    assert item["cover_image_url"] == "https://cdn.shopify.com/s/files/1/0254/9599/products/awilhelmscream.jpg"


@respx.mock
async def test_crawl_catalog_no_quotes_falls_back_to_vendor(crawler):
    _mock_single_page([_CLEVELAND_BOUND])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "No Idea Records"
    assert items[0]["title"] == "CLEVELAND BOUND DEATH SENTENCE — LP"


@respx.mock
async def test_crawl_catalog_drops_cd_sibling_variant(crawler):
    _mock_single_page([_ARMALITE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Armalite — LP"
    assert items[0]["price"] == 14.00


@respx.mock
async def test_crawl_catalog_drops_cassette_and_download_variants(crawler):
    _mock_single_page([_ACHERS])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_vinyl_drops_cd_and_download_siblings(crawler):
    _mock_single_page([_AMPERE_LIKE_SHADOWS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Like Shadows — LP"
    assert items[0]["price"] == 14.00


@respx.mock
async def test_crawl_catalog_keeps_multiple_vinyl_color_variants(crawler):
    _mock_single_page([_ASSHOLEPARADE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {
        "Student Ghetto Violence — GREEN VINYL LP+CD",
        "Student Ghetto Violence — PURPLE VINYL LP",
    }
    assert all(item["artist"] == "ASSHOLEPARADE" for item in items)


@respx.mock
async def test_crawl_catalog_recognizes_curly_quote_inch_mark(crawler):
    _mock_single_page([_AGAINST_ME_CURLY_QUOTE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Sink, Florida, Sink / Unsubstantiated Rumors — DARK GREEN 7\u201d"


@respx.mock
async def test_crawl_catalog_recognizes_straight_quote_inch_mark(crawler):
    _mock_single_page([_AMPERE_RAEIN_SPLIT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == 'Split — BLUE 8"'


@respx.mock
async def test_crawl_catalog_recognizes_double_prime_inch_mark(crawler):
    _mock_single_page([_DOUBLE_PRIME_INCH_MARK])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Test Pattern — GREEN 10″"


@respx.mock
async def test_crawl_catalog_drops_bare_color_variant_with_no_format_token(crawler):
    _mock_single_page([_DEFIANCE_OHIO_BARE_COLOR])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_unavailable_variant(crawler):
    _mock_single_page([_UNAVAILABLE_VARIANT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_parenthetical_no_quotes_falls_back_to_vendor(crawler):
    _mock_single_page([_ACRID_BUZZSAW])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "No Idea Records"
    assert items[0]["title"] == "ACRID / LEFT FOR DEAD BUZZSAW (BLUE-GREEN VARIANT) — BLUE-GREEN BUZZSAW-SHAPED LP"


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_A_WILHELM_SCREAM, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_parses_curly_quote_title_delimiter(crawler):
    _mock_single_page([_CURLY_QUOTE_TITLE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "SOME BAND"
    assert items[0]["title"] == "Album Name — RED VINYL LP"


def test_site_metadata():
    assert Crawler.site_name == "No Idea Records"
    assert Crawler.base_url == "https://noidearecords.com"
    assert Crawler.genre == "punk"
    assert Crawler.crawler_type == "catalog"
