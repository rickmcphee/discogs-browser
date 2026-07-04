import json
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import auth_core
import config
import db
from logging_config import get_logger
from rate_limit import RateLimiter

router = APIRouter()
log = get_logger("session")

login_limiter = RateLimiter(config.LOGIN_MAX_FAILURES, config.LOGIN_LOCKOUT_SECONDS)

_DUMMY_HASH = auth_core.hash_password("dummy-password-for-timing")


class SetupRequest(BaseModel):
    bootstrap_token: str
    password: str


class SetupVerifyRequest(BaseModel):
    code: str


class LoginRequest(BaseModel):
    password: str
    code: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    code: str


class FactorRequest(BaseModel):
    password: str
    code: str


def _client_key(request):
    return request.client.host if request.client else "unknown"


def _is_secure(request):
    proto = request.headers.get("x-forwarded-proto", "").lower()
    if proto:
        return proto == "https"
    return request.url.scheme == "https"


def _set_session_cookie(request, response, token):
    response.set_cookie(
        config.COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=_is_secure(request),
        max_age=config.SESSION_MAX_SECONDS,
        path="/",
    )


def _valid_session(conn, token):
    row = db.get_session(conn, auth_core.hash_token(token))
    if row is None:
        return False
    now = datetime.utcnow()
    if now > datetime.fromisoformat(row["expires_at"]):
        return False
    idle = now - datetime.fromisoformat(row["last_seen_at"])
    return idle <= timedelta(seconds=config.SESSION_IDLE_SECONDS)


@router.get("/auth/status")
def auth_status(request: Request):
    conn = db.get_connection()
    if not db.owner_exists(conn):
        return {"state": "setup_required"}
    token = request.cookies.get(config.COOKIE_NAME)
    if token and _valid_session(conn, token):
        return {"state": "authenticated"}
    return {"state": "unauthenticated"}


@router.post("/auth/setup")
def setup(body: SetupRequest):
    conn = db.get_connection()
    if db.owner_exists(conn):
        raise HTTPException(status_code=409, detail="Already set up")
    if not config.BOOTSTRAP_TOKEN_FILE.exists():
        raise HTTPException(status_code=403, detail="Setup not available")
    expected = config.BOOTSTRAP_TOKEN_FILE.read_text().strip()
    if not expected or body.bootstrap_token.strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid bootstrap token")

    secret = auth_core.generate_totp_secret()
    db.create_owner(conn, auth_core.hash_password(body.password), secret, [])
    return {
        "secret": secret,
        "provisioning_uri": auth_core.totp_provisioning_uri(secret),
    }


@router.post("/auth/setup/verify")
def setup_verify(body: SetupVerifyRequest):
    conn = db.get_connection()
    owner = db.get_owner(conn)
    if owner is None:
        raise HTTPException(status_code=409, detail="Run setup first")
    # setup/verify is unauthenticated (allowlisted) and only valid during first-run,
    # before recovery codes are issued. Once issued, setup is complete; reuse would let
    # anyone with a single TOTP code rotate recovery codes. Post-setup rotation goes
    # through /auth/regenerate-recovery-codes (password + TOTP).
    if json.loads(owner["recovery_codes"]):
        raise HTTPException(status_code=409, detail="Already set up")
    if not auth_core.verify_totp(owner["totp_secret"], body.code):
        raise HTTPException(status_code=400, detail="Invalid code")
    codes = auth_core.generate_recovery_codes()
    db.set_owner_recovery_codes(conn, [auth_core.hash_token(c) for c in codes])
    if config.BOOTSTRAP_TOKEN_FILE.exists():
        config.BOOTSTRAP_TOKEN_FILE.unlink()
    log.info("Owner setup completed")
    return {"recovery_codes": codes}


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    conn = db.get_connection()
    key = _client_key(request)
    if login_limiter.is_locked(key):
        raise HTTPException(status_code=429, detail="Too many attempts, try later")

    owner = db.get_owner(conn)
    if owner is None:
        auth_core.verify_password(_DUMMY_HASH, body.password)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not auth_core.verify_password(owner["password_hash"], body.password):
        login_limiter.register_failure(key)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    second_factor_ok = auth_core.verify_totp(owner["totp_secret"], body.code) or \
        db.consume_recovery_code(conn, auth_core.hash_token(body.code.strip()))
    if not second_factor_ok:
        login_limiter.register_failure(key)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    login_limiter.clear(key)
    token = auth_core.new_session_token()
    now = datetime.utcnow()
    db.create_session(
        conn,
        auth_core.hash_token(token),
        now.isoformat(),
        (now + timedelta(seconds=config.SESSION_MAX_SECONDS)).isoformat(),
    )
    _set_session_cookie(request, response, token)
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    conn = db.get_connection()
    token = request.cookies.get(config.COOKIE_NAME)
    if token:
        db.delete_session(conn, auth_core.hash_token(token))
    response.delete_cookie(config.COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(body: ChangePasswordRequest):
    conn = db.get_connection()
    owner = db.get_owner(conn)
    if owner is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not auth_core.verify_password(owner["password_hash"], body.current_password) or \
            not auth_core.verify_totp(owner["totp_secret"], body.code):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    db.update_owner_password(conn, auth_core.hash_password(body.new_password))
    return {"ok": True}


@router.post("/auth/reset-totp")
def reset_totp(body: FactorRequest):
    conn = db.get_connection()
    owner = db.get_owner(conn)
    if owner is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not auth_core.verify_password(owner["password_hash"], body.password) or \
            not auth_core.verify_totp(owner["totp_secret"], body.code):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    secret = auth_core.generate_totp_secret()
    db.update_owner_totp(conn, secret)
    return {"secret": secret, "provisioning_uri": auth_core.totp_provisioning_uri(secret)}


@router.post("/auth/regenerate-recovery-codes")
def regenerate_recovery_codes(body: FactorRequest):
    conn = db.get_connection()
    owner = db.get_owner(conn)
    if owner is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not auth_core.verify_password(owner["password_hash"], body.password) or \
            not auth_core.verify_totp(owner["totp_secret"], body.code):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    codes = auth_core.generate_recovery_codes()
    db.set_owner_recovery_codes(conn, [auth_core.hash_token(c) for c in codes])
    return {"recovery_codes": codes}
