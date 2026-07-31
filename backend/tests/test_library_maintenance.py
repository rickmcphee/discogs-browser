import pytest

import db


# Matches the admin_conn fixture convention in test_stock_crud.py/test_crawl_queue.py
# (schema init + TRUNCATE teardown), but as an autouse fixture rather than a
# connection-yielding one -- these tests need both an admin connection (for
# create_user/upsert_catalog_release/etc.) and a separately pooled user_scope
# connection in the same test body, so they take pg_test_db directly instead.
@pytest.fixture(autouse=True)
def _clean_tables(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    yield
    with db.get_admin_pool().connection() as conn:
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
        conn.commit()


def test_get_missing_releases_for_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=True)
        db.upsert_listing(conn, "r1", crawler_id, "https://x", 9.99, None, "USD", None)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        missing = db.get_missing_releases(conn, alice["id"])
    assert missing == ["r2"]


def test_get_crawl_status_for_user(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py")
        crawler_id = conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        status = db.get_crawl_status_for_user(conn, alice["id"])
    assert status == {"total": 1, "missing": 1, "oldest_checked": None}


def test_clear_wishlist_flags_not_in_and_delete_orphaned_releases(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=False, in_wishlist=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        cleared = db.clear_wishlist_flags_not_in(conn, alice["id"], {"r1"})
        assert cleared == 1
        deleted = db.delete_orphaned_releases(conn, alice["id"])
        assert deleted == ["r2"]
        remaining = conn.execute("SELECT discogs_id FROM library_items WHERE user_id = %s", [alice["id"]]).fetchall()
    assert [r["discogs_id"] for r in remaining] == ["r1"]


def test_get_distinct_artists_for_user_scope(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Zzz", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_artists(conn, alice["id"], scope="collection")
    assert artists == ["Zzz"]
