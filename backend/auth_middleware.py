from datetime import datetime, timedelta

from starlette.concurrency import run_in_threadpool
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


def _resolve_session(token: str):
    """Blocking Postgres I/O — run this via run_in_threadpool from the async
    dispatch method, never awaited directly on the event loop. Returns
    user_id on success, "expired" if the session existed but lapsed, or
    None if no session matched at all."""
    with db.get_identity_pool().connection() as conn:
        row = db.get_session_by_token_hash(conn, session_tokens.hash_token(token))
        if row is None:
            return None

        # now is reused for both this comparison and the touch_session write
        # below — safe only because last_seen_at is Python-origin end to end
        # (an earlier task's amendment), never a Postgres-side clock read, so
        # both sides of every comparison stay Python-to-Python.
        now = datetime.utcnow()
        if now > row["expires_at"] or \
                (now - row["last_seen_at"]) > timedelta(seconds=config.SESSION_IDLE_SECONDS):
            db.delete_session(conn, row["token_hash"])
            conn.commit()
            return "expired"

        db.touch_session(conn, row["token_hash"], now=now)
        conn.commit()
    return row["user_id"]


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

        result = await run_in_threadpool(_resolve_session, token)
        if result is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        if result == "expired":
            return JSONResponse({"detail": "Session expired"}, status_code=401)

        request.state.user_id = result
        return await call_next(request)
