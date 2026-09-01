from authlib.integrations.httpx_client import OAuth1Client

import config

REQUEST_TOKEN_URL = "https://api.discogs.com/oauth/request_token"
AUTHORIZE_URL = "https://www.discogs.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://api.discogs.com/oauth/access_token"
IDENTITY_URL = "https://api.discogs.com/oauth/identity"
_TIMEOUT = 30.0

# Tests inject an in-memory transport here (see conftest.py's bridge fixture):
# authlib >=1.8 transports over httpx2, which respx cannot patch, so mocking
# has to enter through the client itself rather than the httpx module.
_transport = None


def _require_consumer_credentials():
    if not config.DISCOGS_CONSUMER_KEY or not config.DISCOGS_CONSUMER_SECRET:
        raise RuntimeError(
            "DISCOGS_CONSUMER_KEY and DISCOGS_CONSUMER_SECRET must both be set (non-empty) "
            "before using the Discogs OAuth client"
        )


def start_handshake() -> dict:
    _require_consumer_credentials()
    callback_url = f"{config.BACKEND_BASE_URL}/api/auth/discogs/callback"
    with OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        redirect_uri=callback_url,
        timeout=_TIMEOUT,
        transport=_transport,
    ) as client:
        request_token = client.fetch_request_token(REQUEST_TOKEN_URL)
        authorize_url = client.create_authorization_url(
            AUTHORIZE_URL, request_token=request_token["oauth_token"]
        )
        return {
            "oauth_token": request_token["oauth_token"],
            "oauth_token_secret": request_token["oauth_token_secret"],
            "authorize_url": authorize_url,
        }


def fetch_access_token(request_token: str, request_token_secret: str, verifier: str) -> dict:
    _require_consumer_credentials()
    with OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        token=request_token,
        token_secret=request_token_secret,
        timeout=_TIMEOUT,
        transport=_transport,
    ) as client:
        return client.fetch_access_token(ACCESS_TOKEN_URL, verifier)


def fetch_identity(oauth_token: str, oauth_token_secret: str) -> dict:
    _require_consumer_credentials()
    with OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        token=oauth_token,
        token_secret=oauth_token_secret,
        timeout=_TIMEOUT,
        transport=_transport,
    ) as client:
        r = client.get(IDENTITY_URL)
        r.raise_for_status()
        return r.json()
