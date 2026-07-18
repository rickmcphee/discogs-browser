import io
import sqlite3

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import avatar
import config
import db as db_module
from auth_middleware import AuthMiddleware
from routers import session as session_router

HDR = {"X-Requested-With": "fetch"}


@pytest.fixture
def client(tmp_config_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BOOTSTRAP_TOKEN_FILE", tmp_config_dir / "bootstrap_token")
    monkeypatch.setattr(avatar, "AVATAR_FILE", tmp_path / "avatar.png")
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(c)
    monkeypatch.setattr(db_module, "get_connection", lambda: c)
    session_router.login_limiter.clear("testclient")

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(session_router.router, prefix="/api")
    return TestClient(app)


def _login(client):
    config.BOOTSTRAP_TOKEN_FILE.write_text("boot")
    r = client.post("/api/auth/setup", json={"bootstrap_token": "boot", "password": "pw"}, headers=HDR)
    secret = r.json()["secret"]
    client.post("/api/auth/setup/verify", json={"code": pyotp.TOTP(secret).now()}, headers=HDR)
    client.post("/api/auth/login", json={"password": "pw", "code": pyotp.TOTP(secret).now()}, headers=HDR)


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
