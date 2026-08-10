import os
from datetime import datetime

import psycopg
import pytest

import config
import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        # catalog is truncated too: the migration tests below seed a catalog
        # row per test, and catalog has no FK back to any of the others, so
        # CASCADE alone would leave it behind for the next test's duplicate key.
        conn.execute("TRUNCATE catalog, users, sessions, library_items, invites CASCADE")
        conn.commit()


def _connect_as(role_name: str, password: str):
    """A real connection authenticated as the given role (not the admin
    connection pg_test_db points everything at), for asserting on GRANT
    boundaries rather than RLS."""
    dsn = config._with_userinfo(os.environ["TEST_DATABASE_URL"], role_name, password)
    return psycopg.connect(dsn)


def test_users_table_has_rls_enabled(admin_conn):
    row = admin_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'users'"
    ).fetchone()
    assert row["relrowsecurity"] is True
    assert row["relforcerowsecurity"] is True


def test_library_items_table_has_rls_enabled(admin_conn):
    row = admin_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'library_items'"
    ).fetchone()
    assert row["relrowsecurity"] is True
    assert row["relforcerowsecurity"] is True


def test_app_identity_role_has_bypassrls(admin_conn):
    row = admin_conn.execute(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_identity'"
    ).fetchone()
    assert row["rolbypassrls"] is True


def test_app_user_role_does_not_have_bypassrls(admin_conn):
    row = admin_conn.execute(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_user'"
    ).fetchone()
    assert row["rolbypassrls"] is False


def test_invites_table_has_rls_disabled(admin_conn):
    row = admin_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'invites'"
    ).fetchone()
    assert row["relrowsecurity"] is False
    assert row["relforcerowsecurity"] is False


def test_app_identity_cannot_query_library_items(admin_conn):
    with _connect_as("app_identity", os.environ["IDENTITY_DB_PASSWORD"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM library_items")


def test_app_user_cannot_query_users(admin_conn):
    with _connect_as("app_user", os.environ["APP_DB_PASSWORD"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM users")


def test_app_user_cannot_query_sessions(admin_conn):
    with _connect_as("app_user", os.environ["APP_DB_PASSWORD"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM sessions")


def test_app_user_has_full_dml_on_crawl_queue(admin_conn):
    """DELETE is the load-bearing one: routers/settings.update_crawler purges a
    disabled crawler's pending rows through get_app_pool(). It shipped without
    the grant, and every router test runs on pg_test_db's superuser pool, where
    no ACL is consulted -- so this asserts the grant itself rather than a
    statement that happens to need it."""
    granted = {
        priv: admin_conn.execute(
            "SELECT has_table_privilege('app_user', 'crawl_queue', %s) AS granted", [priv]
        ).fetchone()["granted"]
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    assert granted == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}


def test_app_identity_can_update_session_last_seen_at(admin_conn):
    admin_conn.execute(
        "INSERT INTO users (discogs_user_id, discogs_username) VALUES (%s, %s)",
        [909090, "sessionupdatetestuser"],
    )
    user_id = admin_conn.execute(
        "SELECT id FROM users WHERE discogs_user_id = %s", [909090]
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) "
        "VALUES (%s, %s, NOW() + INTERVAL '1 day')",
        ["sessionupdatetesttoken", user_id],
    )
    admin_conn.commit()

    new_last_seen_at = datetime(2030, 1, 1)
    with _connect_as("app_identity", os.environ["IDENTITY_DB_PASSWORD"]) as conn:
        conn.execute(
            "UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s",
            [new_last_seen_at, "sessionupdatetesttoken"],
        )
        conn.commit()

    row = admin_conn.execute(
        "SELECT last_seen_at FROM sessions WHERE token_hash = %s",
        ["sessionupdatetesttoken"],
    ).fetchone()
    assert row["last_seen_at"] == new_last_seen_at


def test_init_tenant_schema_raises_when_identity_password_blank(pg_test_db, monkeypatch):
    monkeypatch.setattr(config, "IDENTITY_DB_PASSWORD", "")
    with pytest.raises(RuntimeError):
        db.init_tenant_schema()


def test_init_tenant_schema_raises_when_app_password_blank(pg_test_db, monkeypatch):
    monkeypatch.setattr(config, "APP_DB_PASSWORD", "")
    with pytest.raises(RuntimeError):
        db.init_tenant_schema()


def test_users_table_has_admin_and_recommendation_columns(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        user = db.create_user(conn, discogs_user_id=901, discogs_username="admincolstestuser")
        row = conn.execute(
            "SELECT is_admin, anthropic_api_key, recommendation_item_limit FROM users WHERE id = %s",
            [user["id"]],
        ).fetchone()
        conn.execute("DELETE FROM users WHERE id = %s", [user["id"]])
        conn.commit()
    assert row["is_admin"] is False
    assert row["anthropic_api_key"] is None
    assert row["recommendation_item_limit"] == 300


def test_stock_item_judgments_is_rls_isolated_per_user(pg_test_db, monkeypatch):
    # Same pattern as test_rls_isolation.py's two_users_one_shared_release
    # fixture: init the schema first (this test must pass standalone against
    # a fresh test DB, not just when an earlier admin_conn-based test in this
    # file happened to create the tables first), then point the app-role pool
    # at the real app_user role instead of the admin/superuser DSN pg_test_db
    # defaults it to -- a superuser connection always bypasses RLS (regardless
    # of FORCE ROW LEVEL SECURITY), so db.user_scope() below would prove
    # nothing without this.
    db.init_global_schema()
    db.init_tenant_schema()
    monkeypatch.setattr(
        config,
        "APP_DATABASE_URL",
        config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=902, discogs_username="rlsjudgetestalice")
        bob = db.create_user(conn, discogs_user_id=903, discogs_username="rlsjudgetestbob")
        conn.execute(
            "INSERT INTO stock_item_judgments (user_id, item_key, recommended) VALUES (%s, %s, %s)",
            [alice["id"], "key-1", True],
        )
        conn.commit()

    try:
        with db.user_scope(bob["id"]) as conn:
            rows = conn.execute("SELECT * FROM stock_item_judgments").fetchall()
        assert rows == []

        with db.user_scope(alice["id"]) as conn:
            rows = conn.execute("SELECT * FROM stock_item_judgments").fetchall()
        assert len(rows) == 1
        assert rows[0]["item_key"] == "key-1"
    finally:
        with db.get_admin_pool().connection() as conn:
            conn.execute("DELETE FROM stock_item_judgments WHERE user_id IN (%s, %s)", [alice["id"], bob["id"]])
            conn.execute("DELETE FROM users WHERE id IN (%s, %s)", [alice["id"], bob["id"]])
            conn.commit()


def test_stock_item_judgments_insert_with_mismatched_user_id_is_rejected(pg_test_db, monkeypatch):
    """WITH CHECK on stock_item_judgments_isolation (backend/db.py) must reject
    an INSERT for a user_id other than the scoped app.user_id -- the write-side
    counterpart to test_stock_item_judgments_is_rls_isolated_per_user above,
    modeled on test_rls_isolation.py::test_insert_with_mismatched_user_id_is_rejected
    for library_items_isolation."""
    db.init_global_schema()
    db.init_tenant_schema()
    monkeypatch.setattr(
        config,
        "APP_DATABASE_URL",
        config._with_userinfo(
            os.environ["TEST_DATABASE_URL"], "app_user", os.environ["APP_DB_PASSWORD"]
        ),
    )
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=904, discogs_username="rlswritetestalice")
        bob = db.create_user(conn, discogs_user_id=905, discogs_username="rlswritetestbob")
        conn.commit()

    try:
        with db.user_scope(alice["id"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO stock_item_judgments (user_id, item_key, recommended) "
                    "VALUES (%s, %s, %s)",
                    [bob["id"], "key-mismatch", True],
                )
    finally:
        with db.get_admin_pool().connection() as conn:
            conn.execute("DELETE FROM stock_item_judgments WHERE user_id IN (%s, %s)", [alice["id"], bob["id"]])
            conn.execute("DELETE FROM users WHERE id IN (%s, %s)", [alice["id"], bob["id"]])
            conn.commit()


def test_library_items_has_price_paid_column(admin_conn):
    row = admin_conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'library_items' AND column_name = 'price_paid'"
    ).fetchone()
    assert row is not None
    assert row["data_type"] == "text"


def _readd_legacy_catalog_price(admin_conn):
    """Recreate the pre-migration shape: init_tenant_schema() has already
    dropped the column by the time the fixture yields, so a migration test
    has to put it back before it has anything to migrate."""
    admin_conn.execute("ALTER TABLE catalog ADD COLUMN IF NOT EXISTS discogs_price TEXT")
    admin_conn.commit()


def test_backfill_copies_the_global_price_to_a_sole_collection_owner(admin_conn):
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    admin_conn.execute(
        "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'r1', TRUE)",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()

    assert admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()["price_paid"] == "42.50"


def test_backfill_does_not_overwrite_an_already_recorded_price(admin_conn):
    """Redundant on the real one-shot run -- price_paid is created empty in the
    same schema application -- but it is the contract if the source column is
    ever reintroduced by a restore or a rollback experiment. Re-running must
    not clobber a price the user has recorded since."""
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    admin_conn.execute(
        "INSERT INTO library_items (user_id, discogs_id, in_collection, price_paid) "
        "VALUES (%s, 'r1', TRUE, '99.99')",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()

    assert admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()["price_paid"] == "99.99"


def test_backfill_leaves_a_contested_release_null_for_everyone(admin_conn):
    # The global value is whichever user synced last, so with two owners it
    # cannot be attributed. Copying it to both would be the same cross-tenant
    # leak in reverse.
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    for user in (alice, bob):
        admin_conn.execute(
            "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'r1', TRUE)",
            [user["id"]],
        )
    admin_conn.commit()

    db.init_tenant_schema()

    prices = [
        r["price_paid"] for r in admin_conn.execute(
            "SELECT price_paid FROM library_items WHERE discogs_id = 'r1'"
        ).fetchall()
    ]
    assert prices == [None, None]


def test_backfill_skips_a_wantlist_only_holder(admin_conn):
    # A sole holder who only wants the release never paid this price.
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    admin_conn.execute(
        "INSERT INTO library_items (user_id, discogs_id, in_wishlist) VALUES (%s, 'r1', TRUE)",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()

    assert admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()["price_paid"] is None


def test_migration_drops_the_global_catalog_price_column(admin_conn):
    _readd_legacy_catalog_price(admin_conn)
    db.init_tenant_schema()
    assert admin_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'catalog' AND column_name = 'discogs_price'"
    ).fetchone() is None


def test_migration_is_idempotent_and_does_not_refill_a_cleared_price(admin_conn):
    """TENANT_SCHEMA re-runs on every boot. Once the source column is gone the
    guard must make the whole block a no-op -- otherwise a second boot either
    errors or resurrects a value the user deliberately cleared."""
    _readd_legacy_catalog_price(admin_conn)
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title, discogs_price) "
        "VALUES ('r1', 'A', 'T', '42.50')"
    )
    admin_conn.execute(
        "INSERT INTO library_items (user_id, discogs_id, in_collection) VALUES (%s, 'r1', TRUE)",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()
    admin_conn.execute(
        "UPDATE library_items SET price_paid = NULL WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    )
    admin_conn.commit()

    db.init_tenant_schema()

    assert admin_conn.execute(
        "SELECT price_paid FROM library_items WHERE user_id = %s AND discogs_id = 'r1'",
        [alice["id"]],
    ).fetchone()["price_paid"] is None
