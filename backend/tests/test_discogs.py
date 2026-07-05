import pytest
import respx
import httpx
from discogs import get_identity, iter_collection_pages, iter_wantlist_pages, parse_release, fetch_release_barcode

_RELEASE_URL = "https://api.discogs.com/releases/456"

_COLLECTION_URL = "https://api.discogs.com/users/testuser/collection/folders/0/releases"

_WANTLIST_URL = "https://api.discogs.com/users/testuser/wants"

_ITEM = {
    "id": 1,
    "basic_information": {
        "id": 456,
        "title": "Kind of Blue",
        "year": 1959,
        "artists": [{"name": "Miles Davis"}],
        "labels": [{"name": "Columbia"}],
        "formats": [{"name": "Vinyl"}],
        "cover_image": "https://example.com/img.jpg",
        "resource_url": "https://api.discogs.com/releases/456",
    }
}


@respx.mock
def test_get_identity_returns_username():
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 1, "username": "testuser"})
    )
    result = get_identity("mytoken")
    assert result["username"] == "testuser"


@respx.mock
def test_get_identity_raises_on_bad_token():
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(401, json={"message": "Invalid token."})
    )
    with pytest.raises(httpx.HTTPStatusError):
        get_identity("badtoken")


@respx.mock
def test_iter_collection_pages_single_page():
    respx.get(_COLLECTION_URL).mock(
        return_value=httpx.Response(200, json={
            "pagination": {"page": 1, "pages": 1, "per_page": 100, "items": 1},
            "releases": [_ITEM],
        })
    )
    pages = list(iter_collection_pages("mytoken", "testuser"))
    assert len(pages) == 1
    page, total_pages, items = pages[0]
    assert page == 1
    assert total_pages == 1
    assert len(items) == 1
    assert items[0]["basic_information"]["title"] == "Kind of Blue"


@respx.mock
def test_iter_collection_pages_multi_page():
    def handler(request):
        p = int(request.url.params.get("page", 1))
        return httpx.Response(200, json={
            "pagination": {"page": p, "pages": 2, "per_page": 100, "items": 2},
            "releases": [_ITEM],
        })
    respx.get(_COLLECTION_URL).mock(side_effect=handler)
    pages = list(iter_collection_pages("mytoken", "testuser"))
    assert len(pages) == 2
    assert pages[0][0] == 1
    assert pages[1][0] == 2


@respx.mock
def test_iter_wantlist_pages_single_page():
    respx.get(_WANTLIST_URL).mock(
        return_value=httpx.Response(200, json={
            "pagination": {"page": 1, "pages": 1, "per_page": 100, "items": 1},
            "wants": [_ITEM],
        })
    )
    pages = list(iter_wantlist_pages("mytoken", "testuser"))
    assert len(pages) == 1
    page, total_pages, items = pages[0]
    assert page == 1
    assert total_pages == 1
    assert len(items) == 1
    assert items[0]["basic_information"]["title"] == "Kind of Blue"


@respx.mock
def test_iter_wantlist_pages_multi_page():
    def handler(request):
        p = int(request.url.params.get("page", 1))
        return httpx.Response(200, json={
            "pagination": {"page": p, "pages": 2, "per_page": 100, "items": 2},
            "wants": [_ITEM],
        })
    respx.get(_WANTLIST_URL).mock(side_effect=handler)
    pages = list(iter_wantlist_pages("mytoken", "testuser"))
    assert len(pages) == 2
    assert pages[0][0] == 1
    assert pages[1][0] == 2


def test_parse_release():
    parsed = parse_release(_ITEM)
    assert parsed["discogs_id"] == "r456"
    assert parsed["artist"] == "Miles Davis"
    assert parsed["title"] == "Kind of Blue"
    assert parsed["year"] == 1959
    assert parsed["label"] == "Columbia"
    assert parsed["format"] == "Vinyl"
    assert parsed["discogs_url"] == "https://www.discogs.com/release/456"
    assert parsed["barcode"] is None


@respx.mock
def test_fetch_release_barcode_returns_digits():
    respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={
        "identifiers": [{"type": "Barcode", "value": "0 25218 14252 6"}]
    }))
    assert fetch_release_barcode("token", 456) == "025218142526"


@respx.mock
def test_fetch_release_barcode_strips_non_digits():
    respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={
        "identifiers": [{"type": "Barcode", "value": "ABC-123 456"}]
    }))
    assert fetch_release_barcode("token", 456) == "123456"


@respx.mock
def test_fetch_release_barcode_returns_empty_when_absent():
    respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={
        "identifiers": [{"type": "Matrix / Runout", "value": "SomeMatrix"}]
    }))
    assert fetch_release_barcode("token", 456) == ""


@respx.mock
def test_fetch_release_barcode_returns_empty_when_no_identifiers():
    respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={}))
    assert fetch_release_barcode("token", 456) == ""
