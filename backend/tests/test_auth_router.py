from datetime import datetime, timedelta

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import db
import session_tokens
from auth_middleware import AuthMiddleware
from rate_limit import RateLimiter
from routers import session as session_router

app = FastAPI()
app.add_middleware(AuthMiddleware)
app.include_router(session_router.router, prefix="/api")


@pytest.fixture
def client(pg_test_db, monkeypatch):
    db.init_global_schema()
    db.init_tenant_schema()
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "consumer-key")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "consumer-secret")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    monkeypatch.setattr(config, "BACKEND_BASE_URL", "http://localhost:8000")
    yield TestClient(app)
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "TRUNCATE users, sessions, oauth_request_state, pending_signups, invites CASCADE"
        )
        conn.commit()


def test_status_unauthenticated_with_no_cookie(client):
    r = client.get("/api/auth/status")
    assert r.json() == {"state": "unauthenticated"}


def test_status_authenticated_includes_is_admin(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)
    r = client.get("/api/auth/status")
    assert r.json()["user"]["is_admin"] is True


def test_status_authenticated_defaults_is_admin_false_for_new_user(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)
    r = client.get("/api/auth/status")
    assert r.json()["user"]["is_admin"] is False


@respx.mock
def test_discogs_start_redirects_to_discogs_and_stores_request_state(client):
    respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=req-token-1&oauth_token_secret=req-secret-1",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )
    r = client.get("/api/auth/discogs/start", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "req-token-1" in r.headers["location"]

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT request_token_secret FROM oauth_request_state WHERE request_token = 'req-token-1'"
        ).fetchone()
    assert row["request_token_secret"] == "req-secret-1"


@respx.mock
def test_discogs_start_redirects_gracefully_when_discogs_request_token_call_fails(client):
    respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(401, text="invalid consumer key")
    )
    r = client.get("/api/auth/discogs/start", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "auth_error=discogs_failed" in r.headers["location"]


def test_discogs_start_redirects_gracefully_when_consumer_credentials_unset(client, monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    r = client.get("/api/auth/discogs/start", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "auth_error=discogs_failed" in r.headers["location"]


@respx.mock
def test_callback_for_existing_user_creates_session_and_redirects(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=777, discogs_username="alice")
        db.create_oauth_request_state(conn, "req-token-1", "req-secret-1")
        conn.commit()

    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=access-1&oauth_token_secret=access-secret-1",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 777, "username": "alice"})
    )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-1", "oauth_verifier": "verifier-1"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "signup_pending" not in r.headers["location"]
    assert config.COOKIE_NAME in r.cookies

    with db.get_admin_pool().connection() as conn:
        session = db.get_session_by_token_hash(
            conn, session_tokens.hash_token(r.cookies[config.COOKIE_NAME])
        )
    assert session["user_id"] == user["id"]


@respx.mock
def test_callback_for_new_user_creates_pending_signup_and_redirects_with_token(client):
    with db.get_admin_pool().connection() as conn:
        db.create_oauth_request_state(conn, "req-token-2", "req-secret-2")
        conn.commit()

    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=access-2&oauth_token_secret=access-secret-2",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 888, "username": "bob"})
    )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-2", "oauth_verifier": "verifier-2"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "signup_pending=" in r.headers["location"]
    assert config.COOKIE_NAME not in r.cookies


@respx.mock
def test_callback_redirects_gracefully_when_discogs_access_token_call_fails(client):
    with db.get_admin_pool().connection() as conn:
        db.create_oauth_request_state(conn, "req-token-3", "req-secret-3")
        conn.commit()

    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(401, text="invalid or expired verifier")
    )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-3", "oauth_verifier": "bad-verifier"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "auth_error=discogs_failed" in r.headers["location"]
    assert config.COOKIE_NAME not in r.cookies


@respx.mock
def test_discogs_callback_locks_out_after_repeated_failures(client, monkeypatch):
    # discogs_oauth_limiter is a module-level singleton constructed once at import
    # time with config.LOGIN_MAX_FAILURES already baked in, so patching the config
    # value here wouldn't reach it — swap in a fresh, isolated instance instead.
    monkeypatch.setattr(
        session_router, "discogs_oauth_limiter", RateLimiter(2, config.LOGIN_LOCKOUT_SECONDS)
    )
    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(401, text="invalid or expired verifier")
    )
    for _ in range(2):
        with db.get_admin_pool().connection() as conn:
            db.create_oauth_request_state(conn, "req-token-lockout", "req-secret-lockout")
            conn.commit()
        client.get(
            "/api/auth/discogs/callback",
            params={"oauth_token": "req-token-lockout", "oauth_verifier": "bad"},
            follow_redirects=False,
        )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-lockout", "oauth_verifier": "bad"},
    )
    assert r.status_code == 429


def test_redeem_invite_creates_user_and_session(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        conn.execute(
            "INSERT INTO invites (code, created_by) VALUES (%s, %s)",
            ["INVITE123", admin_user["id"]],
        )
        db.create_pending_signup(
            conn, "signup-token-1", 888, "bob", b"encrypted-token", b"encrypted-secret"
        )
        conn.commit()

    r = client.post(
        "/api/auth/redeem-invite",
        json={"signup_token": "signup-token-1", "invite_code": "INVITE123"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert config.COOKIE_NAME in r.cookies

    with db.get_admin_pool().connection() as conn:
        user = db.get_user_by_discogs_id(conn, 888)
        invite = conn.execute(
            "SELECT redeemed_by FROM invites WHERE code = 'INVITE123'"
        ).fetchone()
    assert user is not None
    assert invite["redeemed_by"] == user["id"]


def test_redeem_invite_rejects_already_redeemed_code(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        other_user = db.create_user(conn, discogs_user_id=2, discogs_username="other")
        conn.execute(
            "INSERT INTO invites (code, created_by, redeemed_by, redeemed_at) "
            "VALUES (%s, %s, %s, CURRENT_TIMESTAMP)",
            ["USED123", admin_user["id"], other_user["id"]],
        )
        db.create_pending_signup(
            conn, "signup-token-2", 999, "carol", b"x", b"y"
        )
        conn.commit()

    r = client.post(
        "/api/auth/redeem-invite",
        json={"signup_token": "signup-token-2", "invite_code": "USED123"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 400

    # rejection must not have burned the pending signup — it should still be redeemable
    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM pending_signups WHERE signup_token = 'signup-token-2'"
        ).fetchone()
    assert row is not None


def test_redeem_invite_rejects_expired_pending_signup(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        conn.execute(
            "INSERT INTO invites (code, created_by) VALUES (%s, %s)",
            ["VALID999", admin_user["id"]],
        )
        conn.execute(
            "INSERT INTO pending_signups (signup_token, discogs_user_id, discogs_username, "
            "oauth_token_encrypted, oauth_secret_encrypted, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ["old-signup", 5, "dave", b"x", b"y", datetime.utcnow() - timedelta(minutes=20)],
        )
        conn.commit()

    r = client.post(
        "/api/auth/redeem-invite",
        json={"signup_token": "old-signup", "invite_code": "VALID999"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 400


def test_create_invite_requires_admin(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()

    client.cookies.set(config.COOKIE_NAME, token)
    r = client.post("/api/auth/invites", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_create_invite_as_admin_returns_code_that_redeems_successfully(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [admin_user["id"]])
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), admin_user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)

    r = client.post("/api/auth/invites", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    code = r.json()["code"]
    assert code

    with db.get_admin_pool().connection() as conn:
        invite = conn.execute(
            "SELECT created_by, redeemed_by FROM invites WHERE code = %s", [code]
        ).fetchone()
    assert invite["created_by"] == admin_user["id"]
    assert invite["redeemed_by"] is None

    # Prove the minted code is genuinely usable, not just present in the table.
    with db.get_admin_pool().connection() as conn:
        db.create_pending_signup(conn, "signup-token-invite", 777, "erin", b"x", b"y")
        conn.commit()
    r = client.post(
        "/api/auth/redeem-invite",
        json={"signup_token": "signup-token-invite", "invite_code": code},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200


def test_logout_deletes_session_and_clears_cookie(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=42, discogs_username="alice")
        conn.commit()
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()

    client.cookies.set(config.COOKIE_NAME, token)
    r = client.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200

    with db.get_admin_pool().connection() as conn:
        assert db.get_session_by_token_hash(conn, session_tokens.hash_token(token)) is None
