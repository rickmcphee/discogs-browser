import httpx
import pytest
import respx

from crawlers.jetglowrecordings import Crawler

_MEDIA = [{"id": 1, "name": "Vinyl - Cassette - CD"}]


def _product(**overrides):
    base = {
        "id": 1,
        "name": "LOWDRIVE - RISE",
        "url": "/product/lowdrive-rise",
        "status": "active",
        "images": [{"url": "https://assets.bigcartel.com/product_images/1/rise.jpg"}],
        "options": [{"id": 10, "name": "Vinyl", "price": 25.0, "sold_out": False}],
        "artists": [{"id": 1, "name": "Lowdrive"}],
        "categories": _MEDIA,
    }
    return {**base, **overrides}


# --- artist / album parsing ------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("LOWDRIVE - RISE", ("LOWDRIVE", "RISE")),
    ("KORY CLARKE - PAYBACK'S A BITCH", ("KORY CLARKE", "PAYBACK'S A BITCH")),
    # Everything after the first separator stays in the album, including a
    # second separator -- the trailing format blurb is removed later, by
    # _strip_format_suffix, not here.
    (
        "WE ARE IMPALA - LES EFIMERES - VINYL EDITION",
        ("WE ARE IMPALA", "LES EFIMERES - VINYL EDITION"),
    ),
])
def test_parse_artist_title_splits_on_first_separator(name, expected):
    assert Crawler._parse_artist_title(name, []) == expected


def test_parse_artist_title_prefers_title_split_over_curated_artists():
    # Confirmed live: this product is tagged "Space Age Playboys" while its
    # own title bills Warrior Soul. The literal split has to win.
    name = "WARRIOR SOUL - THE SPACE AGE PLAYBOYS (30TH ANNIVERSARY) CD ED."
    artists = [{"id": 2, "name": "Space Age Playboys"}]
    assert Crawler._parse_artist_title(name, artists)[0] == "WARRIOR SOUL"


def test_parse_artist_title_prefers_title_split_over_slashed_curated_artist():
    # Confirmed live: all three Kory Clarke releases are tagged
    # "Kory Clarke / Warrior Soul", which matches no Discogs artist.
    name = "KORY CLARKE - OPIUM HOTEL II"
    artists = [{"id": 3, "name": "Kory Clarke / Warrior Soul"}]
    assert Crawler._parse_artist_title(name, artists) == ("KORY CLARKE", "OPIUM HOTEL II")


def test_parse_artist_title_falls_back_to_curated_artists_when_no_separator():
    name = "Peaks"
    artists = [{"id": 4, "name": "Peaks"}]
    assert Crawler._parse_artist_title(name, artists) == ("Peaks", "Peaks")


def test_parse_artist_title_does_not_split_hyphen_glued_to_a_word():
    name = "LOWDRIVE-RISE Reissue"
    assert Crawler._parse_artist_title(name, []) == (None, "LOWDRIVE-RISE Reissue")


def test_parse_artist_title_returns_none_when_no_separator_and_no_curated_artists():
    assert Crawler._parse_artist_title("Peaks", []) == (None, "Peaks")


def test_parse_artist_title_returns_none_when_curated_artist_name_is_blank():
    # A blank name must not produce an empty-string artist -- that would slip
    # past _items()'s `if artist is None` guard.
    for artists in ([{"id": 1, "name": ""}], [{"id": 1, "name": "   "}], [{"id": 1}]):
        assert Crawler._parse_artist_title("Peaks", artists) == (None, "Peaks")


def test_parse_artist_title_normalizes_various_artists_to_various():
    # Discogs' entity name is "Various" -- _library_match_fragment does exact
    # LOWER() equality on artist, so "Various Artists" would never match.
    assert Crawler._parse_artist_title("Various Artists - Jetglow Sampler", [])[0] == "Various"


def test_parse_artist_title_unescapes_html_entities():
    name = "KORY CLARKE - PAYBACK&#x27;S A BITCH"
    assert Crawler._parse_artist_title(name, []) == ("KORY CLARKE", "PAYBACK'S A BITCH")


# --- trailing format-blurb strip -------------------------------------------

@pytest.mark.parametrize("album,expected", [
    # Every real trailing blurb shape on the live store.
    ("LES EFIMERES - VINYL EDITION", "LES EFIMERES"),
    ("VISIONS - VINYLS AND BUNDLE", "VISIONS"),
    ("APART - VINYL AND BUNDLE", "APART"),
    ("MR. RIGHT HAND MAN - VINYL AND CD", "MR. RIGHT HAND MAN"),
    ("BURN THE STREETS AGAIN - CD AND CASSETTE", "BURN THE STREETS AGAIN"),
    ("Lonely Men In Love - LP version, CD included", "Lonely Men In Love"),
    (
        "DRUGS, GOD AND THE NEW REPUBLIC (35th ANNYVERSARY ED.) - VINYL",
        "DRUGS, GOD AND THE NEW REPUBLIC (35th ANNYVERSARY ED.)",
    ),
])
def test_strip_format_suffix_removes_trailing_blurb(album, expected):
    assert Crawler._strip_format_suffix(album) == expected


@pytest.mark.parametrize("album", [
    # No trailing " - " segment at all.
    "RISE",
    "MIND THE BOLLOCKS HERE'S THE KELLY GANG",
    # A parenthesised format note is not a separate segment, so it survives.
    "CARILLON INFERNALE (LP VERSION)",
    "Blackborn Special Edition Double LP (CD included)",
    # The trailing segment contains an ordinary word, so it is a real part of
    # the title, not a blurb.
    "Live At Rocketbooster - The Full Session",
])
def test_strip_format_suffix_leaves_real_titles_alone(album):
    assert Crawler._strip_format_suffix(album) == album


def test_strip_format_suffix_never_consumes_the_whole_album():
    # An album made entirely of format words must not be stripped to "".
    assert Crawler._strip_format_suffix("VINYL - CD") == "VINYL"


# --- availability ----------------------------------------------------------

def test_items_drops_sold_out_products_by_product_status():
    # The decisive divergence from asbestosrecords.py: confirmed live, all 114
    # options on this store report sold_out=False, including on every product
    # the storefront renders "Sold Out". Filtering on the option flag alone
    # would publish all six of them as in stock.
    product = _product(
        name="WARRIOR SOUL - THE SPACE AGE PLAYBOYS (30TH ANNIVERSARY) DOUBLE VINYL ED.",
        status="sold-out",
        options=[{"id": 11, "name": "Double Vinyl + CD", "price": 45.0, "sold_out": False}],
    )
    assert Crawler._items(product) == []


def test_items_still_honours_the_option_level_sold_out_flag():
    # Inert on this store today, but a partially sold-out product could set it.
    product = _product(options=[
        {"id": 10, "name": "Black Vinyl", "price": 15.0, "sold_out": True},
        {"id": 11, "name": "Red Vinyl", "price": 20.0, "sold_out": False},
    ])
    items = Crawler._items(product)
    assert [i["title"] for i in items] == ["RISE — Red Vinyl"]


# --- category gate ---------------------------------------------------------

def test_items_drops_non_media_categories():
    shirt = _product(
        name="CRAVEN T-SHIRT",
        categories=[{"id": 2, "name": "T-Shirts & Sweatshirts"}],
        options=[{"id": 12, "name": "Size M", "price": 15.0, "sold_out": False}],
    )
    assert Crawler._items(shirt) == []


def test_items_drops_a_poster_whose_option_names_vinyl():
    # Confirmed live: the poster product carries an option literally named
    # "Poster + Vinyl". _VINYL_RE would accept it, so the merch category gate
    # is what keeps it out -- this is why the gate exists.
    poster = _product(
        name='"Hobolo" - Raquel Burgueno Sepulvea',
        categories=[{"id": 3, "name": "Poster & Postcard"}],
        options=[
            {"id": 13, "name": "Poster", "price": 15.0, "sold_out": False},
            {"id": 14, "name": "Poster + Vinyl", "price": 25.0, "sold_out": False},
        ],
    )
    assert Crawler._items(poster) == []


# --- per-option vinyl gate -------------------------------------------------

def test_items_keeps_only_the_vinyl_options_of_a_mixed_format_product():
    # Confirmed live. A product-level format gate cannot express this: the CD
    # and the opaque "Special Box" sit alongside the vinyl in one product.
    product = _product(
        name="KELLY GANG - MIND THE BOLLOCKS HERE'S THE KELLY GANG",
        options=[
            {"id": 20, "name": "CD", "price": 8.0, "sold_out": False},
            {"id": 21, "name": "Vinyl", "price": 17.0, "sold_out": False},
            {"id": 22, "name": "Special Box", "price": 25.0, "sold_out": False},
        ],
    )
    items = Crawler._items(product)
    assert [(i["title"], i["price"]) for i in items] == [
        ("MIND THE BOLLOCKS HERE'S THE KELLY GANG — Vinyl", 17.0),
    ]


@pytest.mark.parametrize("option_name,kept", [
    ("Black Vinyl", True),
    ("Smoked Red Vinyl", True),
    ("Vinyl + CD", True),
    ("Black Vinyls + CD", True),
    ("Bundle Black Vinyls + Digipack CD", True),
    ("Special Bundle Vinyl, CD, Cassette, Stiker, Pin", True),
    ("Vinyl Blue", True),
    ("CD", False),
    ("CD Digipack", False),
    ("CD Digipack + Cassette", False),
    ("Cassette", False),
    ("CD only", False),
    ("T-Shirt + Postcard w/download code", False),
    # Opaque bundles are declined: their contents are unknowable from the
    # feed, which is why the gate is a positive match rather than a
    # non-vinyl blocklist.
    ("Special Bluedeep Bundle 1", False),
    ("Special Box", False),
])
def test_items_option_vinyl_gate(option_name, kept):
    product = _product(options=[
        {"id": 30, "name": option_name, "price": 20.0, "sold_out": False},
    ])
    assert len(Crawler._items(product)) == (1 if kept else 0)


def test_items_falls_back_to_the_product_name_when_the_option_echoes_it():
    # Big Cartel has no "Default Title" placeholder -- a single-option product
    # repeats its own name, so the option carries no format signal. This
    # fallback is the only thing keeping these releases.
    name = "THE BROKENDOLLS - CARILLON INFERNALE (LP VERSION)"
    product = _product(
        name=name,
        options=[{"id": 31, "name": name, "price": 12.0, "sold_out": False}],
    )
    items = Crawler._items(product)
    assert len(items) == 1
    # No variant suffix, and no doubled title.
    assert items[0]["title"] == "CARILLON INFERNALE (LP VERSION)"


def test_items_drops_an_echoing_option_with_no_format_token_anywhere():
    name = "CAPOBRANCO - In Dipendenza"
    product = _product(
        name=name,
        options=[{"id": 32, "name": name, "price": 15.0, "sold_out": False}],
    )
    assert Crawler._items(product) == []


# --- row shape -------------------------------------------------------------

def test_items_emits_full_row_shape_in_eur():
    items = Crawler._items(_product())
    assert items[0] == {
        "artist": "LOWDRIVE",
        "title": "RISE — Vinyl",
        "format": "Vinyl",
        "currency": "EUR",
        "price": 25.0,
        "url": "https://jetglowrecordings.bigcartel.com/product/lowdrive-rise",
        "cover_image_url": "https://assets.bigcartel.com/product_images/1/rise.jpg",
    }


def test_items_strips_the_blurb_before_appending_the_option_name():
    # The end-to-end point of _strip_format_suffix: without it this reads
    # "... (35th ANNYVERSARY ED.) - VINYL — Vinyl + CD".
    product = _product(
        name="WARRIOR SOUL - DRUGS, GOD AND THE NEW REPUBLIC (35th ANNYVERSARY ED.) - VINYL",
        options=[
            {"id": 40, "name": "Vinyl", "price": 38.0, "sold_out": False},
            {"id": 41, "name": "Vinyl + CD", "price": 48.0, "sold_out": False},
        ],
    )
    assert [i["title"] for i in Crawler._items(product)] == [
        "DRUGS, GOD AND THE NEW REPUBLIC (35th ANNYVERSARY ED.) — Vinyl",
        "DRUGS, GOD AND THE NEW REPUBLIC (35th ANNYVERSARY ED.) — Vinyl + CD",
    ]


def test_items_drops_products_with_no_artist_source():
    product = _product(name="Peaks", artists=[], options=[
        {"id": 42, "name": "Black Vinyl", "price": 5.0, "sold_out": False},
    ])
    assert Crawler._items(product) == []


def test_items_falls_back_to_none_cover_image_when_no_images():
    assert Crawler._items(_product(images=[]))[0]["cover_image_url"] is None


def test_items_handles_non_numeric_price():
    product = _product(options=[
        {"id": 43, "name": "Vinyl", "price": None, "sold_out": False},
    ])
    assert Crawler._items(product)[0]["price"] is None


# --- crawl_catalog ---------------------------------------------------------

_PRODUCTS_URL = "https://jetglowrecordings.bigcartel.com/products.json"


@respx.mock
async def test_crawl_catalog_yields_items_from_the_single_response():
    mixed = _product(
        id=2,
        name="KRÖWNN - BLÜEDEEP",
        url="/product/krownn-bluedeep",
        options=[
            {"id": 50, "name": "Vinyl Blue + CD", "price": 25.0, "sold_out": False},
            {"id": 51, "name": "Vinyl Blue", "price": 20.0, "sold_out": False},
            {"id": 52, "name": "CD", "price": 10.0, "sold_out": False},
            {"id": 53, "name": "Cassette", "price": 8.0, "sold_out": False},
        ],
    )
    respx.get(_PRODUCTS_URL).mock(return_value=httpx.Response(200, json=[_product(), mixed]))

    items = [item async for item in Crawler().crawl_catalog()]

    assert [i["title"] for i in items] == [
        "RISE — Vinyl",
        "BLÜEDEEP — Vinyl Blue + CD",
        "BLÜEDEEP — Vinyl Blue",
    ]


@respx.mock
async def test_crawl_catalog_yields_nothing_when_every_product_is_excluded():
    sold_out = _product(id=3, status="sold-out")
    shirt = _product(id=4, categories=[{"id": 2, "name": "T-Shirts & Sweatshirts"}])
    respx.get(_PRODUCTS_URL).mock(return_value=httpx.Response(200, json=[sold_out, shirt]))

    assert [item async for item in Crawler().crawl_catalog()] == []


@respx.mock
async def test_crawl_catalog_raises_on_an_http_error():
    # "[] means the site answered and has nothing; any failure must raise" --
    # a crawler that swallows errors never cools its site off.
    respx.get(_PRODUCTS_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in Crawler().crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Jetglow Recordings"
    assert Crawler.base_url == "https://jetglowrecordings.bigcartel.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
