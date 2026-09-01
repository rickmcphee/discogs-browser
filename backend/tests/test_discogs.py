import pytest
import respx
import httpx
import config
from authlib.oauth1.rfc5849 import client_auth as oauth1_client_auth
from discogs import (
    HTTPStatusError,
    get_identity,
    fetch_collection_fields,
    iter_collection_pages,
    iter_wantlist_pages,
    parse_release,
    fetch_release_barcode,
)

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


@pytest.fixture(autouse=True)
def _oauth_consumer_creds(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "consumer-key")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "consumer-secret")


@respx.mock
def test_get_identity_signs_with_users_own_oauth_token():
    route = respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 1, "username": "alice"})
    )
    result = get_identity("user-token", "user-token-secret")
    assert result["username"] == "alice"
    auth_header = route.calls.last.request.headers["authorization"]
    assert 'oauth_token="user-token"' in auth_header


def _signature(auth_header):
    marker = 'oauth_signature="'
    start = auth_header.index(marker) + len(marker)
    end = auth_header.index('"', start)
    return auth_header[start:end]


@respx.mock
def test_get_identity_signature_changes_with_token_secret(monkeypatch):
    # OAuth1 signatures normally vary request-to-request purely from the
    # nonce/timestamp entropy authlib mixes in, independent of token_secret.
    # Pin both to fixed values (authlib.oauth1.rfc5849.client_auth.sign()
    # calls these as bare module globals, so patching the module attributes
    # here is picked up by ClientAuth.sign for every request) so token_secret
    # is the only thing left that can change the signature.
    monkeypatch.setattr(oauth1_client_auth, "generate_nonce", lambda: "fixed-nonce")
    monkeypatch.setattr(oauth1_client_auth, "generate_timestamp", lambda: "1700000000")

    route = respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 1, "username": "alice"})
    )
    get_identity("user-token", "secret-one")
    header_one = route.calls[-1].request.headers["authorization"]

    get_identity("user-token", "secret-two")
    header_two = route.calls[-1].request.headers["authorization"]

    assert _signature(header_one) != _signature(header_two)


@respx.mock
def test_get_identity_raises_runtime_error_when_consumer_credentials_missing(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    with pytest.raises(RuntimeError):
        get_identity("user-token", "user-token-secret")


@respx.mock
def test_get_identity_raises_on_bad_token():
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(401, json={"message": "Invalid token."})
    )
    with pytest.raises(HTTPStatusError):
        get_identity("badtoken", "badtoken-secret")


@respx.mock
def test_fetch_collection_fields_signs_with_users_own_oauth_token():
    route = respx.get("https://api.discogs.com/users/testuser/collection/fields").mock(
        return_value=httpx.Response(200, json={"fields": [{"id": 1, "name": "Price"}]})
    )
    fields = fetch_collection_fields("user-token", "user-token-secret", "testuser")
    assert fields == {1: "Price"}
    auth_header = route.calls.last.request.headers["authorization"]
    assert 'oauth_token="user-token"' in auth_header


@respx.mock
def test_iter_collection_pages_signs_with_users_own_oauth_token():
    route = respx.get(_COLLECTION_URL).mock(
        return_value=httpx.Response(200, json={
            "pagination": {"page": 1, "pages": 1, "per_page": 100, "items": 1},
            "releases": [_ITEM],
        })
    )
    pages = list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    assert len(pages) == 1
    page, total_pages, items = pages[0]
    assert page == 1
    assert total_pages == 1
    assert len(items) == 1
    assert items[0]["basic_information"]["title"] == "Kind of Blue"
    auth_header = route.calls.last.request.headers["authorization"]
    assert 'oauth_token="user-token"' in auth_header


@respx.mock
def test_iter_collection_pages_multi_page():
    def handler(request):
        p = int(request.url.params.get("page", 1))
        return httpx.Response(200, json={
            "pagination": {"page": p, "pages": 2, "per_page": 100, "items": 2},
            "releases": [_ITEM],
        })
    respx.get(_COLLECTION_URL).mock(side_effect=handler)
    pages = list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    assert len(pages) == 2
    assert pages[0][0] == 1
    assert pages[1][0] == 2


@respx.mock
def test_iter_wantlist_pages_signs_with_users_own_oauth_token():
    route = respx.get(_WANTLIST_URL).mock(
        return_value=httpx.Response(200, json={
            "pagination": {"page": 1, "pages": 1, "per_page": 100, "items": 1},
            "wants": [_ITEM],
        })
    )
    pages = list(iter_wantlist_pages("user-token", "user-token-secret", "testuser"))
    assert len(pages) == 1
    page, total_pages, items = pages[0]
    assert page == 1
    assert total_pages == 1
    assert len(items) == 1
    assert items[0]["basic_information"]["title"] == "Kind of Blue"
    auth_header = route.calls.last.request.headers["authorization"]
    assert 'oauth_token="user-token"' in auth_header


@respx.mock
def test_iter_wantlist_pages_multi_page():
    def handler(request):
        p = int(request.url.params.get("page", 1))
        return httpx.Response(200, json={
            "pagination": {"page": p, "pages": 2, "per_page": 100, "items": 2},
            "wants": [_ITEM],
        })
    respx.get(_WANTLIST_URL).mock(side_effect=handler)
    pages = list(iter_wantlist_pages("user-token", "user-token-secret", "testuser"))
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
    # Named for what it is. No discogs_price key remains -- that name read as a
    # marketplace figure, and the misreading is what put it on a global column.
    assert parsed["price_paid"] is None
    assert "discogs_price" not in parsed


def _item_with_note(field_id, value):
    return {
        "basic_information": {
            "id": 456, "title": "Kind of Blue", "year": 1959,
            "artists": [{"name": "Miles Davis"}], "labels": [{"name": "Columbia"}],
            "formats": [{"name": "Vinyl"}], "cover_image": "",
        },
        "notes": [{"field_id": field_id, "value": value}],
    }


def test_parse_release_reads_the_matched_custom_field_into_price_paid():
    assert parse_release(_item_with_note(7, "42.50"), price_field_id=7)["price_paid"] == "42.50"


def test_parse_release_price_paid_is_none_when_the_user_has_no_price_field():
    # The exact condition that caused the data loss: no field named "Price", so
    # price_field_id is None and nothing is read.
    assert parse_release(_item_with_note(7, "42.50"), price_field_id=None)["price_paid"] is None


def test_parse_release_price_paid_is_none_when_the_field_is_empty():
    # An empty custom field is a cleared price, not a missing one -- the
    # collection-sync call site passes this None through as an authoritative clear.
    assert parse_release(_item_with_note(7, ""), price_field_id=7)["price_paid"] is None


@respx.mock
def test_fetch_release_barcode_signs_with_users_own_oauth_token():
    route = respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={
        "identifiers": [{"type": "Barcode", "value": "0 25218 14252 6"}]
    }))
    assert fetch_release_barcode("user-token", "user-token-secret", 456) == "025218142526"
    auth_header = route.calls.last.request.headers["authorization"]
    assert 'oauth_token="user-token"' in auth_header


@respx.mock
def test_fetch_release_barcode_strips_non_digits():
    respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={
        "identifiers": [{"type": "Barcode", "value": "ABC-123 456"}]
    }))
    assert fetch_release_barcode("user-token", "user-token-secret", 456) == "123456"


@respx.mock
def test_fetch_release_barcode_returns_empty_when_absent():
    respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={
        "identifiers": [{"type": "Matrix / Runout", "value": "SomeMatrix"}]
    }))
    assert fetch_release_barcode("user-token", "user-token-secret", 456) == ""


@respx.mock
def test_fetch_release_barcode_returns_empty_when_no_identifiers():
    respx.get(_RELEASE_URL).mock(return_value=httpx.Response(200, json={}))
    assert fetch_release_barcode("user-token", "user-token-secret", 456) == ""
