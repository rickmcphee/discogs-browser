import httpx
import pytest
import respx

from crawlers.monorailmusic import Crawler

_PRODUCTS_URL = "https://monorailmusic.com/collections/all/products.json"


# ---------------------------------------------------------------------------
# Fixtures captured live from monorailmusic.com on 2026-09-09, trimmed to the
# fields this crawler reads. Provenance is marked at each definition: a
# fixture that says "captured" is a real product, one that says "invented" is
# a shape the live catalog does not currently carry.
# ---------------------------------------------------------------------------

# Captured. The plain shape: "Artist - Album", one in-stock vinyl variant.
_BULL_OF_THE_WOODS = {
    "title": "13th Floor Elevators - Bull of the Woods",
    "handle": "13th-floor-elevators-bull-of-the-woods",
    "vendor": "Monorail Music",
    "product_type": "Music",
    "tags": ["13th Floor Elevators"],
    "images": [{"src": "https://cdn.shopify.com/s/files/1/1000/6088/9430/files/a1333281053_10.png?v=1788772050"}],
    "variants": [{"title": "Vinyl LP", "price": "25.99", "available": True, "featured_image": None}],
}

# Captured. A release sold as a record AND a CD on one product -- the shape
# that forces the format gate to run per variant rather than per product.
# Both variants are in stock and both carry their own image.
_AUTOSMILE = {
    "title": "@ - Autosmile",
    "handle": "at-autosmile",
    "vendor": "4AD",
    "product_type": "Music",
    "tags": ["At"],
    "images": [{"src": "https://cdn.shopify.com/product.jpg"}],
    "variants": [
        {"title": "Soil Brown Vinyl", "price": "23.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/vinyl-variant.png"}},
        {"title": "CD", "price": "11.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/cd-variant.jpg"}},
    ],
}

# Captured. A title with no artist half at all, and the anchor for the sole-tag
# fallback: the act is named only in the tags.
_ACTION_TIME_VISION = {
    "title": "Action Time Vision",
    "handle": "action-time-vision",
    "vendor": "HEAVY SOUL RECORDS",
    "product_type": "Music",
    "tags": ["Alternative TV"],
    "images": [{"src": "https://cdn.shopify.com/atv.png"}],
    "variants": [{"title": "7\"", "price": "14.99", "available": True, "featured_image": None}],
}

# Captured. The number-range trap: the first spaced dash sits between two
# years, so a plain split credits the record to "Far East New Rock Invention
# 1969". Sold out live; made available here so the parse is what is under test.
_FAR_EAST = {
    "title": "Far East New Rock Invention 1969 - 1975",
    "handle": "far-east-new-rock-invention-1969-1975",
    "vendor": "Monorail Music",
    "product_type": "Music",
    "tags": ["Various Artists"],
    "images": [],
    "variants": [{"title": "LP", "price": "32.99", "available": True, "featured_image": None}],
}

# Captured. A record whose sole variant is sold out.
_TEN_EAST = {
    "title": "10 East",
    "handle": "10-east",
    "vendor": "Dischord",
    "product_type": "Music",
    "tags": ["Lungfish"],
    "images": [{"src": "https://cdn.shopify.com/10-east.png"}],
    "variants": [{"title": "7\" vinyl", "price": "12.99", "available": False, "featured_image": None}],
}

# Captured. An in-store album launch: a record bundled with an event ticket,
# at a price that is neither. It carries an EMPTY product_type, which is why
# the kind gate admits a named type instead of rejecting a list of them.
_ALBUM_LAUNCH = {
    "title": "'The Bad Fire' Album Launch",
    "handle": "the-bad-fire-album-launch",
    "vendor": "Monorail Music",
    "product_type": "",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/launch.jpg"}],
    "variants": [
        {"title": "2LP (Monorail Exclusive Lemon Opaque Vinyl SIGNED) + Ticket",
         "price": "47.99", "available": True, "featured_image": None},
        {"title": "Ticket Only (1 per customer)", "price": "22.00", "available": True,
         "featured_image": None},
    ],
}

# Captured. Three in-stock pressings of one 7" on one product URL -- the
# reason the pressing name is appended to every row's title.
_THE_GIVER = {
    "title": "Chappell Roan - The Giver",
    "handle": "chappell-roan-the-giver",
    "vendor": "ISLAND",
    "product_type": "Music",
    "tags": ["Chappell Roan"],
    "images": [{"src": "https://cdn.shopify.com/giver.jpg"}],
    "variants": [
        {"title": "Limited Neon Orange 7\" with \"The Construction Worker\" Alternate Cover",
         "price": "13.99", "available": True, "featured_image": None},
        {"title": "Limited Swirl 7\" with \"The Plumber\" Alternate Cover",
         "price": "13.99", "available": True, "featured_image": None},
        {"title": "Limited Aqua 7\" with \"The Lawyer\" Alternate Cover",
         "price": "13.99", "available": False, "featured_image": None},
    ],
}

# Captured. A CD whose bonus poster is measured in inches. Without the
# dimension guard the `10" X 10"` is read as a 10-inch single and this is
# published as a record.
_TORTURED_POETS_CD = {
    "title": "Taylor Swift - The Tortured Poets Department",
    "handle": "taylor-swift-the-tortured-poets-department",
    "vendor": "UMR",
    "product_type": "Music",
    "tags": ["Taylor Swift"],
    "images": [],
    "variants": [{
        "title": "CD With \"The Manuscript\" Bonus Track + Collectible 20pp Booklet + 10\" X 10\" Poster",
        "price": "13.99", "available": True, "featured_image": None,
    }],
}

# Captured. A record bundled with a demo CD: the pressing names both media,
# and it is a record.
_YUMMY_FUR = {
    "title": "THE YUMMY FUR - Everybody Talks About The Weather",
    "handle": "the-yummy-fur-everybody-talks-about-the-weather",
    "vendor": "Monorail Music",
    "product_type": "Music",
    "tags": ["The Yummy Fur"],
    "images": [],
    "variants": [{"title": "Black vinyl LP with demo CD", "price": "24.99",
                  "available": True, "featured_image": None}],
}

# Captured. A pressing named by colour alone. It names no format, so the
# positive gate drops it -- the accepted cost documented on the gate.
_ECO_MIX = {
    "title": "Songs:Ohia - Didn’t It Rain (ReVINYL Edition)",
    "handle": "songs-ohia-didn-t-it-rain-revinyl-edition",
    "vendor": "SECRETLY CANADIAN",
    "product_type": "Music",
    "tags": ["Songs:Ohia"],
    "images": [],
    "variants": [{"title": "Eco Mix Random Colour", "price": "22.99",
                  "available": True, "featured_image": None}],
}

# Captured. A film, at the product_type the kind gate keeps out.
_FILM = {
    "title": "William Wyler - The Children's Hour",
    "handle": "william-wyler-the-childrens-hour",
    "vendor": "BFI",
    "product_type": "Film & TV",
    "tags": ["William Wyler"],
    "images": [],
    "variants": [{"title": "Blu-Ray", "price": "17.99", "available": True, "featured_image": None}],
}

# Captured. A multi-tag product: the store's tags are alphabetically sorted
# and mix the act with genre words, so the first tag here is a genre.
_LUCY_DACUS = {
    "title": "Lucy Dacus - No Burden (2026 Reissue)",
    "handle": "lucy-dacus-no-burden-2026-reissue",
    "vendor": "MATADOR",
    "product_type": "Music",
    "tags": ["Alt", "Lucy Dacus"],
    "images": [],
    "variants": [{"title": "Seaglass Colour Vinyl", "price": "28.99",
                  "available": True, "featured_image": None}],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([]))


async def _crawl(products):
    _mock_single_page(products)
    return [item async for item in Crawler().crawl_catalog()]


@pytest.fixture
def crawler():
    return Crawler()


def test_site_metadata():
    assert Crawler.site_name == "Monorail Music"
    assert Crawler.base_url == "https://monorailmusic.com"
    assert Crawler.genre == "marketplace"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre_summary


# --------------------------------------------------------------------------
# Title parsing
# --------------------------------------------------------------------------

@respx.mock
async def test_parses_artist_and_album_and_appends_the_pressing():
    assert await _crawl([_BULL_OF_THE_WOODS]) == [{
        "artist": "13th Floor Elevators",
        "title": "Bull of the Woods — Vinyl LP",
        "format": "Vinyl",
        "price": 25.99,
        "currency": "GBP",
        "url": "https://monorailmusic.com/products/13th-floor-elevators-bull-of-the-woods",
        "cover_image_url":
            "https://cdn.shopify.com/s/files/1/1000/6088/9430/files/a1333281053_10.png?v=1788772050",
    }]


@respx.mock
async def test_splits_on_the_first_dash_not_the_last():
    # Invented: no live title needs this, but album halves carrying their own
    # " - " run are routine, and a greedy split would report the artist as
    # "Adrianne Lenker - Songs".
    product = {**_BULL_OF_THE_WOODS, "title": "Adrianne Lenker - Songs - And Instrumentals"}
    items = await _crawl([product])
    assert items[0]["artist"] == "Adrianne Lenker"
    assert items[0]["title"].startswith("Songs - And Instrumentals — ")


@respx.mock
async def test_hyphenated_name_is_never_split_mid_word():
    # Invented. The separator requires whitespace on both sides, so a
    # hyphenated act keeps its name and the title falls to the tag fallback.
    product = {**_BULL_OF_THE_WOODS, "title": "Chik-Chik", "tags": ["Chik-Chik"]}
    items = await _crawl([product])
    assert items[0]["artist"] == "Chik-Chik"
    assert items[0]["title"] == "Chik-Chik — Vinyl LP"


@respx.mock
async def test_en_dash_separates_too():
    # Invented shape, live vocabulary: a handful of titles use an en dash.
    product = {**_BULL_OF_THE_WOODS, "title": "Broadcast – Distant Call"}
    items = await _crawl([product])
    assert items[0]["artist"] == "Broadcast"
    assert items[0]["title"] == "Distant Call — Vinyl LP"


@respx.mock
async def test_year_range_is_not_read_as_a_separator():
    # The dash sits between two years, so the split is refused and the sole
    # tag supplies the artist -- which is the right answer for all three live
    # titles shaped this way.
    items = await _crawl([_FAR_EAST])
    assert items[0]["artist"] == "Various Artists"
    assert items[0]["title"] == "Far East New Rock Invention 1969 - 1975 — LP"


@respx.mock
async def test_whitespace_in_the_title_is_collapsed():
    # Invented: guards the composed title against a payload carrying tabs or
    # doubled spaces, which would otherwise reach item_key verbatim.
    product = {**_BULL_OF_THE_WOODS, "title": "13th  Floor\tElevators -  Bull of the Woods"}
    items = await _crawl([product])
    assert items[0]["artist"] == "13th Floor Elevators"
    assert items[0]["title"] == "Bull of the Woods — Vinyl LP"


# --------------------------------------------------------------------------
# The artist fallback
# --------------------------------------------------------------------------

@respx.mock
async def test_sole_tag_supplies_the_artist_when_the_title_has_none():
    items = await _crawl([_ACTION_TIME_VISION])
    assert items[0]["artist"] == "Alternative TV"
    # The album keeps the whole title: the tag took nothing out of it.
    assert items[0]["title"] == "Action Time Vision — 7\""


@respx.mock
async def test_the_title_beats_the_tag_when_it_carries_an_artist():
    # Shopify stores tags as a comma-separated string, so an act whose name
    # contains a comma arrives pre-split and the tag is a fragment. The title
    # carries the name whole, which is why it is primary.
    product = {
        **_BULL_OF_THE_WOODS,
        "title": "Black Country, New Road - For The First Time",
        "tags": ["Black Country", "New Road"],
    }
    items = await _crawl([product])
    assert items[0]["artist"] == "Black Country, New Road"


@respx.mock
async def test_multiple_tags_never_supply_the_artist():
    # Tags are alphabetically sorted and mix the act with genre words, so on a
    # multi-tag product the first tag is whichever sorts first. Two tags may
    # equally be one comma-split name or two collaborators, and nothing in the
    # payload separates the readings -- so a title with no artist is skipped.
    product = {**_LUCY_DACUS, "title": "No Burden (2026 Reissue)"}
    items = await _crawl([product, _BULL_OF_THE_WOODS])
    assert [i["artist"] for i in items] == ["13th Floor Elevators"]


@respx.mock
async def test_a_genre_tag_never_becomes_the_artist():
    # The concrete failure the one-tag rule prevents: this product's tags sort
    # the genre word first.
    items = await _crawl([_LUCY_DACUS])
    assert items[0]["artist"] == "Lucy Dacus"


@respx.mock
async def test_no_tags_and_no_separator_yields_nothing():
    product = {**_BULL_OF_THE_WOODS, "title": "Untitled Oddity", "tags": []}
    items = await _crawl([product, _THE_GIVER])
    assert {i["artist"] for i in items} == {"Chappell Roan"}


@respx.mock
async def test_blank_and_non_string_tags_are_ignored():
    # Invented. A tag list whose only usable entry is one string still counts
    # as a sole tag; junk entries must not make it look like a multi-tag
    # product and cost the row.
    product = {**_ACTION_TIME_VISION, "tags": ["  ", None, "Alternative TV", 7]}
    items = await _crawl([product])
    assert items[0]["artist"] == "Alternative TV"


@respx.mock
async def test_vendor_is_never_used_as_the_artist():
    # `vendor` is the label here ("Dischord", "4AD", "BFI"), never the act.
    product = {**_BULL_OF_THE_WOODS, "title": "Some Compilation", "tags": []}
    items = await _crawl([product, _THE_GIVER])
    assert "Monorail Music" not in {i["artist"] for i in items}
    assert {i["artist"] for i in items} == {"Chappell Roan"}


# --------------------------------------------------------------------------
# The product_type gate
# --------------------------------------------------------------------------

@respx.mock
async def test_drops_products_outside_the_music_kind():
    items = await _crawl([_FILM, _BULL_OF_THE_WOODS])
    assert [i["artist"] for i in items] == ["13th Floor Elevators"]


@respx.mock
async def test_drops_the_album_launch_ticket_bundle():
    # A record and an event ticket sold as one product, at a price that is
    # neither. It names vinyl and would pass every other test, and it carries
    # an empty product_type rather than `Events`.
    items = await _crawl([_ALBUM_LAUNCH, _BULL_OF_THE_WOODS])
    assert [i["artist"] for i in items] == ["13th Floor Elevators"]


@respx.mock
async def test_the_kind_gate_is_case_and_whitespace_insensitive():
    product = {**_BULL_OF_THE_WOODS, "product_type": "  music  "}
    assert len(await _crawl([product])) == 1


# --------------------------------------------------------------------------
# The per-variant format gate
# --------------------------------------------------------------------------

@respx.mock
async def test_keeps_the_vinyl_variant_and_drops_the_cd_on_one_product():
    items = await _crawl([_AUTOSMILE])
    assert len(items) == 1
    assert items[0]["title"] == "Autosmile — Soil Brown Vinyl"
    assert items[0]["price"] == 23.99


@respx.mock
async def test_a_record_bundled_with_a_cd_is_still_a_record():
    items = await _crawl([_YUMMY_FUR])
    assert len(items) == 1
    assert items[0]["title"].endswith("— Black vinyl LP with demo CD")


@respx.mock
async def test_an_inch_measurement_of_a_poster_is_not_a_record():
    # `10" X 10"` is the bonus poster's size on a CD. Deleted before the inch
    # marker can read it as a 10-inch single.
    items = await _crawl([_TORTURED_POETS_CD, _BULL_OF_THE_WOODS])
    assert [i["artist"] for i in items] == ["13th Floor Elevators"]


@respx.mock
async def test_a_pressing_named_only_by_colour_is_dropped():
    # The accepted cost of a positive gate: a colour is not a format claim,
    # and colour names a CD edition too.
    items = await _crawl([_ECO_MIX, _BULL_OF_THE_WOODS])
    assert [i["artist"] for i in items] == ["13th Floor Elevators"]


@pytest.mark.parametrize("pressing", [
    "LP", "Vinyl LP", "2LP", "2xLP", "DLP", "LP2", "Black Vinyl", "7\"", "7 Inch",
    "12\" RECORD", "2x12\"", "10\"", "Limited Edition 12-inch Mix", "VL",
    "Vinyl Longplay 33 1/3", "White biovinyl + signed print", "Picture Disc",
    "180g LP", "flexi disc",
])
def test_live_vinyl_vocabulary_is_admitted(pressing):
    assert Crawler._is_vinyl(pressing) is True


@pytest.mark.parametrize("pressing", [
    "CD", "2CD", "Cd", "cd", "Compact Disc", "CD ALBUM", "CD EP", "CD with obi",
    "Cassette", "Cassette tape", "SACD", "CDr", "Digipak", "6 CDBoxset",
    "Blu-Ray", "DVD", "Eco Mix Random Colour", "Apricot Color Wax",
    "2026 Repress", "Limited Edition", "Deluxe Booklet Edition", "Ticket Only (1 per customer)",
])
def test_non_vinyl_and_formatless_vocabulary_is_rejected(pressing):
    assert Crawler._is_vinyl(pressing) is False


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

@respx.mock
async def test_sold_out_product_yields_nothing():
    assert await _crawl([_TEN_EAST]) == []


@respx.mock
async def test_only_the_literal_true_admits_a_variant():
    # The string "false" is truthy, so a falsiness test would publish a
    # sold-out record as in stock.
    for flag in ("false", "true", 1, None):
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
            return_value=_page_response([{
                **_BULL_OF_THE_WOODS,
                "variants": [{**_BULL_OF_THE_WOODS["variants"][0], "available": flag}],
            }, _AUTOSMILE]))
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
            return_value=_page_response([]))
        items = [i async for i in Crawler().crawl_catalog()]
        assert [i["artist"] for i in items] == ["@"], flag
        respx.reset()


# --------------------------------------------------------------------------
# One row per in-stock pressing
# --------------------------------------------------------------------------

@respx.mock
async def test_each_in_stock_pressing_gets_its_own_row():
    items = await _crawl([_THE_GIVER])
    assert len(items) == 2
    assert {i["title"] for i in items} == {
        "The Giver — Limited Neon Orange 7\" with \"The Construction Worker\" Alternate Cover",
        "The Giver — Limited Swirl 7\" with \"The Plumber\" Alternate Cover",
    }
    # Same artist and same product URL, so the pressing in the title is the
    # only thing keeping these rows off one item_key.
    assert len({(i["artist"], i["title"], i["url"]) for i in items}) == 2
    assert len({i["url"] for i in items}) == 1


@respx.mock
async def test_the_pressing_is_appended_even_on_a_sole_variant():
    # Not only on multi-variant products: a sibling selling out must not
    # re-title the surviving rows and orphan the saves keyed on the old
    # item_key.
    items = await _crawl([_BULL_OF_THE_WOODS])
    assert items[0]["title"] == "Bull of the Woods — Vinyl LP"


@respx.mock
async def test_the_album_stays_a_prefix_of_the_row_title():
    # db._library_release_match_sql matches a stock title exactly or as a
    # prefix followed by a space, so appending after the album is what keeps
    # a library release matchable.
    items = await _crawl([_THE_GIVER])
    assert all(i["title"].startswith("The Giver ") for i in items)


# --------------------------------------------------------------------------
# Prices, images, URLs
# --------------------------------------------------------------------------

@respx.mock
async def test_price_is_parsed_from_the_string_the_store_sends():
    items = await _crawl([_BULL_OF_THE_WOODS])
    assert items[0]["price"] == 25.99
    assert items[0]["currency"] == "GBP"


@pytest.mark.parametrize("raw", [None, "", "free", True, False, float("nan"),
                                 float("inf"), "0", "-5", [], {}])
def test_unusable_prices_become_none(raw):
    assert Crawler._price({"price": raw}) is None


@respx.mock
async def test_a_malformed_price_still_emits_the_row():
    product = {**_BULL_OF_THE_WOODS,
               "variants": [{**_BULL_OF_THE_WOODS["variants"][0], "price": None}]}
    items = await _crawl([product, _ACTION_TIME_VISION])
    assert len(items) == 2
    # Isolated nulls stay tolerated; only a catalog with no price anywhere
    # raises. See test_a_catalog_with_no_prices_at_all_raises.
    assert items[0]["price"] is None
    assert items[1]["price"] == 14.99


@respx.mock
async def test_cover_image_prefers_the_variant_image():
    items = await _crawl([_AUTOSMILE])
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/vinyl-variant.png"


@respx.mock
async def test_cover_image_falls_back_to_the_product_image():
    items = await _crawl([_BULL_OF_THE_WOODS])
    assert items[0]["cover_image_url"].endswith("a1333281053_10.png?v=1788772050")


@respx.mock
async def test_cover_image_is_none_when_the_product_has_no_images():
    items = await _crawl([_FAR_EAST])
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_url_is_built_from_the_handle():
    items = await _crawl([_ACTION_TIME_VISION])
    assert items[0]["url"] == "https://monorailmusic.com/products/action-time-vision"


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

@respx.mock
async def test_paginates_until_an_empty_page():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_BULL_OF_THE_WOODS]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_ACTION_TIME_VISION]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [i async for i in Crawler().crawl_catalog()]
    assert [i["artist"] for i in items] == ["13th Floor Elevators", "Alternative TV"]


# --------------------------------------------------------------------------
# Drift guards. replace_stock_items() DELETEs the previous snapshot before
# inserting and _sync_stock only skips it when the crawl raised, so a
# completed-but-empty walk is destructive where a raise is inert.
# --------------------------------------------------------------------------

@respx.mock
async def test_an_empty_collection_raises():
    with pytest.raises(RuntimeError, match="returned no products"):
        await _crawl([])


@respx.mock
async def test_a_catalog_with_no_music_kind_raises():
    with pytest.raises(RuntimeError, match="kind-taxonomy drift"):
        await _crawl([_FILM])


@respx.mock
async def test_a_catalog_whose_music_names_no_vinyl_raises():
    # Both gates here are positive, so a renamed format vocabulary empties the
    # walk while every product still parses.
    cd_only = {**_AUTOSMILE, "variants": [_AUTOSMILE["variants"][1]]}
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        await _crawl([cd_only])


@respx.mock
async def test_a_catalog_with_no_prices_at_all_raises():
    product = {**_BULL_OF_THE_WOODS,
               "variants": [{**_BULL_OF_THE_WOODS["variants"][0], "price": None}]}
    other = {**_ACTION_TIME_VISION,
             "variants": [{**_ACTION_TIME_VISION["variants"][0], "price": "junk"}]}
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _crawl([product, other])


@respx.mock
async def test_losing_every_artist_raises_rather_than_emptying_the_snapshot():
    product = {**_BULL_OF_THE_WOODS, "title": "Bull of the Woods", "tags": []}
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _crawl([product])


@respx.mock
async def test_losing_every_variant_collection_raises():
    # Reported as a variants-level failure, not as the format-taxonomy drift
    # that is equally true of it: a product whose variants cannot be read has
    # no admitted pressing either, so the format guard would otherwise answer
    # every variants-level failure with the one diagnosis that is not the cause.
    with pytest.raises(RuntimeError, match="variant-identity-source drift"):
        await _crawl([{**_BULL_OF_THE_WOODS, "variants": []},
                      {**_ACTION_TIME_VISION, "variants": None}])


@respx.mock
async def test_a_renamed_format_vocabulary_still_reports_itself():
    # The ordering above must not hide the guard it sits in front of: a
    # renamed format vocabulary leaves every variant perfectly readable.
    renamed = {**_BULL_OF_THE_WOODS,
               "variants": [{**_BULL_OF_THE_WOODS["variants"][0], "title": "Long Player"}]}
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        await _crawl([renamed])


@respx.mock
async def test_a_non_string_variant_title_is_counted_not_raised_through():
    # Discard-and-keep-going: one malformed variant must not abort the source.
    product = {**_AUTOSMILE, "variants": [
        {"title": 7, "price": "9.99", "available": True, "featured_image": None},
        _AUTOSMILE["variants"][0],
    ]}
    items = await _crawl([product])
    assert [i["title"] for i in items] == ["Autosmile — Soil Brown Vinyl"]


@respx.mock
async def test_losing_every_product_identity_raises():
    product = {**_BULL_OF_THE_WOODS, "handle": ""}
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _crawl([product])


@respx.mock
async def test_losing_every_availability_flag_raises():
    product = {**_BULL_OF_THE_WOODS,
               "variants": [{**_BULL_OF_THE_WOODS["variants"][0], "available": "false"}]}
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _crawl([product])


@respx.mock
async def test_one_readable_variant_does_not_vouch_for_an_unreadable_sibling():
    # all(), not any(): a product whose black pressing is a readable False and
    # whose coloured pressing carries the string "false" yields nothing, and
    # under any() would vouch for an emptiness half its own doing.
    product = {**_THE_GIVER, "variants": [
        {**_THE_GIVER["variants"][0], "available": False},
        {**_THE_GIVER["variants"][1], "available": "false"},
    ]}
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _crawl([product])


@respx.mock
async def test_a_genuinely_sold_out_catalog_does_not_raise():
    # The guards must not fire on a shop that has simply sold out: everything
    # here is readable, parseable and out of stock.
    assert await _crawl([_TEN_EAST]) == []


@respx.mock
async def test_an_isolated_bad_product_does_not_raise_beside_real_rows():
    # Each empty-outcome guard is gated on having yielded nothing, so one
    # artist-less or unreadable product among real rows stays an ordinary
    # skipped row.
    artistless = {**_BULL_OF_THE_WOODS, "title": "No Artist Here", "tags": []}
    unreadable = {**_ACTION_TIME_VISION, "variants": []}
    items = await _crawl([artistless, unreadable, _THE_GIVER])
    assert len(items) == 2
    assert {i["artist"] for i in items} == {"Chappell Roan"}


@respx.mock
async def test_a_cd_only_product_never_vouches_for_the_catalog():
    # The per-product tallies are gated on the product having an admitted
    # pressing. A CD-only product would never have yielded a row whatever its
    # title said, so it must not satisfy the artist guard on behalf of a
    # record that has lost its own.
    cd_only_with_artist = {**_AUTOSMILE, "variants": [_AUTOSMILE["variants"][1]]}
    artistless_record = {**_BULL_OF_THE_WOODS, "title": "Bull of the Woods", "tags": []}
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _crawl([cd_only_with_artist, artistless_record])


@respx.mock
async def test_a_non_mapping_variant_entry_is_discarded_not_read():
    # Dropped before anything reads it, so a junk entry is an ordinary skipped
    # row rather than an AttributeError from inside the yield loop, aborting
    # the whole source over one malformed entry.
    product = {**_AUTOSMILE, "variants": ["junk", None, _AUTOSMILE["variants"][0]]}
    items = await _crawl([product])
    assert [i["title"] for i in items] == ["Autosmile — Soil Brown Vinyl"]


@respx.mock
async def test_every_variant_being_non_mapping_raises():
    # And it is counted, not silently dropped: a catalog whose variants have
    # all been mangled must not complete as a legitimately empty walk.
    with pytest.raises(RuntimeError, match="variant-identity-source drift"):
        await _crawl([{**_BULL_OF_THE_WOODS, "variants": ["junk"]}])


@respx.mock
async def test_a_blank_variant_title_yields_no_row():
    # Every live product names its format, so a nameless variant is drift
    # rather than the sole-variant placeholder other stores in this fleet
    # send. Admitting it would emit a bare-album row sharing its title and
    # URL -- and so its item_key -- with any sibling built the same way.
    product = {**_THE_GIVER, "variants": [
        {**_THE_GIVER["variants"][0], "title": ""},
        {**_THE_GIVER["variants"][1], "title": "   "},
    ]}
    items = await _crawl([product, _BULL_OF_THE_WOODS])
    assert [i["title"] for i in items] == ["Bull of the Woods — Vinyl LP"]


@respx.mock
async def test_every_variant_title_being_blank_raises():
    with pytest.raises(RuntimeError, match="variant-identity-source drift"):
        await _crawl([{**_BULL_OF_THE_WOODS, "variants": [
            {**_BULL_OF_THE_WOODS["variants"][0], "title": ""}]}])
