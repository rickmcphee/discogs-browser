import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, listings, crawlers, stock_items, stock_item_judgments CASCADE")
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
    with pytest.raises(Exception):
        admin_conn.execute(
            "INSERT INTO listings (release_id, crawler_id, url) VALUES ('d1', %s, 'http://y')",
            [crawler_id],
        )
    admin_conn.rollback()
