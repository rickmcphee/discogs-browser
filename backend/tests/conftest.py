import os
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import db
import session_tokens
from auth_middleware import AuthMiddleware


def _with_database(url, dbname):
    """Swap the database name on a DSN, leaving netloc — and therefore the
    userinfo and its percent-encoding — untouched. Mirror image of
    config._with_userinfo (backend/config.py:14), which swaps the other half.
    Kept here rather than in config.py: production has no use for it."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


# Inverted relative to what db._ensure_role sets, so every assertion about
# these roles — and every connection made as either — depends on _ensure_role
# running in this process. Roles are cluster-level, so nothing else in the
# suite ever resets them: without this, app_user/app_identity are leftovers
# from whenever the multi-tenant work first ran locally, and
# test_app_identity_role_has_bypassrls passes with the ALTER ROLE deleted.
_POISONED_BYPASSRLS = {"app_user": "BYPASSRLS", "app_identity": "NOBYPASSRLS"}

# What db._ensure_role sets, i.e. what teardown puts back. Both roles, not just
# app_user: a session that never reaches init_tenant_schema() (running only
# crawler-parsing tests, say) would otherwise leave app_identity inverted in the
# cluster with no repair path. Passwords are deliberately not restored -- the
# correct value is whatever IDENTITY_DB_PASSWORD/APP_DB_PASSWORD held, and the
# next init_tenant_schema() rewrites them anyway.
_CORRECT_BYPASSRLS = {"app_user": "NOBYPASSRLS", "app_identity": "BYPASSRLS"}


def _set_bypassrls(conn, roles, wanted):
    for role, bypass in wanted.items():
        if roles.get(role) is None:
            continue
        conn.execute(
            sql.SQL("ALTER ROLE {} {}").format(sql.Identifier(role), sql.SQL(bypass))
        )


def _poison_app_roles(conn, observed):
    """Invert the attributes db._ensure_role owns, recording each role's
    rolbypassrls as actually read back after poisoning, or None for a role
    absent from the cluster (a fresh CI cluster has nothing to poison).

    Fills a dict the caller owns rather than returning one, so that a failure
    partway through still leaves the caller able to un-poison what did land."""
    for role, bypass in _POISONED_BYPASSRLS.items():
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role]).fetchone() is None:
            observed[role] = None
            continue
        conn.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {} {}").format(
                sql.Identifier(role), sql.Literal(uuid.uuid4().hex), sql.SQL(bypass)
            )
        )
        observed[role] = conn.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", [role]
        ).fetchone()[0]


@pytest.fixture(scope="session", autouse=True)
def pg_run_database():
    """Every pytest session gets its own empty database.

    The old shared-database arrangement made schema-shape tests vacuous: the
    per-test TRUNCATE teardowns drop rows, never columns, and the local
    discogs_browser_test lives in a persistent Docker volume, so a migration
    applied once stayed applied whether or not its ALTER TABLE was still in
    db.py. It also meant two worktrees running pytest at once truncated each
    other's fixture rows mid-test.

    TEMPLATE template0 rather than a hand-recreated schema: a recreated
    `public` schema loses the default `GRANT USAGE ... TO PUBLIC` that
    TENANT_SCHEMA silently relies on, which fails ~88 tests. See
    docs/specifications/shaping/2026-08-09-test-database-freshness-design.md.
    """
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        # Most test files never touch Postgres, and this fixture is autouse --
        # provisioning unconditionally would make a running database with
        # CREATEDB a precondition for the crawler-parsing tests too. Yield None
        # instead and let pg_test_db raise for the files that genuinely need it,
        # exactly as it did before this fixture existed.
        yield None
        return

    base_name = urlsplit(base_url).path.lstrip("/")
    run_name = f"{base_name}_run_{uuid.uuid4().hex[:8]}"
    maintenance_url = _with_database(base_url, "postgres")

    with psycopg.connect(maintenance_url, autocommit=True) as conn:
        try:
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(run_name)
                )
            )
        except psycopg.errors.InsufficientPrivilege as exc:
            raise RuntimeError(
                "the role in TEST_DATABASE_URL needs CREATEDB to provision the "
                f"per-run test database {run_name}"
            ) from exc

    run_url = _with_database(base_url, run_name)
    roles = {}
    try:
        with psycopg.connect(maintenance_url, autocommit=True) as conn:
            try:
                _poison_app_roles(conn, roles)
            except psycopg.errors.InsufficientPrivilege as exc:
                # CREATEDB alone gets as far as the CREATE DATABASE above, so
                # without this the failure looks like a bare permission error on
                # a role the reader never asked to touch.
                raise RuntimeError(
                    "the role in TEST_DATABASE_URL cannot ALTER ROLE app_user/"
                    "app_identity, which this fixture does to stop the "
                    "tenant-schema role assertions passing vacuously. Setting "
                    "BYPASSRLS needs a superuser (or a role that has BYPASSRLS "
                    "itself) and changing a password needs CREATEROLE -- see the "
                    "note above db.init_tenant_schema. CREATEDB is not enough."
                ) from exc
        os.environ["TEST_DATABASE_URL"] = run_url
        with psycopg.connect(run_url) as conn:
            tables_at_start = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchone()[0]

        yield {
            "database": run_name,
            "base_database": base_name,
            "tables_at_start": tables_at_start,
            "app_user_bypassrls_at_start": roles.get("app_user"),
            "app_identity_bypassrls_at_start": roles.get("app_identity"),
        }
    finally:
        # finally, not straight-line code: poisoning needs undoing and the
        # database needs dropping even when setup raised between CREATE DATABASE
        # and yield (ALTER ROLE ... BYPASSRLS needs superuser, so a CREATEDB-only
        # admin role fails there -- see db.py's note above init_tenant_schema).
        for attr in ("_admin_pool", "_identity_pool", "_app_pool"):
            pool = getattr(db, attr)
            if pool is not None:
                pool.close()
            setattr(db, attr, None)
        os.environ["TEST_DATABASE_URL"] = base_url
        with psycopg.connect(maintenance_url, autocommit=True) as conn:
            # Roles first: a failing DROP must not skip un-poisoning, or a
            # cluster role is left holding an attribute it should never keep.
            _set_bypassrls(conn, roles, _CORRECT_BYPASSRLS)
            # FORCE so a pool connection that outlived the loop above cannot
            # block the drop and leak the database.
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(run_name)
                )
            )


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


@pytest.fixture(autouse=True)
def _fast_catalog_crawl_sleep(request, monkeypatch):
    """crawl_catalog() sleeps real seconds per page; *_crawler.py tests check parsing, not timing."""
    if request.module.__name__.endswith("_crawler"):
        async def fake_sleep(seconds):
            pass

        monkeypatch.setattr("shopify_catalog.sleep", fake_sleep)
        # angryyoungandpoor.py and amoeba.py pace their own Playwright calls rather
        # than going through shopify_catalog.iter_products() (they're catalog_browser
        # crawlers, not httpx ones) -- patch their module-local `sleep` bindings too,
        # when importable.
        for module_name in ("angryyoungandpoor", "amoeba"):
            try:
                monkeypatch.setattr(f"{module_name}.sleep", fake_sleep)
            except (ModuleNotFoundError, AttributeError):
                pass
