import logging
import logging.handlers
import queue
import sys
import threading
import time
from typing import Optional

import config

_APP_LOGGERS: set = set()

_QUEUE_MAXSIZE = 5000
_FLUSH_INTERVAL_SECONDS = 1.0
_PRUNE_INTERVAL_SECONDS = 3600
_RETENTION_DAYS = 30

_log_queue: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_writer_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


class _AppOnlyFilter(logging.Filter):
    """Pass only records emitted by this application's own loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in _APP_LOGGERS


def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app_only = _AppOnlyFilter()

    queue_handler = logging.handlers.QueueHandler(_log_queue)
    queue_handler.addFilter(app_only)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.addFilter(app_only)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(queue_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    _APP_LOGGERS.add(name)
    return logging.getLogger(name)


def flush_queue():
    """Drain whatever's currently queued and insert it into app_logs in one
    batch. Called repeatedly by the background writer thread; also called
    directly by tests, which need a deterministic write with no sleep/poll."""
    records = []
    while True:
        try:
            records.append(_log_queue.get_nowait())
        except queue.Empty:
            break
    if not records:
        return

    import db

    rows = []
    for record in records:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
        rows.append((record.created, record.levelname, record.name, config.MACHINE_ID, message))

    try:
        with db.get_admin_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO app_logs (ts, level, logger_name, machine_id, message) "
                    "VALUES (to_timestamp(%s), %s, %s, %s, %s)",
                    rows,
                )
            conn.commit()
    except Exception as e:
        print(f"logging_config: failed to write {len(rows)} log record(s) to Postgres: {e}", file=sys.stderr)


def _prune_old_logs():
    import db

    try:
        with db.get_admin_pool().connection() as conn:
            conn.execute(
                f"DELETE FROM app_logs WHERE ts < now() - interval '{_RETENTION_DAYS} days'"
            )
            conn.commit()
    except Exception as e:
        print(f"logging_config: failed to prune old log rows: {e}", file=sys.stderr)


def _writer_loop():
    last_prune = time.monotonic()
    while not _stop_event.wait(timeout=_FLUSH_INTERVAL_SECONDS):
        flush_queue()
        now = time.monotonic()
        if now - last_prune >= _PRUNE_INTERVAL_SECONDS:
            _prune_old_logs()
            last_prune = now
    flush_queue()  # final drain so nothing queued right before shutdown is lost


def start_log_writer():
    global _writer_thread
    _stop_event.clear()
    _writer_thread = threading.Thread(target=_writer_loop, name="log-writer", daemon=True)
    _writer_thread.start()


def stop_log_writer():
    global _writer_thread
    _stop_event.set()
    if _writer_thread is not None:
        _writer_thread.join(timeout=5)
        _writer_thread = None
    flush_queue()  # catch anything enqueued during the shutdown handoff
