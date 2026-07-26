# Multi-Tenant Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `backend/db.py`'s SQLite/single-owner schema with a Postgres schema that separates globally-shared catalog/listing data from per-user data enforced via Row-Level Security, and provide a one-time script to migrate the maintainer's existing SQLite data into it.

**Architecture:** Two Postgres roles split the trust boundary that plain RLS-by-`user_id` can't handle on its own: `app_identity` (has `BYPASSRLS`, narrow grants on `users`/`invites`/`sessions` only) resolves *who is making this request* before any user context exists — this is what login and account creation need, since you cannot scope a query by `user_id` before you know it. `app_user` (ordinary role, RLS-scoped) handles everything once identity is resolved — currently just `library_items`, plus read access to the global tables. Global tables (`catalog`, `listings`, `crawlers`, `stock_items`, `stock_item_judgments`) carry over from today's schema unchanged in shape and are not RLS-protected at all — they were already implicitly global.

This plan does **not** keep the existing SQLite-backed app running afterward. `main.py` and the routers will not import successfully against the new `db.py` until the auth (OAuth) and crawl-queue plans land and rewire them. That's intentional — per the earlier decision to retire single-owner mode as a hard cutover rather than an incrementally-compatible migration, this plan establishes the new foundation in isolation, proven by its own test suite. Wiring `main.py`/routers back up happens as each follow-on plan rebuilds its area against this schema.

**Tech Stack:** PostgreSQL 14+, `psycopg[binary]` 3.x (no ORM — raw SQL, matching this codebase's existing plain-driver style in `discogs.py`/`plex.py`), `psycopg-pool`. Tests require a real local Postgres reachable via `TEST_DATABASE_URL` (a `docker run postgres` one-liner is given in Task 1) — RLS behavior cannot be faithfully tested against SQLite, so the existing in-memory-SQLite test fixture pattern does not carry over for this module.

**Out of scope for this plan** (deferred to the plan that actually uses each): rewriting `get_releases`'s search/pagination logic, Plex-matching helpers, stock-item-judgment queries, and settings storage — those move to the OAuth/session, crawl-queue, and Plex-reachability plans respectively, since that's where their new shape actually gets exercised. The `crawl_queue` table itself is also deferred to the crawl-queue plan, per the architecture spec's own decomposition.

---

### Task 1: Postgres dependency, config, and connection pools

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/config.py`
- Create: `backend/db.py` (full rewrite — delete existing SQLite content)
- Test: `backend/tests/test_db_pools.py`

- [ ] **Step 1: Add Postgres dependencies**

In `backend/pyproject.toml`, replace nothing (SQLite is stdlib, no dependency to remove) and add to `dependencies`:

```toml
    "psycopg[binary]>=3.1,<4.0",
    "psycopg-pool>=3.2,<4.0",
```

Add to `[project.optional-dependencies].dev`:

```toml
    "pytest-dotenv>=0.5",
```

- [ ] **Step 2: Start a local Postgres for dev/test**

```bash
docker run -d --name discogs-browser-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
```

Set for local dev/test (e.g. in `backend/.env`, loaded by `pytest-dotenv`):

```
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test
IDENTITY_DB_PASSWORD=identity_dev_password
APP_DB_PASSWORD=app_dev_password
```

```bash
docker exec discogs-browser-pg psql -U postgres -c "CREATE DATABASE discogs_browser_test"
```

- [ ] **Step 3: Add Postgres config to `backend/config.py`**

Add near the existing `DB_FILE` line (do not remove `DB_FILE` — the migration script in Task 7 still reads the old SQLite file by that path):

```python
from urllib.parse import urlsplit, urlunsplit


def _with_userinfo(url: str, username: str, password: str) -> str:
    """Swap the userinfo (user:pass) on a DSN without touching host/port/path,
    so this works for any real DATABASE_URL, not just the dev-default one."""
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, f"{username}:{password}@{host}", parts.path, parts.query, parts.fragment))


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/discogs_browser")
IDENTITY_DATABASE_URL = os.environ.get(
    "IDENTITY_DATABASE_URL",
    _with_userinfo(DATABASE_URL, "app_identity", os.environ.get("IDENTITY_DB_PASSWORD", "")),
)
APP_DATABASE_URL = os.environ.get(
    "APP_DATABASE_URL",
    _with_userinfo(DATABASE_URL, "app_user", os.environ.get("APP_DB_PASSWORD", "")),
)
```

(Earlier draft of this step used `DATABASE_URL.replace("postgres:postgres@", ...)`, a literal string match that silently no-ops — falling back to the raw admin DSN — for any real `DATABASE_URL` whose userinfo isn't exactly `postgres:postgres`. Since the whole point of `app_identity`/`app_user` is to be non-superuser, RLS-bounded roles, a silent fallback to the admin connection would defeat that boundary the moment Task 3's RLS policies land. Caught in Task 1's code-quality review; fixed here with a proper URL-component swap instead of string matching.)

- [ ] **Step 4: Write the failing test for pool construction**

```python
# backend/tests/test_db_pools.py
import os

import pytest

import db


@pytest.fixture(autouse=True)
def _test_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(db.config, "DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    db._admin_pool = None
    db._identity_pool = None
    db._app_pool = None
    yield
    for pool in (db._admin_pool, db._identity_pool, db._app_pool):
        if pool is not None:
            pool.close()


def test_admin_pool_connects_and_runs_a_query():
    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd backend && pytest tests/test_db_pools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db'` or `AttributeError` — `db.py` doesn't exist yet in its new form.

- [ ] **Step 6: Write `backend/db.py`'s connection layer**

```python
# backend/db.py
from contextlib import contextmanager
from typing import Optional

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

_admin_pool: Optional[ConnectionPool] = None
_identity_pool: Optional[ConnectionPool] = None
_app_pool: Optional[ConnectionPool] = None


def get_admin_pool() -> ConnectionPool:
    global _admin_pool
    if _admin_pool is None:
        _admin_pool = ConnectionPool(
            config.DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}
        )
    return _admin_pool


def get_identity_pool() -> ConnectionPool:
    global _identity_pool
    if _identity_pool is None:
        _identity_pool = ConnectionPool(
            config.IDENTITY_DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}
        )
    return _identity_pool


def get_app_pool() -> ConnectionPool:
    global _app_pool
    if _app_pool is None:
        _app_pool = ConnectionPool(
            config.APP_DATABASE_URL, min_size=2, max_size=10, kwargs={"row_factory": dict_row}
        )
    return _app_pool


@contextmanager
def user_scope(user_id: int):
    """A connection from the RLS-scoped app_user role, with app.user_id set
    for the duration of one transaction. Every query run through this
    connection against library_items sees only that user's rows."""
    with get_app_pool().connection() as conn:
        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
        yield conn
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && pytest tests/test_db_pools.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd backend && git add pyproject.toml config.py db.py tests/test_db_pools.py
git commit -m "feat: add Postgres connection pools for multi-tenant data model"
```

---

### Task 2: Global schema (catalog, listings, crawlers, stock_items, stock_item_judgments)

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_global_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_global_schema.py
import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, listings, crawlers, stock_items, stock_item_judgments CASCADE")
        conn.commit()


def test_catalog_table_exists_with_expected_columns(admin_conn):
    cols = {
        r["column_name"]
        for r in admin_conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'catalog'"
        ).fetchall()
    }
    assert cols == {
        "discogs_id", "artist", "title", "year", "label", "format",
        "discogs_price", "barcode", "cover_image_url", "discogs_url", "last_synced",
    }


def test_listings_unique_on_release_and_crawler(admin_conn):
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO listings (release_id, crawler_id, url) VALUES ('d1', %s, 'http://x')",
        [crawler_id],
    )
    with pytest.raises(Exception):
        admin_conn.execute(
            "INSERT INTO listings (release_id, crawler_id, url) VALUES ('d1', %s, 'http://y')",
            [crawler_id],
        )
    admin_conn.rollback()
```

Add a `pg_test_db` fixture (used by every test in this plan) to `backend/tests/conftest.py`:

```python
# backend/tests/conftest.py (add, do not remove the existing sqlite `conn` fixture)
import os

import pytest


@pytest.fixture
def pg_test_db(monkeypatch):
    import db as db_module

    monkeypatch.setattr(db_module.config, "DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(
        db_module.config, "IDENTITY_DATABASE_URL", os.environ["TEST_DATABASE_URL"]
    )
    monkeypatch.setattr(db_module.config, "APP_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    db_module._admin_pool = None
    db_module._identity_pool = None
    db_module._app_pool = None
    yield
    for pool in (db_module._admin_pool, db_module._identity_pool, db_module._app_pool):
        if pool is not None:
            pool.close()
```

Note: this fixture points *all three* roles at the same admin DSN for Tasks 2-3's schema tests, since role-specific DSNs and RLS enforcement are only exercised starting Task 4's isolation test — keeping earlier tests focused on schema shape, not yet on role separation.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_global_schema.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'init_global_schema'`

- [ ] **Step 3: Add the global schema DDL and `init_global_schema` to `db.py`**

```python
# backend/db.py (append)
GLOBAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    discogs_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    label TEXT,
    format TEXT,
    discogs_price TEXT,
    barcode TEXT,
    cover_image_url TEXT,
    discogs_url TEXT,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawlers (
    id SERIAL PRIMARY KEY,
    site_name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
    crawler_type TEXT NOT NULL DEFAULT 'release',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_run TIMESTAMP
);

CREATE TABLE IF NOT EXISTS listings (
    id SERIAL PRIMARY KEY,
    release_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    url TEXT NOT NULL,
    price DOUBLE PRECISION,
    shipping DOUBLE PRECISION,
    currency TEXT,
    condition TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(release_id, crawler_id)
);

CREATE TABLE IF NOT EXISTS stock_items (
    id SERIAL PRIMARY KEY,
    crawler_id INTEGER NOT NULL REFERENCES crawlers(id),
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT,
    price DOUBLE PRECISION,
    currency TEXT,
    url TEXT NOT NULL,
    cover_image_url TEXT,
    item_key TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_item_judgments (
    item_key TEXT PRIMARY KEY,
    recommended BOOLEAN NOT NULL,
    reason TEXT,
    judged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_global_schema():
    with get_admin_pool().connection() as conn:
        conn.execute(GLOBAL_SCHEMA)
        conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_global_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add db.py tests/conftest.py tests/test_global_schema.py
git commit -m "feat: add global catalog/listings/crawlers Postgres schema"
```

---

### Task 3: Per-user schema, roles, and RLS

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_tenant_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_tenant_schema.py
import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, sessions, library_items, invites CASCADE")
        conn.commit()


def test_users_table_has_rls_enabled(admin_conn):
    row = admin_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'users'"
    ).fetchone()
    assert row["relrowsecurity"] is True
    assert row["relforcerowsecurity"] is True


def test_library_items_table_has_rls_enabled(admin_conn):
    row = admin_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'library_items'"
    ).fetchone()
    assert row["relrowsecurity"] is True
    assert row["relforcerowsecurity"] is True


def test_app_identity_role_has_bypassrls(admin_conn):
    row = admin_conn.execute(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_identity'"
    ).fetchone()
    assert row["rolbypassrls"] is True


def test_app_user_role_does_not_have_bypassrls(admin_conn):
    row = admin_conn.execute(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_user'"
    ).fetchone()
    assert row["rolbypassrls"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_tenant_schema.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'init_tenant_schema'`

- [ ] **Step 3: Add the per-user schema, roles, and RLS policies to `db.py`**

```python
# backend/db.py (append)
from psycopg import sql

TENANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    discogs_user_id INTEGER UNIQUE NOT NULL,
    discogs_username TEXT NOT NULL,
    discogs_oauth_token_encrypted BYTEA,
    discogs_oauth_secret_encrypted BYTEA,
    plex_base_url TEXT,
    plex_token TEXT,
    plex_match_threshold INTEGER NOT NULL DEFAULT 90,
    invited_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS library_items (
    user_id INTEGER NOT NULL REFERENCES users(id),
    discogs_id TEXT NOT NULL REFERENCES catalog(discogs_id),
    in_collection BOOLEAN NOT NULL DEFAULT FALSE,
    in_wishlist BOOLEAN NOT NULL DEFAULT FALSE,
    plex_url TEXT,
    plex_matched_at TIMESTAMP,
    last_synced TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, discogs_id)
);

CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER REFERENCES users(id),
    redeemed_by INTEGER REFERENCES users(id),
    redeemed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE library_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE library_items FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS users_isolation ON users;
CREATE POLICY users_isolation ON users
    USING (id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS sessions_isolation ON sessions;
CREATE POLICY sessions_isolation ON sessions
    USING (user_id = current_setting('app.user_id', true)::int);

DROP POLICY IF EXISTS library_items_isolation ON library_items;
CREATE POLICY library_items_isolation ON library_items
    USING (user_id = current_setting('app.user_id', true)::int);
"""


def _ensure_role(conn, role_name: str, password: str, bypass_rls: bool):
    exists = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", [role_name]
    ).fetchone()
    if not exists:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(password)
            )
        )
    conn.execute(
        sql.SQL("ALTER ROLE {} {}").format(
            sql.Identifier(role_name),
            sql.SQL("BYPASSRLS" if bypass_rls else "NOBYPASSRLS"),
        )
    )


def init_tenant_schema():
    with get_admin_pool().connection() as conn:
        conn.execute(TENANT_SCHEMA)
        _ensure_role(conn, "app_identity", config.IDENTITY_DB_PASSWORD, bypass_rls=True)
        _ensure_role(conn, "app_user", config.APP_DB_PASSWORD, bypass_rls=False)

        conn.execute("GRANT SELECT, INSERT, UPDATE ON users TO app_identity")
        conn.execute("GRANT SELECT, UPDATE ON invites TO app_identity")
        conn.execute("GRANT SELECT, INSERT, DELETE ON sessions TO app_identity")
        conn.execute("GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO app_identity")

        conn.execute(
            "GRANT SELECT ON catalog, listings, crawlers, stock_items, stock_item_judgments TO app_user"
        )
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON library_items TO app_user")
        conn.commit()
```

Why two roles instead of one RLS-scoped role for everything: `users`/`sessions` must be queried *before* a `user_id` is known — that's what login and account creation are for (looking up a session by its token, or a user by their `discogs_user_id`, to find out who's asking). RLS-by-`user_id` can't gate a query that doesn't know the `user_id` yet without also making legitimate login lookups return nothing. `app_identity` exists to make exactly those pre-context lookups, narrowly (it can't touch `library_items` at all — no grant exists for it there, so bypassing RLS on `users`/`sessions` doesn't expose anyone's collection). `invites` deliberately has no RLS policy at all (see the architecture spec's Data model section) since redemption is also a pre-context operation.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_tenant_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add db.py tests/test_tenant_schema.py
git commit -m "feat: add per-user Postgres schema with Row-Level Security"
```

---

### Task 4: RLS isolation proof

**Files:**
- Modify: `backend/config.py` (point `IDENTITY_DATABASE_URL`/`APP_DATABASE_URL` at the real roles for this test)
- Test: `backend/tests/test_rls_isolation.py`

This is the test the whole two-role/RLS design exists to satisfy: a query against `library_items` under one user's scope must never return another user's rows, even if a future caller forgets a `WHERE` clause.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rls_isolation.py
import os

import pytest

import db


@pytest.fixture
def two_users_one_shared_release(pg_test_db, monkeypatch):
    db.init_global_schema()
    db.init_tenant_schema()
    # Point the app-role pool at the real app_user role for this test only —
    # earlier tasks' fixtures use the admin DSN for all three pools.
    monkeypatch.setattr(
        db.config,
        "APP_DATABASE_URL",
        os.environ["TEST_DATABASE_URL"].replace(
            "postgres:postgres@", f"app_user:{os.environ['APP_DB_PASSWORD']}@"
        ),
    )
    db._app_pool = None

    with db.get_admin_pool().connection() as admin:
        alice = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (1, 'alice') RETURNING id",
        ).fetchone()
        bob = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (2, 'bob') RETURNING id",
        ).fetchone()
        admin.execute("INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'A', 'T')")
        admin.execute(
            "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'd1', TRUE)",
            [alice["id"]],
        )
        admin.commit()

    yield alice["id"], bob["id"]

    with db.get_admin_pool().connection() as admin:
        admin.execute("TRUNCATE users, catalog, library_items CASCADE")
        admin.commit()


def test_user_sees_only_their_own_library_items(two_users_one_shared_release):
    alice_id, _bob_id = two_users_one_shared_release
    with db.user_scope(alice_id) as conn:
        rows = conn.execute("SELECT * FROM library_items").fetchall()
    assert len(rows) == 1
    assert rows[0]["discogs_id"] == "d1"


def test_other_user_sees_nothing_for_a_release_they_dont_own(two_users_one_shared_release):
    _alice_id, bob_id = two_users_one_shared_release
    with db.user_scope(bob_id) as conn:
        rows = conn.execute("SELECT * FROM library_items").fetchall()
    assert rows == []


def test_query_with_no_where_clause_still_returns_only_the_scoped_users_rows(
    two_users_one_shared_release,
):
    """The property RLS exists to guarantee: a query that forgot a WHERE
    user_id = ... clause must still be isolated, not just queries that
    remembered to add one."""
    alice_id, _bob_id = two_users_one_shared_release
    with db.user_scope(alice_id) as conn:
        all_rows = conn.execute("SELECT discogs_id FROM library_items").fetchall()
    assert [r["discogs_id"] for r in all_rows] == ["d1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_rls_isolation.py -v`
Expected: FAIL — most likely a connection/auth error, since `APP_DB_PASSWORD` may not yet be set consistently between the role-creation call in Task 3 and this test's connection attempt, or the role's password differs from what Task 1's `.env` declares. Confirm the specific failure before moving on — do not skip to Step 3 without seeing it fail for the *expected* reason (auth/connection), not an unrelated error.

- [ ] **Step 3: Fix any DSN/password mismatch and rerun**

If the failure is a password mismatch, ensure `backend/.env`'s `APP_DB_PASSWORD` matches what `_ensure_role` wrote in Task 3 for this test database (re-run `db.init_tenant_schema()` against the test DB if the role was created with a stale password from an earlier test run — roles persist across test runs since `TRUNCATE` doesn't drop them).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_rls_isolation.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 5: Commit**

```bash
cd backend && git add tests/test_rls_isolation.py
git commit -m "test: prove library_items RLS isolation holds without an explicit WHERE clause"
```

---

### Task 5: Minimal catalog/listings CRUD

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_catalog_crud.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_crud.py
import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, listings, crawlers CASCADE")
        conn.commit()


def test_upsert_catalog_release_inserts_then_updates(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["artist"] == "A"
    assert row["year"] == 1999

    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T (Reissue)", "year": 2005,
        "label": "L", "format": "LP", "discogs_price": "$15", "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["title"] == "T (Reissue)"
    assert row["year"] == 2005


def test_get_catalog_release_returns_none_when_missing(admin_conn):
    assert db.get_catalog_release(admin_conn, "does-not-exist") is None


def test_upsert_listing_inserts_then_updates(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    admin_conn.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Test', 'x')")
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test'"
    ).fetchone()["id"]
    admin_conn.commit()

    db.upsert_listing(admin_conn, "d1", crawler_id, "http://x/1", 9.99, 2.0, "USD", "Mint")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM listings WHERE release_id = 'd1' AND crawler_id = %s", [crawler_id]
    ).fetchone()
    assert row["price"] == 9.99

    db.upsert_listing(admin_conn, "d1", crawler_id, "http://x/1", 7.50, 2.0, "USD", "Near Mint")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM listings WHERE release_id = 'd1' AND crawler_id = %s", [crawler_id]
    ).fetchone()
    assert row["price"] == 7.50
    assert row["condition"] == "Near Mint"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_catalog_crud.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'upsert_catalog_release'`

- [ ] **Step 3: Implement the CRUD functions in `db.py`**

```python
# backend/db.py (append)
from typing import Optional


def upsert_catalog_release(conn, data: dict):
    conn.execute(
        """
        INSERT INTO catalog (discogs_id, artist, title, year, label, format, discogs_price,
                              barcode, cover_image_url, discogs_url, last_synced)
        VALUES (%(discogs_id)s, %(artist)s, %(title)s, %(year)s, %(label)s, %(format)s,
                %(discogs_price)s, %(barcode)s, %(cover_image_url)s, %(discogs_url)s, CURRENT_TIMESTAMP)
        ON CONFLICT (discogs_id) DO UPDATE SET
            artist = EXCLUDED.artist, title = EXCLUDED.title, year = EXCLUDED.year,
            label = EXCLUDED.label, format = EXCLUDED.format, discogs_price = EXCLUDED.discogs_price,
            barcode = EXCLUDED.barcode, cover_image_url = EXCLUDED.cover_image_url,
            discogs_url = EXCLUDED.discogs_url, last_synced = CURRENT_TIMESTAMP
        """,
        data,
    )


def get_catalog_release(conn, discogs_id: str) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM catalog WHERE discogs_id = %s", [discogs_id]
    ).fetchone()


def upsert_listing(
    conn,
    release_id: str,
    crawler_id: int,
    url: str,
    price: Optional[float],
    shipping: Optional[float],
    currency: Optional[str],
    condition: Optional[str],
):
    conn.execute(
        """
        INSERT INTO listings (release_id, crawler_id, url, price, shipping, currency, condition, last_checked)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (release_id, crawler_id) DO UPDATE SET
            url = EXCLUDED.url, price = EXCLUDED.price, shipping = EXCLUDED.shipping,
            currency = EXCLUDED.currency, condition = EXCLUDED.condition, last_checked = CURRENT_TIMESTAMP
        """,
        [release_id, crawler_id, url, price, shipping, currency, condition],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_catalog_crud.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add db.py tests/test_catalog_crud.py
git commit -m "feat: add catalog and listings CRUD helpers"
```

---

### Task 6: Minimal users/library_items CRUD

**Files:**
- Modify: `backend/db.py`
- Test: `backend/tests/test_user_crud.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_user_crud.py
import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, catalog, library_items CASCADE")
        conn.commit()


def test_create_user_then_get_by_discogs_id(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()
    assert user["discogs_username"] == "alice"

    found = db.get_user_by_discogs_id(admin_conn, 42)
    assert found["id"] == user["id"]


def test_get_user_by_discogs_id_returns_none_when_missing(admin_conn):
    assert db.get_user_by_discogs_id(admin_conn, 999) is None


def test_upsert_library_item_and_get_for_user(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_collection=True)
    admin_conn.commit()

    items = db.get_library_items_for_user(admin_conn, user["id"])
    assert len(items) == 1
    assert items[0]["discogs_id"] == "d1"
    assert items[0]["in_collection"] is True
    assert items[0]["in_wishlist"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_user_crud.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'create_user'`

- [ ] **Step 3: Implement the CRUD functions in `db.py`**

```python
# backend/db.py (append)
def create_user(conn, discogs_user_id: int, discogs_username: str, invited_by: Optional[int] = None) -> dict:
    return conn.execute(
        """
        INSERT INTO users (discogs_user_id, discogs_username, invited_by, created_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING *
        """,
        [discogs_user_id, discogs_username, invited_by],
    ).fetchone()


def get_user_by_discogs_id(conn, discogs_user_id: int) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM users WHERE discogs_user_id = %s", [discogs_user_id]
    ).fetchone()


def upsert_library_item(
    conn,
    user_id: int,
    discogs_id: str,
    in_collection: Optional[bool] = None,
    in_wishlist: Optional[bool] = None,
):
    existing = conn.execute(
        "SELECT in_collection, in_wishlist FROM library_items WHERE user_id = %s AND discogs_id = %s",
        [user_id, discogs_id],
    ).fetchone()
    resolved_collection = in_collection if in_collection is not None else (
        existing["in_collection"] if existing else False
    )
    resolved_wishlist = in_wishlist if in_wishlist is not None else (
        existing["in_wishlist"] if existing else False
    )
    conn.execute(
        """
        INSERT INTO library_items (user_id, discogs_id, in_collection, in_wishlist, last_synced)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, discogs_id) DO UPDATE SET
            in_collection = EXCLUDED.in_collection, in_wishlist = EXCLUDED.in_wishlist,
            last_synced = CURRENT_TIMESTAMP
        """,
        [user_id, discogs_id, resolved_collection, resolved_wishlist],
    )


def get_library_items_for_user(conn, user_id: int) -> list[dict]:
    return conn.execute(
        "SELECT * FROM library_items WHERE user_id = %s", [user_id]
    ).fetchall()
```

`create_user`/`get_user_by_discogs_id` are written to run over the `app_identity` connection in real request handling (login/account-creation), and are exercised here directly over the admin connection since RLS isn't the thing under test in this task — Task 4 already proved isolation; this task proves the CRUD logic itself.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_user_crud.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add db.py tests/test_user_crud.py
git commit -m "feat: add users and library_items CRUD helpers"
```

---

### Task 7: One-time SQLite → Postgres migration script

**Files:**
- Create: `backend/scripts/migrate_from_sqlite.py`
- Test: `backend/tests/test_migrate_from_sqlite.py`

This script is run once, by hand, to move the maintainer's existing single-owner SQLite data into the new schema. It is not a general import feature.

**Open verification item:** the script resolves a Discogs username to its numeric `id` via `GET https://api.discogs.com/users/{username}`, assumed to be a public, unauthenticated endpoint based on Discogs' general REST API shape — this was not independently confirmed against Discogs' current developer docs during planning. Verify this before running the script for real; a `--discogs-user-id` flag is provided as a fallback that skips the lookup entirely if the endpoint doesn't behave as expected.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_migrate_from_sqlite.py
import sqlite3

import pytest
import respx
import httpx

import db
from scripts.migrate_from_sqlite import migrate


@pytest.fixture
def sqlite_source(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE releases (
            discogs_id TEXT PRIMARY KEY, artist TEXT, title TEXT, year INTEGER,
            label TEXT, format TEXT, discogs_price TEXT, barcode TEXT,
            cover_image_url TEXT, discogs_url TEXT, in_collection INTEGER,
            in_wishlist INTEGER, plex_url TEXT, plex_matched_at TIMESTAMP,
            last_synced TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO releases VALUES ('d1', 'A', 'T', 1999, 'L', 'LP', '$10', '123', "
        "'http://x/cover.jpg', 'http://x/release/d1', 1, 0, NULL, NULL, '2026-01-01')"
    )
    conn.execute(
        "CREATE TABLE crawlers (id INTEGER PRIMARY KEY, site_name TEXT, module_path TEXT, "
        "crawler_type TEXT, enabled INTEGER, last_run TIMESTAMP)"
    )
    conn.execute("INSERT INTO crawlers VALUES (1, 'Test Site', 'crawlers.test', 'release', 1, NULL)")
    conn.execute(
        "CREATE TABLE listings (id INTEGER PRIMARY KEY, release_id TEXT, crawler_id INTEGER, "
        "url TEXT, price REAL, shipping REAL, currency TEXT, condition TEXT, last_checked TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO listings VALUES (1, 'd1', 1, 'http://x/1', 9.99, 2.0, 'USD', 'Mint', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    return path


@respx.mock
def test_migrate_creates_user_catalog_library_item_and_listing(pg_test_db, sqlite_source):
    db.init_global_schema()
    db.init_tenant_schema()
    respx.get("https://api.discogs.com/users/alice").mock(
        return_value=httpx.Response(200, json={"id": 777})
    )

    user_id = migrate(sqlite_source, discogs_username="alice")

    with db.get_admin_pool().connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = %s", [user_id]).fetchone()
        assert user["discogs_user_id"] == 777

        catalog_row = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'd1'").fetchone()
        assert catalog_row["artist"] == "A"

        library_item = conn.execute(
            "SELECT * FROM library_items WHERE user_id = %s AND discogs_id = 'd1'", [user_id]
        ).fetchone()
        assert library_item["in_collection"] is True

        listing = conn.execute("SELECT * FROM listings WHERE release_id = 'd1'").fetchone()
        assert listing["price"] == 9.99

        conn.execute("TRUNCATE users, catalog, library_items, listings, crawlers CASCADE")
        conn.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_migrate_from_sqlite.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.migrate_from_sqlite'`

- [ ] **Step 3: Write `backend/scripts/migrate_from_sqlite.py`**

```python
# backend/scripts/migrate_from_sqlite.py
import argparse
import sqlite3
from pathlib import Path
from typing import Optional

import httpx

import db


def resolve_discogs_user_id(username: str) -> int:
    r = httpx.get(f"https://api.discogs.com/users/{username}")
    r.raise_for_status()
    return r.json()["id"]


def migrate(sqlite_path: Path, discogs_username: Optional[str] = None, discogs_user_id: Optional[int] = None) -> int:
    if discogs_user_id is None:
        if discogs_username is None:
            raise ValueError("must provide discogs_username or discogs_user_id")
        discogs_user_id = resolve_discogs_user_id(discogs_username)

    sconn = sqlite3.connect(sqlite_path)
    sconn.row_factory = sqlite3.Row

    db.init_global_schema()
    db.init_tenant_schema()

    with db.get_admin_pool().connection() as pconn:
        existing = pconn.execute(
            "SELECT id FROM users WHERE discogs_user_id = %s", [discogs_user_id]
        ).fetchone()
        if existing:
            user_id = existing["id"]
        else:
            user_id = pconn.execute(
                "INSERT INTO users (discogs_user_id, discogs_username, created_at) "
                "VALUES (%s, %s, CURRENT_TIMESTAMP) RETURNING id",
                [discogs_user_id, discogs_username or str(discogs_user_id)],
            ).fetchone()["id"]

        for r in sconn.execute("SELECT * FROM releases").fetchall():
            db.upsert_catalog_release(pconn, {
                "discogs_id": r["discogs_id"], "artist": r["artist"], "title": r["title"],
                "year": r["year"], "label": r["label"], "format": r["format"],
                "discogs_price": r["discogs_price"], "barcode": r["barcode"],
                "cover_image_url": r["cover_image_url"], "discogs_url": r["discogs_url"],
            })
            db.upsert_library_item(
                pconn, user_id=user_id, discogs_id=r["discogs_id"],
                in_collection=bool(r["in_collection"]), in_wishlist=bool(r["in_wishlist"]),
            )

        crawler_id_map = {}
        for c in sconn.execute("SELECT * FROM crawlers").fetchall():
            row = pconn.execute(
                """
                INSERT INTO crawlers (site_name, module_path, crawler_type, enabled, last_run)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (site_name) DO UPDATE SET module_path = EXCLUDED.module_path
                RETURNING id
                """,
                [c["site_name"], c["module_path"], c["crawler_type"], bool(c["enabled"]), c["last_run"]],
            ).fetchone()
            crawler_id_map[c["id"]] = row["id"]

        for l in sconn.execute("SELECT * FROM listings").fetchall():
            db.upsert_listing(
                pconn, l["release_id"], crawler_id_map[l["crawler_id"]], l["url"],
                l["price"], l["shipping"], l["currency"], l["condition"],
            )

        pconn.commit()

    sconn.close()
    return user_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--discogs-username")
    group.add_argument("--discogs-user-id", type=int)
    args = parser.parse_args()
    new_user_id = migrate(args.sqlite_path, args.discogs_username, args.discogs_user_id)
    print(f"Migrated. New Postgres user_id={new_user_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_migrate_from_sqlite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add scripts/migrate_from_sqlite.py tests/test_migrate_from_sqlite.py
git commit -m "feat: add one-time SQLite to Postgres migration script"
```

---

## Plan self-review

**Spec coverage:** the architecture spec's "Data model" section (global `catalog`/`listings`/`crawlers`/`stock_items`/`stock_item_judgments`, per-user `users`/`sessions`/`library_items`/`invites`, RLS) is covered by Tasks 2-3. The "Migration path" section is covered by Task 7. The spec's RLS testing requirement ("a query issued under user A's session context must never return user B's rows, even without an explicit WHERE clause") is covered directly by Task 4. The spec did not fully work out how `users`/`sessions` lookups happen *before* a session exists (login, account creation) — this plan resolves that gap with the `app_identity`/`app_user` role split, documented in the Architecture section above as a refinement of, not a contradiction to, the spec's RLS design.

**Deferred, not missing:** `crawl_queue`, OAuth token population/encryption, and invite redemption logic are intentionally not in this plan — they belong to the crawl-queue and auth plans, which depend on this one.

**Type/signature consistency:** `upsert_library_item`'s parameter names (`user_id`, `discogs_id`, `in_collection`, `in_wishlist`) match `get_library_items_for_user`'s output column names, and match the `library_items` table's actual columns from Task 3 — checked directly against the DDL above, not assumed.
