import httpx
import respx
import pytest
from crawlers.spkr import Crawler

_PRODUCTS_URL = "https://spkr.store/collections/vinyl/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-07, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape -- vendor is the literal `details`,
# the artist is in the title as `Artist - Album (descriptor)`, and the sole
# variant is Shopify's placeholder.
_DARVAZA_PRODUCT = {
    "title": "Darvaza - We Are Him (Vinyl Gatefold LP)",
    "vendor": "details",
    "handle": "darvaza-we-are-him-vinyl",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/884388897250_001.jpg"}],
    "variants": [
        {"id": 54784101417283, "title": "Default Title", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/884388897250_001.jpg"}},
    ],
}

# Captured: the same album's CD, typed `CD`. Outside the vinyl collection on
# the live store; here it stands in for a CD that drifted into it.
_DARVAZA_CD_PRODUCT = {
    "title": "Darvaza - We Are Him (CD Digipak)",
    "vendor": "details",
    "handle": "darvaza-we-are-him-cd",
    "product_type": "CD",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/884388897243.jpg"}],
    "variants": [
        {"id": 54467294167363, "title": "Default Title", "price": "14.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/884388897243.jpg"}},
    ],
}

# Captured: a pre-order (listed in the store's `pre-order` collection, with
# nothing on the product itself saying so, and every pressing available),
# whose variants are catalogue numbers under a `SKU` option, each with its
# own image.
_MONARK_PRODUCT = {
    "title": "Blodtår - Monark (Vinyl Gatefold LP)",
    "vendor": "details",
    "handle": "blodtar-monark-vinyl",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/7350142983819.jpg"}],
    "variants": [
        {"id": 54676474855747, "title": "NVP236LP", "price": "22.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/7350142983819.jpg"}},
        {"id": 54676474888515, "title": "NVP236LPS", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/7350142984359.png"}},
    ],
}

# Captured: a `Format` + `Colour` product whose title carries a bare
# `(Vinyl)` and whose variants name the format and colour together; no
# variant images.
_VARE_PRODUCT = {
    "title": "Tenhi - Väre (Vinyl)",
    "vendor": "details",
    "handle": "tenhi-vare-vinyl",
    "product_type": "Vinyl",
    "tags": ["Prophecy Productions"],
    "images": [{"src": "https://cdn.shopify.com/884388705319.jpg"}],
    "variants": [
        {"id": 49270617309507, "title": "Vinyl LP / black", "price": "21.99", "available": True, "featured_image": None},
        {"id": 49270617342275, "title": "Vinyl 2-LP Gatefold / Black", "price": "38.98", "available": True, "featured_image": None},
        {"id": 49270617375043, "title": "Vinyl 2-LP Gatefold / Clear", "price": "42.99", "available": True, "featured_image": None},
    ],
}

# Captured: the one product whose trailing parenthesis is a colour rather
# than a format, with the format in the variants instead.
_SCHNEE_PRODUCT = {
    "title": "Paysage d'Hiver - Schnee (Black)",
    "vendor": "details",
    "handle": "paysage-dhiver-schnee-vinyl",
    "product_type": "Vinyl",
    "tags": ["Kunsthall"],
    "images": [{"src": "https://cdn.shopify.com/884388871823_001.jpg"}],
    "variants": [
        {"id": 49271920722243, "title": "Vinyl 2-LP Gatefold", "price": "32.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/884388871823_001.jpg"}},
        {"id": 49271920755011, "title": "Vinyl 2-LP", "price": "49.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/884388871830_001.jpg"}},
    ],
}

# Captured: an HTML entity typed into a variant title, beside a lower-case
# colour; the album carries a parenthesised subtitle before the descriptor.
_DUSK_PRODUCT = {
    "title": "Cradle Of Filth - Dusk And Her Embrace (The Original Sin) (Vinyl 2-LP)",
    "vendor": "details",
    "handle": "cradle-of-filth-dusk-and-her-embrace-the-original-sin-vinyl-2-lp",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/4068651002190.jpg"}],
    "variants": [
        {"id": 53766850642243, "title": "&hellip; (DSR308LPgold)", "price": "34.99", "available": True, "featured_image": None},
        {"id": 54099828048195, "title": "transparent cream/black marble", "price": "35.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/4068651002190.jpg"}},
    ],
}

# Captured: a sold-out colour beside an in-stock black pressing.
_NACHTHYMNEN_PRODUCT = {
    "title": "Abigor - Nachthymnen (From The Twilight Kingdom) (Vinyl LP)",
    "vendor": "details",
    "handle": "abigor-nachthymnen-from-the-twilight-kingdom-vinyl-lp",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/4068651002824.jpg"}],
    "variants": [
        {"id": 54077537648963, "title": "Galaxy Purple/Blue", "price": "36.99", "available": False, "featured_image": None},
        {"id": 54077537681731, "title": "Black", "price": "33.99", "available": True, "featured_image": None},
    ],
}

# Captured: an album with a ` - ` of its own.
_SURTURIAN_PRODUCT = {
    "title": "Surturian - II - Hessian Spears (Vinyl LP)",
    "vendor": "details",
    "handle": "surturian-ii-hessian-spears-vinyl",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/884388899124.jpg"}],
    "variants": [
        {"id": 54781831840067, "title": "Default Title", "price": "21.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/884388899124.jpg"}},
    ],
}

# Captured: a double space inside the title, and the colour in the
# descriptor of a single-variant product.
_SUBMARINE_PRODUCT = {
    "title": "Rollerball - Submarine:  Beneath The Desert Floor Chapter 9 (Vinyl LP - Marble)",
    "vendor": "details",
    "handle": "rollerball-submarine-beneath-the-desert-floor-chapter-9-vinyl-lp-marble",
    "product_type": "Vinyl",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/850074887140.jpg"}],
    "variants": [
        {"id": 54203500069187, "title": "Default Title", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/850074887140.jpg"}},
    ],
}

# Captured: a Cyrillic album title and handle.
_WORMWOOD_PRODUCT = {
    "title": "Dymna Lotva - Палын (Wormwood) (Vinyl LP - Oxblood red)",
    "vendor": "details",
    "handle": "dymna-lotva-палын-wormwood-vinyl-lp-oxblood-red",
    "product_type": "Vinyl",
    "tags": ["Prophecy Productions"],
    "images": [{"src": "https://cdn.shopify.com/884388887640.jpg"}],
    "variants": [
        {"id": 52494745338179, "title": "Default Title", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Captured: a Various Artists compilation, sold out.
_WALL_PRODUCT = {
    "title": "Various Artists - The Wall (Redux) (Vinyl 2-LP Gatefold - Solid Blue)",
    "vendor": "details",
    "handle": "various-artists-the-wall-redux-vinyl-2-lp-gatefold",
    "product_type": "Vinyl",
    "tags": ["Magnetic Eye Records"],
    "images": [{"src": "https://cdn.shopify.com/850797007382.jpg"}],
    "variants": [
        {"id": 49271821173059, "title": "Default Title", "price": "29.99", "available": False, "featured_image": None},
    ],
}

# Captured: the one product with no images at all; every pressing sold out.
_BLACK_ELECTRIC_PRODUCT = {
    "title": "Black Electric - Black Electric (Vinyl LP)",
    "vendor": "details",
    "handle": "black-electric-black-electric-vinyl-lp",
    "product_type": "Vinyl",
    "tags": [],
    "images": [],
    "variants": [
        {"id": 53387258659139, "title": "Purple/Blue Splatter", "price": "29.99", "available": False, "featured_image": None},
        {"id": 53387258691907, "title": "Black", "price": "23.99", "available": False, "featured_image": None},
        {"id": 53387258724675, "title": "Clear/Gold", "price": "26.99", "available": False, "featured_image": None},
    ],
}

# Captured: a boxset that bundles a record, typed `Boxset / Bundle`. Outside
# the vinyl collection on the live store.
_BOXSET_PRODUCT = {
    "title": "Nocternity - Onyx LP Boxset (Vinyl Box)",
    "vendor": "details",
    "handle": "nocternity-onyx-lp-boxset-boxset-bundle",
    "product_type": "Boxset / Bundle",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/884388897670.jpg"}],
    "variants": [
        {"id": 54460551692611, "title": "Default Title", "price": "47.81", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/884388897670.jpg"}},
    ],
}

# Captured: a shirt, sized variants. Outside the vinyl collection on the
# live store.
_TEE_PRODUCT = {
    "title": "Panopticon - Kentucky (T-Shirt)",
    "vendor": "details",
    "handle": "panopticon-kentucky-t-shirt",
    "product_type": "T-Shirt",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/7350142981143.jpg"}],
    "variants": [
        {"id": 54781830594883, "title": "M", "price": "20.99", "available": True, "featured_image": None},
        {"id": 54781830627651, "title": "XL", "price": "20.99", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_pages(*products, empty_page=2):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response(list(products)))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(empty_page)}).mock(
        return_value=_page_response([]))


def _one_pressing(product, index=0, **overrides):
    # A captured product reduced to one of its variants, so a test can alter
    # that variant without carrying the siblings along.
    return {**product, "variants": [{**product["variants"][index], **overrides}]}


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_DARVAZA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Darvaza",
        "title": "We Are Him (Vinyl Gatefold LP)",
        "format": "Vinyl",
        "price": 24.99,
        "currency": "EUR",
        "url": "https://spkr.store/products/darvaza-we-are-him-vinyl",
        "cover_image_url": "https://cdn.shopify.com/884388897250_001.jpg",
    }]


@respx.mock
async def test_every_pressing_of_a_product_yields_its_own_row(crawler):
    _mock_pages(_VARE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["title"], i["price"]) for i in items] == [
        ("Väre (Vinyl) — Vinyl LP / black", 21.99),
        ("Väre (Vinyl) — Vinyl 2-LP Gatefold / Black", 38.98),
        ("Väre (Vinyl) — Vinyl 2-LP Gatefold / Clear", 42.99),
    ]


@pytest.mark.parametrize("product_type", ["Vinyl", "vinyl", "VINYL", " Vinyl ", "Vinyl - 2LP"])
def test_vinyl_type_gate_admits(product_type):
    assert Crawler._items({**_DARVAZA_PRODUCT, "product_type": product_type})


@pytest.mark.parametrize("product_type", ["CD", "MC", "Boxset / Bundle", "Artbook", "T-Shirt", "Vinylic", "", None])
def test_vinyl_type_gate_rejects(product_type):
    assert Crawler._items({**_DARVAZA_PRODUCT, "product_type": product_type}) == []


@pytest.mark.parametrize("product", [_DARVAZA_CD_PRODUCT, _BOXSET_PRODUCT, _TEE_PRODUCT])
def test_non_vinyl_products_yield_nothing(product):
    # The boxset's title says "Vinyl Box"; the type says otherwise, and the
    # type is the gate.
    assert Crawler._items(product) == []


@pytest.mark.parametrize("title,expected", [
    ("Darvaza - We Are Him (Vinyl Gatefold LP)", ("Darvaza", "We Are Him (Vinyl Gatefold LP)")),
    # The album's own " - " stays on the album: the first separator splits.
    ("Surturian - II - Hessian Spears (Vinyl LP)", ("Surturian", "II - Hessian Spears (Vinyl LP)")),
    ("Nachtmystium - Addicts - Black Meddle Pt. II (Vinyl 2-LP Gatefold)",
     ("Nachtmystium", "Addicts - Black Meddle Pt. II (Vinyl 2-LP Gatefold)")),
    # A subtitle in parentheses before the descriptor is kept, as is the
    # descriptor itself.
    ("Abigor - Nachthymnen (From The Twilight Kingdom) (Vinyl LP)",
     ("Abigor", "Nachthymnen (From The Twilight Kingdom) (Vinyl LP)")),
    ("Paysage d'Hiver - Schnee (Black)", ("Paysage d'Hiver", "Schnee (Black)")),
    ("Dymna Lotva - Палын (Wormwood) (Vinyl LP - Oxblood red)", ("Dymna Lotva", "Палын (Wormwood) (Vinyl LP - Oxblood red)")),
    # A split credit is kept as written; a hyphenated word is not a separator.
    ("Draugveil & Selvnatt - Blades & Roses (Vinyl LP - White)", ("Draugveil & Selvnatt", "Blades & Roses (Vinyl LP - White)")),
    ("E-L-R - Atropa (Vinyl LP)", ("E-L-R", "Atropa (Vinyl LP)")),
    # Whitespace is collapsed, inside the title and around it.
    ("Rollerball - Submarine:  Beneath The Desert Floor Chapter 9 (Vinyl LP - Marble)",
     ("Rollerball", "Submarine: Beneath The Desert Floor Chapter 9 (Vinyl LP - Marble)")),
    ("  Darvaza  -  We Are Him  ", ("Darvaza", "We Are Him")),
    # Various Artists is written the way Discogs writes it.
    ("Various Artists - The Wall (Redux) (Vinyl 2-LP Gatefold - Solid Blue)", ("Various", "The Wall (Redux) (Vinyl 2-LP Gatefold - Solid Blue)")),
    ("VARIOUS ARTISTS - Whom the Moon a Nightsong sings (Vinyl 2-LP Gatefold)", ("Various", "Whom the Moon a Nightsong sings (Vinyl 2-LP Gatefold)")),
    ("Various - Döminance And Submissön (Vinyl 2-LP Gatefold - Black)", ("Various", "Döminance And Submissön (Vinyl 2-LP Gatefold - Black)")),
    # Not of the form: no separator, a separator with nothing on one side,
    # a hyphen that is not spaced.
    ("Lantlôs (Vinyl LP)", ("", "")),
    ("Darvaza -", ("", "")),
    ("- We Are Him", ("", "")),
    ("Darvaza-We Are Him", ("", "")),
    ("", ("", "")),
    (None, ("", "")),
])
def test_artist_and_title_come_from_the_dashed_title(title, expected):
    assert Crawler._artist_title(title) == expected


@respx.mock
async def test_a_title_without_the_separator_yields_nothing(crawler):
    # Altered: the title lost its artist. A row credited to nothing can never
    # match a Discogs release, so it is skipped rather than emitted.
    _mock_pages({**_DARVAZA_PRODUCT, "title": "We Are Him (Vinyl Gatefold LP)"}, _SURTURIAN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Surturian"]


@pytest.mark.parametrize("title", [
    "Black", "Splatter", "Clear/Black Marble", "transparent cream/black marble",
    "NVP236LPS", "DSR357LP-blk", "CW55", "&hellip;", "…",
    "Vinyl Picture LP | Zoetrope", "Vinyl 2-LP Gatefold / Clear", "Vinyl LP",
    "Black 12\" Vinyl + DVD", "12\" + DVD", "2xLP + CD", "Bio-Vinyl Black",
])
def test_pressing_gate_admits_records_and_colour_names(title):
    assert Crawler._is_pressing(title)


@pytest.mark.parametrize("title", [
    "CD", "CD Digipak", "2xCD", "2-CD", "MC", "Cassette", "Tape", "DVD", "Digital",
    "T-Shirt", "Black T-Shirt", "12\" x 12\" Poster",
])
def test_pressing_gate_rejects_other_media_and_merch(title):
    assert not Crawler._is_pressing(title)


@respx.mock
async def test_a_variant_naming_another_medium_is_skipped(crawler):
    # Invented: a `Format` product that grew a CD beside its LPs. The type
    # says vinyl, so the negative variant gate is what keeps the CD out.
    product = {**_SCHNEE_PRODUCT, "variants": _SCHNEE_PRODUCT["variants"] + [
        {"id": 3, "title": "CD Digipak", "price": "14.99", "available": True, "featured_image": None}]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Schnee (Black) — Vinyl 2-LP Gatefold", "Schnee (Black) — Vinyl 2-LP"]


@respx.mock
async def test_placeholder_variant_carries_the_title_alone(crawler):
    _mock_pages(_SUBMARINE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Submarine: Beneath The Desert Floor Chapter 9 (Vinyl LP - Marble)"]


@respx.mock
async def test_placeholder_variant_matching_is_case_insensitive(crawler):
    _mock_pages(_one_pressing(_DARVAZA_PRODUCT, title="default title"))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["We Are Him (Vinyl Gatefold LP)"]


@respx.mock
async def test_placeholder_on_a_multi_variant_product_is_skipped(crawler):
    # Altered: Shopify never issues the placeholder beside a real variant, so
    # here it is malformed data. Admitted with no descriptor it would share
    # the bare title, and so the item_key, with every sibling built the same
    # way; admitted with one it would put "Default Title" on a row.
    product = {**_MONARK_PRODUCT, "variants": _MONARK_PRODUCT["variants"] + [
        {"id": 3, "title": "Default Title", "price": "22.99", "available": True, "featured_image": None}]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Monark (Vinyl Gatefold LP) — NVP236LP", "Monark (Vinyl Gatefold LP) — NVP236LPS"]


@respx.mock
async def test_blank_variant_title_is_skipped(crawler):
    # Altered: a blank title is never a pressing, on either kind of product.
    _mock_pages(_one_pressing(_DARVAZA_PRODUCT, title="  "),
                {**_MONARK_PRODUCT, "variants": [{**_MONARK_PRODUCT["variants"][0], "title": ""}, _MONARK_PRODUCT["variants"][1]]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Monark (Vinyl Gatefold LP) — NVP236LPS"]


@respx.mock
async def test_variant_title_is_appended_on_every_row_that_names_one(crawler):
    # A product reduced to one named pressing still carries it: keyed on the
    # variant count, the row would re-title the day a sibling was delisted.
    _mock_pages(_one_pressing(_NACHTHYMNEN_PRODUCT, index=1))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Nachthymnen (From The Twilight Kingdom) (Vinyl LP) — Black"]


@respx.mock
async def test_variant_title_entities_are_unescaped(crawler):
    _mock_pages(_DUSK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Dusk And Her Embrace (The Original Sin) (Vinyl 2-LP) — … (DSR308LPgold)",
        "Dusk And Her Embrace (The Original Sin) (Vinyl 2-LP) — transparent cream/black marble",
    ]


@respx.mock
async def test_variant_title_whitespace_is_collapsed(crawler):
    _mock_pages(_one_pressing(_NACHTHYMNEN_PRODUCT, index=1, title="  Black \n Bio-Vinyl  "))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Nachthymnen (From The Twilight Kingdom) (Vinyl LP) — Black Bio-Vinyl"]


@respx.mock
async def test_various_artists_is_credited_as_various(crawler):
    _mock_pages(_one_pressing(_WALL_PRODUCT, available=True))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Various"]


@respx.mock
async def test_a_preorder_carries_no_marker_and_no_availability_bypass(crawler):
    # The pre-order shelf is not read: a title marker would re-key the row
    # when the record ships. And a pre-order pressing reporting False is
    # gone allocation, not not-yet-released.
    _mock_pages(_MONARK_PRODUCT, _one_pressing(_MONARK_PRODUCT, available=False))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Monark (Vinyl Gatefold LP) — NVP236LP",
        "Monark (Vinyl Gatefold LP) — NVP236LPS",
    ]
    assert all("pre-order" not in str(call.request.url) for call in respx.calls)


@respx.mock
async def test_sold_out_pressing_is_skipped_beside_its_in_stock_sibling(crawler):
    _mock_pages(_NACHTHYMNEN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Nachthymnen (From The Twilight Kingdom) (Vinyl LP) — Black"]


@respx.mock
async def test_sold_out_product_is_skipped(crawler):
    _mock_pages(_WALL_PRODUCT, _BLACK_ELECTRIC_PRODUCT, _DARVAZA_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Darvaza"]


@respx.mock
async def test_junk_variant_entries_are_ignored(crawler):
    # Dropped before anything reads them, so the placeholder rule sees the
    # mapping entries only: the placeholder is still the sole variant and
    # still carries the title alone.
    _mock_pages({**_DARVAZA_PRODUCT, "variants": [None, "junk", 3] + _DARVAZA_PRODUCT["variants"]})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["We Are Him (Vinyl Gatefold LP)"]


@respx.mock
async def test_product_missing_its_handle_is_skipped(crawler):
    _mock_pages({**_DARVAZA_PRODUCT, "handle": ""}, {**_SURTURIAN_PRODUCT, "handle": None}, _MONARK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["artist"] for i in items} == {"Blodtår"}


@respx.mock
@pytest.mark.parametrize("mutate", [
    lambda p: {**p, "handle": ""},
    lambda p: {**p, "handle": None},
    lambda p: {k: v for k, v in p.items() if k != "handle"},
])
async def test_catalog_without_handles_raises(crawler, mutate):
    _mock_pages(mutate(_DARVAZA_PRODUCT), mutate(_MONARK_PRODUCT))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_less_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages({**_DARVAZA_PRODUCT, "handle": ""}, _MONARK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 2


@respx.mock
async def test_a_sold_out_product_missing_its_handle_still_raises(crawler):
    # The identity tally is taken before the availability filter: a
    # handle-less product is drift whether or not it is in stock.
    _mock_pages(_one_pressing({**_DARVAZA_PRODUCT, "handle": ""}, available=False))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_on_a_non_record_does_not_satisfy_the_identity_guard(crawler):
    _mock_pages(_TEE_PRODUCT, {**_DARVAZA_PRODUCT, "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "-5", "nan", "inf", True, False, 0, -1.5, float("nan")])
def test_unusable_price_yields_none(raw):
    assert Crawler._price({"price": raw}) is None


@pytest.mark.parametrize("raw,expected", [("24.99", 24.99), ("130.00", 130.0), (5.99, 5.99), (30, 30.0)])
def test_usable_price_is_parsed(raw, expected):
    assert Crawler._price({"price": raw}) == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    variant = {k: v for k, v in _DARVAZA_PRODUCT["variants"][0].items() if k != "price"}
    _mock_pages({**_DARVAZA_PRODUCT, "variants": [variant]}, _MONARK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 22.99, 24.99]


@respx.mock
@pytest.mark.parametrize("mutate", [
    lambda v: {**v, "price": None},
    lambda v: {**v, "price": "free"},
    lambda v: {k: x for k, x in v.items() if k != "price"},
])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    _mock_pages({**_DARVAZA_PRODUCT, "variants": [mutate(v) for v in _DARVAZA_PRODUCT["variants"]]},
                {**_MONARK_PRODUCT, "variants": [mutate(v) for v in _MONARK_PRODUCT["variants"]]})
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    _mock_pages(_one_pressing(_DARVAZA_PRODUCT, price=None), _SURTURIAN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 21.99]


@respx.mock
async def test_an_empty_catalog_does_not_trip_the_price_guard(crawler):
    _mock_pages(_one_pressing(_DARVAZA_PRODUCT, available=False, price=None))
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    _mock_pages(_MONARK_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["cover_image_url"] for i in items] == [
        "https://cdn.shopify.com/7350142983819.jpg", "https://cdn.shopify.com/7350142984359.png"]


@respx.mock
async def test_cover_falls_back_to_product_image(crawler):
    _mock_pages(_VARE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["cover_image_url"] for i in items} == {"https://cdn.shopify.com/884388705319.jpg"}


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    _mock_pages(_one_pressing(_BLACK_ELECTRIC_PRODUCT, index=1, available=True))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["cover_image_url"] for i in items] == [None]


@respx.mock
async def test_url_is_built_from_the_handle_as_written(crawler):
    _mock_pages(_WORMWOOD_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["url"] for i in items] == ["https://spkr.store/products/dymna-lotva-палын-wormwood-vinyl-lp-oxblood-red"]


@respx.mock
async def test_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_without_the_vinyl_type_raises(crawler):
    # Altered: the shelf answers with CDs and merch only. Completing empty
    # would have replace_stock_items() delete the previous snapshot.
    _mock_pages(_DARVAZA_CD_PRODUCT, _TEE_PRODUCT, _BOXSET_PRODUCT)
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_whose_records_have_no_dashed_title_raises(crawler):
    # Altered: the records renamed to a quoted convention. The artist is
    # read out of the ` - `, so every record is skipped while the type tally
    # stays non-zero -- artist-source drift.
    _mock_pages({**_DARVAZA_PRODUCT, "title": "Darvaza 'We Are Him'"}, {**_MONARK_PRODUCT, "title": "Monark"})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_dashed_title_on_merch_does_not_satisfy_the_artist_guard(crawler):
    # The tee's title parses; the only record's does not. Counting parses
    # across every product would let the tee vouch for records that have
    # lost their artist source.
    _mock_pages(_TEE_PRODUCT, {**_DARVAZA_PRODUCT, "title": "We Are Him"})
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    lambda v: {**v, "available": "true"},
    lambda v: {**v, "available": 1},
    lambda v: {**v, "available": None},
    lambda v: {k: x for k, x in v.items() if k != "available"},
])
async def test_catalog_without_a_readable_availability_flag_raises(crawler, mutate):
    _mock_pages({**_DARVAZA_PRODUCT, "variants": [mutate(v) for v in _DARVAZA_PRODUCT["variants"]]},
                {**_MONARK_PRODUCT, "variants": [mutate(v) for v in _MONARK_PRODUCT["variants"]]})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("raw", ["true", "false", 1, "yes"])
async def test_a_non_boolean_flag_is_never_emitted_as_in_stock(crawler, raw):
    _mock_pages(_one_pressing(_DARVAZA_PRODUCT, available=raw), _SURTURIAN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Surturian"]


@respx.mock
async def test_a_malformed_variant_does_not_hide_its_healthy_sibling(crawler):
    product = {**_MONARK_PRODUCT, "variants": [
        {**_MONARK_PRODUCT["variants"][0], "available": "true"}, _MONARK_PRODUCT["variants"][1]]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Monark (Vinyl Gatefold LP) — NVP236LPS"]


@respx.mock
async def test_one_readable_sold_out_product_cannot_vouch_for_an_unreadable_catalog(crawler):
    _mock_pages(_WALL_PRODUCT, _one_pressing(_DARVAZA_PRODUCT, available="false"))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_sold_out_pressing_does_not_vouch_for_a_malformed_sibling(crawler):
    # every(), not any(): the readable False on one pressing must not vouch
    # for the string "false" on the other.
    product = {**_MONARK_PRODUCT, "variants": [
        {**_MONARK_PRODUCT["variants"][0], "available": False},
        {**_MONARK_PRODUCT["variants"][1], "available": "false"}]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_cd_variant_with_a_malformed_flag_does_not_make_the_record_unreadable(crawler):
    # Invented: readability is judged over the admitted pressings only.
    product = {**_SCHNEE_PRODUCT, "variants": [
        {**_SCHNEE_PRODUCT["variants"][0], "available": False},
        {**_SCHNEE_PRODUCT["variants"][1], "available": False},
        {"id": 3, "title": "CD Digipak", "price": "14.99", "available": "true", "featured_image": None}]}
    _mock_pages(product)
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_readable_cd_variant_does_not_vouch_for_an_unreadable_record(crawler):
    product = {**_SCHNEE_PRODUCT, "variants": [
        {**_SCHNEE_PRODUCT["variants"][0], "available": "false"},
        {"id": 3, "title": "CD Digipak", "price": "14.99", "available": False, "featured_image": None}]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_flag_on_a_non_record_does_not_satisfy_the_stock_guard(crawler):
    _mock_pages(_TEE_PRODUCT, _one_pressing(_DARVAZA_PRODUCT, available="false"))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_two_products_cannot_each_satisfy_half_of_the_yield_guards(crawler):
    # Nested tallies: a vinyl product with no artist and a readable, sold-out
    # tee between them satisfy every tally taken independently, and neither
    # could ever yield.
    _mock_pages({**_DARVAZA_PRODUCT, "title": "We Are Him"}, _one_pressing(_TEE_PRODUCT, available=False))
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_unreadable_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages(_one_pressing(_DARVAZA_PRODUCT, available="true"), _SURTURIAN_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Surturian"]


@respx.mock
async def test_a_fully_readable_sold_out_product_completes_empty(crawler):
    _mock_pages(_BLACK_ELECTRIC_PRODUCT)
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_a_cleanly_sold_out_catalog_completes_empty(crawler):
    _mock_pages(_WALL_PRODUCT, _BLACK_ELECTRIC_PRODUCT, _one_pressing(_DARVAZA_PRODUCT, available=False))
    assert [item async for item in crawler.crawl_catalog()] == []


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    _mock_pages(_DARVAZA_PRODUCT, empty_page=3)
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_SURTURIAN_PRODUCT]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Darvaza", "Surturian"]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "SPKR.store"
    assert Crawler.base_url == "https://spkr.store"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
    assert Crawler.genre_summary
