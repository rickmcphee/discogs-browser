import logging
import pytest
from logging_config import setup_logging, NOISY_LOGGERS


@pytest.fixture
def restore_logging():
    """Snapshot and restore global logging state around setup_logging()."""
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = root.handlers[:]
    saved_noisy = {name: logging.getLogger(name).level for name in NOISY_LOGGERS}
    yield
    root.setLevel(saved_level)
    root.handlers[:] = saved_handlers
    for name, level in saved_noisy.items():
        logging.getLogger(name).setLevel(level)


def test_root_logger_captures_debug(tmp_config_dir, restore_logging):
    setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_noisy_loggers_are_quieted(tmp_config_dir, restore_logging):
    setup_logging()
    for name, expected in NOISY_LOGGERS.items():
        assert logging.getLogger(name).level == expected
        assert expected > logging.DEBUG, f"{name} would still emit DEBUG noise"
