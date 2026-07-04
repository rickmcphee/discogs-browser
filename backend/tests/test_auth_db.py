from datetime import datetime, timedelta

import db as db_module


def test_owner_lifecycle(conn):
    assert db_module.owner_exists(conn) is False
    db_module.create_owner(conn, "phash", "secret", ["h1", "h2"])
    assert db_module.owner_exists(conn) is True
    row = db_module.get_owner(conn)
    assert row["password_hash"] == "phash"
    assert row["totp_secret"] == "secret"


def test_owner_is_single_row(conn):
    db_module.create_owner(conn, "phash", "secret", [])
    db_module.create_owner(conn, "phash2", "secret2", [])
    row = db_module.get_owner(conn)
    assert row["password_hash"] == "phash2"


def test_update_password_and_totp(conn):
    db_module.create_owner(conn, "phash", "secret", [])
    db_module.update_owner_password(conn, "newhash")
    db_module.update_owner_totp(conn, "newsecret")
    row = db_module.get_owner(conn)
    assert row["password_hash"] == "newhash"
    assert row["totp_secret"] == "newsecret"


def test_recovery_code_consume(conn):
    db_module.create_owner(conn, "p", "s", ["h1", "h2"])
    assert db_module.consume_recovery_code(conn, "h1") is True
    assert db_module.consume_recovery_code(conn, "h1") is False
    assert db_module.consume_recovery_code(conn, "h2") is True


def test_set_recovery_codes_replaces(conn):
    db_module.create_owner(conn, "p", "s", ["h1"])
    db_module.set_owner_recovery_codes(conn, ["a", "b"])
    assert db_module.consume_recovery_code(conn, "h1") is False
    assert db_module.consume_recovery_code(conn, "a") is True


def test_delete_owner(conn):
    db_module.create_owner(conn, "p", "s", [])
    db_module.delete_owner(conn)
    assert db_module.owner_exists(conn) is False


def test_session_lifecycle(conn):
    now = datetime(2026, 1, 1, 12, 0, 0)
    exp = now + timedelta(days=30)
    db_module.create_session(conn, "tokhash", now.isoformat(), exp.isoformat())
    row = db_module.get_session(conn, "tokhash")
    assert row["token_hash"] == "tokhash"
    later = (now + timedelta(hours=1)).isoformat()
    db_module.touch_session(conn, "tokhash", later)
    assert db_module.get_session(conn, "tokhash")["last_seen_at"] == later
    db_module.delete_session(conn, "tokhash")
    assert db_module.get_session(conn, "tokhash") is None


def test_purge_expired_sessions(conn):
    db_module.create_session(conn, "old", "2020-01-01T00:00:00", "2020-02-01T00:00:00")
    db_module.create_session(conn, "new", "2026-01-01T00:00:00", "2099-01-01T00:00:00")
    db_module.purge_expired_sessions(conn, "2026-06-01T00:00:00")
    assert db_module.get_session(conn, "old") is None
    assert db_module.get_session(conn, "new") is not None
