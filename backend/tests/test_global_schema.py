import psycopg.errors
import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, listings, crawlers, stock_items, crawl_queue, stock_item_identities CASCADE")
        conn.commit()


def test_catalog_table_exists_with_expected_columns(admin_conn):
    cols = {
        r["column_name"]
        for r in admin_conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'catalog'"
        ).fetchall()
    }
    assert cols == {
        "discogs_id", "artist", "title", "year", "label", "format",
        "discogs_price", "barcode", "cover_image_url", "discogs_url", "last_synced",
    }


def test_listings_unique_on_release_and_crawler(admin_conn):
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO listings (release_id, crawler_id, url) VALUES ('d1', %s, 'http://x')",
        [crawler_id],
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO listings (release_id, crawler_id, url) VALUES ('d1', %s, 'http://y')",
            [crawler_id],
        )
    admin_conn.rollback()


def test_catalog_primary_key_enforced(admin_conn):
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'A', 'T')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'B', 'U')"
        )
    admin_conn.rollback()


def test_crawlers_site_name_unique(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.other')"
        )
    admin_conn.rollback()


def test_listings_rejects_release_id_not_in_catalog(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_conn.execute(
            "INSERT INTO listings (release_id, crawler_id, url) VALUES ('missing', %s, 'http://x')",
            [crawler_id],
        )
    admin_conn.rollback()


def test_listings_rejects_crawler_id_not_in_crawlers(admin_conn):
    admin_conn.execute(
        "INSERT INTO catalog (discogs_id, artist, title) VALUES ('d1', 'A', 'T')"
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_conn.execute(
            "INSERT INTO listings (release_id, crawler_id, url) VALUES ('d1', 999999, 'http://x')"
        )
    admin_conn.rollback()


def test_stock_items_insert_and_select(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    admin_conn.execute(
        """
        INSERT INTO stock_items (crawler_id, artist, title, format, price, currency, url, item_key)
        VALUES (%s, 'A', 'T', 'LP', 19.99, 'USD', 'http://x', 'key1')
        """,
        [crawler_id],
    )
    row = admin_conn.execute(
        "SELECT artist, title, price, item_key FROM stock_items WHERE item_key = 'key1'"
    ).fetchone()
    assert row["artist"] == "A"
    assert row["title"] == "T"
    assert row["price"] == 19.99
    assert row["item_key"] == "key1"


def test_crawl_queue_table_exists_with_unique_constraint(admin_conn):
    crawler_id = admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', '/x.py') RETURNING id"
    ).fetchone()["id"]
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.execute(
        "INSERT INTO crawl_queue (discogs_id, crawler_id) VALUES ('r1', %s)", [crawler_id]
    )
    admin_conn.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO crawl_queue (discogs_id, crawler_id) VALUES ('r1', %s)", [crawler_id]
        )
    admin_conn.rollback()


def test_crawlers_requires_discogs_release_defaults_to_false(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()
    assert row["requires_discogs_release"] is False


def test_crawlers_requires_discogs_release_can_be_set_true(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path, requires_discogs_release) VALUES ('Test Site', 'crawlers.test', TRUE)"
    )
    row = admin_conn.execute(
        "SELECT requires_discogs_release FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()
    assert row["requires_discogs_release"] is True


def test_stock_item_identities_table_exists_with_expected_columns(admin_conn):
    cols = {
        r["column_name"]
        for r in admin_conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'stock_item_identities'"
        ).fetchall()
    }
    assert cols == {"item_key", "artist", "title", "format", "last_seen"}


def test_listings_accepts_item_key_based_row_with_null_release_id(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO listings (item_key, crawler_id, url) VALUES ('key1', %s, 'http://x')",
        [crawler_id],
    )
    row = admin_conn.execute(
        "SELECT release_id, item_key FROM listings WHERE item_key = 'key1'"
    ).fetchone()
    assert row["release_id"] is None
    assert row["item_key"] == "key1"


def test_listings_unique_on_item_key_and_crawler(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO listings (item_key, crawler_id, url) VALUES ('key1', %s, 'http://x')",
        [crawler_id],
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO listings (item_key, crawler_id, url) VALUES ('key1', %s, 'http://y')",
            [crawler_id],
        )
    admin_conn.rollback()


def test_listings_rejects_item_key_not_in_stock_item_identities(admin_conn):
    admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', 'crawlers.test')"
    )
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test Site'"
    ).fetchone()["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_conn.execute(
            "INSERT INTO listings (item_key, crawler_id, url) VALUES ('missing', %s, 'http://x')",
            [crawler_id],
        )
    admin_conn.rollback()


def test_crawl_queue_accepts_item_key_based_row_with_null_discogs_id(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    crawler_id = admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', '/x.py') RETURNING id"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('key1', %s)", [crawler_id]
    )
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT discogs_id, item_key FROM crawl_queue WHERE item_key = 'key1'"
    ).fetchone()
    assert row["discogs_id"] is None
    assert row["item_key"] == "key1"


def test_crawl_queue_unique_on_item_key_and_crawler(admin_conn):
    admin_conn.execute(
        "INSERT INTO stock_item_identities (item_key, artist, title) VALUES ('key1', 'A', 'T')"
    )
    crawler_id = admin_conn.execute(
        "INSERT INTO crawlers (site_name, module_path) VALUES ('Test Site', '/x.py') RETURNING id"
    ).fetchone()["id"]
    admin_conn.execute(
        "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('key1', %s)", [crawler_id]
    )
    admin_conn.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_conn.execute(
            "INSERT INTO crawl_queue (item_key, crawler_id) VALUES ('key1', %s)", [crawler_id]
        )
    admin_conn.rollback()
