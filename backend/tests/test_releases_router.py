import pytest

import db
from routers import releases as releases_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([releases_router.router])


def test_list_releases_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/releases")
    assert r.json()["total"] == 1
    assert r.json()["releases"][0]["discogs_id"] == "r1"


def test_list_releases_scope_wishlist(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_wishlist=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/releases?scope=wishlist")
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r2"}


def test_list_releases_unmatched_filter(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_collection=True)
        db.set_plex_match(conn, alice["id"], "r1", "https://plex.local/album/1")
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/releases?unmatched=true")
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r2"}


def test_list_releases_cross_user_isolation(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        for rid in ("r1", "r2"):
            db.upsert_catalog_release(conn, {
                "discogs_id": rid, "artist": "A", "title": "T", "year": None, "label": None,
                "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
                "discogs_url": None,
            })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, bob["id"], "r2", in_collection=True)
        conn.commit()

    alice_client = authed_client_factory(alice["id"])
    r = alice_client.get("/api/releases")
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r1"}

    bob_client = authed_client_factory(bob["id"])
    r = bob_client.get("/api/releases")
    ids = {rel["discogs_id"] for rel in r.json()["releases"]}
    assert ids == {"r2"}


def test_list_artists_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Zzz", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_catalog_release(conn, {
            "discogs_id": "r2", "artist": "Aaa", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        db.upsert_library_item(conn, alice["id"], "r2", in_wishlist=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/artists?scope=discogs")
    assert r.json()["artists"] == ["Zzz"]


def test_list_crawlers_unscoped(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/crawlers")
    assert r.json()["crawlers"][0]["site_name"] == "Amazon"
