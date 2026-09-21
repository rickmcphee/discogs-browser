from xml.sax.saxutils import escape

import httpx
import pytest
import respx

from config import save_config
from crawlers.cookingvinyl import Crawler

_FEED_URL = "https://cookingvinyl.tmstor.es/productfeed"


@pytest.fixture(autouse=True)
def _unpaced(tmp_config_dir):
    """`crawl_catalog()` reads its pacing out of the config table, so every
    test here needs the table to exist. Autouse rather than a per-test
    parameter because every test in this file goes through `crawl_catalog()` --
    there are no parse-only helpers to test separately, the feed being read in
    one pass."""
    save_config({"crawl_delay_seconds": 0})


def _product(
    pid="114723",
    availability="in stock",
    artist="Altered Images",
    name="Clara Libre White Vinyl",
    price="25",
    currency="GBP",
    purl="https://cookingvinyl.tmstor.es/product/114723",
    imgurl="https://images.tmstor.es/cookingvinyl/114723-280c6a81.jpg",
    category="543523",
    omit=(),
):
    fields = [
        ("pid", pid),
        ("availability", availability),
        ("condition", "new"),
        ("desc", "Formed in Glasgow in 1979, Altered Images lsquoClara Librersquo"),
        ("imgurl", imgurl),
        ("purl", purl),
        ("artist", artist),
        ("name", name),
        ("price", price),
        ("currency", currency),
        ("google_product_category", category),
        ("release_date", "2023-04-22"),
        ("custom1", artist),
        ("category", "music"),
    ]
    body = "".join(
        f"<{tag}>{escape(value)}</{tag}>" for tag, value in fields
        if tag not in omit and value is not None
    )
    return f"<product>{body}</product>"


def _feed(*products):
    return (
        '<?xml version="1.0"?>'
        '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0" encoding="UTF-8">'
        f"<merchant>{''.join(products)}</merchant></rss>"
    )


async def _crawl(body, status=200):
    with respx.mock:
        route = respx.get(_FEED_URL).mock(return_value=httpx.Response(status, text=body))
        items = [item async for item in Crawler().crawl_catalog()]
    return items, route


# --- the happy path -------------------------------------------------------

async def test_yields_one_item_per_vinyl_product_with_every_field():
    items, _ = await _crawl(_feed(_product()))

    assert items == [{
        "artist": "Altered Images",
        "title": "Clara Libre White Vinyl",
        "format": "Vinyl",
        "price": 25.0,
        "currency": "GBP",
        "url": "https://cookingvinyl.tmstor.es/product/114723",
        "cover_image_url": "https://images.tmstor.es/cookingvinyl/114723-280c6a81.jpg",
    }]


async def test_title_is_the_feed_name_verbatim_pressing_and_all():
    items, _ = await _crawl(_feed(
        _product(name="Telephone Free Landslide Victory RSD 2025 Orange Marble (Store) Vinyl"),
    ))

    assert items[0]["title"] == "Telephone Free Landslide Victory RSD 2025 Orange Marble (Store) Vinyl"


async def test_xml_entities_in_the_name_are_decoded():
    # Written as the live feed writes them, not through the escaping helper:
    # the store emits `&amp;` and numeric character references for the
    # typographic punctuation in its titles.
    raw = (
        "<product><availability>in stock</availability>"
        "<imgurl>https://images.tmstor.es/cookingvinyl/167414.png</imgurl>"
        "<purl>https://cookingvinyl.tmstor.es/product/167414</purl>"
        "<artist>Sophie Ellis-Bextor</artist>"
        "<name>Sophie Ellis-Bextor&#x2019;s Kitchen Disco &#x2013; Live at The London "
        "Palladium RSD 2025 Gold &amp; Store Double Vinyl</name>"
        "<price>40</price><currency>GBP</currency></product>"
    )
    items, _ = await _crawl(_feed(raw))

    assert items[0]["title"] == (
        "Sophie Ellis-Bextor’s Kitchen Disco – Live at The London "
        "Palladium RSD 2025 Gold & Store Double Vinyl"
    )


async def test_sends_an_identifying_user_agent_rather_than_httpx_default():
    # Cloudflare blocklists `python-httpx/<version>` on this host, so a request
    # that leaves the default header in place gets a 403 interstitial.
    _, route = await _crawl(_feed(_product()))

    sent = route.calls[0].request.headers["user-agent"]
    assert "httpx" not in sent.lower()
    assert sent.startswith("DiscogsCollectionBrowser/")


# --- the format gate ------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Clara Libre White Vinyl",
    "Young As The Morning Old As The Sea LP",
    "True North Double Heavyweight 2LP",
    "Some Record 2xLP",
    "Some Record DLP",
    "Some Record LP2",
    'Some Record 12" Single',
    "Some Record 10 inch",
    "From Under Liquid Glass Picture Disc",
    "Shed Seven (Signed & Numbered) Test Pressing Vinyl",
])
async def test_admits_every_vinyl_naming_the_store_uses(name):
    items, _ = await _crawl(_feed(_product(name=name)))

    assert [item["title"] for item in items] == [name]


@pytest.mark.parametrize("name", [
    "True North CD",
    "A Matter of Time 2CD Album Deluxe CD",
    "Liquid Gold Cassette",
    "Heart To Mouth Digital Download",
    "Classic Logo T-Shirt (Black)",
    "Chasing Rainbows Anniversary Mug",
    "Ladies Shed Seven White Polo Shirt",
    "Red Football Scarf",
    "Rock Cap",
    "Key Live 2025 Tour Fridge Magnets",
    "Logo Sweatshirt - Blue",
])
async def test_rejects_products_that_are_not_records(name):
    # The feed is the whole shop, so a product not proven to be a record is
    # not one -- but the format guard must still fire rather than silently
    # emptying the snapshot, so pair each with one real record.
    items, _ = await _crawl(_feed(_product(name=name), _product(name="Real Record LP")))

    assert [item["title"] for item in items] == ["Real Record LP"]


@pytest.mark.parametrize("name", [
    # Joined with "+", naming a second medium.
    "Shed Seven Red Edition CD (Signed) + Vinyl (Signed)",
    "Shed Seven Digipak CD + Yellow Edition Vinyl + Cassette + T-Shirt",
    "Hometime Limited Edition Heavyweight Clear and Black Smoke Vinyl + 2CD Digipak",
    # Joined with "+", naming no second medium at all -- two records at one
    # price, which is a listing for neither.
    "A Matter of Time + Liquid Gold Red & Black Marble Vinyl Represses",
    # Joined with "&" -- only the medium word catches these.
    "True North 2LP Heavyweight Vinyl & CD",
    "True North 2LP Heavyweight Vinyl & Black T-Shirt",
    "True North 2LP Heavyweight Vinyl & Deluxe Download with Extended Booklet",
    # The medium carries a count glued to it, and the join is "&" or "/" --
    # neither a bundle marker. Nothing but the medium pattern can catch these,
    # and a pattern that cannot see a glued count publishes them at the bundle
    # price. The first four are live on the platform's flagship store.
    "Legend / Legend Extended (40th Anniversary Edition) Double Vinyl & 2CD",
    "The Journey - Part 3 Double LP & 2CD",
    "Tapping The Vein 3LP/2CD Deluxe Bookpack Boxset",
    'Harvest (50th Anniversary Edition) 2LP/7"/2DVD Boxset',
    "Hometime Heavyweight Vinyl & 2xCD Digipak",
    "Hometime Heavyweight Vinyl & 3 CD Digipak",
    "Some Album Vinyl & 2 x CD",
    "Some Album Vinyl & 2xCassette",
])
async def test_rejects_bundles_whichever_way_they_are_joined(name):
    items, _ = await _crawl(_feed(_product(name=name), _product(name="Real Record LP")))

    assert [item["title"] for item in items] == ["Real Record LP"]


@pytest.mark.parametrize("name", [
    "Changed Giver RSD 2024 Half White & Half Black Vinyl",
    "A Matter of Time Red & Black Marble Vinyl",
    "Liquid Gold Red & Black Marble Double Vinyl",
    "Shed Seven (Signed & Numbered) Test Pressing Vinyl",
])
async def test_keeps_records_whose_colour_is_written_with_an_ampersand(name):
    # "&" is not a bundle marker here: the store joins colours with it, and
    # every "&"-joined bundle names its second medium instead.
    items, _ = await _crawl(_feed(_product(name=name)))

    assert [item["title"] for item in items] == [name]


@pytest.mark.parametrize("name", [
    "Digital Ash In A Digital Urn Vinyl",
    "Bottle Cap Blues LP",
])
async def test_keeps_a_record_whose_title_holds_a_word_the_medium_list_leaves_out(name):
    # The medium test only ever sees a name that already named vinyl, so a
    # word in it costs every record whose title contains that word. "digital"
    # and "cap" never appear in a bundle, so they are not in the list.
    items, _ = await _crawl(_feed(_product(name=name)))

    assert [item["title"] for item in items] == [name]


# --- artist, price, availability -----------------------------------------

@pytest.mark.parametrize("artist", ["Various Artists", "various artists", "Various"])
async def test_collapses_the_various_artists_spellings_onto_discogs_own_name(artist):
    items, _ = await _crawl(_feed(_product(artist=artist)))

    assert items[0]["artist"] == "Various"


async def test_leaves_an_ordinary_artist_alone():
    items, _ = await _crawl(_feed(_product(artist="Camper Van Beethoven")))

    assert items[0]["artist"] == "Camper Van Beethoven"


@pytest.mark.parametrize("raw,expected", [
    ("25", 25.0),
    ("22.99", 22.99),
    ("", None),
    ("free", None),
    ("0", None),
    ("-1", None),
    ("nan", None),
    ("inf", None),
])
async def test_reads_a_usable_price_and_answers_none_for_the_rest(raw, expected):
    # One priced sibling, so the all-rows-unpriced guard stays out of the way.
    items, _ = await _crawl(_feed(
        _product(name="Under Test LP", price=raw),
        _product(name="Priced Sibling LP", price="19"),
    ))

    under_test = next(i for i in items if i["title"] == "Under Test LP")
    assert under_test["price"] == expected


async def test_reads_the_currency_per_product_rather_than_hardcoding_gbp():
    items, _ = await _crawl(_feed(_product(currency="EUR")))

    assert items[0]["currency"] == "EUR"


async def test_an_absent_currency_falls_back_to_the_stores_own_not_to_none():
    # frontend formatPrice() reads a null currency as USD, so None here would
    # put a dollar sign on a sterling price.
    items, _ = await _crawl(_feed(_product(omit=("currency",))))

    assert items[0]["currency"] == "GBP"


async def test_keeps_a_preorder_and_does_not_mark_it_in_the_title():
    items, _ = await _crawl(_feed(
        _product(availability="preorder", name="The Song Diaries NAD 2026 Turquoise Vinyl"),
    ))

    assert [item["title"] for item in items] == ["The Song Diaries NAD 2026 Turquoise Vinyl"]


async def test_drops_a_product_whose_availability_this_crawler_does_not_know():
    items, _ = await _crawl(_feed(
        _product(name="Unknown State LP", availability="on backorder"),
        _product(name="Real Record LP"),
    ))

    assert [item["title"] for item in items] == ["Real Record LP"]


async def test_skips_a_product_missing_its_identity_without_taking_the_walk_down():
    items, _ = await _crawl(_feed(
        _product(name="No Artist LP", omit=("artist",)),
        _product(name="Real Record LP"),
    ))

    assert [item["title"] for item in items] == ["Real Record LP"]


async def test_an_absent_image_becomes_none_rather_than_an_empty_string():
    items, _ = await _crawl(_feed(_product(omit=("imgurl",))))

    assert items[0]["cover_image_url"] is None


# --- the drift guards -----------------------------------------------------

async def test_raises_when_the_body_is_not_xml():
    # The shape of the Cloudflare challenge page this host serves everywhere
    # but here: HTML, with void elements XML cannot parse.
    with pytest.raises(RuntimeError, match="did not return XML"):
        await _crawl(
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            "<title>Just a moment...</title></head>"
            "<body>Enable JavaScript and cookies to continue</body></html>"
        )


async def test_raises_when_the_root_element_is_not_rss():
    with pytest.raises(RuntimeError, match="not <rss>"):
        await _crawl("<feed><entry/></feed>")


async def test_raises_when_the_rss_carries_no_merchant():
    with pytest.raises(RuntimeError, match="no <merchant>"):
        await _crawl('<rss version="2.0"><channel/></rss>')


async def test_raises_when_the_feed_carries_no_products():
    with pytest.raises(RuntimeError, match="carried no products"):
        await _crawl(_feed())


async def test_raises_when_no_product_names_a_vinyl_format():
    # A catalog of nothing but CDs and merchandise is format-naming drift, not
    # a sold-out shelf -- and completing empty would wipe the snapshot.
    with pytest.raises(RuntimeError, match="format-naming drift"):
        await _crawl(_feed(
            _product(name="True North CD"),
            _product(name="Classic Logo T-Shirt (Black)"),
        ))


async def test_raises_when_every_record_reads_as_a_bundle():
    # The rejection tests over-matching is a separate breakage from the store
    # renaming its formats, and it empties the walk the same way, so it gets
    # its own guard and its own message.
    with pytest.raises(RuntimeError, match="bundle-detection drift"):
        await _crawl(_feed(
            _product(name="Some Record LP + Art Print"),
            _product(name="Another Record Vinyl & CD"),
        ))


async def test_raises_when_nothing_yielded_and_a_record_lost_its_identity():
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _crawl(_feed(_product(name="No URL LP", omit=("purl",))))


async def test_raises_when_nothing_yielded_and_a_record_lost_its_availability():
    with pytest.raises(RuntimeError, match=r"unrecognised availability \(\(empty\)\)"):
        await _crawl(_feed(_product(name="No Availability LP", omit=("availability",))))


async def test_the_stock_guard_names_the_value_it_did_not_recognise():
    # A missing field and a value the platform has newly introduced both reach
    # this guard, and they send whoever reads it to different places -- so the
    # message reports the value rather than asserting the field was absent.
    with pytest.raises(RuntimeError, match=r"unrecognised availability \(on backorder\)"):
        await _crawl(_feed(_product(name="Backordered LP", availability="on backorder")))


async def test_raises_when_no_row_at_all_carries_a_price():
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _crawl(_feed(
            _product(name="First LP", price="tbc"),
            _product(name="Second LP", price=""),
        ))


async def test_tolerates_an_isolated_missing_price():
    items, _ = await _crawl(_feed(
        _product(name="Unpriced LP", price="tbc"),
        _product(name="Priced LP", price="19"),
    ))

    assert [(i["title"], i["price"]) for i in items] == [
        ("Unpriced LP", None),
        ("Priced LP", 19.0),
    ]


async def test_one_yielded_row_stands_the_identity_and_stock_guards_down():
    # Both guards speak only about a walk that came back with nothing at all,
    # so an isolated broken product beside a real one is an ordinary skip.
    items, _ = await _crawl(_feed(
        _product(name="No URL LP", omit=("purl",)),
        _product(name="No Availability LP", omit=("availability",)),
        _product(name="Real Record LP"),
    ))

    assert [item["title"] for item in items] == ["Real Record LP"]


async def test_an_http_error_propagates_rather_than_emptying_the_snapshot():
    with pytest.raises(httpx.HTTPStatusError):
        await _crawl("<html>forbidden</html>", status=403)
