import pytest
import respx
import httpx
from discogs import get_identity, fetch_collection, parse_release


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
def test_fetch_collection_single_page():
    respx.get("https://api.discogs.com/users/testuser/collection/folders/0/releases").mock(
        return_value=httpx.Response(200, json={
            "pagination": {"page": 1, "pages": 1, "per_page": 100, "items": 1},
            "releases": [{
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
            }]
        })
    )
    releases = fetch_collection("mytoken", "testuser")
    assert len(releases) == 1
    assert releases[0]["basic_information"]["title"] == "Kind of Blue"


def test_parse_release():
    raw = {
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
    parsed = parse_release(raw)
    assert parsed["discogs_id"] == "r456"
    assert parsed["artist"] == "Miles Davis"
    assert parsed["title"] == "Kind of Blue"
    assert parsed["year"] == 1959
    assert parsed["label"] == "Columbia"
    assert parsed["format"] == "Vinyl"
    assert parsed["discogs_url"] == "https://www.discogs.com/release/456"
