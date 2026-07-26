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

        listing = conn.execute("SELECT * FROM listings WHERE release_id = 'd1'").fetchone()
        assert listing["price"] == 9.99

        conn.execute("TRUNCATE users, catalog, library_items, listings, crawlers CASCADE")
        conn.commit()
