import io
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import avatar
import config
import db
import session_tokens
from auth_middleware import AuthMiddleware
from routers import session as session_router

HDR = {"X-Requested-With": "fetch"}


@pytest.fixture
def client(pg_test_db, monkeypatch, tmp_path):
    db.init_global_schema()
    db.init_tenant_schema()

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(session_router.router, prefix="/api")
    yield TestClient(app)

    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE users, sessions CASCADE")
        conn.commit()


def _login(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=42, discogs_username="alice")
        token = session_tokens.new_session_token()
        db.create_session(
            conn,
            session_tokens.hash_token(token),
            user["id"],
            datetime.utcnow() + timedelta(days=1),
        )
        conn.commit()
    client.cookies.set(config.COOKIE_NAME, token)


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def test_get_avatar_requires_auth(client):
    assert client.get("/api/auth/avatar").status_code == 401


def test_post_avatar_requires_auth(client):
    r = client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")}, headers=HDR)
    assert r.status_code == 401


def test_delete_avatar_requires_auth(client):
    assert client.delete("/api/auth/avatar", headers=HDR).status_code == 401


def test_get_avatar_404_when_none_uploaded(client):
    _login(client)
    assert client.get("/api/auth/avatar").status_code == 404


def test_upload_then_get_avatar(client):
    _login(client)
    r = client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")}, headers=HDR)
    assert r.status_code == 200
    r2 = client.get("/api/auth/avatar")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "image/png"


def test_upload_rejects_invalid_image(client):
    _login(client)
    r = client.post("/api/auth/avatar", files={"file": ("a.png", b"not an image", "image/png")}, headers=HDR)
    assert r.status_code == 400


def test_upload_rejects_oversized_file(client):
    _login(client)
    oversized = b"\x00" * (avatar.MAX_UPLOAD_BYTES + 1)
    r = client.post("/api/auth/avatar", files={"file": ("a.png", oversized, "image/png")}, headers=HDR)
    assert r.status_code == 400


def test_delete_avatar_removes_it(client):
    _login(client)
    client.post("/api/auth/avatar", files={"file": ("a.png", _png_bytes(), "image/png")}, headers=HDR)
    r = client.delete("/api/auth/avatar", headers=HDR)
    assert r.status_code == 200
    assert client.get("/api/auth/avatar").status_code == 404


def test_delete_avatar_noop_when_missing(client):
    _login(client)
    assert client.delete("/api/auth/avatar", headers=HDR).status_code == 200
