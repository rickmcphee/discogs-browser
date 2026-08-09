"""Drop per-run test databases left behind by a crashed pytest session, and
undo the app_user/app_identity BYPASSRLS poison if a crash left it applied.

DO NOT RUN THIS WHILE A SUITE IS IN FLIGHT. It is not safe to do so and cannot
be made safe: pg_test_db closes every pool at the end of each test, so a live
run's own database sits with zero backends in the gaps between tests, and
`DROP DATABASE ... WITH (FORCE)` would kill it mid-suite. The pg_stat_activity
check below narrows the window; it does not close it.

Scope is limited by construction to `<base>_run_<8 hex>` for the exact base
database named in TEST_DATABASE_URL -- matched in Python rather than with a
bare SQL LIKE, so the hand-made discogs_browser_test_pricepaid / _wishlist
scratch databases, and any unrelated database that merely happens to contain
`_run_` (`job_run_history`), are never candidates.

Passwords are not repaired: this run's random values are unknowable, and the
next init_tenant_schema() rewrites both roles from IDENTITY_DB_PASSWORD /
APP_DB_PASSWORD anyway.

Usage:
  cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
    python scripts/drop_leaked_test_dbs.py [--dry-run]
"""

import os
import re
import sys
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

# What db._ensure_role sets, i.e. what a crashed run may have left inverted.
_CORRECT_BYPASSRLS = {"app_user": "NOBYPASSRLS", "app_identity": "BYPASSRLS"}
_CORRECT_BYPASSRLS_VALUE = {"app_user": False, "app_identity": True}


def _with_database(url, dbname):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def main():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        print("TEST_DATABASE_URL is not set; refusing to guess which cluster to clean.", file=sys.stderr)
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv[1:]
    base_name = urlsplit(base_url).path.lstrip("/")
    run_database = re.compile(re.escape(base_name) + r"_run_[0-9a-f]{8}\Z")
    maintenance_url = _with_database(base_url, "postgres")
    did_something = False

    with psycopg.connect(maintenance_url, autocommit=True) as conn:
        candidates = [
            row[0]
            for row in conn.execute(
                "SELECT datname FROM pg_database WHERE NOT datistemplate"
            ).fetchall()
            if run_database.fullmatch(row[0])
        ]
        for datname in candidates:
            if conn.execute(
                "SELECT 1 FROM pg_stat_activity WHERE datname = %s", [datname]
            ).fetchone() is not None:
                print(f"Skipped {datname}: it has active connections (a run in progress?)")
                did_something = True
                continue
            if dry_run:
                print(f"Would drop leaked database {datname}")
            else:
                conn.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(datname))
                )
                print(f"Dropped leaked database {datname}")
            did_something = True

        for role, bypass in _CORRECT_BYPASSRLS.items():
            row = conn.execute(
                "SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", [role]
            ).fetchone()
            if row is None or row[0] == _CORRECT_BYPASSRLS_VALUE[role]:
                continue
            if dry_run:
                print(f"Would repair {role}: rolbypassrls is {row[0]}, should be "
                      f"{_CORRECT_BYPASSRLS_VALUE[role]}")
            else:
                conn.execute(
                    sql.SQL("ALTER ROLE {} {}").format(sql.Identifier(role), sql.SQL(bypass))
                )
                print(f"Repaired {role}: rolbypassrls was {row[0]}, now "
                      f"{_CORRECT_BYPASSRLS_VALUE[role]}")
            did_something = True

    if not did_something:
        print(f"Nothing to do: no leaked {base_name}_run_* databases, roles not poisoned.")


if __name__ == "__main__":
    main()
