import pytest

import db
from routers import collection as collection_router


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([collection_router.router])


def test_collection_status_scoped_to_calling_user(pg_test_db, authed_client_factory):
    with db.get_admin_pool().connection() as conn:
        alice = db.create_user(conn, discogs_user_id=1, discogs_username="alice")
        bob = db.create_user(conn, discogs_user_id=2, discogs_username="bob")
        db.upsert_catalog_release(conn, {
            "discogs_id": "r1", "artist": "A", "title": "T", "year": None, "label": None,
            "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
            "discogs_url": None,
        })
        db.upsert_library_item(conn, alice["id"], "r1", in_collection=True)
        conn.commit()

    client = authed_client_factory(alice["id"])
    r = client.get("/api/collection/status")
    assert r.json()["total"] == 1

    client = authed_client_factory(bob["id"])
    r = client.get("/api/collection/status")
    assert r.json()["total"] == 0
