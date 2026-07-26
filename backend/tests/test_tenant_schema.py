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
