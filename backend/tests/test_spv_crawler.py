import httpx
import respx
import pytest
from crawlers.spv import Crawler

_PRODUCTS_URL = "https://store.spv.de/collections/vinyl/products.json"

# Product titles and handles below are real store listings; prices, tags,
# variants and image URLs are synthesized -- the store's products.json feed is
# unreachable from this environment (see the design doc's "Unverified against
# the live feed" section), so only the title/handle shape is confirmed.

_SODOM = {
    "title": 'Sodom "1982" LP (exclusive)',
    "vendor": "Steamhammer",
    "handle": "sodom-1982-lp-exclusive",
    "tags": ["Sodom", "vinyl", "exclusive"],
    "images": [{"src": "https://cdn.shopify.com/sodom-fallback.jpg"}],
    "variants": [
        {"title": "Black & White Splatter", "price": "27.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/sodom-splatter.jpg"}},
    ],
}

_MAGNUM = {
    "title": 'Magnum "The Monster Roars" LP (white & black marbled vinyl)',
    "vendor": "Steamhammer",
    "handle": "magnum-the-monster-roars-lp-white-black-marbled-vinyl",
    "tags": ["Magnum", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/magnum.jpg"}],
    "variants": [
        {"title": "White & Black Marbled", "price": "31.99", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(products)
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([])
    )


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_per_available_variant(crawler):
    _mock_single_page([_SODOM])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Sodom"
    assert item["title"] == "1982"
    assert item["format"] == "Vinyl"
    assert item["price"] == 27.99
    assert item["currency"] == "EUR"
    assert item["url"] == "https://store.spv.de/products/sodom-1982-lp-exclusive"
    assert item["cover_image_url"] == "https://cdn.shopify.com/sodom-splatter.jpg"


@respx.mock
async def test_crawl_catalog_falls_back_to_product_image(crawler):
    _mock_single_page([_MAGNUM])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Magnum"
    assert items[0]["title"] == "The Monster Roars"
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/magnum.jpg"


@respx.mock
async def test_crawl_catalog_paginates_until_empty_page(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_SODOM])
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_MAGNUM])
    )
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([])
    )
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Sodom", "Magnum"]


def test_parses_typographic_quotes():
    product = {**_SODOM, "title": 'Sodom “1982” LP (exclusive)'}
    items = Crawler._items(product)
    assert items[0]["artist"] == "Sodom"
    assert items[0]["title"] == "1982"


def test_parses_apostrophe_in_artist_name():
    product = {
        **_SODOM,
        "title": 'Satan\'s Fall "Destination Destruction" LP (exclusive)',
        "handle": "satans-fall-destination-destruction-lp-exclusive",
    }
    items = Crawler._items(product)
    assert items[0]["artist"] == "Satan's Fall"
    assert items[0]["title"] == "Destination Destruction"


def test_album_capture_stops_at_first_closing_quote():
    # A quoted word inside the trailing blurb must not extend the album capture.
    product = {**_SODOM, "title": 'Sodom "1982" LP (the "exclusive" pressing)'}
    items = Crawler._items(product)
    assert items[0]["title"] == "1982"


def test_optional_separator_before_quoted_album_is_not_kept_in_artist():
    # asianmanrecords.py's quoted parser allows `Artist - "Album"`; a dash left
    # dangling on the artist would never match a Discogs release.
    for title in ('Sodom - "1982" LP', 'Sodom – "1982" LP', 'Sodom — "1982" LP'):
        items = Crawler._items({**_SODOM, "title": title})
        assert items[0]["artist"] == "Sodom", title
        assert items[0]["title"] == "1982", title


def test_inch_mark_format_blurb_is_kept():
    # The inch mark is the same character as a straight quote -- the album
    # capture must close before it, leaving the blurb intact for the gate.
    items = Crawler._items({**_SODOM, "title": 'Sodom "1982" 12" (exclusive)'})
    assert len(items) == 1
    assert items[0]["title"] == "1982"


def test_dash_fallback_accepts_en_and_em_dash():
    for title in ("Sodom – 1982", "Sodom — 1982"):
        items = Crawler._items({**_SODOM, "title": title})
        assert items[0]["artist"] == "Sodom", title
        assert items[0]["title"] == "1982", title


def test_preorder_tag_adds_suffix_and_keeps_unavailable_variant():
    product = {
        **_SODOM,
        "tags": ["Sodom", "Pre-Order"],
        "variants": [{**_SODOM["variants"][0], "available": False}],
    }
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "1982 (Pre-Order)"


def test_preorder_tag_matches_unspaced_spelling():
    product = {**_SODOM, "tags": ["preorder"]}
    assert Crawler._items(product)[0]["title"] == "1982 (Pre-Order)"


def test_preorder_tag_accepts_comma_string_tags():
    product = {**_SODOM, "tags": "Sodom, pre-order, vinyl"}
    assert Crawler._items(product)[0]["title"] == "1982 (Pre-Order)"


def test_unavailable_variant_dropped_when_not_preorder():
    product = {**_SODOM, "variants": [{**_SODOM["variants"][0], "available": False}]}
    assert Crawler._items(product) == []


def test_non_vinyl_format_blurb_dropped():
    for blurb in ("CD", "CD (digipak)", "Cassette", "T-Shirt", "DVD"):
        product = {**_SODOM, "title": f'Sodom "1982" {blurb}'}
        assert Crawler._items(product) == [], blurb


def test_vinyl_keyword_overrides_non_vinyl_keyword():
    # A bundle naming both formats is a vinyl release, not a CD.
    product = {**_SODOM, "title": 'Sodom "1982" LP+CD'}
    assert len(Crawler._items(product)) == 1


def test_unrecognised_format_blurb_is_kept():
    # The source collection is the store's own vinyl collection, so the gate is
    # negative: an unfamiliar blurb must not silently drop real stock.
    product = {**_SODOM, "title": 'Sodom "1982" Deluxe Edition'}
    assert len(Crawler._items(product)) == 1


def test_unquoted_title_falls_back_to_dash_split():
    product = {**_SODOM, "title": "Sodom - 1982"}
    items = Crawler._items(product)
    assert items[0]["artist"] == "Sodom"
    assert items[0]["title"] == "1982"


def test_dash_split_does_not_clip_hyphenated_artist():
    product = {**_SODOM, "title": "Cro-Mags - Age Of Quarrel"}
    items = Crawler._items(product)
    assert items[0]["artist"] == "Cro-Mags"
    assert items[0]["title"] == "Age Of Quarrel"


def test_dash_fallback_drops_non_vinyl_trailing_format():
    # Regression: the format gate reads only the quoted parser's `extra` group,
    # so before the trailing-format split the dash path bypassed it entirely --
    # 'Sodom - 1982 CD' published as Vinyl with "CD" left in the title, while
    # the quoted equivalent was correctly dropped.
    for title in ("Sodom - 1982 CD", "Sodom - 1982 Cassette", "Sodom - 1982 DVD"):
        assert Crawler._items({**_SODOM, "title": title}) == [], title


def test_dash_fallback_strips_vinyl_trailing_format_from_title():
    for title, expected in (
        ("Sodom - 1982 LP", "1982"),
        ("Sodom - 1982 2LP", "1982"),
        ('Sodom - 1982 12"', "1982"),
        ("Sodom - 1982 10 INCH", "1982"),
    ):
        items = Crawler._items({**_SODOM, "title": title})
        assert items and items[0]["title"] == expected, title


def test_dash_fallback_keeps_album_that_is_itself_a_format_word():
    # The leading \s+ means a single-word album has nothing before it to match,
    # so an album genuinely named "Tape" survives.
    items = Crawler._items({**_SODOM, "title": "Sodom - Tape"})
    assert items and items[0]["title"] == "Tape"


def test_unparseable_title_is_skipped_even_when_vendor_is_populated():
    # Regression: an earlier draft fell back to `vendor` here, and this test
    # only passed because it blanked it. `vendor` holds the label on this store,
    # so the fallback published "Steamhammer" as the artist -- a row that can
    # never match a Discogs release. Assert against a realistic label vendor.
    product = {**_SODOM, "title": "Label Sampler 2026", "vendor": "Steamhammer"}
    assert Crawler._items(product) == []


def test_unparseable_title_is_skipped_with_no_vendor_either():
    product = {**_SODOM, "title": "Label Sampler 2026", "vendor": ""}
    assert Crawler._items(product) == []


def test_vendor_is_never_used_as_an_artist_source():
    # The parser takes the title only -- no vendor argument to fall back to.
    assert Crawler._parse_title("Label Sampler 2026") == ("", "Label Sampler 2026", "")


def test_null_variants_yield_nothing():
    assert Crawler._items({**_SODOM, "variants": None}) == []


def test_unparseable_price_becomes_none():
    product = {**_SODOM, "variants": [{**_SODOM["variants"][0], "price": "on request"}]}
    assert Crawler._items(product)[0]["price"] is None


def test_site_metadata():
    assert Crawler.site_name == "SPV Entertainment"
    assert Crawler.base_url == "https://store.spv.de"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
