import httpx
import pytest
import respx

from crawlers.translationloss import Crawler

_PRODUCTS_URL = "https://translationloss.com/collections/vinyl/products.json"

# CAPTURED (trimmed): a multi-variant record whose title carries its format
# token in the middle, before a parenthetical qualifier.
_TWO_LP_PRODUCT = {
    "title": "Having 2xLP (20th Anniversary Edition)",
    "vendor": "Trespassers William",
    "handle": "having-2xlp-20th-anniversary-edition",
    "product_type": '2x12"',
    "images": [{"src": "https://cdn.shopify.com/having.jpg"}],
    "variants": [
        {"title": "Translucent Gold with Splatter Edition", "price": "39.99", "available": True},
        {"title": "Copper Black Ice Shimmer Edition", "price": "41.99", "available": True},
    ],
}

# CAPTURED (trimmed): the store's ordinary single-LP shape, with each colour
# carrying its own sleeve shot.
_LP_PRODUCT = {
    "title": "Celestial Rot (Vinyl)",
    "vendor": "All Out War",
    "handle": "celestial-rot-vinyl",
    "product_type": '12"',
    "images": [{"src": "https://cdn.shopify.com/celestial-rot.jpg"}],
    "variants": [
        {"title": "Orange Edition", "price": "23.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/celestial-rot-orange.jpg"}},
        {"title": "Custom Splatter Edition", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/celestial-rot-splatter.jpg"}},
    ],
}

# CAPTURED (trimmed): a single-variant record. The pressing is appended here
# too, so a sibling colour appearing later cannot re-title this row.
_SINGLE_VARIANT_PRODUCT = {
    "title": "Coastlands LP",
    "vendor": "Coastlands",
    "handle": "coastlands-lp",
    "product_type": '12"',
    "images": [{"src": "https://cdn.shopify.com/coastlands.jpg"}],
    "variants": [
        {"title": "Custom Half and Half Edition", "price": "26.99", "available": True},
    ],
}

# CAPTURED (trimmed): the store's only 10", and the only entirely sold-out
# fixture here.
_TEN_INCH_PRODUCT = {
    "title": "Viper In Hand",
    "vendor": "Bloodlet",
    "handle": "bloodlet-viper-in-hand",
    "product_type": '10"',
    "images": [{"src": "https://cdn.shopify.com/viper-in-hand.jpg"}],
    "variants": [
        {"title": "Coke Bottle Green with HEAVY Aqua Blue Splatter", "price": "15.99", "available": False},
    ],
}

# CAPTURED (trimmed): a split release. The full billing is in `vendor`, joined
# with "&", and the title repeats it in the store's own shorthand.
_SPLIT_PRODUCT = {
    "title": "Coltsblood - UN Split LP",
    "vendor": "Coltsblood & UN",
    "handle": "split",
    "product_type": '12"',
    "images": [{"src": "https://cdn.shopify.com/split.jpg"}],
    "variants": [
        {"title": "Coke Bottle Green with Rainbow Splatter", "price": "21.99", "available": True},
    ],
}

# CAPTURED (trimmed): a title whose format token trails a parenthetical, and a
# second parenthetical naming the colour.
_TRAILING_FORMAT_PRODUCT = {
    "title": "Monster in the Creek (20th Anniversary Reissue) Vinyl",
    "vendor": "Giant Squid",
    "handle": "monster-in-the-creek-20th-anniversary-reissue-vinyl",
    "product_type": '12"',
    "images": [{"src": "https://cdn.shopify.com/monster.jpg"}],
    "variants": [
        {"title": "Milky Clear with Splatter Edition", "price": "26.99", "available": True},
    ],
}

# CAPTURED (trimmed): the CD, cassette, apparel and bundle types that share the
# store's `all` shelf and would be admitted by a gate that trusted the
# collection's own curation.
_CD_PRODUCT = {
    "title": "A Determinism of Morality CD",
    "vendor": "Rosetta",
    "handle": "a-determinism-of-morality",
    "product_type": "CD",
    "images": [{"src": "https://cdn.shopify.com/determinism-cd.jpg"}],
    "variants": [{"title": "Default Title", "price": "9.99", "available": True}],
}
_TWO_CD_PRODUCT = {
    "title": "Crossbearer/Into The Wire (Reissue) CD",
    "vendor": "Starkweather",
    "handle": "crossbearer-into-the-wire-reissue",
    "product_type": "2xCD",
    "images": [{"src": "https://cdn.shopify.com/crossbearer.jpg"}],
    "variants": [{"title": "Default Title", "price": "9.99", "available": True}],
}
_CD_DVD_PRODUCT = {
    "title": "Last Call CD",
    "vendor": "Cable",
    "handle": "last-call",
    "product_type": "CD/DVD",
    "images": [{"src": "https://cdn.shopify.com/last-call.jpg"}],
    "variants": [{"title": "Default Title", "price": "10.99", "available": True}],
}
_CASSETTE_PRODUCT = {
    "title": "Atheist's Cornea Cassette",
    "vendor": "Envy",
    "handle": "atheists-cornea",
    "product_type": "Cassette",
    "images": [{"src": "https://cdn.shopify.com/cornea.jpg"}],
    "variants": [{"title": "White", "price": "9.99", "available": False}],
}
_SHIRT_PRODUCT = {
    "title": "A Single Flower T-Shirt",
    "vendor": "We Lost The Sea",
    "handle": "a-single-flower-t-shirt",
    "product_type": "T-Shirt",
    "images": [{"src": "https://cdn.shopify.com/flower-shirt.jpg"}],
    "variants": [
        {"title": "Black / S", "price": "26.99", "available": True},
        {"title": "Black / M", "price": "26.99", "available": True},
    ],
}
_KIT_PRODUCT = {
    "title": 'Giant Squid "Aquarium Pack"',
    "vendor": "Giant Squid",
    "handle": "giant-squid-aquarium-pack",
    "product_type": "Kit",
    "images": [{"src": "https://cdn.shopify.com/aquarium-pack.jpg"}],
    "variants": [{"title": "Black / S", "price": "69.99", "available": False}],
}


def _page(products):
    return httpx.Response(200, json={"products": products})


def _mock_walk(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page([]))


@pytest.fixture
def crawler():
    return Crawler()


async def _run(crawler):
    return [item async for item in crawler.crawl_catalog()]


# --- item shape -------------------------------------------------------------

@respx.mock
async def test_row_shape(crawler):
    _mock_walk([_LP_PRODUCT])
    items = await _run(crawler)
    assert items == [
        {
            "artist": "All Out War",
            "title": "Celestial Rot (Vinyl) — Orange Edition",
            "format": "Vinyl",
            "price": 23.99,
            "currency": "USD",
            "url": "https://translationloss.com/products/celestial-rot-vinyl",
            "cover_image_url": "https://cdn.shopify.com/celestial-rot-orange.jpg",
        },
        {
            "artist": "All Out War",
            "title": "Celestial Rot (Vinyl) — Custom Splatter Edition",
            "format": "Vinyl",
            "price": 24.99,
            "currency": "USD",
            "url": "https://translationloss.com/products/celestial-rot-vinyl",
            "cover_image_url": "https://cdn.shopify.com/celestial-rot-splatter.jpg",
        },
    ]


# --- product-type gate ------------------------------------------------------

@respx.mock
async def test_admits_every_live_vinyl_product_type(crawler):
    _mock_walk([_LP_PRODUCT, _TWO_LP_PRODUCT, _TEN_INCH_PRODUCT])
    assert [Crawler._is_vinyl_product(p) for p in (_LP_PRODUCT, _TWO_LP_PRODUCT, _TEN_INCH_PRODUCT)] == [True] * 3
    items = await _run(crawler)
    # The 10" is sold out, so only the two in-stock records yield rows.
    assert {i["artist"] for i in items} == {"All Out War", "Trespassers William"}


@respx.mock
async def test_excludes_cd_cassette_apparel_and_bundle_types(crawler):
    _mock_walk([_CD_PRODUCT, _TWO_CD_PRODUCT, _CD_DVD_PRODUCT, _CASSETTE_PRODUCT,
                _SHIRT_PRODUCT, _KIT_PRODUCT, _LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"All Out War"}


@respx.mock
async def test_a_disc_count_does_not_make_a_cd_type_read_as_vinyl(crawler):
    # The inch marker allows a counted prefix ("2x12\""), so its digit run must
    # not be satisfied by the 2 in "2xCD" with nothing but an x after it.
    assert Crawler._is_vinyl_product(_TWO_CD_PRODUCT) is False
    _mock_walk([_TWO_CD_PRODUCT, _TWO_LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Trespassers William"}


@respx.mock
async def test_admits_a_vinyl_format_the_store_does_not_yet_stock(crawler):
    # INVENTED: the gate reads the shape of a vinyl format rather than today's
    # three literals, so a 7" the store presses next is admitted with no edit.
    seven_inch = {**_LP_PRODUCT, "product_type": '7"', "handle": "seven-inch"}
    two_by_ten = {**_SINGLE_VARIANT_PRODUCT, "product_type": '2x10"', "handle": "two-by-ten"}
    named = {**_TRAILING_FORMAT_PRODUCT, "product_type": "2xLP", "handle": "named-lp"}
    _mock_walk([seven_inch, two_by_ten, named])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"All Out War", "Coastlands", "Giant Squid"}


@respx.mock
async def test_a_type_naming_no_vinyl_format_is_excluded_by_default(crawler):
    # INVENTED: a type the store does not currently use. Nothing about it reads
    # as a vinyl format, so it stays out without needing to be listed.
    product = {**_LP_PRODUCT, "product_type": "Boxed Set Bundle", "handle": "boxed-set"}
    _mock_walk([product, _TWO_LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Trespassers William"}


# --- artist -----------------------------------------------------------------

@respx.mock
async def test_artist_comes_from_vendor_not_the_title(crawler):
    _mock_walk([_TRAILING_FORMAT_PRODUCT])
    items = await _run(crawler)
    assert [i["artist"] for i in items] == ["Giant Squid"]


@respx.mock
async def test_a_multi_act_vendor_is_left_joined(crawler):
    # "&" is an ordinary part of a single artist's own name as well as a split
    # billing, and nothing in the payload separates the two readings, so the
    # vendor is carried through as the store writes it.
    _mock_walk([_SPLIT_PRODUCT])
    items = await _run(crawler)
    assert [i["artist"] for i in items] == ["Coltsblood & UN"]


@respx.mock
async def test_a_title_dash_is_not_read_as_a_billing_split(crawler):
    _mock_walk([_SPLIT_PRODUCT])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Coltsblood - UN Split LP — Coke Bottle Green with Rainbow Splatter"]


@respx.mock
async def test_product_without_a_vendor_is_skipped(crawler):
    product = {**_LP_PRODUCT, "vendor": "", "handle": "no-vendor"}
    _mock_walk([product, _TWO_LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Trespassers William"}


@respx.mock
async def test_artist_and_title_whitespace_is_collapsed(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "vendor": "  Coast \n lands  ", "title": " Coastlands\tLP "}
    _mock_walk([product])
    items = await _run(crawler)
    assert items[0]["artist"] == "Coast lands"
    assert items[0]["title"] == "Coastlands LP — Custom Half and Half Edition"


# --- title ------------------------------------------------------------------

@respx.mock
async def test_format_tokens_in_the_title_are_kept(crawler):
    # db._library_release_match_sql matches a catalog title exactly or as a
    # prefix followed by a space, so a format token after the album name never
    # costs a match — and 2xLP against LP is a real difference between
    # pressings.
    _mock_walk([_TWO_LP_PRODUCT, _TRAILING_FORMAT_PRODUCT, _SINGLE_VARIANT_PRODUCT])
    items = await _run(crawler)
    assert [i["title"] for i in items] == [
        "Having 2xLP (20th Anniversary Edition) — Translucent Gold with Splatter Edition",
        "Having 2xLP (20th Anniversary Edition) — Copper Black Ice Shimmer Edition",
        "Monster in the Creek (20th Anniversary Reissue) Vinyl — Milky Clear with Splatter Edition",
        "Coastlands LP — Custom Half and Half Edition",
    ]


@respx.mock
async def test_each_title_still_starts_with_its_album_name(crawler):
    # The library match is a prefix test against the catalog title, so whatever
    # else a row's title carries has to come after the album name.
    _mock_walk([_TWO_LP_PRODUCT, _SINGLE_VARIANT_PRODUCT])
    items = await _run(crawler)
    assert all(
        i["title"].lower().startswith(album.lower() + " ")
        for i, album in zip(items, ["Having", "Having", "Coastlands"])
    )


# --- variant descriptor -----------------------------------------------------

@respx.mock
async def test_pressing_is_appended_even_on_a_single_variant_product(crawler):
    _mock_walk([_SINGLE_VARIANT_PRODUCT])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Coastlands LP — Custom Half and Half Edition"]


@respx.mock
async def test_sole_placeholder_variant_carries_the_bare_album_title(crawler):
    # INVENTED: no live variant carries Shopify's placeholder, but a product
    # created with no options would.
    product = {**_SINGLE_VARIANT_PRODUCT,
               "variants": [{"title": "Default Title", "price": "26.99", "available": True}]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Coastlands LP"]


@respx.mock
async def test_placeholder_beside_a_sibling_is_skipped(crawler):
    # A placeholder on a multi-variant product is malformed data: a row built
    # on it would share the bare title and the product URL — and so the
    # item_key — with every sibling built the same way.
    product = {**_LP_PRODUCT, "variants": [
        {"title": "Default Title", "price": "23.99", "available": True},
        {"title": "Orange Edition", "price": "24.99", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Celestial Rot (Vinyl) — Orange Edition"]


@respx.mock
async def test_blank_variant_title_is_skipped(crawler):
    product = {**_LP_PRODUCT, "variants": [
        {"title": "   ", "price": "23.99", "available": True},
        {"title": "Orange Edition", "price": "24.99", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Celestial Rot (Vinyl) — Orange Edition"]


@respx.mock
async def test_variant_title_whitespace_is_collapsed(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "variants": [
        {"title": " Custom  Half and\nHalf Edition ", "price": "26.99", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Coastlands LP — Custom Half and Half Edition"]


# --- availability -----------------------------------------------------------

@respx.mock
async def test_only_literal_true_admits_a_variant(crawler):
    product = {**_LP_PRODUCT, "variants": [
        {"title": "Real", "price": "23.99", "available": True},
        {"title": "Falsey", "price": "23.99", "available": False},
        {"title": "Stringy", "price": "23.99", "available": "false"},
        {"title": "Numeric", "price": "23.99", "available": 1},
        {"title": "Absent", "price": "23.99"},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Celestial Rot (Vinyl) — Real"]


@respx.mock
async def test_sold_out_product_yields_nothing_without_raising(crawler):
    _mock_walk([_TEN_INCH_PRODUCT, _LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"All Out War"}


# --- prices -----------------------------------------------------------------

@pytest.mark.parametrize("raw", [True, "nan", "0", "-5.00", "free", None, {}])
@respx.mock
async def test_unusable_price_becomes_none(crawler, raw):
    product = {**_SINGLE_VARIANT_PRODUCT, "variants": [
        {"title": "Bone", "price": raw, "available": True},
        {"title": "Black", "price": "26.99", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["price"] for i in items] == [None, 26.99]


@respx.mock
async def test_numeric_price_is_accepted(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "variants": [
        {"title": "Bone", "price": 26.99, "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["price"] for i in items] == [26.99]


# --- images -----------------------------------------------------------------

@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    _mock_walk([_LP_PRODUCT])
    items = await _run(crawler)
    assert [i["cover_image_url"] for i in items] == [
        "https://cdn.shopify.com/celestial-rot-orange.jpg",
        "https://cdn.shopify.com/celestial-rot-splatter.jpg",
    ]


@respx.mock
async def test_product_image_is_the_fallback(crawler):
    _mock_walk([_SINGLE_VARIANT_PRODUCT])
    items = await _run(crawler)
    assert [i["cover_image_url"] for i in items] == ["https://cdn.shopify.com/coastlands.jpg"]


@respx.mock
async def test_missing_images_leave_cover_none(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "images": []}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["cover_image_url"] for i in items] == [None]


# --- malformed payloads -----------------------------------------------------

@respx.mock
async def test_null_variants_yields_nothing_for_that_product(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "variants": None, "handle": "null-variants"}
    _mock_walk([product, _LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"All Out War"}


@respx.mock
async def test_non_mapping_variant_entries_are_dropped(crawler):
    product = {**_LP_PRODUCT, "variants": [
        "Orange Edition",
        {"title": "Custom Splatter Edition", "price": "24.99", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Celestial Rot (Vinyl) — Custom Splatter Edition"]


@respx.mock
async def test_product_without_a_handle_is_skipped(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "handle": ""}
    _mock_walk([product, _LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"All Out War"}


# --- pagination -------------------------------------------------------------

@respx.mock
async def test_walks_every_page_until_exhausted(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page([_LP_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page([_SINGLE_VARIANT_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page([]))
    items = await _run(crawler)
    assert [i["artist"] for i in items] == ["All Out War", "All Out War", "Coastlands"]


# --- drift guards -----------------------------------------------------------

@respx.mock
async def test_raises_when_the_collection_is_empty(crawler):
    _mock_walk([])
    with pytest.raises(RuntimeError, match="returned no products"):
        await _run(crawler)


@respx.mock
async def test_raises_when_no_product_carries_a_vinyl_type(crawler):
    _mock_walk([_CD_PRODUCT, _CASSETTE_PRODUCT, _SHIRT_PRODUCT])
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        await _run(crawler)


@respx.mock
async def test_a_shelf_gone_all_cd_raises_on_the_format_source_not_the_artist(crawler):
    # The CD rows still carry perfectly readable vendors, so a guard order that
    # let the artist tally fire first would point the next reader at a source
    # that never broke.
    _mock_walk([_CD_PRODUCT, _TWO_CD_PRODUCT])
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_no_vinyl_product_carries_a_vendor(crawler):
    _mock_walk([{**_LP_PRODUCT, "vendor": ""}, {**_TWO_LP_PRODUCT, "vendor": "   "}])
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _run(crawler)


@respx.mock
async def test_a_non_vinyl_vendor_cannot_vouch_for_the_artist_source(crawler):
    # The artist tally is nested inside the type gate, so only a vinyl-typed
    # product's vendor counts toward it. Tallied over every product, the CD
    # row's vendor would satisfy it and the walk would raise on the variants
    # instead -- naming a source that never broke.
    _mock_walk([_CD_PRODUCT, {**_LP_PRODUCT, "vendor": ""}])
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_no_vinyl_product_names_a_pressing(crawler):
    _mock_walk([
        {**_LP_PRODUCT, "variants": []},
        {**_TWO_LP_PRODUCT, "variants": [{"title": "", "price": "39.99", "available": True}]},
    ])
    with pytest.raises(RuntimeError, match="pressing-source drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_rows_were_yielded_but_none_is_priced(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "variants": [
        {"title": "Bone", "price": None, "available": True},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _run(crawler)


@respx.mock
async def test_an_isolated_null_price_among_real_rows_is_tolerated(crawler):
    product = {**_LP_PRODUCT, "variants": [
        {"title": "Bone", "price": None, "available": True},
        {"title": "Orange Edition", "price": "23.99", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["price"] for i in items] == [None, 23.99]


@respx.mock
async def test_raises_when_nothing_yielded_and_a_record_has_no_handle(crawler):
    _mock_walk([{**_LP_PRODUCT, "handle": ""}])
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _run(crawler)


@respx.mock
async def test_identity_guard_names_the_title_too_not_only_the_handle(crawler):
    # The artist comes from `vendor`, so a blank title reaches the identity
    # tally with the artist intact — the guard must not point at the handle
    # alone.
    _mock_walk([{**_LP_PRODUCT, "title": ""}])
    with pytest.raises(RuntimeError, match="carry no title or no handle -- identity-source drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_nothing_yielded_and_a_flag_is_unreadable(crawler):
    product = {**_SINGLE_VARIANT_PRODUCT, "variants": [
        {"title": "Bone", "price": "26.99", "available": "false"},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _run(crawler)


@respx.mock
async def test_readability_is_judged_over_every_admitted_variant(crawler):
    # A readable False beside an unreadable "false" is still unreadable: one
    # readable variant must not vouch for an emptiness half its own doing.
    product = {**_LP_PRODUCT, "variants": [
        {"title": "Bone", "price": "23.99", "available": False},
        {"title": "Orange Edition", "price": "23.99", "available": "false"},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _run(crawler)


@respx.mock
async def test_a_genuinely_sold_out_catalog_is_a_legitimate_empty_result(crawler):
    _mock_walk([_TEN_INCH_PRODUCT])
    assert await _run(crawler) == []


@respx.mock
async def test_an_isolated_unreadable_product_among_real_rows_does_not_raise(crawler):
    _mock_walk([{**_SINGLE_VARIANT_PRODUCT, "handle": ""}, _LP_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"All Out War"}


# --- registration metadata --------------------------------------------------

def test_site_metadata():
    assert Crawler.site_name == "Translation Loss Records"
    assert Crawler.base_url == "https://translationloss.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
    assert Crawler.genre_summary
