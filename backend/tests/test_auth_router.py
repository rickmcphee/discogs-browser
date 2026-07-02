import sqlite3

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import db as db_module
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
    app.include_router(session_router.router, prefix="/api")
    yield TestClient(app)
    c.close()


def _complete_setup(client):
    config.BOOTSTRAP_TOKEN_FILE.write_text("boot123")
    r = client.post("/api/auth/setup", json={"bootstrap_token": "boot123", "password": "pw"})
    assert r.status_code == 200
    secret = r.json()["secret"]
    code = pyotp.TOTP(secret).now()
    r2 = client.post("/api/auth/setup/verify", json={"code": code})
    assert r2.status_code == 200
    return secret, r2.json()["recovery_codes"]


def test_status_setup_required(client):
    assert client.get("/api/auth/status").json()["state"] == "setup_required"


def test_setup_rejects_bad_token(client):
    config.BOOTSTRAP_TOKEN_FILE.write_text("boot123")
    r = client.post("/api/auth/setup", json={"bootstrap_token": "wrong", "password": "pw"})
    assert r.status_code == 403


def test_setup_and_login_flow(client):
    secret, recovery = _complete_setup(client)
    assert config.BOOTSTRAP_TOKEN_FILE.exists() is False
    assert client.get("/api/auth/status").json()["state"] == "unauthenticated"

    code = pyotp.TOTP(secret).now()
    r = client.post("/api/auth/login", json={"password": "pw", "code": code})
    assert r.status_code == 200
    assert config.COOKIE_NAME in r.cookies


def test_setup_locked_after_completion(client):
    _complete_setup(client)
    config.BOOTSTRAP_TOKEN_FILE.write_text("boot123")
    r = client.post("/api/auth/setup", json={"bootstrap_token": "boot123", "password": "x"})
    assert r.status_code == 409


def test_login_wrong_password(client):
    _complete_setup(client)
    r = client.post("/api/auth/login", json={"password": "bad", "code": "000000"})
    assert r.status_code == 401


def test_login_with_recovery_code(client):
    secret, recovery = _complete_setup(client)
    r = client.post("/api/auth/login", json={"password": "pw", "code": recovery[0]})
    assert r.status_code == 200
    r2 = client.post("/api/auth/login", json={"password": "pw", "code": recovery[0]})
    assert r2.status_code == 401


def test_login_lockout(client):
    _complete_setup(client)
    for _ in range(config.LOGIN_MAX_FAILURES):
        client.post("/api/auth/login", json={"password": "bad", "code": "000000"})
    r = client.post("/api/auth/login", json={"password": "bad", "code": "000000"})
    assert r.status_code == 429
