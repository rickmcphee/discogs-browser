# Test database freshness design

Date: 2026-08-09
Branch: `worktree-test-schema-freshness`

## Problem

Schema-shape tests can pass vacuously against the long-lived local test
database.

`backend/tests/conftest.py`'s `pg_test_db` fixture and the per-file
`admin_conn` fixtures (`backend/tests/test_tenant_schema.py:11-18`,
`backend/tests/test_global_schema.py:7-13`) tear down with
`TRUNCATE ... CASCADE`. That removes rows; it never drops a column, table,
index, policy, or role. The local `discogs_browser_test` database lives in a
persistent Docker volume (container `discogs-browser-pg`, anonymous volume
`be918a86…`), so once a migration line in `GLOBAL_SCHEMA` or `TENANT_SCHEMA`
(both `backend/db.py`) has been applied even once, the resulting column is
permanently present locally — independent of whether the
`ALTER TABLE ... ADD COLUMN` line is still in `db.py`.

The tests that exist specifically to guard a migration are therefore the ones
least able to do so locally:

- `test_library_items_has_price_paid_column` (added in a3db7ea on
  `worktree-library-price-paid`)
- `test_global_schema.py::test_catalog_table_exists_with_expected_columns`
- `test_global_schema.py::test_stock_item_identities_table_exists_with_expected_columns`
- `test_tenant_schema.py::test_users_table_has_admin_and_recommendation_columns`
- every future migration test of the same shape

a3db7ea's TDD sequence did prove its migration — the failure was observed
before the `db.py` line was added — but a re-run on the same volume could
not have. Delete the `ALTER TABLE` line today and the test still passes
locally. Only CI, which provisions a fresh `postgres:16` service per job,
genuinely guards it.

Roles are worse. `init_tenant_schema()` creates `app_identity` and `app_user`
via `_ensure_role` (`backend/db.py:284`), and roles are cluster-level, not
schema-level or database-level. Nothing in the suite ever resets them, so
`test_app_identity_role_has_bypassrls` and
`test_app_user_role_does_not_have_bypassrls` assert on state that has
persisted in the local cluster since the first time the multi-tenant work
ran. They would pass with `_ensure_role`'s `ALTER ROLE` deleted.

### A second problem found while investigating

The suite has one hard-coded database name shared by every checkout. This
repo's workflow is one git worktree per unit of work, and four are active
right now. Two worktrees running `pytest` concurrently both `TRUNCATE` and
re-`init` the same `discogs_browser_test`, so each run deletes the other's
fixture rows mid-test. This produces scattered, file-clustered, irreproducible
failures — which is exactly what was observed during this investigation, and
what caused two of the measurements below to be discarded. Someone chasing
those failures already improvised a workaround: the local cluster contains
hand-made `discogs_browser_test_pricepaid` and `discogs_browser_test_wishlist`
databases alongside the canonical one.

The stale-role variant of the same collision is also live: `_ensure_role`'s
`ALTER ROLE ... PASSWORD` is unconditional, so every test run rewrites both
role passwords cluster-wide. A run that starts while the roles hold some
other value fails on 30-second connection timeouts until the first
`init_tenant_schema()` repairs them — one observed run took 147s instead of
66s for this reason.

## Evidence

All measured on this machine against `postgres:16`, full backend suite
(738 tests).

| Measurement | Result |
| --- | --- |
| `DROP SCHEMA public CASCADE` + `CREATE SCHEMA public` | 0.024s |
| `CREATE DATABASE … TEMPLATE template0` | 0.024s |
| `DROP DATABASE … WITH (FORCE)` | 0.013–1.16s |
| `init_global_schema()` | 0.026s |
| `init_tenant_schema()` | 0.025s |
| Both re-run against an already-built database | 0.014s |
| Full suite, pristine cluster, bare database and bare roles | **738 passed, 66s** |
| Full suite, pristine cluster, roles poisoned (passwords + `NOBYPASSRLS`) | **738 passed, 71s** |
| Full suite, pristine cluster, roles poisoned (passwords + both bits inverted) | **738 passed, 75s** |
| Full suite, shared local database, concurrent worktree run | 18–88 failed (discarded) |
| Full suite, shared local database, stale role passwords | 20 failed, 147s |
| `DROP SCHEMA public CASCADE` + `CREATE SCHEMA public`, verbatim | 88 failed |
| `test_tenant_schema` + `test_rls_isolation`, poisoned, `_ensure_role`'s `ALTER ROLE` deleted | **12 failed** |

Three findings follow, and they set the design.

**Recreation is not expensive.** A once-per-session reset is ~0.05s against a
66s suite — under 0.1%. The premise that a blanket approach is too costly and
a targeted opt-in path is needed does not survive measurement; targeting would
buy a second provisioning path for nothing.

**`DROP SCHEMA public CASCADE; CREATE SCHEMA public;` is not equivalent to a
fresh database.** Run verbatim, it produced 88 failures. A fresh `postgres:16`
database's `public` schema carries
`{pg_database_owner=UC/pg_database_owner,=U/pg_database_owner}` — `USAGE`
granted to `PUBLIC`. A hand-recreated one carries `NULL`: owner only.
`TENANT_SCHEMA` never issues `GRANT USAGE ON SCHEMA public`, so it silently
depends on that default; without it `app_user` and `app_identity` lose access
to every object and each grant and RLS test fails. Any drop-schema variant has
to reconstruct owner and ACL by hand, correctly, forever. Creating a database
from `template0` gets the ACL right by construction, which is why the design
below provisions a database rather than resetting a schema.

**The schema strings do fully self-construct.** Against a genuinely pristine
cluster the whole suite passes: 738 tests, no failures. So resetting to bare
does not expose latent DDL gaps, and every local failure seen during this
investigation was contamination rather than a real defect.

## Design

One session-scoped autouse fixture in `backend/tests/conftest.py`. Each
`pytest` invocation gets its own database, created empty and dropped
afterwards, and starts with the two app roles' state deliberately wrong.

### Per-run database

The fixture derives a maintenance DSN from `TEST_DATABASE_URL` by swapping the
path to `/postgres`, creates `<base>_run_<8 hex>` with
`TEMPLATE template0`, then rewrites `os.environ["TEST_DATABASE_URL"]` in place
to point at it.

Rewriting the environment variable rather than adding a new fixture parameter
is what keeps this change small. `TEST_DATABASE_URL` is read at fixture-call
time in nine places across five files — `conftest.py:19,21,24`,
`test_db_pools.py:11`, `test_tenant_schema.py:25,158,198`,
`test_rls_isolation.py:26`, `test_crawl_manager.py:46` — and no test
hard-codes a database name
(verified by grep). Every one of those call sites follows automatically, so no
existing test file changes.

DSN munging reuses the `urlsplit`/`urlunsplit` approach of
`config._with_userinfo` (`backend/config.py:14`), keeping `netloc` verbatim so
userinfo and its percent-encoding survive. The helper lives in `conftest.py`,
not `config.py` — this is test infrastructure and `config.py` has no
production need for it.

Teardown closes `db._admin_pool` / `_identity_pool` / `_app_pool`, restores
`TEST_DATABASE_URL`, and issues `DROP DATABASE ... WITH (FORCE)` so a leftover
pool connection cannot block the drop.

This also fixes the concurrency collision: two worktrees running `pytest` at
the same time no longer share a database, and the suite gets faster and
deterministic as a side effect.

### Role poisoning

Roles cannot be reset the way a database can. `DROP ROLE app_user` fails —
verified: `role "app_user" cannot be dropped because some objects depend on
it / 12 objects in database discogs_browser_test_pricepaid`. Cluster roles
carry grants in every database, so dropping them needs `DROP OWNED BY`
executed inside each database that references them, which for a developer
whose dev app shares the cluster would mean reaching into their real data.
That is not acceptable for a test fixture.

Instead the fixture inverts, before the first `init_tenant_schema()` of the
run, exactly the attributes `_ensure_role` is responsible for setting:

| Role | Poisoned to | Corrected by `_ensure_role` to |
| --- | --- | --- |
| `app_user` | `BYPASSRLS`, random password | `NOBYPASSRLS`, `APP_DB_PASSWORD` |
| `app_identity` | `NOBYPASSRLS`, random password | `BYPASSRLS`, `IDENTITY_DB_PASSWORD` |

Roles absent from the cluster (CI's case) are skipped — there is nothing to
poison, and the run is already honest.

Inverting rather than merely clearing is what makes the assertions bite.
`test_app_identity_role_has_bypassrls` now requires the `bypass_rls=True`
branch to have run; `test_app_user_role_does_not_have_bypassrls` requires the
`bypass_rls=False` branch; the random passwords make every `app_user` /
`app_identity` connection in `test_rls_isolation.py`,
`test_crawl_manager.py` and `test_tenant_schema.py` depend on `_ensure_role`
running in this process.

Verified: with poisoning applied and `_ensure_role`'s `ALTER ROLE` commented
out, `test_tenant_schema.py` and `test_rls_isolation.py` produced 12 failures —
both role-attribute cases plus all four RLS-isolation cases. Today, without
poisoning, that same deletion leaves them green, because the roles are
pre-existing cluster state. That contrast is the defect.

Poisoning is free: measured at 738 passed with poisoned roles, because every
fixture that connects as an app role calls `init_global_schema()` and
`init_tenant_schema()` first. No test remediation is needed.

The `ALTER ROLE` uses `psycopg.sql.SQL(...).format(sql.Identifier(...),
sql.Literal(...))`, the same composition `_ensure_role` uses at
`backend/db.py:296` — role names and passwords are never interpolated as raw
strings.

Teardown returns `app_user` to `NOBYPASSRLS` so a normal exit cannot leave a
cluster role holding an elevated bit. A hard crash between poison and
correction can, and `make test-db-clean` (below) normalizes it.

### Guard test

A new `backend/tests/test_pg_fixtures.py` asserts the harness is doing what it
claims, so silently reverting to a reused database is a test failure rather
than a return to invisible vacuity. The session fixture yields a record of
what it *measured*, not what it assumed:

- `database` — the run database name
- `tables_at_start` — `information_schema.tables` count in schema `public`,
  queried against the new database before any test runs
- `app_user_bypassrls_at_start` / `app_identity_bypassrls_at_start` — read
  back after poisoning, `None` when the role was absent

The three cases: the session database differs from the base name in
`TEST_DATABASE_URL` and matches `current_database()`; `tables_at_start` is 0;
and the poisoned bits were observed inverted, or the roles were absent.

`tables_at_start` is measured rather than assumed on purpose — asserting a
constant the fixture hard-codes would reintroduce the exact vacuity this spec
is about.

### Leaked-database cleanup

A crashed session (SIGKILL, power loss) leaks its run database. No automatic
sweep at session start: a prefix sweep cannot distinguish a crashed run's
leftovers from a concurrent run's live database, and getting that wrong
reintroduces the collision this design removes.

Instead `backend/scripts/drop_leaked_test_dbs.py`, following the existing
`backend/scripts/` dev-utility convention (`capture_fixture.py`), drops every
`%_run_%` database with no backend in `pg_stat_activity` and normalizes
`app_user` to `NOBYPASSRLS`. The `_run_` infix keeps it clear of the
hand-made `discogs_browser_test_pricepaid` / `_wishlist` databases, which it
must not touch. A `make test-db-clean` target invokes it.

### Requirements this adds

The admin role in `TEST_DATABASE_URL` needs `CREATEDB` and login access to the
`postgres` maintenance database. Satisfied by the local Docker `postgres`
superuser and by CI's `postgres` user; a developer pointing
`TEST_DATABASE_URL` at a managed Postgres with a non-superuser admin role
would not be, and the fixture should fail with a clear message naming
`CREATEDB` rather than a bare `InsufficientPrivilege`.

## Scope

Touches:

- `backend/tests/conftest.py` — new session-scoped autouse fixture, a
  `_with_database` DSN helper, and a `_poison_app_roles` helper. `pg_test_db`
  and the `TRUNCATE` teardowns are left exactly as they are: with a bare
  database per run they are now an intra-run optimization rather than the only
  cleanup, and rewriting 27 test files' fixtures is not this change.
- `backend/tests/test_pg_fixtures.py` — new, three cases.
- `backend/scripts/drop_leaked_test_dbs.py` — new.
- `Makefile` — new `test-db-clean` target.
- `README.md` — "Running tests" currently shows `cd backend && pytest` with no
  mention of `TEST_DATABASE_URL`, `IDENTITY_DB_PASSWORD` or
  `APP_DB_PASSWORD`, none of which the suite runs without. Document the real
  invocation, the per-run database, and the `CREATEDB` requirement.
- `CLAUDE.md` — "Tests" section gains the invariant that a test may never
  assume pre-existing schema or role state.

Explicitly not touched:

- `backend/db.py` — no change to `GLOBAL_SCHEMA`, `TENANT_SCHEMA`,
  `init_global_schema`, `init_tenant_schema` or `_ensure_role`. This is test
  infrastructure only.
- `.github/workflows/fly-deploy.yml` — CI already provisions a fresh database
  per job and needs no change. It gains the per-run database for free.
- The 27 existing test files that use `pg_test_db`.

## Non-goals

- No migration tooling. The repo's convention is idempotent DDL in
  `db.py`, documented in the `crawl_queue_pending_idx` comment
  (`backend/db.py:129-140`), and that stays.
- No parallel test execution. `pytest-xdist` is not installed; per-run
  databases would help if it were, but adding it is separate work.
- No attempt to make concurrent runs share roles safely. Two suites racing on
  the same cluster still interfere at the role level, because `db.py`
  hard-codes the role names and changing that is a production behavior
  change. The window is one test — the next `init_tenant_schema()` repairs
  it — and the mitigation is a per-worktree Postgres container. Documented,
  not solved.

## Documentation impact

Per the shaping checklist: this repo has no `AGENTS.md` and no `.agents/`
directory, so `documenting-agents`, `documenting-instructions`,
`documenting-inputs` and `documenting-outputs` have no target here.

No runtime input or output changes: no new trigger, event, message, API
caller, scheduled job, or external call. The change is confined to the test
harness. `README.md` and `CLAUDE.md` updates are in scope above because the
golden test command and a hard rule about test isolation both change.

## Risks

- **A test that depended on accumulated state fails.** Measured: none do
  (738 passed from pristine). Residual risk is a test added between this spec
  and its implementation.
- **`CREATEDB` unavailable.** Fails fast with a message naming the missing
  privilege rather than obscurely.
- **Leaked databases accumulate.** Bounded by `make test-db-clean`; each is
  ~10MB.
- **A crash leaves `app_user` with `BYPASSRLS`.** Normal teardown prevents it;
  `make test-db-clean` repairs it. Only reachable on a cluster that is by
  definition a test cluster.
