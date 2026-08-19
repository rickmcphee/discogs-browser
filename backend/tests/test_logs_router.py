import pytest

import db
from routers import logs as logs_router
from routers.logs import (
    _parse_levels, _fetch_history, _fetch_max_id, _fetch_new, _row_to_payload, clear_logs,
)


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([logs_router.router])


def _non_admin_client(authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    return authed_client_factory(user["id"])


def test_clear_logs_requires_admin(pg_test_db, authed_client_factory):
    client = _non_admin_client(authed_client_factory)
    r = client.delete("/api/logs", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_logs_stream_requires_admin(pg_test_db, authed_client_factory):
    client = _non_admin_client(authed_client_factory)
    r = client.get("/api/logs/stream")
    assert r.status_code == 403


def test_parse_levels_normalizes_and_ignores_blanks():
    assert _parse_levels("info, debug ,,") == {"INFO", "DEBUG"}
    assert _parse_levels(None) is None
    assert _parse_levels("") is None


def test_parse_levels_ignores_unknown_values():
    assert _parse_levels("info,foo") == {"INFO"}
    assert _parse_levels("foo,bar") is None


def _insert_row(conn, level, message, logger_name="crawler", machine_id="m1"):
    conn.execute(
        "INSERT INTO app_logs (level, logger_name, machine_id, message) VALUES (%s, %s, %s, %s)",
        [level, logger_name, machine_id, message],
    )


def test_fetch_history_returns_chronological_order(clean_app_logs_table):
    with db.get_admin_pool().connection() as conn:
        _insert_row(conn, "INFO", "first")
        _insert_row(conn, "INFO", "second")
        _insert_row(conn, "INFO", "third")
        conn.commit()

    rows = _fetch_history(None)
    assert [r["message"] for r in rows] == ["first", "second", "third"]


def test_fetch_history_filters_by_level(clean_app_logs_table):
    with db.get_admin_pool().connection() as conn:
        _insert_row(conn, "DEBUG", "debug line")
        _insert_row(conn, "ERROR", "error line")
        conn.commit()

    rows = _fetch_history({"ERROR"})
    assert [r["message"] for r in rows] == ["error line"]


def test_fetch_history_is_time_ordered_not_id_ordered(clean_app_logs_table):
    """Two Machines flushing ~1s batches interleave in time but not in id: A's
    whole batch lands as one id block, then B's overlapping-in-time batch. The
    seed has to read chronologically regardless."""
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "INSERT INTO app_logs (ts, level, logger_name, machine_id, message) VALUES "
            "('2026-08-17 10:00:00+00', 'INFO', 'crawler', 'a', 'a-first'), "
            "('2026-08-17 10:00:02+00', 'INFO', 'crawler', 'a', 'a-third'), "
            "('2026-08-17 10:00:01+00', 'INFO', 'crawler', 'b', 'b-second')"
        )
        conn.commit()

    rows = _fetch_history(None)
    assert [r["message"] for r in rows] == ["a-first", "b-second", "a-third"]


def test_fetch_max_id_seeds_the_poll_cursor(clean_app_logs_table):
    assert _fetch_max_id() == 0
    with db.get_admin_pool().connection() as conn:
        _insert_row(conn, "INFO", "only")
        conn.commit()
        expected = conn.execute("SELECT max(id) AS n FROM app_logs").fetchone()["n"]

    # An ERROR-only history seed is empty here, so generate() falls back to this
    # rather than leaving last_id at 0 and rescanning the table on every poll.
    assert _fetch_history({"ERROR"}) == []
    assert _fetch_max_id() == expected


def test_fetch_new_only_returns_rows_after_last_id(clean_app_logs_table):
    with db.get_admin_pool().connection() as conn:
        _insert_row(conn, "INFO", "old")
        conn.commit()
        old_id = conn.execute("SELECT id FROM app_logs WHERE message = 'old'").fetchone()["id"]
        _insert_row(conn, "INFO", "new")
        conn.commit()

    rows = _fetch_new(old_id, None)
    assert [r["message"] for r in rows] == ["new"]


def test_row_to_payload_formats_timestamp(clean_app_logs_table):
    with db.get_admin_pool().connection() as conn:
        conn.execute(
            "INSERT INTO app_logs (level, logger_name, machine_id, message) "
            "VALUES ('INFO', 'crawler', 'm1', 'hi')"
        )
        conn.commit()
        row = conn.execute("SELECT id, ts, level, logger_name, machine_id, message FROM app_logs WHERE message = 'hi'").fetchone()

    payload = _row_to_payload(row)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "crawler"
    assert payload["machine"] == "m1"
    assert payload["message"] == "hi"
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", payload["time"])


def test_clear_logs_deletes_all_rows(clean_app_logs_table):
    with db.get_admin_pool().connection() as conn:
        _insert_row(conn, "INFO", "will be cleared")
        conn.commit()

    clear_logs()

    with db.get_admin_pool().connection() as conn:
        count = conn.execute("SELECT count(*) AS n FROM app_logs").fetchone()["n"]
    assert count == 0
