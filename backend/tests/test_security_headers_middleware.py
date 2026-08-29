import pytest
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import config
from auth_middleware import AuthMiddleware
from security_headers_middleware import SecurityHeadersMiddleware, unhandled_exception_headers

EXPECTED_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
}


def _assert_security_headers(headers):
    for name, value in EXPECTED_HEADERS.items():
        assert headers[name] == value


@pytest.fixture
def client():
    # Mirrors main.py's exact middleware order and exception-handler wiring
    # so this test exercises the same ordering interactions production has,
    # not just SecurityHeadersMiddleware in isolation.
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://tracktempest.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_exception_handler(Exception, unhandled_exception_headers)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/protected")
    def protected(request: Request):
        return {"user_id": request.state.user_id}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    return TestClient(app, raise_server_exceptions=False)


def test_security_headers_on_normal_response(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    _assert_security_headers(r.headers)


def test_security_headers_on_auth_rejection(client):
    r = client.get("/api/protected")
    assert r.status_code == 401
    _assert_security_headers(r.headers)


def test_security_headers_on_cors_preflight(client):
    r = client.options(
        "/api/health",
        headers={
            "Origin": "https://tracktempest.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.headers["access-control-allow-origin"] == "https://tracktempest.com"
    _assert_security_headers(r.headers)


def test_security_headers_on_unhandled_exception(client):
    # ServerErrorMiddleware wraps the whole app outside every
    # app.add_middleware() layer, including SecurityHeadersMiddleware --
    # this only passes because of the app.add_exception_handler(Exception, ...)
    # wiring, not the middleware itself.
    r = client.get("/boom")
    assert r.status_code == 500
    _assert_security_headers(r.headers)


@pytest.fixture
def allowlisted_origin(monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_ORIGINS", ["https://tracktempest.com"])
    return "https://tracktempest.com"


# The regression these cover: ServerErrorMiddleware builds a 500 outside
# CORSMiddleware as well as outside SecurityHeadersMiddleware, so an unhandled
# exception used to reach the browser with no Access-Control-Allow-Origin. On
# the cross-origin production deployment (SPA and API on different hosts) the
# browser then blocked the response before the app could read it, and fetch
# rejected with a bare "Failed to fetch" -- which the frontend renders. A
# backend error was indistinguishable from an unreachable backend.
def test_cors_headers_on_unhandled_exception(client, allowlisted_origin):
    r = client.get("/boom", headers={"Origin": allowlisted_origin})
    assert r.status_code == 500
    assert r.headers["access-control-allow-origin"] == allowlisted_origin
    assert r.headers["access-control-allow-credentials"] == "true"
    _assert_security_headers(r.headers)


def test_cors_headers_not_echoed_to_disallowed_origin(client, allowlisted_origin):
    # allow_credentials=True means echoing an arbitrary origin would let any
    # site read this response. Only the allowlist is echoed, exactly as
    # CORSMiddleware does for the responses it builds.
    r = client.get("/boom", headers={"Origin": "https://evil.example"})
    assert r.status_code == 500
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-credentials" not in r.headers
    _assert_security_headers(r.headers)


def test_unhandled_exception_varies_on_origin(client, allowlisted_origin):
    # The header depends on the request's Origin, so a shared cache must not
    # serve one origin's 500 to another.
    r = client.get("/boom", headers={"Origin": allowlisted_origin})
    assert r.headers["vary"] == "Origin"
    r = client.get("/boom")
    assert r.headers["vary"] == "Origin"
    assert "access-control-allow-origin" not in r.headers
