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


def test_get_missing_releases_is_scoped_per_user(pg_test_db):
    # If the li.user_id filter were dropped from get_missing_releases, alice's
    # call would surface bob's still-missing r2 too (and vice versa) -- each
    # user must only ever see their own missing releases.
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.register_crawler(conn, "Amazon", "/x.py")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_missing_releases(conn, alice["id"]) == ["r1"]

    with db.user_scope(bob["id"]) as conn:
        assert db.get_missing_releases(conn, bob["id"]) == ["r2"]


def test_get_missing_releases_excludes_wishlist_only_items(pg_test_db):
    # Nothing surfaces price data for wishlist items anymore (Discogs tab
    # rename removed those columns), so the missing-prices crawl shouldn't
    # spend crawl budget on them -- only in_collection rows are candidates.
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.register_crawler(conn, "Amazon", "/x.py")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        assert db.get_missing_releases(conn, alice["id"]) == ["r1"]


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


def test_get_crawl_status_for_user_with_zero_total(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    with db.user_scope(alice["id"]) as conn:
        status = db.get_crawl_status_for_user(conn, alice["id"])
    assert status == {"total": 0, "missing": 0, "oldest_checked": None}


def test_get_crawl_status_for_user_with_no_enabled_crawlers(pg_test_db):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
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


def test_clear_wishlist_flags_not_in_with_empty_seen_ids_clears_all(pg_test_db):
    # The real-world call shape when a wishlist sync returns zero items --
    # locks in that `!= ALL(%s)` with an empty array still clears every
    # wishlist-flagged row, rather than being (incorrectly) a no-op.
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
        cleared = db.clear_wishlist_flags_not_in(conn, alice["id"], set())
        assert cleared == 2
        rows = conn.execute(
            "SELECT in_wishlist FROM library_items WHERE user_id = %s", [alice["id"]]
        ).fetchall()
    assert all(row["in_wishlist"] is False for row in rows)


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
