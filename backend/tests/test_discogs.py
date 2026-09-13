import logging

import pytest
import respx
import httpx
import config
import discogs
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


# --- Rate-limit (HTTP 429) retry ------------------------------------------
#
# See docs/specifications/shaping/2026-09-13-discogs-api-429-retry-design.md.
# A sync runs just under Discogs' 60 requests/minute, so a 429 is a matter of
# timing rather than misbehaviour -- and before this, one on a page fetch ended
# the whole sync and abandoned every page after it.


@pytest.fixture
def slept(monkeypatch):
    """Records what _get_with_retry would have slept, without spending it.

    Same module-local-`sleep` patch convention conftest.py uses to keep the
    catalog-crawler tests off the wall clock.
    """
    waits = []
    monkeypatch.setattr(discogs, "sleep", waits.append)
    return waits


def _collection_page(page=1, pages=1):
    return httpx.Response(200, json={
        "pagination": {"page": page, "pages": pages, "per_page": 100, "items": 1},
        "releases": [_ITEM],
    })


def _rate_limited(retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return httpx.Response(429, headers=headers, json={"message": "You are making requests too quickly."})


@respx.mock
def test_iter_collection_pages_retries_a_rate_limited_page_and_continues(slept):
    # The failure this whole change exists for: a 429 mid-walk used to
    # propagate out of the generator and end the sync, losing every page after
    # this one -- not just the page that got the 429.
    respx.get(_COLLECTION_URL).mock(side_effect=[
        _collection_page(page=1, pages=2),
        _rate_limited(),
        _collection_page(page=2, pages=2),
    ])
    pages = list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    assert [p[0] for p in pages] == [1, 2]
    assert slept == [5.0]


@respx.mock
def test_iter_wantlist_pages_retries_a_rate_limited_page_and_continues(slept):
    respx.get(_WANTLIST_URL).mock(side_effect=[
        _rate_limited(),
        httpx.Response(200, json={
            "pagination": {"page": 1, "pages": 1, "per_page": 100, "items": 1},
            "wants": [_ITEM],
        }),
    ])
    pages = list(iter_wantlist_pages("user-token", "user-token-secret", "testuser"))
    assert len(pages) == 1
    assert slept == [5.0]


@respx.mock
def test_a_persistent_429_gives_up_after_the_capped_retries(slept):
    route = respx.get(_COLLECTION_URL).mock(return_value=_rate_limited())
    with pytest.raises(HTTPStatusError) as exc:
        list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    # Same exception type the caller saw before any of this: a page walk still
    # reaches _sync_collection_blocking's handler, it just takes a bounded wait
    # to get there.
    assert exc.value.response.status_code == 429
    assert len(route.calls) == 1 + discogs._MAX_RETRIES
    assert slept == [5.0, 20.0, 60.0]


@respx.mock
def test_retry_after_header_is_honoured_in_place_of_the_backoff(slept):
    respx.get(_COLLECTION_URL).mock(side_effect=[
        _rate_limited(retry_after="7"),
        _collection_page(),
    ])
    list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    assert slept == [7.0]


@respx.mock
def test_retry_after_is_capped_at_one_rate_limit_window(slept):
    # A stated wait far past the documented 60s window is describing something
    # other than that window, and sleeping it would be indistinguishable from
    # the hang this is meant to prevent.
    respx.get(_COLLECTION_URL).mock(side_effect=[
        _rate_limited(retry_after="99999"),
        _collection_page(),
    ])
    list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    assert slept == [discogs._MAX_RETRY_WAIT]


@pytest.mark.parametrize("retry_after", ["soon", "-5", "Wed, 21 Oct 2026 07:28:00 GMT", ""])
@respx.mock
def test_an_unusable_retry_after_falls_back_to_the_backoff(slept, retry_after):
    respx.get(_COLLECTION_URL).mock(side_effect=[
        _rate_limited(retry_after=retry_after),
        _collection_page(),
    ])
    list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    assert slept == [5.0]


@respx.mock
def test_a_non_429_error_status_raises_immediately_without_retrying(slept):
    # The caller-semantics guarantee: _sync_collection_blocking branches on
    # HTTPStatusError around fetch_collection_fields, and a 500 must not be
    # sat on for a minute and a half first.
    route = respx.get(_COLLECTION_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(HTTPStatusError):
        list(iter_collection_pages("user-token", "user-token-secret", "testuser"))
    assert len(route.calls) == 1
    assert slept == []


@respx.mock
def test_fetch_release_barcode_retries_a_rate_limited_request(slept):
    respx.get(_RELEASE_URL).mock(side_effect=[
        _rate_limited(retry_after="3"),
        httpx.Response(200, json={"identifiers": [{"type": "Barcode", "value": "025218142526"}]}),
    ])
    assert fetch_release_barcode("user-token", "user-token-secret", 456) == "025218142526"
    assert slept == [3.0]


@respx.mock
def test_get_identity_retries_a_rate_limited_request(slept):
    respx.get("https://api.discogs.com/oauth/identity").mock(side_effect=[
        _rate_limited(),
        httpx.Response(200, json={"id": 1, "username": "alice"}),
    ])
    assert get_identity("user-token", "user-token-secret")["username"] == "alice"
    assert slept == [5.0]


@respx.mock
def test_fetch_collection_fields_retries_a_rate_limited_request(slept):
    respx.get("https://api.discogs.com/users/testuser/collection/fields").mock(side_effect=[
        _rate_limited(),
        httpx.Response(200, json={"fields": [{"id": 1, "name": "Price"}]}),
    ])
    assert fetch_collection_fields("user-token", "user-token-secret", "testuser") == {1: "Price"}
    assert slept == [5.0]


@respx.mock
def test_a_retry_logs_at_warning_and_giving_up_logs_at_error(slept, caplog):
    respx.get(_COLLECTION_URL).mock(return_value=_rate_limited(retry_after="7"))
    with caplog.at_level(logging.WARNING, logger="discogs"):
        with pytest.raises(HTTPStatusError):
            list(iter_collection_pages("user-token", "user-token-secret", "testuser"))

    warnings = [r for r in caplog.records
                if r.name == "discogs" and r.levelno == logging.WARNING]
    assert len(warnings) == discogs._MAX_RETRIES
    first = warnings[0].getMessage()
    assert "429" in first and "Retry-After: 7" in first and "retry 1/3" in first

    errors = [r for r in caplog.records
              if r.name == "discogs" and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "giving up" in errors[0].getMessage()
