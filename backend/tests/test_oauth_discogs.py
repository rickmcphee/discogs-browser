import httpx
import pytest
import respx

import config
import oauth_discogs


@pytest.fixture(autouse=True)
def _consumer_credentials(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "consumer-key")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "consumer-secret")
    monkeypatch.setattr(config, "BACKEND_BASE_URL", "http://localhost:8000")


@respx.mock
def test_start_handshake_returns_token_secret_and_authorize_url():
    route = respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=req-token-123&oauth_token_secret=req-secret-456",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )

    result = oauth_discogs.start_handshake()

    assert result["oauth_token"] == "req-token-123"
    assert result["oauth_token_secret"] == "req-secret-456"
    assert "req-token-123" in result["authorize_url"]
    assert result["authorize_url"].startswith("https://www.discogs.com/oauth/authorize")

    # the whole point of this task's amendment: confirm oauth_callback was
    # actually sent, not just that the call succeeded against a mock that
    # doesn't care what parameters it received
    sent_request = route.calls.last.request
    auth_header = sent_request.headers["authorization"]
    assert "oauth_callback=" in auth_header
    assert "localhost%3A8000" in auth_header or "localhost:8000" in auth_header


@respx.mock
def test_start_handshake_raises_clearly_on_discogs_error_response():
    respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(401, text="invalid consumer key")
    )
    with pytest.raises(Exception):
        oauth_discogs.start_handshake()


def test_start_handshake_raises_clearly_when_consumer_credentials_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    with pytest.raises(RuntimeError, match="DISCOGS_CONSUMER_KEY"):
        oauth_discogs.start_handshake()


@respx.mock
def test_fetch_access_token_returns_token_and_secret():
    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=access-token-789&oauth_token_secret=access-secret-012",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )

    result = oauth_discogs.fetch_access_token("req-token-123", "req-secret-456", "verifier-code")

    assert result["oauth_token"] == "access-token-789"
    assert result["oauth_token_secret"] == "access-secret-012"


def test_fetch_access_token_raises_clearly_when_consumer_credentials_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    with pytest.raises(RuntimeError, match="DISCOGS_CONSUMER_KEY"):
        oauth_discogs.fetch_access_token("req-token-123", "req-secret-456", "verifier-code")


@respx.mock
def test_fetch_identity_returns_discogs_user_id_and_username():
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 777, "username": "alice"})
    )

    result = oauth_discogs.fetch_identity("access-token-789", "access-secret-012")

    assert result["id"] == 777
    assert result["username"] == "alice"


def test_fetch_identity_raises_clearly_when_consumer_credentials_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    with pytest.raises(RuntimeError, match="DISCOGS_CONSUMER_KEY"):
        oauth_discogs.fetch_identity("access-token-789", "access-secret-012")
