import httpx
import pytest
import respx

from crawlers.asbestosrecords import Crawler


@pytest.mark.parametrize("name,artists,expected_artist,expected_album", [
    # Ordinary shape: split on " - ".
    (
        "Suicide Machines - Destruction by Definition LP",
        [],
        "Suicide Machines",
        "Destruction by Definition LP",
    ),
    # Multi-word album with its own internal punctuation survives untouched
    # after the first separator.
    (
        "Sgt Scagnetti - Just Another Trick LP",
        [{"id": 1, "name": "Sgt Scagnetti"}],
        "Sgt Scagnetti",
        "Just Another Trick LP",
    ),
])
def test_parse_artist_title_splits_on_first_hyphen(name, artists, expected_artist, expected_album):
    assert Crawler._parse_artist_title(name, artists) == (expected_artist, expected_album)


def test_parse_artist_title_falls_back_to_curated_artists_when_no_separator():
    # "The Least Worst of the Suicide Machines 2xLP" has no hyphen at all.
    # Bigcartel's own curated `artists` field names the real artist.
    name = "The Least Worst of the Suicide Machines 2xLP"
    artists = [{"id": 72919, "name": "Suicide Machines", "permalink": "suicide-machines"}]
    assert Crawler._parse_artist_title(name, artists) == ("Suicide Machines", name)


def test_parse_artist_title_does_not_split_hyphen_glued_to_a_word():
    # "Machines-On" has no surrounding whitespace, so the whitespace-anchored
    # separator must not treat it as the artist/album boundary -- confirmed
    # live, this exact title has no other hyphen, so it falls through to the
    # curated `artists` fallback exactly like the no-hyphen case above.
    name = "The Suicide Machines-On the Eve of Destruction 2xLP"
    artists = [{"id": 72919, "name": "Suicide Machines", "permalink": "suicide-machines"}]
    assert Crawler._parse_artist_title(name, artists) == ("Suicide Machines", name)


def test_parse_artist_title_returns_none_artist_when_no_separator_and_no_curated_artists():
    name = "Black guy fawkes birthday bash!"
    assert Crawler._parse_artist_title(name, []) == (None, name)


def test_parse_artist_title_normalizes_various_artists_to_various():
    # Discogs' own entity name is "Various", not "Various Artists" --
    # db.py's _library_match_fragment does an exact LOWER() equality against
    # the catalog artist, so "Various Artists" would never match.
    name = "Various Artists - No Worries: east coast love for a west coast friend"
    artists = [{"id": 12481, "name": "The Slackers"}]
    assert Crawler._parse_artist_title(name, artists) == (
        "Various", "No Worries: east coast love for a west coast friend"
    )


def test_parse_artist_title_unescapes_html_entities():
    # Confirmed live: this exact title carries a literal HTML entity in the
    # JSON `name` field.
    name = "River City Extension - Don&#x27;t Let the Sun Go Down on Your Anger 2xLP"
    assert Crawler._parse_artist_title(name, []) == (
        "River City Extension", "Don't Let the Sun Go Down on Your Anger 2xLP"
    )


_MULTI_OPTION_PRODUCT = {
    "id": 1,
    "name": 'Sgt Scagnetti - Just Another Trick LP',
    "url": "/product/sgt-scagnetti-just-another-trick-lp",
    "status": "active",
    "images": [{"url": "https://assets.bigcartel.com/product_images/1/scagnetti.jpg"}],
    "options": [
        {"id": 10, "name": "Maroon vinyl", "price": 25.0, "sold_out": False},
        {"id": 11, "name": "Test Pressing", "price": 50.0, "sold_out": False},
    ],
    "artists": [],
    "categories": [],
}

_SINGLE_OPTION_PRODUCT = {
    "id": 2,
    "name": "No Fun At All - Master Celebrations 2xLP (import) **PREORDER**",
    "url": "/product/no-fun-at-all-master-celebrations-2xlp-import-preorder",
    "status": "active",
    "images": [{"url": "https://assets.bigcartel.com/product_images/2/nofunatall.jpg"}],
    "options": [
        {"id": 20, "name": "No Fun At All - Master Celebrations 2xLP (import) **PREORDER**",
         "price": 35.0, "sold_out": False},
    ],
    "artists": [],
    "categories": [{"id": 1, "name": "Vinyl"}],
}

_NON_VINYL_PRODUCT = {
    "id": 3,
    "name": "Protect Trans Kids - Tshirt",
    "url": "/product/protect-trans-kids-tshirt",
    "status": "active",
    "images": [],
    "options": [{"id": 30, "name": "Medium", "price": 25.0, "sold_out": False}],
    "artists": [],
    "categories": [{"id": 2, "name": "Shirts"}],
}

_NO_ATTRIBUTION_PRODUCT = {
    "id": 4,
    "name": "Ashen Dawn Live Session LP",
    "url": "/product/ashen-dawn-live-session-lp",
    "status": "active",
    "images": [],
    "options": [{"id": 40, "name": "Ashen Dawn Live Session LP", "price": 33.0, "sold_out": False}],
    "artists": [],
    "categories": [],
}

# Confirmed live: a real release with no format token anywhere in its name,
# but tagged with the store's own `Vinyl` category -- one of the 11 products
# the name-only gate was found to drop on final whole-branch review.
_VINYL_CATEGORY_NO_FORMAT_TOKEN_PRODUCT = {
    "id": 5,
    "name": "David McWane - The Gypsy Mile",
    "url": "/product/david-mcwane-the-gypsy-mile",
    "status": "active",
    "images": [{"url": "https://assets.bigcartel.com/product_images/5/gypsymile.jpg"}],
    "options": [{"id": 50, "name": "David McWane - The Gypsy Mile", "price": 20.0, "sold_out": False}],
    "artists": [],
    "categories": [{"id": 1, "name": "Vinyl"}],
}

# Confirmed live: a standalone CD product -- no format token in its name and
# no `Vinyl` category, so it must stay excluded under the union gate.
_CD_ONLY_PRODUCT = {
    "id": 6,
    "name": "Treephort - And the Streets Will Run Red CD",
    "url": "/product/treephort-and-the-streets-will-run-red-cd",
    "status": "active",
    "images": [],
    "options": [{"id": 60, "name": "Treephort - And the Streets Will Run Red CD",
                 "price": 10.0, "sold_out": False}],
    "artists": [],
    "categories": [{"id": 3, "name": "CDs"}],
}

# Confirmed live: a subscription bundle, not a release -- matches `_FORMAT_RE`
# on the word "Vinyl" in its own name, so it's kept regardless of the
# `Vinyl`-category check. Documented, accepted noise, unchanged by the union
# gate.
_VINYL_WORD_BUNDLE_PRODUCT = {
    "id": 7,
    "name": "2025 Ska Vinyl Supscription - 10 LPs",
    "url": "/product/2025-ska-vinyl-supscription-10-lps",
    "status": "active",
    "images": [],
    "options": [{"id": 70, "name": "2025 Ska Vinyl Supscription - 10 LPs",
                 "price": 250.0, "sold_out": False}],
    "artists": [],
    "categories": [],
}


def test_items_emits_one_row_per_available_option_with_variant_suffix():
    items = Crawler._items(_MULTI_OPTION_PRODUCT)
    assert [i["title"] for i in items] == [
        "Just Another Trick LP — Maroon vinyl",
        "Just Another Trick LP — Test Pressing",
    ]
    assert all(i["artist"] == "Sgt Scagnetti" for i in items)
    assert [i["price"] for i in items] == [25.0, 50.0]


def test_items_emits_full_row_shape():
    items = Crawler._items(_MULTI_OPTION_PRODUCT)
    assert items[0] == {
        "artist": "Sgt Scagnetti",
        "title": "Just Another Trick LP — Maroon vinyl",
        "format": "Vinyl",
        "price": 25.0,
        "currency": "USD",
        "url": "https://asbestosrecords.bigcartel.com/product/sgt-scagnetti-just-another-trick-lp",
        "cover_image_url": "https://assets.bigcartel.com/product_images/1/scagnetti.jpg",
    }


def test_items_omits_variant_suffix_when_option_name_equals_product_name():
    # Bigcartel has no Shopify-style "Default Title" placeholder -- a
    # single-option product just repeats its own name as the option name.
    items = Crawler._items(_SINGLE_OPTION_PRODUCT)
    assert len(items) == 1
    assert items[0]["title"] == "Master Celebrations 2xLP (import) **PREORDER**"


def test_items_drops_products_with_no_format_token_in_name():
    assert Crawler._items(_NON_VINYL_PRODUCT) == []


def test_items_includes_vinyl_categorized_product_with_no_format_token_in_name():
    # The union gate: `_FORMAT_RE` alone would drop this product (no
    # vinyl/lp/ep/inch token in its name), but its `Vinyl` category now
    # includes it -- the whole-branch-review-confirmed gap this fix closes.
    items = Crawler._items(_VINYL_CATEGORY_NO_FORMAT_TOKEN_PRODUCT)
    assert len(items) == 1
    assert items[0]["artist"] == "David McWane"
    assert items[0]["title"] == "The Gypsy Mile"


def test_items_drops_cd_only_product_with_no_vinyl_category():
    # Neither signal fires: no format token in the name, and the store's own
    # `categories` field says CDs, not Vinyl.
    assert Crawler._items(_CD_ONLY_PRODUCT) == []


def test_items_keeps_accepted_noise_bundle_matching_format_regex_on_name():
    # Documents the known, accepted false positive -- unaffected by the
    # union gate since it already passes on the `\bvinyl\b` word match.
    items = Crawler._items(_VINYL_WORD_BUNDLE_PRODUCT)
    assert len(items) == 1
    assert items[0]["title"] == "10 LPs"


def test_items_drops_products_with_no_artist_source():
    assert Crawler._items(_NO_ATTRIBUTION_PRODUCT) == []


def test_items_skips_sold_out_options():
    product = {**_MULTI_OPTION_PRODUCT, "options": [
        {**_MULTI_OPTION_PRODUCT["options"][0], "sold_out": True},
        _MULTI_OPTION_PRODUCT["options"][1],
    ]}
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "Just Another Trick LP — Test Pressing"


def test_items_returns_empty_when_all_options_sold_out():
    product = {**_MULTI_OPTION_PRODUCT, "options": [
        {**o, "sold_out": True} for o in _MULTI_OPTION_PRODUCT["options"]
    ]}
    assert Crawler._items(product) == []


def test_items_falls_back_to_none_cover_image_when_no_images():
    product = {**_SINGLE_OPTION_PRODUCT, "images": []}
    items = Crawler._items(product)
    assert items[0]["cover_image_url"] is None


def test_items_handles_non_numeric_price():
    product = {**_MULTI_OPTION_PRODUCT, "options": [
        {"id": 10, "name": "Maroon vinyl", "price": None, "sold_out": False},
    ]}
    items = Crawler._items(product)
    assert items[0]["price"] is None


_PRODUCTS_URL = "https://asbestosrecords.bigcartel.com/products.json"


@respx.mock
async def test_crawl_catalog_yields_items_from_the_single_response():
    respx.get(_PRODUCTS_URL).mock(
        return_value=httpx.Response(200, json=[_MULTI_OPTION_PRODUCT, _SINGLE_OPTION_PRODUCT]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 3  # 2 options on the first product, 1 on the second
    assert items[0]["artist"] == "Sgt Scagnetti"
    assert items[2]["title"] == "Master Celebrations 2xLP (import) **PREORDER**"


@respx.mock
async def test_crawl_catalog_drops_non_vinyl_products_from_the_feed():
    respx.get(_PRODUCTS_URL).mock(
        return_value=httpx.Response(200, json=[_NON_VINYL_PRODUCT, _NO_ATTRIBUTION_PRODUCT]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert items == []


def test_site_metadata():
    assert Crawler.site_name == "Asbestos Records"
    assert Crawler.base_url == "https://asbestosrecords.bigcartel.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre_summary == "Ska, punk, and hardcore label and record store."
