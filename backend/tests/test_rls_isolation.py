"""Proves the property the two-role/RLS architecture exists for: a query
against library_items scoped to one user via db.user_scope() never returns
another user's rows, even for a bare table scan or an aggregate with no
per-row WHERE clause of its own."""

import os

import psycopg
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


def test_insert_with_mismatched_user_id_is_rejected(two_users_one_shared_release):
    """WITH CHECK on library_items_isolation (backend/db.py) must reject an
    INSERT for a user_id other than the scoped app.user_id -- proving the
    isolation guarantee holds for writes, not just the reads covered above."""
    alice_id, bob_id = two_users_one_shared_release
    with db.user_scope(alice_id) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO library_items (user_id, discogs_id, in_wishlist) "
                "VALUES (%s, 'd1', TRUE)",
                [bob_id],
            )


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


@pytest.fixture
def two_users_one_shared_crawler(pg_test_db, monkeypatch):
    db.init_global_schema()
    db.init_tenant_schema()
    monkeypatch.setattr(
        db.config,
        "APP_DATABASE_URL",
        db.config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    with db.get_admin_pool().connection() as admin:
        alice = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (1, 'alice') RETURNING id",
        ).fetchone()
        bob = admin.execute(
            "INSERT INTO users (discogs_user_id, discogs_username) VALUES (2, 'bob') RETURNING id",
        ).fetchone()
        db.register_crawler(admin, "Amazon", "/x.py")
        crawler = admin.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()
        # Both users hide the same crawler in their own row, so a leak in
        # either direction has something real to leak.
        admin.execute(
            "INSERT INTO user_hidden_crawlers (user_id, crawler_id) VALUES (%s, %s)",
            [alice["id"], crawler["id"]],
        )
        admin.commit()

    yield alice["id"], bob["id"], crawler["id"]

    with db.get_admin_pool().connection() as admin:
        admin.execute("TRUNCATE users, crawlers, user_hidden_crawlers CASCADE")
        admin.commit()


def test_user_sees_only_their_own_hidden_crawlers(two_users_one_shared_crawler):
    alice_id, _bob_id, crawler_id = two_users_one_shared_crawler
    with db.user_scope(alice_id) as conn:
        assert db.get_hidden_crawler_ids(conn, alice_id) == [crawler_id]


def test_other_user_does_not_see_the_first_users_hidden_crawler(two_users_one_shared_crawler):
    _alice_id, bob_id, _crawler_id = two_users_one_shared_crawler
    with db.user_scope(bob_id) as conn:
        assert db.get_hidden_crawler_ids(conn, bob_id) == []


def test_set_hidden_crawler_ids_replaces_the_full_set(two_users_one_shared_crawler):
    alice_id, _bob_id, crawler_id = two_users_one_shared_crawler
    with db.get_admin_pool().connection() as admin:
        db.register_crawler(admin, "eBay", "/y.py")
        second = admin.execute("SELECT id FROM crawlers WHERE site_name = 'eBay'").fetchone()["id"]
        admin.commit()

    with db.user_scope(alice_id) as conn:
        db.set_hidden_crawler_ids(conn, alice_id, [second])
        conn.commit()

    with db.user_scope(alice_id) as conn:
        assert db.get_hidden_crawler_ids(conn, alice_id) == [second]
    # crawler_id (the original hidden one) must be gone -- this is a replace,
    # not a merge.
    with db.user_scope(alice_id) as conn:
        assert crawler_id not in db.get_hidden_crawler_ids(conn, alice_id)


def test_set_hidden_crawler_ids_with_mismatched_user_id_is_rejected(two_users_one_shared_crawler):
    alice_id, bob_id, crawler_id = two_users_one_shared_crawler
    with db.user_scope(alice_id) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO user_hidden_crawlers (user_id, crawler_id) VALUES (%s, %s)",
                [bob_id, crawler_id],
            )


def test_set_hidden_crawler_ids_dedupes_duplicate_ids(two_users_one_shared_crawler):
    """A stale client double-posting (or a genuinely duplicated id in the
    payload) must not hit the table's (user_id, crawler_id) primary key
    twice in one executemany -- that raises UniqueViolation today and 500s
    the endpoint."""
    alice_id, _bob_id, crawler_id = two_users_one_shared_crawler
    with db.user_scope(alice_id) as conn:
        db.set_hidden_crawler_ids(conn, alice_id, [crawler_id, crawler_id])
        conn.commit()

    with db.user_scope(alice_id) as conn:
        assert db.get_hidden_crawler_ids(conn, alice_id) == [crawler_id]


def test_set_hidden_crawler_ids_drops_nonexistent_crawler_id(two_users_one_shared_crawler):
    """A crawler id that no longer references a real row (renamed/deleted
    crawler, stale tab) must be silently dropped rather than raising
    ForeignKeyViolation and 500ing the endpoint."""
    alice_id, _bob_id, crawler_id = two_users_one_shared_crawler
    nonexistent_id = crawler_id + 1_000_000
    with db.user_scope(alice_id) as conn:
        db.set_hidden_crawler_ids(conn, alice_id, [crawler_id, nonexistent_id])
        conn.commit()

    with db.user_scope(alice_id) as conn:
        assert db.get_hidden_crawler_ids(conn, alice_id) == [crawler_id]
