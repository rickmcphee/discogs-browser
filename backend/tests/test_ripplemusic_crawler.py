import httpx
import pytest
import respx

import crawl_progress
from crawlers.ripplemusic import Crawler

_VINYL_CATEGORY = [{"id": 1, "name": '12" Vinyl'}]


def _product(**overrides):
    base = {
        "id": 1,
        "name": "Mothership - Mothership Vinyl LP",
        "url": "/product/mothership-mothership-lp-black-vinyl",
        "status": "active",
        "images": [{"url": "https://assets.bigcartel.com/product_images/1/mothership.jpg"}],
        "options": [{"id": 10, "name": "Solid White Vinyl", "price": 24.0, "sold_out": False}],
        "artists": [{"id": 1, "name": "Mothership"}],
        "categories": _VINYL_CATEGORY,
    }
    return {**base, **overrides}


# --- artist / album parsing ------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Mothership - Mothership Vinyl LP", ("Mothership", "Mothership Vinyl LP")),
    (
        "Cortez - Sell the Future Deluxe Vinyl Editions",
        ("Cortez", "Sell the Future Deluxe Vinyl Editions"),
    ),
    # Everything after the first separator stays in the album, including a
    # second separator. Unlike jetglowrecordings.py there is no trailing
    # blurb strip here -- see the design doc's "Title composition".
    (
        'Wo Fat - The Conjuring - 12" Vinyl',
        ("Wo Fat", 'The Conjuring - 12" Vinyl'),
    ),
])
def test_parse_artist_title_splits_on_the_first_separator(name, expected):
    assert Crawler._parse_artist_title(name, []) == expected


def test_parse_artist_title_prefers_the_title_split_over_curated_artists():
    name = "Wino - Forever Gone"
    artists = [{"id": 2, "name": "Saint Vitus"}]
    assert Crawler._parse_artist_title(name, artists) == ("Wino", "Forever Gone")


def test_parse_artist_title_falls_back_to_curated_artists_when_no_separator():
    assert Crawler._parse_artist_title("Vokonis", [{"id": 3, "name": "Vokonis"}]) == (
        "Vokonis", "Vokonis",
    )


def test_parse_artist_title_does_not_split_a_hyphen_glued_to_a_word():
    name = "Godzillionaire-Diminishing Returns"
    assert Crawler._parse_artist_title(name, []) == (None, name)


def test_parse_artist_title_returns_none_when_no_separator_and_no_curated_artists():
    assert Crawler._parse_artist_title("Vokonis", []) == (None, "Vokonis")


def test_parse_artist_title_returns_none_when_the_curated_artist_name_is_blank():
    # A blank name must not produce an empty-string artist -- that would slip
    # past _items()'s `if artist is None` guard.
    for artists in ([{"id": 1, "name": ""}], [{"id": 1, "name": "   "}], [{"id": 1}]):
        assert Crawler._parse_artist_title("Vokonis", artists) == (None, "Vokonis")


def test_parse_artist_title_normalizes_various_artists_to_various():
    # Discogs' entity name is "Various" -- _library_match_fragment does exact
    # LOWER() equality on artist, so "Various Artists" would never match.
    assert Crawler._parse_artist_title("Various Artists - Ripple Sampler", [])[0] == "Various"
    assert Crawler._parse_artist_title("Various Artist - Ripple Sampler", [])[0] == "Various"


def test_parse_artist_title_normalizes_various_on_the_curated_fallback_too():
    # A product with no separator whose curated tag reads "Various Artists" is
    # exactly as unmatchable as a title billing that reads the same way, so
    # normalization cannot live only on the split branch.
    artists = [{"id": 7, "name": "Various Artists"}]
    assert Crawler._parse_artist_title("Ripple Sampler Volume 1", artists) == (
        "Various", "Ripple Sampler Volume 1",
    )


def test_parse_artist_title_unescapes_html_entities():
    name = "Mothership - Don&#x27;t Fear the Reaper LP"
    assert Crawler._parse_artist_title(name, []) == ("Mothership", "Don't Fear the Reaper LP")


# --- product-level vinyl gate ----------------------------------------------

@pytest.mark.parametrize("category_name,kept", [
    # This store splits its media categories by format and size, so the gate
    # is a token regex rather than one exact category string.
    ('12" Vinyl', True),
    ('10" Vinyl', True),
    ('7" Vinyl', True),
    ("Double LP", True),
    ("Test Presses", True),
    ("CDs", False),
    ("Tees", False),
    ("Hoodie", False),
    ("Slipmat", False),
    ("DVD", False),
    ("Books", False),
    ("Merchandise", False),
    # Neither format-bearing nor merch: carries no vinyl signal of its own,
    # so a product filed only here falls to the product-name arm of the gate.
    ("Limited Edition", False),
    ("Rogue Wave Records", False),
])
def test_items_category_arm_of_the_vinyl_gate(category_name, kept):
    product = _product(
        name="Vokonis - Odyssey",  # no format token: isolates the category arm
        categories=[{"id": 9, "name": category_name}],
    )
    assert len(Crawler._items(product)) == (1 if kept else 0)


def test_items_keeps_a_product_with_no_categories_but_a_format_token_in_its_name():
    # asbestosrecords.py's finding: 26% of that store's real vinyl releases
    # carried an empty categories array. The name arm covers them.
    product = _product(name="Mothership - Mothership Vinyl LP", categories=[])
    assert len(Crawler._items(product)) == 1


def test_items_keeps_a_product_with_a_vinyl_category_but_no_format_token_in_its_name():
    # The other half of the same finding: neither signal alone is sufficient.
    product = _product(name="Vokonis - Odyssey", categories=_VINYL_CATEGORY)
    assert len(Crawler._items(product)) == 1


@pytest.mark.parametrize("product_name,kept", [
    # A bare inch mark reads as vinyl here...
    ('Wo Fat - Split 7"', True),
    ('Cortez - Sell the Future 12"', True),
    # ...and as merch here. The mark alone cannot tell the two apart, so it
    # is only trusted when nothing else in the string contradicts it. This
    # store has a Slipmat category, and slipmats are measured in inches.
    ('Ripple Music 12" Slipmat', False),
    ('Ripple Music 7" Storage Book', False),
    # A vinyl word overrides a competing token; the inch mark does not.
    ('Godzillionaire - Diminishing Returns Vinyl and CD variants', True),
])
def test_items_inch_mark_is_only_trusted_when_nothing_contradicts_it(product_name, kept):
    # categories=[] isolates the product-name arm of the gate.
    product = _product(name=product_name, categories=[], artists=[{"id": 1, "name": "Ripple"}])
    assert len(Crawler._items(product)) == (1 if kept else 0)


def test_items_does_not_publish_an_inch_marked_merch_product_via_its_echoing_option():
    # The end-to-end shape of the bug the second _looks_vinyl clause exists to
    # prevent: the inch mark clears the product gate, then the single option
    # echoes the product name and bypasses _is_non_vinyl on the way out.
    name = 'Ripple Music 12" Slipmat'
    product = _product(
        name=name,
        categories=[{"id": 8, "name": "Slipmat"}],
        artists=[{"id": 1, "name": "Ripple Music"}],
        options=[{"id": 70, "name": name, "price": 15.0, "sold_out": False}],
    )
    assert Crawler._items(product) == []


def test_items_drops_an_inch_marked_merch_option():
    # Same rule one layer down: an inch mark must not rescue an option from
    # the blocklist the way a vinyl word does.
    product = _product(options=[
        {"id": 71, "name": '12" Slipmat', "price": 15.0, "sold_out": False},
    ])
    assert Crawler._items(product) == []


@pytest.mark.parametrize("text,kept", [
    # "Vinyl" is a material as well as a format: these are merchandise, and
    # the word describes what they are made of, not that they are records.
    ("Wo Fat - Vinyl Sticker", False),
    ("Ripple Music Vinyl Banner", False),
    ("Ripple Music Vinyl Slipmat", False),
    ("Vinyl Decals", False),
    # Genuine bundles keep their own token -- there is no compound here, the
    # vinyl and the merch item are separate things.
    ("Mothership - Mothership LP + Sticker", True),
    ("Wo Fat - Black Vinyl + Sticker", True),
    # Only the compound is stripped, so an independent format token survives.
    ("Cortez - Vinyl Sticker + LP", True),
    # A mixed-format record still passes: no compound, and the vinyl word is
    # doing format work.
    ("Godzillionaire - Diminishing Returns Limited Vinyl and CD variants", True),
])
def test_items_does_not_treat_vinyl_as_a_format_when_it_names_a_material(text, kept):
    product = _product(name=text, categories=[], artists=[{"id": 1, "name": "Ripple"}])
    assert len(Crawler._items(product)) == (1 if kept else 0)


@pytest.mark.parametrize("text,kept", [
    # Singular and plural of the same noun must behave identically. The two
    # hand-written regexes this replaces disagreed on exactly this: one had
    # `patches`, the other `patch`, and neither had `tee` at all despite the
    # store having a Tees category.
    ("Wo Fat - Vinyl Patch", False),
    ("Wo Fat - Vinyl Patches", False),
    ("Wo Fat - Vinyl Tee", False),
    ("Wo Fat - Vinyl Tees", False),
    ("Wo Fat - Vinyl T-Shirt", False),
    ('Ripple Music 12" Tee', False),
    ('Ripple Music 12" Slipmats', False),
    ('Ripple Music 12" Patches', False),
    # Nouns deliberately kept out of the vocabulary: a "Gatefold Sleeve"
    # variant is a record, so `sleeve` must not read as merch.
    ("Wo Fat - Black Vinyl (Gatefold Sleeve)", True),
])
def test_items_merch_vocabulary_covers_singular_and_plural(text, kept):
    product = _product(name=text, categories=[], artists=[{"id": 1, "name": "Wo Fat"}])
    assert len(Crawler._items(product)) == (1 if kept else 0)


@pytest.mark.parametrize("option_name", [
    "Tee", "Tees", "Slipmats", "Patches", "Hoodies", "Posters", "Beanies",
    "Vinyl Patch", "Vinyl T-Shirt",
])
def test_items_drops_plural_and_clothing_merch_options(option_name):
    product = _product(options=[
        {"id": 90, "name": option_name, "price": 20.0, "sold_out": False},
    ])
    assert Crawler._items(product) == []


def test_items_keeps_an_option_naming_a_sleeve():
    # `sleeve` is excluded from the merch vocabulary on purpose -- dropping a
    # Gatefold Sleeve variant would lose a real record.
    product = _product(options=[
        {"id": 91, "name": "Gatefold Sleeve", "price": 30.0, "sold_out": False},
    ])
    assert len(Crawler._items(product)) == 1


def test_merch_regexes_are_built_from_one_vocabulary():
    # The structural guarantee, not just its current effect: both regexes are
    # derived from _MERCH_NOUNS, so they cannot drift apart the way the
    # hand-written pair did.
    from crawlers import ripplemusic
    for noun in ("tee", "patch", "slipmat", "hoodie"):
        assert noun in ripplemusic._MERCH_NOUN
        assert noun in ripplemusic._NON_VINYL_RE.pattern
        assert noun in ripplemusic._VINYL_MERCH_RE.pattern


def test_items_drops_a_vinyl_material_merch_option():
    # Same rule one layer down: the vinyl-word override must not rescue an
    # option whose "vinyl" is describing a sticker.
    product = _product(options=[
        {"id": 80, "name": "Vinyl Sticker", "price": 5.0, "sold_out": False},
    ])
    assert Crawler._items(product) == []


def test_items_keeps_a_bundle_option_naming_vinyl_and_a_merch_item_separately():
    product = _product(options=[
        {"id": 81, "name": "Black Vinyl + Sticker", "price": 30.0, "sold_out": False},
    ])
    assert len(Crawler._items(product)) == 1


@pytest.mark.parametrize("text,kept", [
    # A test pressing is vinyl by definition, in each form the store might use.
    ("Test Presses", True),
    ("Rare Test Press", True),
    ("Test Pressing", True),
    ("Test Pressings", True),
    # ...but the token needs a terminating boundary, or it swallows any word
    # that merely starts with "press".
    ("Test Pressure", False),
])
def test_items_test_press_token_is_bounded(text, kept):
    product = _product(name=f"Wo Fat - {text}", categories=[],
                       artists=[{"id": 1, "name": "Wo Fat"}])
    assert len(Crawler._items(product)) == (1 if kept else 0)


def test_items_drops_a_product_with_neither_signal():
    product = _product(name="Ripple Music Logo Tee", categories=[{"id": 8, "name": "Tees"}])
    assert Crawler._items(product) == []


# --- per-option non-vinyl filter -------------------------------------------

@pytest.mark.parametrize("option_name,kept", [
    # Colour/edition names carrying no format word at all -- the reason this
    # filter is negative rather than positive like jetglowrecordings.py's.
    ("Solid White Vinyl", True),
    ("Rare Test Press", True),
    ("Worldwide Edition Classic Black Vinyl LP", True),
    ("Limited Edition Coloured Vinyl LP (150 copies)", True),
    ("Clear and Black Marbled", True),
    ("Second Pressing", True),
    # A vinyl token anywhere wins, so bundles survive the broad blocklist.
    ("LP + CD", True),
    ("Black Vinyl + Sticker", True),
    ("Vinyl + T-Shirt Bundle", True),
    # Competing formats and merch on a mixed product.
    ("CD", False),
    ("CD Digipak", False),
    ("Cassette", False),
    ("Digital Download", False),
    ("DVD", False),
    ("T-Shirt", False),
    ("Hoodie", False),
    ("Slipmat", False),
    ("Poster", False),
])
def test_items_option_non_vinyl_filter(option_name, kept):
    product = _product(options=[
        {"id": 30, "name": option_name, "price": 20.0, "sold_out": False},
    ])
    assert len(Crawler._items(product)) == (1 if kept else 0)


def test_items_splits_a_mixed_vinyl_and_cd_product():
    # Live product shape: one release sold in both formats from one product.
    product = _product(
        id=2,
        name="Godzillionaire - Diminishing Returns Limited Vinyl and CD variants",
        url="/product/godzillionaire-diminishing-returns-limited-vinyl-and-cd-variants",
        categories=[{"id": 1, "name": '12" Vinyl'}, {"id": 2, "name": "CDs"}],
        artists=[{"id": 5, "name": "Godzillionaire"}],
        options=[
            {"id": 40, "name": "Black Vinyl", "price": 25.0, "sold_out": False},
            {"id": 41, "name": "Clear Vinyl", "price": 30.0, "sold_out": False},
            {"id": 42, "name": "CD", "price": 12.0, "sold_out": False},
        ],
    )
    assert [i["title"] for i in Crawler._items(product)] == [
        "Diminishing Returns Limited Vinyl and CD variants — Black Vinyl",
        "Diminishing Returns Limited Vinyl and CD variants — Clear Vinyl",
    ]


# --- availability ----------------------------------------------------------

def test_items_skips_sold_out_options():
    product = _product(options=[
        {"id": 50, "name": "Black Vinyl", "price": 25.0, "sold_out": True},
        {"id": 51, "name": "Clear Vinyl", "price": 30.0, "sold_out": False},
    ])
    assert [i["title"] for i in Crawler._items(product)] == [
        "Mothership Vinyl LP — Clear Vinyl",
    ]


def test_items_drops_a_product_whose_status_is_not_active():
    assert Crawler._items(_product(status="sold-out")) == []


def test_items_keeps_a_product_with_no_status_field_at_all():
    # jetglowrecordings.py drops on `status != "active"`, which would empty
    # the whole catalog on a feed that omits the field. An absent status must
    # fall through to the option-level sold_out flag instead.
    product = _product()
    del product["status"]
    assert len(Crawler._items(product)) == 1


def test_items_yields_nothing_when_every_option_is_sold_out():
    product = _product(options=[
        {"id": 52, "name": "Black Vinyl", "price": 25.0, "sold_out": True},
    ])
    assert Crawler._items(product) == []


# --- row shape -------------------------------------------------------------

def test_items_emits_the_full_row_shape_in_usd():
    assert Crawler._items(_product())[0] == {
        "artist": "Mothership",
        "title": "Mothership Vinyl LP — Solid White Vinyl",
        "format": "Vinyl",
        "price": 24.0,
        "currency": "USD",
        "url": "https://ripplemusic.bigcartel.com/product/mothership-mothership-lp-black-vinyl",
        "cover_image_url": "https://assets.bigcartel.com/product_images/1/mothership.jpg",
    }


def test_items_omits_the_suffix_when_the_option_echoes_the_product_name():
    # Big Cartel has no "Default Title" placeholder -- a single-option product
    # repeats its own name, so appending it would double the title.
    name = "Mothership - Mothership Vinyl LP"
    product = _product(options=[{"id": 60, "name": name, "price": 24.0, "sold_out": False}])
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "Mothership Vinyl LP"


def test_items_keeps_an_echoing_option_that_would_trip_the_non_vinyl_filter():
    # The echo check runs first, and has to: this product reached _items via
    # the category arm of the gate, and its single option repeats a name whose
    # only format-ish token is "Poster". Filtering the option on that would
    # drop a release the product-level gate already accepted as a record.
    name = "Wo Fat - The Conjuring + Poster Bundle"
    product = _product(
        name=name,
        categories=_VINYL_CATEGORY,
        options=[{"id": 61, "name": name, "price": 25.0, "sold_out": False}],
    )
    items = Crawler._items(product)
    assert len(items) == 1
    assert items[0]["title"] == "The Conjuring + Poster Bundle"


def test_items_drops_a_product_with_no_artist_source():
    product = _product(name="Ripple Music Vinyl Grab Bag", artists=[])
    assert Crawler._items(product) == []


def test_items_falls_back_to_none_cover_image_when_there_are_no_images():
    assert Crawler._items(_product(images=[]))[0]["cover_image_url"] is None


def test_items_handles_a_non_numeric_price():
    product = _product(options=[
        {"id": 62, "name": "Black Vinyl", "price": None, "sold_out": False},
    ])
    assert Crawler._items(product)[0]["price"] is None


def test_items_unescapes_entities_in_the_option_name():
    product = _product(options=[
        {"id": 63, "name": "Wino&#x27;s Pick Vinyl", "price": 25.0, "sold_out": False},
    ])
    assert Crawler._items(product)[0]["title"] == "Mothership Vinyl LP — Wino's Pick Vinyl"


# --- crawl_catalog ---------------------------------------------------------

_PRODUCTS_URL = "https://ripplemusic.bigcartel.com/products.json"


@respx.mock
async def test_crawl_catalog_follows_pagination_until_a_page_comes_back_empty():
    page1 = [_product(id=1, name="Mothership - Mothership Vinyl LP")]
    page2 = [_product(id=2, name="Wo Fat - The Conjuring LP", url="/product/wo-fat-the-conjuring")]
    route = respx.get(_PRODUCTS_URL).mock(side_effect=[
        httpx.Response(200, json=page1),
        httpx.Response(200, json=page2),
        httpx.Response(200, json=[]),
    ])

    items = [item async for item in Crawler().crawl_catalog()]

    assert [i["artist"] for i in items] == ["Mothership", "Wo Fat"]
    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2", "3"]


@respx.mock
async def test_crawl_catalog_stops_when_the_store_ignores_the_page_param():
    # The behaviour both sibling Big Cartel stores confirmed live: `page=` is
    # accepted and ignored, so page 2 repeats page 1 verbatim. Without the
    # freshness check this loops to the page guard and yields every product 50
    # times over.
    whole_catalog = [_product(id=1), _product(id=2, url="/product/wo-fat")]
    route = respx.get(_PRODUCTS_URL).mock(
        return_value=httpx.Response(200, json=whole_catalog)
    )

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 2
    assert len(route.calls) == 2


@respx.mock
async def test_crawl_catalog_stops_on_a_page_of_products_it_has_already_seen():
    # Partial overlap, not a verbatim repeat: only the unseen row is emitted.
    page1 = [_product(id=1), _product(id=2, url="/product/wo-fat")]
    page2 = [_product(id=2, url="/product/wo-fat"), _product(id=3, url="/product/cortez")]
    respx.get(_PRODUCTS_URL).mock(side_effect=[
        httpx.Response(200, json=page1),
        httpx.Response(200, json=page2),
        httpx.Response(200, json=[]),
    ])

    items = [item async for item in Crawler().crawl_catalog()]

    assert [i["url"] for i in items] == [
        "https://ripplemusic.bigcartel.com/product/mothership-mothership-lp-black-vinyl",
        "https://ripplemusic.bigcartel.com/product/wo-fat",
        "https://ripplemusic.bigcartel.com/product/cortez",
    ]


def _idless(name, slug):
    return {
        "name": name,
        "url": f"/product/{slug}",
        "options": [{"name": "Black Vinyl", "price": 24.0, "sold_out": False}],
        "categories": _VINYL_CATEGORY,
    }


@respx.mock
async def test_crawl_catalog_keys_id_less_products_on_their_url():
    # _key falls back to url. Keying on `id` alone would collapse every id-less
    # row to the same None and stop the walk at page 2, silently dropping the
    # rest of the catalog. Both rows share a name and differ only by url, so
    # the url step is what has to do the work -- a repress sharing a name with
    # the release it replaces is exactly why `name` is not in the chain.
    respx.get(_PRODUCTS_URL).mock(side_effect=[
        httpx.Response(200, json=[_idless("Wo Fat - The Conjuring LP", "wo-fat-2014")]),
        httpx.Response(200, json=[_idless("Wo Fat - The Conjuring LP", "wo-fat-repress")]),
        httpx.Response(200, json=[]),
    ])

    items = [item async for item in Crawler().crawl_catalog()]

    assert [i["url"] for i in items] == [
        "https://ripplemusic.bigcartel.com/product/wo-fat-2014",
        "https://ripplemusic.bigcartel.com/product/wo-fat-repress",
    ]


@respx.mock
async def test_crawl_catalog_stops_on_a_repeated_page_of_id_less_products():
    # The other half: the same id-less rows coming back again must still be
    # recognised as already seen.
    catalog = [_idless("Mothership - Mothership LP", "mothership")]
    route = respx.get(_PRODUCTS_URL).mock(return_value=httpx.Response(200, json=catalog))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 1
    assert len(route.calls) == 2


@respx.mock
async def test_crawl_catalog_stops_at_the_page_guard_and_says_so(monkeypatch, caplog):
    # The runaway backstop, exercised at a patched-down bound. A truncated
    # catalog must be logged, never silent.
    monkeypatch.setattr("crawlers.ripplemusic._MAX_PAGES", 3)
    route = respx.get(_PRODUCTS_URL).mock(side_effect=[
        httpx.Response(200, json=[_product(id=n, url=f"/product/{n}")]) for n in range(1, 5)
    ])

    with caplog.at_level("WARNING"):
        items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 3
    assert len(route.calls) == 3
    assert "may be truncated" in caplog.text


@respx.mock
async def test_crawl_catalog_yields_nothing_when_every_product_is_excluded():
    tee = _product(id=4, name="Ripple Music Logo Tee", categories=[{"id": 8, "name": "Tees"}])
    respx.get(_PRODUCTS_URL).mock(side_effect=[
        httpx.Response(200, json=[tee]),
        httpx.Response(200, json=[]),
    ])

    assert [item async for item in Crawler().crawl_catalog()] == []


@respx.mock
async def test_crawl_catalog_reports_each_page_to_the_progress_reporter():
    # crawl_progress.report_page is how a stock sync renders per-page
    # progress; it is a silent no-op outside one, so nothing else would catch
    # it going missing. Reported count is emitted rows, matching the sibling
    # Big Cartel crawlers (shopify_catalog.iter_products reports products).
    # Page 1 carries three products but only two records: the count reported
    # is emitted rows, not products fetched.
    tee = _product(id=9, name="Ripple Music Logo Tee", categories=[{"id": 8, "name": "Tees"}])
    respx.get(_PRODUCTS_URL).mock(side_effect=[
        httpx.Response(200, json=[_product(id=1), tee, _product(id=2, url="/product/wo-fat")]),
        httpx.Response(200, json=[_product(id=3, url="/product/cortez")]),
        httpx.Response(200, json=[]),
    ])
    reported = []

    async def reporter(page, count):
        reported.append((page, count))

    token = crawl_progress.set_page_reporter(reporter)
    try:
        [item async for item in Crawler().crawl_catalog()]
    finally:
        crawl_progress.reset_page_reporter(token)

    assert reported == [(1, 2), (2, 1)]


@respx.mock
async def test_crawl_catalog_raises_on_an_http_error():
    # "[] means the site answered and has nothing; any failure must raise" --
    # a crawler that swallows errors never cools its site off.
    respx.get(_PRODUCTS_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in Crawler().crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Ripple Music"
    assert Crawler.base_url == "https://ripplemusic.bigcartel.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
