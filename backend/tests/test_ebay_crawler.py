import logging
import time
import respx
import httpx
import pytest
import ebay_api as ebay_api_module
from crawlers.ebay import Crawler

_TOKEN_RESP = {"access_token": "test-token", "expires_in": 7200}
_ITEM = {
    "title": "Miles Davis Kind of Blue Vinyl LP",
    "itemWebUrl": "https://www.ebay.com/itm/123",
    "price": {"value": "12.99", "currency": "USD"},
    "shippingOptions": [{"shippingCost": {"value": "3.50"}}],
    "condition": "Very Good Plus (VG+)",
}
_RELEASE = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl", "barcode": None}


def _mock_token(mock):
    mock.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))


def _mock_search(mock, items):
    payload = {"itemSummaries": items} if items is not None else {}
    mock.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))


_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


@pytest.fixture(autouse=True)
def reset_token_cache():
    ebay_api_module._token = None
    ebay_api_module._token_expires_at = 0.0
    yield
    ebay_api_module._token = None
    ebay_api_module._token_expires_at = 0.0


@pytest.fixture
def crawler(tmp_config_dir):
    import config as config_module
    cfg = config_module.load_config()
    cfg["ebay_app_id"] = "app-id"
    cfg["ebay_cert_id"] = "cert-id"
    config_module.save_config(cfg)
    return Crawler()


@respx.mock
async def test_search_returns_lowest_price_listing(crawler):
    _mock_token(respx)
    _mock_search(respx, [_ITEM])
    results = await crawler.search(_RELEASE, page=None)
    assert len(results) == 1
    r = results[0]
    assert r["price"] == 12.99
    assert r["shipping"] == 3.50
    assert r["currency"] == "USD"
    assert r["condition"] == "Very Good Plus (VG+)"
    assert r["url"] == "https://www.ebay.com/itm/123"


@respx.mock
async def test_search_url_falls_back_to_legacy_item_id(crawler):
    item_no_web_url = {**_ITEM, "itemWebUrl": None, "legacyItemId": "387423084905"}
    del item_no_web_url["itemWebUrl"]
    _mock_token(respx)
    _mock_search(respx, [item_no_web_url])
    results = await crawler.search(_RELEASE, page=None)
    assert results[0]["url"] == "https://www.ebay.com/itm/387423084905"


@respx.mock
async def test_search_returns_empty_when_no_results(crawler):
    _mock_token(respx)
    _mock_search(respx, None)
    results = await crawler.search(_RELEASE, page=None)
    assert results == []


@respx.mock
async def test_search_returns_empty_when_missing_config(tmp_config_dir):
    # No ebay keys in config
    crawler = Crawler()
    results = await crawler.search(_RELEASE, page=None)
    assert results == []
    assert not respx.calls


@pytest.mark.parametrize("status", [403, 409, 429, 500])
async def test_search_raises_on_http_error(crawler, status):
    """An API error is not the same answer as "this release isn't listed".

    Returning [] here made the two indistinguishable to the crawl manager,
    so an eBay error never reached the consecutive-failure circuit breaker
    on the stock-item path (where an empty result is deliberately excluded
    from the breaker) and the site never cooled off."""
    with respx.mock:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(status, json={}))
        with pytest.raises(httpx.HTTPStatusError):
            await crawler.search(_RELEASE, page=None)


async def test_search_logs_the_error_response_body_at_debug(crawler, caplog):
    """eBay documents no per-status meaning for Browse search failures, so the
    body's errors[] array is the only thing that says *why* a 409 happened --
    a daily quota, a suspended keyset and a transient fault all look identical
    from the status line alone. Same reason shopify_catalog dumps 429 headers
    at debug."""
    body = {"errors": [{"errorId": 12001, "longMessage": "The keyset is suspended."}]}
    with respx.mock:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(409, json=body))
        with caplog.at_level(logging.DEBUG, logger="ebay_api"):
            with pytest.raises(httpx.HTTPStatusError):
                await crawler.search(_RELEASE, page=None)

    debug_messages = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
    assert any("The keyset is suspended." in m for m in debug_messages)
    assert any("12001" in m for m in debug_messages)


async def test_error_response_body_is_logged_as_a_single_line(crawler, caplog):
    """Each app_logs row is one record rendered as one line by the LogViewer,
    so an uncollapsed multi-line body reads as a single unreadable smear there.
    Level filtering itself is unaffected (routers/logs.py filters in SQL on the
    stored level column) -- this is legibility, not visibility."""
    pretty_json = '{\n  "errors": [\n    {\n      "errorId": 12001\n    }\n  ]\n}'
    with respx.mock:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(409, text=pretty_json))
        with caplog.at_level(logging.DEBUG, logger="ebay_api"):
            with pytest.raises(httpx.HTTPStatusError):
                await crawler.search(_RELEASE, page=None)

    body_lines = [r.getMessage() for r in caplog.records if "response body" in r.getMessage()]
    assert len(body_lines) == 1
    assert "\n" not in body_lines[0]
    assert "\r" not in body_lines[0]
    assert "12001" in body_lines[0]  # collapsed, not stripped of content


async def test_error_response_body_logging_is_truncated(crawler, caplog):
    """A non-JSON error page (a proxy's HTML, say) must not dump tens of KB
    into a single app_logs row the viewer has to render."""
    with respx.mock:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
        respx.get(_SEARCH_URL).mock(
            return_value=httpx.Response(503, html="<html>" + ("x" * 50_000) + "</html>")
        )
        with caplog.at_level(logging.DEBUG, logger="ebay_api"):
            with pytest.raises(httpx.HTTPStatusError):
                await crawler.search(_RELEASE, page=None)

    body_lines = [r.getMessage() for r in caplog.records if "response body" in r.getMessage()]
    assert len(body_lines) == 1
    assert len(body_lines[0]) < 3_000


async def test_search_raises_on_request_error(crawler):
    with respx.mock:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
        respx.get(_SEARCH_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        with pytest.raises(httpx.RequestError):
            await crawler.search(_RELEASE, page=None)


@respx.mock
async def test_token_is_cached(crawler):
    _mock_token(respx)
    _mock_search(respx, [_ITEM])
    # Two searches — token endpoint should only be called once
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json={"itemSummaries": [_ITEM]}))
    await crawler.search(_RELEASE, page=None)
    await crawler.search(_RELEASE, page=None)
    token_calls = [c for c in respx.calls if str(c.request.url).startswith(_TOKEN_URL)]
    assert len(token_calls) == 1


@respx.mock
async def test_token_refreshed_when_expired(crawler):
    # Pre-fill with an expired token
    ebay_api_module._token = "old-token"
    ebay_api_module._token_expires_at = time.time() - 1  # already expired
    _mock_token(respx)
    _mock_search(respx, [_ITEM])
    await crawler.search(_RELEASE, page=None)
    token_calls = [c for c in respx.calls if str(c.request.url).startswith(_TOKEN_URL)]
    assert len(token_calls) == 1
    assert ebay_api_module._token == "test-token"


@respx.mock
async def test_search_constrains_category_by_format(crawler):
    _mock_token(respx)
    _mock_search(respx, [_ITEM])
    await crawler.search(_RELEASE, page=None)
    search_call = next(c for c in respx.calls if str(c.request.url).startswith(_SEARCH_URL))
    assert search_call.request.url.params["category_ids"] == "176985"


@respx.mock
async def test_search_omits_category_for_unmapped_format(crawler):
    _mock_token(respx)
    _mock_search(respx, [{**_ITEM, "title": "Miles Davis Kind of Blue Box Set"}])
    await crawler.search({**_RELEASE, "format": "Box Set"}, page=None)
    search_call = next(c for c in respx.calls if str(c.request.url).startswith(_SEARCH_URL))
    assert "category_ids" not in search_call.request.url.params


def test_search_url_format():
    url = Crawler.search_url({"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"})
    assert "collectorschoicemusic" in url
    assert "Miles" in url or "miles" in url.lower()
    assert "Kind" in url or "kind" in url.lower()


def test_search_url_encodes_spaces():
    url = Crawler.search_url({"artist": "The Beatles", "title": "Abbey Road", "format": "Vinyl"})
    assert " " not in url
    assert "collectorschoicemusic" in url



async def test_config_round_trip(tmp_config_dir):
    import config as config_module
    cfg = config_module.load_config()
    cfg["ebay_app_id"] = "my-app-id"
    cfg["ebay_cert_id"] = "my-cert-id"
    config_module.save_config(cfg)
    reloaded = config_module.load_config()
    assert reloaded["ebay_app_id"] == "my-app-id"
    assert reloaded["ebay_cert_id"] == "my-cert-id"
