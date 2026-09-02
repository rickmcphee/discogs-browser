import httpx
import pytest
import respx
from config import save_config
from crawlers.byrdlandrecords import Crawler

_BASE = "https://shop.byrdlandrecords.com"
_SHOP_ID = 639364


def _page_url(page: int) -> str:
    return f"{_BASE}/vinyl/page{page}.html"


# CAPTURED: copied verbatim from the live
# /vinyl/page1.html?format=json&limit=100 response on 2026-09-02.
_CAPTURED_PRODUCT = {
    "id": 70995273,
    "vid": 119900596,
    "image": 77301533,
    "brand": False,
    "code": "",
    "ean": "656605919614",
    "sku": "",
    "score": 0,
    "price": {
        "price": 32,
        "price_incl": 32,
        "price_excl": 32,
        "price_old": 0,
        "price_old_incl": 0,
        "price_old_excl": 0,
    },
    "available": True,
    "unit": False,
    "url": "sufjan-stevens-enjoy-your-rabbit.html",
    "title": "Sufjan Stevens - Enjoy Your Rabbit",
    "fulltitle": "Sufjan Stevens - Enjoy Your Rabbit",
    "variant": "Default",
    "description": "",
    "data_01": "",
}


def _product(**overrides):
    """ALTERED: the captured product above with fields swapped per test."""
    return {**_CAPTURED_PRODUCT, **overrides}


def _payload(products, page=1, pages=1, shop_id=_SHOP_ID, currency="usd", count=None):
    """ALTERED: the live response envelope, trimmed to the keys the crawler
    reads. The live payload also carries `theme`, `request`, `layout`,
    `template`, `renderer` and `datadog_browser` blocks, none of which this
    crawler touches."""
    return {
        "collection": {
            # Collection-wide total, not this page's size. Defaults to the page
            # length so single-page payloads stay terse; multi-page tests pass
            # it explicitly, because a falling count now aborts the walk.
            "count": len(products) if count is None else count,
            "page": page,
            "pages": pages,
            "limit": 100,
            "products": {str(p["id"]): p for p in products},
        },
        "shop": {"id": shop_id, "currency": currency},
    }


def _parse(title, **overrides):
    return Crawler._parse_product(_product(title=title, **overrides), _SHOP_ID, "USD")


# --- title splitting -------------------------------------------------------

def test_parse_product_splits_on_a_spaced_hyphen():
    item = _parse("Sufjan Stevens - Enjoy Your Rabbit")
    assert (item["artist"], item["title"]) == ("Sufjan Stevens", "Enjoy Your Rabbit")


def test_parse_product_splits_on_en_dash_and_em_dash():
    # CAPTURED: the en-dash form is live on this store; the em-dash arm comes
    # from the shared [-–—] class jackpotrecords.py/cleorecs.py also use.
    assert _parse("Kelly Lee Owens – Dreamstate")["artist"] == "Kelly Lee Owens"
    assert _parse("Kelly Lee Owens — Dreamstate")["artist"] == "Kelly Lee Owens"


def test_parse_product_splits_when_whitespace_is_only_on_one_side():
    # CAPTURED: both asymmetric forms are live -- "Alkaline Trio -\tGoddamit"
    # and "Gracie\tAbrams- Daughter from Hell".
    assert _parse("Alkaline Trio - Goddamit")["title"] == "Goddamit"
    assert _parse("Watchhouse -This Side of Jordan")["title"] == "This Side of Jordan"
    assert _parse("Big Boys- Fun, Fun, Fun")["artist"] == "Big Boys"


def test_parse_product_does_not_split_a_hyphen_inside_a_name():
    # The reason the separator requires whitespace on at least one side.
    # All three are live titles whose hyphen is not an artist/album boundary.
    assert _parse("Jay-Z - Reasonable Doubt")["artist"] == "Jay-Z"
    assert _parse("Now That's What I Call K-Pop") is None
    assert _parse("Country Funk Vol. 3 1975-1982") is None


def test_parse_product_splits_on_the_first_separator_only():
    item = _parse("XTC - Live Boots - Live At Emerald City 1981")
    assert (item["artist"], item["title"]) == (
        "XTC", "Live Boots - Live At Emerald City 1981",
    )


def test_parse_product_collapses_tabs_and_double_spaces_inside_the_artist():
    # CAPTURED: "Chuck\tProphet - Wake The Dead (Orange Vinyl)" is live. The
    # separator regex tolerates a tab beside the dash on its own, but a tab
    # *inside* the artist would survive into the stock row without the
    # whitespace collapse.
    item = _parse("Chuck\tProphet - Wake The Dead (Orange Vinyl)")
    assert item["artist"] == "Chuck Prophet"
    assert item["title"] == "Wake The Dead (Orange Vinyl)"


def test_parse_product_strips_the_tab_run_the_store_pads_annotations_with():
    # CAPTURED: RSD rows pad the trailing annotation with tabs --
    # "David Bowie\t-\tHallo Spaceboy\t(RSD 2026)".
    item = _parse("David Bowie\t-\tHallo Spaceboy\t(RSD 2026)")
    assert (item["artist"], item["title"]) == ("David Bowie", "Hallo Spaceboy (RSD 2026)")


def test_parse_product_skips_a_title_with_no_separator():
    # CAPTURED: live titles carrying no artist/album boundary at all. `brand`
    # is False on every product in this category, so there is no fallback
    # artist source -- the fleet's "no artist source -> skip" convention.
    assert _parse("Nigeria 70") is None
    assert _parse("Boone Creek") is None
    assert _parse("Stax Does The Beatles") is None


def test_parse_product_skips_a_blank_or_whitespace_only_title():
    assert _parse("") is None
    assert _parse("   \t  ") is None


def test_parse_product_does_not_split_on_a_colon():
    # CAPTURED: a colon is part of the album on this store
    # ("Eccentric Soul: The Tammy Label"), never an artist boundary, so it is
    # deliberately not in the separator class.
    assert _parse("Eccentric Soul: The Tammy Label") is None


# --- format filtering ------------------------------------------------------

def test_parse_product_skips_cds_and_cassettes():
    # CAPTURED: all four forms are live in the Vinyl category.
    assert _parse("Watusi - Cult Flu (CD)") is None
    assert _parse("Anna Tivel - Outsiders CD") is None
    assert _parse("Fulton Lights - Well the Night Has Come (Cassette)") is None
    assert _parse("Latchwork - Bald Mountain Breaks CASSETTE") is None


def test_parse_product_skips_a_counted_cd_or_dvd():
    # A bare \bcds?\b cannot see the CD in "2xCD" -- the spv.py /
    # onetwothreefourgo.py regression this pattern's \d*[x×]? guards against.
    assert _parse("Some Artist - Anthology 2xCD") is None
    assert _parse("Some Artist - Anthology 3xDVD") is None


def test_parse_product_skips_a_cd_the_store_marked_by_negating_vinyl():
    # CAPTURED: 16 live products annotate a mis-filed CD as "CD NOT VINYL"
    # in some casing. The phrase contains the vinyl override's strongest
    # keyword, so without _NOT_VINYL_RE the annotation rescues the very
    # product it exists to exclude.
    assert _parse("The Four Tops - The Definitive Collection CD NOT VINYL") is None
    assert _parse("Cat Stevens - Greatest Hits (CD NOT VINYL)") is None
    assert _parse("Ardamus - Enshrouded: Devils [EP] (CD not Vinyl)") is None
    assert _parse("Wale - Folarin II ***CD (not vinyl)") is None
    assert _parse("Flowerbomb & Pretty Bitter - Take Me Out (Split Cassette) (Not Vinyl)") is None


def test_parse_product_keeps_a_record_bundled_with_a_disc():
    # CAPTURED: "Ata Kak - Obaa Sima (Anniversary Remaster)(Splatter Vinyl
    # LP+DVD)" is a vinyl LP that names a DVD; the vinyl override keeps it.
    item = _parse("Ata Kak - Obaa Sima (Splatter Vinyl LP+DVD)")
    assert item is not None and item["artist"] == "Ata Kak"


def test_parse_product_does_not_treat_tape_book_or_magazine_as_a_format():
    # CAPTURED false positives. These are why the non-vinyl pattern carries
    # no `tapes?`, `book` or `magazine` alternative: this store fuses the
    # format into the title, so the pattern is read against the album name
    # and the artist name too.
    assert _parse("Felbm - Tape 1/Tape 2") is not None
    assert _parse("Paul Cauthen - Book of Paul") is not None
    assert _parse("Peel Dream Magazine - Rose Main Reading Room") is not None
    assert _parse("YHWH Nailgun - Magazine") is not None


# --- field mapping ---------------------------------------------------------

def test_parse_product_maps_the_remaining_fields():
    item = _parse("Sufjan Stevens - Enjoy Your Rabbit")
    assert item["format"] == "Vinyl"
    assert item["currency"] == "USD"
    assert item["price"] == 32.0
    assert item["url"] == f"{_BASE}/sufjan-stevens-enjoy-your-rabbit.html"


def test_cover_image_url_is_built_from_the_shop_and_image_ids():
    item = _parse("Sufjan Stevens - Enjoy Your Rabbit")
    assert item["cover_image_url"] == (
        f"https://cdn.shoplightspeed.com/shops/{_SHOP_ID}/files/77301533/"
        "sufjan-stevens-enjoy-your-rabbit.jpg"
    )


def test_cover_image_url_is_none_when_the_product_has_no_image():
    # CAPTURED: a minority of live products carry `"image": 0`.
    assert _parse("Sufjan Stevens - Enjoy Your Rabbit", image=0)["cover_image_url"] is None


def test_price_of_zero_is_reported_as_unknown_not_free():
    # CAPTURED: one live product is listed at 0.
    assert _parse("Sufjan Stevens - Enjoy Your Rabbit", price={"price": 0})["price"] is None


def test_price_is_none_when_the_field_is_missing_or_unparsable():
    assert _parse("A - B", price={})["price"] is None
    assert _parse("A - B", price={"price": "n/a"})["price"] is None


def test_price_rejects_non_finite_and_negative_values():
    # nan is the dangerous one: it is truthy, so a bare falsiness check lets
    # it reach the stock row, where it breaks JSON serialisation downstream.
    for bad in ("nan", "inf", "-inf", -5, "-12.50"):
        assert _parse("A - B", price={"price": bad})["price"] is None, bad


def test_price_rejects_a_boolean():
    # bool is an int subclass, so float(True) would price a record at 1.
    assert _parse("A - B", price={"price": True})["price"] is None


# --- pagination ------------------------------------------------------------

@respx.mock
async def test_crawl_catalog_yields_items_from_a_single_page(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=_payload([_CAPTURED_PRODUCT])))

    items = [item async for item in Crawler().crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Sufjan Stevens"


@respx.mock
async def test_crawl_catalog_requests_the_json_format_and_the_max_page_size(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    route = respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload([_CAPTURED_PRODUCT])))

    [item async for item in Crawler().crawl_catalog()]
    params = route.calls[0].request.url.params
    assert params["format"] == "json"
    # Larger values are silently ignored by the store, not clamped, falling
    # back to a page of 12.
    assert params["limit"] == "100"


@respx.mock
async def test_crawl_catalog_paginates_by_path_up_to_the_reported_page_count(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    page2 = [_product(id=900, url="p900.html", title="Last Artist - Last Album")]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=2, count=101)))
    route2 = respx.get(_page_url(2)).mock(
        return_value=httpx.Response(200, json=_payload(page2, page=2, pages=2, count=101)))

    items = [item async for item in Crawler().crawl_catalog()]
    assert route2.call_count == 1
    assert len(items) == 101
    assert items[-1]["artist"] == "Last Artist"


@respx.mock
async def test_crawl_catalog_stops_at_the_last_page_without_requesting_past_it(tmp_config_dir):
    """The store answers a past-the-end page with page 1 again, HTTP 200 -- so
    an unbounded loop would re-ingest the first page forever."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=_payload([_CAPTURED_PRODUCT], pages=1)))
    route2 = respx.get(_page_url(2)).mock(
        return_value=httpx.Response(200, json=_payload([_CAPTURED_PRODUCT], page=1, pages=1)))

    items = [item async for item in Crawler().crawl_catalog()]
    assert len(items) == 1
    assert route2.call_count == 0


@respx.mock
async def test_crawl_catalog_raises_when_the_store_serves_a_page_it_was_not_asked_for(tmp_config_dir):
    """A silently-ignored pager is the failure this guard exists for: the
    `?page=` querystring is ignored by this store, so a regression to it
    would answer page 1 for every request with HTTP 200 and look healthy."""
    save_config({"crawl_delay_seconds": 0})
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=3, count=250)))
    respx.get(_page_url(2)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=3, count=250)))

    with pytest.raises(RuntimeError, match="pagination contract drift"):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_dedupes_a_product_that_resurfaces_on_a_later_page(tmp_config_dir):
    """Newest-first ordering means a product added mid-walk shifts every later
    page down by one and re-serves a row already yielded."""
    save_config({"crawl_delay_seconds": 0})
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=2, count=101)))
    respx.get(_page_url(2)).mock(return_value=httpx.Response(
        200, json=_payload([page1[99], _product(id=900, url="p900.html", title="New - Row")],
                           page=2, pages=2, count=101)))

    items = [item async for item in Crawler().crawl_catalog()]
    assert len(items) == 101
    assert sum(1 for i in items if i["url"].endswith("p99.html")) == 1


@respx.mock
async def test_crawl_catalog_follows_a_page_count_that_grows_mid_walk(tmp_config_dir):
    """The listing is newest-first and the store keeps selling during the walk,
    so an insertion can push `count` over a page boundary and grow `pages`
    after page 1 has already answered. Sampling the bound once would leave the
    newly reported final page unfetched."""
    save_config({"crawl_delay_seconds": 0})
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=2, count=200)))
    respx.get(_page_url(2)).mock(return_value=httpx.Response(
        200, json=_payload([_product(id=900, url="p900.html", title="Mid - Walk")],
                           page=2, pages=3, count=250)))
    route3 = respx.get(_page_url(3)).mock(return_value=httpx.Response(
        200, json=_payload([_product(id=901, url="p901.html", title="Grew - Late")],
                           page=3, pages=3, count=250)))

    items = [item async for item in Crawler().crawl_catalog()]
    assert route3.call_count == 1
    assert items[-1]["artist"] == "Grew"


@respx.mock
async def test_crawl_catalog_raises_when_the_catalog_shrinks_mid_walk(tmp_config_dir):
    """A product sold from a page the walk has already passed shifts every
    later product back one offset, sliding the next page's first row onto the
    page just read -- so it is never fetched, the crawl succeeds anyway, and
    replace_stock_items() deletes that still-in-stock row. Dedupe cannot see
    it: an insertion surfaces as a duplicate, a deletion as nothing at all.
    A falling `count` is the signal, so the walk aborts and keeps the previous
    snapshot."""
    save_config({"crawl_delay_seconds": 0})
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=2, count=150)))
    # One row from page 1 has sold by the time page 2 is asked for.
    respx.get(_page_url(2)).mock(return_value=httpx.Response(
        200, json=_payload([_product(id=900, url="p900.html", title="Shifted - Row")],
                           page=2, pages=2, count=149)))

    with pytest.raises(RuntimeError, match="catalog shrank from 150 to 149"):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_tolerates_a_catalog_that_grows_mid_walk(tmp_config_dir):
    """Growth is the safe direction and must not abort: an insertion shifts
    rows forward, so the next page re-serves one already yielded (deduped) and
    skips nothing. The only row it can cost is the new arrival itself, which
    no snapshot held yet and the next run picks up."""
    save_config({"crawl_delay_seconds": 0})
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=2, count=101)))
    respx.get(_page_url(2)).mock(return_value=httpx.Response(
        200, json=_payload([_product(id=900, url="p900.html", title="New - Arrival")],
                           page=2, pages=2, count=102)))

    items = [item async for item in Crawler().crawl_catalog()]
    assert len(items) == 101


@respx.mock
async def test_crawl_catalog_raises_when_the_item_count_is_unusable(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    body = _payload([_CAPTURED_PRODUCT], page=1, pages=1)
    body["collection"]["count"] = "3312"
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(RuntimeError, match="reports no usable item count"):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_the_page_count_is_missing(tmp_config_dir):
    """Defaulting to 1 would turn a whole multi-page catalog into a successful
    single-page snapshot, and replace_stock_items() would delete the rest."""
    save_config({"crawl_delay_seconds": 0})
    body = _payload([_CAPTURED_PRODUCT], page=1, pages=1)
    del body["collection"]["pages"]
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(RuntimeError, match="reports no usable page count"):
        [item async for item in Crawler().crawl_catalog()]


# True is in here on purpose: bool is an int subclass, so `isinstance(pages,
# int)` alone accepts it and `True >= 1`, which would bound the whole catalog
# to a single page. `_page_count` rejects it explicitly and this pins that.
@pytest.mark.parametrize("bad_pages", [0, -1, "34", 3.5, True, None])
@respx.mock
async def test_crawl_catalog_raises_on_a_non_positive_or_non_integer_page_count(
    tmp_config_dir, bad_pages
):
    save_config({"crawl_delay_seconds": 0})
    body = _payload([_CAPTURED_PRODUCT], page=1, pages=1)
    body["collection"]["pages"] = bad_pages
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(RuntimeError, match="reports no usable page count"):
        [item async for item in Crawler().crawl_catalog()]


# --- drift guards ----------------------------------------------------------

@respx.mock
async def test_crawl_catalog_raises_when_the_first_page_is_empty(tmp_config_dir):
    """replace_stock_items() DELETEs before it inserts, so completing with
    nothing wipes the snapshot and records the site as healthy."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=_payload([])))

    with pytest.raises(RuntimeError, match="no products on"):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_no_product_parses(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    unparsable = [_product(id=i, url=f"p{i}.html", title="No Separator Here") for i in range(3)]
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=_payload(unparsable)))

    with pytest.raises(RuntimeError, match="parsed 0 vinyl items"):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_a_page_inside_the_range_is_empty(tmp_config_dir):
    """The store derives `pages` from `count`, so a page within the reported
    range always has rows. Guarding only page 1 would let a later empty page
    complete the walk successfully having silently dropped that page's stock,
    which replace_stock_items() then deletes."""
    save_config({"crawl_delay_seconds": 0})
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=2, count=150)))
    respx.get(_page_url(2)).mock(
        return_value=httpx.Response(200, json=_payload([], page=2, pages=2, count=150)))

    with pytest.raises(RuntimeError, match="no products on /vinyl/page2.html"):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_the_payload_carries_no_shop_identity(tmp_config_dir):
    """Defaulting the currency would record a crawl of a re-denominated store
    as healthy; a missing shop id would quietly strip every cover URL."""
    save_config({"crawl_delay_seconds": 0})
    body = _payload([_CAPTURED_PRODUCT])
    body["shop"] = {}
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(RuntimeError, match="carries no shop id/currency"):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_the_currency_alone_is_missing(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    body = _payload([_CAPTURED_PRODUCT])
    body["shop"] = {"id": _SHOP_ID}
    respx.get(_page_url(1)).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(RuntimeError, match="carries no shop id/currency"):
        [item async for item in Crawler().crawl_catalog()]


def test_parse_product_raises_when_a_product_carries_no_url():
    """The url is the row's identity. Emitting the store root instead would
    publish a bogus one, and the crawl would still count as healthy."""
    with pytest.raises(RuntimeError, match="carries no url"):
        Crawler._parse_product(_product(title="A - B", url=""), _SHOP_ID, "USD")


def test_parse_product_does_not_raise_for_a_url_less_product_it_would_skip_anyway():
    """The url check sits after the filters, so a mis-filed CD with a broken
    payload doesn't fail the whole crawl over a row that is discarded."""
    assert Crawler._parse_product(_product(title="Watusi - Cult Flu (CD)", url=""), _SHOP_ID, "USD") is None
    assert Crawler._parse_product(_product(title="No Separator", url=""), _SHOP_ID, "USD") is None


@respx.mock
async def test_crawl_catalog_raises_on_http_error(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_page_url(1)).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in Crawler().crawl_catalog()]


# --- payload-derived shop identity ----------------------------------------

@respx.mock
async def test_crawl_catalog_takes_the_shop_id_and_currency_from_the_payload(tmp_config_dir):
    """Both are echoed in every response, so neither is hardcoded here."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_page_url(1)).mock(return_value=httpx.Response(
        200, json=_payload([_CAPTURED_PRODUCT], shop_id=111222, currency="cad")))

    items = [item async for item in Crawler().crawl_catalog()]
    assert items[0]["currency"] == "CAD"
    assert items[0]["cover_image_url"].startswith(
        "https://cdn.shoplightspeed.com/shops/111222/files/77301533/")


@respx.mock
async def test_crawl_catalog_reports_each_page_it_fetches(tmp_config_dir):
    import crawl_progress

    save_config({"crawl_delay_seconds": 0})
    reported = []
    token = crawl_progress.set_page_reporter(
        lambda page, count: _record(reported, page, count))
    page1 = [_product(id=i, url=f"p{i}.html", title=f"Artist {i} - Album {i}") for i in range(100)]
    respx.get(_page_url(1)).mock(
        return_value=httpx.Response(200, json=_payload(page1, page=1, pages=2, count=101)))
    respx.get(_page_url(2)).mock(return_value=httpx.Response(
        200, json=_payload([_CAPTURED_PRODUCT], page=2, pages=2, count=101)))
    try:
        [item async for item in Crawler().crawl_catalog()]
    finally:
        crawl_progress.reset_page_reporter(token)

    assert reported == [(1, 100), (2, 1)]


async def _record(sink, page, count):
    sink.append((page, count))
