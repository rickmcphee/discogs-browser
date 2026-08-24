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


def _variant(title, available=True, price="27.99"):
    return {"title": title, "price": price, "available": available, "featured_image": None}


def test_spelled_and_spaced_inch_markers_take_the_vinyl_override():
    # Regression: the gate took only an unspaced mark on 7/10/12, while
    # _TRAILING_FORMAT_RE also accepted a space and the word INCH. A bundle
    # like "10 INCH + CD" therefore lost the override and was dropped as a CD,
    # while "12\" + CD" was kept -- the same shape as the 2xLP+CD bug.
    for title in ('Sodom "1982" 10 INCH + CD', 'Sodom "1982" 12 " + CD',
                  'Sodom "1982" 10 Inch + CD'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_inch_markers_alone_are_still_vinyl():
    for title in ('Sodom "1982" 10 INCH', 'Sodom "1982" 12"', 'Sodom "1982" 7"'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_multiplier_notation_non_vinyl_is_dropped():
    # "2xCD" is real Shopify notation -- this repo's own temporaryresidence
    # fixtures carry it -- and the plain \d* prefix does not reach across the x.
    for title in ('Sodom "1982" 2xCD', "Sodom - 1982 2xCD", 'Sodom "1982" 2×CD',
                  'Sodom "1982" 2xDVD'):
        assert Crawler._items({**_SODOM, "title": title}) == [], title


def test_multiplier_notation_vinyl_is_kept():
    for title in ('Sodom "1982" 2xLP', 'Sodom "1982" 2×LP', 'Sodom "1982" 2xLP+CD'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_digital_is_rejected_on_every_path():
    # `Digital` is a common Shopify variant and was missing from both the
    # denylist and the trailing-format stripper, so it published as Vinyl on
    # all three paths. runforcoverrecords.py rejects it too.
    assert Crawler._items({**_SODOM, "title": 'Sodom "1982" Digital'}) == []
    assert Crawler._items({**_SODOM, "title": "Sodom - 1982 Digital Download"}) == []
    product = {**_SODOM, "variants": [_variant("Black LP"), _variant("Digital")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982"]


def test_album_named_digital_survives():
    # Same single-word guard that protects an album named "Tape".
    items = Crawler._items({**_SODOM, "title": "Sodom - Digital"})
    assert items and items[0]["title"] == "Digital"


def test_non_vinyl_variant_of_a_vinyl_product_is_dropped():
    # The title blurb gates the product; before this, a mixed-format product's
    # CD variant was published as format "Vinyl" titled "1982 — CD".
    product = {**_SODOM, "variants": [_variant("Black LP"), _variant("CD")]}
    items = Crawler._items(product)
    assert [i["title"] for i in items] == ["1982"]
    assert all(i["format"] == "Vinyl" for i in items)


def test_bare_colour_variants_are_not_dropped_by_the_variant_gate():
    # The gate is negative for the same reason the product-level one is: a
    # positive filter would drop every variant named only for its colour.
    product = {**_SODOM, "variants": [_variant("Black"), _variant("Splatter")]}
    assert len(Crawler._items(product)) == 2


def test_product_whose_every_variant_is_non_vinyl_yields_nothing():
    product = {**_SODOM, "variants": [_variant("CD"), _variant("2xCD")]}
    assert Crawler._items(product) == []


def test_qualifier_counts_only_variants_that_can_become_rows():
    # Two variants but one is a CD, so exactly one row is emitted and it needs
    # no qualifier to disambiguate it. Format is safe to filter on before the
    # count -- unlike availability -- because it does not change as stock moves.
    product = {**_SODOM, "variants": [_variant("Black LP"), _variant("CD")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982"]


def test_multi_variant_product_qualifies_each_row():
    # db.compute_item_key hashes (artist, title, url) and replace_stock_items
    # INSERTs with no ON CONFLICT, so identical titles across variants become
    # duplicated rows sharing one identity, one judgment and one saved state.
    product = {**_SODOM, "variants": [_variant("Black"), _variant("Splatter")]}
    titles = [i["title"] for i in Crawler._items(product)]
    assert titles == ["1982 — Black", "1982 — Splatter"]
    assert len(set(titles)) == 2


def test_single_variant_product_is_not_qualified():
    product = {**_SODOM, "variants": [_variant("Black")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982"]


def test_shopify_placeholder_variant_never_reaches_the_title():
    product = {**_SODOM, "variants": [_variant("Default Title"), _variant("Default Title")]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982", "1982"]


def test_variant_qualifier_does_not_depend_on_sibling_availability():
    # Counted over the full variant list, not the filtered one: a qualifier that
    # appeared only while a sibling was in stock would change item_key between
    # syncs and orphan that row's judgment every time stock moved.
    product = {**_SODOM, "variants": [_variant("Black"), _variant("Splatter", available=False)]}
    assert [i["title"] for i in Crawler._items(product)] == ["1982 — Black"]


def test_variant_qualifier_precedes_the_preorder_suffix():
    product = {**_SODOM, "tags": ["pre-order"],
               "variants": [_variant("Black"), _variant("Splatter")]}
    titles = [i["title"] for i in Crawler._items(product)]
    assert titles == ["1982 — Black (Pre-Order)", "1982 — Splatter (Pre-Order)"]
    # The recommended-filter spec's prefix invariant: the clean album title
    # stays an exact or space-terminated prefix of whatever is stored.
    assert all(x.startswith("1982 ") for x in titles)


def test_preorder_tag_matches_the_spaced_spelling():
    # The design claims _PREORDER_RE covers "Pre Order"; nothing pinned it.
    assert Crawler._items({**_SODOM, "tags": ["Pre Order"]})[0]["title"] == "1982 (Pre-Order)"


def test_count_prefixed_non_vinyl_format_is_dropped():
    # Regression: a disc count binds to the format word with no word boundary
    # between them, so `\bcds?\b` could not match the "CD" in "2CD" and a
    # double-CD edition passed the gate as Vinyl. Both parser paths.
    for title in ('Sodom "1982" 2CD', "Sodom - 1982 2CD", 'Sodom "1982" 2DVD'):
        assert Crawler._items({**_SODOM, "title": title}) == [], title


def test_count_prefixed_vinyl_format_is_still_kept():
    for title in ('Sodom "1982" 2LP', 'Sodom "1982" 3LP'):
        assert len(Crawler._items({**_SODOM, "title": title})) == 1, title


def test_vinyl_keyword_overrides_count_prefixed_cd():
    # A bundle naming both is a vinyl release: _VINYL_RE short-circuits.
    assert len(Crawler._items({**_SODOM, "title": 'Sodom "1982" LP+2CD'})) == 1


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
