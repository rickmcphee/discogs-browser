import pytest

from crawlers.cleorecs import Crawler


@pytest.mark.parametrize("title,expected_artist,expected_album", [
    # Ordinary shape: split on " - ", parenthetical stays on the album.
    (
        "UFO - A Conspiracy Of Stars (Colored Double Vinyl LP)",
        "UFO",
        "A Conspiracy Of Stars (Colored Double Vinyl LP)",
    ),
    # En-dash separator. 34 live titles use it; only bigscarymonstersusa.py
    # allows this character today.
    (
        "U.K. Subs – Endangered Species (2 LP)",
        "U.K. Subs",
        "Endangered Species (2 LP)",
    ),
    # Hyphenated artist name. 18 live artists have an internal hyphen with no
    # surrounding space; a plain \s*-\s* split clips this to "Anti".
    (
        "Anti-Flag - Die For The Government (Picture Disc Vinyl)",
        "Anti-Flag",
        "Die For The Government (Picture Disc Vinyl)",
    ),
    # Non-greedy: only the FIRST separator splits, so a dash inside the album
    # title survives.
    (
        "Ministry - Hate To Go - Take Out Or Delivery (Colored Vinyl LP or Deluxe Box Set)",
        "Ministry",
        "Hate To Go - Take Out Or Delivery (Colored Vinyl LP or Deluxe Box Set)",
    ),
])
def test_parse_artist_title_splits_on_first_separator(title, expected_artist, expected_album):
    assert Crawler._parse_artist_title(title) == (expected_artist, expected_album)


def test_parse_artist_title_ignores_separator_inside_trailing_parenthetical():
    # 11 live titles have no artist prefix but do contain " - " inside their
    # trailing bracket. Splitting on it yields the artist
    # "Danzig Sings Elvis (Gatefold Green Vinyl LP".
    title = "Danzig Sings Elvis (Gatefold Green Vinyl LP - Signed by Glenn Danzig)"
    assert Crawler._parse_artist_title(title) == ("Various", title)


def test_parse_artist_title_falls_back_to_various_no_separator():
    # 161 live products carry no artist in the title, overwhelmingly the
    # label's own compilations. "Various" is the literal string Discogs
    # uses, so library matching still works.
    title = "Punk Rock Christmas (Black Vinyl LP Test Pressing)"
    assert Crawler._parse_artist_title(title) == ("Various", title)


def test_strip_trailing_parens_removes_groups_right_to_left():
    assert Crawler._strip_trailing_parens(
        "Alleluia! The Devil's Carnival (Original Motion Picture 2015 Soundtrack) "
        "(Limited Edition Red & Black Marble LP)"
    ) == "Alleluia! The Devil's Carnival"


def test_strip_trailing_parens_stops_at_unbracketed_trailing_text():
    # Live title with text appended after a closing bracket. The strip must
    # stop there, leaving the string a prefix of the original.
    title = (
        "Anti-Flag - Die For The Government (Limited Edition Pink Vinyl)Out Of Print "
        "(Jacket cover has ding Right corner crease )"
    )
    assert Crawler._strip_trailing_parens(title) == (
        "Anti-Flag - Die For The Government (Limited Edition Pink Vinyl)Out Of Print"
    )


_MULTI_COLOUR_PRODUCT = {
    "title": "UFO - A Conspiracy Of Stars (Colored Double Vinyl LP)",
    "vendor": "Cleopatra Records",
    "handle": "ufo-a-conspiracy-of-stars-colored-double-vinyl-lp",
    "product_type": "LP",
    "tags": ["Cleopatra Records", "Double LP", "Pre-Orders", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/product-fallback.png"}],
    "variants": [
        {
            "title": "Red Marble",
            "price": "38.98",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/CLO6869LP-RD-MAR-1.png"},
        },
        {
            "title": "Blue Marble",
            "price": "38.98",
            "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/CLO6878LP-BL-MAR-1.png"},
        },
    ],
}

_DEFAULT_TITLE_PRODUCT = {
    "title": "Anti-Flag - Die For The Government (Picture Disc Vinyl)",
    "vendor": "New Red Archives",
    "handle": "anti-flag-die-for-the-government-picture-disc-vinyl",
    "product_type": "LP",
    "tags": ["Anti-Flag", "Punk", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/CLO2659PD-1-1.png"}],
    "variants": [
        {"title": "Default Title", "price": "24.98", "available": True, "featured_image": None},
    ],
}

_SEVEN_INCH_PRODUCT = {
    "title": "Iggy & The Stooges - Cock In My Pocket (Red 7\" Vinyl)",
    "vendor": "Cleopatra Records",
    "handle": "iggy-the-stooges-cock-in-my-pocket-red-7-vinyl",
    "product_type": "SP",
    "tags": ["7 Inch Vinyl", "Punk Rock"],
    "images": [{"src": "https://cdn.shopify.com/R-1889634-1250363226_1.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "29.99", "available": True, "featured_image": None},
    ],
}

_POSTER_PRODUCT = {
    "title": "Revolting Cocks (12\" x 12\" Poster)",
    "vendor": "Cleopatra Records",
    "handle": "revolting-cocks-12-x-12-poster",
    "product_type": "PS",
    "tags": ["Merch", "Poster"],
    "images": [{"src": "https://cdn.shopify.com/MER0250PS-2.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "29.98", "available": True, "featured_image": None},
    ],
}

_SHIRT_BUNDLE_PRODUCT = {
    "title": "Tank - Filth Hounds Of Hades (Double Vinyl LP + Shirt + Tote Bag Bundle)",
    "vendor": "Cleopatra Records",
    "handle": "tank-filth-hounds-of-hades-double-vinyl-lp-shirt-tote-bag-bundle",
    "product_type": "BND",
    "tags": ["Bundle", "Merch", "T-Shirt"],
    "images": [{"src": "https://cdn.shopify.com/CLO7380LP-BND.png"}],
    "variants": [
        {"title": "Short Sleeve Shirt - Small", "price": "77.97", "available": True, "featured_image": None},
        {"title": "Long Sleeve Shirt - XX-Large", "price": "85.97", "available": True, "featured_image": None},
    ],
}

_BOOK_PRODUCT = {
    "title": "The Dickies And Me by Leonard Graves Phillips (Hardback Book + 7\" Vinyl)",
    "vendor": "Cleopatra Records",
    "handle": "the-dickies-and-me-by-leonard-graves-phillips-hardback-book-7-vinyl",
    "product_type": "BK",
    "tags": ["book", "Hardback Book", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/CLO7264BK-1.png"}],
    "variants": [
        {"title": "Default Title", "price": "69.98", "available": True, "featured_image": None},
    ],
}


def test_items_emits_one_row_per_colour_variant_with_its_own_image():
    items = Crawler._items(_MULTI_COLOUR_PRODUCT)
    assert [i["title"] for i in items] == [
        "A Conspiracy Of Stars (Colored Double Vinyl LP) — Red Marble",
        "A Conspiracy Of Stars (Colored Double Vinyl LP) — Blue Marble",
    ]
    assert [i["cover_image_url"] for i in items] == [
        "https://cdn.shopify.com/CLO6869LP-RD-MAR-1.png",
        "https://cdn.shopify.com/CLO6878LP-BL-MAR-1.png",
    ]
    assert all(i["artist"] == "UFO" for i in items)
    assert all(i["price"] == 38.98 for i in items)


def test_items_omits_shopify_default_title_placeholder():
    # 2,650 of 3,151 live available variants are named "Default Title";
    # appending it the way subpopmegamart.py and twentybuckspin.py do would
    # stamp "— Default Title" onto almost every row.
    items = Crawler._items(_DEFAULT_TITLE_PRODUCT)
    assert len(items) == 1
    assert items[0]["title"] == "Die For The Government (Picture Disc Vinyl)"
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/CLO2659PD-1-1.png"


def test_items_emits_full_row_shape():
    items = Crawler._items(_DEFAULT_TITLE_PRODUCT)
    assert items[0] == {
        "artist": "Anti-Flag",
        "title": "Die For The Government (Picture Disc Vinyl)",
        "format": "Vinyl",
        "price": 24.98,
        "currency": "USD",
        "url": "https://cleorecs.com/products/anti-flag-die-for-the-government-picture-disc-vinyl",
        "cover_image_url": "https://cdn.shopify.com/CLO2659PD-1-1.png",
    }


def test_items_reports_seven_inch_singles_as_vinyl():
    # ebay_api.FORMAT_KEYWORDS/FORMAT_CATEGORY_IDS are keyed on "Vinyl"; a
    # 7" value would resolve both to None and drop eBay's filters.
    items = Crawler._items(_SEVEN_INCH_PRODUCT)
    assert len(items) == 1
    assert items[0]["format"] == "Vinyl"


@pytest.mark.parametrize("product", [_POSTER_PRODUCT, _SHIRT_BUNDLE_PRODUCT, _BOOK_PRODUCT])
def test_items_drops_non_vinyl_products(product):
    assert Crawler._items(product) == []


def test_items_drops_merch_typed_as_vinyl():
    # product_type is correct on today's data, but 20 Buck Spin hit a tote bag
    # typed "VINYL" live, so the title keyword check backs it up.
    product = {**_DEFAULT_TITLE_PRODUCT, "product_type": "LP",
               "title": "Cleopatra Records - Logo Tote Bag"}
    assert Crawler._items(product) == []


def test_items_skips_unavailable_variants():
    product = {**_MULTI_COLOUR_PRODUCT, "variants": [
        {**_MULTI_COLOUR_PRODUCT["variants"][0], "available": False},
        _MULTI_COLOUR_PRODUCT["variants"][1],
    ]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "A Conspiracy Of Stars (Colored Double Vinyl LP) — Blue Marble"


def test_items_keeps_test_pressings_marked_by_their_own_title():
    # All 533 live tag-bearing test pressings also say it in the title, so no
    # decoration is needed — keeping the title verbatim is the marking.
    product = {**_DEFAULT_TITLE_PRODUCT,
               "title": "Punk Rock Christmas (Black Vinyl LP Test Pressing)",
               "tags": ["Test Pressing", "Vinyl Test Pressing"]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["artist"] == "Various"
    assert items[0]["title"] == "Punk Rock Christmas (Black Vinyl LP Test Pressing)"


def test_items_handles_null_variants():
    assert Crawler._items({**_DEFAULT_TITLE_PRODUCT, "variants": None}) == []


def test_items_emits_none_price_on_unparseable_price():
    product = {**_DEFAULT_TITLE_PRODUCT, "variants": [
        {"title": "Default Title", "price": None, "available": True, "featured_image": None},
    ]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["price"] is None


import httpx
import respx


_PRODUCTS_URL = "https://cleorecs.com/collections/vinyl-1/products.json"


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@respx.mock
async def test_crawl_catalog_yields_items_across_pages():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_MULTI_COLOUR_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_SEVEN_INCH_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert [i["artist"] for i in items] == ["UFO", "UFO", "Iggy & The Stooges"]


@respx.mock
async def test_crawl_catalog_drops_non_vinyl_products_from_the_feed():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_POSTER_PRODUCT, _BOOK_PRODUCT, _SHIRT_BUNDLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Cleopatra Records"
    assert Crawler.base_url == "https://cleorecs.com"
    assert Crawler.crawler_type == "catalog"
