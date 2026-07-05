import httpx
import respx
import pytest
from shopify_catalog import iter_products, has_tag, strip_vendor_prefix, resolve_cover_image

_PRODUCTS_URL = "https://example.myshopify.test/collections/vinyl/products.json"


def _page_response(products):
    return httpx.Response(200, json={"products": products})


@respx.mock
async def test_iter_products_yields_each_product_across_pages():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([{"id": 1}, {"id": 2}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "2"}).mock(return_value=_page_response([{"id": 3}]))
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "3"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert [p["id"] for p in products] == [1, 2, 3]


@respx.mock
async def test_iter_products_stops_on_first_empty_page():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=_page_response([]))
    products = [p async for p in iter_products("https://example.myshopify.test", "vinyl")]
    assert products == []


@respx.mock
async def test_iter_products_raises_on_http_error():
    respx.get(_PRODUCTS_URL, params={"limit": "250", "page": "1"}).mock(return_value=httpx.Response(503))
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
