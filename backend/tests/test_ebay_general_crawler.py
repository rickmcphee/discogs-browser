import respx
import httpx
import pytest
import ebay_api as ebay_api_module
from crawlers.ebay_general import Crawler

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_TOKEN_RESP = {"access_token": "test-token", "expires_in": 7200}
_ITEM = {
    "title": "Miles Davis Kind of Blue Vinyl LP",
    "itemWebUrl": "https://www.ebay.com/itm/456",
    "price": {"value": "9.99", "currency": "USD"},
    "shippingOptions": [{"shippingCost": {"value": "4.00"}}],
    "condition": "Very Good (VG)",
}
_RELEASE = {"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl", "barcode": None}


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
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json={"itemSummaries": [_ITEM]}))
    results = await crawler.search(_RELEASE, page=None)
    assert results == [{
        "url": "https://www.ebay.com/itm/456",
        "price": 9.99,
        "shipping": 4.00,
        "currency": "USD",
        "condition": "Very Good (VG)",
    }]


@respx.mock
async def test_search_omits_seller_filter_and_raises_limit(crawler):
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json=_TOKEN_RESP))
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json={"itemSummaries": [_ITEM]}))
    await crawler.search(_RELEASE, page=None)
    search_call = next(c for c in respx.calls if str(c.request.url).startswith(_SEARCH_URL))
    assert "sellers:" not in search_call.request.url.params["filter"]
    assert search_call.request.url.params["limit"] == "5"


@respx.mock
async def test_search_returns_empty_when_missing_config(tmp_config_dir):
    crawler = Crawler()
    results = await crawler.search(_RELEASE, page=None)
    assert results == []
    assert not respx.calls


def test_site_name_is_ebay():
    assert Crawler.site_name == "eBay"


def test_search_url_has_no_seller_path():
    url = Crawler.search_url({"artist": "Miles Davis", "title": "Kind of Blue", "format": "Vinyl"})
    assert "collectorschoicemusic" not in url
    assert url.startswith("https://www.ebay.com/sch/i.html?_nkw=")
    assert "Miles" in url or "miles" in url.lower()
