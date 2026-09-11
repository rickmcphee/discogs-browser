import httpx
import pytest
import respx

from crawlers.joyfulnoiserecordings import Crawler

_PRODUCTS_URL = "https://www.joyfulnoiserecordings.com/collections/all/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-09, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape. `vendor` is the artist, `title` is the
# album, and the medium lives entirely in the variant titles under the store's
# `Format` option -- product_type says "Albums" for the vinyl, the CD and the
# download alike. The bare `VIP` variant is the members' slot; it names no
# format at all, which is why the gate is positive.
_TRAUMANAUT_PRODUCT = {
    "title": "Traumanaut",
    "vendor": "Goblin Cock",
    "handle": "traumanaut",
    "product_type": "Albums",
    "tags": ["Albums", "Goblin Cock", "Releases", "Rob Crow", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/JNR525CoverArtFinal.jpg"}],
    "variants": [
        {"title": "VIP", "price": "27.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/JNR525_VIP_Mockup.jpg"}},
        {"title": "100% Recycled Eco Purple-Pink Vinyl + Digital (Download in AIFF/MP3/WAV)",
         "price": "24.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/JNR525_LP-C1_Mockup.jpg"}},
        {"title": "CD + Digital (Download in AIFF/MP3/WAV)", "price": "12.00",
         "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/JNR525_CD_Mockup.jpg"}},
        {"title": "Digital (Download available in WAV / AIFF / MP3)", "price": "9.00",
         "available": True, "featured_image": None},
    ],
}

# Captured: a record the store's own `vinyl` shelf does not carry -- it holds
# exactly the `Vinyl`-tagged products, and this one is tagged only "InPress".
# Walking that shelf instead of `all` would drop it and every other record
# like it. Also carries the store's two sold-out spellings.
_SURFER_BLOOD_PRODUCT = {
    "title": "1000 Palms",
    "vendor": "Surfer Blood",
    "handle": "1000-palms",
    "product_type": "Albums",
    "tags": ["InPress"],
    "images": [{"src": "https://cdn.shopify.com/albums-1000-palms-1.jpeg"}],
    "variants": [
        {"title": "Sky Blue Vinyl + Digital", "price": "22.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/albums-1000-palms-2.jpeg"}},
        {"title": "Black Vinyl + Digital", "price": "20.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/albums-1000-palms-3.jpg"}},
        {"title": "CD + Digital", "price": "12.00", "available": True,
         "featured_image": None},
        {"title": "Digital (immediate download in both MP3 and WAV.)", "price": "9.00",
         "available": True, "featured_image": None},
        {"title": '[SOLD OUT] VIP-Exclusive Vinyl (limited to 500 hand-numbered copies '
                  'on green and clear "starburst" vinyl, includes exclusive flexi-disc '
                  'bonus song.)',
         "price": "22.00", "available": False, "featured_image": None},
        {"title": "[SOLD OUT] Cassette + Digital (ltd. to 250 copies on blue tapes)",
         "price": "8.00", "available": False, "featured_image": None},
    ],
}

# Captured: the multi-release lot the store shelves as a variant of each album
# it contains. Its `+ Digital` is cut from the head before anything reads it,
# leaving a bare `Triptych Box Set` that names no medium at all -- so the blurb
# answers, and the blurb names a signed poster, a physical good, which vetoes
# before the "Three 2xLPs" beside it can admit the variant. That is what keeps
# a $270 three-album box off three separate records' rows.
_SLEEPYTIME_PRODUCT = {
    "title": "In Glorious Times",
    "vendor": "Sleepytime Gorilla Museum",
    "handle": "in-glorious-times",
    "product_type": "Albums",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/JNR533CoverArtFinal.jpg"}],
    "variants": [
        {"title": "2xLP on Citrus Colored Vinyl (Includes download in AIFF/MP3/WAV)",
         "price": "35.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/JNR533LP-C1Mockup.jpg"}},
        {"title": 'Triptych Box Set + Digital (Three 2xLPs "Grand Opening and Closing", '
                  '"Of Natural History", "In Glorious Times" plus booklets and signed '
                  "poster in tri-fold wooden box w/ etched and die-cut art. "
                  "Hand-numbered out of 777. Instant download in AIFF/MP3/WAV)",
         "price": "270.00", "available": True, "featured_image": None},
        {"title": "Digital (Download in AIFF/MP3/WAV)", "price": "9.00",
         "available": True, "featured_image": None},
    ],
}

# Captured, then altered to `available: True` (live it is sold out): a White
# Label Series record. `vendor` names the series rather than the artist, and
# the artist is only in the product title. The album closes on a curly
# apostrophe that also appears INSIDE it.
_WLS_PRODUCT = {
    "title": "Ambulances 'Frankie Bacon’s Blue, Blue Heart'",
    "vendor": "White Label Series",
    "handle": "ambulances-frankie-bacon-s-blue-blue-heart",
    "product_type": "Subscription",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/JNR350-cover.jpg"}],
    "variants": [
        {"title": "Limited Edition Vinyl + Digital (Limited to 500 copies.)",
         "price": "18.00", "available": True, "featured_image": None},
    ],
}

# Captured, then altered to `available: True` (live it is sold out): the
# store's hand-made lathe-cut singles are titled with the SONG, so the whole
# format signal sits inside the parenthetical. A head-only gate loses these.
_LATHE_PRODUCT = {
    "title": "Can't Let Go, Juno \"Lunch Break Live\" Lathe-Cut (Private Stash)",
    "vendor": "Kishi Bashi",
    "handle": "cant-let-go-juno-lunch-break-live-lathe-cut-private-stash",
    "product_type": "Singles",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/JNR-lathe.jpg"}],
    "variants": [
        {"title": 'Can\'t Let Go Juno (Hand-made lathe-cut 7" limited to 100 copies)',
         "price": "15.00", "available": True, "featured_image": None},
    ],
}

# Captured: an internal placeholder -- a duplicated product (note the handle)
# whose vendor and product_type are both the string "hidden", priced at zero.
# It is a real published product, and the price gate is the only thing between
# it and a Store row crediting an artist called "hidden".
_HIDDEN_PLACEHOLDER_PRODUCT = {
    "title": 'Son Lux "Whispering"',
    "vendor": "hidden",
    "handle": "copy-of-chad-vangaalen-mutant-mussel",
    "product_type": "hidden",
    "tags": [],
    "images": [{"src": "https://cdn.shopify.com/JNR208-cover.jpg"}],
    "variants": [
        {"title": 'Limited Edition 7" (Screen-printed white vinyl, limited to 500 copies.)',
         "price": "0.00", "available": True, "featured_image": None},
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


def _variant(title, **overrides):
    return {"title": title, "price": "20.00", "available": True,
            "featured_image": None, **overrides}


def _product(**overrides):
    # Invented: a minimal well-formed record, for tests about one field.
    return {
        "title": "Some Album", "vendor": "Some Artist", "handle": "some-album",
        "product_type": "Albums", "tags": [],
        "images": [{"src": "https://cdn.shopify.com/cover.jpg"}],
        "variants": [_variant("Black Vinyl + Digital")],
        **overrides,
    }


@pytest.fixture
def crawler():
    return Crawler()


async def _crawl(crawler):
    return [item async for item in crawler.crawl_catalog()]


def test_plugin_identity():
    assert Crawler.site_name == "Joyful Noise Recordings"
    assert Crawler.base_url == "https://www.joyfulnoiserecordings.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "rock"
    assert Crawler.genre_summary


@respx.mock
async def test_crawl_catalog_yields_item_fields(crawler):
    _mock_pages(_one_pressing(_TRAUMANAUT_PRODUCT, index=1))
    assert await _crawl(crawler) == [{
        "artist": "Goblin Cock",
        "title": "Traumanaut — 100% Recycled Eco Purple-Pink Vinyl + Digital",
        "format": "Vinyl",
        "price": 24.0,
        "currency": "USD",
        "url": "https://www.joyfulnoiserecordings.com/products/traumanaut",
        "cover_image_url": "https://cdn.shopify.com/JNR525_LP-C1_Mockup.jpg",
    }]


@respx.mock
async def test_the_walk_covers_the_whole_catalog_not_the_vinyl_shelf(crawler):
    # The store's `vinyl` shelf is exactly its `Vinyl`-tagged products; this
    # record is tagged only "InPress" and is absent from it. The crawler must
    # still find it, which it only does by walking `all`.
    assert "Vinyl" not in _SURFER_BLOOD_PRODUCT["tags"]
    _mock_pages(_SURFER_BLOOD_PRODUCT)
    titles = [i["title"] for i in await _crawl(crawler)]
    assert titles == ["1000 Palms — Sky Blue Vinyl + Digital",
                      "1000 Palms — Black Vinyl + Digital"]


# ---------------------------------------------------------------- format gate

@pytest.mark.parametrize("variant_title", [
    "Black Vinyl + Digital",
    "2xLP on Citrus Colored Vinyl (Includes download in AIFF/MP3/WAV)",
    "Limited Edition Vinyl (Limited to 500 copies.)",
    'Screen-printed 7" Vinyl (limited to 1000 copies)',
    '7" + Digital',
    '12" Test Pressing (Limited to 15 hand numbered copies)',
    "Limited Edition Test Pressing (Limited to 15 copies)",
    "Flexi-Disc",
    "Limited Edition Flexi Disc (limited to 1000 copies)",
    'Lathe-Cut 7" + Digital (Limited to 100 hand-made copies)',
    'Limited Edition Picture Disc 7" (limited to 974 hand-numbered copies)',
    '4x10" Vinyl Box Set in laser-etched hand-built wooden box',
    'Hardbound Book + 7" (First printing of 1000 hand-numbered copies)',
    "12-inch Test Pressing (Limited to 15 hand-numbered copies)",
])
def test_format_gate_admits_records(variant_title):
    assert Crawler._is_vinyl(variant_title)


@pytest.mark.parametrize("variant_title", [
    "CD + Digital",
    "CD + MP3",
    '2xCD "10th Anniversary Edition"',
    "Cassette + Digital (Includes download in high-quality MP3, WAV, and AIFF)",
    "Digital (instant MP3 download)",
    "MP3",
    # Names no medium at all: the members' slot, priced like a pressing but
    # describing none. A negative gate would publish it as vinyl.
    "VIP",
    "Default Title",
    "Yearly",
    "$100 monthly",
    "XL",
    "Zine (Printed and published by Monofonus Press.)",
])
def test_format_gate_rejects_everything_that_is_not_a_record(variant_title):
    assert not Crawler._is_vinyl(variant_title)


@pytest.mark.parametrize("variant_title", [
    # The inch marker is the poster's dimensions, not a record size.
    '18"x24" Poster',
    "Limited Edition Poster + Digital (Hand-screened 18\"x24\" poster, limited "
    "edition of 500.)",
    "Swamp Dogg canvas tote bag (12 oz. natural canvas 15\"W x 16\"H)",
    # 12" here measures a bound book the CDs sit inside.
    '5xCD Box Set (Five compact discs packaged inside an elaborate 12"x12", '
    "27 page bound-book)",
])
def test_an_inch_marker_that_is_not_a_record_size_does_not_admit(variant_title):
    assert not Crawler._is_vinyl(variant_title)


@pytest.mark.parametrize("variant_title", [
    # Merchandise is routinely sold AT record size, so restricting the marker
    # to record sizes does not by itself keep it out — a pair of inch marks is
    # a measurement whatever the numbers are.
    '12"x12" Poster',
    '12"x12" Print',
    '10"x10" Art Print',
    '7"x7" Sticker Sheet',
])
def test_a_record_sized_measurement_is_not_evidence_of_vinyl(variant_title):
    assert not Crawler._is_vinyl(variant_title)


@pytest.mark.parametrize("variant_title", [
    # A disc count is not a measurement: the multiplier carries no inch mark.
    '4x10" Vinyl Box Set',
    # A record may state its own dimensions; the strong word still decides.
    '12"x12" Screen-printed Vinyl',
    # The medium beside it does not veto a head that names vinyl outright.
    'Hardbound Book + 7"',
])
def test_a_measurement_does_not_reject_a_record_that_names_itself(variant_title):
    assert Crawler._is_vinyl(variant_title)


def test_the_head_decides_the_medium_so_a_download_is_not_admitted_by_its_blurb():
    # The digital edition of a box set. Its blurb names the LPs the download
    # covers; reading the whole string would sell it as vinyl.
    assert not Crawler._is_vinyl(
        "Digital (Includes MP3 and WAV downloads of all 5 LPs plus digital "
        "bonus content.)")


def test_the_blurb_still_supplies_the_signal_when_the_head_names_no_medium():
    # The head is the song title. Nothing but the parenthetical says "record".
    assert Crawler._is_vinyl(
        'Can\'t Let Go Juno (Hand-made lathe-cut 7" limited to 100 copies)')


# CAPTURED: the exact live variant title, from the store's "Reissues &
# Remnants" box set. Sold out at capture, which is why the format gate could
# drop it without changing a single row — a latent loss, not a visible one.
_LIVE_BOX_SET_VARIANT = (
    "Limited Edition Box Set + Digital (3xLP on deluxe colored vinyl, packaged "
    "in matte-laminated box set with spot-gloss, limited to 500 hand-numbered "
    "copies. Includes WAV & MP3 download of all audio, plus 23 bonus digital "
    "track.)"
)


def test_a_companion_download_does_not_convict_the_record_it_ships_with():
    # The head names a container and the download that comes with it, and no
    # vinyl; the pressing is stated only in the blurb. Vetoing on the `+
    # Digital` dropped an in-scope pressing.
    assert Crawler._is_vinyl(_LIVE_BOX_SET_VARIANT)


@pytest.mark.parametrize("variant_title", [
    _LIVE_BOX_SET_VARIANT,
    "Limited Edition Box Set + Digital (5xLPs on galaxy swirl colored vinyl "
    '+ bonus 7", packaged in a custom-built, screen-printed wooden box.)',
])
def test_a_box_set_states_its_pressing_in_the_blurb_and_is_admitted(variant_title):
    assert Crawler._is_vinyl(variant_title)


@pytest.mark.parametrize("variant_title", [
    # A variant that IS the download keeps its head whole: there is no joining
    # `+` to cut on, so the veto still reaches it.
    "Digital (Includes MP3 and WAV downloads of all 5 LPs plus digital bonus "
    "content.)",
    "MP3 Download (Flexi Discs are SOLD OUT this is the download only)",
    "CD [3xCD] + Digital (Includes instant download of all 3 LPs in MP3, WAV, "
    "and AIFF.)",
])
def test_cutting_the_companion_does_not_admit_the_download_itself(variant_title):
    assert not Crawler._is_vinyl(variant_title)


def test_a_container_of_another_medium_is_not_rescued_by_a_novelty_pressing():
    # A box of cassettes whose lid doubles as a playable lathe-cut single. The
    # head names only the container, so the blurb decides — and a competing
    # physical medium there vetoes, where the download words never could.
    assert not Crawler._is_vinyl(
        "Box Set + Digital (Limited to 100 hand-numbered copies, featuring 10 "
        "full-length cassettes packaged inside a custom wooden box set, with "
        "lid that doubles as a lathe-cut vinyl single. Includes instant "
        "download.)")


def test_a_box_of_other_albums_stays_out_once_the_companion_is_cut():
    # Cutting `+ Digital` lets the Triptych blurb be read; the other physical
    # goods it names are what keep it out.
    assert not Crawler._is_vinyl(
        'Triptych Box Set + Digital (Three 2xLPs "Grand Opening and Closing", '
        '"Of Natural History", "In Glorious Times" plus booklets and signed '
        "poster in tri-fold wooden box w/ etched and die-cut art.)")


def test_a_vinyl_word_in_the_head_survives_another_medium_beside_it():
    assert Crawler._is_vinyl('Red Vinyl + 7" (includes bonus 7" record & MP3 download)')
    assert Crawler._is_vinyl('Book + 7" (175 page hardcover book w/ 7" on Gold Vinyl)')


@respx.mock
async def test_only_the_vinyl_variants_of_a_multi_format_product_are_listed(crawler):
    _mock_pages(_TRAUMANAUT_PRODUCT)
    assert [i["title"] for i in await _crawl(crawler)] == [
        "Traumanaut — 100% Recycled Eco Purple-Pink Vinyl + Digital"]


# -------------------------------------------------------------- multi-release

@pytest.mark.parametrize("variant_title", [
    "2xLP Bundle + Digital (Both albums for a discounted price.)",
    "4xEP Bundle [black vinyl] + Digital",
    'Snowflathe Bundle (All 18 snowflake-shaped 7" records)',
    "[SOLD OUT] Lathe-Cut Bundle",
    "MYSTERY GRAB BAG (3 LPs and 2 flexi-discs from the JNR catalog)",
    "Complete Box Set (Includes exclusive pressings of the band's first 5 albums)",
    "2012 Flexi Disc Series FULL SET with Box",
])
def test_a_lot_of_several_releases_is_not_a_record(variant_title):
    assert not Crawler._items(_product(variants=[_variant(variant_title)]))


@respx.mock
async def test_a_box_of_other_albums_is_not_listed_under_this_one(crawler):
    # The $270 Triptych box is a variant of each of the three albums it holds.
    _mock_pages(_SLEEPYTIME_PRODUCT)
    items = await _crawl(crawler)
    assert [i["title"] for i in items] == [
        "In Glorious Times — 2xLP on Citrus Colored Vinyl"]
    assert [i["price"] for i in items] == [35.0]


def test_a_lot_named_by_the_product_rather_than_the_variant_is_not_a_record():
    # The store sells its lathe-cut club as a subscription product whose
    # variant names a plain format.
    assert not Crawler._items(_product(
        title="Danielson Artist Enabler Club One-Time Payment",
        variants=[_variant("15 lathe-cuts + Wooden Box + Digital")]))


def test_a_box_set_of_one_release_is_still_a_record():
    items = Crawler._items(_product(
        variants=[_variant('4x10" Vinyl Box Set in a hand-built wooden box')]))
    assert [i["title"] for i in items] == [
        'Some Album — 4x10" Vinyl Box Set in a hand-built wooden box']


# ------------------------------------------------------------------ the credit

def test_the_artist_comes_from_vendor():
    assert Crawler._credit(_TRAUMANAUT_PRODUCT) == ("Goblin Cock", "Traumanaut")


def test_a_series_vendor_yields_to_the_artist_in_the_title():
    artist, album = Crawler._credit(_WLS_PRODUCT)
    assert artist == "Ambulances"
    # The apostrophe inside the album must not close the quote early.
    assert album == "Frankie Bacon’s Blue, Blue Heart"


@pytest.mark.parametrize("title,artist,album", [
    ("Andy the Doorbum 'Art is Shit' [Private Stash]",
     "Andy the Doorbum", "Art is Shit [Private Stash]"),
    ("Cotton Pony 'Boys in the Attic' - curated by David Yow",
     "Cotton Pony", "Boys in the Attic - curated by David Yow"),
    ('Mistresses \'Define "Relationship"\' [Private Stash]',
     "Mistresses", 'Define "Relationship" [Private Stash]'),
    ("Kelman Duran 'Kelman Duran'", "Kelman Duran", "Kelman Duran"),
])
def test_series_titles_split_into_artist_and_album(title, artist, album):
    assert Crawler._credit(
        {"title": title, "vendor": "White Label Series"}) == (artist, album)


def test_vendor_wins_whenever_it_appears_in_the_title():
    # The store also quotes albums on products it vendors correctly; there the
    # two agree, so there is nothing to rescue and nothing to risk.
    assert Crawler._credit({
        "title": "Deerhoof 'Some Album'", "vendor": "Deerhoof",
    }) == ("Deerhoof", "Deerhoof 'Some Album'")


def test_a_title_that_opens_on_the_quote_keeps_the_vendor():
    # `'Emerald Sea' (Test Pressing)` has no artist ahead of the quote.
    assert Crawler._credit({
        "title": "'Emerald Sea' (Test Pressing)", "vendor": "Sound Of Ceres",
    }) == ("Sound Of Ceres", "'Emerald Sea' (Test Pressing)")


def test_an_apostrophe_in_an_ordinary_title_does_not_split_it():
    assert Crawler._credit({
        "title": "Get Yer Ba-Ba's Out", "vendor": "Dale Crover",
    }) == ("Dale Crover", "Get Yer Ba-Ba's Out")


@respx.mock
async def test_a_series_record_reaches_the_row_under_its_artist(crawler):
    _mock_pages(_WLS_PRODUCT)
    items = await _crawl(crawler)
    assert items[0]["artist"] == "Ambulances"
    assert items[0]["title"].startswith("Frankie Bacon’s Blue, Blue Heart —")


# ------------------------------------------------------------------- the title

@respx.mock
async def test_the_row_title_leads_with_the_album_so_it_prefix_matches_a_library_title(crawler):
    _mock_pages(_SURFER_BLOOD_PRODUCT)
    assert all(i["title"].startswith("1000 Palms ")
               for i in await _crawl(crawler))


def test_the_descriptor_drops_the_store_blurb():
    items = Crawler._items(_product(variants=[_variant(
        "Limited Edition Vinyl + Digital (Limited to 400 hand-numbered copies "
        "on Silver Lava w/ Clear Splatter)")]))
    assert items[0]["title"] == "Some Album — Limited Edition Vinyl + Digital"


def test_every_admitted_variant_yields_its_own_identity():
    items = Crawler._items(_SURFER_BLOOD_PRODUCT)
    assert len({i["title"] for i in items}) == len(items)


def test_variants_that_would_collide_when_trimmed_keep_their_full_titles():
    # Trimming is display-only, but item_key hashes the title, so a collision
    # would silently overwrite one row with the other.
    items = Crawler._items(_product(variants=[
        _variant('12" Test Pressing (Limited to 15 hand numbered copies)'),
        _variant('12" Test Pressing (Limited to 10 hand numbered copies)'),
    ]))
    assert [i["title"] for i in items] == [
        'Some Album — 12" Test Pressing (Limited to 15 hand numbered copies)',
        'Some Album — 12" Test Pressing (Limited to 10 hand numbered copies)',
    ]
    assert len({i["title"] for i in items}) == 2


def test_variant_title_whitespace_is_collapsed():
    items = Crawler._items(_product(variants=[_variant("Black   Vinyl\n+ Digital")]))
    assert items[0]["title"] == "Some Album — Black Vinyl + Digital"


def test_a_blank_variant_title_is_skipped():
    assert not Crawler._items(_product(variants=[_variant("   ")]))


# ------------------------------------------------------------------- the stock

@respx.mock
async def test_a_sold_out_variant_is_skipped_beside_its_in_stock_siblings(crawler):
    _mock_pages(_SURFER_BLOOD_PRODUCT)
    titles = [i["title"] for i in await _crawl(crawler)]
    assert not any("VIP-Exclusive" in t for t in titles)
    assert len(titles) == 2


@pytest.mark.parametrize("available", [False, "false", "true", 1, None, "yes"])
def test_only_the_literal_true_admits_a_variant(available):
    assert not Crawler._items(_product(
        variants=[_variant("Black Vinyl + Digital", available=available)]))


# ------------------------------------------------------------------- the price

@pytest.mark.parametrize("price", ["0.00", "0", "-5.00", "nan", "", None, True, "free"])
def test_a_variant_with_no_usable_price_is_not_a_listing(price):
    assert not Crawler._items(_product(
        variants=[_variant("Black Vinyl + Digital", price=price)]))


@respx.mock
async def test_an_unpriced_placeholder_never_reaches_a_row(crawler):
    # Would otherwise be listed with an artist named "hidden".
    _mock_pages(_HIDDEN_PLACEHOLDER_PRODUCT, _TRAUMANAUT_PRODUCT)
    items = await _crawl(crawler)
    assert [i["artist"] for i in items] == ["Goblin Cock"]


def test_the_price_is_read_as_a_number():
    items = Crawler._items(_product(variants=[_variant("Black Vinyl", price="22.50")]))
    assert items[0]["price"] == 22.5


# A price the walk declines to USE is not a price it cannot READ, and only the
# second is evidence of drift. Conflating them is what made the price guard
# fire on every empty walk; see the guard tests below.
@pytest.mark.parametrize("price", ["0.00", "0", "-5.00", "22.00", 22.0])
def test_a_readable_price_is_never_drift_evidence(price):
    assert not Crawler._price_unreadable(_variant("Black Vinyl", price=price))


@pytest.mark.parametrize("price", [None, "", "free", "22.00 USD", True, False,
                                   float("nan"), float("inf"), {"amount": "22.00"},
                                   ["22.00"]])
def test_a_price_that_cannot_be_read_at_all_is_drift_evidence(price):
    assert Crawler._price_unreadable(_variant("Black Vinyl", price=price))


def test_the_stores_own_placeholders_are_not_counted_as_drift():
    # The two "VIP LATHE TEST" products and the `copy-of-...` duplicate are
    # priced at zero on purpose. Counting them left the tally permanently at
    # their number, which is what broke the guard.
    assert Crawler._unreadable_prices(_HIDDEN_PLACEHOLDER_PRODUCT) == 0


# ---------------------------------------------------------------- the identity

@pytest.mark.parametrize("field", ["title", "handle"])
def test_a_product_missing_its_identity_is_skipped(field):
    assert not Crawler._items(_product(**{field: ""}))


def test_junk_variant_entries_are_ignored():
    items = Crawler._items(_product(
        variants=["not a dict", None, 42, _variant("Black Vinyl + Digital")]))
    assert [i["title"] for i in items] == ["Some Album — Black Vinyl + Digital"]


def test_the_cover_prefers_the_variant_image_over_the_products():
    items = Crawler._items(_one_pressing(_TRAUMANAUT_PRODUCT, index=1))
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/JNR525_LP-C1_Mockup.jpg"


def test_the_cover_falls_back_to_the_products_first_image():
    items = Crawler._items(_product(variants=[_variant("Black Vinyl")]))
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/cover.jpg"


# ----------------------------------------------------------------- drift guards
# replace_stock_items() DELETEs the previous snapshot before inserting, and
# _sync_stock only skips that call when the crawl raised -- so a
# completed-but-empty walk is destructive where a raise is inert.

@respx.mock
async def test_an_empty_collection_raises(crawler):
    _mock_pages(empty_page=1)
    with pytest.raises(RuntimeError, match="returned no products"):
        await _crawl(crawler)


@respx.mock
async def test_a_catalog_whose_records_lost_their_artist_raises(crawler):
    _mock_pages(_product(vendor=""), _product(vendor=None, handle="b"))
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_merch_keeping_its_vendor_does_not_vouch_for_vendorless_records(crawler):
    # The guard counts only products that ARE records. Tallied over every
    # product instead, a CD-only product that can never yield a row would
    # satisfy it while every record was skipped for want of an artist — and
    # the completed-but-empty walk would delete the snapshot.
    _mock_pages(
        _product(vendor="", handle="record"),
        _product(vendor="Some Label", handle="cd",
                 variants=[_variant("CD + Digital")]),
    )
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_a_credited_sold_out_record_does_not_vouch_for_a_vendorless_one(crawler):
    _mock_pages(
        _product(vendor="", handle="vendorless"),
        _product(vendor="Some Artist", handle="soldout",
                 variants=[_variant("Black Vinyl + Digital", available=False)]),
    )
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_one_vendorless_record_among_real_rows_is_only_a_skipped_row(crawler):
    _mock_pages(_TRAUMANAUT_PRODUCT, _product(vendor="", handle="vendorless"))
    assert len(await _crawl(crawler)) == 1


@respx.mock
async def test_a_catalog_that_names_no_vinyl_format_raises(crawler):
    # The gate is positive, so the store moving format out of the variant
    # title would empty the walk rather than merely admit too much. Nothing
    # else notices that.
    _mock_pages(_product(variants=[_variant("CD + Digital")]),
                _product(handle="b", variants=[_variant("Digital")]))
    with pytest.raises(RuntimeError, match="format-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_a_catalog_whose_prices_all_broke_raises(crawler):
    # Only unreadable shapes here: a zero is a price the store means, and
    # counting it is what the test below exists to prevent regressing.
    _mock_pages(_product(variants=[_variant("Black Vinyl", price=None)]),
                _product(handle="b", variants=[_variant("Red Vinyl", price="12.00 USD")]))
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_a_sold_out_catalog_keeps_its_placeholders_from_forcing_a_raise(crawler):
    # The honest empty walk: every real record sold out, while the store's
    # zero-priced placeholders stay in stock. Counting those as drift left the
    # tally non-zero whatever the payload did, so this walk raised and pinned
    # the previous snapshot in place -- the one case an empty walk is supposed
    # to be believed. Found by Copilot in review on PR #337.
    _mock_pages(
        _product(variants=[_variant("Black Vinyl + Digital", available=False)]),
        _HIDDEN_PLACEHOLDER_PRODUCT,
        _product(handle="vip-lathe-test", title="VIP LATHE TEST",
                 variants=[_variant('Lathe-Cut 7" / Ships Immediately', price="0.00")]),
    )
    assert await _crawl(crawler) == []


@respx.mock
async def test_a_placeholder_does_not_vouch_for_a_catalog_whose_prices_broke(crawler):
    # The other half of the same distinction: a placeholder alongside a real
    # break must not dilute the tally into silence.
    _mock_pages(
        _HIDDEN_PLACEHOLDER_PRODUCT,
        _product(handle="b", variants=[_variant("Red Vinyl", price=None)]),
    )
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_a_catalog_that_lost_its_handles_raises(crawler):
    _mock_pages(_product(handle=""), _product(handle=None, title="B"))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_a_catalog_whose_stock_flags_went_unreadable_raises(crawler):
    _mock_pages(_product(variants=[_variant("Black Vinyl", available="false")]))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _crawl(crawler)


@respx.mock
@pytest.mark.parametrize("variants", [None, {}, "junk", ["not a dict", 42], [],
                                     1, True, 2.5])
async def test_a_catalog_whose_variants_went_unreadable_raises(crawler, variants):
    # A product whose variants cannot be read yields no pressings, so it looks
    # exactly like one that stocks no records and every tally nested in the
    # pressings branch skips it. One readable sold-out record beside it is
    # enough to keep the format guard satisfied — and then the empty walk
    # would delete the snapshot.
    _mock_pages(
        _product(handle="soldout",
                 variants=[_variant("Black Vinyl + Digital", available=False)]),
        _product(handle="unreadable", variants=variants),
    )
    with pytest.raises(RuntimeError, match="variant-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_unreadable_variants_are_named_before_the_format_guard(crawler):
    # Broken store-wide, both guards' conditions hold; the message must name
    # the upstream cause rather than the format gate it starved.
    _mock_pages(_product(handle="a", variants=None),
                _product(handle="b", variants="junk"))
    with pytest.raises(RuntimeError, match="variant-source drift"):
        await _crawl(crawler)


@respx.mock
async def test_one_malformed_product_among_real_rows_is_only_a_skipped_row(crawler):
    _mock_pages(_TRAUMANAUT_PRODUCT, _product(handle="broken", variants=None))
    assert len(await _crawl(crawler)) == 1


@pytest.mark.parametrize("variants,readable", [
    ([{"title": "Black Vinyl", "price": "20.00", "available": True}], True),
    (None, False), ({}, False), ("junk", False), ([], False),
    (["not a dict"], False),
    ([{"title": "Black Vinyl"}, "not a dict"], False),
    # Truthy scalars: not iterable, so reading them without an isinstance
    # check raises TypeError rather than reaching the guard.
    (1, False), (True, False), (2.5, False),
])
def test_readable_variants_requires_a_non_empty_list_of_mappings(variants, readable):
    assert Crawler._has_readable_variants({"variants": variants}) is readable


@pytest.mark.parametrize("variants", [None, {}, "junk", 1, True, 2.5, 0, ""])
def test_a_variants_field_that_is_not_a_list_reads_as_no_variants(variants):
    # Both readers go through this, which is the point: `_has_readable_variants`
    # already tested isinstance while `_pressings` iterated the raw value, and
    # a truthy scalar took the two apart -- TypeError out of the comprehension,
    # aborting the source before the guard could name the cause.
    assert Crawler._raw_variants({"variants": variants}) == []


def test_a_truthy_scalar_variants_field_reaches_the_guard_instead_of_crashing():
    product = _product(variants=1)
    assert Crawler._pressings(product) == []
    assert Crawler._has_readable_variants(product) is False


@respx.mock
async def test_a_catalog_that_merely_sold_out_does_not_raise(crawler):
    _mock_pages(_product(variants=[_variant("Black Vinyl", available=False)]))
    assert await _crawl(crawler) == []


@respx.mock
async def test_one_unreadable_product_among_real_rows_is_only_a_skipped_row(crawler):
    _mock_pages(_TRAUMANAUT_PRODUCT,
                _product(handle="b", variants=[_variant("Black Vinyl", available="false")]))
    assert len(await _crawl(crawler)) == 1
