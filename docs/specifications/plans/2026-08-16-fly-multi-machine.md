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

## Manual follow-up (not part of this PR)

Note in the PR description, not executed by this plan: after merge, the
operator runs the two `flyctl` commands from the spec's "Manual one-time Fly
setup" section by hand, once, with their own Fly auth.
