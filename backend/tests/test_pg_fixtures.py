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

import pytest

import db


# Skipping rather than passing when TEST_DATABASE_URL is unset is safe here: in
# that case pg_test_db raises for all 28 Postgres-backed test files, so a run
# that lost the variable can never look green.
@pytest.fixture(autouse=True)
def _require_run_database(pg_run_database):
    if pg_run_database is None:
        pytest.skip("TEST_DATABASE_URL unset; no per-run database was provisioned")


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
