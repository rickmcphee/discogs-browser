from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

import config

HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def apply_security_headers(response: Response) -> Response:
    for name, value in HEADERS.items():
        response.headers[name] = value
    return response


def apply_cors_headers(request: Request, response: Response) -> Response:
    """The same headers CORSMiddleware would have added, for a response built
    outside it.

    Only reached from unhandled_exception_headers below, and only matters
    cross-origin -- which production is: the SPA is served from one host and
    the API from another, with no shared-origin proxy in front (see
    config.FRONTEND_ORIGINS). Without these, a browser blocks the 500 before
    the app can read it and fetch rejects with a bare "Failed to fetch",
    hiding both the status and the body. That is not a cosmetic loss: the
    frontend renders the rejection message, so every unhandled backend error
    reached the user as "Failed to fetch" and looked like the API was
    unreachable rather than erroring.

    Allowlisted origins only, echoed one at a time, exactly as CORSMiddleware
    does -- allow_credentials=True means "*" would let any origin read these
    responses. Vary: Origin because the header depends on the request's."""
    origin = request.headers.get("origin")
    response.headers["Vary"] = "Origin"
    if origin and origin in config.FRONTEND_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """No CSP here: /docs and /redoc serve Swagger UI/ReDoc's own inline
    scripts and CDN assets, which a locked-down default-src would break."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        return apply_security_headers(response)


async def unhandled_exception_headers(request: Request, exc: Exception) -> Response:
    """Registered as the app's Exception handler. Starlette's
    ServerErrorMiddleware wraps the whole app -- including
    SecurityHeadersMiddleware and CORSMiddleware -- unconditionally, so a
    truly unhandled exception's 500 response is built outside every
    app.add_middleware() layer and would otherwise ship with none of these
    headers. Body matches ServerErrorMiddleware's own default
    (starlette.middleware.errors.ServerErrorMiddleware.error_response)."""
    response = PlainTextResponse("Internal Server Error", status_code=500)
    return apply_cors_headers(request, apply_security_headers(response))
