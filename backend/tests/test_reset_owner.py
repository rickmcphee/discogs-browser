import db as db_module
import reset_owner


def test_reset_clears_owner_and_sessions(conn):
    db_module.create_owner(conn, "p", "s", [])
    db_module.create_session(conn, "t", "2026-01-01T00:00:00", "2099-01-01T00:00:00")
    reset_owner.reset(conn)
    assert db_module.owner_exists(conn) is False
    assert db_module.get_session(conn, "t") is None
