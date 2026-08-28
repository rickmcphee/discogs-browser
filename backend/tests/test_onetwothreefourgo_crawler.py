import httpx
import respx
import pytest
from crawlers.onetwothreefourgo import Crawler

_PRODUCTS_URL = "https://1234gorecords.shop/collections/all/products.json"

# Every fixture below is a live product captured from the store on 2026-08-28,
# trimmed to the fields the crawler reads. Titles are verbatim, punctuation and
# typos included -- the punctuation is what most of these tests are about.

# The dominant title form: straight quotes around the album, format descriptor
# after it, no status marker.
_PRODUCT = {
    "title": 'Ben Pirani "How Do I Talk To My Brother" LP',
    "vendor": "Secretly Canadian",
    "handle": "ben-pirani-how-do-i-talk-to-my-brother-lp",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/3825480.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "14.99", "available": True, "featured_image": None},
    ],
}

# The doubled-apostrophe delimiter, which 28 live products use and nothing else.
_DOUBLED_APOSTROPHE_PRODUCT = {
    "title": "Superchunk ''I Hate Music'' LP",
    "vendor": "Merge Records",
    "handle": "superchunk-i-hate-music-lp",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/1876533.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "21.99", "available": True, "featured_image": None},
    ],
}

# Curly quotes. Also an LP-typed CD, so it doubles as the mistyped-product case.
_CURLY_QUOTE_CD_PRODUCT = {
    "title": "Jorma Kaukonen & John Hurlbut ”One More Lifetime” CD (RSD 2024)",
    "vendor": "Alliance",
    "handle": "819514012658",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/418467268300_800.webp"}],
    "variants": [
        {"title": "Default Title", "price": "15.99", "available": True, "featured_image": None},
    ],
}

# The store's own classification is right and the title agrees: a real record
# whose descriptor also names a CD. The vinyl override has to keep it.
_HYBRID_PRODUCT = {
    "title": 'Justice "Audio, Video, Disco" 2xLP + CD',
    "vendor": "Forced Exposure",
    "handle": "5060281610645",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/BEC5161064_CU.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "32.99", "available": True, "featured_image": None},
    ],
}

_USED_PRODUCT = {
    "title": 'Used Vinyl: Wire "154" LP (1979 Japanese Issue)',
    "vendor": "Used Product",
    "handle": "102500005981",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/IMG_4930.heic"}],
    "variants": [
        {"title": "Default Title", "price": "100.00", "available": True, "featured_image": None},
    ],
}

# Two live products carry the marker twice. A single strip pass leaves the
# second copy stuck to the artist.
_DOUBLE_MARKER_PRODUCT = {
    "title": 'Used Vinyl: Used Vinyl: Aso-Naga / Restriction "Split" 7"',
    "vendor": "Used Product",
    "handle": "used-vinyl-used-vinyl-aso-naga-restriction-split-7",
    "product_type": '7"',
    "images": [{"src": "https://cdn.shopify.com/FullSizeRender.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "2.00", "available": True, "featured_image": None},
    ],
}

_PREORDER_PRODUCT = {
    "title": 'PRE-ORDER: Blu & Exile "Time Heals Everything" LP',
    "vendor": "Alliance",
    "handle": "784085106429",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/4531401-3679534.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "32.99", "available": True, "featured_image": None},
    ],
}

_DAMAGED_PRODUCT = {
    "title": 'DAMAGED COVER: Blur "Parklife" 2xLP',
    "vendor": "WEA",
    "handle": "5099962484213damaged",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/92_f7c3a637.webp"}],
    "variants": [
        {"title": "Default Title", "price": "36.99", "available": True, "featured_image": None},
    ],
}

# "DAMAGED COVER" with no separator at all before the artist.
_DAMAGED_NO_SEPARATOR_PRODUCT = {
    "title": 'DAMAGED COVER Danzig "S/T" LP (White Vinyl RSD Essentials Edition)',
    "vendor": "Think Indie",
    "handle": "damaged-cover-danzig-s-t-lp-white-vinyl-rsd-essentials-edition-copy",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/Danzig_I_LP_Mockup.webp"}],
    "variants": [
        {"title": "Default Title", "price": "28.99", "available": True, "featured_image": None},
    ],
}

# The one live product the marker rules deliberately do not strip -- a bare
# "DAMAGED " with no colon. See _BAND_NAMED_DAMAGED below for what that buys.
_BARE_DAMAGED_PRODUCT = {
    "title": 'DAMAGED Sultans "Ghost Ship" LP (Opaque White Vinyl)',
    "vendor": "Revolver",
    "handle": "pre-order-damaged-sultans-ghost-ship-lp-opaque-white-vinyl",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/94_766be8ca.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "25.99", "available": True, "featured_image": None},
    ],
}

# Hypothetical, and the whole reason the bare "DAMAGED " form is left alone:
# Damaged Bug is John Dwyer's project, exactly the kind of record a Bay Area
# store stocks.
_BAND_NAMED_DAMAGED = {
    "title": 'Damaged Bug "Bunker Funk" LP',
    "vendor": "Castle Face",
    "handle": "damaged-bug-bunker-funk-lp",
    "product_type": "LP",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "23.99", "available": True, "featured_image": None},
    ],
}

# A LEFT-TO-RIGHT MARK sits between artist and opening quote on 195 live
# products. It is a format character, so str.strip() does not remove it, and
# db._library_match_fragment compares artist with exact LOWER() equality.
# This fixture also has no images.
_INVISIBLE_CHAR_PRODUCT = {
    # \u200e is written as an escape, not pasted: the character it stands for is
    # exactly the one this test is about, and an invisible literal in a fixture
    # is one stray editor save away from vanishing without failing anything.
    "title": 'Used Vinyl: A.R.B \u200e"Yellow Blood" LP (1984 Japanese Press)',
    "vendor": "Used Product",
    "handle": "used-vinyl-a-r-b-yellow-blood-lp-1984-japanese-press",
    "product_type": "LP",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "10.00", "available": True, "featured_image": None},
    ],
}

# An album that is a bare number. Any "does the album look like a format token"
# guard fires on this, and on Adele "21", Blur "13", Mac DeMarco "2".
_NUMERIC_ALBUM_PRODUCT = {
    "title": 'Adele "19" LP',
    "vendor": "Matador",
    "handle": "191404093818",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/61oS8T4cjLL.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "22.99", "available": True, "featured_image": None},
    ],
}

# A quoted phrase inside the pressing notes, after the album's own closing
# quote. Non-greedy groups are what keep the split on the first pair.
_NESTED_QUOTE_PRODUCT = {
    "title": (
        'Robert Ziegler "Music From The Star Wars Saga (Soundtrack)" 2xLP '
        '(May The 4th Be With You Edition "Hyperspace" Blue Splatter Vinyl)'
    ),
    "vendor": "Light In The Attic",
    "handle": "8719262043466",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/96_453d1fdd.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "62.99", "available": True, "featured_image": None},
    ],
}

# A 7" single: the descriptor's inch mark is a third quote character in the
# title, and must not be mistaken for the album's closing one.
_SEVEN_INCH_PRODUCT = {
    "title": 'Ben Pirani & The Means of Production "I Know It Hurts / Something So Precious" 7"',
    "vendor": "Secretly Canadian",
    "handle": "pre-order-ben-pirani-the-means-of-production-i-know-it-hurts-something-so-precious-7",
    "product_type": '7"',
    "images": [{"src": "https://cdn.shopify.com/PST005.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "6.99", "available": True, "featured_image": None},
    ],
}

# No quotes anywhere -- one of the 28 live products with no usable split.
_NO_QUOTE_PRODUCT = {
    "title": "Sophie S/T 2xLP",
    "vendor": "UMG",
    "handle": "pre-order-sophie-s-t-lp",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/1fclp00595__20644.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "42.99", "available": True, "featured_image": None},
    ],
}

# LP-typed, and a CD by its own title. product_type alone would publish it.
_MISTYPED_CD_PRODUCT = {
    "title": 'Kehlani "S/T" CD',
    "vendor": "WEA",
    "handle": "kehlani",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/031226_Kehlani.png"}],
    "variants": [
        {"title": "Default Title", "price": "14.99", "available": True, "featured_image": None},
    ],
}

# A genuine non-vinyl product_type.
_CASSETTE_TYPED_PRODUCT = {
    "title": 'Alabama Shakes "I Must Be Dreaming" Cassette',
    "vendor": "WEA",
    "handle": "alabama-shakes-i-must-be-dreaming-cassette",
    "product_type": "Cassette",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "11.99", "available": True, "featured_image": None},
    ],
}

# Four colour variants, one sold out. Every variant carries the product image
# as its own featured_image.
_MULTI_VARIANT_PRODUCT = {
    "title": 'Shannon and The Clams "Sleep Talk" LP',
    "vendor": "1-2-3-4 Go! Records",
    "handle": "shannon-amp-the-clams-sleep-talk",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/clamssleeptalksmall.jpg"}],
    "variants": [
        {"id": 42432112165064, "title": "Yellow & Blue split with Silver Splatter",
         "price": "7.99", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/clams-silver.jpg"}},
        {"id": 42432112230600, "title": "Yellow & Blue Split with Gold Splatter",
         "price": "7.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/clams-gold.jpg"}},
        {"id": 42432112263368, "title": "Metallic Gold", "price": "7.99", "available": True,
         "featured_image": None},
        {"id": 42432112296136, "title": "Metallic Silver", "price": "7.99", "available": True,
         "featured_image": None},
    ],
}

# The vinyl variant is sold out and only the cassette is in stock. Without the
# competing-format filter this product publishes a cassette as a record.
_CASSETTE_ONLY_IN_STOCK_PRODUCT = {
    "title": 'Roger Bekono "Roger Bekono"',
    "vendor": "Secretly Canadian",
    "handle": "pre-order-roger-bekono-roger-bekono",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/ATFA047.jpg"}],
    "variants": [
        {"id": 42649265897672, "title": "Black LP", "price": "19.99", "available": False,
         "featured_image": None},
        {"id": 42649265930440, "title": "Cassette", "price": "9.99", "available": True,
         "featured_image": None},
    ],
}

# A variant naming CD and DVD alongside 2xLP -- a vinyl box set with bonus
# discs, which the vinyl override keeps.
_HYBRID_VARIANT_PRODUCT = {
    "title": "Lou Reed ''New York'' 2xLP (Multiple Variants)",
    "vendor": "WEA",
    "handle": "loureednewyork",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/4006309-2740428.jpg"}],
    "variants": [
        {"id": 42432013271240, "title": "2xLP Clear Vinyl", "price": "29.99", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/loureed-clear.jpg"}},
        {"id": 42432013304008, "title": "2xLP 2xCD + DVD", "price": "67.49", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/loureed-box.jpg"}},
    ],
}

_UNAVAILABLE_PRODUCT = {
    "title": 'Pavement "Terror Twilight" LP',
    "vendor": "Matador",
    "handle": "pavement-terror-twilight-lp",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/pavement.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "19.99", "available": False, "featured_image": None},
    ],
}

_MALFORMED_PRICE_PRODUCT = {
    "title": 'Pavement "Wowee Zowee" LP',
    "vendor": "Matador",
    "handle": "pavement-wowee-zowee-lp",
    "product_type": "LP",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": None, "available": True, "featured_image": None},
    ],
}

# Count-prefixed non-vinyl formats. A disc count binds to its format word with
# no word boundary between them, so a bare `\bcds?\b` cannot see the CD in
# "3xCD" -- these two were published as records until both sides of the filter
# learned the prefix. LP-typed by the store, CD/DVD by their own titles.
_COUNT_PREFIXED_CD_PRODUCT = {
    "title": 'PRE-ORDER: Tears for Fears "The Seeds of Love (Deluxe Edition)" 3xCD',
    "vendor": "UMG",
    "handle": "tears-for-fears-seeds-of-love-deluxe-3xcd",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/tff.png"}],
    "variants": [
        {"title": "Default Title", "price": "56.99", "available": True, "featured_image": None},
    ],
}

_COUNT_PREFIXED_BOX_SET_PRODUCT = {
    "title": 'PRE-ORDER: Joy Division "ETERNAL (Live)" 14xCD + 2xDVD Box Set',
    "vendor": "WEA",
    "handle": "5026854859309",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/93_fb5bddae.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "129.99", "available": True, "featured_image": None},
    ],
}

# A CD the store filed under 7", so neither the type gate nor a bare CD regex
# catches it -- only the count-prefixed one does.
_COUNT_PREFIXED_MISTYPED_CD_PRODUCT = {
    "title": (
        'Used CD: The Saints "Wild About You 1976-1978 (Complete Studio Recordings)" '
        '2xCD (2000 Aussie Press)'
    ),
    "vendor": "Used Product",
    "handle": "used-cd-the-saints",
    "product_type": '7"',
    "images": [{"src": "https://cdn.shopify.com/saints.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "30.00", "available": True, "featured_image": None},
    ],
}

# The other side of the same rule: a real vinyl box set whose descriptor names
# fifteen CDs. The vinyl override has to survive the count prefix too.
_VINYL_BOX_SET_WITH_BONUS_DISCS = {
    "title": 'Metallica "ReLoad (Remastered Deluxe Box Set)" 2xLP + 15xCD Box Set (180g Vinyl)',
    "vendor": "WEA",
    "handle": "810083963525",
    "product_type": "LP",
    "images": [{"src": "https://cdn.shopify.com/95_91d3f877.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "259.99", "available": True, "featured_image": None},
    ],
}

# "EP" names a release length, not a medium -- EP pressings exist on vinyl and
# CD alike -- so it must not vouch for a descriptor that names a CD. Not live
# on this store; pins the rule against a future one.
_CD_EP_PRODUCT = {
    "title": 'Some Band "Short Player" CD EP',
    "vendor": "Alliance",
    "handle": "some-band-short-player-cd-ep",
    "product_type": "LP",
    "images": [],
    "variants": [
        {"title": "Default Title", "price": "9.99", "available": True, "featured_image": None},
    ],
}

# The inch mark is the opposite case and must keep its override: a real
# record-plus-tape bundle, live on the store.
_VINYL_PLUS_CASSETTE_PRODUCT = {
    "title": 'Used Vinyl: Impiety “Ascension 1991” 7" + Cassette (Diehard Edition 58/130)',
    "vendor": "Used Product",
    "handle": "used-vinyl-impiety-ascension-1991",
    "product_type": '7"',
    "images": [{"src": "https://cdn.shopify.com/impiety.jpg"}],
    "variants": [
        {"title": "Default Title", "price": "45.00", "available": True, "featured_image": None},
    ],
}


def _page_response(products):
    return httpx.Response(200, json={"products": products})


def _mock_single_page(products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response(products))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))


@pytest.fixture
def crawler():
    return Crawler()


@respx.mock
async def test_crawl_catalog_parses_quoted_album_and_emits_full_item(crawler):
    _mock_single_page([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    item = items[0]
    assert item["artist"] == "Ben Pirani"
    assert item["title"] == "How Do I Talk To My Brother LP"
    assert item["price"] == 14.99
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["url"] == "https://1234gorecords.shop/products/ben-pirani-how-do-i-talk-to-my-brother-lp"
    assert item["cover_image_url"] == "https://cdn.shopify.com/3825480.jpg"


@respx.mock
async def test_crawl_catalog_splits_doubled_apostrophe_delimiter(crawler):
    _mock_single_page([_DOUBLED_APOSTROPHE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [("Superchunk", "I Hate Music LP")]


@respx.mock
async def test_crawl_catalog_splits_curly_quote_delimiter(crawler):
    # Curly quotes on an LP-typed CD: the split must work, and the descriptor
    # filter must then drop the row anyway.
    curly_lp = {**_CURLY_QUOTE_CD_PRODUCT, "title": "Jorma Kaukonen & John Hurlbut ”One More Lifetime” LP (RSD 2024)"}
    _mock_single_page([curly_lp])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Jorma Kaukonen & John Hurlbut", "One More Lifetime LP (RSD 2024)"),
    ]


@respx.mock
async def test_crawl_catalog_keeps_the_format_descriptor_in_the_title(crawler):
    # The pressing note is what distinguishes two rows for the same album:
    # dropping it collapses 1,003 live rows onto another row's reading.
    _mock_single_page([_USED_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "154 LP (1979 Japanese Issue) (Used)"


@respx.mock
async def test_crawl_catalog_moves_used_marker_from_prefix_to_suffix(crawler):
    # A prefix is the one position db._library_match_fragment's
    # exact-or-prefix-with-space match cannot survive.
    _mock_single_page([_USED_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Wire"
    assert items[0]["title"].endswith("(Used)")
    assert not items[0]["title"].startswith("Used Vinyl")


@respx.mock
async def test_crawl_catalog_strips_a_repeated_marker(crawler):
    _mock_single_page([_DOUBLE_MARKER_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Aso-Naga / Restriction"
    assert items[0]["title"] == 'Split 7" (Used)'


@respx.mock
async def test_crawl_catalog_marks_pre_orders(crawler):
    _mock_single_page([_PREORDER_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Blu & Exile"
    assert items[0]["title"] == "Time Heals Everything LP (Pre-Order)"


@respx.mock
async def test_crawl_catalog_marks_damaged_copies(crawler):
    _mock_single_page([_DAMAGED_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Blur"
    assert items[0]["title"] == "Parklife 2xLP (Damaged)"


@respx.mock
async def test_crawl_catalog_strips_damaged_cover_without_a_separator(crawler):
    _mock_single_page([_DAMAGED_NO_SEPARATOR_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Danzig"
    assert items[0]["title"] == "S/T LP (White Vinyl RSD Essentials Edition) (Damaged)"


@respx.mock
async def test_crawl_catalog_leaves_a_bare_damaged_prefix_alone(crawler):
    # The cost of the rule the next test pays for: this live product keeps a
    # marker it should not. Pinned so it is a decision, not a surprise.
    _mock_single_page([_BARE_DAMAGED_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "DAMAGED Sultans"


@respx.mock
async def test_crawl_catalog_does_not_mistake_a_band_named_damaged_for_a_marker(crawler):
    _mock_single_page([_BAND_NAMED_DAMAGED])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [("Damaged Bug", "Bunker Funk LP")]


@respx.mock
async def test_crawl_catalog_removes_invisible_format_characters_from_the_artist(crawler):
    _mock_single_page([_INVISIBLE_CHAR_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "A.R.B"
    assert "\u200e" not in items[0]["artist"]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_crawl_catalog_keeps_an_album_whose_name_is_a_bare_number(crawler):
    _mock_single_page([_NUMERIC_ALBUM_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [("Adele", "19 LP")]


@respx.mock
async def test_crawl_catalog_splits_on_the_first_quote_pair_only(crawler):
    _mock_single_page([_NESTED_QUOTE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Robert Ziegler"
    assert items[0]["title"] == (
        'Music From The Star Wars Saga (Soundtrack) 2xLP '
        '(May The 4th Be With You Edition "Hyperspace" Blue Splatter Vinyl)'
    )


@respx.mock
async def test_crawl_catalog_is_not_confused_by_a_trailing_inch_mark(crawler):
    _mock_single_page([_SEVEN_INCH_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["artist"] == "Ben Pirani & The Means of Production"
    assert items[0]["title"] == 'I Know It Hurts / Something So Precious 7"'


@respx.mock
async def test_crawl_catalog_drops_a_product_with_no_quoted_album(crawler):
    # Paired with a parseable product deliberately: a catalog whose *only*
    # vinyl product fails to parse is indistinguishable from total title-format
    # drift, and the drift guard is right to raise on it (see
    # test_crawl_catalog_raises_when_no_vinyl_title_parses).
    _mock_single_page([_NO_QUOTE_PRODUCT, _PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Ben Pirani"]


@respx.mock
async def test_crawl_catalog_never_falls_back_to_vendor_for_artist(crawler):
    # vendor is a distributor ("UMG") or the literal "Used Product" on this
    # store, never the artist.
    _mock_single_page([_NO_QUOTE_PRODUCT, _PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Ben Pirani"]


@respx.mock
async def test_crawl_catalog_drops_a_vinyl_typed_product_whose_descriptor_says_cd(crawler):
    _mock_single_page([_MISTYPED_CD_PRODUCT, _CURLY_QUOTE_CD_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_a_hybrid_release_naming_both_formats(crawler):
    _mock_single_page([_HYBRID_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Justice", "Audio, Video, Disco 2xLP + CD"),
    ]


@respx.mock
async def test_crawl_catalog_skips_a_non_vinyl_product_type(crawler):
    _mock_single_page([_CASSETTE_TYPED_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_skips_an_unavailable_variant(crawler):
    _mock_single_page([_UNAVAILABLE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_emits_one_row_per_available_variant(crawler):
    _mock_single_page([_MULTI_VARIANT_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Sleep Talk LP — Yellow & Blue Split with Gold Splatter",
        "Sleep Talk LP — Metallic Gold",
        "Sleep Talk LP — Metallic Silver",
    ]


@respx.mock
async def test_crawl_catalog_gives_multi_variant_rows_distinct_item_identities(crawler):
    # db.compute_item_key() hashes (artist, title, url) and the url is
    # per-product, so without the variant descriptor these three rows would
    # collapse onto one item_key: one marketplace lookup for three pressings,
    # and three indistinguishable Store rows.
    _mock_single_page([_MULTI_VARIANT_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len({(i["artist"], i["title"], i["url"]) for i in items}) == 3


@respx.mock
async def test_crawl_catalog_keeps_variant_identity_stable_when_siblings_sell_out(crawler):
    # The descriptor is gated on the surviving variant count, not the available
    # one. Gated on availability, this product dropping to one available
    # variant would rewrite that row's title, changing its item_key and
    # orphaning its listings and saved-item rows.
    sold_down = {**_MULTI_VARIANT_PRODUCT, "variants": [
        {**v, "available": v["title"] == "Metallic Gold"}
        for v in _MULTI_VARIANT_PRODUCT["variants"]
    ]}
    _mock_single_page([sold_down])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Sleep Talk LP — Metallic Gold"]


@respx.mock
async def test_crawl_catalog_omits_the_descriptor_for_a_single_variant_product(crawler):
    _mock_single_page([_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["title"] == "How Do I Talk To My Brother LP"


@respx.mock
async def test_crawl_catalog_drops_a_competing_format_variant(crawler):
    # Only the cassette is in stock, so this product must yield nothing rather
    # than publish a cassette as a record.
    _mock_single_page([_CASSETTE_ONLY_IN_STOCK_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_a_variant_naming_both_formats(crawler):
    _mock_single_page([_HYBRID_VARIANT_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Lou Reed", "New York 2xLP (Multiple Variants) — 2xLP 2xCD + DVD"),
    ]


@respx.mock
async def test_crawl_catalog_prefers_the_variant_image_over_the_product_image(crawler):
    _mock_single_page([_MULTI_VARIANT_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["cover_image_url"] for i in items] == [
        "https://cdn.shopify.com/clams-gold.jpg",
        "https://cdn.shopify.com/clamssleeptalksmall.jpg",
        "https://cdn.shopify.com/clamssleeptalksmall.jpg",
    ]


@respx.mock
async def test_crawl_catalog_emits_a_row_with_no_price_rather_than_dropping_it(crawler):
    _mock_single_page([_MALFORMED_PRICE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 1
    assert items[0]["price"] is None


@respx.mock
async def test_crawl_catalog_paginates_until_a_page_comes_back_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_USED_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Ben Pirani", "Wire"]


@respx.mock
async def test_crawl_catalog_drops_a_count_prefixed_cd_descriptor(crawler):
    _mock_single_page([_COUNT_PREFIXED_CD_PRODUCT, _COUNT_PREFIXED_BOX_SET_PRODUCT,
                       _COUNT_PREFIXED_MISTYPED_CD_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_a_vinyl_box_set_with_count_prefixed_bonus_discs(crawler):
    _mock_single_page([_VINYL_BOX_SET_WITH_BONUS_DISCS])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Metallica", "ReLoad (Remastered Deluxe Box Set) 2xLP + 15xCD Box Set (180g Vinyl)"),
    ]


@respx.mock
async def test_crawl_catalog_does_not_bypass_availability_for_a_pre_order(crawler):
    # The sibling label crawlers bypass `available` on a pre-order because
    # their stores flag purchasable pre-orders unavailable. This store does
    # not -- every pre-order-marked product reports available -- so an
    # unavailable one most plausibly means the allocation is gone, and
    # publishing it would put an unbuyable record in front of a user at a
    # price. darksiderecords.py pins the same decision.
    sold_out_preorder = {**_PREORDER_PRODUCT, "variants": [
        {"title": "Default Title", "price": "32.99", "available": False, "featured_image": None},
    ]}
    _mock_single_page([sold_out_preorder])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_does_not_let_ep_vouch_for_a_cd(crawler):
    _mock_single_page([_CD_EP_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_keeps_a_record_plus_cassette_bundle(crawler):
    _mock_single_page([_VINYL_PLUS_CASSETTE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Impiety", 'Ascension 1991 7" + Cassette (Diehard Edition 58/130) (Used)'),
    ]


@respx.mock
async def test_crawl_catalog_puts_the_variant_descriptor_after_the_status_marker(crawler):
    # No live product is both marked and multi-variant, so this composition
    # order exists only here. Both back positions match the catalog the same
    # way -- db._library_match_fragment is exact-or-prefix-with-space, and only
    # the front position breaks it -- so this pins a readability decision, not
    # a correctness one: "— {variant}" is terminal across the fleet, and the
    # marker describes the product while the variant names the pressing.
    marked_multi_variant = {
        **_MULTI_VARIANT_PRODUCT,
        "title": 'Used Vinyl: Shannon and The Clams "Sleep Talk" LP',
    }
    _mock_single_page([marked_multi_variant])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Sleep Talk LP (Used) — Yellow & Blue Split with Gold Splatter",
        "Sleep Talk LP (Used) — Metallic Gold",
        "Sleep Talk LP (Used) — Metallic Silver",
    ]


@respx.mock
async def test_crawl_catalog_falls_back_to_variant_id_for_blank_descriptors(crawler):
    # Without the fallback both rows read "… — " and collapse onto one
    # item_key: db.compute_item_key() hashes (artist, title, url) and the url
    # is per-product, and db.replace_stock_items() INSERTs with no ON CONFLICT
    # guard. Not a shape this store currently produces.
    blank_descriptors = {**_MULTI_VARIANT_PRODUCT, "variants": [
        {"id": 42432112230600, "title": "", "price": "7.99", "available": True,
         "featured_image": None},
        {"id": 42432112263368, "title": None, "price": "8.99", "available": True,
         "featured_image": None},
    ]}
    _mock_single_page([blank_descriptors])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Sleep Talk LP — 42432112230600",
        "Sleep Talk LP — 42432112263368",
    ]
    assert len({(i["artist"], i["title"], i["url"]) for i in items}) == 2


@respx.mock
async def test_crawl_catalog_falls_back_to_variant_id_for_placeholder_descriptors(crawler):
    # Shopify only issues "Default Title" for a single-variant product, so a
    # multi-variant product carrying one is malformed and must not be trusted
    # as a descriptor.
    placeholder_descriptors = {**_MULTI_VARIANT_PRODUCT, "variants": [
        {"id": 111, "title": "Default Title", "price": "7.99", "available": True,
         "featured_image": None},
        {"id": 222, "title": "Default", "price": "8.99", "available": True,
         "featured_image": None},
    ]}
    _mock_single_page([placeholder_descriptors])
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == [
        "Sleep Talk LP — 111",
        "Sleep Talk LP — 222",
    ]


@respx.mock
async def test_crawl_catalog_raises_when_no_stable_variant_identity_exists(crawler):
    # Raising leaves the previous snapshot intact -- _sync_stock records the
    # site as failed and skips replace_stock_items -- rather than replacing
    # good stock with rows that collide on one item_key.
    no_identity = {**_MULTI_VARIANT_PRODUCT, "variants": [
        {"title": "", "price": "7.99", "available": True, "featured_image": None},
        {"title": "", "price": "8.99", "available": True, "featured_image": None},
    ]}
    _mock_single_page([no_identity])
    with pytest.raises(ValueError, match="cannot derive distinct item keys"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_no_vinyl_title_parses(crawler):
    # db.replace_stock_items() DELETEs this crawler's rows before inserting and
    # _sync_stock only skips it when the crawl raised, so returning empty on a
    # title-convention change would wipe the store's whole snapshot and record
    # the site as healthy. dischordrecords.py and sideonedummyrecords.py raise
    # on the same class of drift.
    drifted = [
        {**_PRODUCT, "title": "Ben Pirani / How Do I Talk To My Brother / LP"},
        {**_NUMERIC_ALBUM_PRODUCT, "title": "Adele / 19 / LP"},
    ]
    _mock_single_page(drifted)
    with pytest.raises(RuntimeError, match="title convention has drifted"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_the_collection_returns_nothing(crawler):
    # A renamed or removed collection, which would otherwise wipe the snapshot
    # just as quietly as a parse failure.
    _mock_single_page([])
    with pytest.raises(RuntimeError, match="returned no products at all"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_crawl_catalog_does_not_raise_when_the_catalog_is_sold_out(crawler):
    # The state the drift guard must not mistake for drift: everything parses,
    # nothing is available. Yielding zero rows here is the truth, so the sync
    # should replace the snapshot rather than abort.
    _mock_single_page([_UNAVAILABLE_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_does_not_raise_when_every_product_is_a_mistyped_cd(crawler):
    # Also not drift: these parse fine and are rejected by the format filter,
    # which is the filter working rather than the title convention changing.
    _mock_single_page([_MISTYPED_CD_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_does_not_raise_when_no_product_is_vinyl_typed(crawler):
    _mock_single_page([_CASSETTE_TYPED_PRODUCT])
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


def test_site_metadata(crawler):
    assert crawler.site_name == "1-2-3-4 Go! Records"
    assert crawler.base_url == "https://1234gorecords.shop"
    assert crawler.crawler_type == "catalog"
    assert crawler.genre == "marketplace"
    assert crawler.genre_summary
