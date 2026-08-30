import httpx
import pytest
import respx

from crawlers.waterloorecords import Crawler

_PRODUCTS_URL = "https://waterloorecords.com/collections/vinyl-lps/products.json"


# Real confirmed-live product. The plain shape: one in-stock "New" variant,
# a bracketed format suffix, and an artist whose own name is punctuation.
_LOUDEN_UP_NOW = {
    "title": "!!!/CHK CHK CHK - Louden Up Now [LP]",
    "handle": "chk-chk-chk-louden-up-now-lp-03617209341",
    "vendor": "598",
    "product_type": "Vinyl",
    "tags": ["!!!/CHK CHK CHK", "inventory_link_bt", "T&G"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0933/3833/7576/files/645748.jpg?v=1763982840"}],
    "variants": [{"title": "New", "price": "29.99", "available": True, "featured_image": None}],
}

# Real confirmed-live product, and the anchor for the first-hyphen split:
# the album half carries its own " - " run, so a greedy or last-hyphen split
# would report the artist as "10CC - Deceptive Bends".
_DECEPTIVE_BENDS = {
    "title": "10CC - Deceptive Bends - 180gm Vinyl [LP]",
    "handle": "10cc-deceptive-bends-import-lp-0552024016",
    "vendor": "206",
    "product_type": "Vinyl",
    "tags": ["10CC", "AEC", "inventory_link_bt"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0933/3833/7576/files/4170687-2929143.jpg?v=1763989835"}],
    "variants": [{"title": "New", "price": "32.99", "available": True, "featured_image": None}],
}

# Real confirmed-live product: an in-stock variant beside a cheaper
# out-of-stock one. Pins that the availability gate runs before the
# cheapest-price pick, so the $24.99 sold-out variant never sets the price.
_PETRICHOR = {
    "title": "070 Shake - Petrichor [LP]",
    "handle": "070-shake-petrichor-lp-60245876931",
    "vendor": "101",
    "product_type": "Vinyl",
    "tags": ["070 SHAKE", "inventory_link_bt", "UQCL"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0933/3833/7576/files/4378523-3293186.jpg?v=1763989996"}],
    "variants": [
        {"title": "New / Default / Default", "price": "29.99", "available": True, "featured_image": None},
        {"title": "New / Default / 24.99", "price": "24.99", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live product, and the reason the format gate reads
# product_type and not the title: merch titles are ARTIST-REVERSED
# ("<design> - <artist> - TS"), so parsing this one would file a Miles Davis
# t-shirt under the artist "1970 Circle". It also has five in-stock variants
# that all share one URL, so admitting it would emit five item_key
# collisions on top of the wrong artist.
_MILES_DAVIS_TSHIRT = {
    "title": "1970 Circle - Miles Davis - TS",
    "handle": "davis-miles-l-1970-circle-ts-09999801509",
    "vendor": "American Classics",
    "product_type": "T-SHIRT",
    "tags": ["AMER", "DAVIS MILES"],
    "images": [],
    "variants": [
        {"title": "S / New", "price": "21.99", "available": True, "featured_image": None},
        {"title": "M / New", "price": "21.99", "available": True, "featured_image": None},
        {"title": "L / New", "price": "21.99", "available": True, "featured_image": None},
        {"title": "XL / New", "price": "21.99", "available": True, "featured_image": None},
        {"title": "2XL / New", "price": "23.99", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live product. Same artist and same " - Album [Format]"
# shape as the vinyl rows, so only product_type keeps it out.
_CHK_CHK_CHK_CD = {
    "title": "!!!/CHK CHK CHK - !!! [CD]",
    "handle": "chk-chk-chk-cd-61350500392",
    "vendor": "503",
    "product_type": "CD",
    "tags": ["!!!/CHK CHK CHK", "GSL", "inventory_link_bt"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0933/3833/7576/files/369980.jpg?v=1763982814"}],
    "variants": [
        {"title": "New / Default", "price": "13.99", "available": False, "featured_image": None},
        {"title": "New / Alternate", "price": "13.99", "available": False, "featured_image": None},
    ],
}

# Real confirmed-live product: an admitted non-"Vinyl" vinyl product_type
# that happens to be sold out, and carries no images.
_CHRISTMAS_7IN = {
    "title": "!!! CHK CHK CHK - And Anyway It's Christmas [7-IN VINYL]",
    "handle": "chk-chk-chk-and-anyway-its-christmas-45-0106193617",
    "vendor": "503",
    "product_type": "7-IN VINYL",
    "tags": ["!!! CHK CHK CHK", "inventory_link_bt", "WRPR"],
    "images": [],
    "variants": [{"title": "New", "price": "6.99", "available": False, "featured_image": None}],
}

# Synthetic: no live product in the sampled page has two *in-stock* variants,
# but the shape is structurally reachable (products do carry several
# variants; availability is just sparse). Pins the one-row-per-product rule
# that item_key collisions depend on, and the cheapest-of pick.
_SYNTHETIC_TWO_IN_STOCK = {
    "title": "Synthetic Artist - Two In Stock [LP]",
    "handle": "synthetic-artist-two-in-stock-lp",
    "vendor": "999",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/product.jpg"}],
    "variants": [
        {"title": "New", "price": "34.99", "available": True, "featured_image": None},
        {"title": "Used", "price": "18.99", "available": True, "featured_image": None},
    ],
}

# Synthetic: pins that a malformed price does not drop in-stock vinyl.
_SYNTHETIC_BAD_PRICE = {
    "title": "Synthetic Artist - Bad Price [LP]",
    "handle": "synthetic-artist-bad-price-lp",
    "vendor": "999",
    "product_type": "Vinyl",
    "tags": [],
    "images": [],
    "variants": [{"title": "New", "price": None, "available": True, "featured_image": None}],
}

# Synthetic: featured_image is null on every live variant, so only a made-up
# product can prove resolve_cover_image's variant-first preference is wired up.
_SYNTHETIC_VARIANT_IMAGE = {
    "title": "Synthetic Artist - Variant Image [LP]",
    "handle": "synthetic-artist-variant-image-lp",
    "vendor": "999",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/product.jpg"}],
    "variants": [{
        "title": "New",
        "price": "24.99",
        "available": True,
        "featured_image": {"src": "https://cdn.shopify.com/variant.jpg"},
    }],
}

# Synthetic: a title with no spaced hyphen at all has no artist to report.
_SYNTHETIC_NO_DELIMITER = {
    "title": "Untitled Vinyl Oddity",
    "handle": "untitled-vinyl-oddity",
    "vendor": "999",
    "product_type": "Vinyl",
    "tags": [],
    "images": [],
    "variants": [{"title": "New", "price": "19.99", "available": True, "featured_image": None}],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


def test_site_metadata():
    assert Crawler.site_name == "Waterloo Records"
    assert Crawler.base_url == "https://waterloorecords.com"
    assert Crawler.genre == "marketplace"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre_summary


@respx.mock
async def test_parses_artist_album_and_keeps_the_format_bracket(crawler):
    _mock_single_page([_LOUDEN_UP_NOW])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "!!!/CHK CHK CHK",
        # The bracket stays: it is the only thing distinguishing two
        # pressings of one album. See the crawler's comment on `title`.
        "title": "Louden Up Now [LP]",
        "format": "Vinyl",
        "price": 29.99,
        "currency": "USD",
        "url": "https://waterloorecords.com/products/chk-chk-chk-louden-up-now-lp-03617209341",
        "cover_image_url": "https://cdn.shopify.com/s/files/1/0933/3833/7576/files/645748.jpg?v=1763982840",
    }]


@respx.mock
async def test_splits_on_the_first_hyphen_not_the_last(crawler):
    _mock_single_page([_DECEPTIVE_BENDS])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "10CC"
    assert items[0]["title"] == "Deceptive Bends - 180gm Vinyl [LP]"


@respx.mock
async def test_vendor_is_never_used_as_the_artist(crawler):
    # vendor is a numeric supplier code ("598", "206"), not a label or an
    # artist -- unlike every sibling Shopify crawler in this repo.
    _mock_single_page([_LOUDEN_UP_NOW, _DECEPTIVE_BENDS])
    items = [item async for item in crawler.crawl_catalog()]
    assert {item["artist"] for item in items} == {"!!!/CHK CHK CHK", "10CC"}


@respx.mock
async def test_drops_cds_cassettes_and_merch_by_product_type(crawler):
    _mock_single_page([_CHK_CHK_CHK_CD, _MILES_DAVIS_TSHIRT])
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_merch_never_leaks_a_reversed_artist(crawler):
    # Regression guard with teeth: this t-shirt has five in-stock variants
    # and a reversed title, so a title-based format gate would emit five
    # colliding rows attributed to "1970 Circle" instead of Miles Davis.
    _mock_single_page([_MILES_DAVIS_TSHIRT, _LOUDEN_UP_NOW])
    items = [item async for item in crawler.crawl_catalog()]
    assert [item["artist"] for item in items] == ["!!!/CHK CHK CHK"]


@respx.mock
async def test_admits_non_lp_vinyl_product_types(crawler):
    _mock_single_page([{**_CHRISTMAS_7IN, "variants": [
        {"title": "New", "price": "6.99", "available": True, "featured_image": None},
    ]}])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "!!! CHK CHK CHK"
    # The specific cut lives in the title bracket; `format` stays "Vinyl".
    assert items[0]["title"] == "And Anyway It's Christmas [7-IN VINYL]"
    assert items[0]["format"] == "Vinyl"


@respx.mock
async def test_sold_out_product_yields_nothing(crawler):
    _mock_single_page([_CHRISTMAS_7IN])
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_out_of_stock_variant_never_sets_the_price(crawler):
    _mock_single_page([_PETRICHOR])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    # The $24.99 variant is cheaper but sold out; availability gates first.
    assert items[0]["price"] == 29.99


@respx.mock
async def test_one_row_per_product_at_the_cheapest_in_stock_price(crawler):
    # Two in-stock variants share (artist, title, url), so two rows would
    # collide on item_key. Exactly one row, priced at the cheaper variant.
    _mock_single_page([_SYNTHETIC_TWO_IN_STOCK])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] == 18.99


@respx.mock
async def test_malformed_price_still_emits_the_row(crawler):
    _mock_single_page([_SYNTHETIC_BAD_PRICE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] is None
    assert items[0]["title"] == "Bad Price [LP]"


@respx.mock
async def test_cover_image_falls_back_to_the_product_image(crawler):
    _mock_single_page([_LOUDEN_UP_NOW])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == (
        "https://cdn.shopify.com/s/files/1/0933/3833/7576/files/645748.jpg?v=1763982840"
    )


@respx.mock
async def test_cover_image_is_none_when_the_product_has_no_images(crawler):
    _mock_single_page([_SYNTHETIC_BAD_PRICE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_cover_image_prefers_the_variant_image(crawler):
    _mock_single_page([_SYNTHETIC_VARIANT_IMAGE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/variant.jpg"


@respx.mock
async def test_title_without_a_delimiter_is_skipped(crawler):
    _mock_single_page([_SYNTHETIC_NO_DELIMITER])
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_paginates_until_an_empty_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_LOUDEN_UP_NOW])
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_DECEPTIVE_BENDS])
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([])
    )
    items = [item async for item in crawler.crawl_catalog()]
    assert [item["artist"] for item in items] == ["!!!/CHK CHK CHK", "10CC"]
