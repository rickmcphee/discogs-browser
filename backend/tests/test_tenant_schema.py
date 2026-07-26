import os

import psycopg
import pytest

import config
import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, sessions, library_items, invites CASCADE")
        conn.commit()


def _connect_as(role_name: str, password: str):
    """A real connection authenticated as the given role (not the admin
    connection pg_test_db points everything at), for asserting on GRANT
    boundaries rather than RLS."""
    dsn = config._with_userinfo(os.environ["TEST_DATABASE_URL"], role_name, password)
    return psycopg.connect(dsn)


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


def test_invites_table_has_rls_disabled(admin_conn):
    row = admin_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'invites'"
    ).fetchone()
    assert row["relrowsecurity"] is False
    assert row["relforcerowsecurity"] is False


def test_app_identity_cannot_query_library_items(admin_conn):
    with _connect_as("app_identity", os.environ["IDENTITY_DB_PASSWORD"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM library_items")


def test_app_user_cannot_query_users(admin_conn):
    with _connect_as("app_user", os.environ["APP_DB_PASSWORD"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM users")


def test_app_user_cannot_query_sessions(admin_conn):
    with _connect_as("app_user", os.environ["APP_DB_PASSWORD"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM sessions")
