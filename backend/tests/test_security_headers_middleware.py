import pytest
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

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
