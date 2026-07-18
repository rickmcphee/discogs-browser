import logging
import logging.handlers
from pathlib import Path
import config


# Names of loggers created by this application (via get_logger). Only records
# from these loggers are written to app.log / console, so the log viewer shows
# this application's output only and is not drowned by dependency logging.
_APP_LOGGERS: set = set()


class _AppOnlyFilter(logging.Filter):
    """Pass only records emitted by this application's own loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in _APP_LOGGERS


def setup_logging():
    log_file = config.CONFIG_DIR / "app.log"
    config.CONFIG_DIR.mkdir(exist_ok=True)
    log_file.write_text("")

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app_only = _AppOnlyFilter()

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(app_only)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.addFilter(app_only)

    # Root stays at DEBUG so the app's own loggers emit every level; the
    # app-only filter on the handlers drops all dependency/third-party records.
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    _APP_LOGGERS.add(name)
    return logging.getLogger(name)
