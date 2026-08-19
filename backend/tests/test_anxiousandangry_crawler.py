import httpx
import respx
import pytest
from crawlers.anxiousandangry import Crawler

_PRODUCTS_URL = "https://anxiousandangry.com/collections/record-store/products.json"

# Real confirmed-live case: quoted album, single "Default Title" vinyl
# variant -- title must NOT be suffixed with the meaningless variant label.
_ABSENT_IN_BODY = {
    "title": 'Absent In Body "Plague God" LP',
    "vendor": "Anxious and Angry",
    "handle": "absent-in-body-plague-god-lp",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "LPs", "Record Store", "VINYL"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0046/3142/9190/products/4062656-2795494.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "20.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: two genuine vinyl color variants, no CD sibling
# -- both survive, each title suffixed with its own color descriptor.
_ARRIVALS_PAYLOAD = {
    "title": 'Arrivals, The "Payload" LP',
    "vendor": "Arrivals Payload Pre",
    "handle": "arrivals-the-payload-lp",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "LP", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Orange Vinyl", "price": "30.00", "available": True, "featured_image": None},
        {"title": "Blue Vinyl", "price": "30.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: no quotes at all -- falls back to `vendor` as
# artist. `vendor` here is the store's own name (unlike Arrivals above,
# where `vendor` happens to carry release-specific text).
_HALLOWEEN_KILLS = {
    "title": "Halloween Kills Original Motion Picture Soundtrack LP",
    "vendor": "Anxious and Angry",
    "handle": "halloween-kills-original-motion-picture-soundtrack-lp",
    "product_type": "Record Store",
    "tags": ["Record Store", "Soundtrack", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: CD-only product, "Default Title" variant --
# product-level gate must exclude it entirely (the per-variant filter alone
# can't: "Default Title" doesn't match the negative cd/cassette pattern).
_ARRIVALS_MARVELS_CD = {
    "title": 'Arrivals, The "Marvels of Industry" CD',
    "vendor": "Anxious and Angry",
    "handle": "arrivals-the-marvels-of-industry-cd",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "CD", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: product_type "GIft Card" (sic, this store's own
# typo) -- must be excluded entirely by the product_type allowlist, before
# title parsing is even attempted.
_GIFT_CARD = {
    "title": "Anxious and Angry Gift Card",
    "vendor": "Anxious and Angry",
    "handle": "anxious-and-angry-gift-card",
    "product_type": "GIft Card",
    "tags": ["Gift Card", "Record Store"],
    "images": [],
    "variants": [
        {"title": "$10.00", "price": "10.00", "available": True, "featured_image": None},
        {"title": "$25.00", "price": "25.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: dual-format product with separate LP/CD
# variants -- only the LP variant is a genuine format-token exception to
# this store's usual bare-color/Default-Title variant titles.
_COPYRIGHTS_CD_LP = {
    "title": 'Copyrights, The "Alone In A Dome" CD/LP',
    "vendor": "Copyrights",
    "handle": "copyrights-the-alone-in-a-dome-cd-lp",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "CD", "LP", "Record Store", "VINYL"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/0046/3142/9190/products/a2570152274_10.jpg"}],
    "variants": [
        {"title": "LP", "price": "25.00", "available": True, "featured_image": None},
        {"title": "CD", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: album title ends in a digit immediately before
# the closing quote ('Vol. 2"'), which would false-positive-match an
# inch-mark regex applied to the *whole* title. Must still be correctly
# excluded as CD-only once the regex is scoped to the post-quote suffix.
_FYP_INCOMPLETE_CRAP = {
    "title": 'F.Y.P "Incomplete Crap Vol. 2" CD',
    "vendor": "Anxious and Angry",
    "handle": "f-y-p-incomplete-crap-vol-2-cd",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "CD", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "12.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "7 Inch" spelled out (not a bare inch mark) must
# still be recognized as vinyl.
_WESTERN_ADDICTION_7INCH = {
    "title": 'Western Addiction "Pines" 7 Inch',
    "vendor": "Western Addiction",
    "handle": "western-addiction-pines-7-inch-1",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "LP", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: "Shaped Picture Disc" format suffix, no "LP" or
# inch mark at all -- must still be recognized as vinyl.
_OWTH_PICTURE_DISC = {
    "title": 'Off With Their Heads "I Will Follow You" Shaped Picture Disc',
    "vendor": "Anxious and Angry",
    "handle": "off-with-their-heads-i-will-follow-you-shaped-picture-disc",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "LPs", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: unavailable "Default Title" variant, no
# pre-order tag -- must be skipped, item list empty.
_SAMIAM_BILLY_UNAVAILABLE = {
    "title": 'Samiam "Billy" LP (Color Vinyl)',
    "vendor": "Anxious and Angry",
    "handle": "samiam-billy-lp",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "Record Store", "Samiam", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "28.99", "available": False, "featured_image": None},
    ],
}

# Synthetic (this store's PREORDER tag exists but no vinyl product in the
# sampled catalog currently carries it) -- confirms the carve-out works:
# an unavailable variant is still kept when the product is tagged PREORDER.
_PREORDER_VINYL = {
    "title": 'Some Band "Upcoming Album" LP',
    "vendor": "Anxious and Angry",
    "handle": "some-band-upcoming-album-lp",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "PREORDER", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "22.00", "available": False, "featured_image": None},
    ],
}

# Synthetic -- confirms curly-quote parsing works (defensive test; the store's
# live catalog uses straight quotes, but some titles historically carried
# curly quotes that the regex must handle).
_CURLY_QUOTE_TITLE = {
    "title": 'Curly Quote Band “Album Title Here” LP',
    "vendor": "Curly Quote Band",
    "handle": "curly-quote-band-album-title-here-lp",
    "product_type": "Band Vinyl",
    "tags": ["Band Vinyl", "LP", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "18.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: bare inch mark (") in the title suffix, not
# spelled out -- must be recognized as vinyl format indicator.
_DANNY_CARNEY_7INCH = {
    "title": 'Danny Carney Chainsaw Symphony "Songs That Clowns Hate" 7"',
    "vendor": "Anxious and Angry",
    "handle": "danny-carney-chainsaw-symphony-songs-that-clowns-hate-7",
    "product_type": "Record Store",
    "tags": ["Band Vinyl", "7\"", "Record Store", "VINYL"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "4.50", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: product whose title suffix matches neither the
# vinyl regex NOR the non-vinyl regex (EP with no other format signal) --
# must still be kept, since its product_type ("Band Vinyl") passes the
# allowlist. The permissive suffix gate only decides format among products
# the allowlist already let through.
_TOYGUITAR_MOVE_LIKE_GHOST = {
    "title": 'toyGuitar "Move Like a Ghost" EP',
    "vendor": "Anxious and Angry",
    "handle": "toyguitar-move-like-a-ghost-ep",
    "product_type": "Band Vinyl",
    "tags": ["12\"", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "18.00", "available": True, "featured_image": None},
    ],
}

# Real confirmed-live case: this store's `clothing` collection uses
# product_type "Clothing" (confirmed via a separate live fetch), and its
# titles follow the exact same 'Artist "Album" FORMAT'-shaped convention
# ('"ARF ARF" Tee Shirt') -- a title-only filter cannot tell this apart
# from a real release. Must be excluded by the product_type allowlist even
# though the suffix ("Tee Shirt") matches neither format regex, and even
# though this exact product isn't in `record-store` today -- this is the
# scenario the allowlist exists to guard against if one is ever mis-added.
_MERCH_WRONG_PRODUCT_TYPE = {
    "title": '"ARF ARF" Tee Shirt',
    "vendor": "Anxious and Angry",
    "handle": "arf-arf-tee-shirt",
    "product_type": "Clothing",
    "tags": ["Clothing", "Record Store"],
    "images": [],
    "variants": [
        {"title": "Medium", "price": "20.00", "available": True, "featured_image": None},
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
async def test_crawl_catalog_default_title_variant_not_suffixed(crawler):
    _mock_single_page([_ABSENT_IN_BODY])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Absent In Body"
    assert item["title"] == "Plague God"
    assert item["price"] == 20.00
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://anxiousandangry.com/products/absent-in-body-plague-god-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/s/files/1/0046/3142/9190/products/4062656-2795494.jpg"


@respx.mock
async def test_crawl_catalog_keeps_multiple_vinyl_color_variants(crawler):
    _mock_single_page([_ARRIVALS_PAYLOAD])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {"Payload — Orange Vinyl", "Payload — Blue Vinyl"}
    assert all(item["artist"] == "Arrivals, The" for item in items)


@respx.mock
async def test_crawl_catalog_no_quotes_falls_back_to_vendor(crawler):
    _mock_single_page([_HALLOWEEN_KILLS])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Anxious and Angry"
    assert items[0]["title"] == "Halloween Kills Original Motion Picture Soundtrack LP"


@respx.mock
async def test_crawl_catalog_excludes_cd_only_product(crawler):
    _mock_single_page([_ARRIVALS_MARVELS_CD])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_excludes_gift_card(crawler):
    _mock_single_page([_GIFT_CARD])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_dual_format_keeps_lp_drops_cd(crawler):
    _mock_single_page([_COPYRIGHTS_CD_LP])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Alone In A Dome — LP"
    assert items[0]["price"] == 25.00


@respx.mock
async def test_crawl_catalog_digit_before_closing_quote_not_read_as_inch_mark(crawler):
    _mock_single_page([_FYP_INCOMPLETE_CRAP])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_recognizes_spelled_out_inch(crawler):
    _mock_single_page([_WESTERN_ADDICTION_7INCH])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Pines"


@respx.mock
async def test_crawl_catalog_recognizes_picture_disc(crawler):
    _mock_single_page([_OWTH_PICTURE_DISC])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "I Will Follow You"


@respx.mock
async def test_crawl_catalog_skips_unavailable_variant(crawler):
    _mock_single_page([_SAMIAM_BILLY_UNAVAILABLE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_preorder_keeps_unavailable_variant(crawler):
    _mock_single_page([_PREORDER_VINYL])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Upcoming Album (Pre-Order)"
    assert items[0]["price"] == 22.00


@respx.mock
async def test_crawl_catalog_curly_quote_title_parsing(crawler):
    _mock_single_page([_CURLY_QUOTE_TITLE])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Curly Quote Band"
    assert items[0]["title"] == "Album Title Here"
    assert items[0]["price"] == 18.00



@respx.mock
async def test_crawl_catalog_recognizes_bare_inch_mark(crawler):
    _mock_single_page([_DANNY_CARNEY_7INCH])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Songs That Clowns Hate"
    assert items[0]["price"] == 4.50


@respx.mock
async def test_crawl_catalog_keeps_product_matching_neither_regex(crawler):
    _mock_single_page([_TOYGUITAR_MOVE_LIKE_GHOST])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["title"] == "Move Like a Ghost"


@respx.mock
async def test_crawl_catalog_excludes_merch_with_release_shaped_title(crawler):
    _mock_single_page([_MERCH_WRONG_PRODUCT_TYPE])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_product_with_null_variants(crawler):
    product = {**_ABSENT_IN_BODY, "variants": None}
    _mock_single_page([product])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Anxious and Angry"
    assert Crawler.base_url == "https://anxiousandangry.com"
    assert Crawler.genre == "punk"
    assert Crawler.crawler_type == "catalog"
