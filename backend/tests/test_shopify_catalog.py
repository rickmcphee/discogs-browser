import asyncio
import httpx
import respx
import pytest
from config import save_config
from shopify_catalog import (
    _MAX_PAGE, _PAGE_LIMIT, iter_products, has_tag, strip_vendor_prefix, resolve_cover_image,
)

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


@respx.mock
async def test_iter_products_stops_at_the_endpoint_page_ceiling(tmp_config_dir):
    # Shopify's storefront products.json 400s past _MAX_PAGE, so a collection
    # bigger than _MAX_PAGE * _PAGE_LIMIT can only ever be walked in part. What
    # matters is that the part it did walk survives: page _MAX_PAGE + 1 is never
    # requested, and every product already fetched is still yielded.
    save_config({"crawl_delay_seconds": 0})
    for page in range(1, _MAX_PAGE + 1):
        respx.get(_PRODUCTS_URL, params={"limit": "250", "page": str(page)}).mock(
            return_value=_page_response([{"id": page}] * _PAGE_LIMIT)
        )
    over_ceiling = respx.get(
        _PRODUCTS_URL, params={"limit": "250", "page": str(_MAX_PAGE + 1)}
    ).mock(return_value=httpx.Response(400))

    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]

    assert len(products) == _MAX_PAGE * _PAGE_LIMIT
    assert over_ceiling.call_count == 0


@respx.mock
async def test_iter_products_keeps_earlier_pages_when_pagination_400s(tmp_config_dir):
    # The regression this guards: a 400 used to enter the retry budget below,
    # burn consecutive_failure_limit paced requests against a deterministic
    # error, and then raise -- and _sync_stock skips replace_stock_items
    # entirely when a catalog crawl raises, so a store whose pagination ran out
    # this way wrote no rows at all after a full run of healthy page logs.
    save_config({"crawl_delay_seconds": 0, "consecutive_failure_limit": 10})
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=_page_response([{"id": 1}])
    )
    wall = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(
        return_value=httpx.Response(400)
    )

    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]

    assert [p["id"] for p in products] == [1]
    assert wall.call_count == 1


@respx.mock
async def test_iter_products_still_raises_on_a_first_page_400(tmp_config_dir):
    # A 400 on the very first request is a renamed or misspelled collection, not
    # a pagination ceiling. It has to raise: returning empty would let
    # _sync_stock treat the store as legitimately out of stock and let
    # replace_stock_items wipe its whole snapshot.
    save_config({"crawl_delay_seconds": 0, "consecutive_failure_limit": 2})
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(400)
    )

    with pytest.raises(httpx.HTTPStatusError):
        [p async for p in iter_products("https://example.myshopify.test", "vinyl")]


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
async def test_iter_products_raises_immediately_on_429_without_retrying(tmp_config_dir, monkeypatch):
    # Empirically confirmed (stock-sync-429-followup): Shopify's shared platform-edge
    # IP throttle does not clear within a Retry-After-paced retry window, so retrying
    # a 429 -- honoring Retry-After or not -- just burns the whole consecutive_failure_limit
    # budget before giving up anyway. A 429 must raise on the very first occurrence,
    # never counted against consecutive_failure_limit or retried.
    save_config({"crawl_delay_seconds": 0, "consecutive_failure_limit": 3})
    route = respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5"})
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert exc_info.value.response.status_code == 429
    assert route.call_count == 1


@respx.mock
async def test_iter_products_logs_429_response_headers_at_debug_level(tmp_config_dir, caplog):
    save_config({"crawl_delay_seconds": 0, "consecutive_failure_limit": 3})
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5", "X-Shopify-Shop-Api-Call-Limit": "1/40"})
    )
    with caplog.at_level("DEBUG", logger="shopify_catalog"):
        with pytest.raises(httpx.HTTPStatusError):
            [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    debug_records = [r for r in caplog.records if r.levelname == "DEBUG" and "429" in r.message]
    assert len(debug_records) == 1
    assert "retry-after" in debug_records[0].message.lower()
    assert "5" in debug_records[0].message
    assert "x-shopify-shop-api-call-limit" in debug_records[0].message.lower()


@respx.mock
async def test_iter_products_reports_each_fetched_page_to_the_installed_reporter(tmp_config_dir):
    from crawl_progress import set_page_reporter, reset_page_reporter

    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([{"id": 1}, {"id": 2}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([{"id": 3}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))

    reported = []

    async def _report(page, count):
        reported.append((page, count))

    token = set_page_reporter(_report)
    try:
        [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    finally:
        reset_page_reporter(token)

    assert reported == [(1, 2), (2, 1)]


@respx.mock
async def test_iter_products_works_with_no_page_reporter_installed(tmp_config_dir):
    save_config({"crawl_delay_seconds": 0})
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([{"id": 1}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1]
