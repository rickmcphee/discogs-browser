import httpx
import respx
import pytest
from crawlers.iodinerecords import Crawler

_PRODUCTS_URL = "https://iodinerecords.com/collections/all/products.json"

# Fixtures marked "captured" are live products fetched from the store on
# 2026-09-06, trimmed to the fields the crawler reads (image URLs shortened).
# Ones marked "altered" are captured products with one field changed to reach
# a branch the live data never takes; "invented" products exercise guards the
# live catalog cannot -- each says so at its definition.

# Captured: the store's dominant shape -- vendor is the label, the artist is
# in the title as `Artist 'Album'`, the CD sits beside the colour pressings
# as a sibling variant, each pressing names its format ("12\" Vinyl") and
# carries its own featured image, and the product is tagged both `Vinyl` and
# `format:12"`.
_TALES_PRODUCT = {
    "title": "Piebald 'Tales For The Rages'",
    "vendor": "Iodine Recordings",
    "handle": "piebald-tales-for-the-rages-lp",
    "product_type": "Records",
    "tags": ["format:12\"", "format:cd", "format:lp", "Iodine Recordings", "LP", "Piebald", "Tales For The Rages", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/IOD87_pack.jpg"}],
    "variants": [
        {"id": 43929063096491, "title": "Noise Cult Splatter 12\" Vinyl", "price": "24.99",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/IOD87V1.jpg"}},
        {"id": 43929063129259, "title": "Fun Destroyer Tri-Stripe 12\" Vinyl", "price": "24.99",
         "available": False, "featured_image": {"src": "https://cdn.shopify.com/IOD87V2.jpg"}},
        {"id": 43929063162027, "title": "Sky Blue 12\" Vinyl", "price": "24.99",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/IOD87V3.jpg"}},
        {"id": 43929063194795, "title": "CD", "price": "11.99",
         "available": True, "featured_image": {"src": "https://cdn.shopify.com/IOD87CD.jpg"}},
    ],
}

# Captured: colour-only variant titles that name no format at all, beside a
# CD and a cassette; the artist carries a non-ASCII letter.
_SWORD_PRODUCT = {
    "title": "NØ MAN 'Between The Sword and The Neck'",
    "vendor": "Iodine Recordings",
    "handle": "no-man-between-the-sword-and-the-neck",
    "product_type": "Records",
    "tags": ["Between The Sword and The Neck", "format:12\"", "format:cassette", "format:cd", "format:lp", "Iodine Recordings", "LP", "NØ MAN", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/IOD101_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Noise Cult Splatter", "price": "25.00", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/IOD101V1.jpg"}},
        {"id": 2, "title": "Blood and Paper Stripes", "price": "25.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD101V2.jpg"}},
        {"id": 3, "title": "CD", "price": "12.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD101CD.jpg"}},
        {"id": 4, "title": "Cassette Tape", "price": "12.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD101TAPE.jpg"}},
    ],
}

# Captured: a pre-order, tagged and available, with an apostrophe inside the
# artist's name and a question mark inside the album's; the other pressing is
# sold out.
_GIRL_PRODUCT = {
    "title": "Her Head's on Fire 'Am I Not Your Girl?'",
    "vendor": "Iodine Recordings",
    "handle": "her-heads-on-fire-am-i-not-your-girl",
    "product_type": "Records",
    "tags": ["Am I Not Your Girl?", "format:12\"", "format:lp", "Her Head's On Fire", "Iodine Recordings", "LP", "New Release", "preorder", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/IOD97_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Noise Cult Splatter 12\" Vinyl", "price": "24.99", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/IOD97V1.jpg"}},
        {"id": 2, "title": "Nightmare Swirl 12\" Vinyl", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD97V2.jpg"}},
    ],
}

# Captured: the album title carries an apostrophe inside the quotes, and
# the product carries no variant images.
_SACRED_PRODUCT = {
    "title": "New Forms 'Nothing's Sacred Anymore'",
    "vendor": "Iodine Recordings",
    "handle": "new-forms-nothings-sacred-anymore",
    "product_type": "Records",
    "tags": ["EP", "format:12\"", "Iodine Recordings", "New Forms", "New Release", "Nothing's Sacred Anymore", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/IOD96_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Sacred Splatter 12\" Vinyl", "price": "21.99", "available": False, "featured_image": None},
        {"id": 2, "title": "Burning Leaves Yellow 12\" Vinyl", "price": "21.99", "available": True, "featured_image": None},
    ],
}

# Captured: one of the two products that quote the album with double quotes.
_FRIENDS_PRODUCT = {
    "title": "Gameface \"All My Friends\"",
    "vendor": "Iodine Recordings",
    "handle": "gameface-all-my-friends",
    "product_type": "Records",
    "tags": ["All My Friends", "format:7\"", "Gameface", "Iodine Recordings", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/IOD102_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Classic Black 7\" Vinyl", "price": "11.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD102V3.jpg"}},
    ],
}

# Captured: an edition after the closing quote, and an apostrophe inside the
# quoted album ("Weren't") that must not close it early. Sold separately
# from the Standard Edition product of the same album.
_VENETIAN_DELUXE_PRODUCT = {
    "title": "Piebald 'If It Weren't For Venetian Blinds It Would Be Curtains For Us All' Deluxe Edition",
    "vendor": "Iodine Recordings",
    "handle": "piebald-venetian-blinds-deluxe-lp",
    "product_type": "Records",
    "tags": ["Big Wheel Recreation", "format:12\"", "format:2xlp", "If It Weren't For Venetian Blinds It Would Be Curtains For Us All", "Piebald", "Reissue", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/IOD82DLX_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Sea/Orchid Splatter Deluxe Double LP", "price": "34.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD82DLXV1.jpg"}},
    ],
}

# Captured: Shopify's single-variant placeholder, a parenthesised edition,
# no `Vinyl` tag (only `format:2xlp`), and no variant image.
_SLIP_DELUXE_PRODUCT = {
    "title": "Quicksand 'Slip' (Deluxe)",
    "vendor": "Iodine Recordings",
    "handle": "quicksand-slip-deluxe",
    "product_type": "Records",
    "tags": ["format:2xlp", "Iodine Recordings", "Quicksand", "Reissue", "Slip"],
    "images": [{"src": "https://cdn.shopify.com/IOD30DLX_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Default Title", "price": "39.99", "available": True, "featured_image": None},
    ],
}

# Captured: the store's newer tag taxonomy -- `artist:[...]`, `album:[...]`,
# `format:12"` and no `Vinyl` tag -- with colour-only variant titles.
_PARALLEL_PRODUCT = {
    "title": "Hundreds of AU 'Life In Parallel'",
    "vendor": "Iodine Recordings",
    "handle": "hundreds-of-au-life-in-parallel",
    "product_type": "Records",
    "tags": ["album:[life-in-parallel]", "artist:[hundreds-of-au]", "channel:iodine", "format:12\"", "status: new release"],
    "images": [{"src": "https://cdn.shopify.com/IOD80_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Noise Cult Splatter", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD80V1.jpg"}},
        {"id": 2, "title": "Orange and Red Mosaic", "price": "24.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD80V2.jpg"}},
    ],
}

# Captured: a distro title vendored to another label, with a record-plus-DVD
# variant beside a sold-out plain pressing.
_061502_PRODUCT = {
    "title": "Botch '061502'",
    "vendor": "Hydra Head Records",
    "handle": "botch-061502",
    "product_type": "Records",
    "tags": ["061502", "Botch", "Distro", "format:2xlp", "Hydra Head Records", "Vinyl"],
    "images": [{"src": "https://cdn.shopify.com/HH086_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Black 12\" Vinyl", "price": "24.99", "available": False,
         "featured_image": {"src": "https://cdn.shopify.com/HH086v1.jpg"}},
        {"id": 2, "title": "Black 12\" Vinyl + DVD", "price": "29.99", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/HH086v-dvd.jpg"}},
    ],
}

# Captured: a CD-only record, typed Records and tagged `format:cd` only.
_LIFELINE_PRODUCT = {
    "title": "Jesu 'Lifeline'",
    "vendor": "Iodine Recordings",
    "handle": "jesu-lifeline",
    "product_type": "Records",
    "tags": ["Distro", "format:cd", "Hydra Head Records", "Jesu", "Lifeline"],
    "images": [{"src": "https://cdn.shopify.com/HH127_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "CD", "price": "7.99", "available": True, "featured_image": None},
    ],
}

# Captured: a cassette-only record.
_LIGHT_TOWER_PRODUCT = {
    "title": "Light Tower 'Light Tower'",
    "vendor": "Iodine Recordings",
    "handle": "light-tower-light-tower",
    "product_type": "Records",
    "tags": ["format:cassette", "Iodine Recordings", "Light Tower"],
    "images": [{"src": "https://cdn.shopify.com/IOD37_pack.jpg"}],
    "variants": [
        {"id": 1, "title": "Cassette Tape", "price": "10.00", "available": True,
         "featured_image": {"src": "https://cdn.shopify.com/IOD37TAPE.jpg"}},
    ],
}

# Captured: the one Records-typed product whose title quotes nothing -- the
# label's annual subscription. Its variants name no medium either.
_SUBSCRIPTION_PRODUCT = {
    "title": "Iodine Noise Cult Vol. 5 (Annual)",
    "vendor": "Iodine Recordings",
    "handle": "iodine-noise-cult-vol-5-annual",
    "product_type": "Records",
    "tags": ["Noise Cult", "Subscription"],
    "images": [{"src": "https://cdn.shopify.com/NCV5.jpg"}],
    "variants": [
        {"id": 1, "title": "Records Only", "price": "300.00", "available": True, "featured_image": None},
        {"id": 2, "title": "w/ T-Shirt", "price": "325.00", "available": True, "featured_image": None},
    ],
}

# Captured: merch whose title quotes a name exactly the way the records do.
_TEE_PRODUCT = {
    "title": "Piebald 'Pegasus' T-Shirt",
    "vendor": "Iodine Recordings",
    "handle": "piebald-pegasus-t-shirt",
    "product_type": "Merch",
    "tags": ["format:merch", "Merch", "NO-MEDIA-MAIL", "Piebald", "T-Shirts", "Tales For The Rages", "type:t-shirt"],
    "images": [{"src": "https://cdn.shopify.com/IOD87TEE2.jpg"}],
    "variants": [
        {"id": 1, "title": "S", "price": "29.99", "available": True, "featured_image": None},
        {"id": 2, "title": "M", "price": "29.99", "available": True, "featured_image": None},
    ],
}

# Captured: a book.
_BOOK_PRODUCT = {
    "title": "100 Words or Less: Interviews from Independent Culture",
    "vendor": "Iodine Recordings",
    "handle": "100-words-or-less",
    "product_type": "Books",
    "tags": ["book", "edition:book", "format:book", "New Release"],
    "images": [{"src": "https://cdn.shopify.com/IOP02.jpg"}],
    "variants": [
        {"id": 1, "title": "Default Title", "price": "25.00", "available": True, "featured_image": None},
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
    _mock_pages(_TALES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Piebald",
        "title": "Tales For The Rages — Sky Blue 12\" Vinyl",
        "format": "Vinyl",
        "price": 24.99,
        "currency": "USD",
        "url": "https://iodinerecords.com/products/piebald-tales-for-the-rages-lp",
        "cover_image_url": "https://cdn.shopify.com/IOD87V3.jpg",
    }]


@respx.mock
async def test_only_the_record_variants_of_a_product_yield(crawler):
    # The CD and the cassette are skipped by the variant gate, the sold-out
    # colour by the availability filter; the colour that names no format is
    # a record because the product is tagged as one.
    _mock_pages(_SWORD_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("NØ MAN", "Between The Sword and The Neck — Blood and Paper Stripes")]


def test_music_type_gate():
    # Enumerated, so that a type the store might add for non-music stays out
    # by default.
    for ptype in ["Records", "records", " RECORDS "]:
        assert Crawler._items({**_TALES_PRODUCT, "product_type": ptype}), ptype
    for ptype in ["Merch", "Books", "", "  ", "Record", "Records - Vinyl", "Bundle", None]:
        assert Crawler._items({**_TALES_PRODUCT, "product_type": ptype}) == [], ptype


@pytest.mark.parametrize("title,expected", [
    ("Piebald 'Tales For The Rages'", ("Piebald", "Tales For The Rages")),
    ("Gameface \"All My Friends\"", ("Gameface", "All My Friends")),
    ("Her Head's on Fire 'Am I Not Your Girl?'", ("Her Head's on Fire", "Am I Not Your Girl?")),
    ("New Forms 'Nothing's Sacred Anymore'", ("New Forms", "Nothing's Sacred Anymore")),
    ("Stephen Brodsky 'Stephen Brodsky's Octave Museum'", ("Stephen Brodsky", "Stephen Brodsky's Octave Museum")),
    ("Piebald 'If It Weren't For Venetian Blinds It Would Be Curtains For Us All' Deluxe Edition",
     ("Piebald", "If It Weren't For Venetian Blinds It Would Be Curtains For Us All Deluxe Edition")),
    ("Quicksand 'Slip' (Deluxe)", ("Quicksand", "Slip (Deluxe)")),
    ("Quicksand 'Manic Compression' Deluxe Book", ("Quicksand", "Manic Compression Deluxe Book")),
    ("The Saddest Landscape 'Alone With Heaven' Deluxe 2xLP", ("The Saddest Landscape", "Alone With Heaven Deluxe 2xLP")),
    ("Candy Hearts 'You Could Be Anyone' (Rarities 2010–2016)", ("Candy Hearts", "You Could Be Anyone (Rarities 2010–2016)")),
    ("Drowningman 'Busy Signal At The Suicide Hotline (2012)'", ("Drowningman", "Busy Signal At The Suicide Hotline (2012)")),
    ("Garrison & Orange Island 'Songs From A Central Massachusetts Mill Town'",
     ("Garrison & Orange Island", "Songs From A Central Massachusetts Mill Town")),
    ("Bucket Full Of Teeth 'I / II / III / IV'", ("Bucket Full Of Teeth", "I / II / III / IV")),
    ("Audio Karate '¡OTRA!'", ("Audio Karate", "¡OTRA!")),
    ("Hassan I Sabbah 'Untitled'", ("Hassan I Sabbah", "Untitled")),
    ("Attempt Survivors ‘Educated Hips’", ("Attempt Survivors", "Educated Hips")),
    ("Attempt Survivors “Educated Hips”", ("Attempt Survivors", "Educated Hips")),
    ("  Piebald   'Tales  For The Rages'  ", ("Piebald", "Tales For The Rages")),
    ("Iodine Noise Cult Vol. 5 (Annual)", ("", "")),
    ("Piebald - Tales For The Rages", ("", "")),
    ("'Tales For The Rages'", ("", "")),
    ("Piebald ''", ("", "")),
    ("Piebald 'Tales For The Rages", ("", "")),
    ("Rock'n'Roll Band 'Album'", ("Rock'n'Roll Band", "Album")),
    ("", ("", "")),
    (None, ("", "")),
])
def test_artist_and_album_come_from_the_quoted_title(title, expected):
    # Every shape is captured from the live catalog except the typographic
    # quotes, the whitespace, "Rock'n'Roll" and the malformed forms, which
    # pin the structural rule: a quote opens only after whitespace and closes
    # only before whitespace or the end, so apostrophes on either side never
    # delimit.
    assert Crawler._artist_album(title) == expected


@pytest.mark.parametrize("tags", [
    ["Vinyl"], ["vinyl"], [" VINYL "], ["format:12\""], ["format:7\""], ["format:10\""], ["format:lp"],
    ["format:2xlp"], ["format:3xlp"], ["format: 12\""], ["FORMAT:LP"], ["format:12-inch"],
    ["format:cd", "format:lp"], ["format:cassette", "Vinyl"],
])
def test_product_format_gate_admits_a_vinyl_tag(tags):
    assert Crawler._is_vinyl_product({"tags": tags}), tags


@pytest.mark.parametrize("tags", [
    [], ["format:cd"], ["format:cassette"], ["format:merch"], ["format:book"], ["format:digital"],
    ["Vinyl Exclusives"], ["Piebald", "Reissue", "Iodine Recordings"], ["format:lps and cds"],
    [None, ""],
])
def test_product_format_gate_rejects_everything_else(tags):
    assert not Crawler._is_vinyl_product({"tags": tags}), tags


@pytest.mark.parametrize("title", [
    "Sky Blue 12\" Vinyl", "Black 3xLP Vinyl", "Noise Cult Splatter 2xLP", "12\" LP / Lemonheads Yellow",
    "Black 12\" Vinyl + DVD", "12\" Vinyl + DVD", "12\" + DVD", "2 LPs + CD", "LP + CD", "10 inch",
    "12-inch", "\"Any Color But Yellow\" 12\" Vinyl", "Sea/Orchid Splatter Deluxe Double LP",
    "Coke Bottle", "Silver", "Blood and Paper Stripes", "Swamp & Red Manic Splatter (F&F)",
    "Heart + Fire Smash", "Random Color 7\" Vinyl", "Picture Disc", "Records Only",
])
def test_variant_gate_admits_records_and_colour_names(title):
    assert Crawler._is_vinyl_variant(title), title


@pytest.mark.parametrize("title", [
    "CD", "cd", "2xCD", "2CD", "CD / Standard CD", "Cassette Tape", "Cassette", "Tape", "DVD", "Blu-ray",
    "Digital Download", "Digipak CD", "MC", "w/ T-Shirt", "T-Shirt - S", "Hoodie", "12\" x 12\" Poster",
    "Tote Bag", "Enamel Pin", "Sticker",
])
def test_variant_gate_rejects_other_media_and_merch(title):
    # Negative rather than positive, because the product layer has already
    # said this is a record: only a title naming another medium says
    # otherwise. The poster pins the check order -- a dimension is not a
    # format claim.
    assert not Crawler._is_vinyl_variant(title), title


@respx.mock
async def test_colour_only_pressings_yield_when_the_product_is_tagged_vinyl(crawler):
    _mock_pages(_PARALLEL_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Life In Parallel — Noise Cult Splatter",
                                           "Life In Parallel — Orange and Red Mosaic"]


@respx.mock
async def test_colour_only_pressings_do_not_yield_without_a_vinyl_tag(crawler):
    # Altered: the vinyl tags stripped. A colour name is only a pressing
    # because the product says it is a record; without that, nothing does.
    _mock_pages({**_PARALLEL_PRODUCT, "tags": ["channel:iodine"]}, _TALES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Piebald"]


@respx.mock
async def test_format_tag_admits_a_product_without_the_vinyl_tag(crawler):
    # Captured: `format:2xlp` and no `Vinyl`; the newer taxonomy products
    # are the same shape with `format:12"`.
    _mock_pages(_SLIP_DELUXE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Slip (Deluxe)"]


@respx.mock
async def test_placeholder_variant_row_is_the_title_alone(crawler):
    _mock_pages(_SLIP_DELUXE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == [{
        "artist": "Quicksand",
        "title": "Slip (Deluxe)",
        "format": "Vinyl",
        "price": 39.99,
        "currency": "USD",
        "url": "https://iodinerecords.com/products/quicksand-slip-deluxe",
        "cover_image_url": "https://cdn.shopify.com/IOD30DLX_pack.jpg",
    }]


@respx.mock
async def test_pressing_is_appended_on_a_single_variant_product(crawler):
    # Always appended when the variant names a pressing, not only when the
    # product has siblings: the pressing is part of the row's identity, and
    # that identity must not depend on whether a CD sibling is listed.
    _mock_pages(_FRIENDS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["All My Friends — Classic Black 7\" Vinyl"]


@respx.mock
async def test_delisting_a_cd_sibling_does_not_change_the_vinyl_row_identity(crawler):
    # The failure the always-append rule prevents: keyed on the variant
    # count, this row would be "Tales For The Rages" with the CD listed and
    # "Tales For The Rages — Sky Blue 12\" Vinyl" without it.
    _mock_pages(_TALES_PRODUCT, _one_pressing({**_TALES_PRODUCT, "handle": "tales-2"}, index=2))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Tales For The Rages — Sky Blue 12\" Vinyl"] * 2


@respx.mock
async def test_edition_after_the_closing_quote_stays_on_the_album(crawler):
    _mock_pages(_VENETIAN_DELUXE_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [(
        "Piebald",
        "If It Weren't For Venetian Blinds It Would Be Curtains For Us All Deluxe Edition — Sea/Orchid Splatter Deluxe Double LP",
    )]


@respx.mock
async def test_double_quoted_title_is_parsed(crawler):
    _mock_pages(_FRIENDS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Gameface"]


@respx.mock
async def test_apostrophes_on_either_side_of_the_quotes_do_not_delimit(crawler):
    _mock_pages(_GIRL_PRODUCT, _SACRED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [
        ("Her Head's on Fire", "Am I Not Your Girl? (Pre-Order) — Nightmare Swirl 12\" Vinyl"),
        ("New Forms", "Nothing's Sacred Anymore — Burning Leaves Yellow 12\" Vinyl"),
    ]


@respx.mock
async def test_vendor_is_never_the_artist(crawler):
    # The vendor is always a label -- the store's own or a distro's -- so a
    # product vendored to Hydra Head Records is still credited to Botch.
    _mock_pages(_061502_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [(i["artist"], i["title"]) for i in items] == [("Botch", "061502 — Black 12\" Vinyl + DVD")]


@respx.mock
async def test_records_product_without_a_quoted_title_is_skipped(crawler):
    # Captured: the subscription. Nothing in the payload names an act, so
    # the product is skipped rather than credited to the label.
    _mock_pages(_SUBSCRIPTION_PRODUCT, _TALES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Piebald"]


@respx.mock
async def test_merch_and_books_are_skipped_by_the_type_gate(crawler):
    # The tee's title quotes a name exactly as the records do, so the type
    # gate is what keeps it out, not the title parse.
    assert Crawler._artist_album(_TEE_PRODUCT["title"]) == ("Piebald", "Pegasus T-Shirt")
    _mock_pages(_TEE_PRODUCT, _BOOK_PRODUCT, _TALES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Piebald"]


@respx.mock
async def test_cd_only_and_cassette_only_records_yield_nothing(crawler):
    _mock_pages(_LIFELINE_PRODUCT, _LIGHT_TOWER_PRODUCT, _TALES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Piebald"]


@respx.mock
async def test_a_vinyl_tagged_product_whose_variants_all_name_another_medium_yields_nothing(crawler):
    # Altered: the `Vinyl` tag added to the CD-only record. The product
    # layer admits it and the variant layer rejects every variant.
    _mock_pages({**_LIFELINE_PRODUCT, "tags": _LIFELINE_PRODUCT["tags"] + ["Vinyl"]}, _TALES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Piebald"]


@respx.mock
async def test_preorder_tag_appends_suffix_before_the_pressing(crawler):
    _mock_pages(_GIRL_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Am I Not Your Girl? (Pre-Order) — Nightmare Swirl 12\" Vinyl"]


@respx.mock
async def test_preorder_tag_matching_is_case_insensitive(crawler):
    # Altered: tag re-cased. has_tag normalises, so the store re-casing its
    # own tag must not silently stop marking pre-orders.
    _mock_pages({**_GIRL_PRODUCT, "tags": ["Vinyl", "PreOrder"]})
    items = [item async for item in crawler.crawl_catalog()]
    assert all(" (Pre-Order) — " in i["title"] for i in items)


@respx.mock
async def test_no_preorder_availability_bypass(crawler):
    # Captured as-is: the pre-order's other pressing is sold out and stays
    # out. Every live pre-order reports available True on the pressing that
    # is for sale, so an unavailable one is gone allocation -- pinned so
    # reintroducing deathwishinc.py's bypass would have to be deliberate.
    _mock_pages(_one_pressing(_GIRL_PRODUCT, index=0))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_sold_out_product_is_skipped(crawler):
    _mock_pages(_one_pressing(_TALES_PRODUCT, index=0), _FRIENDS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Gameface"]


@respx.mock
async def test_junk_variant_entry_is_ignored(crawler):
    # Non-mapping entries are dropped before anything reads them, so one is
    # an ordinary skipped row rather than an AttributeError mid-walk.
    product = {**_TALES_PRODUCT, "variants": _TALES_PRODUCT["variants"] + ["not-a-variant", 7]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Tales For The Rages — Sky Blue 12\" Vinyl"]


@respx.mock
async def test_variant_title_whitespace_is_collapsed(crawler):
    # Altered: a double space inside the variant title, which would
    # otherwise carry into the row's identity.
    _mock_pages(_one_pressing(_TALES_PRODUCT, index=2, title="  Sky  Blue 12\" Vinyl "))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Tales For The Rages — Sky Blue 12\" Vinyl"]


@respx.mock
async def test_product_missing_its_handle_is_skipped(crawler):
    # Altered: handle blanked on one product beside a healthy one. It feeds
    # item_key through the URL, so a row without one would be emitted under
    # a fresh identity; the row is dropped instead.
    for blank in ("", "  ", None):
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
            return_value=_page_response([{**_TALES_PRODUCT, "handle": blank}, _FRIENDS_PRODUCT]))
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
            return_value=_page_response([]))
        items = [item async for item in crawler.crawl_catalog()]
        assert [i["artist"] for i in items] == ["Gameface"], blank


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: {k: v for k, v in p.items() if k != "handle"}, id="handle-key-gone"),
    pytest.param(lambda p: {**p, "handle": None}, id="handle-null"),
    pytest.param(lambda p: {**p, "handle": ""}, id="handle-blank"),
])
async def test_catalog_without_handles_raises(crawler, mutate):
    # Altered: handle gone from every record. Each product is skipped rather
    # than re-keyed, so without this guard the walk would complete
    # "successfully" empty and replace_stock_items() would delete the
    # snapshot as though the shelf had cleared.
    _mock_pages(mutate(_TALES_PRODUCT), mutate(_FRIENDS_PRODUCT))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_less_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages(_FRIENDS_PRODUCT, {**_TALES_PRODUCT, "handle": ""})
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Gameface"]


@respx.mock
async def test_a_sold_out_product_missing_its_handle_still_raises(crawler):
    # The identity tally is taken before the availability filter.
    _mock_pages(_one_pressing({**_TALES_PRODUCT, "handle": ""}, index=0))
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_handle_on_a_non_record_does_not_satisfy_the_identity_guard(crawler):
    # The tee has a handle; the only record has none. Tallied against every
    # product, the tee would vouch for it.
    _mock_pages(_TEE_PRODUCT, {**_TALES_PRODUCT, "handle": ""})
    with pytest.raises(RuntimeError, match="identity-source drift"):
        [item async for item in crawler.crawl_catalog()]


@pytest.mark.parametrize("raw", ["n/a", "", None, [], {}, float("nan"),
                                 float("inf"), "0", "-1", 0, -5, True, False])
def test_unusable_price_yields_none(raw):
    # Altered: price corrupted. bool and nan are the two a plain
    # float()-with-fallback lets through -- True would price a record at 1,
    # and nan is truthy, so it would reach the stock row and break JSON
    # serialisation downstream.
    items = Crawler._items(_one_pressing(_TALES_PRODUCT, index=2, price=raw))
    assert items[0]["price"] is None, raw


@pytest.mark.parametrize("raw,expected", [("24.99", 24.99), (24.99, 24.99), ("3.00", 3.0), ("99.99", 99.99)])
def test_usable_price_is_parsed(raw, expected):
    assert Crawler._items(_one_pressing(_TALES_PRODUCT, index=2, price=raw))[0]["price"] == expected


@respx.mock
async def test_missing_price_key_yields_none(crawler):
    # Altered: price key removed, alongside a healthy priced product. An
    # isolated null is tolerated; a catalog with no price anywhere raises.
    variant = {k: v for k, v in _TALES_PRODUCT["variants"][2].items() if k != "price"}
    _mock_pages({**_TALES_PRODUCT, "variants": [variant]}, _FRIENDS_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["price"] for i in items] == [None, 11.99]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda v: {k: x for k, x in v.items() if k != "price"}, id="price-key-gone"),
    pytest.param(lambda v: {**v, "price": None}, id="price-null"),
    pytest.param(lambda v: {**v, "price": "n/a"}, id="price-unparseable"),
    pytest.param(lambda v: {**v, "price": "0"}, id="price-zero"),
    pytest.param(lambda v: {**v, "amount": v["price"], "price": None}, id="price-renamed"),
])
async def test_a_catalog_that_yielded_rows_but_no_prices_raises(crawler, mutate):
    # Rows without the emptiness: `_price` answers None for a value it
    # cannot use, so a price field removed or retyped store-wide re-lists the
    # whole catalog with no prices, which is worse than the snapshot it
    # would replace.
    products = [
        {**_TALES_PRODUCT, "handle": f"unpriced-{i}", "variants": [mutate(_TALES_PRODUCT["variants"][2])]}
        for i in range(2)
    ]
    _mock_pages(*products)
    with pytest.raises(RuntimeError, match="price-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_one_priced_row_is_enough_to_satisfy_the_price_guard(crawler):
    unpriced = [_one_pressing({**_TALES_PRODUCT, "handle": f"unpriced-{i}"}, index=2, price="n/a")
                for i in range(5)]
    _mock_pages(_FRIENDS_PRODUCT, *unpriced)
    items = [item async for item in crawler.crawl_catalog()]
    assert len(items) == 6
    assert [i["price"] for i in items].count(None) == 5


@respx.mock
async def test_an_empty_catalog_does_not_trip_the_price_guard(crawler):
    _mock_pages(_one_pressing(_TALES_PRODUCT, index=0))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_variant_featured_image_wins_over_product_image(crawler):
    _mock_pages(_TALES_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] == "https://cdn.shopify.com/IOD87V3.jpg"


@respx.mock
async def test_cover_falls_back_to_product_image(crawler):
    # Captured: no variant images on this product.
    _mock_pages(_SACRED_PRODUCT)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["cover_image_url"] for i in items] == ["https://cdn.shopify.com/IOD96_pack.jpg"]


@respx.mock
async def test_cover_image_is_none_when_product_has_no_images(crawler):
    # Altered: images emptied; every live record has at least one.
    _mock_pages({**_SACRED_PRODUCT, "images": []})
    items = [item async for item in crawler.crawl_catalog()]
    assert items[0]["cover_image_url"] is None


@respx.mock
async def test_empty_collection_raises(crawler):
    _mock_pages()
    with pytest.raises(RuntimeError, match="no products"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_without_the_records_type_raises(crawler):
    # Altered: a catalog of merch and books only. Completing empty would
    # have replace_stock_items() delete the previous snapshot.
    _mock_pages(_TEE_PRODUCT, _BOOK_PRODUCT)
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_whose_records_have_no_quoted_title_raises(crawler):
    # Altered: the records renamed to the dash convention. The artist is read
    # out of the quoting, so every record is skipped while the type tally
    # stays non-zero -- artist-source drift.
    _mock_pages({**_TALES_PRODUCT, "title": "Piebald - Tales For The Rages"}, _SUBSCRIPTION_PRODUCT)
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_quoted_title_on_merch_does_not_satisfy_the_artist_guard(crawler):
    # The tee's title parses; the only record's does not. Counting parses
    # across every product would let the tee vouch for records that have
    # lost their artist source.
    _mock_pages(_TEE_PRODUCT, _SUBSCRIPTION_PRODUCT)
    with pytest.raises(RuntimeError, match="artist-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_catalog_whose_records_carry_no_vinyl_tag_raises(crawler):
    # Altered: the vinyl tags stripped from every record. The format is read
    # off the tags, so this is the guard that notices the store moving it
    # somewhere else -- every record would yield nothing while the type and
    # artist tallies stayed non-zero.
    _mock_pages({**_TALES_PRODUCT, "tags": ["Piebald", "LP", "Reissue"]}, _LIFELINE_PRODUCT)
    with pytest.raises(RuntimeError, match="format-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_vinyl_tag_on_a_product_without_an_artist_does_not_satisfy_the_format_guard(crawler):
    # Altered: the subscription tagged Vinyl. It can never yield, so it must
    # not vouch for records that have lost their tags.
    _mock_pages({**_SUBSCRIPTION_PRODUCT, "tags": ["Vinyl"]}, {**_TALES_PRODUCT, "tags": []})
    with pytest.raises(RuntimeError, match="format-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: {k: v for k, v in p.items() if k != "variants"}, id="variants-key-gone"),
    pytest.param(lambda p: {**p, "variants": None}, id="variants-null"),
    pytest.param(lambda p: {**p, "variants": []}, id="variants-empty"),
    pytest.param(lambda p: {**p, "variants": ["not-a-dict"]}, id="variant-not-a-mapping"),
    pytest.param(lambda p: {**p, "variants": [{"id": 1, "title": "CD", "price": "9.99", "available": True}]},
                 id="only-a-cd-variant"),
])
async def test_catalog_without_record_variants_raises(crawler, mutate):
    # A tagged record with no readable variant, or none the gate admits, is
    # a record with no pressing to sell -- named as format drift rather than
    # stock drift, and a raise either way.
    _mock_pages(mutate(_TALES_PRODUCT))
    with pytest.raises(RuntimeError, match="format-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda v: {k: x for k, x in v.items() if k != "available"}, id="available-key-gone"),
    pytest.param(lambda v: {**{k: x for k, x in v.items() if k != "available"}, "stock_status": "in_stock"},
                 id="available-renamed"),
    pytest.param(lambda v: {**v, "available": "false"}, id="available-string-false"),
    pytest.param(lambda v: {**v, "available": "true"}, id="available-string-true"),
    pytest.param(lambda v: {**v, "available": ""}, id="available-empty-string"),
    pytest.param(lambda v: {**v, "available": 0}, id="available-int-0"),
    pytest.param(lambda v: {**v, "available": 1}, id="available-int-1"),
    pytest.param(lambda v: {**v, "available": None}, id="available-null"),
])
async def test_catalog_without_a_readable_availability_flag_raises(crawler, mutate):
    # Altered: the field the availability filter reads, removed, renamed or
    # retyped on every record. Without this guard every product yields
    # nothing while every tally above it stays non-zero, and the walk
    # completes "successfully" empty. The string "false" is why this demands
    # the type rather than the key's presence: it is truthy, so a filter
    # reading truthiness would publish a sold-out record as in stock.
    _mock_pages({**_TALES_PRODUCT, "variants": [mutate(_TALES_PRODUCT["variants"][2])]})
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
@pytest.mark.parametrize("raw", ["false", "true", "", "0", 1, 0, None])
async def test_a_non_boolean_flag_is_never_emitted_as_in_stock(crawler, raw):
    # No guard is involved: the healthy row makes `yielded` non-zero, so the
    # outcome guard is skipped, and this is the per-variant filter rejecting
    # the malformed record on its own. Only the literal True admits one.
    _mock_pages(_FRIENDS_PRODUCT, _one_pressing(_TALES_PRODUCT, index=2, available=raw))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Gameface"], raw


@respx.mock
async def test_a_malformed_variant_does_not_hide_its_healthy_sibling(crawler):
    product = {**_TALES_PRODUCT, "variants": [
        {**_TALES_PRODUCT["variants"][2]},
        {**_TALES_PRODUCT["variants"][1], "available": "false"},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["title"] for i in items] == ["Tales For The Rages — Sky Blue 12\" Vinyl"]


@respx.mock
async def test_one_readable_sold_out_product_cannot_vouch_for_an_unreadable_catalog(crawler):
    # The case that defeats an "at least one readable" test, and the reason
    # the guard counts UNREADABLE products instead: one genuinely sold-out
    # record must not vouch for a catalog that has gone unreadable behind it.
    unreadable = [_one_pressing({**_TALES_PRODUCT, "handle": f"unreadable-{i}"}, index=2, available="false")
                  for i in range(3)]
    _mock_pages(_one_pressing(_FRIENDS_PRODUCT, available=False), *unreadable)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_sold_out_pressing_does_not_vouch_for_a_malformed_sibling(crawler):
    # every(), not any(): one pressing is a readable False and the other
    # carries the string "false", so the product yields nothing while an
    # any() test would count it readable as it does so.
    product = {**_TALES_PRODUCT, "variants": [
        {**_TALES_PRODUCT["variants"][0]},
        {**_TALES_PRODUCT["variants"][2], "available": "false"},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_cd_sibling_with_a_malformed_flag_does_not_make_the_record_unreadable(crawler):
    # Readability is judged over the record variants only, because only
    # they could have yielded: a sold-out LP beside a CD whose flag is junk
    # is a sold-out LP, and the walk completes empty as the truth.
    product = {**_TALES_PRODUCT, "variants": [
        {**_TALES_PRODUCT["variants"][0]},
        {**_TALES_PRODUCT["variants"][3], "available": "false"},
    ]}
    _mock_pages(product)
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_a_readable_cd_sibling_does_not_vouch_for_an_unreadable_record(crawler):
    product = {**_TALES_PRODUCT, "variants": [
        {**_TALES_PRODUCT["variants"][2], "available": "false"},
        {**_TALES_PRODUCT["variants"][3], "available": False},
    ]}
    _mock_pages(product)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_a_readable_flag_on_a_non_record_does_not_satisfy_the_stock_guard(crawler):
    # The tee is readable and the record is not; the tee could never have
    # yielded, so it must not vouch for the record's emptiness.
    _mock_pages(_TEE_PRODUCT, _one_pressing(_TALES_PRODUCT, index=2, available="false"))
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_two_products_cannot_each_satisfy_half_of_the_yield_guards(crawler):
    # A row needs the Records type, a quoted title, a vinyl tag and a
    # readable flag on ONE product. The first has an artist but no readable
    # flag; the second a readable flag but no artist. Tallied independently
    # both guards pass and the walk completes empty -- so the tallies are
    # nested.
    artist_no_flag = _one_pressing({**_TALES_PRODUCT, "handle": "artist-no-flag"}, index=2, available="false")
    flag_no_artist = {**_SUBSCRIPTION_PRODUCT, "tags": ["Vinyl"]}
    _mock_pages(artist_no_flag, flag_no_artist)
    with pytest.raises(RuntimeError, match="stock-source drift"):
        [item async for item in crawler.crawl_catalog()]


@respx.mock
async def test_an_unreadable_product_among_yielded_rows_does_not_raise(crawler):
    _mock_pages(_FRIENDS_PRODUCT, _one_pressing({**_TALES_PRODUCT, "handle": "unreadable"}, index=2, available="false"))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Gameface"]


@respx.mock
async def test_a_fully_readable_sold_out_product_completes_empty(crawler):
    _mock_pages({**_TALES_PRODUCT, "variants": _TALES_PRODUCT["variants"][:2]})
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_a_cleanly_sold_out_catalog_completes_empty(crawler):
    # The one case where emptiness is the truth: every record readable and
    # every one of them out of stock.
    _mock_pages({**_TALES_PRODUCT, "variants": _TALES_PRODUCT["variants"][:2]},
                _one_pressing(_FRIENDS_PRODUCT, available=False),
                _one_pressing(_061502_PRODUCT, index=0))
    items = [item async for item in crawler.crawl_catalog()]
    assert items == []


@respx.mock
async def test_crawl_catalog_paginates_until_empty(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([_TALES_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=_page_response([_FRIENDS_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=_page_response([]))
    items = [item async for item in crawler.crawl_catalog()]
    assert [i["artist"] for i in items] == ["Piebald", "Gameface"]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(crawler):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "Iodine Recordings"
    assert Crawler.base_url == "https://iodinerecords.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "punk"
    assert Crawler.genre_summary
