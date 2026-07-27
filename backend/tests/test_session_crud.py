from datetime import datetime, timedelta

import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, sessions CASCADE")
        conn.commit()


def test_create_session_then_get_by_token_hash(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()

    expires_at = datetime.utcnow() + timedelta(days=30)
    db.create_session(admin_conn, "hash-abc", user["id"], expires_at)
    admin_conn.commit()

    row = db.get_session_by_token_hash(admin_conn, "hash-abc")
    assert row["user_id"] == user["id"]


def test_get_session_by_token_hash_returns_none_when_missing(admin_conn):
    assert db.get_session_by_token_hash(admin_conn, "does-not-exist") is None


def test_touch_session_updates_last_seen_at(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()
    db.create_session(admin_conn, "hash-abc", user["id"], datetime.utcnow() + timedelta(days=30))
    admin_conn.commit()
    original = db.get_session_by_token_hash(admin_conn, "hash-abc")["last_seen_at"]

    db.touch_session(admin_conn, "hash-abc")
    admin_conn.commit()
    updated = db.get_session_by_token_hash(admin_conn, "hash-abc")["last_seen_at"]
    assert updated >= original


def test_delete_session_removes_it(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()
    db.create_session(admin_conn, "hash-abc", user["id"], datetime.utcnow() + timedelta(days=30))
    admin_conn.commit()

    db.delete_session(admin_conn, "hash-abc")
    admin_conn.commit()
    assert db.get_session_by_token_hash(admin_conn, "hash-abc") is None
