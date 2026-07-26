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
