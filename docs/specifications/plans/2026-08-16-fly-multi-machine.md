# Fly.io multi-machine scaling — implementation plan

Spec: [`docs/specifications/shaping/2026-08-16-fly-multi-machine-design.md`](../shaping/2026-08-16-fly-multi-machine-design.md)

## Task 1 — `app_config` table + `users.avatar_image` column

`backend/db.py`:
- Add to `GLOBAL_SCHEMA` (near `catalog`/`crawlers`):
  ```sql
  CREATE TABLE IF NOT EXISTS app_config (
      id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
      data JSONB NOT NULL DEFAULT '{}'::jsonb
  );
  ```
- Add to `TENANT_SCHEMA`, after the `users` table definition (matching how
  other post-hoc `users` columns were added, e.g. `recommendation_item_limit`):
  ```sql
  ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_image BYTEA;
  ```

No new RLS policy needed — `avatar_image` is a column on the already-RLS'd
`users` table.

## Task 2 — `config.py`: `load_config`/`save_config` → Postgres

`backend/config.py`:
- Remove `CONFIG_FILE` (no longer used).
- Replace the file-based bodies of `load_config()`/`save_config()`:
  ```python
  def load_config() -> dict:
      import db
      with db.get_admin_pool().connection() as conn:
          row = conn.execute("SELECT data FROM app_config WHERE id = TRUE").fetchone()
      return row["data"] if row else {}


  def save_config(data: dict):
      import db
      with db.get_admin_pool().connection() as conn:
          conn.execute(
              "INSERT INTO app_config (id, data) VALUES (TRUE, %s) "
              "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
              [Json(data)],
          )
          conn.commit()
  ```
  (`from psycopg.types.json import Json` — confirm psycopg's actual import
  path in this codebase before writing; `db.py` already imports from
  `psycopg`.) Function-local `import db` is required, not a style choice —
  see the spec's "circular import" note.
- Every existing caller (`crawl_manager.py`, `main.py`, `discover.py`,
  `shopify_catalog.py`, `routers/settings.py`, and the crawler plugins that
  call `load_config()`) needs no changes; signature and dict shape are
  identical.

## Task 3 — `avatar.py`: per-user, Postgres-backed

`backend/avatar.py`:
- Drop `AVATAR_FILE`.
- `save_avatar(user_id: int, data: bytes) -> None` — keep the existing
  validate/EXIF-transpose/crop/resize logic, then:
  ```python
  import db
  buf = io.BytesIO()
  image.save(buf, format="PNG")
  with db.get_identity_pool().connection() as conn:
      conn.execute("UPDATE users SET avatar_image = %s WHERE id = %s", [buf.getvalue(), user_id])
      conn.commit()
  ```
- `get_avatar(user_id: int) -> Optional[bytes]` (new function, replacing
  callers' direct `AVATAR_FILE.exists()` check):
  ```python
  def get_avatar(user_id: int) -> Optional[bytes]:
      with db.get_identity_pool().connection() as conn:
          row = conn.execute("SELECT avatar_image FROM users WHERE id = %s", [user_id]).fetchone()
      return bytes(row["avatar_image"]) if row and row["avatar_image"] is not None else None
  ```
- `delete_avatar(user_id: int) -> None`:
  ```python
  def delete_avatar(user_id: int) -> None:
      with db.get_identity_pool().connection() as conn:
          conn.execute("UPDATE users SET avatar_image = NULL WHERE id = %s", [user_id])
          conn.commit()
  ```
- Module-level `import db` is fine here (unlike `config.py`, `avatar.py` has
  no existing reverse dependency from `db.py`) — confirm no cycle before
  committing to that; fall back to a function-local import if there is one.

## Task 4 — `routers/session.py`: avatar routes take `user_id`

```python
@router.post("/auth/avatar")
async def upload_avatar(file: UploadFile = File(...), request: Request = None):
    data = await file.read(avatar_storage.MAX_UPLOAD_BYTES + 1)
    try:
        avatar_storage.save_avatar(request.state.user_id, data)
    except avatar_storage.InvalidAvatarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.get("/auth/avatar")
def get_avatar(request: Request):
    data = avatar_storage.get_avatar(request.state.user_id)
    if data is None:
        raise HTTPException(status_code=404)
    return Response(content=data, media_type="image/png")


@router.delete("/auth/avatar")
def remove_avatar(request: Request):
    avatar_storage.delete_avatar(request.state.user_id)
    return {"ok": True}
```
Drop the `FileResponse` import if it becomes unused; add `Response` from
`fastapi`. Match the existing `Request` parameter ordering/style already used
elsewhere in this router file (see `get_user_settings`) rather than the
placeholder shown here.

## Task 5 — Tests

`backend/tests/test_config.py`: `load_config`/`save_config` tests need the
Postgres test DB (`pg_test_db` fixture, per `test_avatar_router.py`'s
pattern) instead of `tmp_config_dir`, plus `db.init_global_schema()` so
`app_config` exists. `tests/conftest.py`'s `tmp_config_dir` fixture
(`conftest.py:239`) becomes dead — remove it once nothing references it.

`backend/tests/test_avatar.py`: rewrite the `avatar_file` fixture into
something that gives each test a `user_id` backed by the Postgres test DB
(create a user via `db.create_user`, matching `test_avatar_router.py`'s
`_login` helper), and change every `avatar.save_avatar(...)` /
`avatar.delete_avatar()` call to pass that `user_id`. Assertions switch from
"file exists / pixel via `Image.open(path)`" to "`avatar.get_avatar(user_id)`
returns the expected bytes / `None`".

`backend/tests/test_avatar_router.py`: drop the
`monkeypatch.setattr(avatar, "AVATAR_FILE", ...)` line in the `client`
fixture (Task 3 removes that attribute); no other change expected since this
file already talks to the router purely over HTTP with a real Postgres-backed
user.

## Task 6 — `fly.toml`

```toml
[http_service]
  ...
  min_machines_running = 2
```

## Task 7 — Docs

- This repo's `CLAUDE.md`, "Data directory" section: drop `config.json` and
  `avatar.png` from the tree listing; update the leading sentence to say
  settings and avatars live in Postgres too.
- Run the pre-PR spec-drift check (`CLAUDE.md`'s "Pre-PR spec-drift check"
  section) across the full diff before opening the PR — this plan already
  covers the one drift found ahead of time (the original deployment spec's
  non-goal and its two related bullets), but re-run the grep pass regardless.

## Task 8 — Schedule convergence (periodic re-sync)

Added after the final whole-branch review found that moving the cron value
into `app_config` doesn't make an already-booted Machine's scheduler notice
a change another Machine wrote.

`backend/main.py`:
- `_configure_schedules(cfg)`: drop the `if schedule:` / `if stock_schedule:`
  guards around the `scheduler.configure(...)`/`configure_stock(...)` calls
  — call them unconditionally. `scheduler.configure()`/`configure_stock()`
  already handle an empty string correctly (remove any existing job, log,
  return), so the guard was actively wrong: it left a cleared schedule's job
  running on any Machine that didn't handle the clearing request.
- Add a periodic background task, started in `startup()` alongside
  `crawl_manager.start_worker_pool()` and cancelled in `shutdown()`
  alongside `crawl_manager.stop_worker_pool()`: every 5 minutes, call
  `_configure_schedules(load_config())` again. Track the `asyncio.Task` in a
  module-level variable so `shutdown()` can cancel and await it.

## Task 9 — Stock sync mutual exclusion (Postgres advisory lock)

Added after the final whole-branch review found `CrawlManager.stock_sync_running`
only guards the same process — with Task 8 making both Machines converge on
the same cron, simultaneous stock syncs become the common case, and
`_sync_stock`'s `DELETE FROM stock_items WHERE crawler_id = %s` + reinsert is
not safe to interleave across processes.

`backend/crawl_manager.py`:
- `start_stock_sync()`: before creating the task, open a dedicated
  connection (`psycopg.connect(config.APP_DATABASE_URL)` — deliberately not
  from `db.get_app_pool()`, since a pooled connection is reused for
  unrelated work later and would silently carry the lock's session state
  with it) and attempt `SELECT pg_try_advisory_lock(<key>)` on it. Use a
  fixed bigint key following `db.py`'s existing `pg_advisory_xact_lock(2026080901)`
  convention (date + sequence, not text). If the lock isn't obtained, close
  the connection and return `False` — same external behavior as today's
  `if self.stock_sync_running: return False` guard, now cross-process too.
  If obtained, pass the connection into `_sync_stock(crawler_id, lock_conn)`.
- `_sync_stock()`: close `lock_conn` in a `finally` block when the sync ends
  (success or failure) — closing the session releases the advisory lock
  automatically, no explicit `pg_advisory_unlock` needed.

## Task 10 — Findings from the final review: tests and docs

- `backend/tests/conftest.py`: `app_config`'s row is reset in
  `tmp_config_dir`'s setup but nothing truncates/resets it afterward, and no
  other fixture's teardown touches it (it has no FK, so the existing
  `TRUNCATE ... CASCADE` calls miss it). Add it to whichever teardown already
  truncates other global-schema tables, so state can't leak into a test that
  doesn't request `tmp_config_dir`.
- `backend/tests/test_config.py`: `test_load_config_missing_returns_empty`
  currently exercises "empty row returns `{}`" (the fixture inserts an empty
  row first), not "no row at all returns `{}`" — the actual `row is None`
  branch in `config.load_config()`. Delete the row explicitly before
  asserting, to cover the real fresh-database path.
- `backend/tests/test_avatar_router.py`: add a test asserting per-user
  isolation — user A uploads an avatar, user B's `GET /auth/avatar` still
  404s (not user A's bytes). This is the actual behavior change this branch
  makes (avatar was previously one shared file even in a multi-tenant app);
  nothing currently tests it with two distinct users.
- `backend/routers/session.py`'s `GET /auth/avatar`: add
  `Cache-Control: private` to the `Response` — it's per-user content served
  from a single URL behind a shared CDN/proxy (Cloudflare), and `FileResponse`
  (the code this replaced) sent caching validators `Response` doesn't.
- This repo's `CLAUDE.md`, "Tests" section: the bullet claiming "Tests that
  don't touch Postgres run with no database at all... only the
  Postgres-backed files fail" is now inaccurate — `tmp_config_dir` (Task 5)
  now depends on `pg_test_db`, so every test requesting it
  (`test_config.py`, `test_logging_config.py`, and four crawler test files)
  requires `TEST_DATABASE_URL` even though most of what they test has
  nothing to do with Postgres. Update the bullet to say so.

## Manual follow-up (not part of this PR)

Note in the PR description, not executed by this plan: after merge, the
operator runs the "Manual one-time Fly setup" section from the spec by hand,
once, with their own Fly auth and `DATABASE_URL` — migrating the existing
`config.json` content into `app_config` (step 1) *before or immediately
after* deploying (skipping it silently stops both cron schedules and can
wipe eBay listing prices — see the spec's "Existing `config.json`" section),
then provisioning the second Machine and volume (step 2).
