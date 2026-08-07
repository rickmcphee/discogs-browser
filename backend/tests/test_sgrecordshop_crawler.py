import json
from pathlib import Path

from crawlers.sgrecordshop import Crawler

FIXTURES = Path(__file__).parent / "fixtures" / "crawlers" / "sgrecordshop"


def _load_fragment(name):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload["data"]["data"]


def test_parse_items_returns_one_item_per_available_block():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    assert len(items) == 3


def test_parse_items_parses_normal_single_artist_item():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "26467060")
    assert item["artist"] == "Kylie Minogue"
    assert item["title"] == "Aphrodite"
    assert item["format"] == "Vinyl LP"
    assert item["price"] == 24.99
    assert item["currency"] == "USD"
    assert item["url"] == "https://www.sgrecordshop.com/p/26467060/"
    assert item["cover_image_url"] == "https://cache.fieldstackintelligence.com/images/2644/13220690-T.JPG"


def test_parse_items_splits_multi_artist_slash_correctly():
    # "21 Savage / Metro Boomin/Savage Mode Ii" -- the artist itself contains
    # "/", so a naive first-"/" split would cut mid-artist. The product-title
    # span ("21 Savage  /  Metro Boomin") is used as the known prefix instead.
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "25883436")
    assert item["artist"] == "21 Savage / Metro Boomin"
    assert item["title"] == "Savage Mode Ii"


def test_parse_items_handles_with_abbreviation_inside_variant_text():
    # "Elephant's Memory/Take It to the Streets (CLEAR W/ BLACK SWIRL
    # VINYL)@Remastered" -- "W/" here abbreviates "with" inside the color
    # variant, not a delimiter. A naive last-"/" split (the opposite fix from
    # the multi-artist case above) would wrongly cut this one instead.
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    item = next(i for i in items if i["pid"] == "26472934")
    assert item["artist"] == "Elephant's Memory"
    assert item["title"] == "Take It to the Streets (CLEAR W/ BLACK SWIRL VINYL)"


def test_parse_items_excludes_unavailable_item():
    items = Crawler._parse_items(_load_fragment("rock_pop_indie_page1.json"))
    assert not any(i["pid"] == "26427979" for i in items)


import httpx
import pytest
import respx
from config import save_config


_METAL_HTML = """
<script type="text/javascript">
    $(document).ready(function () {
        searchFilterable.init({
                CategoryId: "2728",
                SearchId: '11111111-1111-1111-1111-111111111111',
                PageNumber: "1"
        });
    });
</script>
"""

_ELECTRONIC_HTML = """
<script type="text/javascript">
    $(document).ready(function () {
        searchFilterable.init({
                CategoryId: "2738",
                SearchId: '22222222-2222-2222-2222-222222222222',
                PageNumber: "1"
        });
    });
</script>
"""


def _gsrp_response(fragment_html, page_number, total_pages, count):
    return httpx.Response(200, json={
        "success": True,
        "data": {
            "data": fragment_html,
            "itemcount": f"<div>1-{count} of {count} results</div>",
            "pageNumber": page_number,
            "totalPages": total_pages,
        },
    })


@respx.mock
async def test_crawl_catalog_scrapes_search_id_and_yields_parsed_items(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    assert {i["pid"] for i in items} == {"26467060", "25883436", "26472934"}


@respx.mock
async def test_crawl_catalog_paginates_within_a_category(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    page1_route = respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 2, 6))
    page2_route = respx.get(
        "https://www.sgrecordshop.com/gsrp/2",
        params={"so": "9", "af": "-10|-2003|-2", "page": "2"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 2, 2, 6))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    # both pages serve the same 3 pids -- this proves both page requests
    # actually fired (call_count), while dedup correctly still collapses
    # the result to 3, not 6. (An earlier draft of this test asserted
    # len(items) == 6 here, which is wrong: dedup collapsing same-category
    # repeat pids to 3 is correct behavior, not a bug -- caught by actually
    # running this test during plan-writing, not by inspection.)
    assert page1_route.call_count == 1
    assert page2_route.call_count == 1
    assert len(items) == 3


@respx.mock
async def test_crawl_catalog_dedupes_same_pid_across_categories(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", [
        "/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2",
        "/c/2738/record-shop-electronic?&so=9&af=-10|-2003|-2013|-2",
    ])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    respx.get("https://www.sgrecordshop.com/c/2738/record-shop-electronic", params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_ELECTRONIC_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"},
        headers={"X-Search-Guid": "22222222-2222-2222-2222-222222222222"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    # both categories serve the same 3 pids -- dedup means 3, not 6
    assert len(items) == 3


@respx.mock
async def test_crawl_catalog_skips_category_with_no_search_id(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", [
        "/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2",
        "/c/2738/record-shop-electronic?&so=9&af=-10|-2003|-2013|-2",
    ])
    fragment = _load_fragment("rock_pop_indie_page1.json")

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text="<html>no search id here</html>")
    )
    respx.get("https://www.sgrecordshop.com/c/2738/record-shop-electronic", params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_ELECTRONIC_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2013|-2", "page": "1"},
        headers={"X-Search-Guid": "22222222-2222-2222-2222-222222222222"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    items = [item async for item in crawler.crawl_catalog()]
    # first category has no SearchId and is skipped; second still yields
    assert len(items) == 3


@respx.mock
async def test_crawl_catalog_sleeps_between_requests_using_configured_delay(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 40})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])
    fragment = _load_fragment("rock_pop_indie_page1.json")
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("crawlers.sgrecordshop.sleep", fake_sleep)
    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=_gsrp_response(fragment, 1, 1, 3))

    crawler = Crawler()
    [item async for item in crawler.crawl_catalog()]
    assert len(sleep_calls) == 2
    assert all(20 <= s <= 40 for s in sleep_calls)


@respx.mock
async def test_crawl_catalog_raises_on_http_error(monkeypatch, tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    monkeypatch.setattr(Crawler, "_CATEGORIES", ["/c/2728/record-shop-metal?&so=9&af=-10|-2003|-2"])

    respx.get("https://www.sgrecordshop.com/c/2728/record-shop-metal", params={"so": "9", "af": "-10|-2003|-2", "page": "1"}).mock(
        return_value=httpx.Response(200, text=_METAL_HTML)
    )
    respx.get(
        "https://www.sgrecordshop.com/gsrp/1",
        params={"so": "9", "af": "-10|-2003|-2", "page": "1"},
        headers={"X-Search-Guid": "11111111-1111-1111-1111-111111111111"},
    ).mock(return_value=httpx.Response(500))

    crawler = Crawler()
    with pytest.raises(httpx.HTTPStatusError):
        [item async for item in crawler.crawl_catalog()]


def test_site_metadata():
    assert Crawler.site_name == "The Sound Garden"
    assert Crawler.base_url == "https://www.sgrecordshop.com"
    assert Crawler.crawler_type == "catalog"
    assert len(Crawler._CATEGORIES) == 14
