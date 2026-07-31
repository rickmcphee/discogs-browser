import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, users, crawlers CASCADE")
        conn.commit()


def test_replace_stock_items_clears_and_inserts_for_crawler(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]

    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "aphex twin", "title": "Selected Ambient Works", "url": "https://x/1", "price": 20.0, "currency": "USD"},
    ])
    admin_conn.commit()
    rows = admin_conn.execute("SELECT artist FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    assert rows[0]["artist"] == "Aphex Twin"  # title-cased, matching old behavior

    db.replace_stock_items(admin_conn, crawler_id, [])
    admin_conn.commit()
    rows = admin_conn.execute("SELECT * FROM stock_items WHERE crawler_id = %s", [crawler_id]).fetchall()
    assert rows == []


def test_get_stock_items_recommended_filters_to_calling_users_judgments(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    admin_conn.commit()
    item_key = db.compute_item_key("Artist A", "Album A", "https://x/1")
    db.upsert_stock_judgments(admin_conn, alice["id"], [{"item_key": item_key, "recommended": True, "reason": "great"}])
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        assert result["total"] == 1

    with db.user_scope(bob["id"]) as conn:
        result = db.get_stock_items(conn, bob["id"], recommended=True)
        assert result["total"] == 0


def test_get_stock_items_overlapping_excludes_items_matching_users_collection(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], recommended=False)
        assert result["total"] == 1  # plain browse still shows it

        db.upsert_stock_judgments(conn, alice["id"], [{
            "item_key": db.compute_item_key("Artist A", "Album A", "https://x/1"),
            "recommended": True, "reason": "x",
        }])
        result = db.get_stock_items(conn, alice["id"], recommended=True)
        assert result["total"] == 0  # already owned, excluded from recommended view


def test_get_stock_items_overlapping_true_returns_only_items_matching_users_collection(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], overlapping=False)
        assert result["total"] == 2  # plain browse shows both

        result = db.get_stock_items(conn, alice["id"], overlapping=True)
        assert result["total"] == 1
        assert result["items"][0]["artist"] == "Artist A"


def test_get_distinct_stock_artists_plain_browse(admin_conn):
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist A", "title": "Album A2", "url": "https://x/3", "price": 11.0, "currency": "USD"},
    ])
    admin_conn.commit()
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"])
    assert artists == ["Artist A", "Artist B"]  # distinct and sorted


def test_get_distinct_stock_artists_overlapping_filters_to_owned_artists(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    db.register_crawler(admin_conn, "Amazon", "/x.py", crawler_type="catalog")
    admin_conn.commit()
    crawler_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Amazon'").fetchone()["id"]
    db.replace_stock_items(admin_conn, crawler_id, [
        {"artist": "Artist A", "title": "Album A", "url": "https://x/1", "price": 10.0, "currency": "USD"},
        {"artist": "Artist B", "title": "Album B", "url": "https://x/2", "price": 15.0, "currency": "USD"},
    ])
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "r1", "artist": "Artist A", "title": "Album A", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    db.upsert_library_item(admin_conn, alice["id"], "r1", in_collection=True)
    admin_conn.commit()

    with db.user_scope(alice["id"]) as conn:
        artists = db.get_distinct_stock_artists(conn, alice["id"], overlapping=True)
    assert artists == ["Artist A"]
