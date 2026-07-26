import os

import pytest

import db


@pytest.fixture
def two_users_one_shared_release(pg_test_db, monkeypatch):
    db.init_global_schema()
    db.init_tenant_schema()
    # Point the app-role pool at the real app_user role for this test only —
    # earlier tasks' fixtures use the admin DSN for all three pools. Uses
    # config._with_userinfo (Task 1) rather than a literal .replace(), so this
    # works regardless of TEST_DATABASE_URL's actual admin userinfo.
    monkeypatch.setattr(
        db.config,
        "APP_DATABASE_URL",
        db.config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    db._app_pool = None

    with db.get_admin_pool().connection() as admin:
        alice = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (1, 'alice') RETURNING id",
        ).fetchone()
        bob = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (2, 'bob') RETURNING id",
        ).fetchone()
        admin.execute("INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'A', 'T')")
        admin.execute(
            "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'd1', TRUE)",
            [alice["id"]],
        )
        admin.commit()

    yield alice["id"], bob["id"]

    with db.get_admin_pool().connection() as admin:
        admin.execute("TRUNCATE users, catalog, library_items CASCADE")
        admin.commit()


def test_user_sees_only_their_own_library_items(two_users_one_shared_release):
    alice_id, _bob_id = two_users_one_shared_release
    with db.user_scope(alice_id) as conn:
        rows = conn.execute("SELECT * FROM library_items").fetchall()
    assert len(rows) == 1
    assert rows[0]["discogs_id"] == "d1"


def test_other_user_sees_nothing_for_a_release_they_dont_own(two_users_one_shared_release):
    _alice_id, bob_id = two_users_one_shared_release
    with db.user_scope(bob_id) as conn:
        rows = conn.execute("SELECT * FROM library_items").fetchall()
    assert rows == []


def test_query_with_no_where_clause_still_returns_only_the_scoped_users_rows(
    two_users_one_shared_release,
):
    """The property RLS exists to guarantee: a query that forgot a WHERE
    user_id = ... clause must still be isolated, not just queries that
    remembered to add one."""
    alice_id, _bob_id = two_users_one_shared_release
    with db.user_scope(alice_id) as conn:
        all_rows = conn.execute("SELECT discogs_id FROM library_items").fetchall()
    assert [r["discogs_id"] for r in all_rows] == ["d1"]
