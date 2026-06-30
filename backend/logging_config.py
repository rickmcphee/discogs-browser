import logging
import logging.handlers
from pathlib import Path
import config


def setup_logging():
    log_file = config.CONFIG_DIR / "app.log"
    config.CONFIG_DIR.mkdir(exist_ok=True)
    log_file.write_text("")

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicating uvicorn's own access log noise
    logging.getLogger("uvicorn.access").propagate = False
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
