import httpx
import respx
import pytest
from crawlers.realgonemusic import Crawler

_PRODUCTS_URL = "https://realgonemusic.com/collections/vinyl/products.json"

# Real confirmed-live case, and the anchor for this store's central quirk:
# the artist ("3 Inches of Blood") is plainly present in the product title
# but there is no delimiter separating it from the album, and `vendor` is
# the label. Its only variant is unavailable, so this product yields
# nothing -- it exists here to pin the parse via _items() directly.
_THREE_INCHES_OF_BLOOD = {
    "title": "3 Inches of Blood Advance and Vanquish LP",
    "vendor": "Real Gone Music",
    "handle": "3-inches-of-blood-advance-and-vanquish-lp",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064017554_packshot.jpg?v=1721758507"},
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064017554_vinyl.jpg?v=1721758507"},
    ],
    "variants": [
        {
            "title": "Orange & Black",
            "price": "31.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064017554_packshot.jpg?v=1721758507"},
        },
    ],
}

# Real confirmed-live case: two in-stock colour variants alongside an
# in-stock multi-item bundle ($97.99 against a $23.99 single LP) and a
# sold-out Wax Mage. Exercises the bundle drop, the availability gate, and
# per-variant cover images all in one product.
_BARBARA_LEWIS = {
    "title": "Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl",
    "vendor": "Real Gone Music",
    "handle": "barbara-lewis-the-many-grooves-of-barbara-lewis-all-analog-vinyl",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020554_packshot.jpg?v=1771100316"},
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020394_packshot.jpg?v=1771100316"},
    ],
    "variants": [
        {
            "title": "Black Vinyl",
            "price": "23.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020394_packshot.jpg?v=1771100316"},
        },
        {
            "title": "Purple PET Vinyl",
            "price": "23.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020554_packshot.jpg?v=1771100316"},
        },
        {
            "title": "Barbara Lewis Bundle",
            "price": "97.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/BarbaraLewisBundle.jpg?v=1771100316"},
        },
        {
            "title": "Wax Mage",
            "price": "74.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/Barbara_Lewis_Wax_Mage_3.jpg?v=1771961153"},
        },
    ],
}

# Real confirmed-live case: the product's only in-stock variant is a
# bundle, so the bundle drop removes the product from the catalog
# entirely. One of exactly 3 such products live -- the accepted cost the
# design spec records for gate 2.
_CANDIDO = {
    "title": "Candido Dancin' and Prancin' LP",
    "vendor": "Real Gone Music",
    "handle": "candido-dancin-and-prancin-lp",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018117_packshot.jpg?v=1724193558"},
    ],
    "variants": [
        {
            "title": "Black",
            "price": "21.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018117_packshot.jpg?v=1724193558"},
        },
        {
            "title": "Candido Bundle",
            "price": "69.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/candidobundle.jpg?v=1720480957"},
        },
    ],
}

# Real confirmed-live case: Shopify's "Default Title" placeholder, and a
# variant with no featured_image so cover art must fall back to the
# product's first image.
_BOB_FRANK_TEST_PRESSING = {
    "title": "Bob Frank Broke Again Test Pressing",
    "vendor": "Real Gone Music",
    "handle": "bob-frank-broke-again-test-pressing",
    "tags": ["Real Gone Collectibles", "Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/image.png?v=1733861180"},
    ],
    "variants": [
        {"title": "Default Title", "price": "50.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: the OTHER placeholder spelling. 6 live products
# use "Default Title" and 4 use bare "Default", so the sibling crawlers'
# `== "Default Title"` check would miss these 4.
_BILL_LOOSE_TEST_PRESSING = {
    "title": "Bill Loose Cherry, Harry & Raquel Test Pressing",
    "vendor": "Real Gone Music",
    "handle": "bill-loose-cherry-harry-raquel-test-pressing",
    "tags": ["Real Gone Collectibles", "Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/products/1007e3d53e50772fa3a49d3c2efd7099_5cec5a5c-be5a-46c9-b161-abea77ef9b89.jpg?v=1645724959"},
    ],
    "variants": [
        {"title": "Default", "price": "35.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live product, with ONE field changed: live, this variant
# is "available": False, and it is flipped to True here. It is the only
# live product whose variant title repeats its product title verbatim, so
# it is the only realistic literal for the equality collapse -- but with
# the live availability it would be dropped by gate 1 and never reach
# _compose_title. The design spec records this collapse as defensive
# rather than live-exercised for exactly this reason.
_BUCKCHERRY = {
    "title": "Buckcherry 15 (2-LP Set)",
    "vendor": "Real Gone Music",
    "handle": "buckcherry-15-2-lp-set",
    "tags": ["Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018858_mockup.jpg?v=1731023527"},
    ],
    "variants": [
        {
            "title": "Buckcherry 15 (2-LP Set)",
            "price": "44.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064018858_mockup.jpg?v=1731023527"},
        },
    ],
}

# Real confirmed-live case: "Upcoming"-tagged (a genuine pre-order -- its
# product page carries a STREET DATE banner), with one in-stock variant
# whose title carries no vinyl/LP keyword at all and one sold-out variant.
# Pins BOTH deliberate departures at once: the keyword-less variant
# survives (no format filter) and the sold-out one does not (no pre-order
# bypass), with no " (Pre-Order)" suffix anywhere.
_THE_DONNAS = {
    "title": "The Donnas The Donnas (All-Analog) Vinyl",
    "vendor": "Real Gone Music",
    "handle": "the-donnas-the-donnas-lp",
    "tags": ["Upcoming", "Vinyl"],
    "images": [
        {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064022015_mockup.jpg?v=1783551977"},
    ],
    "variants": [
        {
            "title": "Clear with Black & Purple “Inksplosion” PET Plastic",
            "price": "24.99",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/848064022015_mockup.jpg?v=1783551977"},
        },
        {
            "title": "Wax Mage Vinyl",
            "price": "89.99",
            "available": False,
            "featured_image": {"src": "https://cdn.shopify.com/s/files/1/0810/5567/files/donnas_st_wm3.jpg?v=1787077612"},
        },
    ],
}

# Synthetic: no live product has a malformed price. Shopify serves prices
# as decimal strings, so this pins the float() guard against a schema
# change rather than a case seen in the wild.
_SYNTHETIC_BAD_PRICE = {
    "title": "Synthetic Artist Synthetic Album LP",
    "vendor": "Real Gone Music",
    "handle": "synthetic-artist-synthetic-album-lp",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "Black", "price": None, "available": True, "featured_image": None},
        {"title": "Green", "available": True, "featured_image": None},
    ],
}


# Synthetic: no live variant has an empty or whitespace-only title. This
# pins the third _compose_title collapse, which is defensive -- without it
# an empty descriptor would render as a dangling "Product Title — ".
_SYNTHETIC_EMPTY_VARIANT_TITLE = {
    "title": "Synthetic Artist Empty Variant LP",
    "vendor": "Real Gone Music",
    "handle": "synthetic-artist-empty-variant-lp",
    "tags": ["Vinyl"],
    "images": [],
    "variants": [
        {"title": "   ", "price": "24.99", "available": True, "featured_image": None},
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


def test_artist_is_always_the_vendor_never_parsed_from_the_title():
    # The central accepted gap: "3 Inches of Blood" is right there in the
    # title, and the crawler must still report the label. See the design
    # spec's "Artist attribution" section before changing this.
    items = Crawler._items(_BARBARA_LEWIS)
    assert [item["artist"] for item in items] == ["Real Gone Music", "Real Gone Music"]
    # Asserted via _items() because this product's only variant is
    # unavailable, so crawl_catalog() yields nothing for it.
    assert Crawler._items({**_THREE_INCHES_OF_BLOOD, "variants": [
        {"title": "Orange & Black", "price": "31.99", "available": True, "featured_image": None},
    ]})[0]["artist"] == "Real Gone Music"


@respx.mock
async def test_crawl_catalog_drops_bundle_keeps_colour_variants(crawler):
    _mock_single_page([_BARBARA_LEWIS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert {item["title"] for item in items} == {
        "Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl — Black Vinyl",
        "Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl — Purple PET Vinyl",
    }
    assert all(item["format"] == "Vinyl" for item in items)
    assert all(item["currency"] == "USD" for item in items)
    assert all(item["price"] == 23.99 for item in items)
    assert all(
        item["url"]
        == "https://realgonemusic.com/products/barbara-lewis-the-many-grooves-of-barbara-lewis-all-analog-vinyl"
        for item in items
    )


@respx.mock
async def test_crawl_catalog_prefers_variant_image_over_product_image(crawler):
    _mock_single_page([_BARBARA_LEWIS])
    items = [item async for item in crawler.crawl_catalog()]
    by_title = {item["title"]: item for item in items}
    black = by_title["Barbara Lewis The Many Grooves of Barbara Lewis (All-Analog) Vinyl — Black Vinyl"]
    assert black["cover_image_url"] == (
        "https://cdn.shopify.com/s/files/1/0810/5567/files/848064020394_packshot.jpg?v=1771100316"
    )


@respx.mock
async def test_crawl_catalog_falls_back_to_product_image(crawler):
    _mock_single_page([_BOB_FRANK_TEST_PRESSING])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["cover_image_url"] == (
        "https://cdn.shopify.com/s/files/1/0810/5567/files/image.png?v=1733861180"
    )


@respx.mock
async def test_crawl_catalog_bundle_only_product_yields_nothing(crawler):
    _mock_single_page([_CANDIDO])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_default_title_variant_not_suffixed(crawler):
    _mock_single_page([_BOB_FRANK_TEST_PRESSING])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Bob Frank Broke Again Test Pressing"


@respx.mock
async def test_crawl_catalog_bare_default_variant_not_suffixed(crawler):
    _mock_single_page([_BILL_LOOSE_TEST_PRESSING])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Bill Loose Cherry, Harry & Raquel Test Pressing"


@respx.mock
async def test_crawl_catalog_variant_title_matching_product_title_not_doubled(crawler):
    _mock_single_page([_BUCKCHERRY])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Buckcherry 15 (2-LP Set)"


@respx.mock
async def test_crawl_catalog_no_format_filter_and_no_preorder_handling(crawler):
    _mock_single_page([_THE_DONNAS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == (
        "The Donnas The Donnas (All-Analog) Vinyl — Clear with Black & Purple “Inksplosion” PET Plastic"
    )
    assert "(Pre-Order)" not in items[0]["title"]


@respx.mock
async def test_crawl_catalog_malformed_price_becomes_none(crawler):
    _mock_single_page([_SYNTHETIC_BAD_PRICE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    assert all(item["price"] is None for item in items)


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    _mock_single_page([{**_BARBARA_LEWIS, "variants": None}])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_whitespace_variant_title_not_suffixed(crawler):
    _mock_single_page([_SYNTHETIC_EMPTY_VARIANT_TITLE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Synthetic Artist Empty Variant LP"


def test_site_metadata():
    assert Crawler.site_name == "Real Gone Music"
    assert Crawler.base_url == "https://realgonemusic.com"
    assert Crawler.genre == "marketplace"
    assert Crawler.crawler_type == "catalog"
