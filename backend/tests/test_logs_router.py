import pytest

import db
from routers import logs as logs_router
from routers.logs import _line_visible, _parse_levels

INFO_LINE = "2026-07-18 20:19:18  INFO      main  ready"
DEBUG_LINE = "2026-07-18 20:19:18  DEBUG     crawlers.amazon  [Amazon] searching"
ERROR_LINE = "2026-07-18 20:19:18  ERROR     crawler  boom"
CONTINUATION_LINE = "    File \"x.py\", line 1, in <module>"


def test_no_levels_shows_all():
    assert _line_visible(INFO_LINE, None)
    assert _line_visible(DEBUG_LINE, None)


def test_filters_out_unwanted_levels():
    wanted = {"INFO", "WARNING", "ERROR"}
    assert _line_visible(INFO_LINE, wanted)
    assert _line_visible(ERROR_LINE, wanted)
    assert not _line_visible(DEBUG_LINE, wanted)


def test_unparseable_lines_always_shown():
    # Tracebacks / continuation lines carry no level and must not be dropped
    assert _line_visible(CONTINUATION_LINE, {"INFO"})


def test_parse_levels_normalizes_and_ignores_blanks():
    assert _parse_levels("info, debug ,,") == {"INFO", "DEBUG"}
    assert _parse_levels(None) is None
    assert _parse_levels("") is None


def test_parse_levels_ignores_unknown_values():
    # Unknown tokens are dropped; a mix keeps only supported levels
    assert _parse_levels("info,foo") == {"INFO"}
    # Only-unknown means no usable filter -> None (show all) rather than empty
    assert _parse_levels("foo,bar") is None


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([logs_router.router])


def test_logs_stream_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.get("/api/logs/stream")
    assert r.status_code == 403


def test_clear_logs_requires_admin(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.delete("/api/logs", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403


def test_clear_logs_as_admin_succeeds(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", [user["id"]])
        conn.commit()
    client = authed_client_factory(user["id"])
    r = client.delete("/api/logs", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
