# Discogs OAuth Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-owner password+TOTP auth system with Discogs OAuth 1.0a login, gated by invite code for new accounts, built on the Postgres/RLS foundation from the multi-tenant data model plan.

**Architecture:** A new `oauth_discogs.py` module drives the three-legged OAuth 1.0a handshake against Discogs. Two new pre-session tables (`oauth_request_state`, `pending_signups`) hold short-lived state between redirects, with expiry enforced at read time via `DELETE ... RETURNING`, not a cleanup job. `AuthMiddleware` and all auth endpoints move from `db.get_connection()`/SQLite-era functions to `db.get_identity_pool()`, matching the `app_identity` role the data model plan built specifically for pre-context identity lookups. The Discogs OAuth token pair is encrypted at rest with Fernet before ever reaching Postgres. The entire password/TOTP/recovery-code/bootstrap-token surface is deleted outright — no shim, no flag.

**Tech Stack:** `authlib` (OAuth1 client over `httpx`, this app's existing HTTP client), `cryptography` (Fernet symmetric encryption). Same Postgres/pytest-dotenv test setup as the data-model plan — this plan's tests assume `backend/.env` and a running `discogs-browser-pg` container already exist from that work.

**Out of scope for this plan:** wiring any business router (`collection.py`, `releases.py`, `settings.py`) to use `request.state.user_id`/`db.user_scope()` — that's each router's own later rewiring job. Restoring `main.py`'s ability to fully boot — `register_crawler`/crawler-seeding remain broken, owned by the crawl-queue plan; this plan only removes the auth-specific dead code from `main.py`'s startup, it doesn't fix the unrelated import that still breaks it. A cleanup sweep for expired `oauth_request_state`/`pending_signups` rows — already-expired rows are already unusable (checked at read time), so an undeleted one is inert clutter, not a bug.

**A note on unverified assumptions carried over from planning:** `authlib`'s exact OAuth1 client API (constructor args, method names) is synthesized from its documentation, which did not show a complete worked OAuth1-over-httpx example at the time this plan was written. Task 6 includes an explicit verification step against the *installed* package before trusting the code below.

---

### Task 1: Token encryption

**Files:**
- Create: `backend/token_encryption.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/test_token_encryption.py`

- [ ] **Step 1: Add the `cryptography` dependency**

In `backend/pyproject.toml`, add to `dependencies`:

```toml
    "cryptography>=42,<43",
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_token_encryption.py
import pytest
from cryptography.fernet import Fernet

import config
import token_encryption


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_encrypt_then_decrypt_roundtrips():
    ciphertext = token_encryption.encrypt("my-oauth-token-secret")
    assert token_encryption.decrypt(ciphertext) == "my-oauth-token-secret"


def test_ciphertext_is_not_the_plaintext():
    ciphertext = token_encryption.encrypt("my-oauth-token-secret")
    assert b"my-oauth-token-secret" not in ciphertext


def test_decrypt_with_wrong_key_fails_loudly():
    ciphertext = token_encryption.encrypt("my-oauth-token-secret")
    import config as config_module
    from cryptography.fernet import Fernet as _Fernet, InvalidToken

    config_module.TOKEN_ENCRYPTION_KEY = _Fernet.generate_key().decode()
    with pytest.raises(InvalidToken):
        token_encryption.decrypt(ciphertext)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_token_encryption.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'token_encryption'`

- [ ] **Step 4: Write `backend/token_encryption.py`**

```python
# backend/token_encryption.py
from cryptography.fernet import Fernet

import config


def encrypt(plaintext: str) -> bytes:
    return Fernet(config.TOKEN_ENCRYPTION_KEY.encode()).encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return Fernet(config.TOKEN_ENCRYPTION_KEY.encode()).decrypt(ciphertext).decode()
```

Note: this reads `config.TOKEN_ENCRYPTION_KEY` fresh on every call rather than caching a `Fernet` instance at import time — deliberately, so the `monkeypatch.setattr` pattern in tests (and any future key-rotation logic) works without needing a cache-invalidation path. `config.TOKEN_ENCRYPTION_KEY` itself is added in Task 5.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_token_encryption.py -v`
Expected: FAIL — `config` has no `TOKEN_ENCRYPTION_KEY` attribute yet (Task 5 adds it). This is expected; proceed to Step 6 to unblock this specific test only, without doing Task 5's full scope here.

- [ ] **Step 6: Add a minimal placeholder-free stub so this task's own test can pass in isolation**

Add just the one line to `backend/config.py` (Task 5 will build out the rest of the new config vars around it):

```python
TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
```

Run: `cd backend && pytest tests/test_token_encryption.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd backend && git add pyproject.toml token_encryption.py config.py tests/test_token_encryption.py
git commit -m "feat: add Fernet-based token encryption"
```
(append the required AI-attribution trailer block described in this repo's `CLAUDE.md`)

---

### Task 2: Extract session tokens, delete the legacy password/TOTP module

**Files:**
- Create: `backend/session_tokens.py`
- Create: `backend/tests/test_session_tokens.py`
- Delete: `backend/auth_core.py`
- Delete: `backend/tests/test_auth_core.py`

`auth_core.py` currently has 8 functions: `hash_password`/`verify_password` (Argon2), `generate_totp_secret`/`totp_provisioning_uri`/`verify_totp` (TOTP), `generate_recovery_codes`, and `new_session_token`/`hash_token` (generic opaque-token helpers, unrelated to password/TOTP). Only the last two survive — Discogs OAuth replaces the rest entirely.

`test_auth_core.py` currently has 7 tests and **all 7 currently pass** (verified: this module has no `db.py` dependency, so it survived the data-model plan's hard cutover intact). Deleting it is an intentional removal of coverage for code that's being deleted, not an accidental regression — call this out explicitly if anyone asks why a previously-green test file disappeared.

- [ ] **Step 1: Write the failing test for the extracted module**

```python
# backend/tests/test_session_tokens.py
import session_tokens


def test_session_token_and_hash():
    tok = session_tokens.new_session_token()
    assert len(tok) >= 32
    assert session_tokens.hash_token(tok) != tok
    assert session_tokens.hash_token(tok) == session_tokens.hash_token(tok)
```

(This is the exact `test_session_token_and_hash` test currently in `test_auth_core.py`, moved verbatim to its new home.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_session_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_tokens'`

- [ ] **Step 3: Write `backend/session_tokens.py`**

```python
# backend/session_tokens.py
import hashlib
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_session_tokens.py -v`
Expected: PASS

- [ ] **Step 5: Delete the legacy module and its test file**

```bash
cd backend && git rm auth_core.py tests/test_auth_core.py
```

- [ ] **Step 6: Confirm nothing else still imports `auth_core`**

Run: `cd backend && grep -rn "auth_core" --include='*.py' .`
Expected: no output (this task's later steps haven't rewired `routers/session.py`/`auth_middleware.py` yet — those happen in Tasks 7-8 — so if this greps clean already, it confirms nothing *else* in the currently-collectible test surface depends on it. If it's not clean, note what still references it and do not proceed — that's a sign something in this codebase depends on `auth_core` beyond what this plan accounted for.)

- [ ] **Step 7: Run the full test suite for regressions**

Run: `cd backend && pytest tests/test_session_tokens.py tests/test_token_encryption.py -v`
Expected: PASS. (Do not run the full unfiltered suite expecting green — `routers/session.py` and `auth_middleware.py` still reference the now-deleted `auth_core` until Tasks 7-8 land; that's expected mid-plan breakage, not a regression from this task.)

- [ ] **Step 8: Commit**

```bash
cd backend && git add session_tokens.py tests/test_session_tokens.py
git commit -m "refactor: extract session token helpers, delete legacy password/TOTP module"
```

---

### Task 3: OAuth handshake state tables

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_oauth_state.py`

Extends the existing `TENANT_SCHEMA`/`init_tenant_schema()` in `db.py` (the same function that already creates `users`/`sessions`/`library_items`/`invites`) rather than introducing a third schema-init function — `oauth_request_state` and `pending_signups` are pre-session state owned by `app_identity`, exactly like `invites` already is.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_oauth_state.py
from datetime import datetime, timedelta

import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE oauth_request_state, pending_signups CASCADE")
        conn.commit()


def test_create_and_consume_oauth_request_state(admin_conn):
    db.create_oauth_request_state(admin_conn, "req-token-1", "req-secret-1")
    admin_conn.commit()

    row = db.get_and_delete_oauth_request_state(admin_conn, "req-token-1")
    admin_conn.commit()
    assert row["request_token_secret"] == "req-secret-1"

    # single-use: a second consume attempt finds nothing
    assert db.get_and_delete_oauth_request_state(admin_conn, "req-token-1") is None


def test_get_and_delete_oauth_request_state_returns_none_when_missing(admin_conn):
    assert db.get_and_delete_oauth_request_state(admin_conn, "does-not-exist") is None


def test_expired_oauth_request_state_is_rejected_and_still_deleted(admin_conn):
    admin_conn.execute(
        "INSERT INTO oauth_request_state (request_token, request_token_secret, created_at) "
        "VALUES (%s, %s, %s)",
        ["old-token", "old-secret", datetime.utcnow() - timedelta(minutes=11)],
    )
    admin_conn.commit()

    assert db.get_and_delete_oauth_request_state(admin_conn, "old-token", max_age_minutes=10) is None
    remaining = admin_conn.execute(
        "SELECT 1 FROM oauth_request_state WHERE request_token = 'old-token'"
    ).fetchone()
    assert remaining is None


def test_create_and_consume_pending_signup(admin_conn):
    db.create_pending_signup(
        admin_conn, "signup-token-1", 777, "alice", b"encrypted-token", b"encrypted-secret"
    )
    admin_conn.commit()

    row = db.get_and_delete_pending_signup(admin_conn, "signup-token-1")
    admin_conn.commit()
    assert row["discogs_user_id"] == 777
    assert row["discogs_username"] == "alice"
    assert row["oauth_token_encrypted"] == b"encrypted-token"
    assert row["oauth_secret_encrypted"] == b"encrypted-secret"

    assert db.get_and_delete_pending_signup(admin_conn, "signup-token-1") is None


def test_expired_pending_signup_is_rejected_and_still_deleted(admin_conn):
    admin_conn.execute(
        "INSERT INTO pending_signups (signup_token, discogs_user_id, discogs_username, "
        "oauth_token_encrypted, oauth_secret_encrypted, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ["old-signup", 1, "bob", b"x", b"y", datetime.utcnow() - timedelta(minutes=16)],
    )
    admin_conn.commit()

    assert db.get_and_delete_pending_signup(admin_conn, "old-signup", max_age_minutes=15) is None
    remaining = admin_conn.execute(
        "SELECT 1 FROM pending_signups WHERE signup_token = 'old-signup'"
    ).fetchone()
    assert remaining is None
```

(This step's `token` column name was renamed to `signup_token` during this task's own code-quality review, commit `a5bd070` — updated here to match, since this plan text was never backported when the rename happened. Task 7's plan text below had the same staleness, caught and fixed when that task was actually implemented.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_oauth_state.py -v`
Expected: FAIL with `psycopg.errors.UndefinedTable: relation "oauth_request_state" does not exist` (the schema doesn't have these tables yet).

- [ ] **Step 3: Extend `TENANT_SCHEMA` and add CRUD to `db.py`**

Find the closing `"""` of the existing `TENANT_SCHEMA` string in `db.py` (right before the `ALTER TABLE users ENABLE ROW LEVEL SECURITY;` block) and insert these two tables above it:

```sql
-- Neither table gets ENABLE ROW LEVEL SECURITY: both are pre-session state
-- with no per-user row-ownership column to scope a policy on in the first
-- place (unlike users/sessions, where the omission is about grants doing
-- the real work — here a policy would be actively meaningless, not just
-- redundant).
CREATE TABLE IF NOT EXISTS oauth_request_state (
    request_token TEXT PRIMARY KEY,
    request_token_secret TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_signups (
    signup_token TEXT PRIMARY KEY,
    discogs_user_id INTEGER NOT NULL,
    discogs_username TEXT NOT NULL,
    oauth_token_encrypted BYTEA NOT NULL,
    oauth_secret_encrypted BYTEA NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

In `init_tenant_schema()`, alongside the existing `app_identity` grants (`GRANT SELECT, INSERT, UPDATE ON users`, etc.), add:

```python
        conn.execute("GRANT SELECT, INSERT, DELETE ON oauth_request_state TO app_identity")
        conn.execute("GRANT SELECT, INSERT, DELETE ON pending_signups TO app_identity")
```

Then append the CRUD functions to `db.py`:

```python
# backend/db.py (append)
def create_oauth_request_state(conn, request_token: str, request_token_secret: str):
    conn.execute(
        "INSERT INTO oauth_request_state (request_token, request_token_secret) VALUES (%s, %s)",
        [request_token, request_token_secret],
    )


def get_and_delete_oauth_request_state(conn, request_token: str, max_age_minutes: int = 10) -> Optional[dict]:
    row = conn.execute(
        """
        DELETE FROM oauth_request_state WHERE request_token = %(request_token)s
        RETURNING request_token_secret,
                  created_at > NOW() - (%(max_age_minutes)s || ' minutes')::interval AS is_valid
        """,
        {"request_token": request_token, "max_age_minutes": max_age_minutes},
    ).fetchone()
    if row is None or not row["is_valid"]:
        return None
    return row


# Callers must Fernet-encrypt oauth_token_encrypted/oauth_secret_encrypted
# before calling — this function does not enforce it.
def create_pending_signup(
    conn,
    signup_token: str,
    discogs_user_id: int,
    discogs_username: str,
    oauth_token_encrypted: bytes,
    oauth_secret_encrypted: bytes,
):
    conn.execute(
        """
        INSERT INTO pending_signups
            (signup_token, discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [signup_token, discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted],
    )


def get_and_delete_pending_signup(conn, signup_token: str, max_age_minutes: int = 15) -> Optional[dict]:
    row = conn.execute(
        """
        DELETE FROM pending_signups WHERE signup_token = %(signup_token)s
        RETURNING discogs_user_id, discogs_username, oauth_token_encrypted, oauth_secret_encrypted,
                  created_at > NOW() - (%(max_age_minutes)s || ' minutes')::interval AS is_valid
        """,
        {"signup_token": signup_token, "max_age_minutes": max_age_minutes},
    ).fetchone()
    if row is None or not row["is_valid"]:
        return None
    return row
```

Both consume functions use `DELETE ... RETURNING` as a single atomic statement, not a separate `SELECT` followed by a `DELETE` — closing a TOCTOU class of bug an earlier plan's review caught elsewhere in this codebase. The expiry check itself is computed inside the `RETURNING` clause via `NOW() - INTERVAL ...`, entirely within Postgres's own frame of reference, rather than comparing the returned `created_at` against Python's `datetime.utcnow()` — the latter would silently depend on the Postgres session's `TimeZone` GUC matching Python's UTC assumption. (This whole block reflects the corrected version from this task's own code-quality review, commit `a5bd070` — an earlier draft compared `created_at` in Python and used `token` instead of `signup_token` as the column name; both are fixed here, not just in the code that actually shipped.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_oauth_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add db.py tests/test_oauth_state.py
git commit -m "feat: add oauth_request_state and pending_signups tables with CRUD"
```

---

### Task 4: Session CRUD

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_session_crud.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_session_crud.py
from datetime import datetime, timedelta

import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, sessions CASCADE")
        conn.commit()


def test_create_session_then_get_by_token_hash(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()

    expires_at = datetime.utcnow() + timedelta(days=30)
    db.create_session(admin_conn, "hash-abc", user["id"], expires_at)
    admin_conn.commit()

    row = db.get_session_by_token_hash(admin_conn, "hash-abc")
    assert row["user_id"] == user["id"]


def test_get_session_by_token_hash_returns_none_when_missing(admin_conn):
    assert db.get_session_by_token_hash(admin_conn, "does-not-exist") is None


def test_touch_session_updates_last_seen_at(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()
    original_time = datetime.utcnow() - timedelta(minutes=5)
    db.create_session(
        admin_conn, "hash-abc", user["id"], datetime.utcnow() + timedelta(days=30), now=original_time
    )
    admin_conn.commit()
    original = db.get_session_by_token_hash(admin_conn, "hash-abc")["last_seen_at"]
    assert original == original_time

    touched_time = datetime.utcnow()
    db.touch_session(admin_conn, "hash-abc", now=touched_time)
    admin_conn.commit()
    updated = db.get_session_by_token_hash(admin_conn, "hash-abc")["last_seen_at"]
    assert updated == touched_time
    assert updated > original


def test_delete_session_removes_it(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()
    db.create_session(admin_conn, "hash-abc", user["id"], datetime.utcnow() + timedelta(days=30))
    admin_conn.commit()

    db.delete_session(admin_conn, "hash-abc")
    admin_conn.commit()
    assert db.get_session_by_token_hash(admin_conn, "hash-abc") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_session_crud.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'create_session'`

- [ ] **Step 3: Implement the CRUD functions**

```python
# backend/db.py (append)
def create_session(
    conn, token_hash: str, user_id: int, expires_at: datetime, now: Optional[datetime] = None
):
    now = now or datetime.utcnow()
    conn.execute(
        """
        INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [token_hash, user_id, now, expires_at, now],
    )


def get_session_by_token_hash(conn, token_hash: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM sessions WHERE token_hash = %s", [token_hash]
    ).fetchone()


def touch_session(conn, token_hash: str, now: Optional[datetime] = None):
    now = now or datetime.utcnow()
    conn.execute(
        "UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s",
        [now, token_hash],
    )


def delete_session(conn, token_hash: str):
    conn.execute("DELETE FROM sessions WHERE token_hash = %s", [token_hash])
```

(Earlier draft of this step wrote `last_seen_at`/`created_at` via server-side `CURRENT_TIMESTAMP`. That reintroduces the exact class of bug Task 3's code review caught and fixed for `oauth_request_state`/`pending_signups`: a value written by Postgres's clock, later compared against Python's `datetime.utcnow()` in `AuthMiddleware`'s idle-expiry check (Task 8), silently depends on the Postgres session's `TimeZone` GUC matching Python's UTC assumption. `expires_at` was always safe — it's computed by the caller in Python and passed in directly, never touching a Postgres clock function — but `last_seen_at` wasn't. Fixed here by making `last_seen_at`/`created_at` Python-computed too, via an optional `now` parameter that defaults to `datetime.utcnow()`: every timestamp `AuthMiddleware` will ever compare against another Python-computed value now originates from Python on both sides, so the comparison is self-consistent regardless of Postgres's session timezone. The `now` parameter's real purpose is exactly this consistency guarantee, not test convenience — though it also happens to make the touch/create timestamps deterministically testable. Caught during Task 4's code-quality review, before Task 8 could reintroduce the bug it was written to prevent.)

These are written to run over `get_identity_pool()` connections in real usage (`AuthMiddleware`, the auth router) — like `create_user`/`get_user_by_discogs_id` before them, this task exercises them directly over the admin connection since RLS isn't relevant here (`sessions`' RLS policy is documented in `db.py` as defense-in-depth only; the actual protection is grant absence on `app_user`, already proven in the data-model plan).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_session_crud.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add db.py tests/test_session_crud.py
git commit -m "feat: add session CRUD helpers"
```

---

### Task 5: Config additions and bootstrap-token removal

**Files:**
- Modify: `backend/config.py`

- [ ] **Step 1: Add the new OAuth/encryption/frontend-redirect config vars**

In `backend/config.py`, add (near the existing `DATABASE_URL`/DSN block):

```python
DISCOGS_CONSUMER_KEY = os.environ.get("DISCOGS_CONSUMER_KEY", "")
DISCOGS_CONSUMER_SECRET = os.environ.get("DISCOGS_CONSUMER_SECRET", "")
# TOKEN_ENCRYPTION_KEY was added in Task 1; leave it where it is.

# Empty in production (SPA served same-origin, so a relative redirect from
# a backend-issued Location header lands on the SPA correctly). Set to
# http://localhost:5173 for local dev, where the backend (:8000) and the
# Vite dev server (:5173) are different origins.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "")
```

- [ ] **Step 2: Remove the bootstrap-token constant**

Delete this line entirely:

```python
BOOTSTRAP_TOKEN_FILE = CONFIG_DIR / "bootstrap_token"
```

- [ ] **Step 3: Confirm nothing still references it**

Run: `cd backend && grep -rn "BOOTSTRAP_TOKEN_FILE" --include='*.py' .`
Expected at this point in the plan: only `main.py` (Task 9 removes that usage) — if anything else references it, stop and investigate before proceeding.

- [ ] **Step 4: Commit**

```bash
cd backend && git add config.py
git commit -m "feat: add Discogs OAuth and frontend-redirect config, remove bootstrap-token constant"
```

---

### Task 6: Discogs OAuth1.0a client

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/config.py` (one new line — see Step 0)
- Create: `backend/oauth_discogs.py`
- Test: `backend/tests/test_oauth_discogs.py`

**Amendment, caught during this task's own code-quality review:** the original version of this step never set `oauth_callback` on the `POST /oauth/request_token` call. OAuth 1.0a (the "a" specifically closes a session-fixation hole in plain OAuth 1.0 by making the callback parameter mandatory, per RFC 5849 §2.1) requires it — omitting it risks Discogs rejecting the request outright, or silently falling back to out-of-band mode (a manually-copied verifier code instead of an automatic browser redirect), which would break the redirect-based flow Task 7 is built around. Fixed below by adding `BACKEND_BASE_URL` config and passing `redirect_uri` to every `OAuth1Client` construction. Also: the fail-fast credential check (`_require_consumer_credentials()`) is now called from all three functions, not just `start_handshake()` — `oauth_request_state` (Task 3) exists specifically so the handshake can span separate requests, plausibly separate process lifetimes if the app restarts in between, so "only `start_handshake()` checked, so the others don't need to" was never actually a safe assumption.

- [ ] **Step 0: Add the backend's own callback base URL to config**

In `backend/config.py`, near `FRONTEND_BASE_URL`, add:

```python
# The backend's own publicly-reachable base URL, used to build the OAuth
# callback Discogs redirects back to. Defaults to the local dev backend
# port; must be set to the real public URL in any non-local deployment.
BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")
```

Unlike `FRONTEND_BASE_URL` (empty-by-default, since production serves the SPA same-origin), this one defaults to a real value — there's no "same origin, so a relative URL works" escape hatch for a callback URL Discogs itself constructs and redirects to; it always needs to be a fully-qualified URL, so the default has to be *something* usable, and a local dev backend on port 8000 is the least-surprising default for this codebase.

- [ ] **Step 1: Add the `authlib` dependency**

In `backend/pyproject.toml`, add to `dependencies`:

```toml
    "authlib>=1.3,<2.0",
```

Run: `cd backend && pip install -e ".[dev]"` (or your environment's equivalent) to install it.

- [ ] **Step 2: Verify the installed API before writing against it**

This plan's code below is synthesized from `authlib`'s published docs, which did not show a complete worked OAuth1-over-httpx example at planning time. Before writing `oauth_discogs.py` for real, inspect the installed package directly:

```bash
cd backend && .venv/bin/python -c "from authlib.integrations.httpx_client import OAuth1Client; help(OAuth1Client)"
```

Confirm: the constructor accepts `client_id`/`client_secret` (and optionally `token`/`token_secret`); a `fetch_request_token(url)` method exists and returns a dict containing `oauth_token`/`oauth_token_secret`; a `create_authorization_url(url, request_token=...)` method exists; a `fetch_access_token(url, verifier)` method exists. **If any of these don't match**, adjust every code sample in this task to match what you actually find — do not force the installed library to match this plan's guess. Note in your task report whether the API matched as documented or needed adjustment.

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_oauth_discogs.py
import httpx
import pytest
import respx

import config
import oauth_discogs


@pytest.fixture(autouse=True)
def _consumer_credentials(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "consumer-key")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "consumer-secret")
    monkeypatch.setattr(config, "BACKEND_BASE_URL", "http://localhost:8000")


@respx.mock
def test_start_handshake_returns_token_secret_and_authorize_url():
    route = respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=req-token-123&oauth_token_secret=req-secret-456",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )

    result = oauth_discogs.start_handshake()

    assert result["oauth_token"] == "req-token-123"
    assert result["oauth_token_secret"] == "req-secret-456"
    assert "req-token-123" in result["authorize_url"]
    assert result["authorize_url"].startswith("https://www.discogs.com/oauth/authorize")

    # the whole point of this task's amendment: confirm oauth_callback was
    # actually sent, not just that the call succeeded against a mock that
    # doesn't care what parameters it received
    sent_request = route.calls.last.request
    auth_header = sent_request.headers["authorization"]
    assert "oauth_callback=" in auth_header
    assert "localhost%3A8000" in auth_header or "localhost:8000" in auth_header


@respx.mock
def test_start_handshake_raises_clearly_on_discogs_error_response():
    respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(401, text="invalid consumer key")
    )
    with pytest.raises(Exception):
        oauth_discogs.start_handshake()


def test_start_handshake_raises_clearly_when_consumer_credentials_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    with pytest.raises(RuntimeError, match="DISCOGS_CONSUMER_KEY"):
        oauth_discogs.start_handshake()


@respx.mock
def test_fetch_access_token_returns_token_and_secret():
    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=access-token-789&oauth_token_secret=access-secret-012",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )

    result = oauth_discogs.fetch_access_token("req-token-123", "req-secret-456", "verifier-code")

    assert result["oauth_token"] == "access-token-789"
    assert result["oauth_token_secret"] == "access-secret-012"


def test_fetch_access_token_raises_clearly_when_consumer_credentials_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    with pytest.raises(RuntimeError, match="DISCOGS_CONSUMER_KEY"):
        oauth_discogs.fetch_access_token("req-token-123", "req-secret-456", "verifier-code")


@respx.mock
def test_fetch_identity_returns_discogs_user_id_and_username():
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 777, "username": "alice"})
    )

    result = oauth_discogs.fetch_identity("access-token-789", "access-secret-012")

    assert result["id"] == 777
    assert result["username"] == "alice"


def test_fetch_identity_raises_clearly_when_consumer_credentials_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    with pytest.raises(RuntimeError, match="DISCOGS_CONSUMER_KEY"):
        oauth_discogs.fetch_identity("access-token-789", "access-secret-012")
```

The `oauth_callback` assertion in the first test inspects the request's signed `Authorization` header rather than the URL/body, since HMAC-SHA1 OAuth1 puts `oauth_*` parameters there by default for this kind of request — if your actual signed request puts it somewhere else (check what `respx`'s captured request actually looks like when you run this), adjust the assertion to check wherever it really ended up, but don't drop the assertion itself; the point is proving the parameter was sent, not matching this exact header-vs-body guess.

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && pytest tests/test_oauth_discogs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oauth_discogs'`

- [ ] **Step 5: Write `backend/oauth_discogs.py`**

```python
# backend/oauth_discogs.py
from authlib.integrations.httpx_client import OAuth1Client

import config

REQUEST_TOKEN_URL = "https://api.discogs.com/oauth/request_token"
AUTHORIZE_URL = "https://www.discogs.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://api.discogs.com/oauth/access_token"
IDENTITY_URL = "https://api.discogs.com/oauth/identity"


def _require_consumer_credentials():
    if not config.DISCOGS_CONSUMER_KEY or not config.DISCOGS_CONSUMER_SECRET:
        raise RuntimeError(
            "DISCOGS_CONSUMER_KEY and DISCOGS_CONSUMER_SECRET must both be set (non-empty) "
            "before using the Discogs OAuth client"
        )


def start_handshake() -> dict:
    _require_consumer_credentials()
    callback_url = f"{config.BACKEND_BASE_URL}/api/auth/discogs/callback"
    with OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        redirect_uri=callback_url,
    ) as client:
        request_token = client.fetch_request_token(REQUEST_TOKEN_URL)
        authorize_url = client.create_authorization_url(
            AUTHORIZE_URL, request_token=request_token["oauth_token"]
        )
        return {
            "oauth_token": request_token["oauth_token"],
            "oauth_token_secret": request_token["oauth_token_secret"],
            "authorize_url": authorize_url,
        }


def fetch_access_token(request_token: str, request_token_secret: str, verifier: str) -> dict:
    _require_consumer_credentials()
    with OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        token=request_token,
        token_secret=request_token_secret,
    ) as client:
        return client.fetch_access_token(ACCESS_TOKEN_URL, verifier)


def fetch_identity(oauth_token: str, oauth_token_secret: str) -> dict:
    _require_consumer_credentials()
    with OAuth1Client(
        client_id=config.DISCOGS_CONSUMER_KEY,
        client_secret=config.DISCOGS_CONSUMER_SECRET,
        token=oauth_token,
        token_secret=oauth_token_secret,
    ) as client:
        r = client.get(IDENTITY_URL)
        r.raise_for_status()
        return r.json()
```

`redirect_uri` is only passed on `start_handshake()`'s `OAuth1Client` — it's the `oauth_callback` parameter's source for the Temporary Credentials Request (RFC 5849 §2.1), which is the only one of the three calls that needs it; `fetch_access_token`/`fetch_identity` sign different request types where a callback parameter isn't part of the spec.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_oauth_discogs.py -v`
Expected: PASS. If it fails because the installed `authlib` API differs from Step 2's assumptions, fix the code (not the test's intent) to match what Step 2 found, then re-run.

- [ ] **Step 7: Commit**

```bash
cd backend && git add pyproject.toml config.py oauth_discogs.py tests/test_oauth_discogs.py
git commit -m "feat: add Discogs OAuth1.0a client wrapper"
```

---

### Task 7: Auth router rewrite

**Files:**
- Modify: `backend/routers/session.py`
- Test: `backend/tests/test_auth_router.py` (recreated — the old file was already broken pre-plan and covered password/TOTP endpoints that no longer exist; this replaces it with coverage of the new endpoints)

This is the largest task in the plan. Read it fully before starting — later steps depend on helper functions defined in earlier ones.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_auth_router.py
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import config
import db
from main import app


@pytest.fixture
def client(pg_test_db, monkeypatch):
    db.init_global_schema()
    db.init_tenant_schema()
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "consumer-key")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "consumer-secret")
    monkeypatch.setattr(config, "TOKEN_ENCRYPTION_KEY", "kL8mN2pQ7rT5vX9yB3cF6hJ1kM4nP8sU2wZ5aD7eG0i=")
    yield TestClient(app)
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "TRUNCATE users, sessions, oauth_request_state, pending_signups, invites CASCADE"
        )
        conn.commit()


def test_status_unauthenticated_with_no_cookie(client):
    r = client.get("/api/auth/status")
    assert r.json() == {"state": "unauthenticated"}


@respx.mock
def test_discogs_start_redirects_to_discogs_and_stores_request_state(client):
    respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=req-token-1&oauth_token_secret=req-secret-1",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )
    r = client.get("/api/auth/discogs/start", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "req-token-1" in r.headers["location"]

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT request_token_secret FROM oauth_request_state WHERE request_token = 'req-token-1'"
        ).fetchone()
    assert row["request_token_secret"] == "req-secret-1"


@respx.mock
def test_discogs_start_redirects_gracefully_when_discogs_request_token_call_fails(client):
    respx.post("https://api.discogs.com/oauth/request_token").mock(
        return_value=httpx.Response(401, text="invalid consumer key")
    )
    r = client.get("/api/auth/discogs/start", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "auth_error=discogs_failed" in r.headers["location"]


def test_discogs_start_redirects_gracefully_when_consumer_credentials_unset(client, monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_KEY", "")
    monkeypatch.setattr(config, "DISCOGS_CONSUMER_SECRET", "")
    r = client.get("/api/auth/discogs/start", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "auth_error=discogs_failed" in r.headers["location"]


@respx.mock
def test_callback_for_existing_user_creates_session_and_redirects(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=777, discogs_username="alice")
        db.create_oauth_request_state(conn, "req-token-1", "req-secret-1")
        conn.commit()

    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=access-1&oauth_token_secret=access-secret-1",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 777, "username": "alice"})
    )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-1", "oauth_verifier": "verifier-1"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "signup_pending" not in r.headers["location"]
    assert config.COOKIE_NAME in r.cookies

    with db.get_admin_pool().connection() as conn:
        session = db.get_session_by_token_hash(
            conn, __import__("session_tokens").hash_token(r.cookies[config.COOKIE_NAME])
        )
    assert session["user_id"] == user["id"]


@respx.mock
def test_callback_for_new_user_creates_pending_signup_and_redirects_with_token(client):
    with db.get_admin_pool().connection() as conn:
        db.create_oauth_request_state(conn, "req-token-2", "req-secret-2")
        conn.commit()

    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            text="oauth_token=access-2&oauth_token_secret=access-secret-2",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    )
    respx.get("https://api.discogs.com/oauth/identity").mock(
        return_value=httpx.Response(200, json={"id": 888, "username": "bob"})
    )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-2", "oauth_verifier": "verifier-2"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "signup_pending=" in r.headers["location"]
    assert config.COOKIE_NAME not in r.cookies


@respx.mock
def test_callback_redirects_gracefully_when_discogs_access_token_call_fails(client):
    with db.get_admin_pool().connection() as conn:
        db.create_oauth_request_state(conn, "req-token-3", "req-secret-3")
        conn.commit()

    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(401, text="invalid or expired verifier")
    )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-3", "oauth_verifier": "bad-verifier"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "auth_error=discogs_failed" in r.headers["location"]
    assert config.COOKIE_NAME not in r.cookies


@respx.mock
def test_discogs_callback_locks_out_after_repeated_failures(client, monkeypatch):
    # RateLimiter copies max_failures into an instance attribute at
    # construction time, so patching config.LOGIN_MAX_FAILURES afterward
    # never reaches the already-built module-level discogs_oauth_limiter —
    # swap the instance itself instead.
    from rate_limit import RateLimiter
    import routers.session as session_router
    monkeypatch.setattr(
        session_router, "discogs_oauth_limiter", RateLimiter(2, config.LOGIN_LOCKOUT_SECONDS)
    )
    respx.post("https://api.discogs.com/oauth/access_token").mock(
        return_value=httpx.Response(401, text="invalid or expired verifier")
    )
    for _ in range(2):
        with db.get_admin_pool().connection() as conn:
            db.create_oauth_request_state(conn, "req-token-lockout", "req-secret-lockout")
            conn.commit()
        client.get(
            "/api/auth/discogs/callback",
            params={"oauth_token": "req-token-lockout", "oauth_verifier": "bad"},
            follow_redirects=False,
        )

    r = client.get(
        "/api/auth/discogs/callback",
        params={"oauth_token": "req-token-lockout", "oauth_verifier": "bad"},
    )
    assert r.status_code == 429


def test_redeem_invite_creates_user_and_session(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        conn.execute(
            "INSERT INTO invites (code, created_by) VALUES (%s, %s)",
            ["INVITE123", admin_user["id"]],
        )
        db.create_pending_signup(
            conn, "signup-token-1", 888, "bob", b"encrypted-token", b"encrypted-secret"
        )
        conn.commit()

    r = client.post(
        "/api/auth/redeem-invite",
        json={"signup_token": "signup-token-1", "invite_code": "INVITE123"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert config.COOKIE_NAME in r.cookies

    with db.get_admin_pool().connection() as conn:
        user = db.get_user_by_discogs_id(conn, 888)
        invite = conn.execute(
            "SELECT redeemed_by FROM invites WHERE code = 'INVITE123'"
        ).fetchone()
    assert user is not None
    assert invite["redeemed_by"] == user["id"]


def test_redeem_invite_rejects_already_redeemed_code(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        other_user = db.create_user(conn, discogs_user_id=2, discogs_username="other")
        conn.execute(
            "INSERT INTO invites (code, created_by, redeemed_by, redeemed_at) "
            "VALUES (%s, %s, %s, CURRENT_TIMESTAMP)",
            ["USED123", admin_user["id"], other_user["id"]],
        )
        db.create_pending_signup(
            conn, "signup-token-2", 999, "carol", b"x", b"y"
        )
        conn.commit()

    r = client.post(
        "/api/auth/redeem-invite",
        json={"signup_token": "signup-token-2", "invite_code": "USED123"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 400

    # rejection must not have burned the pending signup — it should still be redeemable
    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM pending_signups WHERE signup_token = 'signup-token-2'"
        ).fetchone()
    assert row is not None


def test_redeem_invite_rejects_expired_pending_signup(client):
    with db.get_admin_pool().connection() as conn:
        admin_user = db.create_user(conn, discogs_user_id=1, discogs_username="admin")
        conn.execute(
            "INSERT INTO invites (code, created_by) VALUES (%s, %s)",
            ["VALID999", admin_user["id"]],
        )
        conn.execute(
            "INSERT INTO pending_signups (signup_token, discogs_user_id, discogs_username, "
            "oauth_token_encrypted, oauth_secret_encrypted, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ["old-signup", 5, "dave", b"x", b"y", datetime.utcnow() - timedelta(minutes=20)],
        )
        conn.commit()

    r = client.post(
        "/api/auth/redeem-invite",
        json={"signup_token": "old-signup", "invite_code": "VALID999"},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.status_code == 400


def test_logout_deletes_session_and_clears_cookie(client):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=42, discogs_username="alice")
        conn.commit()
    import session_tokens
    token = session_tokens.new_session_token()
    with db.get_admin_pool().connection() as conn:
        db.create_session(conn, session_tokens.hash_token(token), user["id"], datetime.utcnow() + timedelta(days=1))
        conn.commit()

    client.cookies.set(config.COOKIE_NAME, token)
    r = client.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200

    with db.get_admin_pool().connection() as conn:
        assert db.get_session_by_token_hash(conn, session_tokens.hash_token(token)) is None
```

Note: these tests import `main.app` directly, which at this point in the plan still fails to import (per Task 9, `main.py`'s crawler-registration imports remain broken — that's this plan's own documented non-goal). **This means Step 1's tests cannot actually run yet.** Skip ahead and do Task 9's `main.py` auth-line cleanup *before* running this task's tests for real, or — simpler, and the better default — build this router file and unit-test its logic against a lighter-weight FastAPI app that only mounts this one router, not the full `main.app`:

```python
# add near the top of test_auth_router.py, replacing the `from main import app` import
from fastapi import FastAPI
from auth_middleware import AuthMiddleware
from routers import session as session_router

app = FastAPI()
app.add_middleware(AuthMiddleware)
app.include_router(session_router.router, prefix="/api")
```

This sidesteps `main.py`'s unrelated brokenness entirely and is more in the spirit of this plan's own scope boundary (auth logic tested in isolation, matching the data-model plan's precedent). Use this version, not the `from main import app` version shown in the fixture above — that line was left in deliberately as an example of the wrong approach to catch here, not something to actually ship.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_auth_router.py -v`
Expected: FAIL — the current `routers/session.py` still has the old password/TOTP endpoints and imports the now-deleted `auth_core`, so this will fail at collection (`ImportError`) or with 404s on the new routes, not the specific errors shown for earlier tasks. Confirm it fails for one of these reasons before proceeding.

- [ ] **Step 3: Rewrite `backend/routers/session.py`**

Replace the entire file content:

```python
# backend/routers/session.py
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

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

    # Amendment, caught during the final whole-branch review: an earlier
    # version called start_handshake() unwrapped. Since this endpoint is a
    # real <a href> browser navigation (not fetch), any failure here — a
    # Discogs outage, or _require_consumer_credentials()'s RuntimeError on a
    # misconfigured deployment — surfaced as a raw 500 instead of the
    # graceful ?auth_error=discogs_failed redirect discogs_callback already
    # had for its own Discogs-call failures.
    try:
        handshake = oauth_discogs.start_handshake()
    except Exception:
        log.warning("Discogs OAuth handshake start failed")
        return RedirectResponse(f"{config.FRONTEND_BASE_URL}/?auth_error=discogs_failed")

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
```

Note the `_create_session_for_user(conn, ...)` signature: it takes the caller's own connection and does not commit — both call sites (`discogs_callback`'s existing-user branch, `redeem_invite`) pass their own already-open `conn` and commit once, at the true end of their own transaction. Avatar endpoints (`upload_avatar`/`get_avatar`/`remove_avatar`) from the old file are unrelated to auth and are **not** shown removed above because they should be preserved — copy them back in from the pre-rewrite version of this file if your editor's rewrite dropped them; they don't belong in this plan's scope to touch.

**Amendment, caught during this task's own code-quality review:** three fixes folded into the code above (not shown as a separate diff, since this task hadn't been reviewed against a shipped commit yet when this was caught):
1. **`discogs_oauth_limiter` added**, guarding both `/auth/discogs/start` (volume-capped — every call counts, not just failures, since each one is a real outbound request to Discogs under this app's single shared consumer key) and `/auth/discogs/callback` (failure-capped — an expired/missing `oauth_request_state` or a raised exception from the Discogs API calls counts as a failure). This directly fixes a contradiction with the architecture spec's own stated intent ("the existing in-memory `RateLimiter` is carried over as-is — still useful against invite-redemption/callback abuse") that an earlier draft of this router only half-implemented (rate limiting `redeem-invite` but not `callback`/`start` at all).
2. **`fetch_access_token`/`fetch_identity` wrapped in `try/except`** inside `discogs_callback`. An earlier draft let any exception from these calls (a bad/expired verifier, the user declining consent on Discogs's page, a Discogs-side outage) propagate unhandled to Starlette's default error page — stranding the user mid-navigation with no way back into the app, and inconsistent with this same function's own `state is None` branch three lines above, which already redirects gracefully instead of raising. Now redirects to `?auth_error=discogs_failed`, matching the existing `?auth_error=expired` convention.
3. **Removed the unused `response: Response` parameter from `discogs_callback`'s signature.** Neither branch used it — each constructs and returns its own `RedirectResponse` instead, so the injected parameter was silently dead code that could mislead a future editor into thinking `response.set_cookie(...)` on it would do something.

**Known, deliberately deferred concern, not fixed here:** `_is_secure`'s trust in the client-suppliable `X-Forwarded-Proto` header (carried over unchanged from the old single-owner design) has no reverse-proxy contract established anywhere in this project — `docker-compose.yml` runs the backend with nothing in front of it to strip or overwrite that header. This is a real gap in a publicly-hosted, multi-tenant context, but fixing it means deciding on a deployment/reverse-proxy topology, which is out of this task's scope (and the architecture spec's own stated non-goal of leaving hosting/infra decisions for later). Flagged here so it isn't silently lost track of before that decision gets made.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_auth_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add routers/session.py tests/test_auth_router.py
git commit -m "feat: replace password/TOTP auth router with Discogs OAuth flow"
```

---

### Task 8: AuthMiddleware rewrite

**Files:**
- Modify: `backend/auth_middleware.py`
- Test: `backend/tests/test_auth_middleware.py` (recreated)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_auth_middleware.py
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import config
import db
import session_tokens
from auth_middleware import AuthMiddleware


@pytest.fixture
def app_and_client(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/auth/status")
    def status():
        return {"state": "unauthenticated"}

    @app.get("/api/protected")
    def protected(request: Request):
        return {"user_id": request.state.user_id}

    @app.post("/api/protected-mutate")
    def protected_mutate():
        return {"ok": True}

    yield app, TestClient(app)

    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE users, sessions CASCADE")
        conn.commit()


def _make_session(user_discogs_id=42):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=user_discogs_id, discogs_username="alice")
        token = session_tokens.new_session_token()
        db.create_session(
            conn,
            session_tokens.hash_token(token),
            user["id"],
            datetime.utcnow() + timedelta(days=1),
        )
        conn.commit()
    return token, user["id"]


def test_health_is_allowlisted(app_and_client):
    _app, client = app_and_client
    assert client.get("/api/health").status_code == 200


def test_status_is_allowlisted(app_and_client):
    _app, client = app_and_client
    assert client.get("/api/auth/status").status_code == 200


def test_protected_blocked_without_session(app_and_client):
    _app, client = app_and_client
    assert client.get("/api/protected").status_code == 401


def test_protected_allowed_with_valid_session_and_sets_user_id(app_and_client):
    _app, client = app_and_client
    token, user_id = _make_session()
    client.cookies.set(config.COOKIE_NAME, token)
    r = client.get("/api/protected")
    assert r.status_code == 200
    assert r.json()["user_id"] == user_id


def test_mutating_request_requires_x_requested_with_header(app_and_client):
    _app, client = app_and_client
    token, _ = _make_session()
    client.cookies.set(config.COOKIE_NAME, token)
    r = client.post("/api/protected-mutate")
    assert r.status_code == 403


def test_idle_expired_session_is_rejected_and_deleted(app_and_client, monkeypatch):
    _app, client = app_and_client
    monkeypatch.setattr(config, "SESSION_IDLE_SECONDS", 1)
    token, _ = _make_session()
    token_hash = session_tokens.hash_token(token)
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s",
            [datetime.utcnow() - timedelta(seconds=10), token_hash],
        )
        conn.commit()

    client.cookies.set(config.COOKIE_NAME, token)
    r = client.get("/api/protected")
    assert r.status_code == 401

    with db.get_admin_pool().connection() as conn:
        assert db.get_session_by_token_hash(conn, token_hash) is None


def test_valid_request_touches_last_seen_at(app_and_client):
    _app, client = app_and_client
    token, _ = _make_session()
    token_hash = session_tokens.hash_token(token)
    with db.get_admin_pool().connection() as conn:
        before = db.get_session_by_token_hash(conn, token_hash)["last_seen_at"]

    client.cookies.set(config.COOKIE_NAME, token)
    client.get("/api/protected")

    with db.get_admin_pool().connection() as conn:
        after = db.get_session_by_token_hash(conn, token_hash)["last_seen_at"]
    assert after >= before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_auth_middleware.py -v`
Expected: FAIL — current `auth_middleware.py` imports `auth_core` (now deleted) and `db.get_connection`/`db.owner_exists` (don't exist), so this fails at collection with an `ImportError`/`AttributeError`.

- [ ] **Step 3: Rewrite `backend/auth_middleware.py`**

```python
# backend/auth_middleware.py
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
```

Note the removal of the old `if not db.owner_exists(conn): return 401 "Setup required"` branch entirely — there's no "owner" concept anymore, and every path that needs gating (new-account creation) is already handled by the invite-redemption check in the router itself, not by the middleware.

**Amendment, caught during this task's own code-quality review:** the original version of this step called `db.get_identity_pool()` directly inside `dispatch`, an `async def` method. Starlette's `BaseHTTPMiddleware.dispatch` is not automatically run in a threadpool the way FastAPI wraps synchronous route handlers — it executes directly on the event loop. `db.get_identity_pool()` is a synchronous `psycopg_pool.ConnectionPool`, so every blocking network round trip to Postgres inside the original `dispatch` body (session lookup, then an UPDATE-or-DELETE, then a COMMIT) stalled the *entire worker's event loop* for its duration on every single authenticated request — including any concurrently-running async SSE generators (`crawl_stream()`, `discover_stream()`) on that same worker. Fixed above by extracting the blocking logic into a plain synchronous `_resolve_session()` function and running it via `starlette.concurrency.run_in_threadpool`, so it executes off the event loop. This matters more than a typical micro-optimization given the architecture spec's own stated trajectory toward a "large/public" multi-tenant service — the same "sync DB call from async code" mistake would only get more expensive if copied into future routers built against `db.get_app_pool()`/`user_scope()`, so it's worth fixing at the root here rather than deferring.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_auth_middleware.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add auth_middleware.py tests/test_auth_middleware.py
git commit -m "feat: rewire AuthMiddleware to app_identity-scoped session resolution"
```

---

### Task 9: Delete `reset_owner.py`, clean up `main.py`'s auth-specific startup code

**Files:**
- Delete: `backend/reset_owner.py`
- Delete: `backend/tests/test_reset_owner.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Delete the owner-reset CLI and its test**

```bash
cd backend && git rm reset_owner.py tests/test_reset_owner.py
```

There's no "owner" concept to reset anymore. The equivalent failure mode now — losing access to your Discogs account — is outside this app's control; no recovery tool can fix that, so none is built.

- [ ] **Step 2: Remove the bootstrap-token startup block from `main.py`**

In `backend/main.py`, change the import line:

```python
from config import ensure_dirs, CRAWLERS_DIR, load_config, BOOTSTRAP_TOKEN_FILE
```

to:

```python
from config import ensure_dirs, CRAWLERS_DIR, load_config
```

And in `startup()`, remove exactly this block:

```python
    if not owner_exists(conn):
        token = secrets.token_urlsafe(24)
        BOOTSTRAP_TOKEN_FILE.write_text(token)
        log.info("No owner configured. Bootstrap token: %s", token)
        log.info("Complete first-run setup at the app URL using this token.")
```

Also remove `owner_exists` from the `from db import get_connection, init_db, register_crawler, owner_exists` line (leave `get_connection, init_db, register_crawler` as-is — those remain broken for the unrelated, out-of-scope reason already documented in this plan's header, and fixing them is the crawl-queue plan's job, not this one's).

If `import secrets` at the top of `main.py` is now unused (check with `grep -n "secrets\." main.py` — it should show zero remaining uses after the block above is removed), remove that import line too.

- [ ] **Step 3: Confirm the removed pieces aren't referenced elsewhere**

Run: `cd backend && grep -rn "BOOTSTRAP_TOKEN_FILE\|reset_owner\|owner_exists" --include='*.py' .`
Expected: no output.

- [ ] **Step 4: Note the expected remaining breakage**

Run: `cd backend && python -c "import main"`
Expected: still fails, with an error naming `get_connection`, `init_db`, or `register_crawler` — not `BOOTSTRAP_TOKEN_FILE`, `owner_exists`, or `auth_core`. This confirms this task's edit is complete and correctly scoped: the *remaining* breakage is the crawl-queue plan's, not something this step failed to fix.

- [ ] **Step 5: Commit**

```bash
cd backend && git add main.py
git commit -m "chore: delete owner-reset CLI, remove bootstrap-token startup wiring"
```

---

### Task 10: Frontend API client updates

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Update `AuthState` and add a discriminated `user` field**

In `frontend/src/api/types.ts`, change:

```typescript
export type AuthState = 'setup_required' | 'unauthenticated' | 'authenticated'
```

to:

```typescript
export type AuthStatus =
  | { state: 'unauthenticated' }
  | { state: 'authenticated'; user: { discogs_username: string } }
```

(Renamed from `AuthState` to `AuthStatus` since it's no longer a bare string union — every call site that destructures `.state` off the old bare-string type needs updating in the same pass; `grep -rn "AuthState" frontend/src` to find them all before starting Step 2.)

- [ ] **Step 2: Replace the auth-related functions in `client.ts`**

Remove `setupOwner`, `verifySetup`, `login`, `changePassword` (and `resetTotp`/`regenerateRecoveryCodes` if present — check for them, they weren't shown in the excerpt read during planning but likely exist alongside `changePassword` following the same pattern). Replace `getAuthState` and add the two new functions:

```typescript
export async function getAuthStatus(): Promise<AuthStatus> {
  const r = await apiFetch('/auth/status')
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function discogsLoginUrl(): string {
  return `${BASE}/auth/discogs/start`
}

export async function redeemInvite(signupToken: string, inviteCode: string): Promise<void> {
  const r = await apiFetch('/auth/redeem-invite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signup_token: signupToken, invite_code: inviteCode }),
  })
  if (!r.ok) throw new Error(await r.text())
}
```

`logout` stays exactly as it is — its shape didn't change.

- [ ] **Step 3: Run the frontend test suite for regressions**

Run: `cd frontend && npm test`
Expected: failures in any test file that references the now-removed functions/types (`AuthState`, `login`, `setupOwner`, `verifySetup`, `changePassword`) — this is expected mid-task breakage; Tasks 11-13 fix the files that cause it. Note which files fail here so you're not surprised later.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/api/types.ts src/api/client.ts
git commit -m "feat: replace password/TOTP auth API client with Discogs OAuth"
```

---

### Task 11: LoginScreen rewrite, delete SetupWizard

**Files:**
- Modify: `frontend/src/views/LoginScreen.tsx`
- Delete: `frontend/src/views/SetupWizard.tsx`
- Test: `frontend/src/test/loginScreen.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/test/loginScreen.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import LoginScreen from '../views/LoginScreen'

vi.mock('../api/client', () => ({
  discogsLoginUrl: () => '/api/auth/discogs/start',
}))

describe('LoginScreen', () => {
  it('renders a Continue with Discogs link pointing at the OAuth start endpoint', () => {
    render(<LoginScreen />)
    const link = screen.getByRole('link', { name: /continue with discogs/i })
    expect(link).toHaveAttribute('href', '/api/auth/discogs/start')
  })

  it('does not render a password field', () => {
    render(<LoginScreen />)
    expect(screen.queryByPlaceholderText(/password/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/loginScreen.test.tsx`
Expected: FAIL — the current component takes an `onAuthenticated` prop, renders a password form, and has no link with this accessible name.

- [ ] **Step 3: Rewrite `LoginScreen.tsx`**

```typescript
// frontend/src/views/LoginScreen.tsx
import { discogsLoginUrl } from '../api/client'

export default function LoginScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded shadow w-80 space-y-4 text-center">
        <h1 className="text-xl font-semibold">Sign In</h1>
        <a
          href={discogsLoginUrl()}
          className="block w-full bg-blue-600 text-white rounded py-2 hover:bg-blue-700"
        >
          Continue with Discogs
        </a>
      </div>
    </div>
  )
}
```

This is a real page navigation (`<a href>`, not a `fetch`-driven `onClick`) — `/api/auth/discogs/start` issues a server-side redirect to Discogs, which a `fetch` call cannot follow the browser through.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/loginScreen.test.tsx`
Expected: PASS

- [ ] **Step 5: Delete `SetupWizard.tsx`**

```bash
cd frontend && git rm src/views/SetupWizard.tsx
```

No first-run wizard concept survives — every signup goes through the same invite-gated OAuth callback path, whether it's the very first account or the thousandth.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/views/LoginScreen.tsx src/test/loginScreen.test.tsx
git commit -m "feat: replace password/TOTP login form with Discogs OAuth link, delete first-run wizard"
```

---

### Task 12: Invite-code screen and App.tsx wiring

**Files:**
- Create: `frontend/src/views/InviteCodeScreen.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/inviteCodeScreen.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/test/inviteCodeScreen.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import InviteCodeScreen from '../views/InviteCodeScreen'

const { redeemInvite } = vi.hoisted(() => ({
  redeemInvite: vi.fn(),
}))

vi.mock('../api/client', () => ({ redeemInvite }))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('InviteCodeScreen', () => {
  it('submits the entered code with the given signup token and calls onRedeemed on success', async () => {
    redeemInvite.mockResolvedValue(undefined)
    const onRedeemed = vi.fn()
    render(<InviteCodeScreen signupToken="signup-token-1" onRedeemed={onRedeemed} />)

    fireEvent.change(screen.getByPlaceholderText(/invite code/i), { target: { value: 'INVITE123' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await waitFor(() => expect(redeemInvite).toHaveBeenCalledWith('signup-token-1', 'INVITE123'))
    await waitFor(() => expect(onRedeemed).toHaveBeenCalled())
  })

  it('shows an error and does not call onRedeemed when redemption fails', async () => {
    redeemInvite.mockRejectedValue(new Error('Invalid or already-used invite code'))
    const onRedeemed = vi.fn()
    render(<InviteCodeScreen signupToken="signup-token-1" onRedeemed={onRedeemed} />)

    fireEvent.change(screen.getByPlaceholderText(/invite code/i), { target: { value: 'BAD' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await waitFor(() => expect(screen.getByText(/invalid or already-used/i)).toBeInTheDocument())
    expect(onRedeemed).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/inviteCodeScreen.test.tsx`
Expected: FAIL with a module-not-found error for `../views/InviteCodeScreen`.

- [ ] **Step 3: Write `InviteCodeScreen.tsx`**

```typescript
// frontend/src/views/InviteCodeScreen.tsx
import { useState } from 'react'
import { redeemInvite } from '../api/client'

export default function InviteCodeScreen({
  signupToken,
  onRedeemed,
}: {
  signupToken: string
  onRedeemed: () => void
}) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await redeemInvite(signupToken, code)
      onRedeemed()
    } catch (err: any) {
      setError(err.message || 'Invalid or already-used invite code')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <form onSubmit={submit} className="bg-white p-8 rounded shadow w-80 space-y-4">
        <h1 className="text-xl font-semibold">Enter your invite code</h1>
        <input
          type="text" placeholder="Invite code" value={code}
          onChange={e => setCode(e.target.value)}
          className="w-full border rounded px-3 py-2" autoFocus
        />
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full bg-blue-600 text-white rounded py-2 disabled:opacity-50">
          {busy ? 'Checking…' : 'Continue'}
        </button>
      </form>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/inviteCodeScreen.test.tsx`
Expected: PASS

- [ ] **Step 5: Wire it into `App.tsx`**

Find the existing auth-bootstrap `useEffect` (around the `getAuthState().then(setAuthState)` line) and the render branch that currently does:

```typescript
  if (authState === 'setup_required') {
    return <SetupWizard onComplete={() => setAuthState('authenticated')} />
  }
  if (authState === 'unauthenticated') {
    return <LoginScreen onAuthenticated={() => setAuthState('authenticated')} />
  }
```

Replace the whole auth-bootstrap section with:

```typescript
  const [signupToken, setSignupToken] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('signup_pending')
  })

  // ... inside the existing bootstrap useEffect, replace getAuthState with getAuthStatus:
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthState({ state: 'unauthenticated' }))
    getAuthStatus().then(setAuthState).catch(() => setAuthState({ state: 'unauthenticated' }))
  }, [])
```

and the render branch:

```typescript
  if (authState === null) {
    return null // or existing loading indicator, unchanged
  }
  if (authState.state !== 'authenticated' && signupToken) {
    return (
      <InviteCodeScreen
        signupToken={signupToken}
        onRedeemed={() => {
          setSignupToken(null)
          window.history.replaceState({}, '', window.location.pathname)
          getAuthStatus().then(setAuthState)
        }}
      />
    )
  }
  if (authState.state === 'unauthenticated') {
    return <LoginScreen />
  }
```

**Amendment, caught during this task's own spec-compliance review:** the original version of this branch showed the invite screen whenever `signupToken` was truthy, with no check against `authState.state`. That's a real UX gap, not just a hypothetical: an already-authenticated returning user who revisits a stale bookmarked or back-buttoned `?signup_pending=...` URL would get stuck on the invite-code form with no escape hatch back into the app — submitting it just fails (the `pending_signups` row is long since consumed or expired), and there was no link back to the main app from that screen. Fixed above by gating the invite-screen branch on `authState.state !== 'authenticated'` — an already-authenticated user with a stale token now falls straight through to the main app instead. This doesn't affect the normal signup flow at all: by the time `onRedeemed`'s `getAuthStatus().then(setAuthState)` resolves to `'authenticated'`, `signupToken` has already been cleared by the earlier synchronous `setSignupToken(null)` call in the same handler, so the two conditions are never simultaneously true during a real signup.

Update the `authState` type declaration from `useState<AuthState | null>(null)` to `useState<AuthStatus | null>(null)`, and update the import line to bring in `AuthStatus`/`getAuthStatus`/`InviteCodeScreen` and drop `AuthState`/`getAuthState`/`SetupWizard`. Any later code in `App.tsx` that reads `authState === 'authenticated'` needs to become `authState.state === 'authenticated'` (grep for `authState ===` and `authState !==` to find every call site — there is at least one more, in the effects gating crawl-stream setup, per the earlier `if (authState !== 'authenticated') return` lines found during planning).

- [ ] **Step 6: Run the frontend test suite for regressions**

Run: `cd frontend && npm test`
Expected: PASS for `loginScreen.test.tsx`/`inviteCodeScreen.test.tsx`; any remaining failures should now only be in `account.test.tsx`/`accountNav.test.tsx` (Task 13 fixes those) — confirm nothing else broke.

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/views/InviteCodeScreen.tsx src/App.tsx src/test/inviteCodeScreen.test.tsx
git commit -m "feat: add invite-code screen, wire signup_pending URL param into App bootstrap"
```

---

### Task 13: Trim the Account view

**Files:**
- Modify: `frontend/src/views/Account.tsx`
- Modify: `frontend/src/test/account.test.tsx`

- [ ] **Step 1: Update the failing parts of the existing test**

`account.test.tsx` currently mocks `changePassword` and tests password-change behavior — remove that test and its mock entry:

```typescript
// frontend/src/test/account.test.tsx — remove changePassword from the vi.hoisted mock object
// and remove any `it(...)` blocks that reference it or the password fields.
// Keep every avatar-related test (uploadAvatar/deleteAvatar) untouched — this task
// doesn't touch avatar handling at all.
```

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: FAIL — `Account.tsx` still renders the password/TOTP form the trimmed test no longer expects (or the test file itself won't compile if you removed a mock entry the component still imports — either failure mode is fine to see here, confirm it's one of these two, not something unrelated).

- [ ] **Step 2: Trim `Account.tsx`**

Remove the entire "Account & Security" password-change `<section>` (the `<table>` with current-password/new-password/authenticator-code rows) and its backing state (`currentPassword`, `newPassword`, `authCode`, `passwordMessage`, `submitPasswordChange`) and the `changePassword` import. Replace that section with just a logout control:

```typescript
      {/* Account & Security */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-1 text-left">Account & Security</h2>
        <p className="text-sm text-gray-500 mb-4 text-left">
          Log out of this session.
        </p>
        <button
          onClick={() => logout().then(() => window.location.reload())}
          className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs font-medium transition-colors"
        >
          Log out
        </button>
      </section>
```

The `logout` import from `../api/client` stays — its signature is unchanged.

- [ ] **Step 3: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/account.test.tsx`
Expected: PASS

- [ ] **Step 4: Run the full frontend suite for regressions**

Run: `cd frontend && npm test`
Expected: all green. This closes out every frontend file this plan touches — if anything outside this plan's file list is still failing, stop and investigate before committing (it would mean this plan missed a call site).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/views/Account.tsx src/test/account.test.tsx
git commit -m "feat: remove password/TOTP UI from Account view, keep logout only"
```

---

## Plan self-review

**Spec coverage:** every section of the design spec (`2026-07-26-discogs-oauth-auth-design.md`) maps to a task — data model (Tasks 3-4), config (Task 5), the OAuth client (Task 6), endpoints & flow (Task 7), AuthMiddleware (Task 8), legacy removal (Tasks 2, 9, 11), frontend (Tasks 10-13). The spec's TTL enforcement detail (10 minutes for `oauth_request_state`, 15 for `pending_signups`, checked at read time via `DELETE ... RETURNING`) is implemented exactly as specified in Task 3, not simplified away.

**Placeholder scan:** no TBD/TODO/"add error handling" phrasing found. Task 6 explicitly flags and handles the one genuine unresolved unknown (authlib's exact API) with a concrete verification step rather than either guessing silently or leaving a placeholder.

**Type/signature consistency:** `_create_session_for_user(conn, request, response, user_id)` in Task 7 takes an explicit `conn` and does not commit internally — checked against both call sites (the existing-user branch of `discogs_callback`, and `redeem_invite`) to confirm neither one double-commits or leaves an uncommitted session on error. `db.create_session`/`get_session_by_token_hash`/`touch_session`/`delete_session` (Task 4) match the calls made against them in Tasks 7-8 exactly (same argument order, same names). `AuthStatus`'s shape (Task 10) matches what `auth_status` actually returns (Task 7) and what `App.tsx`'s render branches check (Task 12).

**Cross-task ordering hazard, addressed directly in-task rather than silently assumed:** Task 7's test fixture would naturally want to import `main.app`, but `main.py` doesn't fully import until far outside this plan's scope (crawl-queue plan). Task 7 calls this out explicitly and specifies testing against a minimal FastAPI app mounting only the auth router instead — this was caught during planning, not left for the implementer to discover the hard way.
