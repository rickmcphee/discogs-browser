"""Proves the property the two-role/RLS architecture exists for: a query
against library_items scoped to one user via db.user_scope() never returns
another user's rows, even for a bare table scan or an aggregate with no
per-row WHERE clause of its own."""

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
    # No db._app_pool = None here: pg_test_db (an upstream fixture dependency)
    # already reset it to None before this fixture body runs, and nothing in
    # between touches it.

    with db.get_admin_pool().connection() as admin:
        alice = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (1, 'alice') RETURNING id",
        ).fetchone()
        bob = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (2, 'bob') RETURNING id",
        ).fetchone()
        admin.execute("INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'A', 'T')")
        # Both users track the same catalog release in their own
        # library_items row, so a leak in either direction has something
        # real to leak -- a table with only one row anywhere can't
        # distinguish "isolated" from "RLS silently absent."
        admin.execute(
            "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'd1', TRUE)",
            [alice["id"]],
        )
        admin.execute(
            "INSERT INTO library_items (user_id, discogs_id, in_wishlist) VALUES (%s, 'd1', TRUE)",
            [bob["id"]],
        )
        admin.commit()

    yield alice["id"], bob["id"]

    with db.get_admin_pool().connection() as admin:
        # Postgres TRUNCATE ... CASCADE also truncates any table with an FK
        # reference to the named ones in full (here: sessions, invites) --
        # not a targeted delete of related rows the way ON DELETE CASCADE
        # would be. Harmless in this fixture (those tables start empty), but
        # worth knowing before copying this pattern elsewhere.
        admin.execute("TRUNCATE users, catalog, library_items CASCADE")
        admin.commit()


def test_user_sees_only_their_own_library_items(two_users_one_shared_release):
    alice_id, _bob_id = two_users_one_shared_release
    with db.user_scope(alice_id) as conn:
        rows = conn.execute("SELECT * FROM library_items").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == alice_id
    assert rows[0]["in_collection"] is True


def test_other_user_sees_only_their_own_row_not_the_first_users(two_users_one_shared_release):
    """Isolation has to hold in both directions on the same shared release --
    not just that a user with zero rows trivially sees nothing, but that a
    user's own row comes back while the other user's is excluded."""
    alice_id, bob_id = two_users_one_shared_release
    with db.user_scope(bob_id) as conn:
        rows = conn.execute("SELECT * FROM library_items").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == bob_id
    assert rows[0]["in_wishlist"] is True
    assert rows[0]["user_id"] != alice_id


def test_aggregate_query_also_respects_isolation(two_users_one_shared_release):
    """RLS filters rows before aggregation, not after: COUNT(*) run under a
    user's scope must reflect only their own rows. This is a distinct query
    shape from the bare SELECT * above -- there's no per-row projection for
    a caller to have "forgotten" a WHERE clause on, so this specifically
    checks that aggregates don't get to see the whole table first."""
    alice_id, _bob_id = two_users_one_shared_release
    with db.user_scope(alice_id) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM library_items").fetchone()
    assert row["n"] == 1
