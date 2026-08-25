import httpx
import pytest
import respx

from config import save_config
from crawlers.dischordrecords import Crawler

_LISTING_PAGE_1 = """
<div class='item first'>
<a href="/release/203/the-mark"><img src="https://s3.amazonaws.com/x/203.jpg" />
</a><span class='releaseNumber'>203</span>
<span class='band'><a href="/band/bed-maker">Bed Maker</a></span>
<a href="/release/203/the-mark">The Mark</a>
</div>
<div class='item last'>
<a href="/release/202/plays"><img src="https://s3.amazonaws.com/x/202.jpg" />
</a><span class='releaseNumber'>202</span>
<span class='band'><a href="/band/various-artists">Various Artists</a></span>
<a href="/release/202/plays">Plays</a>
</div>
<br class='clearBoth'>
<nav><ul class="pagination"><li class="page-item disabled"><span class="page-link">&larr;</span></li> <li class="page-item active"><span class="page-link">1</span></li> <li class="page-item"><a class="page-link" rel="next" href="/label/dischord?page=2">2</a></li> <li class="page-item"><a class="page-link" href="/label/dischord?page=3">3</a></li> <li class="page-item"><a class="page-link" href="/label/dischord?page=8">8</a></li> <li class="page-item"><a class="page-link" rel="next" href="/label/dischord?page=2">&rarr;</a></li></ul></nav>
"""

_LISTING_PAGE_NO_PAGINATION = """
<div class='item first last'>
<a href="/release/1/only-release"><img src="https://s3.amazonaws.com/x/1.jpg" />
</a><span class='releaseNumber'>1</span>
<span class='band'><a href="/band/only-band">Only Band</a></span>
<a href="/release/1/only-release">Only Release</a>
</div>
"""


def test_max_page_reads_highest_page_number_from_pagination_nav():
    assert Crawler._max_page(_LISTING_PAGE_1) == 8


def test_max_page_defaults_to_one_when_no_pagination_nav():
    assert Crawler._max_page(_LISTING_PAGE_NO_PAGINATION) == 1


def test_release_hrefs_dedupes_image_and_title_links_per_row():
    assert Crawler._release_hrefs(_LISTING_PAGE_1) == [
        "/release/203/the-mark",
        "/release/202/plays",
    ]


def test_release_hrefs_returns_empty_list_when_none_found():
    assert Crawler._release_hrefs("<html>nothing here</html>") == []


def _detail_page(h1_body, prices_body, og_image="https://s3.amazonaws.com/x/cover.jpg"):
    og_image_tag = f"<meta content='{og_image}' property='og:image'>" if og_image else ""
    return f"""
{og_image_tag}
<div id='productInfo'>
<h1>
{h1_body}
</h1>
<div class='productGeneral' id='productPrices'>
{prices_body}
</div>
<div id='productDescription'>
</div>
</div>
"""


_H1_SINGLE = """
<span class='releaseNumber'>
<a style="font-weight:normal;color:black" href="/label/dischord">Dischord</a>
203
</span>
<a href="/band/bed-maker">Bed Maker</a>
<cite>The Mark</cite>
"""

_H1_VARIOUS_ARTISTS = """
<span class='releaseNumber'>
<a style="font-weight:normal;color:black" href="/label/dischord">Dischord</a>
202
</span>
<a href="/band/various-artists">Various Artists</a>
<cite>Plays</cite>
"""

_ONE_VINYL_BUTTON = '<a rel="nofollow" data-method="post" href="/cart/add/4190">Preorder 7&quot; $8</a>'

_MULTI_FORMAT_BUTTONS = (
    '<a rel="nofollow" data-method="post" href="/cart/add/1">Buy 12&quot; LP $18</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/2">Buy CD $10</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/3">Buy Digital $7</a>'
)

_TWO_VINYL_BUTTONS = (
    '<a rel="nofollow" data-method="post" href="/cart/add/1">Buy 12&quot; LP $18</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/2">Buy 12&quot; LP (Damaged Packaging) $12</a>'
)

_CD_AND_DIGITAL_ONLY = (
    '<a rel="nofollow" data-method="post" href="/cart/add/1">Buy CD $10</a>'
    '<a rel="nofollow" data-method="post" href="/cart/add/2">Buy Digital $7</a>'
)

# Same button, attributes reordered and an extra class added -- the shape a
# Rails template change (or a Turbo migration swapping data-method for
# data-turbo-method) would produce.
_REORDERED_ATTR_BUTTON = (
    '<a href="/cart/add/4190" class="btn" data-turbo-method="post" rel="nofollow">'
    'Preorder 7&quot; $8</a>'
)


def test_parse_release_single_vinyl_format():
    html = _detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert items == [{
        "artist": "Bed Maker",
        "title": "The Mark — 7\"",
        "format": "Vinyl",
        "price": 8.0,
        "currency": "USD",
        "url": "https://dischord.com/release/203/the-mark",
        "cover_image_url": "https://s3.amazonaws.com/x/cover.jpg",
    }]


def test_parse_release_suffixes_format_even_when_it_is_the_only_one():
    # The suffix must not depend on how many formats are currently for sale.
    # This site omits an unavailable format's button entirely, so a release
    # that today shows one format may have shown two yesterday -- deciding
    # the suffix from the live count would rewrite the surviving edition's
    # title, changing its item_key (db.py hashes the title) and orphaning
    # any durable stock_item_judgments row pointing at it.
    one = Crawler._parse_release(_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON),
                                 "/release/203/the-mark")
    two = Crawler._parse_release(_detail_page(_H1_SINGLE, _TWO_VINYL_BUTTONS),
                                 "/release/203/the-mark")
    # The plain-LP edition carries an identical title in both worlds.
    assert one[0]["title"] == "The Mark — 7\""
    assert two[0]["title"] == "The Mark — 12\" LP"
    assert all(" — " in i["title"] for i in one + two)


def test_parse_release_various_artists_band_link():
    html = _detail_page(_H1_VARIOUS_ARTISTS, _ONE_VINYL_BUTTON)
    items = Crawler._parse_release(html, "/release/202/plays")
    assert items[0]["artist"] == "Various Artists"
    assert items[0]["title"] == "Plays — 7\""


def test_parse_release_skips_non_vinyl_formats():
    html = _detail_page(_H1_SINGLE, _MULTI_FORMAT_BUTTONS)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert len(items) == 1
    assert items[0]["price"] == 18.0
    assert items[0]["title"] == "The Mark — 12\" LP"


def test_parse_release_multiple_vinyl_formats_get_suffixed_titles():
    html = _detail_page(_H1_SINGLE, _TWO_VINYL_BUTTONS)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert [i["title"] for i in items] == [
        "The Mark — 12\" LP",
        "The Mark — 12\" LP (Damaged Packaging)",
    ]
    assert [i["price"] for i in items] == [18.0, 12.0]


def test_parse_release_cd_and_digital_only_yields_nothing():
    html = _detail_page(_H1_SINGLE, _CD_AND_DIGITAL_ONLY)
    assert Crawler._parse_release(html, "/release/203/the-mark") == []


def test_parse_release_empty_prices_div_yields_nothing():
    html = _detail_page(_H1_SINGLE, "")
    assert Crawler._parse_release(html, "/release/007-0/flex-your-head-tracks-3") == []


def test_parse_release_missing_cover_image_is_none():
    html = _detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON, og_image=None)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert items[0]["cover_image_url"] is None


def test_parse_release_finds_cart_anchor_regardless_of_attribute_order():
    # Attribute drift must not make a button invisible. Pinning the exact
    # attribute string meant a reordered/extra attribute matched nothing, so
    # the release yielded no vinyl and replace_stock_items cleared its rows
    # while recording the source healthy -- and the whole-crawl zero-vinyl
    # guard can't see it, because other releases still yield items.
    html = _detail_page(_H1_SINGLE, _REORDERED_ATTR_BUTTON)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert [i["title"] for i in items] == ["The Mark — 7\""]
    assert items[0]["price"] == 8.0


def test_parse_release_raises_on_cart_anchor_with_empty_text():
    # An empty cart anchor is drift, and drift must reach a raise rather than
    # vanish: _BUTTON_RE matches it so _BUTTON_TEXT_RE can reject it.
    html = _detail_page(_H1_SINGLE, '<a href="/cart/add/1"></a>')
    with pytest.raises(RuntimeError):
        Crawler._parse_release(html, "/release/203/the-mark")


def test_parse_release_raises_when_h1_does_not_parse():
    html = _detail_page("<span>totally different markup</span>", _ONE_VINYL_BUTTON)
    with pytest.raises(RuntimeError):
        Crawler._parse_release(html, "/release/203/the-mark")


def test_parse_release_raises_when_prices_div_missing_entirely():
    html = f"<div id='productInfo'><h1>{_H1_SINGLE}</h1></div>"
    with pytest.raises(RuntimeError):
        Crawler._parse_release(html, "/release/203/the-mark")


def test_parse_release_raises_on_unparsable_buy_button():
    bad_button = '<a rel="nofollow" data-method="post" href="/cart/add/1">Notify Me</a>'
    html = _detail_page(_H1_SINGLE, bad_button)
    with pytest.raises(RuntimeError):
        Crawler._parse_release(html, "/release/203/the-mark")


def test_price_parses_comma_separated_string():
    assert Crawler._price("1,250") == 1250.0


def test_price_returns_none_on_unparsable_string():
    assert Crawler._price("free") is None


_LABEL_URL = "https://dischord.com/label/dischord"


def _release_url(href):
    return f"https://dischord.com{href}"


@respx.mock
async def test_crawl_catalog_yields_items_from_a_single_page(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    respx.get(_release_url("/release/1/only-release")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 1
    assert items[0]["artist"] == "Bed Maker"
    assert items[0]["url"] == "https://dischord.com/release/1/only-release"


def _listing_page_with_one_release(href):
    return f'<div class="item"><a href="{href}">Release</a></div>'


@respx.mock
async def test_crawl_catalog_paginates_and_fetches_every_release(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    page1_route = respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_1))
    page2_route = respx.get(_LABEL_URL, params={"page": "2"}).mock(
        return_value=httpx.Response(200, text=_listing_page_with_one_release("/release/p2/r2")))
    for i in range(3, 9):
        respx.get(_LABEL_URL, params={"page": str(i)}).mock(
            return_value=httpx.Response(200, text=_listing_page_with_one_release(f"/release/p{i}/r{i}")))
    respx.get(_release_url("/release/203/the-mark")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))
    respx.get(_release_url("/release/202/plays")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_VARIOUS_ARTISTS, _ONE_VINYL_BUTTON)))
    for i in range(2, 9):
        respx.get(_release_url(f"/release/p{i}/r{i}")).mock(
            return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    items = [item async for item in Crawler().crawl_catalog()]

    assert page1_route.call_count == 1
    assert page2_route.call_count == 1
    assert len(items) == 9  # page 1's two releases + one distinct release each on pages 2-8 (7 pages)


@respx.mock
async def test_crawl_catalog_dedupes_release_seen_on_an_earlier_page(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    shared_href = "/release/shared/dup"
    page1_html = (
        f'<div class="item"><a href="{shared_href}">Shared</a></div>'
        '<nav><ul class="pagination"><li class="page-item"><a class="page-link" href="/label/dischord?page=2">2</a></li></ul></nav>'
    )
    page2_html = (
        f'<div class="item"><a href="{shared_href}">Shared</a></div>'
        '<div class="item"><a href="/release/only/page2">Only</a></div>'
    )
    respx.get(_LABEL_URL, params={"page": "1"}).mock(return_value=httpx.Response(200, text=page1_html))
    respx.get(_LABEL_URL, params={"page": "2"}).mock(return_value=httpx.Response(200, text=page2_html))
    shared_route = respx.get(_release_url(shared_href)).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))
    respx.get(_release_url("/release/only/page2")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_VARIOUS_ARTISTS, _ONE_VINYL_BUTTON)))

    items = [item async for item in Crawler().crawl_catalog()]

    assert shared_route.call_count == 1
    assert len(items) == 2


@respx.mock
async def test_crawl_catalog_skips_release_on_404_but_continues(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    page_html = (
        '<div class="item"><a href="/release/missing/gone">Gone</a></div>'
        '<div class="item"><a href="/release/1/only-release">Only</a></div>'
    )
    respx.get(_LABEL_URL, params={"page": "1"}).mock(return_value=httpx.Response(200, text=page_html))
    respx.get(_release_url("/release/missing/gone")).mock(return_value=httpx.Response(404))
    respx.get(_release_url("/release/1/only-release")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 1
    assert items[0]["artist"] == "Bed Maker"


@respx.mock
async def test_crawl_catalog_raises_on_non_404_detail_page_http_error(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    respx.get(_release_url("/release/1/only-release")).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_listing_page_has_no_release_links(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text="<html>empty</html>"))

    with pytest.raises(RuntimeError):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_on_http_error(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_raises_when_entire_crawl_yields_no_vinyl(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    respx.get(_release_url("/release/1/only-release")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _CD_AND_DIGITAL_ONLY)))

    with pytest.raises(RuntimeError):
        [item async for item in Crawler().crawl_catalog()]


@respx.mock
async def test_crawl_catalog_reports_progress_through_each_listing_page_of_detail_fetches(
    monkeypatch, tmp_config_dir
):
    """report_page() alone leaves a whole listing page's worth of paced detail
    fetches -- tens of minutes at the default crawl_delay_seconds, against
    roughly 108 minutes for the whole run -- with nothing reported at all,
    which reads as a hang while the stock sync's advisory lock rejects every
    other source's Refresh."""
    import crawl_progress

    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_1))
    respx.get(_release_url("/release/203/the-mark")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))
    respx.get(_release_url("/release/202/plays")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_VARIOUS_ARTISTS, _ONE_VINYL_BUTTON)))
    for i in range(2, 9):
        respx.get(_LABEL_URL, params={"page": str(i)}).mock(
            return_value=httpx.Response(200, text=_listing_page_with_one_release(f"/release/p{i}/r{i}")))
        respx.get(_release_url(f"/release/p{i}/r{i}")).mock(
            return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    reported = []

    async def reporter(done, total, label):
        reported.append((done, total, label))

    token = crawl_progress.set_detail_reporter(reporter)
    try:
        [item async for item in Crawler().crawl_catalog()]
    finally:
        crawl_progress.reset_detail_reporter(token)

    # The 0/N report lands before the first detail fetch, so the size of the
    # wait is on the record rather than only arriving once a page is done.
    assert reported[:3] == [
        (0, 2, "listing page 1/8"),
        (1, 2, "listing page 1/8"),
        (2, 2, "listing page 1/8"),
    ]
    assert reported[3:5] == [(0, 1, "listing page 2/8"), (1, 1, "listing page 2/8")]
    assert [r for r in reported if r[2] == "listing page 8/8"] == [
        (0, 1, "listing page 8/8"),
        (1, 1, "listing page 8/8"),
    ]


@respx.mock
async def test_crawl_catalog_reports_progress_for_a_release_skipped_on_404(
    monkeypatch, tmp_config_dir
):
    """A 404 skip still advances the fetch count -- otherwise the last report
    of a page could sit below its total forever and read as a stall."""
    import crawl_progress

    save_config({"crawl_delay_seconds": 0})
    page_html = (
        _listing_page_with_one_release("/release/gone/x")
        + _listing_page_with_one_release("/release/here/y")
    )
    respx.get(_LABEL_URL, params={"page": "1"}).mock(return_value=httpx.Response(200, text=page_html))
    respx.get(_release_url("/release/gone/x")).mock(return_value=httpx.Response(404))
    respx.get(_release_url("/release/here/y")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    reported = []

    async def reporter(done, total, label):
        reported.append((done, total, label))

    token = crawl_progress.set_detail_reporter(reporter)
    try:
        items = [item async for item in Crawler().crawl_catalog()]
    finally:
        crawl_progress.reset_detail_reporter(token)

    assert len(items) == 1
    assert reported == [
        (0, 2, "listing page 1/1"),
        (1, 2, "listing page 1/1"),
        (2, 2, "listing page 1/1"),
    ]


@respx.mock
async def test_crawl_catalog_runs_with_no_detail_reporter_installed(monkeypatch, tmp_config_dir):
    """Same contract report_page() already has: a crawler stays directly
    runnable outside a stock sync."""
    save_config({"crawl_delay_seconds": 0})
    respx.get(_LABEL_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, text=_LISTING_PAGE_NO_PAGINATION))
    respx.get(_release_url("/release/1/only-release")).mock(
        return_value=httpx.Response(200, text=_detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)))

    items = [item async for item in Crawler().crawl_catalog()]

    assert len(items) == 1


def test_site_metadata():
    assert Crawler.site_name == "Dischord Records"
    assert Crawler.base_url == "https://dischord.com"
    assert Crawler.crawler_type == "catalog"
    assert Crawler.genre == "punk"
