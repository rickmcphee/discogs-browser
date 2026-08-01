import os
from datetime import datetime, timedelta

import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import db
import session_tokens
from auth_middleware import AuthMiddleware


@pytest.fixture
def pg_test_db(monkeypatch):
    import db as db_module

    monkeypatch.setattr(db_module.config, "DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(
        db_module.config, "IDENTITY_DATABASE_URL", os.environ["TEST_DATABASE_URL"]
    )
    monkeypatch.setattr(
        db_module.config, "APP_DATABASE_URL", os.environ["TEST_DATABASE_URL"]
    )
    db_module._admin_pool = None
    db_module._identity_pool = None
    db_module._app_pool = None
    yield
    for attr in ("_admin_pool", "_identity_pool", "_app_pool"):
        pool = getattr(db_module, attr)
        if pool is not None:
            pool.close()
        setattr(db_module, attr, None)


@pytest.fixture
def authed_client_factory_builder(pg_test_db):
    """Generic base for router test files that need a real TestClient wired
    with AuthMiddleware against Postgres, pre-authenticated as a given user.

    Router selection is deliberately left as a parameter here rather than
    baked in: each test file defines its own `authed_client_factory` fixture
    that calls `authed_client_factory_builder([the router(s) it's testing])`,
    so test bodies can just call `authed_client_factory(user_id)`.
    """
    db.init_global_schema()
    db.init_tenant_schema()

    def _build(routers):
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        for router in routers:
            app.include_router(router, prefix="/api")

        def _factory(user_id):
            token = session_tokens.new_session_token()
            with db.get_admin_pool().connection() as conn:
                db.create_session(
                    conn, session_tokens.hash_token(token), user_id,
                    datetime.utcnow() + timedelta(days=1),
                )
                conn.commit()
            client = TestClient(app)
            client.cookies.set(config.COOKIE_NAME, token)
            return client
        return _factory

    yield _build

    # CASCADE also clears every table with a (possibly indirect) FK back to
    # catalog/users/crawlers -- library_items, listings, crawl_queue,
    # sessions, invites, etc. -- so each test file starts from a clean slate
    # regardless of which of those it happened to touch.
    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
        conn.commit()


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Patch CONFIG_DIR to a temp directory for all tests."""
    crawlers_dir = tmp_path / "crawlers"
    crawlers_dir.mkdir()
    (crawlers_dir / "__init__.py").touch()
    with patch("config.CONFIG_DIR", tmp_path), \
         patch("config.DB_FILE", tmp_path / "db.sqlite"), \
         patch("config.CRAWLERS_DIR", crawlers_dir), \
         patch("config.CONFIG_FILE", tmp_path / "config.json"):
        yield tmp_path
