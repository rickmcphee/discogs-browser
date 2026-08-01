import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        # TRUNCATE ... CASCADE on these three root tables also clears every
        # dependent table (listings, library_items, crawl_queue, sessions,
        # invites) via their FKs -- no need to name them individually.
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
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


def test_get_library_releases_returns_only_calling_users_rows(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    for rid, artist in [("r1", "AAA"), ("r2", "BBB")]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    db.upsert_library_item(admin_conn, bob["id"], "r2", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"])
    assert result["total"] == 1
    assert result["releases"][0]["discogs_id"] == "r1"


def test_get_library_releases_search_and_scope_filters(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Zzz Top", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r2", "artist": "Other", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True, in_wishlist=False)
    db.upsert_library_item(admin_conn, alice["id"], "r2", in_collection=False, in_wishlist=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], search="Zzz")
        assert result["total"] == 1 and result["releases"][0]["discogs_id"] == "r1"

        result = db.get_library_releases(conn, alice["id"], scope="wishlist")
        assert result["total"] == 1 and result["releases"][0]["discogs_id"] == "r2"


def _seed_three_releases_for_price_sort(admin_conn):
    """r1/r2 have Amazon listings (cheap/expensive), r3 has none, so the
    NULL-price ordering arm of the price_<site> sort is exercised too."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Amazon', '/x.py')")
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchone()["id"]
    for rid, artist in [("r1", "Bbb"), ("r2", "Aaa"), ("r3", "Ccc")]:
        db.upsert_catalog_release(admin_conn, {
            "discogs_id": rid, "artist": artist, "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(admin_conn, alice["id"], rid, in_collection=True)
    db.upsert_listing(admin_conn, "r1", crawler_id, "http://x/1", 5.00, None, "USD", None)
    db.upsert_listing(admin_conn, "r2", crawler_id, "http://x/2", 25.00, None, "USD", None)
    admin_conn.commit()
    return alice


def test_get_library_releases_sorts_by_price_for_named_site(admin_conn):
    alice = _seed_three_releases_for_price_sort(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="price_Amazon", order="asc")
    # Cheapest first, then dearer, then the release with no Amazon listing at all.
    assert [r["discogs_id"] for r in result["releases"]] == ["r1", "r2", "r3"]

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="price_Amazon", order="desc")
    assert result["releases"][0]["discogs_id"] == "r3"
    assert [r["discogs_id"] for r in result["releases"][1:]] == ["r2", "r1"]


def test_get_library_releases_price_sort_for_unknown_site_falls_back_to_artist_asc(admin_conn):
    # Documents current behaviour rather than endorsing it: an unrecognised
    # site name silently degrades to artist ASC and ignores `order` entirely,
    # with no signal to the caller. See the note in the Task 22 review.
    alice = _seed_three_releases_for_price_sort(admin_conn)

    with db.user_scope(alice["id"]) as conn:
        result = db.get_library_releases(conn, alice["id"], sort="price_NoSuchSite", order="desc")
    assert [r["discogs_id"] for r in result["releases"]] == ["r2", "r1", "r3"]


def test_get_listings_for_release_joins_crawler_site_name(admin_conn):
    admin_conn.execute("INSERT INTO crawlers (site_name, module_path) VALUES ('Amazon', '/x.py')")
    crawler_id = admin_conn.execute(
        "SELECT id FROM crawlers WHERE site_name = 'Amazon'"
    ).fetchone()["id"]
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_listing(admin_conn, "r1", crawler_id, "https://x", 9.99, 2.0, "USD", "VG+")
    admin_conn.commit()

    listings = db.get_listings_for_release(admin_conn, "r1")
    assert listings["Amazon"]["price"] == 9.99
    assert listings["Amazon"]["condition"] == "VG+"
