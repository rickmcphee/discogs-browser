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
