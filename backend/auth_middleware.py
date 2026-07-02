from datetime import datetime, timedelta

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import auth_core
import config
import db

ALLOWLIST = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/setup/verify",
}

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path

        if not path.startswith("/api"):
            return await call_next(request)

        if request.method in MUTATING and \
                request.headers.get("x-requested-with") != "fetch":
            return JSONResponse({"detail": "Missing X-Requested-With"}, status_code=403)

        if path in ALLOWLIST:
            return await call_next(request)

        conn = db.get_connection()
        if not db.owner_exists(conn):
            return JSONResponse({"detail": "Setup required"}, status_code=401)

        token = request.cookies.get(config.COOKIE_NAME)
        if not token:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        row = db.get_session(conn, auth_core.hash_token(token))
        if row is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        now = datetime.utcnow()
        if now > datetime.fromisoformat(row["expires_at"]) or \
                (now - datetime.fromisoformat(row["last_seen_at"])) > \
                timedelta(seconds=config.SESSION_IDLE_SECONDS):
            db.delete_session(conn, row["token_hash"])
            return JSONResponse({"detail": "Session expired"}, status_code=401)

        db.touch_session(conn, row["token_hash"], now.isoformat())
        return await call_next(request)
