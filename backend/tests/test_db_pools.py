import os
from contextlib import contextmanager

import psycopg
import pytest

import db


@pytest.fixture(autouse=True)
def _test_database_url(monkeypatch):
    monkeypatch.setattr(db.config, "DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    db._admin_pool = None
    db._identity_pool = None
    db._app_pool = None
    yield
    for attr in ("_admin_pool", "_identity_pool", "_app_pool"):
        pool = getattr(db, attr)
        if pool is not None:
            pool.close()
        setattr(db, attr, None)


def test_admin_pool_connects_and_runs_a_query():
    with db.get_admin_pool().connection() as conn:
        row = conn.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1


def test_admin_pool_replaces_a_connection_the_server_closed():
    """Managed Postgres closes connections out from under the pool -- a Neon
    compute restart or a blip at the pooler drops every one this process holds
    at once. Without the pools' check= callback the next borrower is handed the
    dead connection and fails with AdminShutdown on a query that had nothing
    wrong with it; with it, the pool throws that connection away and opens
    another, and the borrower never sees the restart at all."""
    pool = db.get_admin_pool()
    pool.wait(timeout=10)
    with pool.connection() as conn:
        conn.execute("SELECT 1").fetchone()

    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as killer:
        killed = killer.execute(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE pid <> pg_backend_pid() AND datname = current_database()"
        ).fetchone()[0]
    assert killed, "nothing was killed, so the reconnect below proves nothing"

    with pool.connection() as conn:
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


class _FakeConnection:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def connection(self):
        yield self._conn


def test_user_scope_sets_app_user_id_with_parameterized_local_config(monkeypatch):
    fake_conn = _FakeConnection()
    monkeypatch.setattr(db, "get_app_pool", lambda: _FakePool(fake_conn))

    with db.user_scope(42) as conn:
        assert conn is fake_conn

    assert len(fake_conn.executed) == 1
    query, params = fake_conn.executed[0]
    assert query == "SELECT set_config('app.user_id', %s, true)"
    assert params == ["42"]
