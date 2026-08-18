import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, catalog, library_items CASCADE")
        conn.commit()


def test_create_user_then_get_by_discogs_id(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()
    assert user["discogs_username"] == "alice"

    found = db.get_user_by_discogs_id(admin_conn, 42)
    assert found["id"] == user["id"]


def test_get_user_by_discogs_id_returns_none_when_missing(admin_conn):
    assert db.get_user_by_discogs_id(admin_conn, 999) is None


def test_create_user_twice_with_same_discogs_id_raises(admin_conn):
    db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    admin_conn.commit()

    with pytest.raises(Exception):
        db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice-again")
    admin_conn.rollback()


def test_upsert_library_item_and_get_for_user(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_collection=True)
    admin_conn.commit()

    items = db.get_library_items_for_user(admin_conn, user["id"])
    assert len(items) == 1
    assert items[0]["discogs_id"] == "d1"
    assert items[0]["in_collection"] is True
    assert items[0]["in_wishlist"] is False


def test_upsert_library_item_preserves_unspecified_field_on_update(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_collection=True)
    admin_conn.commit()

    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_wishlist=True)
    admin_conn.commit()

    items = db.get_library_items_for_user(admin_conn, user["id"])
    assert len(items) == 1
    assert items[0]["in_collection"] is True
    assert items[0]["in_wishlist"] is True


def test_has_any_price_paid_true_when_a_collection_item_has_a_price(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()
    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_collection=True, price_paid="25.00")
    admin_conn.commit()

    assert db.has_any_price_paid(admin_conn, user["id"]) is True


def test_has_any_price_paid_false_with_no_price_data(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()
    db.upsert_library_item(admin_conn, user_id=user["id"], discogs_id="d1", in_collection=True)
    admin_conn.commit()

    assert db.has_any_price_paid(admin_conn, user["id"]) is False


def test_has_any_price_paid_ignores_other_users(admin_conn):
    alice = db.create_user(admin_conn, discogs_user_id=42, discogs_username="alice")
    bob = db.create_user(admin_conn, discogs_user_id=43, discogs_username="bob")
    db.upsert_catalog_release(admin_conn, {
        "discogs_id": "d1", "artist": "A", "title": "T", "year": None, "label": None,
        "format": None, "discogs_price": None, "barcode": None, "cover_image_url": None,
        "discogs_url": None,
    })
    admin_conn.commit()
    db.upsert_library_item(admin_conn, user_id=alice["id"], discogs_id="d1", in_collection=True, price_paid="25.00")
    admin_conn.commit()

    assert db.has_any_price_paid(admin_conn, bob["id"]) is False
