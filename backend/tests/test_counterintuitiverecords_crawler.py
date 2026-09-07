import httpx
import pytest
import respx

from crawlers.counterintuitiverecords import Crawler

_PRODUCTS_URL = "https://counterintuitiverecords.com/collections/all/products.json"

# CAPTURED (trimmed): a real multi-variant record. Every colour variant names
# no format at all, which is why the variant gate here is negative; the CD and
# Cassette siblings are the only ones it drops.
_VINYL_PRODUCT = {
    "title": "Leisure Hour - ...that one day",
    "vendor": "Leisure Hour",
    "handle": "leisure-hour-that-one-day",
    "product_type": "Vinyl",
    "tags": ["Aged-15+", "cf-type-vinyl", "Leisure Hour", "media", "music", "Pre-Order 10-02-26"],
    "images": [{"src": "https://cdn.shopify.com/leisure-hour-fallback.jpg"}],
    "variants": [
        {"title": "Clear w Green & Red Splatter /200", "price": "25.00", "available": True},
        {"title": "Grape Shimmer /300", "price": "25.00", "available": True},
        {"title": "Green Half & Half /500", "price": "25.00", "available": True},
        {"title": "Cassette Tape", "price": "15.00", "available": True},
        {"title": "Jewel Case CD", "price": "12.00", "available": True},
    ],
}

# CAPTURED (trimmed): the store's mixed-format product_type. Its "Tape" and
# "CD" variants are dropped while the three vinyl colours stay.
_VINYL_CD_PRODUCT = {
    "title": "Computer - Zero",
    "vendor": "Computer",
    "handle": "computer-zero",
    "product_type": "Vinyl/CD",
    "tags": ["Aged-15+", "cf-type-vinyl-cd", "Computer"],
    "images": [{"src": "https://cdn.shopify.com/computer-zero.jpg"}],
    "variants": [
        {"title": "Swirl Vinyl /100", "price": "25.00", "available": False},
        {"title": "Pink Vinyl /200", "price": "25.00", "available": True},
        {"title": "Fig Cream /700", "price": "25.00", "available": True},
        {"title": "Tape", "price": "15.00", "available": False},
        {"title": "CD", "price": "12.00", "available": True},
    ],
}

# CAPTURED (trimmed): the distro shelf's own product_type — a record the store
# did not release but does stock.
_DISTRO_PRODUCT = {
    "title": "The Hotelier - Home, Like Noplace Is There (CIRecs Exclusive)",
    "vendor": "The Hotelier",
    "handle": "the-hotelier-home-like-noplace-is-there",
    "product_type": "Distro Vinyl",
    "tags": ["Aged-15+", "cf-type-distro-vinyl"],
    "images": [{"src": "https://cdn.shopify.com/hotelier.jpg"}],
    "variants": [
        {"title": "Blue w/ Black Splatter (CIR Exclusive)", "price": "30.00", "available": True},
    ],
}

# CAPTURED (trimmed): the live variant that forces the gate's check order. The
# "CD" here is the second disc's C and D sides, not a compact disc — a gate
# testing for another medium before a vinyl word would drop a real record.
_TWO_LP_PRODUCT = {
    "title": "Bears In Trees - Every Moonbeam Every Feverdream",
    "vendor": "Bears In Trees",
    "handle": "bears-in-trees-every-moonbeam-every-feverdream",
    "product_type": "Vinyl",
    "tags": ["Aged-15+", "cf-type-vinyl", "Bears In Trees"],
    "images": [{"src": "https://cdn.shopify.com/bears.jpg"}],
    "variants": [
        {"title": "AB Dark Blue / CD Light Blue 2xLP", "price": "35.00", "available": True},
        {"title": "Jewel Case CD", "price": "12.00", "available": True},
    ],
}

# CAPTURED (trimmed): a sole-variant product carrying Shopify's placeholder.
_PLACEHOLDER_PRODUCT = {
    "title": "Yawners - SUPERBUCLE",
    "vendor": "Yawners",
    "handle": "yawners-superbucle",
    "product_type": "Vinyl",
    "tags": ["Aged-15+", "cf-type-vinyl"],
    "images": [{"src": "https://cdn.shopify.com/yawners.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
    ],
}

# CAPTURED (trimmed): the hyphenated-word title the split must not break on.
_HYPHENATED_TITLE_PRODUCT = {
    "title": "ANORAK! - Self-actualization and the ignorance and hesitation towards it",
    "vendor": "ANORAK!",
    "handle": "anorak-self-actualization",
    "product_type": "Vinyl",
    "tags": ["Aged-15+", "cf-type-vinyl"],
    "images": [{"src": "https://cdn.shopify.com/anorak.jpg"}],
    "variants": [
        {"title": "White w/ Black Splatter", "price": "25.00", "available": True},
    ],
}

# CAPTURED (trimmed): a split release. The full billing lives in the title and
# only one of the bands is in `vendor`, which is why the title split wins.
_SPLIT_PRODUCT = {
    "title": "Mom Jeans / Grad Life - Split",
    "vendor": "Mom Jeans",
    "handle": "mom-jeans-grad-life-split",
    "product_type": "Vinyl",
    "tags": ["Aged-15+", "cf-type-vinyl"],
    "images": [{"src": "https://cdn.shopify.com/split.jpg"}],
    "variants": [
        {"title": "Half Blue/Half Yellow /500", "price": "25.00", "available": True},
    ],
}

# CAPTURED (trimmed): the one live vinyl product whose title carries no artist
# billing — the store's own label compilation. `vendor` is the fallback.
_COMPILATION_PRODUCT = {
    "title": "Counter Intuitive Presents: Cosmic Debris, Vol 2",
    "vendor": "Counter Intuitive Records",
    "handle": "counter-intuitive-presents-cosmic-debris-vol-2",
    "product_type": "Vinyl/CD",
    "tags": ["Aged-15+", "cf-type-vinyl-cd"],
    "images": [{"src": "https://cdn.shopify.com/cosmic-debris.jpg"}],
    "variants": [
        {"title": "Black /600", "price": "10.00", "available": True},
        {"title": "CD", "price": "5.00", "available": False},
        {"title": "Cassette", "price": "15.00", "available": True},
    ],
}

# CAPTURED (trimmed): apparel shelved in the same `all` collection.
_SHIRT_PRODUCT = {
    "title": "A Loss for Words - Pete Weber Shirt",
    "vendor": "A Loss For Words",
    "handle": "a-loss-for-words-pete-weber-shirt",
    "product_type": "Clothing",
    "tags": ["Aged-15+", "cf-type-clothing"],
    "images": [],
    "variants": [
        {"title": "Small", "price": "20.00", "available": True},
        {"title": "Medium", "price": "20.00", "available": True},
    ],
}

# CAPTURED (trimmed): a cassette-only release, excluded on its product_type.
_TAPE_PRODUCT = {
    "title": "Shalfi - Aphelion",
    "vendor": "Shalfi",
    "handle": "shalfi-aphelion",
    "product_type": "Tape",
    "tags": ["Aged-15+", "cf-type-tape"],
    "images": [],
    "variants": [
        {"title": "Blue Cassette", "price": "12.00", "available": True},
    ],
}

# CAPTURED (trimmed): the store's non-product rows, both excluded on type.
_HIDDEN_PRODUCT = {
    "title": "Coupon",
    "vendor": "Counter Intuitive Records",
    "handle": "coupon",
    "product_type": "HIDDEN",
    "tags": ["Coupons"],
    "images": [],
    "variants": [{"title": "Default Title", "price": "0.00", "available": True}],
}
_FEE_PRODUCT = {
    "title": "Shipping Protection",
    "vendor": "Counter Intuitive Records",
    "handle": "shipping-protection",
    "product_type": "mws_fee_generated",
    "tags": [],
    "images": [],
    "variants": [{"title": "Default Title", "price": "1.50", "available": True}],
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
    _mock_walk([_DISTRO_PRODUCT])
    items = await _run(crawler)
    assert items == [{
        "artist": "The Hotelier",
        "title": "Home, Like Noplace Is There (CIRecs Exclusive) — Blue w/ Black Splatter (CIR Exclusive)",
        "format": "Vinyl",
        "price": 30.00,
        "currency": "USD",
        "url": "https://counterintuitiverecords.com/products/the-hotelier-home-like-noplace-is-there",
        "cover_image_url": "https://cdn.shopify.com/hotelier.jpg",
    }]


# --- product-type gate ------------------------------------------------------

@respx.mock
async def test_admits_all_three_vinyl_product_types(crawler):
    _mock_walk([_VINYL_PRODUCT, _VINYL_CD_PRODUCT, _DISTRO_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Leisure Hour", "Computer", "The Hotelier"}


@respx.mock
async def test_excludes_apparel_tape_and_non_product_rows(crawler):
    _mock_walk([_SHIRT_PRODUCT, _TAPE_PRODUCT, _HIDDEN_PRODUCT, _FEE_PRODUCT, _DISTRO_PRODUCT])
    items = await _run(crawler)
    assert [i["artist"] for i in items] == ["The Hotelier"]


@respx.mock
async def test_unknown_product_type_is_excluded_by_default(crawler):
    # INVENTED: a type the store does not currently use. The gate enumerates
    # what it admits, so anything new stays out until it is added.
    product = {**_DISTRO_PRODUCT, "product_type": "Boxed Set Bundle"}
    _mock_walk([product, _VINYL_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Leisure Hour"}


# --- artist / album parsing -------------------------------------------------

@respx.mock
async def test_splits_artist_and_album_on_the_billing_dash(crawler):
    _mock_walk([_VINYL_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Leisure Hour"}
    assert all(i["title"].startswith("...that one day — ") for i in items)


@respx.mock
async def test_hyphenated_word_in_album_is_not_a_split_point(crawler):
    _mock_walk([_HYPHENATED_TITLE_PRODUCT])
    items = await _run(crawler)
    assert items[0]["artist"] == "ANORAK!"
    assert items[0]["title"] == (
        "Self-actualization and the ignorance and hesitation towards it — White w/ Black Splatter")


@respx.mock
async def test_hyphenated_word_in_artist_is_not_a_split_point(crawler):
    # INVENTED: the shape the live catalog cannot currently produce but the
    # regex exists to survive — a hyphenated artist name. A plain \s*-\s*
    # split would credit this to "Bear".
    product = {**_DISTRO_PRODUCT, "title": "Bear-Hunter - Nightjar", "vendor": "Bear-Hunter"}
    _mock_walk([product])
    items = await _run(crawler)
    assert items[0]["artist"] == "Bear-Hunter"
    assert items[0]["title"].startswith("Nightjar — ")


@respx.mock
async def test_split_release_keeps_full_billing_from_title_not_vendor(crawler):
    _mock_walk([_SPLIT_PRODUCT])
    items = await _run(crawler)
    assert items[0]["artist"] == "Mom Jeans / Grad Life"
    assert items[0]["title"] == "Split — Half Blue/Half Yellow /500"


@respx.mock
async def test_falls_back_to_vendor_when_title_has_no_billing_dash(crawler):
    _mock_walk([_COMPILATION_PRODUCT])
    items = await _run(crawler)
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Counter Intuitive Records", "Counter Intuitive Presents: Cosmic Debris, Vol 2 — Black /600"),
    ]


@respx.mock
async def test_product_with_neither_title_split_nor_vendor_is_skipped(crawler):
    # ALTERED: the compilation with its vendor emptied, leaving no artist
    # source at all.
    product = {**_COMPILATION_PRODUCT, "vendor": ""}
    _mock_walk([product, _DISTRO_PRODUCT])
    items = await _run(crawler)
    assert [i["artist"] for i in items] == ["The Hotelier"]


@respx.mock
async def test_title_whitespace_is_collapsed(crawler):
    # ALTERED: doubled spacing around the billing dash and inside the album.
    product = {**_DISTRO_PRODUCT, "title": "The  Hotelier  -   It Never  Goes Out"}
    _mock_walk([product])
    items = await _run(crawler)
    assert items[0]["artist"] == "The Hotelier"
    assert items[0]["title"].startswith("It Never Goes Out — ")


# --- variant format gate ----------------------------------------------------

@respx.mock
async def test_drops_cd_and_cassette_variants_keeps_unformatted_colours(crawler):
    _mock_walk([_VINYL_PRODUCT])
    items = await _run(crawler)
    assert [i["title"] for i in items] == [
        "...that one day — Clear w Green & Red Splatter /200",
        "...that one day — Grape Shimmer /300",
        "...that one day — Green Half & Half /500",
    ]


@respx.mock
async def test_vinyl_word_decides_before_another_medium_word(crawler):
    _mock_walk([_TWO_LP_PRODUCT])
    items = await _run(crawler)
    assert [i["title"] for i in items] == [
        "Every Moonbeam Every Feverdream — AB Dark Blue / CD Light Blue 2xLP",
    ]


@respx.mock
async def test_inch_marker_admits_a_variant(crawler):
    # ALTERED: a 7" pressing beside a digipak, both live shapes on this store.
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": 'Clear w/Blue & Black Splatter 7" Vinyl', "price": "15.00", "available": True},
        {"title": 'Cloudy Green 7"', "price": "15.00", "available": True},
        {"title": "Digipak CD", "price": "12.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"].split("— ")[-1] for i in items] == [
        'Clear w/Blue & Black Splatter 7" Vinyl', 'Cloudy Green 7"',
    ]


@respx.mock
async def test_variant_naming_no_format_is_admitted_by_default(crawler):
    # ALTERED: live colour names that name no medium at all.
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Scrambled Eggs /200", "price": "25.00", "available": True},
        {"title": "Copper Nugget", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert len(items) == 2


@respx.mock
async def test_colour_prefixed_cassette_variants_are_dropped(crawler):
    # ALTERED: the store names cassettes by colour too, so an anchored
    # exact-match gate would let these through.
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Pink Tape", "price": "12.00", "available": True},
        {"title": "Yellow Cassette", "price": "12.00", "available": True},
        {"title": "Bone /1000", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"].split("— ")[-1] for i in items] == ["Bone /1000"]


@respx.mock
async def test_colour_name_embedding_a_medium_substring_is_not_dropped(crawler):
    # INVENTED: "Grape" contains no standalone medium word, and the word
    # boundaries are what keep it in.
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Grape Shimmer /300", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert len(items) == 1


# --- variant placeholder ----------------------------------------------------

@respx.mock
async def test_sole_placeholder_variant_carries_the_bare_album_title(crawler):
    _mock_walk([_PLACEHOLDER_PRODUCT])
    items = await _run(crawler)
    assert items[0]["title"] == "SUPERBUCLE"


@respx.mock
async def test_placeholder_on_a_multi_variant_product_is_skipped(crawler):
    # INVENTED: malformed data. The placeholder names no pressing, so a row
    # built on it would share the bare album title and the product URL — and
    # so the item_key — with a sibling.
    product = {**_PLACEHOLDER_PRODUCT, "variants": [
        {"title": "Default Title", "price": "25.00", "available": True},
        {"title": "Pink /300", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["SUPERBUCLE — Pink /300"]


@respx.mock
async def test_named_variant_is_appended_even_on_a_single_variant_product(crawler):
    _mock_walk([_DISTRO_PRODUCT])
    items = await _run(crawler)
    assert items[0]["title"].endswith(" — Blue w/ Black Splatter (CIR Exclusive)")


@respx.mock
async def test_blank_variant_title_is_skipped(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "   ", "price": "25.00", "available": True},
        {"title": "Bone /1000", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"].split("— ")[-1] for i in items] == ["Bone /1000"]


# --- availability -----------------------------------------------------------

@respx.mock
async def test_only_literal_true_admits_a_variant(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "A /100", "price": "25.00", "available": True},
        {"title": "B /100", "price": "25.00", "available": False},
        {"title": "C /100", "price": "25.00", "available": "false"},
        {"title": "D /100", "price": "25.00", "available": 1},
        {"title": "E /100", "price": "25.00"},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"].split("— ")[-1] for i in items] == ["A /100"]


@respx.mock
async def test_preorder_is_neither_bypassed_nor_marked(crawler):
    # The captured product carries a dated "Pre-Order 10-02-26" tag and
    # reports its pressings available. No marker is written, because item_key
    # hashes the title and the tag goes away when the record ships.
    _mock_walk([_VINYL_PRODUCT])
    items = await _run(crawler)
    assert items
    assert all("Pre-Order" not in i["title"] for i in items)


@respx.mock
async def test_sold_out_product_yields_nothing_without_raising(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": "25.00", "available": False},
    ]}
    _mock_walk([product, _VINYL_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Leisure Hour"}


# --- prices -----------------------------------------------------------------

@respx.mock
@pytest.mark.parametrize("raw", [True, "abc", None, "nan", "0", "-5.00", {}])
async def test_unusable_price_becomes_none(crawler, raw):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": raw, "available": True},
        {"title": "Pink /300", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert items[0]["price"] is None
    assert items[1]["price"] == 25.00


@respx.mock
async def test_numeric_price_is_accepted(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": 25, "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert items[0]["price"] == 25.0


# --- images -----------------------------------------------------------------

@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": "25.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/bone.jpg"}},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/bone.jpg"


@respx.mock
async def test_missing_images_leave_cover_none(crawler):
    product = {**_DISTRO_PRODUCT, "images": []}
    _mock_walk([product])
    items = await _run(crawler)
    assert items[0]["cover_image_url"] is None


# --- malformed payloads -----------------------------------------------------

@respx.mock
async def test_null_variants_yields_nothing_for_that_product(crawler):
    product = {**_DISTRO_PRODUCT, "variants": None}
    _mock_walk([product, _VINYL_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Leisure Hour"}


@respx.mock
async def test_non_mapping_variant_entries_are_dropped(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        "not-a-dict", None, 42,
        {"title": "Bone /1000", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["title"].split("— ")[-1] for i in items] == ["Bone /1000"]


@respx.mock
async def test_product_without_a_handle_is_skipped(crawler):
    product = {**_DISTRO_PRODUCT, "handle": ""}
    _mock_walk([product, _VINYL_PRODUCT])
    items = await _run(crawler)
    assert all(not i["url"].endswith("/products/") for i in items)
    assert {i["artist"] for i in items} == {"Leisure Hour"}


# --- pagination -------------------------------------------------------------

@respx.mock
async def test_walks_every_page_until_exhausted(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page([_DISTRO_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page([_PLACEHOLDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page([]))
    items = await _run(crawler)
    assert [i["artist"] for i in items] == ["The Hotelier", "Yawners"]


# --- drift guards -----------------------------------------------------------

@respx.mock
async def test_raises_when_the_collection_is_empty(crawler):
    _mock_walk([])
    with pytest.raises(RuntimeError, match="returned no products"):
        await _run(crawler)


@respx.mock
async def test_raises_when_no_product_carries_a_vinyl_type(crawler):
    _mock_walk([_SHIRT_PRODUCT, _TAPE_PRODUCT])
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_no_vinyl_product_yields_an_artist(crawler):
    product = {**_DISTRO_PRODUCT, "title": "Untitled", "vendor": ""}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_no_vinyl_product_has_a_record_variant(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Jewel Case CD", "price": "12.00", "available": True},
        {"title": "Blue Cassette", "price": "12.00", "available": True},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="format-source drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_rows_were_yielded_but_none_is_priced(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": None, "available": True},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _run(crawler)


@respx.mock
async def test_an_isolated_null_price_among_real_rows_is_tolerated(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": None, "available": True},
        {"title": "Pink /300", "price": "25.00", "available": True},
    ]}
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["price"] for i in items] == [None, 25.00]


@respx.mock
async def test_raises_when_nothing_yielded_and_a_record_has_no_handle(crawler):
    product = {**_DISTRO_PRODUCT, "handle": ""}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _run(crawler)


@respx.mock
async def test_raises_when_nothing_yielded_and_a_flag_is_unreadable(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": "25.00", "available": "false"},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _run(crawler)


@respx.mock
async def test_readability_is_judged_over_every_admitted_variant(crawler):
    # A readable False beside an unreadable "false" is still unreadable: one
    # readable variant must not vouch for an emptiness half its own doing.
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": "25.00", "available": False},
        {"title": "Pink /300", "price": "25.00", "available": "false"},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _run(crawler)


@respx.mock
async def test_a_genuinely_sold_out_catalog_is_a_legitimate_empty_result(crawler):
    product = {**_DISTRO_PRODUCT, "variants": [
        {"title": "Bone /1000", "price": "25.00", "available": False},
    ]}
    _mock_walk([product])
    assert await _run(crawler) == []


@respx.mock
async def test_an_isolated_unreadable_product_among_real_rows_does_not_raise(crawler):
    broken = {**_DISTRO_PRODUCT, "handle": ""}
    _mock_walk([broken, _VINYL_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Leisure Hour"}


@respx.mock
async def test_all_cd_shelf_does_not_raise_artist_source_drift(crawler):
    # The artist tally sits outside the format gate on purpose: a shelf that
    # legitimately filled up with non-vinyl variants must raise on the format
    # source, not on the artist source, whose titles are all still readable.
    product = {**_VINYL_PRODUCT, "variants": [
        {"title": "Jewel Case CD", "price": "12.00", "available": True},
    ]}
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="format-source drift"):
        await _run(crawler)


# --- registration metadata --------------------------------------------------

def test_site_metadata():
    assert Crawler.site_name == "Counter Intuitive Records"
    assert Crawler.base_url == "https://counterintuitiverecords.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "punk"
    assert Crawler.genre_summary
