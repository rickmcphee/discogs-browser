from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """No CSP here: /docs and /redoc serve Swagger UI/ReDoc's own inline
    scripts and CDN assets, which a locked-down default-src would break."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        return apply_security_headers(response)


async def unhandled_exception_headers(request: Request, exc: Exception) -> Response:
    """Registered as the app's Exception handler. Starlette's
    ServerErrorMiddleware wraps the whole app -- including
    SecurityHeadersMiddleware -- unconditionally, so a truly unhandled
    exception's 500 response is built outside every app.add_middleware()
    layer and would otherwise ship with none of these headers. Body matches
    ServerErrorMiddleware's own default (starlette.middleware.errors
    .ServerErrorMiddleware.error_response)."""
    return apply_security_headers(PlainTextResponse("Internal Server Error", status_code=500))
