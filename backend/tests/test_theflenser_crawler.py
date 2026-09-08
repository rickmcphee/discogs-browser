import unicodedata

import httpx
import pytest
import respx

from crawlers.theflenser import Crawler

_PRODUCTS_URL = "https://nowflensing.com/collections/all/products.json"

# CAPTURED (trimmed): a real multi-pressing record. Every variant names a
# colour and nothing else, which is why the variant gate is negative.
_LP_PRODUCT = {
    "title": 'Agriculture "Agriculture" LP',
    "vendor": "The Flenser",
    "handle": "agriculture-agriculture-lp",
    "product_type": "Vinyl",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/agriculture-fallback.jpg"}],
    "variants": [
        {
            "title": "Burgundy Vinyl", "price": "26.00", "available": True,
            "featured_image": {"src": "https://cdn.shopify.com/agriculture-burgundy.jpg"},
        },
        {"title": "Black Vinyl", "price": "25.00", "available": True},
        {"title": "Orange Vinyl", "price": "25.00", "available": False},
    ],
}

# CAPTURED (trimmed): a sole-variant record carrying Shopify's placeholder.
_PLACEHOLDER_PRODUCT = {
    "title": 'Alan Sparhawk "White Roses, My God" LP',
    "vendor": "Sub Pop",
    "handle": "alan-sparhawk-white-roses-my-god-lp",
    "product_type": "Vinyl",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/white-roses.jpg"}],
    "variants": [{"title": "Default Title", "price": "26.00", "available": True}],
}

# CAPTURED (trimmed): one of the three published, in-stock records that carry
# the vinyl product_type but are absent from the store's own `vinyl` shelf —
# the reason this crawler walks `all` instead.
_OFF_SHELF_PRODUCT = {
    "title": 'Bismuth "The Eternal Marshes" LP',
    "vendor": "Tartarus Records",
    "handle": "bismuth-the-eternal-marshes-lp",
    "product_type": "Vinyl",
    "tags": ["Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/bismuth.jpg"}],
    "variants": [{"title": "Default Title", "price": "23.00", "available": True}],
}

# CAPTURED (trimmed): the store's comma-joined product_type.
_COMMA_TYPED_PRODUCT = {
    "title": 'Wreck and Reference "No Content" 7inch',
    "vendor": "The Flenser",
    "handle": "wreck-and-reference-no-content-7inch",
    "product_type": "Vinyl,Flenser Releases",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/no-content.jpg"}],
    "variants": [{"title": "Default Title", "price": "12.00", "available": True}],
}

# CAPTURED (trimmed): a record in packaging. Its descriptor names a book as
# well as the format, and one of its pressings is sold without the book.
_RECORD_WITH_BOOK_PRODUCT = {
    "title": 'Giles Corey "Giles Corey: Remastered" DLP & Book (pre-order)',
    "vendor": "The Flenser",
    "handle": "giles-corey-remastered",
    "product_type": "Vinyl",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/giles-corey.jpg"}],
    "variants": [
        {"title": "Koi Pond Vinyl with Book", "price": "50.00", "available": True},
        {"title": "Black Vinyl with Book", "price": "49.00", "available": False},
        {"title": "Black Vinyl (no book)", "price": "36.00", "available": True},
    ],
}

# CAPTURED (trimmed): a collaboration billed with an ampersand, which the
# billing reduction must leave alone.
_AMPERSAND_BILLING_PRODUCT = {
    "title": 'Bell Witch & Aerial Ruin "Stygian Bough Volume I" DLP',
    "vendor": "Profound Lore",
    "handle": "bell-witch-aerial-ruin-stygian-bough-volume-i",
    "product_type": "Vinyl",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/stygian.jpg"}],
    "variants": [{"title": "Default Title", "price": "35.00", "available": True}],
}

# CAPTURED (trimmed): a split, billed with a slash. Only this one is reduced.
_SPLIT_PRODUCT = {
    "title": 'Deathgrave / Black Ganion "Split" LP',
    "vendor": "Tartarus Records",
    "handle": "deathgrave-black-ganion-split",
    "product_type": "Vinyl",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/split.jpg"}],
    "variants": [{"title": "Default Title", "price": "20.00", "available": True}],
}

# CAPTURED (trimmed): an album whose own title carries a slash, which the
# reduction must not touch — it reads the artist segment only.
_SLASHED_ALBUM_PRODUCT = {
    "title": 'Agriculture "Living is Easy / The Circle Chant" LP',
    "vendor": "The Flenser",
    "handle": "agriculture-living-is-easy",
    "product_type": "Vinyl",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/living-is-easy.jpg"}],
    "variants": [{"title": "Black Vinyl", "price": "25.00", "available": True}],
}

# CAPTURED (trimmed): the scratch-and-dent bin. Typed `Vinyl`, but its
# "variants" are whole other releases rather than pressings of one record,
# and its descriptor names no format — which is what drops it.
_SCRATCH_AND_DENT_PRODUCT = {
    "title": 'Various "Scratch & Dent" Stock',
    "vendor": "The Flenser",
    "handle": "various-scratch-dent-lps",
    "product_type": "Vinyl",
    "tags": ["cd", "vinyl"],
    "images": [{"src": "https://cdn.shopify.com/scratch-dent.jpg"}],
    "variants": [
        {"title": 'Loss of Self "Twelve Minutes" CD', "price": "7.00", "available": True},
        {"title": 'Succumb "XXI" LP [Swamp Green]', "price": "20.00", "available": True},
    ],
}

# CAPTURED (trimmed): the store's vinyl-shelved bundle. It carries no quoted
# album, so the title parse alone excludes it.
_BUNDLE_PRODUCT = {
    "title": "Mamaleek Vinyl Bundle",
    "vendor": "The Flenser",
    "handle": "mamaleek-vinyl-bundle",
    "product_type": "Vinyl",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/mamaleek-bundle.jpg"}],
    "variants": [{"title": "Default Title", "price": "75.00", "available": True}],
}

# CAPTURED (trimmed): one of the two live records carrying no tags at all —
# the other direction in which a tag-driven format gate is wrong.
_UNTAGGED_PRODUCT = {
    "title": 'Hum "Inlet" DLP',
    "vendor": "Earth Analog Records",
    "handle": "hum-inlet-dlp",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/inlet.jpg"}],
    "variants": [{"title": "Default Title", "price": "35.00", "available": True}],
}

# CAPTURED (trimmed): the one live product whose title names a record format
# while its type does not. Tagged `vinyl`, so a tag-driven gate would admit a
# subscription as a record.
_MEMBERSHIP_PRODUCT = {
    "title": "Flenser Membership - Series Nine - Vinyl Edition",
    "vendor": "The Flenser",
    "handle": "flenser-membership-series-nine-vinyl",
    "product_type": "Membership Series",
    "tags": ["vinyl"],
    "images": [{"src": "https://cdn.shopify.com/membership.jpg"}],
    "variants": [{"title": "Default Title", "price": "150.00", "available": True}],
}

# CAPTURED (trimmed): a CD-only release, excluded on its product_type.
_CD_PRODUCT = {
    "title": 'Chat Pile "Cool World" CD',
    "vendor": "The Flenser",
    "handle": "chat-pile-cool-world-cd",
    "product_type": "CD",
    "tags": ["cd"],
    "images": [{"src": "https://cdn.shopify.com/cool-world-cd.jpg"}],
    "variants": [{"title": "Default Title", "price": "12.00", "available": True}],
}

# CAPTURED (trimmed): apparel, shelved in the same `all` collection.
_APPAREL_PRODUCT = {
    "title": "Chat Pile Logo Shirt",
    "vendor": "The Flenser",
    "handle": "chat-pile-logo-shirt",
    "product_type": "Apparel",
    "tags": ["apparel"],
    "images": [{"src": "https://cdn.shopify.com/shirt.jpg"}],
    "variants": [
        {"title": "Small", "price": "25.00", "available": True},
        {"title": "Medium", "price": "25.00", "available": True},
    ],
}


def _page(products):
    return httpx.Response(200, json={"products": products})


def _mock_walk(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page([]))


def _with(product, **overrides):
    return {**product, **overrides}


@pytest.fixture
def crawler():
    return Crawler()


async def _run(crawler):
    return [item async for item in crawler.crawl_catalog()]


# --- metadata ---------------------------------------------------------------

def test_crawler_metadata(crawler):
    assert crawler.site_name == "The Flenser"
    assert crawler.base_url == "https://nowflensing.com"
    assert crawler.crawler_type == "catalog"
    assert crawler.genre == "metal"
    assert crawler.genre_summary


# --- item shape -------------------------------------------------------------

@respx.mock
async def test_row_shape(crawler):
    _mock_walk([_PLACEHOLDER_PRODUCT])
    assert await _run(crawler) == [{
        "artist": "Alan Sparhawk",
        "title": "White Roses, My God",
        "format": "Vinyl",
        "price": 26.00,
        "currency": "USD",
        "url": "https://nowflensing.com/products/alan-sparhawk-white-roses-my-god-lp",
        "cover_image_url": "https://cdn.shopify.com/white-roses.jpg",
    }]


@respx.mock
async def test_walks_the_all_collection_not_the_vinyl_shelf(crawler):
    _mock_walk([_OFF_SHELF_PRODUCT])
    items = await _run(crawler)
    assert [i["artist"] for i in items] == ["Bismuth"]
    assert respx.calls[0].request.url.path == "/collections/all/products.json"


@respx.mock
async def test_pressing_is_appended_to_the_album_title(crawler):
    _mock_walk([_LP_PRODUCT])
    assert [i["title"] for i in await _run(crawler)] == [
        "Agriculture — Burgundy Vinyl",
        "Agriculture — Black Vinyl",
    ]


@respx.mock
async def test_placeholder_variant_yields_the_bare_album_title(crawler):
    _mock_walk([_PLACEHOLDER_PRODUCT])
    assert [i["title"] for i in await _run(crawler)] == ["White Roses, My God"]


@respx.mock
async def test_placeholder_is_skipped_when_it_is_not_the_sole_variant(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Default Title", "price": "25.00", "available": True},
        {"title": "Black Vinyl", "price": "25.00", "available": True},
    ])
    _mock_walk([product])
    assert [i["title"] for i in await _run(crawler)] == ["Agriculture — Black Vinyl"]


@respx.mock
async def test_variant_image_wins_over_the_product_image(crawler):
    _mock_walk([_LP_PRODUCT])
    assert [i["cover_image_url"] for i in await _run(crawler)] == [
        "https://cdn.shopify.com/agriculture-burgundy.jpg",
        "https://cdn.shopify.com/agriculture-fallback.jpg",
    ]


# --- product_type gate ------------------------------------------------------

@respx.mock
async def test_admits_a_comma_joined_vinyl_product_type(crawler):
    _mock_walk([_COMMA_TYPED_PRODUCT])
    assert [i["artist"] for i in await _run(crawler)] == ["Wreck and Reference"]


@respx.mock
async def test_excludes_cd_and_apparel_products(crawler):
    _mock_walk([_LP_PRODUCT, _CD_PRODUCT, _APPAREL_PRODUCT])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
async def test_a_vinyl_tag_does_not_admit_a_membership(crawler):
    """The store tags its vinyl-edition subscription `vinyl`; product_type is
    the only signal that gets it right."""
    _mock_walk([_LP_PRODUCT, _MEMBERSHIP_PRODUCT])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
async def test_a_record_carrying_no_tags_is_still_admitted(crawler):
    """Two live records carry an empty tags array, so a tag-driven gate would
    be wrong in this direction too."""
    _mock_walk([_UNTAGGED_PRODUCT])
    assert [i["artist"] for i in await _run(crawler)] == ["Hum"]


@respx.mock
async def test_a_type_that_merely_contains_vinyl_is_not_admitted(crawler):
    """`Vinyl Accessories` is not `Vinyl`, and a substring test cannot tell
    them apart. The product is otherwise perfectly yieldable, so the type gate
    is the only thing deciding."""
    _mock_walk([
        _LP_PRODUCT,
        _with(_PLACEHOLDER_PRODUCT, product_type="Distributed titles,Vinyl Accessories"),
    ])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


# --- title parsing ----------------------------------------------------------

@respx.mock
async def test_artist_and_album_come_from_the_quoted_title(crawler):
    _mock_walk([_AMPERSAND_BILLING_PRODUCT])
    item, = await _run(crawler)
    assert item["artist"] == "Bell Witch & Aerial Ruin"
    assert item["title"] == "Stygian Bough Volume I"


@respx.mock
async def test_a_slash_billing_is_reduced_to_the_first_artist(crawler):
    _mock_walk([_SPLIT_PRODUCT])
    assert [i["artist"] for i in await _run(crawler)] == ["Deathgrave"]


@respx.mock
async def test_a_slash_inside_an_album_title_is_untouched(crawler):
    _mock_walk([_SLASHED_ALBUM_PRODUCT])
    item, = await _run(crawler)
    assert item["artist"] == "Agriculture"
    assert item["title"] == "Living is Easy / The Circle Chant — Black Vinyl"


@respx.mock
async def test_a_slash_inside_an_artist_name_is_not_clipped(crawler):
    _mock_walk([_with(_SPLIT_PRODUCT, title='AC/DC "Back in Black" LP')])
    assert [i["artist"] for i in await _run(crawler)] == ["AC/DC"]


@respx.mock
async def test_the_pre_order_note_stays_out_of_the_row_title(crawler):
    """It sits in the descriptor, outside the album, so the row is titled the
    same before and after the record ships — item_key hashes the title."""
    _mock_walk([_RECORD_WITH_BOOK_PRODUCT])
    assert [i["title"] for i in await _run(crawler)] == [
        "Giles Corey: Remastered — Koi Pond Vinyl with Book",
        "Giles Corey: Remastered — Black Vinyl (no book)",
    ]


@respx.mock
async def test_curly_quotes_parse(crawler):
    _mock_walk([_with(_PLACEHOLDER_PRODUCT, title='Alan Sparhawk “White Roses, My God” LP')])
    item, = await _run(crawler)
    assert (item["artist"], item["title"]) == ("Alan Sparhawk", "White Roses, My God")


@respx.mock
async def test_an_unquoted_title_yields_nothing(crawler):
    """`vendor` is the releasing label, never the artist, so there is nothing
    to fall back to — a bundle must not be credited to a record label."""
    _mock_walk([_LP_PRODUCT, _BUNDLE_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Agriculture"}
    assert not any("Bundle" in i["title"] for i in items)


@respx.mock
async def test_a_title_with_no_artist_before_the_quote_yields_nothing(crawler):
    _mock_walk([_LP_PRODUCT, _with(_CD_PRODUCT, product_type="Vinyl", title='"Untitled" LP')])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


# --- descriptor gate --------------------------------------------------------

@respx.mock
async def test_a_descriptor_naming_no_format_is_dropped(crawler):
    """The scratch-and-dent bin is typed `Vinyl`, but its variants are other
    releases rather than pressings of one record."""
    _mock_walk([_LP_PRODUCT, _SCRATCH_AND_DENT_PRODUCT])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Agriculture"}
    assert not any("Scratch" in i["title"] for i in items)


@respx.mock
@pytest.mark.parametrize("descriptor", [
    "LP", "DLP", "3LP", "2xLP", "Deluxe DLP", "DLP (Deluxe Edition)",
    "7inch", "10inch", '12"', "LP + 10inch", "DLP & DVD", "DLP & Zine",
    "DLP & Book (pre-order)", "EP",
])
async def test_every_record_descriptor_is_admitted(crawler, descriptor):
    _mock_walk([_with(_PLACEHOLDER_PRODUCT, title=f'Alan Sparhawk "White Roses, My God" {descriptor}')])
    assert len(await _run(crawler)) == 1


@respx.mock
@pytest.mark.parametrize("descriptor", ["Stock", "CD", "Cassette", "Book", "Shirt", ""])
async def test_a_non_record_descriptor_is_dropped(crawler, descriptor):
    _mock_walk([
        _LP_PRODUCT,
        _with(_PLACEHOLDER_PRODUCT, title=f'Alan Sparhawk "White Roses, My God" {descriptor}'),
    ])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
async def test_a_bundle_written_to_the_stores_convention_is_dropped(crawler):
    """Its `Vinyl Bundle` descriptor would satisfy the format gate on its own
    `Vinyl` — a bundle is not a release and its price is not any record's."""
    _mock_walk([_LP_PRODUCT, _with(_BUNDLE_PRODUCT, title='Mamaleek "Everything Else" Vinyl Bundle')])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
async def test_an_album_named_bundle_survives(crawler):
    """The bundle check reads the descriptor, not the whole title."""
    _mock_walk([_with(_PLACEHOLDER_PRODUCT, title='Alan Sparhawk "Bundle of Joy" LP')])
    assert [i["title"] for i in await _run(crawler)] == ["Bundle of Joy"]


@respx.mock
async def test_a_word_merely_containing_lp_is_not_a_format(crawler):
    _mock_walk([
        _LP_PRODUCT,
        _with(_PLACEHOLDER_PRODUCT, title='Alan Sparhawk "White Roses, My God" Alpine Help'),
    ])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


# --- variant gate -----------------------------------------------------------

@respx.mock
async def test_a_cd_variant_on_a_record_is_dropped(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": True},
        {"title": "Jewel Case CD", "price": "12.00", "available": True},
        {"title": "Cassette", "price": "15.00", "available": True},
    ])
    _mock_walk([product])
    assert [i["title"] for i in await _run(crawler)] == ["Agriculture — Black Vinyl"]


@respx.mock
async def test_a_vinyl_word_decides_before_another_medium_word(crawler):
    """A pressing named for both sides of its second disc is still a record."""
    product = _with(_LP_PRODUCT, variants=[
        {"title": "AB Dark Blue / CD Light Blue 2xLP", "price": "35.00", "available": True},
    ])
    _mock_walk([product])
    assert [i["title"] for i in await _run(crawler)] == [
        "Agriculture — AB Dark Blue / CD Light Blue 2xLP",
    ]


@respx.mock
async def test_a_blank_variant_title_is_skipped(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "   ", "price": "25.00", "available": True},
        {"title": "Black Vinyl", "price": "25.00", "available": True},
    ])
    _mock_walk([product])
    assert [i["title"] for i in await _run(crawler)] == ["Agriculture — Black Vinyl"]


@respx.mock
async def test_a_non_mapping_variant_is_skipped(crawler):
    product = _with(_LP_PRODUCT, variants=["junk", {"title": "Black Vinyl", "price": "25.00", "available": True}])
    _mock_walk([product])
    assert [i["title"] for i in await _run(crawler)] == ["Agriculture — Black Vinyl"]


# --- availability -----------------------------------------------------------

@respx.mock
async def test_a_sold_out_pressing_is_skipped(crawler):
    _mock_walk([_LP_PRODUCT])
    assert "Agriculture — Orange Vinyl" not in [i["title"] for i in await _run(crawler)]


@respx.mock
@pytest.mark.parametrize("available", ["false", "true", 1, None, 0])
async def test_only_the_literal_true_admits_a_pressing(crawler, available):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": available},
        {"title": "White Vinyl", "price": "25.00", "available": True},
    ])
    _mock_walk([product])
    assert [i["title"] for i in await _run(crawler)] == ["Agriculture — White Vinyl"]


@respx.mock
async def test_an_absent_available_key_is_skipped(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00"},
        {"title": "White Vinyl", "price": "25.00", "available": True},
    ])
    _mock_walk([product])
    assert [i["title"] for i in await _run(crawler)] == ["Agriculture — White Vinyl"]


# --- price ------------------------------------------------------------------

@respx.mock
@pytest.mark.parametrize("raw", [None, "", "free", "0.00", "-5.00", "NaN", "Infinity", True, [], {}])
async def test_an_unusable_price_becomes_none(crawler, raw):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": raw, "available": True},
        {"title": "White Vinyl", "price": "25.00", "available": True},
    ])
    _mock_walk([product])
    items = await _run(crawler)
    assert [i["price"] for i in items] == [None, 25.00]


@respx.mock
async def test_a_numeric_price_is_accepted(crawler):
    product = _with(_LP_PRODUCT, variants=[{"title": "Black Vinyl", "price": 25, "available": True}])
    _mock_walk([product])
    assert [i["price"] for i in await _run(crawler)] == [25.00]


# --- pagination -------------------------------------------------------------

@respx.mock
async def test_pagination_walks_until_a_page_is_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page([_LP_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page([_PLACEHOLDER_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page([]))
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture", "Alan Sparhawk"}


# --- drift guards -----------------------------------------------------------

@respx.mock
async def test_an_empty_walk_raises(crawler):
    _mock_walk([])
    with pytest.raises(RuntimeError, match="returned no products"):
        await _run(crawler)


@respx.mock
async def test_a_catalog_with_no_vinyl_type_raises(crawler):
    _mock_walk([_CD_PRODUCT, _APPAREL_PRODUCT])
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        await _run(crawler)


@respx.mock
async def test_a_catalog_whose_titles_stop_parsing_raises(crawler):
    _mock_walk([_BUNDLE_PRODUCT])
    with pytest.raises(RuntimeError, match="title-convention drift"):
        await _run(crawler)


@respx.mock
async def test_a_catalog_that_stops_naming_formats_raises(crawler):
    _mock_walk([_SCRATCH_AND_DENT_PRODUCT])
    with pytest.raises(RuntimeError, match="format-vocabulary drift"):
        await _run(crawler)


@respx.mock
async def test_a_catalog_whose_variants_all_read_as_another_medium_raises(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Jewel Case CD", "price": "12.00", "available": True},
        {"title": "Cassette", "price": "15.00", "available": True},
    ])
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="pressing-source drift"):
        await _run(crawler)


@respx.mock
async def test_rows_with_no_price_at_all_raise(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": None, "available": True},
        {"title": "White Vinyl", "price": None, "available": True},
    ])
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _run(crawler)


@respx.mock
async def test_isolated_null_prices_do_not_raise(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": None, "available": True},
        {"title": "White Vinyl", "price": "25.00", "available": True},
    ])
    _mock_walk([product])
    assert [i["price"] for i in await _run(crawler)] == [None, 25.00]


@respx.mock
async def test_an_empty_result_with_an_identity_less_product_raises(crawler):
    product = _with(_LP_PRODUCT, handle="", variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": True},
    ])
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _run(crawler)


@respx.mock
async def test_an_empty_result_with_an_unreadable_stock_flag_raises(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": "false"},
    ])
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _run(crawler)


@respx.mock
async def test_one_unreadable_pressing_taints_a_sold_out_product(crawler):
    """every(), not any(): a readable sold-out sibling must not vouch for an
    emptiness the unreadable one is half responsible for."""
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": False},
        {"title": "White Vinyl", "price": "25.00", "available": "false"},
    ])
    _mock_walk([product])
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _run(crawler)


@respx.mock
async def test_a_genuinely_sold_out_catalog_is_empty_without_raising(crawler):
    product = _with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": False},
        {"title": "White Vinyl", "price": "25.00", "available": False},
    ])
    _mock_walk([product])
    assert await _run(crawler) == []


@respx.mock
async def test_a_sold_out_product_still_counts_toward_the_guards(crawler):
    """The tallies are taken before the availability filter, so a store that
    has simply sold out does not trip the taxonomy or parse guards."""
    _mock_walk([_with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": False},
    ])])
    assert await _run(crawler) == []


# --- nested quotes (PR #331 review) ----------------------------------------

@respx.mock
async def test_a_nested_quote_is_rejected_rather_than_truncated(crawler):
    """The album group stops at the inner quote, so this would otherwise parse
    to an album of `The` with a descriptor of `Big" LP` that still names a
    format — a row keyed on a truncated title."""
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, title='Artist "The " Big" LP')])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Agriculture"}
    assert not any(i["title"] == "The" for i in items)


@respx.mock
async def test_a_double_prime_inside_an_album_is_rejected(crawler):
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, title='Artist "Al″bum" LP')])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
@pytest.mark.parametrize("descriptor", ['12"', '2x12"', '7″', '10 "'])
async def test_an_inch_marker_is_not_mistaken_for_a_nested_quote(crawler, descriptor):
    _mock_walk([_with(_PLACEHOLDER_PRODUCT, title=f'Alan Sparhawk "White Roses, My God" {descriptor}')])
    assert [i["title"] for i in await _run(crawler)] == ["White Roses, My God"]


@respx.mock
async def test_a_stray_quote_beside_a_real_inch_marker_is_still_rejected(crawler):
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, title='Artist "The " Big" 12"')])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
async def test_a_catalog_of_nested_quotes_raises(crawler):
    _mock_walk([_with(_LP_PRODUCT, title='Artist "The " Big" LP')])
    with pytest.raises(RuntimeError, match="title-convention drift"):
        await _run(crawler)


# --- identity and pressing-name guards (PR #331 review) ---------------------

@respx.mock
async def test_an_empty_result_with_a_title_less_product_raises(crawler):
    """The identity tally sits beside the parse chain rather than inside it:
    a blank title fails the parse, so nested behind it this could never fire
    for the missing title its own message names."""
    _mock_walk([
        _with(_LP_PRODUCT, title="", variants=[
            {"title": "Black Vinyl", "price": "25.00", "available": True},
        ]),
        _with(_PLACEHOLDER_PRODUCT, variants=[
            {"title": "Default Title", "price": "26.00", "available": False},
        ]),
    ])
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _run(crawler)


@respx.mock
async def test_a_title_less_product_does_not_raise_while_others_yield(crawler):
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, title="")])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
async def test_an_unnamed_in_stock_pressing_beside_a_sold_out_one_raises(crawler):
    """It leaves the walk looking sold out when it is not."""
    _mock_walk([_with(_LP_PRODUCT, variants=[
        {"title": "   ", "price": "25.00", "available": True},
        {"title": "Black Vinyl", "price": "25.00", "available": False},
    ])])
    with pytest.raises(RuntimeError, match="pressing-name drift"):
        await _run(crawler)


@respx.mock
async def test_an_unnamed_pressing_that_is_readably_sold_out_does_not_raise(crawler):
    """It could not have yielded a row anyway, so it neither caused the empty
    result nor casts doubt on it."""
    _mock_walk([_with(_LP_PRODUCT, variants=[
        {"title": "   ", "price": "25.00", "available": False},
        {"title": "Black Vinyl", "price": "25.00", "available": False},
    ])])
    assert await _run(crawler) == []


@respx.mock
async def test_an_in_stock_cd_sibling_does_not_raise(crawler):
    """A variant naming another medium is a deliberate skip, not an unreadable
    one — a CD being in stock says nothing about whether the record is."""
    _mock_walk([_with(_LP_PRODUCT, variants=[
        {"title": "Black Vinyl", "price": "25.00", "available": False},
        {"title": "Jewel Case CD", "price": "12.00", "available": True},
    ])])
    assert await _run(crawler) == []


@respx.mock
async def test_an_empty_result_with_a_non_mapping_variant_raises(crawler):
    _mock_walk([_with(_LP_PRODUCT, variants=[
        "junk",
        {"title": "Black Vinyl", "price": "25.00", "available": False},
    ])])
    with pytest.raises(RuntimeError, match="pressing-name drift"):
        await _run(crawler)


@respx.mock
async def test_an_empty_result_with_a_misplaced_placeholder_raises(crawler):
    _mock_walk([_with(_LP_PRODUCT, variants=[
        {"title": "Default Title", "price": "25.00", "available": True},
        {"title": "Black Vinyl", "price": "25.00", "available": False},
    ])])
    with pytest.raises(RuntimeError, match="pressing-name drift"):
        await _run(crawler)


@respx.mock
async def test_an_unnamed_pressing_does_not_raise_while_rows_are_yielded(crawler):
    _mock_walk([_with(_LP_PRODUCT, variants=[
        {"title": "   ", "price": "25.00", "available": True},
        {"title": "Black Vinyl", "price": "25.00", "available": True},
    ])])
    assert [i["title"] for i in await _run(crawler)] == ["Agriculture — Black Vinyl"]


# --- second review round (PR #331) ------------------------------------------

@respx.mock
@pytest.mark.parametrize("descriptor", ['12"CD', '7"Cassette', "12inchesPoster"])
async def test_an_inch_marker_glued_to_another_word_is_not_a_format(crawler, descriptor):
    """Without a right-hand boundary the fragment matched the leading `12"` and
    read a compact disc as a record."""
    _mock_walk([
        _LP_PRODUCT,
        _with(_PLACEHOLDER_PRODUCT, title=f'Alan Sparhawk "White Roses, My God" {descriptor}'),
    ])
    assert {i["artist"] for i in await _run(crawler)} == {"Agriculture"}


@respx.mock
async def test_a_nested_quote_glued_to_a_word_is_rejected(crawler):
    """The unbounded fragment accounted for the stray quote as an inch marker,
    so the truncated album passed the nested-quote check."""
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, title='Artist "The " 12"CD')])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Agriculture"}
    assert not any(i["title"] == "The" for i in items)


@respx.mock
async def test_two_inch_markers_do_not_vouch_for_each_other(crawler):
    """Both quotes sit inside a valid marker, so the position check alone
    passed `54"` that is really the album's closing quote plus junk."""
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, title='Artist "The " 54" 12"')])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Agriculture"}
    assert not any(i["title"] == "The" for i in items)


@respx.mock
async def test_the_placeholder_is_not_sole_beside_a_non_mapping_entry(crawler):
    """The sole-variant rule is about the product's variants, so it has to be
    asked of the raw list — the filtered one has already dropped the junk."""
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, variants=[
        "junk",
        {"title": "Default Title", "price": "26.00", "available": True},
    ])])
    items = await _run(crawler)
    assert [i["title"] for i in items] == ["Agriculture — Burgundy Vinyl", "Agriculture — Black Vinyl"]


@respx.mock
async def test_an_empty_result_with_a_variantless_record_raises(crawler):
    """Shopify gives every product a variant, so a record carrying none is a
    broken payload that leaves no other trace."""
    _mock_walk([
        _with(_LP_PRODUCT, variants=[]),
        _with(_PLACEHOLDER_PRODUCT, variants=[
            {"title": "Default Title", "price": "26.00", "available": False},
        ]),
    ])
    with pytest.raises(RuntimeError, match="variant-source drift"):
        await _run(crawler)


@respx.mock
async def test_a_record_whose_only_variant_is_another_medium_does_not_raise(crawler):
    """Odd store data the gate read correctly, not a broken payload. Paired
    with a readably sold-out record so the catalog-wide `pressing-source`
    guard is not what would fire."""
    _mock_walk([
        _with(_LP_PRODUCT, variants=[
            {"title": "Jewel Case CD", "price": "12.00", "available": True},
        ]),
        _with(_PLACEHOLDER_PRODUCT, variants=[
            {"title": "Default Title", "price": "26.00", "available": False},
        ]),
    ])
    assert await _run(crawler) == []


@respx.mock
async def test_an_excluded_bin_cannot_arm_the_identity_guard(crawler):
    """A product this crawler drops on purpose must not make a genuinely
    sold-out crawl raise and preserve a stale in-stock snapshot."""
    _mock_walk([
        _with(_SCRATCH_AND_DENT_PRODUCT, handle="", variants=[
            {"title": '   ', "price": "20.00", "available": True},
        ]),
        _with(_PLACEHOLDER_PRODUCT, variants=[
            {"title": "Default Title", "price": "26.00", "available": False},
        ]),
    ])
    assert await _run(crawler) == []


@respx.mock
async def test_an_excluded_bundle_cannot_arm_the_pressing_name_guard(crawler):
    _mock_walk([
        _with(_BUNDLE_PRODUCT, variants=[{"title": "  ", "price": "75.00", "available": True}]),
        _with(_PLACEHOLDER_PRODUCT, variants=[
            {"title": "Default Title", "price": "26.00", "available": False},
        ]),
    ])
    assert await _run(crawler) == []


@respx.mock
async def test_a_title_less_product_still_raises_though_it_never_parses(crawler):
    """The blank-title tally stays ahead of the parse — it is the only place a
    missing title can be seen at all."""
    _mock_walk([
        _with(_LP_PRODUCT, title="", variants=[
            {"title": "Black Vinyl", "price": "25.00", "available": True},
        ]),
        _with(_PLACEHOLDER_PRODUCT, variants=[
            {"title": "Default Title", "price": "26.00", "available": False},
        ]),
    ])
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _run(crawler)


# --- Unicode normalization (PR #331, third review round) --------------------

def _nfd(text):
    return unicodedata.normalize("NFD", text)


@respx.mock
async def test_a_decomposed_accent_cannot_bypass_the_nested_quote_guard(crawler):
    """`\\w` reads a combining mark as a word separator, so in NFD the accent
    before `54` opened the inch marker's boundary, accounted for the stray
    quote, and admitted a title its NFC spelling rejects."""
    _mock_walk([_LP_PRODUCT, _with(_PLACEHOLDER_PRODUCT, title=_nfd('Artist "The " É54" LP'))])
    items = await _run(crawler)
    assert {i["artist"] for i in items} == {"Agriculture"}
    assert not any(i["title"] == "The" for i in items)


@pytest.mark.parametrize("title", [
    'Artist "The " É54" LP',
    'Artist "Álbum" LP',
    'Artist "Album" 12"',
    'Artist "Ünderdog" 10inch',
])
def test_a_title_reads_the_same_in_nfc_and_nfd(crawler, title):
    """Compared under NFC, because the two spellings are canonically
    equivalent rather than equal — what must not differ is the decision and
    the content, not the encoding the store happened to send."""
    def parsed(text):
        return tuple(unicodedata.normalize("NFC", part)
                     for part in crawler._parse_title({"title": text}))

    assert parsed(title) == parsed(_nfd(title))


@pytest.mark.parametrize("variant", ["éCD", "Café Cassette", "Ölive Green Vinyl", "Black Vinyl"])
def test_a_variant_classifies_the_same_in_nfc_and_nfd(crawler, variant):
    assert crawler._is_record_variant(variant) == crawler._is_record_variant(_nfd(variant))


@respx.mock
async def test_an_accented_album_is_emitted_unfolded(crawler):
    """The fold is decision-time only — nothing emitted is ever folded."""
    _mock_walk([_with(_PLACEHOLDER_PRODUCT, title=_nfd('Alan Sparhawk "Ámbar" LP'))])
    item, = await _run(crawler)
    assert item["title"] == _nfd("Ámbar")
    assert "ß" not in item["title"]


# --- bundle example (PR #331, third review round) ---------------------------

@respx.mock
async def test_a_bundle_word_in_the_album_is_admitted(crawler):
    """The documented outcome of reading the descriptor rather than the whole
    title, and the shape an earlier comment wrongly claimed this rule caught."""
    _mock_walk([_with(_PLACEHOLDER_PRODUCT, title='Mamaleek "Vinyl Bundle" LP')])
    assert [i["title"] for i in await _run(crawler)] == ["Vinyl Bundle"]
