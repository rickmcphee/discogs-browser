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


def test_list_artists_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "Zzz", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/artists?scope=collection")
    assert r.json()["artists"] == ["Zzz"]


def test_list_crawlers_unscoped(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        db.register_crawler(conn, "Amazon", "/x.py")
        user = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        conn.commit()

    client = authed_client_factory(user["id"])
    r = client.get("/api/crawlers")
    assert r.json()["crawlers"][0]["site_name"] == "Amazon"
