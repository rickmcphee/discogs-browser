# Unified log store across Fly Machines

## Problem

[`2026-08-16-fly-multi-machine-design.md`](2026-08-16-fly-multi-machine-design.md)
moved `config.json` and `avatar.png` into Postgres so both Fly Machines see
consistent state, but explicitly left `app.log` as machine-local: "a log
forking across Machines is just what server logs do." In practice this
means the in-app Log Viewer (`GET /api/logs/stream`, `backend/routers/logs.py`)
only ever shows the log of whichever Machine Fly's proxy happened to route
that SSE connection to — not a complete picture, and unhelpful when
investigating a specific user's report, since half the activity may have
happened on the other Machine.

Now that the app has invited users beyond the operator, being able to look
back further than "whatever's currently in the local file" also matters:
tracking down what a specific user experienced over the past weeks needs
retention, not just a live tail.

This design supersedes that prior spec's `app.log` Non-goal.

## Goals

- The Log Viewer shows one merged, correctly time-ordered stream regardless
  of which Machine's SSE connection the browser lands on.
- Log history persists across Machine restarts/redeploys, not just within a
  single process's lifetime (today's `setup_logging()` truncates `app.log`
  on every boot).
- Retain 30 days of history, to support investigating user-reported issues
  after the fact.
- Machine identity is visible per log line, and resolved generically — not
  hardcoded to Fly's `FLY_MACHINE_ID` — so this isn't broken if the
  deployment target changes, and behaves sensibly under local `docker-compose`
  or bare `uvicorn --reload` too.
- No regression in today's functionality: level filtering, message
  regex filtering, pause/resume, screenshot links, "Clear Logs".

## Non-goals

- Browsing/searching the full 30-day history from the Log Viewer UI itself
  (date range pickers, search-within-history). The UI stays a live-tail-plus
  recent-history view, same shape as today. The 30-day retention exists so
  the data is available via a direct Postgres query when investigating a
  specific report — not as a UI feature.
- Local multi-instance testing infrastructure (e.g. `docker-compose` scaled
  to multiple `backend` replicas). Machine-id resolution is made generic
  enough to behave correctly if that's ever set up, but standing it up is
  not part of this change.
- Unifying with `fly logs`/the Fly dashboard. The console handler (stdout)
  is untouched by this change and remains what Fly's own log aggregation
  reads.
- Cross-Machine fan-out for *crawl* SSE events (`CrawlManager`). Unrelated
  system; still an accepted gap per the prior spec's Non-goals.

## Design

### `app_logs` (global, replaces the local `app.log` file)

A new table in the global (non-tenant, non-RLS) schema, added to
`db.py`'s `init_global_schema()` alongside `catalog`/`crawlers`/`app_config`:

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

One row per `LogRecord` (not per raw text line) — a multi-line traceback
from `logger.exception(...)` becomes a single row carrying its real level,
rather than today's file-tail behaviour where continuation lines fail the
level-detection regex and are shown regardless of the active filter.

### Machine identity

```python
MACHINE_ID = os.environ.get("FLY_MACHINE_ID") or socket.gethostname()
```

`FLY_MACHINE_ID` is checked first only because it's the more precise,
documented identifier when present (set automatically by Fly). Nothing else
in this design depends on being on Fly specifically: `socket.gethostname()`
resolves to the container's own hostname under Docker (unique per container
automatically), and to the host machine's hostname for bare local dev — both
meaningful without any Fly-specific assumption or extra configuration.

### Write path (non-blocking)

`logging.Handler.emit()` runs synchronously wherever `logger.info(...)` etc.
is called, including from async request handlers — a blocking Postgres
INSERT per call would stall the event loop. Standard stdlib-native fix:

- `logging_config.setup_logging()` attaches a `logging.handlers.QueueHandler`
  (bounded `queue.Queue`, e.g. maxsize 5000) to the root logger, with the
  same `_AppOnlyFilter` used today. Enqueueing is non-blocking; on overflow,
  records are dropped and counted rather than blocking the caller. This
  handler is attached at module-import time (as `setup_logging()` already
  is, called from `main.py` before `app = FastAPI(...)`), so nothing emitted
  during import is lost — it just waits in the queue.
- The existing console `StreamHandler` (stdout, same `_AppOnlyFilter`) is
  unchanged — still what `fly logs`/Docker's own log capture reads.
- A dedicated background `threading.Thread` (daemon) drains the queue and
  batches records into one `executemany` INSERT via `db.get_admin_pool()`,
  flushing every ~1s or N records, whichever comes first. Started from
  `main.py`'s `startup()` **after** `init_global_schema()` runs, so the
  table is guaranteed to exist before the first flush; stopped via a stop
  `Event` and joined (with a short timeout) in `shutdown()`. This mirrors
  the existing periodic-background-task pattern already used for
  `crawl_manager`'s worker pool and the schedule-resync task.
- `flush_queue()` is a separate, directly callable function doing one
  drain-and-insert pass — this is what the background thread loop calls
  repeatedly, and what tests call directly instead of sleeping/polling for
  a background thread to do its work.
- On DB error: caught, printed to stderr, batch dropped, loop continues.
  Never raises into the logging call site, never crashes the app. Records
  emitted between process start and `startup()` completing `init_global_schema()`
  are, as a consequence, not persisted to Postgres (the writer thread
  hasn't started yet) — still visible via stdout/`fly logs`. This mirrors
  today's situation, where the file-based log's own history is equally
  irrelevant until the app is fully up.
- Same thread checks elapsed time since last prune (roughly hourly) and, if
  due, runs `DELETE FROM app_logs WHERE ts < now() - interval '30 days'`.

### Read path

`GET /api/logs/stream` (`backend/routers/logs.py`) drops file tailing
entirely:

- On connect: `SELECT id, ts, level, logger_name, machine_id, message FROM
  app_logs WHERE level filter ORDER BY id DESC LIMIT 100`, reversed to
  chronological order, sent as the history seed (replaces today's
  `_HISTORY_LINES = 100` file-tail seed).
- Then polls every 0.5s (same cadence as today's file tail) for
  `id > last_seen_id` matching the level filter, ordered by `id`.
- Level filtering becomes an exact SQL `WHERE level IN (...)` instead of the
  current regex-based `_line_visible` heuristic — every row now carries a
  real level, so the "unparseable lines always pass through" special case
  is no longer needed.
- Each row is sent as structured JSON (`id`, `time`, `level`, `logger`,
  `message`, `machine`) instead of one flattened formatted-text line.
  `time` is formatted server-side as `%Y-%m-%d %H:%M:%S` to match today's
  displayed format exactly.
- `DELETE /api/logs` becomes `DELETE FROM app_logs` (no WHERE — matches
  today's "clear everything" behavior), still followed by
  `clear_screenshots()`, unchanged.

### Frontend (`LogViewer.tsx`)

- `LogEntry` is built directly from the structured SSE payload; `LOG_RE`
  regex parsing is removed (the backend already has these as columns —
  flattening to text and re-parsing it back out is no longer necessary now
  that the source is structured rather than a raw tailed file).
- New "Machine" column, positioned next to the existing Logger column,
  showing `machine_id` truncated the same way Logger is today.
- `message` renders with `white-space: pre-wrap` so a multi-line traceback
  (now one row, per the schema note above) displays with its original line
  breaks intact, as a single logical entry instead of several separate rows.
- `SCREENSHOT_RE` marker parsing and URL auto-linking (`renderMessage`) are
  unchanged — both operate on the `message` text regardless of how it got
  there.
- `logParsing.test.ts` and `LogViewer.test.tsx` are rewritten against the
  new structured payload shape.

### Testing

`app_logs` writes/reads are tested using this repo's existing
`TEST_DATABASE_URL`/`pg_run_database` fixture pattern (same one
`test_config.py` already relies on, per this repo's `CLAUDE.md` "Tests"
section). `test_logging_config.py`'s current file-based assertions
(`config.CONFIG_DIR / "app.log"`) are replaced with `flush_queue()` calls
followed by a direct `SELECT` against `app_logs` in the test database —
deterministic, no sleep/poll needed since `flush_queue()` is synchronous.

### Rollout

No backfill of existing local `app.log` content on either Machine — it's
debug scrollback, not data worth migrating. `app.log` and its
`RotatingFileHandler` are removed outright (no dual-write period, no
backwards-compat shim): nothing else in the codebase reads the file
(confirmed — only `logging_config.py` and `routers/logs.py` reference it).

## Amendments (2026-08-18, final whole-branch review)

- **Both `/logs` routes are admin-only.** `GET /api/logs/stream` and
  `DELETE /api/logs` carry `dependencies=[Depends(require_admin)]`, matching
  `routers/settings.py`. The read path is the operator's whole application
  log and the delete path wipes 30 days of shared history for everyone; neither
  is per-user data. `App.tsx` also gates the `<LogViewer />` *mount* (not just
  the nav button) on `showAdminNav`, since the component opens its SSE stream
  from a mount effect regardless of whether its container is CSS-hidden.
- **The history seed is ordered by `ts`, not `id`.** `ORDER BY id DESC LIMIT
  100` stays as an inner query (cheap, and it correctly picks the last N rows),
  wrapped in an outer `ORDER BY ts, id`. Across Machines, id order is not time
  order — each Machine's ~1s batch lands as one contiguous id block, so an
  id-ordered seed renders a visibly zig-zagging `time` column. The live poll is
  still `id`-ordered; `id` is a cursor there, not a sort key. When the seed
  comes back empty (a level filter matching nothing yet), the cursor is seeded
  from `max(id)` rather than left at 0, so the poll doesn't scan the whole
  table twice a second until the first matching row appears.
- **The first prune happens at writer start, not an hour in.** `last_prune` is
  seeded a full interval in the past. Seeding it with "now" meant retention only
  ever ran after an hour of uptime, and every rolling deploy or restart reset
  that clock.
- **The writer loop body is wrapped in `try/except Exception`.** `flush_queue()`
  guards only its DB block; anything else in the loop raising would unwind the
  `while`, kill the daemon thread unnoticed (`_writer_thread` stays non-None, so
  `stop_log_writer()`'s `join()` returns instantly), and leave the queue to fill
  to `_QUEUE_MAXSIZE` and then throw `queue.Full` on every subsequent log call.

## Open questions

None.
