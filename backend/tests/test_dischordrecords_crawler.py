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


import pytest


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


def test_parse_release_single_vinyl_format():
    html = _detail_page(_H1_SINGLE, _ONE_VINYL_BUTTON)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert items == [{
        "artist": "Bed Maker",
        "title": "The Mark",
        "format": "Vinyl",
        "price": 8.0,
        "currency": "USD",
        "url": "https://dischord.com/release/203/the-mark",
        "cover_image_url": "https://s3.amazonaws.com/x/cover.jpg",
    }]


def test_parse_release_various_artists_band_link():
    html = _detail_page(_H1_VARIOUS_ARTISTS, _ONE_VINYL_BUTTON)
    items = Crawler._parse_release(html, "/release/202/plays")
    assert items[0]["artist"] == "Various Artists"
    assert items[0]["title"] == "Plays"


def test_parse_release_skips_non_vinyl_formats():
    html = _detail_page(_H1_SINGLE, _MULTI_FORMAT_BUTTONS)
    items = Crawler._parse_release(html, "/release/203/the-mark")
    assert len(items) == 1
    assert items[0]["price"] == 18.0


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
