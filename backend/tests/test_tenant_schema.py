import os
from datetime import datetime

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


def test_app_identity_can_update_session_last_seen_at(admin_conn):
    admin_conn.execute(
        "INSERT INTO users (discogs_user_id, discogs_username) VALUES (%s, %s)",
        [909090, "sessionupdatetestuser"],
    )
    user_id = admin_conn.execute(
        "SELECT id FROM users WHERE discogs_user_id = %s", [909090]
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) "
        "VALUES (%s, %s, NOW() + INTERVAL '1 day')",
        ["sessionupdatetesttoken", user_id],
    )
    admin_conn.commit()

    new_last_seen_at = datetime(2030, 1, 1)
    with _connect_as("app_identity", os.environ["IDENTITY_DB_PASSWORD"]) as conn:
        conn.execute(
            "UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s",
            [new_last_seen_at, "sessionupdatetesttoken"],
        )
        conn.commit()

    row = admin_conn.execute(
        "SELECT last_seen_at FROM sessions WHERE token_hash = %s",
        ["sessionupdatetesttoken"],
    ).fetchone()
    assert row["last_seen_at"] == new_last_seen_at


def test_init_tenant_schema_raises_when_identity_password_blank(pg_test_db, monkeypatch):
    monkeypatch.setattr(config, "IDENTITY_DB_PASSWORD", "")
    with pytest.raises(RuntimeError):
        db.init_tenant_schema()


def test_init_tenant_schema_raises_when_app_password_blank(pg_test_db, monkeypatch):
    monkeypatch.setattr(config, "APP_DB_PASSWORD", "")
    with pytest.raises(RuntimeError):
        db.init_tenant_schema()
