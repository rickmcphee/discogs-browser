# App Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add always-enforced single-owner authentication (Argon2 password + TOTP second factor, server-side sessions) to the Discogs Browser app.

**Architecture:** A single `AuthMiddleware` gates every `/api/*` request against a server-side `session` table. Credentials live in a single-row `owner` table. A first-run setup wizard, gated by a bootstrap token written to logs/data dir, provisions the owner. The SPA branches on `GET /api/auth/status` between setup, login, and the app. The existing crawler-login router is renamed to free the `/api/auth/*` namespace.

**Tech Stack:** FastAPI, SQLite (thread-local `sqlite3`), `argon2-cffi`, `pyotp`, React + TypeScript SPA, pytest.

Spec: [`docs/superpowers/specs/2026-07-02-app-authentication-design.md`](../specs/2026-07-02-app-authentication-design.md)

---

## File Structure

**Backend (new):**
- `backend/auth_core.py` — pure crypto helpers: password hashing, TOTP, recovery codes, session tokens. No DB, no FastAPI.
- `backend/rate_limit.py` — in-memory login lockout.
- `backend/auth_middleware.py` — `AuthMiddleware` (allowlist, CSRF header, session validation).
- `backend/routers/session.py` — app-auth endpoints under `/api/auth/*`.
- `backend/reset_owner.py` — CLI recovery command.
- `backend/tests/test_auth_core.py`, `test_auth_db.py`, `test_rate_limit.py`, `test_auth_router.py`, `test_auth_middleware.py`.

**Backend (modified):**
- `backend/db.py` — add `owner` + `session` tables to `SCHEMA`, add helpers.
- `backend/config.py` — session/lockout/cookie constants, bootstrap-token path.
- `backend/main.py` — mount `session` router, add `AuthMiddleware`, generate bootstrap token at startup, `allow_credentials` on CORS.
- `backend/routers/auth.py` → renamed `backend/routers/crawler_auth.py`, remounted at `/api/crawler-auth`.
- `backend/pyproject.toml` — add deps.

**Frontend (new):**
- `frontend/src/views/LoginScreen.tsx`
- `frontend/src/views/SetupWizard.tsx`

**Frontend (modified):**
- `frontend/src/api/client.ts` — `apiFetch` wrapper (adds `X-Requested-With`, handles 401), auth functions, rename crawler-auth calls.
- `frontend/src/api/types.ts` — auth types.
- `frontend/src/App.tsx` — branch on auth status.
- `frontend/src/views/Settings.tsx` — Account/Security section.

---

## Task 1: Add backend dependencies

**Files:**
- Modify: `backend/pyproject.toml:9-18`

- [ ] **Step 1: Add runtime deps**

In the `dependencies` array, add:

```toml
    "argon2-cffi>=23.1",
    "pyotp>=2.9",
```

- [ ] **Step 2: Install**

Run: `cd backend && pip install -e ".[dev]"`
Expected: installs `argon2-cffi` and `pyotp` without error.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore: add argon2-cffi and pyotp for app auth"
```

---

## Task 2: `auth_core.py` — crypto helpers (TDD)

**Files:**
- Create: `backend/auth_core.py`
- Test: `backend/tests/test_auth_core.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_auth_core.py`:

```python
import pyotp
import auth_core


def test_password_hash_roundtrip():
    h = auth_core.hash_password("hunter2")
    assert h != "hunter2"
    assert auth_core.verify_password(h, "hunter2") is True
    assert auth_core.verify_password(h, "wrong") is False


def test_verify_password_bad_hash_returns_false():
    assert auth_core.verify_password("not-a-hash", "x") is False


def test_totp_verify_accepts_current_code():
    secret = auth_core.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert auth_core.verify_totp(secret, code) is True


def test_totp_verify_rejects_wrong_code():
    secret = auth_core.generate_totp_secret()
    assert auth_core.verify_totp(secret, "000000") is False


def test_provisioning_uri_contains_issuer():
    secret = auth_core.generate_totp_secret()
    uri = auth_core.totp_provisioning_uri(secret)
    assert uri.startswith("otpauth://totp/")
    assert "Discogs%20Browser" in uri or "Discogs Browser" in uri


def test_recovery_codes_generate_and_hash():
    codes = auth_core.generate_recovery_codes(10)
    assert len(codes) == 10
    assert len(set(codes)) == 10
    hashes = [auth_core.hash_token(c) for c in codes]
    assert auth_core.hash_token(codes[0]) in hashes


def test_session_token_and_hash():
    tok = auth_core.new_session_token()
    assert len(tok) >= 32
    assert auth_core.hash_token(tok) != tok
    assert auth_core.hash_token(tok) == auth_core.hash_token(tok)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_auth_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth_core'`.

- [ ] **Step 3: Write the implementation**

Create `backend/auth_core.py`:

```python
import hashlib
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

_ph = PasswordHasher()

ISSUER = "Discogs Browser"


def hash_password(password):
    return _ph.hash(password)


def verify_password(stored_hash, password):
    try:
        return _ph.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def generate_totp_secret():
    return pyotp.random_base32()


def totp_provisioning_uri(secret, account="owner"):
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def verify_totp(secret, code):
    if not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def generate_recovery_codes(n=10):
    return [secrets.token_hex(5) for _ in range(n)]


def new_session_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_auth_core.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/auth_core.py backend/tests/test_auth_core.py
git commit -m "feat: add auth_core crypto helpers (password, totp, sessions)"
```

---

## Task 3: `db.py` — owner and session tables + helpers (TDD)

**Files:**
- Modify: `backend/db.py:6-40` (SCHEMA), append helpers at end of file
- Test: `backend/tests/test_auth_db.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_auth_db.py`:

```python
from datetime import datetime, timedelta

import db as db_module


def test_owner_lifecycle(conn):
    assert db_module.owner_exists(conn) is False
    db_module.create_owner(conn, "phash", "secret", ["h1", "h2"])
    assert db_module.owner_exists(conn) is True
    row = db_module.get_owner(conn)
    assert row["password_hash"] == "phash"
    assert row["totp_secret"] == "secret"


def test_owner_is_single_row(conn):
    db_module.create_owner(conn, "phash", "secret", [])
    db_module.create_owner(conn, "phash2", "secret2", [])
    row = db_module.get_owner(conn)
    assert row["password_hash"] == "phash2"


def test_update_password_and_totp(conn):
    db_module.create_owner(conn, "phash", "secret", [])
    db_module.update_owner_password(conn, "newhash")
    db_module.update_owner_totp(conn, "newsecret")
    row = db_module.get_owner(conn)
    assert row["password_hash"] == "newhash"
    assert row["totp_secret"] == "newsecret"


def test_recovery_code_consume(conn):
    db_module.create_owner(conn, "p", "s", ["h1", "h2"])
    assert db_module.consume_recovery_code(conn, "h1") is True
    assert db_module.consume_recovery_code(conn, "h1") is False
    assert db_module.consume_recovery_code(conn, "h2") is True


def test_set_recovery_codes_replaces(conn):
    db_module.create_owner(conn, "p", "s", ["h1"])
    db_module.set_owner_recovery_codes(conn, ["a", "b"])
    assert db_module.consume_recovery_code(conn, "h1") is False
    assert db_module.consume_recovery_code(conn, "a") is True


def test_delete_owner(conn):
    db_module.create_owner(conn, "p", "s", [])
    db_module.delete_owner(conn)
    assert db_module.owner_exists(conn) is False


def test_session_lifecycle(conn):
    now = datetime(2026, 1, 1, 12, 0, 0)
    exp = now + timedelta(days=30)
    db_module.create_session(conn, "tokhash", now.isoformat(), exp.isoformat())
    row = db_module.get_session(conn, "tokhash")
    assert row["token_hash"] == "tokhash"
    later = (now + timedelta(hours=1)).isoformat()
    db_module.touch_session(conn, "tokhash", later)
    assert db_module.get_session(conn, "tokhash")["last_seen_at"] == later
    db_module.delete_session(conn, "tokhash")
    assert db_module.get_session(conn, "tokhash") is None


def test_purge_expired_sessions(conn):
    db_module.create_session(conn, "old", "2020-01-01T00:00:00", "2020-02-01T00:00:00")
    db_module.create_session(conn, "new", "2026-01-01T00:00:00", "2099-01-01T00:00:00")
    db_module.purge_expired_sessions(conn, "2026-06-01T00:00:00")
    assert db_module.get_session(conn, "old") is None
    assert db_module.get_session(conn, "new") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_auth_db.py -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'owner_exists'`.

- [ ] **Step 3: Extend the SCHEMA**

In `backend/db.py`, append to the `SCHEMA` string (before the closing `"""`):

```sql

CREATE TABLE IF NOT EXISTS owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    totp_secret TEXT NOT NULL,
    recovery_codes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    password_changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
```

- [ ] **Step 4: Add helper functions**

Append to the end of `backend/db.py`:

```python
import json as _json
from datetime import datetime as _datetime


def owner_exists(conn) -> bool:
    return conn.execute("SELECT 1 FROM owner WHERE id = 1").fetchone() is not None


def get_owner(conn):
    return conn.execute("SELECT * FROM owner WHERE id = 1").fetchone()


def create_owner(conn, password_hash, totp_secret, recovery_hashes):
    now = _datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO owner (id, password_hash, totp_secret, recovery_codes,
                              created_at, password_changed_at)
           VALUES (1, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               password_hash=excluded.password_hash,
               totp_secret=excluded.totp_secret,
               recovery_codes=excluded.recovery_codes,
               created_at=excluded.created_at,
               password_changed_at=excluded.password_changed_at""",
        [password_hash, totp_secret, _json.dumps(recovery_hashes), now, now],
    )
    conn.commit()


def update_owner_password(conn, password_hash):
    conn.execute(
        "UPDATE owner SET password_hash = ?, password_changed_at = ? WHERE id = 1",
        [password_hash, _datetime.utcnow().isoformat()],
    )
    conn.commit()


def update_owner_totp(conn, totp_secret):
    conn.execute("UPDATE owner SET totp_secret = ? WHERE id = 1", [totp_secret])
    conn.commit()


def set_owner_recovery_codes(conn, recovery_hashes):
    conn.execute(
        "UPDATE owner SET recovery_codes = ? WHERE id = 1",
        [_json.dumps(recovery_hashes)],
    )
    conn.commit()


def consume_recovery_code(conn, code_hash) -> bool:
    row = conn.execute("SELECT recovery_codes FROM owner WHERE id = 1").fetchone()
    if row is None:
        return False
    codes = _json.loads(row["recovery_codes"])
    if code_hash not in codes:
        return False
    codes.remove(code_hash)
    conn.execute("UPDATE owner SET recovery_codes = ? WHERE id = 1", [_json.dumps(codes)])
    conn.commit()
    return True


def delete_owner(conn):
    conn.execute("DELETE FROM owner WHERE id = 1")
    conn.commit()


def create_session(conn, token_hash, created_at, expires_at):
    conn.execute(
        """INSERT INTO session (token_hash, created_at, expires_at, last_seen_at)
           VALUES (?, ?, ?, ?)""",
        [token_hash, created_at, expires_at, created_at],
    )
    conn.commit()


def get_session(conn, token_hash):
    return conn.execute(
        "SELECT * FROM session WHERE token_hash = ?", [token_hash]
    ).fetchone()


def touch_session(conn, token_hash, last_seen_at):
    conn.execute(
        "UPDATE session SET last_seen_at = ? WHERE token_hash = ?",
        [last_seen_at, token_hash],
    )
    conn.commit()


def delete_session(conn, token_hash):
    conn.execute("DELETE FROM session WHERE token_hash = ?", [token_hash])
    conn.commit()


def purge_expired_sessions(conn, now_iso):
    conn.execute("DELETE FROM session WHERE expires_at < ?", [now_iso])
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_auth_db.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/tests/test_auth_db.py
git commit -m "feat: add owner and session tables with db helpers"
```

---

## Task 4: `rate_limit.py` — login lockout (TDD)

**Files:**
- Create: `backend/rate_limit.py`
- Test: `backend/tests/test_rate_limit.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_rate_limit.py`:

```python
from rate_limit import RateLimiter


def test_allows_until_threshold():
    rl = RateLimiter(max_failures=3, lockout_seconds=100)
    t = [1000.0]
    clock = lambda: t[0]
    assert rl.is_locked("k", now=clock()) is False
    rl.register_failure("k", now=clock())
    rl.register_failure("k", now=clock())
    assert rl.is_locked("k", now=clock()) is False
    rl.register_failure("k", now=clock())
    assert rl.is_locked("k", now=clock()) is True


def test_lockout_expires():
    rl = RateLimiter(max_failures=1, lockout_seconds=100)
    rl.register_failure("k", now=1000.0)
    assert rl.is_locked("k", now=1050.0) is True
    assert rl.is_locked("k", now=1101.0) is False


def test_clear_resets():
    rl = RateLimiter(max_failures=1, lockout_seconds=100)
    rl.register_failure("k", now=1000.0)
    rl.clear("k")
    assert rl.is_locked("k", now=1000.0) is False


def test_keys_are_independent():
    rl = RateLimiter(max_failures=1, lockout_seconds=100)
    rl.register_failure("a", now=1000.0)
    assert rl.is_locked("a", now=1000.0) is True
    assert rl.is_locked("b", now=1000.0) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_rate_limit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rate_limit'`.

- [ ] **Step 3: Write the implementation**

Create `backend/rate_limit.py`:

```python
import time as _time


class RateLimiter:
    def __init__(self, max_failures, lockout_seconds):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures = {}  # key -> list[timestamp]

    def _prune(self, key, now):
        cutoff = now - self.lockout_seconds
        kept = [t for t in self._failures.get(key, []) if t >= cutoff]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def is_locked(self, key, now=None):
        now = _time.time() if now is None else now
        kept = self._prune(key, now)
        return len(kept) >= self.max_failures

    def register_failure(self, key, now=None):
        now = _time.time() if now is None else now
        self._prune(key, now)
        self._failures.setdefault(key, []).append(now)

    def clear(self, key):
        self._failures.pop(key, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_rate_limit.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/rate_limit.py backend/tests/test_rate_limit.py
git commit -m "feat: add in-memory login rate limiter"
```

---

## Task 5: `config.py` — auth constants

**Files:**
- Modify: `backend/config.py` (append after existing constants)

- [ ] **Step 1: Add constants**

Append to `backend/config.py`:

```python
COOKIE_NAME = "db_session"
BOOTSTRAP_TOKEN_FILE = CONFIG_DIR / "bootstrap_token"

SESSION_IDLE_SECONDS = int(os.environ.get("SESSION_IDLE_SECONDS", 7 * 86400))
SESSION_MAX_SECONDS = int(os.environ.get("SESSION_MAX_SECONDS", 30 * 86400))
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", 5))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 300))
```

- [ ] **Step 2: Verify import**

Run: `cd backend && python -c "import config; print(config.COOKIE_NAME, config.SESSION_IDLE_SECONDS)"`
Expected: `db_session 604800`

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat: add auth config constants"
```

---

## Task 6: Rename crawler-auth router

**Files:**
- Rename: `backend/routers/auth.py` → `backend/routers/crawler_auth.py`
- Modify: `backend/main.py:9` (import), `:85` (include)
- Modify: `frontend/src/api/client.ts:132,138,147,152`

- [ ] **Step 1: Rename the file and re-path routes**

Run: `cd backend && git mv routers/auth.py routers/crawler_auth.py`

In `backend/routers/crawler_auth.py`, change every route decorator path prefix from `/auth/` to `/crawler-auth/`:
- `@router.get("/auth/status")` → `@router.get("/crawler-auth/status")`
- `@router.post("/auth/login")` → `@router.post("/crawler-auth/login")`
- `@router.post("/auth/done")` → `@router.post("/crawler-auth/done")`
- `@router.delete("/auth/state")` → `@router.delete("/crawler-auth/state")`

- [ ] **Step 2: Update main.py**

In `backend/main.py`, change the import line:

```python
from routers import collection, releases, settings, crawl, logs, screenshots, crawler_auth, health
```

And change the include line `app.include_router(auth.router, prefix="/api")` to:

```python
app.include_router(crawler_auth.router, prefix="/api")
```

- [ ] **Step 3: Update frontend call sites**

In `frontend/src/api/client.ts`, update the four crawler-auth fetches:
- `${BASE}/auth/status` → `${BASE}/crawler-auth/status`
- `${BASE}/auth/login` → `${BASE}/crawler-auth/login`
- `${BASE}/auth/done` → `${BASE}/crawler-auth/done`
- `${BASE}/auth/state` → `${BASE}/crawler-auth/state`

- [ ] **Step 4: Verify backend imports and existing tests pass**

Run: `cd backend && python -c "import main" && pytest -q`
Expected: import OK; existing suite passes.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/crawler_auth.py backend/main.py frontend/src/api/client.ts
git commit -m "refactor: rename crawler auth to /api/crawler-auth, free /api/auth"
```

---

## Task 7: `routers/session.py` — app auth endpoints (TDD)

**Files:**
- Create: `backend/routers/session.py`
- Test: `backend/tests/test_auth_router.py`

This router is mounted in Task 9. Tests here mount it on a bare `FastAPI` app so they run before middleware exists.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_auth_router.py`:

```python
import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import db as db_module
from routers import session as session_router


@pytest.fixture
def client(conn, tmp_config_dir):
    app = FastAPI()
    app.include_router(session_router.router, prefix="/api")
    session_router.login_limiter.clear("testclient")
    return TestClient(app)


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


def test_setup_and_login_flow(client, conn):
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
    # code is consumed
    r2 = client.post("/api/auth/login", json={"password": "pw", "code": recovery[0]})
    assert r2.status_code == 401


def test_login_lockout(client):
    _complete_setup(client)
    for _ in range(config.LOGIN_MAX_FAILURES):
        client.post("/api/auth/login", json={"password": "bad", "code": "000000"})
    r = client.post("/api/auth/login", json={"password": "bad", "code": "000000"})
    assert r.status_code == 429
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_auth_router.py -v`
Expected: FAIL — import error for `routers.session`.

- [ ] **Step 3: Write the implementation**

Create `backend/routers/session.py`:

```python
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

# A precomputed hash so login timing is similar whether or not an owner exists.
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


@router.get("/auth/status")
def auth_status(request: Request):
    conn = db.get_connection()
    if not db.owner_exists(conn):
        return {"state": "setup_required"}
    token = request.cookies.get(config.COOKIE_NAME)
    if token and _valid_session(conn, token):
        return {"state": "authenticated"}
    return {"state": "unauthenticated"}


def _valid_session(conn, token) -> bool:
    row = db.get_session(conn, auth_core.hash_token(token))
    if row is None:
        return False
    now = datetime.utcnow()
    if now > datetime.fromisoformat(row["expires_at"]):
        return False
    idle = now - datetime.fromisoformat(row["last_seen_at"])
    return idle <= timedelta(seconds=config.SESSION_IDLE_SECONDS)


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
    # Store the password now; TOTP secret is confirmed in the verify step.
    # Persist the pending secret on the owner row and finalize on verify.
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
    # Allowlisted (unauthenticated) endpoint: fail closed once setup is complete.
    # Completion == recovery codes issued. Otherwise anyone with a single TOTP code
    # (but not the password) could re-run this to wipe/reissue the owner's codes.
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
    if not auth_core.verify_password(owner["password_hash"], body.current_password) or \
            not auth_core.verify_totp(owner["totp_secret"], body.code):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    db.update_owner_password(conn, auth_core.hash_password(body.new_password))
    return {"ok": True}


@router.post("/auth/reset-totp")
def reset_totp(body: FactorRequest):
    conn = db.get_connection()
    owner = db.get_owner(conn)
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
    if not auth_core.verify_password(owner["password_hash"], body.password) or \
            not auth_core.verify_totp(owner["totp_secret"], body.code):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    codes = auth_core.generate_recovery_codes()
    db.set_owner_recovery_codes(conn, [auth_core.hash_token(c) for c in codes])
    return {"recovery_codes": codes}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_auth_router.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/session.py backend/tests/test_auth_router.py
git commit -m "feat: add app auth router (setup, login, logout, account mgmt)"
```

---

## Task 8: `auth_middleware.py` — request gate (TDD)

**Files:**
- Create: `backend/auth_middleware.py`
- Test: `backend/tests/test_auth_middleware.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_auth_middleware.py`:

```python
import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import db as db_module
from auth_middleware import AuthMiddleware
from routers import session as session_router


@pytest.fixture
def client(conn, tmp_config_dir):
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(session_router.router, prefix="/api")

    @app.get("/api/releases")
    def releases():
        return {"ok": True}

    @app.post("/api/collection/refresh")
    def refresh():
        return {"ok": True}

    session_router.login_limiter.clear("testclient")
    return TestClient(app)


HDR = {"X-Requested-With": "fetch"}


def _login(client):
    config.BOOTSTRAP_TOKEN_FILE.write_text("boot")
    r = client.post("/api/auth/setup", json={"bootstrap_token": "boot", "password": "pw"}, headers=HDR)
    secret = r.json()["secret"]
    client.post("/api/auth/setup/verify", json={"code": pyotp.TOTP(secret).now()}, headers=HDR)
    client.post("/api/auth/login", json={"password": "pw", "code": pyotp.TOTP(secret).now()}, headers=HDR)


def test_protected_blocked_when_unauthenticated(client):
    assert client.get("/api/releases").status_code == 401


def test_allowlisted_status_open(client):
    assert client.get("/api/auth/status").status_code == 200


def test_health_open(client):
    @client.app.get("/api/health")
    def health():
        return {"ok": True}
    assert client.get("/api/health").status_code == 200


def test_mutating_request_requires_header(client):
    _login(client)
    # cookie is retained by TestClient; missing X-Requested-With -> 403
    assert client.post("/api/collection/refresh").status_code == 403
    assert client.post("/api/collection/refresh", headers=HDR).status_code == 200


def test_protected_allowed_after_login(client):
    _login(client)
    assert client.get("/api/releases").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_auth_middleware.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth_middleware'`.

- [ ] **Step 3: Write the implementation**

Create `backend/auth_middleware.py`:

```python
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

        # Only gate API routes; static assets are served by nginx/vite.
        if not path.startswith("/api"):
            return await call_next(request)

        # CSRF: state-changing requests must carry the SPA's custom header.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_auth_middleware.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/auth_middleware.py backend/tests/test_auth_middleware.py
git commit -m "feat: add AuthMiddleware gating /api with session + CSRF header"
```

---

## Task 9: Wire router + middleware + bootstrap token into `main.py`

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Import the new modules**

In `backend/main.py`, update the routers import to add `session`, and add module imports:

```python
from routers import collection, releases, settings, crawl, logs, screenshots, crawler_auth, health, session
from auth_middleware import AuthMiddleware
import auth_core
import secrets as _secrets
```

- [ ] **Step 2: Add middleware (after CORS middleware block)**

Immediately after the existing `app.add_middleware(CORSMiddleware, ...)` call, add:

```python
app.add_middleware(AuthMiddleware)
```

Also add `allow_credentials=True` to the existing `CORSMiddleware` args so cookies flow in dev.

- [ ] **Step 3: Generate the bootstrap token at startup**

Inside the `startup()` function, after `init_db(conn)`, add:

```python
    if not db.owner_exists(conn):
        token = _secrets.token_urlsafe(24)
        config.BOOTSTRAP_TOKEN_FILE.write_text(token)
        log.info("No owner configured. Bootstrap token: %s", token)
        log.info("Complete first-run setup at the app URL using this token.")
```

(Requires `import db` and `import config` — `db` is already imported via `from db import ...`; add `import db` and `import config` at the top if not present. `config.load_config` is already imported, so add a bare `import config`.)

- [ ] **Step 4: Mount the session router**

Add alongside the other `include_router` calls:

```python
app.include_router(session.router, prefix="/api")
```

- [ ] **Step 5: Verify the app boots and the full suite passes**

Run: `cd backend && python -c "import main" && pytest -q`
Expected: import OK; all tests pass.

- [ ] **Step 6: Manual smoke test**

Run: `cd backend && DISCOGS_BROWSER_DATA=/tmp/db-auth-smoke uvicorn main:app --port 8001 &` then:

```bash
curl -s localhost:8001/api/auth/status
curl -s -X POST localhost:8001/api/collection/refresh
```

Expected: first returns `{"state":"setup_required"}`; second returns 403 (missing header) — confirming the gate is live. Read the bootstrap token from the uvicorn log output. Stop the server afterward.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py
git commit -m "feat: wire auth middleware, session router, bootstrap token"
```

---

## Task 10: `reset_owner.py` — CLI recovery (TDD)

**Files:**
- Create: `backend/reset_owner.py`
- Test: `backend/tests/test_reset_owner.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_reset_owner.py`:

```python
import db as db_module
import reset_owner


def test_reset_clears_owner_and_sessions(conn):
    db_module.create_owner(conn, "p", "s", [])
    db_module.create_session(conn, "t", "2026-01-01T00:00:00", "2099-01-01T00:00:00")
    reset_owner.reset(conn)
    assert db_module.owner_exists(conn) is False
    assert db_module.get_session(conn, "t") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_reset_owner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reset_owner'`.

- [ ] **Step 3: Write the implementation**

Create `backend/reset_owner.py`:

```python
"""CLI recovery: clear the owner and all sessions, returning the app to first-run setup.

Run inside the container / on the host:  python -m reset_owner
"""
import db


def reset(conn):
    db.delete_owner(conn)
    conn.execute("DELETE FROM session")
    conn.commit()


def main():
    import config
    from db import get_connection, init_db

    conn = get_connection()
    init_db(conn)
    reset(conn)
    if config.BOOTSTRAP_TOKEN_FILE.exists():
        config.BOOTSTRAP_TOKEN_FILE.unlink()
    print("Owner and sessions cleared. Restart the app to get a new bootstrap token.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_reset_owner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/reset_owner.py backend/tests/test_reset_owner.py
git commit -m "feat: add reset_owner CLI recovery command"
```

---

## Task 11: Frontend API client — auth wrapper, types, functions

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/types.ts`

- [ ] **Step 1: Add auth types**

Append to `frontend/src/api/types.ts`:

```typescript
export type AuthState = 'setup_required' | 'unauthenticated' | 'authenticated'

export interface SetupResponse {
  secret: string
  provisioning_uri: string
}
```

- [ ] **Step 2: Add `apiFetch` wrapper and register a 401 handler**

At the top of `frontend/src/api/client.ts` (after the imports and `const BASE = '/api'`), add:

```typescript
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn }

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Requested-With', 'fetch')
  const r = await fetch(`${BASE}${path}`, { ...init, headers })
  if (r.status === 401 && path !== '/auth/status' && path !== '/auth/login') {
    onUnauthorized?.()
  }
  return r
}
```

- [ ] **Step 3: Route existing calls through `apiFetch`**

Replace every existing `fetch(\`${BASE}...\`, ...)` call in `client.ts` with an `apiFetch('...', ...)` call (drop the `${BASE}` prefix, pass the path only). For example:

```typescript
export async function getReleases(params: {...}): Promise<ReleasesResponse> {
  const q = new URLSearchParams()
  // ...unchanged param building...
  const r = await apiFetch(`/releases?${q}`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}
```

Apply the same transformation to `checkHealth`, `getCollectionStatus`, `refreshCollection`, `getArtists`, `getCrawlers`, `getSettings`, `saveSettings`, `setCrawlerEnabled`, and the renamed `crawler-auth` calls. `checkHealth` keeps its try/catch. This ensures every mutating request carries `X-Requested-With`.

- [ ] **Step 4: Add app-auth functions**

First, add the auth types to the existing top-of-file `import type { ... } from './types'` line (extend it with `AuthState, SetupResponse`) — do **not** add a second import statement lower in the file, which is invalid ES module syntax.

Then append the functions to `frontend/src/api/client.ts`:

```typescript
export async function getAuthState(): Promise<AuthState> {
  const r = await apiFetch('/auth/status')
  if (!r.ok) throw new Error(await r.text())
  return (await r.json()).state
}

export async function setupOwner(bootstrapToken: string, password: string): Promise<SetupResponse> {
  const r = await apiFetch('/auth/setup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bootstrap_token: bootstrapToken, password }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function verifySetup(code: string): Promise<string[]> {
  const r = await apiFetch('/auth/setup/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  })
  if (!r.ok) throw new Error(await r.text())
  return (await r.json()).recovery_codes
}

export async function login(password: string, code: string): Promise<void> {
  const r = await apiFetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password, code }),
  })
  if (!r.ok) throw new Error(await r.text())
}

export async function logout(): Promise<void> {
  await apiFetch('/auth/logout', { method: 'POST' })
}

export async function changePassword(currentPassword: string, newPassword: string, code: string): Promise<void> {
  const r = await apiFetch('/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword, code }),
  })
  if (!r.ok) throw new Error(await r.text())
}
```

- [ ] **Step 5: Verify the frontend type-checks and builds**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/types.ts
git commit -m "feat: add auth API client wrapper, functions, and types"
```

---

## Task 12: `LoginScreen.tsx`

**Files:**
- Create: `frontend/src/views/LoginScreen.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/views/LoginScreen.tsx`:

```tsx
import { useState } from 'react'
import { login } from '../api/client'

export default function LoginScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(password, code)
      onAuthenticated()
    } catch {
      setError('Invalid credentials')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <form onSubmit={submit} className="bg-white p-8 rounded shadow w-80 space-y-4">
        <h1 className="text-xl font-semibold">Sign in</h1>
        <input
          type="password" placeholder="Password" value={password}
          onChange={e => setPassword(e.target.value)}
          className="w-full border rounded px-3 py-2" autoFocus
        />
        <input
          type="text" inputMode="numeric" placeholder="Authenticator code or recovery code"
          value={code} onChange={e => setCode(e.target.value)}
          className="w-full border rounded px-3 py-2"
        />
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50">
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/LoginScreen.tsx
git commit -m "feat: add LoginScreen component"
```

---

## Task 13: `SetupWizard.tsx`

**Files:**
- Create: `frontend/src/views/SetupWizard.tsx`

- [ ] **Step 1: Add a QR dependency**

Run: `cd frontend && npm install qrcode.react`
Expected: installs `qrcode.react`.

- [ ] **Step 2: Write the component**

Create `frontend/src/views/SetupWizard.tsx`:

```tsx
import { useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { setupOwner, verifySetup } from '../api/client'

type Step = 'credentials' | 'totp' | 'recovery'

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState<Step>('credentials')
  const [bootstrapToken, setBootstrapToken] = useState('')
  const [password, setPassword] = useState('')
  const [uri, setUri] = useState('')
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  async function submitCredentials(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const res = await setupOwner(bootstrapToken, password)
      setUri(res.provisioning_uri)
      setStep('totp')
    } catch {
      setError('Setup failed — check the bootstrap token.')
    }
  }

  async function submitTotp(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const codes = await verifySetup(code)
      setRecovery(codes)
      setStep('recovery')
    } catch {
      setError('Invalid code — try again.')
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded shadow w-96 space-y-4">
        <h1 className="text-xl font-semibold">First-run setup</h1>

        {step === 'credentials' && (
          <form onSubmit={submitCredentials} className="space-y-4">
            <p className="text-sm text-gray-600">
              Enter the bootstrap token from the server log and choose a password.
            </p>
            <input type="text" placeholder="Bootstrap token" value={bootstrapToken}
              onChange={e => setBootstrapToken(e.target.value)}
              className="w-full border rounded px-3 py-2" />
            <input type="password" placeholder="Choose a password" value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full border rounded px-3 py-2" />
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <button type="submit" className="w-full bg-blue-600 text-white rounded py-2">Continue</button>
          </form>
        )}

        {step === 'totp' && (
          <form onSubmit={submitTotp} className="space-y-4">
            <p className="text-sm text-gray-600">Scan with your authenticator app, then enter the code.</p>
            <div className="flex justify-center"><QRCodeSVG value={uri} size={180} /></div>
            <input type="text" inputMode="numeric" placeholder="6-digit code" value={code}
              onChange={e => setCode(e.target.value)}
              className="w-full border rounded px-3 py-2" />
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <button type="submit" className="w-full bg-blue-600 text-white rounded py-2">Verify</button>
          </form>
        )}

        {step === 'recovery' && (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              Save these recovery codes somewhere safe. Each can be used once in place of your authenticator.
            </p>
            <ul className="grid grid-cols-2 gap-1 font-mono text-sm bg-gray-50 p-3 rounded">
              {recovery.map(c => <li key={c}>{c}</li>)}
            </ul>
            <button onClick={onComplete} className="w-full bg-blue-600 text-white rounded py-2">
              I've saved them — continue
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/SetupWizard.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat: add SetupWizard component with TOTP QR + recovery codes"
```

---

## Task 14: `App.tsx` — branch on auth status

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Import auth pieces**

Add to the imports in `frontend/src/App.tsx`:

```tsx
import LoginScreen from './views/LoginScreen'
import SetupWizard from './views/SetupWizard'
import { getAuthState, setUnauthorizedHandler } from './api/client'
import type { AuthState } from './api/types'
```

- [ ] **Step 2: Add auth state and gate before the main render**

Near the other `useState` calls, add:

```tsx
  const [authState, setAuthState] = useState<AuthState | null>(null)
```

After the existing effects, add an effect that resolves auth on mount and registers the 401 handler:

```tsx
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState('unauthenticated'))
    getAuthState().then(setAuthState).catch(() => setAuthState('unauthenticated'))
  }, [])
```

- [ ] **Step 3: Render the gate**

Immediately before the component's main `return (` (the existing app UI), add:

```tsx
  if (authState === null) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">Loading…</div>
  }
  if (authState === 'setup_required') {
    return <SetupWizard onComplete={() => setAuthState('authenticated')} />
  }
  if (authState === 'unauthenticated') {
    return <LoginScreen onAuthenticated={() => setAuthState('authenticated')} />
  }
```

Note: the existing health-poll and SSE effects run regardless; they will simply receive 401s until authenticated, and the registered handler keeps `authState` correct. This is acceptable — no code change needed there.

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: gate app on auth status (setup/login/app)"
```

---

## Task 15: Settings — Account / Security section

**Files:**
- Modify: `frontend/src/views/Settings.tsx`

- [ ] **Step 1: Add change-password + logout UI**

In `frontend/src/views/Settings.tsx`, import the auth functions:

```tsx
import { changePassword, logout } from '../api/client'
```

Add local state and handlers within the `Settings` component:

```tsx
  const [curPw, setCurPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [pwCode, setPwCode] = useState('')
  const [pwMsg, setPwMsg] = useState<string | null>(null)

  async function submitPasswordChange() {
    setPwMsg(null)
    try {
      await changePassword(curPw, newPw, pwCode)
      setPwMsg('Password changed.')
      setCurPw(''); setNewPw(''); setPwCode('')
    } catch {
      setPwMsg('Failed — check current password and code.')
    }
  }
```

Render a new section (place it consistently with the existing Settings sections' markup/classes):

```tsx
  <section className="space-y-3">
    <h2 className="text-lg font-semibold">Account &amp; Security</h2>
    <input type="password" placeholder="Current password" value={curPw}
      onChange={e => setCurPw(e.target.value)} className="w-full border rounded px-3 py-2" />
    <input type="password" placeholder="New password" value={newPw}
      onChange={e => setNewPw(e.target.value)} className="w-full border rounded px-3 py-2" />
    <input type="text" inputMode="numeric" placeholder="Authenticator code" value={pwCode}
      onChange={e => setPwCode(e.target.value)} className="w-full border rounded px-3 py-2" />
    {pwMsg && <p className="text-sm">{pwMsg}</p>}
    <div className="flex gap-2">
      <button onClick={submitPasswordChange} className="bg-blue-600 text-white rounded px-4 py-2">
        Change password
      </button>
      <button onClick={() => logout().then(() => window.location.reload())}
        className="bg-gray-200 rounded px-4 py-2">Log out</button>
    </div>
  </section>
```

(If `useState` is not already imported in this file, add it to the React import.)

- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Settings.tsx
git commit -m "feat: add Account & Security settings (change password, logout)"
```

---

## Task 16: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Start backend and frontend against a clean data dir**

Run:
```bash
cd backend && DISCOGS_BROWSER_DATA=/tmp/db-auth-e2e uvicorn main:app --reload --port 8000 &
cd frontend && npm run dev
```

- [ ] **Step 2: Verify setup wizard**

Open http://localhost:5173. Expect the setup wizard. Read the bootstrap token from the uvicorn log, enter it + a password, scan the QR with an authenticator, enter the code, save the recovery codes, continue. Expect the normal app.

- [ ] **Step 3: Verify session enforcement**

In a separate terminal: `curl -s localhost:8000/api/releases` → expect 401. Reload the SPA → still authenticated (cookie present).

- [ ] **Step 4: Verify logout + login**

In Settings, click Log out. Expect the login screen. Log in with password + authenticator code. Expect the app.

- [ ] **Step 5: Verify recovery + reset**

Log out, log in using a recovery code instead of the TOTP → succeeds. Then run `cd backend && DISCOGS_BROWSER_DATA=/tmp/db-auth-e2e python -m reset_owner`, restart backend, reload SPA → back to setup wizard with a new bootstrap token.

- [ ] **Step 6: Stop servers.**

---

## Task 17: Documentation

**Files:**
- Modify: `backend/CLAUDE.md` or repo `CLAUDE.md` (Key invariants), `README.md`

- [ ] **Step 1: Document auth in CLAUDE.md**

Add a "Key invariants" bullet: app auth is a single owner (password + TOTP), always enforced by `AuthMiddleware`; `/api/auth/*` is app auth, `/api/crawler-auth/*` is the crawler browser-login flow; recover via `python -m reset_owner`.

- [ ] **Step 2: Document deployment posture in README**

Add: `Secure` cookie is set from `X-Forwarded-Proto`/scheme; production must terminate TLS and pass `X-Forwarded-Proto` (and run uvicorn with `--proxy-headers`). First run prints a bootstrap token to the log; complete setup at the app URL.

- [ ] **Step 3: Commit**

```bash
git add backend/CLAUDE.md README.md
git commit -m "docs: document app authentication and deployment posture"
```

---

## Notes for the implementer

- **Proxy headers.** For the conditional `Secure` cookie to work behind nginx, uvicorn must run with `--proxy-headers --forwarded-allow-ips="*"` (or equivalent) so `X-Forwarded-Proto` is trusted. Update the Docker/compose start command accordingly when TLS is introduced; not required for the plain-HTTP LAN path.
- **Test isolation.** The `conn` fixture (in `tests/conftest.py`) provides an in-memory DB with `init_db` already run, and `tmp_config_dir` patches `config.*` paths including `CONFIG_DIR` — so `config.BOOTSTRAP_TOKEN_FILE` resolves under the temp dir. `BOOTSTRAP_TOKEN_FILE` is computed at import time from `CONFIG_DIR`; if a test needs the patched path, reference `config.BOOTSTRAP_TOKEN_FILE` through the `config` module (the tests above do), and set/clear it explicitly within the test.
- **SSE under auth.** `GET /api/crawl/stream` is a GET (non-mutating), so it needs no `X-Requested-With`; `EventSource` sends the session cookie same-origin automatically. It is gated by the middleware like any other `/api` route.
```
