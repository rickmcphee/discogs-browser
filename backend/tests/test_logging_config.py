import logging
import pytest
import db
from logging_config import setup_logging, get_logger, flush_queue


@pytest.fixture
def restore_logging():
    """Snapshot and restore global logging state around setup_logging()."""
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = root.handlers[:]
    yield
    for handler in root.handlers:
        if handler not in saved_handlers:
            handler.close()
    root.setLevel(saved_level)
    root.handlers[:] = saved_handlers


def test_root_logger_captures_debug(clean_app_logs_table, restore_logging):
    setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_only_application_loggers_are_written(clean_app_logs_table, restore_logging):
    setup_logging()
    get_logger("crawler").debug("APP debug line")
    get_logger("crawler").info("APP info line")
    logging.getLogger("httpx").warning("DEP httpx line")
    logging.getLogger("uvicorn.access").info("DEP uvicorn line")
    flush_queue()

    with db.get_admin_pool().connection() as conn:
        rows = conn.execute("SELECT message FROM app_logs ORDER BY id").fetchall()
    messages = [r["message"] for r in rows]
    assert "APP debug line" in messages
    assert "APP info line" in messages
    assert "DEP httpx line" not in messages
    assert "DEP uvicorn line" not in messages


def test_records_are_tagged_with_machine_id(clean_app_logs_table, restore_logging):
    import config

    setup_logging()
    get_logger("crawler").info("tagged line")
    flush_queue()

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT machine_id, level, logger_name FROM app_logs WHERE message = %s",
            ["tagged line"],
        ).fetchone()
    assert row["machine_id"] == config.MACHINE_ID
    assert row["level"] == "INFO"
    assert row["logger_name"] == "crawler"


def test_exception_traceback_is_appended_to_the_stored_message(clean_app_logs_table, restore_logging):
    setup_logging()
    log = get_logger("crawler")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("caught something")
    flush_queue()

    with db.get_admin_pool().connection() as conn:
        row = conn.execute(
            "SELECT message, level FROM app_logs WHERE message LIKE %s",
            ["caught something%"],
        ).fetchone()
    assert row is not None
    assert row["level"] == "ERROR"
    assert "ValueError: boom" in row["message"]
    assert "Traceback (most recent call last)" in row["message"]


def test_flush_queue_is_a_noop_on_an_empty_queue(clean_app_logs_table, restore_logging):
    setup_logging()
    flush_queue()  # nothing queued -- must not raise or insert anything

    with db.get_admin_pool().connection() as conn:
        count = conn.execute("SELECT count(*) AS n FROM app_logs").fetchone()["n"]
    assert count == 0
