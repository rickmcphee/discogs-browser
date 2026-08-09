"""Drop per-run test databases left behind by a crashed pytest session, and
undo the app_user BYPASSRLS poison if a crash left it applied.

Only touches databases with a `_run_` infix and no active backend, so the
hand-made discogs_browser_test_pricepaid / _wishlist scratch databases and any
concurrently-running session's database are out of reach by construction.

Usage:
  cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test \
    python scripts/drop_leaked_test_dbs.py
"""

import os
import sys
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

LEAKED_DB_QUERY = r"""
SELECT d.datname
FROM pg_database d
WHERE d.datname LIKE '%\_run\_%'
  AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname)
"""


def _with_database(url, dbname):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def main():
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        print("TEST_DATABASE_URL is not set; refusing to guess which cluster to clean.", file=sys.stderr)
        sys.exit(1)

    maintenance_url = _with_database(base_url, "postgres")
    did_something = False

    with psycopg.connect(maintenance_url, autocommit=True) as conn:
        leaked = [row[0] for row in conn.execute(LEAKED_DB_QUERY).fetchall()]
        for datname in leaked:
            conn.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(datname))
            )
            print(f"Dropped leaked database {datname}")
            did_something = True

        role_row = conn.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", ["app_user"]
        ).fetchone()
        if role_row is not None:
            was_bypassrls = role_row[0]
            if was_bypassrls:
                conn.execute(
                    sql.SQL("ALTER ROLE {} NOBYPASSRLS").format(sql.Identifier("app_user"))
                )
                print("Repaired app_user: rolbypassrls was true, now false")
                did_something = True
            elif did_something:
                print("app_user.rolbypassrls was already false; no repair needed")

    if not did_something:
        print("Nothing to do: no leaked databases and app_user was not poisoned.")


if __name__ == "__main__":
    main()
