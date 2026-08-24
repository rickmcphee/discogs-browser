import httpx
import respx
import pytest
from crawlers.darksiderecords import Crawler

_PRODUCTS_URL = "https://shop.darksiderecords.com/collections/new-vinyl-in-stock/products.json"

# Real confirmed-live case: the dominant title form -- hyphen glued to the
# artist, space only after it. Vendor is the distributor, not the artist.
_PRODUCT = {
    "title": "Jay Reatard- Blood Visions (Vinyl)",
    "vendor": "THE ORCHARD",
    "handle": "jay-reatard-blood-visions",
    "product_type": "New Vinyl/Rock",
    "tags": ["instore-available"],
    "images": [{"src": "https://cdn.shopify.com/jay-reatard-fallback.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "24.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: en-dash used the same asymmetric way as the
# dominant hyphen form (4 products live).
_ENDASH_PRODUCT = {
    "title": "Talib Kweli & Madlib– Liberation 2 (Vinyl)",
    "vendor": "THE ORCHARD",
    "handle": "talib-kweli-madlib-liberation-2",
    "product_type": "New Vinyl/Hip Hop",
    "tags": ["instore-available"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "27.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: a second, spaced hyphen inside the album name.
# The non-greedy artist group must split on the first separator, not the
# later one, leaving the rest of the album intact.
_INNER_HYPHEN_PRODUCT = {
    "title": "The Specials- Live From The Cathedral - Black Vinyl [Import] (Black, United Kingdom - Import)",
    "vendor": "REDEYE MUSIC DISTRIBUTION",
    "handle": "the-specials-live-from-the-cathedral",
    "product_type": "New Vinyl/Rock",
    "tags": ["instore-available"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "72.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: the artist's own name contains a hyphen with no
# adjacent whitespace, so neither regex alternative may split on it. 45 such
# products live ("Run-Dmc", "Jean-Luc Ponty", "Olivia Newton-John").
_HYPHENATED_ARTIST_PRODUCT = {
    "title": "Blink-182- Buddha (Vinyl)",
    "vendor": "AEC",
    "handle": "blink-182-buddha",
    "product_type": "New Vinyl/Punk",
    "tags": ["instore-available"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no separator at all -- a soundtrack with no
# artist to extract. Must be dropped rather than attributed to the
# distributor. 178 of 5,141 products live have this shape.
_NO_SEPARATOR_PRODUCT = {
    "title": "Hocus Pocus (Original Motion Picture Soundtrack) [Blue Jay 2LP Vinyl]",
    "vendor": "WMX",
    "handle": "hocus-pocus-original-motion-picture-soundtrack",
    "product_type": "New Vinyl/Soundtracks",
    "tags": ["instore-available"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "34.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: sleeve-damaged discounted copy. Kept, with the
# "(DAMAGED)" marker preserved verbatim in the title -- that marker is what
# makes the below-market price self-explanatory. 173 emitted live.
_DAMAGED_PRODUCT = {
    "title": "Adam Lambert- Original High [Clear Vinyl] [Limited Edition] (DAMAGED)",
    "vendor": "Alliance - BT",
    "handle": "adam-lambert-original-high-clear-vinyl-limited-edition-damaged",
    "product_type": "New Vinyl/Rock",
    "tags": ["damaged", "ds-import", "instore-available"],
    "images": [{"src": "https://cdn.shopify.com/adam-lambert.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "23.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: one of 3 products in the collection with no
# available variant.
_UNAVAILABLE_PRODUCT = {
    "title": "Joji- Smithereens (Vinyl)",
    "vendor": "WMX",
    "handle": "joji-smithereens-3",
    "product_type": "New Vinyl/Rock",
    "tags": ["ds-import", "instore-available"],
    "images": [{"src": "https://cdn.shopify.com/joji.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "27.99", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live case: a pre-order-tagged product the store already
# marks available. It must be emitted plainly, with no " (Pre-Order)" suffix
# and no bypass of the availability gate -- pins the deliberate absence of
# the sibling crawlers' pre-order handling.
_PREORDER_PRODUCT = {
    "title": "Geese- Getting Killed (Vinyl)",
    "vendor": "AEC",
    "handle": "geese-getting-killed",
    "product_type": "New Vinyl/Rock",
    "tags": ["instore-available", "preorder_bt", "pre-order vinyl"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "29.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: 13 products carry a "Missing image" tag and an
# empty images array -- cover_image_url must be None, not an IndexError.
_NO_IMAGE_PRODUCT = {
    "title": "Elliott Smith- Elliott Smith (Indie Exclusive) (Vinyl)",
    "vendor": "REDEYE MUSIC DISTRIBUTION",
    "handle": "elliott-smith-elliott-smith-indie-exclusive",
    "product_type": "New Vinyl/Rock",
    "tags": ["instore-available", "Missing image"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "28.99", "available": True, "featured_image": None},
    ],
}

# Hypothetical: no non-"New Vinyl" product_type is live in this collection
# (all 5,141 are "New Vinyl/<genre>"), but the store sells books, CDs, board
# games and plush elsewhere. Pins the gate that keeps a misfiled one out.
_NON_VINYL_PRODUCT = {
    "title": "Miles Davis- Kind Of Blue (CD)",
    "vendor": "UMG",
    "handle": "miles-davis-kind-of-blue-cd",
    "product_type": "New CDs/Jazz",
    "tags": ["instore-available"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "12.99", "available": True, "featured_image": None},
    ],
}

# Hypothetical multi-variant product -- none live (all 5,141 products have
# exactly one "Default Title" variant). Two available variants prove a future
# multi-variant product isn't silently reduced to its first; the third,
# unavailable, proves a sold-out sibling is skipped without dropping the rest.
_MULTI_VARIANT_PRODUCT = {
    "title": "Big Thief- Double Infinity (Vinyl)",
    "vendor": "BWSCD, INC.",
    "handle": "big-thief-double-infinity",
    "product_type": "New Vinyl/Rock",
    "tags": ["instore-available"],
    "images": [{"src": "https://cdn.shopify.com/big-thief-fallback.jpg"}],
    "variants": [
        {"id": 48373708947677, "title": "Black Vinyl", "price": "29.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/big-thief-black.jpg"}},
        {"id": 48373708980445, "title": "Indie Exclusive Colored Vinyl", "price": "34.99",
         "available": True, "featured_image": None},
        {"id": 48373709013213, "title": "Repress", "price": "27.99", "available": False,
         "featured_image": None},
    ],
}

_MALFORMED_PRICE_PRODUCT = {
    "title": "Tycho- Awake (Clear Vinyl)",
    "vendor": "THE ORCHARD",
    "handle": "tycho-awake-clear-vinyl",
    "product_type": "New Vinyl/Electronic",
    "tags": ["instore-available"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": None, "available": True, "featured_image": None},
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
async def test_crawl_catalog_parses_artist_and_album_from_glued_hyphen_title(crawler):
    _mock_single_page([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Jay Reatard"
    assert item["title"] == "Blood Visions (Vinyl)"
    assert item["price"] == 24.99
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://shop.darksiderecords.com/products/jay-reatard-blood-visions"
    assert item["cover_image_url"] == "https://cdn.shopify.com/jay-reatard-fallback.jpg"


@respx.mock
async def test_crawl_catalog_splits_en_dash_title(crawler):
    _mock_single_page([_ENDASH_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Talib Kweli & Madlib"
    assert items[0]["title"] == "Liberation 2 (Vinyl)"


@respx.mock
async def test_crawl_catalog_splits_on_first_separator_only(crawler):
    _mock_single_page([_INNER_HYPHEN_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "The Specials"
    assert items[0]["title"] == (
        "Live From The Cathedral - Black Vinyl [Import] (Black, United Kingdom - Import)"
    )


@respx.mock
async def test_crawl_catalog_keeps_hyphenated_artist_name_intact(crawler):
    _mock_single_page([_HYPHENATED_ARTIST_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Blink-182"
    assert items[0]["title"] == "Buddha (Vinyl)"


@respx.mock
async def test_crawl_catalog_drops_product_with_no_separator(crawler):
    _mock_single_page([_NO_SEPARATOR_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_never_falls_back_to_vendor_for_artist(crawler):
    _mock_single_page([_NO_SEPARATOR_PRODUCT, _PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Jay Reatard"]
    assert all(i["artist"] != "WMX" for i in items)


@respx.mock
async def test_crawl_catalog_keeps_damaged_copy_with_marker_in_title(crawler):
    _mock_single_page([_DAMAGED_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Adam Lambert"
    assert items[0]["title"] == "Original High [Clear Vinyl] [Limited Edition] (DAMAGED)"
    assert items[0]["price"] == 23.99


@respx.mock
async def test_crawl_catalog_skips_unavailable_variant(crawler):
    _mock_single_page([_UNAVAILABLE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_emits_preorder_plainly_without_suffix(crawler):
    _mock_single_page([_PREORDER_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Geese"
    assert items[0]["title"] == "Getting Killed (Vinyl)"
    assert "Pre-Order" not in items[0]["title"]


@respx.mock
async def test_crawl_catalog_does_not_bypass_availability_for_preorder(crawler):
    sold_out_preorder = {**_PREORDER_PRODUCT, "variants": [
        {"title": "Default Title", "price": "29.99", "available": False, "featured_image": None},
    ]}
    _mock_single_page([sold_out_preorder])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_handles_product_with_no_images(crawler):
    _mock_single_page([_NO_IMAGE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_crawl_catalog_skips_non_vinyl_product_type(crawler):
    _mock_single_page([_NON_VINYL_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_emits_one_row_per_available_variant(crawler):
    _mock_single_page([_MULTI_VARIANT_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert [i["price"] for i in items] == [29.99, 34.99]


@respx.mock
async def test_crawl_catalog_gives_multi_variant_rows_distinct_item_identities(crawler):
    # db.compute_item_key() hashes exactly (artist, title, url), and the url is
    # per-product, so without a variant descriptor in the title both rows would
    # collapse onto one item_key: one marketplace lookup for two pressings, and
    # two indistinguishable Store rows at different prices.
    _mock_single_page([_MULTI_VARIANT_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Double Infinity (Vinyl) — Black Vinyl",
        "Double Infinity (Vinyl) — Indie Exclusive Colored Vinyl",
    ]
    assert len({(i["artist"], i["title"], i["url"]) for i in items}) == 2


@respx.mock
async def test_crawl_catalog_keeps_variant_identity_stable_when_sibling_sells_out(crawler):
    # The descriptor is gated on the total variant count, not the available
    # one. If it were gated on availability, this product dropping to a single
    # available variant would rewrite that row's title, changing its item_key
    # and orphaning its listings and saved-item rows.
    sold_out_sibling = {**_MULTI_VARIANT_PRODUCT, "variants": [
        {"title": "Black Vinyl", "price": "29.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/big-thief-black.jpg"}},
        {"title": "Indie Exclusive Colored Vinyl", "price": "34.99", "available": False,
         "featured_image": None},
        {"title": "Repress", "price": "27.99", "available": False, "featured_image": None},
    ]}
    _mock_single_page([sold_out_sibling])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Double Infinity (Vinyl) — Black Vinyl"


@respx.mock
async def test_crawl_catalog_omits_descriptor_for_single_variant_product(crawler):
    # The live shape: one "Default Title" variant, so the title stays bare.
    _mock_single_page([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "Blood Visions (Vinyl)"


@respx.mock
async def test_crawl_catalog_falls_back_to_variant_id_for_placeholder_descriptors(crawler):
    # Shopify only issues "Default Title" for a product with exactly one
    # variant, so this shape is malformed data. It still must not collapse to
    # the bare album for every row: that would put both back onto one
    # item_key, the collision the descriptor exists to prevent. A raw id reads
    # poorly, but identity correctness wins here.
    placeholder_variants = {**_MULTI_VARIANT_PRODUCT, "variants": [
        {"id": 111, "title": "Default Title", "price": "29.99", "available": True,
         "featured_image": None},
        {"id": 222, "title": "   ", "price": "31.99", "available": True, "featured_image": None},
    ]}
    _mock_single_page([placeholder_variants])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Double Infinity (Vinyl) — 111",
        "Double Infinity (Vinyl) — 222",
    ]
    assert len({(i["artist"], i["title"], i["url"]) for i in items}) == 2


@respx.mock
async def test_crawl_catalog_falls_back_to_bare_album_when_no_stable_variant_value(crawler):
    # Nothing stable left to disambiguate on -- no title, no id. The bare
    # album is all that remains; the rows do collide, and that is the honest
    # floor rather than a fabricated identity.
    no_identity = {**_MULTI_VARIANT_PRODUCT, "variants": [
        {"title": "Default Title", "price": "29.99", "available": True, "featured_image": None},
        {"title": "", "price": "31.99", "available": True, "featured_image": None},
    ]}
    _mock_single_page([no_identity])
    items = [item async for item in crawler.crawl_catalog()]
    # Asserted as a sequence, not a set: a set would collapse the two
    # identical titles and pass even if one row were silently dropped, which
    # is the one-row-per-available-variant guarantee this branch must keep.
    assert [i["title"] for i in items] == [
        "Double Infinity (Vinyl)",
        "Double Infinity (Vinyl)",
    ]
    assert [i["price"] for i in items] == [29.99, 31.99]


@respx.mock
async def test_crawl_catalog_prefers_variant_image_over_product_image(crawler):
    _mock_single_page([_MULTI_VARIANT_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/big-thief-black.jpg"
    assert items[1]["cover_image_url"] == "https://cdn.shopify.com/big-thief-fallback.jpg"


@respx.mock
async def test_crawl_catalog_emits_row_with_none_price_when_price_malformed(crawler):
    _mock_single_page([_MALFORMED_PRICE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] is None
    assert items[0]["artist"] == "Tycho"


@respx.mock
async def test_crawl_catalog_paginates_until_empty_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_HYPHENATED_ARTIST_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Jay Reatard", "Blink-182"]


def test_site_metadata():
    assert Crawler.site_name == "Darkside Records"
    assert Crawler.base_url == "https://shop.darksiderecords.com"
    assert Crawler.genre == "marketplace"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre_summary
