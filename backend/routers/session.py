import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

import avatar as avatar_storage
import config
import db
import oauth_discogs
import session_tokens
import token_encryption
from logging_config import get_logger
from rate_limit import RateLimiter

router = APIRouter()
log = get_logger("session")

redeem_limiter = RateLimiter(config.LOGIN_MAX_FAILURES, config.LOGIN_LOCKOUT_SECONDS)
discogs_oauth_limiter = RateLimiter(config.LOGIN_MAX_FAILURES, config.LOGIN_LOCKOUT_SECONDS)


class RedeemInviteRequest(BaseModel):
    signup_token: str
    invite_code: str


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").lower()
    if proto:
        return proto == "https"
    return request.url.scheme == "https"


def _set_session_cookie(request: Request, response: Response, token: str):
    response.set_cookie(
        config.COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=_is_secure(request),
        max_age=config.SESSION_MAX_SECONDS,
        path="/",
    )


def _create_session_for_user(conn, request: Request, response: Response, user_id: int):
    """Caller owns the transaction: this does not commit. Call within the
    same conn/transaction as whatever created or looked up the user, so a
    session is never left committed for a user row that later rolled back."""
    token = session_tokens.new_session_token()
    db.create_session(
        conn,
        session_tokens.hash_token(token),
        user_id,
        datetime.utcnow() + timedelta(seconds=config.SESSION_MAX_SECONDS),
    )
    _set_session_cookie(request, response, token)


@router.get("/auth/status")
def auth_status(request: Request):
    token = request.cookies.get(config.COOKIE_NAME)
    if not token:
        return {"state": "unauthenticated"}
    with db.get_identity_pool().connection() as conn:
        row = db.get_session_by_token_hash(conn, session_tokens.hash_token(token))
        if row is None:
            return {"state": "unauthenticated"}
        user = conn.execute(
            "SELECT discogs_username FROM users WHERE id = %s", [row["user_id"]]
        ).fetchone()
    return {"state": "authenticated", "user": {"discogs_username": user["discogs_username"]}}


@router.get("/auth/discogs/start")
def discogs_start(request: Request):
    key = _client_key(request)
    if discogs_oauth_limiter.is_locked(key):
        raise HTTPException(status_code=429, detail="Too many attempts, try later")
    discogs_oauth_limiter.register_failure(key)  # counts every call, not just failures —
    # this limiter caps raw handshake volume (each call is a real outbound request to
    # Discogs under this app's shared consumer key), not repeated wrong-guess attempts

    handshake = oauth_discogs.start_handshake()
    with db.get_identity_pool().connection() as conn:
        db.create_oauth_request_state(
            conn, handshake["oauth_token"], handshake["oauth_token_secret"]
        )
        conn.commit()
    return RedirectResponse(handshake["authorize_url"])


@router.get("/auth/discogs/callback")
def discogs_callback(oauth_token: str, oauth_verifier: str, request: Request):
    key = _client_key(request)
    if discogs_oauth_limiter.is_locked(key):
        raise HTTPException(status_code=429, detail="Too many attempts, try later")

    with db.get_identity_pool().connection() as conn:
        state = db.get_and_delete_oauth_request_state(conn, oauth_token)
        conn.commit()
    if state is None:
        discogs_oauth_limiter.register_failure(key)
        return RedirectResponse(f"{config.FRONTEND_BASE_URL}/?auth_error=expired")

    try:
        access = oauth_discogs.fetch_access_token(
            oauth_token, state["request_token_secret"], oauth_verifier
        )
        identity = oauth_discogs.fetch_identity(access["oauth_token"], access["oauth_token_secret"])
    except Exception:
        log.warning("Discogs OAuth exchange failed for oauth_token=%s", oauth_token)
        discogs_oauth_limiter.register_failure(key)
        return RedirectResponse(f"{config.FRONTEND_BASE_URL}/?auth_error=discogs_failed")

    discogs_user_id = identity["id"]
    discogs_username = identity["username"]

    with db.get_identity_pool().connection() as conn:
        user = db.get_user_by_discogs_id(conn, discogs_user_id)
        if user is not None:
            redirect = RedirectResponse(config.FRONTEND_BASE_URL or "/")
            _create_session_for_user(conn, request, redirect, user["id"])
            conn.commit()
            return redirect

        signup_token = secrets.token_urlsafe(32)
        db.create_pending_signup(
            conn,
            signup_token,
            discogs_user_id,
            discogs_username,
            token_encryption.encrypt(access["oauth_token"]),
            token_encryption.encrypt(access["oauth_token_secret"]),
        )
        conn.commit()
    return RedirectResponse(f"{config.FRONTEND_BASE_URL}/?signup_pending={signup_token}")


@router.post("/auth/redeem-invite")
def redeem_invite(body: RedeemInviteRequest, request: Request, response: Response):
    key = _client_key(request)
    if redeem_limiter.is_locked(key):
        raise HTTPException(status_code=429, detail="Too many attempts, try later")

    with db.get_identity_pool().connection() as conn:
        pending = db.get_and_delete_pending_signup(conn, body.signup_token)
        if pending is None:
            redeem_limiter.register_failure(key)
            conn.commit()
            raise HTTPException(status_code=400, detail="Signup expired, start over")

        invite = conn.execute(
            "SELECT created_by FROM invites WHERE code = %s AND redeemed_by IS NULL",
            [body.invite_code],
        ).fetchone()
        if invite is None:
            redeem_limiter.register_failure(key)
            conn.rollback()  # restores the pending_signups row deleted above — a bad
            # invite code shouldn't burn a valid OAuth grant the user could retry with
            raise HTTPException(status_code=400, detail="Invalid or already-used invite code")

        user = db.create_user(
            conn, pending["discogs_user_id"], pending["discogs_username"], invited_by=invite["created_by"]
        )
        conn.execute(
            "UPDATE users SET discogs_oauth_token_encrypted = %s, discogs_oauth_secret_encrypted = %s "
            "WHERE id = %s",
            [pending["oauth_token_encrypted"], pending["oauth_secret_encrypted"], user["id"]],
        )
        conn.execute(
            "UPDATE invites SET redeemed_by = %s, redeemed_at = CURRENT_TIMESTAMP WHERE code = %s",
            [user["id"], body.invite_code],
        )
        redeem_limiter.clear(key)
        _create_session_for_user(conn, request, response, user["id"])
        conn.commit()
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(config.COOKIE_NAME)
    if token:
        with db.get_identity_pool().connection() as conn:
            db.delete_session(conn, session_tokens.hash_token(token))
            conn.commit()
    response.delete_cookie(config.COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/auth/avatar")
async def upload_avatar(file: UploadFile = File(...)):
    data = await file.read(avatar_storage.MAX_UPLOAD_BYTES + 1)
    try:
        avatar_storage.save_avatar(data)
    except avatar_storage.InvalidAvatarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.get("/auth/avatar")
def get_avatar():
    if not avatar_storage.AVATAR_FILE.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(avatar_storage.AVATAR_FILE))


@router.delete("/auth/avatar")
def remove_avatar():
    avatar_storage.delete_avatar()
    return {"ok": True}
