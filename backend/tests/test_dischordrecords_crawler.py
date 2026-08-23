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
