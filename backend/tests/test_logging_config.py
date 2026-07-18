import logging
import pytest
from logging_config import setup_logging, get_logger


@pytest.fixture
def restore_logging():
    """Snapshot and restore global logging state around setup_logging()."""
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = root.handlers[:]
    yield
    # Close handlers setup_logging() added so their file descriptors don't leak
    for handler in root.handlers:
        if handler not in saved_handlers:
            handler.close()
    root.setLevel(saved_level)
    root.handlers[:] = saved_handlers


def test_root_logger_captures_debug(tmp_config_dir, restore_logging):
    setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_only_application_loggers_are_written(tmp_config_dir, restore_logging):
    import config
    setup_logging()
    get_logger("crawler").debug("APP debug line")
    get_logger("crawler").info("APP info line")
    logging.getLogger("httpx").warning("DEP httpx line")
    logging.getLogger("uvicorn.access").info("DEP uvicorn line")
    for handler in logging.getLogger().handlers:
        handler.flush()
    log = (config.CONFIG_DIR / "app.log").read_text()
    assert "APP debug line" in log
    assert "APP info line" in log
    assert "DEP httpx line" not in log
    assert "DEP uvicorn line" not in log
