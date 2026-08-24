# Test database freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop schema-shape and role-shape tests from passing vacuously against the long-lived local test database. Every `pytest` session provisions its own empty database and starts with the two app roles' attributes deliberately inverted, so `GLOBAL_SCHEMA`, `TENANT_SCHEMA` and `_ensure_role` must actually construct everything they are asserted on.

**Design:** [`docs/specifications/shaping/2026-08-09-test-database-freshness-design.md`](../shaping/2026-08-09-test-database-freshness-design.md)

**Architecture:** One session-scoped autouse fixture in `backend/tests/conftest.py` creates `<base>_run_<8 hex>` with `TEMPLATE template0`, rewrites `os.environ["TEST_DATABASE_URL"]` in place so all nine existing read sites follow with no edits, poisons `app_user`/`app_identity`, and drops the database at teardown. A new `backend/tests/test_pg_fixtures.py` asserts on facts the fixture *measured* (not hard-coded), so a silent revert to a reused database is a failure rather than a return to invisible vacuity.

**Tech Stack:** pytest, psycopg 3 (`psycopg.sql` composition), PostgreSQL 16.

## Global Constraints

- **No `backend/db.py` changes.** `GLOBAL_SCHEMA`, `TENANT_SCHEMA`, `init_global_schema`, `init_tenant_schema` and `_ensure_role` are untouched. Task 4 modifies `db.py` temporarily to prove the guards bite and **reverts before committing** — verify with `git diff backend/db.py` returning empty.
- **No changes to the existing test files that use `pg_test_db`.** `pg_test_db` and the per-file `TRUNCATE` teardowns stay exactly as they are; with a bare database per run they become an intra-run optimization rather than the only cleanup.
- **No changes to `.github/workflows/fly-deploy.yml`.** CI already provisions a fresh database per job; it gains the per-run database for free.
- Role names and passwords go through `sql.Identifier` / `sql.Literal`, never string interpolation — mirroring `_ensure_role` at `backend/db.py:296`.
- Python ≥3.9 syntax (no `str | None`); no comments unless the WHY is non-obvious — per `CLAUDE.md` style notes.
- `backend/version.py`'s `VERSION` takes the automatic **minor** bump on this PR.
- Every commit needs the full AI-attribution trailer block from `CLAUDE.md` (`ai-generated`, `ai-model`, `ai-tool`, `ai-surface`, `ai-executor`), created via `git commit -F <file>`, never `git commit -m`.

**Standard test invocation** used by every Run line below (the suite does not run without all three variables — see Task 6):

```bash
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55433/discogs_browser_test IDENTITY_DB_PASSWORD=ci-test-identity-password APP_DB_PASSWORD=ci-test-app-password pytest -q
```

Referred to below as `$PYTEST`.

**Execution environment — port 55433, not 5432.** This branch is being built while another worktree runs the suite against the shared `discogs-browser-pg` container on 5432. Because `app_user`/`app_identity` are cluster-level, a private *database* name is not enough to isolate from that; the whole cluster has to be separate. So this execution uses a dedicated container:

```bash
docker run -d --name dbtest-freshness -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=discogs_browser_test -p 55433:5432 postgres:16
```

Every `docker exec discogs-browser-pg ...` verification command below therefore reads `docker exec dbtest-freshness ...` instead. This is the same per-worktree-container practice Task 6 documents. Remove the container when the branch is done.

---

### Task 1: Guard test for the per-run database (fails first)

**Files:**
- Create: `backend/tests/test_pg_fixtures.py`

**Interfaces:**
- Consumes: the `pg_run_database` session fixture from Task 2, which does not exist yet — that is why this task fails.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the failing test file**

```python
"""Guards the per-session test-database harness in conftest.py.

Without these, a silent revert to a reused database restores the invisible
vacuity docs/specifications/shaping/2026-08-09-test-database-freshness-design.md
exists to remove: schema-shape tests passing because some earlier run created
the column, not because db.py's schema strings still do.

Every assertion here reads a value the fixture *measured* against the live
database. Asserting a constant the fixture hard-codes would reintroduce
exactly the vacuity being fixed.
"""
import re

import db


def test_session_runs_against_its_own_database(pg_run_database, pg_test_db):
    with db.get_admin_pool().connection() as conn:
        current = conn.execute("SELECT current_database() AS name").fetchone()["name"]
    assert current == pg_run_database["database"]
    assert current != pg_run_database["base_database"]
    assert re.fullmatch(
        re.escape(pg_run_database["base_database"]) + r"_run_[0-9a-f]{8}", current
    )


def test_run_database_started_with_no_tables(pg_run_database):
    assert pg_run_database["tables_at_start"] == 0


def test_app_roles_start_with_inverted_bypassrls(pg_run_database):
    # None means the role was absent from the cluster (a fresh CI cluster):
    # nothing to poison, and the run is already honest.
    assert pg_run_database["app_user_bypassrls_at_start"] in (True, None)
    assert pg_run_database["app_identity_bypassrls_at_start"] in (False, None)
```

- [ ] **Step 2: Confirm it fails for the right reason**

Run: `$PYTEST tests/test_pg_fixtures.py`

Expect three errors reading `fixture 'pg_run_database' not found` — not assertion failures. An assertion failure here means a fixture of that name already exists and this plan is stale.

- [ ] **Step 3: Commit**

`test: add guards for the per-session test database (failing)`

---

### Task 2: Per-run database fixture

**Files:**
- Modify: `backend/tests/conftest.py` — add imports, `_with_database`, and the `pg_run_database` fixture above the existing `pg_test_db` fixture (line 15).

**Interfaces:**
- Consumes: `os.environ["TEST_DATABASE_URL"]`; `db._admin_pool` / `_identity_pool` / `_app_pool` (for teardown), already manipulated the same way by `pg_test_db`.
- Produces: `pg_run_database`, a session-scoped dict with keys `database`, `base_database`, `tables_at_start`, `app_user_bypassrls_at_start`, `app_identity_bypassrls_at_start`. Task 1's tests and Task 3 both consume it. Also rewrites `os.environ["TEST_DATABASE_URL"]` for the whole session, which every existing read site picks up: `conftest.py:19,21,24`, `test_db_pools.py:11`, `test_tenant_schema.py:25,158,198`, `test_rls_isolation.py:26`, `test_crawl_manager.py:46`.

- [ ] **Step 1: Add imports and the DSN helper**

Add to the import block: `import uuid`, `from urllib.parse import urlsplit, urlunsplit`, `import psycopg`, `from psycopg import sql`.

```python
def _with_database(url, dbname):
    """Swap the database name on a DSN, leaving netloc — and therefore the
    userinfo and its percent-encoding — untouched. Mirror image of
    config._with_userinfo (backend/config.py:14), which swaps the other half.
    Kept here rather than in config.py: production has no use for it."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))
```

- [ ] **Step 2: Add the fixture, without poisoning yet**

Poisoning lands in Task 3; return `None` for both role keys here so Task 1's
role case passes trivially in the meantime (`None` is its "role absent" arm)
and Task 3 makes it meaningful.

```python
@pytest.fixture(scope="session", autouse=True)
def pg_run_database():
    """Every pytest session gets its own empty database.

    The old shared-database arrangement made schema-shape tests vacuous: the
    per-test TRUNCATE teardowns drop rows, never columns, and the local
    discogs_browser_test lives in a persistent Docker volume, so a migration
    applied once stayed applied whether or not its ALTER TABLE was still in
    db.py. It also meant two worktrees running pytest at once truncated each
    other's fixture rows mid-test.

    TEMPLATE template0 rather than a hand-recreated schema: a recreated
    `public` schema loses the default `GRANT USAGE ... TO PUBLIC` that
    TENANT_SCHEMA silently relies on, which fails ~88 tests. See
    docs/specifications/shaping/2026-08-09-test-database-freshness-design.md.
    """
    base_url = os.environ["TEST_DATABASE_URL"]
    base_name = urlsplit(base_url).path.lstrip("/")
    run_name = f"{base_name}_run_{uuid.uuid4().hex[:8]}"
    maintenance_url = _with_database(base_url, "postgres")

    with psycopg.connect(maintenance_url, autocommit=True) as conn:
        try:
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(run_name)
                )
            )
        except psycopg.errors.InsufficientPrivilege as exc:
            raise RuntimeError(
                "the role in TEST_DATABASE_URL needs CREATEDB to provision the "
                f"per-run test database {run_name}"
            ) from exc

    run_url = _with_database(base_url, run_name)
    os.environ["TEST_DATABASE_URL"] = run_url
    with psycopg.connect(run_url) as conn:
        tables_at_start = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()[0]

    yield {
        "database": run_name,
        "base_database": base_name,
        "tables_at_start": tables_at_start,
        "app_user_bypassrls_at_start": None,
        "app_identity_bypassrls_at_start": None,
    }

    for attr in ("_admin_pool", "_identity_pool", "_app_pool"):
        pool = getattr(db, attr)
        if pool is not None:
            pool.close()
        setattr(db, attr, None)
    os.environ["TEST_DATABASE_URL"] = base_url
    with psycopg.connect(maintenance_url, autocommit=True) as conn:
        # FORCE so a pool connection that outlived the loop above cannot
        # block the drop and leak the database.
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(run_name)
            )
        )
```

- [ ] **Step 2a: Verify the fixture ordering assumption**

Session-scoped autouse fixtures run before function-scoped ones, so
`pg_run_database` rewrites the environment variable before `pg_test_db`'s body
reads it. Confirm empirically rather than trusting it:

Run: `$PYTEST tests/test_pg_fixtures.py -v`

All three pass. If `test_session_runs_against_its_own_database` reports the
base database name, the ordering assumption is wrong and `pg_test_db` must
take `pg_run_database` as an explicit parameter — make that change before
continuing.

- [ ] **Step 3: Confirm no existing test regressed**

Run: `$PYTEST`

Expect **741 passed** (738 existing + 3 new), and a run time near the 66s
pristine-cluster baseline rather than the 86–147s contaminated figures.

- [ ] **Step 4: Confirm the database is actually dropped**

Run: `docker exec dbtest-freshness psql -U postgres -Atc "SELECT count(*) FROM pg_database WHERE datname LIKE '%\_run\_%'"`

Expect `0`.

- [ ] **Step 5: Commit**

`test: give each pytest session its own database`

---

### Task 3: Role poisoning

**Files:**
- Modify: `backend/tests/conftest.py` — add `_POISONED_BYPASSRLS` and `_poison_app_roles`; call it from `pg_run_database` and normalize `app_user` at teardown.

**Interfaces:**
- Consumes: the maintenance connection already open in `pg_run_database`.
- Produces: real values for `app_user_bypassrls_at_start` / `app_identity_bypassrls_at_start`, which Task 1's third case asserts on.

- [ ] **Step 1: Add the poison helper**

```python
# Inverted relative to what db._ensure_role sets, so every assertion about
# these roles — and every connection made as either — depends on _ensure_role
# running in this process. Roles are cluster-level, so nothing else in the
# suite ever resets them: without this, app_user/app_identity are leftovers
# from whenever the multi-tenant work first ran locally, and
# test_app_identity_role_has_bypassrls passes with the ALTER ROLE deleted.
_POISONED_BYPASSRLS = {"app_user": "BYPASSRLS", "app_identity": "NOBYPASSRLS"}


def _poison_app_roles(conn):
    """Invert the attributes db._ensure_role owns. Returns each role's
    rolbypassrls as actually read back after poisoning, or None for a role
    absent from the cluster (a fresh CI cluster has nothing to poison)."""
    observed = {}
    for role, bypass in _POISONED_BYPASSRLS.items():
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role]).fetchone() is None:
            observed[role] = None
            continue
        conn.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {} {}").format(
                sql.Identifier(role), sql.Literal(uuid.uuid4().hex), sql.SQL(bypass)
            )
        )
        observed[role] = conn.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", [role]
        ).fetchone()[0]
    return observed
```

- [ ] **Step 2: Wire it into the fixture**

In the setup block, after `CREATE DATABASE`: `roles = _poison_app_roles(conn)`. Replace the two `None` literals in the yielded dict with `roles["app_user"]` and `roles["app_identity"]`.

In the teardown block, after the `DROP DATABASE`:

```python
        # A crash between poisoning and _ensure_role's correction would
        # otherwise leave a cluster role holding BYPASSRLS. scripts/
        # drop_leaked_test_dbs.py repairs that case; this covers normal exits.
        if roles["app_user"] is not None:
            conn.execute(sql.SQL("ALTER ROLE {} NOBYPASSRLS").format(sql.Identifier("app_user")))
```

- [ ] **Step 3: Verify**

Run: `$PYTEST`

Expect **741 passed**. Measured blast radius of poisoning is zero — every fixture that connects as an app role calls `init_global_schema()` then `init_tenant_schema()` first — so any failure here is a real order-dependency worth investigating, not an expected cost.

- [ ] **Step 4: Confirm teardown left the cluster clean**

Run: `docker exec dbtest-freshness psql -U postgres -Atc "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname IN ('app_user','app_identity')"`

Expect `app_user|f`.

- [ ] **Step 5: Commit**

`test: poison app role state before each pytest session`

---

### Task 4: Prove the guards bite (verification only — nothing committed)

The point of this change is that deleting code under test turns the suite red. Verify it, the way `test_crawl_manager.py:28-37` documents having done for its RLS repoint. Each step edits tracked files and reverts them; **no commit in this task**.

- [ ] **Step 1: A deleted migration line fails**

Add `ALTER TABLE library_items ADD COLUMN IF NOT EXISTS price_paid TEXT;` to `TENANT_SCHEMA` after line 196, plus a3db7ea's `test_library_items_has_price_paid_column` in `test_tenant_schema.py`. Confirm green. Then delete only the `db.py` line.

Run: `$PYTEST tests/test_tenant_schema.py -k price_paid`

Expect failure. Before this change, this passed with the line deleted — that is the whole defect. Revert both edits.

- [ ] **Step 2: A deleted column in an existing assertion fails**

Delete `ALTER TABLE crawlers ADD COLUMN IF NOT EXISTS requires_discogs_release BOOLEAN NOT NULL DEFAULT FALSE;` (`backend/db.py:81`).

Run: `$PYTEST tests/test_global_schema.py -k requires_discogs_release`

Expect failure. Revert.

- [ ] **Step 3: A deleted `ALTER ROLE` fails**

Comment out the `conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {} {}")...)` call in `_ensure_role` (`backend/db.py:295-301`).

Run: `$PYTEST tests/test_tenant_schema.py tests/test_rls_isolation.py`

Expect failures in the role-attribute cases and in every app-role connection. Note that failures surface partly as 30-second pool timeouts, so this run is slow. Revert.

- [ ] **Step 4: Confirm nothing from this task survives**

Run: `git diff --stat` — expect empty. Run `git diff backend/db.py` — expect empty.

---

### Task 5: Leaked-database cleanup utility

**Files:**
- Create: `backend/scripts/drop_leaked_test_dbs.py` (following the existing `backend/scripts/capture_fixture.py` dev-utility convention)
- Modify: `Makefile` — add `test-db-clean` to `.PHONY` and a target

**Interfaces:**
- Consumes: `TEST_DATABASE_URL` from the environment.
- Produces: a CLI utility. Nothing imports it.

No automatic sweep at session start, deliberately: a prefix sweep cannot tell a crashed run's leftovers from a concurrent run's live database, and guessing wrong reintroduces the collision this design removes. Hence the `pg_stat_activity` check and the manual invocation.

- [ ] **Step 1: Write the script**

```python
"""Drop per-run test databases left behind by a crashed pytest session, and
undo the app_user BYPASSRLS poison if a crash left it applied.

Only touches databases with a `_run_` infix and no active backend, so the
hand-made discogs_browser_test_pricepaid / _wishlist scratch databases and any
concurrently-running session's database are out of reach by construction.

Usage:
  cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \\
    python scripts/drop_leaked_test_dbs.py
"""
```

Connect to the maintenance database via the same `_with_database` logic (import it from `tests.conftest` is wrong — duplicate the four-line helper here rather than making `scripts/` depend on `tests/`), then:

```sql
SELECT d.datname
FROM pg_database d
WHERE d.datname LIKE '%\_run\_%'
  AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname)
```

`DROP DATABASE ... WITH (FORCE)` each result via `sql.Identifier`, printing each name. Then `ALTER ROLE app_user NOBYPASSRLS` when that role exists, printing whether it needed repair.

- [ ] **Step 2: Verify against a deliberately leaked database**

Create one by hand, confirm the script drops it, and confirm a second run reports nothing to do:

```bash
docker exec dbtest-freshness psql -U postgres -c 'CREATE DATABASE discogs_browser_test_run_deadbeef'
```

Run: `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test python scripts/drop_leaked_test_dbs.py`

- [ ] **Step 3: Verify it refuses to touch what it must not**

Confirm `discogs_browser_test`, `discogs_browser_test_pricepaid` and `discogs_browser_test_wishlist` all still exist afterwards:

Run: `docker exec dbtest-freshness psql -U postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'discogs_browser_test%'"`

- [ ] **Step 4: Add the Makefile target**

```make
test-db-clean:
	cd $(BACKEND_DIR) && python scripts/drop_leaked_test_dbs.py
```

- [ ] **Step 5: Commit**

`chore: add cleanup for leaked per-run test databases`

---

### Task 6: Documentation

**Files:**
- Modify: `README.md` — "Running tests" (line 178)
- Modify: `CLAUDE.md` — "Tests" (line 125)

- [ ] **Step 1: Fix the README's test instructions**

`README.md` currently shows `cd backend && pytest` and mentions none of `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD` or `APP_DB_PASSWORD` — the suite does not run without all three. Replace with the real invocation, and add: each session provisions and drops its own `<base>_run_<hex>` database, so the role in `TEST_DATABASE_URL` needs `CREATEDB` and access to the `postgres` maintenance database; `make test-db-clean` clears databases leaked by a crashed run.

- [ ] **Step 2: Add the invariant to `CLAUDE.md`**

Under "Tests", state that a test may never assume pre-existing schema or role state: each session starts from an empty database with the app roles' attributes inverted, so anything a test asserts on must be constructed by the code under test in that run. Link the design doc. Note the remaining sharp edge: roles are cluster-level, so two suites running concurrently against one cluster still interfere at the role level for up to one test — give each worktree its own Postgres container if running suites in parallel.

- [ ] **Step 3: Commit**

`docs: document the per-run test database and its invariant`

---

### Task 7: Final verification and version bump

- [ ] **Step 1: Bump the version**

`backend/version.py` — minor bump (automatic, per `CLAUDE.md`; not a major bump).

- [ ] **Step 2: Full backend suite, twice in a row**

Run: `$PYTEST` — expect **741 passed** both times, with the second run proving no leaked state makes the first run unrepeatable.

- [ ] **Step 3: Confirm the cluster is left clean**

Run: `docker exec dbtest-freshness psql -U postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE '%\_run\_%'"` — expect empty.

- [ ] **Step 4: Frontend suite unaffected**

Run: `cd frontend && npm run test`

- [ ] **Step 5: Confirm `db.py` is untouched by the whole branch**

Run: `git diff main...HEAD -- backend/db.py` — expect empty. This is the plan's central constraint; verify it mechanically rather than by memory.

- [ ] **Step 6: Commit and open the PR**

`chore: bump version`, then use the `sdlc:pr-review-prep` skill.
