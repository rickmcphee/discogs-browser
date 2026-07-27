from datetime import datetime, timedelta

import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE oauth_request_state, pending_signups CASCADE")
        conn.commit()


def test_create_and_consume_oauth_request_state(admin_conn):
    db.create_oauth_request_state(admin_conn, "req-token-1", "req-secret-1")
    admin_conn.commit()

    row = db.get_and_delete_oauth_request_state(admin_conn, "req-token-1")
    admin_conn.commit()
    assert row["request_token_secret"] == "req-secret-1"

    # single-use: a second consume attempt finds nothing
    assert db.get_and_delete_oauth_request_state(admin_conn, "req-token-1") is None


def test_get_and_delete_oauth_request_state_returns_none_when_missing(admin_conn):
    assert db.get_and_delete_oauth_request_state(admin_conn, "does-not-exist") is None


def test_expired_oauth_request_state_is_rejected_and_still_deleted(admin_conn):
    admin_conn.execute(
        "INSERT INTO oauth_request_state (request_token, request_token_secret, created_at) "
        "VALUES (%s, %s, %s)",
        ["old-token", "old-secret", datetime.utcnow() - timedelta(minutes=11)],
    )
    admin_conn.commit()

    assert db.get_and_delete_oauth_request_state(admin_conn, "old-token", max_age_minutes=10) is None
    remaining = admin_conn.execute(
        "SELECT 1 FROM oauth_request_state WHERE request_token = 'old-token'"
    ).fetchone()
    assert remaining is None


def test_create_and_consume_pending_signup(admin_conn):
    db.create_pending_signup(
        admin_conn, "signup-token-1", 777, "alice", b"encrypted-token", b"encrypted-secret"
    )
    admin_conn.commit()

    row = db.get_and_delete_pending_signup(admin_conn, "signup-token-1")
    admin_conn.commit()
    assert row["discogs_user_id"] == 777
    assert row["discogs_username"] == "alice"
    assert row["oauth_token_encrypted"] == b"encrypted-token"
    assert row["oauth_secret_encrypted"] == b"encrypted-secret"

    assert db.get_and_delete_pending_signup(admin_conn, "signup-token-1") is None


def test_expired_pending_signup_is_rejected_and_still_deleted(admin_conn):
    admin_conn.execute(
        "INSERT INTO pending_signups (token, discogs_user_id, discogs_username, "
        "oauth_token_encrypted, oauth_secret_encrypted, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ["old-signup", 1, "bob", b"x", b"y", datetime.utcnow() - timedelta(minutes=16)],
    )
    admin_conn.commit()

    assert db.get_and_delete_pending_signup(admin_conn, "old-signup", max_age_minutes=15) is None
    remaining = admin_conn.execute(
        "SELECT 1 FROM pending_signups WHERE token = 'old-signup'"
    ).fetchone()
    assert remaining is None
