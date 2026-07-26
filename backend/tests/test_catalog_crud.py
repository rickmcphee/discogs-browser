import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, listings, crawlers CASCADE")
        conn.commit()


def test_upsert_catalog_release_inserts_then_updates(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["artist"] == "A"
    assert row["title"] == "T"
    assert row["year"] == 1999
    assert row["label"] == "L"
    assert row["format"] == "LP"
    assert row["discogs_price"] == "$10"
    assert row["barcode"] == "123"
    assert row["cover_image_url"] == "http://x/cover.jpg"
    assert row["discogs_url"] == "http://x/release/d1"

    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A2", "title": "T (Reissue)", "year": 2005,
        "label": "L2", "format": "12\"", "discogs_price": "$15", "barcode": "456",
        "cover_image_url": "http://x/cover2.jpg", "discogs_url": "http://x/release/d1-reissue",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["artist"] == "A2"
    assert row["title"] == "T (Reissue)"
    assert row["year"] == 2005
    assert row["label"] == "L2"
    assert row["format"] == '12"'
    assert row["discogs_price"] == "$15"
    assert row["barcode"] == "456"
    assert row["cover_image_url"] == "http://x/cover2.jpg"
    assert row["discogs_url"] == "http://x/release/d1-reissue"


def test_get_catalog_release_returns_none_when_missing(admin_conn):
    assert db.get_catalog_release(admin_conn, "does-not-exist") is None


def test_upsert_catalog_release_can_overwrite_field_back_to_null(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": "123",
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["barcode"] == "123"

    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": 1999,
        "label": "L", "format": "LP", "discogs_price": "$10", "barcode": None,
        "cover_image_url": "http://x/cover.jpg", "discogs_url": "http://x/release/d1",
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["barcode"] is None


def test_upsert_listing_inserts_then_updates(admin_conn):
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None,
        "label": None, "format": None, "discogs_price": None, "barcode": None,
        "cover_image_url": None, "discogs_url": None,
    })
    admin_conn.commit()
    row = db.get_catalog_release(admin_conn, "d1")
    assert row["year"] is None
    assert row["label"] is None
    assert row["format"] is None
    assert row["discogs_price"] is None
    assert row["barcode"] is None
    assert row["cover_image_url"] is None
    assert row["discogs_url"] is None

    admin_conn.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Test', 'x')")
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Test'"
    ).fetchone()["id"]
    admin_conn.commit()

    db.upsert_listing(admin_conn, "d1", crawler_id, "http://x/1", 9.99, 2.0, "USD", "Mint")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM listings WHERE release_id = 'd1' AND crawler_id = %s", [crawler_id]
    ).fetchone()
    assert row["price"] == 9.99

    db.upsert_listing(admin_conn, "d1", crawler_id, "http://x/1", 7.50, 2.0, "USD", "Near Mint")
    admin_conn.commit()
    row = admin_conn.execute(
        "SELECT * FROM listings WHERE release_id = 'd1' AND crawler_id = %s", [crawler_id]
    ).fetchone()
    assert row["price"] == 7.50
    assert row["condition"] == "Near Mint"
