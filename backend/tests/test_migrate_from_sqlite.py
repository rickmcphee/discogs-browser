import sqlite3

import pytest
import respx
import httpx

import db
from scripts.migrate_from_sqlite import migrate


@pytest.fixture
def sqlite_source(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE releases (
            discogs_id TEXT PRIMARY KEY, artist TEXT, title TEXT, year INTEGER,
            label TEXT, format TEXT, discogs_price TEXT, barcode TEXT,
            cover_image_url TEXT, discogs_url TEXT, in_collection INTEGER,
            in_wishlist INTEGER, plex_url TEXT, plex_matched_at TIMESTAMP,
            last_synced TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO releases VALUES ('d1', 'A', 'T', 1999, 'L', 'LP', '$10', '123', "
        "'http://x/cover.jpg', 'http://x/release/d1', 1, 0, NULL, NULL, '2026-01-01')"
    )
    conn.execute(
        "CREATE TABLE crawlers (id INTEGER PRIMARY KEY, site_name TEXT, module_path TEXT, "
        "crawler_type TEXT, enabled INTEGER, last_run TIMESTAMP)"
    )
    conn.execute("INSERT INTO crawlers VALUES (1, 'Test Site', 'crawlers.test', 'release', 1, NULL)")
    conn.execute(
        "CREATE TABLE listings (id INTEGER PRIMARY KEY, release_id TEXT, crawler_id INTEGER, "
        "url TEXT, price REAL, shipping REAL, currency TEXT, condition TEXT, last_checked TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO listings VALUES (1, 'd1', 1, 'http://x/1', 9.99, 2.0, 'USD', 'Mint', '2026-01-01')"
    )
    conn.execute(
        "CREATE TABLE stock_items (id INTEGER PRIMARY KEY, crawler_id INTEGER, artist TEXT, "
        "title TEXT, format TEXT, price REAL, currency TEXT, url TEXT, cover_image_url TEXT, "
        "item_key TEXT, last_seen TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO stock_items VALUES (1, 1, 'B', 'Stock Item', 'LP', 19.99, 'USD', "
        "'http://x/stock/1', 'http://x/stock1.jpg', 'stock-key-1', '2026-01-01')"
    )
    conn.execute(
        "CREATE TABLE stock_item_judgments (item_key TEXT PRIMARY KEY, recommended INTEGER, "
        "reason TEXT, judged_at TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO stock_item_judgments VALUES ('stock-key-1', 1, 'good pressing', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    return path


@respx.mock
def test_migrate_creates_user_catalog_library_item_and_listing(pg_test_db, sqlite_source):
    db.init_global_schema()
    db.init_tenant_schema()
    respx.get("https://api.discogs.com/users/alice").mock(
        return_value=httpx.Response(200, json={"id": 777})
    )

    user_id = migrate(sqlite_source, discogs_username="alice")

    with db.get_admin_pool().connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = %s", [user_id]).fetchone()
        assert user["discogs_user_id"] == 777

        catalog_row = conn.execute("SELECT * FROM catalog WHERE discogs_id = 'd1'").fetchone()
        assert catalog_row["artist"] == "A"

        library_item = conn.execute(
            "SELECT * FROM library_items WHERE user_id = %s AND discogs_id = 'd1'", [user_id]
        ).fetchone()
        assert library_item["in_collection"] is True

        # Look up the migrated crawler's actual (new, Postgres-assigned) id rather
        # than assuming it equals the source SQLite id of 1 — that assumption
        # would silently pass even if the migration forgot to remap crawler_id,
        # since a fresh schema's first SERIAL id also happens to be 1.
        new_crawler_id = conn.execute(
            "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
        ).fetchone()["id"]

        listing = conn.execute("SELECT * FROM listings WHERE release_id = 'd1'").fetchone()
        assert listing["price"] == 9.99
        assert listing["crawler_id"] == new_crawler_id

        stock_item = conn.execute(
            "SELECT * FROM stock_items WHERE item_key = 'stock-key-1'"
        ).fetchone()
        assert stock_item["title"] == "Stock Item"
        assert stock_item["price"] == 19.99
        assert stock_item["crawler_id"] == new_crawler_id

        conn.execute(
            "TRUNCATE users, catalog, library_items, listings, crawlers, "
            "stock_items, stock_item_judgments CASCADE"
        )
        conn.commit()


@respx.mock
def test_migrate_does_not_copy_stock_item_judgments(pg_test_db, sqlite_source):
    # stock_item_judgments moved from a global table to per-user/RLS (see
    # docs/superpowers/specs/2026-07-26-multi-tenant-architecture-design.md's
    # migration-path amendment) -- old global judgment rows have no user_id
    # to attach to, so they must not be copied. sqlite_source already seeds
    # one such row ('stock-key-1').
    db.init_global_schema()
    db.init_tenant_schema()
    respx.get("https://api.discogs.com/users/alice").mock(
        return_value=httpx.Response(200, json={"id": 777})
    )

    migrate(sqlite_source, discogs_username="alice")

    with db.get_admin_pool().connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM stock_item_judgments"
        ).fetchone()["count"]
        assert count == 0

        conn.execute(
            "TRUNCATE users, catalog, library_items, listings, crawlers, "
            "stock_items, stock_item_judgments CASCADE"
        )
        conn.commit()
