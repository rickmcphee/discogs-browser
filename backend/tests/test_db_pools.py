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
