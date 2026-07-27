from datetime import datetime, timedelta

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import config
import db
import session_tokens

ALLOWLIST = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/discogs/start",
    "/api/auth/discogs/callback",
    "/api/auth/redeem-invite",
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

        token = request.cookies.get(config.COOKIE_NAME)
        if not token:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        with db.get_identity_pool().connection() as conn:
            row = db.get_session_by_token_hash(conn, session_tokens.hash_token(token))
            if row is None:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)

            now = datetime.utcnow()
            if now > row["expires_at"] or \
                    (now - row["last_seen_at"]) > timedelta(seconds=config.SESSION_IDLE_SECONDS):
                db.delete_session(conn, row["token_hash"])
                conn.commit()
                return JSONResponse({"detail": "Session expired"}, status_code=401)

            db.touch_session(conn, row["token_hash"], now=now)
            conn.commit()

        request.state.user_id = row["user_id"]
        return await call_next(request)
