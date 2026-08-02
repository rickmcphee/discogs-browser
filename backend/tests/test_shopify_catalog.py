import asyncio
import httpx
import respx
import pytest
from config import save_config
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PRODUCTS_URL = "https://example.myshopify.test/collections/vinyl/products.json"


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@respx.mock
async def test_iter_products_yields_each_product_across_pages(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([{"id": 1}, {"id": 2}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([{"id": 3}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1, 2, 3]


@respx.mock
async def test_iter_products_stops_on_first_empty_page(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert products == []


@respx.mock
async def test_iter_products_uses_configured_crawl_delay_seconds(tmp_config_dir, monkeypatch):
    save_config({"crawl_delay_seconds": 40, "consecutive_failure_limit": 10})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("shopify_catalog.sleep", fake_sleep)
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([]))
    [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert sleep_calls
    assert all(20 <= s <= 40 for s in sleep_calls)


@respx.mock
async def test_iter_products_retries_after_transient_failure(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0, "consecutive_failure_limit": 3})
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"})
    route.side_effect = [httpx.Response(503), _page_response([{"id": 1}])]
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1]
    assert route.call_count == 2


@respx.mock
async def test_iter_products_raises_after_consecutive_failure_limit_reached(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0, "consecutive_failure_limit": 2})
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert route.call_count == 2


@respx.mock
async def test_iter_products_fails_fast_when_failure_limit_is_zero(tmp_config_dir):
    # consecutive_failure_limit=0 means "disabled" elsewhere in this codebase, but a
    # disabled limit must not mean unlimited retries here — unlike crawl_releases,
    # this loop has no next item to fall through to, so it would retry forever.
    save_config({"crawl_delay_seconds": 0, "consecutive_failure_limit": 0})
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))

    async def _collect():
        return [p async for p in iter_products("https://example.myshopify.test", "vinyl")]

    with pytest.raises(httpx.HTTPStatusError):
        await asyncio.wait_for(_collect(), timeout=1.0)
    assert route.call_count == 1


def test_has_tag_matches_case_insensitively():
    assert has_tag({"tags": ["Pre-Order", "vinyl"]}, "pre-order") is True


def test_has_tag_false_when_absent():
    assert has_tag({"tags": ["vinyl"]}, "pre-order") is False


def test_has_tag_false_when_tags_missing():
    assert has_tag({}, "pre-order") is False


def test_has_tag_false_when_tags_is_none():
    assert has_tag({"tags": None}, "pre-order") is False


def test_strip_vendor_prefix_removes_matching_prefix():
    assert strip_vendor_prefix("NAILS - Every Bridge Burning", "NAILS") == "Every Bridge Burning"


def test_strip_vendor_prefix_keeps_title_when_no_match():
    assert strip_vendor_prefix(
        "Hackett & Rothery - The Roaring Waves - LP", "Steve Hackett"
    ) == "Hackett & Rothery - The Roaring Waves - LP"


def test_resolve_cover_image_prefers_variant_featured_image():
    product = {"images": [{"src": "https://x/fallback.png"}]}
    variant = {"featured_image": {"src": "https://x/variant.png"}}
    assert resolve_cover_image(product, variant) == "https://x/variant.png"


def test_resolve_cover_image_falls_back_to_product_image():
    product = {"images": [{"src": "https://x/fallback.png"}]}
    variant = {}
    assert resolve_cover_image(product, variant) == "https://x/fallback.png"


def test_resolve_cover_image_none_when_neither_present():
    assert resolve_cover_image({"images": []}, {}) is None


@respx.mock
async def test_iter_products_respects_retry_after_header_on_429(tmp_config_dir, monkeypatch):
    save_config({"crawl_delay_seconds": 30, "consecutive_failure_limit": 3})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("shopify_catalog.sleep", fake_sleep)
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"})
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "5"}),
        _page_response([{"id": 1}]),
    ]
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1]
    # sleep_calls[0] is the pre-request delay before the first (failing) attempt;
    # sleep_calls[1] is the delay before the retry, which must be exactly Retry-After.
    assert sleep_calls[1] == 5.0


@respx.mock
async def test_iter_products_falls_back_to_jitter_when_429_has_no_retry_after(tmp_config_dir, monkeypatch):
    save_config({"crawl_delay_seconds": 40, "consecutive_failure_limit": 3})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("shopify_catalog.sleep", fake_sleep)
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"})
    route.side_effect = [httpx.Response(429), _page_response([{"id": 1}])]
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1]
    assert 20 <= sleep_calls[1] <= 40


@respx.mock
async def test_iter_products_caps_retry_after_at_600_seconds(tmp_config_dir, monkeypatch):
    save_config({"crawl_delay_seconds": 30, "consecutive_failure_limit": 3})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("shopify_catalog.sleep", fake_sleep)
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"})
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "99999"}),
        _page_response([]),
    ]
    [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert sleep_calls[1] == 600.0


def test_parse_retry_after_returns_none_for_missing_invalid_or_negative():
    from shopify_catalog import _parse_retry_after
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("not-a-number") is None
    assert _parse_retry_after("-5") is None


def test_parse_retry_after_passes_through_a_valid_value():
    from shopify_catalog import _parse_retry_after
    assert _parse_retry_after("5") == 5.0


def test_parse_retry_after_caps_at_max():
    from shopify_catalog import _parse_retry_after
    assert _parse_retry_after("99999") == 600.0


def test_parse_retry_after_returns_none_for_nan_and_infinity():
    from shopify_catalog import _parse_retry_after
    assert _parse_retry_after("nan") is None
    assert _parse_retry_after("inf") is None
