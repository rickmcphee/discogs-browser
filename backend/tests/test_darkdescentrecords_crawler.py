import html
import json

import httpx
import pytest
import respx
from config import save_config
from crawlers.darkdescentrecords import Crawler

_PRODUCTS_URL = "https://www.darkdescentrecords.com/shop/wp-json/wc/store/v1/products"


def test_parse_artist_title_splits_on_en_dash():
    assert Crawler._parse_artist_title("Eldfödd – Risen from the Flames LP") == (
        "Eldfödd", "Risen from the Flames LP",
    )


def test_parse_artist_title_unescapes_html_entities_before_splitting():
    # Confirmed live: the Store API's `name` field carries the en dash as
    # the literal entity `&#8211;`, not the unicode character.
    assert Crawler._parse_artist_title("Candarian &#8211; Trepanacion LP") == (
        "Candarian", "Trepanacion LP",
    )


def test_parse_artist_title_splits_on_first_en_dash_when_album_has_its_own():
    # "Ascendency / Chaotian / Septage / Sequestrum – "Tetralogy of
    # Death – Vol. 2" LP" -- confirmed live, the only multi-dash title
    # in the vinyl-lp category. The album itself keeps its internal dash.
    name = 'Ascendency / Chaotian / Septage / Sequestrum – “Tetralogy of Death – Vol. 2” LP'
    assert Crawler._parse_artist_title(name) == (
        "Ascendency / Chaotian / Septage / Sequestrum",
        "“Tetralogy of Death – Vol. 2” LP",
    )


def test_parse_artist_title_returns_none_when_no_separator():
    # Confirmed live: the one holdout in the vinyl-lp category with no
    # "Artist – Title" separator at all.
    name = "Regere Sinister / Reptile Womb Split LP"
    assert Crawler._parse_artist_title(name) == (None, name)


_SIMPLE_PRODUCT = {
    "type": "simple",
    "name": "Eldfödd &#8211; Risen from the Flames LP",
    "permalink": "https://www.darkdescentrecords.com/shop/product/eldfodd-risen-from-the-flames-lp/",
    "is_purchasable": True,
    "is_in_stock": True,
    "prices": {"price": "2700", "currency_code": "USD", "currency_minor_unit": 2},
    "images": [{"src": "https://www.darkdescentrecords.com/shop/media/eldfoddblackvinyl.webp"}],
}

_VARIABLE_PRODUCT = {
    "type": "variable",
    "name": "Candarian &#8211; Trepanacion LP",
    "permalink": "https://www.darkdescentrecords.com/shop/product/candarian-trepanacion-lp/",
    "is_purchasable": True,
    "is_in_stock": True,
    "prices": {"price": "2500", "currency_code": "USD", "currency_minor_unit": 2},
    "images": [{"src": "https://www.darkdescentrecords.com/shop/media/fallback.jpg"}],
}

_VARIATIONS_PAYLOAD = [
    {
        "attributes": {"attribute_variant": "Black"},
        "display_price": 25,
        "is_purchasable": True,
        "is_in_stock": True,
        "image": {"src": "https://www.darkdescentrecords.com/shop/media/candarian-black.jpg"},
    },
    {
        "attributes": {"attribute_variant": "Transparent Red/Black Smoke"},
        "display_price": 26,
        "is_purchasable": True,
        "is_in_stock": True,
        "image": {"src": "https://www.darkdescentrecords.com/shop/media/candarian-red.jpg"},
    },
    {
        "attributes": {"attribute_variant": "Test Pressing"},
        "display_price": 100,
        "is_purchasable": False,
        "is_in_stock": False,
        "image": {},
    },
]


def _variation_page_html(variations=_VARIATIONS_PAYLOAD):
    # Real markup HTML-entity-encodes the embedded JSON attribute --
    # confirmed live (`&quot;` in place of `"`).
    encoded = html.escape(json.dumps(variations), quote=True)
    return f'<form class="variations_form" data-product_variations="{encoded}"></form>'


async def test_items_yields_single_row_for_simple_product():
    items = await Crawler._items(_SIMPLE_PRODUCT, client=None, delay=0)
    assert items == [{
        "artist": "Eldfödd",
        "title": "Risen from the Flames LP",
        "format": "Vinyl",
        "price": 27.0,
        "currency": "USD",
        "url": "https://www.darkdescentrecords.com/shop/product/eldfodd-risen-from-the-flames-lp/",
        "cover_image_url": "https://www.darkdescentrecords.com/shop/media/eldfoddblackvinyl.webp",
    }]


async def test_items_skips_unpurchasable_or_out_of_stock_simple_product():
    product = {**_SIMPLE_PRODUCT, "is_in_stock": False}
    assert await Crawler._items(product, client=None, delay=0) == []


async def test_items_skips_product_with_no_artist_source():
    product = {**_SIMPLE_PRODUCT, "name": "No Separator Here LP"}
    assert await Crawler._items(product, client=None, delay=0) == []


@respx.mock
async def test_items_fetches_variation_detail_for_variable_product():
    respx.get(_VARIABLE_PRODUCT["permalink"]).mock(
        return_value=httpx.Response(200, text=_variation_page_html()))

    async with httpx.AsyncClient() as client:
        items = await Crawler._items(_VARIABLE_PRODUCT, client=client, delay=0)

    assert [i["title"] for i in items] == [
        "Trepanacion LP — Black",
        "Trepanacion LP — Transparent Red/Black Smoke",
    ]
    assert [i["price"] for i in items] == [25.0, 26.0]
    assert items[0]["cover_image_url"] == "https://www.darkdescentrecords.com/shop/media/candarian-black.jpg"


def test_variable_items_skips_unpurchasable_or_out_of_stock_variations():
    items = Crawler._variable_items(
        _variation_page_html(), _VARIABLE_PRODUCT, "Candarian", "Trepanacion LP",
        _VARIABLE_PRODUCT["permalink"], "USD",
    )
    assert not any(i["title"].endswith("Test Pressing") for i in items)


def test_variable_items_falls_back_to_parent_image_when_variation_has_none():
    # A purchasable, in-stock variation with an empty `image` -- distinct
    # from the Test Pressing fixture, which is filtered out before image
    # selection ever runs and so never exercises this fallback branch.
    no_image_variation = {
        "attributes": {"attribute_variant": "White"},
        "display_price": 27,
        "is_purchasable": True,
        "is_in_stock": True,
        "image": {},
    }
    items = Crawler._variable_items(
        _variation_page_html([no_image_variation]),
        _VARIABLE_PRODUCT, "Candarian", "Trepanacion LP", _VARIABLE_PRODUCT["permalink"], "USD",
    )
    assert items[0]["cover_image_url"] == "https://www.darkdescentrecords.com/shop/media/fallback.jpg"


def test_variable_items_raises_on_markup_drift():
    # Not "the site has nothing" -- a missing data-product_variations blob
    # on a variable product's own page is a parser/markup failure and must
    # raise, not silently drop the product from the batch (a swallowed []
    # here would let replace_stock_items() wipe this crawler's previous
    # rows while recording the source as healthy).
    with pytest.raises(RuntimeError):
        Crawler._variable_items(
            "<html>no variation data here</html>", _VARIABLE_PRODUCT,
            "Candarian", "Trepanacion LP", _VARIABLE_PRODUCT["permalink"], "USD",
        )


def test_price_converts_minor_units_to_a_float():
    assert Crawler._price({"price": "2700", "currency_minor_unit": 2}) == 27.0


def test_price_returns_none_when_missing():
    assert Crawler._price({}) is None


@respx.mock
async def test_crawl_catalog_yields_items_from_a_single_page(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=[_SIMPLE_PRODUCT]))

    items = [item async for item in Crawler().crawl_catalog()]
    assert len(items) == 1
    assert items[0]["artist"] == "Eldfödd"


@respx.mock
async def test_crawl_catalog_paginates_until_a_short_page(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    full_page = [{**_SIMPLE_PRODUCT, "permalink": f"https://www.darkdescentrecords.com/shop/product/p{i}/"}
                 for i in range(100)]
    page2_route = respx.get(
        _PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "2"},
    ).mock(return_value=httpx.Response(200, json=[_SIMPLE_PRODUCT]))
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=full_page))

    items = [item async for item in Crawler().crawl_catalog()]
    assert page2_route.call_count == 1
    assert len(items) == 101


@respx.mock
async def test_crawl_catalog_stops_after_a_short_first_page_without_fetching_page_two(
    monkeypatch, tmp_config_dir
):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=[_SIMPLE_PRODUCT]))
    page2_route = respx.get(
        _PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "2"},
    ).mock(return_value=httpx.Response(200, json=[]))

    items = [item async for item in Crawler().crawl_catalog()]
    assert len(items) == 1
    assert page2_route.call_count == 0


@respx.mock
async def test_crawl_catalog_raises_on_http_error(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in Crawler().crawl_catalog()]


def test_needs_detail_fetch_matches_the_products_items_actually_fetches():
    """crawl_catalog counts these before a page's loop while _items branches on
    the same predicate, so the progress total cannot drift from what the loop
    fetches."""
    assert Crawler._needs_detail_fetch(_VARIABLE_PRODUCT) is True
    assert Crawler._needs_detail_fetch(_SIMPLE_PRODUCT) is False
    assert Crawler._needs_detail_fetch({**_VARIABLE_PRODUCT, "is_in_stock": False}) is False
    assert Crawler._needs_detail_fetch({**_VARIABLE_PRODUCT, "is_purchasable": False}) is False
    assert Crawler._needs_detail_fetch({**_VARIABLE_PRODUCT, "name": "No Separator LP"}) is False


@respx.mock
async def test_crawl_catalog_reports_progress_through_a_pages_variable_product_fetches(
    monkeypatch, tmp_config_dir
):
    """report_page() fires only once the whole page is done, which at the
    default crawl_delay_seconds is many paced detail fetches later -- silence
    long enough to read as a hang."""
    import crawl_progress

    save_config({"crawl_delay_seconds": 0})
    variable_b = {**_VARIABLE_PRODUCT,
                  "name": "Other &#8211; Second LP",
                  "permalink": "https://www.darkdescentrecords.com/shop/product/second-lp/"}
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=[_VARIABLE_PRODUCT, _SIMPLE_PRODUCT, variable_b]))
    respx.get(_VARIABLE_PRODUCT["permalink"]).mock(
        return_value=httpx.Response(200, text=_variation_page_html()))
    respx.get(variable_b["permalink"]).mock(
        return_value=httpx.Response(200, text=_variation_page_html()))

    reported = []

    async def reporter(done, total, label):
        reported.append((done, total, label))

    token = crawl_progress.set_detail_reporter(reporter)
    try:
        [item async for item in Crawler().crawl_catalog()]
    finally:
        crawl_progress.reset_detail_reporter(token)

    # The simple product costs no request, so it neither advances the counter
    # nor counts toward the total -- one report per paced fetch, the property
    # report_page() already has for a one-phase crawler.
    assert reported == [
        (0, 2, "listing page 1"),
        (1, 2, "listing page 1"),
        (2, 2, "listing page 1"),
    ]


@respx.mock
async def test_crawl_catalog_reports_nothing_for_a_page_of_only_simple_products(
    monkeypatch, tmp_config_dir
):
    """A page with no detail fetches has no wait to announce, so "0/0" would
    describe one that isn't coming."""
    import crawl_progress

    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=[_SIMPLE_PRODUCT]))

    reported = []

    async def reporter(done, total, label):
        reported.append((done, total, label))

    token = crawl_progress.set_detail_reporter(reporter)
    try:
        items = [item async for item in Crawler().crawl_catalog()]
    finally:
        crawl_progress.reset_detail_reporter(token)

    assert len(items) == 1
    assert reported == []


@respx.mock
async def test_crawl_catalog_labels_progress_with_the_listing_page_it_is_on(
    monkeypatch, tmp_config_dir
):
    import crawl_progress

    save_config({"crawl_delay_seconds": 0})
    full_page = [{**_SIMPLE_PRODUCT, "permalink": f"https://www.darkdescentrecords.com/shop/product/p{i}/"}
                 for i in range(100)]
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=full_page))
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "2"}).mock(
        return_value=httpx.Response(200, json=[_VARIABLE_PRODUCT]))
    respx.get(_VARIABLE_PRODUCT["permalink"]).mock(
        return_value=httpx.Response(200, text=_variation_page_html()))

    reported = []

    async def reporter(done, total, label):
        reported.append((done, total, label))

    token = crawl_progress.set_detail_reporter(reporter)
    try:
        [item async for item in Crawler().crawl_catalog()]
    finally:
        crawl_progress.reset_detail_reporter(token)

    assert reported == [(0, 1, "listing page 2"), (1, 1, "listing page 2")]


@respx.mock
async def test_crawl_catalog_runs_with_no_detail_reporter_installed(monkeypatch, tmp_config_dir):
    """Same contract report_page() already has: a crawler stays directly
    runnable outside a stock sync."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"category": "vinyl-lp", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=[_VARIABLE_PRODUCT]))
    respx.get(_VARIABLE_PRODUCT["permalink"]).mock(
        return_value=httpx.Response(200, text=_variation_page_html()))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 2


def test_site_metadata():
    assert Crawler.site_name == "Dark Descent Records"
    assert Crawler.base_url == "https://www.darkdescentrecords.com/shop"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "metal"
