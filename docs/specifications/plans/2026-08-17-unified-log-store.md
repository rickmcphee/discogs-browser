# Unified log store across Fly Machines — implementation plan

Spec: [`docs/specifications/shaping/2026-08-17-unified-log-store-design.md`](../shaping/2026-08-17-unified-log-store-design.md)

This plan follows the task-prose-plus-code style (no per-step TDD checkboxes,
no header boilerplate) already used in
[`docs/specifications/plans/2026-08-16-fly-multi-machine.md`](2026-08-16-fly-multi-machine.md) —
the plan for the spec this one supersedes on `app.log` — rather than the more
granular generic template, since that's this repo's established convention
for plans of this shape.

## Task 1 — `app_logs` table

`backend/db.py`:
- Add to `GLOBAL_SCHEMA`, right after the existing `app_config` block (before
  `CREATE TABLE IF NOT EXISTS listings`):
  ```sql
  CREATE TABLE IF NOT EXISTS app_logs (
      id BIGSERIAL PRIMARY KEY,
      ts TIMESTAMPTZ NOT NULL DEFAULT now(),
      level TEXT NOT NULL,
      logger_name TEXT NOT NULL,
      machine_id TEXT NOT NULL,
      message TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS app_logs_ts_idx ON app_logs (ts);
  ```
  No RLS needed — global schema, same as `catalog`/`crawlers`/`app_config`.

`backend/tests/conftest.py`:
- `authed_client_factory_builder`'s teardown (`conftest.py:243`) currently
  runs `TRUNCATE catalog, users, crawlers, app_config CASCADE`. `app_logs`
  has no FK to any of those tables, so `CASCADE` never reaches it — same gap
  the prior plan's Task 10 already found and fixed for `app_config`. Add it
  to the same statement: `TRUNCATE catalog, users, crawlers, app_config,
  app_logs CASCADE`.
- Add a new fixture, used by Task 3's and Task 5's tests, right after
  `pg_test_db`:
  ```python
  @pytest.fixture
  def clean_app_logs_table(pg_test_db):
      db.init_global_schema()
      with db.get_admin_pool().connection() as conn:
          conn.execute("TRUNCATE app_logs")
          conn.commit()
      yield
      with db.get_admin_pool().connection() as conn:
          conn.execute("TRUNCATE app_logs")
          conn.commit()
  ```

Verify: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test IDENTITY_DB_PASSWORD=test APP_DB_PASSWORD=test pytest tests/test_db.py -v` (or any existing schema-smoke test) still passes — `init_global_schema()` is idempotent and this only adds a new `CREATE TABLE IF NOT EXISTS`, so nothing existing should break.

## Task 2 — Machine identity

`backend/config.py`:
- Add `import socket` to the existing imports.
- Near the other environment-derived constants (after `BACKEND_BASE_URL`):
  ```python
  # FLY_MACHINE_ID is set automatically by Fly for every Machine; checked
  # first only because it's the more precise, documented identifier when
  # present. socket.gethostname() covers every other case without any
  # deployment-specific assumption: it resolves to the container's own
  # hostname under Docker (unique per container automatically, so this is
  # already correct if docker-compose is ever scaled to multiple replicas)
  # and to the host machine's hostname for bare local dev.
  MACHINE_ID = os.environ.get("FLY_MACHINE_ID") or socket.gethostname()
  ```

`backend/tests/test_config.py`:
- Add:
  ```python
  def test_machine_id_prefers_fly_machine_id(monkeypatch):
      monkeypatch.setenv("FLY_MACHINE_ID", "3287561a1e4487")
      import importlib
      import config
      importlib.reload(config)
      assert config.MACHINE_ID == "3287561a1e4487"


  def test_machine_id_falls_back_to_hostname(monkeypatch):
      monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
      import importlib
      import socket
      import config
      importlib.reload(config)
      assert config.MACHINE_ID == socket.gethostname()
  ```
  `importlib.reload(config)` is required because `MACHINE_ID` is computed
  once at import time — `monkeypatch.setenv`/`delenv` alone wouldn't change
  an already-imported module's constant. Reloading `config` re-derives
  `DATABASE_URL` etc. from the current environment too, same caveat
  `test_main.py`'s CORS tests already call out for the same reason — keep
  these two tests self-contained and don't rely on `pg_test_db`/`DATABASE_URL`
  staying repointed at the test database afterward in the same test function.

Run: `pytest tests/test_config.py -v` — expect both new tests to pass.

## Task 3 — `logging_config.py`: Postgres-backed write path

`backend/logging_config.py` — replace the file-based body entirely:

```python
import logging
import logging.handlers
import queue
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
        print(f"logging_config: failed to write {len(rows)} log record(s) to Postgres: {e}")


def _prune_old_logs():
    import db

    try:
        with db.get_admin_pool().connection() as conn:
            conn.execute(
                f"DELETE FROM app_logs WHERE ts < now() - interval '{_RETENTION_DAYS} days'"
            )
            conn.commit()
    except Exception as e:
        print(f"logging_config: failed to prune old log rows: {e}")


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
```

`_RETENTION_DAYS` is interpolated directly into the DDL string (not a bind
parameter) because Postgres's `interval` literal syntax doesn't accept a
parameterized number directly in `interval '%s days'` — this value is an
internal constant, never user input, so this isn't a SQL-injection concern
the way any of the parameterized queries elsewhere in this codebase are.

`_log_queue`/`_stop_event` are module-level singletons rather than
attributes on some object — there's exactly one process-wide log queue,
matching how `_APP_LOGGERS` already works in this file.

Overflow behavior: `logging.handlers.QueueHandler`'s default `enqueue()`
uses `put_nowait`, so a full queue raises `queue.Full` inside `emit()`,
caught by the stdlib `Handler.handleError()` (prints to stderr, doesn't
propagate). Filling 5000 queued records between one 1-second flush is far
beyond this app's actual log volume, so this is left as the stdlib default
rather than adding custom drop-and-count bookkeeping for a case that isn't
expected to occur.

`backend/tests/test_logging_config.py` — replace entirely:

```python
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
```

Run: `pytest tests/test_logging_config.py -v` — expect all five tests to
pass.

## Task 4 — Wire the writer thread into `main.py` startup/shutdown

`backend/main.py`:
- Change the import line:
  ```python
  from logging_config import setup_logging, get_logger, start_log_writer, stop_log_writer
  ```
- In `startup()`, immediately after `init_global_schema()` (so the table
  exists before the first flush) and before `migrate_legacy_config_file()`:
  ```python
      init_global_schema()
      start_log_writer()
      # Immediately after the CREATE TABLE that gives it somewhere to write, and
  ```
  (the existing comment on the next line, about `migrate_legacy_config_file`
  running before anything reads `load_config()`, stays where it is —
  unrelated to this change, just now one line further down).
- In `shutdown()`, as the last line, after `scheduler.shutdown()`:
  ```python
      scheduler.shutdown()
      stop_log_writer()
  ```
  Last, not first, so shutdown's own log lines (worker pool stopping,
  scheduler stopping) still get flushed before the writer thread stops.

`backend/tests/test_main.py` — add, near
`test_shutdown_cancels_the_schedule_resync_task`:

```python
def test_startup_starts_log_writer_and_shutdown_stops_it(pg_test_db):
    import logging_config
    import main

    with patch("main.crawl_manager.start_worker_pool", new=AsyncMock()), \
         patch("main.crawl_manager.stop_worker_pool", new=AsyncMock()), \
         patch("main.migrate_legacy_config_file"):
        with TestClient(main.app):
            assert logging_config._writer_thread is not None
            assert logging_config._writer_thread.is_alive()
    assert logging_config._writer_thread is None
```

Run: `pytest tests/test_main.py -v` — expect the new test, and every
existing test in the file, to pass.

## Task 5 — `routers/logs.py`: Postgres-backed read path

`backend/routers/logs.py` — replace entirely:

```python
import asyncio
import json
from typing import Optional
from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool
import db
from screenshots import clear_screenshots

router = APIRouter()

_SUPPORTED_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_HISTORY_ROWS = 100
_POLL_INTERVAL_SECONDS = 0.5


def _parse_levels(levels: Optional[str]) -> Optional[set]:
    """Parse a comma-separated levels query param into an uppercase set.

    Unknown values are ignored. Returns None when no usable level is requested
    (meaning: show every level) rather than an empty, everything-filtered set.
    """
    if not levels:
        return None
    wanted = {part.strip().upper() for part in levels.split(",") if part.strip()}
    wanted &= _SUPPORTED_LEVELS
    return wanted or None


def _fetch_history(levels: Optional[set]) -> list:
    level_list = list(levels) if levels else None
    with db.get_admin_pool().connection() as conn:
        rows = conn.execute(
            "SELECT id, ts, level, logger_name, machine_id, message FROM app_logs "
            "WHERE (%(levels)s::text[] IS NULL OR level = ANY(%(levels)s)) "
            "ORDER BY id DESC LIMIT %(limit)s",
            {"levels": level_list, "limit": _HISTORY_ROWS},
        ).fetchall()
    return list(reversed(rows))


def _fetch_new(last_id: int, levels: Optional[set]) -> list:
    level_list = list(levels) if levels else None
    with db.get_admin_pool().connection() as conn:
        return conn.execute(
            "SELECT id, ts, level, logger_name, machine_id, message FROM app_logs "
            "WHERE id > %(last_id)s AND (%(levels)s::text[] IS NULL OR level = ANY(%(levels)s)) "
            "ORDER BY id",
            {"last_id": last_id, "levels": level_list},
        ).fetchall()


def _row_to_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "time": row["ts"].strftime("%Y-%m-%d %H:%M:%S"),
        "level": row["level"],
        "logger": row["logger_name"],
        "message": row["message"],
        "machine": row["machine_id"],
    }


@router.delete("/logs")
def clear_logs():
    with db.get_admin_pool().connection() as conn:
        conn.execute("DELETE FROM app_logs")
        conn.commit()
    clear_screenshots()
    return {"ok": True}


@router.get("/logs/stream")
async def logs_stream(levels: Optional[str] = Query(None)):
    wanted = _parse_levels(levels)

    async def generate():
        history = await run_in_threadpool(_fetch_history, wanted)
        last_id = 0
        for row in history:
            last_id = row["id"]
            yield {"data": json.dumps(_row_to_payload(row))}

        while True:
            new_rows = await run_in_threadpool(_fetch_new, last_id, wanted)
            for row in new_rows:
                last_id = row["id"]
                yield {"data": json.dumps(_row_to_payload(row))}
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    return EventSourceResponse(generate())
```

Dropped from the old version: `LOG_FILE`, `_LEVEL_RE`, `_line_visible` —
level filtering is now an exact SQL `WHERE level = ANY(...)` instead of a
regex heuristic over formatted text, since every row now carries a real
level (no more "unparseable continuation line" case — see the spec's
"Schema" section on multi-line tracebacks becoming one row).

`backend/tests/test_logs_router.py` — replace entirely:

```python
import db
from routers.logs import _parse_levels, _fetch_history, _fetch_new, _row_to_payload, clear_logs


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
```

Fix before running: `test_row_to_payload_formats_timestamp` doesn't depend
on `clean_app_logs_table`, so it leaks a row into whatever test runs after
it in the same session (and `test_row_to_payload_formats_timestamp_cleanup`
above is a leftover placeholder, not a real test — delete it). Rewrite
`test_row_to_payload_formats_timestamp` to take `clean_app_logs_table` as a
fixture parameter like every other test in this file, and delete the
`_cleanup` stub entirely.

Run: `pytest tests/test_logs_router.py -v` — expect every test to pass.

## Task 6 — Frontend: `LogViewer.tsx`

`frontend/src/views/LogViewer.tsx`:
- Drop `LOG_RE` and `parseLine` — the SSE payload is now structured JSON,
  not one flattened text line to re-parse.
- `LogEntry` gains a `machine` field and drops `raw` (it was only ever
  populated for use by the old regex-parsing fallback; `key={e.id}` is
  already what React uses to key each row, and nothing else in this file
  reads `e.raw`):
  ```typescript
  interface LogEntry {
    id: number
    time: string
    level: Level
    logger: string
    message: string
    machine: string
    screenshotPath?: string
  }
  ```
- Replace `parseLine` with a function that reads the structured payload
  directly:
  ```typescript
  const KNOWN_LEVELS: Level[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR']

  function parsePayload(data: any): LogEntry {
    let message: string = data.message ?? ''
    let screenshotPath: string | undefined
    const sm = message.match(SCREENSHOT_RE)
    if (sm) {
      screenshotPath = sm[1]
      message = message.slice(0, message.length - sm[0].length)
    }
    const level = (KNOWN_LEVELS as string[]).includes(data.level) ? (data.level as Level) : 'OTHER'
    return {
      id: data.id,
      time: data.time ?? '',
      level,
      logger: data.logger ?? '',
      message,
      machine: data.machine ?? '',
      screenshotPath,
    }
  }
  ```
- In the SSE `onmessage` handler, replace `const { line } = JSON.parse(e.data); if (!line) return; ... parseLine(line, idRef.current++)` with:
  ```typescript
  source.onmessage = (e) => {
    const data = JSON.parse(e.data)
    setEntries((prev) => {
      const entry = parsePayload(data)
      const next = [...prev, entry]
      return next.length > 2000 ? next.slice(-2000) : next
    })
  }
  ```
  Drop `idRef` entirely (`id` now comes from the backend's real `app_logs.id`,
  stable and already unique — no local counter needed).
- Add a "Machine" column, matching the existing Logger column's width/style,
  positioned right after Logger:
  ```tsx
  {/* Column headers */}
  <div className="flex gap-0 px-4 py-1 border-b border-gray-800 text-gray-600 select-none">
    <span className="w-36 shrink-0">Time</span>
    <span className="w-16 shrink-0">Level</span>
    <span className="w-28 shrink-0">Logger</span>
    <span className="w-24 shrink-0">Machine</span>
    <span>Message</span>
  </div>
  ```
  and in the row rendering, right after the Logger `<span>`:
  ```tsx
  <span className="w-28 shrink-0 text-gray-500 truncate">{e.logger}</span>
  <span className="w-24 shrink-0 text-gray-600 truncate">{e.machine}</span>
  <span className={`flex-1 break-all text-left ${LEVEL_COLORS[e.level]}`}>
  ```
- Multi-line messages (a traceback is now one row): add
  `whitespace-pre-wrap` to the message `<span>`'s className, alongside the
  existing `break-all`.

Verify manually: `cd frontend && npm run dev`, open Settings → Logs, trigger
a log line (e.g. any API call), confirm a Machine column appears and the
row renders correctly. Not part of this task's automated tests — full flow
verification happens after Task 7's tests, before merging.

## Task 7 — Frontend tests

`frontend/src/test/logParsing.test.ts` — delete this file. It tests
`LOG_RE`/`parseLine`, both removed in Task 6; there's no more text-line
parsing logic in this codebase to test (the backend now sends structured
JSON, and `JSON.parse` needs no dedicated test).

`frontend/src/test/LogViewer.test.tsx` — replace entirely:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import LogViewer from '../views/LogViewer'
import { openLogsStream } from '../api/client'

class MockEventSource {
  static instance: MockEventSource | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()

  constructor() {
    MockEventSource.instance = this
  }

  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
  }
}

vi.mock('../api/client', () => ({
  openLogsStream: vi.fn(() => new MockEventSource()),
  screenshotUrl: (path: string) => `/api/screenshots/${path}`,
  clearLogs: vi.fn(),
}))

let nextId = 1
function emitEntry(overrides: Partial<{ time: string; level: string; logger: string; message: string; machine: string }>) {
  act(() => {
    MockEventSource.instance?.emit({
      id: nextId++,
      time: '2026-06-27 15:30:32',
      level: 'INFO',
      logger: 'main',
      machine: 'fdca1234',
      message: 'default message',
      ...overrides,
    })
  })
}

beforeEach(() => {
  MockEventSource.instance = null
  nextId = 1
  ;(openLogsStream as any).mockClear()
})
afterEach(() => { vi.restoreAllMocks() })

describe('LogViewer', () => {
  it('renders with empty state initially', () => {
    render(<LogViewer />)
    expect(screen.getByText(/No log entries/i)).toBeInTheDocument()
  })

  it('displays a structured INFO log entry, including its machine tag', () => {
    render(<LogViewer />)
    emitEntry({ level: 'INFO', logger: 'main', machine: 'fdca1234', message: 'Discogs Browser started' })
    expect(screen.getByText('Discogs Browser started')).toBeInTheDocument()
    expect(screen.getAllByText('INFO').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('main')).toBeInTheDocument()
    expect(screen.getByText('fdca1234')).toBeInTheDocument()
  })

  it('displays an ERROR entry', () => {
    render(<LogViewer />)
    emitEntry({ level: 'ERROR', logger: 'routers.crawl', message: 'Something broke' })
    expect(screen.getByText('Something broke')).toBeInTheDocument()
    expect(screen.getAllByText('ERROR').length).toBeGreaterThanOrEqual(2)
  })

  it('hides INFO lines when INFO toggle is off', () => {
    render(<LogViewer />)
    emitEntry({ level: 'INFO', message: 'Hello world' })
    expect(screen.getByText('Hello world')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'INFO' }))
    expect(screen.queryByText('Hello world')).not.toBeInTheDocument()
  })

  it('filters by message regexp', () => {
    render(<LogViewer />)
    emitEntry({ message: 'Collection refresh started' })
    emitEntry({ message: 'Crawler loaded successfully' })

    const input = screen.getByPlaceholderText(/Filter message/i)
    fireEvent.change(input, { target: { value: 'refresh' } })

    expect(screen.getByText('Collection refresh started')).toBeInTheDocument()
    expect(screen.queryByText('Crawler loaded successfully')).not.toBeInTheDocument()
  })

  it('shows a regex error indicator for invalid regexp', () => {
    render(<LogViewer />)
    const input = screen.getByPlaceholderText(/Filter message/i)
    fireEvent.change(input, { target: { value: '[invalid' } })
    expect(input).toHaveClass('border-red-500')
  })

  it('clears all entries when Clear is clicked', () => {
    render(<LogViewer />)
    emitEntry({ message: 'Something happened' })
    expect(screen.getByText('Something happened')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.queryByText('Something happened')).not.toBeInTheDocument()
    expect(screen.getByText(/No log entries/i)).toBeInTheDocument()
  })

  it('shows line count', () => {
    render(<LogViewer />)
    emitEntry({ message: 'Line one' })
    emitEntry({ message: 'Line two' })
    expect(screen.getByText('2 lines')).toBeInTheDocument()
  })

  it('closes EventSource on unmount', () => {
    const { unmount } = render(<LogViewer />)
    const source = MockEventSource.instance!
    unmount()
    expect(source.close).toHaveBeenCalled()
  })

  it('shows DEBUG lines only when DEBUG toggle is enabled', () => {
    render(<LogViewer />)
    emitEntry({ level: 'DEBUG', message: 'debug detail' })
    expect(screen.queryByText('debug detail')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'DEBUG' }))
    emitEntry({ level: 'DEBUG', message: 'debug after enable' })
    expect(screen.getByText('debug after enable')).toBeInTheDocument()
  })

  it('opens the stream with the default visible levels (DEBUG excluded)', () => {
    render(<LogViewer />)
    const levels = (openLogsStream as any).mock.calls.at(-1)[0]
    expect(new Set(levels)).toEqual(new Set(['INFO', 'WARNING', 'ERROR']))
  })

  it('reconnects with the updated levels when a toggle changes', async () => {
    render(<LogViewer />)
    fireEvent.click(screen.getByRole('button', { name: 'DEBUG' }))
    await waitFor(() => {
      const levels = (openLogsStream as any).mock.calls.at(-1)[0]
      expect(new Set(levels)).toEqual(new Set(['DEBUG', 'INFO', 'WARNING', 'ERROR']))
    })
  })

  it('renders a multi-line message with its line breaks intact', () => {
    render(<LogViewer />)
    emitEntry({ level: 'ERROR', message: 'caught something\nTraceback (most recent call last):\n  File "x.py", line 1' })
    const cell = screen.getByText(/caught something/)
    expect(cell.className).toContain('whitespace-pre-wrap')
  })
})
```

Run: `cd frontend && npm test -- LogViewer` — expect every test to pass.
Then run the full frontend suite once
(`npm test`) to confirm deleting `logParsing.test.ts` didn't leave a dangling
reference anywhere.

## Task 8 — Docs

`CLAUDE.md`, "Data directory" section (`CLAUDE.md:52-59`): remove the
`app.log` line from the tree listing and reword the leading sentence:

```
App settings, avatars, catalog, listings, logs, and per-user data live in
Postgres (see `DATABASE_URL` below). Local filesystem state under
`DISCOGS_BROWSER_DATA` (default `~/.discogs-browser/`) is now limited to:

```
~/.discogs-browser/
├── crawlers/            # bundled crawler plugins only — no runtime plugin loading from user-writable paths in the hosted deployment
└── screenshots/         # debug screenshots, YYYYMMDD_HHMMSS/
```
```

Before opening the PR, run this repo's required "Pre-PR spec-drift check"
(`CLAUDE.md`'s own section by that name) across the full diff — this task
already covers the one drift found ahead of time (the "Data directory"
section above), but re-run the grep pass across both spec trees regardless,
per that section's own instructions, since it explicitly applies "even when
the current change itself has no spec/plan of its own" gaps beyond what was
anticipated here.

## Task 9 — `docker-compose.yml` local verification

Not a code change — a manual check before merging, since this plan's spec
was explicitly scoped to also work correctly under local `docker-compose`
(see the spec's Goals: machine-id resolution must behave sensibly there).

Run:
```bash
docker compose up --build
```
Open the app, generate some log activity (load the Settings → Logs tab,
trigger a crawl or a few API calls), and confirm:
- Log lines appear in the Log Viewer, each showing a Machine column value
  (the backend container's hostname, e.g. a short hex string Docker
  assigns — not "local", not empty).
- `docker compose restart backend`, then confirm previously-seen log lines
  are still present after reconnecting the Log Viewer (persistence across a
  restart, per the spec's Goals — this is the behavior that regressed from
  today's file-truncate-on-boot).
- `docker compose down && docker compose up` (full stop/start, not just a
  restart) still shows the same persisted history, confirming this isn't
  relying on anything that only survives a soft restart.
