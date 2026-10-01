import httpx
import respx
import pytest
from crawlers.revolver import Crawler

_PRODUCTS_URL = "https://shop.revolvermag.com/collections/all-vinyl/products.json"


def _product(title, product_type="LP", handle="h", variants=None, images=None, vendor="AEC"):
    # Titles below are captured from the live all-vinyl collection on
    # 2026-10-01; the surrounding fields are trimmed to what the crawler reads.
    return {
        "title": title,
        "vendor": vendor,
        "handle": handle,
        "product_type": product_type,
        "images": images if images is not None else [{"src": f"https://cdn.shopify.com/{handle}.jpg"}],
        "variants": variants if variants is not None else [
            {"title": "Default Title", "price": "30.99", "available": True}],
    }


_SMOKE = _product(
    "NICKELBACK 'EVERYTHING UNDER THE SUN' LP (Smoke Vinyl)",
    handle="nickelback-everything-under-the-sun-lp-smoke-vinyl")


def _mock_pages(*products):
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(200, json={"products": list(products)}))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=httpx.Response(200, json={"products": []}))


async def _crawl(*products):
    _mock_pages(*products)
    return [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_yields_item_fields():
    assert await _crawl(_SMOKE) == [{
        "artist": "NICKELBACK",
        "title": "EVERYTHING UNDER THE SUN LP (Smoke Vinyl)",
        "format": "Vinyl",
        "price": 30.99,
        "currency": "USD",
        "url": "https://shop.revolvermag.com/products/nickelback-everything-under-the-sun-lp-smoke-vinyl",
        "cover_image_url": "https://cdn.shopify.com/nickelback-everything-under-the-sun-lp-smoke-vinyl.jpg",
    }]


@pytest.mark.parametrize("title,expected", [
    ("NICKELBACK 'THE BEST OF NICKELBACK VOL. 1' 2LP",
     ("NICKELBACK", "THE BEST OF NICKELBACK VOL. 1", "2LP")),
    # Typographic quotes, opened with either of the store's spellings.
    ("NOTHING ‘A SHORT HISTORY OF DECAY’ LP (Sweetart Vinyl)",
     ("NOTHING", "A SHORT HISTORY OF DECAY", "LP (Sweetart Vinyl)")),
    ("THE ROLLING STONES ’12x5’ LP", ("THE ROLLING STONES", "12x5", "LP")),
    # An apostrophe in the artist is not an opening quote: it follows a letter.
    ("KING'S X 'MANIC MOONLIGHT' LP (Orange Vinyl)",
     ("KING'S X", "MANIC MOONLIGHT", "LP (Orange Vinyl)")),
    # Apostrophes before a space inside the album: the album is greedy, which a
    # lazy split gets wrong on every one of these live titles.
    ("KING GIZZARD & THE LIZARD WIZARD 'INFEST THE RATS' NEST' LP (Injection Molded Pet Ecosonic Vinyl)",
     ("KING GIZZARD & THE LIZARD WIZARD", "INFEST THE RATS' NEST", "LP (Injection Molded Pet Ecosonic Vinyl)")),
    ("BLACK SABBATH 'WE SOLD OUR SOUL FOR ROCK 'N' ROLL' 2LP",
     ("BLACK SABBATH", "WE SOLD OUR SOUL FOR ROCK 'N' ROLL", "2LP")),
    ("CLIPSE 'LET GOD SORT EM' OUT' LP (Deluxe Edition)",
     ("CLIPSE", "LET GOD SORT EM' OUT", "LP (Deluxe Edition)")),
    # An inch mark after the album is not a closing quote: it is a double one.
    ("CHAT PILE ‘MASKS’ 7” (Color Vinyl)", ("CHAT PILE", "MASKS", "7” (Color Vinyl)")),
    # Self-titled reads as the act's name, which is how Discogs titles it.
    ("QUICKSAND 'S/T' 7\" (Translucent Orange Vinyl)",
     ("QUICKSAND", "QUICKSAND", "7\" (Translucent Orange Vinyl)")),
])
def test_title_is_split_at_the_quoted_album(title, expected):
    assert Crawler._parse_title(title) == expected


@pytest.mark.parametrize("title", [
    "STAND BY ME SOUNDTRACK LP (Turquoise Blue Vinyl)",
    "PEARL JAM 'VITALOGY 2LP",
    "PAINTED SHIELD - PAINTED SHIELD LP",
    "",
])
def test_a_title_without_a_quoted_album_names_no_artist(title):
    assert Crawler._parse_title(title) is None


@respx.mock
async def test_vendor_is_never_the_artist():
    items = await _crawl(_product("GRAVEYARD 'FEVER' LP (Oxblood Vinyl)", vendor="WARNER"))
    assert [i["artist"] for i in items] == ["GRAVEYARD"]


@respx.mock
@pytest.mark.parametrize("product_type", ["LP", "7\"", "BOX SET", "Vinyl", "lp"])
async def test_vinyl_product_types_are_admitted(product_type):
    assert len(await _crawl(_product("SLAYER 'HELL AWAITS' 3LP", product_type=product_type))) == 1


@respx.mock
async def test_other_product_types_are_rejected():
    items = await _crawl(
        _SMOKE,
        # Captured: typed Bundle, though its title reads as a plain record.
        _product("MOTÖRHEAD 'NO SLEEP 'TIL HAMMERSMITH' LP (Clear w/ Copper & Black Swirls Vinyl)",
                 product_type="Bundle", handle="b"),
        # Altered: a vinyl-looking title, so only the type can reject it.
        _product("JOHN R MILLER 'THE GREAT UNKNOWING' LP", product_type="CD", handle="c"),
        _product("THE BLED 'HIS FIRST CRUSH' BUNDLE", product_type="", handle="d"),
        _product("HELD. 'X' T-SHIRT", product_type="Shirt", handle="e"),
        _product("REVOLVER 'X' MAGAZINE", product_type="Magazine", handle="f"),
    )
    assert [i["url"].rsplit("/", 1)[1] for i in items] == [_SMOKE["handle"]]


@pytest.mark.parametrize("title", [
    # Another medium typed as an LP.
    "SORXE 'MATTER & VOID' CD",
    "MY CHEMICAL ROMANCE 'DANGER DAYS: TRUE LIVES OF THE FABULOUS KILLJOYS' CASSETTE (Deluxe, Petrol Blue))",
    # EP names a length, not a medium, so it cannot vouch for another one.
    "X 'Y' CD EP",
    "X 'Y' CASSETTE EP (Red Shell)",
    # Merch bundles typed as an LP.
    "HEALTH ‘CONFLICT DLC’ LP (Exclusive – Limited to 400, Coke Bottle Clear Vinyl) + REVOLVER WINTER ISSUE",
    "MASTODON ‘BLOOD MOUNTAIN’ LP (Exclusive – Orange Vinyl) w/ SIGNED 12\"x12\" PAUL ROMANO PRINT",
    "THE LIVING '1982' WHITE LP & EXCLUSIVE T-SHIRT BUNDLE",
    "MIKE MCCREADY ‘FAREWELL TO SEASONS’ LP (Exclusive – Limited to 500, \"Poltergeist\" Vinyl) + 12\"x12\" DELUXE GRAPHIC NOVEL",
    # A poster's dimensions are not a record size, and a glued disc is a disc.
    "X 'Y' CD + 12\"x12\" POSTER",
    "X 'Y' CD + 12\" x 12\" POSTER",
    "X 'Y' CD + 12\" × 12\" POSTER",
    "X 'Y' (12\"CD)",
    "X 'Y' 7\"Cassette",
    # Merch that parses into the artist half.
    "PUSCIFER x Revolver Special Collector's Edition Magazine w/ 'Global Probing, Live from Prescott' 2LP (Coke Bottle Clear w/Black Smoke)",
])
def test_off_shelf_products_are_rejected(title):
    artist, _album, extra = Crawler._parse_title(title)
    assert Crawler._is_off_shelf(artist, extra)


@pytest.mark.parametrize("title", [
    # A record with a disc or a second record beside it is still a record.
    "SEPULTURA 'THE CLOUD OF UNKNOWING' EP + CD (Oxblood Vinyl)",
    "BAD RELIGION ‘THE DISSENT OF MAN’ LP + CD",
    "ALICE COOPER 'ROAD' 2LP + DVD",
    "SYSTEM OF A DOWN ‘TOXICITY’ 25TH ANNIVERSARY LP + 7\"",
    # A band can be named with a merch word; only a bundle joiner makes it merch.
    "PEEL DREAM MAGAZINE 'ROSE MAIN READING ROOM' LP",
    "X 'Y' 12\" + CD",
    # A disc count is not a dimension: two 12-inch records and a CD.
    "X 'Y' 2x12\" + CD",
    # Merch words inside the pressing bracket describe the vinyl.
    "KITTIE 'SPIT' LP (Leopard Print Vinyl)",
    "SPEED ‘ALL MY ANGELS’ EP (Beer Marble Vinyl w/B-Side Screen Print)",
    # A reissue is not a magazine issue: the bracket is not read for merch.
    "NECROPHOBIC 'DEATH TO ALL' LP (RE-ISSUE 2022)",
    # Packaging and inserts are not bundles.
    "MELVINS 'FIVE LEGGED DOG' 4LP WITH POSTER (Colored Vinyl)",
    "MOTORHEAD 'THE MANTICORE TAPES' 2LP (Deluxe Bookpack, Clear Vinyl + 7\")",
    # The album is never read for merch or media words.
    "ICE NINE KILLS 'EVERY TRICK IN THE BOOK' LP (Black Vinyl)",
    "HIM 'TEARS ON TAPE' LP (Clear Vinyl)",
    "MINOR THREAT 'FIRST DEMO TAPE' 7\"",
])
def test_records_are_not_off_shelf(title):
    artist, _album, extra = Crawler._parse_title(title)
    assert not Crawler._is_off_shelf(artist, extra)


@respx.mock
async def test_each_in_stock_pressing_of_a_multi_variant_product_yields():
    # Captured: colour pressings as variants, one sold out, and a zine sold
    # beside them on the same product.
    product = _product(
        "PRESIDENT ‘BLOOD OF YOUR EMPIRE’ LP (Exclusive – Various Color Vinyl)",
        handle="president",
        variants=[
            {"title": "Custard Tart", "price": "26.99", "available": True,
             "featured_image": {"src": "https://cdn.shopify.com/custard.jpg"}},
            {"title": "Brown & White Galaxy", "price": "26.99", "available": False},
            {"title": "President x Revolver Collector's Zine", "price": "5.99", "available": True},
        ])
    items = await _crawl(product)
    assert [(i["title"], i["price"], i["cover_image_url"]) for i in items] == [(
        "BLOOD OF YOUR EMPIRE LP (Exclusive – Various Color Vinyl) — Custard Tart",
        26.99, "https://cdn.shopify.com/custard.jpg")]


@respx.mock
async def test_placeholder_beside_real_variants_is_skipped():
    product = _product("X 'Y' LP", variants=[
        {"title": "Default Title", "price": "20", "available": True},
        {"title": "Red", "price": "20", "available": True}])
    assert [i["title"] for i in await _crawl(product)] == ["Y LP — Red"]


@respx.mock
@pytest.mark.parametrize("raw", ["true", 1, None])
async def test_only_a_literal_true_is_in_stock(raw):
    items = await _crawl(_SMOKE, _product("X 'Y' LP", handle="x", variants=[
        {"title": "Default Title", "price": "20", "available": raw}]))
    assert len(items) == 1


@pytest.mark.parametrize("raw,expected", [
    ("30.99", 30.99), (25, 25.0), (True, None), ("nan", None), ("inf", None),
    ("0", None), ("-1", None), ("", None), (None, None), (10 ** 400, None),
])
def test_price_parsing(raw, expected):
    assert Crawler._price({"price": raw}) == expected


@respx.mock
async def test_malformed_entries_are_skipped_not_raised():
    items = await _crawl(
        None, "junk", _SMOKE,
        _product("X 'Y' LP", handle="x", variants=[None, {"title": "Default Title", "price": "1", "available": True}]),
        {**_product("X 'Z' LP", handle="z"), "title": 7},
        {**_product("X 'W' LP", handle="w"), "images": [{"src": 123}], "variants": "bad"},
    )
    assert [i["url"].rsplit("/", 1)[1] for i in items] == [_SMOKE["handle"], "x"]


@respx.mock
async def test_cover_falls_back_to_the_product_image_and_rejects_non_strings():
    product = _product("X 'Y' LP", handle="x", images=[{"src": 5}, {"src": "https://cdn/p.jpg"}], variants=[
        {"title": "Default Title", "price": "1", "available": True, "featured_image": {"src": None}}])
    assert [i["cover_image_url"] for i in await _crawl(product)] == ["https://cdn/p.jpg"]


@respx.mock
async def test_crawl_catalog_paginates_until_empty():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(200, json={"products": [_SMOKE]}))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=httpx.Response(200, json={"products": [_product("X 'Y' LP", handle="x")]}))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(
        return_value=httpx.Response(200, json={"products": []}))
    assert len([i async for i in Crawler().crawl_catalog()]) == 2


# Guards: a completed-but-empty walk deletes the previous snapshot, so each
# way the payload can stop carrying what the crawler reads must raise.

@respx.mock
async def test_empty_collection_raises():
    with pytest.raises(RuntimeError, match="no products"):
        await _crawl()


@respx.mock
async def test_collection_without_a_vinyl_type_raises():
    with pytest.raises(RuntimeError, match="format-taxonomy drift"):
        await _crawl(_product("X 'Y' LP", product_type="Records"))


@respx.mock
async def test_collection_without_quoted_titles_raises():
    with pytest.raises(RuntimeError, match="artist-source drift"):
        await _crawl(_product("X - Y LP"))


@respx.mock
async def test_collection_of_nothing_but_bundles_and_other_media_raises():
    with pytest.raises(RuntimeError, match="format-source drift"):
        await _crawl(_product("X 'Y' LP + REVOLVER WINTER ISSUE"), _product("X 'Z' CD", handle="z"))


@respx.mock
async def test_collection_whose_records_lost_their_handle_raises():
    with pytest.raises(RuntimeError, match="identity-source drift"):
        await _crawl(_product("X 'Y' LP", handle=""))


@respx.mock
async def test_unreadable_stock_raises_even_beside_a_sold_out_record():
    with pytest.raises(RuntimeError, match="stock-source drift"):
        await _crawl(
            _product("X 'Y' LP", handle="y", variants=[{"title": "Default Title", "price": "1", "available": "true"}]),
            _product("X 'Z' LP", handle="z", variants=[{"title": "Default Title", "price": "1", "available": False}]),
        )


_SOLD_OUT = _product("X 'Z' LP", handle="sold-out", variants=[
    {"title": "Default Title", "price": "1", "available": False}])


@respx.mock
@pytest.mark.parametrize("variants", [
    "bad", None, [], [None], [{"price": "1", "available": True}],
    [{"title": "", "price": "1", "available": True}],
    # A readable sold-out pressing must not vouch for a malformed sibling.
    [{"title": "Red", "price": "1", "available": False}, None],
])
async def test_unreadable_variants_beside_a_sold_out_record_raise(variants):
    with pytest.raises(RuntimeError, match="variant-source drift"):
        await _crawl(_SOLD_OUT, {**_product("X 'Y' LP", handle="y"), "variants": variants})


@respx.mock
async def test_a_store_wide_variant_drift_is_diagnosed_as_such():
    with pytest.raises(RuntimeError, match="variant-source drift"):
        await _crawl(_product("X 'Y' LP", variants="bad"))


@pytest.mark.parametrize("title", ["Black 12\"x12\" Sleeve CD", "Black 12\" x 12\" Sleeve CD", "Black 12″ × 12″ Sleeve CD", "Red 12\"CD", "7\"Cassette"])
def test_variant_gate_does_not_read_a_glued_or_measured_inch_as_vinyl(title):
    product = _product("X 'Y' LP", variants=[{"title": title, "price": "1", "available": True}])
    assert Crawler._vinyl_variants(product) == []


@respx.mock
@pytest.mark.parametrize("bad", [
    None, "junk",
    {**_product("X 'Y' LP", handle="y"), "product_type": []},
    {**_product("X 'Y' LP", handle="y"), "product_type": None},
    {**_product("X 'Y' LP", handle="y"), "title": 7},
])
async def test_unreadable_products_beside_a_sold_out_record_raise(bad):
    with pytest.raises(RuntimeError, match="product-source drift"):
        await _crawl(_SOLD_OUT, bad)


@respx.mock
async def test_legitimately_excluded_products_beside_a_sold_out_record_complete_empty():
    # An empty type, a non-vinyl type and an unquoted string title are all
    # readable reasons to skip, not drift.
    assert await _crawl(
        _SOLD_OUT,
        _product("THE BLED 'HIS FIRST CRUSH' BUNDLE", product_type="", handle="a"),
        _product("X 'Y' CD", product_type="CD", handle="b"),
        _product("STAND BY ME SOUNDTRACK LP", handle="c"),
    ) == []


@respx.mock
async def test_unreadable_variants_among_yielded_rows_do_not_raise():
    items = await _crawl(_SMOKE, _product("X 'Y' LP", handle="y", variants="bad"))
    assert len(items) == 1


@respx.mock
async def test_a_cleanly_sold_out_collection_completes_empty():
    assert await _crawl(_product("X 'Y' LP", variants=[
        {"title": "Default Title", "price": "1", "available": False}])) == []


@respx.mock
async def test_rows_without_any_price_raise():
    with pytest.raises(RuntimeError, match="price-source drift"):
        await _crawl(_product("X 'Y' LP", variants=[
            {"title": "Default Title", "price": None, "available": True}]))


@respx.mock
async def test_one_unpriced_row_among_priced_ones_is_tolerated():
    items = await _crawl(_SMOKE, _product("X 'Y' LP", handle="x", variants=[
        {"title": "Default Title", "price": "n/a", "available": True}]))
    assert [i["price"] for i in items] == [30.99, None]


def test_site_metadata():
    assert Crawler.site_name == "Revolver"
    assert Crawler.base_url == "https://shop.revolvermag.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
    assert Crawler.genre_summary


@respx.mock
async def test_the_walk_never_sends_the_session_cookie_page_one_set():
    # The store's edge answers 429 to a page-2 request carrying page 1's
    # session cookies (observed live 2026-10-01), so the crawler walks with a
    # jar that refuses them.
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(200, json={"products": [_SMOKE]},
                                    headers={"set-cookie": "_shopify_essential=abc; Path=/"}))
    page2 = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=httpx.Response(200, json={"products": []}))
    assert len([i async for i in Crawler().crawl_catalog()]) == 1
    assert "cookie" not in page2.calls.last.request.headers
