import time
import respx
import httpx
import pytest
import crawlers.ebay as ebay_module
from crawlers.ebay import Crawler

_TOKEN_RESP = {"access_token": "test-token", "expires_in": 7200}
_ITEM = {
    "itemWebUrl": "https://www.ebay.com/itm/123",
    "price": {"value": "12.99", "currency": "USD"},
    "shippingOptions": [{"shippingCost": {"value": "3.50"}}],
    "condition": "Very Good Plus (VG+)",
}
_RELEASE = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"}


def _mock_token(mock):
    mock.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))


def _mock_search(mock, items):
    payload = {"itemSummaries": items} if items is not None else {}
    mock.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))


_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


@pytest.fixture(autouse=True)
def reset_token_cache():
    ebay_module._token = None
    ebay_module._token_expires_at = 0.0
    yield
    ebay_module._token = None
    ebay_module._token_expires_at = 0.0


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


async def test_search_returns_empty_on_http_error(crawler):
    with respx.mock:
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(403, json={}))
        results = await crawler.search(_RELEASE, page=None)
        assert results == []


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
    ebay_module._token = "old-token"
    ebay_module._token_expires_at = time.time() - 1  # already expired
    _mock_token(respx)
    _mock_search(respx, [_ITEM])
    await crawler.search(_RELEASE, page=None)
    token_calls = [c for c in respx.calls if str(c.request.url).startswith(_TOKEN_URL)]
    assert len(token_calls) == 1
    assert ebay_module._token == "test-token"


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
