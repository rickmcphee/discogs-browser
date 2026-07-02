import sqlite3

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import db as db_module
from auth_middleware import AuthMiddleware
from routers import session as session_router


@pytest.fixture
def client(tmp_config_dir, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_TOKEN_FILE", tmp_config_dir / "bootstrap_token")
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    db_module.init_db(c)
    monkeypatch.setattr(db_module, "get_connection", lambda: c)
    session_router.login_limiter.clear("testclient")

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(session_router.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/releases")
    def releases():
        return {"ok": True}

    @app.post("/api/collection/refresh")
    def refresh():
        return {"ok": True}

    return TestClient(app)


HDR = {"X-Requested-With": "fetch"}


def _login(client):
    config.BOOTSTRAP_TOKEN_FILE.write_text("boot")
    r = client.post("/api/auth/setup", json={"bootstrap_token": "boot", "password": "pw"}, headers=HDR)
    secret = r.json()["secret"]
    client.post("/api/auth/setup/verify", json={"code": pyotp.TOTP(secret).now()}, headers=HDR)
    client.post("/api/auth/login", json={"password": "pw", "code": pyotp.TOTP(secret).now()}, headers=HDR)


def test_protected_blocked_when_unauthenticated(client):
    assert client.get("/api/releases").status_code == 401


def test_allowlisted_status_open(client):
    assert client.get("/api/auth/status").status_code == 200


def test_health_open(client):
    assert client.get("/api/health").status_code == 200


def test_mutating_request_requires_header(client):
    _login(client)
    assert client.post("/api/collection/refresh").status_code == 403
    assert client.post("/api/collection/refresh", headers=HDR).status_code == 200


def test_protected_allowed_after_login(client):
    _login(client)
    assert client.get("/api/releases").status_code == 200


def test_idle_expired_session_rejected_and_deleted(client):
    _login(client)
    conn = db_module.get_connection()
    th = conn.execute("SELECT token_hash FROM session").fetchone()["token_hash"]
    conn.execute("UPDATE session SET last_seen_at = ? WHERE token_hash = ?",
                 ["2000-01-01T00:00:00", th])
    conn.commit()
    assert client.get("/api/releases").status_code == 401
    assert conn.execute("SELECT 1 FROM session WHERE token_hash = ?", [th]).fetchone() is None


def test_absolute_expired_session_rejected(client):
    _login(client)
    conn = db_module.get_connection()
    th = conn.execute("SELECT token_hash FROM session").fetchone()["token_hash"]
    conn.execute("UPDATE session SET expires_at = ? WHERE token_hash = ?",
                 ["2000-01-01T00:00:00", th])
    conn.commit()
    assert client.get("/api/releases").status_code == 401


def test_status_unauthenticated_for_expired_session(client):
    _login(client)
    conn = db_module.get_connection()
    conn.execute("UPDATE session SET last_seen_at = ?", ["2000-01-01T00:00:00"])
    conn.commit()
    assert client.get("/api/auth/status").json()["state"] == "unauthenticated"
