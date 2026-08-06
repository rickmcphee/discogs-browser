from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import config
import db
import session_tokens
from auth_middleware import AuthMiddleware


@pytest.fixture
def app_and_client(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/auth/status")
    def status():
        return {"state": "unauthenticated"}

    @app.get("/api/protected")
    def protected(request: Request):
        return {"user_id": request.state.user_id}

    @app.post("/api/protected-mutate")
    def protected_mutate():
        return {"ok": True}

    yield app, TestClient(app)

    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE users, sessions CASCADE")
        conn.commit()


def _make_session(user_discogs_id=42):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=user_discogs_id, discogs_username="alice")
        token = session_tokens.new_session_token()
        db.create_session(
            conn,
            session_tokens.hash_token(token),
            user["id"],
            datetime.utcnow() + timedelta(days=1),
        )
        conn.commit()
    return token, user["id"]


def test_health_is_allowlisted(app_and_client):
    _app, client = app_and_client
    assert client.get("/api/health").status_code == 200


def test_status_is_allowlisted(app_and_client):
    _app, client = app_and_client
    assert client.get("/api/auth/status").status_code == 200


def test_protected_blocked_without_session(app_and_client):
    _app, client = app_and_client
    assert client.get("/api/protected").status_code == 401


def test_protected_allowed_with_valid_session_and_sets_user_id(app_and_client):
    _app, client = app_and_client
    token, user_id = _make_session()
    client.cookies.set(config.COOKIE_NAME, token)
    r = client.get("/api/protected")
    assert r.status_code == 200
    assert r.json()["user_id"] == user_id


def test_mutating_request_requires_x_requested_with_header(app_and_client):
    _app, client = app_and_client
    token, _ = _make_session()
    client.cookies.set(config.COOKIE_NAME, token)
    r = client.post("/api/protected-mutate")
    assert r.status_code == 403


def test_idle_expired_session_is_rejected_and_deleted(app_and_client, monkeypatch):
    _app, client = app_and_client
    monkeypatch.setattr(config, "SESSION_IDLE_SECONDS", 1)
    token, _ = _make_session()
    token_hash = session_tokens.hash_token(token)
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s",
            [datetime.utcnow() - timedelta(seconds=10), token_hash],
        )
        conn.commit()

    client.cookies.set(config.COOKIE_NAME, token)
    r = client.get("/api/protected")
    assert r.status_code == 401

    with db.get_admin_pool().connection() as conn:
        assert db.get_session_by_token_hash(conn, token_hash) is None


def test_valid_request_touches_last_seen_at(app_and_client):
    _app, client = app_and_client
    token, _ = _make_session()
    token_hash = session_tokens.hash_token(token)
    with db.get_admin_pool().connection() as conn:
        before = db.get_session_by_token_hash(conn, token_hash)["last_seen_at"]

    client.cookies.set(config.COOKIE_NAME, token)
    client.get("/api/protected")

    with db.get_admin_pool().connection() as conn:
        after = db.get_session_by_token_hash(conn, token_hash)["last_seen_at"]
    assert after >= before
