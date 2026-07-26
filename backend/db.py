from contextlib import contextmanager
from typing import Optional

from psycopg import sql
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
